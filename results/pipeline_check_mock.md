# Results summary

> **Rows marked `mock` come from the stand-in MockLLM, not a real language model. They only show that the pipeline runs end to end; do not report them as LLM results.**

Reference makespan: MILP optimum for 23 of 26 scenarios, best known otherwise. Gap is computed over feasible plans only.

| Tier | Method | Input | Runs | Feasible | Mean gap | Median gap | Mean plan time | LLM calls | Tokens (in/out) | Model F1 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S | milp | spec | 10 | 100% | 0.0% | 0.0% | 0.03 s | 0.0 | 0/0 | - |
| S | hungarian | spec | 10 | 100% | 15.1% | 0.0% | 0.00 s | 0.0 | 0/0 | - |
| S | auction | spec | 10 | 100% | 9.5% | 0.0% | 0.00 s | 0.0 | 0/0 | - |
| S | llm-direct [mock:mock] | explicit | 10 | 60% | 13.3% | 0.0% | 0.00 s | 1.0 | 799/25 | - |
| S | llm-direct [mock:mock] | informal | 10 | 70% | 2.2% | 0.0% | 0.00 s | 1.0 | 621/24 | - |
| S | llm-pefa [mock:mock] | explicit | 10 | 90% | 17.3% | 0.0% | 0.00 s | 1.5 | 1263/38 | - |
| S | llm-pefa [mock:mock] | informal | 10 | 80% | 24.5% | 0.0% | 0.00 s | 1.5 | 996/38 | - |
| S | llm-formulate [mock:mock] | explicit | 10 | 100% | 0.0% | 0.0% | 0.04 s | 1.0 | 1036/494 | 1.00 |
| S | llm-formulate [mock:mock] | informal | 10 | 70% | 0.0% | 0.0% | 0.03 s | 1.0 | 857/494 | 0.98 |
| S | agent [mock:mock] | explicit | 10 | 100% | 0.0% | 0.0% | 0.03 s | 4.0 | 7394/562 | 0.99 |
| S | agent [mock:mock] | informal | 10 | 100% | 0.0% | 0.0% | 0.03 s | 4.0 | 6682/563 | 1.00 |
| M | milp | spec | 10 | 100% | 0.0% | 0.0% | 0.74 s | 0.0 | 0/0 | - |
| M | hungarian | spec | 10 | 90% | 28.3% | 19.9% | 0.00 s | 0.0 | 0/0 | - |
| M | auction | spec | 10 | 100% | 27.0% | 21.0% | 0.00 s | 0.0 | 0/0 | - |
| M | llm-direct [mock:mock] | explicit | 10 | 40% | 33.1% | 26.0% | 0.00 s | 1.0 | 1128/37 | - |
| M | llm-direct [mock:mock] | informal | 10 | 20% | 63.9% | 63.9% | 0.00 s | 1.0 | 869/37 | - |
| M | llm-pefa [mock:mock] | explicit | 10 | 70% | 19.5% | 16.4% | 0.00 s | 1.6 | 1876/60 | - |
| M | llm-pefa [mock:mock] | informal | 10 | 80% | 42.6% | 43.3% | 0.00 s | 1.9 | 1771/71 | - |
| M | llm-formulate [mock:mock] | explicit | 10 | 60% | 0.0% | 0.0% | 0.72 s | 1.0 | 1364/900 | 0.99 |
| M | llm-formulate [mock:mock] | informal | 10 | 80% | 0.0% | 0.0% | 0.62 s | 1.0 | 1105/897 | 0.99 |
| M | agent [mock:mock] | explicit | 10 | 90% | 3.8% | 0.0% | 0.82 s | 4.2 | 11057/1153 | 0.99 |
| M | agent [mock:mock] | informal | 10 | 80% | 0.0% | 0.0% | 0.81 s | 4.0 | 8910/968 | 0.98 |
| L | milp | spec | 6 | 100% | 0.0% | 0.0% | 43.49 s | 0.0 | 0/0 | - |
| L | hungarian | spec | 6 | 83% | 28.9% | 27.6% | 0.00 s | 0.0 | 0/0 | - |
| L | auction | spec | 6 | 100% | 17.8% | 20.3% | 0.01 s | 0.0 | 0/0 | - |

## Violations in infeasible plans

| Method / input | Violations (count) |
|---|---|
| agent [mock] / explicit | deadlock 1 |
| agent [mock] / informal | capability 1, deadline 2 |
| hungarian / spec | deadline 2 |
| llm-direct [mock] / explicit | capability 6, deadline 1, payload 2, standby 1, standby_conflict 1, unreachable 2 |
| llm-direct [mock] / informal | capability 6, deadline 2, deadlock 1, standby 3, standby_conflict 3, unreachable 4 |
| llm-formulate [mock] / explicit | capability 2, deadline 1 |
| llm-formulate [mock] / informal | capability 2 |
| llm-pefa [mock] / explicit | deadline 4, standby 1, standby_conflict 1 |
| llm-pefa [mock] / informal | deadline 3, deadlock 1 |
