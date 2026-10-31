# Apollo — slide content

Six slides. Facts come from CLAUDE.md, README.md, docs/demo_topics.md and docs/benchmark.md (an exact copy of
runs/benchmark-20261101-033340/benchmark.md); the model chains on slide 2 are from the team's current .env
(scripts/check_keys.py output). Anything to confirm before presenting is marked [CHECK]. Slide text (title and body,
not the "Slide N" label) is at most 40 words, except slide 4 (47 words: its title and table were given as written).

---

## Slide 1 — The problem

**One topic in, one cited report out**

- Who it's for: students and analysts who need a source-backed overview fast.
- Other research agents make the model more autonomous; Apollo makes the research process auditable.

**Speaker notes:** The brief (INNOVISTA, 36 hours) asks for a multi-agent research system under six rules: real data
only, logic in code, sources not memory, always stop, fail safely, trace every claim. The hardest is the last: every
kept factual sentence must cite a passage that supports it. Apollo plans the report, searches live sources, reviews the
evidence for gaps, and removes any sentence its cited passage does not support. Slide 5 shows where each rule lives
in the code.

---

## Slide 2 — Architecture

`PLAN → [SEARCH → REVIEW → ROUTE] × N → WRITE → VERIFY`

- **Apollo**: plans; `route()` decides (`agents.py`)
- **Athena**: parallel search (`search.py`)
- **Hermes**: reviews evidence
- Model failover: each role has a chain (2 NVIDIA keys, Groq, Gemini); if one fails, the next takes over.

**Speaker notes:** Three agents, one loop. Apollo's `apollo_plan()` drafts sections, sub-questions, perspectives and
acceptance criteria; `validate_plan()` checks them in code; `pipeline.py` runs the loop step by step. Athena
(`agents.athena`, `search.py`) runs every sub-question and source in parallel threads: Tavily web and news, arXiv,
SEC EDGAR. Hermes (`agents.hermes`) asks the fast model which passages are relevant, whether each criterion holds and
which figures conflict; `numeric_contradictions()` also compares figures in code. `route()` turns that into gaps and
stops on the round cap, time cap, 70% of the token budget, the search-call cap, or no new domains. Then the writers
(`writer.py`): each section sees only its own passages, `verify()` removes unsupported sentences, `assemble()` adds
introduction, conclusion, methodology, limitations and numbered references. Failover is in `apollo/llm.py`: the strong
and the fast role each try their chain of provider:model entries in order (the current .env has two NVIDIA keys, Groq
and Gemini in both), skipping a bad key, a rate-limited key, a missing model, or a model whose limit is smaller than
the request.

---

## Slide 3 — Live demo

1. **Two rounds**: India's AI market 2030: 11 gaps found, 6 closed in round 2.
2. **Contradiction**: global generative AI market 2030: $213.50 billion vs $128.64 billion, both cited.
3. **Failure**: sources `arxiv, edgar` only → unfillable.

**Speaker notes:** Topic 1 numbers are from the run in docs/benchmark.md
(runs/20261101-015916-forecasts-of-india-s-ai-market-size-by-2030), the same run docs/demo_topics.md describes. The
benchmark counts gaps as (sub-question, gap type) pairs: 11 found in round 1; 6 closed, because q5, q6 and q7 each had
a coverage gap and a criteria gap and all three ended covered after round 2. Show the Trace tab. Start with the plan
editor (tick *Let me review the plan before searching*), then **Approve plan**; switch on *Review gaps between rounds*
to show **Run next round** / **Write report now**. Topic 2: contradictions found vary from run to run. Topic 3: add a
narrow local sub-question (for example drone seed sowing by village panchayats in Pithoragarh district) with sources
`arxiv, edgar`; EDGAR returns nothing and arXiv papers don't answer it, so it appears under "No usable sources found"
in Limitations. EDGAR needs `SEC_USER_AGENT`. [CHECK] The failure demo with `arxiv, edgar` only has not been recorded
in a saved run: rehearse it once. Finish on the Evidence tab (the passages each sentence cites) and the Removed
sentences tab.

---

## Slide 4 — Same verification bar, far more verified evidence

| | Apollo | Baseline |
|---|---|---|
| Claims surviving verification | 84% | 85% |
| Verified sentences (3 topics) | 139 | 42 |
| Distinct websites cited (mean) | 24 | 6 |
| Gaps found / closed | 26 / 9 | not measured |
| Cost (tokens, time) | ~6x | 1x |

3 topics: a direction, not proof.

[CHECK: hand-check numbers]

