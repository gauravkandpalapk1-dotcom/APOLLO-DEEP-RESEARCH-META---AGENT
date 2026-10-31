"""Offline edge-case tests: each drives a whole run with the fake LLM and fake search (tests/test_offline.py style)
and checks that it finishes with the expected stop reason or Limitations line. Run: python -m tests.test_edge_cases"""
import json
import re
import sys

from apollo import agents
from apollo.agents import PlanError, _apply_review, plan_from_rows
from apollo.config import Limits
from apollo.llm import LLM, AllProvidersFailed, BudgetExceeded
from apollo.pipeline import Apollo
from apollo.search import Doc, SearchError
from apollo.writer import UNVERIFIED, is_limitation, verify
from tests import test_offline
from tests.test_failure_and_conflicts import _state
from tests.test_offline import FakeLLM, fake_search
from tests.test_verify import ALLOWED, SkippingVerifier

TOPIC = "Impact of AI on Indian agriculture"


def _run(limits: Limits | None = None, llm=None, search=fake_search):
    """A whole run; returns (report, events, the Limitations section)."""
    agents.run_search = search
    test_offline.calls.clear()
    app = Apollo(limits or Limits(max_rounds=3, parallel_searches=1), llm=llm or FakeLLM())
    events = list(app.research(app.plan(TOPIC)))
    assert events[-1].kind == "done", "the run must finish with a report"
    rep = events[-1].data["report"]
    md = rep.markdown
    return rep, events, md[md.index("## Limitations"):md.index("## References")] if "## Limitations" in md else ""


def _docs(query, source_type, urls):
    return [Doc(f"Doc {u}", u, f"Farmers AI adoption data for {query}. " * 30, source_type, "2025-03-01") for u in urls]


# ---------------- searches ----------------
def test_empty_search():
    rep, events, lim = _run(search=lambda query, source_type, limits: [])
    assert rep.state.stop_reason == "complete: 0 covered, 0 partial, 4 unfillable", rep.state.stop_reason
    assert lim.count("No usable sources found for") == 4 and "search call(s) returned nothing" in lim
    assert all(e.data["detail"] == "no sources found" for e in events if e.kind == "gap" and e.data.get("type") == "coverage")


def test_search_that_raises():
    def broken(query, source_type, limits):
        raise SearchError("connection reset")
    rep, events, lim = _run(search=broken)
    n = len(rep.state.failed_searches)
    assert n > 0 and f"{n} search call(s) failed or timed out and were skipped" in lim, lim
    assert rep.state.stop_reason == "complete: 0 covered, 0 partial, 4 unfillable", rep.state.stop_reason
    assert any(e.kind == "search_fail" and "connection reset" in e.text for e in events)


def test_duplicate_urls_across_sub_questions():
    shared = ["https://one.example/report", "https://two.example/report"]   # every query returns the same two pages
    rep, events, lim = _run(search=lambda query, source_type, limits: _docs(query, source_type, shared))
    urls = [r["url"] for r in rep.references]
    assert sorted(urls) == shared, f"each page is listed once however many sub-questions found it: {urls}"
    assert rep.state.stop_reason.startswith("complete"), rep.state.stop_reason
    assert rep.markdown.count("](https://one.example/report)") >= 1


def test_two_chunks_from_one_domain():
    def one_site_for_q1(query, source_type, limits):
        if query == "farmers AI adoption" or query.startswith("q1"):          # q1's queries: two pages, one site
            return _docs(query, source_type, ["https://same.example/a", "https://same.example/b"])
        return fake_search(query, source_type, limits)
    rep, events, lim = _run(search=one_site_for_q1)
    q1 = rep.state.plan.subqs["q1"]
    assert q1.status == "partial" and "only 1 domain(s)" in q1.status_reason, (q1.status, q1.status_reason)
    assert f"Incomplete evidence for: *{q1.question}* (still had gaps after 2 searches: only 1 domain(s))" in lim, lim


def test_contradictory_figures_from_two_domains():
    rep, events, lim = _run()                               # the fake Hermes reports 10 vs 50 million for q1
    found = [c for c in rep.state.contradictions if c["found_by"] == "code"]
    assert found and len({rep.state.chunks[c].domain for c in found[0]["chunk_ids"]}) == 2
    domains = ", ".join(dict.fromkeys(rep.state.chunks[c].domain for c in found[0]["chunk_ids"]))
    q1 = rep.state.plan.subqs[found[0]["sq_id"]].question
    assert f"Sources disagreed on *{q1}*: {domains} give different figures (see that section)." in lim, lim
    assert "10 million vs 50 million" not in lim, "the unverified figures stay out of Limitations"


