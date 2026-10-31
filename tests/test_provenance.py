"""Offline tests of actors on events, the code-generated Methodology section and the provenance export.
Run: python -m tests.test_provenance"""
import json
import sys
import tempfile
from pathlib import Path

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo, provenance
from apollo.state import ACTORS
from scripts import run_topic
from tests.test_offline import FakeLLM, fake_search


def _run():
    agents.run_search = fake_search
    app = Apollo(Limits(max_rounds=3, parallel_searches=2), llm=FakeLLM())
    events = list(app.research(app.plan("Impact of AI on Indian agriculture")))
    return events, events[-1].data["report"]


def test_every_event_has_an_actor():
    events, _ = _run()
    assert all(e.actor in ACTORS for e in events), {(e.kind, e.actor) for e in events if e.actor not in ACTORS}
    by_kind = {}
    for e in events:
        by_kind.setdefault(e.kind, set()).add(e.actor)
    expected = {"plan": {"apollo"}, "round": {"apollo"}, "search": {"athena"}, "search_fail": {"athena"},
                "review": {"hermes"}, "decision": {"apollo"}, "write": {"writer"}, "verify": {"writer"},
                "done": {"system"}}
    for kind, actors in expected.items():
        assert by_kind.get(kind) == actors, (kind, by_kind.get(kind))
    assert by_kind["gap"] <= {"apollo", "hermes"}          # route gaps are Apollo's; code-found conflicts Hermes'


def test_methodology_lists_every_round():
    _, rep = _run()
    md, st = rep.markdown, rep.state
    assert md.index("## Conclusion") < md.index("## Methodology") < md.index("## Limitations")
    method = md[md.index("## Methodology"):md.index("## Limitations")]
    assert len(st.round_log) == st.round == 2
    for r in st.round_log:
        assert f"- Round {r['round']}: {sum(r['queries'].values())} search call(s)" in method, method
    assert "search again" in method and "decided to stop" in method
    assert "- Perspectives: farmer, industry, regulator." in method
    assert "- Plan: 2 sections, 4 sub-questions." in method
    assert f"- Stop reason: {st.stop_reason}." in method
    assert "- Models: not recorded." in method               # the fake LLM has no status(); nothing is invented
    s = rep.stats
    assert f"- Verification: {s['sentences_kept']} sentence(s) kept, {s['sentences_removed']} removed" in method


def test_provenance_loads_and_covers_every_cited_chunk():
    _, rep = _run()
    prov = json.loads(json.dumps(provenance(rep), default=str))
    st = rep.state
    assert prov["run_id"] == st.run_id and prov["topic"] == st.plan.topic
    assert prov["settings"]["max_rounds"] == 3 and prov["settings"]["enabled_sources"] == list(st.limits.enabled_sources)
    chunk_ids = {c["id"] for c in prov["chunks"]}
    assert chunk_ids == set(st.chunks) and all(c["domain"] and c["text"] for c in prov["chunks"])
    cited = {cid for x in prov["sentences"] if x["verdict"] == "kept" for cid in x["chunk_ids"]}
    cited |= {ev["chunk_id"] for x in prov["sentences"] if x["verdict"] == "kept" for ev in x["evidence"]}
    assert cited and cited <= chunk_ids, cited - chunk_ids
    assert [r["round"] for r in prov["rounds"]] == [1, 2] and len(prov["decisions"]) == 2
    assert prov["reviews"] and prov["stats"]["sentences_kept"] == rep.stats["sentences_kept"]
    rounds = {s["round"] for s in prov["searches"]}
    assert rounds == {1, 2} and all(s["query"] and s["source"] for s in prov["searches"])
    failed = [s for s in prov["searches"] if s["status"] == "failed"]
    assert failed and all(s["source"] == "edgar" and "timeout" in s["error"] for s in failed)   # fake edgar times out
    assert sum(sum(r["queries"].values()) for r in prov["rounds"]) == len(prov["searches"])
    q = prov["plan"]["sections"][0]["sub_questions"][0]
    assert {"perspective", "acceptance_criteria", "status", "queries"} <= set(q)


def test_run_topic_saves_provenance_and_actors():
    agents.run_search = fake_search
    with tempfile.TemporaryDirectory() as tmp:
        code, out = run_topic.run("Impact of AI on Indian agriculture", Limits(), runs_dir=tmp,
                                  app=Apollo(Limits(max_rounds=3, parallel_searches=2), llm=FakeLLM()))
        assert code == 0
        prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
        assert prov["chunks"] and prov["sentences"]
        events = [json.loads(line) for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        assert all(e["actor"] in ACTORS for e in events)
        assert {e["actor"] for e in events} >= {"apollo", "athena", "hermes", "writer", "system"}


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_every_event_has_an_actor, test_methodology_lists_every_round,
              test_provenance_loads_and_covers_every_cited_chunk, test_run_topic_saves_provenance_and_actors):
        t()
        print("ok  ", t.__name__)
    print("ALL PROVENANCE TESTS PASSED")


if __name__ == "__main__":
    main()
