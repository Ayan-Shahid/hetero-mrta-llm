"""Gap 2 component: formal verification of the extracted model and of the plan.

Gap 2 is that LLM planners check whether single actions are executable (COHERENT's
feedback) or check nothing at all (one-shot model extraction), so a misread deadline or
a missing precedence edge goes straight into the plan. This component adds three checks:

1. Model sanity: every task has at least one robot that could do it; deadlines are not
   shorter than durations; ids are unique and referenced consistently (schema validation).
2. Cross-check: an independent second reading of the brief (a different prompt and
   procedure) is compared fact by fact with the agent's model; every disagreement is
   reported back and the agent must re-read the brief before the model is accepted.
3. Plan gate: ``finish`` refuses a plan that violates the agent's own model (once), and
   ``check`` lists the violated constraints of any plan.

Switches: AgentConfig.g2_check (plan gate and check tool), AgentConfig.g2_crosscheck.
"""

from __future__ import annotations

from typing import Any

from ..checker import check
from ..geometry import eligible
from ..schema import Plan, Scenario


def sanity_problems(spec: Scenario) -> list[str]:
    problems = []
    n_type: dict[str, int] = {}
    for r in spec.robots:
        n_type[r.type] = n_type.get(r.type, 0) + 1
    blocked = set()
    for s in spec.standby:
        have = n_type.get(s.robot_type, 0)
        if s.count > have:
            problems.append(f"standby asks for {s.count} {s.robot_type} but your model has {have}")
        if s.count >= have:
            blocked.add(s.robot_type)
    for t in spec.tasks:
        able = [r for r in spec.robots if eligible(spec, r, t)]
        if able and all(r.type in blocked for r in able):
            problems.append(f"the standby rule keeps every robot that can do {t.id} free "
                            f"({', '.join(r.id for r in able)}); check the standby rule against the brief")
        if not able:
            problems.append(f"no robot in your model can do {t.id} (needs {t.required}, payload {t.payload:g} kg, "
                            f"at {t.location})")
        if t.deadline is not None and t.deadline < t.duration:
            problems.append(f"{t.id} has a deadline ({t.deadline:g} s) shorter than its duration ({t.duration:g} s)")
        if t.deadline is not None and t.deadline > spec.horizon:
            problems.append(f"{t.id} has a deadline after the horizon; check both numbers")
    return problems


def _num(x: float | None) -> str:
    return "none" if x is None else f"{x:g}"


def _close(a: float | None, b: float | None, tol: float = 0.5) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def diff_models(mine: Scenario, other: Scenario, limit: int = 25) -> list[dict[str, Any]]:
    """Facts on which two readings of the same brief disagree, phrased for the agent."""
    out: list[dict[str, Any]] = []
    o_tasks = {t.id.lower(): t for t in other.tasks}
    m_ids = {t.id.lower() for t in mine.tasks}
    for t in mine.tasks:
        ot = o_tasks.get(t.id.lower())
        if ot is None:
            out.append({"item": f"task:{t.id}", "issue": "the second reading has no such task"})
            continue
        if sorted(t.required) != sorted(ot.required):
            out.append({"item": f"task:{t.id}", "field": "required", "yours": sorted(t.required),
                        "second_reading": sorted(ot.required)})
        for fld in ("payload", "duration", "deadline"):
            a, b = getattr(t, fld), getattr(ot, fld)
            if not _close(a, b):
                out.append({"item": f"task:{t.id}", "field": fld, "yours": _num(a), "second_reading": _num(b)})
        if t.location.strip().lower() != ot.location.strip().lower():
            out.append({"item": f"task:{t.id}", "field": "location", "yours": t.location,
                        "second_reading": ot.location})
    for tid, ot in o_tasks.items():
        if tid not in m_ids:
            out.append({"item": f"task:{ot.id}", "issue": "missing from your model but found by the second reading"})
    o_robots = {r.id.lower(): r for r in other.robots}
    for r in mine.robots:
        orb = o_robots.get(r.id.lower())
        if orb is None:
            out.append({"item": f"robot:{r.id}", "issue": "the second reading has no such robot"})
            continue
        if sorted(r.capabilities) != sorted(orb.capabilities):
            out.append({"item": f"robot:{r.id}", "field": "capabilities", "yours": sorted(r.capabilities),
                        "second_reading": sorted(orb.capabilities)})
        if r.mobility != orb.mobility:
            out.append({"item": f"robot:{r.id}", "field": "mobility", "yours": r.mobility.value,
                        "second_reading": orb.mobility.value})
        for fld in ("speed", "payload", "endurance"):
            a, b = getattr(r, fld), getattr(orb, fld)
            if not _close(min(a, 86400), min(b, 86400)):
                out.append({"item": f"robot:{r.id}", "field": fld, "yours": _num(a), "second_reading": _num(b)})
    mp = {(p.before.lower(), p.after.lower()) for p in mine.precedence}
    op = {(p.before.lower(), p.after.lower()) for p in other.precedence}
    for a, b in sorted(op - mp):
        out.append({"item": "precedence", "issue": f"second reading has {a.upper()} before {b.upper()}; you do not"})
    for a, b in sorted(mp - op):
        out.append({"item": "precedence", "issue": f"you have {a.upper()} before {b.upper()}; second reading does not"})
    ms = {(s.robot_type, s.count) for s in mine.standby}
    os_ = {(s.robot_type, s.count) for s in other.standby}
    if ms != os_:
        out.append({"item": "standby", "yours": sorted(ms), "second_reading": sorted(os_)})
    if not _close(mine.horizon, other.horizon, tol=1.0):
        out.append({"item": "model", "field": "horizon", "yours": _num(mine.horizon),
                    "second_reading": _num(other.horizon)})
    return out[:limit]


def plan_violations(spec: Scenario, plan: Plan) -> tuple[bool, list[str], float | None]:
    res = check(spec, plan)
    ms = res.schedule.makespan if res.schedule and res.schedule.makespan != float("inf") else None
    return res.feasible, [v.detail for v in res.violations][:20], ms