**Speaker notes:** The baseline is one web search, one strong-model call and the same `verify()`. The baseline won
supported-claim rate on 2 of 3 topics (semiconductors 0.862 vs 0.827, global generative AI 1.0 vs 0.769 on 8 claims);
Apollo won it on India's AI market (0.923 vs 0.692). Only 3 of the 8 flagged contradictions were real, all in the
generative AI topic. Means from benchmark.md: Apollo supported-claim rate 0.84, domains cited 24.333, tokens
94049.333, seconds 179.3; baseline 0.851, 6.0, 14882.0, 29.7. "Verified sentences" sums kept claims (43 + 60 + 36 and
25 + 8 + 9); gaps are (sub-question, gap type) pairs; the baseline has no gap step, so its cell is not measured. The
Apollo numbers come from saved runs made with earlier code (benchmark.md lists the commits made since). The hand check
of sampled sentences is not filled in yet. Full table, copied from benchmark.md:

| Topic | System | Status | Supported-claim rate (kept/drafted) | Unverified removed | Domains cited | Sub-questions covered/planned | Gaps found/closed | Contradictions | Tokens | Seconds |
|---|---|---|---|---|---|---|---|---|---|---|
| India's semiconductor manufacturing push: fabs, subsidies and challenges | apollo | ok | 0.827 (43/52) | 0 | 27 | 5/10 | 10/0 | 2 | 91550 | 242.9 |
| India's semiconductor manufacturing push: fabs, subsidies and challenges | baseline | ok | 0.862 (25/29) | 0 | 7 |  |  |  | 19360 | 37.9 |
| Global generative AI market size forecasts for 2030 | apollo | ok | 0.769 (60/78) | 0 | 29 | 9/11 | 5/3 | 5 | 102992 | 157.1 |
| Global generative AI market size forecasts for 2030 | baseline | ok | 1.0 (8/8) | 0 | 5 |  |  |  | 12215 | 19.3 |
| Forecasts of India's AI market size by 2030 | apollo | ok | 0.923 (36/39) | 0 | 17 | 7/10 | 11/6 | 1 | 87606 | 137.9 |
| Forecasts of India's AI market size by 2030 | baseline | ok | 0.692 (9/13) | 0 | 6 |  |  |  | 13071 | 31.9 |

3 topic(s) is a small sample: the numbers show a direction, not a proven result.

---

## Slide 5 — Rules in code

| Rule | Function | File |
|---|---|---|
| Real data only | `apollo_plan()`, `run_search()` | agents, search |
| Logic in code | `validate_plan()`, `route()` | agents |
| Sources, not memory | `write_section()` | writer |
| Always stop | `route()` | agents |
| Fail safely | `athena()` | agents |
| Trace every claim | `verify()` | writer |

**Speaker notes:** Files are under `apollo/`. More of each rule: `verify()` also backs "sources, not memory";
`LLM.chat()` in `llm.py` enforces the token budget; `write_and_verify()` in `writer.py` and `Apollo.finish()` in
`pipeline.py` also keep runs failing safely. Real data: the plan always comes from a live model or the user's edits
(`PlanError` instead of a default plan) and every result from a live API. Logic in code: `route()` decides gaps,
coverage by distinct domains and every stop. Always stop: round cap, time cap (checked between rounds), 70% of the
token budget, search-call cap, no new domains, per-sub-question attempt cap; `LLM.chat()` raises `BudgetExceeded`.
Fail safely: retries and timeouts, failed and empty searches recorded, one repair attempt for bad JSON, one section
failing doesn't lose the others, `Apollo.finish()` turns a total failure into a report that says why. Trace: a kept
sentence needs a citation to its section's passages and a clear "supported" verdict; `render_citations()` makes the
numbered, clickable references.

---

## Slide 6 — Roadmap and credits

**Next**: hybrid retrieval with embeddings and a reranker · PDF and paper ingestion · LangGraph orchestration with
checkpoints · a larger hand-checked benchmark.

**Credits**: inspired by STORM and MindSearch; built with AI coding assistance (Claude Code).

**Speaker notes:** Today's known limitations (README.md): relevance ranking is keyword overlap, not embeddings; the
time cap is checked between rounds, so a running round and the writing phase can run past it; parallel LLM calls can
go somewhat over the token budget; acceptance criteria and relevance are judged by the fast model, and the verifier is
also a model; the introduction and conclusion are checked against the passages the body cites, not the body's
sentences; Hermes can flag a rounding difference or a different metric as a contradiction; arXiv results are abstracts
only and EDGAR text is windows around the query terms; an edited plan keeps at most 3 sub-questions per section and 6
sections; runs started in the UI are not saved to `runs/`; the PDF can't draw a few characters and leaves URL fragments
when a URL contains parentheses; the estimated cost is 0 unless prices are set. STORM: perspective-guided question
asking and outline-first writing. MindSearch: a planner that splits a question into sub-questions and searches them in
parallel.
