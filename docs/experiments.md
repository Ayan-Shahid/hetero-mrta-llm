# Running the experiments

Every number in the README and the paper comes from files that the commands below write to
`results/`. They can all be re-run from a fresh clone.

## 1. Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
bash scripts/setup_hooks.sh          # commit-message and secret checks (for contributors)
python -m pytest -q                  # 55 tests, about 10 s
cp .env.example .env                 # then paste the API keys you have into .env
```

### Models

| Config | Provider | Notes |
|---|---|---|
| `configs/models/qwen-local.yaml` | Ollama, local | Free and unlimited. This is the main model for the full sweep |
| `configs/models/gemini-flash.yaml` | Google Gemini API | Free tier. Set `rpm` and `rpd` to your quota |
| `configs/models/claude-haiku.yaml` | Anthropic API | Paid. Run the pilot first and check the cost estimate |
| `configs/models/mock.yaml` | none | A deterministic stand-in for tests. **Not a language model** |

**Local model (Ollama).** Ollama's default context window is shorter than the agent's prompts, and
it truncates them silently. Create the 16k-context model once:

```bash
ollama pull qwen2.5:7b-instruct
ollama create qwen2.5-7b-16k -f configs/ollama/qwen2.5-7b-16k.Modelfile
python -m mrta.llm --config configs/models/qwen-local.yaml --ping
```

For two parallel runs (`--workers 2`), let Ollama serve two requests at once. Run
`launchctl setenv OLLAMA_NUM_PARALLEL 2` on macOS, then restart the Ollama app.

**Gemini.** Put `GEMINI_API_KEY=...` in `.env`, set `rpm` and `rpd` in the config to the limits
AI Studio shows for your key, then run `python -m mrta.llm --config configs/models/gemini-flash.yaml --ping`.
When the daily quota is used up, the runner stops with exit code 3. Run the same command the next day
and it continues where it stopped.

## 2. Pilot (about 10 to 30 minutes per model)

```bash
python experiments/run.py --exp E1,E2,E3,E5 --model configs/models/qwen-local.yaml --pilot
python experiments/estimate.py --model qwen2.5-7b          # projected hours, calls, tokens, cost
```

The pilot uses only the **dev** split (2 scenarios per tier), and its results go to
`results/runs/pilot/`, so they never mix with test results.

## 3. Full sweep

```bash
bash experiments/sweep.sh configs/models/qwen-local.yaml 2      # main model, 2 parallel runs
```

The stages run in priority order:
1. The classical baselines.
2. E1, E2 and E3, one repeat on the 40 test scenarios × 2 brief styles.
3. E5, the ablations.
4. Repeats 2 and 3 of E1, E2 and E3.
5. Optionally, the finer ablations (E5b).

Stopping and re-running is safe, because finished runs are skipped.

A second model on a subset, for example Gemini on S and M:

```bash
python experiments/run.py --exp E2,E3 --model configs/models/gemini-flash.yaml --tiers S,M --repeats 1
```

## 4. Analysis

```bash
python scripts/benchmark_report.py       # results/benchmark/difficulty.csv, report.md
python experiments/analyze.py --primary qwen2.5-7b
python experiments/failures.py --model qwen2.5-7b
```

| Experiment (brief, section 4.2) | Produced by | Main outputs |
|---|---|---|
| E1 Baseline | `run.py --exp E1` | `results/tables/E1_classical.*`, rows `LLM-direct` and `LLM-formulate` in `main_results.*` |
| E2 Base paper (reproduced) | `run.py --exp E2` | row `COHERENT-style PEFA` in `main_results.*` |
| E3 Proposed system | `run.py --exp E3` | row `Agent (full)` in `main_results.*` |
| E4 Gap-wise | `analyze.py` | `E4_gap1_optimality_tests.*`, `E4_gap2_*.*`, `E4_gap3_*.*` |
| E5 Ablation | `run.py --exp E5` + `analyze.py` | `E5_ablation.*`, `fig_ablation_*.png` |
| E6 Efficiency and reliability | `analyze.py` | `E6_efficiency.*`, `fig_tokens_vs_feasibility_*.png` |
| Failure analysis | `failures.py` | `results/failures/<model>/categories.md`, `cases/*.md` |
| Gap-resolution table | `analyze.py` | `results/tables/gap_resolution.*` |

All tables come as Markdown (README), CSV and LaTeX (`\input{}` in the paper). `results/summary.json`
holds every headline number.

## Protocol

- **Splits:** fixed in `data/benchmark/splits.json`. The dev split has 10 scenarios (S-000 to S-003,
  M-000 to M-003, L-000 and L-001); the test split has the other 40. Prompts and settings were only
  ever checked on dev scenarios.
- **Decoding:** temperature 0.2. Repeat `r` sends `seed = r`, which also goes into the cache key.
  Main configurations are run 3 times and reported as mean ± std over repeats.
- **Solver limits:** the MILP limit is 60 s for the classical baseline. Inside the agent, the limit
  comes from the instance size (`mrta/gaps/g1_portfolio.py::milp_budget`).
- **Base paper:** COHERENT's PEFA loop is re-implemented and run on our benchmark (`llm-pefa`). Its
  published numbers (BEHAVIOR-1K, success rate 0.975) are on a different task and metric and are only
  quoted, never compared directly.
- **Record keeping:** every run's provider, model id, seed, tokens, latency and cost are in its CSV row,
  and the full trajectory is in `logs/<model>/<exp>/<run id>.jsonl`.
