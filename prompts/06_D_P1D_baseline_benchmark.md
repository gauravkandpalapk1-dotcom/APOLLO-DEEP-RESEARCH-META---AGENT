# Prompt 1D — baseline and benchmark script
Owner: D · Branch: role-d · When: H1 · Needs: 0.2

```text
Read CLAUDE.md. Create new files only: scripts/baseline.py, scripts/benchmark.py,
benchmark_topics.txt. Reuse search.py, llm.py and writer.verify(); don't modify them.

1. baseline.py: one Tavily search on the topic (10 results with content), turned into
   chunks with ids C1..Cn, then ONE strong-model call that writes a cited report using
   those ids in the same [C3] format Apollo uses. Then run writer.verify() on it.
   Save to runs/<timestamp>-baseline-<slug>/ like scripts/run_topic.py does.
2. benchmark.py: for each topic in benchmark_topics.txt, run Apollo (same code path as
   run_topic.py) and the baseline with the same models. Compute per system:
   supported-claim rate (kept / factual sentences drafted), distinct domains cited,
   sub-questions covered / planned (Apollo only), gaps found and closed (Apollo only),
   contradictions surfaced, tokens, wall-clock seconds.
   Write runs/benchmark-<timestamp>/benchmark.csv and benchmark.md (a markdown table).
3. Also write handcheck.csv: 10 random kept sentences per system per topic, with columns
   system, topic, sentence, passage, url, supported (left blank for a human).
4. If a run fails, record it as failed with the error. Never fill in a number that wasn't
   measured.
```
