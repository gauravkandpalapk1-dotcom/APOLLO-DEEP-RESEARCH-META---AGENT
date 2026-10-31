"""The orchestrator: one topic in, one cited report out. A plain-Python state machine:

    PLAN -> [SEARCH -> REVIEW -> ROUTE]* -> WRITE -> VERIFY -> ASSEMBLE

Every step yields Events so the UI can show progress live."""
from dataclasses import dataclass, field

from .agents import apollo_plan, athena, close_open, follow_up_jobs, hermes, route, status_summary
from .config import Limits
from .llm import LLM, AllProvidersFailed, Budget, BudgetExceeded
from .state import Plan, RunState
from .writer import assemble, write_all


@dataclass
class Report:
    markdown: str
    references: list[dict]
    sources: list[dict]
    stats: dict
    removed: list[dict]
    state: RunState
    # every verified sentence: {section, text_with_refs, verdict, reason, evidence: [{ref_number, url, title,
    # domain, published, passage}]}, for the evidence inspector
    sentences: list[dict] = field(default_factory=list)


class Apollo:
    def __init__(self, limits: Limits | None = None, llm: LLM | None = None):
        self.limits = limits or Limits()
        self.budget = Budget(self.limits.token_budget)
        self.llm = llm or LLM(self.budget)
        self.llm.budget = self.budget

    def _notices(self, state: RunState):
        drain = getattr(self.llm, "drain_notices", None)
        for text in (drain() if drain else []):
            yield state.event("system", text)

    def plan(self, topic: str) -> Plan:
        return apollo_plan(topic, self.llm, self.limits)

    def research(self, plan: Plan):
        """Generator of Events. The last event has kind 'done' and data={'report': Report}."""
        state = RunState(plan=plan, limits=self.limits)
        yield state.event("plan", f"Plan: {len(plan.sections)} sections, {len(plan.subqs)} sub-questions",
                          {"sections": [{"title": s.title, "questions": [plan.subqs[q].question for q in s.subq_ids]}
                                        for s in plan.sections]})
        for w in plan.warnings:
            yield state.event("warning", w)

        jobs = [(sq, sq.query) for sq in plan.subqs.values()]
        while True:
            state.round += 1
            yield state.event("round", f"Round {state.round}: Athena runs {len(jobs)} quer{'y' if len(jobs) == 1 else 'ies'}")
            yield from athena(state, jobs)
            yield from self._notices(state)
            try:
                yield from hermes(state, self.llm)
            except BudgetExceeded as e:
                yield state.event("error", str(e))
            yield from self._notices(state)
            decision, reason, gaps = route(state, self.budget)
            for g in gaps:
                yield state.event("gap", f"{g['sq'].id} {g['type']} gap: {g['detail']}",
                                  {"sq": g["sq"].id, "type": g["type"], "detail": g["detail"]})
            yield state.event("decision", f"Apollo: {'search again' if decision == 'search' else 'stop'} ({reason})",
                              {"decision": decision, "reason": reason, "tokens": self.budget.used})
            if decision == "write":
                state.stop_reason = reason
                break
            jobs = follow_up_jobs(state, gaps)
            if not jobs:  # nothing left to search for: never loop without work
                close_open(state, "no follow-up queries left")
                state.stop_reason = f"no follow-up queries left: {status_summary(plan)}"
                break

        yield state.event("write", f"Writing {len(plan.sections)} sections from their own evidence")
        sentences = []
        try:
            results = write_all(state, self.llm)
            for r in results:
                for p in r.problems:
                    yield state.event("system", f"Verifier, '{r.section.title}': {p}")
                yield state.event("verify", f"'{r.section.title}': {r.kept} sentences kept, "
                                            f"{len(r.removed)} removed by verification",
                                  {"removed": r.removed})
            md, refs, stats, removed, problems, sentences = assemble(state, self.llm, results)
            for p in problems:
                yield state.event("system", f"Verifier, introduction/conclusion: {p}")
        except BudgetExceeded as e:
            yield state.event("error", f"Stopped while writing: {e}")
            md, refs, stats, removed = (f"# {plan.topic}\n\nThe token budget ran out before the report could be "
                                        f"written. Raise the budget and run again.", [], {}, [])
        except AllProvidersFailed as e:
            yield state.event("error", f"Every model failed while writing: {str(e)[:300]}")
            md, refs, stats, removed = (f"# {plan.topic}\n\nNo language model was reachable while writing the "
                                        f"report, so no report was produced. Run `python -m scripts.check_keys` "
                                        f"to see which keys and models work.", [], {}, [])
        yield from self._notices(state)

        stats.update({"rounds": state.round, "stop_reason": state.stop_reason, "tokens_used": self.budget.used,
                      "sources_collected": len({c.url for c in state.chunks.values()}),
                      "failed_searches": len(state.failed_searches), "seconds": round(state.elapsed, 1)})
        if hasattr(self.llm, "status"):
            stats["models"] = self.llm.status()
        report = Report(md, refs, source_table(state, refs), stats, removed, state, sentences)
        yield state.event("done", "Report ready", {"report": report})


def source_table(state: RunState, refs: list[dict]) -> list[dict]:
    cited = {r["url"]: r["n"] for r in refs}
    titles = {s.id: s.title for s in state.plan.sections}
    rows = {}
    for c in state.chunks.values():
        row = rows.setdefault(c.url, {"ref": cited.get(c.url, ""), "title": c.title, "url": c.url,
                                      "type": c.source_type, "published": c.published or (c.year or ""),
                                      "sections": set(), "round": c.round})
        row["sections"].add(titles[state.plan.subqs[c.sq_id].section_id])
    out = []
    for r in rows.values():
        r["sections"] = ", ".join(sorted(r["sections"]))
        out.append(r)
    return sorted(out, key=lambda r: (r["ref"] == "", r["ref"] or 0, r["round"]))
