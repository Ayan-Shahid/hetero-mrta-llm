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

## Real-model iterations (Qwen2.5-7B, local, via Ollama)

The pilot entries compare three saved pilots on the **dev split**: 6 scenarios × 2 brief styles = 12 runs
per method, repeat 0. The files are `results/runs/pilot_before_fix/`, `pilot_after_fix1/` and
`pilot_after_fix2/`. Every number below is the share of those 12 runs. "No plan" means the run ended
without a plan (status other than `ok`). Model F1 is the extraction F1 against the ground-truth model.
With 12 runs per method these pilot numbers are indicative only. The test-split sweep is the real measurement.

### 2026-11-10 · Pilot 1: extracted models rejected, agent runs out of steps
- **Observed.** In the first real-model pilot (`results/runs/pilot_before_fix/`), the full agent made a
  feasible plan on 2/12 runs (16.7%) and ended without a plan on 9/12 (75%). Mean model F1 was 0.873.
  The trajectories in `logs/pilot/qwen2.5-7b/` show `submit_spec` being rejected again and again:
  - capability names written as synonyms ("thermal camera", "robotic arm", "cargo carrier");
  - a missing `base` location even though the robots start there;
  - numbers given as strings, and zone or type synonyms.

  The validation errors were long, so the model could not see which fact to fix. It retyped the whole
  model and used up the 12-step budget.
- **Change** (commit `5ddc308`, `mrta/llm/methods.py::clean_spec`, `mrta/llm/edits.py`,
  `mrta/llm/prompts.py`):
  - normalise capability, zone and type synonyms, and add the missing base;
  - keep an invalid model editable, so the agent can fix one fact with an edit instead of resubmitting;
  - shorten validation errors;
  - make the deadline and payload instructions in the prompt clearer.

  Regression tests: `tests/test_pilot_fixes.py`.
- **Effect** (`results/runs/pilot_after_fix1/`):

  | Full agent, dev pilot | Before | After |
  |---|---|---|
  | Feasible | 2/12 | 3/12 |
  | No plan | 75% | 67% |
  | Model F1 | 0.873 | 0.906 |

  The ablations improved in the same direction. Without G1, no-plan runs fell from 58% to 17%. Without
  G3, feasible runs rose from 2/12 to 4/12. The baselines that do not use the agent's tools did not
  change (LLM-direct 0/12, PEFA 1/12).

### 2026-11-11 · Pilot 2: invented timing rules make models unsatisfiable
- **Observed.** After fix 1, most of the remaining no-plan runs had a model that was valid but
  unsatisfiable. The model had added deadlines, precedence links or standby robots that the brief does
  not state. A typical case: a standby rule that keeps every robot able to do a task in reserve. The
  solver portfolio then reported "infeasible" and the agent kept editing other facts.
- **Change** (commit `367e790`):
  - `mrta/gaps/g3_language.py`: drop deadlines, precedence and standby facts the brief does not state.
  - `mrta/gaps/g2_verify.py`: flag a standby rule that blocks every capable robot.
  - Agent: when no robot can do a task, the error quotes the robot lines from the brief.

  Regression tests: added to `tests/test_pilot_fixes.py`.
- **Effect** (`results/runs/pilot_after_fix2/`):

  | Full agent, dev pilot | After fix 1 | After fix 2 |
  |---|---|---|
  | Feasible | 3/12 | 8/12 (66.7%) |
  | No plan | 67% | 42% |
  | Model F1 | 0.906 | 0.936 |

  Without G2, feasible runs rose from 1/12 to 5/12. Fix 2 was the last change before the test-split
  sweep, and the code has not changed since.

### 2026-11-11 · Test-split sweep, stages 1 and 2: what the frozen system gets wrong
These are **observations on the test split**. We report them but made no change from them, because
changing the system based on test results would be tuning on the test set (brief, section 4.1). The
proposed fixes are future work and need to be developed on the dev split.

Source: `results/runs/qwen2.5-7b/`, repeat 0, 80 runs per configuration. Tables come from
`python experiments/analyze.py --primary qwen2.5-7b --repeats 1`, and failure categories from
`python experiments/failures.py --model qwen2.5-7b --repeats 1`.

- **Step-budget exhaustion is the main failure.** The full agent made a feasible plan on 26/80 runs
  (32.5%). It ended without a plan on 38/80 (47.5%), all of them category F1 in
  `results/failures/qwen2.5-7b/categories.md`. Every one of the 38 used all 12 steps.
  - These runs averaged 5.9 rejected `submit_spec` calls, against 2.7 in runs that produced a plan.
  - 25 of the 38 had cross-check disagreements, with a mean of 14.0 at first.
  - 27 of the 38 runs were on informal briefs.

  Budget fallback does not help in these runs, because the agent never held a model that was both valid
  and satisfiable. A likely fix (not applied): a larger step budget for L scenarios, and capping how many
  disagreements the agent must review.
- **The G2 cross-check costs more than it gains with a 7B model.**
  - Removing G2 raised feasible plans from 32.5% to 47.5% (McNemar p = 0.023, `E5_ablation.md`) and
    cut tokens per run from 60.8k to 35.8k.
  - The cross-check found disagreements on 64/80 runs, 11.4 per run on average. After the review, the
    model F1 went from 0.915 to 0.900: it improved on 17 runs and worsened on 33 (`E4_gap2_crosscheck.md`).
  - The second reading is made by the same small model, so many of its "disagreements" are its own
    misreadings. The agent then "corrects" facts that were right.
  - We report G2 as **not addressed** in the gap-resolution table.
- **G1 is what makes the agent work.** Without the solver portfolio, the agent writes the plan itself.
  Feasible plans fall to 2.5% (−30.0 pp, p = 8.1e-7), close to LLM-direct (1.2%) and PEFA (3.8%).
- **Informal briefs remain hard (G3).** The full agent made feasible plans on 52.5% of explicit briefs
  and 12.5% of informal ones (−40 pp, p = 0.0004). Model F1 fell from 0.953 to 0.838.
  - Without G3 (single-pass extraction, no questions), informal F1 is 0.775, so the staged extraction
    improves extraction.
  - The difference in feasible plans (−7.5 pp overall) is not significant (p = 0.29).
- **Formatting is not the problem.** There were 0 unparsable replies across all agent runs (E6), so the
  format reminders and action-name mapping added before the pilots were not needed for this model.

### 2026-11-11 · Analysis snapshot while the sweep is still running
- **Observed.** At the Phase 2 submission, the sweep (`experiments/sweep.sh`) had finished stage 1
  (E1–E3, repeat 1) and stage 2 (E5). Stage 3 (repeats 2 and 3) was still running. The analysis takes
  mean ± std over the repeats it finds, so a partly finished repeat 2 would mix a full repeat with a
  small subset of scenarios.
- **Change.** `experiments/analyze.py` and `experiments/failures.py` take `--repeats N` and use only
  repeats 0 to N−1. The submitted tables use `--repeats 1`.
- **Effect.** Every reported number is from one complete repeat on the 40 test scenarios. For that
  reason, the tables show no std. When the sweep finishes, re-run both scripts without `--repeats` to get
  mean ± std over 3 repeats.
