"""Small edit language so the agent can fix single facts of a formal model without retyping it.

    {"op": "set", "target": "task:T3", "field": "deadline", "value": 1800}
    {"op": "set", "target": "robot:D1", "field": "capabilities", "value": ["camera", "carry"]}
    {"op": "set", "target": "location:pump house", "field": "zone", "value": "indoor"}
    {"op": "set", "target": "model", "field": "horizon", "value": 7200}
    {"op": "add_precedence", "before": "T1", "after": "T4"}
    {"op": "remove_precedence", "before": "T1", "after": "T4"}
    {"op": "set_standby", "robot_type": "quadcopter", "count": 1}
    {"op": "clear_standby"}
    {"op": "remove", "target": "task:T9"}
    {"op": "add", "target": "robot", "value": {"id": "D3", ...}}      (also add_robot / add_task / add_location)

Edits work on the plain-dict form of a model and return (new_dict, problems).
"""

from __future__ import annotations

import copy
from typing import Any

_SECTIONS = {"task": ("tasks", "id"), "robot": ("robots", "id"), "location": ("locations", "name")}


def _find(model: dict[str, Any], target: str) -> tuple[list, int] | None:
    kind, _, key = str(target).partition(":")
    sec = _SECTIONS.get(kind.strip().lower())
    if sec is None:
        return None
    items = model.setdefault(sec[0], [])
    key_l = key.strip().lower()
    for i, item in enumerate(items):
        if str(item.get(sec[1], "")).strip().lower() == key_l:
            return items, i
    return None


def apply_edits(model: dict[str, Any], edits: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    out = copy.deepcopy(model)
    problems: list[str] = []
    if not isinstance(edits, list):
        return out, ["edits must be a list"]
    for n, ed in enumerate(edits[:60]):
        if not isinstance(ed, dict):
            problems.append(f"edit {n}: not an object")
            continue
        op = str(ed.get("op", "set")).lower()
        if op.startswith("add_") and op[4:] in _SECTIONS:  # add_robot, add_task, add_location
            ed = {**ed, "target": op[4:]}
            op = "add"
        try:
            if op == "add":
                kind = str(ed.get("target", "")).partition(":")[0].strip().lower()
                value = ed.get("value") if isinstance(ed.get("value"), dict) else \
                    {k: v for k, v in ed.items() if k not in ("op", "target")}
                if kind not in _SECTIONS:
                    problems.append(f"edit {n}: add needs target robot, task or location")
                    continue
                sec, key = _SECTIONS[kind]
                if _find(out, f"{kind}:{value.get(key, '')}") is not None:
                    problems.append(f"edit {n}: {kind} {value.get(key)} already exists; use set")
                    continue
                out.setdefault(sec, []).append(dict(value))
            elif op == "set":
                target, fld = str(ed.get("target", "")), str(ed.get("field", ""))
                if target.strip().lower() == "model":
                    out[fld] = ed.get("value")
                    continue
                hit = _find(out, target)
                if hit is None:
                    kind = target.partition(":")[0]
                    if kind in _SECTIONS and fld == _SECTIONS[kind][1]:
                        problems.append(f"edit {n}: unknown {target}")
                        continue
                    if kind in _SECTIONS:  # create the item with this one field set
                        sec, key = _SECTIONS[kind]
                        out.setdefault(sec, []).append({key: target.partition(":")[2].strip(), fld: ed.get("value")})
                        continue
                    problems.append(f"edit {n}: unknown target '{target}'")
                    continue
                items, i = hit
                items[i][fld] = ed.get("value")
            elif op == "remove":
                hit = _find(out, str(ed.get("target", "")))
                if hit is None:
                    problems.append(f"edit {n}: unknown target '{ed.get('target')}'")
                    continue
                items, i = hit
                items.pop(i)
            elif op == "add_precedence":
                pair = {"before": str(ed["before"]), "after": str(ed["after"])}
                if pair not in out.setdefault("precedence", []):
                    out["precedence"].append(pair)
            elif op == "remove_precedence":
                pair = {"before": str(ed["before"]), "after": str(ed["after"])}
                out["precedence"] = [p for p in out.get("precedence", []) if p != pair]
            elif op == "set_standby":
                rest = [s for s in out.get("standby", []) if s.get("robot_type") != ed.get("robot_type")]
                count = int(ed.get("count", 1))
                out["standby"] = rest + ([{"robot_type": ed.get("robot_type"), "count": count}] if count > 0 else [])
            elif op == "clear_standby":
                out["standby"] = []
            else:
                problems.append(f"edit {n}: unknown op '{op}'")
        except (KeyError, TypeError, ValueError) as err:
            problems.append(f"edit {n}: {type(err).__name__}: {err}")
    return out, problems
