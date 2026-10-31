# Prompt 0.2 — headless runner and first live run
Owner: all (one laptop) · When: H0.5 · Needs: check_keys says READY

```text
Read CLAUDE.md.

1. Write scripts/run_topic.py: python -m scripts.run_topic "<topic>" [--rounds N] [--sources web,news,arxiv]
   It runs plan + research without Streamlit, prints every event as one line
   (time, kind, text), and saves to runs/<timestamp>-<slug>/:
   report.md, events.jsonl, sources.json, stats.json.
   A failed run must still save events.jsonl and an error.txt.
2. Run it on "Impact of AI on Indian agriculture" with web only, then with web,news,arxiv.
   Fix any live-API problem you find (request format, response parsing, timeouts).
   If SEC EDGAR or arXiv misbehaves, make sure turning that source off is enough;
   don't let one source break the run.
3. Report: what failed and how you fixed it; rounds used; stop reason; number of sources;
   sentences kept and removed; which models answered and how many model or key
   switches happened. Do not paste the whole report.

Gate 1 passes when one real topic runs end to end and writes a cited report.md.
```

After Gate 1: commit, push to a private GitHub repo, teammates clone it and run setup.
