"""Non-agentic LLM methods (baselines) and shared helpers.

llm-direct     one call: brief -> plan JSON (the "LLM answers directly" mode).
llm-pefa       COHERENT-style loop (Liu et al., ICRA 2025), adapted to static allocation: a central
               assigner proposes a plan, robot executors check whether each assignment is executable
               (equipment, payload, reachability, ids), failures are fed back, and the assigner adjusts
               with the last 5 rounds of history.  Like COHERENT it has no notion of time, so deadlines,
               endurance, standby and makespan are never fed back.
llm-formulate  one call: brief -> formal model JSON; the MILP solves it (the "LLM writes the optimisation
               model" mode, in the spirit of LiP-LLM and LLM-based MILP construction).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..checker import check
from ..schema import Plan, Scenario
from .client import ChatClient, LLMResponse, QuotaExhausted, parse_json
from .prompts import DIRECT_SYSTEM, FORMULATE_SYSTEM, PEFA_FEEDBACK, brief_message

EXECUTABILITY = {"unknown_robot", "unknown_task", "duplicate", "missing", "capability", "payload", "unreachable"}


@dataclass
class MethodResult:
    plan: Plan | None
    status: str  # ok | parse_error | spec_invalid | solver_failed | budget | no_plan | error
    time_s: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_latency_s: float = 0.0
    iterations: int = 0
    solver_time_s: float = 0.0
    spec: Scenario | None = None
    error: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # method-specific counters (tool calls, cross-check, ...)


class Meter:
    """Wraps a client and records calls, tokens and latency for one method run."""

    def __init__(self, client: ChatClient, result: MethodResult):
        self.client, self.result = client, result

    def chat(self, messages, *, purpose: str, context: dict[str, Any]) -> LLMResponse:
        resp = self.client.chat(messages, purpose=purpose, context=context)
        r = self.result
        r.llm_calls += 1
        r.prompt_tokens += resp.prompt_tokens
        r.completion_tokens += resp.completion_tokens
        r.llm_latency_s += resp.latency_s
        r.trace.append({"type": "llm", "purpose": purpose, "reply": resp.text[:4000], "cached": resp.cached,
                        "prompt_tokens": resp.prompt_tokens, "completion_tokens": resp.completion_tokens,
                        "latency_s": round(resp.latency_s, 3)})
        return resp


def _canon(ids: list[str]) -> dict[str, str]:
    return {i.strip().lower(): i for i in ids}


def parse_plan(text: str, scn: Scenario) -> Plan:
    """Parse a plan reply.  Ids are matched case-insensitively (formatting leniency only)."""
    data = parse_json(text)
    if not isinstance(data, dict) or "routes" not in data:
        raise ValueError("reply has no 'routes' object")
    robots = _canon([r.id for r in scn.robots])
    tasks = _canon([t.id for t in scn.tasks])
    routes: dict[str, list[str]] = {}
    raw = data["routes"]
    if isinstance(raw, list):  # [{"robot": .., "tasks": [..]}]
        raw = {item.get("robot"): item.get("tasks", []) for item in raw if isinstance(item, dict)}
    if not isinstance(raw, dict):
        raise ValueError("'routes' must map robot ids to task lists")
    for rid, tids in raw.items():
        if isinstance(tids, str):
            tids = [tids]
        rid = robots.get(str(rid).strip().lower(), str(rid))
        routes.setdefault(rid, []).extend(tasks.get(str(t).strip().lower(), str(t)) for t in (tids or []))
    standby = [robots.get(str(s).strip().lower(), str(s)) for s in (data.get("standby") or [])]
    return Plan(routes=routes, standby=standby, meta={"notes": str(data.get("notes", ""))[:500]})


def clean_spec(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields the formal model knows; drop extras an LLM may add."""
    keep = {
        "top": {"id", "horizon", "locations", "robots", "tasks", "precedence", "standby"},
        "locations": {"name", "x", "y", "zone"},
        "robots": {"id", "type", "mobility", "capabilities", "speed", "payload", "endurance", "start"},
        "tasks": {"id", "kind", "description", "location", "required", "payload", "duration", "deadline"},
        "precedence": {"before", "after"},
        "standby": {"robot_type", "count"},
    }
    if not isinstance(data, dict):
        raise ValueError("model must be a JSON object")
    out = {k: v for k, v in data.items() if k in keep["top"]}
    out.setdefault("id", "llm-spec")
    for key in ("locations", "robots", "tasks", "precedence", "standby"):
        items = out.get(key) or []
        out[key] = [{k: v for k, v in it.items() if k in keep[key]} for it in items if isinstance(it, dict)]
    for t in out["tasks"]:
        t.setdefault("kind", "task")
        if t.get("payload") is None:
            t["payload"] = 0
    for r in out["robots"]:
        caps = r.get("capabilities") or []
        r["capabilities"] = [str(c).lower() for c in caps]
        if isinstance(r.get("mobility"), str):
            r["mobility"] = r["mobility"].lower()
    for loc in out["locations"]:
        if isinstance(loc.get("zone"), str):
            loc["zone"] = loc["zone"].lower()
    return out


