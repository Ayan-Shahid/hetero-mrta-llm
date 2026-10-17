"""How faithfully did an LLM turn the brief into the formal model?

A scenario is flattened into a set of atomic facts (one per capability, payload,
deadline, precedence edge, ...).  Precision/recall/F1 over facts measure model
extraction quality; per-category recall shows *what* gets lost (for example,
deadlines or precedence in informal briefs).
"""

from __future__ import annotations

from collections import defaultdict

from .schema import Scenario


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def facts(scn: Scenario) -> set[tuple]:
    out: set[tuple] = set()
    for r in scn.robots:
        out.add(("robot_mobility", r.id, r.mobility.value))
        out.add(("robot_speed", r.id, _r(r.speed)))
        out.add(("robot_payload", r.id, _r(r.payload) if "carry" in r.capabilities else 0.0))
        out.add(("robot_endurance", r.id, _r(min(r.endurance, 86400))))
        out.add(("robot_start", r.id, r.start))
        for c in r.capabilities:
            out.add(("robot_cap", r.id, c))
    for t in scn.tasks:
        out.add(("task_location", t.id, t.location))
        out.add(("task_duration", t.id, _r(t.duration)))
        out.add(("task_payload", t.id, _r(t.payload)))
        out.add(("task_deadline", t.id, _r(t.deadline)))
        for c in t.required:
            out.add(("task_req", t.id, c))
    for p in scn.precedence:
        out.add(("precedence", p.before, p.after))
    for s in scn.standby:
        out.add(("standby", s.robot_type, s.count))
    out.add(("horizon", "", _r(scn.horizon)))
    return out


def compare(pred: Scenario, truth: Scenario) -> dict[str, float]:
    fp, ft = facts(pred), facts(truth)
    hit = fp & ft
    precision = len(hit) / len(fp) if fp else 0.0
    recall = len(hit) / len(ft) if ft else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    out = {"spec_precision": round(precision, 4), "spec_recall": round(recall, 4), "spec_f1": round(f1, 4)}
    by_cat_true, by_cat_hit = defaultdict(int), defaultdict(int)
    for f in ft:
        by_cat_true[f[0]] += 1
        if f in fp:
            by_cat_hit[f[0]] += 1
    for cat, n in by_cat_true.items():
        out[f"recall_{cat}"] = round(by_cat_hit[cat] / n, 4)
    # constraints that matter most for feasibility, reported explicitly
    extra_prec = {f for f in fp - ft if f[0] == "precedence"}
    out["invented_precedence"] = len(extra_prec)
    return out
