# Demo topics

Three topics for the live demo, chosen from the topic scout and checked with live runs on 1 November 2026. All runs
used `python -m scripts.run_topic "<topic>" --sources web,news,arxiv` (at most 3 rounds). Every figure below comes
from the run folders named in each section (under `runs/`, which is not committed).

| Topic | Rounds | Stop reason | Gaps found | Contradictions | Failures | Shows |
|---|---|---|---|---|---|---|
| India's semiconductor manufacturing push: fabs, subsidies and challenges | 2 | complete: 5 covered, 5 partial, 0 unfillable | 10 (5 coverage, 5 criteria), none closed | 2, neither a real conflict | 0 failed searches | Honest "partial" results under strict acceptance criteria |
| Global generative AI market size forecasts for 2030 | 2 | complete: 9 covered, 2 partial, 0 unfillable | 5 (2 coverage, 1 criteria, 2 contradiction), 2 closed | 5: 3 real (2030 market size), 2 not | 0 failed searches | Contradiction detection with both figures cited |
| Forecasts of India's AI market size by 2030 | 2 | complete: 7 covered, 3 partial, 0 unfillable | 11 (5 coverage, 5 criteria, 1 contradiction), 3 closed | 1, a rounding difference | 0 failed searches | Gaps found in round 1 and closed in round 2 |

