"""Prompts for the LLM methods.  Keep them here so ablations can swap them."""

from __future__ import annotations

PLAN_FORMAT = """Answer with one JSON object and nothing else:
{
  "notes": "one or two sentences on how you chose",
  "standby": ["<robot id kept free on purpose>"],
  "routes": {"<robot id>": ["<task id>", "<task id>"], "<robot id>": ["<task id>"]}
}
Every task id must appear exactly once across all routes. A robot does its tasks in the listed order.
Leave out robots that do nothing (list them under "standby" if the brief asks for standby robots)."""

DIRECT_SYSTEM = f"""You are the mission planner for a team of heterogeneous robots (drones, ground robots, robot arms).
Read the mission brief, decide which robot does which task and in what order, and make the last task finish as
early as possible while respecting every rule in the brief: equipment, payload, where each robot can go,
deadlines, precedence, standby, endurance and the time horizon. Work out travel times from the coordinates,
speeds and movement rules given in the brief.

{PLAN_FORMAT}"""

# COHERENT-style (Liu et al., ICRA 2025) proposal -> executability feedback -> adjustment.
PEFA_FEEDBACK = """Robot executors checked the plan. These assignments cannot be executed:
{failures}
Revise the plan so every robot can execute its tasks. Keep the assignments that worked unless changing them
helps. Return the complete revised plan in the same JSON format."""

PEFA_OK = "Robot executors report that every assignment is executable."

SPEC_FIELDS = """Fields (times in seconds, distances in metres, masses in kg; use null for a missing deadline):
{
  "id": "<mission id or any short name>",
  "horizon": <latest finish time for every task, s>,
  "locations": [{"name": "<as written in the brief>", "x": <number>, "y": <number>,
                 "zone": "outdoor" | "roof" | "indoor"}],
  "robots": [{"id": "<robot id>", "type": "quadcopter" | "rover" | "mobile_manipulator" | "fixed_arm",
              "mobility": "air" | "ground" | "fixed",
              "capabilities": [subset of "camera", "thermal", "carry", "manipulate"],
              "speed": <m/s, 0 if fixed>, "payload": <kg, 0 if it cannot carry>,
              "endurance": <s, use 86400 for mains-powered robots>, "start": "<location name>"}],
  "tasks": [{"id": "<task id>", "kind": "<short label>", "description": "<short text>",
             "location": "<location name>", "required": [capabilities the task needs],
             "payload": <kg to carry, 0 if none>, "duration": <s on site>, "deadline": <s or null>}],
  "precedence": [{"before": "<task id>", "after": "<task id>"}],
  "standby": [{"robot_type": "<robot type>", "count": <int>}]
}
Capabilities: "camera" (any visual inspection or photos), "thermal" (heat / hot-spot checks),
"carry" (moving an item or keeping something on board; set the task payload), "manipulate" (any arm work:
valves, filters, picking up samples). Include the base location if robots start there. Every location
referenced by a robot or task must appear in "locations". Convert minutes to seconds."""

SPEC_FORMAT = "Answer with one JSON object and nothing else.\n" + SPEC_FIELDS

FORMULATE_SYSTEM = f"""You translate mission briefs for heterogeneous robot teams into a formal optimisation model.
A solver will compute the allocation from your model, so copy every number exactly and do not invent
constraints that the brief does not state.

{SPEC_FORMAT}"""

AGENT_SYSTEM = f"""You are a planning agent for a heterogeneous robot team. You turn a mission brief into a verified
task allocation by calling tools. On every turn reply with one JSON object and nothing else:
{{"thought": "<short reasoning>", "action": "<tool name>", "args": {{...}}}}

Tools:
- submit_spec      args: {{"spec": <formal model>}}. Stores your formal model of the brief and validates it.
                   Fix every reported problem and submit again. The model format is described below.
- solve            args: {{"method": "milp" | "auction" | "hungarian", "time_limit_s": <number>}}.
                   Solves the stored model. "milp" is exact but slow for large missions; "auction" and
                   "hungarian" are fast heuristics. Returns status, makespan and the routes.
- check            args: {{}} or {{"plan": <plan>}}. Verifies the latest solution (or the given plan) against
                   the stored model and lists violated constraints.
- ask_human        args: {{"question": "<one precise question>"}}. Ask the mission owner when the brief is
                   ambiguous. The answer may be unavailable.
- finish           args: {{}} to submit the latest verified solution, or {{"plan": <plan>}}.

Suggested procedure: submit_spec, solve with "milp" (use "auction" first when there are more than 12 tasks,
then "milp" with a time limit if time allows), check, then finish. If the model is infeasible, re-read the
brief for misread numbers before relaxing anything; never drop a constraint the brief states.
You have at most {{max_steps}} tool calls.

Formal model format for submit_spec (the value of "spec"):
{SPEC_FIELDS}

Plan format (for check/finish with an explicit plan):
{{"standby": ["<robot id>"], "routes": {{"<robot id>": ["<task id>", ...]}}}}"""


def brief_message(brief: str) -> str:
    return f"MISSION BRIEF\n-------------\n{brief}"
