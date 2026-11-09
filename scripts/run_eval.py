#!/usr/bin/env python3
"""Run methods over the benchmark and append one CSV row per run.

Classical baselines get the ground-truth formal model; LLM methods get only the brief.

    python scripts/run_eval.py --methods classical                       # MILP, Hungarian, auction
    python scripts/run_eval.py --methods llm --llm mock --tiers S        # pipeline check, no API key
    python scripts/run_eval.py --methods llm --llm configs/llm.yaml --tiers S,M --resume
"""

import argparse
import csv
import json
import time
from pathlib import Path

import _path  # noqa: F401
from mrta.evaluate import CLASSICAL, FIELDS, LLM, run_classical, run_llm
from mrta.llm.client import make_client
from mrta.benchmark import load


def parse_methods(text: str) -> list[str]:
    out = []
    for m in text.split(","):
        m = m.strip()
        out += list(CLASSICAL) if m == "classical" else list(LLM) if m == "llm" else \
            list(CLASSICAL + LLM) if m == "all" else [m]
    unknown = [m for m in out if m not in CLASSICAL + LLM]
    if unknown:
        raise SystemExit(f"unknown method(s) {unknown}; choose from {CLASSICAL + LLM}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", default="data/benchmark")
    ap.add_argument("--methods", default="classical", help="comma list, or classical / llm / all")
    ap.add_argument("--styles", default="explicit,informal")
    ap.add_argument("--llm", default="mock", help="mock, mock-oracle, or a YAML config such as configs/llm.yaml")
    ap.add_argument("--tiers", default="", help="e.g. S,M (default: all)")
    ap.add_argument("--split", default="all", choices=("all", "dev", "test"))
    ap.add_argument("--limit", type=int, default=0, help="max scenarios per tier (0 = all)")
    ap.add_argument("--time-limit", type=float, default=60.0, help="MILP time limit in seconds")
    ap.add_argument("--out", default="results/runs.csv")
    ap.add_argument("--traces", default="results/traces", help="folder for LLM transcripts ('' to skip)")
    ap.add_argument("--resume", action="store_true", help="skip runs already in --out")
    args = ap.parse_args()

    methods = parse_methods(args.methods)
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    tiers = {t.strip() for t in args.tiers.split(",") if t.strip()}
    client = make_client(args.llm) if any(m in LLM for m in methods) else None

    records = load(args.bench, split=args.split, tiers=tiers, limit=args.limit)
    if not records:
        raise SystemExit(f"no scenarios found in {args.bench}; run scripts/make_benchmark.py first")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if args.resume and out.exists():
        with open(out) as fh:
            for row in csv.DictReader(fh):
                done.add((row["scenario"], row["method"], row["input"], row["provider"], row["model"]))
    new_file = not out.exists() or out.stat().st_size == 0
    trace_dir = Path(args.traces) if args.traces else None
    if trace_dir:
        trace_dir.mkdir(parents=True, exist_ok=True)

    with open(out, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        t_start = time.time()
        for rec in records:
            scn = rec.scenario
            for method in methods:
                inputs = ["spec"] if method in CLASSICAL else styles
                for inp in inputs:
                    prov = "" if method in CLASSICAL else client.config.provider
                    model = "" if method in CLASSICAL else client.config.model
                    if (scn.id, method, inp, prov, model) in done:
                        continue
                    if method in CLASSICAL:
                        row = run_classical(method, scn, rec.tier, args.time_limit)
                    else:
                        row, trace = run_llm(method, client, scn, rec.tier, inp, rec.briefs[inp],
                                             args.time_limit)
                        if trace_dir:
                            name = f"{scn.id}__{method}__{inp}__{prov}-{model}".replace("/", "_").replace(":", "_")
                            (trace_dir / f"{name}.json").write_text(json.dumps(trace, indent=1, default=str))
                    writer.writerow(row)
                    fh.flush()
                    mk = f"{row['makespan']:.0f}s" if row["makespan"] is not None else "-"
                    print(f"{scn.id:7} {method:14} {inp:9} feasible={str(row['feasible']):5} makespan={mk:>7} "
                          f"time={row['plan_time_s']:.2f}s status={row['status']}", flush=True)
        print(f"done in {time.time() - t_start:.0f}s -> {out}")


if __name__ == "__main__":
    main()
