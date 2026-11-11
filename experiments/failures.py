#!/usr/bin/env python3
"""Failure analysis (brief, section 4.3): group failed runs into categories, count them, and render
representative success and failure trajectories.

    python experiments/failures.py --model qwen2.5-7b
    python experiments/failures.py --model qwen2.5-7b --methods agent:full,llm-pefa

Writes results/failures/<model>/categories.{csv,md} and results/failures/<model>/cases/*.md

Category of a failed run (first rule that applies):
  F1 protocol / format   no usable plan: unparsable replies, invalid model, step budget, API error
  F2 timing misread      ground-truth violation is a deadline or the horizon
  F3 precedence misread  ground-truth violation is a deadlock / ordering problem
  F4 requirement misread ground-truth violation is a capability, payload or reachability problem
  F5 standby missed      ground-truth violation is the standby rule
  F6 endurance misread   ground-truth violation is a robot's endurance
  F7 incomplete plan     a task is missing, duplicated or has an unknown id
  F8 planning error      the agent's model was correct (F1 = 1) yet the plan is infeasible
For F2-F6 the misread facts are listed from the comparison of the agent's model with the ground truth.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mrta.schema import Scenario  # noqa: E402
from mrta.spec_eval import facts  # noqa: E402

CATS = {
    "F1": "protocol / format (no usable plan)", "F2": "timing misread (deadline / horizon)",
    "F3": "precedence misread", "F4": "requirement or capability misread", "F5": "standby rule missed",
    "F6": "endurance misread", "F7": "incomplete plan (missing / duplicate task)",
    "F8": "planning error with a correct model",
}
VIOL = [("deadline", "F2"), ("horizon", "F2"), ("deadlock", "F3"), ("capability", "F4"), ("payload", "F4"),
        ("unreachable", "F4"), ("standby", "F5"), ("standby_conflict", "F5"), ("endurance", "F6"),
        ("missing", "F7"), ("duplicate", "F7"), ("unknown_task", "F7"), ("unknown_robot", "F7")]


def categorise(row: pd.Series) -> str:
    if row["status"] in ("parse_error", "no_plan", "error", "spec_invalid", "solver_failed"):
        return "F1"
    try:
        viol = json.loads(row["violations"]) if isinstance(row["violations"], str) else {}
    except ValueError:
        viol = {}
    if not viol:
        return "F1"
    if row.get("spec_f1") == 1.0:
        return "F8"
    for kind, cat in VIOL:
        if viol.get(kind):
            return cat
    return "F7"


def fact_diff(spec: dict | None, truth: Scenario) -> list[str]:
    if not spec:
        return []
    try:
        mine = facts(Scenario.model_validate(spec))
    except Exception:  # noqa: BLE001
        return ["model did not validate"]
    gt = facts(truth)
    out = [f"missed or wrong: {f[0]} {f[1]} = {f[2]}" for f in sorted(gt - mine, key=str)]
    out += [f"invented: {f[0]} {f[1]} = {f[2]}" for f in sorted(mine - gt, key=str)]
    return out


LOGS = ROOT / "logs"


def load_trace(model: str, row: pd.Series) -> list[dict]:
    path = LOGS / model / row["exp"] / f"{row['run_id']}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def render_case(model: str, row: pd.Series, truth: Scenario, title: str) -> str:
    events = load_trace(model, row)
    head = events[0] if events and events[0].get("type") == "run" else {}
    lines = [f"# {title}", "",
             f"- run: `{row['run_id']}` (model {model}, seed {row.get('seed')})",
             f"- scenario {row['scenario']} (tier {row['tier']}), {row['input']} brief",
             f"- result: feasible = {row['feasible']}, makespan = {row['makespan']}, status = {row['status']}, "
             f"violations = {row['violations']}",
             f"- LLM calls {row['llm_calls']}, tokens {int(row['prompt_tokens'])} + {int(row['completion_tokens'])}, "
             f"time {row['plan_time_s']:.1f} s", ""]
    diffs = fact_diff(head.get("spec"), truth)
    if diffs:
        lines += ["## Model facts that differ from the ground truth", ""] + [f"- {d}" for d in diffs[:15]] + [""]
    lines += ["## Trajectory", ""]
    for ev in events:
        if ev.get("type") == "step":
            obs = json.dumps(ev.get("observation"), default=str)
            args = json.dumps(ev.get("args"), default=str)
            lines.append(f"**Step {ev['step']}: `{ev['action']}`** - {ev.get('thought', '')}")
            lines.append(f"- args: `{args[:300]}{'...' if len(args) > 300 else ''}`")
            lines.append(f"- observation: `{obs[:500]}{'...' if len(obs) > 500 else ''}`")
            lines.append(f"- state after: `{json.dumps(ev.get('state'))}`")
            lines.append("")
        elif ev.get("type") == "llm" and row["method"] != "agent":
            lines.append(f"**LLM call ({ev.get('purpose')})**: `{str(ev.get('reply', ''))[:400]}`")
            lines.append("")
    if head.get("plan"):
        lines += ["## Final plan", "", "```json", json.dumps(head["plan"].get("routes"), indent=1), "```", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="model folder under results/runs/")
    ap.add_argument("--methods", default="agent:full,llm-pefa,llm-formulate,llm-direct")
    ap.add_argument("--runs", default="results/runs")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--out", default="results/failures")
    ap.add_argument("--repeats", type=int, default=0, help="use only repeats 0..N-1 (0 = all)")
    args = ap.parse_args()
    global LOGS
    LOGS = ROOT / args.logs
    d = ROOT / args.runs / args.model
    frames = [pd.read_csv(f) for f in sorted(d.glob("E*.csv"))]
    if not frames:
        sys.exit(f"no runs in {d}")
    df = pd.concat(frames, ignore_index=True)
    df = df[df["split"] == "test"]
    if args.repeats:
        df = df[df["repeat"].astype(int) < args.repeats]
    df["key"] = df["method"] + df["config"].fillna("").map(lambda c: f":{c}" if c else "")
    df["feasible"] = df["feasible"].astype(str) == "True"
    truth = {}
    for f in (ROOT / "data" / "benchmark").glob("*-*.json"):
        rec = json.loads(f.read_text())
        truth[rec["scenario"]["id"]] = Scenario.model_validate(rec["scenario"])

    out = ROOT / args.out / args.model
    (out / "cases").mkdir(parents=True, exist_ok=True)
    rows = []
    for k in args.methods.split(","):
        s = df[df.key == k]
        if not len(s):
            continue
        fails = s[~s.feasible].copy()
        fails["category"] = fails.apply(categorise, axis=1)
        cnt = Counter(fails["category"])
        rec = {"Method": k, "Runs": len(s), "Failures": len(fails), "Failure %": f"{100 * len(fails) / len(s):.1f}"}
        for c in CATS:
            rec[c] = cnt.get(c, 0)
        rows.append(rec)
        if k == "agent:full":
            fails.to_csv(out / "agent_full_failures.csv", index=False)
            # one failure from each of the two most frequent categories (two from one if only one exists)
            order = [c for c, _ in Counter(fails["category"]).most_common()]
            picked = []
            for c in order:
                g = fails[fails.category == c].sort_values(["tier", "scenario"], ascending=[False, True])
                picked.append(g.iloc[0])
                if len(picked) == 2:
                    break
            if len(picked) == 1 and (fails.category == order[0]).sum() > 1:
                picked.append(fails[fails.category == order[0]].sort_values(["tier", "scenario"]).iloc[-1])
            for i, row in enumerate(picked, 1):
                (out / "cases" / f"failure_{i}_{row['category']}.md").write_text(
                    render_case(args.model, row, truth[row["scenario"]],
                                f"Failure case {i}: {CATS[row['category']]}"))
            succ = s[s.feasible].sort_values(["tier", "input"], ascending=[False, False])
            for i, (_, row) in enumerate(succ.head(2).iterrows(), 1):
                (out / "cases" / f"success_{i}.md").write_text(
                    render_case(args.model, row, truth[row["scenario"]], f"Success case {i}"))
    t = pd.DataFrame(rows)
    t.to_csv(out / "categories.csv", index=False)
    legend = "\n".join(f"- **{c}** {v}" for c, v in CATS.items())
    (out / "categories.md").write_text(f"**Failure categories on the test split ({args.model})**\n\n"
                                       + (t.to_markdown(index=False) if len(t) else "_no runs_") + "\n\n" + legend + "\n")
    print((out / "categories.md").read_text())
    print(f"cases -> {out / 'cases'}")


if __name__ == "__main__":
    main()
