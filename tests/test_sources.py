"""Offline tests of source independence: domains and source classes, coverage by distinct domains,
contradictions only across domains, the search-call cap and the "no new domains" stop.
Run: python -m tests.test_sources"""
import sys

from apollo import agents
from apollo.agents import REVIEW_SYSTEM, numeric_contradictions, route, validate_plan
from apollo.config import CHUNK_DATA_RULE, Limits
from apollo.pipeline import Apollo
from apollo.search import Doc, classify_source
from apollo.state import Chunk, RunState, domain_of
from apollo.writer import VERIFY_SYSTEM, WRITER_SYSTEM
from tests import test_offline
from tests.test_offline import FakeLLM, fake_search

LIMITS = Limits(enabled_sources=("web",))


class FakeBudget:
    used, limit = 0, 1000


def _state(urls_per_sq: dict[str, list[str]]) -> RunState:
    raw = {"sections": [{"title": f"S{i}", "sub_questions": [{"question": f"Question {i}?"}]} for i in (1, 2)]}
    state = RunState(plan=validate_plan("topic", raw, LIMITS), limits=LIMITS)
    state.round = 1
    for sq_id, urls in urls_per_sq.items():
        for url in urls:
            cid = f"C{len(state.chunks) + 1}"
            state.chunks[cid] = Chunk(cid, sq_id, url, "t", "text", "web", "2025-01-01", 2025, 1.0, 1)
        state.plan.subqs[sq_id].answered = True
    return state


def test_domain_and_classify_source():
    assert domain_of("https://WWW.Example.COM:8080/a?b=1") == "example.com"
    assert domain_of("not a url") == "" and domain_of("http://[bad") == ""
    cases = [
        ("https://pib.gov.in/PressReleasePage.aspx?PRID=1", "web", "government"),
        ("https://agricoop.nic.in/en/schemes", "web", "government"),
        ("https://www.who.int/news/item/1", "web", "government"),
        ("https://www.iitb.ac.in/research/paper.pdf", "web", "academic"),
        ("https://arxiv.org/abs/2401.00001", "arxiv", "academic"),
        ("https://www.sec.gov/Archives/edgar/data/1/0001.htm", "edgar", "filing"),
        ("https://www.sec.gov/news/press-release", "web", "government"),     # sec.gov but not a filing
        ("https://economictimes.indiatimes.com/news/1", "news", "news"),
        ("https://pib.gov.in/x", "news", "news"),                           # anything from the news search
        ("https://notgov.com/a", "web", "other"),                           # ".gov" must be a real suffix
        ("https://fakewho.int/a", "web", "other"),                          # who.int and its subdomains only
        ("https://krishicenter.com/2026/05/04/x", "web", "other"),
    ]
    for url, st, expected in cases:
        assert classify_source(url, st) == expected, (url, st, classify_source(url, st))
    d = Doc("t", "https://www.icar.gov.in/x", "text", "web", None)
    assert (d.domain, d.source_class) == ("icar.gov.in", "government")
    assert Chunk("C1", "q1", "https://www.icar.gov.in/x", "t", "x", "web", None, None, 1.0, 1).domain == "icar.gov.in"


def test_same_domain_does_not_cover():
    state = _state({"q1": ["https://a.example/1", "https://www.a.example/2"],     # one site, two pages
                    "q2": ["https://a.example/1", "https://b.example/1"]})
    _, _, gaps = route(state, FakeBudget())
    assert [(g["sq"].id, g["type"], g["detail"]) for g in gaps] == [("q1", "coverage", "only 1 domain(s)")], gaps
    assert state.plan.subqs["q2"].status == "covered"


def test_contradiction_needs_two_domains():
    state = _state({"q1": ["https://a.example/1", "https://a.example/2", "https://b.example/1"]})
    claim = lambda cid, url, v: {"chunk_id": cid, "sq_id": "q1", "url": url, "entity": "India",
                                 "metric": "farmers using AI", "value": v, "unit": "million", "as_of_year": 2025}
    state.claims = [claim("C1", "https://a.example/1", "10"), claim("C2", "https://a.example/2", "50")]
    assert numeric_contradictions(state) == [], "one site disagreeing with itself is not a contradiction"
    state.claims.append(claim("C3", "https://b.example/1", "12"))
    found = numeric_contradictions(state)
    assert len(found) == 1 and found[0]["chunk_ids"] == ["C3", "C2"], found    # 12 (b) vs 50 (a)


def test_search_cap_stops_the_run():
    agents.run_search = fake_search
    before = len(test_offline.calls)
    app = Apollo(Limits(max_rounds=3, parallel_searches=2, max_search_calls=5), llm=FakeLLM())
    events = list(app.research(app.plan("topic")))           # round 1 needs 6 calls: one is never run
    st = events[-1].data["report"].state
    assert len(test_offline.calls) - before == 5 and st.search_calls == 5
    assert any(e.kind == "search_cap" for e in events)
    assert st.stop_reason.startswith("search-call cap reached"), st.stop_reason
    assert st.round == 1 and events[-1].kind == "done"


def test_no_new_domains_stop():
    state = _state({"q1": ["https://a.example/1"], "q2": ["https://a.example/1", "https://b.example/1"]})
    state.round, state.new_domains_this_round = 2, 0
    decision, reason, _ = route(state, FakeBudget())
    assert decision == "write" and reason.startswith("no new domains this round"), reason


def test_every_prompt_with_retrieved_text_has_the_data_rule():
    for system in (REVIEW_SYSTEM, WRITER_SYSTEM, VERIFY_SYSTEM):
        assert CHUNK_DATA_RULE in system, system


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_domain_and_classify_source, test_same_domain_does_not_cover, test_contradiction_needs_two_domains,
              test_search_cap_stops_the_run, test_no_new_domains_stop, test_every_prompt_with_retrieved_text_has_the_data_rule):
        t()
        print("ok  ", t.__name__)
    print("ALL SOURCES TESTS PASSED")


if __name__ == "__main__":
    main()
