"""The model-edit language used by submit_spec."""

import json

from mrta.llm.edits import apply_edits
from mrta.scenarios import generate


def test_edits_change_single_facts():
    scn = generate("M", 3, version=2)
    model = json.loads(scn.model_dump_json())
    tid = scn.tasks[0].id
    new, problems = apply_edits(model, [
        {"op": "set", "target": f"task:{tid}", "field": "deadline", "value": 1234},
        {"op": "add_precedence", "before": scn.tasks[1].id, "after": tid},
        {"op": "set", "target": "model", "field": "horizon", "value": 99999},
        {"op": "set_standby", "robot_type": "quadcopter", "count": 1},
        {"op": "bogus"},
    ])
    assert problems and "unknown op" in problems[0]
    assert next(t for t in new["tasks"] if t["id"] == tid)["deadline"] == 1234
    assert {"before": scn.tasks[1].id, "after": tid} in new["precedence"]
    assert new["horizon"] == 99999
    assert {"robot_type": "quadcopter", "count": 1} in new["standby"]
    assert model["horizon"] != 99999  # the input is not modified
