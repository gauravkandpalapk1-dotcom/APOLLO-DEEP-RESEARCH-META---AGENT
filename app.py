"""Streamlit UI. Run: streamlit run app.py"""
import base64
import html
import json
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

from apollo.agents import PlanError, plan_from_rows, plan_rows
from apollo.config import SOURCE_TYPES, Limits
from apollo.llm import AllProvidersFailed, BudgetExceeded
from apollo.pipeline import Apollo, provenance
from apollo.writer import highlight, plain_sentence
from scripts.handcheck_summary import newest_benchmark

st.set_page_config(page_title="Apollo research", page_icon="🔭", layout="wide")

ICONS = {"plan": "🗺️", "round": "🔁", "search": "🔎", "search_fail": "⚠️", "review": "🧐", "gap": "🕳️",
         "decision": "🧭", "write": "✍️", "verify": "✅", "error": "❌", "done": "📄"}
ACTORS = {"apollo": ("Apollo", "blue"), "athena": ("Athena", "green"), "hermes": ("Hermes", "orange"),
          "writer": ("Writer", "violet"), "oracle": ("Oracle", "red"), "system": ("System", "gray")}
ASSETS = Path(__file__).resolve().parent / "assets"
INTRO_VIDEO = ASSETS / "apollo_intro.mp4"
# The "Now working" card's portrait per actor; writer, oracle and system events use Apollo's. The emoji stands in
# when the image file is missing.
PORTRAITS = {"apollo": ("agent_apollo.jpg", "☀️"), "athena": ("agent_athena.jpg", "🦉"),
             "hermes": ("agent_hermes.jpg", "🪽")}
STATUS_FILL = {"open": "#d9d9d9", "covered": "#9bd89b", "partial": "#f6c76b", "unfillable": "#f08c8c"}
LEGEND = "Sub-questions: :gray[■] open · :green[■] covered · :orange[■] partial · :red[■] unfillable"

ss = st.session_state
# phase of the current run: None, "round" (searching), "gaps" (waiting for the gap review), "writing", "done".
# Finding "round" or "writing" at the start of a script run means the last one was interrupted by a rerun.
for k, v in {"plan": None, "rows": None, "events": [], "report": None, "run": None, "phase": None,
             "action": None, "gap_rows": [], "gate_log": [], "paused_at": None, "intro_played": False}.items():
    ss.setdefault(k, v)

# ---------------- settings ----------------
with st.sidebar:
    show_intro = st.toggle("Show intro video", value=True,
                           help="Plays full screen once when the page opens. Switch off and on to play it again.")
    st.header("Limits")
    max_rounds = st.slider("Maximum research rounds", 1, 5, 3)
    minutes = st.slider("Time limit for research (minutes)", 2, 30, 10)
    budget = st.number_input("Token budget", 50_000, 2_000_000, 400_000, step=50_000)
    sources = st.multiselect("Source types", SOURCE_TYPES, default=["web", "news", "arxiv"],
                             help="edgar needs SEC_USER_AGENT in .env and only helps for US-listed companies")
    review_plan = st.checkbox("Let me review the plan before searching", value=True)
    review_gaps = st.toggle("Review gaps between rounds", value=False,
                            help="Pause after each round to choose which gaps to follow up and edit their queries.")
    st.header("Models")
    models_slot = st.empty()


@lru_cache(maxsize=1)
def video_data_uri() -> str:
    return "data:video/mp4;base64," + base64.b64encode(INTRO_VIDEO.read_bytes()).decode()


