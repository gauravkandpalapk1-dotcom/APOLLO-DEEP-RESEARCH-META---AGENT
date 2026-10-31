"""The orchestrator: one topic in, one cited report out. A plain-Python state machine:

    PLAN -> [SEARCH -> REVIEW -> ROUTE]* -> WRITE -> VERIFY -> ASSEMBLE

Every step yields Events so the UI can show progress live; each event names its actor."""
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from . import config
from .agents import (GAP_PRIORITY, apollo_plan, athena, close_open, follow_up_jobs, gap_query, hermes, route,
                     status_summary)
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
            yield state.event("system", text, actor="system")

    def plan(self, topic: str) -> Plan:
        return apollo_plan(topic, self.llm, self.limits)

    @staticmethod
    def _as(actor: str, events):
        """Pass on an agent's events, labelled with that agent."""
        for ev in events:
            ev.actor = actor
            yield ev

    @staticmethod
    def _log_search(state: RunState, ev):
        if ev.kind not in ("search", "search_fail") or not ev.data:
            return
        source = ev.data.get("source") or (re.match(r"\[(\w+)\]", ev.text) or [None, ""])[1]
        failed = "urls" not in ev.data                    # an exception, not an empty result
        state.search_log.append({"round": state.round, "sq": ev.data.get("sq"), "query": ev.data.get("query"),
                                 "source": source, "urls": ev.data.get("urls", []),
                                 "status": "failed" if failed else ("ok" if ev.data["urls"] else "no results"),
                                 "error": ev.text if failed else ""})

    def research(self, plan: Plan):
        """Generator of Events. The last event has kind 'done' and data={'report': Report}.
        The whole run in one go: every gap Apollo finds is approved and followed up."""
        if config.ORCHESTRATOR == "langgraph":         # the same run as a LangGraph StateGraph (apollo/graph.py)
            from .graph import research
            yield from research(self, plan)
            return
        state = self.start_run(plan)
        while not state.done:
            yield from self.run_round(state)
            if not state.done:
                self.approve_gaps(state, [g["id"] for g in state.pending_gaps], {})
        yield from self.finish(state)

    # ---------- the same run, step by step (for a human gap gate in the UI) ----------
    def start_run(self, plan: Plan) -> RunState:
        """A fresh run: no rounds yet, every planned sub-question queued for the first search."""
        state = RunState(plan=plan, limits=self.limits)
        state.next_jobs = [(sq, sq.query) for sq in plan.subqs.values()]
        return state

    def run_round(self, state: RunState):
        """One round: search, review, route. Yields Events. Afterwards state.pending_gaps holds this round's gaps,
        each with an "id" and the proposed follow-up "query", and state.done is True if Apollo decided to write.
        The first call also announces the plan."""
        if state.done:
            return
        plan = state.plan
        if state.round == 0:
            yield state.event("plan", f"Plan: {len(plan.sections)} sections, {len(plan.subqs)} sub-questions",
                              {"sections": [{"title": s.title, "questions": [plan.subqs[q].question for q in s.subq_ids]}
                                            for s in plan.sections]}, actor="apollo")
            for w in plan.warnings:
                yield state.event("warning", w, actor="apollo")

        jobs = state.next_jobs
        state.round += 1
        yield state.event("round", f"Round {state.round}: Athena runs {len(jobs)} quer{'y' if len(jobs) == 1 else 'ies'}",
                          actor="apollo")
        for ev in self._as("athena", athena(state, jobs)):
            self._log_search(state, ev)
            yield ev
        yield from self._notices(state)
        try:
            yield from self._as("hermes", hermes(state, self.llm))
        except BudgetExceeded as e:
            yield state.event("error", str(e), actor="hermes")
        yield from self._notices(state)
        decision, reason, gaps = route(state, self.budget)
        for i, g in enumerate(gaps):
            g["id"], g["query"] = f"g{state.round}-{i + 1}", gap_query(state, g)
            yield state.event("gap", f"{g['sq'].id} {g['type']} gap: {g['detail']}",
                              {"id": g["id"], "sq": g["sq"].id, "type": g["type"], "detail": g["detail"],
                               "query": g["query"]}, actor="apollo")
        yield state.event("decision", f"Apollo: {'search again' if decision == 'search' else 'stop'} ({reason})",
                          {"decision": decision, "reason": reason, "tokens": self.budget.used}, actor="apollo")
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

    def approve_gaps(self, state: RunState, approved_ids: list[str], edited_queries: dict[str, str] | None = None,
                     write_now: bool = False) -> list:
        """The gap gate. Follow up only the approved gaps in the next round, using an edited query where one is given
        (gap id -> query); returns those (SubQ, query) jobs. write_now stops searching and goes to writing."""
        if state.done:
            return []
        if write_now:
            close_open(state, "user chose to write")
            state.done, state.next_jobs = True, []
            state.stop_reason = f"user chose to write: {status_summary(state.plan)}"
            return []
        wanted = set(approved_ids)
        approved = [g for g in state.pending_gaps if g["id"] in wanted]
        jobs = follow_up_jobs(state, approved)
        edits = {}                                  # one query per sub-question: the highest-priority edit wins
        for g in sorted(approved, key=lambda g: GAP_PRIORITY[g["type"]], reverse=True):
            query = str((edited_queries or {}).get(g["id"]) or "").strip()[:200]
            if query:
                edits[g["sq"].id] = query
        jobs = [(sq, edits.get(sq.id, query)) for sq, query in jobs]
        if not jobs:  # nothing left to search for: never loop without work
            close_open(state, "no follow-up queries left")
            state.done = True
            state.stop_reason = f"no follow-up queries left: {status_summary(state.plan)}"
        state.next_jobs = jobs
        return jobs

    def finish(self, state: RunState):
        """Write, verify and assemble the report. Yields Events ending with "done" (data={'report': Report})."""
        plan = state.plan
        yield state.event("write", f"Writing {len(plan.sections)} sections from their own evidence", actor="writer")
        sentences = []
        try:
            results = write_all(state, self.llm)
            for r in results:
                for p in r.problems:
                    yield state.event("system", f"Verifier, '{r.section.title}': {p}", actor="writer")
                yield state.event("verify", f"'{r.section.title}': {r.kept} sentences kept, "
                                            f"{len(r.removed)} removed by verification",
                                  {"removed": r.removed}, actor="writer")
            md, refs, stats, removed, problems, sentences = assemble(state, self.llm, results)
            for p in problems:
                yield state.event("system", f"Verifier, introduction/conclusion: {p}", actor="writer")
        except BudgetExceeded as e:
            yield state.event("error", f"Stopped while writing: {e}", actor="writer")
            md, refs, stats, removed = (f"# {plan.topic}\n\nThe token budget ran out before the report could be "
                                        f"written. Raise the budget and run again.", [], {}, [])
        except AllProvidersFailed as e:
            yield state.event("error", f"Every model failed while writing: {str(e)[:300]}", actor="writer")
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
        yield state.event("done", "Report ready", {"report": report}, actor="system")


