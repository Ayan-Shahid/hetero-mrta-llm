"""Sequential single-item (SSI) auction with MiniMax bidding.

Market-based allocation (Lagoudakis et al., RSS 2005; Koenig et al., 2006):
tasks are auctioned one at a time.  Every eligible robot bids the finish time
of its own route after inserting the task at its best position (MiniMax rule,
which targets mission time).  The lowest bid wins; ties go to the smaller
increase in total travel.  Candidate insertions that would break a deadline,
the horizon, a robot's endurance, or create a deadlock are not allowed to bid.
"""

from __future__ import annotations

import time

from ..geometry import TravelTable, eligibility_matrix
from ..schedule import DeadlockError, Evaluator
from ..schema import Plan, Scenario
from .common import SolveResult, reserve_standby, time_feasible


def _feasible(scn: Scenario, ev: Evaluator, tl) -> bool:
    for _, tid, _, _, _, fin in tl.visits:
        if not time_feasible(scn, ev.tasks[tid].deadline, fin):
            return False
    return all(tl.robot_return[rid] <= ev.robots[rid].endurance + 1e-6 for rid in tl.robot_return)


def solve_auction(scn: Scenario, **_: object) -> SolveResult:
    t0 = time.perf_counter()
    table = TravelTable(scn)
    ev = Evaluator(scn, table)
    elig = eligibility_matrix(scn)
    reserved = reserve_standby(scn, elig, table)
    robots = [r for r in scn.robots if r.id not in reserved]
    routes: dict[str, list[str]] = {r.id: [] for r in robots}
    unassigned = sorted(t.id for t in scn.tasks)
    forced = 0
    base_travel = 0.0

    while unassigned:
        best = None  # (bid, travel_increase, tid, rid, route)
        fallback = None
        for tid in unassigned:
            for robot in robots:
                if tid not in elig[robot.id]:
                    continue
                route = routes[robot.id]
                for k in range(len(route) + 1):
                    cand = route[:k] + [tid] + route[k:]
                    trial = dict(routes)
                    trial[robot.id] = cand
                    try:
                        tl = ev.timeline(trial)
                    except DeadlockError:
                        continue
                    key = (tl.robot_end[robot.id], tl.total_travel - base_travel, tid, robot.id)
                    if _feasible(scn, ev, tl):
                        if best is None or key < best[0]:
                            best = (key, tid, robot.id, cand, tl.total_travel)
                    elif fallback is None or (tl.makespan, *key) < fallback[0]:
                        fallback = ((tl.makespan, *key), tid, robot.id, cand, tl.total_travel)
        pick = best or fallback
        if pick is None:
            break  # some task has no eligible robot
        if best is None:
            forced += 1
        _, tid, rid, cand, total = pick
        routes[rid] = cand
        base_travel = total
        unassigned.remove(tid)

    plan = Plan(routes={k: v for k, v in routes.items() if v}, standby=reserved, meta={"method": "auction"})
    status = "heuristic" if not unassigned and forced == 0 else "infeasible"
    return SolveResult(plan, status, time.perf_counter() - t0,
                       {"unassigned": unassigned, "forced": forced, "reserved": reserved})
