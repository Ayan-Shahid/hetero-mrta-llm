#!/usr/bin/env python3
"""Difficulty and bias statistics for the benchmark (supervisor feedback: "explain how you make
sure the scenarios are not too easy or biased toward your agent").

    python scripts/benchmark_report.py           # -> results/benchmark/difficulty.csv, results/benchmark/report.md

Per scenario it reports
* size: robots, tasks, deadlines, precedence edges, standby rules;
* eligibility: how many robots could do each task (fewer = more constrained);
* deadline slack: deadline / (fastest possible finish of that task alone);
* random-plan feasibility: share of 300 random plans (each task to a random eligible robot, random
  order, standby honoured) that are feasible - a direct measure of how easy the instance is;
* heuristic success: whether the auction and the Hungarian heuristics find a feasible plan, and their
  gap to the reference (from results/runs/classical/E1.csv when it exists);
* brief statistics: words in each brief, word overlap between the two briefs, and how many
  requirement words the informal brief leaves implicit.
"""

from __future__ import annotations

import csv
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mrta.benchmark import load  # noqa: E402
from mrta.catalog import CAPABILITY_TEXT  # noqa: E402
from mrta.checker import check  # noqa: E402
from mrta.geometry import TravelTable, can_reach, eligibility_matrix  # noqa: E402
from mrta.schema import Plan  # noqa: E402
from mrta.solvers import SOLVERS  # noqa: E402


def random_feasibility(scn, samples: int = 300, seed: int = 0) -> float:
    rng = random.Random(f"rand-{scn.id}-{seed}")
    elig = eligibility_matrix(scn)
    task_to = {t.id: [r for r, ts in elig.items() if t.id in ts] for t in scn.tasks}
    ok = 0
    for _ in range(samples):
        standby = []
        for s in scn.standby:
            cands = [r.id for r in scn.robots if r.type == s.robot_type]
            standby += rng.sample(cands, k=min(s.count, len(cands)))
        routes: dict[str, list[str]] = {}
        dead = False
        for t in scn.tasks:
            opts = [r for r in task_to[t.id] if r not in standby]
            if not opts:
                dead = True
                break
            routes.setdefault(rng.choice(opts), []).append(t.id)
        if dead:
            continue
        for r in routes:
            rng.shuffle(routes[r])
        ok += check(scn, Plan(routes=routes, standby=standby)).feasible
    return ok / samples


def words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def main() -> None:
    recs = load(ROOT / "data" / "benchmark", split="all")
    ref_path = ROOT / "results" / "runs" / "classical" / "E1.csv"
    classical: dict[tuple[str, str], dict] = {}
    if ref_path.exists():
        with open(ref_path) as fh:
            for row in csv.DictReader(fh):
                classical[(row["scenario"], row["method"])] = row
    req_words = {w for txt in CAPABILITY_TEXT.values() for w in words(txt)} | {"payload", "needs", "equipment"}
    rows = []
    for rec in recs:
        scn = rec.scenario
        elig = eligibility_matrix(scn)
        per_task = [sum(t.id in ts for ts in elig.values()) for t in scn.tasks]
        table = TravelTable(scn)
        slacks = []
        for t in scn.tasks:
            if t.deadline is None:
                continue
            lb = min(table.from_start(r, t) + t.duration for r in scn.robots
                     if set(t.required) <= set(r.capabilities) and t.payload <= r.payload
                     and can_reach(r, scn.loc(t.location)))
            slacks.append(t.deadline / lb)
        heur = {}
        for m in ("auction", "hungarian"):
            res = SOLVERS[m](scn)
            heur[m] = bool(res.plan is not None and check(scn, res.plan).feasible)
        ref = None
        for m in ("milp", "hungarian", "auction"):
            row = classical.get((scn.id, m))
            if row and row["feasible"] == "True" and row["makespan"]:
                ref = float(row["makespan"]) if ref is None else min(ref, float(row["makespan"]))
        gaps = {}
        for m in ("auction", "hungarian"):
            row = classical.get((scn.id, m))
            if ref and row and row["feasible"] == "True" and row["makespan"]:
                gaps[m] = (float(row["makespan"]) - ref) / ref
        we, wi = words(rec.briefs["explicit"]), words(rec.briefs["informal"])
        overlap = len(set(we) & set(wi)) / len(set(we) | set(wi))
        rows.append({
            "id": scn.id, "tier": rec.tier, "split": rec.split, "robots": len(scn.robots), "tasks": len(scn.tasks),
            "deadlines": sum(t.deadline is not None for t in scn.tasks), "precedence": len(scn.precedence),
            "standby": len(scn.standby), "eligible_per_task": round(sum(per_task) / len(per_task), 3),
            "min_eligible": min(per_task), "single_robot_tasks": sum(n == 1 for n in per_task),
            "deadline_slack_mean": round(sum(slacks) / len(slacks), 3) if slacks else "",
            "deadline_slack_min": round(min(slacks), 3) if slacks else "",
            "random_feasible": round(random_feasibility(scn), 4),
            "auction_feasible": heur["auction"], "hungarian_feasible": heur["hungarian"],
            "auction_gap": round(gaps["auction"], 4) if "auction" in gaps else "",
            "hungarian_gap": round(gaps["hungarian"], 4) if "hungarian" in gaps else "",
            "words_explicit": len(we), "words_informal": len(wi), "word_overlap": round(overlap, 3),
            "req_words_explicit": sum(w in req_words for w in we), "req_words_informal": sum(w in req_words for w in wi),
        })
        print(f"{scn.id}: random-feasible {rows[-1]['random_feasible']:.2f}, auction {heur['auction']}, "
              f"hungarian {heur['hungarian']}", flush=True)
    out = ROOT / "results" / "benchmark"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "difficulty.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    import pandas as pd

    d = pd.DataFrame(rows)
    lines = ["# Benchmark difficulty report", "", "Generated by `python scripts/benchmark_report.py`; one row per "
             "scenario in `difficulty.csv`.", ""]
    agg = []
    for tier in ("S", "M", "L"):
        s = d[d.tier == tier]
        agg.append({"Tier": tier, "Scenarios": len(s), "Deadlines": f"{s.deadlines.mean():.1f}",
                    "Precedence": f"{s.precedence.mean():.1f}", "Standby": f"{int((s.standby > 0).sum())}/{len(s)}",
                    "Eligible robots per task": f"{s.eligible_per_task.mean():.2f}",
                    "Tasks only one robot can do": f"{s.single_robot_tasks.mean():.1f}",
                    "Min deadline slack": f"{pd.to_numeric(s.deadline_slack_min, errors='coerce').mean():.2f}",
                    "Random plans feasible": f"{100 * s.random_feasible.mean():.1f}%",
                    "Auction feasible": f"{int(s.auction_feasible.sum())}/{len(s)}",
                    "Hungarian feasible": f"{int(s.hungarian_feasible.sum())}/{len(s)}",
                    "Auction mean gap": "-" if s.auction_gap.eq("").all() else
                    f"{100 * pd.to_numeric(s.auction_gap, errors='coerce').mean():.1f}%",
                    "Words explicit / informal": f"{s.words_explicit.mean():.0f} / {s.words_informal.mean():.0f}",
                    "Word overlap": f"{s.word_overlap.mean():.2f}",
                    "Requirement words explicit / informal": f"{s.req_words_explicit.mean():.0f} / "
                                                             f"{s.req_words_informal.mean():.0f}"})
    lines.append(pd.DataFrame(agg).to_markdown(index=False))
    lines.append("")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print((out / "report.md").read_text())


if __name__ == "__main__":
    main()
