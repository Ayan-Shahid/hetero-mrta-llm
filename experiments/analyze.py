#!/usr/bin/env python3
"""Turn saved runs into the tables, statistics and figures used in the README and the paper.

    python experiments/analyze.py                         # every model found in results/runs/
    python experiments/analyze.py --models qwen2.5-7b     # only some models

Reads   results/runs/classical/E1.csv and results/runs/<model>/E*.csv
Writes  results/tables/*.md, *.csv, *.tex     (main results, E4 per gap, E5 ablation, E6 efficiency,
                                              gap-resolution table, benchmark summary)
        results/figures/*.png
        results/summary.json                  (every headline number, for the paper)

Conventions
-----------
* Reference makespan = best feasible makespan any classical method found for the scenario
  (the MILP optimum for every scenario where the MILP proved optimality).
* Gap = (makespan - reference) / reference, over feasible plans only.
* Success@5 = feasible and gap <= 5 %.
* A rate per configuration is computed per repeat and reported as mean +- std over repeats.
* Paired tests pair runs on (scenario, brief style, repeat): McNemar's exact test for
  feasibility, Wilcoxon signed-rank for gaps (pairs where both plans are feasible).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
TIERS = ["S", "M", "L"]
NAMES = {
    "milp": "MILP (exact)", "hungarian": "Iterated Hungarian", "auction": "SSI auction",
    "llm-direct": "LLM-direct", "llm-formulate": "LLM-formulate", "llm-pefa": "COHERENT-style PEFA",
    "agent:full": "Agent (full)", "agent:no-g1": "Agent without G1 (no solvers)",
    "agent:no-g2": "Agent without G2 (no verification)", "agent:no-g3": "Agent without G3 (single-pass model, no questions)",
    "agent:no-g2-crosscheck": "Agent without cross-check", "agent:no-g3-clarify": "Agent without questions",
    "agent:no-g1-portfolio": "Agent with MILP only (no portfolio)", "agent:phase1": "Agent (Phase 1 version)",
}
ORDER = list(NAMES)


# ----------------------------------------------------------------------------- loading
def load_runs(runs: Path, models: list[str] | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    cls_path = runs / "classical" / "E1.csv"
    if not cls_path.exists():
        raise SystemExit(f"{cls_path} missing; run: python experiments/run.py --exp E1 --classical-only --split all")
    cls = pd.read_csv(cls_path)
    frames = []
    for d in sorted(p for p in runs.iterdir() if p.is_dir() and p.name not in ("classical", "pilot")):
        if models and d.name not in models:
            continue
        for f in sorted(d.glob("E*.csv")):
            df = pd.read_csv(f)
            if len(df):
                df["model_dir"] = d.name
                frames.append(df)
    llm = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return cls, llm


def key(df: pd.DataFrame) -> pd.Series:
    cfg = df["config"].fillna("").astype(str)
    return np.where(cfg != "", df["method"].astype(str) + ":" + cfg, df["method"].astype(str))


def prepare(cls: pd.DataFrame, llm: pd.DataFrame, split: str) -> tuple[pd.DataFrame, pd.Series]:
    feas = cls[cls["feasible"].astype(str) == "True"]
    ref = feas.groupby("scenario")["makespan"].min()
    cls = cls.assign(key=cls["method"], model_dir="classical", repeat=0, input="spec")
    df = pd.concat([cls, llm], ignore_index=True) if len(llm) else cls
    sys.path.insert(0, str(ROOT))
    from mrta.evaluate import FIELDS

    for c in FIELDS:
        if c not in df:
            df[c] = np.nan
    df["key"] = key(df)
    if split != "all":
        df = df[df["split"] == split]
    df["feasible"] = df["feasible"].astype(str) == "True"
    df["ref"] = df["scenario"].map(ref)
    df["gap"] = np.where(df["feasible"], (df["makespan"] - df["ref"]) / df["ref"], np.nan)
    df["success5"] = df["feasible"] & (df["gap"] <= 0.05 + 1e-9)
    df["tokens"] = df["prompt_tokens"].fillna(0) + df["completion_tokens"].fillna(0)
    return df, ref


# ----------------------------------------------------------------------------- statistics
def mean_std_over_repeats(sub: pd.DataFrame, col: str) -> tuple[float, float, int]:
    per = sub.groupby("repeat")[col].mean()
    n = int(per.size)
    return float(per.mean()), float(per.std(ddof=1)) if n > 1 else 0.0, n


def fmt_ms(m: float, s: float, n: int, pct: bool = True) -> str:
    if m is None or (isinstance(m, float) and math.isnan(m)):
        return "-"
    f = (lambda v: f"{100 * v:.1f}") if pct else (lambda v: f"{v:.2f}")
    return f"{f(m)} ± {f(s)}" if n > 1 else f(m)


def mcnemar(a: pd.Series, b: pd.Series) -> tuple[int, int, float]:
    """a, b: boolean Series on the same index. Returns (a only, b only, exact two-sided p)."""
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    n10 = int((j["a"] & ~j["b"]).sum())
    n01 = int((~j["a"] & j["b"]).sum())
    p = stats.binomtest(n10, n10 + n01, 0.5).pvalue if n10 + n01 else 1.0
    return n10, n01, float(p)


def wilcoxon(a: pd.Series, b: pd.Series) -> tuple[int, float]:
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    d = j["a"] - j["b"]
    if len(d) < 6 or np.allclose(d, 0):
        return len(d), 1.0
    return len(d), float(stats.wilcoxon(j["a"], j["b"], zero_method="zsplit").pvalue)


def boot_ci(values: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(n, len(values)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired(df: pd.DataFrame, k1: str, k2: str, model: str, col: str) -> pd.DataFrame:
    idx = ["scenario", "input", "repeat"]
    a = df[(df.key == k1) & (df.model_dir == model)].set_index(idx)[col]
    b = df[(df.key == k2) & (df.model_dir == model)].set_index(idx)[col]
    return pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()


# ----------------------------------------------------------------------------- output helpers
def write_table(rows: list[dict], name: str, out: Path, caption: str = "") -> pd.DataFrame:
    t = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / f"{name}.csv", index=False)
    md = t.to_markdown(index=False) if len(t) else "_no data yet_"
    (out / f"{name}.md").write_text((f"**{caption}**\n\n" if caption else "") + md + "\n")
    if len(t):
        cols = "l" * len(t.columns)
        lines = [r"\begin{tabular}{" + cols + "}", r"\toprule",
                 " & ".join(_tex(c) for c in t.columns) + r" \\", r"\midrule"]
        for _, r in t.iterrows():
            lines.append(" & ".join(_tex(v) for v in r.values) + r" \\")
        lines += [r"\bottomrule", r"\end{tabular}"]
        (out / f"{name}.tex").write_text("\n".join(lines) + "\n")
    return t


def _tex(v) -> str:
    s = "-" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)
    return (s.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")
            .replace("±", r"$\pm$").replace("→", r"$\rightarrow$").replace("#", r"\#").replace("≤", r"$\le$")
            .replace("≥", r"$\ge$").replace("Δ", r"$\Delta$"))


def name(k: str) -> str:
    return NAMES.get(k, k)


# ----------------------------------------------------------------------------- tables
def main_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    rows = []
    groups = [("classical", k) for k in ("milp", "hungarian", "auction")]
    llm_models = sorted(m for m in df.model_dir.unique() if m != "classical")
    for m in llm_models:
        for k in ORDER[3:]:
            if ((df.model_dir == m) & (df.key == k)).any():
                groups.append((m, k))
    for m, k in groups:
        for tier in TIERS + ["all"]:
            sub = df[(df.model_dir == m) & (df.key == k)]
            if tier != "all":
                sub = sub[sub.tier == tier]
            if not len(sub):
                continue
            fm, fs, n = mean_std_over_repeats(sub, "feasible")
            sm, ss, _ = mean_std_over_repeats(sub, "success5")
            gm = sub["gap"].mean()
            rows.append({"Model": m, "Method": name(k), "Tier": tier, "Runs": len(sub), "Repeats": n,
                         "Feasible %": fmt_ms(fm, fs, n), "Success@5 %": fmt_ms(sm, ss, n),
                         "Mean gap %": "-" if math.isnan(gm) else f"{100 * gm:.1f}",
                         "Median gap %": "-" if sub["gap"].isna().all() else f"{100 * sub['gap'].median():.1f}",
                         "Plan time s": f"{sub['plan_time_s'].median():.1f}",
                         "LLM calls": f"{sub['llm_calls'].mean():.1f}" if m != "classical" else "0"})
            summary.setdefault("main", {}).setdefault(m, {}).setdefault(k, {})[tier] = {
                "feasible_mean": fm, "feasible_std": fs, "repeats": n, "runs": int(len(sub)),
                "success5_mean": sm, "gap_mean": None if math.isnan(gm) else float(gm)}
    write_table(rows, "main_results", tables, "Main results on the test split (mean ± std over repeats)")
    compact = []
    for m, k in groups:
        sub = df[(df.model_dir == m) & (df.key == k)]
        rec = {"Model": m, "Method": name(k)}
        for tier in TIERS:
            st = sub[sub.tier == tier]
            rec[f"Feasible {tier}"] = "-" if not len(st) else f"{100 * st['feasible'].mean():.0f}%"
        fm, fs, n = mean_std_over_repeats(sub, "feasible")
        sm, ss, _ = mean_std_over_repeats(sub, "success5")
        rec["Feasible (all)"] = fmt_ms(fm, fs, n)
        rec["Success@5 (all)"] = fmt_ms(sm, ss, n)
        rec["Mean gap"] = "-" if sub["gap"].isna().all() else f"{100 * sub['gap'].mean():.1f}%"
        rec["Tokens/run"] = "0" if m == "classical" else f"{sub['tokens'].mean():.0f}"
        rec["Repeats"] = n
        compact.append(rec)
    write_table(compact, "main_results_compact", tables, "Test split: feasible plans by tier, success@5 and gap "
                "(mean ± std over repeats)")


def by_style_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        for k in ORDER[3:]:
            sub = df[(df.model_dir == m) & (df.key == k)]
            if not len(sub):
                continue
            rec = {"Model": m, "Method": name(k)}
            vals = {}
            for style in ("explicit", "informal"):
                s = sub[sub.input == style]
                fm, fs, n = mean_std_over_repeats(s, "feasible") if len(s) else (float("nan"), 0, 0)
                f1 = s["spec_f1"].mean() if "spec_f1" in s and s["spec_f1"].notna().any() else float("nan")
                rec[f"Feasible % ({style})"] = fmt_ms(fm, fs, n)
                rec[f"Model F1 ({style})"] = "-" if math.isnan(f1) else f"{f1:.3f}"
                vals[style] = fm
            a = sub[sub.input == "explicit"].set_index(["scenario", "repeat"])["feasible"]
            b = sub[sub.input == "informal"].set_index(["scenario", "repeat"])["feasible"]
            n10, n01, p = mcnemar(a, b)
            drop = vals.get("explicit", float("nan")) - vals.get("informal", float("nan"))
            rec["Drop (pp)"] = "-" if math.isnan(drop) else f"{100 * drop:.1f}"
            rec["McNemar p"] = f"{p:.3g}"
            rows.append(rec)
            summary.setdefault("by_style", {}).setdefault(m, {})[k] = {"drop": None if math.isnan(drop) else drop,
                                                                     "p": p, "explicit_only": n10,
                                                                     "informal_only": n01}
    write_table(rows, "E4_gap3_explicit_vs_informal", tables,
                "Gap 3: explicit vs informal briefs (paired by scenario and repeat)")


def recall_table(df: pd.DataFrame, tables: Path) -> None:
    cols = [c for c in ("recall_task_req", "recall_task_payload", "recall_task_duration", "recall_task_deadline",
                        "recall_precedence", "recall_standby", "recall_robot_cap", "recall_horizon") if c in df]
    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        for k in ("llm-formulate", "agent:full", "agent:no-g3", "agent:no-g2"):
            for style in ("explicit", "informal"):
                s = df[(df.model_dir == m) & (df.key == k) & (df.input == style)]
                if not len(s) or "spec_f1" not in s or s["spec_f1"].isna().all():
                    continue
                rec = {"Model": m, "Method": name(k), "Brief": style, "F1": f"{s['spec_f1'].mean():.3f}"}
                for c in cols:
                    v = s[c].mean()
                    rec[c.replace("recall_", "")] = "-" if math.isnan(v) else f"{v:.2f}"
                rows.append(rec)
    write_table(rows, "E4_gap3_recall_by_constraint", tables,
                "Model-extraction recall by fact type (1.00 = every fact of that type recovered)")


def gap1_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        for other in ("llm-pefa", "llm-direct", "llm-formulate"):
            pr_f = paired(df, "agent:full", other, m, "feasible")
            if not len(pr_f):
                continue
            n10, n01, p = mcnemar(pr_f["a"].astype(bool), pr_f["b"].astype(bool))
            pr_g = paired(df, "agent:full", other, m, "gap")
            n_g, p_g = wilcoxon(pr_g["a"], pr_g["b"]) if len(pr_g) else (0, 1.0)
            diff = (pr_f["a"].astype(float) - pr_f["b"].astype(float)).values
            lo, hi = boot_ci(diff)
            rows.append({"Model": m, "Comparison": f"Agent (full) vs {name(other)}", "Pairs": len(pr_f),
                         "Feasible Δ (pp)": f"{100 * diff.mean():.1f}",
                         "95% CI (pp)": "-" if math.isnan(lo) else f"[{100 * lo:.1f}, {100 * hi:.1f}]",
                         "Agent-only / other-only": f"{n10} / {n01}", "McNemar p": f"{p:.3g}",
                         "Gap pairs": n_g, "Median gap Δ (pp)": "-" if not len(pr_g) else
                         f"{100 * (pr_g['a'] - pr_g['b']).median():.1f}", "Wilcoxon p": f"{p_g:.3g}"})
            summary.setdefault("gap1_tests", {}).setdefault(m, {})[other] = {
                "pairs": int(len(pr_f)), "feasible_delta": float(diff.mean()), "ci": [lo, hi], "mcnemar_p": p,
                "wilcoxon_p": p_g}
    # classical reference rows: how far the heuristics are from optimal
    for k in ("hungarian", "auction"):
        s = df[df.key == k]
        rows.append({"Model": "classical", "Comparison": f"{name(k)} vs reference", "Pairs": len(s),
                     "Feasible Δ (pp)": f"{100 * (s['feasible'].mean() - 1):.1f}", "95% CI (pp)": "-",
                     "Agent-only / other-only": "-", "McNemar p": "-", "Gap pairs": int(s["gap"].notna().sum()),
                     "Median gap Δ (pp)": f"{100 * s['gap'].median():.1f}", "Wilcoxon p": "-"})
    write_table(rows, "E4_gap1_optimality_tests", tables,
                "Gap 1: paired comparisons on the test split (Δ = agent minus other)")


def gap2_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    kinds = ["capability", "payload", "unreachable", "deadline", "horizon", "endurance", "standby",
             "standby_conflict", "deadlock", "missing", "duplicate", "unknown_robot", "unknown_task"]
    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        for k in ORDER[3:]:
            s = df[(df.model_dir == m) & (df.key == k)]
            if not len(s):
                continue
            counts = {kk: 0 for kk in kinds}
            for v in s["violations"].fillna("{}"):
                try:
                    for kk, n in json.loads(v).items():
                        counts[kk] = counts.get(kk, 0) + n
                except (TypeError, ValueError):
                    pass
            rec = {"Model": m, "Method": name(k), "Runs": len(s), "Infeasible %": f"{100 * (1 - s['feasible'].mean()):.1f}"}
            for kk in ("capability", "payload", "unreachable", "deadline", "endurance", "standby", "deadlock",
                       "missing"):
                rec[kk] = counts.get(kk, 0)
            rows.append(rec)
    write_table(rows, "E4_gap2_violations_by_type", tables, "Gap 2: ground-truth violations by type (total counts)")

    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        s = df[(df.model_dir == m) & (df.key == "agent:full")]
        if not len(s) or "disagreements_first" not in s:
            continue
        d1 = s["disagreements_first"].dropna()
        f_first, f_final = s["spec_f1_first"].dropna(), s["spec_f1"].dropna()
        improved = (s["spec_f1"] > s["spec_f1_first"] + 1e-9).sum() if "spec_f1_first" in s else 0
        worse = (s["spec_f1"] < s["spec_f1_first"] - 1e-9).sum() if "spec_f1_first" in s else 0
        gate = s["gate_blocks"].fillna(0)
        rows.append({"Model": m, "Runs": len(s), "Runs with cross-check disagreements": int((d1 > 0).sum()),
                     "Mean disagreements": f"{d1.mean():.2f}" if len(d1) else "-",
                     "Model F1 before → after cross-check": f"{f_first.mean():.3f} → {f_final.mean():.3f}"
                     if len(f_first) else "-",
                     "Runs where F1 improved / worsened": f"{improved} / {worse}",
                     "Plans blocked by the gate": int((gate > 0).sum())})
        summary.setdefault("gap2_crosscheck", {})[m] = {"runs": int(len(s)), "with_disagreements": int((d1 > 0).sum()),
                                                       "f1_first": float(f_first.mean()) if len(f_first) else None,
                                                       "f1_final": float(f_final.mean()) if len(f_final) else None,
                                                       "improved": int(improved), "worsened": int(worse)}
    write_table(rows, "E4_gap2_crosscheck", tables, "Gap 2: what the cross-check and the plan gate caught (agent, full)")


def ablation_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        full = df[(df.model_dir == m) & (df.key == "agent:full")]
        if not len(full):
            continue
        for k in ["agent:full", "agent:no-g1", "agent:no-g2", "agent:no-g2-crosscheck", "agent:no-g3",
                  "agent:no-g3-clarify", "agent:no-g1-portfolio"]:
            s = df[(df.model_dir == m) & (df.key == k)]
            if not len(s):
                continue
            reps = sorted(s["repeat"].unique())
            f_full = full[full.repeat.isin(reps)]
            rec = {"Model": m, "Configuration": name(k), "Runs": len(s),
                   "Feasible %": f"{100 * s['feasible'].mean():.1f}", "Success@5 %": f"{100 * s['success5'].mean():.1f}",
                   "Mean gap %": "-" if s["gap"].isna().all() else f"{100 * s['gap'].mean():.1f}",
                   "Model F1": "-" if s["spec_f1"].isna().all() else f"{s['spec_f1'].mean():.3f}",
                   "Tokens/run": f"{s['tokens'].mean():.0f}", "LLM calls/run": f"{s['llm_calls'].mean():.1f}"}
            if k != "agent:full":
                pr = paired(df[df.repeat.isin(reps)], "agent:full", k, m, "feasible")
                n10, n01, p = mcnemar(pr["a"].astype(bool), pr["b"].astype(bool))
                rec["Δ feasible vs full (pp)"] = f"{100 * (s['feasible'].mean() - f_full['feasible'].mean()):.1f}"
                rec["McNemar p"] = f"{p:.3g}"
                summary.setdefault("ablation", {}).setdefault(m, {})[k] = {
                    "feasible": float(s["feasible"].mean()), "full": float(f_full["feasible"].mean()), "p": p}
            else:
                rec["Δ feasible vs full (pp)"], rec["McNemar p"] = "-", "-"
            rows.append(rec)
    write_table(rows, "E5_ablation", tables, "E5: removing one gap component at a time (same runs, paired)")


def efficiency_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    rows = []
    for m in sorted(x for x in df.model_dir.unique() if x != "classical"):
        for k in ORDER[3:]:
            s = df[(df.model_dir == m) & (df.key == k)]
            if not len(s):
                continue
            pf = s["parse_failures"].fillna(0) if "parse_failures" in s else pd.Series(0, index=s.index)
            errors = s["status"].isin(["error", "parse_error", "spec_invalid", "no_plan"]).mean()
            rows.append({"Model": m, "Method": name(k), "Runs": len(s),
                         "LLM calls": f"{s['llm_calls'].mean():.1f}",
                         "Prompt tokens": f"{s['prompt_tokens'].mean():.0f}",
                         "Completion tokens": f"{s['completion_tokens'].mean():.0f}",
                         "LLM latency s": f"{s['llm_latency_s'].mean():.1f}",
                         "Solver time s": f"{s['solver_time_s'].mean():.2f}",
                         "Plan time s (median)": f"{s['plan_time_s'].median():.1f}",
                         "Tool calls": "-" if "n_tool_calls" not in s or s["n_tool_calls"].isna().all()
                         else f"{s['n_tool_calls'].mean():.1f}",
                         "Format failures/run": f"{pf.mean():.2f}", "No-plan or error %": f"{100 * errors:.1f}",
                         "Cost USD/run": f"{s['cost_usd'].fillna(0).mean():.4f}"})
    write_table(rows, "E6_efficiency", tables, "E6: cost, latency, tool use and reliability per run")


def classical_table(df: pd.DataFrame, tables: Path, summary: dict) -> None:
    rows = []
    for k in ("milp", "hungarian", "auction"):
        for tier in TIERS:
            s = df[(df.key == k) & (df.tier == tier)]
            if not len(s):
                continue
            rows.append({"Method": name(k), "Tier": tier, "Scenarios": len(s), "Feasible": f"{int(s['feasible'].sum())}/{len(s)}",
                         "Mean gap %": "-" if s["gap"].isna().all() else f"{100 * s['gap'].mean():.1f}",
                         "Median gap %": "-" if s["gap"].isna().all() else f"{100 * s['gap'].median():.1f}",
                         "Mean time s": f"{s['plan_time_s'].mean():.2f}",
                         "Proved optimal": "-" if k != "milp" else f"{int((s['status'] == 'optimal').sum())}/{len(s)}"})
            summary.setdefault("classical", {}).setdefault(k, {})[tier] = {
                "feasible": int(s["feasible"].sum()), "n": int(len(s)),
                "gap_mean": None if s["gap"].isna().all() else float(s["gap"].mean())}
    write_table(rows, "E1_classical", tables, "E1: classical allocators on the ground-truth model")


def gap_resolution(df: pd.DataFrame, tables: Path, summary: dict, model: str | None) -> None:
    """The gap-resolution table required by the brief (README and paper)."""
    models = [m for m in sorted(df.model_dir.unique()) if m != "classical"]
    if not models:
        write_table([], "gap_resolution", tables)
        return
    m = model if model in models else models[0]
    s = df[df.model_dir == m]

    def rate(k, col="feasible", style=None):
        x = s[s.key == k]
        if style:
            x = x[x.input == style]
        return x[col].mean() if len(x) else float("nan")

    def pct(v):
        return "-" if v is None or math.isnan(v) else f"{100 * v:.1f}%"

    rows = []
    # Gap 1: feasibility and success@5 vs the base paper approach
    pe, ag = rate("llm-pefa", "success5"), rate("agent:full", "success5")
    pf, af = rate("llm-pefa"), rate("agent:full")
    t1 = summary.get("gap1_tests", {}).get(m, {}).get("llm-pefa", {})
    verdict1 = _verdict(af - pf if not math.isnan(af - pf) else float("nan"), t1.get("mcnemar_p", 1.0),
                        target=rate("agent:full", "gap") <= 0.05 if not math.isnan(rate("agent:full", "gap")) else False)
    rows.append({"Gap": "Gap 1: no comparison with optimal / classical allocation",
                 "Method / component": "G1 verified solver portfolio (mrta/gaps/g1_portfolio.py)",
                 "Metric": "Feasible plans; success@5 (feasible and ≤5% above optimum)",
                 "Base paper result": f"{pct(pf)}; {pct(pe)}", "Our result": f"{pct(af)}; {pct(ag)}",
                 "Addressed?": verdict1})
    # Gap 2: infeasible-plan rate, with and without verification
    nf, n2 = 1 - rate("agent:full"), 1 - rate("agent:no-g2")
    p_e = 1 - pf if not math.isnan(pf) else float("nan")
    abl = summary.get("ablation", {}).get(m, {}).get("agent:no-g2", {})
    verdict2 = _verdict((n2 - nf) if not math.isnan(n2) else (p_e - nf), abl.get("p", 1.0),
                        target=(not math.isnan(p_e)) and nf <= p_e / 2)
    rows.append({"Gap": "Gap 2: plans and extracted models are not formally verified",
                 "Method / component": "G2 cross-check + plan gate (mrta/gaps/g2_verify.py)",
                 "Metric": "Infeasible-plan rate (ground truth)",
                 "Base paper result": pct(p_e), "Our result": f"{pct(nf)} (without G2: {pct(n2)})",
                 "Addressed?": verdict2})
    # Gap 3: explicit -> informal drop
    def drop(k):
        return rate(k, style="explicit") - rate(k, style="informal")
    d_pe, d_ag = drop("llm-pefa"), drop("agent:full")
    st = summary.get("by_style", {}).get(m, {}).get("agent:full", {})
    p3 = drop_test(s, "agent:full", "llm-pefa")
    summary.setdefault("gap3_drop_test", {})[m] = {"agent_drop": d_ag, "pefa_drop": d_pe, "wilcoxon_p": p3}
    verdict3 = _verdict(d_pe - d_ag if not math.isnan(d_pe - d_ag) else float("nan"), p3,
                        target=(not math.isnan(d_ag)) and d_ag <= 0.05)
    rows.append({"Gap": "Gap 3: informal language barely tested",
                 "Method / component": "G3 staged extraction + clarification (mrta/gaps/g3_language.py)",
                 "Metric": "Drop in feasible plans, explicit → informal brief",
                 "Base paper result": "-" if math.isnan(d_pe) else f"{100 * d_pe:.1f} pp",
                 "Our result": "-" if math.isnan(d_ag) else f"{100 * d_ag:.1f} pp (McNemar p = {st.get('p', float('nan')):.3g})",
                 "Addressed?": verdict3})
    t = write_table(rows, "gap_resolution", tables, f"Gap-resolution table (model: {m}; base paper = our "
                    "reproduction of COHERENT's PEFA loop on the same test split)")
    summary["gap_resolution"] = {"model": m, "rows": t.to_dict(orient="records")}


def drop_test(s: pd.DataFrame, k1: str, k2: str) -> float:
    """Wilcoxon test that method k1 loses less than k2 from explicit to informal briefs.

    Per (scenario, repeat) the drop is feasible(explicit) - feasible(informal) in {-1, 0, 1};
    the test compares the paired drops of the two methods.
    """
    def drops(k):
        x = s[s.key == k].pivot_table(index=["scenario", "repeat"], columns="input", values="feasible",
                                      aggfunc="first")
        if not {"explicit", "informal"} <= set(x.columns):
            return pd.Series(dtype=float)
        return (x["explicit"].astype(float) - x["informal"].astype(float)).rename(k)
    a, b = drops(k1), drops(k2)
    _, p = wilcoxon(a, b)
    return p


def _verdict(improvement: float, p: float, target: bool) -> str:
    if improvement is None or math.isnan(improvement) or improvement <= 0:
        return "Not"
    if p < 0.05 and target:
        return "Fully"
    return "Partially"


def benchmark_table(df: pd.DataFrame, tables: Path) -> None:
    path = ROOT / "results" / "benchmark" / "difficulty.csv"
    if path.exists():
        d = pd.read_csv(path)
        rows = []
        for tier in TIERS:
            s = d[d.tier == tier]
            rows.append({"Tier": tier, "Scenarios": len(s), "Robots": int(s.robots.mean()), "Tasks": int(s.tasks.mean()),
                         "Deadlines/scenario": f"{s.deadlines.mean():.1f}", "Precedence/scenario": f"{s.precedence.mean():.1f}",
                         "With standby": f"{int((s.standby > 0).sum())}/{len(s)}",
                         "Eligible robots/task": f"{s.eligible_per_task.mean():.2f}",
                         "Random-plan feasible %": f"{100 * s.random_feasible.mean():.1f}",
                         "Auction feasible": f"{int(s.auction_feasible.sum())}/{len(s)}"})
        write_table(rows, "benchmark_summary", tables, "Benchmark v2 at a glance")


# ----------------------------------------------------------------------------- figures
def figures(df: pd.DataFrame, figs: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 11, "axes.titlesize": 12, "figure.dpi": 150, "savefig.bbox": "tight"})
    figs.mkdir(parents=True, exist_ok=True)
    models = [m for m in sorted(df.model_dir.unique()) if m != "classical"]
    main_keys = ["auction", "hungarian", "llm-direct", "llm-formulate", "llm-pefa", "agent:full"]
    for m in models or [None]:
        fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
        for ax, col, title in ((axes[0], "feasible", "Feasible plans (%)"), (axes[1], "success5", "Success@5 (%)")):
            width = 0.13
            for i, k in enumerate(main_keys):
                vals = []
                for tier in TIERS:
                    s = df[(df.key == k) & (df.tier == tier)]
                    if k not in ("auction", "hungarian", "milp"):
                        s = s[s.model_dir == m]
                    vals.append(100 * s[col].mean() if len(s) else 0)
                ax.bar(np.arange(3) + (i - 2.5) * width, vals, width, label=name(k))
            ax.set_xticks(range(3), [f"Tier {t}" for t in TIERS])
            ax.set_ylim(0, 105)
            ax.set_title(title)
            ax.grid(axis="y", alpha=0.3)
        axes[1].legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9)
        tag = m or "classical"
        fig.suptitle(f"Test split, model: {tag}" if m else "Test split, classical only")
        fig.savefig(figs / f"fig_feasibility_by_tier_{tag}.png")
        plt.close(fig)

        if m is None:
            continue
        # explicit vs informal
        fig, ax = plt.subplots(figsize=(7, 3.6))
        keys = [k for k in ("llm-direct", "llm-formulate", "llm-pefa", "agent:no-g3", "agent:full")
                if ((df.key == k) & (df.model_dir == m)).any()]
        for j, style in enumerate(("explicit", "informal")):
            vals = [100 * df[(df.key == k) & (df.model_dir == m) & (df.input == style)]["feasible"].mean() for k in keys]
            ax.bar(np.arange(len(keys)) + (j - 0.5) * 0.38, vals, 0.38, label=f"{style} brief")
        ax.set_xticks(range(len(keys)), [name(k).replace(" (", "\n(") for k in keys], fontsize=9)
        ax.set_ylabel("Feasible plans (%)")
        ax.set_ylim(0, 105)
        ax.legend()
        ax.grid(axis="y", alpha=0.3)
        ax.set_title(f"Explicit vs informal briefs ({m})")
        fig.savefig(figs / f"fig_explicit_vs_informal_{m}.png")
        plt.close(fig)

        # ablation
        keys = [k for k in ("agent:full", "agent:no-g1", "agent:no-g2", "agent:no-g3", "agent:no-g2-crosscheck",
                            "agent:no-g3-clarify") if ((df.key == k) & (df.model_dir == m)).any()]
        if len(keys) > 1:
            fig, ax = plt.subplots(figsize=(7, 3.4))
            vals = [100 * df[(df.key == k) & (df.model_dir == m) & (df.repeat == 0)]["feasible"].mean() for k in keys]
            ax.barh(range(len(keys)), vals)
            ax.set_yticks(range(len(keys)), [name(k) for k in keys], fontsize=9)
            ax.invert_yaxis()
            ax.set_xlabel("Feasible plans (%), repeat 0")
            ax.set_xlim(0, 105)
            ax.grid(axis="x", alpha=0.3)
            ax.set_title(f"Ablation ({m})")
            fig.savefig(figs / f"fig_ablation_{m}.png")
            plt.close(fig)

        # efficiency: tokens vs feasibility
        fig, ax = plt.subplots(figsize=(6, 3.6))
        for k in ORDER[3:]:
            s = df[(df.key == k) & (df.model_dir == m)]
            if len(s):
                ax.scatter(s["tokens"].mean() / 1000, 100 * s["feasible"].mean(), s=60)
                ax.annotate(name(k), (s["tokens"].mean() / 1000, 100 * s["feasible"].mean()), fontsize=8,
                            xytext=(4, 4), textcoords="offset points")
        ax.set_xlabel("Tokens per run (thousands)")
        ax.set_ylabel("Feasible plans (%)")
        ax.grid(alpha=0.3)
        ax.set_title(f"Cost vs reliability ({m})")
        fig.savefig(figs / f"fig_tokens_vs_feasibility_{m}.png")
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default="results/runs")
    ap.add_argument("--models", default="", help="comma list of model folders (default: all)")
    ap.add_argument("--split", default="test")
    ap.add_argument("--primary", default="", help="model used for the gap-resolution table")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    models = [m for m in args.models.split(",") if m] or None
    cls, llm = load_runs(ROOT / args.runs, models)
    df, _ = prepare(cls, llm, args.split)
    tables, figs = ROOT / args.out / "tables", ROOT / args.out / "figures"
    summary: dict = {"split": args.split, "n_runs": int(len(df)),
                     "models": sorted(m for m in df.model_dir.unique() if m != "classical")}
    classical_table(df, tables, summary)
    benchmark_table(df, tables)
    main_table(df, tables, summary)
    if len(llm):
        by_style_table(df, tables, summary)
        recall_table(df, tables)
        gap1_table(df, tables, summary)
        gap2_table(df, tables, summary)
        ablation_table(df, tables, summary)
        efficiency_table(df, tables, summary)
        gap_resolution(df, tables, summary, args.primary or None)
    figures(df, figs)
    (ROOT / args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    print(f"tables -> {tables}\nfigures -> {figs}\nsummary -> {ROOT / args.out / 'summary.json'}")


if __name__ == "__main__":
    sys.exit(main())
