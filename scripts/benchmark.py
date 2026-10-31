"""Benchmark Apollo against the single-shot baseline on every topic in benchmark_topics.txt.
Run: python -m scripts.benchmark [--topics benchmark_topics.txt] [--rounds N] [--sources web] [--limit N] [--seed 0]
     python -m scripts.benchmark --apollo-runs runs/<folder> runs/<folder> ...   (reuse saved Apollo runs)

Writes runs/benchmark-<timestamp>/: benchmark.csv, benchmark.md (markdown table + metric definitions),
handcheck.csv (random kept sentences with their passages, "supported" left blank for a human), and each run's
own folder under apollo/ and baseline/. A failed run is recorded as failed with its error; a number that was not
measured (failed run, or a metric that does not apply to that system) is left blank.
With --apollo-runs, Apollo is not run again: its numbers are read from those folders' saved files (provenance.json,
stats.json, events.jsonl), the topics are theirs, and only the baseline runs live."""
import argparse
import csv
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from apollo import config
from apollo.config import SOURCE_TYPES, Limits
from apollo.pipeline import Apollo
from apollo.state import domain_of
from apollo.writer import NO_CLAIM, UNVERIFIED, plain_sentence
from scripts import baseline, run_topic

COLUMNS = ["topic", "system", "status", "error", "kept_claims", "drafted_claims", "supported_claim_rate",
           "unverified_removed", "domains_cited", "subqs_covered", "subqs_planned", "gaps_found", "gaps_closed",
           "contradictions", "tokens", "seconds", "strong_model", "fast_model", "run_dir"]
HANDCHECK_COLUMNS = ["system", "topic", "sentence", "passage", "url", "supported"]
HANDCHECK_PER_RUN = 5
SMALL_SAMPLE = ("{n} topic(s) is a small sample: the numbers show a direction, not a proven result.")

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


def capture_run(topic: str, limits: Limits, runs_dir: Path, make_app=Apollo) -> dict:
    """Apollo through the same code path as scripts/run_topic.py; research() is only wrapped to keep the report
    and events. Returns {report (None if the run failed), events, out (run folder or None), seconds, error}."""
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
        return {"report": None, "events": [], "out": None, "seconds": round(time.time() - started, 1),
                "error": f"{type(e).__name__}: {e}"[:300]}
    rep = captured.get("report")
    return {"report": rep, "events": captured["events"], "out": out, "seconds": round(time.time() - started, 1),
            "error": _error_of(out) or ("" if rep else "no report")}


def run_apollo(topic: str, limits: Limits, runs_dir: Path, make_app=Apollo) -> tuple[dict, list[dict]]:
    run = capture_run(topic, limits, runs_dir, make_app)
    rep, out, seconds = run["report"], run["out"], run["seconds"]
    if rep is None:
        return _row(topic, "apollo", status="failed", error=run["error"], run_dir=str(out or "")), []
    kept, drafted, unverified = claim_counts(rep.sentences)
    final = {q: sq.status for q, sq in rep.state.plan.subqs.items()}
    found, closed = gap_counts(run["events"], final)
    row = _row(topic, "apollo", status="ok", error=run["error"], kept_claims=kept, drafted_claims=drafted,
               supported_claim_rate=round(kept / drafted, 3) if drafted else "", unverified_removed=unverified,
               domains_cited=len({domain_of(r["url"]) for r in rep.references}),
               subqs_covered=sum(s == "covered" for s in final.values()), subqs_planned=len(final),
               gaps_found=found, gaps_closed=closed, contradictions=len(rep.state.contradictions),
               tokens=rep.stats.get("tokens_used", ""), seconds=seconds, run_dir=str(out), **_models(rep.stats))
    return row, rep.sentences


