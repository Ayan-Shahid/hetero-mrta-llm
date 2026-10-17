"""Exact MILP for heterogeneous time-extended task allocation (see docs/formal_model.md).

Routing formulation with big-M time propagation (MTZ-style subtour elimination):

  y[r,t]   robot r performs task t                  (only eligible pairs exist)
  x[r,i,j] robot r goes from node i to node j        (i in {S} u T_r, j in T_r u {E})
  z[r]     robot r is kept on standby                (only robots of standby types)
  s[t]     start time of task t;  Cmax  makespan

  min  Cmax + w * total_travel                       (w small: travel only breaks ties)
  s.t. sum_r y[r,t] = 1                              every task done once
       flow conservation through S, each task, E     one route per robot
       s[t] >= tau_r(S,t) x[r,S,t]                   travel from the start
       s[j] >= s[i] + d_i + tau_r(i,j) - M(1-x)      sequencing on a route
       s[b] >= s[a] + d_a           for (a,b) in P   precedence (across robots)
       s[t] + d_t <= min(D_t, H)                     deadlines, horizon
       s[i] + d_i + tau_r(i,S) <= E_r + M(1-x[r,i,E]) endurance incl. return
       sum_{r in type} z[r] >= k,  x[r,S,E] >= z[r]  standby
       Cmax >= s[t] + d_t
       sum_t (d_t + min_in_r(t)) y[r,t] <= Cmax, E_r (valid load cuts)

Solved with HiGHS through PuLP by default; any PuLP MILP solver works.
"""

from __future__ import annotations

import time
import warnings

import pulp

# PuLP 3.x warns about its upcoming 4.0 API on every variable; the pinned 3.x API is what we use.
warnings.filterwarnings("ignore", message="Constructing LpVariable", category=DeprecationWarning)

from ..geometry import TravelTable, eligibility_matrix
from ..schema import Plan, Scenario
from .common import SolveResult

S, E = "__S__", "__E__"


def available_solver(time_limit: float, gap_rel: float, msg: bool, threads: int | None = None):
    """First available MILP backend: HiGHS (pip install highspy), then CBC, then Gurobi."""
    names = set(pulp.listSolvers(onlyAvailable=True))
    if "HiGHS" in names:
        kw = {"msg": msg, "timeLimit": time_limit, "gapRel": gap_rel}
        if threads:
            kw["threads"] = threads
        return pulp.HiGHS(**kw), "HiGHS"
    for name in ("PULP_CBC_CMD", "COIN_CMD", "HiGHS_CMD"):
        if name in names:
            return pulp.getSolver(name, msg=msg, timeLimit=time_limit, gapRel=gap_rel), name
    if "GUROBI" in names:
        return pulp.GUROBI(msg=msg, timeLimit=time_limit, gapRel=gap_rel), "GUROBI"
    raise RuntimeError("No MILP solver found. Install HiGHS with: pip install highspy")


