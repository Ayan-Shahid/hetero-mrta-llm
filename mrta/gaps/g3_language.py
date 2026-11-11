"""Gap 3 component: robust model extraction from informal language.

Gap 3 is that LLM planners are tested on explicit commands, and fall apart on the kind
of brief a site manager writes (LiP-LLM drops from 0.93 to 0.44 success on ambiguous
instructions). This component has two parts:

1. Staged extraction (``draft_model`` tool): three focused LLM passes, each with a small
   output and a reading guide - (a) map, fleet and horizon, (b) tasks and their
   requirements, (c) deadlines, precedence and standby - assembled into one model and
   validated. Each pass only has to get one kind of fact right.
2. Targeted clarification (``ask_human`` tool): the agent may ask the mission owner up to
   ``max_questions`` precise questions. The owner is simulated by :class:`MissionOwner`,
   which answers only with the lines of the explicit brief that mention the task, robot or
   rule the question names (what a human who wrote the mission would say), so it cannot
   answer vague questions and never volunteers facts that were not asked about.

3. Grounding check (inside ``draft_model``): every deadline, precedence edge and standby rule
   in the draft must be supported by the brief's text - the deadline by a time-limit phrase in
   that task's own sentence, a precedence edge by the earlier task's id in the later task's
   sentence, standby by the word "standby". Unsupported facts are removed and reported.
   Added after the first pilot, where invented deadlines and standby rules were the most
   common extraction errors (docs/devlog.md). It assumes, as in every benchmark brief, that
   each task's sentence names its id.

Switches: AgentConfig.g3_staged, AgentConfig.g3_clarify.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..scenarios.nl import render_explicit
from ..schema import Scenario
from .g2_verify import sanity_problems


def _stage(meter, system: str, brief: str, purpose: str, context: dict[str, Any]) -> tuple[dict | None, str]:
    from ..llm.client import parse_json
    from ..llm.prompts import brief_message

    messages = [{"role": "system", "content": system}, {"role": "user", "content": brief_message(brief)}]
    resp = meter.chat(messages, purpose=purpose, context=context)
    try:
        data = parse_json(resp.text)
        return (data if isinstance(data, dict) else None), ""
    except ValueError as err:
        return None, f"{purpose}: {err}"


_DEADLINE_CUE = re.compile(r"deadline|within the first|finished by|no later than|at the latest|has to be (?:done|"
                           r"finished|completed) (?:by|within)", re.I)
_ORDER_CUE = re.compile(r"after|once|only when|until|before|is done|finished|arrived|first", re.I)


def task_sentences(brief: str, task_ids: list[str]) -> dict[str, str]:
    """The text that describes each task: the line (or sentence) whose first task id is that task."""
    out: dict[str, str] = {}
    ids = {t.lower(): t for t in task_ids}
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in task_ids) + r")\b", re.I) if task_ids else None
    if pattern is None:
        return out
    for line in brief.splitlines():
        for chunk in re.split(r"(?<=[.;])\s+(?=\(?T\d)", line):
            m = pattern.search(chunk)
            if m:
                tid = ids[m.group(1).lower()]
                out[tid] = (out.get(tid, "") + " " + chunk).strip()
    return out


def ground_in_brief(draft: dict[str, Any], brief: str) -> tuple[dict[str, Any], list[str]]:
    """Remove deadlines, precedence edges and standby rules that the brief's text does not support."""
    notes: list[str] = []
    tasks = [t for t in draft.get("tasks", []) if isinstance(t, dict)]
    ids = [str(t.get("id")) for t in tasks if t.get("id")]
    text = task_sentences(brief, ids)
    for t in tasks:
        tid = str(t.get("id"))
        if t.get("deadline") is not None and tid in text and not _DEADLINE_CUE.search(text[tid]):
            notes.append(f"removed the deadline of {tid}: its sentence in the brief states no time limit")
            t["deadline"] = None
    kept = []
    for p in draft.get("precedence", []) or []:
        if not isinstance(p, dict):
            continue
        before, after = str(p.get("before")), str(p.get("after"))
        line = text.get(after, "")
        if line and not (re.search(rf"\b{re.escape(before)}\b", line, re.I) and _ORDER_CUE.search(line)):
            notes.append(f"removed '{before} before {after}': the brief's sentence for {after} does not mention {before}")
            continue
        kept.append(p)
    draft["precedence"] = kept
    if draft.get("standby") and "standby" not in brief.lower() and "stand by" not in brief.lower():
        notes.append("removed the standby rule: the brief does not mention standby")
        draft["standby"] = []
    return draft, notes