def saved_apollo(run_dir: str | Path) -> tuple[dict, list[dict], dict]:
    """Apollo's benchmark row from a run folder saved by scripts/run_topic.py, without running it again.
    Returns (row, sentences, info about the run: folder, date, code version)."""
    run_dir = Path(run_dir)
    prov = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    stats = json.loads((run_dir / "stats.json").read_text(encoding="utf-8"))
    events = [SimpleNamespace(**json.loads(line))
              for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    final = {q["id"]: q["status"] for s in prov["plan"]["sections"] for q in s["sub_questions"]}
    sentences = prov["sentences"]
    kept, drafted, unverified = claim_counts(sentences)
    found, closed = gap_counts(events, final)
    row = _row(prov["topic"], "apollo", status="ok", kept_claims=kept, drafted_claims=drafted,
               supported_claim_rate=round(kept / drafted, 3) if drafted else "", unverified_removed=unverified,
               domains_cited=len({domain_of(r["url"]) for r in prov["references"]}),
               subqs_covered=sum(s == "covered" for s in final.values()), subqs_planned=len(final),
               gaps_found=found, gaps_closed=closed, contradictions=len(prov["contradictions"]),
               tokens=stats.get("tokens_used", ""), seconds=stats.get("seconds", ""), run_dir=str(run_dir),
               **_models(stats))
    made = datetime.fromtimestamp((run_dir / "stats.json").stat().st_mtime)
    return row, sentences, {"topic": prov["topic"], "dir": str(run_dir), "made": made, **code_at(made)}


def code_at(when: datetime) -> dict:
    """The last commit before `when` and the commits made after it (fixes the saved run does not have).
    Empty if git isn't available."""
    def log(*args) -> list[str]:
        try:
            out = subprocess.run(["git", "log", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M", *args],
                                 capture_output=True, text=True, timeout=20, encoding="utf-8")
            return out.stdout.strip().splitlines() if out.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            return []
    stamp = when.strftime("%Y-%m-%d %H:%M:%S")
    before = log("-1", f"--before={stamp}")
    return {"commit_before": before[0] if before else "", "commits_after": log(f"--after={stamp}")}


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


def baseline_wins(rows: list[dict]) -> list[str]:
    """Every topic and metric where the baseline did better than Apollo (both runs ok)."""
    better = {"supported_claim_rate": ("supported-claim rate", max), "domains_cited": ("domains cited", max),
              "tokens": ("tokens", min), "seconds": ("seconds", min)}
    out = []
    for topic in dict.fromkeys(r["topic"] for r in rows):
        pair = {r["system"]: r for r in rows if r["topic"] == topic and r["status"] == "ok"}
        if {"apollo", "baseline"} <= set(pair):
            for key, (name, best) in better.items():
                a, b = pair["apollo"][key], pair["baseline"][key]
                if a != "" and b != "" and float(a) != float(b) and best(float(a), float(b)) == float(b):
                    out.append(f"{topic}: {name} (baseline {b}, Apollo {a})")
    return out


def handcheck_rows(system: str, topic: str, sentences: list[dict], rng: random.Random) -> list[dict]:
    """Up to HANDCHECK_PER_RUN random kept claims, each with all the passages it cites; "supported" is for a human
    to fill in."""
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
                  make_app=Apollo, make_llm=None, apollo_runs: list[str | Path] | None = None) -> Path:
    """apollo_runs: saved Apollo run folders to reuse instead of running Apollo; their topics replace `topics`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = {}
    for d in apollo_runs or []:
        row, sentences, info = saved_apollo(d)
        saved[row["topic"]] = (row, sentences, info)
    if saved:
        topics = list(saved)
    rows, checks = [], []
    for i, topic in enumerate(topics):
        apollo = (lambda: saved[topic][:2]) if topic in saved else (lambda: run_apollo(topic, limits, out / "apollo",
                                                                                        make_app))
        for system, runner in (("apollo", apollo),
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
    wins = baseline_wins(rows)
    setup = (f"{len(topics)} topic(s), Tavily search depth: {config.SEARCH_DEPTH}, run {datetime.now():%Y-%m-%d %H:%M}. "
             + ("Apollo: saved runs (not run again, see below); baseline: run now." if saved else
                f"Apollo sources: {', '.join(limits.enabled_sources)}, max rounds: {limits.max_rounds}."))
    md = ["# Apollo vs single-shot baseline", "", setup, "", _md_table(rows), "",
          SMALL_SAMPLE.format(n=len(topics)) + " Rows where the baseline did better: "
          + ("; ".join(wins) if wins else "none") + ".", "",
          "## Means", "", _means(rows), "",
          "## Failed runs", "", "\n".join(f"- {r['system']} / {r['topic']}: {r['error']}" for r in failed) or "None.",
          ""]
    if saved:
        md += ["## Saved Apollo runs", "",
               "These Apollo runs were made earlier and read from their saved files (their tokens and seconds come "
               "from each run's stats.json); the code has changed since. A run may also include changes that were "
               "not yet committed when it was made.", ""]
        for row, _, info in saved.values():
            later = info["commits_after"]
            md += [f"- **{info['topic']}**: `{info['dir']}`, made {info['made']:%Y-%m-%d %H:%M}; last commit before "
                   f"it: {info['commit_before'] or 'unknown'}. "
                   + (f"Committed since ({len(later)}), not in this run: " + "; ".join(later) if later else
                      "No commits since.")]
        md.append("")
    md += ["## How each number is measured", "", DEFINITIONS, "",
          f"Hand check: `handcheck.csv` has up to {HANDCHECK_PER_RUN} random kept sentences per system per topic "
          f"with the passages they cite; fill in the *supported* column (yes/no) by reading them, then run "
          f"`python -m scripts.handcheck_summary` to add the results to this file.", ""]
    (out / "benchmark.md").write_text("\n".join(md), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Benchmark Apollo against the single-shot baseline.")
    p.add_argument("--topics", default="benchmark_topics.txt")
    p.add_argument("--rounds", type=int, default=Limits.max_rounds)
    p.add_argument("--sources", type=run_topic.parse_sources, default=("web",),
                   help=f"Apollo's source types, from: {', '.join(SOURCE_TYPES)} (the baseline always uses one web search)")
    p.add_argument("--limit", type=int, default=0, help="only the first N topics")
    p.add_argument("--seed", type=int, default=0, help="seed for picking hand-check sentences")
    p.add_argument("--apollo-runs", nargs="+", default=None, metavar="RUN_DIR",
                   help="saved Apollo run folders to reuse instead of running Apollo (their topics are used)")
    args = p.parse_args(argv)
    for d in args.apollo_runs or []:
        if not (Path(d) / "provenance.json").exists():
            p.error(f"{d} is not a saved Apollo run (no provenance.json)")
    topics = [] if args.apollo_runs else read_topics(args.topics)[:args.limit or None]
    if not topics and not args.apollo_runs:
        p.error(f"no topics in {args.topics}")
    out = run_benchmark(topics, Limits(max_rounds=args.rounds, enabled_sources=args.sources),
                        Path("runs") / f"benchmark-{datetime.now():%Y%m%d-%H%M%S}", seed=args.seed,
                        apollo_runs=args.apollo_runs)
    print("\n" + (out / "benchmark.md").read_text(encoding="utf-8"))
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
