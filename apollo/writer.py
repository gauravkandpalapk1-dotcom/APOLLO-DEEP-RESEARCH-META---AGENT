"""Two-stage writing: section writers (each sees only its own evidence) -> verification -> assembler."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .agents import status_counts
from .config import CHUNK_DATA_RULE
from .llm import LLM
from .state import Chunk, RunState, Section, tokens

CITE = re.compile(r"\[(C\d+(?:\s*,\s*C\d+)*)\]")
LIMITATION_NEG = re.compile(r"\b(no|not|insufficient|lack(?:ed|ing)?|limited|unavailable|could not|did not)\b", re.I)
LIMITATION_SUBJ = re.compile(r"\b(sources?|evidence|data)\b", re.I)


@dataclass
class VerifiedSentence:
    section: str
    text: str                 # with internal [C3] citations
    chunk_ids: list[str]
    verdict: str              # kept | removed
    reason: str


@dataclass
class SectionResult:
    section: Section
    text: str
    kept: int = 0
    removed: list = field(default_factory=list)   # (sentence, reason)
    problems: list = field(default_factory=list)  # why the verifier returned too few verdicts, for system events
    sentences: list = field(default_factory=list)  # VerifiedSentence records in report order


# ---------------- stage 1: section writers ----------------
WRITER_SYSTEM = ("You are a section writer for a research report. You write only from the evidence chunks provided "
                 "and never add facts from your own knowledge. " + CHUNK_DATA_RULE)


def section_evidence(state: RunState, section: Section) -> list[Chunk]:
    out = []
    for qid in section.subq_ids:
        out.extend(state.top_chunks(qid, state.limits.chunks_per_sq))
    return out


def write_section(state: RunState, llm: LLM, section: Section) -> tuple[str, list[Chunk]]:
    chunks = section_evidence(state, section)
    notes = []
    for qid in section.subq_ids:
        sq = state.plan.subqs[qid]
        line = f"- {sq.question}"
        if sq.status == "unfillable":
            line += "  [NO usable sources were found: say so in one sentence, add nothing else about it]"
        elif sq.status == "partial":
            line += f"  [evidence is incomplete ({sq.missing or 'thin coverage'}): state this limitation]"
        notes.append(line)
    for con in state.contradictions:
        if con["sq_id"] in section.subq_ids:
            notes.append(f"- Sources disagree: {con['description']} (chunks {', '.join(con['chunk_ids'])}). "
                         f"Report both with citations.")
    evidence = "\n".join(f'<chunk id="{c.id}" url="{c.url}" published="{c.published or c.year or "unknown"}">'
                         f'{c.text[:1500]}</chunk>' for c in chunks) or "(no evidence)"
    user = f"""Report topic: {state.plan.topic}
Section: {section.title}

Questions this section must answer:
{chr(10).join(notes)}

Evidence:
{evidence}

