"""Single-shot baseline to compare Apollo against: one web search, one strong-model call, then the same claim
verification Apollo uses (writer.verify). No planning, no review, no follow-up searches.
Run: python -m scripts.baseline "<topic>"

Saves to runs/<timestamp>-baseline-<slug>/: report.md, events.jsonl, sources.json, stats.json, sentences.json.
A failed run still saves events.jsonl (written line by line) plus error.txt."""
import argparse
import json
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from apollo.config import CHUNK_DATA_RULE, Limits
from apollo.llm import LLM, Budget
from apollo.search import Doc, tavily
from apollo.state import Chunk, chunk_text, relevance
from apollo.writer import UNVERIFIED, render_citations, sentence_evidence, verify
from scripts.run_topic import _write_json, slugify

RESULTS = 10          # one search, 10 results with page content
CHUNKS_PER_DOC = 2    # same as Athena: the two passages of each page most relevant to the question

BASELINE_SYSTEM = ("You are a research writer. You write only from the evidence chunks provided and never add facts "
                   "from your own knowledge. " + CHUNK_DATA_RULE)


def fetch(topic: str, limits: Limits) -> list[Doc]:
    return tavily(topic, replace(limits, results_per_query=RESULTS), "general")


def build_chunks(topic: str, docs: list[Doc]) -> dict[str, Chunk]:
    chunks = {}
    for d in docs:
        best = sorted(((relevance(p, topic), p) for p in chunk_text(d.text)), reverse=True)[:CHUNKS_PER_DOC]
        for score, text in best:
            cid = f"C{len(chunks) + 1}"
            chunks[cid] = Chunk(cid, "baseline", d.url, d.title, text, d.source_type, d.published, d.year, score, 1,
                                d.domain, d.source_class)
    return chunks


def write_report(topic: str, chunks: dict[str, Chunk], llm) -> str:
    evidence = "\n".join(f'<chunk id="{c.id}" url="{c.url}" published="{c.published or c.year or "unknown"}">'
                         f'{c.text[:1500]}</chunk>' for c in chunks.values())
    user = f"""Topic: {topic}

Evidence:
{evidence}

Write a research report on the topic: 5 to 8 paragraphs of markdown prose with no headings.
Rules:
1. Every sentence that states a fact ends with citations like [C3] or [C3, C9], placed before the full stop.
2. Cite only chunks whose text directly supports that sentence.
3. If the evidence does not cover something, say the sources found did not cover it. Never fill gaps from memory.
4. Where sources disagree, give both figures with their citations.
5. Give the year of a figure when the chunk states it."""
    return llm.chat(BASELINE_SYSTEM, user, temperature=0.3, role="strong")


def run(topic: str, limits: Limits, runs_dir: str | Path = "runs", llm=None) -> tuple[int, Path, dict | None]:
    """Baseline for one topic. Returns (exit code, run folder, result or None). Never raises.
    result: markdown, references, sentences (same shape as Apollo's Report.sentences), stats."""
    base = Path(runs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}-baseline-{slugify(topic)}"
    out, n = base, 1
    while out.exists():
        n += 1
        out = base.with_name(f"{base.name}-{n}")
    out.mkdir(parents=True)
    started = time.time()
    result, error = None, None

    with open(out / "events.jsonl", "w", encoding="utf-8") as log:
        def emit(kind: str, text: str, data: dict | None = None):
            t = round(time.time() - started, 1)
            print(f"{t:>6}s {kind:<11} {text}", flush=True)
            log.write(json.dumps({"t": t, "kind": kind, "text": text, "data": data}, default=str,
                                 ensure_ascii=False) + "\n")
            log.flush()

        try:
            llm = llm or LLM(Budget(limits.token_budget))
            for w in getattr(llm, "warnings", []):
                emit("warning", w)
            emit("search", f"Baseline: one web search for '{topic}' ({RESULTS} results)")
            docs = fetch(topic, limits)
            chunks = build_chunks(topic, docs)
            emit("chunks", f"{len(docs)} results -> {len(chunks)} chunks", {"urls": [d.url for d in docs]})
            if not chunks:
                raise RuntimeError("the search returned no usable text")
            emit("write", "One strong-model call writes the whole report")
            draft = write_report(topic, chunks, llm)
            draft = "\n".join(line for line in draft.splitlines() if not line.lstrip().startswith("#"))
            clean, records, problems = verify(draft, chunks, llm, "Baseline")
            for p in problems:
                emit("system", f"Verifier: {p}")
            kept = sum(v.verdict == "kept" for v in records)
            emit("verify", f"{kept} sentences kept, {len(records) - kept} removed by verification")

            md, refs = render_citations(f"# {topic}\n\n{clean or '*No sentence survived verification.*'}", chunks)
            md += "\n\n## References\n\n" + "\n".join(
                f"{r['n']}. [{r['title']}]({r['url']}) — {r['type']}{', ' + r['published'] if r['published'] else ''}"
                for r in refs)
            stats = {"topic": topic, "system": "baseline", "sources_collected": len({d.url for d in docs}),
                     "chunks": len(chunks), "sources_cited": len(refs), "sentences_kept": kept,
                     "sentences_removed": len(records) - kept,
                     "sentences_unverified_removed": sum(v.reason == UNVERIFIED for v in records),
                     "tokens_used": llm.budget.used, "seconds": round(time.time() - started, 1)}
            if hasattr(llm, "status"):
                stats["models"] = llm.status()
            cited = {r["url"]: r["n"] for r in refs}
            sources = [{"ref": cited.get(c.url, ""), "title": c.title, "url": c.url, "type": c.source_type,
                        "domain": c.domain, "source_class": c.source_class, "published": c.published or ""}
                       for c in {c.url: c for c in chunks.values()}.values()]
            result = {"markdown": md, "references": refs, "sentences": sentence_evidence(records, chunks, refs),
                      "stats": stats, "sources": sources}
            emit("done", "Baseline report ready")
        except (Exception, KeyboardInterrupt):
            error = traceback.format_exc()
            emit("error", f"Baseline failed: {error.strip().splitlines()[-1][:200]}")

    if result is not None:
        (out / "report.md").write_text(result["markdown"], encoding="utf-8")
        _write_json(out / "sources.json", result["sources"])
        _write_json(out / "stats.json", result["stats"])
        _write_json(out / "sentences.json", result["sentences"])
    if error:
        (out / "error.txt").write_text(error, encoding="utf-8")
        return 1, out, None
    return 0, out, result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the single-shot baseline on one topic.")
    p.add_argument("topic")
    args = p.parse_args(argv)
    if not args.topic.strip():
        p.error("topic is empty")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    code, out, result = run(args.topic.strip(), Limits(enabled_sources=("web",)))
    if result:
        s = result["stats"]
        print(f"\nsources={s['sources_collected']} cited={s['sources_cited']} kept={s['sentences_kept']} "
              f"removed={s['sentences_removed']} tokens={s['tokens_used']} seconds={s['seconds']}")
    print(("FAILED, see error.txt in " if code else "Saved to ") + str(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
