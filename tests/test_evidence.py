"""Offline tests of the evidence inspector data: every report sentence with its verdict, reason and the exact
passages it cites, reference numbers that match the report, duplicates marked, and the bold-overlap helper.
Run: python -m tests.test_evidence"""
import sys

from apollo import agents
from apollo.config import Limits
from apollo.pipeline import Apollo
from apollo.state import Section
from apollo.writer import (CITE, NO_CLAIM, SectionResult, VerifiedSentence, dedupe, highlight, plain_sentence)
from tests.test_offline import FakeLLM, fake_search


def _report():
    agents.run_search = fake_search
    app = Apollo(Limits(max_rounds=3, parallel_searches=2), llm=FakeLLM())
    events = list(app.research(app.plan("Impact of AI on Indian agriculture")))
    return events[-1].data["report"]


def test_report_sentences_carry_their_evidence():
    rep = _report()
    sents = rep.sentences
    assert sents and {"Introduction", "Conclusion"} <= {x["section"] for x in sents}
    refs = {r["n"]: r["url"] for r in rep.references}
    for x in sents:
        assert "[C" not in x["text_with_refs"] and not CITE.search(x["text_with_refs"]), x   # no chunk id leaks
        assert x["verdict"] in ("kept", "removed") and x["reason"], x                     # every verdict has a reason
        if x["verdict"] == "kept":
            assert x["evidence"] or x["reason"] == NO_CLAIM, x   # a kept factual sentence always shows a passage
            for ev in x["evidence"]:
                assert refs[ev["ref_number"]] == ev["url"] and ev["passage"] and ev["domain"], ev
                assert f"[[{ev['ref_number']}]]({ev['url']})" in x["text_with_refs"], x
            assert x["text_with_refs"] in rep.markdown, x["text_with_refs"]   # the inspector shows the report's text
    kept_facts = [x for x in sents if x["verdict"] == "kept" and x["evidence"]]
    assert kept_facts, "the fake run keeps cited sentences"
    removed = {x["reason"] for x in sents if x["verdict"] == "removed"}
    assert {"no valid citation", "not supported by cited source"} <= removed, removed
    assert any(x["section"] == "Conclusion" and x["verdict"] == "removed" for x in sents)   # the invented [C999] fact
    assert rep.stats["sentences_kept"] == sum(x["verdict"] == "kept" for x in sents
                                              if x["section"] not in ("Introduction", "Conclusion"))


def test_dedupe_marks_the_duplicate_record():
    a = SectionResult(Section("s1", "A", []), "Drones cut spraying costs by 30% in Punjab [C1].",
                      sentences=[VerifiedSentence("A", "Drones cut spraying costs by 30% in Punjab [C1].", ["C1"],
                                                  "kept", "supported by the cited source")])
    b_text = "Drones cut spraying costs by 30% in Punjab [C2]. Soil sensors are spreading [C3]."
    b = SectionResult(Section("s2", "B", []), b_text, sentences=[
        VerifiedSentence("B", "Drones cut spraying costs by 30% in Punjab [C2].", ["C2"], "kept", "ok"),
        VerifiedSentence("B", "Soil sensors are spreading [C3].", ["C3"], "kept", "ok")])
    assert dedupe([a, b]) == 1
    assert b.text == "Soil sensors are spreading [C3]."
    assert [(v.verdict, v.reason) for v in b.sentences] == [("removed", "duplicate"), ("kept", "ok")]
    assert a.sentences[0].verdict == "kept"


def test_highlight_and_plain_sentence():
    sentence = "Drones were used on 150 farms in Punjab [[1]](https://pib.gov.in/x)."
    assert plain_sentence(sentence) == "Drones were used on 150 farms in Punjab."
    out = highlight("Farmers in Punjab used drones on 150 farms *fast* via https links.", sentence)
    for word in ("Punjab", "used", "drones", "150", "farms"):
        assert f"**{word}**" in out, out
    assert "**in**" not in out and "**on**" not in out and "**https**" not in out, out   # stopwords, the link
    assert r"\*fast\*" in out, "markdown in web text is escaped"


def main():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for t in (test_report_sentences_carry_their_evidence, test_dedupe_marks_the_duplicate_record,
              test_highlight_and_plain_sentence):
        t()
        print("ok  ", t.__name__)
    print("ALL EVIDENCE TESTS PASSED")


if __name__ == "__main__":
    main()