# Full-window splash: covers the header and sidebar, fades out when the video ends (or on "Skip intro"), leaving the
# page underneath. The CSS animation hides it anyway after 12 s in case the script or autoplay is blocked.
SPLASH = """
<div id="apollo-intro">
  <video id="apollo-intro-video" src="{src}" autoplay muted playsinline></video>
  <button id="apollo-intro-skip" type="button">Skip intro ›</button>
</div>
<style>
  #apollo-intro {{position: fixed; inset: 0; z-index: 1000001; background: #000; display: flex;
                 align-items: center; justify-content: center; transition: opacity .8s ease;
                 animation: apollo-intro-out .8s ease 12s forwards}}
  #apollo-intro video {{width: 100vw; height: 100vh; object-fit: cover}}
  #apollo-intro.gone {{opacity: 0; pointer-events: none}}
  #apollo-intro-skip {{position: absolute; right: 28px; bottom: 28px; padding: 8px 18px; border-radius: 999px;
                      border: 1px solid rgba(255,255,255,.6); background: rgba(0,0,0,.45); color: #fff;
                      font-size: 15px; cursor: pointer}}
  @keyframes apollo-intro-out {{to {{opacity: 0; visibility: hidden; pointer-events: none}}}}
</style>
<script>
  (() => {{
    const root = (document.currentScript && document.currentScript.getRootNode()) || document;
    const intro = root.getElementById("apollo-intro"), video = root.getElementById("apollo-intro-video");
    if (!intro) return;
    const close = () => {{ intro.classList.add("gone"); setTimeout(() => intro.remove(), 900); }};
    video.addEventListener("ended", close);
    video.addEventListener("error", close);
    root.getElementById("apollo-intro-skip").addEventListener("click", close);
    const playing = video.play();
    if (playing) playing.catch(close);                  // autoplay refused: go straight to the page
  }})();
</script>
"""


def play_intro():
    """The splash, once per browser session (a reload plays it again); skipped silently if the video is missing."""
    if not show_intro:
        ss.intro_played = False                         # switching the toggle back on plays it again
        return
    if ss.intro_played or not INTRO_VIDEO.is_file():
        return
    ss.intro_played = True
    st.html(SPLASH.format(src=video_data_uri()), unsafe_allow_javascript=True)


def limits() -> Limits:
    return Limits(max_rounds=max_rounds, max_seconds=minutes * 60, token_budget=int(budget),
                  enabled_sources=tuple(sources or ["web"]))


def reset():
    ss.update(plan=None, rows=None, events=[], report=None, run=None, phase=None, action=None, gap_rows=[],
              gate_log=[], paused_at=None)


KEY_HELP = ("Check the *_API_KEYS and STRONG_CHAIN / FAST_CHAIN entries in .env, then run "
            "`python -m scripts.check_keys` to see which keys and models work.")


def problem(e: Exception, step: str) -> str:
    """What went wrong and how to fix it, in one message and without a stack trace."""
    detail = (str(e).strip() or type(e).__name__)[:300]
    if isinstance(e, AllProvidersFailed):
        return f"No language model answered {step}. {KEY_HELP} Details: {detail}"
    if isinstance(e, BudgetExceeded):
        return f"The token budget ran out {step}. Raise **Token budget** in the sidebar and try again."
    if ".env" in detail:                         # e.g. no usable model: the message already names the variable
        return f"{detail}"
    return (f"Something went wrong {step} ({type(e).__name__}: {detail}). Try again; if it keeps happening, "
            f"press **Start a new topic**.")


# ---------------- plan graph ----------------
def _dot_label(text: str, width: int = 34, limit: int = 110) -> str:
    text = text if len(text) <= limit else text[:limit - 1] + "…"
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    return "\\n".join(x.replace("\\", "\\\\").replace('"', '\\"') for x in lines)


def plan_dot(plan) -> str:
    """Topic -> sections -> sub-questions as DOT text, each sub-question filled by its status."""
    out = ["digraph plan {", "rankdir=LR;",
           'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, color="#888888"];',
           f'topic [label="{_dot_label(plan.topic, 26)}", fillcolor="#cfe2ff"];']
    for s in plan.sections:
        out += [f'{s.id} [label="{_dot_label(s.title, 26)}", fillcolor="#ffffff"];', f"topic -> {s.id};"]
        for q in s.subq_ids:
            sq = plan.subqs[q]
            out += [f'{q} [label="{q}: {_dot_label(sq.question)}", tooltip="{sq.status}", '
                    f'fillcolor="{STATUS_FILL.get(sq.status, STATUS_FILL["open"])}"];', f"{s.id} -> {q};"]
    return "\n".join(out + ["}"])


def show_graph(slot, plan):
    with slot.container():
        st.graphviz_chart(plan_dot(plan), width="stretch")
        st.caption(LEGEND)


