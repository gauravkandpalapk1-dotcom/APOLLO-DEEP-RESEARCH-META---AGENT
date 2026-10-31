# Prompt S1 — demo topic scout
Owner: D · When: H4, before B's 2B-2 · First write 8 to 10 candidate topics in scout_topics.txt

```text
Read CLAUDE.md. Create scripts/scout_topics.py; don't change pipeline code.

It takes candidate topics from scout_topics.txt (I'll write 8 to 10), runs each through the
same code path as scripts/run_topic.py with max 3 rounds, and writes scout.md with one row
per topic: rounds used, stop reason, gaps found by type, contradictions found,
unfillable sub-questions, failed searches, sources, distinct domains, minutes, tokens.
Then recommend, from these numbers only:
- the best two-round topic (2+ rounds, gaps closed, at least 3 distinct areas)
- the best contradiction topic (a contradiction with both figures kept in the report)
- the best failure topic (at least one unfillable sub-question, run still finished)
If no topic fits a role, say so instead of picking the closest.
```
