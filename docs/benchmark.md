# Benchmark (v2): how it is built, how hard it is, and what could bias it

The benchmark lives in `data/benchmark/`. There is one JSON file per scenario, containing the
ground-truth formal model and two briefs: an explicit one and an informal one. Regenerate it with:

```bash
python scripts/make_benchmark.py             # v2: S=20, M=20, L=10, fixed dev/test split
python scripts/benchmark_report.py           # difficulty statistics -> results/benchmark/
```

The Phase 1 set (v1, 26 scenarios) is kept in `data/benchmark_v1/` so the Phase 1 numbers
(`results/phase1/`) stay reproducible.

## 1. Generation

`mrta/scenarios/generator.py` draws every scenario from a seed (`random.Random(f"{tier}-{seed}-{attempt}")`),
so the same command always writes the same files. The generation steps are:

1. **Team.** Every team has at least one quadcopter and one mobile manipulator. The other robots are
   drawn with weights quadcopter 0.35, rover 0.30, mobile manipulator 0.20, fixed arm 0.15. Speed,
   payload, endurance and the optional thermal camera are drawn from per-type ranges
   (`mrta/catalog.py`).
2. **Map.** Rooftop, indoor and outdoor locations are placed on a 300 m × 300 m site, at least 40 m apart.
   Fixed arms get their own indoor work cell.
3. **Tasks.** Each task is drawn from 7 templates: roof inspection, thermal scan, photo survey, delivery,
   valve service, part swap and sample pickup. A task is only placed where at least one robot can do it.
   Manipulation tasks are sometimes paired with a delivery to the same place, which creates a precedence edge.
4. **Standby.** With probability 0.5, one quadcopter has to be kept free (only when the team has two).
5. **Deadlines and horizon** come from a *witness plan*, which stays feasible by construction (see below).
   Each task gets a deadline with probability 0.4, set no earlier than the later of two values:
   - the task's lower bound (fastest robot, travelling straight from its start) × U(1.3, 2.2);
   - the witness plan's finish time for the task × U(1.0, 1.2).

   The horizon is the larger of 1.4 × the witness makespan and the witness makespan + 300 s.
6. **Feasibility.** The witness plan is re-checked with the same checker used for evaluation. A scenario
   that fails is redrawn. **Every scenario is therefore feasible.** When a method returns an infeasible
   plan, the fault is the method's, not the scenario's.

### What changed from v1, and why

| Change | Reason |
|---|---|
| **Witness.** v1 used the auction plan; v2 uses the better of the auction and Hungarian plans, improved by a deterministic local search (`improve_plan`) | With an auction witness, the auction heuristic could never miss a deadline, so the benchmark was biased toward it. In v2, 4 of 50 scenarios defeat the auction, 12 of 50 defeat the Hungarian method, and 3 defeat both |
| Deadline probability 0.3 → 0.4; slack ranges tightened; horizon factor 1.6 → 1.4 | More binding time constraints, which is where LLM planners fail |
| 26 → 50 scenarios, with a fixed dev/test split | The supervisor's feedback; enough runs for paired tests |
| No time-limited MILP inside the generator | Tried first, then dropped. A MILP's best plan at its time limit depends on how fast the machine is, so the benchmark was not reproducible |

## 2. How hard is it? (`results/benchmark/report.md`)

| Tier | Scenarios (dev/test) | Robots | Tasks | Deadlines | Precedence | Standby | Eligible robots per task | Random plans feasible | Auction feasible | Hungarian feasible | Auction mean gap |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S | 20 (4/16) | 3 | 5 | 1.9 | 0.2 | 2/20 | 1.33 | 50.6% | 19/20 | 18/20 | 5.4% |
| M | 20 (4/16) | 5 | 10 | 3.9 | 0.9 | 7/20 | 2.09 | 11.3% | 17/20 | 12/20 | 20.7% |
| L | 10 (2/8) | 8 | 16 | 6.1 | 1.5 | 6/10 | 3.24 | 4.3% | 10/10 | 8/10 | 29.3% |

The columns measure the following:
- **Random plans feasible** is the share of 300 random plans that pass the checker. Each random plan
  gives every task to a randomly chosen robot that is able to do it, orders each route randomly, and
  honours standby. It falls from about 1 in 2 on S to 1 in 23 on L, so guessing does not work beyond
  the small tier.
