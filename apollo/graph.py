"""LangGraph runner, chosen with ORCHESTRATOR=langgraph: the same run as Apollo.research(), as a StateGraph.

    plan -> search -> review -> route --"search"--> search
                                      --"write"---> write -> verify -> assemble -> END

Each node calls the existing functions (Apollo.start_run, athena, hermes, route, Apollo.approve_gaps, write_all,
assemble) in the same order as Apollo.run_round() and Apollo.finish(), and emits the same Events, streamed live
through LangGraph's custom stream. Whether to search again or write comes from route() (and approve_gaps(), which
research() calls with every gap approved); the graph adds no logic of its own.
The run's objects (the app, its RunState, the section results) stay outside the graph state, which holds only the
next step, so the in-memory checkpointer never has to serialise locks or API clients."""
import uuid
from collections import Counter
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from .agents import athena, gap_query, hermes, route
from .llm import AllProvidersFailed, BudgetExceeded
from .state import Plan
from .writer import assemble, write_all

NODES = ("plan", "search", "review", "route", "write", "verify", "assemble")


class Step(TypedDict):
    next: str            # "search" or "write": route()'s decision after approve_gaps()


def _emit(events):
    write = get_stream_writer()
    for ev in events:
        write(ev)


def build(app, plan: Plan):
    """The compiled graph for one run, and the dict its nodes share (state, results, report)."""
    run = {"state": None, "results": None, "failed": None, "report": None}

    def writing_failed(e: Exception):
        """What Apollo.finish() does when writing or assembling stops: an error event and a report saying why."""
        state = run["state"]
        if isinstance(e, BudgetExceeded):
            _emit([state.event("error", f"Stopped while writing: {e}", actor="writer")])
            run["failed"] = (f"# {state.plan.topic}\n\nThe token budget ran out before the report could be "
                             f"written. Raise the budget and run again.", [], {}, [])
        else:
            _emit([state.event("error", f"Every model failed while writing: {str(e)[:300]}", actor="writer")])
            run["failed"] = (f"# {state.plan.topic}\n\nNo language model was reachable while writing the "
                             f"report, so no report was produced. Run `python -m scripts.check_keys` "
                             f"to see which keys and models work.", [], {}, [])

    def plan_node(_: Step) -> Step:
        state = run["state"] = app.start_run(plan)
        _emit([state.event("plan", f"Plan: {len(plan.sections)} sections, {len(plan.subqs)} sub-questions",
                           {"sections": [{"title": s.title, "questions": [plan.subqs[q].question for q in s.subq_ids]}
                                         for s in plan.sections]}, actor="apollo")])
        _emit(state.event("warning", w, actor="apollo") for w in plan.warnings)
        return {"next": "search"}

    def search_node(_: Step) -> Step:
        state = run["state"]
        jobs = state.next_jobs
        state.round += 1
        _emit([state.event("round", f"Round {state.round}: Athena runs {len(jobs)} "
                                    f"quer{'y' if len(jobs) == 1 else 'ies'}", actor="apollo")])
        for ev in app._as("athena", athena(state, jobs)):
            app._log_search(state, ev)
            _emit([ev])
        _emit(app._notices(state))
        return {"next": "review"}

    def review_node(_: Step) -> Step:
        state = run["state"]
        try:
            _emit(app._as("hermes", hermes(state, app.llm)))
        except BudgetExceeded as e:
            _emit([state.event("error", str(e), actor="hermes")])
        _emit(app._notices(state))
        return {"next": "route"}

    def route_node(_: Step) -> Step:
        state = run["state"]
        decision, reason, gaps = route(state, app.budget)
        for i, g in enumerate(gaps):
            g["id"], g["query"] = f"g{state.round}-{i + 1}", gap_query(state, g)
            _emit([state.event("gap", f"{g['sq'].id} {g['type']} gap: {g['detail']}",
                               {"id": g["id"], "sq": g["sq"].id, "type": g["type"], "detail": g["detail"],
                                "query": g["query"]}, actor="apollo")])
        _emit([state.event("decision", f"Apollo: {'search again' if decision == 'search' else 'stop'} ({reason})",
                           {"decision": decision, "reason": reason, "tokens": app.budget.used}, actor="apollo")])
        state.round_log.append({
            "round": state.round,
            "queries": dict(Counter(s["source"] for s in state.search_log if s["round"] == state.round)),
            "new_sources": state.new_sources_this_round, "new_domains": state.new_domains_this_round,
            "gaps": dict(Counter(g["type"] for g in gaps)),
            "gap_details": [{"id": g["id"], "sq": g["sq"].id, "type": g["type"], "detail": g["detail"],
                             "query": g["query"]} for g in gaps],
            "decision": decision, "reason": reason})
        state.pending_gaps, state.next_jobs = gaps, []
        if decision == "write":
            state.done, state.stop_reason = True, reason
        else:                                       # as research(): every gap approved; may still end the search
            app.approve_gaps(state, [g["id"] for g in state.pending_gaps], {})
        return {"next": "write" if state.done else "search"}

    def write_node(_: Step) -> Step:
        state = run["state"]
        _emit([state.event("write", f"Writing {len(state.plan.sections)} sections from their own evidence",
                           actor="writer")])
        try:
            run["results"] = write_all(state, app.llm)
        except (BudgetExceeded, AllProvidersFailed) as e:
            writing_failed(e)
        return {"next": "verify"}

    def verify_node(_: Step) -> Step:
        state = run["state"]
        for r in run["results"] or []:
            _emit(state.event("system", f"Verifier, '{r.section.title}': {p}", actor="writer") for p in r.problems)
            _emit([state.event("verify", f"'{r.section.title}': {r.kept} sentences kept, "
                                         f"{len(r.removed)} removed by verification",
                               {"removed": r.removed}, actor="writer")])
        return {"next": "assemble"}

    def assemble_node(_: Step) -> Step:
        from .pipeline import Report, source_table
        state, sentences = run["state"], []
        if run["failed"] is None:
            try:
                md, refs, stats, removed, problems, sentences = assemble(state, app.llm, run["results"])
                _emit(state.event("system", f"Verifier, introduction/conclusion: {p}", actor="writer")
                      for p in problems)
            except (BudgetExceeded, AllProvidersFailed) as e:
                writing_failed(e)
        if run["failed"] is not None:
            md, refs, stats, removed = run["failed"]
        _emit(app._notices(state))
        stats.update({"rounds": state.round, "stop_reason": state.stop_reason, "tokens_used": app.budget.used,
                      "sources_collected": len({c.url for c in state.chunks.values()}),
                      "failed_searches": len(state.failed_searches), "seconds": round(state.elapsed, 1)})
        if hasattr(app.llm, "status"):
            stats["models"] = app.llm.status()
        run["report"] = Report(md, refs, source_table(state, refs), stats, removed, state, sentences)
        _emit([state.event("done", "Report ready", {"report": run["report"]}, actor="system")])
        return {"next": "end"}

    g = StateGraph(Step)
    for name, fn in zip(NODES, (plan_node, search_node, review_node, route_node, write_node, verify_node,
                                assemble_node)):
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "search")
    g.add_edge("search", "review")
    g.add_edge("review", "route")
    g.add_conditional_edges("route", lambda s: s["next"], {"search": "search", "write": "write"})
    g.add_edge("write", "verify")
    g.add_edge("verify", "assemble")
    g.add_edge("assemble", END)
    return g.compile(checkpointer=InMemorySaver()), run


def research(app, plan: Plan):
    """Generator of the same Events as Apollo.research(); the last has kind 'done' and data={'report': Report}."""
    graph, _ = build(app, plan)
    config = {"configurable": {"thread_id": uuid.uuid4().hex},
              "recursion_limit": 3 * app.limits.max_rounds + 10}    # 3 nodes a round, plus plan / write / verify / ...
    yield from graph.stream({"next": "search"}, config, stream_mode="custom")
