# Build prompts — paste these into Claude Code in order

Each file holds one prompt. Copy only the text inside the ```text block, paste it into
Claude Code, and wait for it to finish and report before the next one.

Rules for every prompt:
- Before accepting a change, ask Claude Code: "Explain what you changed and why, in plain words."
- Run `python -m tests.run_all` (or the "Run tests" task) after every prompt.
- Commit after each prompt passes. If a prompt goes wrong: `git checkout .` and rerun it
  with the error message added.
- One Claude Code session per person, each on its own branch. A merges into main.

| # | File | Owner | When |
|---|------|-------|------|
| 01 | 01_ALL_P0.1_check_keys_and_tour.md | All (one laptop) | H0 |
| 02 | 02_ALL_P0.2_first_live_run.md | All (one laptop) | H0.5 -> Gate 1 |
| 03 | 03_A_P1A_criteria_perspectives.md | A | H1 |
| 04 | 04_B_P1B_domains_classes_cap.md | B | H1 (merge first) |
| 05 | 05_C_P1C_evidence_inspector.md | C | H1 |
| 06 | 06_D_P1D_baseline_benchmark.md | D | H1 |
| 07 | 07_B_P2B1_events_methodology_provenance.md | B | H3 |
| 08 | 08_D_S1_demo_topic_scout.md | D | H4 |
| 09 | 09_A_P2A1_gap_gate_plan_expander.md | A | H4 (push interface early) |
| 10 | 10_B_P2B2_live_hardening.md | B | H5 |
| 11 | 11_C_P2C_plan_graph_gap_ui.md | C | H5 |
| 12 | 12_B_S2_edge_case_tests.md | B | H6 |
| 13 | 13_A_P2A2_cost_meter.md | A | H7 |
| 14 | 14_A_G2_integration_audit.md | A | H8 -> Gate 2 (feature freeze) |
| 15 | 15_D_P3D_benchmark_runs.md | D | H11 |
| 16 | 16_C_P3C_ui_polish.md | C | H11 |
| 17 | 17_A_P3A_readme.md | A | H12 |
| 18 | 18_A_S5_preflight.md | A | H13 -> Gate 3, and H16 |
| 19 | 19_D_S6_slides.md | D | H13 |
| 20 | 20_PRESENTER_S7_mock_judge.md | Presenter | H14+ |
| 21 | 21_A_P4_langgraph_STRETCH.md | A | Only after Gate 3 |

Any time: anytime/S3_debug_an_error.md, anytime/S4_merge_conflict.md, and the three STUDY prompts.

By person (files are numbered in time order, so each person just goes down their list):
- A: 03, 09, 13, 14, 17, 18, (21)
- B: 04, 07, 10, 12
- C: 05, 11, 16
- D: 06, 08, 15, 19
