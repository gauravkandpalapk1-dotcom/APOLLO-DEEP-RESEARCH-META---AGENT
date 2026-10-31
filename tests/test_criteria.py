"""Offline tests of perspectives and acceptance criteria: the planner prompt, plan validation, the plan-editor
round trip, Hermes' verdicts by criterion id, the "criteria" gap in route(), and the guards that skip
malformed LLM items instead of crashing. Run: python -m tests.test_criteria"""
import re
from datetime import datetime

import pandas as pd

from apollo import agents
from apollo.agents import (PlanError, _apply_review, _criteria, _criteria_verdicts, _cut_criterion, _evidence_block,
                           _perspective, apollo_plan, follow_up_jobs, hermes, plan_from_rows, plan_rows, route, validate_plan)
from apollo.config import Limits
from apollo.llm import Budget
from apollo.pipeline import Apollo
from apollo.state import Chunk, RunState
from apollo.writer import limitations
from tests.test_offline import FakeLLM, fake_search

LIMITS = Limits(enabled_sources=("web",))


class FakeBudget:
    used, limit = 0, 1000


class ReturnsLLM:
    """Fake LLM whose json() returns a fixed value and remembers the last prompt."""
    def __init__(self, value):
        self.value, self.budget, self.prompts = value, Budget(10**9), []

    def json(self, system, user, **kw):
        self.prompts.append(user)
        return self.value


def _raw(criteria=None, perspective="farmer"):
    return {"sections": [{"title": f"S{i}", "sub_questions": [
        {"question": f"Question {i}?", "query": f"query {i}", "perspective": perspective,
         "acceptance_criteria": criteria}]} for i in (1, 2)]}


def _state(criteria, n_urls=2):
    plan = validate_plan("topic", _raw(criteria), LIMITS)
    state = RunState(plan=plan, limits=LIMITS)
    state.round = 1
    for sq in plan.subqs.values():
        for k in range(n_urls):
            cid = f"C{len(state.chunks) + 1}"
            state.chunks[cid] = Chunk(cid, sq.id, f"https://{sq.id}-{k}.example/", "t", "text", "web",
                                      "2025-01-01", 2025, 1.0, 1)
        sq.answered = True
    return state


def test_planner_prompt_asks_for_criteria_with_a_relative_year():
    llm = ReturnsLLM(_raw([{"id": "c1", "text": "a named scheme"}]))
    apollo_plan("Impact of AI on Indian agriculture", llm, LIMITS)
    prompt, since = llm.prompts[0], datetime.now().year - 2
    assert "acceptance criteria" in prompt and '"acceptance_criteria": [{"id": "c1", "text":' in prompt
    assert "perspectives" in prompt
    assert f"dated {since} or later" in prompt
    assert set(re.findall(r"\b(?:19|20)\d\d\b", prompt)) == {str(since)}, "no fixed past year in the prompt"
    assert "1 or 2 acceptance criteria" in prompt and "news article" in prompt and "RMSE" in prompt
    assert "Good:" in prompt and "Bad:" in prompt


def test_old_years_in_criteria_are_rewritten():
    since, now = datetime.now().year - 2, datetime.now().year
    texts = lambda raw: [c["text"] for c in _criteria(raw)]
    assert texts([f"an adoption figure dated {since - 3}"]) == [f"an adoption figure dated {since} or later"]
    assert texts([f"the FY{since - 1} budget allocation"]) == [f"the FY{since} or later budget allocation"]
    assert texts([f"a figure dated {since - 3} or later"]) == [f"a figure dated {since - 3} or later"]  # already open-ended
    assert texts([f"a figure dated {since}", f"a target for {now + 4}"]) == [f"a figure dated {since}",
                                                                             f"a target for {now + 4}"]
    assert texts(["at least one named government scheme"]) == ["at least one named government scheme"]
    for rng in ("a trend between 2019 and 2021", "growth over 2019-2021", "figures for 2018 to 2020",
                "yields 2019–2021"):                         # ranges are left alone
        assert texts([rng]) == [rng], rng
    sq = validate_plan("topic", _raw([f"a figure dated {since - 5}"]), LIMITS).subqs["q1"]
    assert sq.acceptance_criteria == [{"id": "c1", "text": f"a figure dated {since} or later"}]


def test_criteria_ids_cap_trim_dedupe_and_junk():
    long = " ".join(f"word{i}" for i in range(60))           # ~400 chars: cut to 200 at a word boundary
    got = _criteria([{"id": "c7", "text": "a named scheme"}, "A named scheme.", None, {"id": "c2"}, 3, ["a"],
                     {"id": "c3", "text": long}, "a third one"])
    assert got == [{"id": "c1", "text": "a named scheme"}, {"id": "c2", "text": _cut_criterion(long)}], got
    # renumbered, dot-duplicate and junk dropped, capped at 2
    assert _criteria(["a figure; in INR"]) == [{"id": "c1", "text": "a figure; in INR"}]
    assert _criteria(None) == [] and _criteria([]) == [] and _criteria([None, {}]) == []   # zero is allowed
    assert [c["text"] for c in _criteria("one; two ;")] == ["one", "two"]
    assert validate_plan("topic", _raw(None), LIMITS).subqs["q1"].acceptance_criteria == []


