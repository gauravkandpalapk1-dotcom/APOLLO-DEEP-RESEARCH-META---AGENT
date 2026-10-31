# Prompt 2B-1 — actor events, methodology, provenance export
Owner: B · When: H3 · Needs: 1B

```text
Read CLAUDE.md. Work in apollo/state.py, apollo/pipeline.py, apollo/writer.py, app.py
(downloads only) and tests/.

1. Event gets actor: apollo | athena | hermes | writer | oracle | system. Set it on every
   event the pipeline emits.
2. A Methodology section, generated in code (no LLM), placed before Limitations: topic;
   perspectives; number of sections and sub-questions; for each round, queries sent per
   source type, new sources and new domains, gaps by type, Apollo's decision; the stop
   reason; models used per role; sentences kept and removed by verification.
3. Provenance export: one JSON file per run with run_id, settings, plan, per-round
   queries and result URLs, every chunk (id, url, domain, source_class, published, round,
   text), every Hermes review, every decision, every verified sentence, and stats.
   Save it in the run folder and add a "Download provenance JSON" button in app.py.
4. Tests: every event has an actor; the methodology lists every round; the provenance
   JSON loads and every cited chunk id in the report exists in it.
```
