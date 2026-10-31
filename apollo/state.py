"""Shared state passed between the three agents. Plain dataclasses so the whole run is inspectable."""
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse
from uuid import uuid4

from .config import Limits

STOP = set("the a an of in on for to and or with by from at as is are was were be this that what which how "
           "does do did its their into about over under than more most".split())


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        end = min(len(text), i + size)
        if end < len(text):                      # try to end on a sentence boundary
            dot = text.rfind(". ", i + size // 2, end)
            end = dot + 1 if dot != -1 else end
        out.append(text[i:end].strip())
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return out


def domain_of(url: str) -> str:
    """Lowercased host without a leading "www.", e.g. https://WWW.PIB.gov.in/x -> pib.gov.in."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def relevance(chunk: str, question: str) -> float:
    q = tokens(question)
    return len(q & tokens(chunk)) / len(q) if q else 0.0


@dataclass
class SubQ:
    id: str
    section_id: str
    question: str
    query: str
    source_types: list[str]
    status: str = "open"          # open | covered | partial | unfillable
    attempts: int = 0
    queries: list[str] = field(default_factory=list)
    answered: bool = False        # Hermes' judgement from the evidence
    missing: str = ""
    follow_up: str = ""
    status_reason: str = ""
    perspective: str = ""         # stakeholder view this question is asked from (farmer, regulator, ...)
    acceptance_criteria: list[dict] = field(default_factory=list)  # 0 to 3 {"id": "c1", "text": ...} checkable statements
    criteria_met: dict[str, bool] = field(default_factory=dict)    # Hermes' latest verdict per criterion id
    added_in_round: int = 1


@dataclass
class Section:
    id: str
    title: str
    subq_ids: list[str]


@dataclass
class Plan:
    topic: str
    sections: list[Section]
    subqs: dict[str, SubQ]
    warnings: list[str] = field(default_factory=list)   # malformed planner output that was skipped


@dataclass
class Chunk:
    id: str
    sq_id: str
    url: str
    title: str
    text: str
    source_type: str
    published: str | None
    year: int | None
    score: float
    round: int
    domain: str = ""                 # filled from url when not given
    source_class: str = "other"      # government | academic | filing | news | other (search.classify_source)

    def __post_init__(self):
        if not self.domain:
            self.domain = domain_of(self.url)


@dataclass
class Event:
    kind: str       # plan | round | search | search_fail | review | gap | decision | write | verify | done | error
    text: str
    data: dict | None = None
    t: float = 0.0
    actor: str = "system"   # who did it: one of ACTORS


ACTORS = ("apollo", "athena", "hermes", "writer", "oracle", "system")


@dataclass
class RunState:
    plan: Plan
    limits: Limits
    started: float = field(default_factory=time.time)
    round: int = 0
    chunks: dict[str, Chunk] = field(default_factory=dict)
    seen: set = field(default_factory=set)            # (sq_id, url) pairs already stored
    claims: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    failed_searches: list[dict] = field(default_factory=list)
    unavailable_sources: dict[str, str] = field(default_factory=dict)   # source type -> what is wrong (bad key, quota)
    new_sources_this_round: int = 0
    new_domains_this_round: int = 0                   # new (sub-question, domain) pairs found this round
    search_calls: int = 0                             # search API calls dispatched so far (capped by Limits)
    search_cap_hit: bool = False
    stop_reason: str = ""
    review_log: list[dict] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}")
    search_log: list[dict] = field(default_factory=list)   # every search call: round, sq, query, source, urls, status
    round_log: list[dict] = field(default_factory=list)    # per round: queries per source, new sources/domains,
                                                           # gaps, Apollo's decision
    # step-by-step runs (Apollo.start_run / run_round / approve_gaps / finish)
    next_jobs: list = field(default_factory=list)          # (SubQ, query) pairs the next round will search
    pending_gaps: list[dict] = field(default_factory=list) # last round's gaps: route's gap + "id" and proposed "query"
    done: bool = False                                     # True once the research loop should stop and write

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    def event(self, kind: str, text: str, data: dict | None = None, actor: str = "system") -> Event:
        return Event(kind, text, data, round(self.elapsed, 1), actor if actor in ACTORS else "system")

    def chunks_for(self, sq_id: str) -> list[Chunk]:
        return [c for c in self.chunks.values() if c.sq_id == sq_id]

    def top_chunks(self, sq_id: str, n: int) -> list[Chunk]:
        """Best chunks for a sub-question, one per URL first so evidence is diverse."""
        ranked = sorted(self.chunks_for(sq_id), key=lambda c: c.score, reverse=True)
        picked, urls = [], set()
        for c in ranked:
            if c.url not in urls:
                picked.append(c)
                urls.add(c.url)
        for c in ranked:
            if c not in picked:
                picked.append(c)
        return picked[:n]

    def distinct_urls(self, sq_id: str) -> set[str]:
        return {c.url for c in self.chunks_for(sq_id)}

    def distinct_domains(self, sq_id: str) -> set[str]:
        return {c.domain for c in self.chunks_for(sq_id)}
