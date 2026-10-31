"""Apollo (planning + routing), Athena (searching), Hermes (reviewing).
LLMs only extract and judge text; every decision about gaps, retries and stopping is made in code here."""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .config import CHUNK_DATA_RULE, Limits
from .llm import LLM
from .search import run_search
from .state import Chunk, Plan, RunState, Section, SubQ, chunk_text, domain_of, relevance


class PlanError(Exception):
    pass


# =====================================================================
# APOLLO: planning
# =====================================================================
PLAN_SYSTEM = "You are Apollo, the lead analyst who plans a research report before any searching starts."


def apollo_plan(topic: str, llm: LLM, limits: Limits) -> Plan:
    allowed = ", ".join(limits.enabled_sources)
    since = datetime.now().year - 2      # recency criteria are relative to today, never a fixed past year
    user = f"""Topic: {topic}

First list 3 or 4 stakeholder perspectives on this topic (for example farmer, regulator, industry, researcher).
Then plan a research report with 3 to 5 sections. Give each section 1 to 3 specific sub-questions that a web
search can answer with evidence (figures, named organisations, dates, documented outcomes), spread across
the perspectives. Each sub-question names the one perspective it is asked from.
For each sub-question give a short search query (4 to 10 words) and the source types to use, chosen from: {allowed}.
Use "arxiv" only for technical or scientific questions, "edgar" only for US-listed company financials,
"news" for developments in the last two years. Include "web" for every sub-question.
Give each sub-question 1 or 2 acceptance criteria: checkable statements about the evidence that must hold
before the question counts as answered. A criterion must be something a typical news article, government
page, company page or report would state. Prefer one broad criterion over two narrow ones. Rules:
- When recency matters write "dated {since} or later"; never require one fixed past year.
- No statistical metrics (R², RMSE, F1, mAP, p-values) and no named regions, organisations or document types,
  unless the sub-question itself is about them.
- Nothing about the number of sources (code checks that), and nothing vague like "good coverage".
Good: "at least one adoption figure dated {since} or later"; "at least one named government scheme".
Bad: "R² or RMSE for yield models in Maharashtra from an ICAR bulletin"; "detailed information on adoption".

JSON shape:
{{"perspectives": ["..."],
 "sections": [{{"title": "...", "sub_questions": [{{"question": "...", "perspective": "...", "query": "...",
   "source_types": ["web"], "acceptance_criteria": [{{"id": "c1", "text": "..."}}]}}]}}]}}"""
    last_err = None
    for _ in range(2):
        try:
            return validate_plan(topic, llm.json(PLAN_SYSTEM, user, temperature=0.3, role="strong"), limits)
        except (PlanError, ValueError, KeyError, TypeError) as e:
            last_err = e
    raise PlanError(f"Could not get a usable plan: {last_err}")


def validate_plan(topic: str, raw: dict, limits: Limits) -> Plan:
    """Code-level checks on the LLM's plan: caps, allowed sources, no empty or duplicate questions.
    Items that are not JSON objects are skipped and recorded in plan.warnings instead of crashing."""
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        raise PlanError("Planner output has no list of sections")
    sections, subqs, seen_q, warnings = [], {}, set(), []
    for s_raw in raw["sections"][:6]:
        if not isinstance(s_raw, dict):
            warnings.append(f"Planner returned a section that is not an object ({str(s_raw)[:60]!r}); skipped")
            continue
        title = str(s_raw.get("title", "")).strip()
        if not title:
            continue
        sec = Section(id=f"s{len(sections) + 1}", title=title, subq_ids=[])
        q_list = s_raw.get("sub_questions")
        for q_raw in (q_list if isinstance(q_list, list) else [])[:3]:
            if not isinstance(q_raw, dict):
                warnings.append(f"Planner returned a sub-question that is not an object in '{title}' "
                                f"({str(q_raw)[:60]!r}); skipped")
                continue
            question = str(q_raw.get("question", "")).strip()
            if not question or question.lower() in seen_q:
                continue
            seen_q.add(question.lower())
            st_raw = q_raw.get("source_types")
            types = [t for t in (st_raw if isinstance(st_raw, list) else []) if t in limits.enabled_sources]
            if "web" in limits.enabled_sources and "web" not in types:
                types.insert(0, "web")
            if not types:
                types = [limits.enabled_sources[0]]
            sq = SubQ(id=f"q{len(subqs) + 1}", section_id=sec.id, question=question,
                      query=str(q_raw.get("query") or question).strip()[:200], source_types=types,
                      perspective=_perspective(q_raw.get("perspective")),
                      acceptance_criteria=_criteria(q_raw.get("acceptance_criteria")))
            subqs[sq.id] = sq
            sec.subq_ids.append(sq.id)
        if sec.subq_ids:
            sections.append(sec)
    if len(sections) < 2:
        raise PlanError("Plan has fewer than 2 usable sections")
    return Plan(topic=topic, sections=sections, subqs=subqs, warnings=warnings)


