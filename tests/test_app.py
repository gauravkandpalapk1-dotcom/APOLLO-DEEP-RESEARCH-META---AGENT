"""Headless tests of the Streamlit UI with the fake LLM and fake search: plan graph, actor-labelled log, gap review
one round per click, the Trace tab, a rerun in the middle of a run, the full-window intro video and the "Now working"
card.
Run: python -m tests.test_app"""
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
    _click(at, "Approve plan")              # paused after round 1: the panel is live
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
    _click(at, "Approve plan")
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
    assert any(b.label == "Approve plan" for b in at.button), "plan approval"
    _click(at, "Approve plan")
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
    _click(at, "Approve plan")
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
    _click(at, "Approve plan")
    assert at.session_state["phase"] == "gaps" and at.session_state["run"].round == 1, "stops after one round"
    assert len(_graphs(at)) == 1 and not any(b.label == "Approve plan" for b in at.button),         "the plan editor is gone once the run starts"
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
    _click(at, "Approve plan")
    at.session_state["phase"] = "round"                          # as if the page reran while a round was running
    at.run()
    assert not at.exception and any("stopped before it finished" in w.value for w in at.warning)
    _click(at, "Write report now")
    assert at.session_state["report"] is not None and at.session_state["phase"] == "done"
    assert at.session_state["report"].state.stop_reason.startswith("user chose to write")


def test_empty_states_say_what_to_do():
    agents.run_search = fake_search
    pipeline.LLM = lambda budget: UIFakeLLM()
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("press **Plan research**" in i.value for i in at.info), "research tab before any plan"
    assert "Plan a topic to see the active models" in _sidebar(at)
    at = _app(review_gaps=False)
    agents.run_search = lambda query, source_type, limits: []          # nothing found at all (searches start now)
    _click(at, "Approve plan")
    infos = [i.value for i in at.info]
    assert any(v.startswith("No sources were collected") and "TAVILY_API_KEYS" in v for v in infos), infos
    assert not any("url" in d.value.columns for d in at.dataframe), "no empty sources table"


def test_errors_explain_the_fix_without_a_stack_trace():
    from apollo.llm import AllProvidersFailed

    def no_models(budget):
        raise RuntimeError("No usable LLM in STRONG_CHAIN. Fill in .env (keys and provider:model entries) "
                           "and run: python -m scripts.check_keys")

    class EveryModelDown(UIFakeLLM):
        def json(self, system, user, **kw):
            raise AllProvidersFailed("All strong models failed: nvidia key #1: auth")
    agents.run_search = fake_search
    for make, expected in ((no_models, "STRONG_CHAIN"),
                           (lambda budget: EveryModelDown(), "No language model answered while planning")):
        pipeline.LLM = make
        at = AppTest.from_file(APP, default_timeout=60).run()
        at.text_input[0].input(TOPIC).run()
        _click(at, "Plan research")
        errors = [e.value for e in at.error]
        assert any(expected in e for e in errors) and any("check_keys" in e for e in errors), errors
    real_route = pipeline.route
    pipeline.route = lambda state, budget: (_ for _ in ()).throw(ValueError("route broke"))   # a bug mid-round
    try:
        pipeline.LLM = lambda budget: UIFakeLLM()
        at = _app(review_gaps=False)
        _click(at, "Approve plan")
        errors = [e.value for e in at.error]
        assert any("Something went wrong during the run (ValueError: route broke)" in e for e in errors), errors
        assert any(b.label == "Write report now" for b in at.button)
    finally:
        pipeline.route = real_route
    _click(at, "Write report now")                                     # recovers: writes from what was found
    assert at.session_state["report"] is not None


def test_button_labels_are_the_standard_ones():
    at = _app(review_gaps=True)
    seen = {b.label for b in at.button}
    _click(at, "Approve plan")
    seen |= {b.label for b in at.button}
    _click(at, "Write report now")
    seen |= {b.label for b in at.button} | {e.proto.label for e in at.get("download_button")}
    assert seen == {"Plan research", "Approve plan", "Run next round", "Write report now", "Start a new topic",
                    "Download Markdown", "Download PDF", "Download provenance JSON"}, seen


def _splash(at: AppTest) -> list[str]:
    """The full-window intro splash(es) on the page."""
    return [h.proto.body for h in at.get("html") if 'id="apollo-intro"' in h.proto.body]


def _videos(at: AppTest) -> list:
    return [v.proto for v in at.get("video")] + _splash(at)


