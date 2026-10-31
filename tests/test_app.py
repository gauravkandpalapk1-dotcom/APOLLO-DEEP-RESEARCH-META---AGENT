"""Headless tests of the Streamlit UI with the fake LLM and fake search: plan graph, actor-labelled log, gap review
one round per click, the Trace tab, and a rerun in the middle of a run. Run: python -m tests.test_app"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

from apollo import agents, pipeline
from tests.test_offline import FakeLLM, fake_search

APP = str(Path(__file__).resolve().parent.parent / "app.py")
TOPIC = "Impact of AI on Indian agriculture"
REAL_LLM, REAL_SEARCH = pipeline.LLM, agents.run_search


class UIFakeLLM(FakeLLM):
    warnings = []                                            # the app shows the real LLM's key warnings

    def status(self):                                        # what the real LLM reports, for the Models panel
        used = self.budget.used
        return {"active": {"strong": "fake:strong-model #1", "fast": "fake:fast-model #1"}, "switches": 2,
                "usage": {"strong": {"calls": 3, "prompt_tokens": used // 2, "completion_tokens": used // 4,
                                     "tokens": used // 2 + used // 4},
                          "fast": {"calls": 1, "prompt_tokens": used // 4, "completion_tokens": 0,
                                   "tokens": used // 4}},
                "usage_by_model": {}, "cost_usd": 0.0, "priced": False, "warnings": []}


def _app(review_gaps: bool) -> AppTest:
    agents.run_search = fake_search
    pipeline.LLM = lambda budget: UIFakeLLM()
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.slider[0].set_value(3)                                    # maximum rounds
    at.multiselect[0].set_value(["web", "news", "arxiv", "edgar"])
    [t for t in at.toggle if t.label == "Review gaps between rounds"][0].set_value(review_gaps)
    at.text_input[0].input(TOPIC).run()
    _click(at, "Plan research")
    return at


def _click(at: AppTest, label: str):
    [b for b in at.button if b.label == label][0].click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _graphs(at: AppTest) -> list[str]:
    return [g.proto.spec for g in at.get("graphviz_chart")]


def _markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def _sidebar(at: AppTest) -> str:
    return "\n".join([m.value for m in at.sidebar.markdown] + [c.value for c in at.sidebar.caption])


def test_models_panel_live_and_after_the_run():
    agents.run_search = fake_search
    pipeline.LLM = lambda budget: UIFakeLLM()
    assert "No run yet." in _sidebar(AppTest.from_file(APP, default_timeout=60).run())
    at = _app(review_gaps=True)
    _click(at, "Approve plan and start research")              # paused after round 1: the panel is live
    side = _sidebar(at)
    state = at.session_state["run"]
    tavily = sum(x["source"] in ("web", "news") and x["status"] != "failed" for x in state.search_log)
    assert tavily > 0 and f"Tavily searches this run: **{tavily}**" in side, side
    assert "**Strong:** `fake:strong-model #1`" in side and "**Fast:** `fake:fast-model #1`" in side
    assert "Model or key switches: **2**" in side and "(no prices set)" in side and "0.0000" in side
    assert " in, " in side and " out)" in side                  # prompt and completion tokens shown
    _click(at, "Write report now")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Model or key switches"] == "2" and metrics["Estimated cost"] == "$0.0000", metrics
    assert {"Strong tokens", "Fast tokens", "Tavily searches"} <= set(metrics), metrics
    assert "Tavily searches this run" in _sidebar(at), "the panel stays after the run"


def test_plan_review_shows_graph_and_editor():
    at = _app(review_gaps=False)
    graphs = _graphs(at)
    assert len(graphs) == 1 and "digraph plan" in graphs[0] and "topic -> s1" in graphs[0], graphs
    assert graphs[0].count('fillcolor="#d9d9d9"') == 4, "every sub-question starts open (grey)"
    assert any("open" in c.value and "unfillable" in c.value for c in at.caption), "legend shown"
    assert at.session_state["run"] is None and at.session_state["phase"] is None


def test_plan_editor_accepts_any_source_subset():
    at = _app(review_gaps=True)
    at.session_state["plan_editor"] = {"edited_rows": {0: {"sources": ["arxiv", "edgar"]}}, "added_rows": [],
                                       "deleted_rows": []}
    at.run()
    _click(at, "Approve plan and start research")
    state = at.session_state["run"]
    assert state.plan.subqs["q1"].source_types == ["arxiv", "edgar"], state.plan.subqs["q1"].source_types
    assert {s["source"] for s in state.search_log if s["sq"] == "q1"} == {"arxiv", "edgar"}, "no web for q1"


def test_brief_frontend_checklist():
    """The brief's frontend list and bonus goals, as far as a headless render can check them."""
    from apollo.export import markdown_to_pdf
    at = _app(review_gaps=True)
    labels = {w.label for w in [*at.slider, *at.number_input, *at.multiselect, *at.checkbox, *at.toggle]}
    assert {"Maximum research rounds", "Time limit for research (minutes)", "Token budget", "Source types",
            "Let me review the plan before searching", "Review gaps between rounds"} <= labels, labels
    assert at.text_input[0].label == "Research topic"
    assert any(b.label == "Approve plan and start research" for b in at.button), "plan approval"
    _click(at, "Approve plan and start research")
    log = _markdown(at)                                         # live progress: plan, searches, reviews, gaps
    for icon, kind in (("🗺️", "plan"), ("🔎", "search"), ("🧐", "review"), ("🕳️", "gap")):
        assert icon in log, f"no {kind} line in the live log"
    assert at.session_state["gap_rows"], "gap approval"
    sources = {s["source"] for s in at.session_state["run"].search_log}
    assert {"web", "arxiv", "edgar"} <= sources, sources       # several source types searched in one run
    _click(at, "Write report now")
    rep = at.session_state["report"]
    tabs = [t.label for t in at.tabs]                         # tree order: the result tabs sit inside "Research"
    assert {"Research", "Benchmark"} <= set(tabs)
    assert [t for t in tabs if t not in ("Research", "Benchmark")] == ["Report", "Evidence", "Sources", "Trace",
                                                                       "Removed sentences"]
    report = _markdown(at)
    assert "## References" in report and "[[1]](https://" in report, "clickable citations and references"
    src = next(d.value for d in at.dataframe if "url" in d.value.columns)
    assert {"url", "sections", "title"} <= set(src.columns) and src["url"].str.startswith("https://").all()
    assert any(e.label.startswith("Introduction: ") for e in at.expander), "evidence viewer per sentence"
    downloads = [e.proto.label for e in at.get("download_button")]
    assert downloads == ["Download Markdown", "Download PDF", "Download provenance JSON"], downloads
    assert markdown_to_pdf(rep.markdown)[:5] == b"%PDF-"