def solve_milp(scn: Scenario, time_limit: float = 60.0, gap_rel: float = 1e-4, msg: bool = False,
               travel_weight: float = 1e-3, threads: int | None = None, **_: object) -> SolveResult:
    t0 = time.perf_counter()
    table = TravelTable(scn)
    tasks = {t.id: t for t in scn.tasks}
    robots = {r.id: r for r in scn.robots}
    H = scn.horizon
    elig = eligibility_matrix(scn)
    succ_of = {(p.before, p.after) for p in scn.precedence}

    def latest_finish(tid: str) -> float:
        d = tasks[tid].deadline
        return H if d is None else min(d, H)

    # Prune pairs that cannot work even alone (travel there, do it, come back).
    T: dict[str, list[str]] = {}
    for rid, robot in robots.items():
        keep = []
        for tid in elig[rid]:
            task = tasks[tid]
            arrive = table.from_start(robot, task)
            fin = arrive + task.duration
            if fin <= latest_finish(tid) + 1e-9 and fin + table.to_start(robot, task) <= robot.endurance + 1e-9:
                keep.append(tid)
        T[rid] = keep
    orphan = sorted(t for t in tasks if not any(t in T[r] for r in robots))
    if orphan:
        return SolveResult(None, "infeasible", time.perf_counter() - t0,
                           {"reason": f"no robot can do {orphan} within capabilities, deadlines and endurance"})

    prob = pulp.LpProblem("hetero_mrta", pulp.LpMinimize)
    tidx = {t: k for k, t in enumerate(tasks)}  # numeric names keep PuLP happy with any id string
    ridx = {r: k for k, r in enumerate(robots)}
    for t in tasks:
        if latest_finish(t) - tasks[t].duration < -1e-9:
            return SolveResult(None, "infeasible", time.perf_counter() - t0,
                               {"reason": f"task {t} is longer than its deadline"})
    s = {t: pulp.LpVariable(f"s_{tidx[t]}", lowBound=0, upBound=latest_finish(t) - tasks[t].duration)
         for t in tasks}
    cmax = pulp.LpVariable("Cmax", lowBound=0, upBound=H)
    y: dict[tuple[str, str], pulp.LpVariable] = {}
    x: dict[tuple[str, str, str], pulp.LpVariable] = {}
    tau: dict[tuple[str, str, str], float] = {}

    for rid, robot in robots.items():
        Tr = T[rid]
        for t in Tr:
            y[rid, t] = pulp.LpVariable(f"y_{ridx[rid]}_{tidx[t]}", cat="Binary")
        for i in [S] + Tr:
            for j in Tr + [E]:
                if i == j:
                    continue
                if i == S and j == E:
                    leg = 0.0
                elif i == S:
                    leg = table.from_start(robot, tasks[j])
                elif j == E:
                    leg = table.to_start(robot, tasks[i])
                else:
                    if (j, i) in succ_of:  # j must finish before i starts: i -> j on one route deadlocks
                        continue
                    leg = table.task_to_task(robot, tasks[i], tasks[j])
                    earliest_i = table.from_start(robot, tasks[i])
                    if earliest_i + tasks[i].duration + leg + tasks[j].duration > latest_finish(j) + 1e-9:
                        continue  # arc can never be on time
                ni = "s" if i == S else tidx[i]
                nj = "e" if j == E else tidx[j]
                x[rid, i, j] = pulp.LpVariable(f"x_{ridx[rid]}_{ni}_{nj}", cat="Binary")
                tau[rid, i, j] = leg

    # Objective
    prob += cmax + travel_weight * pulp.lpSum(tau[k] * v for k, v in x.items())

    # Each task exactly once
    for t in tasks:
        prob += pulp.lpSum(y[r, t] for r in robots if (r, t) in y) == 1, f"assign_{tidx[t]}"

    # Flow conservation
    for rid in robots:
        Tr = T[rid]
        k = ridx[rid]
        prob += pulp.lpSum(x[rid, S, j] for j in Tr + [E] if (rid, S, j) in x) == 1, f"leave_{k}"
        prob += pulp.lpSum(x[rid, i, E] for i in [S] + Tr if (rid, i, E) in x) == 1, f"end_{k}"
        for t in Tr:
            prob += pulp.lpSum(x[rid, i, t] for i in [S] + Tr if (rid, i, t) in x) == y[rid, t], f"in_{k}_{tidx[t]}"
            prob += pulp.lpSum(x[rid, t, j] for j in Tr + [E] if (rid, t, j) in x) == y[rid, t], f"out_{k}_{tidx[t]}"

    # Timing
    for (rid, i, j), var in x.items():
        robot = robots[rid]
        leg = tau[rid, i, j]
        if i == S and j == E:
            continue
        if i == S:
            prob += s[j] >= leg * var
        elif j == E:
            big_m = H + leg
            if big_m > robot.endurance:  # otherwise the constraint can never bind
                prob += s[i] + tasks[i].duration + leg <= robot.endurance + big_m * (1 - var)
        else:
            big_m = latest_finish(i) + leg
            prob += s[j] >= s[i] + tasks[i].duration + leg - big_m * (1 - var)

    for p in scn.precedence:
        prob += s[p.after] >= s[p.before] + tasks[p.before].duration, f"prec_{tidx[p.before]}_{tidx[p.after]}"
    for t, task in tasks.items():
        prob += cmax >= s[t] + task.duration, f"mk_{tidx[t]}"

    # Valid load cuts: a robot's tasks plus the cheapest way into each of them fit before Cmax / endurance
    for rid, robot in robots.items():
        terms = []
        for t in T[rid]:
            ins = [tau[rid, i, t] for i in [S] + T[rid] if (rid, i, t) in x]
            terms.append((tasks[t].duration + min(ins, default=0.0)) * y[rid, t])
        if terms:
            prob += pulp.lpSum(terms) <= cmax, f"load_{ridx[rid]}"
            prob += pulp.lpSum(terms) <= robot.endurance, f"loadE_{ridx[rid]}"

    # Standby
    for k, req in enumerate(scn.standby):
        cands = [rid for rid, r in robots.items() if r.type == req.robot_type]
        z = {rid: pulp.LpVariable(f"z{k}_{ridx[rid]}", cat="Binary") for rid in cands}
        prob += pulp.lpSum(z.values()) >= req.count, f"standby_{k}"
        for rid in cands:
            prob += x[rid, S, E] >= z[rid], f"idle_{k}_{ridx[rid]}"

    build_s = time.perf_counter() - t0
    solver, backend = available_solver(time_limit, gap_rel, msg, threads)
    prob.solve(solver)
    elapsed = time.perf_counter() - t0

    info = {"backend": backend, "build_s": round(build_s, 3), "n_binaries": len(x) + len(y),
            "pulp_status": pulp.LpStatus[prob.status], "sol_status": prob.sol_status}
    bound = _dual_bound(prob)
    if bound is not None:
        info["best_bound"] = bound

    if prob.sol_status in (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible) or (
            prob.status == pulp.LpStatusOptimal and cmax.value() is not None):
        routes: dict[str, list[str]] = {}
        for rid in robots:
            route, node, guard = [], S, 0
            while node != E and guard <= len(tasks) + 1:
                nxt = [j for (r, i, j), v in x.items() if r == rid and i == node and (v.value() or 0) > 0.5]
                if not nxt:
                    break
                node = nxt[0]
                if node != E:
                    route.append(node)
                guard += 1
            if route:
                routes[rid] = route
        standby_ids = [rid for rid in robots if (rid, S, E) in x and (x[rid, S, E].value() or 0) > 0.5
                       and any(robots[rid].type == q.robot_type for q in scn.standby)]
        info["objective_cmax"] = cmax.value()
        proven = prob.sol_status == pulp.LpSolutionOptimal
        status = "optimal" if proven else "feasible"
        if bound is not None and cmax.value():
            info["mip_gap"] = max(0.0, (cmax.value() - bound) / max(cmax.value(), 1e-9))
        plan = Plan(routes=routes, standby=standby_ids, meta={"method": "milp", "status": status})
        return SolveResult(plan, status, elapsed, info)

    status = "infeasible" if prob.status == pulp.LpStatusInfeasible else "no_solution"
    return SolveResult(None, status, elapsed, info)


def _dual_bound(prob: pulp.LpProblem) -> float | None:
    model = getattr(prob, "solverModel", None)
    try:
        info = model.getInfo()  # highspy
        val = float(info.mip_dual_bound)
        return val if abs(val) < 1e20 else None
    except Exception:
        return None