Write 2 to 4 paragraphs of markdown prose with no headings.
Rules:
1. Every sentence that states a fact ends with citations like [C3] or [C3, C9], placed before the full stop.
2. Cite only chunks whose text directly supports that sentence.
3. If the evidence does not cover something, say the sources found did not cover it. Never fill gaps from memory.
4. Where sources disagree, give both figures with their citations.
5. Give the year of a figure when the chunk states it."""
    return llm.chat(WRITER_SYSTEM, user, temperature=0.3, role="strong"), chunks


# ---------------- verification: code checks, then an LLM support check ----------------
def split_sentences(paragraph: str) -> list[str]:
    paragraph = re.sub(r"\.\s*((?:\[C\d+(?:\s*,\s*C\d+)*\]\s*)+)", lambda m: " " + m.group(1).strip() + ". ", paragraph)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", paragraph) if s.strip()]


def cited_ids(sentence: str) -> list[str]:
    return [i.strip() for m in CITE.finditer(sentence) for i in m.group(1).split(",")]


def is_limitation(sentence: str) -> bool:
    return bool(LIMITATION_NEG.search(sentence) and LIMITATION_SUBJ.search(sentence))


VERIFY_SYSTEM = ("You check whether source text supports a sentence. Be strict: paraphrase is fine, added facts are not. "
                 + CHUNK_DATA_RULE)


def verify(text: str, allowed: dict[str, Chunk], llm: LLM,
           section: str = "") -> tuple[str, list[VerifiedSentence], list[str]]:
    """Returns (clean_text, records, problems): one VerifiedSentence per drafted sentence, kept or removed with a
    reason. Every kept factual sentence cites a real chunk and the verifier confirmed the chunk supports it;
    sentences the verifier never judged are removed."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    plan = []                                    # paragraphs, each a list of [sentence, status, chunk_ids, reason]
    to_check = []
    for p in paragraphs:
        row = []
        for s in split_sentences(p):
            ids = cited_ids(s)
            bad = [i for i in ids if i not in allowed]
            for b in bad:                         # strip citations to chunks this section never had
                s = re.sub(rf"\b{b}\b\s*,?\s*", "", s)
            s = re.sub(r"\[\s*,?\s*\]", "", s).replace(" .", ".").strip()
            good = [i for i in ids if i in allowed]
            if not good:
                row.append([s, "keep", [], NO_CLAIM] if is_limitation(s) else [s, "drop", [], "no valid citation"])
                continue
            entry = [s, "check", good, ""]
            row.append(entry)
            to_check.append(entry)
        plan.append(row)

    problems = []
    missing = _check(to_check, VERIFY_BATCH, allowed, llm, problems)
    missing = _check(missing, VERIFY_RETRY_BATCH, allowed, llm, problems)   # one retry, smaller batches
    for entry in missing:                         # never keep a factual sentence nobody verified
        entry[1], entry[3] = "drop", UNVERIFIED

    out_paras = [" ".join(e[0] for e in row if e[1] == "keep") for row in plan]
    records = [VerifiedSentence(section, s, ids, "kept" if status == "keep" else "removed", reason)
               for row in plan for s, status, ids, reason in row]
    return "\n\n".join(p for p in out_paras if p), records, problems


VERIFY_BATCH, VERIFY_RETRY_BATCH = 6, 3
UNVERIFIED = "could not be verified"
NO_CLAIM = "says the sources did not cover something; makes no factual claim, so no citation is needed"


def _check(entries: list, size: int, allowed: dict[str, Chunk], llm: LLM, problems: list) -> list:
    """Ask the verifier about entries in batches; mark each keep or drop with a reason. Returns the entries that
    got no verdict, and records why in problems (the exception, or an excerpt of a reply with too few verdicts)."""
    missing = []
    for start in range(0, len(entries), size):
        batch = entries[start:start + size]
        items = []
        for i, (s, _, ids, _) in enumerate(batch):
            src = "\n".join(f'<chunk id="{cid}">{allowed[cid].text[:1500]}</chunk>' for cid in ids)
            items.append(f"### Item {i}\nSentence: {CITE.sub('', s).strip()}\nSources:\n{src}")
        res, verdict = None, {}
        try:
            res = llm.json(VERIFY_SYSTEM, "\n\n".join(items) +
                           '\n\nFor each item, is every fact in the sentence stated in its sources?\n'
                           'JSON: {"results": [{"i": 0, "supported": true, "reason": "short"}]}', role="fast")
            results = res.get("results") if isinstance(res, dict) else None
            for r in results if isinstance(results, list) else []:
                if isinstance(r, dict) and str(r.get("i", "")).isdigit() and int(r["i"]) < len(batch):
                    verdict[int(r["i"])] = r
            if len(verdict) < len(batch):
                problems.append(f"verifier gave {len(verdict)} verdict(s) for {len(batch)} sentence(s); "
                                f"reply: {json.dumps(res, ensure_ascii=False, default=str)[:200]}")
        except Exception as e:
            problems.append(f"verifier call failed for {len(batch)} sentence(s): {type(e).__name__}: {str(e)[:200]}")
        for i, entry in enumerate(batch):
            r = verdict.get(i)
            if r is None:
                missing.append(entry)
            elif r.get("supported") is False:
                entry[1], entry[3] = "drop", str(r.get("reason") or "not supported by cited source")
            else:
                entry[1], entry[3] = "keep", str(r.get("reason") or "supported by the cited source")
    return missing