def test_benchmark_tab_shows_the_newest_benchmark():
    import tempfile
    from scripts import handcheck_summary
    real = handcheck_summary.newest_benchmark
    try:
        handcheck_summary.newest_benchmark = lambda runs_dir="runs": None
        at = AppTest.from_file(APP, default_timeout=60).run()
        assert any("No benchmark has been run yet" in i.value for i in at.info)
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp) / "benchmark-20261101-000000"
            bench.mkdir()
            (bench / "benchmark.md").write_text("# Apollo vs single-shot baseline\n\nCosts $4.2 and $7.8 here.\n",
                                                encoding="utf-8")
            handcheck_summary.newest_benchmark = lambda runs_dir="runs": bench
            at = AppTest.from_file(APP, default_timeout=60).run()
            assert not at.exception
            md = "\n".join(m.value for m in at.markdown)
            assert "# Apollo vs single-shot baseline" in md and "\\$4.2" in md, "shown, with $ not read as LaTeX"
            assert any(str(bench) in c.value for c in at.caption)
    finally:
        handcheck_summary.newest_benchmark = real


def test_full_run_with_the_toggle_off():
    at = _app(review_gaps=False)
    _click(at, "Approve plan and start research")
    rep = at.session_state["report"]
    assert rep is not None and at.session_state["phase"] == "done" and rep.state.round >= 2
    actors = {e["actor"] for e in at.session_state["events"]}
    assert {"apollo", "athena", "hermes", "writer"} <= actors, actors
    final = _graphs(at)[0]                                      # Trace tab graph: final statuses
    assert 'fillcolor="#9bd89b"' in final and 'fillcolor="#f08c8c"' in final, "covered green, unfillable red"
    trace = [d.value for d in at.dataframe]
    rounds = next(df for df in trace if "gaps by type" in df.columns)
    assert list(rounds["round"]) == list(range(1, rep.state.round + 1))
    assert {"queries sent", "new domains", "decision", "reason"} <= set(rounds.columns)
    assert "your choice" not in rounds.columns, "no gap review in this run"
    events = next(df for df in trace if "actor" in df.columns)
    assert {"Apollo", "Athena", "Hermes", "Writer"} <= set(events["actor"])