def _perspective(raw) -> str:
    return (" ".join(raw.split())[:80].strip() if isinstance(raw, str) else "") or "general"


def _criteria(raw) -> list[dict]:
    """At most 2 distinct criteria as [{"id": "c1", "text": ...}], text at most 200 characters. Items may be
    {"id", "text"} objects or plain strings; anything else is skipped. Ids are renumbered c1, c2 here, so
    Hermes' verdicts are always matched against ids we control. Zero criteria is allowed."""
    if isinstance(raw, str):
        raw = raw.split(";")
    texts, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        text = item.get("text") if isinstance(item, dict) else item
        if not isinstance(text, str):
            continue
        text = _cut_criterion(_recent_years(" ".join(text.split())))
        key = text.lower().rstrip(".")
        if key and key not in seen:
            seen.add(key)
            texts.append(text)
    return [{"id": f"c{i + 1}", "text": t} for i, t in enumerate(texts[:MAX_CRITERIA])]


MAX_CRITERIA = 2
CRITERION_CHARS = 200
TRAILING_OR_LATER = re.compile(r"\s+(?:dated\s+)?\S+\s+or later\W*$", re.I)


def _cut_criterion(text: str) -> str:
    """At most CRITERION_CHARS, cut at a word boundary. A trailing "<year> or later" (with "dated" if present)
    is always kept whole, so the cut never turns "dated 2024 or later" into "dated 2024 or"."""
    if len(text) <= CRITERION_CHARS:
        return text
    m = TRAILING_OR_LATER.search(text)
    suffix = m.group() if m and len(m.group()) < CRITERION_CHARS // 2 else ""
    body, room = text[:len(text) - len(suffix)], CRITERION_CHARS - len(suffix)
    head = body[:room + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else body[:room]
    return cut.rstrip(" ,;:-") + suffix


YEAR = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")
YEAR_RANGE = re.compile(r"(?:19|20)\d\d\s*(?:-|–|—|to|and)\s*(?:19|20)\d\d", re.I)


def _recent_years(text: str) -> str:
    """A criterion that demands a year older than (this year - 2), without "or later", would reject newer
    evidence. Rewrite each such year to "<this year - 2> or later". Year ranges are left alone."""
    since = datetime.now().year - 2
    if "or later" in text.lower() or YEAR_RANGE.search(text):
        return text
    return YEAR.sub(lambda m: f"{since} or later" if int(m.group()) < since else m.group(), text)


CRITERIA_COLUMNS = tuple(f"c{i + 1}" for i in range(MAX_CRITERIA))


def plan_rows(plan: Plan) -> list[dict]:
    """The plan as editable table rows for the UI; plan_from_rows() turns them back into a Plan.
    Each acceptance criterion gets its own column named by its id, so its text can contain any character."""
    rows = []
    for s in plan.sections:
        for q in s.subq_ids:
            sq = plan.subqs[q]
            text = {c["id"]: c["text"] for c in sq.acceptance_criteria}
            rows.append({"section": s.title, "perspective": sq.perspective, "question": sq.question,
                         **{k: text.get(k, "") for k in CRITERIA_COLUMNS},
                         "query": sq.query, "sources": ", ".join(sq.source_types)})
    return rows


def plan_from_rows(topic: str, rows: list[dict], limits: Limits) -> Plan:
    """Rebuild a plan after a human edits it in the UI
    (rows: section, perspective, question, c1, c2, query, sources). An empty criterion cell means none."""
    grouped: dict[str, list] = {}
    for r in rows:
        if str(r.get("question", "")).strip():
            grouped.setdefault(str(r.get("section") or "General").strip(), []).append({
                "question": r["question"], "query": r.get("query") or r["question"],
                "perspective": r.get("perspective"),
                "acceptance_criteria": [{"id": k, "text": r.get(k)} for k in CRITERIA_COLUMNS],
                "source_types": [t.strip() for t in str(r.get("sources", "web")).split(",") if t.strip()]})
    raw = {"sections": [{"title": k, "sub_questions": v[:3]} for k, v in grouped.items()]}
    return validate_plan(topic, raw, limits)


# =====================================================================
# ATHENA: searching (parallel)
# =====================================================================
def athena(state: RunState, jobs: list[tuple[SubQ, str]]):
    """Run every (sub-question, query) across its source types in parallel. Yields events."""
    limits = state.limits
    state.new_sources_this_round = 0
    domains_before = {(c.sq_id, c.domain) for c in state.chunks.values()}
    tasks = [(sq, query, st) for sq, query in jobs for st in sq.source_types]
    room = max(0, limits.max_search_calls - state.search_calls)
    if len(tasks) > room:                         # hard cap on search API calls per run
        state.search_cap_hit = True
        yield state.event("search_cap", f"Search-call cap ({limits.max_search_calls}) reached: "
                                        f"{len(tasks) - room} search(es) not run",
                          {"cap": limits.max_search_calls, "skipped": len(tasks) - room})
        tasks = tasks[:room]
    state.search_calls += len(tasks)
    for sq, query in jobs:
        if any(t[0] is sq and t[1] == query for t in tasks):
            sq.attempts += 1
            sq.queries.append(query)

    with ThreadPoolExecutor(max_workers=limits.parallel_searches) as pool:
        futures = {pool.submit(run_search, q, st, limits): (sq, q, st) for sq, q, st in tasks}
        for fut in as_completed(futures):
            sq, q, st = futures[fut]
            try:
                docs = fut.result()
            except Exception as e:  # any error from one source is a failed search, never a crashed run
                state.failed_searches.append({"sq": sq.id, "query": q, "source": st, "error": str(e)[:200]})
                yield state.event("search_fail", f"[{st}] search failed for {sq.id}: {str(e)[:120]}",
                                  {"sq": sq.id, "query": q})
                continue
            added = _store_docs(state, sq, docs)
            kind = "search" if docs else "search_fail"
            msg = (f"[{st}] {sq.id} '{q}' → {len(docs)} results, {added} new sources"
                   if docs else f"[{st}] {sq.id} '{q}' → no results")
            yield state.event(kind, msg, {"sq": sq.id, "query": q, "source": st,
                                          "urls": [d.url for d in docs]})
    state.new_domains_this_round = len({(c.sq_id, c.domain) for c in state.chunks.values()} - domains_before)


def _store_docs(state: RunState, sq: SubQ, docs) -> int:
    added = 0
    for d in docs:
        if (sq.id, d.url) in state.seen:
            continue
        state.seen.add((sq.id, d.url))
        pieces = chunk_text(d.text)
        scored = sorted(((relevance(p, sq.question + " " + sq.query), p) for p in pieces), reverse=True)[:2]
        for score, text in scored:
            cid = f"C{len(state.chunks) + 1}"
            state.chunks[cid] = Chunk(cid, sq.id, d.url, d.title, text, d.source_type, d.published,
                                      d.year, score, state.round, d.domain, d.source_class)
        added += 1
    state.new_sources_this_round += added
    return added


# =====================================================================
# HERMES: reviewing
# =====================================================================
REVIEW_SYSTEM = ("You are Hermes, a strict research reviewer. You judge only from the evidence text given to you, "
                 "never from your own knowledge. " + CHUNK_DATA_RULE)


def _evidence_block(state: RunState, sq: SubQ, max_chars: int = 900) -> str:
    out = [f'<sub_question id="{sq.id}">{sq.question}</sub_question>']
    out += [f'<criterion sq="{sq.id}" id="{cr["id"]}">{cr["text"]}</criterion>' for cr in sq.acceptance_criteria]
    chunks = state.top_chunks(sq.id, state.limits.chunks_per_sq)
    for c in chunks:
        out.append(f'<chunk id="{c.id}" sq="{sq.id}" url="{c.url}" published="{c.published or "unknown"}">'
                   f'{c.text[:max_chars]}</chunk>')
    if not chunks:
        out.append("(no evidence collected)")
    return "\n".join(out)


def _review_section(state: RunState, llm: LLM, section: Section, sqs: list[SubQ]) -> dict:
    evidence = "\n\n".join(_evidence_block(state, sq) for sq in sqs)
    user = f"""Report topic: {state.plan.topic}
Section: {section.title}

For each sub-question, read its chunks and judge whether they actually answer it, and whether each of its
acceptance criteria (the <criterion> lines) holds, judged only from the chunks shown for that sub-question.

{evidence}

Return JSON:
{{"sub_questions": [{{"id": "q1", "answered": true, "criteria_met": {{"c1": true, "c2": false}},
   "missing": "what is still missing; empty if nothing",
   "follow_up_query": "short web search query that would fill the gap, empty if answered and every criterion is met"}}],
 "claims": [{{"chunk_id": "C3", "sq_id": "q1", "entity": "...", "metric": "...", "value": "12.5", "unit": "%", "as_of_year": 2024}}],
 "source_years": {{"C3": 2023}},
 "contradictions": [{{"sq_id": "q1", "chunk_ids": ["C3", "C9"], "description": "...", "follow_up_query": "..."}}]}}

Rules: "criteria_met" is keyed by the id of each <criterion> line of that sub-question ({{}} if it has none).
"claims" lists only quantitative facts stated in a chunk (at most 5 per sub-question).
"source_years" gives the year a chunk's information refers to, only when the chunk states it.
"contradictions" lists chunks that state incompatible facts about the same thing."""
    return llm.json(REVIEW_SYSTEM, user, role="fast")


def hermes(state: RunState, llm: LLM):
    """Review every open sub-question, section by section in parallel. Yields events."""
    by_section = {}
    for sq in state.plan.subqs.values():
        if sq.status == "open":
            by_section.setdefault(sq.section_id, []).append(sq)
    sections = {s.id: s for s in state.plan.sections}

    with ThreadPoolExecutor(max_workers=state.limits.parallel_searches) as pool:
        futures = {pool.submit(_review_section, state, llm, sections[sid], sqs): sid
                   for sid, sqs in by_section.items()}
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                review = fut.result()
            except Exception as e:  # a failed review leaves the sub-questions open; the router handles it
                yield state.event("error", f"Review of {sections[sid].title} failed: {str(e)[:150]}")
                continue
            if not isinstance(review, dict):
                yield state.event("warning", f"Hermes' review of '{sections[sid].title}' was not a JSON object; ignored")
                continue
            skipped = _apply_review(state, by_section[sid], review)
            if skipped:
                yield state.event("warning", f"Hermes returned {skipped} malformed sub-question item(s) for "
                                             f"'{sections[sid].title}'; skipped")
            state.review_log.append({"round": state.round, "section": sid, "review": review})
            yield state.event("review", f"Hermes reviewed '{sections[sid].title}'", {"review": review})

    for c in numeric_contradictions(state):
        if not any(set(c["chunk_ids"]) == set(x["chunk_ids"]) for x in state.contradictions):
            state.contradictions.append(c)
            yield state.event("gap", f"Code found conflicting figures for {c['sq_id']}: {c['description']}", c)


def _apply_review(state: RunState, sqs: list[SubQ], review: dict) -> int:
    """Store Hermes' judgements. Returns how many sub_questions items were malformed and skipped."""
    ids = {sq.id: sq for sq in sqs}
    items, skipped = review.get("sub_questions"), 0
    if not isinstance(items, list):
        items, skipped = [], int(items is not None)
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        sq = ids.get(str(item.get("id")))
        if sq:
            sq.answered = bool(item.get("answered"))
            sq.missing = str(item.get("missing") or "")[:300]
            sq.follow_up = str(item.get("follow_up_query") or "")[:200]
            sq.criteria_met = _criteria_verdicts(sq, item.get("criteria_met"))
    # dates: only fill where the search API gave none, and only for chunks that exist
    years = review.get("source_years")
    for cid, year in (years.items() if isinstance(years, dict) else []):
        c = state.chunks.get(cid)
        if c and c.year is None and str(year).isdigit() and 1990 <= int(year) <= datetime.now().year:
            c.year = int(year)
    # claims: replace this round's claims for these sub-questions, keep only those pointing at real chunks
    state.claims = [cl for cl in state.claims if cl.get("sq_id") not in ids]
    for cl in _objects(review.get("claims")):
        c = state.chunks.get(str(cl.get("chunk_id")))
        if c and c.sq_id in ids:
            state.claims.append({**cl, "sq_id": c.sq_id, "url": c.url})
    for con in _objects(review.get("contradictions")):
        chunk_ids = con.get("chunk_ids")
        cids = [x for x in (chunk_ids if isinstance(chunk_ids, list) else []) if str(x) in state.chunks]
        urls = {state.chunks[x].url for x in cids}
        if len(cids) >= 2 and len(urls) >= 2 and con.get("sq_id") in ids:
            if not any(set(cids) == set(x["chunk_ids"]) for x in state.contradictions):
                state.contradictions.append({"sq_id": con["sq_id"], "chunk_ids": cids,
                                             "description": str(con.get("description", ""))[:300],
                                             "follow_up_query": str(con.get("follow_up_query", ""))[:200],
                                             "chased": False, "found_by": "hermes"})
    return skipped


def _objects(raw) -> list[dict]:
    """Only the JSON objects in a list from the LLM; anything else is dropped."""
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _criteria_verdicts(sq: SubQ, raw) -> dict[str, bool]:
    """Hermes' true/false per criterion id. Unknown ids (criteria Hermes invents) and non-boolean answers are
    ignored; a criterion with no verdict is left out, so route() treats it as not met."""
    planned = {c["id"] for c in sq.acceptance_criteria}
    out = {}
    for key, val in (raw.items() if isinstance(raw, dict) else []):
        key = str(key).strip().lower()
        if isinstance(val, str) and val.strip().lower() in ("true", "false"):
            val = val.strip().lower() == "true"
        if key in planned and isinstance(val, bool):
            out[key] = val
    return out


def _num(v) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def numeric_contradictions(state: RunState, tolerance: float = 0.25) -> list[dict]:
    """Same entity + metric + unit + year from two different domains, values differing by more than 25%."""
    groups = {}
    for cl in state.claims:
        val = _num(cl.get("value"))
        if val is None:
            continue
        key = (cl["sq_id"], str(cl.get("entity", "")).lower().strip(), str(cl.get("metric", "")).lower().strip(),
               str(cl.get("unit", "")).lower().strip(), str(cl.get("as_of_year", "")))
        if key[1] and key[2]:
            groups.setdefault(key, []).append((val, cl))
    found = []
    for key, items in groups.items():
        # widest gap between two claims from different domains (one site repeating itself is not a contradiction)
        pairs = [(a, b) for a in items for b in items if a[0] < b[0] and domain_of(a[1]["url"]) != domain_of(b[1]["url"])]
        if not pairs:
            continue
        lo, hi = max(pairs, key=lambda p: p[1][0] - p[0][0])
        if abs(hi[0] - lo[0]) > tolerance * max(abs(hi[0]), abs(lo[0]), 1e-9):
            found.append({"sq_id": key[0], "chunk_ids": [lo[1]["chunk_id"], hi[1]["chunk_id"]],
                          "description": f"{key[1]} {key[2]}: {lo[0]:g} vs {hi[0]:g} {key[3]}".strip(),
                          "follow_up_query": "", "chased": False, "found_by": "code"})
    return found


# =====================================================================
# APOLLO: routing and stopping (pure code)
# =====================================================================
def route(state: RunState, budget) -> tuple[str, str, list[dict]]:
    """Decide what happens next. Returns (next_step, reason, gaps)."""
    limits = state.limits
    gaps = []
    for sq in state.plan.subqs.values():
        if sq.status != "open":
            continue
        sq_gaps, n_domains = [], len(state.distinct_domains(sq.id))
        if n_domains < limits.min_domains_per_sq:   # independent sources: pages on one site count once
            sq_gaps.append({"type": "coverage", "detail": f"only {n_domains} domain(s)"})
        elif not sq.answered:
            sq_gaps.append({"type": "coverage", "detail": sq.missing or "reviewer says the evidence doesn't answer it"})
        unmet = [c for c in sq.acceptance_criteria if not sq.criteria_met.get(c["id"])]
        if unmet:
            sq_gaps.append({"type": "criteria",
                            "detail": "unmet criteria: " + "; ".join(f"{c['id']} {c['text']}" for c in unmet)})
        years = [c.year for c in state.chunks_for(sq.id) if c.year]
        if years and max(years) < limits.stale_before_year:
            sq_gaps.append({"type": "stale", "detail": f"newest dated source is from {max(years)}"})
        for con in state.contradictions:
            if con["sq_id"] == sq.id and not con["chased"]:
                sq_gaps.append({"type": "contradiction", "detail": con["description"], "con": con})

        if not sq_gaps:
            sq.status = "covered"
        elif sq.attempts >= limits.max_attempts_per_sq:
            other = [g["detail"] for g in sq_gaps if g["type"] != "criteria"]   # unmet criteria go in Limitations
            _close(sq, state, f"still had gaps after {sq.attempts} searches"
                              + (": " + "; ".join(other) if other else ", acceptance criteria not met"))
        else:
            gaps.extend({"sq": sq, **g} for g in sq_gaps)

    hard_stop = None
    if state.search_cap_hit or state.search_calls >= limits.max_search_calls:
        hard_stop = "search-call cap reached"
    elif state.round >= limits.max_rounds:
        hard_stop = f"round limit ({limits.max_rounds}) reached"
    elif state.elapsed >= limits.max_seconds:
        hard_stop = f"time limit ({limits.max_seconds}s) reached"
    elif budget.used >= 0.7 * budget.limit:   # keep 30% of the budget for writing
        hard_stop = f"research budget used ({budget.used} tokens)"
    elif state.round > 1 and state.new_domains_this_round == 0:
        hard_stop = "no new domains this round"

    if not gaps:
        return "write", f"complete: {status_summary(state.plan)}", []
    if hard_stop:
        for g in gaps:
            if g["sq"].status == "open":
                _close(g["sq"], state, f"stopped with open gaps ({hard_stop})")
        return "write", f"{hard_stop}: {status_summary(state.plan)}", gaps
    return "search", f"{len(gaps)} gap(s) found", gaps


def status_counts(plan: Plan) -> dict[str, int]:
    return {s: sum(sq.status == s for sq in plan.subqs.values()) for s in ("covered", "partial", "unfillable")}


def status_summary(plan: Plan) -> str:
    return ", ".join(f"{n} {s}" for s, n in status_counts(plan).items())


def close_open(state: RunState, reason: str):
    """Close every still-open sub-question (partial or unfillable), e.g. when the loop stops early."""
    for sq in state.plan.subqs.values():
        if sq.status == "open":
            _close(sq, state, reason)


def _close(sq: SubQ, state: RunState, reason: str):
    sq.status = "partial" if state.chunks_for(sq.id) else "unfillable"
    sq.status_reason = reason


GAP_PRIORITY = {"contradiction": 0, "coverage": 1, "criteria": 2, "stale": 3}


def gap_query(state: RunState, g: dict) -> str:
    """The follow-up query a gap would get, chosen by gap type. No side effects, so it can be shown before approval."""
    year = datetime.now().year
    sq = g["sq"]
    if g["type"] == "contradiction":
        query = g["con"].get("follow_up_query") or f"{sq.query} official figures"
    elif g["type"] == "stale":
        query = f"{sq.query} {year}"
    else:
        query = sq.follow_up or f"{sq.query} data report"
    if query in sq.queries:
        query = f"{query} {year} latest"
    return query


def follow_up_jobs(state: RunState, gaps: list[dict]) -> list[tuple[SubQ, str]]:
    """One targeted query per sub-question, from its highest-priority gap."""
    jobs, done = [], set()
    for g in sorted(gaps, key=lambda g: GAP_PRIORITY[g["type"]]):
        sq = g["sq"]
        if sq.id in done:
            continue
        if g["type"] == "contradiction":
            g["con"]["chased"] = True
        jobs.append((sq, gap_query(state, g)))
        done.add(sq.id)
    return jobs
