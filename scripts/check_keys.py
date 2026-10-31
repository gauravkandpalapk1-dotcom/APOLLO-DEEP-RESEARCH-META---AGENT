"""Test every LLM chain entry with every key, and every Tavily key.
Run: python -m scripts.check_keys      (never prints keys)"""
import sys
import time

from apollo import search
from apollo.config import DISABLE_THINKING, LLM_TIMEOUTS, THINKING_OFF, Limits
from apollo.llm import LLM, Budget, classify_error


def thinking_extra(model: str):
    if not DISABLE_THINKING:
        return None
    for family, extra in THINKING_OFF.items():
        if family in model.lower():
            return extra
    return None


def probe(llm: LLM, c) -> dict:
    """Try JSON mode + thinking switch first, then drop each if the provider rejects it."""
    client = llm._client(c)
    extra = thinking_extra(c.model)
    attempts = [(True, True), (False, True), (True, False), (False, False)] if extra else [(True, False), (False, False)]
    last_err = ""
    start = time.time()
    for json_mode, use_switch in attempts:
        kwargs = dict(model=c.model, temperature=0, timeout=LLM_TIMEOUTS.get(c.provider, LLM_TIMEOUTS["default"]),
                      messages=[{"role": "system", "content": "Reply with JSON only."},
                                {"role": "user", "content": 'Return exactly {"ok": true}'}])
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if use_switch:
            kwargs["extra_body"] = extra
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # report every provider error instead of crashing
            kind = classify_error(e)
            last_err = f"{kind}: {str(e)[:70]}"
            if kind in ("json_mode", "params", "other"):
                continue
            break
        msg = resp.choices[0].message
        text = msg.content or ""
        thinking = "<think>" in text.lower() or bool(getattr(msg, "reasoning_content", None))
        return {"ok": bool(text.strip()), "json": "yes" if json_mode else "no",
                "switch": ("yes" if use_switch else "no") if extra else "-",
                "thinking": "yes" if thinking else "no", "secs": round(time.time() - start, 1),
                "err": "" if text.strip() else "empty reply"}
    return {"ok": False, "json": "?", "switch": "?", "thinking": "?",
            "secs": round(time.time() - start, 1), "err": last_err}


def main() -> int:
    try:
        llm = LLM(Budget(10**9))
    except RuntimeError as e:
        print("FAILED:", e)
        return 1
    for w in llm.warnings:
        print("WARNING:", w)

    print(f"\n{'role':<11}{'candidate':<52}{'result':<8}{'json':<6}{'no-think':<10}{'thinks':<8}{'secs':<6}error")
    seen, ok_roles = {}, set()
    for role in ("strong", "fast"):
        for c in llm.candidates(role):
            if c not in seen:
                seen[c] = probe(llm, c)
            r = seen[c]
            if r["ok"]:
                ok_roles.add(role)
            print(f"{role:<11}{c.label[:50]:<52}{'OK' if r['ok'] else 'FAILED':<8}{r['json']:<6}"
                  f"{r['switch']:<10}{r['thinking']:<8}{r['secs']:<6}{r['err']}")

    print()
    tavily_ok = False
    try:
        keys = search.tavily_keys()
    except search.SearchError as e:
        keys = []
        print("Tavily: FAILED:", e)
    for i, key in enumerate(keys, 1):
        try:
            search._tavily_once(key, "test", Limits(results_per_query=1), "general")
            print(f"Tavily key #{i}: OK")
            tavily_ok = True
        except Exception as e:  # report and continue with the next key
            print(f"Tavily key #{i}: FAILED: {str(e)[:90]}")

    print()
    problems = [name for name, ok in (("strong model", "strong" in ok_roles), ("fast model", "fast" in ok_roles),
                                      ("Tavily", tavily_ok)) if not ok]
    if problems:
        print("NOT READY. No working: " + ", ".join(problems) +
              ". Fix the FAILED lines above (usually a wrong model id or key) and run again.")
        return 1
    print("READY: at least one strong model, one fast model and one Tavily key work.")
    print("Tip: a model whose 'thinks' column says yes is slower; put it later in its chain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