# ---------------- models panel ----------------
def tavily_searches(state) -> int:
    """Web and news searches that reached Tavily this run (a call that failed used no credits)."""
    return sum(x["source"] in ("web", "news") and x["status"] != "failed" for x in state.search_log) if state else 0


def models_now() -> tuple[dict | None, int]:
    """(model status, Tavily searches) for the finished report, or live from the current run."""
    if ss.report:
        return ss.report.stats.get("models"), tavily_searches(ss.report.state)
    llm = getattr(ss.get("app"), "llm", None)
    return (llm.status() if hasattr(llm, "status") else None), tavily_searches(ss.run)


def role_tokens(u: dict) -> str:
    return (f"{u.get('tokens', 0):,} tokens ({u.get('prompt_tokens', 0):,} in, "
            f"{u.get('completion_tokens', 0):,} out), {u.get('calls', 0)} calls")


def cost_text(status: dict) -> str:
    return f"\\${status.get('cost_usd', 0):.4f}" + ("" if status.get("priced") else " (no prices set)")


def show_models():
    status, searches = models_now()
    with models_slot.container():
        if not status:
            st.caption("No run yet. Plan a topic to see the active models, tokens and estimated cost here.")
            return
        for role in ("strong", "fast"):
            st.markdown(f"**{role.title()}:** `{status['active'].get(role, 'not used yet')}`  \n"
                        + role_tokens(status["usage"].get(role, {})))
        st.markdown(f"Model or key switches: **{status['switches']}**  \n"
                    f"Estimated cost: **{cost_text(status)}**  \n"
                    f"Tavily searches this run: **{searches}**")


# ---------------- live log ----------------
def no_latex(text: str) -> str:
    """st.markdown reads text between two "$" signs as LaTeX, which garbles "$4.2 billion ... $7.8 billion"."""
    return text.replace("$", "\\$")


def log_line(e: dict):
    name, colour = ACTORS.get(e.get("actor"), ACTORS["system"])
    text = no_latex(e["text"])
    st.markdown(f":{colour}[**{name}**] {ICONS.get(e['kind'], '•')} `{e['t']:>6}s` {text}")


@lru_cache(maxsize=None)
def portrait(actor: str) -> str:
    """An <img> of the actor's portrait (inlined, so st.markdown can round it), or its emoji if the file is missing."""
    name, emoji = PORTRAITS.get(actor, PORTRAITS["apollo"])
    path = ASSETS / name
    if not path.is_file():
        return f'<span style="font-size:44px;line-height:64px">{emoji}</span>'
    data = base64.b64encode(path.read_bytes()).decode()
    return (f'<img src="data:image/jpeg;base64,{data}" alt="{name}" '
            f'style="width:64px;height:64px;border-radius:12px;object-fit:cover">')


def now_card(slot, e: dict):
    """The "Now working" card: who is acting and what they are doing, from the latest event."""
    name = ACTORS.get(e.get("actor"), ACTORS["system"])[0]
    text = no_latex(html.escape(e["text"]))
    slot.markdown(f'<div style="display:flex;gap:14px;align-items:center;padding:10px 14px;margin-bottom:8px;'
                  f'border:1px solid rgba(128,128,128,.35);border-radius:14px">{portrait(e.get("actor"))}'
                  f'<div><div style="font-size:.8em;opacity:.7">Now working</div><b>{name}</b>'
                  f'<div>{text}</div></div></div>', unsafe_allow_html=True)


def record(ev, metrics):
    ss.events.append({"t": ev.t, "actor": ev.actor, "kind": ev.kind, "text": ev.text, "data": ev.data})
    now_card(now_slot, ss.events[-1])                   # now_slot: set by the run block, the only caller
    log_line(ss.events[-1])
    m_round, m_sources, m_gaps, m_tokens = metrics
    m_round.metric("Round", ss.run.round)
    m_sources.metric("Source URLs returned",
                     len({u for e in ss.events if e["kind"] == "search" for u in (e["data"] or {}).get("urls", [])}))
    m_gaps.metric("Gaps flagged", sum(e["kind"] == "gap" for e in ss.events))
    m_tokens.metric("Tokens used", f"{ss.app.budget.used:,}")
    show_models()