def test_gap_review_one_round_per_click():
    at = _app(review_gaps=True)
    _click(at, "Approve plan and start research")
    assert at.session_state["phase"] == "gaps" and at.session_state["run"].round == 1, "stops after one round"
    assert len(_graphs(at)) == 1 and not any(b.label == "Approve plan and start research" for b in at.button),         "the plan editor is gone once the run starts"
    assert at.session_state["report"] is None
    md = _markdown(at)
    assert ":blue[**Apollo**]" in md and ":green[**Athena**]" in md and ":orange[**Hermes**]" in md
    gaps = at.session_state["gap_rows"]
    assert gaps and {"approve", "id", "type", "sub-question", "detail", "query"} <= set(gaps[0])
    first = gaps[0]
    at.session_state["gap_editor_1"] = {"edited_rows": {0: {"query": "my own query"}}, "added_rows": [],
                                         "deleted_rows": []}
    _click(at, "Run next round")
    state = at.session_state["run"]
    assert state.round == 2 and at.session_state["gate_log"][0]["edited"] == 1
    assert any(s["round"] == 2 and s["query"] == "my own query" for s in state.search_log), \
        f"edited query for {first['id']} was searched"
    if at.session_state["phase"] == "gaps":
        _click(at, "Write report now")
    rep = at.session_state["report"]
    assert rep is not None and at.session_state["phase"] == "done"
    rounds = next(d.value for d in at.dataframe if "gaps by type" in d.value.columns)
    assert "your choice" in rounds.columns and "run next round" in rounds["your choice"].iloc[0]


def test_rerun_mid_round_does_not_crash():
    at = _app(review_gaps=True)
    _click(at, "Approve plan and start research")
    at.session_state["phase"] = "round"                          # as if the page reran while a round was running
    at.run()
    assert not at.exception and any("interrupted" in w.value for w in at.warning)
    _click(at, "Write report from what was found")
    assert at.session_state["report"] is not None and at.session_state["phase"] == "done"
    assert at.session_state["report"].state.stop_reason.startswith("user chose to write")


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        for t in (test_models_panel_live_and_after_the_run, test_plan_review_shows_graph_and_editor,
                  test_plan_editor_accepts_any_source_subset, test_brief_frontend_checklist,
                  test_benchmark_tab_shows_the_newest_benchmark,
                  test_full_run_with_the_toggle_off,
                  test_gap_review_one_round_per_click, test_rerun_mid_round_does_not_crash):
            t()
            print("ok  ", t.__name__)
    finally:
        pipeline.LLM, agents.run_search = REAL_LLM, REAL_SEARCH
    print("ALL APP TESTS PASSED")


if __name__ == "__main__":
    main()
