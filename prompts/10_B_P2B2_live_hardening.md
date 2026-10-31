# Prompt 2B-2 — live hardening and the failure topic
Owner: B · When: H5 · Needs: 2B-1, and D's scout results

```text
Read CLAUDE.md. Use scripts/run_topic.py for live runs.

1. Run the three demo topic candidates that D gives you, each with web,news,arxiv.
   Fix every error you see. Typical ones: Tavily quota or auth errors (emit a clear event
   naming the problem), empty arXiv results, EDGAR returning nothing for non-US topics,
   slow pages (per-source timeout in config).
2. Confirm the failure path: a sub-question with zero usable results must end as
   "unfillable", appear in Limitations, and show its failed or empty searches in the trace.
   If it doesn't, fix it.
3. Write docs/demo_topics.md from the actual runs: for each topic, rounds used, stop
   reason, gaps found, contradictions found, failures, and what it demonstrates.
   Use only what the runs produced.
```