def run_one_round(app, state, graph_slot, metrics):
    ss.phase = "round"
    with st.status(f"Round {state.round + 1}: Athena is searching and Hermes is reviewing…", expanded=True) as status:
        for ev in app.run_round(state):
            record(ev, metrics)
        status.update(label=f"Round {state.round} done", state="complete", expanded=False)
    show_graph(graph_slot, state.plan)
    ss.phase = "gaps"
    ss.gap_rows = [{"approve": True, "id": g["id"], "type": g["type"],
                    "sub-question": f"{g['sq'].id}: {g['sq'].question}", "detail": g["detail"], "query": g["query"]}
                   for g in state.pending_gaps]
    ss.paused_at = time.time()


def write_report(app, state, metrics):
    ss.phase = "writing"
    with st.status("Writing the sections and verifying every sentence…", expanded=True) as status:
        for ev in app.finish(state):
            if ev.kind == "done":
                ss.report, ss.phase = ev.data["report"], "done"
                status.update(label="Report ready", state="complete", expanded=False)
                break
            record(ev, metrics)


def gap_choices() -> list[dict]:
    """The gap rows with the user's edits from the gap editor applied."""
    rows = [dict(r) for r in ss.gap_rows]
    edits = ss.get(f"gap_editor_{ss.run.round}") or {}
    for i, change in edits.get("edited_rows", {}).items():
        rows[int(i)].update(change)
    return rows


def resume_clock(state):
    """Time spent waiting for the gap review doesn't count against the research time limit."""
    if ss.paused_at:
        state.started += time.time() - ss.paused_at
        ss.paused_at = None


# ---------------- topic input ----------------
play_intro()
st.title("Apollo")
st.caption("Give it a topic. Apollo plans the report, Athena searches, Hermes checks for gaps, "
           "and every sentence in the final report links to the source that supports it.")

research_tab, benchmark_tab = st.tabs(["Research", "Benchmark"])

