"""LLM methods and the agent run end to end with the offline MockLLM (no API key needed)."""

import pytest

from mrta.llm import LLM_METHODS, make_client
from mrta.llm.client import parse_json
from mrta.llm.methods import parse_plan
from mrta.checker import check
from mrta.scenarios import generate, render
from mrta.spec_eval import compare


@pytest.mark.parametrize("method", list(LLM_METHODS))
@pytest.mark.parametrize("style", ["explicit", "informal"])
def test_oracle_mock_gives_feasible_plans(method, style):
    scn = generate("S", 1)
    client = make_client("mock-oracle")
    res = LLM_METHODS[method](client, scn, render(scn, style), style)
    assert res.plan is not None and res.llm_calls >= 1
    assert check(scn, res.plan).feasible


def test_noisy_mock_errors_are_caught():
    scn = generate("M", 0)
    client = make_client("mock")
    client.rate = 0.9
    res = LLM_METHODS["llm-direct"](client, scn, render(scn, "explicit"), "explicit")
    assert not check(scn, res.plan).feasible


def test_spec_compare_perfect_and_degraded():
    scn = generate("S", 2)
    assert compare(scn, scn)["spec_f1"] == 1.0
    worse = scn.model_copy(deep=True)
    for t in worse.tasks:
        t.deadline = None
    worse.precedence = []
    assert compare(worse, scn)["spec_f1"] < 1.0


def test_parsers_tolerate_chatter_and_case():
    scn = generate("S", 0)
    text = "Sure! ```json\n{\"routes\": {\"" + scn.robots[0].id.lower() + "\": [\"t1\"]}, \"standby\": []}\n```"
    plan = parse_plan(text, scn)
    assert plan.routes == {scn.robots[0].id: ["T1"]}
    assert parse_json('noise {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
