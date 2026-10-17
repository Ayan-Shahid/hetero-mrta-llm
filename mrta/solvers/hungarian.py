"""Iterated Hungarian assignment (classical ST-SR-IA baseline, applied in rounds).

The Hungarian algorithm solves one-task-per-robot assignment optimally.  For
more tasks than robots we apply it round by round: in each round every free
robot may take at most one *ready* task (all predecessors already allocated),
with cost = the time that robot would finish the task when appended to its
route.  Pairs that would break a capability, deadline, horizon or endurance
constraint get a prohibitive cost.  This is the standard way to use
instantaneous assignment for a time-extended problem; it is myopic, so it is
not optimal.
"""

from __future__ import annotations

import time

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..geometry import TravelTable, eligibility_matrix
from ..schema import Plan, Scenario
from .common import SolveResult, reserve_standby, time_feasible

BIG = 1e12


def solve_hungarian(scn: Scenario, **_: object) -> SolveResult:
    t0 = time.perf_counter()
    table = TravelTable(scn)
    elig = eligibility_matrix(scn)
    reserved = reserve_standby(scn, elig, table)
    robots = [r for r in scn.robots if r.id not in reserved]
    tasks = {t.id: t for t in scn.tasks}
    preds = scn.predecessors()

    last: dict[str, str | None] = {r.id: None for r in robots}
    avail = {r.id: 0.0 for r in robots}
    finish: dict[str, float] = {}
    routes: dict[str, list[str]] = {r.id: [] for r in robots}
    unassigned = set(tasks)
    forced = 0

    def projected(robot, tid: str) -> tuple[float, float]:
        task = tasks[tid]
        prev = last[robot.id]
        leg = table.from_start(robot, task) if prev is None else table.task_to_task(robot, tasks[prev], task)
        ready = max((finish[p] for p in preds[tid]), default=0.0)
        start = max(avail[robot.id] + leg, ready)
        fin = start + task.duration
        return fin, fin + table.to_start(robot, task)

    while unassigned:
        ready = sorted(t for t in unassigned if all(p in finish for p in preds[t]))
        if not ready:
            break  # remaining tasks wait on predecessors nobody can do
        cost = np.full((len(robots), len(ready)), BIG)
        for i, robot in enumerate(robots):
            for j, tid in enumerate(ready):
                if tid not in elig[robot.id]:
                    continue
                fin, back = projected(robot, tid)
                if time_feasible(scn, tasks[tid].deadline, fin) and back <= robot.endurance:
                    cost[i, j] = fin
        rows, cols = linear_sum_assignment(cost)
        chosen = [(i, j) for i, j in zip(rows, cols) if cost[i, j] < BIG]
        if not chosen:
            # No time-feasible pair left: append the earliest-finishing eligible pair anyway
            # (the checker will report the violation) so the plan stays complete.
            best = None
            for i, robot in enumerate(robots):
                for j, tid in enumerate(ready):
                    if tid in elig[robot.id]:
                        fin, _ = projected(robot, tid)
                        if best is None or fin < best[0]:
                            best = (fin, i, j)
            if best is None:
                break
            chosen = [(best[1], best[2])]
            forced += 1
        for i, j in chosen:
            robot, tid = robots[i], ready[j]
            fin, _ = projected(robot, tid)
            routes[robot.id].append(tid)
            last[robot.id] = tid
            avail[robot.id] = fin
            finish[tid] = fin
            unassigned.discard(tid)

    plan = Plan(routes={k: v for k, v in routes.items() if v}, standby=reserved, meta={"method": "hungarian"})
    status = "heuristic" if not unassigned and forced == 0 else "infeasible"
    return SolveResult(plan, status, time.perf_counter() - t0,
                       {"unassigned": sorted(unassigned), "forced": forced, "reserved": reserved})