- **Eligible robots per task** is how many robots could do a task. On S most tasks have exactly one
  possible robot, so the main difficulty is reading the brief correctly. On L there are about three
  candidates per task, so the main difficulty is choosing among them well.
- **Auction mean gap** is how much later the auction plan finishes than the best known plan. It grows
  from 5% to 29% with size, so near-optimal allocation genuinely matters on M and L. This is what
  Gap 1 measures.

## 3. Could the benchmark favour the proposed agent?

These are the sources of bias we know of, and what we did about each:

| Possible bias | Mitigation | Remaining risk (for the paper's threats-to-validity section) |
|---|---|---|
| The generator only produces feasible scenarios, and the agent's own solvers can reach a feasible plan whenever its model is correct | Deadlines are tighter than the heuristics are guaranteed to meet (v2 witness). The baselines get exactly the same scenarios | On 46 of 50 scenarios the auction alone is feasible, so with a correct model, feasibility is mostly a question of *reading the brief*. That is the point of Gaps 2 and 3, and should be stated openly |
| The agent's capability guide (`CAPABILITY_GUIDE` in `mrta/llm/prompts.py`) was written knowing the generator's phrase banks | The same guide is given to the cross-check reading and the staged extraction. `llm-formulate` gets its own short capability list. The `phase1` agent preset has the guide switched off and can be run as an extra ablation | Phrasing outside the phrase banks (section 4) is not tested |
| The ground truth comes from the same generator that writes the briefs | Briefs are rendered from the model by fixed templates; nothing is hand-edited | Ambiguity in real briefs (missing numbers, contradictions) is not modelled |
| The simulated mission owner answers `ask_human` from the ground truth | It only answers with brief lines that mention the task, robot or place the question names (`MissionOwner`), at most 2 questions per run. `agent:no-g3-clarify` measures how much the questions add | A real person could give more or less helpful answers |
| Prompts tuned on the evaluation set | Prompts were only checked on the 10 dev scenarios. Every reported number uses the 40 test scenarios | - |
| One generator, one domain | - | Results may not transfer to other domains (warehouses, households) |

## 4. How the informal briefs are written

Both briefs are rendered from the same ground-truth model by `mrta/scenarios/nl.py` and carry
exactly the same information. They differ only in how it is expressed:

| Fact | Explicit brief | Informal brief |
|---|---|---|
| Task requirement | Stated: "Needs: thermal camera." | Implied by one of 2 or 3 phrasings per task type (`informal` in `mrta/catalog.py`), e.g. "look for overheating equipment around {loc}" → thermal camera |
| Units | Seconds, metres, kg | Minutes for durations, deadlines, endurance and horizon ("lasts 57 minutes") |
| Deadline | "Deadline: finished by t = 300 s." | "it has to be finished within the first 5 minutes" |
| Precedence | "Can start only after T3 is finished." | "but only once the spare pump (T3) has arrived" or "only once job T3 is done" |
| Robot | Structured: mobility rule, equipment list, endurance | One conversational sentence per robot, chosen from 1 or 2 templates per type ("D1, a quadcopter waiting at base, carries a camera and a thermal camera, cruises at 7.5 m/s ...") |
| Standby | "Keep at least 1 quadcopter on standby: it must not be given any task." | "Keep one quadcopter on standby; don't give it any jobs." |
| Rules | Numbered list | Folded into the opening and closing sentences |

The choice of phrasing is seeded per scenario (`informal-{id}`), so briefs are reproducible.
Measured over the 50 scenarios (`results/benchmark/report.md`):
- Informal briefs are about 20% shorter.
- Only 33% to 38% of the words are shared between the two briefs.
- The informal brief contains 7 to 18 requirement words (camera, carrier, arm, thermal), against
  36 to 101 in the explicit brief. Most requirements must therefore be inferred.

**Example (S-005, task T2).**
- Explicit: "T2: Run a thermal scan of loading dock. Location: loading dock. Duration: 210 s.
  Needs: thermal camera."
- Informal: "(T2) Look for overheating equipment around loading dock - that takes 3.5 minutes on site."

**Limits of the informal style.**
- The templates are still regular; a real site manager would be messier.
- Every number is present. Nothing is vague ("soon", "a bit later"), missing, or contradictory.
- Task ids are kept in both styles so plans can be scored.

Section 3 lists these limits as threats to validity.
