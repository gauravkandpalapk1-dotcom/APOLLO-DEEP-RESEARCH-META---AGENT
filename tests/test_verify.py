"""Offline tests of claim verification: batches of 6, one retry for sentences without a verdict, removal of
anything still unverified, the reasons logged as system events, and the stop summaries.
Run: python -m tests.test_verify"""
import re

from apollo import agents, pipeline
from apollo.config import Limits
from apollo.llm import Budget
from apollo.pipeline import Apollo
from apollo.state import Chunk
from apollo.writer import UNVERIFIED, VERIFY_BATCH, VERIFY_RETRY_BATCH, verify
from tests.test_offline import FakeLLM, fake_search

ALLOWED = {"C1": Chunk("C1", "q1", "https://a.example/1", "t", "Farm fact text.", "web", None, None, 1.0, 1)}


class SkippingVerifier:
    """Answers every item except sentences containing SKIP (never) or FLAKY (only on the second try).
    With fail_first=True its first call raises instead of answering."""
    def __init__(self, fail_first=False):
        self.budget, self.batches, self.seen, self.fail_first = Budget(10**9), [], set(), fail_first

    def json(self, system, user, **kw):
        items = re.findall(r"### Item (\d+)\nSentence: (.*)", user)
        self.batches.append(len(items))
        if self.fail_first and len(self.batches) == 1:
            raise RuntimeError("verifier timed out")
        results = []
        for i, s in items:
            first_time = s not in self.seen
            self.seen.add(s)
            if "SKIP" in s or ("FLAKY" in s and first_time):
                continue
            results.append({"i": int(i), "supported": True})
        return {"results": results}


def _text(n_plain=6):
    sents = [f"Plain fact number {k} [C1]." for k in range(n_plain)]
    return " ".join(sents + ["A FLAKY fact [C1].", "A SKIP fact [C1]."])


def test_skipped_items_are_retried_then_removed():
    llm = SkippingVerifier()
    text, kept, removed, problems = verify(_text(), ALLOWED, llm)
    assert llm.batches[:2] == [VERIFY_BATCH, 2], llm.batches          # 8 sentences -> 6 + 2
    assert all(b <= VERIFY_RETRY_BATCH for b in llm.batches[2:]) and len(llm.batches) == 3, llm.batches
    assert "FLAKY" in text and "SKIP" not in text and kept == 7
    assert removed == [("A SKIP fact [C1].", UNVERIFIED)], removed
    assert len(problems) == 2 and all("verdict(s) for" in p and "reply:" in p for p in problems), problems


def test_failed_call_is_logged_and_retried():
    llm = SkippingVerifier(fail_first=True)
    text, kept, removed, problems = verify(_text(), ALLOWED, llm)
    assert any("RuntimeError: verifier timed out" in p for p in problems), problems
    assert "Plain fact number 0" in text, "the failed batch's sentences get their retry and are kept"
    assert [s for s, why in removed] == ["A SKIP fact [C1]."]


def test_nothing_unverified_is_ever_kept():
    class NeverAnswers(SkippingVerifier):
        def json(self, system, user, **kw):
            super().json(system, user, **kw)
            return {"results": "not a list"}
    text, kept, removed, problems = verify(_text(), ALLOWED, NeverAnswers())
    assert kept == 0 and text == "" and len(removed) == 8 and all(why == UNVERIFIED for _, why in removed)


def test_pipeline_logs_problems_and_counts_unverified():
    class SkipDrones(FakeLLM):                               # the fake verifier never answers "Drones ..." sentences
        def json(self, system, user, **kw):
            res = super().json(system, user, **kw)
            if system.startswith("You check whether"):
                items = dict(re.findall(r"### Item (\d+)\nSentence: (.*)", user))
                res["results"] = [r for r in res["results"] if not items[str(r["i"])].startswith("Drones")]
            return res

    agents.run_search = fake_search
    app = Apollo(Limits(max_rounds=3, parallel_searches=2), llm=SkipDrones())
    events = list(app.research(app.plan("topic")))
    report = events[-1].data["report"]
    n = report.stats["sentences_unverified_removed"]
    assert n > 0 and "Drones evidence point" not in report.markdown
    assert n == sum(r["reason"] == UNVERIFIED for r in report.removed)
    assert any(e.kind == "system" and e.text.startswith("Verifier, ") for e in events)
    assert f"{n} drafted sentence(s) were removed because the verifier could not check them" in report.markdown


def test_no_follow_up_stop_reason_has_counts():
    agents.run_search = fake_search
    original = pipeline.follow_up_jobs
    pipeline.follow_up_jobs = lambda state, gaps: []        # force the "nothing left to search" exit
    try:
        app = Apollo(Limits(max_rounds=3, parallel_searches=2), llm=FakeLLM())
        events = list(app.research(app.plan("topic")))
    finally:
        pipeline.follow_up_jobs = original
    st = events[-1].data["report"].state
    assert re.fullmatch(r"no follow-up queries left: \d+ covered, \d+ partial, \d+ unfillable", st.stop_reason), st.stop_reason
    assert all(sq.status != "open" for sq in st.plan.subqs.values())


def main():
    for t in (test_skipped_items_are_retried_then_removed, test_failed_call_is_logged_and_retried,
              test_nothing_unverified_is_ever_kept, test_pipeline_logs_problems_and_counts_unverified,
              test_no_follow_up_stop_reason_has_counts):
        t()
        print("ok  ", t.__name__)
    print("ALL VERIFY TESTS PASSED")


if __name__ == "__main__":
    main()
