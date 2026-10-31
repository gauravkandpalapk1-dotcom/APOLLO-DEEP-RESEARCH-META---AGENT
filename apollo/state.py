"""Shared state passed between the three agents. Plain dataclasses so the whole run is inspectable."""
import re
import time
from dataclasses import dataclass, field

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


@dataclass
class Event:
    kind: str       # plan | round | search | search_fail | review | gap | decision | write | verify | done | error
    text: str
    data: dict | None = None
    t: float = 0.0


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
    new_sources_this_round: int = 0
    stop_reason: str = ""
    review_log: list[dict] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    def event(self, kind: str, text: str, data: dict | None = None) -> Event:
        return Event(kind, text, data, round(self.elapsed, 1))

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
