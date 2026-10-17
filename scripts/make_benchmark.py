#!/usr/bin/env python3
"""Generate the benchmark: one JSON per scenario with the ground-truth model and both briefs.

    python scripts/make_benchmark.py                      # S=10, M=10, L=6 into data/benchmark
    python scripts/make_benchmark.py --tiers S=20,M=20 --out data/bench2 --seed 100
"""

import argparse
import csv
import json
from pathlib import Path

import _path  # noqa: F401
from mrta.scenarios import STYLES, generate, render


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tiers", default="S=10,M=10,L=6", help="tier=count pairs")
    ap.add_argument("--out", default="data/benchmark")
    ap.add_argument("--seed", type=int, default=0, help="first seed")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for part in args.tiers.split(","):
        tier, count = part.split("=")
        for seed in range(args.seed, args.seed + int(count)):
            scn = generate(tier.strip(), seed)
            record = {"tier": tier.strip(), "seed": seed, "scenario": scn.model_dump(mode="json"),
                      "briefs": {style: render(scn, style) for style in STYLES}}
            (out / f"{scn.id}.json").write_text(json.dumps(record, indent=1))
            index.append({"id": scn.id, "tier": tier.strip(), "seed": seed, "robots": len(scn.robots),
                          "tasks": len(scn.tasks), "precedence": len(scn.precedence),
                          "deadlines": sum(t.deadline is not None for t in scn.tasks),
                          "standby": len(scn.standby), "horizon_s": scn.horizon,
                          "robot_types": " ".join(sorted(r.type for r in scn.robots))})
            print(f"{scn.id}: {len(scn.robots)} robots, {len(scn.tasks)} tasks")
    with open(out / "index.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(index[0]))
        writer.writeheader()
        writer.writerows(index)
    print(f"wrote {len(index)} scenarios to {out}/")


if __name__ == "__main__":
    main()
