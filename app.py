"""Streamlit UI. Run: streamlit run app.py"""
import pandas as pd
import streamlit as st

from apollo.agents import PlanError, plan_from_rows, plan_rows
from apollo.config import SOURCE_TYPES, Limits
from apollo.pipeline import Apollo
from apollo.writer import highlight, plain_sentence

st.set_page_config(page_title="Apollo research", page_icon="🔭", layout="wide")

ICONS = {"plan": "🗺️", "round": "🔁", "search": "🔎", "search_fail": "⚠️", "review": "🧐", "gap": "🕳️",
         "decision": "🧭", "write": "✍️", "verify": "✅", "error": "❌", "done": "📄"}

ss = st.session_state
for k, v in {"plan": None, "rows": None, "events": [], "report": None}.items():
    ss.setdefault(k, v)

# ---------------- settings ----------------
with st.sidebar:
    st.header("Limits")
    max_rounds = st.slider("Maximum research rounds", 1, 5, 3)
    minutes = st.slider("Time limit for research (minutes)", 2, 30, 10)
    budget = st.number_input("Token budget", 50_000, 2_000_000, 400_000, step=50_000)
    sources = st.multiselect("Source types", SOURCE_TYPES, default=["web", "news", "arxiv"],
                             help="edgar needs SEC_USER_AGENT in .env and only helps for US-listed companies")
    review_plan = st.checkbox("Let me review the plan before searching", value=True)


def limits() -> Limits:
    return Limits(max_rounds=max_rounds, max_seconds=minutes * 60, token_budget=int(budget),
                  enabled_sources=tuple(sources or ["web"]))


# ---------------- topic input ----------------
st.title("Apollo")
st.caption("Give it a topic. Apollo plans the report, Athena searches, Hermes checks for gaps, "
           "and every sentence in the final report links to the source that supports it.")

topic = st.text_input("Research topic", placeholder="Impact of AI on Indian agriculture")
col_a, col_b = st.columns([1, 5])
if col_a.button("Plan research", type="primary", disabled=not topic.strip()):
    ss.update(plan=None, rows=None, events=[], report=None)
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
    except RuntimeError as e:
        st.error(str(e))

# ---------------- human-in-the-loop plan review ----------------
start = False
if ss.plan and not ss.report:
    if review_plan:
        st.subheader("Review the plan")
        st.caption("Edit, add or delete rows. c1 and c2 are the acceptance criteria (leave a cell empty for none). "
                   "Sources can be any of: " + ", ".join(sources or ["web"]))
        edited = st.data_editor(pd.DataFrame(ss.rows), num_rows="dynamic", width="stretch",
                                key="plan_editor")
        start = st.button("Approve plan and start research", type="primary")
        if start:
            try:
                ss.plan = plan_from_rows(ss.plan.topic, edited.fillna("").to_dict("records"), limits())
            except PlanError as e:
                st.error(f"The edited plan isn't usable: {e}")
                start = False
    else:
        start = True