# ---------------- LLM replies ----------------
class RepliesInText(FakeLLM):
    """Answers through chat() as raw text, so LLM.json's parsing and one repair attempt are the real ones.
    bad_replies: how many replies to systems starting with `target` come back as prose instead of JSON."""
    json = LLM.json

    def __init__(self, target: str, bad_replies: int):
        super().__init__()
        self.target, self.bad, self.calls = target, bad_replies, 0

    def chat(self, system, user, json_mode=False, **kw):
        if system.startswith("You are a section writer"):
            return super().chat(system, user, json_mode, **kw)
        if system.startswith(self.target):
            self.calls += 1
            if self.calls <= self.bad:
                self.budget.add(500)
                return "Sure, here is what I found: the plan looks reasonable."
        return json.dumps(FakeLLM.json(self, system, user))


def test_reply_not_json_then_json_after_repair():
    llm = RepliesInText("You are Apollo", bad_replies=1)
    rep, events, lim = _run(llm=llm)
    assert llm.calls == 2, "one bad reply, one repaired reply"
    assert len(rep.state.plan.subqs) == 4
    assert rep.state.stop_reason == "complete: 3 covered, 0 partial, 1 unfillable", rep.state.stop_reason


def test_reply_never_valid_json():
    llm = RepliesInText("You are Hermes", bad_replies=10**6)   # every review is prose, also after the repair
    rep, events, lim = _run(llm=llm)
    assert sum(e.kind == "error" and e.text.startswith("Review of ") for e in events) >= 2
    assert rep.state.stop_reason == "complete: 0 covered, 3 partial, 1 unfillable", rep.state.stop_reason
    assert lim.count("Incomplete evidence for") == 3
    planner = RepliesInText("You are Apollo", bad_replies=10**6)
    agents.run_search = fake_search
    try:
        Apollo(Limits(), llm=planner).plan(TOPIC)
        raise AssertionError("a planner that never returns JSON must raise PlanError")
    except PlanError as e:
        assert str(e).startswith("Could not get a usable plan") and planner.calls == 4   # 2 tries, each repaired once


# ---------------- writer sentences ----------------
def test_sentence_citing_a_chunk_that_does_not_exist():
    rep, events, lim = _run()                              # the fake writer adds "... [C999]." to every section
    bad = [r for r in rep.removed if r["sentence"].startswith("This one cites a chunk the section never had")]
    assert len(bad) == 2 and all(r["reason"] == "no valid citation (cites C999, not in this section's evidence)"
                                 for r in bad), bad
    assert "C999" not in rep.markdown and "drafted sentence(s) were removed in verification" in lim


def test_sentence_with_no_citation():
    rep, events, lim = _run()                              # and "This sentence has no citation ..."
    bad = [r for r in rep.removed if r["sentence"].startswith("This sentence has no citation")]
    assert bad and all(r["reason"] == "no valid citation" for r in bad), bad
    assert "no citation and should be removed" not in rep.markdown
    assert "drafted sentence(s) were removed in verification" in lim


# ---------------- caps ----------------
def test_round_cap():
    rep, events, lim = _run(Limits(max_rounds=1, parallel_searches=1))
    assert rep.state.round == 1 and rep.state.stop_reason.startswith("round limit (1) reached: "), rep.state.stop_reason
    assert "Stop condition: round limit (1) reached" in lim


def test_time_cap():
    rep, events, lim = _run(Limits(max_seconds=0, parallel_searches=1))
    assert rep.state.round == 1 and rep.state.stop_reason.startswith("time limit (0s) reached: "), rep.state.stop_reason
    assert "Stop condition: time limit (0s) reached" in lim


class BudgetedLLM(FakeLLM):
    """Refuses calls once the budget is used up, as the real LLM does."""
    def chat(self, system, user, json_mode=False, **kw):
        self._check()
        return super().chat(system, user, json_mode, **kw)

    def json(self, system, user, **kw):
        self._check()
        return super().json(system, user, **kw)

    def _check(self):
        if self.budget.exceeded:
            raise BudgetExceeded(f"Token budget of {self.budget.limit} used up")