def removed_of(records: list[VerifiedSentence]) -> list[tuple[str, str]]:
    return [(v.text, v.reason) for v in records if v.verdict == "removed"]


def write_and_verify(state: RunState, llm: LLM, section: Section) -> SectionResult:
    draft, chunks = write_section(state, llm, section)
    text, records, problems = verify(draft, {c.id: c for c in chunks}, llm, section.title)
    return SectionResult(section, text, sum(v.verdict == "kept" for v in records), removed_of(records), problems,
                         records)


def write_all(state: RunState, llm: LLM) -> list[SectionResult]:
    with ThreadPoolExecutor(max_workers=state.limits.parallel_searches) as pool:
        return list(pool.map(lambda s: write_and_verify(state, llm, s), state.plan.sections))


# ---------------- stage 2: assembler ----------------
def _jaccard(a: str, b: str) -> float:
    ta, tb = tokens(CITE.sub("", a)), tokens(CITE.sub("", b))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def dedupe(results: list[SectionResult]) -> int:
    """Remove sentences that repeat an earlier section's sentence almost word for word; their records are
    marked removed with reason "duplicate"."""
    seen, dropped = [], 0
    for r in results:
        paras = []
        for p in r.text.split("\n\n"):
            keep = []
            for s in split_sentences(p):
                if any(_jaccard(s, t) >= 0.75 for t in seen):
                    dropped += 1
                    rec = next((v for v in r.sentences if v.verdict == "kept" and v.text.strip() == s.strip()), None)
                    if rec:
                        rec.verdict, rec.reason = "removed", "duplicate"
                    else:
                        r.sentences.append(VerifiedSentence(r.section.title, s, cited_ids(s), "removed", "duplicate"))
                    continue
                keep.append(s)
                seen.append(s)
            if keep:
                paras.append(" ".join(keep))
        r.text = "\n\n".join(paras)
    return dropped


ASSEMBLER_SYSTEM = "You are the editor assembling a cited research report. You summarise; you never add new facts."


def intro_and_conclusion(state: RunState, llm: LLM, results: list[SectionResult]) -> tuple[str, str]:
    body = "\n\n".join(f"## {r.section.title}\n{r.text}" for r in results if r.text)
    res = llm.json(ASSEMBLER_SYSTEM, f"""Topic: {state.plan.topic}

Report body:
{body}

Write an introduction (one paragraph saying what the report covers) and a conclusion (one or two paragraphs
on the main findings and open questions). Reuse the body's citations exactly, e.g. [C3], on every factual sentence.
Do not state anything the body does not state.
JSON: {{"introduction": "...", "conclusion": "..."}}""", role="strong")
    return str(res.get("introduction", "")), str(res.get("conclusion", ""))


def methodology(state: RunState, models: dict | None, kept: int, removed: int, unverified: int) -> str:
    """How the report was made, written by code from the run's own logs (no LLM)."""
    plan = state.plan
    perspectives = list(dict.fromkeys(sq.perspective for sq in plan.subqs.values() if sq.perspective))
    lines = [f"- Topic: {plan.topic}.",
             f"- Perspectives: {', '.join(perspectives) or 'none recorded'}.",
             f"- Plan: {len(plan.sections)} sections, {len(plan.subqs)} sub-questions."]
    for r in state.round_log:
        queries = sum(r["queries"].values())
        by_type = ", ".join(f"{t} {n}" for t, n in sorted(r["queries"].items())) or "none"
        gaps = ", ".join(f"{t} {n}" for t, n in sorted(r["gaps"].items())) or "none"
        decision = "search again" if r["decision"] == "search" else "stop"
        lines.append(f"- Round {r['round']}: {queries} search call(s) ({by_type}); {r['new_sources']} new source(s), "
                     f"{r['new_domains']} new domain(s); gaps: {gaps}; Apollo decided to {decision} ({r['reason']}).")
    lines.append(f"- Stop reason: {state.stop_reason or 'not recorded'}.")
    if models and models.get("active"):
        roles = ", ".join(f"{role} = {label}" for role, label in models["active"].items())
        lines.append(f"- Models: {roles} ({models.get('switches', 0)} model or key switch(es) during the run).")
    else:
        lines.append("- Models: not recorded.")
    lines.append(f"- Verification: {kept} sentence(s) kept, {removed} removed"
                 + (f" (including {unverified} the verifier could not check)" if unverified else "") + ".")
    return "\n".join(lines)


