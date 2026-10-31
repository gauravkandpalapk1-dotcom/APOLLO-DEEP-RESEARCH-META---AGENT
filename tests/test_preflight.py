"""Offline tests of the demo pre-flight check: .env names (never values), the 2-sub-question live run with the fake
LLM and fake search, PDF export, disk checks, archiving old runs, and READY / NOT READY.
Run: python -m tests.test_preflight"""
import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo
from scripts import preflight
from tests.test_offline import FakeLLM, fake_search

GOOD_ENV = {"STRONG_CHAIN": "nvidia:org/strong,gemini:flash", "FAST_CHAIN": "nvidia:org/fast",
            "NVIDIA_API_KEYS": "nv-secret-1", "NVIDIA_BASE_URL": "https://nvidia.example/v1",
            "GEMINI_API_KEYS": "gm-secret-1", "GEMINI_BASE_URL": "https://gemini.example/v1",
            "TAVILY_API_KEYS": "tv-secret-1", "SEC_USER_AGENT": "Test Person test.person@university.ac.in",
            "SEARCH_DEPTH": "basic"}


def _failed(checks):
    return [c.name for c in checks if not c.ok]


def test_env_names_only_and_every_rule():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "docs").mkdir()
        for doc in preflight.DOCS:
            (root / doc).write_text("x", encoding="utf-8")
        assert _failed(preflight.check_env(GOOD_ENV, root)) == []
        assert _failed(preflight.check_env({k: v for k, v in GOOD_ENV.items() if k != "SEARCH_DEPTH"}, root)) == []
        bad = {**GOOD_ENV, "GEMINI_API_KEYS": "", "SEARCH_DEPTH": "advanced", "TAVILY_API_KEYS": "",
               "SEC_USER_AGENT": "Your Name your.email@example.com"}
        assert _failed(preflight.check_env(bad, root)) == [
            "GEMINI_API_KEYS and GEMINI_BASE_URL are set", "TAVILY_API_KEYS is set",
            "SEC_USER_AGENT is set to a real name and email", "SEARCH_DEPTH is basic"]
        (root / "docs" / "benchmark.md").unlink()
        checks = preflight.check_env(GOOD_ENV, root)
        assert _failed(checks) == ["docs/benchmark.md exists"]
        text = " ".join(f"{c.name} {c.detail} {c.fix}" for c in checks)
        assert not any(v in text for v in ("nv-secret-1", "gm-secret-1", "tv-secret-1", "test.person")), \
            "no value is ever shown"


def test_small_plan_keeps_two_sub_questions():
    plan = Apollo(Limits(), llm=FakeLLM()).plan("Impact of AI on Indian agriculture")
    small = preflight.small_plan(plan)
    assert [s.subq_ids for s in small.sections] == [["q1"], ["q3"]] and set(small.subqs) == {"q1", "q3"}
    assert len(plan.subqs) == 4, "the original plan is unchanged"


def test_live_run_and_pdf_with_fakes():
    agents.run_search = fake_search
    with tempfile.TemporaryDirectory() as tmp:
        checks, run_dir = preflight.check_live_run(Path(tmp), make_app=lambda limits: Apollo(limits, llm=FakeLLM()))
        assert _failed(checks) == [], [(c.name, c.detail) for c in checks]
        assert "verified sentence(s)" in checks[2].detail and int(checks[2].detail.split()[0]) >= 1
        pdf = preflight.check_pdf(run_dir)
        assert _failed(pdf) == [] and (run_dir / "report.pdf").read_bytes()[:5] == b"%PDF-"
    assert _failed(preflight.check_pdf(None)) == ["PDF export works"]

    def broken(limits):
        raise RuntimeError("No usable LLM in STRONG_CHAIN")
    checks, run_dir = preflight.check_live_run(Path("unused"), make_app=broken)
    assert run_dir is None and _failed(checks) == ["live run starts"]


def test_disk_checks_and_archiving_old_runs():
    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp) / "runs"
        old, new = runs / "20260101-000000-old", runs / "20261101-000000-new"
        for folder in (old, new):
            folder.mkdir(parents=True)
            (folder / "report.md").write_text("report " * 100, encoding="utf-8")
        week_ago = time.time() - 7 * 86400
        os.utime(old, (week_ago, week_ago))
        checks = preflight.check_disk(Path(tmp), runs)
        assert [c.name for c in checks] == ["free disk space above 1 GB", "runs/ is under 500 MB"] and checks[1].ok
        target, n = preflight.archive_old_runs(runs, days=1)
        assert n == 1 and target.exists() and not old.exists() and new.exists()
        assert preflight.archive_old_runs(runs, days=1) == (None, 0)


def test_show_and_ready_line():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = preflight.show("1. Step", [preflight.Check("a", True, "fine"), preflight.Check("b", False, "", "do X")])
    assert ok is False and "PASS  a (fine)" in buf.getvalue() and "FAIL  b" in buf.getvalue()
    assert "fix: do X" in buf.getvalue()
    tests = preflight.check_tests(modules=("tests.test_list_models",))
    assert _failed(tests) == [], [(c.name, c.detail) for c in tests]


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    real = agents.run_search
    try:
        for t in (test_env_names_only_and_every_rule, test_small_plan_keeps_two_sub_questions,
                  test_live_run_and_pdf_with_fakes, test_disk_checks_and_archiving_old_runs, test_show_and_ready_line):
            t()
            print("ok  ", t.__name__)
    finally:
        agents.run_search = real
    print("ALL PREFLIGHT TESTS PASSED")


if __name__ == "__main__":
    main()
