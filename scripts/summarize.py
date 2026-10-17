#!/usr/bin/env python3
"""Aggregate results/runs.csv into a Markdown table (results/summary.md).

Gap to optimal uses the MILP makespan when the MILP proved optimality for that
scenario; otherwise the best feasible makespan any method found ("best known").
Gaps are averaged over feasible plans only, so always read them next to the
feasibility rate.

    python scripts/summarize.py --runs results/runs.csv --out results/summary.md
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

import _path  # noqa: F401

TIER_ORDER = {"S": 0, "M": 1, "L": 2}
METHOD_ORDER = {m: i for i, m in enumerate(["milp", "hungarian", "auction", "llm-direct", "llm-pefa",
                                            "llm-formulate", "agent"])}


def _f(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default="results/runs.csv")
    ap.add_argument("--out", default="results/summary.md")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.runs)))
    if not rows:
        raise SystemExit("no rows")

    ref: dict[str, tuple[float, str]] = {}
    for r in rows:
        if r["method"] == "milp" and r["status"] == "optimal" and r["feasible"] == "True":
            ref[r["scenario"]] = (float(r["makespan"]), "optimal")
    for r in rows:
        mk = _f(r["makespan"])
        if r["feasible"] == "True" and mk is not None and ref.get(r["scenario"], ("", ""))[1] != "optimal":
            if r["scenario"] not in ref or mk < ref[r["scenario"]][0]:
                ref[r["scenario"]] = (mk, "best known")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        label = r["method"] if not r["provider"] else f"{r['method']} [{r['provider']}:{r['model']}]"
        groups[(r["tier"], label, r["method"], r["input"])].append(r)

    lines = ["# Results summary", ""]
    if any(r["provider"] == "mock" for r in rows):
        lines += ["> **Rows marked `mock` come from the stand-in MockLLM, not a real language model. They only show "
                  "that the pipeline runs end to end; do not report them as LLM results.**", ""]
    n_opt = sum(1 for v in ref.values() if v[1] == "optimal")
    lines += [f"Reference makespan: MILP optimum for {n_opt} of {len(ref)} scenarios, best known otherwise. "
              "Gap is computed over feasible plans only.", "",
              "| Tier | Method | Input | Runs | Feasible | Mean gap | Median gap | Mean plan time | "
              "LLM calls | Tokens (in/out) | Model F1 |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key in sorted(groups, key=lambda k: (TIER_ORDER.get(k[0], 9), METHOD_ORDER.get(k[2], 99), k[1], k[3])):
        tier, label, _, inp = key
        rs = groups[key]
        feas = [r for r in rs if r["feasible"] == "True"]
        gaps = [100 * (float(r["makespan"]) - ref[r["scenario"]][0]) / ref[r["scenario"]][0]
                for r in feas if r["scenario"] in ref and ref[r["scenario"]][0] > 0]
        times = [float(r["plan_time_s"]) for r in rs]
        calls = [float(r["llm_calls"] or 0) for r in rs]
        tin = [float(r["prompt_tokens"] or 0) for r in rs]
        tout = [float(r["completion_tokens"] or 0) for r in rs]
        f1 = [x for x in (_f(r.get("spec_f1", "")) for r in rs) if x is not None]
        gap_mean = f"{statistics.mean(gaps):.1f}%" if gaps else "-"
        gap_median = f"{statistics.median(gaps):.1f}%" if gaps else "-"
        f1_mean = f"{statistics.mean(f1):.2f}" if f1 else "-"
        lines.append(f"| {tier} | {label} | {inp} | {len(rs)} | {100 * len(feas) / len(rs):.0f}% | {gap_mean} | "
                     f"{gap_median} | {statistics.mean(times):.2f} s | {statistics.mean(calls):.1f} | "
                     f"{statistics.mean(tin):.0f}/{statistics.mean(tout):.0f} | {f1_mean} |")

    viol: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["feasible"] != "True" and r["violations"] not in ("", "{}"):
            label = r["method"] if not r["provider"] else f"{r['method']} [{r['provider']}]"
            for kind, n in json.loads(r["violations"]).items():
                viol[f"{label} / {r['input']}"][kind] += int(n)
    if viol:
        lines += ["", "## Violations in infeasible plans", "", "| Method / input | Violations (count) |", "|---|---|"]
        for k in sorted(viol):
            lines.append(f"| {k} | " + ", ".join(f"{kind} {n}" for kind, n in sorted(viol[k].items())) + " |")

    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
