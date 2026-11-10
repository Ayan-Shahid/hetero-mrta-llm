"""Regression tests for the failures seen in the first real-model pilot (qwen2.5-7b, dev split).

See docs/devlog.md, entry "Pilot: extracted models rejected or unsatisfiable".
"""

import json

from mrta.benchmark import load
from mrta.llm.agent import Agent, AGENT_PRESETS
from mrta.llm.client import make_client
from mrta.llm.edits import apply_edits
from mrta.llm.methods import MethodResult, clean_spec
from mrta.schema import Scenario


def _spec(sid="M-000"):
    return json.loads(load("data/benchmark", ids=[sid])[0].scenario.model_dump_json())


def test_capability_synonyms_from_the_pilot_are_normalised():
    m = _spec()
    m["tasks"][0]["required"] = ["thermal camera"]
    m["tasks"][1]["required"] = ["robotic arm", "cargo carrier"]
    m["robots"][0]["capabilities"] = ["Camera", "thermal camera", "cargo carrier"]
    out = clean_spec(m)
    assert out["tasks"][0]["required"] == ["thermal"]
    assert out["tasks"][1]["required"] == ["manipulate", "carry"]
    assert out["robots"][0]["capabilities"] == ["camera", "thermal", "carry"]
    Scenario.model_validate(out)


def test_missing_base_is_added_when_robots_start_there():
    m = _spec("S-001")
    m["locations"] = [loc for loc in m["locations"] if loc["name"] != "base"]
    out = clean_spec(m)
    assert out["locations"][0] == {"name": "base", "x": 0.0, "y": 0.0, "zone": "outdoor"}
    Scenario.model_validate(out)


def test_numeric_strings_and_synonyms_for_zone_and_type():
    m = _spec()
    m["horizon"] = "2400"
    m["locations"][1]["zone"] = "Rooftop"
    m["robots"][0]["type"] = "drone"
    out = clean_spec(m)
    assert out["horizon"] == 2400.0 and out["locations"][1]["zone"] == "roof"
    assert out["robots"][0]["type"] == "quadcopter"


def test_invalid_model_stays_editable_and_errors_are_short():
    rec = load("data/benchmark", ids=["M-000"])[0]
    out = MethodResult(plan=None, status="")
    agent = Agent(make_client("mock-oracle"), rec.scenario, rec.briefs["explicit"], "explicit",
                  AGENT_PRESETS["no-g2-crosscheck"], None, out)
    bad = _spec()
    bad["robots"][0]["start"] = "hangar"  # unknown location
    obs = agent.t_submit_spec({"spec": bad})
    assert obs["ok"] is False and all(len(p) < 400 for p in obs["problems"])
    fixed = agent.t_submit_spec({"base": "current", "edits": [
        {"op": "set", "target": f"robot:{bad['robots'][0]['id']}", "field": "start", "value": "base"}]})
    assert fixed["ok"] is True, fixed


def test_add_robot_edit_from_the_pilot():
    m = _spec()
    new, problems = apply_edits(m, [{"op": "add_robot", "value": {"id": "X9", "type": "rover"}},
                                    {"op": "add", "target": "robot", "value": {"id": "X9"}}])
    assert any(r["id"] == "X9" for r in new["robots"])
    assert problems and "already exists" in problems[0]
