"""Athena's tools. Each returns a list of Doc; failures raise SearchError, which Athena catches
and logs, so a failed or empty search never crashes the run."""
import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from .config import (ACADEMIC_DOMAINS, ACADEMIC_SECOND_LEVEL, ACADEMIC_SUFFIXES, FILING_PREFIXES, GOVERNMENT_DOMAINS,
                     GOVERNMENT_SUFFIXES, Limits, env)
from .state import domain_of


@dataclass
class Doc:
    title: str
    url: str
    text: str
    source_type: str          # web | news | arxiv | edgar
    published: str | None     # date string if the API gives one
    domain: str = field(init=False)
    source_class: str = field(init=False)

    def __post_init__(self):
        self.domain = domain_of(self.url)
        self.source_class = classify_source(self.url, self.source_type)

    @property
    def year(self) -> int | None:
        m = re.search(r"(19|20)\d{2}", self.published or "")
        return int(m.group()) if m else None


def _in(domain: str, names) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in names)


def classify_source(url: str, source_type: str) -> str:
    """government | academic | filing | news | other, from the editable lists in config.py.
    Order: SEC filing, then anything from the news search, then government, academic, other."""
    domain = domain_of(url)
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        path = ""
    if any((domain + path).startswith(p) for p in FILING_PREFIXES):
        return "filing"
    if source_type == "news":
        return "news"
    if domain.endswith(GOVERNMENT_SUFFIXES) or _in(domain, GOVERNMENT_DOMAINS):
        return "government"
    parts = domain.split(".")
    if (domain.endswith(ACADEMIC_SUFFIXES) or _in(domain, ACADEMIC_DOMAINS)
            or (len(parts) >= 3 and parts[-2] in ACADEMIC_SECOND_LEVEL)):
        return "academic"
    return "other"


class SearchError(Exception):
    pass


def _retry(fn, limits: Limits):
    last = None
    for attempt in range(limits.search_retries + 1):
        try:
            return fn()
        except (requests.RequestException, ValueError, ET.ParseError) as e:
            last = e
            resp = getattr(e, "response", None)
            if resp is not None and 400 <= resp.status_code < 500:
                break  # client errors (bad key, quota, bad request) won't fix themselves
            time.sleep(1.5 * (attempt + 1))
    raise SearchError(str(last))


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


# ---------- Tavily: general web and news, with key rotation ----------
_tavily_lock = threading.Lock()
_tavily_state = {"current": 0, "dead": set(), "cooldown": {}}


def tavily_keys() -> list[str]:
    raw = env("TAVILY_API_KEYS", required=False) or env("TAVILY_API_KEY", required=False) or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise SearchError("No Tavily key: set TAVILY_API_KEYS in .env")
    return keys


def _tavily_once(key: str, query: str, limits: Limits, topic: str) -> dict:
    r = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"api_key": key, "query": query, "topic": topic, "max_results": limits.results_per_query,
              "include_raw_content": True, "search_depth": "advanced"},
        timeout=limits.search_timeout,
    )
    r.raise_for_status()
    return r.json()


def tavily(query: str, limits: Limits, topic: str = "general") -> list[Doc]:
    keys = tavily_keys()
    errors = []
    for _ in range(len(keys) * 2):
        now = time.time()
        with _tavily_lock:
            order = [(_tavily_state["current"] + i) % len(keys) for i in range(len(keys))]
            usable = [i for i in order if i not in _tavily_state["dead"]
                      and _tavily_state["cooldown"].get(i, 0) <= now]
        if not usable:
            break
        i = usable[0]
        try:
            data = _retry(lambda: _tavily_once(keys[i], query, limits, topic), limits)
        except SearchError as e:
            msg = str(e)
            code = re.search(r"\b(4\d\d|5\d\d)\b", msg)
            code = int(code.group()) if code else None
            with _tavily_lock:
                if code in (401, 403):
                    _tavily_state["dead"].add(i)             # bad key: never again this run
                elif code in (429, 432, 433) or "limit" in msg.lower() or "quota" in msg.lower():
                    _tavily_state["cooldown"][i] = time.time() + 60
                else:
                    raise                                     # network or other error: a failed search
                _tavily_state["current"] = (i + 1) % len(keys)
            errors.append(f"Tavily key #{i + 1}: {msg[:80]}")
            continue
        with _tavily_lock:
            _tavily_state["current"] = i
        docs = []
        for item in data.get("results", []):
            text = (item.get("raw_content") or item.get("content") or "").strip()
            if item.get("url") and text:
                docs.append(Doc(item.get("title") or item["url"], item["url"], text[:20000],
                                "news" if topic == "news" else "web", item.get("published_date")))
        return docs
    raise SearchError("All Tavily keys failed: " + "; ".join(errors or ["every key is dead or cooling down"]))


# ---------- arXiv: research papers (abstracts) ----------
def arxiv(query: str, limits: Limits) -> list[Doc]:
    def call():
        r = requests.get("https://export.arxiv.org/api/query",
                         params={"search_query": f"all:{query}", "max_results": limits.results_per_query,
                                 "sortBy": "relevance"},
                         timeout=limits.search_timeout)
        r.raise_for_status()
        return ET.fromstring(r.text)

    root = _retry(call, limits)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    docs = []
    for e in root.findall("a:entry", ns):
        title = " ".join((e.findtext("a:title", "", ns)).split())
        summary = " ".join((e.findtext("a:summary", "", ns)).split())
        url = e.findtext("a:id", "", ns)
        if url and summary:
            docs.append(Doc(title, url, f"{title}. {summary}", "arxiv", e.findtext("a:published", None, ns)))
    return docs


# ---------- SEC EDGAR full-text search: company filings ----------
def edgar(query: str, limits: Limits) -> list[Doc]:
    # SEC requires a User-Agent with contact details: "Name email@example.com"
    headers = {"User-Agent": env("SEC_USER_AGENT")}

    def call():
        r = requests.get("https://efts.sec.gov/LATEST/search-index",
                         params={"q": query, "forms": "10-K,10-Q,8-K"}, headers=headers,
                         timeout=limits.search_timeout)
        r.raise_for_status()
        return r.json()

    hits = _retry(call, limits).get("hits", {}).get("hits", [])[:3]
    terms = [t.lower() for t in re.findall(r"\w{4,}", query)]
    docs = []
    for h in hits:
        src = h.get("_source", {})
        try:
            adsh, filename = h["_id"].split(":", 1)
            cik = int(src["ciks"][0])
        except (KeyError, ValueError, IndexError):
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}"
        try:
            page = _retry(lambda: requests.get(url, headers=headers, timeout=limits.search_timeout), limits)
            text = strip_html(page.text)
        except SearchError:
            continue
        # filings are huge: keep windows around the query terms
        windows, low = [], text.lower()
        for t in terms:
            i = low.find(t)
            if i != -1:
                windows.append(text[max(0, i - 600): i + 900])
            if len(windows) >= 4:
                break
        if windows:
            name = (src.get("display_names") or ["SEC filing"])[0]
            docs.append(Doc(f"{name} — {src.get('form', 'filing')}", url, " … ".join(windows), "edgar",
                            src.get("file_date")))
        time.sleep(0.2)  # stay well under SEC's rate limit
    return docs


def run_search(query: str, source_type: str, limits: Limits) -> list[Doc]:
    if source_type == "web":
        return tavily(query, limits, "general")
    if source_type == "news":
        return tavily(query, limits, "news")
    if source_type == "arxiv":
        return arxiv(query, limits)
    if source_type == "edgar":
        return edgar(query, limits)
    raise SearchError(f"Unknown source type {source_type}")