def staged_extraction(meter, brief: str, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Run the three passes and return (draft model as a dict, problems)."""
    from ..llm.methods import clean_spec
    from ..llm.prompts import STAGE_CONSTRAINTS, STAGE_FLEET, STAGE_TASKS

    problems: list[str] = []
    fleet, err = _stage(meter, STAGE_FLEET, brief, "stage_fleet", context)
    if fleet is None:
        return {}, [err or "stage 1 returned no object"]
    names = [str(loc.get("name")) for loc in fleet.get("locations", []) if isinstance(loc, dict)]
    robots = [f"{r.get('id')} ({r.get('type')})" for r in fleet.get("robots", []) if isinstance(r, dict)]
    ctx_text = json.dumps({"locations": names, "robots": robots})
    tasks_out, err = _stage(meter, STAGE_TASKS.replace("{context}", ctx_text), brief, "stage_tasks", context)
    if tasks_out is None:
        return {"locations": fleet.get("locations", []), "robots": fleet.get("robots", [])}, [err]
    tasks = [t for t in tasks_out.get("tasks", []) if isinstance(t, dict)]
    ids = [str(t.get("id")) for t in tasks]
    cons, err = _stage(meter, STAGE_CONSTRAINTS.replace("{task_ids}", ", ".join(ids)), brief,
                       "stage_constraints", context)
    if cons is None:
        problems.append(err)
        cons = {}
    deadlines = cons.get("deadlines") or {}
    if isinstance(deadlines, list):  # [{"task": .., "deadline": ..}]
        deadlines = {str(d.get("task") or d.get("id")): d.get("deadline") for d in deadlines if isinstance(d, dict)}
    dl = {str(k).strip().lower(): v for k, v in deadlines.items()}
    for t in tasks:
        t["deadline"] = dl.get(str(t.get("id")).strip().lower())
    draft = {"id": "draft", "horizon": cons.get("horizon") or fleet.get("horizon"),
             "locations": fleet.get("locations", []), "robots": fleet.get("robots", []), "tasks": tasks,
             "precedence": cons.get("precedence") or [], "standby": cons.get("standby") or []}
    draft, notes = ground_in_brief(draft, brief)
    problems += [f"note: {n}" for n in notes]
    try:
        draft = clean_spec(draft)
        spec = Scenario.model_validate(draft)
        problems += sanity_problems(spec)
    except Exception as err:  # noqa: BLE001 - report, the agent repairs with edits
        problems.append(str(err)[:1200])
    return draft, problems


class MissionOwner:
    """Simulated mission owner who answers from the explicit brief (see module docstring)."""

    def __init__(self, scn: Scenario, max_questions: int = 2):
        self.lines = render_explicit(scn).splitlines()
        self.robot_ids = [r.id for r in scn.robots]
        self.task_ids = [t.id for t in scn.tasks]
        self.locations = [loc.name for loc in scn.locations]
        self.max_questions = max_questions
        self.asked = 0

    def __call__(self, question: str) -> str:
        self.asked += 1
        if self.asked > self.max_questions:
            return "No more questions, please; go with your best reading of the brief."
        q = question.lower()
        keys: list[str] = []
        for tid in self.task_ids:
            if re.search(rf"\b{re.escape(tid.lower())}\b", q):
                keys.append(f"- {tid}:")
        for rid in self.robot_ids:
            if re.search(rf"\b{re.escape(rid.lower())}\b", q):
                keys.append(f"- {rid} (")
        for loc in self.locations:
            if loc.lower() != "base" and loc.lower() in q:
                keys.append(f"- {loc}:")
        rules = []
        if "standby" in q:
            rules.append("on standby")
        if "horizon" in q or "wrapped up" in q or "overall" in q:
            rules.append("Every task must be finished by")
        found = [ln for ln in self.lines if any(ln.startswith(k) for k in keys) or any(r in ln for r in rules)]
        if not found:
            return "I'm not sure what you mean. Ask about a specific task, robot or place by its name."
        return " ".join(found[:3])