def provenance(report: Report) -> dict:
    """Everything needed to audit one run, as plain JSON-ready data: settings, plan, every search and its result
    URLs, every chunk, every Hermes review, every routing decision, every verified sentence, and the stats."""
    st = report.state
    return {
        "run_id": st.run_id,
        "topic": st.plan.topic,
        "settings": asdict(st.limits),
        "plan": {"warnings": st.plan.warnings,
                 "sections": [{"id": s.id, "title": s.title, "sub_questions": [
                     {"id": q, "question": sq.question, "perspective": sq.perspective, "query": sq.query,
                      "source_types": sq.source_types, "acceptance_criteria": sq.acceptance_criteria,
                      "criteria_met": sq.criteria_met, "status": sq.status, "status_reason": sq.status_reason,
                      "queries": sq.queries, "attempts": sq.attempts}
                     for q in s.subq_ids for sq in [st.plan.subqs[q]]]} for s in st.plan.sections]},
        "rounds": st.round_log,
        "searches": st.search_log,
        "chunks": [{"id": c.id, "sq_id": c.sq_id, "url": c.url, "title": c.title, "domain": c.domain,
                    "source_class": c.source_class, "source_type": c.source_type, "published": c.published,
                    "year": c.year, "round": c.round, "text": c.text} for c in st.chunks.values()],
        "reviews": st.review_log,
        "decisions": [{"round": r["round"], "decision": r["decision"], "reason": r["reason"],
                       "gaps": r["gap_details"]} for r in st.round_log],
        "contradictions": st.contradictions,
        "sentences": report.sentences,
        "references": report.references,
        "stop_reason": st.stop_reason,
        "stats": report.stats,
    }


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
