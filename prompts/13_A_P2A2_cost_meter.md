# Prompt 2A-2 — cost meter and model status panel
Owner: A · When: H7 · Needs: 2A-1

```text
Read CLAUDE.md. Failover, roles, the concurrency limit and <think> stripping already exist in
apollo/llm.py; don't rewrite them. Work in apollo/config.py, apollo/llm.py (usage tracking
only), app.py and tests/.

1. Track prompt and completion tokens separately, per role and per provider:model.
2. Optional prices in .env.example: PRICE_IN_<PROVIDER> and PRICE_OUT_<PROVIDER> in US
   dollars per million tokens (default 0, since free tiers cost nothing). Estimated cost =
   tokens x price. Add the variables to .env.example with a comment; never open .env.
3. app.py sidebar, a "Models" panel that updates during a run: the active model per role,
   the number of model or key switches, tokens per role, and the estimated cost.
   After a run, the stats bar shows the same numbers.
4. Tests: token counts add up per role; cost is 0 with no prices and correct with prices.
   python -m tests.run_all must pass.
```
