# Prompt S2 — edge-case test suite
Owner: B · When: H6 · Needs: 2B-1

```text
Read CLAUDE.md. Add tests in tests/test_edge_cases.py using the fake LLM and fake search
style of tests/test_offline.py. Don't change pipeline code unless a test exposes a bug;
if it does, fix it and say what the bug was.

Cover: an empty search; a search that raises; duplicate URLs across sub-questions;
two chunks from one domain; contradictory figures from two domains; an LLM reply that
isn't JSON, then is JSON after repair; an LLM reply that is never valid JSON; a writer
sentence citing a chunk id that doesn't exist; a sentence with no citation; the round
cap; the time cap; the token budget; the search-call cap; a plan with fewer than two
sections. Each test asserts the run finishes and the stop reason or Limitations line
is the expected one.
```
