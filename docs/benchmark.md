# Apollo vs single-shot baseline

3 topic(s), Tavily search depth: advanced, run 2026-11-01 03:35. Apollo: saved runs (not run again, see below); baseline: run now.

| Topic | System | Status | Supported-claim rate (kept/drafted) | Unverified removed | Domains cited | Sub-questions covered/planned | Gaps found/closed | Contradictions | Tokens | Seconds |
|---|---|---|---|---|---|---|---|---|---|---|
| India's semiconductor manufacturing push: fabs, subsidies and challenges | apollo | ok | 0.827 (43/52) | 0 | 27 | 5/10 | 10/0 | 2 | 91550 | 242.9 |
| India's semiconductor manufacturing push: fabs, subsidies and challenges | baseline | ok | 0.862 (25/29) | 0 | 7 |  |  |  | 19360 | 37.9 |
| Global generative AI market size forecasts for 2030 | apollo | ok | 0.769 (60/78) | 0 | 29 | 9/11 | 5/3 | 5 | 102992 | 157.1 |
| Global generative AI market size forecasts for 2030 | baseline | ok | 1.0 (8/8) | 0 | 5 |  |  |  | 12215 | 19.3 |
| Forecasts of India's AI market size by 2030 | apollo | ok | 0.923 (36/39) | 0 | 17 | 7/10 | 11/6 | 1 | 87606 | 137.9 |
| Forecasts of India's AI market size by 2030 | baseline | ok | 0.692 (9/13) | 0 | 6 |  |  |  | 13071 | 31.9 |

3 topic(s) is a small sample: the numbers show a direction, not a proven result. Rows where the baseline did better: India's semiconductor manufacturing push: fabs, subsidies and challenges: supported-claim rate (baseline 0.862, Apollo 0.827); India's semiconductor manufacturing push: fabs, subsidies and challenges: tokens (baseline 19360, Apollo 91550); India's semiconductor manufacturing push: fabs, subsidies and challenges: seconds (baseline 37.9, Apollo 242.9); Global generative AI market size forecasts for 2030: supported-claim rate (baseline 1.0, Apollo 0.769); Global generative AI market size forecasts for 2030: tokens (baseline 12215, Apollo 102992); Global generative AI market size forecasts for 2030: seconds (baseline 19.3, Apollo 157.1); Forecasts of India's AI market size by 2030: tokens (baseline 13071, Apollo 87606); Forecasts of India's AI market size by 2030: seconds (baseline 31.9, Apollo 137.9).

## Means

- **apollo** (mean over 3 successful run(s)): supported-claim rate 0.84, domains cited 24.333, tokens 94049.333, seconds 179.3
- **baseline** (mean over 3 successful run(s)): supported-claim rate 0.851, domains cited 6.0, tokens 14882.0, seconds 29.7

## Failed runs

None.

## Saved Apollo runs

These Apollo runs were made earlier and read from their saved files (their tokens and seconds come from each run's stats.json); the code has changed since. A run may also include changes that were not yet committed when it was made.

- **India's semiconductor manufacturing push: fabs, subsidies and challenges**: `runs\20261101-015039-india-s-semiconductor-manufacturing-push-fabs-subs`, made 2026-11-01 01:55; last commit before it: 0c1ec5b 2026-11-01 01:24 2B-2: failure path by relevance, normalised contradiction check. Committed since (6), not in this run: 4bb4a11 2026-11-01 03:19 G2: integration and rules audit fixes, SEARCH_DEPTH setting; d8f20d1 2026-11-01 02:53 2A-2: cost meter and Models panel; 5f40584 2026-11-01 02:47 2B-2: scout topic list used for the demo topic scout; ad217ec 2026-11-01 02:42 S2: edge-case test suite; a807464 2026-11-01 02:27 2C: plan graph, gap review between rounds, actor log, round trace; 255f3d9 2026-11-01 02:12 2B-2 Part B: live runs, demo topics, verifier and contradiction fixes
- **Global generative AI market size forecasts for 2030**: `runs\20261101-020708-global-generative-ai-market-size-forecasts-for-203`, made 2026-11-01 02:10; last commit before it: 0c1ec5b 2026-11-01 01:24 2B-2: failure path by relevance, normalised contradiction check. Committed since (6), not in this run: 4bb4a11 2026-11-01 03:19 G2: integration and rules audit fixes, SEARCH_DEPTH setting; d8f20d1 2026-11-01 02:53 2A-2: cost meter and Models panel; 5f40584 2026-11-01 02:47 2B-2: scout topic list used for the demo topic scout; ad217ec 2026-11-01 02:42 S2: edge-case test suite; a807464 2026-11-01 02:27 2C: plan graph, gap review between rounds, actor log, round trace; 255f3d9 2026-11-01 02:12 2B-2 Part B: live runs, demo topics, verifier and contradiction fixes
- **Forecasts of India's AI market size by 2030**: `runs\20261101-015916-forecasts-of-india-s-ai-market-size-by-2030`, made 2026-11-01 02:02; last commit before it: 0c1ec5b 2026-11-01 01:24 2B-2: failure path by relevance, normalised contradiction check. Committed since (6), not in this run: 4bb4a11 2026-11-01 03:19 G2: integration and rules audit fixes, SEARCH_DEPTH setting; d8f20d1 2026-11-01 02:53 2A-2: cost meter and Models panel; 5f40584 2026-11-01 02:47 2B-2: scout topic list used for the demo topic scout; ad217ec 2026-11-01 02:42 S2: edge-case test suite; a807464 2026-11-01 02:27 2C: plan graph, gap review between rounds, actor log, round trace; 255f3d9 2026-11-01 02:12 2B-2 Part B: live runs, demo topics, verifier and contradiction fixes

## How each number is measured

- **supported-claim rate** = kept claims / drafted claims. A drafted claim is every sentence the writer
  produced except sentences that only say the sources did not cover something (no factual claim) and sentences
  dropped as near-duplicates of an earlier sentence. Kept claims passed writer.verify(): they cite a retrieved
  passage and the verifier judged that the passage supports them. Sentences the verifier could not check count as
  not kept (they are removed); their number is in *unverified removed*.
- **domains cited**: distinct websites (lowercased host, "www." removed) among the report's references.
- **sub-questions covered / planned** (Apollo only): sub-questions that ended "covered" / sub-questions in the plan.
- **gaps found / closed** (Apollo only): distinct (sub-question, gap type) pairs flagged in any round / those whose
  sub-question ended "covered".
- **contradictions** (Apollo only): conflicting figures flagged by Hermes or by code. The baseline has no step that
  looks for contradictions, so its cell is blank (not measured), not 0.
- **tokens**: all LLM tokens the system used (planning, reviews, writing, verification).
- **seconds**: wall-clock time for the whole run, measured around it.
- Both systems use the same model chains from .env; *strong_model* / *fast_model* show the model that answered last.

Hand check: `handcheck.csv` has up to 5 random kept sentences per system per topic with the passages they cite; fill in the *supported* column (yes/no) by reading them, then run `python -m scripts.handcheck_summary` to add the results to this file.
