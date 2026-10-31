"""Scout demo topics: run each candidate in scout_topics.txt through the same code path as scripts/run_topic.py
(max 3 rounds), tabulate what happened, and recommend topics for the demo from those numbers only.
Run: python -m scripts.scout_topics [--topics scout_topics.txt] [--sources web] [--limit N]

Writes runs/scout-<timestamp>/scout.md (table + recommendations + rules), scout.csv, and each run's own folder."""
import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from apollo.config import SOURCE_TYPES, Limits
from apollo.pipeline import Apollo
from scripts import run_topic
from scripts.benchmark import capture_run, read_topics

COLUMNS = ["topic", "status", "error", "rounds", "stop_reason", "gaps_found", "gaps_by_type", "gaps_closed",
           "contradictions", "contradictions_both_cited", "unfillable", "covered", "subqs", "failed_searches",
           "sources", "domains", "areas", "area_names", "minutes", "tokens", "run_dir"]
MIN_AREAS = 3   # "distinct areas" = distinct source classes among the sources collected

RULES = f"""- **Best two-round topic**: 2 or more rounds, at least one gap closed (flagged in a round, sub-question ended
  covered), and sources from at least {MIN_AREAS} distinct areas. *Areas* are source classes (government,
  academic, filing, news, other). Ranked by gaps closed, then distinct domains.
- **Best contradiction topic**: at least one contradiction whose conflicting passages are both cited by sentences
  kept in the report (so both figures are in it). Ranked by such contradictions, then distinct domains.
- **Best failure topic**: at least one unfillable sub-question in a run that still finished with a report. Ranked by
  sub-questions covered (the rest of the run should look healthy), then fewer unfillable.
- Only successful runs are considered, and only the numbers in the table are used. If no topic meets a role's rule,
  that role says so; the closest topic is not picked."""


def scout_row(topic: str, run: dict) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update(topic=topic, run_dir=str(run["out"] or ""), minutes=round(run["seconds"] / 60, 1))
    rep = run["report"]
    if rep is None:
        row.update(status="failed", error=run["error"])
        return row
    st = rep.state
    final = {q: sq.status for q, sq in st.plan.subqs.items()}
    gaps = set()                                   # distinct (sub-question, gap type) pairs flagged in any round
    for e in run["events"]:
        if e.kind == "gap" and e.data:
            gaps.add((e.data.get("sq") or e.data.get("sq_id"), e.data.get("type") or "contradiction"))
    by_type = Counter(t for _, t in gaps)
    cited = {cid for x in rep.sentences if x["verdict"] == "kept" for cid in x["chunk_ids"]}
    both = sum(len(c["chunk_ids"]) >= 2 and all(cid in cited for cid in c["chunk_ids"]) for c in st.contradictions)
    areas = sorted({c.source_class for c in st.chunks.values()})
    row.update(status="ok", error=run["error"], rounds=st.round, stop_reason=st.stop_reason, gaps_found=len(gaps),
               gaps_by_type=", ".join(f"{t} {n}" for t, n in sorted(by_type.items())),
               gaps_closed=sum(final.get(sq) == "covered" for sq, _ in gaps), contradictions=len(st.contradictions),
               contradictions_both_cited=both, unfillable=sum(s == "unfillable" for s in final.values()),
               covered=sum(s == "covered" for s in final.values()), subqs=len(final),
               failed_searches=len(st.failed_searches), sources=len({c.url for c in st.chunks.values()}),
               domains=len({c.domain for c in st.chunks.values()}), areas=len(areas), area_names=", ".join(areas),
               tokens=rep.stats.get("tokens_used", ""))
    return row


