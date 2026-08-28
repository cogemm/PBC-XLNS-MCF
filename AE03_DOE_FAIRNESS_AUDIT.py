#!/usr/bin/env python3
"""AE Comment 3: independently audit equal-budget DoE calibration.

The script verifies all 54 OA configurations (27 PBC + 27 Random ALNS),
recomputes the selection criterion, checks the frozen confirmatory settings,
and estimates calibration-selection stability by block bootstrap.  It does not
rerun calibration or change any result.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pandas as pd
    import yaml
except ImportError as exc:
    raise SystemExit("Install the locked project requirements before running this audit.") from exc


BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20271103
MAX_ACCEPTABLE_CALIBRATION_OVERSHOOT_SECONDS = 5.0


def locate_root() -> Path:
    for root in [Path.cwd(), Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (root / "scripts" / "eo_doe.py").is_file() and (root / "calibration").is_dir():
            return root.resolve()
    raise SystemExit("Project root not found.")


def as_bool(series: "pd.Series") -> "pd.Series":
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "t"})


def same(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def bootstrap_winner_probability(relative: "pd.DataFrame", seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    values = relative.to_numpy(dtype=float)
    columns = np.asarray(relative.columns, dtype=object)
    counts = {str(column): 0 for column in columns}
    for _ in range(BOOTSTRAP_SAMPLES):
        indices = rng.integers(0, len(values), size=len(values))
        winner = str(columns[int(np.argmin(values[indices].mean(axis=0)))])
        counts[winner] += 1
    return {key: value / BOOTSTRAP_SAMPLES for key, value in sorted(counts.items(), key=lambda item: -item[1])}


def analyse_family(root: Path, algorithm: str, expected_method: str) -> dict[str, Any]:
    frames = []
    experiment_rows = []
    for design_number in range(1, 28):
        design_id = f"d{design_number:02d}"
        experiment = root / "results" / f"eo_cal_{algorithm}_{design_id}"
        integrity_path = experiment / "analysis" / "task_integrity.csv"
        merged_path = experiment / "analysis" / "merged_run_results.csv"
        if not integrity_path.exists() or not merged_path.exists():
            raise RuntimeError(f"missing aggregate evidence for {experiment.name}")
        integrity = pd.read_csv(integrity_path)
        data = pd.read_csv(merged_path)
        complete = len(integrity) == 36 and as_bool(integrity["complete"]).all()
        methods_ok = set(data["method"]) == {expected_method}
        rows_ok = len(data) == 36
        seeds_ok = data["seed"].nunique() == 3
        instances_ok = data["instance"].nunique() == 12
        audits = [
            "feasible", "balanced", "strongly_connected", "eulerian",
            "directed_required_covered", "undirected_required_covered", "cost_consistent",
        ]
        audits_ok = all(column in data and as_bool(data[column]).all() for column in audits)
        max_overshoot = float(data["budget_overshoot_seconds"].max())
        experiment_rows.append({
            "design_id": design_id, "complete": bool(complete), "rows_ok": rows_ok,
            "methods_ok": methods_ok, "seeds_ok": seeds_ok, "instances_ok": instances_ok,
            "audits_ok": audits_ok, "max_overshoot_seconds": max_overshoot,
        })
        part = data[["instance", "seed", "strict_cost"]].copy()
        part["design_id"] = design_id
        frames.append(part)

    all_data = pd.concat(frames, ignore_index=True)
    wide = all_data.pivot(index=["instance", "seed"], columns="design_id", values="strict_cost").sort_index()
    block_best = wide.min(axis=1)
    relative = 100.0 * wide.sub(block_best, axis=0).div(block_best.abs().clip(lower=1e-12), axis=0)
    ranks = wide.rank(axis=1, method="average")
    recomputed = pd.DataFrame({
        "mean_relative_deviation_pct": relative.mean(axis=0),
        "median_relative_deviation_pct": relative.median(axis=0),
        "mean_rank": ranks.mean(axis=0),
    }).reset_index().rename(columns={"index": "design_id"})
    recomputed = recomputed.sort_values(
        ["mean_relative_deviation_pct", "mean_rank", "design_id"], kind="mergesort"
    ).reset_index(drop=True)
    winner = str(recomputed.iloc[0]["design_id"])
    return {
        "algorithm": algorithm,
        "method": expected_method,
        "configurations": 27,
        "blocks_per_configuration": 36,
        "method_runs": len(all_data),
        "winner_recomputed": winner,
        "winner_score": recomputed.iloc[0].to_dict(),
        "runner_evidence": experiment_rows,
        "all_complete": all(
            row["complete"] and row["rows_ok"] and row["methods_ok"]
            and row["seeds_ok"] and row["instances_ok"] and row["audits_ok"]
            and row["max_overshoot_seconds"] <= MAX_ACCEPTABLE_CALIBRATION_OVERSHOOT_SECONDS
            for row in experiment_rows
        ),
        "maximum_overshoot_seconds": max(row["max_overshoot_seconds"] for row in experiment_rows),
        "bootstrap_winner_probability": bootstrap_winner_probability(relative, BOOTSTRAP_SEED + (0 if algorithm == "pbc" else 1)),
        "selection_table": recomputed.to_dict(orient="records"),
    }


def main() -> None:
    root = locate_root()
    out = root / "results" / "revision_evidence"
    out.mkdir(parents=True, exist_ok=True)
    protocol = json.loads((root / "calibration" / "CALIBRATION_PROTOCOL.json").read_text(encoding="utf-8"))
    selected = json.loads((root / "calibration" / "selected_parameters.json").read_text(encoding="utf-8"))
    confirmatory = yaml.safe_load((root / "configs" / "eo_confirmatory_120x30_strict.yaml").read_text(encoding="utf-8"))

    pbc = analyse_family(root, "pbc", "pbc_xlns")
    alns = analyse_family(root, "alns", "random_alns")
    selected_match = (
        pbc["winner_recomputed"] == selected["pbc"]["design_id"]
        and alns["winner_recomputed"] == selected["alns"]["design_id"]
    )
    calibration_seeds = {int(value) for value in protocol["seeds"]}
    confirmatory_seeds = {int(value) for value in confirmatory["seeds"]}
    seed_disjoint = calibration_seeds.isdisjoint(confirmatory_seeds)
    equal_budget = (
        protocol.get("equal_budget") is True
        and pbc["method_runs"] == alns["method_runs"] == 972
        and float(protocol["seconds_per_run"]) == 300.0
    )

    params = confirmatory["parameters"]
    frozen_matches = True
    # The PBC row contains baseline values for the four ALNS-only settings.
    # Confirmation correctly replaces those with the independently calibrated
    # ALNS row, so they must not be compared against the PBC record.
    alns_specific = {
        "random_alns_destroy_min", "random_alns_destroy_max",
        "sa_start_fraction", "sa_end_fraction",
    }
    for key, value in selected["pbc"]["parameters"].items():
        if key in alns_specific:
            continue
        actual = params.get(key)
        if key == "scoring_weights_json":
            frozen_matches &= json.loads(actual) == json.loads(value)
        else:
            frozen_matches &= same(actual, value)
    for key in sorted(alns_specific):
        frozen_matches &= same(params.get(key), selected["alns"]["parameters"].get(key))

    ready = all((pbc["all_complete"], alns["all_complete"], selected_match, seed_disjoint, equal_budget, frozen_matches))
    report = {
        "design": protocol.get("design"),
        "development_instances": protocol.get("instances"),
        "calibration_seeds": sorted(calibration_seeds),
        "confirmatory_seeds": sorted(confirmatory_seeds),
        "seed_sets_disjoint": seed_disjoint,
        "equal_budget": equal_budget,
        "selected_rows_match_recomputation": selected_match,
        "selected_settings_frozen_in_confirmation": bool(frozen_matches),
        "pbc": pbc,
        "random_alns": alns,
        "submission_ready_for_comment_3": ready,
    }
    json_path = out / "AE03_doe_fairness_audit.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    pbc_probability = pbc["bootstrap_winner_probability"].get(pbc["winner_recomputed"], 0.0)
    alns_probability = alns["bootstrap_winner_probability"].get(alns["winner_recomputed"], 0.0)
    lines = [
        "# AE Comment 3 DoE fairness audit",
        "",
        f"Overall: **{'PASS' if ready else 'NOT READY'}**.",
        "",
        f"- Design: {protocol.get('design')}",
        f"- Equal runs: {pbc['method_runs']} PBC and {alns['method_runs']} Random-ALNS calibration runs",
        f"- Selected rows: PBC {pbc['winner_recomputed']}; Random ALNS {alns['winner_recomputed']}",
        f"- Maximum calibration overshoot: PBC {pbc['maximum_overshoot_seconds']:.3f} s; Random ALNS {alns['maximum_overshoot_seconds']:.3f} s",
        f"- Calibration/confirmation seeds disjoint: {seed_disjoint}",
        f"- Frozen settings match: {bool(frozen_matches)}",
        "",
        "## Selection-stability warning",
        "",
        f"The block-bootstrap probability that the observed winner remains best is {pbc_probability:.1%} for PBC and {alns_probability:.1%} for Random ALNS. This does not invalidate the predeclared selection rule, but it does mean that the paper must not describe the selected levels as uniquely or precisely optimal.",
        "",
    ]
    md_path = out / "AE03_doe_fairness_audit.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(md_path)
    print(json_path)
    if not ready:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
