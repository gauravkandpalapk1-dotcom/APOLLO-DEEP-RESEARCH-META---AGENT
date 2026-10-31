"""Summarise the hand check of a benchmark: how many of the sampled kept sentences a person marked as supported,
per system per topic, appended to that benchmark's benchmark.md (replacing an earlier summary).
Run: python -m scripts.handcheck_summary [runs/benchmark-<timestamp>]   (default: the newest benchmark folder)

In handcheck.csv, "supported" is read as yes (yes, y, true, 1) or no (no, n, false, 0); any other value, or a blank,
counts as not checked yet. Nothing is guessed: unchecked rows are reported as unchecked."""
import argparse
import csv
import sys
from pathlib import Path

HEADING = "## Hand check"
YES, NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}


def newest_benchmark(runs_dir: str | Path = "runs") -> Path | None:
    folders = sorted(p for p in Path(runs_dir).glob("benchmark-*") if (p / "benchmark.md").exists())
    return folders[-1] if folders else None


def summarise(rows: list[dict]) -> list[dict]:
    """One entry per (system, topic), in file order: sampled, checked, supported."""
    out = {}
    for r in rows:
        s = out.setdefault((r["system"], r["topic"]), {"system": r["system"], "topic": r["topic"], "sampled": 0,
                                                      "checked": 0, "supported": 0})
        answer = (r.get("supported") or "").strip().lower()
        s["sampled"] += 1
        s["checked"] += answer in YES | NO
        s["supported"] += answer in YES
    return list(out.values())


def section(summary: list[dict]) -> str:
    lines = [HEADING, "", "Kept sentences sampled at random and checked by a person against the passages they cite.",
             "", "| Topic | System | Supported / checked | Sampled | Not checked yet |", "|---|---|---|---|---|"]
    for s in summary:
        lines.append(f"| {s['topic']} | {s['system']} | {s['supported']}/{s['checked']} | {s['sampled']} | "
                     f"{s['sampled'] - s['checked']} |")
    for system in dict.fromkeys(s["system"] for s in summary):
        mine = [s for s in summary if s["system"] == system]
        checked, supported = sum(s["checked"] for s in mine), sum(s["supported"] for s in mine)
        lines.append("")
        lines.append(f"- **{system}**: {supported}/{checked} checked sentences supported"
                     + (f" ({supported / checked:.0%})" if checked else " (none checked yet)") + ".")
    return "\n".join(lines) + "\n"


def append_summary(bench_dir: str | Path) -> str:
    bench_dir = Path(bench_dir)
    rows = list(csv.DictReader(open(bench_dir / "handcheck.csv", encoding="utf-8")))
    text = section(summarise(rows))
    md_path = bench_dir / "benchmark.md"
    md = md_path.read_text(encoding="utf-8")
    if HEADING in md:                                    # replace an earlier summary, keep what follows it
        before, _, rest = md.partition(HEADING)
        nxt = rest.find("\n## ")
        md = before + text + (rest[nxt + 1:] if nxt != -1 else "")
    else:
        md = md.rstrip("\n") + "\n\n" + text
    md_path.write_text(md, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Add the hand-check results to a benchmark's benchmark.md.")
    p.add_argument("bench_dir", nargs="?", help="benchmark folder (default: the newest runs/benchmark-*)")
    args = p.parse_args(argv)
    bench_dir = Path(args.bench_dir) if args.bench_dir else newest_benchmark()
    if bench_dir is None or not (bench_dir / "handcheck.csv").exists():
        p.error("no benchmark folder with a handcheck.csv found; run python -m scripts.benchmark first")
    print(append_summary(bench_dir))
    print(f"Added to {bench_dir / 'benchmark.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
