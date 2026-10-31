"""Offline tests of the demo topic scout: per-topic numbers, recommendations made only from those numbers,
"no topic fits" instead of the closest, and failed runs recorded. Run: python -m tests.test_scout"""
import csv
import sys
import tempfile
from pathlib import Path

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo
from scripts import scout_topics
from tests.test_offline import FakeLLM, fake_search
from tests.test_run_topic import PlanFailsLLM


def _row(topic, **kw):
    row = {"topic": topic, "status": "ok", "rounds": 1, "gaps_closed": 0, "areas": 1, "area_names": "other",
           "domains": 5, "contradictions": 0, "contradictions_both_cited": 0, "unfillable": 0, "covered": 4,
           "subqs": 4, "stop_reason": "complete: 4 covered, 0 partial, 0 unfillable"}
    row.update(kw)
    return row


def test_recommendations_follow_the_rules():
    rows = [_row("A", rounds=2, gaps_closed=2, areas=3, area_names="academic, government, news", domains=10),
            _row("B", rounds=2, gaps_closed=3, areas=3, area_names="academic, government, other", domains=8),
            _row("near miss", rounds=2, gaps_closed=5, areas=2, domains=30),           # only 2 areas: not eligible
            _row("C", contradictions=2, contradictions_both_cited=1),
            _row("D", contradictions=3, contradictions_both_cited=0),                  # found but not both cited
            _row("E", unfillable=1, covered=3), _row("F", unfillable=2, covered=3), _row("G", unfillable=1, covered=1),
            {**_row("broken", rounds=3, gaps_closed=9, areas=5, unfillable=1, covered=4,
                    contradictions_both_cited=4), "status": "failed"}]          # failed runs are never picked
    recs = scout_topics.recommend(rows)
    assert recs["two-round"][0]["topic"] == "B", recs["two-round"]          # most gaps closed among eligible
    assert recs["contradiction"][0]["topic"] == "C"
    assert recs["failure"][0]["topic"] == "E"                               # healthiest run, then fewer unfillable


def test_no_topic_fits_is_said_not_guessed():
    rows = [_row("A", rounds=2, gaps_closed=1, areas=2), _row("B", contradictions=1), _row("C")]
    recs = scout_topics.recommend(rows)
    for role in ("two-round", "contradiction", "failure"):
        chosen, why = recs[role]
        assert chosen is None and why.startswith("No topic fits"), (role, why)


def test_scout_end_to_end_with_a_failed_topic():
    agents.run_search = fake_search
    llms = iter([FakeLLM(), PlanFailsLLM()])
    with tempfile.TemporaryDirectory() as tmp:
        out = scout_topics.run_scout(["Impact of AI on Indian agriculture", "a topic that fails"],
                                     Limits(max_rounds=3, parallel_searches=2), Path(tmp) / "scout",
                                     make_app=lambda limits: Apollo(limits, llm=next(llms)))
        rows = list(csv.DictReader(open(out / "scout.csv", encoding="utf-8")))
        ok, failed = rows
        assert ok["status"] == "ok" and ok["rounds"] == "2" and ok["unfillable"] == "1"   # fake q3 finds nothing
        assert int(ok["gaps_found"]) >= int(ok["gaps_closed"]) >= 1 and "coverage" in ok["gaps_by_type"]
        assert int(ok["contradictions"]) >= 1 and ok["failed_searches"] != "0" and int(ok["domains"]) > 1
        assert ok["areas"] == "1" and ok["area_names"] == "other"       # fake domains are all .example
        assert failed["status"] == "failed" and "AllProvidersFailed" in failed["error"] and failed["rounds"] == ""
        md = (out / "scout.md").read_text(encoding="utf-8")
        assert "| Impact of AI on Indian agriculture | ok | 2 |" in md and "failed: " in md
        assert "**Best two-round topic:** No topic fits" in md          # only 1 area in the fake run
        assert "**Best failure topic:** Impact of AI on Indian agriculture" in md
        assert "## Rules" in md


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_recommendations_follow_the_rules, test_no_topic_fits_is_said_not_guessed,
              test_scout_end_to_end_with_a_failed_topic):
        t()
        print("ok  ", t.__name__)
    print("ALL SCOUT TESTS PASSED")


if __name__ == "__main__":
    main()
