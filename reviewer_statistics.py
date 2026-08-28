#!/usr/bin/env python3
"""Fail-closed 30-run statistical analysis for the AE response.

The primary experimental unit is the benchmark instance.  Thirty matched seed
runs estimate each instance-method mean; they are not pooled as 3,600
independent observations.  The normality decision and all multiplicity rules
are predeclared below and exported with the results.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import f as f_distribution
from scipy.stats import friedmanchisquare, rankdata, shapiro, ttest_rel, wilcoxon


METHODS = ["pbc_xlns", "without_persistent_cuts", "random_alns"]
METHOD_LABELS = {
    "pbc_xlns": "PBC-XLNS",
    "without_persistent_cuts": "PBC without persistent cuts",
    "random_alns": "Random ALNS",
}
PAIRS = [
    ("pbc_xlns", "random_alns"),
    ("pbc_xlns", "without_persistent_cuts"),
    ("without_persistent_cuts", "random_alns"),
]


def as_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.lower().str.strip().isin({"1", "true", "yes", "t"})


def holm(values: list[float]) -> list[float]:
    p = np.asarray(values, dtype=float)
    order = np.argsort(p)
    adjusted = np.empty_like(p)
    running = 0.0
    for position, original in enumerate(order):
        running = max(running, (len(p) - position) * p[original])
        adjusted[original] = min(1.0, running)
    return adjusted.tolist()


def rank_biserial(difference: np.ndarray) -> float:
    nonzero = difference[~np.isclose(difference, 0.0, rtol=0.0, atol=1e-9)]
    if nonzero.size == 0:
        return 0.0
    ranks = rankdata(np.abs(nonzero), method="average")
    positive = float(ranks[nonzero > 0].sum())
    negative = float(ranks[nonzero < 0].sum())
    return (positive - negative) / (positive + negative)


def bootstrap_ci(values: np.ndarray, seed: int, samples: int = 20000) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = np.empty(samples, dtype=float)
    for start in range(0, samples, 500):
        stop = min(start + 500, samples)
        index = rng.integers(0, len(values), size=(stop - start, len(values)))
        means[start:stop] = values[index].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def repeated_measures_anova(matrix: np.ndarray) -> dict[str, float]:
    n, k = matrix.shape
    grand = float(matrix.mean())
    ss_total = float(((matrix - grand) ** 2).sum())
    ss_method = float(n * ((matrix.mean(axis=0) - grand) ** 2).sum())
    ss_subject = float(k * ((matrix.mean(axis=1) - grand) ** 2).sum())
    ss_error = max(0.0, ss_total - ss_method - ss_subject)
    df_method = k - 1
    df_error = (n - 1) * (k - 1)
    statistic = (ss_method / df_method) / max(ss_error / df_error, 1e-300)
    return {
        "statistic": statistic,
        "df1": float(df_method),
        "df2": float(df_error),
        "p_value": float(f_distribution.sf(statistic, df_method, df_error)),
    }


def require_complete(root: Path) -> pd.DataFrame:
    analysis = root / "analysis"
    integrity_path = analysis / "task_integrity.csv"
    merged_path = analysis / "merged_run_results.csv"
    if not integrity_path.exists() or not merged_path.exists():
        raise RuntimeError("aggregate the complete confirmatory experiment before statistics")
    integrity = pd.read_csv(integrity_path)
    if len(integrity) != 3600 or not as_bool(integrity["complete"]).all():
        raise RuntimeError(
            f"formal analysis requires 3600/3600 complete tasks; observed {as_bool(integrity['complete']).sum()}"
        )
    data = pd.read_csv(merged_path)
    required = {"instance", "seed", "method", "strict_cost", "strict_deadline_enforced"}
    missing = required - set(data)
    if missing:
        raise RuntimeError(f"merged results lack columns: {sorted(missing)}")
    if len(data) != 10800:
        raise RuntimeError(f"expected 10,800 method rows, observed {len(data)}")
    if set(data["method"]) != set(METHODS):
        raise RuntimeError(f"method set differs from preregistration: {sorted(set(data['method']))}")
    counts = data.groupby(["instance", "method"])["seed"].nunique()
    if len(counts) != 360 or not counts.eq(30).all():
        raise RuntimeError("every instance-method cell must contain 30 unique seeds")
    if data.duplicated(["instance", "seed", "method"]).any():
        raise RuntimeError("duplicate instance-seed-method rows detected")
    if not as_bool(data["strict_deadline_enforced"]).all():
        raise RuntimeError("at least one run lacks strict deadline enforcement")
    return data


def analyse(root: Path) -> Path:
    data = require_complete(root)
    output = root / "reviewer_analysis"
    output.mkdir(parents=True, exist_ok=True)

    instance_method = data.groupby(["instance", "method"], as_index=False).agg(
        mean_cost=("strict_cost", "mean"),
        sd_cost=("strict_cost", "std"),
        median_cost=("strict_cost", "median"),
        best_cost=("strict_cost", "min"),
        worst_cost=("strict_cost", "max"),
        seeds=("seed", "nunique"),
        mean_seconds=("search_seconds", "mean"),
        max_overshoot_seconds=("budget_overshoot_seconds", "max"),
    )
    instance_method.to_csv(output / "instance_method_30run_summary.csv", index=False)
    wide = instance_method.pivot(index="instance", columns="method", values="mean_cost")[METHODS].sort_index()

    # Omnibus parametric and nonparametric analyses are both reported.  The
    # predeclared primary route is chosen after Shapiro-Wilk tests of all three
    # paired instance-mean contrasts.
    normality_rows: list[dict[str, Any]] = []
    all_normal = True
    pair_differences: dict[tuple[str, str], np.ndarray] = {}
    for reference, comparator in PAIRS:
        difference = wide[comparator].to_numpy() - wide[reference].to_numpy()
        pair_differences[(reference, comparator)] = difference
        stat, p_value = shapiro(difference)
        normal = bool(p_value >= 0.05)
        all_normal = all_normal and normal
        normality_rows.append({
            "level": "primary_instance_means", "instance": "ALL_120",
            "reference": reference, "comparator": comparator, "n": len(difference),
            "shapiro_w": float(stat), "p_value": float(p_value),
            "normal_at_0_05": normal,
        })

    # Within-instance diagnostics and matched seed tests.  These are secondary;
    # their p-values are Holm-adjusted across the 120 instances for each pair.
    seed_rows: list[dict[str, Any]] = []
    for reference, comparator in PAIRS:
        pair_rows = []
        for instance, block in data[data["method"].isin([reference, comparator])].groupby("instance"):
            paired = block.pivot(index="seed", columns="method", values="strict_cost")[[reference, comparator]]
            difference = paired[comparator].to_numpy() - paired[reference].to_numpy()
            if np.allclose(difference, 0.0, rtol=0.0, atol=1e-9):
                sw_stat, sw_p, test_name, test_stat, raw_p = 1.0, 1.0, "all_ties", 0.0, 1.0
            else:
                sw_stat, sw_p = shapiro(difference)
                if sw_p >= 0.05:
                    tested = ttest_rel(paired[comparator], paired[reference])
                    test_name, test_stat, raw_p = "paired_t", float(tested.statistic), float(tested.pvalue)
                else:
                    tested = wilcoxon(difference, zero_method="pratt", alternative="two-sided", method="auto")
                    test_name, test_stat, raw_p = "wilcoxon_pratt", float(tested.statistic), float(tested.pvalue)
            pair_rows.append({
                "instance": instance, "reference": reference, "comparator": comparator,
                "n": len(difference), "mean_difference_comparator_minus_reference": float(difference.mean()),
                "shapiro_w": float(sw_stat), "shapiro_p": float(sw_p),
                "selected_test": test_name, "test_statistic": test_stat, "p_raw": raw_p,
            })
        adjusted = holm([row["p_raw"] for row in pair_rows])
        for row, value in zip(pair_rows, adjusted):
            row["p_holm_within_pair_120"] = value
            row["significant_0_05"] = bool(value < 0.05)
        seed_rows.extend(pair_rows)
    pd.DataFrame(seed_rows).to_csv(output / "per_instance_30seed_tests.csv", index=False)

    rm = repeated_measures_anova(wide.to_numpy())
    friedman = friedmanchisquare(*(wide[method].to_numpy() for method in METHODS))
    omnibus = pd.DataFrame([
        {"test": "repeated_measures_anova", "primary": all_normal, **rm},
        {"test": "friedman", "primary": not all_normal, "statistic": float(friedman.statistic),
         "df1": float(len(METHODS) - 1), "df2": math.nan, "p_value": float(friedman.pvalue)},
    ])
    omnibus.to_csv(output / "omnibus_tests.csv", index=False)

    posthoc: list[dict[str, Any]] = []
    for pair_index, (reference, comparator) in enumerate(PAIRS):
        difference = pair_differences[(reference, comparator)]
        sw = next(row for row in normality_rows if row["reference"] == reference and row["comparator"] == comparator)
        if sw["normal_at_0_05"]:
            result = ttest_rel(wide[comparator], wide[reference])
            test_name = "paired_t"
            statistic, p_value = float(result.statistic), float(result.pvalue)
        elif np.allclose(difference, 0.0, rtol=0.0, atol=1e-9):
            test_name, statistic, p_value = "all_ties", 0.0, 1.0
        else:
            result = wilcoxon(difference, zero_method="pratt", alternative="two-sided", method="auto")
            test_name, statistic, p_value = "wilcoxon_pratt", float(result.statistic), float(result.pvalue)
        relative = 100.0 * (
            wide[comparator].to_numpy() - wide[reference].to_numpy()
        ) / np.maximum(np.abs(wide[comparator].to_numpy()), 1e-12)
        ci_low, ci_high = bootstrap_ci(relative, seed=20271101 + pair_index)
        tolerance = 1e-9
        posthoc.append({
            "reference": reference, "comparator": comparator,
            "interpretation": "positive effects favour reference (lower cost)",
            "n_instances": len(difference), "normality_p": sw["p_value"],
            "selected_test": test_name, "statistic": statistic, "p_raw": p_value,
            "mean_relative_improvement_pct": float(relative.mean()),
            "ci95_low_pct": ci_low, "ci95_high_pct": ci_high,
            "cohen_dz": float(difference.mean() / difference.std(ddof=1)) if difference.std(ddof=1) > 0 else 0.0,
            "rank_biserial": rank_biserial(difference),
            "wins": int((difference > tolerance).sum()),
            "ties": int((np.abs(difference) <= tolerance).sum()),
            "losses": int((difference < -tolerance).sum()),
        })
    adjusted = holm([row["p_raw"] for row in posthoc])
    for row, value in zip(posthoc, adjusted):
        row["p_holm_3"] = value
        row["significant_0_05"] = bool(value < 0.05)
    pd.DataFrame(normality_rows).to_csv(output / "normality_tests.csv", index=False)
    pd.DataFrame(posthoc).to_csv(output / "posthoc_tests.csv", index=False)

    method_summary = instance_method.groupby("method", as_index=False).agg(
        instances=("instance", "nunique"), mean_instance_cost=("mean_cost", "mean"),
        median_instance_cost=("mean_cost", "median"), mean_within_instance_sd=("sd_cost", "mean"),
        mean_search_seconds=("mean_seconds", "mean"), max_overshoot_seconds=("max_overshoot_seconds", "max"),
    )
    method_summary["mean_rank"] = [
        float(wide.rank(axis=1, method="average")[method].mean()) for method in method_summary["method"]
    ]
    method_summary.to_csv(output / "method_summary.csv", index=False)

    machine = {
        "complete_tasks": 3600,
        "method_rows": 10800,
        "instances": 120,
        "seeds_per_instance_method": 30,
        "primary_unit": "instance mean over 30 matched seeds",
        "normality_alpha": 0.05,
        "omnibus_primary": "repeated_measures_anova" if all_normal else "friedman",
        "posthoc_rule": "paired t if Shapiro p>=0.05, otherwise Wilcoxon-Pratt; Holm over 3 contrasts",
        "normality": normality_rows,
        "omnibus": omnibus.to_dict(orient="records"),
        "posthoc": posthoc,
    }
    (output / "response_values.json").write_text(json.dumps(machine, indent=2), encoding="utf-8")

    lines = [
        "# Confirmatory statistical report", "",
        "This report is generated only after 3,600/3,600 tasks pass provenance, audit, and completeness checks.", "",
        f"Primary omnibus test: **{machine['omnibus_primary']}** (decision fixed by paired-difference normality).", "",
        "## Pairwise results", "",
        "| Reference | Comparator | Test | Mean improvement % (95% CI) | Holm p | W-T-L |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in posthoc:
        lines.append(
            f"| {METHOD_LABELS[row['reference']]} | {METHOD_LABELS[row['comparator']]} | "
            f"{row['selected_test']} | {row['mean_relative_improvement_pct']:.4f} "
            f"[{row['ci95_low_pct']:.4f}, {row['ci95_high_pct']:.4f}] | "
            f"{row['p_holm_3']:.6g} | {row['wins']}-{row['ties']}-{row['losses']} |"
        )
    lines += ["", "Positive improvement and effect-size values favour the reference method.", ""]
    (output / "STATISTICAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment-root", type=Path,
        default=Path(__file__).resolve().parents[1] / "results" / "eo_confirmatory_120x30_strict",
    )
    args = parser.parse_args()
    print(analyse(args.experiment_root.expanduser().resolve()))


if __name__ == "__main__":
    main()