def test_token_budget():
    # 500 tokens per fake call: planner + 2 Hermes reviews = 1,500 by the end of round 1, over 70% of 2,000
    rep, events, lim = _run(Limits(token_budget=2000, parallel_searches=1), llm=BudgetedLLM())
    assert rep.state.round == 1, rep.state.round
    assert rep.state.stop_reason.startswith("research budget used (1500 tokens): "), rep.state.stop_reason
    # writing then runs out part-way: the section written first is kept, the other says why it is missing
    assert "## Adoption" in rep.markdown and "*This section could not be written: the token budget ran out.*" \
        in rep.markdown
    assert "The section *Policy* could not be written: the token budget ran out." in lim, lim
    assert any(e.kind == "system" and "section not written (the token budget ran out)" in e.text for e in events)
    # with no budget left for any section, the run still finishes, with a report that says why it is empty
    rep, events, lim = _run(Limits(token_budget=1500, parallel_searches=1), llm=BudgetedLLM())
    assert any(e.kind == "error" and e.text.startswith("Stopped while writing: Token budget") for e in events)
    assert "The token budget ran out before the report could be written" in rep.markdown


def test_search_call_cap():
    rep, events, lim = _run(Limits(max_search_calls=3, parallel_searches=1))
    assert rep.state.search_calls == 3 and rep.state.search_cap_hit
    assert rep.state.stop_reason.startswith("search-call cap reached: "), rep.state.stop_reason
    assert "Stop condition: search-call cap reached" in lim


# ---------------- plans ----------------
def test_plan_with_fewer_than_two_sections():
    class OneSection(FakeLLM):
        def json(self, system, user, **kw):
            res = super().json(system, user, **kw)
            if system.startswith("You are Apollo"):
                res["sections"] = res["sections"][:1]
            return res
    try:
        Apollo(Limits(), llm=OneSection()).plan(TOPIC)
        raise AssertionError("a one-section plan must be refused")
    except PlanError as e:
        assert "fewer than 2 usable sections" in str(e), e
    try:                                                    # the same rule for a plan edited down in the UI
        plan_from_rows(TOPIC, [{"section": "Only", "question": "Q1?"}, {"section": "Only", "question": "Q2?"}],
                       Limits())
        raise AssertionError("an edited one-section plan must be refused")
    except PlanError as e:
        assert "fewer than 2 usable sections" in str(e), e


# ---------------- Gate 2 audit regressions ----------------
def test_contradiction_with_odd_sq_id_does_not_crash():
    class OddSqIds(FakeLLM):
        def json(self, system, user, **kw):
            res = super().json(system, user, **kw)
            if system.startswith("You are Hermes"):
                ids = re.findall(r'<chunk id="(C\d+)"', user)[:2]
                res["contradictions"] = [{"sq_id": ["q1"], "chunk_ids": ids, "description": "a list"},
                                         {"sq_id": {"id": "q1"}, "chunk_ids": ids, "description": "an object"}]
            return res
    rep, events, lim = _run(llm=OddSqIds())
    assert rep.state.stop_reason.startswith("complete"), rep.state.stop_reason
    assert not any(c["found_by"] == "hermes" for c in rep.state.contradictions)


def test_one_section_failing_keeps_the_others():
    class PolicyWriterDown(FakeLLM):
        def chat(self, system, user, json_mode=False, **kw):
            if system.startswith("You are a section writer") and "Section: Policy" in user:
                raise AllProvidersFailed("All strong models failed: every key is dead or cooling down")
            return super().chat(system, user, json_mode, **kw)
    rep, events, lim = _run(llm=PolicyWriterDown())
    assert "## Adoption" in rep.markdown and "evidence point" in rep.markdown, "the other section is kept"
    assert "*This section could not be written: no language model was reachable.*" in rep.markdown
    assert "The section *Policy* could not be written: no language model was reachable." in lim
    assert not any(e.kind == "error" for e in events)


def test_limitation_sentences_without_citation_hold_no_figures():
    assert is_limitation("The sources did not cover drone sowing in Pithoragarh.")
    assert not is_limitation("The sources show limited adoption of 12% in 2024.")      # a figure: needs a citation
    assert not is_limitation("The data show limited adoption among farmers.")          # "limited" is a claim
    text = ("The sources did not cover drone sowing in Pithoragarh. The data show limited adoption of 12% in 2024. "
            "No data covers the scheme in district X [C999].")                         # its fake citation is stripped
    clean, records, _ = verify(text, ALLOWED, SkippingVerifier())
    assert clean == "The sources did not cover drone sowing in Pithoragarh.", clean
    assert [r.reason.startswith("no valid citation") for r in records if r.verdict == "removed"] == [True, True]


