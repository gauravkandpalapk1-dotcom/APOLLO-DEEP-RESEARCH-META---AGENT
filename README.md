# Apollo — deep research meta-agent

Apollo turns one research topic into a cited report. Three agents work in a loop: **Apollo** plans sections and
sub-questions and decides in code when to search again or stop, **Athena** runs live searches in parallel (web,
news, arXiv, SEC EDGAR), and **Hermes** reviews the evidence for gaps and conflicting figures. Section writers
draft only from their own retrieved passages, and a verifier removes every sentence that doesn't cite a passage
supporting it, so each kept claim links to its source.

```
topic → PLAN → [ SEARCH → REVIEW → ROUTE ] × N → WRITE (per section) → VERIFY → ASSEMBLE → report
        (you can edit the plan)   (you can approve gaps between rounds)
```

## Setup

Python 3.11 or newer (developed on 3.14).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then fill it in (below); never commit .env
python -m scripts.check_keys                             # repeat until it prints READY
python -m tests.run_all                                  # offline: no keys or network needed
streamlit run app.py
```

`setup.bat` / `setup.sh` do the first three steps and run the offline tests. In VS Code the same steps are tasks
(*Terminal → Run Task*): **Setup (first time)**, **Check keys**, **Run tests**, **Run Apollo app**.

### `.env`

Copy `.env.example` and fill in at least one LLM provider and one Tavily key:

| Variable | What it is |
|---|---|
| `NVIDIA_API_KEYS`, `GEMINI_API_KEYS`, `GROQ_API_KEYS`, `HF_API_KEYS` | Keys per provider, several allowed, comma-separated. A provider with no key is skipped. The matching `*_BASE_URL` values are already filled in. |
| `STRONG_CHAIN`, `FAST_CHAIN` | Models as `provider:model` entries, tried left to right; when one fails (bad key, rate limit, missing model) the next is used. STRONG = planner, section writers, assembler; FAST = Hermes and the verifier. Entries still holding a placeholder id are skipped with a warning. |
| `TAVILY_API_KEYS` | Tavily keys for web and news search, several allowed, comma-separated (a key that is out of credits is skipped). |
| `SEARCH_DEPTH` | `basic` (default, 1 Tavily credit per search) or `advanced` (2 credits, more relevant snippets). |
| `SEC_USER_AGENT` | Your name and email; needed only for the `edgar` source. |
| `DISABLE_THINKING`, `LLM_MAX_CONCURRENCY` | Ask models to skip "thinking" where supported; how many LLM calls run at once. |
| `PRICE_IN_<PROVIDER>`, `PRICE_OUT_<PROVIDER>` | Optional US dollars per million tokens, for the estimated cost in the app. Default 0 (free tiers). |

`python -m scripts.check_keys` tests every chain entry with every key and every Tavily key (it never prints a key)
and ends with `READY` when at least one strong model, one fast model and one Tavily key work, or `NOT READY` with
what to fix.

### Other commands

- `python -m scripts.run_topic "<topic>" --sources web,news,arxiv --rounds 2` — a run without the UI, saved to
  `runs/<timestamp>-<topic>/` (report, events, sources, stats, provenance JSON).
- `python -m scripts.benchmark` — Apollo vs a single-shot baseline on `benchmark_topics.txt`; `--apollo-runs DIR...`
  reuses saved Apollo runs instead of running Apollo again. `python -m scripts.handcheck_summary` adds the
  hand-check results to the newest `benchmark.md`.
- `python -m scripts.baseline "<topic>"`, `python -m scripts.scout_topics`, `python -m scripts.list_models`.

## Architecture

- **Apollo, planner and controller** — `apollo/agents.py`: `apollo_plan()` asks the strong model for sections,
  sub-questions, perspectives and acceptance criteria, `validate_plan()` checks them in code, and `route()` turns the
  evidence into gaps and decides to search again or stop. `apollo/pipeline.py` (`Apollo`) runs the loop step by step
  (`start_run`, `run_round`, `approve_gaps`, `finish`) and emits the events the UI shows.
- **Athena, searcher** — `apollo/agents.py` `athena()` runs every (sub-question, source) search in parallel threads;
  `apollo/search.py` holds the adapters (Tavily web and news, arXiv, SEC EDGAR) with retries, timeouts and key rotation.
- **Hermes, reviewer** — `apollo/agents.py` `hermes()` / `_review_section()` asks the fast model, section by section,
  which passages are relevant, whether each acceptance criterion holds, which figures conflict;
  `_apply_review()` validates the reply and `numeric_contradictions()` compares extracted figures in code.
- **Writers, verifier, assembler** — `apollo/writer.py`: `write_section()` (each section sees only its own passages),
  `verify()` (citation check in code, then a support check by the fast model), `assemble()` (introduction,
  conclusion, methodology, limitations, numbered references).
- Shared pieces: `apollo/llm.py` (multi-key, multi-model failover, token budget, usage), `apollo/state.py`
  (run state, chunking), `apollo/config.py` (limits, `.env`), `apollo/export.py` (PDF), `app.py` (Streamlit UI).

## How each rule is met

| Rule | Function | File |
|---|---|---|
| Real data only (no pre-written plans, results, reviews or reports) | `apollo_plan()` (live LLM, `PlanError` instead of a default plan), `run_search()` / `tavily()` / `arxiv()` / `edgar()` (live APIs only), `assemble()` (fallback text is meta text, never facts) | `agents.py`, `search.py`, `writer.py` |
| Logic in code | `validate_plan()`, `route()` (gaps, coverage by distinct domains, hard stops), `approve_gaps()`, `numeric_contradictions()` | `agents.py`, `pipeline.py` |
| Sources, not memory | `section_evidence()` + `write_section()` (only that section's passages), `verify()` (removes what the passages don't support) | `writer.py` |
| Always stop | `route()`: round cap, time cap (checked between rounds), 70% of the token budget, search-call cap, "no new domains this round", per-sub-question attempt cap; `LLM.chat()` raises `BudgetExceeded`; `athena()` stops dispatching at the search-call cap | `agents.py`, `llm.py` |
| Fail safely | `_retry()` and per-source timeouts; `athena()` records failed and empty searches; `hermes()` keeps going when a review fails; `LLM.json()` (one repair attempt); `write_and_verify()` (one section failing doesn't lose the others); `Apollo.finish()` turns a total failure into a report that says why | `search.py`, `agents.py`, `llm.py`, `writer.py`, `pipeline.py` |
| Trace every claim | `verify()` / `_check()`: a kept factual sentence must cite a passage of its section and get a clear "supported" verdict; unchecked sentences are removed; `render_citations()` turns citations into numbered, clickable references | `writer.py` |

## Bonus goals in the UI

- **Several source types**: sidebar *Source types* (web, news, arxiv, edgar), and a *sources* column per
  sub-question in the plan editor that accepts any subset (for example only `arxiv, edgar`).
- **Parallel searches**: Athena runs up to 4 searches at once (`Limits.parallel_searches`); the live log shows the
  searches of a round arriving together. The number is not adjustable in the UI.
- **Approval of the plan**: tick *Let me review the plan before searching*; edit, add or delete rows (questions,
  acceptance criteria, queries, sources) next to the plan graph, then press **Approve plan**.
- **Approval of gaps**: switch on *Review gaps between rounds*; after each round untick gaps or edit their queries,
  then press **Run next round** or **Write report now**.

The app also shows a live log labelled by agent, a plan graph coloured by sub-question status, an Evidence tab (the
exact passages each sentence cites), Sources, Trace (one row per round), Removed sentences, a Models panel (active
model per role, model or key switches, tokens, estimated cost, Tavily searches), a Benchmark tab, and downloads
(**Download Markdown**, **Download PDF**, **Download provenance JSON**).

## Demo

From [docs/demo_topics.md](docs/demo_topics.md) (live runs on 1 November 2026, web + news + arXiv):

1. **Two rounds:** *Forecasts of India's AI market size by 2030*. Round 1 flagged 11 gaps; after round 2, three
   sub-questions (q5, q6, q7) went from gap to covered. Show the Trace tab.
2. **Contradiction:** *Global generative AI market size forecasts for 2030*. Research firms' 2030 forecasts
   disagree (for example $213.50 billion vs $128.64 billion), and the report cites both sides. Contradictions found
   vary from run to run.
3. **Failure:** in the plan editor, add a narrow local sub-question (for example drone seed sowing by village
   panchayats in Pithoragarh district) and set its sources to `arxiv, edgar` only. EDGAR full-text search returns
   nothing for it, and arXiv returns papers that don't answer it, so the sub-question ends **unfillable**: it appears
   under "No usable sources found" in Limitations and its searches are in the trace. EDGAR needs `SEC_USER_AGENT`;
   without it those searches are listed as failed instead. Rehearse it once before presenting.

## Results

[docs/benchmark.md](docs/benchmark.md) compares Apollo with a single-shot baseline (one web search, one strong-model
call, the same `verify()`) on the three demo topics. Means over the 3 topics, as written there:

- **apollo**: supported-claim rate 0.84, domains cited 24.333, tokens 94049.333, seconds 179.3
- **baseline**: supported-claim rate 0.851, domains cited 6.0, tokens 14882.0, seconds 29.7

The baseline had the higher supported-claim rate on two of the three topics (0.862 vs 0.827, and 1.0 vs 0.769 on
8 claims); Apollo was higher on the third (0.923 vs 0.692) and cited more websites on all three (27 vs 7, 29 vs 5,
17 vs 6), using more tokens and time (means above). As the file says: 3 topics is a small sample; the numbers show a direction, not a proven
result. The Apollo numbers come from saved runs made with earlier code (listed in the file with the commits made
since), and the hand check of sampled sentences has not been filled in yet.

## Known limitations

- Relevance ranking of passages is keyword overlap (`state.relevance()`), not embeddings.
- The time cap is checked between rounds; a round already running and the writing phase can run past it.
- Parallel LLM calls can go somewhat over the token budget before it stops them.
- Acceptance criteria and passage relevance are judged by the fast model; code validates the reply but not the
  judgement. The verifier is also a model and can be wrong both ways.
- The introduction and conclusion are checked against the passages the body cites, not against the body's sentences.
- Hermes can report a "contradiction" that is really a rounding difference or a different metric; Limitations then
  names only the websites involved.
- arXiv results are abstracts only; EDGAR text is windows around the query terms.
- An edited plan keeps at most 3 sub-questions per section and 6 sections; extra rows are dropped without a warning.
- Runs started in the UI are not saved to `runs/` (`scripts/run_topic.py` saves them); the Benchmark tab reads only
  the local `runs/benchmark-*` folders.
- The PDF export can't draw a few characters (for example the non-breaking hyphen) and leaves URL fragments when a
  URL contains parentheses.
- The estimated cost is 0 unless you set prices; Tavily credits are counted as searches, not credits.

## Credits

Inspired by STORM (Stanford; perspective-guided question asking and outline-first writing) and MindSearch (a
planner that splits a question into sub-questions and searches them in parallel). Built during the INNOVISTA
hackathon with AI coding assistance (Claude Code).