The failure path (a sub-question ending "unfillable") did not occur in these three runs. It did occur in live scout
runs of two of these topics; see [Failure path](#failure-path) below.

## 1. India's semiconductor manufacturing push: fabs, subsidies and challenges

Run: `runs/20261101-015039-india-s-semiconductor-manufacturing-push-fabs-subs`

- **Rounds:** 2 (round 1: 10 queries; round 2: 5 follow-up queries).
- **Stop reason:** complete: 5 covered, 5 partial, 0 unfillable.
- **Gaps found:** 10 on five sub-questions (q2, q6, q8, q9, q10): 5 coverage and 5 unmet-criteria gaps. Examples:
  q2 "no relevant evidence" for executive quotes comparing India's subsidies with the US CHIPS Act; q10 "only 1
  domain(s)" for export controls on lithography tools. All five sub-questions were searched again in round 2 and
  ended partial; none was closed.
- **Contradictions:** 2, both from Hermes, and neither is a real conflict. One said itself "No direct value
  conflict"; the other compared Gujarat's and Assam's incentives (different states). This run predates the fix
  that drops such entries (see [Fixes made during these runs](#fixes-made-during-these-runs)).
- **Failures:** no failed or empty searches. Three fast-model calls hit rate limits and moved to the next model.
- **Output:** 128 sources collected, 32 cited; 42 sentences kept, 9 removed by verification; 4.0 minutes,
  91,550 tokens.
- **Sources searched:** 15 web, 15 news, 0 arXiv. The planner chose arXiv only for technical questions, and judged
  none of the ten sub-questions to be one.
- **What it demonstrates:** acceptance criteria that ask for specific evidence (named executives, state policy
  documents, DVA targets by year), follow-up searches aimed at each gap, and a report that says which criteria are
  still unmet instead of filling them with general text.

## 2. Global generative AI market size forecasts for 2030

Run: `runs/20261101-020708-global-generative-ai-market-size-forecasts-for-203`

- **Rounds:** 2.
- **Stop reason:** complete: 9 covered, 2 partial, 0 unfillable.
- **Gaps found:** 5: q2 "no relevant evidence" plus an unmet criterion (forecasting methodology), q11 "only 1
  domain(s)", and 2 contradiction gaps (q1, q6). q2 and q11 ended covered after round 2.
- **Contradictions:** 5. Three are real and all concern the 2030 market size; both sides of each are cited by
  sentences in the report:
  - nextmsc.com predicts $213.50 billion while bonafideresearch.com predicts $128.64 billion (found by Hermes);
  - forecasts range from $100.5 billion to $213.50 billion across named research firms (Hermes);
  - $129.83 billion vs $213.50 billion (found by the code check on extracted figures).

  The other two compared different metrics (share of "AI leaders" vs share of organisations using generative AI;
  CIO cost concerns vs adoption rates). The filter that drops self-described "different metrics" entries was
  added after this run.
- **Failures:** no failed or empty searches. The verifier skipped one sentence in two batches; the retry checked
  them, and no sentence was removed as unverified.
- **Output:** 114 sources collected, 40 cited; 58 sentences kept, 18 removed by verification; 2.6 minutes,
  102,992 tokens.
- **Sources searched:** 15 web, 10 news, 3 arXiv (the only run here that used arXiv: two technical sub-questions on
  AI cost and architecture).
- **What it demonstrates:** different research firms' 2030 forecasts disagree by up to about 2x, and Apollo finds
  the disagreement, reports both figures with citations, and lists it in Limitations.
- **Note:** this was the third run of this topic. The first run
  (`runs/20261101-015916-global-generative-ai-market-size-forecasts-for-203`) lost 6 sentences to a verifier
  parsing bug. The second (`runs/20261101-020317-…`) found $129.83 billion vs $667.96 billion, but only the first
  figure reached the report text. Both bugs were fixed before the third run.

## 3. Forecasts of India's AI market size by 2030

Run: `runs/20261101-015916-forecasts-of-india-s-ai-market-size-by-2030`

- **Rounds:** 2 (round 2 searched again for 11 gaps).
- **Stop reason:** complete: 7 covered, 3 partial, 0 unfillable.
- **Gaps found:** 11 on six sub-questions: 5 coverage, 5 unmet-criteria, 1 contradiction. q5 (AI revenue of major
  IT firms, "no relevant evidence"), q6 (adoption by vertical, "only 1 domain(s)") and q7 (AI funding totals,
  "only 1 domain(s)") ended covered after round 2. q2, q3 and q8 ended partial.
- **Contradictions:** 1, from Hermes: coingeek.com gives the IndiaAI Mission outlay as Rs 10,000 crore, pib.gov.in
  as ₹10,371.92 crore. This is a rounded figure, not a real conflict (about 4% apart; the code check, which
  tolerates up to 25%, did not flag it). The report states the official ₹10,371.92 crore figure.
- **Failures:** no failed or empty searches. Two fast-model calls hit rate limits and moved to the next model.
- **Output:** 135 sources collected, 25 cited; 36 sentences kept, 3 removed by verification; 2.3 minutes,
  87,606 tokens.
- **Sources searched:** 16 web, 13 news, 0 arXiv.
- **What it demonstrates:** the research loop. Round 1 flags gaps per sub-question, Apollo runs follow-up
  searches for them, and three sub-questions move from gap to covered.

## Failure path

None of the three runs above produced an unfillable sub-question; whether one occurs varies between runs of the
same topic. The failure path did occur in these live scout runs (same code path, `--rounds 2`, web and news):

| Run | Unfillable | Why |
|---|---|---|
| `runs/scout-20261101-015034/runs/20261101-015034-global-generative-ai-market-size-forecasts-for-203` | 1 of 9 (q3: vendors' AI revenue vs the 2030 forecasts) | 4 searches returned 37 passages; none judged relevant |
| `runs/scout-20261101-012459/runs/20261101-012459-forecasts-of-india-s-ai-market-size-by-2030` | 1 of 10 (q8: valuation multiples of Indian GenAI startups) | 4 searches returned 33 passages; none judged relevant |
| `runs/scout-20261101-015034/runs/20261101-015451-drone-based-seed-sowing-programmes-run-by-village-` | 11 of 12 | Each sub-question's 4 searches returned passages (17 to 34 each); none judged relevant |
| `runs/scout-20261101-012459/runs/20261101-013428-how-many-startups-are-there-in-india-estimates-fro` | 10 of 10 | Tavily ran out of credits: 24 searches failed |

In each case the unfillable sub-question:

- ended with status `unfillable` after its last attempt;
- appears in Limitations as "No usable sources found for: *question*";
- shows its searches in the trace (`events.jsonl`), with the "no relevant evidence" gap, or with each failed
  search and a "source unavailable" warning in the outage run;
- has no sentence citing its passages.

The outage run also lists both unavailable sources and "24 search call(s) failed or timed out and were skipped"
in Limitations. In that run the gap read "no relevant evidence" although nothing was found at all; it now reads
"no sources found". No live run had a search that returned nothing, so the "search call(s) returned nothing" line
is covered only by the offline tests.

For a reliable failure demo, the Pithoragarh topic fails almost entirely, which makes the rest of its report thin;
the two market-size topics fail on one sub-question in some runs only.

## Fixes made during these runs

- **Verifier replies with several JSON objects.** The verifier model sometimes sent one verdict object per sentence
  instead of `{"results": [...]}`; those verdicts were lost and checkable sentences were removed as unverified
  (6 in the first generative AI run). Such replies are now read in full.
- **Both sides of a contradiction reach the writer.** A contradiction can pair passages from two sub-questions; the
  second passage was filtered out when Hermes had not marked it relevant to its own sub-question, so the writer
  could not cite it. A section's evidence now always includes both sides of its contradictions.
- **Non-conflicts dropped.** Hermes contradictions whose own description says there is no conflict, or that the
  figures measure different things, are no longer recorded. Descriptions name the website instead of internal
  passage ids and are cut at a word boundary.
- **"No sources found".** A sub-question whose searches all failed or came back empty now gets that reason instead
  of "no relevant evidence".
