#!/usr/bin/env python3
"""AE Comment 4: repair timing-contaminated tasks and regenerate statistics.

This is a complete PyCharm entry point.  It identifies task groups solely from
timing metadata (never from solution quality), archives and reruns the whole
three-method task sequentially, rebuilds the aggregate, and reruns the
predeclared reviewer statistics.

Before pressing Run on Windows:
  1. Connect AC power.
  2. Set Sleep to Never for the duration of this script.
  3. Close CPU-heavy applications.

Existing runs are preserved by the project's runner as ``oldrun_NNN``.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


# A post-budget overshoot above 5 s is treated as operational contamination.
# This threshold is based only on timing, not on cost or which method won.
OVERSHOOT_THRESHOLD_SECONDS = 5.0
AUTO_RERUN_CONTAMINATED_TASKS = True
RERUN_WORKERS = 1
WATCHDOG_SECONDS_PER_THREE_METHOD_TASK = 2400


def locate_root() -> Path:
    for root in [Path.cwd(), Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (
            (root / "scripts" / "run_experiments.py").is_file()
            and (root / "scripts" / "reviewer_statistics.py").is_file()
            and (root / "configs" / "eo_confirmatory_120x30_strict.yaml").is_file()
        ):
            return root.resolve()
    raise SystemExit("Project root not found.")


def run(command: list[str], root: Path) -> None:
    print("\n[command]", subprocess.list2cmdline(command), flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(command, cwd=root, env=env)
    if completed.returncode != 0:
        raise SystemExit(f"Command failed with return code {completed.returncode}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def contaminated_tasks(merged_path: Path) -> dict[tuple[str, int], list[dict[str, Any]]]:
    found: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in read_csv(merged_path):
        overshoot = float(row.get("budget_overshoot_seconds") or 0.0)
        if overshoot > OVERSHOOT_THRESHOLD_SECONDS:
            key = (row["instance"], int(row["seed"]))
            found.setdefault(key, []).append({
                "method": row["method"],
                "search_seconds": float(row["search_seconds"]),
                "overshoot_seconds": overshoot,
            })
    return found


def load_task_indices(tasks_path: Path) -> dict[tuple[str, int], int]:
    mapping: dict[tuple[str, int], int] = {}
    with tasks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            mapping[(str(row["instance"]), int(row["seed"]))] = int(row["index"])
    return mapping


def validate_task_run(root: Path, experiment_root: Path, instance: str, seed: int) -> list[dict[str, str]]:
    path = experiment_root / "shards" / "strict_time_300s" / instance / f"seed_{seed}" / "run" / "results.csv"
    rows = read_csv(path)
    expected = {"pbc_xlns", "without_persistent_cuts", "random_alns"}
    if len(rows) != 3 or {row["method"] for row in rows} != expected:
        raise RuntimeError(f"rerun is incomplete: {path}")
    bad = [
        row for row in rows
        if float(row.get("budget_overshoot_seconds") or 0.0) > OVERSHOOT_THRESHOLD_SECONDS
    ]
    if bad:
        raise RuntimeError(
            f"rerun still exceeded {OVERSHOOT_THRESHOLD_SECONDS:g} s: "
            + ", ".join(f"{row['method']}={row['budget_overshoot_seconds']}" for row in bad)
        )
    return rows


def final_integrity(merged_path: Path) -> dict[str, Any]:
    rows = read_csv(merged_path)
    if len(rows) != 10800:
        raise RuntimeError(f"expected 10,800 rows, observed {len(rows)}")
    methods = Counter(row["method"] for row in rows)
    cells: dict[tuple[str, str], set[int]] = {}
    execution: Counter[tuple[str, int]] = Counter()
    max_overshoot = 0.0
    audit_columns = (
        "feasible", "balanced", "strongly_connected", "eulerian",
        "directed_required_covered", "undirected_required_covered", "cost_consistent",
    )
    for row in rows:
        cells.setdefault((row["instance"], row["method"]), set()).add(int(row["seed"]))
        execution[(row["method"], int(float(row["execution_order"]))) ] += 1
        max_overshoot = max(max_overshoot, float(row.get("budget_overshoot_seconds") or 0.0))
        for column in audit_columns:
            if str(row.get(column, "")).strip().lower() not in {"1", "true", "yes", "t"}:
                raise RuntimeError(f"audit failure: {row['instance']} {row['seed']} {row['method']} {column}")
    if len(cells) != 360 or any(len(seeds) != 30 for seeds in cells.values()):
        raise RuntimeError("every one of the 360 instance-method cells must contain 30 unique seeds")
    expected_orders = {(method, order): 1200 for method in methods for order in (1, 2, 3)}
    if dict(execution) != expected_orders:
        raise RuntimeError(f"method-order rotation is unbalanced: {dict(execution)}")
    if max_overshoot > OVERSHOOT_THRESHOLD_SECONDS:
        raise RuntimeError(f"timing contamination remains; maximum overshoot={max_overshoot}")
    return {
        "rows": len(rows),
        "method_rows": dict(methods),
        "instance_method_cells": len(cells),
        "seeds_per_cell": 30,
        "execution_order_counts": {f"{method}:order{order}": count for (method, order), count in sorted(execution.items())},
        "maximum_overshoot_seconds": max_overshoot,
        "overshoot_threshold_seconds": OVERSHOOT_THRESHOLD_SECONDS,
    }


def main() -> None:
    root = locate_root()
    python = sys.executable
    config = root / "configs" / "eo_confirmatory_120x30_strict.yaml"
    experiment_root = root / "results" / "eo_confirmatory_120x30_strict"
    merged = experiment_root / "analysis" / "merged_run_results.csv"
    tasks = load_task_indices(experiment_root / "tasks.jsonl")
    initial = contaminated_tasks(merged)
    print(f"[audit] contaminated task groups: {len(initial)}", flush=True)
    for (instance, seed), detail in sorted(initial.items()):
        print(f"  - {instance} seed={seed}: {detail}", flush=True)

    repair_log: list[dict[str, Any]] = []
    if initial and not AUTO_RERUN_CONTAMINATED_TASKS:
        raise SystemExit("Set AUTO_RERUN_CONTAMINATED_TASKS=True after disabling Windows sleep.")
    for position, ((instance, seed), old_rows) in enumerate(sorted(initial.items()), start=1):
        task_index = tasks[(instance, seed)]
        print(f"\n[repair {position}/{len(initial)}] {instance} seed={seed} task_index={task_index}", flush=True)
        command = [
            python, str(root / "scripts" / "run_experiments.py"),
            "--watchdog-seconds", str(WATCHDOG_SECONDS_PER_THREE_METHOD_TASK),
            "--config", str(config),
            "--workers", str(RERUN_WORKERS),
            "--task-index", str(task_index),
            "--force", "--no-aggregate", "--no-download",
        ]
        run(command, root)
        new_rows = validate_task_run(root, experiment_root, instance, seed)
        repair_log.append({
            "instance": instance, "seed": seed, "task_index": task_index,
            "selection_rule": f"any method budget_overshoot_seconds > {OVERSHOOT_THRESHOLD_SECONDS:g}",
            "old_timing_rows": old_rows,
            "new_timing_rows": [
                {
                    "method": row["method"],
                    "search_seconds": float(row["search_seconds"]),
                    "overshoot_seconds": float(row["budget_overshoot_seconds"]),
                }
                for row in new_rows
            ],
        })

    # Rebuild all derived files from the current 3,600 canonical task runs.
    run([
        python, str(root / "src" / "aggregate_results.py"),
        "--experiment-root", str(experiment_root),
        "--benchmark-dir", str(root / "data" / "corberan_mcpp"),
    ], root)
    run([
        python, str(root / "scripts" / "reviewer_statistics.py"),
        "--experiment-root", str(experiment_root),
    ], root)

    integrity = final_integrity(merged)
    posthoc_path = experiment_root / "reviewer_analysis" / "posthoc_tests.csv"
    posthoc = read_csv(posthoc_path)
    report = {
        "selection_used_only_timing_metadata": True,
        "rerun_task_groups": len(repair_log),
        "repairs": repair_log,
        "final_integrity": integrity,
        "posthoc_results": posthoc,
        "submission_ready_for_comment_4": True,
    }
    evidence = root / "results" / "revision_evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    output = evidence / "AE04_confirmatory_budget_repair_and_statistics.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n[complete]", output)
    print(experiment_root / "reviewer_analysis" / "STATISTICAL_REPORT.md")


if __name__ == "__main__":
    main()
