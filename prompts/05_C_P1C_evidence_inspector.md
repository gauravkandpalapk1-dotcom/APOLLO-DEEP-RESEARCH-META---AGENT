# Prompt 1C — evidence inspector
Owner: C · Branch: role-c · When: H1 · Needs: Gate 1

```text
Read CLAUDE.md. Work in apollo/writer.py, apollo/pipeline.py, app.py and tests/.
Goal: a judge clicks any report sentence and sees the exact passage that supports it.

1. verify() returns, besides the clean text, a list of VerifiedSentence records:
   section, text, chunk_ids, verdict ("kept" or "removed"), reason.
   Keep them through dedupe() (mark removed duplicates with reason "duplicate") and
   assemble() (intro and conclusion included).
2. After render_citations(), map chunk ids to reference numbers. Report gets
   sentences: list of {section, text_with_refs, verdict, reason,
   evidence: [{ref_number, url, title, domain, published, passage}]}.
3. New "Evidence" tab in app.py: a section filter; each kept sentence in an expander that
   shows its cited passages with link, domain and date; words shared by the sentence and
   the passage shown in bold (simple word overlap, ignoring stopwords). A toggle
   "Show removed sentences" lists removed ones with their reason.
4. Tests: every kept sentence has at least one evidence item; every removed sentence has
   a reason; no chunk id leaks into text_with_refs.
```
