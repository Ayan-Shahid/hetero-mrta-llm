"""Structured contracts shared by every component.

Every solver (MILP, Hungarian, auction) and every LLM method reads a `Scenario`
and returns a `Plan`.  The checker validates any `Plan` against the ground-truth
`Scenario`, so a plan written by an LLM and one computed by the MILP are judged
by exactly the same rules.

Units: metres, seconds, kilograms.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Closed capability vocabulary. Keeping it closed lets us validate LLM output.
Capability = Literal["camera", "thermal", "carry", "manipulate"]
CAPABILITIES: tuple[str, ...] = ("camera", "thermal", "carry", "manipulate")


class Mobility(str, Enum):
    AIR = "air"  # straight-line flight; outdoor and rooftop locations only
    GROUND = "ground"  # road grid (|dx|+|dy|); outdoor and indoor locations only
    FIXED = "fixed"  # cannot move; works only at its own station


class Zone(str, Enum):
    OUTDOOR = "outdoor"
    ROOF = "roof"
    INDOOR = "indoor"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Location(_Model):
    name: str
    x: float
    y: float
    zone: Zone = Zone.OUTDOOR


class Robot(_Model):
    id: str
    type: str = Field(description="quadcopter | rover | mobile_manipulator | fixed_arm")
    mobility: Mobility
    capabilities: list[Capability]
    speed: float = Field(ge=0, description="m/s; 0 for fixed robots")
    payload: float = Field(ge=0, description="max carried mass in kg")
    endurance: float = Field(gt=0, description="s of operation, including the return to start")
    start: str = Field(description="name of the start location")


class Task(_Model):
    id: str
    kind: str = Field(description="template name, e.g. inspect_roof, deliver")
    description: str = ""
    location: str
    required: list[Capability] = Field(default_factory=list)
    payload: float = Field(default=0.0, ge=0)
    duration: float = Field(gt=0, description="service time on site, s")
    deadline: float | None = Field(default=None, description="latest finish time, s")


class Precedence(_Model):
    before: str
    after: str


class Standby(_Model):
    robot_type: str
    count: int = Field(ge=1)


class Event(_Model):
    """Dynamic change during execution (used from Phase 2 on)."""

    time: float = Field(ge=0)
    kind: Literal["robot_failure", "new_task", "deadline_change"]
    robot: str | None = None
    task: Task | None = None
    task_id: str | None = None
    new_deadline: float | None = None


class Scenario(_Model):
    id: str
    horizon: float = Field(gt=0, description="every task must finish by this time, s")
    locations: list[Location]
    robots: list[Robot]
    tasks: list[Task]
    precedence: list[Precedence] = Field(default_factory=list)
    standby: list[Standby] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    objective: Literal["makespan"] = "makespan"

    @model_validator(mode="after")
    def _check_references(self) -> "Scenario":
        problems: list[str] = []
        loc_names = [loc.name for loc in self.locations]
        robot_ids = [r.id for r in self.robots]
        task_ids = [t.id for t in self.tasks]
        for label, ids in (("location", loc_names), ("robot", robot_ids), ("task", task_ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                problems.append(f"duplicate {label} ids: {dupes}")
        locs = set(loc_names)
        for r in self.robots:
            if r.start not in locs:
                problems.append(f"robot {r.id} starts at unknown location '{r.start}'")
            if r.mobility != Mobility.FIXED and r.speed <= 0:
                problems.append(f"robot {r.id} is mobile but has speed {r.speed}")
        for t in self.tasks:
            if t.location not in locs:
                problems.append(f"task {t.id} is at unknown location '{t.location}'")
        tids = set(task_ids)
        for p in self.precedence:
            if p.before not in tids or p.after not in tids:
                problems.append(f"precedence {p.before}->{p.after} references an unknown task")
            if p.before == p.after:
                problems.append(f"precedence {p.before}->{p.after} is a self-loop")
        types = {r.type for r in self.robots}
        for s in self.standby:
            if s.robot_type not in types:
                problems.append(f"standby asks for robot type '{s.robot_type}', which is not in the team")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    # Convenience lookups ---------------------------------------------------
    def loc(self, name: str) -> Location:
        return self._index("locations", "name")[name]

    def robot(self, rid: str) -> Robot:
        return self._index("robots", "id")[rid]

    def task(self, tid: str) -> Task:
        return self._index("tasks", "id")[tid]

    def _index(self, attr: str, key: str) -> dict[str, Any]:
        return {getattr(item, key): item for item in getattr(self, attr)}

    def predecessors(self) -> dict[str, list[str]]:
        preds: dict[str, list[str]] = {t.id: [] for t in self.tasks}
        for p in self.precedence:
            preds.setdefault(p.after, []).append(p.before)
        return preds


class Plan(_Model):
    """A complete allocation: an ordered task list per robot.

    Robots that are not in `routes` (or have an empty list) stay idle.
    Robots in `standby` are deliberately kept free and must have no tasks.
    """

    routes: dict[str, list[str]] = Field(default_factory=dict)
    standby: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def assignment(self) -> dict[str, str]:
        """task id -> robot id (first occurrence wins)."""
        out: dict[str, str] = {}
        for rid, tids in self.routes.items():
            for tid in tids:
                out.setdefault(tid, rid)
        return out


ViolationKind = Literal[
    "unknown_robot",
    "unknown_task",
    "duplicate",
    "missing",
    "capability",
    "payload",
    "unreachable",
    "deadline",
    "horizon",
    "endurance",
    "standby",
    "standby_conflict",
    "deadlock",
]


class Violation(_Model):
    kind: ViolationKind
    detail: str
    robot: str | None = None
    task: str | None = None


class Visit(_Model):
    robot: str
    task: str
    travel: float
    arrive: float
    start: float
    finish: float


class Schedule(_Model):
    visits: list[Visit]
    makespan: float
    total_travel: float = Field(description="sum of all travel legs, including each robot's return to start")
    robot_return: dict[str, float] = Field(description="time each robot is back at its start")


class CheckResult(_Model):
    feasible: bool
    violations: list[Violation]
    schedule: Schedule | None = None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[v.kind] = out.get(v.kind, 0) + 1
        return out
