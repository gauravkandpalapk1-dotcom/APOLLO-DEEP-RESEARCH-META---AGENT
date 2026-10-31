"""Offline tests of live hardening: clear messages for Tavily key and quota problems, per-source timeouts,
empty arXiv / EDGAR results handled as "no results", a single warning when a source is down, and the citation
rules every writer prompt carries. Run: python -m tests.test_hardening"""
import os
import sys

import requests

from apollo import agents, search
from apollo.agents import athena, validate_plan
from apollo.config import Limits
from apollo.state import RunState
from apollo.writer import WRITING_RULES, limitations
from scripts import baseline

LIMITS = Limits(search_retries=0)
ATOM = '<feed xmlns="http://www.w3.org/2005/Atom">{}</feed>'


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self.payload, self.text = status, payload, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)

    def json(self):
        return self.payload


def _with(name, fake, fn):
    real = getattr(requests, name)
    setattr(requests, name, fake)
    try:
        return fn()
    finally:
        setattr(requests, name, real)


def _reset_tavily(keys):
    os.environ["TAVILY_API_KEYS"] = keys
    search._tavily_state.update({"current": 0, "dead": set(), "cooldown": {}, "reasons": {}})


def _raises(fn, exc):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError(f"expected {exc.__name__}")


def test_tavily_problems_are_named_without_keys():
    _reset_tavily("secret-key-AAA,secret-key-BBB")
    statuses = {"secret-key-AAA": 401, "secret-key-BBB": 432}
    msg = _with("post", lambda url, headers=None, json=None, timeout=None: Resp(statuses[json["api_key"]]),
                lambda: _raises(lambda: search.tavily("q", LIMITS), search.SourceUnavailable))
    assert "key #1 was rejected (HTTP 401)" in msg and "key #2 is out of credits" in msg, msg
    assert "TAVILY_API_KEYS" in msg and "secret-key" not in msg, msg
    _reset_tavily("")
    os.environ.pop("TAVILY_API_KEY", None)
    assert "TAVILY_API_KEYS" in _raises(lambda: search.tavily("q", LIMITS), search.SourceUnavailable)
    _reset_tavily("k1")                                       # a network error is a failed search, not "unavailable"

    def boom(url, headers=None, json=None, timeout=None):
        raise requests.ConnectionError("connection reset")
    msg = _with("post", boom, lambda: _raises(lambda: search.tavily("q", LIMITS), search.SearchError))
    assert "connection reset" in msg


def test_per_source_timeouts_reach_the_http_calls():
    _reset_tavily("k1")
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen[json["topic"]] = timeout
        return Resp(200, {"results": []})
    limits = Limits(search_retries=0, source_timeouts={"web": 41, "news": 42, "arxiv": 43, "edgar": 44})
    _with("post", post, lambda: (search.tavily("q", limits, "general"), search.tavily("q", limits, "news")))
    assert seen == {"general": 41, "news": 42}, seen

    def get(url, params=None, headers=None, timeout=None):
        seen[url] = timeout
        return Resp(200, {"hits": {"hits": []}}, ATOM.format(""))
    os.environ["SEC_USER_AGENT"] = "Test Person test.person@university.ac.in"
    _with("get", get, lambda: (search.arxiv("q", limits), search.edgar("q", limits)))
    assert seen["https://export.arxiv.org/api/query"] == 43 and seen["https://efts.sec.gov/LATEST/search-index"] == 44
    assert Limits().timeout("web") == 30 and Limits().timeout("unknown") == Limits().search_timeout


def test_arxiv_empty_error_and_query_cleanup():
    sent = []

    def get(url, params=None, headers=None, timeout=None):
        sent.append(params["search_query"])
        return Resp(200, text=ATOM.format(""))
    assert _with("get", get, lambda: search.arxiv('"crop yield": (India)', LIMITS)) == []      # empty = no results
    assert sent == ["all:crop yield India"], sent
    assert search.arxiv('":()"', LIMITS) == []                                                  # nothing left to ask
    error_feed = ATOM.format('<entry><id>http://arxiv.org/api/errors#incorrect_id_format</id>'
                             '<title>Error</title><summary>incorrect id format</summary></entry>')
    msg = _with("get", lambda url, params=None, headers=None, timeout=None: Resp(200, text=error_feed),
                lambda: _raises(lambda: search.arxiv("q", LIMITS), search.SearchError))
    assert "arXiv rejected the query: incorrect id format" in msg


def test_edgar_user_agent_and_non_us_topics():
    for agent in ("", "Your Name your.email@example.com"):
        os.environ["SEC_USER_AGENT"] = agent
        assert "SEC_USER_AGENT" in _raises(lambda: search.edgar("q", LIMITS), search.SourceUnavailable)
    os.environ["SEC_USER_AGENT"] = "Test Person test.person@university.ac.in"
    get = lambda url, params=None, headers=None, timeout=None: Resp(200, {"hits": {"total": {"value": 0}, "hits": []}})
    assert _with("get", get, lambda: search.edgar("Indian panchayat drones", LIMITS)) == []     # no US filings
    assert _with("get", lambda *a, **k: Resp(200, {"hits": None}), lambda: search.edgar("q", LIMITS)) == []
    refused = lambda url, params=None, headers=None, timeout=None: Resp(403)
    msg = _with("get", refused, lambda: _raises(lambda: search.edgar("q", LIMITS), search.SourceUnavailable))
    assert "HTTP 403" in msg


def test_athena_warns_once_when_a_source_is_down():
    raw = {"sections": [{"title": f"S{i}", "sub_questions": [{"question": f"Question {i}?"}]} for i in (1, 2)]}
    state = RunState(plan=validate_plan("topic", raw, Limits(enabled_sources=("web",))),
                     limits=Limits(enabled_sources=("web",), parallel_searches=1))

    def down(query, source_type, limits):
        raise search.SourceUnavailable("Tavily is unavailable: key #1 is out of credits for its plan (HTTP 432)")
    real = agents.run_search
    agents.run_search = down
    try:
        events = list(athena(state, [(sq, sq.query) for sq in state.plan.subqs.values()]))
    finally:
        agents.run_search = real
    warnings = [e for e in events if e.kind == "warning"]
    assert len(warnings) == 1 and "web" in warnings[0].text and "HTTP 432" in warnings[0].text
    assert sum(e.kind == "search_fail" for e in events) == 2 and len(state.failed_searches) == 2
    state.stop_reason = "complete"
    assert "The web source was unavailable during this run: Tavily is unavailable" in limitations(state, [])


def test_every_writer_prompt_requires_cited_summaries():
    assert "Summary, overview and transition sentences" in WRITING_RULES
    from apollo import writer
    import inspect
    assert "{WRITING_RULES}" in inspect.getsource(writer.write_section)
    assert "{WRITING_RULES}" in inspect.getsource(baseline.write_report)
    assembler = inspect.getsource(writer.intro_and_conclusion)
    assert "Leave out any sentence you" in assembler and "including a general opening line" in assembler


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_tavily_problems_are_named_without_keys, test_per_source_timeouts_reach_the_http_calls,
              test_arxiv_empty_error_and_query_cleanup, test_edgar_user_agent_and_non_us_topics,
              test_athena_warns_once_when_a_source_is_down, test_every_writer_prompt_requires_cited_summaries):
        t()
        print("ok  ", t.__name__)
    print("ALL HARDENING TESTS PASSED")


if __name__ == "__main__":
    main()
