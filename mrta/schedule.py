"""Turn routes into a timed schedule (one implementation used everywhere).

Each robot leaves its start at t = 0 and visits its tasks in route order.
A task starts once the robot has arrived *and* every precedence predecessor
(possibly served by another robot) has finished, so robots wait when needed.
If route orders and precedence constraints form a cycle, no schedule exists
(deadlock).  Travel time to an unreachable location is infinite.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .geometry import TravelTable
from .schema import Plan, Scenario, Schedule, Visit


class DeadlockError(Exception):
    def __init__(self, tasks: list[str]):
        self.tasks = tasks
        super().__init__(f"route order and precedence form a cycle involving {tasks}")


@dataclass
class Timeline:
    visits: list[tuple[str, str, float, float, float, float]]  # robot, task, travel, arrive, start, finish
    finish: dict[str, float]  # task -> finish (first occurrence)
    robot_end: dict[str, float]  # robot -> finish of its last task (0 if idle)
    robot_return: dict[str, float]  # robot -> time back at start (0 if idle)
    total_travel: float
    makespan: float


class Evaluator:
    """Fast, allocation-free timing of candidate routes (used by solvers and the checker)."""

    def __init__(self, scn: Scenario, table: TravelTable | None = None):
        self.scn = scn
        self.table = table or TravelTable(scn)
        self.robots = {r.id: r for r in scn.robots}
        self.tasks = {t.id: t for t in scn.tasks}
        self.preds = scn.predecessors()

    def timeline(self, routes: dict[str, list[str]]) -> Timeline:
        robots, tasks, table = self.robots, self.tasks, self.table
        nodes: list[tuple[str, str]] = []
        route_prev: list[int | None] = []
        first: dict[str, int] = {}
        for rid, tids in routes.items():
            if rid not in robots:
                continue
            prev: int | None = None
            for tid in tids:
                if tid not in tasks:
                    continue
                idx = len(nodes)
                nodes.append((rid, tid))
                route_prev.append(prev)
                first.setdefault(tid, idx)
                prev = idx

        n = len(nodes)
        deps: list[list[int]] = [[] for _ in range(n)]
        for i, (_, tid) in enumerate(nodes):
            if route_prev[i] is not None:
                deps[i].append(route_prev[i])
            for p in self.preds.get(tid, ()):
                j = first.get(p)
                if j is not None and j != i:
                    deps[i].append(j)
        children: list[list[int]] = [[] for _ in range(n)]
        indeg = [len(d) for d in deps]
        for i, d in enumerate(deps):
            for j in d:
                children[j].append(i)

        fin: list[float | None] = [None] * n
        rows: list[tuple[str, str, float, float, float, float] | None] = [None] * n
        queue = deque(i for i in range(n) if indeg[i] == 0)
        done = 0
        while queue:
            i = queue.popleft()
            rid, tid = nodes[i]
            robot, task = robots[rid], tasks[tid]
            prev = route_prev[i]
            if prev is None:
                leg, depart = table.from_start(robot, task), 0.0
            else:
                leg, depart = table.task_to_task(robot, tasks[nodes[prev][1]], task), fin[prev]
            arrive = depart + leg
            ready = 0.0
            for j in deps[i]:
                if j != prev and fin[j] > ready:
                    ready = fin[j]
            start = arrive if arrive > ready else ready
            fin[i] = start + task.duration
            rows[i] = (rid, tid, leg, arrive, start, fin[i])
            done += 1
            for c in children[i]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    queue.append(c)
        if done < n:
            raise DeadlockError(sorted({nodes[i][1] for i in range(n) if fin[i] is None}))

        robot_end = {rid: 0.0 for rid in robots}
        robot_return = {rid: 0.0 for rid in robots}
        last_task: dict[str, str] = {}
        total = 0.0
        for row in rows:
            rid, tid, leg, _, _, f = row
            robot_end[rid] = f  # rows of a robot appear in route order
            last_task[rid] = tid
            total += leg
        for rid, tid in last_task.items():
            back = table.to_start(robots[rid], tasks[tid])
            robot_return[rid] = robot_end[rid] + back
            total += back
        finish = {}
        for i, (_, tid) in enumerate(nodes):
            finish.setdefault(tid, fin[i])
        makespan = max((r[5] for r in rows), default=0.0)
        return Timeline(rows, finish, robot_end, robot_return, total, makespan)


def compute_schedule(scn: Scenario, plan: Plan, table: TravelTable | None = None) -> Schedule:
    tl = Evaluator(scn, table).timeline(plan.routes)
    visits = [Visit.model_construct(robot=r, task=t, travel=leg, arrive=a, start=s, finish=f)
              for r, t, leg, a, s, f in tl.visits]
    return Schedule.model_construct(visits=visits, makespan=tl.makespan, total_travel=tl.total_travel,
                                    robot_return=tl.robot_return)