def limitations(state: RunState, results: list[SectionResult], unverified: int = 0) -> str:
    lines = [f"- Stop condition: {state.stop_reason}."]
    for sq in state.plan.subqs.values():
        if sq.status in ("partial", "unfillable"):
            label = "No usable sources found" if sq.status == "unfillable" else "Incomplete evidence"
            unmet = [c["text"] for c in sq.acceptance_criteria if not sq.criteria_met.get(c["id"])]
            if sq.status == "partial" and unmet:   # the criteria say what is missing; Hermes' text would repeat them
                lines.append(f"- {label} for: *{sq.question}*. Unmet criteria: " + "; ".join(unmet) + ".")
            else:
                lines.append(f"- {label} for: *{sq.question}* ({sq.status_reason}).")
    for con in state.contradictions:
        lines.append(f"- Sources disagreed on {con['description']} (see the cited figures in that section).")
    if state.failed_searches:
        lines.append(f"- {len(state.failed_searches)} search call(s) failed or timed out and were skipped.")
    removed = sum(why != UNVERIFIED for r in results for _, why in r.removed)
    if removed:
        lines.append(f"- {removed} drafted sentence(s) were removed in verification (no valid citation, or the cited source did not support them).")
    if unverified:
        lines.append(f"- {unverified} drafted sentence(s) were removed because the verifier could not check them.")
    return "\n".join(lines)


def render_citations(markdown: str, chunks: dict[str, Chunk]) -> tuple[str, list[dict]]:
    """Replace [C3, C9] with numbered, clickable [1][2] references ordered by first use."""
    numbers, refs = {}, []

    def repl(m):
        out = []
        for cid in dict.fromkeys(i.strip() for i in m.group(1).split(",")):
            c = chunks.get(cid)
            if not c:
                continue
            if c.url not in numbers:
                numbers[c.url] = len(numbers) + 1
                refs.append({"n": numbers[c.url], "url": c.url, "title": c.title,
                             "type": c.source_type, "published": c.published or (str(c.year) if c.year else "")})
            out.append(f"[[{numbers[c.url]}]]({c.url})")
        return "".join(out)

    return CITE.sub(repl, markdown), refs


def sentence_evidence(records: list[VerifiedSentence], chunks: dict[str, Chunk], refs: list[dict]) -> list[dict]:
    """Each sentence with its citations turned into the report's reference numbers, and the exact passages it cites.
    A cited chunk whose source is not in the references (only possible for removed sentences) has ref_number None."""
    number = {r["url"]: r["n"] for r in refs}

    def repl(m):
        cs = [chunks.get(cid.strip()) for cid in dict.fromkeys(m.group(1).split(","))]
        return "".join(f"[[{number[c.url]}]]({c.url})" for c in cs if c and c.url in number)

    out = []
    for v in records:
        evidence = [{"ref_number": number.get(c.url), "chunk_id": c.id, "url": c.url, "title": c.title,
                     "domain": c.domain, "published": c.published or (str(c.year) if c.year else ""),
                     "passage": c.text}
                    for c in (chunks.get(cid) for cid in dict.fromkeys(v.chunk_ids)) if c]
        text = re.sub(r"\s+([.!?])", r"\1", CITE.sub(repl, v.text)).strip()
        out.append({"section": v.section, "text_with_refs": text, "verdict": v.verdict, "reason": v.reason,
                    "chunk_ids": list(v.chunk_ids), "evidence": evidence})
    return out


