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
        rng = random.Random(f"{scn.id}|{purpose}|{context.get('style', '')}|{context.get('round', 0)}|{self.rate}")
        if purpose in ("direct", "pefa"):
            body = self._plan(scn, rng, context)
        elif purpose == "formulate":
            body = self._spec(scn, rng)
        elif purpose == "agent":
            body = self._agent_step(scn, rng, context)
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

    # ---------------------------------------------------------------- agent
    def _agent_step(self, scn: Scenario, rng: random.Random, context: dict[str, Any]) -> dict:
        state = context["state"]
        if state.spec is None:
            if state.spec_rejections == 0:
                spec = self._spec(scn, rng)
            else:
                spec = json.loads(scn.model_dump_json())
            return {"thought": "Write the formal model.", "action": "submit_spec", "args": {"spec": spec}}
        if state.last_solution is None:
            method = "milp" if len(state.spec.tasks) <= 12 else "auction"
            return {"thought": "Solve it.", "action": "solve", "args": {"method": method, "time_limit_s": 30}}
        if state.last_check is None:
            return {"thought": "Verify.", "action": "check", "args": {}}
        return {"thought": "Done.", "action": "finish", "args": {}}
