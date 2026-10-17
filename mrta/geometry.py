"""Travel model and robot-task eligibility.

* air robots fly straight lines (Euclidean distance) and cannot enter indoor locations;
* ground robots follow the road grid (Manhattan distance) and cannot reach rooftops;
* fixed robots never move and can only work at their own station.
"""

from __future__ import annotations

import math

from .schema import Location, Mobility, Robot, Scenario, Task, Zone

INF = float("inf")


def distance(robot: Robot, a: Location, b: Location) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    if robot.mobility == Mobility.AIR:
        return math.hypot(dx, dy)
    return abs(dx) + abs(dy)


def can_reach(robot: Robot, loc: Location) -> bool:
    if robot.mobility == Mobility.FIXED:
        return loc.name == robot.start
    if robot.mobility == Mobility.AIR:
        return loc.zone != Zone.INDOOR
    return loc.zone != Zone.ROOF  # ground


def travel_time(robot: Robot, a: Location, b: Location) -> float:
    """Seconds for `robot` to go from a to b (inf if impossible)."""
    if a.name == b.name:
        return 0.0
    if robot.mobility == Mobility.FIXED:
        return INF
    if not (can_reach(robot, a) and can_reach(robot, b)):
        return INF
    return distance(robot, a, b) / robot.speed


def ineligibility_reason(scn: Scenario, robot: Robot, task: Task) -> str | None:
    """None if `robot` can do `task`, otherwise the violation kind."""
    missing = set(task.required) - set(robot.capabilities)
    if missing:
        return "capability"
    if task.payload > robot.payload + 1e-9:
        return "payload"
    if not can_reach(robot, scn.loc(task.location)):
        return "unreachable"
    return None


def eligible(scn: Scenario, robot: Robot, task: Task) -> bool:
    return ineligibility_reason(scn, robot, task) is None


def eligibility_matrix(scn: Scenario) -> dict[str, list[str]]:
    """robot id -> ids of tasks it can do (ignoring time and standby)."""
    return {r.id: [t.id for t in scn.tasks if eligible(scn, r, t)] for r in scn.robots}


class TravelTable:
    """Cached travel times between a robot's start and task locations."""

    def __init__(self, scn: Scenario):
        self.scn = scn
        self._locs = {loc.name: loc for loc in scn.locations}
        self._cache: dict[tuple[str, str, str], float] = {}

    def between(self, robot: Robot, a: str, b: str) -> float:
        key = (robot.id, a, b)
        if key not in self._cache:
            self._cache[key] = travel_time(robot, self._locs[a], self._locs[b])
        return self._cache[key]

    def task_to_task(self, robot: Robot, t1: Task, t2: Task) -> float:
        return self.between(robot, t1.location, t2.location)

    def from_start(self, robot: Robot, task: Task) -> float:
        return self.between(robot, robot.start, task.location)

    def to_start(self, robot: Robot, task: Task) -> float:
        return self.between(robot, task.location, robot.start)
