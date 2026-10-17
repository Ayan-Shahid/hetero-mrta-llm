"""Hand-built scenario where every constraint can be broken on purpose."""

import pytest

from mrta.checker import check
from mrta.schema import Location, Plan, Precedence, Robot, Scenario, Standby, Task, Zone


@pytest.fixture
def scn() -> Scenario:
    return Scenario(
        id="unit", horizon=600,
        locations=[Location(name="base", x=0, y=0), Location(name="roof", x=30, y=40, zone=Zone.ROOF),
                   Location(name="shed", x=60, y=0, zone=Zone.INDOOR), Location(name="cell", x=0, y=50, zone=Zone.INDOOR)],
        robots=[Robot(id="D1", type="quadcopter", mobility="air", capabilities=["camera", "carry"], speed=10,
                      payload=2, endurance=300, start="base"),
                Robot(id="D2", type="quadcopter", mobility="air", capabilities=["camera"], speed=10,
                      payload=2, endurance=300, start="base"),
                Robot(id="G1", type="rover", mobility="ground", capabilities=["carry", "camera"], speed=2,
                      payload=20, endurance=1000, start="base"),
                Robot(id="A1", type="fixed_arm", mobility="fixed", capabilities=["manipulate"], speed=0,
                      payload=0, endurance=86400, start="cell")],
        tasks=[Task(id="T1", kind="inspect_roof", location="roof", required=["camera"], duration=60),
               Task(id="T2", kind="deliver", location="shed", required=["carry"], payload=8, duration=30,
                    deadline=200),
               Task(id="T3", kind="valve_service", location="cell", required=["manipulate"], duration=100)],
        precedence=[Precedence(before="T2", after="T3")],
        standby=[Standby(robot_type="quadcopter", count=1)],
    )


GOOD = {"D1": ["T1"], "G1": ["T2"], "A1": ["T3"]}


def kinds(res):
    return {v.kind for v in res.violations}


def test_feasible_plan(scn):
    res = check(scn, Plan(routes=GOOD, standby=["D2"]))
    assert res.feasible, res.violations
    # G1: 60 m on the grid at 2 m/s = 30 s, unload 30 s -> T2 done at 60; T3 waits for it -> 60 + 100
    assert res.schedule.makespan == pytest.approx(160)


def test_capability_payload_reach(scn):
    res = check(scn, Plan(routes={"D2": ["T3"], "D1": ["T2"], "G1": ["T1"]}))
    assert {"capability", "unreachable", "payload"} <= kinds(res)


def test_coverage(scn):
    res = check(scn, Plan(routes={"D1": ["T1", "T1"], "G1": ["T2", "T9"], "X9": []}))
    assert {"duplicate", "missing", "unknown_task", "unknown_robot"} <= kinds(res)


def test_standby(scn):
    res = check(scn, Plan(routes={"D1": ["T1"], "D2": [], "G1": ["T2"], "A1": ["T3"]}, standby=["D1"]))
    assert "standby_conflict" in kinds(res)
    both_busy = {"D1": ["T1"], "D2": ["T1"], "G1": ["T2"], "A1": ["T3"]}
    assert "standby" in kinds(check(scn, Plan(routes=both_busy)))


def test_deadline_and_endurance(scn):
    slow = scn.model_copy(deep=True)
    slow.robots[2].speed = 0.25  # G1 now needs 240 s to reach the shed -> misses the 200 s deadline
    slow.robots[0].endurance = 50  # D1 cannot make the 100 s round trip to the roof
    res = check(slow, Plan(routes=GOOD))
    assert {"deadline", "endurance"} <= kinds(res)


def test_deadlock(scn):
    # G1 is told to do T1 before T2, but T1 may only start after T2 has finished: a cycle.
    cyc = scn.model_copy(deep=True)
    cyc.tasks[0].location = "shed"  # make T1 doable by G1 (camera, ground)
    cyc.precedence = [Precedence(before="T2", after="T1")]
    res = check(cyc, Plan(routes={"G1": ["T1", "T2"], "A1": ["T3"]}))
    assert "deadlock" in kinds(res)
