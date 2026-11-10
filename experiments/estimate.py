#!/usr/bin/env python3
"""Project the time, tokens and cost of a full sweep from a pilot run.

    python experiments/run.py --exp E1,E2,E3,E5 --model configs/models/qwen-local.yaml --pilot
    python experiments/estimate.py --model qwen2.5-7b

The pilot covers 2 dev scenarios per tier; the projection scales the per-tier means to the test
split (16 S, 16 M, 8 L scenarios x 2 brief styles) and the repeats in experiments/plan.yaml.
Wall-clock time assumes one worker; divide by about 1.5 for --workers 2 on a local model.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEST = {"S": 16, "M": 16, "L": 8}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="model label (folder under results/runs/pilot/)")
    ap.add_argument("--rpd", type=int, default=0, help="daily request limit, to estimate days needed")
    args = ap.parse_args()
    d = ROOT / "results" / "runs" / "pilot" / args.model
    if not d.exists():
        sys.exit(f"no pilot results in {d}")
    plan = yaml.safe_load((ROOT / "experiments" / "plan.yaml").read_text())["experiments"]
    df = pd.concat([pd.read_csv(f) for f in d.glob("E*.csv")], ignore_index=True)
    df["key"] = df["method"] + df["config"].fillna("").map(lambda c: f":{c}" if c else "")
    rows, total = [], {"hours": 0.0, "calls": 0.0, "tokens": 0.0, "cost": 0.0}
    for exp, spec in plan.items():
        reps = spec.get("repeats", 1)
        for m in spec.get("methods", []):
            s = df[df.key == m]
            if not len(s):
                continue
            per_tier = s.groupby("tier")[["plan_time_s", "llm_calls", "prompt_tokens", "completion_tokens",
                                          "cost_usd"]].mean()
            n = {t: TEST[t] * 2 * reps for t in TEST}
            secs = sum(per_tier.loc[t, "plan_time_s"] * n[t] for t in TEST if t in per_tier.index)
            calls = sum(per_tier.loc[t, "llm_calls"] * n[t] for t in TEST if t in per_tier.index)
            toks = sum((per_tier.loc[t, "prompt_tokens"] + per_tier.loc[t, "completion_tokens"]) * n[t]
                       for t in TEST if t in per_tier.index)
            cost = sum(per_tier.loc[t, "cost_usd"] * n[t] for t in TEST if t in per_tier.index)
            rows.append({"exp": exp, "method": m, "repeats": reps, "runs": sum(n.values()),
                         "hours": round(secs / 3600, 1), "LLM calls": int(calls), "M tokens": round(toks / 1e6, 2),
                         "USD": round(cost, 2)})
            for k, v in (("hours", secs / 3600), ("calls", calls), ("tokens", toks), ("cost", cost)):
                total[k] += v
    out = pd.DataFrame(rows)
    print(out.to_markdown(index=False))
    print(f"\nTOTAL: {total['hours']:.1f} h, {int(total['calls'])} LLM calls, {total['tokens'] / 1e6:.1f} M tokens, "
          f"${total['cost']:.2f}")
    if args.rpd:
        print(f"At {args.rpd} requests/day that is {total['calls'] / args.rpd:.1f} days of quota.")


if __name__ == "__main__":
    main()
