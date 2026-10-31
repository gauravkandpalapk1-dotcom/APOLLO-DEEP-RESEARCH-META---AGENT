"""Offline tests of the single-shot baseline and the benchmark: files written, the same verification as Apollo,
failed runs recorded with their error and blank numbers, metric definitions, and the hand-check sample.
Run: python -m tests.test_benchmark"""
import csv
import json
import re
import tempfile
from pathlib import Path

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo
from apollo.search import Doc, SearchError
from apollo.state import Event
from apollo.writer import NO_CLAIM, UNVERIFIED
from scripts import baseline, benchmark
from tests.test_offline import FakeLLM, fake_search
from tests.test_run_topic import PlanFailsLLM

LIMITS = Limits(max_rounds=3, parallel_searches=2, enabled_sources=("web",))


class BaselineLLM(FakeLLM):
    """Writes the baseline report: one cited sentence per chunk, a heading, an uncited and an unsupported sentence."""
    def chat(self, system, user, json_mode=False, **kw):
        if system.startswith("You are a research writer"):
            self.budget.add(500)
            ids = re.findall(r'<chunk id="(C\d+)"', user)
            body = " ".join(f"Finding number {k} about farm technology is documented [{cid}]."
                            for k, cid in enumerate(ids))
            return (f"## A heading the prompt said not to write\n\n{body} "
                    f"This sentence has no citation at all. UNSUPPORTED claim the source does not make [{ids[0]}].")
        return super().chat(system, user, json_mode, **kw)


def fake_fetch(topic, limits):
    if "broken" in topic:
        raise SearchError("All Tavily keys failed: fake")
    return [Doc(f"Page {k}", f"https://site{k}.example/p", f"Farm technology data point {k}. " * 80, "web",
                "2025-05-01") for k in range(10)]


def test_baseline_writes_files_and_verifies():
    baseline.fetch = fake_fetch
    with tempfile.TemporaryDirectory() as tmp:
        code, out, result = baseline.run("Farm technology", LIMITS, runs_dir=tmp, llm=BaselineLLM())
        assert code == 0 and result, (out / "error.txt").read_text(encoding="utf-8")
        for name in ("report.md", "events.jsonl", "sources.json", "stats.json", "sentences.json"):
            assert (out / name).exists(), name
        sents = result["sentences"]
        assert len([x for x in sents if x["verdict"] == "kept"]) == 20          # 10 pages x 2 chunks, all cited
        reasons = {x["reason"] for x in sents if x["verdict"] == "removed"}
        assert reasons == {"no valid citation", "not supported by cited source"}, reasons
        assert not any("heading" in x["text_with_refs"] for x in sents), "headings are not sentences"
        assert "[[1]](https://site0.example/p)" in result["markdown"] and "## References" in result["markdown"]
        assert result["stats"]["sources_collected"] == 10 and result["stats"]["chunks"] == 20


