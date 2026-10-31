"""Benchmark Apollo against the single-shot baseline on every topic in benchmark_topics.txt.
Run: python -m scripts.benchmark [--topics benchmark_topics.txt] [--rounds N] [--sources web] [--limit N] [--seed 0]

Writes runs/benchmark-<timestamp>/: benchmark.csv, benchmark.md (markdown table + metric definitions),
handcheck.csv (random kept sentences with their passages, "supported" left blank for a human), and each run's
own folder under apollo/ and baseline/. A failed run is recorded as failed with its error; a number that was not
measured (failed run, or a metric that does not apply to that system) is left blank."""
import argparse
import csv
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from apollo.config import SOURCE_TYPES, Limits
from apollo.pipeline import Apollo
from apollo.state import domain_of
from apollo.writer import NO_CLAIM, UNVERIFIED, plain_sentence
from scripts import baseline, run_topic

COLUMNS = ["topic", "system", "status", "error", "kept_claims", "drafted_claims", "supported_claim_rate",
           "unverified_removed", "domains_cited", "subqs_covered", "subqs_planned", "gaps_found", "gaps_closed",
           "contradictions", "tokens", "seconds", "strong_model", "fast_model", "run_dir"]
HANDCHECK_COLUMNS = ["system", "topic", "sentence", "passage", "url", "supported"]
HANDCHECK_PER_RUN = 10

DEFINITIONS = """- **supported-claim rate** = kept claims / drafted claims. A drafted claim is every sentence the writer
  produced except sentences that only say the sources did not cover something (no factual claim) and sentences
  dropped as near-duplicates of an earlier sentence. Kept claims passed writer.verify(): they cite a retrieved
  passage and the verifier judged that the passage supports them. Sentences the verifier could not check count as
  not kept (they are removed); their number is in *unverified removed*.
- **domains cited**: distinct websites (lowercased host, "www." removed) among the report's references.
- **sub-questions covered / planned** (Apollo only): sub-questions that ended "covered" / sub-questions in the plan.
- **gaps found / closed** (Apollo only): distinct (sub-question, gap type) pairs flagged in any round / those whose
  sub-question ended "covered".
- **contradictions** (Apollo only): conflicting figures flagged by Hermes or by code. The baseline has no step that
  looks for contradictions, so its cell is blank (not measured), not 0.
- **tokens**: all LLM tokens the system used (planning, reviews, writing, verification).
- **seconds**: wall-clock time for the whole run, measured around it.
- Both systems use the same model chains from .env; *strong_model* / *fast_model* show the model that answered last."""


def claim_counts(sentences: list[dict]) -> tuple[int, int, int]:
    """(kept claims, drafted claims, unverified removed) from Report.sentences-shaped records."""
    drafted = [x for x in sentences if x["reason"] not in (NO_CLAIM, "duplicate")]
    kept = sum(x["verdict"] == "kept" for x in drafted)
    return kept, len(drafted), sum(x["reason"] == UNVERIFIED for x in drafted)


def gap_counts(events: list, final_status: dict[str, str]) -> tuple[int, int]:
    """(distinct (sub-question, gap type) pairs flagged, those whose sub-question ended covered)."""
    found = set()
    for e in events:
        if e.kind == "gap" and e.data:
            sq = e.data.get("sq") or e.data.get("sq_id")
            found.add((sq, e.data.get("type") or "contradiction"))
    return len(found), sum(final_status.get(sq) == "covered" for sq, _ in found)


def _row(topic: str, system: str, **values) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update(topic=topic, system=system, **values)
    return row


def _models(stats: dict) -> dict:
    active = (stats.get("models") or {}).get("active") or {}
    return {"strong_model": active.get("strong", ""), "fast_model": active.get("fast", "")}


def _error_of(out: Path) -> str:
    err = out / "error.txt"
    if not err.exists():
        return ""
    lines = err.read_text(encoding="utf-8").strip().splitlines()
    return lines[-1][:300] if lines else "unknown error"


