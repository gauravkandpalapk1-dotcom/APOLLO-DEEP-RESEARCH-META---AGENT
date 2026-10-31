# Prompt 2C — plan graph, gap review UI, actor log
Owner: C · When: H5 · Needs: 1C, and A's 2A-1 interface pushed

```text
Read CLAUDE.md. Work in app.py only. Use A's start_run / run_round / approve_gaps /
finish interface; don't change pipeline code.

1. Plan graph with st.graphviz_chart (DOT text): topic -> sections -> sub-questions.
   Node colour by status: open grey, covered green, partial amber, unfillable red; a
   dashed border for sub-questions added in later rounds. Add a small legend. Show it
   beside the plan editor and redraw it after every round during a run.
2. Live log: prefix each line with the event's actor (Apollo, Athena, Hermes, Writer,
   Oracle), each with its own colour.
3. Gap review (sidebar toggle "Review gaps between rounds"): Streamlit reruns the script
   on every click, so run ONE round per click. Keep RunState in st.session_state. After a
   round, show the gaps in st.data_editor (approve checkbox, type, sub-question, detail,
   editable query) with buttons "Run next round" and "Write report now".
   With the toggle off, run all rounds in one go as today.
4. Trace tab: one row per round: queries sent, new domains, gaps by type, decision, reason.
5. Check by hand: a full run with the toggle on and off; refreshing mid-run must not crash.
```
