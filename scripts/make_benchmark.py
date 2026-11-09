#!/usr/bin/env python3
"""Generate the benchmark: one JSON per scenario with the ground-truth model and both briefs.

    python scripts/make_benchmark.py                                  # Phase 2 set: v2, S=20, M=20, L=10
    python scripts/make_benchmark.py --version 1 --tiers S=10,M=10,L=6 --out data/benchmark_v1   # Phase 1 set

The split is fixed by seed: the first --dev-per-tier seeds of each tier form the development
split (prompt and threshold choices may look at it); every other scenario is test-only.
"""

import argparse
import csv
import json
import time
from pathlib import Path

import _path  # noqa: F401
from mrta.scenarios import STYLES, generate, render

DEV_PER_TIER = {"S": 4, "M": 4, "L": 2}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tiers", default="S=20,M=20,L=10", help="tier=count pairs")
    ap.add_argument("--version", type=int, default=2, choices=(1, 2), help="generator version (see docs/benchmark.md)")
    ap.add_argument("--out", default="data/benchmark")
    ap.add_argument("--seed", type=int, default=0, help="first seed")
    ap.add_argument("--dev-per-tier", default="S=4,M=4,L=2", help="development scenarios per tier")
    args = ap.parse_args()

    dev_n = {k: int(v) for k, v in (p.split("=") for p in args.dev_per_tier.split(",") if p)}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index, splits = [], {"dev": [], "test": []}
    for part in args.tiers.split(","):
        tier, count = part.split("=")
        tier = tier.strip()
        for seed in range(args.seed, args.seed + int(count)):
            t0 = time.perf_counter()
            scn = generate(tier, seed, version=args.version)
            split = "dev" if seed - args.seed < dev_n.get(tier, 0) else "test"
            splits[split].append(scn.id)
            record = {"tier": tier, "seed": seed, "version": args.version, "split": split,
                      "scenario": scn.model_dump(mode="json"),
                      "briefs": {style: render(scn, style) for style in STYLES}}
            (out / f"{scn.id}.json").write_text(json.dumps(record, indent=1))
            index.append({"id": scn.id, "tier": tier, "seed": seed, "split": split, "robots": len(scn.robots),
                          "tasks": len(scn.tasks), "precedence": len(scn.precedence),
                          "deadlines": sum(t.deadline is not None for t in scn.tasks),
                          "standby": len(scn.standby), "horizon_s": scn.horizon,
                          "robot_types": " ".join(sorted(r.type for r in scn.robots))})
            print(f"{scn.id} [{split}]: {len(scn.robots)} robots, {len(scn.tasks)} tasks "
                  f"({time.perf_counter() - t0:.1f}s)", flush=True)
    with open(out / "index.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(index[0]))
        writer.writeheader()
        writer.writerows(index)
    (out / "splits.json").write_text(json.dumps({"version": args.version, **splits}, indent=1))
    print(f"wrote {len(index)} scenarios to {out}/ (dev {len(splits['dev'])}, test {len(splits['test'])})")


if __name__ == "__main__":
    main()