def test_intro_video_plays_full_window_once_then_the_page():
    agents.run_search = fake_search
    pipeline.LLM = lambda budget: UIFakeLLM()
    at = AppTest.from_file(APP, default_timeout=60).run()
    splash = _splash(at)
    assert len(splash) == 1 and not at.get("video"), "the splash only, no in-page player"
    body = splash[0]
    assert "data:video/mp4;base64," in body and "autoplay muted playsinline" in body and " loop" not in body
    assert "position: fixed; inset: 0" in body and "100vw" in body and "100vh" in body, "covers the whole window"
    assert 'addEventListener("ended", close)' in body and "Skip intro" in body, "ends (or is skipped) to the page"
    assert at.title[0].value == "Apollo", "the page is drawn underneath, ready when the video ends"
    at.text_input[0].input(TOPIC).run()
    assert not _splash(at), "once per session: the next rerun doesn't replay it"
    toggle = [t for t in at.sidebar.toggle if t.label == "Show intro video"][0]
    toggle.set_value(False).run()
    assert not _splash(at)
    [t for t in at.sidebar.toggle if t.label == "Show intro video"][0].set_value(True).run()
    assert len(_splash(at)) == 1, "switching the toggle off and on plays it again"
    _click(at, "Plan research")
    assert at.session_state["plan"] is not None and not _videos(at)
    _click(at, "Approve plan")
    assert not _videos(at), "never during a run"


def test_missing_assets_folder_does_not_crash():
    import shutil
    import tempfile
    agents.run_search = fake_search
    pipeline.LLM = lambda budget: UIFakeLLM()
    with tempfile.TemporaryDirectory() as tmp:                  # app.py alone: no assets/ next to it
        shutil.copy(APP, Path(tmp) / "app.py")
        at = AppTest.from_file(str(Path(tmp) / "app.py"), default_timeout=60).run()
        assert not at.exception and not _videos(at)
        [t for t in at.toggle if t.label == "Review gaps between rounds"][0].set_value(True)   # pause: card stays
        at.text_input[0].input(TOPIC).run()
        _click(at, "Plan research")
        _click(at, "Approve plan")
        card = next(m.value for m in at.markdown if "Now working" in m.value)
        assert "<img" not in card and any(emoji in card for emoji in ("☀️", "🦉", "🪽")), card


def test_now_working_card_shows_the_acting_agent():
    at = _app(review_gaps=True)
    _click(at, "Approve plan")                                   # paused at the gap review: the card stays
    events = at.session_state["events"]
    card = next(m.value for m in at.markdown if "Now working" in m.value)
    assert f"<b>{ACTOR_NAMES[events[-1]['actor']]}</b>" in card, card
    for actor, image in (("apollo", "agent_apollo"), ("athena", "agent_athena"), ("hermes", "agent_hermes"),
                         ("writer", "agent_apollo"), ("oracle", "agent_apollo")):
        text = f"{actor} is doing <step> 7 for $5"
        at.session_state["events"] = events + [{"t": 1.0, "actor": actor, "kind": "system", "text": text,
                                                "data": None}]
        at.run()
        card = next(m.value for m in at.markdown if "Now working" in m.value)
        assert f'alt="{image}.jpg"' in card and f"<b>{ACTOR_NAMES[actor]}</b>" in card, (actor, card[-300:])
        assert "&lt;step&gt;" in card and "\\$5" in card, "event text escaped: no HTML, no LaTeX"


ACTOR_NAMES = {"apollo": "Apollo", "athena": "Athena", "hermes": "Hermes", "writer": "Writer", "oracle": "Oracle",
               "system": "System"}


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        for t in (test_models_panel_live_and_after_the_run, test_plan_review_shows_graph_and_editor,
                  test_plan_editor_accepts_any_source_subset, test_brief_frontend_checklist,
                  test_benchmark_tab_shows_the_newest_benchmark,
                  test_full_run_with_the_toggle_off,
                  test_gap_review_one_round_per_click, test_rerun_mid_round_does_not_crash,
                  test_empty_states_say_what_to_do, test_errors_explain_the_fix_without_a_stack_trace,
                  test_button_labels_are_the_standard_ones, test_intro_video_plays_full_window_once_then_the_page,
                  test_missing_assets_folder_does_not_crash, test_now_working_card_shows_the_acting_agent):
            t()
            print("ok  ", t.__name__)
    finally:
        pipeline.LLM, agents.run_search = REAL_LLM, REAL_SEARCH
    print("ALL APP TESTS PASSED")


if __name__ == "__main__":
    main()
