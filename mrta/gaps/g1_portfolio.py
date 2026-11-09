"""Gap 1 component: solver-backed allocation with a verified solver portfolio.

Gap 1 is that LLM allocators (COHERENT and others) are never compared with optimal or
classical allocation, and write plans themselves. With this component on, the agent
does not write routes: it hands its formal model to a portfolio that runs the fast
heuristics first, then the exact MILP with a time limit scaled to the instance size,
verifies every candidate against the model, and returns the best feasible one.

Switch: AgentConfig.g1_solvers (off = the agent must write routes itself; the
``solve`` tool disappears). AgentConfig.g1_portfolio (off = ``solve`` runs one solver
the agent names, as in Phase 1).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..checker import check
from ..schema import Plan, Scenario
from ..solvers import SOLVERS


def milp_budget(scn: Scenario, cap: float = 60.0) -> float:
    """MILP time limit from the instance size: about 1 s at 5 tasks, 30 s at 16 tasks, capped."""
    n = len(scn.tasks) * max(1, len(scn.robots))
    return float(min(cap, max(2.0, 0.25 * n)))


@dataclass
class PortfolioResult:
    plan: Plan | None
    method: str
    status: str
    makespan: float | None
    time_s: float
    candidates: list[dict[str, Any]] = field(default_factory=list)


def solve_portfolio(scn: Scenario, time_limit: float | None = None, order: tuple[str, ...] = ("auction",
                    "hungarian", "milp")) -> PortfolioResult:
    t0 = time.perf_counter()
    best: tuple[float, str, Plan, str] | None = None
    cands = []
    for name in order:
        limit = time_limit if (time_limit and name == "milp") else milp_budget(scn)
        res = SOLVERS[name](scn, time_limit=limit)
        entry: dict[str, Any] = {"method": name, "status": res.status, "time_s": round(res.time_s, 3)}
        if res.plan is not None:
            chk = check(scn, res.plan)
            entry["feasible"] = chk.feasible
            if chk.feasible and chk.schedule is not None:
                entry["makespan_s"] = round(chk.schedule.makespan, 1)
                if best is None or chk.schedule.makespan < best[0] - 1e-6:
                    best = (chk.schedule.makespan, name, res.plan, res.status)
            else:
                entry["violations"] = [v.detail for v in chk.violations][:5]
        cands.append(entry)
        if name == "milp" and res.status == "optimal":
            break
    elapsed = time.perf_counter() - t0
    if best is None:
        return PortfolioResult(None, "none", "infeasible", None, elapsed, cands)
    return PortfolioResult(best[2], best[1], best[3], best[0], elapsed, cands)
