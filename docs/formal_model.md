# Formal model: heterogeneous time-extended task allocation

This is the problem every method in the repository solves and the definition the checker
(`mrta/checker.py`) enforces. Code references are given so each equation can be traced to
the line that implements it.

## 1. Sets and parameters (`mrta/schema.py`)

| Symbol | Meaning | Field |
|---|---|---|
| $\mathcal{L}$ | locations; each $l$ has coordinates $(x_l, y_l)$ and a zone $z_l \in \{\text{outdoor}, \text{roof}, \text{indoor}\}$ | `Location` |
| $\mathcal{C}$ | capabilities $\{\text{camera}, \text{thermal}, \text{carry}, \text{manipulate}\}$ | `Capability` |
| $R$ | robots. Robot $r$ has type $\kappa_r$, mobility $\mu_r \in \{\text{air}, \text{ground}, \text{fixed}\}$, capabilities $C_r \subseteq \mathcal{C}$, speed $v_r$, payload limit $W_r$, endurance $E_r$, start location $o_r$ | `Robot` |
| $T$ | tasks. Task $t$ has location $\ell_t$, required capabilities $K_t \subseteq \mathcal{C}$, payload $w_t$, duration $d_t > 0$, optional deadline $D_t$ ($+\infty$ if none) | `Task` |
| $P \subseteq T \times T$ | precedence: $(a, b) \in P$ means $b$ may start only after $a$ has finished (possibly on another robot) | `Precedence` |
| $\Sigma$ | standby rules $(\kappa, k)$: at least $k$ robots of type $\kappa$ receive no task | `Standby` |
| $H$ | mission horizon: every task finishes by $H$ | `Scenario.horizon` |

## 2. Movement and eligibility (`mrta/geometry.py`)

Reachability: air robots reach every zone except indoor, ground robots every zone except
roof, and a fixed robot only its own station $o_r$.

Travel time between locations $l, l'$:

