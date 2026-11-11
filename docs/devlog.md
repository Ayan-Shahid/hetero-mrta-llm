# Development log

One entry per major iteration: **date · what failed or was missing · what changed · measured effect**.
Numbers come from files in `results/` (named in each entry). Entries that use the mock model test only
the pipeline's code paths; they say nothing about LLM quality.

---

### 2026-11-08 · Phase 1 feedback: benchmark too small, no fixed split
- **Observed.** 26 scenarios (S10/M10/L6) is too few for paired statistical tests (supervisor feedback).
  There was no separation between the scenarios used to debug prompts and the ones reported.
- **Change.** `scripts/make_benchmark.py` now writes S20/M20/L10 = 50 scenarios and a fixed split in
  `data/benchmark/splits.json`: dev = first 4/4/2 seeds per tier (10 scenarios), test = the other 40.
  The runner reads the split (`mrta/benchmark.py`), and the experiments use the test split only.
- **Effect.** 40 test scenarios × 2 brief styles = 80 paired inputs per method per repeat, up from 52 in
  Phase 1. That is 240 paired runs per main configuration with 3 repeats.

### 2026-11-08 · Benchmark biased toward the auction heuristic
- **Observed.** v1 set deadlines from the auction plan's finish times, so the auction plan was always
  feasible by construction: auction feasible on 26/26 scenarios (`results/phase1/summary.md`). A
  benchmark on which one of the compared heuristics can never fail is biased (supervisor feedback on
  "too easy or biased").
- **Change (attempt 1).** Use the better of the auction plan and a 15 s MILP run as the witness.
  Rejected: generating 50 scenarios took 3 min 31 s, and the MILP's best plan at the time limit depends on
  machine speed, so the benchmark could not be regenerated identically.
- **Change (attempt 2, kept).** Witness = the better of the auction and Hungarian plans, improved by a
  deterministic first-improvement local search (`improve_plan` in `mrta/scenarios/generator.py`). Deadline
  probability 0.3 → 0.4, slack ranges tightened, horizon factor 1.6 → 1.4.
- **Effect.**
  - Generation takes 2.4 s for 50 scenarios, and two independent generations are byte-identical.
  - Auction feasible on 46/50, Hungarian on 38/50, both infeasible on 3/50 (`results/benchmark/difficulty.csv`).
  - Random-plan feasibility is 50.6% / 11.3% / 4.3% on S / M / L (`results/benchmark/report.md`).

### 2026-11-08 · Classical baselines on benchmark v2
- **Observed.** Phase 1 numbers were on v1 and cannot be compared with Phase 2.
- **Change.** Re-ran MILP (60 s limit), iterated Hungarian and SSI auction on all 50 v2 scenarios
  (`python experiments/run.py --exp E1 --classical-only --split all`).
- **Effect.** The MILP proves optimality on 16/16 S, 16/16 M and 6/8 L test scenarios. On the test split,
  the mean gap to the reference is 1.3% / 17.3% / 35.2% for the auction and 8.0% / 11.4% / 39.5% for the
  Hungarian method on S / M / L (`results/tables/E1_classical.md`).

### 2026-11-08 · Gap components made separate and switchable
- **Observed.** The Phase 1 agent had one fixed tool set. No experiment could tell which part helped,
  which the ablation (E5) needs.
- **Change.** Each gap's component is now a module in `mrta/gaps/`, with switches in `AgentConfig` and
  named presets (`full`, `no-g1`, `no-g2`, `no-g3`, `no-g2-crosscheck`, `no-g3-clarify`, `phase1`):
  - G1: verified solver portfolio;
  - G2: model sanity checks, an independent second reading compared fact by fact, and a finish gate;
  - G3: three-pass staged extraction and a simulated mission owner for questions.

  The system prompt lists only the enabled tools.
- **Effect.** `tests/test_agent_presets.py::test_every_preset_runs_end_to_end` runs all 8 presets end to end.
  The tool lists differ as intended.

### 2026-11-08 · Out of step budget means no plan
- **Observed.** In the Phase 1 agent, a run that used all its steps before calling `solve` returned no plan
  (status `no_plan`), even when it already held a valid model.
- **Change.** After the loop, the agent falls back in this order:
  1. a valid model → run the verified portfolio (status `budget_fallback`);
  2. no model, but a valid staged draft → use the draft (`used_draft_fallback`);
  3. otherwise → the last solution.

  Replies that cannot be parsed get a format reminder; 3 in a row end the run. Common action-name
  variants (`draft`, `submit`, `verify`, ...) are mapped to the real tools.
- **Effect.** No run can now end without a plan while a valid model exists. The effect on real
  models will be measured in the sweep (`status` column; `E6_efficiency` "No-plan or error %").

### 2026-11-08 · Local model would silently truncate the agent's context
- **Observed.** The agent's first prompt is about 2,150 tokens on an L brief (system prompt 1,235 + brief).
  Measured on the mock pilot, the prompt grows to 3,000 to 5,000 tokens per call once the draft model and the
  cross-check are in the conversation. Ollama's default context window is smaller, and it drops the oldest
  tokens without an error.
- **Change.** `configs/ollama/*.Modelfile` sets `num_ctx 16384`, and `configs/models/qwen-local.yaml` points
  to that model.
- **Effect.** Every prompt fits. Check with `ollama show qwen2.5-7b-16k`.

### 2026-11-08 · Free-tier quotas and repeats
- **Observed.** The Gemini free tier allows only a limited number of requests per day. With the Phase 1
  client, a 429 error after the retries ran out ended the sweep, and the quota counter reset at an
  unknown time. Repeats at temperature 0 with a cache return identical answers, so mean ± std would be
  meaningless.
- **Change.**
  - Client: `rpm` / `rpd` pacing with a day counter shared through a file, and `QuotaExhausted` stops the
    sweep cleanly (exit code 3). Finished runs are skipped on restart.
  - Repeats: temperature 0.2, `seed = repeat`, and the seed is part of the cache key.
  - The Anthropic endpoint rejects `response_format: json_object` (per its documentation), so
    `json_mode: false` is set for Claude.
- **Effect.** `tests/test_client_limits.py::test_rate_limiter_stops_at_daily_quota` passes. Three repeats are three
  different samples.

---

## To be added from the real-model runs

The brief asks for iterations driven by **observed failures of the system**. Add an entry for each change
you make after looking at the pilot or the dev-split runs, in the format above. Typical candidates to
look for in `logs/<model>/`:

- the model replies with prose or several JSON objects (count: `parse_failures`);
- it never calls `draft_model` first, or retypes the whole model instead of using edits;
- the cross-check produces many false disagreements (`disagreements_first` high, but `spec_f1_first`
  already 1.0);
- a stage of the staged extraction systematically misses one fact type (`recall_*` columns).

For each one, record the measured value before the change (on the **dev** split), the change, and the
value after. Never tune on the test split.
