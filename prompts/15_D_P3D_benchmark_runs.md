# Prompt 3D — benchmark runs
Owner: D · When: H11 · Needs: Gate 2

```text
Read CLAUDE.md. Don't change pipeline code.

1. Put the three demo topics in benchmark_topics.txt and run python -m scripts.benchmark.
2. After the team fills the "supported" column in handcheck.csv by hand, add
   scripts/handcheck_summary.py: hand-checked support out of 10 per system per topic,
   appended to benchmark.md.
3. Add a "Benchmark" tab in app.py that shows the newest benchmark.md, or a note that no
   benchmark has been run yet.
4. Under the table in benchmark.md, add one honest line: 3 topics is a small sample; the
   numbers show a direction, not a proven result. Include any row where the baseline wins.
```
