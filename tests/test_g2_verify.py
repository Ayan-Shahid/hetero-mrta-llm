"""G2: model sanity checks and the cross-check diff."""

import json

from mrta.gaps.g2_verify import diff_models, plan_violations, sanity_problems
from mrta.scenarios import generate
from mrta.schema import Plan, Scenario


def test_crosscheck_reports_changed_facts():
    scn = generate("M", 3, version=2)
    other = json.loads(scn.model_dump_json())
    other["tasks"][0]["deadline"] = 5.0 if other["tasks"][0]["deadline"] is None else None
    other["precedence"] = []
    diffs = diff_models(scn, Scenario.model_validate(other))
    fields = {(d["item"], d.get("field")) for d in diffs}
    assert (f"task:{scn.tasks[0].id}", "deadline") in fields
    if scn.precedence:
        assert any(d["item"] == "precedence" for d in diffs)
    assert diff_models(scn, scn) == []


def test_sanity_flags_impossible_tasks():
    scn = generate("M", 3, version=2)
    bad = json.loads(scn.model_dump_json())
    bad["tasks"][0]["required"] = ["camera", "thermal", "carry", "manipulate"]
    assert sanity_problems(Scenario.model_validate(bad))
    assert sanity_problems(scn) == []


def test_plan_violations_on_empty_plan():
    scn = generate("S", 2, version=2)
    ok, viol, _ = plan_violations(scn, Plan(routes={}))
    assert not ok and viol
