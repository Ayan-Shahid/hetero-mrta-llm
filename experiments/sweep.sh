#!/usr/bin/env bash
# Full experiment sweep in priority order: whatever has finished is already usable.
#   bash experiments/sweep.sh configs/models/qwen-local.yaml 2      # model config, parallel workers
# Safe to stop (Ctrl+C) and re-run: finished runs are skipped.
set -e
cd "$(dirname "$0")/.."
MODEL="${1:-configs/models/qwen-local.yaml}"
WORKERS="${2:-1}"
run() { python experiments/run.py --model "$MODEL" --workers "$WORKERS" "$@"; }

echo "== stage 0: classical baselines (no LLM) =="
python experiments/run.py --exp E1 --classical-only --split all
echo "== stage 1: E1, E2, E3 - first repeat on the whole test split =="
run --exp E1,E2,E3 --repeats 1
echo "== stage 2: E5 ablations (one repeat) =="
run --exp E5
echo "== stage 3: E1, E2, E3 - repeats 2 and 3 =="
run --exp E1,E2,E3 --repeats 3
echo "== stage 4 (optional): E5b finer ablations =="
run --exp E5b
python experiments/analyze.py
