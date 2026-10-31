"""Demo pre-flight check: everything the demo needs, in order, each PASS or FAIL with a fix, then READY or NOT READY.
Run: python -m scripts.preflight [--archive-old-runs DAYS]

1. .env: every variable the app needs is set (names only, values are never printed), SEC_USER_AGENT is a real
   name and email, SEARCH_DEPTH is basic; docs/benchmark.md and docs/demo_topics.md exist.
2. Keys: at least one working strong model, fast model, a fallback for each role, and a Tavily key
   (the same probes as scripts/check_keys.py: one tiny call per model and key, one 1-result search per Tavily key).
3. Offline tests: tests.test_offline and tests.test_edge_cases pass.
4. A short live run (1 round, web only, 2 sections with one sub-question each) finishes in under 3 minutes and
   writes a report with at least one verified citation. This is the only research run.
5. PDF export of that report works.
6. Free disk space is above 1 GB and runs/ is under 500 MB. With --archive-old-runs DAYS, run folders older than
   DAYS days are zipped into runs/archive-<date>.zip and then removed (only after the zip is written)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from apollo import config
from apollo.config import PROVIDERS, Limits
from apollo.pipeline import Apollo
from apollo.state import Plan
from scripts import run_topic

TOPIC = "Rooftop solar adoption in Indian cities"
LIVE_SECONDS = 180
MIN_FREE_BYTES = 1 << 30
MAX_RUNS_BYTES = 500 << 20
EXAMPLE_AGENT = "your.email@example.com"
DOCS = ("docs/benchmark.md", "docs/demo_topics.md")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


# ---------------- 1. .env ----------------
def chain_providers(environ) -> set[str]:
    """Providers named in STRONG_CHAIN and FAST_CHAIN ("provider:model" entries)."""
    out = set()
    for var in ("STRONG_CHAIN", "FAST_CHAIN"):
        for item in (environ.get(var) or "").split(","):
            if ":" in item:
                out.add(item.split(":", 1)[0].strip().lower())
    return out


def check_env(environ=os.environ, root: Path = Path(".")) -> list[Check]:
    """Names and shapes only: no value is ever printed."""
    has = lambda name: bool((environ.get(name) or "").strip())
    out = [Check("STRONG_CHAIN is set", has("STRONG_CHAIN"), fix="Set STRONG_CHAIN in .env to provider:model entries, "
                                                                "e.g. nvidia:<model id> (see .env.example).")]
    out.append(Check("FAST_CHAIN is set", has("FAST_CHAIN"),
                     fix="Set FAST_CHAIN in .env (without it the fast role reuses STRONG_CHAIN, which is slower)."))
    used = chain_providers(environ)
    for provider in sorted(used):
        if provider not in PROVIDERS:
            out.append(Check(f"chain provider '{provider}' is known", False,
                             fix=f"Use one of {', '.join(PROVIDERS)} as the provider: prefix in STRONG_CHAIN/FAST_CHAIN."))
            continue
        name = provider.upper()
        out.append(Check(f"{name}_API_KEYS and {name}_BASE_URL are set",
                         has(f"{name}_API_KEYS") and has(f"{name}_BASE_URL"),
                         fix=f"The chains use {provider}: set {name}_API_KEYS (comma-separated) and {name}_BASE_URL "
                             f"in .env, or remove the {provider}: entries from the chains."))
    out.append(Check("TAVILY_API_KEYS is set", has("TAVILY_API_KEYS") or has("TAVILY_API_KEY"),
                     fix="Set TAVILY_API_KEYS in .env (several keys allowed, comma-separated)."))
    agent = (environ.get("SEC_USER_AGENT") or "").strip()
    out.append(Check("SEC_USER_AGENT is set to a real name and email",
                     bool(agent) and EXAMPLE_AGENT not in agent and "@" in agent,
                     detail="" if agent else "not set",
                     fix="Set SEC_USER_AGENT in .env to your name and email (SEC EDGAR refuses requests without it)."))
    depth = config.search_depth(environ.get("SEARCH_DEPTH"))
    out.append(Check("SEARCH_DEPTH is basic", depth == "basic",
                     detail="not set, so basic" if not has("SEARCH_DEPTH") else f"is {depth}",
                     fix="Set SEARCH_DEPTH=basic in .env (advanced costs 2 Tavily credits per search)."))
    for doc in DOCS:
        out.append(Check(f"{doc} exists", (root / doc).exists(),
                         fix=f"Restore {doc} from git (git checkout -- {doc})."))
    return out


# ---------------- 2. keys ----------------
def check_keys() -> list[Check]:
    from apollo.llm import LLM, Budget
    from scripts.check_keys import probe_all, probe_tavily
    try:
        llm = LLM(Budget(10**9))
    except RuntimeError as e:
        return [Check("LLM chains load", False, str(e)[:200], "Fill in the keys and chains in .env, then run "
                                                              "python -m scripts.check_keys.")]
    working = {"strong": set(), "fast": set()}
    failed = []
    for role, c, r in probe_all(llm):
        if r["ok"]:
            working[role].add(c.label)
        else:
            failed.append(f"{c.label} ({r['err'][:40]})")
    note = ("failed: " + "; ".join(sorted(set(failed)))[:300]) if failed else ""
    fix_model = "Run python -m scripts.check_keys, then fix or remove the FAILED entries in STRONG_CHAIN/FAST_CHAIN."
    out = [Check("a strong model works", bool(working["strong"]), f"{len(working['strong'])} working", fix_model),
           Check("a fast model works", bool(working["fast"]), f"{len(working['fast'])} working", fix_model),
           Check("each role has a fallback", len(working["strong"]) >= 2 and len(working["fast"]) >= 2,
                 f"strong {len(working['strong'])}, fast {len(working['fast'])} working" + (f"; {note}" if note else ""),
                 "Add a second working key or provider:model entry to each chain, so failover has somewhere to go.")]
    keys, problem = probe_tavily()
    ok = [i for i, good, _ in keys if good]
    out.append(Check("a Tavily key works", bool(ok), problem or f"{len(ok)} of {len(keys)} key(s) work",
                     "Check TAVILY_API_KEYS in .env and the credits left on your Tavily account."))
    return out


# ---------------- 3. offline tests ----------------
def check_tests(modules=("tests.test_offline", "tests.test_edge_cases"), timeout: int = 600) -> list[Check]:
    out = []
    for module in modules:
        try:
            r = subprocess.run([sys.executable, "-m", module], capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
            out.append(Check(f"{module} passes", r.returncode == 0, tail[0][:150],
                             f"Run python -m {module} and fix the first failing check."))
        except subprocess.TimeoutExpired:
            out.append(Check(f"{module} passes", False, f"still running after {timeout}s",
                             f"Run python -m {module} to see where it hangs."))
    return out


# ---------------- 4. short live run ----------------
def small_plan(plan: Plan) -> Plan:
    """The first two sections, one sub-question each: a 2-search live check, not a full demo."""
    sections = [replace(s, subq_ids=s.subq_ids[:1]) for s in plan.sections[:2]]
    keep = {q for s in sections for q in s.subq_ids}
    return replace(plan, sections=sections, subqs={q: sq for q, sq in plan.subqs.items() if q in keep})


def check_live_run(runs_dir: Path, make_app=Apollo, topic: str = TOPIC) -> tuple[list[Check], Path | None]:
    limits = Limits(max_rounds=1, enabled_sources=("web",), max_search_calls=6)
    try:
        app = make_app(limits)
    except Exception as e:
        return [Check("live run starts", False, f"{type(e).__name__}: {str(e)[:150]}",
                      "Fix the .env and key checks above first.")], None
    full_plan = app.plan
    app.plan = lambda t: small_plan(full_plan(t))
    started = time.time()
    code, out = run_topic.run(topic, limits, runs_dir=runs_dir, app=app)
    seconds = round(time.time() - started, 1)
    report = out / "report.md"
    verified = 0
    if (out / "provenance.json").exists():
        prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
        verified = sum(x["verdict"] == "kept" and bool(x["chunk_ids"]) for x in prov["sentences"])
    error = (out / "error.txt").read_text(encoding="utf-8").strip().splitlines()[-1:] if code else []
    return [Check("live run finishes with a report", code == 0 and report.exists(),
                  f"{out}" + (f": {error[0][:150]}" if error else ""),
                  f"Open {out / 'events.jsonl'} to see where it failed; run python -m scripts.check_keys."),
            Check(f"live run takes under {LIVE_SECONDS // 60} minutes", seconds < LIVE_SECONDS, f"{seconds}s",
                  "Put faster models first in STRONG_CHAIN/FAST_CHAIN (check_keys shows seconds per model) or "
                  "lower LLM_MAX_CONCURRENCY if calls are being rate-limited."),
            Check("report has at least one verified citation", verified >= 1, f"{verified} verified sentence(s)",
                  "Check the Removed sentences in the report and the verifier events; a fast model that "
                  "can't return JSON verdicts removes everything.")], out


# ---------------- 5. PDF ----------------
def check_pdf(run_dir: Path | None) -> list[Check]:
    if run_dir is None or not (run_dir / "report.md").exists():
        return [Check("PDF export works", False, "no report to export", "Fix the live run first.")]
    try:
        from apollo.export import markdown_to_pdf
        pdf = markdown_to_pdf((run_dir / "report.md").read_text(encoding="utf-8"))
        (run_dir / "report.pdf").write_bytes(pdf)
        return [Check("PDF export works", pdf[:5] == b"%PDF-", f"{run_dir / 'report.pdf'}, {len(pdf):,} bytes")]
    except ImportError:
        return [Check("PDF export works", False, "fpdf2 is missing", "Run pip install -r requirements.txt.")]
    except Exception as e:
        return [Check("PDF export works", False, f"{type(e).__name__}: {str(e)[:150]}",
                      "Use Download Markdown in the demo, and report the error.")]


# ---------------- 6. disk ----------------
def folder_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def check_disk(root: Path = Path("."), runs_dir: Path = Path("runs")) -> list[Check]:
    free, used = shutil.disk_usage(root).free, folder_bytes(runs_dir)
    return [Check("free disk space above 1 GB", free > MIN_FREE_BYTES, f"{free / (1 << 30):.1f} GB free",
                  "Free some disk space (empty the recycle bin, delete large downloads)."),
            Check("runs/ is under 500 MB", used < MAX_RUNS_BYTES, f"{used / (1 << 20):.1f} MB",
                  "Archive old runs: python -m scripts.preflight --archive-old-runs 1")]


def archive_old_runs(runs_dir: Path, days: float) -> tuple[Path | None, int]:
    """Zip run folders older than `days` into runs/archive-<date>.zip, then remove them. Returns (zip, count)."""
    cutoff = time.time() - days * 86400
    old = [p for p in runs_dir.iterdir() if p.is_dir() and p.stat().st_mtime < cutoff] if runs_dir.exists() else []
    if not old:
        return None, 0
    target = runs_dir / f"archive-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in old:
            for f in folder.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(runs_dir))
    with zipfile.ZipFile(target) as z:            # remove only what the zip really holds
        if z.testzip() is not None:
            raise OSError(f"{target} is damaged; nothing was removed")
    for folder in old:
        shutil.rmtree(folder)
    return target, len(old)


# ---------------- report ----------------
def show(step: str, checks: list[Check]) -> bool:
    print(f"\n{step}")
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}" + (f" ({c.detail})" if c.detail else ""))
        if not c.ok and c.fix:
            print(f"        fix: {c.fix}")
    return all(c.ok for c in checks)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Check everything the demo needs, then print READY or NOT READY.")
    p.add_argument("--archive-old-runs", type=float, metavar="DAYS",
                   help="first zip run folders older than DAYS days into runs/archive-<date>.zip and remove them")
    args = p.parse_args(argv)
    runs = Path("runs")
    if args.archive_old_runs is not None:
        target, n = archive_old_runs(runs, args.archive_old_runs)
        print(f"Archived {n} run folder(s) to {target}" if n else "No run folders old enough to archive.")

    results = [show("1. .env and docs", check_env())]
    key_checks = check_keys()
    results.append(show("2. Keys (same probes as scripts/check_keys.py)", key_checks))
    results.append(show("3. Offline tests", check_tests()))
    usable = all(c.ok for c in key_checks if c.name != "each role has a fallback")
    if usable:
        live, run_dir = check_live_run(runs / "preflight")
    else:
        live, run_dir = [Check("live run", False, "skipped: no working model or Tavily key",
                               "Fix step 2 first.")], None
    results.append(show(f"4. Short live run (1 round, web only, 2 sub-questions): {TOPIC}", live))
    results.append(show("5. PDF export", check_pdf(run_dir)))
    results.append(show("6. Disk", check_disk(Path("."), runs)))
    ready = all(results)
    print("\n" + ("READY" if ready else "NOT READY"))
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