def run_apollo(topic: str, limits: Limits, runs_dir: Path, make_app=Apollo) -> tuple[dict, list[dict]]:
    """Apollo through the same code path as scripts/run_topic.py; research() is only wrapped to keep the report."""
    started, captured = time.time(), {"events": []}
    try:
        app = make_app(limits)
        original = app.research

        def research(plan):
            for ev in original(plan):
                captured["events"].append(ev)
                if ev.kind == "done":
                    captured["report"] = ev.data["report"]
                yield ev

        app.research = research
        code, out = run_topic.run(topic, limits, runs_dir=runs_dir, app=app)
    except Exception as e:                         # e.g. no keys in .env: the run never started
        return _row(topic, "apollo", status="failed", error=f"{type(e).__name__}: {e}"[:300]), []
    seconds = round(time.time() - started, 1)
    rep = captured.get("report")
    if rep is None:
        return _row(topic, "apollo", status="failed", error=_error_of(out) or "no report", run_dir=str(out)), []
    kept, drafted, unverified = claim_counts(rep.sentences)
    final = {q: sq.status for q, sq in rep.state.plan.subqs.items()}
    found, closed = gap_counts(captured["events"], final)
    row = _row(topic, "apollo", status="ok", error=_error_of(out), kept_claims=kept, drafted_claims=drafted,
               supported_claim_rate=round(kept / drafted, 3) if drafted else "", unverified_removed=unverified,
               domains_cited=len({domain_of(r["url"]) for r in rep.references}),
               subqs_covered=sum(s == "covered" for s in final.values()), subqs_planned=len(final),
               gaps_found=found, gaps_closed=closed, contradictions=len(rep.state.contradictions),
               tokens=rep.stats.get("tokens_used", ""), seconds=seconds, run_dir=str(out), **_models(rep.stats))
    return row, rep.sentences


def run_baseline(topic: str, limits: Limits, runs_dir: Path, make_llm=None) -> tuple[dict, list[dict]]:
    started = time.time()
    try:
        code, out, result = baseline.run(topic, limits, runs_dir=runs_dir, llm=make_llm() if make_llm else None)
    except Exception as e:
        return _row(topic, "baseline", status="failed", error=f"{type(e).__name__}: {e}"[:300]), []
    seconds = round(time.time() - started, 1)
    if result is None:
        return _row(topic, "baseline", status="failed", error=_error_of(out) or "no report", run_dir=str(out)), []
    kept, drafted, unverified = claim_counts(result["sentences"])
    stats = result["stats"]
    row = _row(topic, "baseline", status="ok", kept_claims=kept, drafted_claims=drafted,
               supported_claim_rate=round(kept / drafted, 3) if drafted else "", unverified_removed=unverified,
               domains_cited=len({domain_of(r["url"]) for r in result["references"]}),
               tokens=stats.get("tokens_used", ""), seconds=seconds, run_dir=str(out), **_models(stats))
    return row, result["sentences"]


def handcheck_rows(system: str, topic: str, sentences: list[dict], rng: random.Random) -> list[dict]:
    """Up to 10 random kept claims, each with all the passages it cites; "supported" is for a human to fill in."""
    kept = [x for x in sentences if x["verdict"] == "kept" and x["evidence"]]
    picked = rng.sample(kept, min(HANDCHECK_PER_RUN, len(kept)))
    return [{"system": system, "topic": topic, "sentence": plain_sentence(x["text_with_refs"]),
             "passage": "\n---\n".join(ev["passage"] for ev in x["evidence"]),
             "url": " ".join(ev["url"] for ev in x["evidence"]), "supported": ""} for x in picked]