def executability_failures(scn: Scenario, plan: Plan) -> list[tuple[str | None, str | None, str]]:
    res = check(scn, plan)
    return [(v.robot, v.task, v.detail) for v in res.violations if v.kind in EXECUTABILITY]


# ----------------------------------------------------------------------------- methods
def run_direct(client: ChatClient, scn: Scenario, brief: str, style: str, **_: Any) -> MethodResult:
    out = MethodResult(plan=None, status="error")
    meter = Meter(client, out)
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": DIRECT_SYSTEM}, {"role": "user", "content": brief_message(brief)}]
    try:
        resp = meter.chat(messages, purpose="direct", context={"scenario": scn, "style": style})
        out.plan, out.status = parse_plan(resp.text, scn), "ok"
    except QuotaExhausted:
        raise
    except Exception as err:  # noqa: BLE001 - every failure is a result, not a crash
        out.status, out.error = "parse_error", str(err)[:300]
    out.iterations = 1
    out.time_s = time.perf_counter() - t0
    return out


def run_pefa(client: ChatClient, scn: Scenario, brief: str, style: str, max_rounds: int = 5,
             history: int = 5, **_: Any) -> MethodResult:
    out = MethodResult(plan=None, status="error")
    meter = Meter(client, out)
    t0 = time.perf_counter()
    head = [{"role": "system", "content": DIRECT_SYSTEM}, {"role": "user", "content": brief_message(brief)}]
    turns: list[dict[str, str]] = []
    previous: dict | None = None
    failures: list = []
    for rnd in range(max_rounds):
        out.iterations = rnd + 1
        messages = head + turns[-2 * history:]
        ctx = {"scenario": scn, "style": style, "round": rnd, "previous_plan": previous,
               "failures": [(f[0], f[1]) for f in failures]}
        try:
            resp = meter.chat(messages, purpose="pefa", context=ctx)
        except QuotaExhausted:
            raise
        except Exception as err:  # noqa: BLE001
            out.status, out.error = "error", str(err)[:300]
            break
        turns.append({"role": "assistant", "content": resp.text})
        try:
            plan = parse_plan(resp.text, scn)
        except Exception as err:  # noqa: BLE001
            turns.append({"role": "user", "content": f"Your reply could not be parsed ({err}). "
                                                     "Return the full plan in the required JSON format."})
            continue
        out.plan, out.status = plan, "ok"
        previous = {"routes": plan.routes, "standby": plan.standby}
        failures = executability_failures(scn, plan)
        if not failures:
            break
        listing = "\n".join(f"- {d}" for _, _, d in failures[:25])
        turns.append({"role": "user", "content": PEFA_FEEDBACK.format(failures=listing)})
    if out.plan is None and out.status == "error" and out.error is None:
        out.status = "parse_error"
    out.time_s = time.perf_counter() - t0
    return out


def run_formulate(client: ChatClient, scn: Scenario, brief: str, style: str, solver: str = "milp",
                  time_limit: float = 60.0, **_: Any) -> MethodResult:
    from ..solvers import SOLVERS

    out = MethodResult(plan=None, status="error")
    meter = Meter(client, out)
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": FORMULATE_SYSTEM}, {"role": "user", "content": brief_message(brief)}]
    out.iterations = 1
    try:
        resp = meter.chat(messages, purpose="formulate", context={"scenario": scn, "style": style})
        spec = Scenario.model_validate(clean_spec(parse_json(resp.text)))
    except QuotaExhausted:
        raise
    except Exception as err:  # noqa: BLE001
        out.status, out.error = "spec_invalid", str(err)[:300]
        out.time_s = time.perf_counter() - t0
        return out
    out.spec = spec
    res = SOLVERS[solver](spec, time_limit=time_limit)
    out.solver_time_s = res.time_s
    if res.plan is None:
        out.status, out.error = "solver_failed", f"{res.status}: {res.info.get('reason', '')}"[:300]
    else:
        out.plan, out.status = res.plan, "ok"
    out.time_s = time.perf_counter() - t0
    return out
