"""Proposed system, Phase 1 skeleton: a tool-using planning agent.

The agent never sees the ground truth.  It builds its own formal model of the
brief (submit_spec), solves it with a solver it chooses (solve), verifies the
result against its model (check), may ask the mission owner (ask_human), and
submits (finish).  The harness then checks the final plan against the ground
truth, so model-extraction errors still show up as violations.

Phase 2+ extends this loop: self-consistency on the extracted model, targeted
clarification questions, and event-driven re-planning (see README roadmap).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..checker import check
from ..geometry import eligible
from ..schema import CheckResult, Plan, Scenario
from .client import ChatClient, parse_json
from .methods import Meter, MethodResult, clean_spec
from .prompts import AGENT_SYSTEM, brief_message


@dataclass
class AgentState:
    """Everything the agent knows; also the record we log for analysis."""

    brief: str
    spec: Scenario | None = None
    spec_rejections: int = 0
    last_solution: Plan | None = None
    last_solution_info: dict[str, Any] = field(default_factory=dict)
    last_check: CheckResult | None = None
    final_plan: Plan | None = None
    questions: list[str] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    human: Callable[[str], str] | None = None  # oracle for ask_human (None = unavailable)


# ----------------------------------------------------------------------------- tools
def tool_submit_spec(state: AgentState, args: dict[str, Any]) -> dict[str, Any]:
    try:
        spec = Scenario.model_validate(clean_spec(args.get("spec") or {}))
    except Exception as err:  # noqa: BLE001
        state.spec_rejections += 1
        return {"ok": False, "problems": [str(err)[:1500]]}
    problems = []
    for t in spec.tasks:
        if not any(eligible(spec, r, t) for r in spec.robots):
            problems.append(f"no robot in your model can do {t.id} (needs {t.required}, payload {t.payload:g} kg, "
                            f"at {t.location})")
        if t.deadline is not None and t.deadline < t.duration:
            problems.append(f"{t.id} has a deadline shorter than its duration")
    if problems:
        state.spec_rejections += 1
        return {"ok": False, "problems": problems}
    state.spec, state.last_solution, state.last_check = spec, None, None
    return {"ok": True, "summary": f"{len(spec.robots)} robots, {len(spec.tasks)} tasks, "
                                   f"{len(spec.precedence)} precedence links, {len(spec.standby)} standby rules, "
                                   f"horizon {spec.horizon:g} s"}


def tool_solve(state: AgentState, args: dict[str, Any]) -> dict[str, Any]:
    from ..solvers import SOLVERS

    if state.spec is None:
        return {"error": "submit_spec first"}
    method = str(args.get("method", "milp")).lower()
    if method not in SOLVERS:
        return {"error": f"unknown method {method}; use milp, auction or hungarian"}
    limit = float(args.get("time_limit_s", 30) or 30)
    res = SOLVERS[method](state.spec, time_limit=max(1.0, min(limit, 120.0)))
    state.last_solution_info = {"method": method, "status": res.status, "time_s": round(res.time_s, 3)}
    if res.plan is None:
        return {"status": res.status, "reason": res.info.get("reason", "no solution found")}
    state.last_solution, state.last_check = res.plan, None
    sched = check(state.spec, res.plan).schedule
    return {"status": res.status, "makespan_s": round(sched.makespan, 1) if sched else None,
            "routes": res.plan.routes, "standby": res.plan.standby, "solve_time_s": round(res.time_s, 2)}


def _plan_arg(state: AgentState, args: dict[str, Any]) -> Plan | None:
    if "plan" in args and args["plan"]:
        raw = args["plan"]
        return Plan(routes={str(k): [str(t) for t in v] for k, v in (raw.get("routes") or {}).items()},
                    standby=[str(s) for s in raw.get("standby") or []])
    return state.last_solution


def tool_check(state: AgentState, args: dict[str, Any]) -> dict[str, Any]:
    if state.spec is None:
        return {"error": "submit_spec first"}
    plan = _plan_arg(state, args)
    if plan is None:
        return {"error": "nothing to check; solve first or pass a plan"}
    res = check(state.spec, plan)
    state.last_check = res
    return {"feasible": res.feasible, "violations": [v.detail for v in res.violations][:20],
            "makespan_s": round(res.schedule.makespan, 1) if res.schedule else None}


def tool_ask_human(state: AgentState, args: dict[str, Any]) -> dict[str, Any]:
    q = str(args.get("question", ""))[:500]
    state.questions.append(q)
    if state.human is None:
        return {"answer": "No one is available to answer. Follow the brief as written."}
    return {"answer": state.human(q)}


def tool_finish(state: AgentState, args: dict[str, Any]) -> dict[str, Any]:
    plan = _plan_arg(state, args)
    if plan is None:
        return {"error": "no plan to submit; solve first"}
    state.final_plan = plan
    return {"done": True}


TOOLS: dict[str, Callable[[AgentState, dict[str, Any]], dict[str, Any]]] = {
    "submit_spec": tool_submit_spec, "solve": tool_solve, "check": tool_check,
    "ask_human": tool_ask_human, "finish": tool_finish,
}


# ----------------------------------------------------------------------------- loop
def run_agent(client: ChatClient, scn: Scenario, brief: str, style: str, max_steps: int = 10,
              human: Callable[[str], str] | None = None, **_: Any) -> MethodResult:
    out = MethodResult(plan=None, status="no_plan")
    meter = Meter(client, out)
    state = AgentState(brief=brief, human=human)
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": AGENT_SYSTEM.replace("{max_steps}", str(max_steps))},
                {"role": "user", "content": brief_message(brief)}]
    for step in range(max_steps):
        out.iterations = step + 1
        try:
            resp = meter.chat(messages, purpose="agent",
                              context={"scenario": scn, "style": style, "state": state, "round": step})
        except Exception as err:  # noqa: BLE001
            out.status, out.error = "error", str(err)[:300]
            break
        messages.append({"role": "assistant", "content": resp.text})
        try:
            action = parse_json(resp.text)
            name, args = str(action["action"]), action.get("args") or {}
        except Exception:  # noqa: BLE001
            name, args = "?", {}
            obs: dict[str, Any] = {"error": 'reply with one JSON object {"thought", "action", "args"}'}
        else:
            tool = TOOLS.get(name)
            if tool is None:
                obs = {"error": f"unknown tool '{name}'"}
            else:
                t_tool = time.perf_counter()
                try:
                    obs = tool(state, args if isinstance(args, dict) else {})
                except Exception as err:  # noqa: BLE001 - tool errors go back to the agent
                    obs = {"error": f"{type(err).__name__}: {err}"[:500]}
                if name == "solve":
                    out.solver_time_s += time.perf_counter() - t_tool
        state.log.append({"step": step, "action": name, "observation": obs})
        if state.final_plan is not None:
            break
        messages.append({"role": "user", "content": "OBSERVATION " + json.dumps(obs, default=str)[:6000]})

    out.spec = state.spec
    if state.final_plan is not None:
        out.plan, out.status = state.final_plan, "ok"
    elif state.last_solution is not None:
        out.plan, out.status = state.last_solution, "budget"  # ran out of steps; use the last solution
    out.trace.append({"agent_log": state.log, "questions": state.questions})
    out.time_s = time.perf_counter() - t0
    return out
