"""Offline tests of the step-by-step run (start_run / run_round / approve_gaps / finish) behind a human gap gate:
research() is unchanged, unticked gaps are not searched, edited queries are used, and "write now" stops.
Run: python -m tests.test_steps"""
import sys

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo
from tests import test_offline
from tests.test_offline import FakeLLM, fake_search


def _app():
    agents.run_search = fake_search
    test_offline.calls.clear()                              # the fake numbers its URLs with this counter
    return Apollo(Limits(max_rounds=3, parallel_searches=1), llm=FakeLLM())   # one thread: deterministic order


def _key(events):
    return [(e.kind, e.actor, e.text) for e in events]


def _round1():
    app = _app()
    state = app.start_run(app.plan("Impact of AI on Indian agriculture"))
    events = list(app.run_round(state))
    return app, state, events


def _round2_searches(app, state):
    return {(e.data["sq"], e.data["query"]) for e in app.run_round(state) if e.kind in ("search", "search_fail")}


def test_research_equals_the_steps_with_every_gap_approved():
    app = _app()
    via_research = list(app.research(app.plan("Impact of AI on Indian agriculture")))
    app = _app()
    state = app.start_run(app.plan("Impact of AI on Indian agriculture"))
    steps = []
    while not state.done:
        steps += list(app.run_round(state))
        if not state.done:
            app.approve_gaps(state, [g["id"] for g in state.pending_gaps], {})
    steps += list(app.finish(state))
    assert _key(via_research) == _key(steps)
    assert via_research[-1].data["report"].markdown == steps[-1].data["report"].markdown


def test_gap_ids_and_proposed_queries():
    app, state, events = _round1()
    gaps = state.pending_gaps
    assert not state.done and state.round == 1 and gaps
    ids = [g["id"] for g in gaps]
    assert len(set(ids)) == len(ids) and all(i.startswith("g1-") for i in ids)
    shown = {e.data["id"]: e.data for e in events if e.kind == "gap" and "id" in e.data}
    assert set(shown) == set(ids) and all(shown[g["id"]]["query"] == g["query"] for g in gaps)
    assert all(not c["chased"] for c in state.contradictions), "showing a proposed query must not chase anything"
    assert {(g["sq"].id, g["type"]) for g in gaps} >= {("q1", "contradiction"), ("q2", "coverage"), ("q4", "criteria")}


def test_unticked_gap_is_not_searched():
    app, state, _ = _round1()
    q2 = [g["id"] for g in state.pending_gaps if g["sq"].id == "q2"]
    keep = [g["id"] for g in state.pending_gaps if g["sq"].id != "q2"]
    jobs = app.approve_gaps(state, keep, {})
    assert q2 and "q2" not in {sq.id for sq, _ in jobs} and {"q1", "q4"} <= {sq.id for sq, _ in jobs}
    searched = {sq for sq, _ in _round2_searches(app, state)}
    assert "q2" not in searched and {"q1", "q4"} <= searched


def test_edited_query_is_used_and_unapproved_contradiction_stays_open():
    app, state, _ = _round1()
    q4 = next(g for g in state.pending_gaps if g["sq"].id == "q4")
    approved = [g["id"] for g in state.pending_gaps if g["sq"].id != "q1"]     # leave q1's contradiction unticked
    jobs = app.approve_gaps(state, approved, {q4["id"]: "  drone budget India 2026 crore  "})
    assert ("q4", "drone budget India 2026 crore") in {(sq.id, q) for sq, q in jobs}
    assert all(not c["chased"] for c in state.contradictions if c["sq_id"] == "q1")
    assert ("q4", "drone budget India 2026 crore") in _round2_searches(app, state)


def test_write_now_stops_after_round_one():
    app, state, _ = _round1()
    assert app.approve_gaps(state, [], {}, write_now=True) == []
    assert state.done and state.stop_reason.startswith("user chose to write"), state.stop_reason
    assert list(app.run_round(state)) == [] and state.round == 1                # no more rounds once done
    assert all(sq.status != "open" for sq in state.plan.subqs.values())
    events = list(app.finish(state))
    rep = events[-1].data["report"]
    assert events[-1].kind == "done" and rep.state.round == 1 and rep.stats["rounds"] == 1
    assert "user chose to write" in rep.markdown and len(state.round_log) == 1


def test_unticking_every_gap_ends_the_search():
    app, state, _ = _round1()
    assert app.approve_gaps(state, [], {}) == []
    assert state.done and state.stop_reason.startswith("no follow-up queries left"), state.stop_reason
    assert app.approve_gaps(state, [g["id"] for g in state.pending_gaps], {}) == []   # nothing to approve once done


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_research_equals_the_steps_with_every_gap_approved, test_gap_ids_and_proposed_queries,
              test_unticked_gap_is_not_searched, test_edited_query_is_used_and_unapproved_contradiction_stays_open,
              test_write_now_stops_after_round_one, test_unticking_every_gap_ends_the_search):
        t()
        print("ok  ", t.__name__)
    print("ALL STEPS TESTS PASSED")


if __name__ == "__main__":
    main()
