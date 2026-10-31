"""Offline test of scripts/list_models.py with fake providers. Checks: ids printed, keys never printed,
Gemini's "models/" prefix removed, a dead first key falls through to the next one.
Run: python -m tests.test_list_models"""
import io
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace

from scripts import list_models

SECRET_DEAD, SECRET_GOOD = "sk-dead-SECRET-111", "sk-good-SECRET-222"


class FakeAuthError(Exception):
    status_code = 401


def fake_factory(key, base_url):
    def models_list():
        if key == SECRET_DEAD:
            raise FakeAuthError(f"Incorrect API key provided: {key}")   # providers sometimes echo the key
        if "gemini" in base_url:
            return [SimpleNamespace(id="models/gemini-x-flash"), SimpleNamespace(id="models/gemini-x-pro")]
        return [SimpleNamespace(id="llama-x-70b"), SimpleNamespace(id="llama-x-70b")]
    return SimpleNamespace(models=SimpleNamespace(list=models_list))


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    providers = {"gemini": {"keys": [SECRET_DEAD, SECRET_GOOD], "base_url": "https://gemini.example"},
                 "groq": {"keys": [SECRET_GOOD], "base_url": "https://groq.example"},
                 "hf": {"keys": [SECRET_DEAD], "base_url": "https://hf.example"}}
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = list_models.main(providers, fake_factory)
    out = buf.getvalue()
    assert code == 0
    assert "SECRET" not in out, "a key leaked into the output"
    assert "gemini:gemini-x-flash" in out and "models/" not in out
    assert "gemini: 2 models (key #2)" in out, out
    assert out.count("groq:llama-x-70b") == 1, "duplicate ids should be listed once"
    assert "hf: FAILED (key #1: FakeAuthError 401)" in out, out
    print("ok   test_list_models")
    print("ALL LIST_MODELS TESTS PASSED")


if __name__ == "__main__":
    main()