def test_verifier_keeps_only_a_clear_yes():
    class OddVerdicts:
        budget = None

        def json(self, system, user, **kw):                  # the same odd verdict for a sentence on every try
            odd = {"0": {"supported": "false"}, "1": {"supported": "true"}, "2": {}, "3": {"supported": "maybe"},
                   "4": {"supported": True}}
            items = re.findall(r"### Item (\d+)\nSentence: Fact number (\d)", user)
            return {"results": [{"i": int(i), **odd[n]} for i, n in items]}
    text = " ".join(f"Fact number {k} [C1]." for k in range(5))
    clean, records, _ = verify(text, ALLOWED, OddVerdicts())
    got = {r.text.split(" [")[0]: (r.verdict, r.reason) for r in records}
    assert got["Fact number 0"][0] == "removed" and got["Fact number 0"][1] != UNVERIFIED     # "false" drops it
    assert got["Fact number 1"][0] == "kept" and got["Fact number 4"][0] == "kept"
    assert got["Fact number 2"] == ("removed", UNVERIFIED) and got["Fact number 3"] == ("removed", UNVERIFIED)


def test_limitations_never_quote_the_reviewer():
    class Chatty(FakeLLM):                                   # Hermes' free text includes an unverified figure
        def json(self, system, user, **kw):
            res = super().json(system, user, **kw)
            if system.startswith("You are Hermes"):
                for item in res["sub_questions"]:
                    item["answered"], item["missing"] = False, "Experts say 73% of farmers use AI already"
            return res
    rep, events, lim = _run(llm=Chatty())
    assert "73%" not in lim and "Experts say" not in lim, lim
    assert "the reviewer found the evidence incomplete" in lim, lim


def test_failed_introduction_leaves_no_error_text():
    class EditorDown(FakeLLM):
        def json(self, system, user, **kw):
            if system.startswith("You are the editor"):
                raise ValueError("No JSON object in model output: 'Our secret figure is 99%'")
            return super().json(system, user, **kw)
    rep, events, lim = _run(llm=EditorDown())
    assert "99%" not in rep.markdown and "could not be generated" not in rep.markdown
    assert "This report covers: Adoption; Policy." in rep.markdown


def test_quoted_false_is_not_answered():
    state = _state()
    q1, q2 = state.plan.subqs["q1"], state.plan.subqs["q2"]
    _apply_review(state, [q1, q2], {"sub_questions": [{"id": "q1", "answered": "false"},
                                                      {"id": "q2", "answered": "true"}]})
    assert q1.answered is False and q2.answered is True


def test_mixed_citation_with_a_bad_id_keeps_the_good_one():
    clean, records, _ = verify("Farm fact one [C1, C99]. Farm fact two [C99, C1].", ALLOWED, SkippingVerifier())
    assert clean == "Farm fact one [C1]. Farm fact two [C1].", clean
    assert all(r.verdict == "kept" and r.chunk_ids == ["C1"] for r in records), records


def test_lone_surrogate_in_web_text_is_written_safely():
    doc = Doc("Title \ud83d", "https://x.example/a", "Text cut mid emoji \ud83d here", "web", None)
    json.dumps(doc.__dict__, ensure_ascii=False).encode("utf-8")       # raised UnicodeEncodeError before the fix
    assert "\ud83d" not in doc.text and doc.text.startswith("Text cut mid emoji")


TESTS = (test_empty_search, test_search_that_raises, test_duplicate_urls_across_sub_questions,
         test_two_chunks_from_one_domain, test_contradictory_figures_from_two_domains,
         test_reply_not_json_then_json_after_repair, test_reply_never_valid_json,
         test_sentence_citing_a_chunk_that_does_not_exist, test_sentence_with_no_citation, test_round_cap,
         test_time_cap, test_token_budget, test_search_call_cap, test_plan_with_fewer_than_two_sections,
         test_contradiction_with_odd_sq_id_does_not_crash, test_one_section_failing_keeps_the_others,
         test_limitation_sentences_without_citation_hold_no_figures, test_verifier_keeps_only_a_clear_yes,
         test_limitations_never_quote_the_reviewer, test_failed_introduction_leaves_no_error_text,
         test_quoted_false_is_not_answered, test_mixed_citation_with_a_bad_id_keeps_the_good_one,
         test_lone_surrogate_in_web_text_is_written_safely)


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    real = agents.run_search
    try:
        for t in TESTS:
            t()
            print("ok  ", t.__name__)
    finally:
        agents.run_search = real
    print("ALL EDGE CASE TESTS PASSED")


if __name__ == "__main__":
    main()
