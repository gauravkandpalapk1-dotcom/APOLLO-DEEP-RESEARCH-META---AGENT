"""Offline tests of the single-shot baseline and the benchmark: files written, the same verification as Apollo,
failed runs recorded with their error and blank numbers, metric definitions, and the hand-check sample.
Run: python -m tests.test_benchmark"""
import csv
import json
import re
import sys
import tempfile
from pathlib import Path

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo
from apollo.search import Doc, SearchError
from apollo.state import Event
from apollo.writer import NO_CLAIM, UNVERIFIED
from scripts import baseline, benchmark, handcheck_summary, run_topic
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
        assert all(n <= benchmark.HANDCHECK_PER_RUN for n in counts.values()) and counts[("baseline", "Farm technology")] == benchmark.HANDCHECK_PER_RUN
        assert not any(c["topic"] == "a broken topic" for c in checks)

        md = (out / "benchmark.md").read_text(encoding="utf-8")
        assert "| Topic | System |" in md and "## Failed runs" in md and "a broken topic" in md
        assert "## How each number is measured" in md


def test_handcheck_sample_is_reproducible():
    sents = [{"verdict": "kept", "reason": "ok", "text_with_refs": f"Fact {k} [[1]](https://a.example/).",
              "evidence": [{"passage": f"p{k}", "url": "https://a.example/"}]} for k in range(30)]
    pick = lambda seed: [r["sentence"] for r in benchmark.handcheck_rows(
        "apollo", "t", sents, benchmark.random.Random(f"{seed}|apollo|t"))]
    assert pick(0) == pick(0) and len(pick(0)) == benchmark.HANDCHECK_PER_RUN and pick(0) != pick(1)
    assert pick(0)[0].startswith("Fact ") and "[[" not in pick(0)[0]


def test_saved_apollo_runs_are_reused_not_rerun():
    agents.run_search, baseline.fetch = fake_search, fake_fetch
    with tempfile.TemporaryDirectory() as tmp:
        live, _ = benchmark.run_apollo("Farm technology", LIMITS, Path(tmp) / "runs",
                                       make_app=lambda limits: Apollo(limits, llm=FakeLLM()))
        saved = Path(live["run_dir"])
        again, _, info = benchmark.saved_apollo(saved)       # the saved files give the same numbers as the live run
        same = [c for c in benchmark.COLUMNS if c not in ("seconds", "error")]
        assert {c: again[c] for c in same} == {c: live[c] for c in same}, (again, live)
        assert info["topic"] == "Farm technology" and info["dir"] == str(saved)

        def never(limits):
            raise AssertionError("Apollo must not run again when its saved run is given")
        out = benchmark.run_benchmark(["ignored topic"], LIMITS, Path(tmp) / "bench", make_app=never,
                                      make_llm=BaselineLLM, apollo_runs=[saved])
        rows = list(csv.DictReader(open(out / "benchmark.csv", encoding="utf-8")))
        assert [(r["topic"], r["system"]) for r in rows] == [("Farm technology", "apollo"),
                                                             ("Farm technology", "baseline")]
        apollo = rows[0]
        stats = json.loads((saved / "stats.json").read_text(encoding="utf-8"))
        assert apollo["status"] == "ok" and apollo["run_dir"] == str(saved)
        assert apollo["kept_claims"] == str(live["kept_claims"]) and int(apollo["tokens"]) == stats["tokens_used"]
        assert float(apollo["seconds"]) == stats["seconds"], "a saved run's seconds come from its stats.json"
        md = (out / "benchmark.md").read_text(encoding="utf-8")
        assert "## Saved Apollo runs" in md and str(saved) in md and "not run again" in md
        assert "1 topic(s) is a small sample: the numbers show a direction, not a proven result." in md
        assert "Rows where the baseline did better: " in md and "Farm technology: tokens (baseline 2500, Apollo" in md
        checks = list(csv.DictReader(open(out / "handcheck.csv", encoding="utf-8")))
        assert {c["system"] for c in checks} == {"apollo", "baseline"}
        assert all(sum(c["system"] == s for c in checks) <= benchmark.HANDCHECK_PER_RUN for s in ("apollo", "baseline"))


def test_baseline_wins_lists_every_better_metric():
    row = lambda system, **kw: {**{c: "" for c in benchmark.COLUMNS}, "topic": "t", "system": system, "status": "ok",
                                **kw}
    rows = [row("apollo", supported_claim_rate="0.8", domains_cited="20", tokens="90000", seconds="200"),
            row("baseline", supported_claim_rate="0.9", domains_cited="8", tokens="9000", seconds="20")]
    assert benchmark.baseline_wins(rows) == ["t: supported-claim rate (baseline 0.9, Apollo 0.8)",
                                             "t: tokens (baseline 9000, Apollo 90000)",
                                             "t: seconds (baseline 20, Apollo 200)"]
    rows[1]["status"] = "failed"
    assert benchmark.baseline_wins(rows) == [], "no comparison with a failed run"


def test_handcheck_summary_counts_only_checked_rows():
    with tempfile.TemporaryDirectory() as tmp:
        bench = Path(tmp) / "benchmark-1"
        bench.mkdir()
        (bench / "benchmark.md").write_text("# Apollo vs single-shot baseline\n\n| table |\n", encoding="utf-8")
        answers = {("apollo", "A"): ["yes", "Y", "no", "", "maybe"], ("baseline", "A"): ["no", "no", "1", "", ""]}
        with open(bench / "handcheck.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=benchmark.HANDCHECK_COLUMNS)
            w.writeheader()
            for (system, topic), values in answers.items():
                for v in values:
                    w.writerow({"system": system, "topic": topic, "sentence": "s", "passage": "p", "url": "u",
                                "supported": v})
        assert handcheck_summary.newest_benchmark(tmp) == bench
        handcheck_summary.append_summary(bench)
        handcheck_summary.append_summary(bench)                  # run again: replaced, not added twice
        md = (bench / "benchmark.md").read_text(encoding="utf-8")
        assert md.count("## Hand check") == 1 and md.startswith("# Apollo vs single-shot baseline")
        assert "| A | apollo | 2/3 | 5 | 2 |" in md and "| A | baseline | 1/3 | 5 | 2 |" in md, md
        assert "**apollo**: 2/3 checked sentences supported (67%)" in md


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_baseline_writes_files_and_verifies, test_baseline_failure_saves_events_and_error,
              test_metric_helpers, test_benchmark_records_failures_and_never_invents_numbers,
              test_handcheck_sample_is_reproducible, test_saved_apollo_runs_are_reused_not_rerun,
              test_baseline_wins_lists_every_better_metric, test_handcheck_summary_counts_only_checked_rows):
        t()
        print("ok  ", t.__name__)
    print("ALL BENCHMARK TESTS PASSED")


if __name__ == "__main__":
    main()
