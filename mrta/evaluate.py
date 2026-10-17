"""Run one method on one scenario and turn the outcome into a results row."""

from __future__ import annotations

import json
import time
from typing import Any

from .checker import check
from .llm import LLM_METHODS
from .llm.client import ChatClient
from .schema import Plan, Scenario
from .solvers import SOLVERS
from .spec_eval import compare

CLASSICAL = tuple(SOLVERS)
LLM = tuple(LLM_METHODS)
ALL_METHODS = CLASSICAL + LLM


def score_plan(scn: Scenario, plan: Plan | None) -> dict[str, Any]:
    if plan is None:
        return {"feasible": False, "n_violations": None, "violations": "{}", "makespan": None, "total_travel": None}
    res = check(scn, plan)
    sched = res.schedule
    finite = sched is not None and sched.makespan != float("inf")
    return {
        "feasible": res.feasible,
        "n_violations": len(res.violations),
        "violations": json.dumps(res.counts(), sort_keys=True),
        "makespan": round(sched.makespan, 2) if finite else None,
        "total_travel": round(sched.total_travel, 2) if finite and sched.total_travel != float("inf") else None,
    }


def _base(scn: Scenario, tier: str) -> dict[str, Any]:
    return {"scenario": scn.id, "tier": tier, "n_robots": len(scn.robots), "n_tasks": len(scn.tasks),
            "n_precedence": len(scn.precedence), "n_deadlines": sum(t.deadline is not None for t in scn.tasks),
            "standby": bool(scn.standby)}


def run_classical(method: str, scn: Scenario, tier: str, time_limit: float = 60.0) -> dict[str, Any]:
    t0 = time.perf_counter()
    res = SOLVERS[method](scn, time_limit=time_limit)
    row = {**_base(scn, tier), "method": method, "input": "spec", "provider": "", "model": "",
           "status": res.status, "plan_time_s": round(time.perf_counter() - t0, 4),
           "solver_time_s": round(res.time_s, 4), "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
           "llm_latency_s": 0.0, "iterations": 0, "error": res.info.get("reason", "")}
    if method == "milp":
        row["mip_gap"] = round(res.info.get("mip_gap", 0.0), 5) if "mip_gap" in res.info else None
    row.update(score_plan(scn, res.plan))
    return row


def run_llm(method: str, client: ChatClient, scn: Scenario, tier: str, style: str, brief: str,
            time_limit: float = 60.0) -> tuple[dict[str, Any], dict[str, Any]]:
    fn = LLM_METHODS[method]
    t0 = time.perf_counter()
    try:
        res = fn(client, scn, brief, style, time_limit=time_limit)
    except Exception as err:  # noqa: BLE001 - one bad run must not kill a sweep
        from .llm.methods import MethodResult

        res = MethodResult(plan=None, status="error", error=f"{type(err).__name__}: {err}"[:300])
    cfg = client.config
    row = {**_base(scn, tier), "method": method, "input": style, "provider": cfg.provider, "model": cfg.model,
           "status": res.status, "plan_time_s": round(time.perf_counter() - t0, 4),
           "solver_time_s": round(res.solver_time_s, 4), "llm_calls": res.llm_calls,
           "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
           "llm_latency_s": round(res.llm_latency_s, 3), "iterations": res.iterations, "error": res.error or ""}
    row.update(score_plan(scn, res.plan))
    if res.spec is not None:
        cmp = compare(res.spec, scn)
        row.update({k: cmp[k] for k in ("spec_f1", "spec_precision", "spec_recall")})
        for key in ("recall_task_req", "recall_task_deadline", "recall_precedence", "recall_standby"):
            row[key] = cmp.get(key)
    trace = {"scenario": scn.id, "method": method, "style": style, "status": res.status,
             "plan": res.plan.model_dump() if res.plan else None, "trace": res.trace}
    return row, trace


FIELDS = ["scenario", "tier", "n_robots", "n_tasks", "n_precedence", "n_deadlines", "standby", "method", "input",
          "provider", "model", "status", "feasible", "n_violations", "violations", "makespan", "total_travel",
          "plan_time_s", "solver_time_s", "llm_calls", "prompt_tokens", "completion_tokens", "llm_latency_s",
          "iterations", "mip_gap", "spec_f1", "spec_precision", "spec_recall", "recall_task_req",
          "recall_task_deadline", "recall_precedence", "recall_standby", "error"]
