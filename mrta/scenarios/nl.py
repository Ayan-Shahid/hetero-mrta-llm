"""Render a Scenario as a natural-language mission brief.

Two styles carry exactly the same information:

* explicit  - structured brief: every capability, unit and constraint is spelled out;
* informal  - conversational brief: task requirements are implied ("have a look at the
              roof" needs a camera and a drone), times are in minutes, and constraints
              are phrased the way a site manager would say them.

The informal style tests whether an LLM can recover the formal model from the
kind of description a human would actually give.
"""

from __future__ import annotations

import random

from ..catalog import CAPABILITY_TEXT, TASK_KINDS
from ..schema import Mobility, Robot, Scenario, Task, Zone


def num(x: float) -> str:
    return f"{x:g}"


def minutes(sec: float) -> str:
    m = sec / 60
    return f"{m:g} minute" if m == 1 else f"{m:g} minutes"


def _item(task: Task) -> str | None:
    if task.kind != "deliver":
        return None
    # description: "Deliver the {item} ({w} kg) to {loc}"
    text = task.description
    if text.startswith("Deliver the ") and " (" in text:
        return text[len("Deliver the "):text.index(" (")]
    return "package"


# --------------------------------------------------------------------------- explicit
def render_explicit(scn: Scenario) -> str:
    lines = [f"MISSION BRIEF {scn.id}",
             "Units: metres, seconds, kilograms. Time starts at t = 0 when every robot leaves its start location.",
             "", "Locations (x, y, zone):"]
    for loc in scn.locations:
        lines.append(f"- {loc.name}: ({num(loc.x)}, {num(loc.y)}), {loc.zone.value}")

    lines += ["", "Robots:"]
    for r in scn.robots:
        lines.append("- " + _robot_explicit(r))

    preds = scn.predecessors()
    lines += ["", "Tasks:"]
    for t in scn.tasks:
        need = ", ".join(CAPABILITY_TEXT[c] for c in t.required)
        if t.payload:
            need += f" (payload {num(t.payload)} kg)"
        parts = [f"- {t.id}: {t.description}.", f"Location: {t.location}.", f"Duration: {num(t.duration)} s.",
                 f"Needs: {need}."]
        if t.deadline is not None:
            parts.append(f"Deadline: finished by t = {num(t.deadline)} s.")
        if preds.get(t.id):
            parts.append("Can start only after " + " and ".join(sorted(preds[t.id])) + " is finished.")
        lines.append(" ".join(parts))

    lines += ["", "Rules:",
              "1. Every task is done by exactly one robot. A robot does its tasks one at a time, in the order "
              "you list them, travelling between task locations.",
              "2. A robot can only do a task if it has the needed equipment, enough payload capacity, and can "
              "reach the task location.",
              "3. A task that must start after another task waits until that task is finished, even if a "
              "different robot does it.",
              "4. Each robot must be back at its start location before its endurance runs out."]
    k = 5
    for s in scn.standby:
        lines.append(f"{k}. Keep at least {s.count} {s.robot_type} on standby: it must not be given any task.")
        k += 1
    lines.append(f"{k}. Every task must be finished by t = {num(scn.horizon)} s.")
    lines += ["", "Objective: minimise the finish time of the last task (the makespan)."]
    return "\n".join(lines)


def _robot_explicit(r: Robot) -> str:
    equip = []
    for c in r.capabilities:
        equip.append(f"cargo carrier (max {num(r.payload)} kg)" if c == "carry" else CAPABILITY_TEXT[c])
    equip_s = ", ".join(equip)
    if r.mobility == Mobility.FIXED:
        return (f"{r.id} ({r.type}): installed at {r.start} and cannot move; it can only work on tasks located "
                f"at {r.start}. Equipment: {equip_s}. Mains-powered (no endurance limit).")
    if r.mobility == Mobility.AIR:
        move = (f"Flies in straight lines at {num(r.speed)} m/s; can reach outdoor and roof locations, "
                f"not indoor ones.")
    else:
        move = (f"Drives on the road grid (travel distance = |dx| + |dy|) at {num(r.speed)} m/s; can reach "
                f"outdoor and indoor locations, not roofs.")
    return (f"{r.id} ({r.type}): starts at {r.start}. {move} Equipment: {equip_s}. "
            f"Endurance: {num(r.endurance)} s, including the return to its start.")


