# Prompt 2A-1 — gap gate refactor and plan expander
Owner: A · When: H4 · Needs: 1A and 1B merged · Push the interface early for C

```text
Read CLAUDE.md. Work in apollo/pipeline.py, apollo/agents.py, apollo/state.py and tests/.

Part 1: split the run into steps, behaviour unchanged.
1. In pipeline.Apollo add:
   start_run(plan) -> RunState
   run_round(state) -> generator of Events; runs one search + review + route and stores
       state.pending_gaps (each gap gets an id) and state.done (True when route says write)
   approve_gaps(state, approved_ids: list[str], edited_queries: dict[str, str],
       write_now: bool = False) -> follow-up jobs for the next round; write_now sets
       state.done and stop_reason "user chose to write"
   finish(state) -> generator of Events ending with the "done" event and the Report
2. research(plan) stays and calls these in sequence, approving all gaps, so the existing
   app and the offline test work unchanged. Push this interface first (stubs are fine)
   and tell C.

Part 2: plan expander.
3. Hermes' review also returns missing_areas: [{section_id, question, why}].
4. route() adds a missing area as a new SubQ only if fewer than Limits.max_added_subqs
   (default 2) were added this run and it isn't a near-duplicate of an existing question
   (word-overlap Jaccard above 0.6). New SubQ: query = question, source_types ["web"],
   added_in_round = current round. Emit an event "Apollo added sub-question ...".
5. Tests: research() output is unchanged for the existing fake; approve_gaps with one gap
   unticked runs only the other; write_now stops after round 1; a missing area is added
   once and a duplicate is rejected.
```