REF_LINK = re.compile(r"\[\[\d+\]\]\([^)]*\)")
MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~$])")


def plain_sentence(text_with_refs: str) -> str:
    """The sentence without its [[n]](url) reference links, e.g. for an expander label."""
    return re.sub(r"\s+([.!?,;])", r"\1", REF_LINK.sub("", text_with_refs)).strip()


def highlight(passage: str, sentence: str) -> str:
    """Markdown passage with the words it shares with the sentence in bold (stopwords and short words ignored).
    The passage is web text, so markdown characters in it are escaped first."""
    shared = tokens(plain_sentence(sentence)) & tokens(passage)
    return re.sub(r"[A-Za-z0-9]+", lambda m: f"**{m.group()}**" if m.group().lower() in shared else m.group(),
                  MD_SPECIAL.sub(r"\\\1", passage))


def assemble(state: RunState, llm: LLM, results: list[SectionResult]):
    dropped = dedupe(results)
    used_ids = {i for r in results for i in cited_ids(r.text)}
    allowed = {i: state.chunks[i] for i in used_ids if i in state.chunks}
    intro, concl = "", ""
    intro_removed, problems, intro_records, concl_records = [], [], [], []
    try:
        intro_raw, concl_raw = intro_and_conclusion(state, llm, results)
        intro, intro_records, p1 = verify(intro_raw, allowed, llm, "Introduction")
        concl, concl_records, p2 = verify(concl_raw, allowed, llm, "Conclusion")
        intro_removed, problems = removed_of(intro_records) + removed_of(concl_records), p1 + p2
    except Exception as e:
        intro = f"*Introduction could not be generated ({str(e)[:80]}).*"
    if not intro:  # LLM intro was empty or fully removed in verification: list the sections (no facts, no citations)
        intro = "This report covers: " + "; ".join(r.section.title for r in results) + "."

    parts = [f"# {state.plan.topic}", "## Introduction", intro or "*No introduction.*"]
    for r in results:
        parts += [f"## {r.section.title}", r.text or "*The sources found did not support any statements for this section.*"]
    unverified = sum(why == UNVERIFIED for r in results for _, why in r.removed) + \
        sum(why == UNVERIFIED for _, why in intro_removed)
    kept = sum(v.verdict == "kept" for r in results for v in r.sentences)               # after duplicates
    removed = sum(len(r.removed) for r in results) + len(intro_removed)
    models = llm.status() if hasattr(llm, "status") else None
    parts += ["## Conclusion", concl or "*No conclusion.*",
              "## Methodology", methodology(state, models, kept, removed, unverified),
              "## Limitations", limitations(state, results, unverified)]
    md, refs = render_citations("\n\n".join(parts), state.chunks)
    md += "\n\n## References\n\n" + "\n".join(
        f"{r['n']}. [{r['title']}]({r['url']}) — {r['type']}{', ' + r['published'] if r['published'] else ''}"
        for r in refs)
    records = intro_records + [v for r in results for v in r.sentences] + concl_records
    sentences = sentence_evidence(records, state.chunks, refs)
    stats = {
        "sentences_kept": kept,
        "sentences_removed": removed,
        "sentences_unverified_removed": unverified,
        "duplicates_removed": dropped,
        "sources_cited": len(refs),
        **{f"subqs_{s}": n for s, n in status_counts(state.plan).items()},
    }
    removed_log = [{"section": r.section.title, "sentence": s, "reason": why} for r in results for s, why in r.removed]
    removed_log += [{"section": "Intro/Conclusion", "sentence": s, "reason": why} for s, why in intro_removed]
    return md, refs, stats, removed_log, problems, sentences
