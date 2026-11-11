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


# Words small models use instead of the four capability names (seen in the Phase 2 pilot:
# "thermal camera", "robotic arm", "cargo carrier"). Mapped before validation, for every method.
_CAP_WORDS = [("thermal", "thermal"), ("heat", "thermal"), ("infrared", "thermal"),
              ("manipul", "manipulate"), ("arm", "manipulate"), ("gripper", "manipulate"),
              ("carr", "carry"), ("cargo", "carry"), ("payload", "carry"), ("haul", "carry"), ("lift", "carry"),
              ("deliver", "carry"), ("camera", "camera"), ("photo", "camera"), ("visual", "camera"),
              ("inspect", "camera"), ("vision", "camera")]
_MOBILITY = {"aerial": "air", "flying": "air", "fly": "air", "drone": "air", "wheeled": "ground",
             "legged": "ground", "road": "ground", "static": "fixed", "stationary": "fixed", "none": "fixed"}
_ZONE = {"rooftop": "roof", "outside": "outdoor", "outdoors": "outdoor", "indoors": "indoor", "inside": "indoor"}
_TYPE = {"drone": "quadcopter", "uav": "quadcopter", "ground_robot": "rover", "ugv": "rover",
         "mobile manipulator": "mobile_manipulator", "manipulator": "mobile_manipulator",
         "arm": "fixed_arm", "robot_arm": "fixed_arm", "robot arm": "fixed_arm", "fixed arm": "fixed_arm"}


def canonical_capability(word: Any) -> str | None:
    w = str(word).strip().lower()
    if w in ("camera", "thermal", "carry", "manipulate"):
        return w
    for key, cap in _CAP_WORDS:
        if key in w:
            return cap
    return None


def _caps(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for v in values or []:
        c = canonical_capability(v)
        if c and c not in out:
            out.append(c)
    return out


def _num(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return float(v.strip().split()[0])
        except (ValueError, IndexError):
            return v
    return v


def clean_spec(data: dict[str, Any]) -> dict[str, Any]:
    """Normalise an LLM-written model before validation.

    Keeps only fields the formal model knows, maps synonyms to the canonical names
    (capabilities, mobility, robot types, zones), turns numeric strings into numbers, adds the
    base at (0, 0) when robots start there but the model forgot to list it (every brief states
    that position), and drops deadlines after the horizon (they constrain nothing).
    Facts are never invented: only names are normalised.
    """
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
    out["horizon"] = _num(out.get("horizon"))
    for key in ("locations", "robots", "tasks", "precedence", "standby"):
        items = out.get(key) or []
        out[key] = [{k: v for k, v in it.items() if k in keep[key]} for it in items if isinstance(it, dict)]
    for t in out["tasks"]:
        t.setdefault("kind", "task")
        t["required"] = _caps(t.get("required"))
        for f in ("payload", "duration", "deadline"):
            t[f] = _num(t.get(f))
        if t.get("payload") is None:
            t["payload"] = 0
        if isinstance(t.get("deadline"), (int, float)) and isinstance(out.get("horizon"), (int, float)) \
                and t["deadline"] > out["horizon"]:
            t["deadline"] = None
    for r in out["robots"]:
        r["capabilities"] = _caps(r.get("capabilities"))
        if isinstance(r.get("mobility"), str):
            m = r["mobility"].strip().lower()
            r["mobility"] = _MOBILITY.get(m, m)
        if isinstance(r.get("type"), str):
            ty = r["type"].strip().lower()
            r["type"] = _TYPE.get(ty, ty.replace(" ", "_"))
        for f in ("speed", "payload", "endurance"):
            r[f] = _num(r.get(f))
    for loc in out["locations"]:
        if isinstance(loc.get("zone"), str):
            z = loc["zone"].strip().lower()
            loc["zone"] = _ZONE.get(z, z)
        for f in ("x", "y"):
            loc[f] = _num(loc.get(f))
    out["standby"] = [s for s in out["standby"] if not (isinstance(s.get("count"), (int, float)) and s["count"] <= 0)]
    for s in out["standby"]:
        if isinstance(s.get("robot_type"), str):
            ty = s["robot_type"].strip().lower()
            s["robot_type"] = _TYPE.get(ty, ty.replace(" ", "_"))
    names = {str(loc.get("name", "")).strip().lower() for loc in out["locations"]}
    if "base" not in names and any(str(r.get("start", "")).strip().lower() == "base" for r in out["robots"]):
        out["locations"].insert(0, {"name": "base", "x": 0.0, "y": 0.0, "zone": "outdoor"})
    return out


def explain_validation(err: Exception, model: dict[str, Any] | None = None, limit: int = 8) -> list[str]:
    """Short, actionable lines instead of a raw pydantic dump (the pilot showed the model
    repeating the same mistake when it got the full error text)."""
    errors = getattr(err, "errors", None)
    if not callable(errors):
        return [str(err)[:600]]
    lines = []
    for e in errors()[:limit]:
        loc = list(e.get("loc", ()))
        where = ".".join(str(x) for x in loc)
        if model and len(loc) >= 2 and loc[0] in ("tasks", "robots", "locations") and isinstance(loc[1], int):
            items = model.get(loc[0]) or []
            if loc[1] < len(items):
                ident = items[loc[1]].get("id") or items[loc[1]].get("name")
                where = f"{loc[0][:-1]} {ident}" + ("." + ".".join(str(x) for x in loc[2:]) if loc[2:] else "")
        msg = e.get("msg", "")
        if e.get("type") == "literal_error":
            msg = f"{e.get('input')!r} is not allowed; {msg[0].lower() + msg[1:]}"
        lines.append(f"{where}: {msg}")
    extra = len(errors()) - limit
    if extra > 0:
        lines.append(f"... and {extra} more of the same kind")
    return lines


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