def test_baseline_failure_saves_events_and_error():
    baseline.fetch = fake_fetch
    with tempfile.TemporaryDirectory() as tmp:
        code, out, result = baseline.run("a broken topic", LIMITS, runs_dir=tmp, llm=BaselineLLM())
        assert code == 1 and result is None
        assert "SearchError" in (out / "error.txt").read_text(encoding="utf-8")
        events = [json.loads(line) for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert events[-1]["kind"] == "error"


def test_metric_helpers():
    s = lambda verdict, reason, ev=1: {"verdict": verdict, "reason": reason, "evidence": [{}] * ev}
    sents = [s("kept", "supported"), s("kept", "supported"), s("kept", NO_CLAIM, 0), s("removed", "duplicate"),
             s("removed", "no valid citation", 0), s("removed", UNVERIFIED)]
    assert benchmark.claim_counts(sents) == (2, 4, 1)      # no-claim and duplicate sentences are not drafted claims
    ev = lambda data: Event("gap", "", data)
    events = [ev({"sq": "q1", "type": "coverage"}), ev({"sq": "q1", "type": "coverage"}),   # same gap, two rounds
              ev({"sq": "q2", "type": "criteria"}), ev({"sq_id": "q3", "found_by": "code"}), Event("round", "x")]
    assert benchmark.gap_counts(events, {"q1": "covered", "q2": "partial", "q3": "covered"}) == (3, 2)


def test_benchmark_records_failures_and_never_invents_numbers():
    agents.run_search, baseline.fetch = fake_search, fake_fetch
    llms = iter([FakeLLM(), PlanFailsLLM()])               # Apollo works on the first topic, fails on the second
    with tempfile.TemporaryDirectory() as tmp:
        out = benchmark.run_benchmark(["Farm technology", "a broken topic"], LIMITS, Path(tmp) / "bench",
                                      make_app=lambda limits: Apollo(limits, llm=next(llms)), make_llm=BaselineLLM)
        rows = list(csv.DictReader(open(out / "benchmark.csv", encoding="utf-8")))
        by = {(r["system"], r["topic"]): r for r in rows}
        assert len(rows) == 4

        ok = by[("apollo", "Farm technology")]
        assert ok["status"] == "ok" and int(ok["subqs_planned"]) == 4 and ok["contradictions"] != ""
        assert float(ok["supported_claim_rate"]) == round(int(ok["kept_claims"]) / int(ok["drafted_claims"]), 3)
        assert int(ok["gaps_found"]) >= int(ok["gaps_closed"]) > 0 and int(ok["domains_cited"]) > 1

        base = by[("baseline", "Farm technology")]
        assert base["status"] == "ok" and (base["kept_claims"], base["drafted_claims"]) == ("20", "22")
        assert base["subqs_planned"] == base["gaps_found"] == base["contradictions"] == "", "Apollo-only metrics"

        measured = [c for c in benchmark.COLUMNS if c not in ("topic", "system", "status", "error", "run_dir")]
        for key in (("apollo", "a broken topic"), ("baseline", "a broken topic")):
            r = by[key]
            assert r["status"] == "failed" and r["error"], r
            assert all(r[c] == "" for c in measured), {c: r[c] for c in measured if r[c]}   # nothing made up
        assert "AllProvidersFailed" in by[("apollo", "a broken topic")]["error"]
        assert "SearchError" in by[("baseline", "a broken topic")]["error"]

        checks = list(csv.DictReader(open(out / "handcheck.csv", encoding="utf-8")))
        assert checks and all(c["supported"] == "" and c["passage"] and c["url"] for c in checks)
        counts = {}
        for c in checks:
            counts[(c["system"], c["topic"])] = counts.get((c["system"], c["topic"]), 0) + 1
        assert all(n <= benchmark.HANDCHECK_PER_RUN for n in counts.values()) and counts[("baseline", "Farm technology")] == 10
        assert not any(c["topic"] == "a broken topic" for c in checks)

        md = (out / "benchmark.md").read_text(encoding="utf-8")
        assert "| Topic | System |" in md and "## Failed runs" in md and "a broken topic" in md
        assert "## How each number is measured" in md


def test_handcheck_sample_is_reproducible():
    sents = [{"verdict": "kept", "reason": "ok", "text_with_refs": f"Fact {k} [[1]](https://a.example/).",
              "evidence": [{"passage": f"p{k}", "url": "https://a.example/"}]} for k in range(30)]
    pick = lambda seed: [r["sentence"] for r in benchmark.handcheck_rows(
        "apollo", "t", sents, benchmark.random.Random(f"{seed}|apollo|t"))]
    assert pick(0) == pick(0) and len(pick(0)) == 10 and pick(0) != pick(1)
    assert pick(0)[0].startswith("Fact ") and "[[" not in pick(0)[0]


def main():
    for t in (test_baseline_writes_files_and_verifies, test_baseline_failure_saves_events_and_error,
              test_metric_helpers, test_benchmark_records_failures_and_never_invents_numbers,
              test_handcheck_sample_is_reproducible):
        t()
        print("ok  ", t.__name__)
    print("ALL BENCHMARK TESTS PASSED")


if __name__ == "__main__":
    main()
