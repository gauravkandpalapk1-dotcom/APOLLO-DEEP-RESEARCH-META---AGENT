# Prompt 4 — LangGraph wrapper (STRETCH)
Owner: A · Branch: langgraph · When: only after Gate 3 passed and backups are recorded

```text
Read CLAUDE.md. Create a new branch. Add apollo/graph.py only; don't change existing
functions or their behaviour.

1. Build a LangGraph StateGraph whose nodes call the existing functions:
   plan -> search (athena) -> review (hermes) -> route, with a conditional edge from route:
   "search" goes back to search, "write" goes to write -> verify -> assemble -> END.
   The decision comes from the existing route(); no new logic in the graph.
2. Pin the langgraph version in requirements.txt. Use the in-memory checkpointer only.
3. A setting ORCHESTRATOR=python|langgraph chooses the runner; default python.
4. Stream the same Events to the UI, so app.py doesn't change.
5. Merge only if: the offline test passes in both modes, two live runs in langgraph mode
   match python mode (same stop reasons, similar source counts), and nothing in the
   default path changed.
```
