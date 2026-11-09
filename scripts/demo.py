#!/usr/bin/env python3
"""Command-line interface: brief -> method -> plan -> checker -> schedule (+ Gantt chart, + trace).

    python scripts/demo.py                                                 # agent + MockLLM on a benchmark scenario
    python scripts/demo.py --scenario data/benchmark/M-004.json --llm configs/models/qwen-local.yaml
    python scripts/demo.py --method agent:no-g2 --style explicit --llm configs/models/gemini-flash.yaml
    python scripts/demo.py --scenario data/benchmark/L-003.json --gantt results/figures/gantt_L-003.png
    python scripts/demo.py --scenario data/benchmark/S-007.json --interactive    # you answer ask_human

With --interactive the agent's clarification questions are shown to you and your typed answer is
passed back (instead of the simulated mission owner used in the experiments).
"""

import argparse
import json
from pathlib import Path

import _path  # noqa: F401
from mrta.checker import check
from mrta.evaluate import CLASSICAL, LLM, split_method
from mrta.gaps import MissionOwner
from mrta.llm import LLM_METHODS, make_client
from mrta.llm.agent import run_agent
from mrta.llm.client import load_dotenv
from mrta.scenarios import generate, render
from mrta.schema import Scenario
from mrta.solvers import SOLVERS

