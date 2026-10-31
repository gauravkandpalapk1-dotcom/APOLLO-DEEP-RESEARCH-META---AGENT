"""Offline test of the full loop with a fake LLM and fake search. No API keys or network needed.
Checks: two-round loop, empty/failed searches handled, stop logic, citation verification.
Run: python -m tests.test_offline"""
import json
import re

from apollo import agents
from apollo.config import Limits
from apollo.llm import Budget
from apollo.pipeline import Apollo
from apollo.search import Doc, SearchError
from apollo.writer import CITE


class FakeLLM:
    def __init__(self):
        self.budget = Budget(10**9)

    def chat(self, system, user, json_mode=False, **kw):
        self.budget.add(500)
        if system.startswith("You are a section writer"):
            ids = re.findall(r'<chunk id="(C\d+)"', user)
            if not ids:
                return "The sources found did not cover this question."
            words = ["Irrigation", "Drones", "Subsidies", "Satellites", "Markets", "Credit", "Weather", "Soil",
                     "Pricing", "Training", "Storage", "Insurance"]
            s = [f"{words[i % 12]} evidence point {cid} is described in detail here [{cid}]." for i, cid in enumerate(ids)]
            s.append("This sentence has no citation and should be removed.")
            s.append("This one cites a chunk the section never had [C999].")
            s.append(f"UNSUPPORTED claim the source does not make [{ids[0]}].")
            return " ".join(s)
        return json.dumps(self.json(system, user))

    def json(self, system, user, **kw):
        self.budget.add(500)
        if system.startswith("You are Apollo"):
            return {"sections": [
                {"title": "Adoption", "sub_questions": [
                    {"question": "How many farmers use AI tools?", "query": "farmers AI adoption", "source_types": ["web"]},
                    {"question": "What slows adoption?", "query": "AI adoption barriers farmers", "source_types": ["web", "arxiv"]}]},
                {"title": "Policy", "sub_questions": [
                    {"question": "Which obscure scheme covers drones in district X?", "query": "obscure scheme", "source_types": ["web", "edgar"]}]}]}
        if system.startswith("You are Hermes"):
            sqs = re.findall(r'<sub_question id="(q\d+)">', user)
            chunk_sq = re.findall(r'<chunk id="(C\d+)" sq="(q\d+)"', user)
            claims = [{"chunk_id": c, "sq_id": q, "entity": "India", "metric": "farmers using AI",
                       "value": "10" if i % 2 == 0 else "50", "unit": "million", "as_of_year": 2025}
                      for i, (c, q) in enumerate(chunk_sq) if q == "q1"]
            return {"sub_questions": [{"id": q, "answered": any(s == q for _, s in chunk_sq),
                                       "missing": "", "follow_up_query": f"{q} better query"} for q in sqs],
                    "claims": claims, "source_years": {}, "contradictions": []}
        if system.startswith("You check whether"):
            items = re.findall(r"### Item (\d+)\nSentence: (.*)", user)
            return {"results": [{"i": int(i), "supported": not s.startswith("UNSUPPORTED")} for i, s in items]}
        if system.startswith("You are the editor"):
            ids = re.findall(r"\[(C\d+)\]", user.split("Write an introduction")[0])
            return {"introduction": f"This report covers the topic [{ids[0]}].",
                    "conclusion": f"Main finding restated [{ids[-1]}]. Invented conclusion fact [C999]."}
        raise AssertionError(system)


calls = []


def fake_search(query, source_type, limits):
    calls.append((query, source_type))
    if source_type == "edgar":
        raise SearchError("timeout")                       # failed search
    if "obscure" in query or query.startswith("q3"):
        return []                                          # empty search
    if "barriers" in query and "better" not in query:      # q2: thin in round 1, filled in round 2
        return [Doc("Barriers A", "https://a.example/1", "Farmers face cost barriers. " * 20, "web", "2025-01-01")]
    n = len(calls)
    return [Doc(f"Doc {n}-{k}", f"https://x.example/{n}/{k}", f"Farmers AI adoption data {n} {k}. " * 30,
                source_type, "2025-03-01") for k in range(2)]


def main():
    agents.run_search = fake_search
    app = Apollo(Limits(max_rounds=3, parallel_searches=2), llm=FakeLLM())
    plan = app.plan("Impact of AI on Indian agriculture")
    events = list(app.research(plan))
    for e in events:
        if e.kind != "done":
            print(f"{e.t:>5}s {e.kind:<11} {e.text}")
    report = events[-1].data["report"]
    st = report.state

    assert events[-1].kind == "done"
    assert st.round == 2, f"expected 2 rounds, got {st.round}"
    assert st.plan.subqs["q3"].status == "unfillable"
    assert st.plan.subqs["q1"].status == "covered" and st.plan.subqs["q2"].status == "covered"
    assert st.failed_searches, "edgar failure should be recorded"
    assert any(c["found_by"] == "code" for c in st.contradictions), "10 vs 50 million should be flagged"
    assert "C999" not in report.markdown and not CITE.search(report.markdown), "raw chunk ids leaked"
    assert "UNSUPPORTED" not in report.markdown
    assert "no citation and should be removed" not in report.markdown
    assert "Invented conclusion fact" not in report.markdown
    assert "No usable sources found" in report.markdown
    assert report.references and "[[1]](" in report.markdown
    print("\nSTATS", report.stats)
    print("\n" + report.markdown[:1500])
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