with research_tab:
    topic = st.text_input("Research topic", placeholder="Impact of AI on Indian agriculture")
    col_a, col_b = st.columns([1, 5])
    if col_a.button("Plan research", type="primary", disabled=not topic.strip()):
        reset()
        try:
            with st.spinner("Apollo is planning sections and sub-questions…"):
                app = Apollo(limits())
                for w in app.llm.warnings:
                    st.warning(w)
                ss.plan = app.plan(topic.strip())
                ss.rows = plan_rows(ss.plan)
                ss.app = app
        except PlanError as e:
            st.error(f"Planning failed: {e}. Try rephrasing the topic.")
        except Exception as e:                   # no usable model in .env, every model failed, network trouble
            st.error(problem(e, "while planning"))

    if not ss.plan and ss.run is None and not ss.report:
        st.info("Type a topic and press **Plan research**. Apollo drafts sections and sub-questions first; with "
                "*Let me review the plan before searching* ticked in the sidebar, you can edit them before any "
                "search runs.")

    # ---------------- human-in-the-loop plan review ----------------
    start = False
    if ss.plan and ss.run is None and not ss.report:
        if review_plan:
            st.subheader("Review the plan")
            editor_col, graph_col = st.columns([3, 2])
            with editor_col:
                st.caption("Edit, add or delete rows. c1 and c2 are the acceptance criteria (leave a cell empty for "
                           "none). Pick any sources for each sub-question; only those enabled in the sidebar are "
                           "searched (" + ", ".join(sources or ["web"]) + ").")
                table = pd.DataFrame(ss.rows)
                table["sources"] = table["sources"].map(lambda s: [t.strip() for t in str(s).split(",") if t.strip()])
                text = st.column_config.TextColumn
                edited = st.data_editor(table, num_rows="dynamic", width="stretch", key="plan_editor",
                                        hide_index=True, column_config={
                                            "section": text("section", width="small"),
                                            "perspective": text("perspective", width="small"),
                                            "question": text("question", width="medium"),
                                            "c1": text("c1", width="small"), "c2": text("c2", width="small"),
                                            "query": text("query", width="small"),
                                            "sources": st.column_config.MultiselectColumn(
                                                "sources", options=list(SOURCE_TYPES), default=["web"])})
                start = st.button("Approve plan", type="primary")
            edited["sources"] = edited["sources"].map(lambda v: ", ".join(v) if isinstance(v, (list, tuple))
                                                      else str(v or ""))
            rows = edited.fillna("").to_dict("records")
            try:
                draft = plan_from_rows(ss.plan.topic, rows, limits())
            except PlanError:
                draft = ss.plan
            with graph_col:
                show_graph(st.empty(), draft)
            if start:
                try:
                    ss.plan = plan_from_rows(ss.plan.topic, rows, limits())
                except PlanError as e:
                    st.error(f"The edited plan isn't usable: {e}")
                    start = False
        else:
            start = True

    # ---------------- the run: one round per script run when gaps are reviewed ----------------
    if start:
        ss.app.limits = limits()
        ss.app.budget.limit = ss.app.limits.token_budget
        ss.run, ss.events, ss.gate_log, ss.phase = ss.app.start_run(ss.plan), [], [], "start"
        st.rerun()                                       # redraw without the plan editor before searching starts

    if ss.run is not None and not ss.report:
        app, state = ss.app, ss.run
        interrupted = ss.phase in ("round", "writing")
        action, ss.action = ss.action, None
        live, side = st.columns([3, 2])
        with side:
            graph_slot = st.empty()
            show_graph(graph_slot, state.plan)
            metrics = (st.empty(), st.empty(), st.empty(), st.empty())
        with live:
            now_slot = st.empty()
            if ss.events:
                now_card(now_slot, ss.events[-1])
                with st.expander(f"Run log so far ({len(ss.events)} events)", expanded=False):
                    for e in ss.events:
                        log_line(e)
            if interrupted and action != "write":
                st.warning("The last step stopped before it finished (the page reran, or something went wrong). "
                           "Press **Write report now** to write the report from what was found so far, or "
                           "**Start a new topic**.")
                st.button("Write report now", type="primary", on_click=lambda: ss.update(action="write"))
            else:
                failed = False
                try:
                    if action == "write":
                        resume_clock(state)
                        choice = "write after interruption" if interrupted else "write report now"
                        ss.gate_log.append({"round": state.round, "approved": 0, "edited": 0, "choice": choice})
                        app.approve_gaps(state, [], write_now=True)
                    elif action == "next":
                        resume_clock(state)
                        rows = gap_choices()
                        approved = [r["id"] for r in rows if r["approve"]]
                        original = {g["id"]: g["query"] for g in state.pending_gaps}
                        edits = {r["id"]: r["query"] for r in rows if str(r["query"]).strip() != original.get(r["id"])}
                        ss.gate_log.append({"round": state.round, "choice": "run next round",
                                            "approved": len(approved), "edited": len(edits)})
                        app.approve_gaps(state, approved, edits)
                    if ss.phase == "start" or action == "next":
                        while not state.done:
                            run_one_round(app, state, graph_slot, metrics)
                            if state.done or review_gaps:
                                break
                            app.approve_gaps(state, [g["id"] for g in state.pending_gaps], {})
                    if state.done:
                        write_report(app, state, metrics)
                except Exception as e:               # the phase stays "round" / "writing": the next run offers to write
                    failed = True
                    st.error(problem(e, "during the run"))
                    st.button("Write report now", type="primary", key="write_after_error",
                              on_click=lambda: ss.update(action="write"))
                if state.done and not failed:
                    st.rerun()
                elif ss.phase == "gaps" and not failed:
                    st.subheader(f"Gaps after round {state.round}")
                    st.caption("Untick a gap to skip it; edit a query to search for something else. Apollo follows up "
                               "at most one gap per sub-question (the most important one).")
                    st.data_editor(pd.DataFrame(ss.gap_rows), key=f"gap_editor_{state.round}", hide_index=True,
                                   width="stretch", disabled=["id", "type", "sub-question", "detail"],
                                   column_config={"approve": st.column_config.CheckboxColumn("follow up",
                                                                                             width="small"),
                                                  "id": st.column_config.TextColumn("id", width="small"),
                                                  "type": st.column_config.TextColumn("type", width="small"),
                                                  "sub-question": st.column_config.TextColumn("sub-question",
                                                                                              width="medium"),
                                                  "detail": st.column_config.TextColumn("detail", width="medium"),
                                                  "query": st.column_config.TextColumn("query", width="medium")})
                    b1, b2, _ = st.columns([1, 1, 3])
                    b1.button("Run next round", type="primary", on_click=lambda: ss.update(action="next"))
                    b2.button("Write report now", on_click=lambda: ss.update(action="write"))
        if st.button("Start a new topic", key="new_topic_running"):
            reset()
            st.rerun()

    # ---------------- results ----------------
    rep = ss.report
    if rep:
        s = rep.stats
        c = st.columns(6)
        c[0].metric("Rounds", s.get("rounds"))
        c[1].metric("Sources collected", s.get("sources_collected"))
        c[2].metric("Sources cited", s.get("sources_cited", 0))
        c[3].metric("Sentences kept", s.get("sentences_kept", 0))
        c[4].metric("Removed in verification", s.get("sentences_removed", 0))
        c[5].metric("Tokens", f"{s.get('tokens_used', 0):,}")
        st.caption(f"Stop condition: {s.get('stop_reason')} · {s.get('failed_searches')} failed searches · "
                   f"{s.get('sentences_unverified_removed', 0)} unverifiable sentences removed · {s.get('seconds')}s")
        m = s.get("models")
        if m:
            c = st.columns(5)
            c[0].metric("Strong tokens", f"{m['usage'].get('strong', {}).get('tokens', 0):,}")
            c[1].metric("Fast tokens", f"{m['usage'].get('fast', {}).get('tokens', 0):,}")
            c[2].metric("Model or key switches", m["switches"])
            c[3].metric("Estimated cost", f"${m.get('cost_usd', 0):.4f}")
            c[4].metric("Tavily searches", tavily_searches(rep.state))
            st.caption("Models used: " + ", ".join(f"{role} = {label}" for role, label in m["active"].items())
                       + " · " + " · ".join(f"{role}: {role_tokens(u)}" for role, u in m["usage"].items())
                       + ("" if m.get("priced") else " · no prices set, so the cost is 0"))

        t_report, t_evidence, t_sources, t_trace, t_removed = st.tabs(
            ["Report", "Evidence", "Sources", "Trace", "Removed sentences"])
        with t_report:
            st.markdown(no_latex(rep.markdown))
            d1, d2, d3 = st.columns(3)
            d1.download_button("Download Markdown", rep.markdown, file_name="apollo_report.md", mime="text/markdown")
            d3.download_button("Download provenance JSON",
                               json.dumps(provenance(rep), indent=2, default=str, ensure_ascii=False),
                               file_name=f"apollo_provenance_{rep.state.run_id}.json", mime="application/json")
            try:
                from apollo.export import markdown_to_pdf
                d2.download_button("Download PDF", markdown_to_pdf(rep.markdown), file_name="apollo_report.pdf",
                                   mime="application/pdf")
            except ImportError:
                d2.caption("PDF export needs the fpdf2 package: run `pip install fpdf2`. Download Markdown works now.")
            except Exception as e:
                d2.caption(f"PDF export failed ({type(e).__name__}: {str(e)[:120]}). Use Download Markdown instead.")
        with t_evidence:
            st.caption("Open any sentence to see the exact passages it cites. Words the sentence shares with a "
                       "passage are in bold.")
            sentences = rep.sentences
            sections = list(dict.fromkeys(x["section"] for x in sentences))
            f1, f2 = st.columns([3, 1])
            pick = f1.selectbox("Section", ["All sections"] + sections, key="ev_section")
            show_removed = f2.toggle("Show removed sentences", key="ev_removed")
            shown = [x for x in sentences if pick in ("All sections", x["section"])]
            if not any(x["verdict"] == "kept" for x in sentences):
                st.info("No sentence survived verification, so there is nothing to inspect yet. The *Removed "
                        "sentences* tab shows what was dropped and why.")
            elif not any(x["verdict"] == "kept" for x in shown):
                st.caption("No kept sentences in this section. Pick another section or *All sections*.")
            for x in (x for x in shown if x["verdict"] == "kept"):
                plain = plain_sentence(x["text_with_refs"])
                with st.expander(no_latex(f"{x['section']}: {plain[:160]}{'…' if len(plain) > 160 else ''}")):
                    st.markdown(no_latex(x["text_with_refs"]))
                    if not x["evidence"]:
                        st.caption(f"No cited passage: this sentence {x['reason']}.")
                    for ev in x["evidence"]:
                        ref = f"[{ev['ref_number']}] " if ev["ref_number"] else ""
                        st.markdown(f"**{ref}[{ev['title'] or ev['url']}]({ev['url']})** · {ev['domain']} · "
                                    f"{ev['published'] or 'date unknown'}")
                        st.markdown("> " + no_latex(highlight(ev["passage"], x["text_with_refs"])))
            if show_removed:
                st.subheader("Removed sentences")
                removed_rows = [x for x in shown if x["verdict"] == "removed"]
                for x in removed_rows:
                    st.markdown(no_latex(f"- **{x['section']}**: {plain_sentence(x['text_with_refs'])}  \n  "
                                         f"*Removed: {x['reason']}*"))
                if not removed_rows:
                    st.write("No sentences were removed in this section.")
        with t_sources:
            if not rep.sources:
                st.info("No sources were collected: every search failed or returned nothing. The *Trace* tab shows "
                        "each search; if web or news searches failed, check TAVILY_API_KEYS in .env.")
            else:
                src = pd.DataFrame(rep.sources)
                for col in src.columns:   # e.g. ref numbers or years mixed with "": one text column for Arrow
                    if src[col].dropna().map(type).nunique() > 1:
                        src[col] = src[col].fillna("").astype(str)
                st.dataframe(src, width="stretch", hide_index=True,
                             column_config={"url": st.column_config.LinkColumn("URL"),
                                            "ref": st.column_config.TextColumn("Ref #")})
        with t_trace:
            g_col, r_col = st.columns([2, 3])
            show_graph(g_col.empty(), rep.state.plan)
            gate = {g["round"]: g for g in ss.gate_log}
            if not rep.state.round_log:
                r_col.info("No research round finished, so there is nothing to trace yet.")
            else:
                r_col.dataframe(pd.DataFrame([{
                    "round": r["round"],
                    "queries sent": ", ".join(f"{src} {n}" for src, n in r["queries"].items()) or "0",
                    "new domains": r["new_domains"],
                    "gaps by type": ", ".join(f"{t} {n}" for t, n in r["gaps"].items()) or "none",
                    "decision": r["decision"], "reason": r["reason"],
                    **({"your choice": (f"{gate[r['round']]['choice']} ({gate[r['round']]['approved']} approved, "
                                        f"{gate[r['round']]['edited']} edited)" if r["round"] in gate else "")}
                       if gate else {})} for r in rep.state.round_log]), width="stretch", hide_index=True)
            st.dataframe(pd.DataFrame([{"t": e["t"], "actor": ACTORS.get(e.get("actor"), ACTORS["system"])[0],
                                        "kind": e["kind"], "text": e["text"]} for e in ss.events]),
                         width="stretch", hide_index=True)
            for log in rep.state.review_log:
                with st.expander(f"Hermes review, round {log['round']}, section {log['section']}"):
                    st.json(log["review"])
        with t_removed:
            if rep.removed:
                st.dataframe(pd.DataFrame(rep.removed), width="stretch", hide_index=True)
            else:
                st.info("Verification removed nothing: every drafted sentence cited a passage that supports it.")

        if st.button("Start a new topic"):
            reset()
            st.rerun()

# ---------------- benchmark ----------------
with benchmark_tab:
    bench_dir = newest_benchmark()
    if bench_dir is None:
        st.info("No benchmark has been run yet. Run `python -m scripts.benchmark` (or with `--apollo-runs` to "
                "reuse saved Apollo runs); the newest `runs/benchmark-*/benchmark.md` is shown here.")
    else:
        st.caption(f"Newest benchmark: {bench_dir}")
        try:
            st.markdown(no_latex((bench_dir / "benchmark.md").read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            st.error(f"Couldn't read {bench_dir / 'benchmark.md'} ({type(e).__name__}). Run "
                     f"`python -m scripts.benchmark` again to write a new one.")

show_models()