# Reference categorical palette (dataviz skill), slots 1-4 in fixed order, one per robot type.
TYPE_COLORS = {"quadcopter": "#2a78d6", "rover": "#eb6834", "mobile_manipulator": "#1baf7a", "fixed_arm": "#eda100"}
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def gantt(scn: Scenario, schedule, title: str, path: Path, standby: list[str] | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Patch

    robots = list(scn.robots)
    types = {r.id: r.type for r in robots}
    fig, ax = plt.subplots(figsize=(10, 0.55 * len(robots) + 1.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    rows = {r.id: i for i, r in enumerate(reversed(robots))}
    for v in schedule.visits:
        y = rows[v.robot]
        depart = v.arrive - v.travel
        if v.travel > 0:
            ax.plot([depart, v.arrive], [y, y], color=INK_2, lw=1.2, alpha=0.6, solid_capstyle="butt")
        if v.start > v.arrive + 0.5:  # waiting for a predecessor
            ax.plot([v.arrive, v.start], [y, y], color=INK_2, lw=1.2, ls=(0, (1, 2)), alpha=0.8)
        width = v.finish - v.start
        ax.add_patch(FancyBboxPatch((v.start, y - 0.28), width, 0.56, boxstyle="round,pad=0,rounding_size=4",
                                    mutation_aspect=0.02, facecolor=TYPE_COLORS.get(types[v.robot], "#888888"),
                                    edgecolor=SURFACE, linewidth=2))
        ax.text(v.start + width / 2, y, v.task, ha="center", va="center", fontsize=7.5, color=INK)
    busy = {v.robot for v in schedule.visits}
    for rid, y in rows.items():
        if rid not in busy:
            note = "standby" if rid in (standby or []) else "idle"
            ax.text(schedule.makespan * 0.01, y, note, va="center", fontsize=8, color=INK_2, style="italic")
    makespan = schedule.makespan
    ax.axvline(makespan, color=INK_2, lw=1, ls="--")
    ax.text(makespan, len(robots) - 0.35, f" makespan {makespan:.0f} s", color=INK_2, fontsize=8, va="bottom")
    ax.set_yticks(list(rows.values()))
    ax.set_yticklabels([f"{rid} ({types[rid].replace('_', ' ')})" for rid in rows], fontsize=8, color=INK_2)
    ax.set_xlabel("time (s)", fontsize=8, color=INK_2)
    ax.tick_params(axis="x", labelsize=8, colors=INK_2)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_xlim(0, makespan * 1.08 + 1)
    ax.set_ylim(-0.7, len(robots) - 0.2)
    present = [t for t in TYPE_COLORS if t in set(types.values())]
    handles = [Patch(facecolor=TYPE_COLORS[t], label=t.replace("_", " ")) for t in present]
    handles += [plt.Line2D([], [], color=INK_2, lw=1.2, alpha=0.6, label="travel"),
                plt.Line2D([], [], color=INK_2, lw=1.2, ls=(0, (1, 2)), label="waiting")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=len(handles), frameon=False,
              fontsize=8, labelcolor=INK_2)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="data/benchmark/M-004.json", help="benchmark JSON ('' = generate one)")
    ap.add_argument("--method", default="agent:full", choices=list(CLASSICAL) + list(LLM) + ["agent"])
    ap.add_argument("--style", default="informal", choices=["explicit", "informal"])
    ap.add_argument("--llm", default="mock", help="mock, mock-oracle, or a model config such as "
                                                  "configs/models/qwen-local.yaml")
    ap.add_argument("--gantt", default="", help="save a Gantt chart PNG here")
    ap.add_argument("--trace", default="", help="save the agent trajectory (JSONL) here")
    ap.add_argument("--interactive", action="store_true", help="answer the agent's questions yourself")
    args = ap.parse_args()
    load_dotenv()

    if args.scenario and Path(args.scenario).exists():
        rec = json.loads(Path(args.scenario).read_text())
        scn, brief = Scenario.model_validate(rec["scenario"]), rec["briefs"][args.style]
    else:
        scn = generate("M", 4, version=2)
        brief = render(scn, args.style)

    print(f"=== {scn.id}: {len(scn.robots)} robots, {len(scn.tasks)} tasks ({args.style} brief) ===")
    print(brief[:900] + ("\n..." if len(brief) > 900 else ""))

    if args.method in CLASSICAL:
        res = SOLVERS[args.method](scn)
        plan, label, extra = res.plan, args.method, f"status={res.status}, {res.time_s:.2f}s"
    else:
        client = make_client(args.llm)
        base, preset = split_method(args.method)
        if base == "agent":
            if args.interactive:
                def human(q: str) -> str:
                    return input(f"\nAgent asks: {q}\nYour answer> ")
            else:
                human = MissionOwner(scn)
            res = run_agent(client, scn, brief, args.style, config=preset, human=human)
        else:
            res = LLM_METHODS[base](client, scn, brief, args.style)
        plan, label = res.plan, f"{args.method} [{client.config.name}]"
        extra = (f"status={res.status}, {res.llm_calls} LLM calls, {res.prompt_tokens}+{res.completion_tokens} "
                 f"tokens, {res.time_s:.2f}s")
        steps = [ev for ev in res.trace if ev.get("type") == "step"]
        if steps:
            print("\nAgent steps:")
            for ev in steps:
                print(f"  {ev['step']:>2}: {ev['action']:<12} {ev['thought'][:70]!r}")
                print(f"      -> {json.dumps(ev['observation'], default=str)[:160]}")
        if args.trace:
            Path(args.trace).parent.mkdir(parents=True, exist_ok=True)
            Path(args.trace).write_text("".join(json.dumps(ev, default=str) + "\n" for ev in res.trace))
            print(f"\ntrajectory saved to {args.trace}")
        if args.llm.startswith("mock"):
            print("\n(MockLLM is a stand-in for testing the pipeline, not a language model.)")

    print(f"\n=== {label}: {extra} ===")
    if plan is None:
        print("no plan produced")
        return
    for rid, tids in plan.routes.items():
        print(f"  {rid:4} -> {' -> '.join(tids)}")
    if plan.standby:
        print(f"  standby: {', '.join(plan.standby)}")

    result = check(scn, plan)
    print(f"\nChecker: {'FEASIBLE' if result.feasible else 'INFEASIBLE'}")
    for v in result.violations:
        print(f"  [{v.kind}] {v.detail}")
    optimum = SOLVERS["milp"](scn)
    opt = check(scn, optimum.plan).schedule.makespan if optimum.plan else None
    if result.schedule:
        mk = result.schedule.makespan
        gap = f", gap to MILP {100 * (mk - opt) / opt:.1f}%" if opt and result.feasible else ""
        print(f"Makespan {mk:.0f} s (MILP {opt:.0f} s{gap})" if opt else f"Makespan {mk:.0f} s")
        print("\n  robot task  arrive   start  finish")
        for v in sorted(result.schedule.visits, key=lambda v: v.start):
            print(f"  {v.robot:5} {v.task:5} {v.arrive:6.0f}  {v.start:6.0f}  {v.finish:6.0f}")
        if args.gantt:
            title = f"{scn.id} - {label} - {'feasible' if result.feasible else 'INFEASIBLE'}"
            gantt(scn, result.schedule, title, Path(args.gantt), plan.standby)
            print(f"\nGantt chart saved to {args.gantt}")


if __name__ == "__main__":
    main()