def _md_table(rows: list[dict]) -> str:
    head = ["Topic", "System", "Status", "Supported-claim rate (kept/drafted)", "Unverified removed", "Domains cited",
            "Sub-questions covered/planned", "Gaps found/closed", "Contradictions", "Tokens", "Seconds"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        rate = f"{r['supported_claim_rate']} ({r['kept_claims']}/{r['drafted_claims']})" if r["drafted_claims"] != "" else ""
        pair = lambda a, b: f"{r[a]}/{r[b]}" if r[a] != "" else ""
        lines.append("| " + " | ".join(str(x) for x in [
            r["topic"], r["system"], r["status"], rate, r["unverified_removed"], r["domains_cited"],
            pair("subqs_covered", "subqs_planned"), pair("gaps_found", "gaps_closed"), r["contradictions"],
            r["tokens"], r["seconds"]]) + " |")
    return "\n".join(lines)


def _means(rows: list[dict]) -> str:
    lines = []
    for system in dict.fromkeys(r["system"] for r in rows):
        ok = [r for r in rows if r["system"] == system and r["status"] == "ok"]
        if not ok:
            lines.append(f"- **{system}**: no successful runs")
            continue
        def mean(key):
            values = [float(r[key]) for r in ok if r[key] != ""]
            return round(sum(values) / len(values), 3) if values else "n/a"

        lines.append(f"- **{system}** (mean over {len(ok)} successful run(s)): supported-claim rate "
                     f"{mean('supported_claim_rate')}, domains cited {mean('domains_cited')}, tokens {mean('tokens')}, "
                     f"seconds {mean('seconds')}")
    return "\n".join(lines)


def read_topics(path: str | Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [t.strip() for t in lines if t.strip() and not t.strip().startswith("#")]


def run_benchmark(topics: list[str], limits: Limits, out_dir: str | Path, seed: int = 0,
                  make_app=Apollo, make_llm=None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows, checks = [], []
    for i, topic in enumerate(topics):
        for system, runner in (("apollo", lambda: run_apollo(topic, limits, out / "apollo", make_app)),
                               ("baseline", lambda: run_baseline(topic, limits, out / "baseline", make_llm))):
            print(f"\n===== [{i + 1}/{len(topics)}] {system}: {topic}", flush=True)
            row, sentences = runner()
            rows.append(row)
            checks += handcheck_rows(system, topic, sentences, random.Random(f"{seed}|{system}|{topic}"))
            print(f"===== {system}: {row['status']}" + (f" ({row['error']})" if row["status"] == "failed" else ""),
                  flush=True)

    with open(out / "benchmark.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    with open(out / "handcheck.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HANDCHECK_COLUMNS)
        w.writeheader()
        w.writerows(checks)
    failed = [r for r in rows if r["status"] == "failed"]
    md = ["# Apollo vs single-shot baseline", "",
          f"{len(topics)} topic(s), sources: {', '.join(limits.enabled_sources)}, max rounds: {limits.max_rounds}, "
          f"run {datetime.now():%Y-%m-%d %H:%M}.", "", _md_table(rows), "", "## Means", "", _means(rows), "",
          "## Failed runs", "", "\n".join(f"- {r['system']} / {r['topic']}: {r['error']}" for r in failed) or "None.",
          "", "## How each number is measured", "", DEFINITIONS, "",
          f"Hand check: `handcheck.csv` has up to {HANDCHECK_PER_RUN} random kept sentences per system per topic "
          f"with the passages they cite; fill in the *supported* column (yes/no) by reading them.", ""]
    (out / "benchmark.md").write_text("\n".join(md), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark Apollo against the single-shot baseline.")
    p.add_argument("--topics", default="benchmark_topics.txt")
    p.add_argument("--rounds", type=int, default=Limits.max_rounds)
    p.add_argument("--sources", type=run_topic.parse_sources, default=("web",),
                   help=f"Apollo's source types, from: {', '.join(SOURCE_TYPES)} (the baseline always uses one web search)")
    p.add_argument("--limit", type=int, default=0, help="only the first N topics")
    p.add_argument("--seed", type=int, default=0, help="seed for picking hand-check sentences")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    topics = read_topics(args.topics)[:args.limit or None]
    if not topics:
        p.error(f"no topics in {args.topics}")
    out = run_benchmark(topics, Limits(max_rounds=args.rounds, enabled_sources=args.sources),
                        Path("runs") / f"benchmark-{datetime.now():%Y%m%d-%H%M%S}", seed=args.seed)
    print("\n" + (out / "benchmark.md").read_text(encoding="utf-8"))
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
