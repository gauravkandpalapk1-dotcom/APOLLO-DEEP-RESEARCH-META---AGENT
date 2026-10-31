# Apollo — deep research meta-agent

One topic in, one cited report out. Three agents work as one pipeline:

- **Apollo** (manager): plans sections and sub-questions, routes work, decides when to stop.
- **Athena** (searcher): live searches (Tavily web + news, arXiv, SEC EDGAR), run in parallel.
- **Hermes** (reviewer): reads the evidence, flags coverage gaps, stale sources and contradictions.

```
topic → PLAN → [ SEARCH → REVIEW → ROUTE ]×N → WRITE (per section) → VERIFY → ASSEMBLE → report
                 (human can edit the plan)   └ stops on: complete | round cap | time cap | budget | no new sources
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then fill in keys and model chains
python -m scripts.check_keys                             # must end with READY
python -m tests.run_all                             # no keys needed; must print ALL CHECKS PASSED
streamlit run app.py
```

## Where each rule is met

| Rule | Where in the code |
|---|---|
| Logic in code | `agents.route()` decides gaps and stopping; `validate_plan()` checks the plan; `numeric_contradictions()` finds conflicting figures without an LLM. LLMs only extract and judge text. |
| Always stop | `route()`: round cap, wall-clock cap, 70% of token budget, "no new sources", per-question attempt cap. `LLM.chat` raises `BudgetExceeded` at 100%. |
| Fail safely | `search._retry` with backoff and timeouts; Athena catches every `SearchError`; empty results become a logged gap; a question that stays empty is marked *unfillable* and reported honestly. |
| Sources, not memory | Writers see only their section's chunks and are told to say "not covered" instead of filling gaps. |
| Trace every claim | `writer.verify()`: drops sentences with no citation or citations to chunks the section never had (code), then an LLM support check drops sentences their cited chunk doesn't support. Removed sentences are shown in the UI. |
| Two-stage writing | `write_all()` (sections in parallel, own evidence only) → `assemble()` (dedupe, intro, conclusion, limitations, references). |

## Demo plan (test both topics before presenting)

1. **Two-round topic:** a broad topic such as "Impact of AI on Indian agriculture" with max rounds 3. Point at the Trace tab: round-1 gaps, the follow-up queries, the stop condition.
2. **Failure topic:** include a narrow sub-question that returns nothing (edit it into the plan in the review step), or enable `edgar` for a non-US topic. Show the "No usable sources found" line in Limitations and that the run didn't crash.
3. Show the "Removed in verification" tab: this is the proof that claims are traced.

## Tuning (apollo/config.py)

`min_sources_per_sq`, `stale_years`, `chunks_per_sq` and the 25% contradiction tolerance in `numeric_contradictions()` are the knobs that change how many rounds you get. Measure against a baseline (one LLM call over one search) on the same topics: sources cited, sentences removed, rounds, tokens.

## Known limitations

- Relevance ranking is keyword overlap, not embeddings.
- The LLM support check can be wrong both ways; unchecked sentences are counted in the stats.
- arXiv results are abstracts only; EDGAR text is windows around query terms.
