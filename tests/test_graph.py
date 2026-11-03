"""Offline tests of the LangGraph runner (ORCHESTRATOR=langgraph): the graph's shape, the same Events and report as
the default Python runner, the in-memory checkpointer, and the offline and edge-case suites passing in both modes.
Run: python -m tests.test_graph"""
import json
import sys

from apollo import agents, config, graph
from apollo.config import Limits
from apollo.pipeline import Apollo
from tests import test_edge_cases, test_offline
from tests.test_offline import FakeLLM, fake_search

TOPIC = "Impact of AI on Indian agriculture"


def _mode(name: str):
    config.ORCHESTRATOR = name


def _run(mode: str, llm=None, limits=None):
    """A whole offline run in one mode: (events, report)."""
    _mode(mode)
    agents.run_search = fake_search
    test_offline.calls.clear()                     # fake_search's URLs depend on its call count
    app = Apollo(limits or Limits(max_rounds=3, parallel_searches=1), llm=llm or FakeLLM())
    events = list(app.research(app.plan(TOPIC)))
    return events, events[-1].data["report"]


def _comparable(events):
    """Each event without its timestamp (and the done event without the Report object)."""
    return [(e.kind, e.actor, e.text, json.dumps(e.data if e.kind != "done" else None, default=str, sort_keys=True))
            for e in events]


def test_orchestrator_setting():
    assert config.orchestrator(None) == config.orchestrator("") == config.orchestrator("airflow") == "python"
    assert config.orchestrator(" LangGraph ") == "langgraph" and config.orchestrator("python") == "python"


def test_graph_shape_and_checkpointer():
    plan = Apollo(Limits(), llm=FakeLLM()).plan(TOPIC)
    compiled, _ = graph.build(Apollo(Limits(), llm=FakeLLM()), plan)
    drawn = compiled.get_graph()
    assert set(graph.NODES) <= set(drawn.nodes), drawn.nodes
    edges = {(e.source, e.target) for e in drawn.edges}
    assert {("plan", "search"), ("search", "review"), ("review", "route"), ("route", "search"), ("route", "write"),
            ("write", "verify"), ("verify", "assemble"), ("assemble", "__end__")} <= edges, edges
    assert type(compiled.checkpointer).__name__ == "InMemorySaver"


def test_same_events_and_report_in_both_modes():
    budget_run = Limits(token_budget=2000, parallel_searches=1)       # writing runs out part-way
    for llm_cls, limits in ((FakeLLM, None), (test_edge_cases.BudgetedLLM, budget_run)):
        py_events, py = _run("python", llm_cls(), limits)
        lg_events, lg = _run("langgraph", llm_cls(), limits)
        assert _comparable(lg_events) == _comparable(py_events), llm_cls.__name__
        assert lg.markdown == py.markdown and lg.references == py.references and lg.sentences == py.sentences
        assert {k: v for k, v in lg.stats.items() if k != "seconds"} == \
               {k: v for k, v in py.stats.items() if k != "seconds"}
        assert lg.state.round_log == py.state.round_log and lg.state.stop_reason == py.state.stop_reason
    _mode("python")


def test_checkpoints_record_the_route_decisions():
    _mode("langgraph")
    agents.run_search = fake_search
    test_offline.calls.clear()
    app = Apollo(Limits(max_rounds=3, parallel_searches=1), llm=FakeLLM())
    compiled, run = graph.build(app, app.plan(TOPIC))
    cfg = {"configurable": {"thread_id": "t1"}, "recursion_limit": 30}
    events = list(compiled.stream({"next": "search"}, cfg, stream_mode="custom"))
    assert events[-1].kind == "done" and run["report"] is events[-1].data["report"]
    history = list(reversed(list(compiled.get_state_history(cfg))))   # one checkpoint per step, oldest first
    path = [s.next[0] for s in history if s.next]
    assert path == ["__start__", "plan", "search", "review", "route", "search", "review", "route", "write", "verify",
                    "assemble"], path
    after_route = [history[i + 1].values["next"] for i, s in enumerate(history) if s.next == ("route",)]
    assert after_route == ["search", "write"], after_route        # route(): search again, then write
    assert compiled.get_state(cfg).values == {"next": "end"}
    _mode("python")


def test_offline_and_edge_cases_pass_in_langgraph_mode():
    _mode("langgraph")
    try:
        test_offline.main()
        test_edge_cases.main()
    finally:
        _mode("python")


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    real, mode = agents.run_search, config.ORCHESTRATOR
    try:
        for t in (test_orchestrator_setting, test_graph_shape_and_checkpointer,
                  test_same_events_and_report_in_both_modes, test_checkpoints_record_the_route_decisions,
                  test_offline_and_edge_cases_pass_in_langgraph_mode):
            t()
            print("ok  ", t.__name__)
    finally:
        agents.run_search, config.ORCHESTRATOR = real, mode
    print("ALL GRAPH TESTS PASSED")


if __name__ == "__main__":
    main()