def test_criterion_cut_at_word_boundary_keeps_or_later():
    words = " ".join(f"word{i}" for i in range(60))
    cut = _cut_criterion(words)
    assert len(cut) <= 200 and words.startswith(cut) and words[len(cut)] == " ", cut   # whole words only
    tail = " dated 2024 or later"
    long = "at least one validated accuracy metric " + "for a specific AI model " * 10 + "from a field trial" + tail
    cut = _cut_criterion(long)
    assert len(cut) <= 200 and cut.endswith(tail), cut                                 # "or later" never cut
    body = cut[:-len(tail)]
    assert long.startswith(body) and long[len(body)] == " ", body
    assert _criteria([long])[0]["text"] == cut
    assert _cut_criterion("x" * 250) == "x" * 200                                     # no spaces: hard cut
    assert _cut_criterion("a figure dated 2024 or later") == "a figure dated 2024 or later"


def test_perspective_cleanup():
    assert _perspective("  Farmer ") == "Farmer"
    for junk in (None, "", "   ", ["farmer"], 3, {"x": 1}):
        assert _perspective(junk) == "general", junk


def test_plan_rows_round_trip_like_the_editor():
    plan = validate_plan("topic", _raw([{"id": "c1", "text": "price per acre; in INR"},
                                        {"id": "c2", "text": "a figure dated 2024 or later"}]), LIMITS)
    rows = plan_rows(plan)
    assert {"perspective", "c1", "c2"} <= set(rows[0]) and "c3" not in rows[0]
    assert (rows[0]["c1"], rows[0]["c2"]) == ("price per acre; in INR", "a figure dated 2024 or later")
    df = pd.DataFrame(rows + [{k: None for k in rows[0]}])  # a blank row the user added in st.data_editor
    again = plan_from_rows("topic", df.fillna("").to_dict("records"), LIMITS)
    for q in plan.subqs:
        assert again.subqs[q].acceptance_criteria == plan.subqs[q].acceptance_criteria
        assert again.subqs[q].perspective == plan.subqs[q].perspective
    rows[0].update(c1="", c2="edited text")                  # user clears c1 and edits c2
    edited = plan_from_rows("topic", rows, LIMITS).subqs["q1"].acceptance_criteria
    assert edited == [{"id": "c1", "text": "edited text"}], edited


def test_hermes_verdicts_by_id():
    state = _state(["a named government scheme", "a figure dated 2024 or later"])
    sq = state.plan.subqs["q1"]
    _apply_review(state, [sq], {"sub_questions": [{"id": "q1", "answered": True, "criteria_met": {
        "c1": True,
        " C2 ": "false",                                    # case, spaces and string booleans are accepted
        "c9": True,                                         # an id Hermes invented: ignored
        "a named government scheme": True}}]})             # criterion text instead of id: ignored
    assert sq.criteria_met == {"c1": True, "c2": False}, sq.criteria_met
    assert _criteria_verdicts(sq, {"c1": "maybe", "c2": 1}) == {}   # not booleans: ignored
    assert _criteria_verdicts(sq, ["c1"]) == {} and _criteria_verdicts(sq, None) == {}


def test_route_criteria_gap_uses_hermes_follow_up():
    state = _state(["a named government scheme", "a figure dated 2024 or later"])
    q1, q2 = state.plan.subqs["q1"], state.plan.subqs["q2"]
    q1.criteria_met = {"c1": True, "c2": False}
    q1.follow_up = "India agri AI adoption figures 2025"
    q2.criteria_met = {"c1": True, "c2": True}
    decision, _, gaps = route(state, FakeBudget())
    assert decision == "search"
    assert [(g["sq"].id, g["type"]) for g in gaps] == [("q1", "criteria")], gaps
    assert gaps[0]["detail"] == "unmet criteria: c2 a figure dated 2024 or later"
    assert q2.status == "covered"
    assert follow_up_jobs(state, gaps) == [(q1, "India agri AI adoption figures 2025")]


def test_missing_verdict_is_unmet_and_zero_criteria_is_fine():
    state = _state(["a named government scheme"])          # Hermes said "answered" but gave no verdicts
    _, _, gaps = route(state, FakeBudget())
    assert {g["type"] for g in gaps} == {"criteria"} and len(gaps) == 2
    state = _state(None)                                     # no criteria at all: only code checks apply
    decision, _, gaps = route(state, FakeBudget())
    assert decision == "write" and not gaps and all(sq.status == "covered" for sq in state.plan.subqs.values())