# ---------------- live run ----------------
if start:
    app = ss.app
    app.limits = limits()
    app.budget.limit = app.limits.token_budget
    live, side = st.columns([3, 1])
    with side:
        m_round, m_sources, m_gaps, m_tokens = st.empty(), st.empty(), st.empty(), st.empty()
    n_gaps = 0
    with live:
        with st.status("Researching…", expanded=True) as status:
            for ev in app.research(ss.plan):
                if ev.kind == "done":
                    ss.report = ev.data["report"]
                    status.update(label="Report ready", state="complete", expanded=False)
                    break
                ss.events.append({"t": ev.t, "kind": ev.kind, "text": ev.text, "data": ev.data})
                st.write(f"{ICONS.get(ev.kind, '•')} `{ev.t:>6}s` {ev.text}")
                if ev.kind == "gap":
                    n_gaps += 1
                state_sources = len({u for e in ss.events if e["kind"] == "search" for u in e["data"]["urls"]})
                m_round.metric("Round", sum(1 for e in ss.events if e["kind"] == "round"))
                m_sources.metric("Source URLs returned", state_sources)
                m_gaps.metric("Gaps flagged", n_gaps)
                m_tokens.metric("Tokens used", f"{app.budget.used:,}")

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
        st.caption("Models used: " + ", ".join(f"{role} = {label}" for role, label in m["active"].items())
                   + f" · model or key switches: {m['switches']}")

    t_report, t_evidence, t_sources, t_trace, t_removed = st.tabs(
        ["Report", "Evidence", "Sources", "Trace", "Removed sentences"])
    with t_report:
        st.markdown(rep.markdown)
        d1, d2 = st.columns(2)
        d1.download_button("Download Markdown", rep.markdown, file_name="apollo_report.md", mime="text/markdown")
        try:
            from apollo.export import markdown_to_pdf
            d2.download_button("Download PDF", markdown_to_pdf(rep.markdown), file_name="apollo_report.pdf",
                               mime="application/pdf")
        except Exception as e:
            d2.caption(f"PDF export unavailable: {e}")
    with t_evidence:
        st.caption("Open any sentence to see the exact passages it cites. Words the sentence shares with a "
                   "passage are in bold.")
        sentences = rep.sentences
        sections = list(dict.fromkeys(x["section"] for x in sentences))
        f1, f2 = st.columns([3, 1])
        pick = f1.selectbox("Section", ["All sections"] + sections, key="ev_section")
        show_removed = f2.toggle("Show removed sentences", key="ev_removed")
        shown = [x for x in sentences if pick in ("All sections", x["section"])]
        for x in (x for x in shown if x["verdict"] == "kept"):
            plain = plain_sentence(x["text_with_refs"])
            with st.expander(f"{x['section']}: {plain[:160]}{'…' if len(plain) > 160 else ''}"):
                st.markdown(x["text_with_refs"])
                if not x["evidence"]:
                    st.caption(f"No cited passage: this sentence {x['reason']}.")
                for ev in x["evidence"]:
                    ref = f"[{ev['ref_number']}] " if ev["ref_number"] else ""
                    st.markdown(f"**{ref}[{ev['title'] or ev['url']}]({ev['url']})** · {ev['domain']} · "
                                f"{ev['published'] or 'date unknown'}")
                    st.markdown("> " + highlight(ev["passage"], x["text_with_refs"]))
        if show_removed:
            st.subheader("Removed sentences")
            removed_rows = [x for x in shown if x["verdict"] == "removed"]
            for x in removed_rows:
                st.markdown(f"- **{x['section']}**: {plain_sentence(x['text_with_refs'])}  \n  *Removed: {x['reason']}*")
            if not removed_rows:
                st.write("No sentences were removed in this section.")
    with t_sources:
        src = pd.DataFrame(rep.sources)
        if "ref" in src:   # numbers for cited sources, "" for the rest: one text column so Arrow can show it
            src["ref"] = src["ref"].astype(str)
        st.dataframe(src, width="stretch", hide_index=True,
                     column_config={"url": st.column_config.LinkColumn("URL"),
                                    "ref": st.column_config.TextColumn("Ref #")})
    with t_trace:
        st.dataframe(pd.DataFrame([{k: e[k] for k in ("t", "kind", "text")} for e in ss.events]),
                     width="stretch", hide_index=True)
        for log in rep.state.review_log:
            with st.expander(f"Hermes review, round {log['round']}, section {log['section']}"):
                st.json(log["review"])
    with t_removed:
        if rep.removed:
            st.dataframe(pd.DataFrame(rep.removed), width="stretch", hide_index=True)
        else:
            st.write("Verification removed nothing.")

    if st.button("Start a new topic"):
        ss.update(plan=None, rows=None, events=[], report=None)
        st.rerun()
