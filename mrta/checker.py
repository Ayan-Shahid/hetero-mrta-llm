"""Feasibility checker: the single source of truth for "is this plan correct?".

A plan is feasible iff it satisfies every hard constraint of the formal model
(see docs/formal_model.md):

  C1 coverage      every task is assigned exactly once, only known ids are used
  C2 capability    the robot has every capability the task requires
  C3 payload       the task's payload does not exceed the robot's payload limit
  C4 reachability  the robot can physically reach the task location
  C5 standby       at least `count` robots of each standby type get no tasks,
                   and robots listed as standby get no tasks
  C6 precedence    route order + precedence admit a schedule (no deadlock);
                   predecessors finish before successors start (enforced by waiting)
  C7 deadlines     each task finishes by its deadline and by the mission horizon
  C8 endurance     each robot is back at its start within its endurance
"""

from __future__ import annotations

from .geometry import TravelTable, ineligibility_reason
from .schedule import DeadlockError, compute_schedule
from .schema import CheckResult, Plan, Scenario, Violation

EPS = 1e-4  # seconds; absorbs floating-point noise from solvers


def check(scn: Scenario, plan: Plan, table: TravelTable | None = None) -> CheckResult:
    table = table or TravelTable(scn)
    robots = {r.id: r for r in scn.robots}
    tasks = {t.id: t for t in scn.tasks}
    out: list[Violation] = []

    # C1 coverage and ids
    seen: dict[str, int] = {}
    for rid, tids in plan.routes.items():
        if rid not in robots:
            out.append(Violation(kind="unknown_robot", robot=rid, detail=f"robot '{rid}' does not exist"))
        for tid in tids:
            if tid not in tasks:
                out.append(Violation(kind="unknown_task", robot=rid, task=tid, detail=f"task '{tid}' does not exist"))
                continue
            seen[tid] = seen.get(tid, 0) + 1
    for tid, n in seen.items():
        if n > 1:
            out.append(Violation(kind="duplicate", task=tid, detail=f"task {tid} is assigned {n} times"))
    for tid in tasks:
        if tid not in seen:
            out.append(Violation(kind="missing", task=tid, detail=f"task {tid} is not assigned to any robot"))

    # C2-C4 robot/task compatibility
    for rid, tids in plan.routes.items():
        if rid not in robots:
            continue
        robot = robots[rid]
        for tid in tids:
            if tid not in tasks:
                continue
            task = tasks[tid]
            reason = ineligibility_reason(scn, robot, task)
            if reason == "capability":
                need = sorted(set(task.required) - set(robot.capabilities))
                out.append(Violation(kind="capability", robot=rid, task=tid,
                                     detail=f"{rid} ({robot.type}) lacks {need} needed by {tid}"))
            elif reason == "payload":
                out.append(Violation(kind="payload", robot=rid, task=tid,
                                     detail=f"{tid} needs {task.payload:g} kg but {rid} carries at most {robot.payload:g} kg"))
            elif reason == "unreachable":
                loc = scn.loc(task.location)
                out.append(Violation(kind="unreachable", robot=rid, task=tid,
                                     detail=f"{rid} ({robot.mobility.value}) cannot reach {loc.name} ({loc.zone.value})"))

    # C5 standby
    busy = {rid for rid, tids in plan.routes.items() if tids}
    for rid in plan.standby:
        if rid not in robots:
            out.append(Violation(kind="unknown_robot", robot=rid, detail=f"standby robot '{rid}' does not exist"))
        elif rid in busy:
            out.append(Violation(kind="standby_conflict", robot=rid, detail=f"{rid} is listed as standby but has tasks"))
    for req in scn.standby:
        idle = [r.id for r in scn.robots if r.type == req.robot_type and r.id not in busy]
        if len(idle) < req.count:
            out.append(Violation(kind="standby",
                                 detail=f"need {req.count} idle {req.robot_type}(s) on standby, only {len(idle)} left free"))

    # C6-C8 timing
    schedule = None
    try:
        schedule = compute_schedule(scn, plan, table)
    except DeadlockError as err:
        out.append(Violation(kind="deadlock", detail=str(err)))
    if schedule is not None:
        for v in schedule.visits:
            task = tasks[v.task]
            if v.finish == float("inf"):
                continue  # already reported as unreachable
            if task.deadline is not None and v.finish > task.deadline + EPS:
                out.append(Violation(kind="deadline", robot=v.robot, task=v.task,
                                     detail=f"{v.task} finishes at {v.finish:.1f} s, deadline {task.deadline:g} s"))
            if v.finish > scn.horizon + EPS:
                out.append(Violation(kind="horizon", robot=v.robot, task=v.task,
                                     detail=f"{v.task} finishes at {v.finish:.1f} s, after the {scn.horizon:g} s horizon"))
        for rid, back in schedule.robot_return.items():
            robot = robots[rid]
            if back != float("inf") and back > robot.endurance + EPS:
                out.append(Violation(kind="endurance", robot=rid,
                                     detail=f"{rid} is back at {back:.1f} s, endurance is {robot.endurance:g} s"))

    return CheckResult(feasible=not out, violations=out, schedule=schedule)
