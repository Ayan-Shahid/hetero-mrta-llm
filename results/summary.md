# Results summary

Reference makespan: MILP optimum for 23 of 26 scenarios, best known otherwise. Gap is computed over feasible plans only.

| Tier | Method | Input | Runs | Feasible | Mean gap | Median gap | Mean plan time | LLM calls | Tokens (in/out) | Model F1 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S | milp | spec | 20 | 100% | 0.0% | 0.0% | 0.02 s | 0.0 | 0/0 | - |
| S | hungarian | spec | 20 | 100% | 15.1% | 0.0% | 0.00 s | 0.0 | 0/0 | - |
| S | auction | spec | 20 | 100% | 9.5% | 0.0% | 0.00 s | 0.0 | 0/0 | - |
| M | milp | spec | 20 | 100% | 0.0% | 0.0% | 0.48 s | 0.0 | 0/0 | - |
| M | hungarian | spec | 20 | 90% | 28.3% | 19.9% | 0.00 s | 0.0 | 0/0 | - |
| M | auction | spec | 20 | 100% | 27.0% | 21.0% | 0.00 s | 0.0 | 0/0 | - |
| L | milp | spec | 12 | 100% | 0.0% | 0.0% | 38.91 s | 0.0 | 0/0 | - |
| L | hungarian | spec | 12 | 83% | 28.9% | 27.6% | 0.00 s | 0.0 | 0/0 | - |
| L | auction | spec | 12 | 100% | 17.8% | 20.3% | 0.01 s | 0.0 | 0/0 | - |

## Violations in infeasible plans

| Method / input | Violations (count) |
|---|---|
| hungarian / spec | deadline 4 |
