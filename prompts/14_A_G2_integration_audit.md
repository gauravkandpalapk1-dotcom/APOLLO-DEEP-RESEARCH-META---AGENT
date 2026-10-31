# Gate 2 — integration and rules audit
Owner: A, on main after all Phase 2 merges · When: H8 (feature freeze)

```text
Read CLAUDE.md. This is a review task: fix only bugs that break a run or a rule.
List everything else instead of fixing it.

1. Run python -m tests.run_all and scripts/run_topic.py on the three demo topics in
   docs/demo_topics.md. Report pass or fail for each.
2. For each rule in CLAUDE.md, name the code that enforces it (file and function) and any
   path that breaks it. Look especially for: hardcoded text that could reach a report;
   any loop without a cap; any exception that can escape a run; any report sentence that
   can skip verify().
3. Check the brief's frontend list in the running app: topic input with settings, live
   progress (plan, searches, reviews, gaps), source viewer with URL and section, report
   with clickable citations and references, Markdown and PDF export. Report what's missing.
4. Check the three bonus goals work in the UI: several source types, parallel searches,
   approval of the plan and of gaps.
5. Output a table: item, status (ok / fixed / open), file, note.
```

If anything is still open, apply the cut lines from the plan doc instead of building around it.
