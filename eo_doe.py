#!/usr/bin/env python3
"""Generate, run-independent and analyse the predeclared OA(27) calibration.

The proposed PBC-XLNS and Random ALNS receive exactly the same number of
configurations, instances, seeds and seconds.  Calibration instances/seeds are
disjoint in role from the confirmatory experiment.  This module never edits a
completed experiment and refuses to select a setting unless all 27 designs are
complete.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "configs" / "generated_doe"
CALIBRATION_DIR = ROOT / "calibration"

DEVELOPMENT_INSTANCES = [
    "MA0532", "MA0567", "MA1035", "MA1562", "MA2047", "MA3065",
    "MB0537", "MB1062", "MB1535", "MB2067", "MB3042", "MB3067",
]
CALIBRATION_SEEDS = [20270101, 20270102, 20270103]
CONFIRMATORY_SEEDS = list(range(20271001, 20271031))

BASE_PARAMETERS: dict[str, Any] = {
    "cost_scale": 1000,
    "multistart_restarts": 12,
    "neighborhood_min": 6,
    "neighborhood_max": 28,
    "neighborhood_step": 4,
    "neighborhood_top_fraction": 0.60,
    "cluster_fraction": 0.25,
    "benders_max_iterations": 12,
    "master_time_limit": 4.0,
    "master_cut_limit": 256,
    "cut_pool_limit": 1500,
    "enumeration_limit": 16,
    "restart_stagnation": 18,
    "mode_adaptation_probability": 0.35,
    "path_target_weight": 0.65,
    "weighted_sampling_floor": 0.05,
    "weighted_sampling_jitter": 0.10,
    "random_alns_destroy_min": 1,
    "random_alns_destroy_max": 24,
    "sa_start_fraction": 0.012,
    "sa_end_fraction": 0.00005,
}

PBC_FACTORS: dict[str, list[Any]] = {
    "dual_primary": [0.62, 0.70, 0.78],
    "disagreement_primary": [0.60, 0.68, 0.75],
    "exposure_primary": [0.58, 0.65, 0.72],
    "mixed_dual": [0.30, 0.40, 0.50],
    "mixed_disagreement": [0.20, 0.30, 0.40],
    "explore_random": [0.20, 0.35, 0.50],
    "path_target_weight": [0.50, 0.65, 0.80],
    "neighborhood_top_fraction": [0.40, 0.60, 0.80],
    "cluster_fraction": [0.10, 0.25, 0.40],
    "mode_adaptation_probability": [0.20, 0.35, 0.50],
    "neighborhood_min": [4, 6, 8],
    "neighborhood_max": [20, 28, 36],
    "restart_stagnation": [12, 18, 24],
}

ALNS_FACTORS: dict[str, list[Any]] = {
    "random_alns_destroy_min": [1, 2, 4],
    "random_alns_destroy_max": [16, 24, 32],
    "sa_start_fraction": [0.006, 0.012, 0.024],
    "sa_end_fraction": [0.000025, 0.000050, 0.000100],
}


def oa27() -> np.ndarray:
    """Strength-two OA(27,13,3,2) over GF(3)."""
    vectors = [
        (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 2, 0),
        (1, 0, 1), (1, 0, 2), (0, 1, 1), (0, 1, 2), (1, 1, 1),
        (1, 1, 2), (1, 2, 1), (1, 2, 2),
    ]
    rows = []
    for base in product(range(3), repeat=3):
        rows.append([sum(a * b for a, b in zip(vector, base)) % 3 for vector in vectors])
    design = np.asarray(rows, dtype=int)
    for left in range(design.shape[1]):
        for right in range(left + 1, design.shape[1]):
            pairs = {(int(a), int(b)) for a, b in zip(design[:, left], design[:, right])}
            if len(pairs) != 9:
                raise AssertionError("OA construction lost pairwise balance")
    return design


def scoring_matrix(values: dict[str, Any]) -> dict[str, dict[str, float]]:
    def remaining(primary: float, ratio: float) -> tuple[float, float]:
        rest = 1.0 - primary
        return rest * ratio, rest * (1.0 - ratio)

    dual_exposure, dual_length = remaining(float(values["dual_primary"]), 0.12 / 0.22)
    dis_dual, dis_length = remaining(float(values["disagreement_primary"]), 0.15 / 0.25)
    exp_dual, exp_length = remaining(float(values["exposure_primary"]), 0.18 / 0.28)
    mixed_dual = float(values["mixed_dual"])
    mixed_dis = float(values["mixed_disagreement"])
    mixed_length = 0.05
    mixed_exp = 1.0 - mixed_dual - mixed_dis - mixed_length
    if mixed_exp < 0:
        raise ValueError("mixed-mode factor combination creates a negative exposure weight")
    explore_random = float(values["explore_random"])
    nonrandom = 1.0 - explore_random
    matrix = {
        "dual": {"dual": float(values["dual_primary"]), "disagreement": 0.0,
                 "exposure": dual_exposure, "length": dual_length, "random": 0.0},
        "disagreement": {"dual": dis_dual, "disagreement": float(values["disagreement_primary"]),
                         "exposure": 0.0, "length": dis_length, "random": 0.0},
        "exposure": {"dual": exp_dual, "disagreement": 0.0,
                     "exposure": float(values["exposure_primary"]), "length": exp_length, "random": 0.0},
        "mixed": {"dual": mixed_dual, "disagreement": mixed_dis,
                  "exposure": mixed_exp, "length": mixed_length, "random": 0.0},
        "path": {"dual": mixed_dual, "disagreement": mixed_dis,
                 "exposure": mixed_exp, "length": mixed_length, "random": 0.0},
        "explore": {"dual": 0.0, "disagreement": nonrandom * (0.45 / 0.65),
                    "exposure": 0.0, "length": nonrandom * (0.20 / 0.65),
                    "random": explore_random},
    }
    return {
        mode: {component: round(weight, 12) for component, weight in row.items()}
        for mode, row in matrix.items()
    }


def rows_for(algorithm: str) -> list[dict[str, Any]]:
    design = oa27()
    factors = PBC_FACTORS if algorithm == "pbc" else ALNS_FACTORS
    rows: list[dict[str, Any]] = []
    for index, levels in enumerate(design, start=1):
        values = {
            name: choices[int(levels[column])]
            for column, (name, choices) in enumerate(factors.items())
        }
        parameters = dict(BASE_PARAMETERS)
        if algorithm == "pbc":
            for name in (
                "path_target_weight", "neighborhood_top_fraction", "cluster_fraction",
                "mode_adaptation_probability", "neighborhood_min", "neighborhood_max",
                "restart_stagnation",
            ):
                parameters[name] = values[name]
            parameters["scoring_weights_json"] = json.dumps(
                scoring_matrix(values), sort_keys=True, separators=(",", ":")
            )
        else:
            parameters.update(values)
        rows.append({
            "algorithm": algorithm,
            "design_id": f"d{index:02d}",
            "levels": [int(item) for item in levels[:len(factors)]],
            "factor_values": values,
            "parameters": parameters,
        })
    return rows


def config_for(row: dict[str, Any]) -> dict[str, Any]:
    algorithm = row["algorithm"]
    method = "pbc_xlns" if algorithm == "pbc" else "random_alns"
    return {
        "experiment_name": f"eo_cal_{algorithm}_{row['design_id']}",
        "benchmark_dir": "data/corberan_mcpp",
        "results_root": "results",
        "dataset_source": "auto",
        "suite": "main",
        "reference_method": method,
        "methods": [method],
        "instances": DEVELOPMENT_INSTANCES,
        "seeds": CALIBRATION_SEEDS,
        "workers": 4,
        "budget_profiles": [{
            "name": "calibration_time_300s", "mode": "time", "seconds": 300,
            "hard_timeout_seconds": 900,
        }],
        "parameters": row["parameters"],
    }


def generate() -> list[Path]:
    GENERATED.mkdir(parents=True, exist_ok=True)
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    all_rows: list[dict[str, Any]] = []
    for algorithm in ("pbc", "alns"):
        for row in rows_for(algorithm):
            path = GENERATED / f"{algorithm}_{row['design_id']}.yaml"
            path.write_text(yaml.safe_dump(config_for(row), sort_keys=False), encoding="utf-8")
            paths.append(path)
            all_rows.append(row)
    flat = []
    for row in all_rows:
        flat.append({"algorithm": row["algorithm"], "design_id": row["design_id"],
                     **row["factor_values"], "parameters_json": json.dumps(row["parameters"], sort_keys=True)})
    pd.DataFrame(flat).to_csv(CALIBRATION_DIR / "tested_parameter_values.csv", index=False)
    protocol = {
        "design": "OA(27,13,3,2)",
        "objective": "minimum mean relative deviation from the best design on matched instance-seed blocks",
        "tie_break": ["mean_rank", "design_id"],
        "algorithms": ["pbc", "alns"],
        "equal_budget": True,
        "instances": DEVELOPMENT_INSTANCES,
        "seeds": CALIBRATION_SEEDS,
        "seconds_per_run": 300,
        "confirmatory_seeds": CONFIRMATORY_SEEDS,
        "pbc_factors": PBC_FACTORS,
        "alns_factors": ALNS_FACTORS,
    }
    (CALIBRATION_DIR / "CALIBRATION_PROTOCOL.json").write_text(
        json.dumps(protocol, indent=2), encoding="utf-8"
    )
    return paths


def analyse_algorithm(algorithm: str) -> tuple[dict[str, Any], pd.DataFrame]:
    designs = rows_for(algorithm)
    frames = []
    for row in designs:
        root = ROOT / "results" / f"eo_cal_{algorithm}_{row['design_id']}"
        integrity_path = root / "analysis" / "task_integrity.csv"
        merged_path = root / "analysis" / "merged_run_results.csv"
        if not integrity_path.exists() or not merged_path.exists():
            raise RuntimeError(f"missing aggregate for {root.name}; run every generated config first")
        integrity = pd.read_csv(integrity_path)
        complete = (
            integrity["complete"].fillna(False)
            if pd.api.types.is_bool_dtype(integrity["complete"])
            else integrity["complete"].astype(str).str.lower().isin({"1", "true", "yes", "t"})
        )
        if len(integrity) != 36 or not complete.all():
            raise RuntimeError(f"{root.name} is incomplete; selection is blocked")
        frame = pd.read_csv(merged_path)
        if len(frame) != 36:
            raise RuntimeError(f"{root.name} expected 36 rows, observed {len(frame)}")
        frame = frame[["instance", "seed", "strict_cost"]].copy()
        frame["design_id"] = row["design_id"]
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True)
    data["block_best"] = data.groupby(["instance", "seed"])["strict_cost"].transform("min")
    data["relative_deviation_pct"] = 100.0 * (data["strict_cost"] - data["block_best"]) / data["block_best"].abs().clip(lower=1e-12)
    data["rank"] = data.groupby(["instance", "seed"])["strict_cost"].rank(method="average")
    summary = data.groupby("design_id", as_index=False).agg(
        mean_relative_deviation_pct=("relative_deviation_pct", "mean"),
        median_relative_deviation_pct=("relative_deviation_pct", "median"),
        mean_rank=("rank", "mean"),
        blocks=("strict_cost", "size"),
    ).sort_values(["mean_relative_deviation_pct", "mean_rank", "design_id"], kind="mergesort")
    winner_id = str(summary.iloc[0]["design_id"])
    winner = next(row for row in designs if row["design_id"] == winner_id)
    selected = {
        "algorithm": algorithm,
        "design_id": winner_id,
        "selection_criterion": "mean_relative_deviation_pct, then mean_rank, then design_id",
        "factor_values": winner["factor_values"],
        "parameters": winner["parameters"],
        "score": summary.iloc[0].to_dict(),
    }
    return selected, summary


def select() -> Path:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    selected = {}
    for algorithm in ("pbc", "alns"):
        choice, summary = analyse_algorithm(algorithm)
        selected[algorithm] = choice
        summary.to_csv(CALIBRATION_DIR / f"{algorithm}_selection_table.csv", index=False)
    output = CALIBRATION_DIR / "selected_parameters.json"
    output.write_text(json.dumps(selected, indent=2), encoding="utf-8")
    make_confirmatory_config(selected)
    return output


def make_confirmatory_config(selected: dict[str, Any] | None = None) -> Path:
    if selected is None:
        path = CALIBRATION_DIR / "selected_parameters.json"
        if not path.exists():
            raise RuntimeError("run calibration selection before creating confirmatory config")
        selected = json.loads(path.read_text(encoding="utf-8"))
    parameters = dict(BASE_PARAMETERS)
    parameters.update(selected["pbc"]["parameters"])
    for name in ALNS_FACTORS:
        parameters[name] = selected["alns"]["parameters"][name]
    config = {
        "experiment_name": "eo_confirmatory_120x30_strict",
        "benchmark_dir": "data/corberan_mcpp",
        "results_root": "results",
        "dataset_source": "auto",
        "suite": "reviewer_confirmatory",
        "reference_method": "pbc_xlns",
        "instances": ["*"],
        "seeds": CONFIRMATORY_SEEDS,
        "workers": 4,
        "budget_profiles": [{
            "name": "strict_time_300s", "mode": "time", "seconds": 300,
            "hard_timeout_seconds": 1500,
        }],
        "parameters": parameters,
    }
    output = ROOT / "configs" / "eo_confirmatory_120x30_strict.yaml"
    output.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["generate", "select", "confirmatory-config"])
    args = parser.parse_args()
    if args.action == "generate":
        paths = generate()
        print(json.dumps({"generated_configs": len(paths), "directory": str(GENERATED)}, indent=2))
    elif args.action == "select":
        print(select())
    else:
        print(make_confirmatory_config())


if __name__ == "__main__":
    main()