def recommend(rows: list[dict]) -> dict[str, tuple[dict | None, str]]:
    """Role -> (chosen row or None, one-line reason), using only the table's numbers."""
    ok = [r for r in rows if r["status"] == "ok"]
    out = {}

    two = [r for r in ok if r["rounds"] >= 2 and r["gaps_closed"] >= 1 and r["areas"] >= MIN_AREAS]
    best = max(two, key=lambda r: (r["gaps_closed"], r["domains"]), default=None)
    out["two-round"] = (best, f"{best['rounds']} rounds, {best['gaps_closed']} gap(s) closed, {best['areas']} areas "
                              f"({best['area_names']}), {best['domains']} domains" if best else
                        f"No topic fits: none had 2+ rounds with a gap closed and sources from {MIN_AREAS}+ areas.")

    con = [r for r in ok if r["contradictions_both_cited"] >= 1]
    best = max(con, key=lambda r: (r["contradictions_both_cited"], r["domains"]), default=None)
    out["contradiction"] = (best, f"{best['contradictions_both_cited']} contradiction(s) with both sides cited in the "
                                  f"report ({best['contradictions']} found)" if best else
                            "No topic fits: no contradiction had both conflicting passages cited in the report.")

    fail = [r for r in ok if r["unfillable"] >= 1]
    best = max(fail, key=lambda r: (r["covered"], -r["unfillable"]), default=None)
    out["failure"] = (best, f"{best['unfillable']} unfillable sub-question(s), {best['covered']}/{best['subqs']} "
                            f"covered, run finished ({best['stop_reason']})" if best else
                      "No topic fits: every finished run filled all its sub-questions.")
    return out


def _md(rows: list[dict], recs: dict, limits: Limits) -> str:
    head = ["Topic", "Status", "Rounds", "Stop reason", "Gaps found (by type)", "Gaps closed",
            "Contradictions (both sides cited)", "Unfillable", "Failed searches", "Sources", "Domains", "Areas",
            "Minutes", "Tokens"]
    lines = ["# Demo topic scout", "",
             f"{len(rows)} topic(s), sources: {', '.join(limits.enabled_sources)}, max rounds: {limits.max_rounds}, "
             f"run {datetime.now():%Y-%m-%d %H:%M}.", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        if r["status"] == "failed":
            lines.append(f"| {r['topic']} | failed: {r['error']} |" + " |" * (len(head) - 2))
            continue
        lines.append("| " + " | ".join(str(x) for x in [
            r["topic"], "ok", r["rounds"], r["stop_reason"], f"{r['gaps_found']} ({r['gaps_by_type'] or 'none'})",
            r["gaps_closed"], f"{r['contradictions']} ({r['contradictions_both_cited']})", r["unfillable"],
            r["failed_searches"], r["sources"], r["domains"], f"{r['areas']} ({r['area_names']})", r["minutes"],
            r["tokens"]]) + " |")
    lines += ["", "## Recommendations", ""]
    for role, title in (("two-round", "Best two-round topic"), ("contradiction", "Best contradiction topic"),
                        ("failure", "Best failure topic")):
        row, why = recs[role]
        lines.append(f"- **{title}:** " + (f"{row['topic']} — {why}." if row else why))
    lines += ["", "## Rules", "", RULES, ""]
    return "\n".join(lines)


def run_scout(topics: list[str], limits: Limits, out_dir: str | Path, make_app=Apollo) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, topic in enumerate(topics):
        print(f"\n===== [{i + 1}/{len(topics)}] {topic}", flush=True)
        rows.append(scout_row(topic, capture_run(topic, limits, out / "runs", make_app)))
        print(f"===== {rows[-1]['status']}" + (f" ({rows[-1]['error']})" if rows[-1]["status"] == "failed" else ""),
              flush=True)
    with open(out / "scout.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    (out / "scout.md").write_text(_md(rows, recommend(rows), limits), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Scout demo topics with Apollo and recommend the best ones.")
    p.add_argument("--topics", default="scout_topics.txt")
    p.add_argument("--rounds", type=int, default=3, help="maximum rounds per topic (default 3)")
    p.add_argument("--sources", type=run_topic.parse_sources, default=("web",),
                   help=f"comma-separated, from: {', '.join(SOURCE_TYPES)}")
    p.add_argument("--limit", type=int, default=0, help="only the first N topics")
    args = p.parse_args(argv)
    if not Path(args.topics).exists():
        p.error(f"{args.topics} not found: write one candidate topic per line (lines starting with # are ignored)")
    topics = read_topics(args.topics)[:args.limit or None]
    if not topics:
        p.error(f"no topics in {args.topics}")
    out = run_scout(topics, Limits(max_rounds=args.rounds, enabled_sources=args.sources),
                    Path("runs") / f"scout-{datetime.now():%Y%m%d-%H%M%S}")
    print("\n" + (out / "scout.md").read_text(encoding="utf-8"))
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
