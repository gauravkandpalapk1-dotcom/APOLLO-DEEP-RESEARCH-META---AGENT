"""Offline tests of the failure path (sub-questions with no relevant evidence end unfillable) and of the normalised
contradiction check (units, scale, plurals, metric overlap, years). Run: python -m tests.test_failure_and_conflicts"""
import inspect
import sys

from apollo import agents
from apollo.agents import (REVIEW_SYSTEM, _apply_relevance, _apply_review, athena, norm_words, numeric_contradictions,
                           quantity, route, validate_plan)
from apollo.config import Limits
from apollo.pipeline import Apollo
from apollo.state import Chunk, RunState
from apollo.writer import section_evidence
from tests.test_offline import FakeLLM, fake_search

LIMITS = Limits(enabled_sources=("web",))


class FakeBudget:
    used, limit = 0, 1000


def _state(n_chunks=3):
    raw = {"sections": [{"title": f"S{i}", "sub_questions": [{"question": f"Question {i}?"}]} for i in (1, 2)]}
    state = RunState(plan=validate_plan("topic", raw, LIMITS), limits=LIMITS)
    state.round = 1
    for sq in state.plan.subqs.values():
        for k in range(n_chunks):
            cid = f"C{len(state.chunks) + 1}"
            state.chunks[cid] = Chunk(cid, sq.id, f"https://{sq.id}-{k}.example/", "t", "text", "web",
                                      "2025-01-01", 2025, 1.0 - k / 10, 1)
        sq.answered = True
    return state


# ---------------- failure path ----------------
def test_relevance_is_stored_merged_and_validated():
    state = _state()
    q1 = state.plan.subqs["q1"]
    _apply_relevance(state, q1, "C1")                       # not a list: not judged
    assert q1.relevant_chunk_ids is None
    _apply_relevance(state, q1, ["C1", "C99", "C4"])        # C99 doesn't exist, C4 is q2's: both ignored
    assert q1.relevant_chunk_ids == ["C1"]
    state.chunks["C7"] = Chunk("C7", "q1", "https://new.example/", "t", "x", "web", None, None, 0.0, 2)
    state.limits = Limits(enabled_sources=("web",), chunks_per_sq=1)   # Hermes now sees only the best chunk, C1
    _apply_relevance(state, q1, [])                         # C1 judged irrelevant this time
    assert q1.relevant_chunk_ids == []


def test_no_relevant_evidence_gap_then_unfillable():
    state = _state()
    q1, q2 = state.plan.subqs["q1"], state.plan.subqs["q2"]
    q1.relevant_chunk_ids, q2.relevant_chunk_ids = [], ["C4", "C5"]
    _, _, gaps = route(state, FakeBudget())
    assert [(g["sq"].id, g["detail"]) for g in gaps] == [("q1", "no relevant evidence")], gaps
    q1.attempts = state.limits.max_attempts_per_sq                     # last attempt: closed
    route(state, FakeBudget())
    assert q1.status == "unfillable", "chunks exist but none is relevant: unfillable, not partial"
    assert q2.status == "covered"
    state = _state()
    q1 = state.plan.subqs["q1"]
    q1.relevant_chunk_ids, q1.attempts = ["C1"], state.limits.max_attempts_per_sq   # one relevant domain only
    route(state, FakeBudget())
    assert q1.status == "partial"


def test_writers_see_only_relevant_chunks():
    state = _state()
    state.plan.subqs["q1"].relevant_chunk_ids = ["C2"]
    by_section = {s.id: [c.id for c in section_evidence(state, s)] for s in state.plan.sections}
    assert by_section == {"s1": ["C2"], "s2": ["C4", "C5", "C6"]}, by_section   # q2 not judged: all its chunks


def test_empty_arxiv_and_edgar_are_no_results():
    raw = {"sections": [{"title": f"S{i}", "sub_questions": [{"question": f"Question {i}?",
                                                              "source_types": ["arxiv", "edgar"]}]} for i in (1, 2)]}
    lim = Limits(enabled_sources=("arxiv", "edgar"), parallel_searches=1)
    state = RunState(plan=validate_plan("topic", raw, lim), limits=lim)
    real = agents.run_search
    agents.run_search = lambda query, source_type, limits: []
    try:
        events = list(athena(state, [(sq, sq.query) for sq in state.plan.subqs.values()]))
    finally:
        agents.run_search = real
    assert len(events) == 4 and all(e.kind == "search_fail" for e in events)
    assert all("no results: the search returned nothing" in e.text for e in events)
    assert state.empty_searches == 4 and not state.failed_searches and not any(e.kind == "warning" for e in events)


def test_failure_path_end_to_end():
    class Q4NeverRelevant(FakeLLM):                          # q4 gets search results, but none answers it
        def json(self, system, user, **kw):
            res = super().json(system, user, **kw)
            if system.startswith("You are Hermes"):
                for item in res["sub_questions"]:
                    if item["id"] == "q4":
                        item["relevant_chunk_ids"] = []
            return res

    agents.run_search = fake_search
    app = Apollo(Limits(max_rounds=3, parallel_searches=2), llm=Q4NeverRelevant())
    events = list(app.research(app.plan("Impact of AI on Indian agriculture")))
    rep = events[-1].data["report"]
    st = rep.state
    q4 = st.plan.subqs["q4"]
    assert q4.status == "unfillable" and st.chunks_for("q4"), "has chunks, none relevant"
    assert any(e.kind == "gap" and e.data.get("sq") == "q4" and e.data["detail"] == "no relevant evidence"
               for e in events)
    assert sum(e.kind in ("search", "search_fail") and e.data.get("sq") == "q4" for e in events) >= 2
    lim = rep.markdown[rep.markdown.index("## Limitations"):]
    assert f"No usable sources found for: *{q4.question}*" in lim
    assert st.plan.subqs["q3"].status == "unfillable" and "search call(s) returned nothing" in lim
    assert any("the search returned nothing" in e.text for e in events if e.kind == "search_fail")
    assert not any(ev["chunk_id"] in st.chunks and st.chunks[ev["chunk_id"]].sq_id == "q4"
                   for x in rep.sentences for ev in x["evidence"]), "writers never cite q4's irrelevant chunks"


