"""Offline tests of scripts/run_topic.py with the fake LLM and fake search from test_offline.
Checks: files written on success, events.jsonl + error.txt on failure, one broken source never breaks a run.
Run: python -m tests.test_run_topic"""
import json
import tempfile
from pathlib import Path

from apollo import agents
from apollo.config import Limits
from apollo.llm import AllProvidersFailed
from apollo.pipeline import Apollo
from scripts import run_topic
from tests.test_offline import FakeLLM, fake_search

TOPIC = "Impact of AI on Indian agriculture"


class PlanFailsLLM(FakeLLM):
    def json(self, system, user, **kw):
        raise AllProvidersFailed("All strong models failed: fake")


class UncitedIntroLLM(FakeLLM):
    def json(self, system, user, **kw):
        res = super().json(system, user, **kw)
        if system.startswith("You are the editor"):
            res["introduction"] = "Here we look at many things about farming."   # no citation: verify drops it
        return res


class WriterCrashesLLM(FakeLLM):
    def chat(self, system, user, json_mode=False, **kw):
        if system.startswith("You are a section writer"):
            raise ValueError("boom in the writer")
        return super().chat(system, user, json_mode, **kw)


def broken_arxiv_search(query, source_type, limits):
    if source_type == "arxiv":
        raise AttributeError("'list' object has no attribute 'get'")   # an unexpected, non-SearchError bug
    return fake_search(query, source_type, limits)


def _events(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def _app(llm) -> Apollo:
    return Apollo(Limits(max_rounds=3, parallel_searches=2), llm=llm)


def test_success_writes_all_files(tmp: Path):
    agents.run_search = fake_search
    code, out = run_topic.run(TOPIC, Limits(), runs_dir=tmp, app=_app(FakeLLM()))
    assert code == 0, (out / "error.txt").read_text(encoding="utf-8")
    for name in ("report.md", "events.jsonl", "sources.json", "stats.json", "removed.json"):
        assert (out / name).exists(), name
    removed = json.loads((out / "removed.json").read_text(encoding="utf-8"))
    assert any("UNSUPPORTED" in r["sentence"] for r in removed)
    assert not (out / "error.txt").exists()
    assert "[[1]](" in (out / "report.md").read_text(encoding="utf-8")
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["topic"] == TOPIC and stats["rounds"] == 2
    assert json.loads((out / "sources.json").read_text(encoding="utf-8"))
    kinds = [e["kind"] for e in _events(out)]
    assert kinds[0] == "planning" and kinds[-1] == "done" and "search" in kinds
    assert "-impact-of-ai-on-indian-agriculture" in out.name


def test_dropped_intro_falls_back_to_section_list(tmp: Path):
    agents.run_search = fake_search
    code, out = run_topic.run(TOPIC, Limits(), runs_dir=tmp, app=_app(UncitedIntroLLM()))
    assert code == 0
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "This report covers: Adoption; Policy." in md and "*No introduction.*" not in md
    removed = json.loads((out / "removed.json").read_text(encoding="utf-8"))
    assert any(r["section"] == "Intro/Conclusion" and "many things" in r["sentence"] for r in removed)


def test_plan_failure_saves_events_and_error(tmp: Path):
    code, out = run_topic.run(TOPIC, Limits(), runs_dir=tmp, app=_app(PlanFailsLLM()))
    assert code == 1
    assert "AllProvidersFailed" in (out / "error.txt").read_text(encoding="utf-8")
    assert [e["kind"] for e in _events(out)][-1] == "error"
    assert not (out / "report.md").exists()


def test_mid_run_crash_keeps_events_so_far(tmp: Path):
    agents.run_search = fake_search
    code, out = run_topic.run(TOPIC, Limits(), runs_dir=tmp, app=_app(WriterCrashesLLM()))
    assert code == 1
    assert "boom in the writer" in (out / "error.txt").read_text(encoding="utf-8")
    kinds = [e["kind"] for e in _events(out)]
    assert "search" in kinds and "write" in kinds and kinds[-1] == "error"


def test_one_broken_source_does_not_break_the_run(tmp: Path):
    agents.run_search = broken_arxiv_search
    code, out = run_topic.run(TOPIC, Limits(), runs_dir=tmp, app=_app(FakeLLM()))
    assert code == 0, (out / "error.txt").read_text(encoding="utf-8")
    fails = [e for e in _events(out) if e["kind"] == "search_fail" and "[arxiv]" in e["text"]]
    assert fails and "has no attribute" in fails[0]["text"]
    assert "[[1]](" in (out / "report.md").read_text(encoding="utf-8")


def test_parse_sources():
    assert run_topic.parse_sources("web, News,web") == ("web", "news")
    for bad in ("web,google", ""):
        try:
            run_topic.parse_sources(bad)
        except Exception:
            continue
        raise AssertionError(f"{bad!r} should be rejected")


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for test in (test_success_writes_all_files, test_dropped_intro_falls_back_to_section_list,
                     test_plan_failure_saves_events_and_error,
                     test_mid_run_crash_keeps_events_so_far, test_one_broken_source_does_not_break_the_run):
            test(tmp)
            print(f"ok   {test.__name__}")
    test_parse_sources()
    print("ok   test_parse_sources")
    agents.run_search = fake_search
    print("ALL RUN_TOPIC TESTS PASSED")


if __name__ == "__main__":
    main()
