"""Offline edge-case tests: each drives a whole run with the fake LLM and fake search (tests/test_offline.py style)
and checks that it finishes with the expected stop reason or Limitations line. Run: python -m tests.test_edge_cases"""
import json
import sys

from apollo import agents
from apollo.agents import PlanError, plan_from_rows
from apollo.config import Limits
from apollo.llm import LLM, BudgetExceeded
from apollo.pipeline import Apollo
from apollo.search import Doc, SearchError
from tests import test_offline
from tests.test_offline import FakeLLM, fake_search

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
    assert "Sources disagreed: India farmers using AI: 10 million vs 50 million" in lim, lim


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
    # writing then runs out: the run still finishes, with a report that says why it is empty
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


TESTS = (test_empty_search, test_search_that_raises, test_duplicate_urls_across_sub_questions,
         test_two_chunks_from_one_domain, test_contradictory_figures_from_two_domains,
         test_reply_not_json_then_json_after_repair, test_reply_never_valid_json,
         test_sentence_citing_a_chunk_that_does_not_exist, test_sentence_with_no_citation, test_round_cap,
         test_time_cap, test_token_budget, test_search_call_cap, test_plan_with_fewer_than_two_sections)


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
