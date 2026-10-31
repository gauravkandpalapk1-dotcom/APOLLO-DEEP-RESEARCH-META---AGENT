"""Offline tests for multi-key, multi-model failover. Run: python -m tests.test_llm_failover"""
import os
import sys
import threading
from types import SimpleNamespace

os.environ.update({
    "NVIDIA_API_KEYS": "nv-key-1,nv-key-2", "NVIDIA_BASE_URL": "https://nvidia.example/v1",
    "GEMINI_API_KEYS": "gm-key-1", "GEMINI_BASE_URL": "https://gemini.example/v1",
    "GROQ_API_KEYS": "", "HF_API_KEYS": "", "LLM_API_KEY": "",
    "STRONG_CHAIN": "nvidia:nvidia/nemotron-ultra,nvidia:deepseek-ai/deepseek-v4-pro,gemini:gemini-flash",
    "FAST_CHAIN": "nvidia:nvidia/nemotron-super,gemini:gemini-flash,groq:GROQ_MODEL_ID,nvidia:org/model:free",
})

from apollo.llm import LLM, AllProvidersFailed, Budget, parse_chain  # noqa: E402


class FakeError(Exception):
    def __init__(self, status, msg="error"):
        super().__init__(f"Error code: {status} - {msg}")
        self.status_code = status


class FakeClient:
    """behaviour(provider, key, model, kwargs) -> reply text, or raises."""
    def __init__(self, provider, key, behaviour, log):
        self.provider, self.key, self.behaviour, self.log = provider, key, behaviour, log
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.log.append((self.provider, self.key, kw["model"], "response_format" in kw, "extra_body" in kw))
        text = self.behaviour(self.provider, self.key, kw["model"], kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                               usage=SimpleNamespace(total_tokens=10))


def make(behaviour):
    log = []
    llm = LLM(Budget(10**9), client_factory=lambda p, k: FakeClient(p, k, behaviour, log))
    return llm, log


def test_chain_parsing():
    providers = {"nvidia": {"keys": ["a"]}, "gemini": {"keys": ["b"]}}
    entries, warns = parse_chain("nvidia:org/model:free, gemini:GEMINI_FLASH_MODEL_ID, groq:x, bad", providers)
    assert entries == [("nvidia", "org/model:free")], entries
    assert len(warns) == 3


def test_first_key_rate_limited_then_second_key():
    def b(p, k, m, kw):
        if k == "nv-key-1":
            raise FakeError(429, "rate limit")
        return '{"ok": true}'
    llm, log = make(b)
    assert llm.json("s", "u", role="strong") == {"ok": True}
    assert log[0][1] == "nv-key-1" and log[1][1] == "nv-key-2"
    assert "nemotron-ultra #2" in llm.active["strong"]


def test_dead_key_skipped_afterwards():
    def b(p, k, m, kw):
        if k == "nv-key-1":
            raise FakeError(401, "invalid api key")
        return "fine"
    llm, log = make(b)
    llm.chat("s", "u"); llm.chat("s", "u")
    assert sum(1 for x in log if x[1] == "nv-key-1") == 1


def test_model_not_found_moves_to_next_model_then_provider():
    def b(p, k, m, kw):
        if p == "nvidia":
            raise FakeError(404, "model not found")
        return "from gemini"
    llm, log = make(b)
    assert llm.chat("s", "u") == "from gemini"
    models_tried = [x[2] for x in log]
    assert models_tried[:2] == ["nvidia/nemotron-ultra", "deepseek-ai/deepseek-v4-pro"], models_tried


def test_response_format_and_thinking_switch_downgrades():
    def b(p, k, m, kw):
        if "response_format" in kw:
            raise FakeError(400, "response_format not supported")
        if "extra_body" in kw:
            raise FakeError(400, "unrecognized chat_template_kwargs")
        return '<think>hmm {"no": 1}</think>{"ok": 1}'
    llm, log = make(b)
    assert llm.json("s", "u", role="strong") == {"ok": 1}   # think block stripped before parsing
    assert log[-1][3] is False and log[-1][4] is False


def test_all_fail_raises():
    def b(p, k, m, kw):
        raise FakeError(401, "unauthorized")
    llm, _ = make(b)
    try:
        llm.chat("s", "u")
    except AllProvidersFailed as e:
        assert "strong" in str(e)
    else:
        raise AssertionError("expected AllProvidersFailed")


def test_parallel_calls():
    calls = {"n": 0}
    lock = threading.Lock()

    def b(p, k, m, kw):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n % 3 == 0:
            raise FakeError(503, "overloaded")
        return "ok"
    llm, _ = make(b)
    results, errors = [], []

    def run():
        try:
            results.append(llm.chat("s", "u", role="fast"))
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=run) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) + len(errors) == 20
    assert llm.usage["fast"]["calls"] == len(results) or llm.usage["fast"]["calls"] >= len(results)


def test_tavily_rotation():
    import requests
    from apollo import search
    from apollo.config import Limits
    os.environ["TAVILY_API_KEYS"] = "tv1,tv2"
    search._tavily_state.update({"current": 0, "dead": set(), "cooldown": {}})
    seen = []

    class Resp:
        def __init__(self, status):
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} Client Error", response=self)

        def json(self):
            return {"results": [{"url": "https://a.example/x", "title": "A", "content": "text"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(json["api_key"])
        return Resp(401 if json["api_key"] == "tv1" else 200)

    real = requests.post
    requests.post = fake_post
    try:
        docs = search.tavily("q", Limits(search_retries=0))
        docs2 = search.tavily("q", Limits(search_retries=0))
    finally:
        requests.post = real
    assert docs and docs2 and seen == ["tv1", "tv2", "tv2"], seen


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print("ALL FAILOVER TESTS PASSED")


if __name__ == "__main__":
    main()