# --------------------------------------------------------------------------- informal
_OPEN = ("Hi team, here is today's job at the site.", "Morning! Plan for today's site work:",
         "Here's what we need from the robots today.")
_ZONE = {Zone.ROOF: "a rooftop", Zone.INDOOR: "indoors", Zone.OUTDOOR: "outside"}


def render_informal(scn: Scenario, seed: str | int | None = None) -> str:
    rng = random.Random(f"informal-{scn.id}-{seed}")
    out = [rng.choice(_OPEN),
           "Positions are in metres from the base at (0, 0). Drones fly straight and can't go indoors; ground "
           "robots follow the road grid (so they travel |dx| + |dy|) and can't get onto roofs. Everyone leaves "
           "their start spot at time zero."]
    places = [f"{loc.name} ({num(loc.x)}, {num(loc.y)}, {_ZONE[loc.zone]})"
              for loc in scn.locations if loc.name != "base"]
    out.append("Places: " + "; ".join(places) + ".")

    out.append("The fleet: " + " ".join(_robot_informal(r, rng) for r in scn.robots))

    preds = scn.predecessors()
    tasks = {t.id: t for t in scn.tasks}
    jobs = []
    for t in scn.tasks:
        kind = TASK_KINDS[t.kind]
        phrase = rng.choice(kind.informal).format(loc=t.location, item=_item(t), w=num(t.payload))
        bits = [f"({t.id}) {phrase[0].upper() + phrase[1:]}"]
        bits.append(f"- that takes {minutes(t.duration)} on site")
        for p in sorted(preds.get(t.id, [])):
            pt = tasks[p]
            if pt.kind == "deliver":
                bits.append(f"but only once the {_item(pt)} ({p}) has arrived")
            else:
                bits.append(f"but only once job {p} is done")
        if t.deadline is not None:
            bits.append(f"and it has to be finished within the first {minutes(t.deadline)}")
        jobs.append(" ".join(bits) + ".")
    out.append("Jobs:\n" + "\n".join(jobs))

    closing = []
    for s in scn.standby:
        count = "one" if s.count == 1 else str(s.count)
        noun = s.robot_type.replace("_", " ") + ("" if s.count == 1 else "s")
        closing.append(f"Keep {count} {noun} on standby; don't give it any jobs.")
    closing.append(f"Everything has to be wrapped up within {minutes(scn.horizon)} of the start, and the sooner "
                   "the last job finishes, the better. Every robot has to make it back to where it started "
                   "before its battery runs out.")
    out.append(" ".join(closing))
    return "\n\n".join(out)


def _robot_informal(r: Robot, rng: random.Random) -> str:
    has = set(r.capabilities)
    if r.type == "fixed_arm":
        return (f"{r.id} is a robot arm bolted down in {r.start}; it can't move but can work on jobs inside "
                f"{r.start}, and it runs on mains power.")
    life = minutes(r.endurance)
    if r.type == "quadcopter":
        cams = "a camera and a thermal camera" if "thermal" in has else "a camera"
        return rng.choice((
            f"{r.id} is a quadcopter at {r.start} with {cams}; it flies at {num(r.speed)} m/s, can lift "
            f"{num(r.payload)} kg, and its battery lasts {life} including the flight home.",
            f"{r.id}, a quadcopter waiting at {r.start}, carries {cams}, cruises at {num(r.speed)} m/s, lifts up "
            f"to {num(r.payload)} kg and has {life} of flight time, return trip included.",
        ))
    if r.type == "rover":
        extra = []
        if "camera" in has:
            extra.append("a camera")
        if "thermal" in has:
            extra.append("a thermal camera")
        gear = (" It has " + " and ".join(extra) + ".") if extra else ""
        return (f"{r.id} is a rover parked at {r.start} that hauls up to {num(r.payload)} kg at {num(r.speed)} m/s "
                f"and runs for {life} including the drive back.{gear}")
    # mobile manipulator
    return (f"{r.id} is our mobile manipulator at {r.start}: gripper arm, camera, room for {num(r.payload)} kg, "
            f"moves at {num(r.speed)} m/s and lasts {life} including getting back.")


def render(scn: Scenario, style: str) -> str:
    if style == "explicit":
        return render_explicit(scn)
    if style == "informal":
        return render_informal(scn)
    raise ValueError(f"unknown style {style!r}")


STYLES = ("explicit", "informal")