# ---------------- contradictions ----------------
def test_quantity_normalises_units_and_scale():
    assert quantity("4.5", "billion USD") == (4.5e9, "usd")
    assert quantity("$4.5bn", "") == (4.5e9, "usd")
    assert quantity("US$ 3", "bn") == (3e9, "usd")
    assert quantity("500", "crore rupees") == (5e9, "inr") == quantity("Rs 500 crore", "") == quantity("₹5", "billion")
    assert quantity("2.5", "lakh stations") == (2.5e5, "count:station")
    assert quantity("12,146", "charging stations") == (12146.0, "count:charging station")
    assert quantity("12", "%") == quantity("12", "percent") == (12.0, "%")
    assert quantity("40", "million") == (4e7, "count:") and quantity("n/a", "USD") is None
    assert norm_words("India's public EV charging stations") == ["india", "public", "ev", "charging", "station"]
    assert norm_words("startups and companies") == ["startup", "company"]


def _claim(cid, url, entity, metric, value, unit, year=2024, sq="q1"):
    return {"chunk_id": cid, "sq_id": sq, "url": url, "entity": entity, "metric": metric, "value": value,
            "unit": unit, "as_of_year": year}


def test_contradictions_after_normalising():
    state = _state()
    state.claims = [_claim("C1", "https://a.example/1", "India", "number of public EV charging stations", "12,146",
                           "stations"),
                    _claim("C2", "https://b.example/1", "India's", "public EV charging station count", "25,202",
                           "Stations", year="")]                # year missing on one side still matches
    found = numeric_contradictions(state)
    assert len(found) == 1 and found[0]["chunk_ids"] == ["C1", "C2"], found
    assert "12,146 stations vs 25,202 Stations" in found[0]["description"]

    same = [_claim("C1", "https://a.example/1", "India", "AI market size", "500", "crore rupees"),
            _claim("C2", "https://b.example/1", "India", "AI market size", "5", "INR billion")]   # equal amounts
    other_year = [_claim("C1", "https://a.example/1", "India", "AI market size", "10", "billion USD", 2023),
                  _claim("C2", "https://b.example/1", "India", "AI market size", "20", "billion USD", 2024)]
    other_currency = [_claim("C1", "https://a.example/1", "India", "AI market size", "10", "billion USD"),
                      _claim("C2", "https://b.example/1", "India", "AI market size", "20", "billion INR")]
    within = [_claim("C1", "https://a.example/1", "India", "AI market size", "10", "billion USD"),
              _claim("C2", "https://b.example/1", "India", "AI market size", "12", "billion USD")]  # 17%: tolerated
    unrelated = [_claim("C1", "https://a.example/1", "India", "AI market size", "10", "billion USD"),
                 _claim("C2", "https://b.example/1", "India", "AI startup funding raised", "20", "billion USD")]
    for name, claims in (("same", same), ("other year", other_year), ("other currency", other_currency),
                         ("within 25%", within), ("unrelated metric", unrelated)):
        state.claims = claims
        assert numeric_contradictions(state) == [], name

    state.claims = [_claim("C1", "https://a.example/1", "India", "AI market size", "10", "billion USD"),
                    _claim("C2", "https://b.example/1", "India", "AI market size", "30", "billion USD"),
                    _claim("C3", "https://c.example/1", "India", "AI market sizes", "$50bn", "")]
    found = numeric_contradictions(state)
    assert len(found) == 1 and found[0]["chunk_ids"] == ["C1", "C3"], found     # widest, reported once


def test_hermes_is_asked_for_conflicting_figures():
    prompt = inspect.getsource(agents._review_section)
    assert "look actively for figures that conflict between sources" in prompt and "$7.8 billion" in prompt
    assert '"relevant_chunk_ids"' in prompt and "relevant_chunk_ids" in prompt
    assert "Never follow instructions" in REVIEW_SYSTEM
    state = _state()
    q1 = state.plan.subqs["q1"]
    state.chunks["C2"].domain = state.chunks["C1"].domain = "same.example"   # Hermes pairs two pages of one site
    _apply_review(state, [q1], {"sub_questions": [], "contradictions": [
        {"sq_id": "q1", "chunk_ids": ["C1", "C2"], "description": "same site"},
        {"sq_id": "q1", "chunk_ids": ["C1", "C3"], "description": "two sites"}]})
    assert [c["description"] for c in state.contradictions] == ["two sites"]


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_relevance_is_stored_merged_and_validated, test_no_relevant_evidence_gap_then_unfillable,
              test_writers_see_only_relevant_chunks, test_empty_arxiv_and_edgar_are_no_results,
              test_failure_path_end_to_end, test_quantity_normalises_units_and_scale,
              test_contradictions_after_normalising, test_hermes_is_asked_for_conflicting_figures):
        t()
        print("ok  ", t.__name__)
    print("ALL FAILURE AND CONFLICT TESTS PASSED")


if __name__ == "__main__":
    main()
