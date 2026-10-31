"""Offline tests of token usage and the cost estimate: prompt and completion tokens per role and per provider:model,
cost 0 without prices and tokens x price with them. Run: python -m tests.test_cost_meter"""
import os
import sys
from types import SimpleNamespace

from tests.test_llm_failover import FakeError  # noqa: F401  (sets the fake provider environment first)
from apollo.config import price  # noqa: E402
from apollo.llm import LLM, Budget  # noqa: E402

PRICES = [f"PRICE_{k}_{p}" for k in ("IN", "OUT") for p in ("NVIDIA", "GEMINI", "GROQ", "HF")]
TOKENS = {"strong": (100, 20), "fast": (30, 5)}          # (prompt, completion) per reply, by role


class UsageClient:
    """Replies with fixed token usage per model; nemotron-super (fast, first in FAST_CHAIN) is missing, so fast
    falls over to gemini-flash. full=False leaves total_tokens out of the usage."""
    def __init__(self, full=True):
        self.full = full
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        if kw["model"] == "nvidia/nemotron-super":
            raise FakeError(404, "model not found")
        prompt, completion = TOKENS["fast" if kw["model"] == "gemini-flash" else "strong"]
        usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
        if self.full:
            usage.total_tokens = prompt + completion
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], usage=usage)


def _llm(full=True):
    llm = LLM(Budget(10**9), client_factory=lambda provider, key: UsageClient(full))
    for _ in range(2):
        llm.json("s", "u", role="strong")
    for _ in range(3):
        llm.json("s", "u", role="fast")
    return llm


def _clear_prices():
    for name in PRICES:
        os.environ.pop(name, None)


def test_tokens_add_up_per_role_and_model():
    _clear_prices()
    for full in (True, False):                       # without total_tokens, prompt + completion is the total
        llm = _llm(full)
        s, f = llm.usage["strong"], llm.usage["fast"]
        assert s == {"calls": 2, "prompt_tokens": 200, "completion_tokens": 40, "tokens": 240}, s
        assert f == {"calls": 3, "prompt_tokens": 90, "completion_tokens": 15, "tokens": 105}, f
        by_model = llm.usage_by_model
        assert set(by_model) == {"nvidia:nvidia/nemotron-ultra", "gemini:gemini-flash"}, by_model   # no key index
        assert by_model["nvidia:nvidia/nemotron-ultra"] == s and by_model["gemini:gemini-flash"] == f
        assert llm.budget.used == 345 == sum(u["tokens"] for u in by_model.values())
        status = llm.status()
        assert status["switches"] == 1 and status["active"]["fast"].startswith("gemini:gemini-flash"), status
        assert status["usage"] == llm.usage and status["usage_by_model"] == by_model


def test_cost_is_zero_without_prices():
    _clear_prices()
    status = _llm().status()
    assert status["cost_usd"] == 0 and status["priced"] is False, status
    assert price("nvidia") == (0.0, 0.0)


def test_cost_with_prices():
    _clear_prices()
    os.environ.update({"PRICE_IN_NVIDIA": "2", "PRICE_OUT_NVIDIA": "8", "PRICE_IN_GEMINI": "0.5",
                       "PRICE_OUT_GEMINI": "not a number"})          # unreadable: counted as 0
    try:
        assert price("gemini") == (0.5, 0.0) and price("groq") == (0.0, 0.0)
        status = _llm().status()
        expected = (200 * 2 + 40 * 8 + 90 * 0.5 + 15 * 0) / 1e6    # strong on nvidia, fast on gemini
        assert abs(status["cost_usd"] - expected) < 1e-12 and status["priced"] is True, status
    finally:
        _clear_prices()


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_tokens_add_up_per_role_and_model, test_cost_is_zero_without_prices, test_cost_with_prices):
        t()
        print("ok  ", t.__name__)
    print("ALL COST METER TESTS PASSED")


if __name__ == "__main__":
    main()
