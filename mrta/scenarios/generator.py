"""Seeded generator of heterogeneous task-allocation scenarios.

Every generated scenario is guaranteed feasible: a heuristic baseline must find
a plan that passes the checker, otherwise deadlines are relaxed or the
scenario is redrawn.  So when a method returns an infeasible plan, the fault is
the method's, not the scenario's.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..catalog import (BASE, LOCATION_NAMES, MANIPULATION_KINDS, ROBOT_TYPES, TASK_KINDS, WORKCELLS)
from ..checker import check
from ..geometry import TravelTable, can_reach
from ..schema import Location, Precedence, Robot, Scenario, Standby, Task, Zone


@dataclass(frozen=True)
class Tier:
    name: str
    robots: int
    tasks: int
    p_deadline: float = 0.3
    p_precedence: float = 0.35
    p_standby: float = 0.5


TIERS: dict[str, Tier] = {
    "S": Tier("S", robots=3, tasks=5),
    "M": Tier("M", robots=5, tasks=10),
    "L": Tier("L", robots=8, tasks=16),
}

TYPE_WEIGHTS = {"quadcopter": 0.35, "rover": 0.30, "mobile_manipulator": 0.20, "fixed_arm": 0.15}


def _round(x: float, step: float) -> float:
    return round(x / step) * step


def _ceil(x: float, step: float) -> float:
    return math.ceil(x / step) * step


def _team(rng: random.Random, n: int) -> list[str]:
    types = ["quadcopter", "mobile_manipulator"]
    names, weights = zip(*TYPE_WEIGHTS.items())
    while len(types) < n:
        t = rng.choices(names, weights)[0]
        if t == "fixed_arm" and types.count("fixed_arm") >= len(WORKCELLS):
            continue
        types.append(t)
    rng.shuffle(types)
    return types


def _place(rng: random.Random, names: list[tuple[str, Zone]]) -> list[Location]:
    locs = [Location(name=BASE, x=0, y=0, zone=Zone.OUTDOOR)]
    for name, zone in names:
        for _ in range(200):
            x, y = rng.randint(-150, 150), rng.randint(-150, 150)
            if all(abs(x - o.x) + abs(y - o.y) >= 40 for o in locs):
                break
        locs.append(Location(name=name, x=x, y=y, zone=zone))
    return locs


def _robots(rng: random.Random, types: list[str]) -> tuple[list[Robot], list[str]]:
    robots, workcells, counters = [], [], {}
    for tname in types:
        spec = ROBOT_TYPES[tname]
        counters[tname] = counters.get(tname, 0) + 1
        caps = list(spec.capabilities) + [c for c, p in spec.optional.items() if rng.random() < p]
        if tname == "fixed_arm":
            cell = WORKCELLS[len(workcells)]
            workcells.append(cell)
            robots.append(Robot(id=f"{spec.prefix}{counters[tname]}", type=tname, mobility=spec.mobility,
                                capabilities=caps, speed=0.0, payload=0.0, endurance=86400.0, start=cell))
            continue
        robots.append(Robot(
            id=f"{spec.prefix}{counters[tname]}", type=tname, mobility=spec.mobility, capabilities=caps,
            speed=_round(rng.uniform(*spec.speed), 0.5), payload=float(rng.randint(*spec.payload)),
            endurance=_round(rng.uniform(*spec.endurance), 60), start=BASE))
    return robots, workcells


def generate(tier: str | Tier, seed: int, scenario_id: str | None = None, max_tries: int = 40) -> Scenario:
    tier = TIERS[tier] if isinstance(tier, str) else tier
    for attempt in range(max_tries):
        rng = random.Random(f"{tier.name}-{seed}-{attempt}")
        scn = _draw(rng, tier, scenario_id or f"{tier.name}-{seed:03d}")
        if scn is not None:
            return scn
    raise RuntimeError(f"could not generate a feasible scenario for tier {tier.name}, seed {seed}")


def _draw(rng: random.Random, tier: Tier, sid: str) -> Scenario | None:
    from ..solvers.auction import solve_auction
    from ..solvers.hungarian import solve_hungarian

    types = _team(rng, tier.robots)
    robots, workcells = _robots(rng, types)

    n_locs = max(4, tier.tasks // 2 + 2)
    roof = rng.sample(LOCATION_NAMES[Zone.ROOF], k=min(2, 1 + n_locs // 6))
    indoor = rng.sample(LOCATION_NAMES[Zone.INDOOR], k=min(3, 1 + n_locs // 5))
    outdoor = rng.sample(LOCATION_NAMES[Zone.OUTDOOR], k=max(2, n_locs - len(roof) - len(indoor)))
    named = [(n, Zone.ROOF) for n in roof] + [(n, Zone.INDOOR) for n in indoor + list(workcells)] + \
            [(n, Zone.OUTDOOR) for n in outdoor]
    locations = _place(rng, named)
    loc_by_name = {loc.name: loc for loc in locations}

    def doable(kind, loc) -> bool:
        k = TASK_KINDS[kind]
        if loc.zone not in k.zones or loc.name == BASE:
            return False
        return any(set(k.required) <= set(r.capabilities) and can_reach(r, loc)
                   and (k.payload[0] <= r.payload or not k.payload[1]) for r in robots)

    tasks: list[dict] = []
    pairs: list[tuple[int, int]] = []
    kinds, weights = zip(*((k, v.weight) for k, v in TASK_KINDS.items()))
    guard = 0
    while len(tasks) < tier.tasks and guard < 500:
        guard += 1
        kind = rng.choices(kinds, weights)[0]
        options = [loc for loc in locations if doable(kind, loc)]
        if kind in MANIPULATION_KINDS and workcells and rng.random() < 0.5:
            options = [loc_by_name[c] for c in workcells if doable(kind, loc_by_name[c])] or options
        if not options:
            continue
        loc = rng.choice(options)
        k = TASK_KINDS[kind]
        task = {"kind": kind, "loc": loc.name, "duration": _round(rng.uniform(*k.duration), 30),
                "payload": 0, "item": None}
        if k.payload[1]:
            heavy_ok = [r for r in robots if "carry" in r.capabilities and can_reach(r, loc)]
            cap = max((r.payload for r in heavy_ok), default=0)
            if cap <= 0:
                continue
            task["payload"] = min(rng.randint(*k.payload), int(cap))
            task["item"] = rng.choice(k.items) if k.items else None
        tasks.append(task)
        # deliver-then-manipulate pairs at the same place create precedence
        if (kind in MANIPULATION_KINDS and len(tasks) < tier.tasks and rng.random() < tier.p_precedence
                and doable("deliver", loc)):
            dk = TASK_KINDS["deliver"]
            carriers = [r for r in robots if "carry" in r.capabilities and can_reach(r, loc)]
            cap = max(r.payload for r in carriers)
            tasks.append({"kind": "deliver", "loc": loc.name, "duration": _round(rng.uniform(*dk.duration), 30),
                          "payload": min(rng.randint(*dk.payload), int(cap)), "item": rng.choice(dk.items)})
            pairs.append((len(tasks) - 1, len(tasks) - 2))  # deliver before manipulation
    if len(tasks) < tier.tasks:
        return None

    order = list(range(len(tasks)))
    rng.shuffle(order)
    ids = {old: f"T{new + 1}" for new, old in enumerate(order)}
    task_objs: list[Task] = []
    for old in sorted(order, key=lambda o: ids[o]):
        t = tasks[old]
        k = TASK_KINDS[t["kind"]]
        desc = k.explicit.format(loc=t["loc"], item=t["item"], w=f"{t['payload']:g}")
        task_objs.append(Task(id=ids[old], kind=t["kind"], description=desc, location=t["loc"],
                              required=list(k.required), payload=float(t["payload"]),
                              duration=float(t["duration"])))
    task_objs.sort(key=lambda t: int(t.id[1:]))
    precedence = [Precedence(before=ids[a], after=ids[b]) for a, b in pairs]

    standby: list[Standby] = []
    if types.count("quadcopter") >= 2 and rng.random() < tier.p_standby:
        standby.append(Standby(robot_type="quadcopter", count=1))

    scn = Scenario(id=sid, horizon=86400.0, locations=locations, robots=robots, tasks=task_objs,
                   precedence=precedence, standby=standby)

    # Horizon and deadlines from what a heuristic can achieve, then verify feasibility.
    base = solve_auction(scn)
    if base.plan is None or not check(scn, base.plan).feasible:
        return None
    sched = check(scn, base.plan).schedule
    finish = {v.task: v.finish for v in sched.visits}
    horizon = _ceil(max(sched.makespan * 1.6, sched.makespan + 300), 300)
    table = TravelTable(scn)
    for t in task_objs:
        if rng.random() < tier.p_deadline:
            lb = min(table.from_start(r, t) + t.duration for r in robots
                     if set(t.required) <= set(r.capabilities) and t.payload <= r.payload
                     and can_reach(r, scn.loc(t.location)))
            t.deadline = float(_ceil(max(lb * rng.uniform(1.6, 2.6), finish[t.id] * rng.uniform(1.0, 1.3)), 60))
            t.deadline = min(t.deadline, horizon)
    scn = scn.model_copy(update={"horizon": float(horizon), "tasks": task_objs})
    scn = Scenario.model_validate(scn.model_dump())
    # Deadlines were set no earlier than the auction plan's finish times, so that plan
    # remains a feasibility witness; fall back to re-solving just in case.
    if check(scn, base.plan).feasible:
        return scn
    for solver in (solve_auction, solve_hungarian):
        res = solver(scn)
        if res.plan is not None and check(scn, res.plan).feasible:
            return scn
    return None


def make_benchmark(tiers: dict[str, int], start_seed: int = 0) -> list[Scenario]:
    """tiers: {"S": 10, "M": 10, ...} -> scenarios with ids like S-000."""
    out = []
    for name, count in tiers.items():
        for seed in range(start_seed, start_seed + count):
            out.append(generate(name, seed))
    return out
