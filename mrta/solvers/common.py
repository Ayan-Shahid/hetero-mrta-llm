"""Shared pieces for the classical baselines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..geometry import TravelTable, eligibility_matrix
from ..schema import Plan, Scenario

EPS = 1e-6


@dataclass
class SolveResult:
    plan: Plan | None
    status: str  # optimal | feasible | heuristic | infeasible | no_solution | error
    time_s: float
    info: dict[str, Any] = field(default_factory=dict)


def reserve_standby(scn: Scenario, elig: dict[str, list[str]] | None = None,
                    table: TravelTable | None = None) -> list[str]:
    """Pick robots to keep on standby for the heuristic baselines.

    Prefers robots that are eligible for the fewest tasks (cheapest to lose),
    breaking ties by keeping back the robot farthest from its tasks, and never
    reserves a robot that is the only one able to do some task.
    """
    elig = elig or eligibility_matrix(scn)
    table = table or TravelTable(scn)
    tasks = {t.id: t for t in scn.tasks}
    reserved: list[str] = []

    def mean_travel(rid: str) -> float:
        robot = scn.robot(rid)
        legs = [table.from_start(robot, tasks[t]) for t in elig[rid]]
        return sum(legs) / len(legs) if legs else 0.0

    for req in scn.standby:
        cands = [r.id for r in scn.robots if r.type == req.robot_type and r.id not in reserved]
        cands.sort(key=lambda rid: (len(elig[rid]), -mean_travel(rid), rid))
        picked = 0
        for rid in cands:
            if picked == req.count:
                break
            others = [o for o in elig if o != rid and o not in reserved]
            sole = any(all(t not in elig[o] for o in others) for t in elig[rid])
            if sole:
                continue
            reserved.append(rid)
            picked += 1
        if picked < req.count:  # could not avoid sole providers; reserve anyway (plan will be infeasible)
            for rid in cands:
                if picked == req.count:
                    break
                if rid not in reserved:
                    reserved.append(rid)
                    picked += 1
    return reserved


def time_feasible(scn: Scenario, task_deadline: float | None, finish: float) -> bool:
    if finish > scn.horizon + EPS:
        return False
    return task_deadline is None or finish <= task_deadline + EPS
