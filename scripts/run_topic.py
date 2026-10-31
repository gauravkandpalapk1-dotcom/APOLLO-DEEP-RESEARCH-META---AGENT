"""Run one topic end to end without Streamlit and save everything to runs/<timestamp>-<slug>/.
Run: python -m scripts.run_topic "<topic>" [--rounds N] [--sources web,news,arxiv]

Files: report.md, events.jsonl, sources.json, stats.json, removed.json (sentences dropped in verification,
with the reason). A failed run still saves events.jsonl (written line by line as the run goes) plus error.txt."""
import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from apollo.config import SOURCE_TYPES, Limits
from apollo.pipeline import Apollo


def slugify(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50] or "topic"


def parse_sources(raw: str) -> tuple[str, ...]:
    sources = tuple(dict.fromkeys(s.strip().lower() for s in raw.split(",") if s.strip()))
    bad = [s for s in sources if s not in SOURCE_TYPES]
    if bad or not sources:
        raise argparse.ArgumentTypeError(f"unknown source(s) {bad or raw!r}; choose from {', '.join(SOURCE_TYPES)}")
    return sources


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


def run(topic: str, limits: Limits, runs_dir: str | Path = "runs", app: Apollo | None = None) -> tuple[int, Path]:
    """Plan + research one topic. Returns (exit code, run folder). Never raises."""
    base = Path(runs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(topic)}"
    out, n = base, 1
    while out.exists():                     # two runs in the same second must not overwrite each other
        n += 1
        out = base.with_name(f"{base.name}-{n}")
    out.mkdir(parents=True)
    started = time.time()
    report, error = None, None

    with open(out / "events.jsonl", "w", encoding="utf-8") as log:
        def emit(kind: str, text: str, data: dict | None = None):
            t = round(time.time() - started, 1)
            print(f"{t:>6}s {kind:<11} {text}", flush=True)
            log.write(json.dumps({"t": t, "kind": kind, "text": text, "data": data},
                                 default=str, ensure_ascii=False) + "\n")
            log.flush()

        try:
            app = app or Apollo(limits)
            for w in getattr(app.llm, "warnings", []):
                emit("warning", w)
            emit("planning", f"Apollo is planning: {topic} (sources: {', '.join(limits.enabled_sources)})")
            plan = app.plan(topic)
            for ev in app.research(plan):
                if ev.kind == "done":
                    report = ev.data["report"]
                    emit(ev.kind, ev.text)          # the Report object itself goes to the files below
                else:
                    emit(ev.kind, ev.text, ev.data)
            if report is None:
                error = "The research loop ended without a report."
        except (Exception, KeyboardInterrupt):  # a failed run still leaves its trace and the reason on disk
            error = traceback.format_exc()
            emit("error", f"Run failed: {error.strip().splitlines()[-1][:200]}")

    if report is not None:
        (out / "report.md").write_text(report.markdown, encoding="utf-8")
        _write_json(out / "sources.json", report.sources)
        _write_json(out / "removed.json", report.removed)
        _write_json(out / "stats.json", {"topic": topic, "enabled_sources": list(limits.enabled_sources),
                                         "max_rounds": limits.max_rounds, **report.stats})
        if not error and not report.references:
            error = "The run finished but the report cites no sources (see the error events in events.jsonl)."
    if error:
        (out / "error.txt").write_text(error, encoding="utf-8")
        return 1, out
    return 0, out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run Apollo on one topic without the Streamlit UI.")
    p.add_argument("topic")
    p.add_argument("--rounds", type=int, default=Limits.max_rounds, help="maximum research rounds")
    p.add_argument("--sources", type=parse_sources, default=("web", "news", "arxiv"),
                   help=f"comma-separated, from: {', '.join(SOURCE_TYPES)}")
    args = p.parse_args(argv)
    if not args.topic.strip():
        p.error("topic is empty")
    if args.rounds < 1:
        p.error("--rounds must be at least 1")
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    code, out = run(args.topic.strip(), Limits(max_rounds=args.rounds, enabled_sources=args.sources))
    stats_file = out / "stats.json"
    if stats_file.exists():
        s = json.loads(stats_file.read_text(encoding="utf-8"))
        print(f"\nrounds={s.get('rounds')} stop='{s.get('stop_reason')}' sources={s.get('sources_collected')} "
              f"cited={s.get('sources_cited', 0)} kept={s.get('sentences_kept', 0)} "
              f"removed={s.get('sentences_removed', 0)} unverified_removed={s.get('sentences_unverified_removed', 0)} "
              f"tokens={s.get('tokens_used')}")
    print(("FAILED, see error.txt in " if code else "Saved to ") + str(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
