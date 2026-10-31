# Prompt S5 — demo pre-flight check
Owner: A · When: H13, and again at H16

```text
Read CLAUDE.md. Create scripts/preflight.py and run it.

It checks, in this order, and prints PASS / FAIL with a fix for each FAIL:
1. Every .env variable the app needs is set (names only, never values).
2. scripts/check_keys.py passes for strong, fast, fallback and Tavily.
3. The offline test and edge-case tests pass.
4. A short live run (1 round, 2 sections) on a simple topic finishes in under 3 minutes
   and writes a report with at least one verified citation.
5. PDF export of that report works.
6. Free disk space above 1 GB; the runs/ folder is under 500 MB (offer to archive old runs).
End with one line: READY or NOT READY.
```

Gate 3 (H13): demo runs twice in a row without errors, and backup screen recordings of real runs are saved.
