# Prompt 1A — acceptance criteria and perspectives
Owner: A · Branch: role-a · When: H1 · Needs: Gate 1

```text
Read CLAUDE.md. Work only in apollo/state.py, apollo/agents.py, app.py (plan editor columns)
and tests/. Goal: Apollo plans from several perspectives and Hermes checks evidence against
explicit acceptance criteria.

1. SubQ gets: perspective: str, acceptance_criteria: list[str] (1 to 3 items),
   criteria_met: dict[str, bool], added_in_round: int = 1.
2. Planner prompt: first list 3 or 4 stakeholder perspectives for the topic (for example
   farmer, regulator, industry, researcher). Then write sections and sub-questions; each
   sub-question names its perspective and 1 to 3 acceptance criteria. A criterion is a
   checkable statement about the evidence, e.g. "a figure dated 2023 or later" or
   "at least one named government scheme". No vague criteria like "good coverage".
3. validate_plan(): keep at most 3 criteria, trim each to 150 characters, and add
   "supported by at least two independent sources" when none are given.
4. Hermes prompt: per sub-question return criteria_met {criterion: true/false}, judged only
   from the chunks shown. Store it on the SubQ. Ignore criteria Hermes invents.
5. route(): after the coverage check, add gap type "criteria" when any criterion is false;
   detail lists the unmet criteria; the follow-up query uses Hermes' follow_up_query.
6. plan_from_rows() and the plan editor: add perspective and criteria columns
   (criteria separated by ";").
7. Tests: the fake LLM returns criteria; assert a criteria gap appears in round 1 and the
   sub-question becomes covered in round 2 once the fake Hermes marks it met.

Done when the offline test passes and a live run shows perspectives and criteria in the plan.
```