def test_stop_reason_counts_and_limitations_list_unmet_criteria():
    state = _state(["a named government scheme", "a figure dated 2025 or later"])
    q1, q2 = state.plan.subqs["q1"], state.plan.subqs["q2"]
    q1.criteria_met = {"c1": True, "c2": True}
    q2.criteria_met = {"c1": True, "c2": False}
    q2.attempts = state.limits.max_attempts_per_sq          # out of searches: closed as partial
    decision, reason, _ = route(state, FakeBudget())
    assert (decision, reason) == ("write", "complete: 1 covered, 1 partial, 0 unfillable"), reason
    assert q2.status_reason == "still had gaps after 2 searches, acceptance criteria not met"
    state.stop_reason = reason
    lim = limitations(state, [])
    assert "- Stop condition: complete: 1 covered, 1 partial, 0 unfillable." in lim
    assert "Unmet criteria: a figure dated 2025 or later." in lim and "a named government scheme" not in lim
    assert "acceptance criteria not met" not in lim, "only the unmet criteria, not the status reason"
    q2.criteria_met = {"c1": True, "c2": True}             # no unmet criteria: the reason is shown instead
    q2.status_reason = "still had gaps after 2 searches: only 1 source(s)"
    assert f"*{q2.question}* (still had gaps after 2 searches: only 1 source(s))." in limitations(state, [])

    state = _state(["a named government scheme"])           # hard stop: the reason still carries the counts
    state.limits = Limits(max_rounds=1, enabled_sources=("web",))
    state.plan.subqs["q1"].criteria_met = {"c1": True}
    decision, reason, _ = route(state, FakeBudget())
    assert reason == "round limit (1) reached: 1 covered, 1 partial, 0 unfillable", reason


def test_evidence_block_marks_missing_evidence_and_shows_ids():
    state = _state(["a named government scheme"], n_urls=0)
    block = _evidence_block(state, state.plan.subqs["q1"])
    assert '<criterion sq="q1" id="c1">a named government scheme</criterion>' in block
    assert "(no evidence collected)" in block


def test_planner_junk_items_are_skipped_with_a_warning():
    raw = _raw([{"id": "c1", "text": "a named scheme"}])
    raw["sections"].insert(0, "not a section")
    raw["sections"][1]["sub_questions"].insert(0, "not a sub-question")
    plan = validate_plan("topic", raw, LIMITS)
    assert len(plan.sections) == 2 and len(plan.subqs) == 2 and len(plan.warnings) == 2, plan.warnings
    for bad in (["a list"], {"sections": "text"}, None):
        try:
            apollo_plan("topic", ReturnsLLM(bad), LIMITS)
            raise AssertionError(f"expected PlanError for {bad!r}")
        except PlanError:
            pass

    class JunkPlanLLM(FakeLLM):
        def json(self, system, user, **kw):
            res = super().json(system, user, **kw)
            if system.startswith("You are Apollo"):
                res["sections"].append(42)
            return res

    agents.run_search = fake_search
    app = Apollo(Limits(max_rounds=1, parallel_searches=2), llm=JunkPlanLLM())
    events = list(app.research(app.plan("topic")))
    assert any(e.kind == "warning" and "not an object" in e.text for e in events)
    assert events[-1].kind == "done"


def test_hermes_junk_items_are_skipped_with_a_warning():
    state = _state(["a named government scheme"])
    review = {"sub_questions": ["junk", None, {"id": ["q1"]},
                                {"id": "q1", "answered": True, "criteria_met": {"c1": True}}],
              "claims": ["x", {"chunk_id": ["C1"]}], "contradictions": [None, {"chunk_ids": 5, "sq_id": "q1"}],
              "source_years": ["bad"]}
    events = list(hermes(state, ReturnsLLM(review)))
    assert any(e.kind == "warning" and "2 malformed" in e.text for e in events), [e.text for e in events]
    assert state.plan.subqs["q1"].criteria_met == {"c1": True}
    state = _state(["a named government scheme"])
    events = list(hermes(state, ReturnsLLM(["not", "an", "object"])))
    assert any(e.kind == "warning" and "not a JSON object" in e.text for e in events)


def main():
    for t in (test_planner_prompt_asks_for_criteria_with_a_relative_year, test_old_years_in_criteria_are_rewritten,
              test_criterion_cut_at_word_boundary_keeps_or_later,
              test_criteria_ids_cap_trim_dedupe_and_junk,
              test_perspective_cleanup, test_plan_rows_round_trip_like_the_editor, test_hermes_verdicts_by_id,
              test_route_criteria_gap_uses_hermes_follow_up, test_missing_verdict_is_unmet_and_zero_criteria_is_fine,
              test_stop_reason_counts_and_limitations_list_unmet_criteria,
              test_evidence_block_marks_missing_evidence_and_shows_ids, test_planner_junk_items_are_skipped_with_a_warning,
              test_hermes_junk_items_are_skipped_with_a_warning):
        t()
        print("ok  ", t.__name__)
    print("ALL CRITERIA TESTS PASSED")


if __name__ == "__main__":
    main()
