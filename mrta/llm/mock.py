"""MockLLM: a deterministic stand-in used for tests and offline pipeline runs.

It is NOT a language model.  It peeks at the ground-truth scenario passed in
`context` and returns well-formed replies with errors injected at
`mock_error_rate` (wrong robot for a task, dropped deadline or precedence,
wrong capability), so every code path - parsing, checking, feedback loops,
agent tools - can be exercised without an API key.  Numbers produced with the
mock say nothing about LLM performance; result tables mark these rows as mock.
"""

from __future__ import annotations

import json
import random
from typing import Any

from ..geometry import eligibility_matrix
from ..schema import Scenario
from .client import LLMConfig, LLMResponse


class MockLLM:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.rate = config.mock_error_rate
        self._calls = 0

    def chat(self, messages, *, purpose: str, context: dict[str, Any] | None = None) -> LLMResponse:
        context = context or {}
        if "scenario" not in context:  # e.g. the --ping check
            return LLMResponse(text='{"ok": true}', prompt_tokens=1, completion_tokens=1, latency_s=0.0)
        scn: Scenario = context["scenario"]
        self._calls += 1
        rng = random.Random(f"{scn.id}|{purpose}|{context.get('style', '')}|{context.get('round', 0)}|{self.rate}"
                            f"|{self.config.seed}")
        if purpose in ("direct", "pefa"):
            body = self._plan(scn, rng, context)
        elif purpose in ("formulate", "crosscheck"):
            body = self._spec(scn, rng)
        elif purpose == "agent":
            body = self._agent_step(scn, rng, context)
        elif purpose.startswith("stage_"):
            body = self._stage(scn, rng, purpose)
        else:
            body = {"ok": True}
        text = json.dumps(body)
        prompt_chars = sum(len(m["content"]) for m in messages)
        return LLMResponse(text=text, prompt_tokens=prompt_chars // 4, completion_tokens=len(text) // 4,
                           latency_s=0.0)

    # ---------------------------------------------------------------- plans
    def _plan(self, scn: Scenario, rng: random.Random, context: dict[str, Any]) -> dict:
        from ..solvers.auction import solve_auction

        previous = context.get("previous_plan")
        failures = context.get("failures") or []
        elig = eligibility_matrix(scn)
        if previous is None:
            res = solve_auction(scn)
            routes = {k: list(v) for k, v in res.plan.routes.items()}
            standby = list(res.plan.standby)
            robot_ids = [r.id for r in scn.robots]
            for rid in list(routes):
                for tid in list(routes[rid]):
                    if rng.random() < self.rate:  # "hallucinated" assignment
                        routes[rid].remove(tid)
                        other = rng.choice(robot_ids)
                        routes.setdefault(other, []).append(tid)
            return {"notes": "mock plan", "standby": standby, "routes": routes}
        routes = {k: list(v) for k, v in previous["routes"].items()}
        for rid, tid in failures:
            if tid is None or rng.random() < 0.1:
                continue  # sometimes the "model" ignores feedback
            options = [r for r in elig if tid in elig[r] and r not in previous.get("standby", [])]
            if rid in routes and tid in routes[rid] and options:
                routes[rid].remove(tid)
                best = min(options, key=lambda r: len(routes.get(r, [])))
                routes.setdefault(best, []).append(tid)
        return {"notes": "mock revision", "standby": previous.get("standby", []), "routes": routes}

    # ---------------------------------------------------------------- specs
    def _spec(self, scn: Scenario, rng: random.Random) -> dict:
        spec = json.loads(scn.model_dump_json())
        spec.pop("events", None)
        spec.pop("objective", None)
        for t in spec["tasks"]:
            if t["deadline"] is not None and rng.random() < self.rate:
                t["deadline"] = None
            if t["required"] and rng.random() < self.rate / 2:
                t["required"] = ["camera"] if t["required"] != ["camera"] else ["thermal"]
        spec["precedence"] = [p for p in spec["precedence"] if rng.random() >= self.rate]
        if spec["standby"] and rng.random() < self.rate:
            spec["standby"] = []
        return spec

    def _stage(self, scn: Scenario, rng: random.Random, purpose: str) -> dict:
        spec = self._spec(scn, rng)
        if purpose == "stage_fleet":
            return {"horizon": spec["horizon"], "locations": spec["locations"], "robots": spec["robots"]}
        if purpose == "stage_tasks":
            return {"tasks": [{k: v for k, v in t.items() if k != "deadline"} for t in spec["tasks"]]}
        return {"deadlines": {t["id"]: t["deadline"] for t in spec["tasks"]}, "precedence": spec["precedence"],
                "standby": spec["standby"], "horizon": spec["horizon"]}

    # ---------------------------------------------------------------- agent
    def _agent_step(self, scn: Scenario, rng: random.Random, context: dict[str, Any]) -> dict:
        state = context["state"]
        cfg = context.get("config")
        g1 = getattr(cfg, "g1_solvers", True)
        g2 = getattr(cfg, "g2_check", True)
        staged = getattr(cfg, "g3_staged", False)

        def step(thought, action, args):
            return {"thought": thought, "action": action, "args": args}

        if staged and state.draft is None:
            return step("Draft the model in focused passes.", "draft_model", {})
        if not state.spec_accepted:
            if state.crosscheck_status == "review":
                if rng.random() < 0.7:  # the "model" fixes what the cross-check found
                    return step("Fix the disagreements.", "submit_spec", {"spec": json.loads(scn.model_dump_json())})
                return step("Keep my values.", "submit_spec", {"base": "current", "edits": []})
            if staged and state.spec_rejections == 0:
                return step("Accept the draft.", "submit_spec", {"base": "draft", "edits": []})
            if state.spec_rejections == 0:
                return step("Write the formal model.", "submit_spec", {"spec": self._spec(scn, rng)})
            return step("Rewrite the model.", "submit_spec", {"spec": json.loads(scn.model_dump_json())})
        if state.last_solution is None or (state.gate_blocks and state.final_plan is None and not g1
                                           and state.last_check_feasible is False):
            if g1:
                method = "auto" if getattr(cfg, "g1_portfolio", True) else (
                    "milp" if len(state.spec.tasks) <= 12 else "auction")
                return step("Solve it.", "solve", {"method": method, "time_limit_s": 30})
            from ..solvers.auction import solve_auction

            res = solve_auction(state.spec)
            routes = {k: list(v) for k, v in (res.plan.routes if res.plan else {}).items()}
            if state.last_solution is None and routes and rng.random() < self.rate * 2:
                rid = rng.choice(sorted(routes))
                if routes[rid]:
                    routes[rid] = list(reversed(routes[rid]))  # a careless ordering
            plan = {"routes": routes, "standby": list(res.plan.standby) if res.plan else []}
            return step("Write the plan.", "check" if g2 else "finish", {"plan": plan})
        if g2 and state.last_check_feasible is None:
            return step("Verify.", "check", {})
        return step("Done.", "finish", {})
