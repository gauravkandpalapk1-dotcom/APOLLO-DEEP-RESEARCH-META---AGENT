"""LLM access with multi-key, multi-provider, multi-model failover.

Every role (strong / fast) has a chain of provider:model entries from .env, tried in order.
Each entry is tried with each of its provider's keys. On failure the client moves on:
  - 401/403 (bad key)            -> that key is dead for this run
  - 429/5xx/timeout/connection   -> that key cools down; try the next candidate
  - 404 / model not found        -> that provider:model entry is dead for this run
  - 400 about response_format    -> retry the same candidate without JSON mode
  - 400 about extra parameters   -> retry the same candidate without the thinking switch
If every candidate fails, AllProvidersFailed is raised and the pipeline turns it into a
safe failure event. Keys are never printed: candidates are named "provider:model #k".
"""
import json
import re
import threading
import time
from dataclasses import dataclass

from .config import (DISABLE_THINKING, LLM_MAX_CONCURRENCY, LLM_TIMEOUTS, PROVIDERS,
                     THINKING_OFF, env, price)

PLACEHOLDER = re.compile(r"^[A-Z0-9_]+$")
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class BudgetExceeded(Exception):
    pass


class AllProvidersFailed(RuntimeError):
    pass


class Budget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def add(self, n: int):
        with self._lock:
            self.used += n

    @property
    def exceeded(self) -> bool:
        return self.used >= self.limit


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in fences, add a sentence, or send several objects one after another; return the
    first whole object, with any further top-level objects under "_more_objects"."""
    text = THINK_BLOCK.sub("", text)
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model output: {text[:200]!r}")
    decoder = json.JSONDecoder()
    try:
        first, pos = decoder.raw_decode(text, start)
    except json.JSONDecodeError:
        return json.loads(text[start:end + 1])
    more = []
    while (pos := text.find("{", pos)) != -1:
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        more.append(obj)
    if more and isinstance(first, dict):
        first["_more_objects"] = more
    return first


def _counter() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "tokens": 0}


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    key_index: int          # 1-based, for display only

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model} #{self.key_index}"


def load_providers() -> dict[str, dict]:
    """{provider: {"keys": [...], "base_url": str}} for providers that have keys."""
    out = {}
    for name in PROVIDERS:
        keys = [k.strip() for k in (env(f"{name.upper()}_API_KEYS", required=False) or "").split(",") if k.strip()]
        base = env(f"{name.upper()}_BASE_URL", required=False)
        if keys and base:
            out[name] = {"keys": keys, "base_url": base}
    # legacy single-model settings become a "legacy" provider
    lk, lb = env("LLM_API_KEY", required=False), env("LLM_BASE_URL", required=False)
    if lk:
        out["legacy"] = {"keys": [lk], "base_url": lb or None}
    return out


def parse_chain(raw: str, providers: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """'nvidia:a/b,gemini:c' -> [(provider, model)], warnings. Splits on the FIRST colon only."""
    entries, warnings = [], []
    for item in [x.strip() for x in (raw or "").split(",") if x.strip()]:
        if ":" not in item:
            warnings.append(f"chain entry '{item}' has no provider: prefix; skipped")
            continue
        provider, model = item.split(":", 1)
        provider, model = provider.strip().lower(), model.strip()
        if not model or PLACEHOLDER.match(model):
            warnings.append(f"chain entry '{item}' still has a placeholder model id; skipped")
        elif provider not in providers:
            warnings.append(f"chain entry '{item}': no {provider.upper()}_API_KEYS set; skipped")
        else:
            entries.append((provider, model))
    return entries, warnings


def classify_error(e: Exception) -> str:
    """auth | retry | model | json_mode | params | other"""
    status = getattr(e, "status_code", None)
    msg = str(e).lower()
    name = type(e).__name__.lower()
    if status in (401, 403) or "invalid api key" in msg or "unauthorized" in msg:
        return "auth"
    if status in RETRYABLE or "timeout" in name or "connection" in name or "rate limit" in msg:
        return "retry"
    if status == 404 or ("model" in msg and ("not found" in msg or "does not exist" in msg)):
        return "model"
    if status == 400 and "response_format" in msg:
        return "json_mode"
    if status in (400, 422) and ("chat_template" in msg or "extra" in msg or "unrecognized" in msg
                                 or "unexpected" in msg or "unsupported" in msg):
        return "params"
    return "other"


def retry_after(e: Exception) -> float | None:
    try:
        value = e.response.headers.get("retry-after")  # type: ignore[attr-defined]
        return float(value) if value else None
    except Exception:
        return None


class LLM:
    def __init__(self, budget: Budget, client_factory=None):
        self.budget = budget
        self.providers = load_providers()
        self.warnings: list[str] = []
        self.chains: dict[str, list[tuple[str, str]]] = {}
        legacy_model = env("LLM_MODEL", required=False)
        for role in ("strong", "fast"):
            entries, warns = parse_chain(env(f"{role.upper()}_CHAIN", required=False) or "", self.providers)
            if "legacy" in self.providers and legacy_model:
                entries.append(("legacy", legacy_model))
            self.chains[role] = entries
            self.warnings += [f"{role}: {w}" for w in warns]
        if not self.chains["fast"]:
            self.chains["fast"] = list(self.chains["strong"])
        if not self.chains["strong"]:
            raise RuntimeError("No usable LLM in STRONG_CHAIN. Fill in .env (keys and provider:model entries) "
                               "and run: python -m scripts.check_keys")
        self._factory = client_factory or self._openai_client
        self._clients: dict[Candidate, object] = {}
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(LLM_MAX_CONCURRENCY)
        self._dead_keys: set[tuple[str, int]] = set()
        self._dead_models: set[tuple[str, str]] = set()
        self._cooldown: dict[tuple[str, int], float] = {}
        self._no_json_mode: set[tuple[str, str]] = set()
        self._no_thinking_switch: set[tuple[str, str]] = set()
        self._sticky: dict[str, Candidate] = {}
        self.usage = {r: _counter() for r in ("strong", "fast")}       # per role
        self.usage_by_model: dict[str, dict] = {}                       # per "provider:model"
        self.active: dict[str, str] = {}
        self.switches = 0
        self._notices: list[str] = []

    # ---------- plumbing ----------
    def _openai_client(self, provider: str, key: str):
        from openai import OpenAI  # imported lazily so offline tests can run without it
        return OpenAI(api_key=key, base_url=self.providers[provider]["base_url"], max_retries=0)

    def _client(self, c: Candidate):
        with self._lock:
            if c not in self._clients:
                key = self.providers[c.provider]["keys"][c.key_index - 1]
                self._clients[c] = self._factory(c.provider, key)
            return self._clients[c]

    def candidates(self, role: str) -> list[Candidate]:
        out = []
        for provider, model in self.chains[role]:
            for i in range(len(self.providers[provider]["keys"])):
                out.append(Candidate(provider, model, i + 1))
        return out

    def _usable(self, c: Candidate, now: float) -> bool:
        return ((c.provider, c.key_index) not in self._dead_keys
                and (c.provider, c.model) not in self._dead_models
                and self._cooldown.get((c.provider, c.key_index), 0) <= now)

    def _note(self, text: str):
        with self._lock:
            self._notices.append(text)

    def drain_notices(self) -> list[str]:
        with self._lock:
            out, self._notices = self._notices, []
        return out

    def _count(self, role: str, c: Candidate, usage):
        """Add one reply's token usage to its role and its provider:model. Call with self._lock held."""
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or 0) or prompt + completion
        for counter in (self.usage[role], self.usage_by_model.setdefault(f"{c.provider}:{c.model}", _counter())):
            counter["calls"] += 1
            counter["prompt_tokens"] += prompt
            counter["completion_tokens"] += completion
            counter["tokens"] += total
        return total

    def cost(self) -> float:
        """Estimated US dollars: prompt and completion tokens per provider:model times PRICE_IN/PRICE_OUT."""
        total = 0.0
        for name, u in self.usage_by_model.items():
            price_in, price_out = price(name.split(":", 1)[0])
            total += (u["prompt_tokens"] * price_in + u["completion_tokens"] * price_out) / 1e6
        return total

    def status(self) -> dict:
        with self._lock:
            return {"active": dict(self.active), "usage": {r: dict(u) for r, u in self.usage.items()},
                    "usage_by_model": {m: dict(u) for m, u in self.usage_by_model.items()},
                    "cost_usd": self.cost(), "priced": any(any(price(p)) for p in self.providers),
                    "switches": self.switches, "warnings": list(self.warnings)}

    # ---------- calls ----------
    def _request(self, c: Candidate, system: str, user: str, json_mode: bool, temperature: float):
        kwargs = dict(model=c.model, temperature=temperature,
                      timeout=LLM_TIMEOUTS.get(c.provider, LLM_TIMEOUTS["default"]),
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        if json_mode and (c.provider, c.model) not in self._no_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if DISABLE_THINKING and (c.provider, c.model) not in self._no_thinking_switch:
            for family, extra in THINKING_OFF.items():
                if family in c.model.lower():
                    kwargs["extra_body"] = extra
                    break
        return self._client(c).chat.completions.create(**kwargs)

    def chat(self, system: str, user: str, json_mode: bool = False, temperature: float = 0.2,
             role: str = "strong") -> str:
        if self.budget.exceeded:
            raise BudgetExceeded(f"Token budget of {self.budget.limit} used up")
        role = role if role in self.chains else "strong"
        failures: list[str] = []
        waited = False
        with self._sem:
            while True:
                now = time.time()
                with self._lock:
                    cands = self.candidates(role)
                    sticky = self._sticky.get(role)
                    if sticky in cands:  # start from the last candidate that worked
                        i = cands.index(sticky)
                        cands = cands[i:] + cands[:i]
                    usable = [c for c in cands if self._usable(c, now)]
                if not usable:
                    cooling = [t for (p, k), t in self._cooldown.items() if t > now]
                    if cooling and not waited and min(cooling) - now <= 15:
                        time.sleep(max(0.0, min(cooling) - now))
                        waited = True
                        continue
                    raise AllProvidersFailed(f"All {role} models failed: " + ("; ".join(failures) or
                                             "every key is dead or cooling down"))
                for c in usable:
                    with self._lock:
                        if not self._usable(c, time.time()):  # state may have changed (dead model)
                            continue
                    text = self._try(c, role, system, user, json_mode, temperature, failures)
                    if text is not None:
                        return text
                if waited:
                    raise AllProvidersFailed(f"All {role} models failed: " + "; ".join(failures))
                waited = True  # one more pass after cool-downs, then give up

    def _try(self, c, role, system, user, json_mode, temperature, failures) -> str | None:
        for _attempt in range(3):  # retries only for json_mode / params downgrades
            try:
                resp = self._request(c, system, user, json_mode, temperature)
            except Exception as e:  # classify every provider error; never crash the run
                kind = classify_error(e)
                short = f"{c.label}: {kind} ({str(e)[:80]})"
                with self._lock:
                    if kind == "json_mode":
                        self._no_json_mode.add((c.provider, c.model))
                        continue
                    if kind == "params":
                        if (c.provider, c.model) in self._no_thinking_switch:
                            failures.append(short)
                            return None
                        self._no_thinking_switch.add((c.provider, c.model))
                        continue
                    failures.append(short)
                    if kind == "auth":
                        self._dead_keys.add((c.provider, c.key_index))
                    elif kind == "model":
                        self._dead_models.add((c.provider, c.model))
                    elif kind == "retry":
                        self._cooldown[(c.provider, c.key_index)] = time.time() + min(retry_after(e) or 60, 120)
                    self.switches += 1
                self._note(f"{role}: {c.label} failed ({kind}); trying the next model or key")
                return None
            text = (resp.choices[0].message.content or "") if resp.choices else ""
            text = THINK_BLOCK.sub("", text).strip()
            with self._lock:
                self.budget.add(self._count(role, c, getattr(resp, "usage", None)))
                if not text:
                    failures.append(f"{c.label}: empty reply")
                    self.switches += 1
                    return None
                if self._sticky.get(role) != c:
                    self._sticky[role] = c
                    self.active[role] = c.label
            return text
        failures.append(f"{c.label}: gave up after downgrades")
        return None

    def json(self, system: str, user: str, role: str = "strong", **kw) -> dict:
        prompt = user + "\n\nRespond with one JSON object only. No prose, no code fences."
        try:
            return parse_json(self.chat(system, prompt, json_mode=True, role=role, **kw))
        except ValueError:
            # one repair attempt
            return parse_json(self.chat(system, prompt + "\nYour last reply was not valid JSON.",
                                        json_mode=True, role=role, **kw))