$$
\tau_r(l, l') =
\begin{cases}
0 & l = l' \\
\lVert (x_{l'} - x_l,\; y_{l'} - y_l) \rVert_2 / v_r & \mu_r = \text{air, both reachable} \\
\lVert (x_{l'} - x_l,\; y_{l'} - y_l) \rVert_1 / v_r & \mu_r = \text{ground, both reachable} \\
+\infty & \text{otherwise}
\end{cases}
$$

Robot $r$ is eligible for task $t$ ($e_{rt} = 1$) iff $K_t \subseteq C_r$, $w_t \le W_r$ and
$\ell_t$ is reachable by $r$.

## 3. Decision and induced schedule (`mrta/schedule.py`)

A plan gives every robot an ordered route $\pi_r = (t_1, \dots, t_{n_r})$, possibly empty.
All robots leave their start at $t = 0$. For consecutive tasks on a route:

$$
a_{t_1} = \tau_r(o_r, \ell_{t_1}), \qquad a_{t_{i+1}} = f_{t_i} + \tau_r(\ell_{t_i}, \ell_{t_{i+1}})
$$

$$
s_t = \max\Big(a_t,\; \max_{(u, t) \in P} f_u\Big), \qquad f_t = s_t + d_t
$$

so a robot waits on site until every predecessor has finished. Robot $r$ is back home at
$\rho_r = f_{t_{n_r}} + \tau_r(\ell_{t_{n_r}}, o_r)$ ($\rho_r = 0$ for an idle robot).
If route orders and $P$ together form a cycle, no schedule exists (deadlock).

## 4. Constraints (`mrta/checker.py`)

| | Constraint | Formal statement |
|---|---|---|
| C1 | coverage | every $t \in T$ appears in exactly one route; only known ids |
| C2 | capability | $t \in \pi_r \Rightarrow K_t \subseteq C_r$ |
| C3 | payload | $t \in \pi_r \Rightarrow w_t \le W_r$ |
| C4 | reachability | $t \in \pi_r \Rightarrow \ell_t$ reachable by $r$ |
| C5 | standby | $\forall (\kappa, k) \in \Sigma:\; \lvert\{ r : \kappa_r = \kappa,\; \pi_r = \emptyset \}\rvert \ge k$ |
| C6 | precedence | the schedule of section 3 exists (no deadlock) |
| C7 | deadlines, horizon | $f_t \le \min(D_t, H)$ for all $t$ |
| C8 | endurance | $\rho_r \le E_r$ for all $r$ |

A plan is **feasible** iff C1 to C8 all hold.

## 5. Objective

$$
\min \; C_{\max} = \max_{t \in T} f_t \qquad \text{(mission completion time, "makespan")}
$$

Total travel $\sum_r \big(\sum_i \tau_r(\cdot) + \text{return leg}\big)$ is reported and used only to break ties.

**Taxonomy.** In Gerkey and Matarić's (2004) terms this is ST-SR-TA: single-task robots,
single-robot tasks, time-extended assignment. Precedence between tasks served by
different robots adds cross-schedule dependencies (the XD class of Korsah et al., 2013).
The problem generalises the heterogeneous vehicle routing problem with time windows, so it
is NP-hard. The Hungarian algorithm is exact only for the instantaneous version (ST-SR-IA);
the repository applies it round by round as a heuristic.

## 6. Exact MILP (`mrta/solvers/milp.py`)

Let $T_r = \{t : e_{rt} = 1\}$ after pruning pairs that cannot work even alone, and add a
start node $S$ and an end node $E$ per robot.

Variables: $y_{rt} \in \{0,1\}$ (robot $r$ does $t$), $x_{rij} \in \{0,1\}$ (robot $r$ goes from
node $i$ to node $j$), $z_r \in \{0,1\}$ (robot $r$ on standby), $s_t \ge 0$, $C_{\max} \ge 0$.

$$
\begin{aligned}
\min\;& C_{\max} + w \sum_{r,i,j} \tau_r(i, j)\, x_{rij} && (w = 10^{-3}) \\
\text{s.t.}\;& \textstyle\sum_r y_{rt} = 1 && \forall t \\
& \textstyle\sum_{j} x_{rSj} = 1, \quad \sum_{i} x_{riE} = 1 && \forall r \\
& \textstyle\sum_{i} x_{rit} = y_{rt} = \sum_{j} x_{rtj} && \forall r, t \in T_r \\
& s_t \ge \tau_r(o_r, \ell_t)\, x_{rSt} && \forall r, t \\
& s_j \ge s_i + d_i + \tau_r(\ell_i, \ell_j) - M_{ij}(1 - x_{rij}) && \forall r, i \ne j \\
& s_b \ge s_a + d_a && \forall (a, b) \in P \\
& s_t + d_t \le \min(D_t, H) && \forall t \\
& s_i + d_i + \tau_r(\ell_i, o_r) \le E_r + M(1 - x_{riE}) && \forall r, i \\
& \textstyle\sum_{r : \kappa_r = \kappa} z_r \ge k, \quad x_{rSE} \ge z_r && \forall (\kappa, k) \in \Sigma \\
& C_{\max} \ge s_t + d_t && \forall t
\end{aligned}
$$

Because every $d_t > 0$, the sequencing constraints also eliminate subtours (MTZ-style).
Big-M values are arc-specific, $M_{ij} = \min(D_i, H) + \tau_r(\ell_i, \ell_j)$. Two families of
valid inequalities tighten the relaxation: for every robot,
$\sum_t (d_t + \min_i \tau_r(i, t))\, y_{rt} \le C_{\max}$ and $\le E_r$ (its tasks plus the cheapest
way into each must fit before the makespan and its endurance). Arcs $i \to j$ are removed
when $(j, i) \in P$ or when $j$ could not meet its deadline even if $i$ came first.

The default backend is HiGHS through PuLP with a time limit (60 s in the experiments).
When the limit is hit, the best plan found is returned with status `feasible` and the
solver's dual bound is logged as `mip_gap`.

## 7. Metrics (`mrta/evaluate.py`, `scripts/summarize.py`)

| Metric | Definition |
|---|---|
| Feasibility rate | share of runs whose plan passes C1 to C8 against the ground truth |
| Optimality gap | $(C_{\max} - C^\star)/C^\star$ over feasible plans; $C^\star$ is the MILP optimum when proven, otherwise the best feasible makespan found by any method |
| Makespan | $C_{\max}$ of the plan's schedule |
| Planning time | wall-clock time from brief to plan, including LLM latency and solver time |
| LLM cost | calls, prompt and completion tokens |
| Model F1 | precision/recall/F1 of the facts in an LLM-written formal model against the ground truth, with per-constraint recall (`mrta/spec_eval.py`) |
| Violations | count of each constraint type broken by infeasible plans |
