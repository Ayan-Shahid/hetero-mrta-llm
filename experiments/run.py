#!/usr/bin/env python3
"""Run experiments E1, E2, E3 and E5 (see experiments/plan.yaml) and save one CSV row per run.

    # classical part of E1 (no LLM, ~10 minutes)
    python experiments/run.py --exp E1 --classical-only

    # LLM experiments with a local model, 3 repeats, test split
    python experiments/run.py --exp E1,E2,E3 --model configs/models/qwen-local.yaml --resume

    # small pilot first: 2 scenarios per tier, 1 repeat, dev split
    python experiments/run.py --exp E1,E2,E3 --model configs/models/qwen-local.yaml --pilot

Results:  results/runs/<model label>/<exp>.csv     (classical: results/runs/classical/E1.csv)
Traces:   logs/<model label>/<exp>/<run id>.jsonl  (one JSON event per line)

Runs are keyed by run id and finished runs are always skipped, so re-running the same command
resumes a sweep (also after a daily-quota stop, exit code 3). --fresh starts over.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mrta.benchmark import load  # noqa: E402
from mrta.evaluate import FIELDS, run_classical, run_llm, split_method  # noqa: E402
from mrta.gaps import MissionOwner  # noqa: E402
from mrta.llm.client import LLMConfig, QuotaExhausted, load_dotenv, make_client  # noqa: E402


def slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text)


class CsvSink:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.done: set[str] = set()
        if path.exists() and path.stat().st_size:
            with open(path) as fh:
                self.done = {row["run_id"] for row in csv.DictReader(fh)}

    def write(self, row: dict) -> None:
        with self.lock:
            new = not self.path.exists() or self.path.stat().st_size == 0
            with open(self.path, "a", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow(row)
            self.done.add(row["run_id"])


def write_trace(path: Path, trace: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        head = {k: v for k, v in trace.items() if k != "events"}
        fh.write(json.dumps({"type": "run", **head}, default=str) + "\n")
        for ev in trace["events"]:
            fh.write(json.dumps(ev, default=str) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", default="E1,E2,E3", help="comma list of E1, E2, E3, E5, E5b")
    ap.add_argument("--model", default="configs/models/mock.yaml", help="model config YAML (or 'mock')")
    ap.add_argument("--plan", default="experiments/plan.yaml")
    ap.add_argument("--bench", default="data/benchmark")
    ap.add_argument("--split", default=None, help="override the plan's split (dev | test | all)")
    ap.add_argument("--tiers", default=None, help="override tiers, e.g. S,M")
    ap.add_argument("--styles", default=None, help="override styles, e.g. explicit")
    ap.add_argument("--repeats", type=int, default=None, help="override repeats")
    ap.add_argument("--methods", default=None, help="run only these methods of the chosen experiments")
    ap.add_argument("--limit", type=int, default=0, help="max scenarios per tier")
    ap.add_argument("--pilot", action="store_true", help="dev split, 2 scenarios per tier, 1 repeat, separate files")
    ap.add_argument("--classical-only", action="store_true", help="only the classical part of E1")
    ap.add_argument("--workers", type=int, default=1, help="parallel runs (2 helps with a local Ollama model)")
    ap.add_argument("--resume", action="store_true", help="(default behaviour) skip runs already in the result files")
    ap.add_argument("--fresh", action="store_true", help="delete this model's result files for the chosen experiments first")
    ap.add_argument("--out", default="results/runs")
    ap.add_argument("--logs", default="logs")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    plan = yaml.safe_load((ROOT / args.plan).read_text())
    defaults = plan["defaults"]
    exps = [e.strip() for e in args.exp.split(",") if e.strip()]
    for e in exps:
        if e not in plan["experiments"]:
            raise SystemExit(f"unknown experiment {e}; E4 and E6 are produced by experiments/analyze.py")

    split = "dev" if args.pilot else (args.split or defaults["split"])
    tiers = args.tiers or ",".join(defaults["tiers"])
    styles = [s for s in (args.styles.split(",") if args.styles else defaults["styles"]) if s]
    limit = 2 if args.pilot and not args.limit else args.limit
    records = load(ROOT / args.bench, split=split, tiers=tiers, limit=limit)
    if not records:
        raise SystemExit("no scenarios selected")
    out_root = ROOT / args.out
    logs_root = ROOT / args.logs
    if args.pilot:
        out_root, logs_root = out_root / "pilot", logs_root / "pilot"

    # ---------------------------------------------------------------- classical part of E1
    if "E1" in exps:
        sink = CsvSink(out_root / "classical" / "E1.csv")
        for rec in records:
            for method in plan["experiments"]["E1"].get("classical", []):
                run_id = f"E1__{method}__{rec.id}"
                if run_id in sink.done:  # deterministic: never re-run
                    continue
                row = run_classical(method, rec.scenario, rec.tier, defaults["milp_time_limit"])
                row.update({"exp": "E1", "run_id": run_id, "repeat": 0, "split": rec.split})
                sink.write(row)
                mk = f"{row['makespan']:.0f}s" if row["makespan"] is not None else "-"
                print(f"E1 {rec.id:6} {method:10} feasible={row['feasible']!s:5} makespan={mk:>7} "
                      f"time={row['plan_time_s']:.2f}s", flush=True)
    if args.classical_only:
        return

    # ---------------------------------------------------------------- LLM experiments
    model_path = args.model
    if model_path in ("mock", "mock-oracle"):
        base_cfg = LLMConfig(provider="mock", model=model_path, label=model_path, cache_dir=None)
    else:
        base_cfg = LLMConfig.load(ROOT / model_path)
    label = slug(base_cfg.name)
    jobs = []
    for e in exps:
        spec = plan["experiments"][e]
        methods = spec.get("methods", [])
        if args.methods:
            wanted = {m.strip() for m in args.methods.split(",")}
            methods = [m for m in methods if m in wanted]
        repeats = 1 if args.pilot else (args.repeats or spec.get("repeats", 1))
        if args.fresh:
            (out_root / label / f"{e}.csv").unlink(missing_ok=True)
        sink = CsvSink(out_root / label / f"{e}.csv")
        for r in range(repeats):
            for rec in records:
                for method in methods:
                    for style in styles:
                        run_id = f"{e}__{slug(method)}__{rec.id}__{style}__r{r}"
                        if run_id in sink.done:
                            continue
                        jobs.append((e, sink, r, rec, method, style, run_id))
    order = {e: i for i, e in enumerate(exps)}
    jobs.sort(key=lambda j: (j[2], order[j[0]]))  # all experiments' first repeat before any second repeat
    if not jobs:
        print("nothing to run (all runs already in the result files)")
        return
    print(f"{len(jobs)} LLM runs with {base_cfg.name} on {len(records)} scenarios ({split} split)", flush=True)

    clients: dict[int, object] = {}
    lock = threading.Lock()

    def client_for(repeat: int):
        with lock:
            if repeat not in clients:
                cfg = LLMConfig(**{**base_cfg.__dict__, "seed": repeat})
                clients[repeat] = make_client(cfg)
            return clients[repeat]

    stop = threading.Event()
    t_start = time.time()
    finished = 0

    def work(job):
        e, sink, r, rec, method, style, run_id = job
        if stop.is_set():
            return None
        client = client_for(r)
        human = MissionOwner(rec.scenario) if split_method(method)[0] == "agent" else None
        row, trace = run_llm(method, client, rec.scenario, rec.tier, style, rec.briefs[style],
                             defaults["milp_time_limit"], human=human)
        row.update({"exp": e, "run_id": run_id, "repeat": r, "split": rec.split})
        sink.write(row)
        write_trace(logs_root / label / e / f"{run_id}.jsonl", trace)
        return row

    exit_code = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                row = fut.result()
            except QuotaExhausted as err:
                if not stop.is_set():
                    print(f"\nSTOPPED: {err}\nResume later with the same command plus --resume.", flush=True)
                stop.set()
                exit_code = 3
                continue
            if row is None:
                continue
            finished += 1
            eta = (time.time() - t_start) / finished * (len(jobs) - finished)
            mk = f"{row['makespan']:.0f}s" if row.get("makespan") is not None else "-"
            print(f"[{finished}/{len(jobs)} eta {eta / 60:.0f} min] {job[0]} {row['scenario']:6} "
                  f"{job[4]:22} {row['input']:8} r{row['repeat']} feasible={row['feasible']!s:5} makespan={mk:>7} "
                  f"calls={row['llm_calls']} time={row['plan_time_s']:.0f}s status={row['status']}", flush=True)
    print(f"done in {(time.time() - t_start) / 60:.1f} min -> {out_root / label}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
