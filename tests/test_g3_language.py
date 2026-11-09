"""G3: simulated mission owner and staged extraction."""

import json

from mrta.gaps.g3_language import MissionOwner, staged_extraction
from mrta.llm.client import LLMConfig, LLMResponse
from mrta.llm.methods import MethodResult, Meter
from mrta.scenarios import generate, render
from mrta.schema import Scenario


class StageStub:
    """Answers each extraction pass with the matching part of the ground truth."""

    config = LLMConfig()

    def __init__(self, scn: Scenario):
        self.spec = json.loads(scn.model_dump_json())

    def chat(self, messages, *, purpose, context=None):
        s = self.spec
        if purpose == "stage_fleet":
            body = {"horizon": s["horizon"], "locations": s["locations"], "robots": s["robots"]}
        elif purpose == "stage_tasks":
            body = {"tasks": [{k: v for k, v in t.items() if k != "deadline"} for t in s["tasks"]]}
        else:
            body = {"deadlines": {t["id"]: t["deadline"] for t in s["tasks"]}, "precedence": s["precedence"],
                    "standby": s["standby"], "horizon": s["horizon"]}
        return LLMResponse(text=json.dumps(body))


def test_mission_owner_answers_only_what_is_asked():
    scn = generate("M", 3, version=2)
    owner = MissionOwner(scn, max_questions=2)
    tid = scn.tasks[0].id
    assert owner(f"What is the deadline of {tid}?").startswith(f"- {tid}:")
    assert "not sure" in owner("what should I do?")
    assert "No more questions" in owner(f"and {tid}?")


def test_staged_extraction_builds_a_valid_model():
    scn = generate("S", 3, version=2)
    meter = Meter(StageStub(scn), MethodResult(plan=None, status=""))
    draft, problems = staged_extraction(meter, render(scn, "informal"), {"scenario": scn, "style": "informal"})
    assert problems == []
    rebuilt = Scenario.model_validate(draft)
    assert [t.deadline for t in rebuilt.tasks] == [t.deadline for t in scn.tasks]
    assert meter.result.llm_calls == 3
