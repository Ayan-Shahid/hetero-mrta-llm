"""Proposed system: a tool-using planning agent with switchable gap components.

The agent never sees the ground truth. It builds its own formal model of the brief,
gets a plan for that model, verifies it, may ask the mission owner, and submits. The
harness then checks the final plan against the ground truth, so model-extraction errors
still count as failures.

Gap components (each can be switched off for the ablation study, experiment E5):

  G1  g1_solvers / g1_portfolio   solve tool backed by a verified solver portfolio
                                  (off: the agent writes routes itself)
  G2  g2_check / g2_crosscheck     plan verification gate + check tool; independent second
                                  reading of the brief compared fact by fact with the model
  G3  g3_staged / g3_clarify       three-pass staged extraction (draft_model tool);
                                  targeted questions to the mission owner (ask_human)

Every step is recorded as a trace event (thought, action, arguments, observation, state
snapshot, tokens, latency) that the experiment runner writes to logs/ as JSONL.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ..gaps.g1_portfolio import milp_budget, solve_portfolio
from ..gaps.g2_verify import diff_models, plan_violations, sanity_problems
from ..gaps.g3_language import staged_extraction
from ..schema import Plan, Scenario
from .client import ChatClient, QuotaExhausted, parse_json
from .edits import apply_edits
from .methods import Meter, MethodResult, clean_spec, explain_validation
from .prompts import (AGENT_HEAD, AGENT_PROCEDURE, AGENT_TAIL, AGENT_TOOLS, AUDIT_SYSTEM, CAPABILITY_GUIDE,
                      SPEC_FIELDS, brief_message)


@dataclass(frozen=True)
class AgentConfig:
    g1_solvers: bool = True
    g1_portfolio: bool = True
    g2_check: bool = True
    g2_crosscheck: bool = True
    g3_staged: bool = True
    g3_clarify: bool = True
    max_steps: int = 12
    max_questions: int = 2
    capability_guide: bool = True

    def tools(self) -> list[str]:
        names = []
        if self.g3_staged:
            names.append("draft_model")
        names.append("submit_spec")
        if self.g1_solvers:
            names.append("solve" if self.g1_portfolio else "solve_single")
        if self.g2_check:
            names.append("check")
        if self.g3_clarify:
            names.append("ask_human")
        names.append("finish")
        return names


# Named configurations used by the experiments (E3 = full, E5 = one component removed at a time).
AGENT_PRESETS: dict[str, AgentConfig] = {
    "full": AgentConfig(),
    "no-g1": AgentConfig(g1_solvers=False),
    "no-g1-portfolio": AgentConfig(g1_portfolio=False),
    "no-g2": AgentConfig(g2_check=False, g2_crosscheck=False),
    "no-g2-crosscheck": AgentConfig(g2_crosscheck=False),
    "no-g3": AgentConfig(g3_staged=False, g3_clarify=False),
    "no-g3-clarify": AgentConfig(g3_clarify=False),
    "phase1": AgentConfig(g1_portfolio=False, g2_crosscheck=False, g3_staged=False, g3_clarify=False,
                          capability_guide=False, max_steps=10),
}


def system_prompt(cfg: AgentConfig) -> str:
    tools = cfg.tools()
    lines = [AGENT_HEAD] + [AGENT_TOOLS[t].replace("{max_questions}", str(cfg.max_questions)) for t in tools]
    first = "call draft_model, then submit_spec with base \"draft\" and edits for anything wrong" if cfg.g3_staged \
        else "write the formal model and call submit_spec"
    verify = "check the plan, " if cfg.g2_check else ""
    if cfg.g1_solvers:
        proc = AGENT_PROCEDURE["solver"].format(first=first, verify=verify)
    else:
        proc = AGENT_PROCEDURE["nosolver"].format(first=first, verify=verify.capitalize())
    if cfg.g2_crosscheck:
        proc += (" After submit_spec, a second independent reading of the brief is compared with your model; re-read "
                 "the brief for every disagreement it lists and resubmit (edits on base \"current\") before solving.")
    if cfg.g3_clarify:
        proc += " Use ask_human only for a fact the brief leaves genuinely unclear."
    guide = CAPABILITY_GUIDE if cfg.capability_guide else ""
    tail = AGENT_TAIL.format(max_steps=cfg.max_steps, spec_fields=SPEC_FIELDS, guide=guide)
    return "\n".join(lines) + "\n\n" + proc + "\n\n" + tail


@dataclass
class AgentState:
    """Everything the agent knows; snapshots of it go into the trace."""

    brief: str
    draft: dict[str, Any] | None = None
    spec: Scenario | None = None
    spec_dict: dict[str, Any] | None = None
    spec_first: dict[str, Any] | None = None  # first valid model, before any cross-check correction
    spec_version: int = 0
    spec_accepted: bool = False
    spec_rejections: int = 0
    second_reading: Scenario | None = None
    crosscheck_status: str = "off"  # off | pending | resolved | skipped
    disagreements_first: int | None = None
    disagreements_final: int | None = None
    last_solution: Plan | None = None
    last_solution_info: dict[str, Any] = field(default_factory=dict)
    last_check_feasible: bool | None = None
    gate_blocks: int = 0
    final_plan: Plan | None = None
    questions: list[str] = field(default_factory=list)
    draft_problems: list[str] = field(default_factory=list)
    human: Callable[[str], str] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"has_draft": self.draft is not None, "spec_version": self.spec_version,
                "spec_accepted": self.spec_accepted, "crosscheck": self.crosscheck_status,
                "has_solution": self.last_solution is not None, "last_check_feasible": self.last_check_feasible,
                "questions": len(self.questions), "gate_blocks": self.gate_blocks}


class Agent:
    def __init__(self, client: ChatClient, scn: Scenario, brief: str, style: str, cfg: AgentConfig,
                 human: Callable[[str], str] | None, out: MethodResult):
        self.cfg, self.scn, self.style = cfg, scn, style
        self.state = AgentState(brief=brief, human=human)
        self.out = out
        self.meter = Meter(client, out)
        self.counts: dict[str, int] = {}

    def ctx(self, **kw: Any) -> dict[str, Any]:
        return {"scenario": self.scn, "style": self.style, "state": self.state, "config": self.cfg, **kw}

    # ------------------------------------------------------------------ tools
    def t_draft_model(self, args: dict[str, Any]) -> dict[str, Any]:
        st = self.state
        if st.draft is None:
            st.draft, st.draft_problems = staged_extraction(self.meter, st.brief, self.ctx(round=-1))
        return {"draft": st.draft, "problems": st.draft_problems[:15],
                "next": "submit_spec with base \"draft\" and edits for anything wrong or missing"}

    def _spec_from_args(self, args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        st = self.state
        if isinstance(args.get("spec"), dict):
            return args["spec"], []
        base = str(args.get("base", "draft" if st.spec_dict is None else "current")).lower()
        model = st.draft if base == "draft" else st.spec_dict
        if model is None:
            return None, [f"there is no {base} model yet" + (" (call draft_model first)" if base == "draft" else "")]
        edits = args.get("edits") or []
        return apply_edits(model, edits)

    def t_submit_spec(self, args: dict[str, Any]) -> dict[str, Any]:
        st, cfg = self.state, self.cfg
        raw, edit_problems = self._spec_from_args(args)
        if raw is None:
            st.spec_rejections += 1
            return {"ok": False, "problems": edit_problems}
        try:
            cleaned = clean_spec(raw)
        except Exception as err:  # noqa: BLE001
            st.spec_rejections += 1
            return {"ok": False, "problems": edit_problems + [str(err)[:600]]}
        try:
            spec = Scenario.model_validate(cleaned)
        except Exception as err:  # noqa: BLE001
            st.spec_rejections += 1
            # Keep the invalid model so the agent can repair it with edits on base "current"
            # (the pilot showed agents looping on "there is no current model yet").
            st.spec_dict = cleaned
            return {"ok": False, "problems": edit_problems + explain_validation(err, cleaned),
                    "hint": "fix only these items with edits on base \"current\"; capabilities must be "
                            "camera, thermal, carry or manipulate"}
        problems = sanity_problems(spec)
        if problems and st.spec_rejections >= 2:
            problems.append("hint: if no robot can do a task, re-read that robot's line in the brief for its "
                            "equipment and how much it can carry, and fix it with a set edit on robot:<id>")
        if problems:
            st.spec_rejections += 1
            st.spec_dict = cleaned  # keep it so edits on base "current" can fix the problems
            return {"ok": False, "problems": edit_problems + problems,
                    "hint": "fix these with edits on base \"current\""}
        st.spec, st.spec_dict, st.spec_version = spec, cleaned, st.spec_version + 1
        if st.spec_first is None:
            st.spec_first = cleaned
        st.last_solution, st.last_check_feasible = None, None
        summary = (f"{len(spec.robots)} robots, {len(spec.tasks)} tasks, {len(spec.precedence)} precedence links, "
                   f"{len(spec.standby)} standby rules, {sum(t.deadline is not None for t in spec.tasks)} deadlines, "
                   f"horizon {spec.horizon:g} s")
        if not cfg.g2_crosscheck:
            st.spec_accepted = True
            return {"ok": True, "summary": summary, "edit_problems": edit_problems}
        if st.crosscheck_status == "off":
            st.second_reading = self._second_reading()
            st.crosscheck_status = "skipped" if st.second_reading is None else "pending"
        if st.crosscheck_status == "skipped":
            st.spec_accepted = True
            return {"ok": True, "summary": summary, "note": "second reading unavailable; model accepted"}
        diffs = diff_models(spec, st.second_reading)
        if st.crosscheck_status == "pending":
            st.disagreements_first = len(diffs)
            if diffs:
                st.crosscheck_status = "review"
                st.spec_accepted = False
                return {"ok": True, "accepted": False, "summary": summary, "disagreements": diffs,
                        "instruction": "Re-read the brief for each item. Keep your value where the brief supports "
                                       "it, otherwise change it. Then submit_spec again (edits on base \"current\", "
                                       "or [] if nothing changes); that submission is accepted."}
        st.crosscheck_status = "resolved"
        st.disagreements_final = len(diffs)
        st.spec_accepted = True
        return {"ok": True, "accepted": True, "summary": summary, "remaining_disagreements": len(diffs)}

    def _second_reading(self) -> Scenario | None:
        messages = [{"role": "system", "content": AUDIT_SYSTEM}, {"role": "user", "content": brief_message(self.state.brief)}]
        resp = self.meter.chat(messages, purpose="crosscheck", context=self.ctx(round=-2))
        try:
            return Scenario.model_validate(clean_spec(parse_json(resp.text)))
        except Exception:  # noqa: BLE001 - an unusable second reading only disables the cross-check
            return None

    def t_solve(self, args: dict[str, Any]) -> dict[str, Any]:
        st, cfg = self.state, self.cfg
        if st.spec is None:
            return {"error": "submit_spec first"}
        if not st.spec_accepted:
            return {"error": "the model is waiting for your review of the cross-check; submit_spec again first"}
        t0 = time.perf_counter()
        if cfg.g1_portfolio:
            res = solve_portfolio(st.spec)
            self.out.solver_time_s += time.perf_counter() - t0
            st.last_solution_info = {"method": res.method, "status": res.status, "time_s": round(res.time_s, 3)}
            if res.plan is None:
                return {"status": "infeasible", "candidates": res.candidates,
                        "hint": "no solver found a feasible plan for your model; re-check deadlines, payloads, "
                                "zones and capabilities against the brief"}
            st.last_solution, st.last_check_feasible = res.plan, None
            return {"status": res.status, "chosen": res.method, "makespan_s": round(res.makespan, 1),
                    "routes": res.plan.routes, "standby": res.plan.standby, "candidates": res.candidates}
        from ..solvers import SOLVERS

        method = str(args.get("method", "milp")).lower()
        if method not in SOLVERS:
            return {"error": f"unknown method {method}; use milp, auction or hungarian"}
        limit = float(args.get("time_limit_s", milp_budget(st.spec)) or 30)
        res = SOLVERS[method](st.spec, time_limit=max(1.0, min(limit, 120.0)))
        self.out.solver_time_s += time.perf_counter() - t0
        st.last_solution_info = {"method": method, "status": res.status, "time_s": round(res.time_s, 3)}
        if res.plan is None:
            return {"status": res.status, "reason": res.info.get("reason", "no solution found")}
        st.last_solution, st.last_check_feasible = res.plan, None
        _, _, ms = plan_violations(st.spec, res.plan)
        return {"status": res.status, "makespan_s": round(ms, 1) if ms else None, "routes": res.plan.routes,
                "standby": res.plan.standby}

    t_solve_single = t_solve

    def _plan_arg(self, args: dict[str, Any]) -> Plan | None:
        raw = args.get("plan")
        if isinstance(raw, dict) and raw:
            from .methods import parse_plan

            try:
                return parse_plan(json.dumps(raw), self.state.spec or self.scn_shell())
            except Exception:  # noqa: BLE001
                return None
        return self.state.last_solution

    def scn_shell(self) -> Scenario:
        return self.scn  # only ids are used by parse_plan's id matching

    def t_check(self, args: dict[str, Any]) -> dict[str, Any]:
        st = self.state
        if st.spec is None:
            return {"error": "submit_spec first"}
        plan = self._plan_arg(args)
        if plan is None:
            return {"error": "nothing to check; solve first or pass a plan"}
        if args.get("plan"):
            st.last_solution = plan
        ok, viol, ms = plan_violations(st.spec, plan)
        st.last_check_feasible = ok
        return {"feasible": ok, "violations": viol, "makespan_s": round(ms, 1) if ms else None}

    def t_ask_human(self, args: dict[str, Any]) -> dict[str, Any]:
        st = self.state
        q = str(args.get("question", ""))[:500]
        st.questions.append(q)
        if len(st.questions) > self.cfg.max_questions:
            return {"answer": "No more questions available. Go with your best reading of the brief."}
        if st.human is None:
            return {"answer": "No one is available to answer. Follow the brief as written."}
        return {"answer": st.human(q)}

    def t_finish(self, args: dict[str, Any]) -> dict[str, Any]:
        st, cfg = self.state, self.cfg
        plan = self._plan_arg(args)
        if plan is None:
            return {"error": "no plan to submit; " + ("solve first" if cfg.g1_solvers else "pass {\"plan\": ...}")}
        if cfg.g2_check and st.spec is not None and st.gate_blocks == 0:
            ok, viol, _ = plan_violations(st.spec, plan)
            if not ok:
                st.gate_blocks += 1
                st.last_solution = plan
                return {"error": "this plan violates your own model", "violations": viol,
                        "hint": "fix the plan (or the model, if it misreads the brief) and finish again"}
        st.final_plan = plan
        return {"done": True}

    # ------------------------------------------------------------------ loop
    def run(self) -> MethodResult:
        cfg, st, out = self.cfg, self.state, self.out
        messages = [{"role": "system", "content": system_prompt(cfg)},
                    {"role": "user", "content": brief_message(st.brief)}]
        allowed = set(cfg.tools())
        bad_replies = 0
        t0 = time.perf_counter()
        for step in range(cfg.max_steps):
            out.iterations = step + 1
            calls_before, tok_before = out.llm_calls, out.prompt_tokens + out.completion_tokens
            lat_before = out.llm_latency_s
            resp = self.meter.chat(messages, purpose="agent", context=self.ctx(round=step))
            messages.append({"role": "assistant", "content": resp.text})
            thought, name, args = "", "?", {}
            try:
                action = parse_json(resp.text)
                name, args = str(action.get("action", "?")), action.get("args") or {}
                thought = str(action.get("thought", ""))[:600]
                if not isinstance(args, dict):
                    args = {}
                name = ALIASES.get(name.strip().lower(), name.strip())
                if name == "submit_spec" and "spec" not in args and ("tasks" in args or "robots" in args):
                    args = {"spec": args}  # the model put the spec straight into args
            except Exception:  # noqa: BLE001
                bad_replies += 1
                out.extra["parse_failures"] = out.extra.get("parse_failures", 0) + 1
                obs: dict[str, Any] = {"error": 'reply with exactly one JSON object {"thought", "action", "args"}'}
            else:
                tool_name = "solve_single" if (name == "solve" and not cfg.g1_portfolio and cfg.g1_solvers) else name
                if tool_name not in allowed:
                    shown = ["solve" if t == "solve_single" else t for t in cfg.tools()]
                    obs = {"error": f"unknown or unavailable tool '{name}'; available tools: {shown}"}
                else:
                    self.counts[name] = self.counts.get(name, 0) + 1
                    try:
                        obs = getattr(self, f"t_{tool_name}")(args)
                    except QuotaExhausted:
                        raise
                    except Exception as err:  # noqa: BLE001 - tool errors go back to the agent
                        obs = {"error": f"{type(err).__name__}: {err}"[:500]}
                bad_replies = 0
            out.trace.append({"type": "step", "step": step, "t": round(time.perf_counter() - t0, 3),
                              "thought": thought, "action": name, "args": _short(args), "observation": _short(obs),
                              "state": st.snapshot(), "llm_calls": out.llm_calls - calls_before,
                              "tokens": out.prompt_tokens + out.completion_tokens - tok_before,
                              "llm_latency_s": round(out.llm_latency_s - lat_before, 3)})
            if st.final_plan is not None or bad_replies >= 3:
                break
            messages.append({"role": "user", "content": "OBSERVATION " + json.dumps(obs, default=str)[:7000]})

        if st.final_plan is None and st.spec is None and st.draft and cfg.g1_solvers:
            try:  # robustness: the agent never stored a model, but the staged draft is valid
                spec = Scenario.model_validate(clean_spec(st.draft))
                if not sanity_problems(spec):
                    st.spec = spec
                    out.extra["used_draft_fallback"] = True
            except Exception:  # noqa: BLE001
                pass
        if st.final_plan is not None:
            out.plan, out.status = st.final_plan, "ok"
        elif cfg.g1_solvers and st.spec is not None:
            res = solve_portfolio(st.spec)  # robustness: out of steps, still return the best verified plan
            out.plan, out.status = res.plan, "budget_fallback" if res.plan else "no_plan"
        elif st.last_solution is not None:
            out.plan, out.status = st.last_solution, "budget"
        else:
            out.status = "parse_error" if bad_replies >= 3 else "no_plan"
        out.spec = st.spec
        out.extra["spec_first"] = st.spec_first
        out.extra.update({"tool_calls": dict(self.counts), "n_tool_calls": sum(self.counts.values()),
                          "questions": len(st.questions), "gate_blocks": st.gate_blocks,
                          "spec_rejections": st.spec_rejections, "crosscheck": st.crosscheck_status,
                          "disagreements_first": st.disagreements_first,
                          "disagreements_final": st.disagreements_final,
                          "solution_method": st.last_solution_info.get("method")})
        out.trace.append({"type": "end", "status": out.status, "questions": st.questions,
                          "config": asdict(cfg), "extra": out.extra})
        return out


ALIASES = {"draft": "draft_model", "draft_spec": "draft_model", "extract": "draft_model",
           "submit": "submit_spec", "submit_model": "submit_spec", "spec": "submit_spec",
           "solve_portfolio": "solve", "auto": "solve", "verify": "check", "check_plan": "check",
           "ask": "ask_human", "question": "ask_human", "done": "finish", "submit_plan": "finish"}


def _short(obj: Any, limit: int = 8000) -> Any:
    text = json.dumps(obj, default=str)
    return obj if len(text) <= limit else text[:limit] + "...(truncated)"


def run_agent(client: ChatClient, scn: Scenario, brief: str, style: str, config: AgentConfig | str = "full",
              human: Callable[[str], str] | None = None, **_: Any) -> MethodResult:
    cfg = AGENT_PRESETS[config] if isinstance(config, str) else config
    out = MethodResult(plan=None, status="no_plan")
    t0 = time.perf_counter()
    agent = Agent(client, scn, brief, style, cfg, human, out)
    try:
        agent.run()
    except QuotaExhausted:
        raise
    except Exception as err:  # noqa: BLE001 - a crashed episode is a result, not a crash of the sweep
        out.status, out.error = "error", f"{type(err).__name__}: {err}"[:300]
    out.time_s = time.perf_counter() - t0
    return out
