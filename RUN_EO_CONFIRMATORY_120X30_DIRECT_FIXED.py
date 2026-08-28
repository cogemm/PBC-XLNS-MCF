#!/usr/bin/env python3
"""Run only the frozen Engineering Optimization 120 x 30 experiment.

Place this file in the root of ``PBC_XLNS_MCF_EO_Revision`` and press Run in
PyCharm.  The script preserves the completed PBC calibration and automatically
runs only missing Random-ALNS calibration configurations.  It then freezes the
selected settings and creates or resumes exactly 3,600 matched instance-seed
tasks. Existing valid tasks are skipped; evidence is never overwritten.

Required project files:
    scripts/eo_doe.py
    scripts/run_experiments.py
    scripts/reviewer_statistics.py
    src/benchmark_runner.py
    data/corberan_mcpp/  (the verified 120-instance dataset)

Scientific design:
    120 instances x 30 matched seeds x 3 methods
    methods: PBC-XLNS, PBC without persistent cuts, Random ALNS
    300 s strict search deadline per method
    primary analysis unit: instance mean across the 30 seeds
"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:  # pragma: no cover - user environment check
    raise SystemExit(
        "PyYAML is required. In the active environment run: pip install pyyaml"
    ) from exc


# ============================== USER SETTINGS ==============================
WORKERS = 4
NO_DOWNLOAD = True

# Parent-process watchdog for one task containing all three 300 s methods.
# This does not alter the 300 s method budgets or the experiment design hash.
WATCHDOG_SECONDS = 2400

# After a long pass, retry only incomplete/failed tasks. Complete tasks are
# verified from results.csv and skipped. Increase only when transient failures
# remain; no completed task is rerun.
MAX_RETRY_PASSES = 3

# Keep this True. The program checks both calibration families, preserves every
# complete result, and runs only incomplete eo_cal_alns_d01..d27 folders.
AUTO_RUN_MISSING_ALNS_CALIBRATION = True
MAX_CALIBRATION_RETRY_PASSES = 3

# Generate the reviewer-requested Shapiro/ANOVA-or-Friedman/post-hoc report
# automatically after all 3,600 tasks are complete.
RUN_REVIEWER_STATISTICS = True
# ===========================================================================


ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
CONFIG = ROOT / "configs" / "eo_confirmatory_120x30_strict.yaml"
SELECTION = ROOT / "calibration" / "selected_parameters.json"
EXPERIMENT_NAME = "eo_confirmatory_120x30_strict"
EXPERIMENT_ROOT = ROOT / "results" / EXPERIMENT_NAME
TASKS_JSONL = EXPERIMENT_ROOT / "tasks.jsonl"
EXPECTED_SEEDS = list(range(20271001, 20271031))
EXPECTED_METHODS = {
    "pbc_xlns",
    "without_persistent_cuts",
    "random_alns",
}
ALNS_ONLY_PARAMETERS = {
    "random_alns_destroy_min",
    "random_alns_destroy_max",
    "sa_start_fraction",
    "sa_end_fraction",
}
REQUIRED_AUDITS = {
    "feasible",
    "balanced",
    "strongly_connected",
    "eulerian",
    "directed_required_covered",
    "undirected_required_covered",
    "cost_consistent",
}


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"\n[STOP] {message}\n")


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "t"}


def same_config_value(actual: Any, expected: Any) -> bool:
    """Compare YAML/JSON values without rejecting equivalent numeric types."""
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    return actual == expected


def command_text(command: Iterable[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in command])


def run(command: list[str], allowed_codes: set[int] | None = None) -> int:
    allowed = {0} if allowed_codes is None else set(allowed_codes)
    print("\n[command]", command_text(command), flush=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = (
        str(ROOT / "src")
        + os.pathsep
        + environment.get("PYTHONPATH", "")
    )
    completed = subprocess.run(command, cwd=ROOT, env=environment)
    if completed.returncode not in allowed:
        fail(
            f"Command returned {completed.returncode}:\n{command_text(command)}"
        )
    return completed.returncode


def require_project() -> None:
    required = [
        ROOT / "scripts" / "eo_doe.py",
        ROOT / "scripts" / "run_experiments.py",
        ROOT / "scripts" / "reviewer_statistics.py",
        ROOT / "src" / "benchmark_runner.py",
        ROOT / "src" / "pbc_xlns_mcf_v10_1.py",
        ROOT / "data" / "corberan_mcpp",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        fail(
            "Put this file in the PBC_XLNS_MCF_EO_Revision project root. "
            "Missing:\n  - " + "\n  - ".join(missing)
        )


def calibration_experiment_complete(algorithm: str, number: int) -> tuple[bool, str]:
    name = f"eo_cal_{algorithm}_d{number:02d}"
    analysis = ROOT / "results" / name / "analysis"
    integrity_path = analysis / "task_integrity.csv"
    merged_path = analysis / "merged_run_results.csv"
    if not integrity_path.exists() or not merged_path.exists():
        return False, f"{name}: aggregate files missing"
    try:
        with integrity_path.open("r", encoding="utf-8-sig", newline="") as handle:
            integrity = list(csv.DictReader(handle))
        with merged_path.open("r", encoding="utf-8-sig", newline="") as handle:
            merged = list(csv.DictReader(handle))
    except Exception as exc:
        return False, f"{name}: cannot read aggregate files ({exc})"
    complete = sum(parse_bool(row.get("complete")) for row in integrity)
    if len(integrity) != 36 or complete != 36 or len(merged) != 36:
        return False, f"{name}: tasks {complete}/36, merged rows {len(merged)}/36"
    return True, f"{name}: complete"


def calibration_problems(algorithm: str) -> list[tuple[int, str]]:
    problems: list[tuple[int, str]] = []
    for number in range(1, 28):
        complete, detail = calibration_experiment_complete(algorithm, number)
        if not complete:
            problems.append((number, detail))
    return problems


def calibration_runner_command(config: Path) -> list[str]:
    command = [
        PYTHON,
        str(ROOT / "scripts" / "run_experiments.py"),
        "--watchdog-seconds",
        str(max(1, WATCHDOG_SECONDS)),
        "--config",
        str(config),
        "--workers",
        str(max(1, WORKERS)),
    ]
    if NO_DOWNLOAD:
        command.append("--no-download")
    return command


def ensure_alns_configs() -> list[Path]:
    directory = ROOT / "configs" / "generated_doe"
    configs = sorted(directory.glob("alns_d*.yaml"))
    if len(configs) != 27:
        run([PYTHON, str(ROOT / "scripts" / "eo_doe.py"), "generate"])
        configs = sorted(directory.glob("alns_d*.yaml"))
    if len(configs) != 27:
        fail("Exactly 27 generated Random-ALNS calibration configs are required.")
    return configs


def run_missing_alns_calibration() -> None:
    """Resume only missing ALNS DoE folders; never rerun complete PBC evidence."""
    pbc_missing = calibration_problems("pbc")
    if pbc_missing:
        shown = "\n  - ".join(detail for _, detail in pbc_missing[:20])
        fail(
            "PBC calibration is unexpectedly incomplete. It was not rerun "
            "automatically because the uploaded PBC evidence was already "
            f"frozen. Missing/incomplete folders:\n  - {shown}"
        )
    missing = calibration_problems("alns")
    if not missing:
        print("[calibration] Random-ALNS 27/27 already complete; nothing rerun.", flush=True)
        return
    if not AUTO_RUN_MISSING_ALNS_CALIBRATION:
        fail(
            f"Random-ALNS calibration has {len(missing)} incomplete configurations. "
            "Set AUTO_RUN_MISSING_ALNS_CALIBRATION=True."
        )
    configs = ensure_alns_configs()
    print(
        f"\n[calibration] {len(missing)}/27 Random-ALNS configurations need work. "
        "Complete PBC configurations will not be touched.",
        flush=True,
    )
    lower_hours = len(missing) * 36 * 300 / max(1, WORKERS) / 3600
    print(
        f"[calibration estimate] method-budget lower bound: about {lower_hours:.1f} "
        "worker-adjusted hours, plus setup/audit/output overhead.",
        flush=True,
    )
    by_number = {int(path.stem.rsplit("d", 1)[1]): path for path in configs}
    for position, (number, _) in enumerate(missing, start=1):
        config = by_number[number]
        print(
            f"\n[ALNS DOE {position}/{len(missing)}] {config.name}",
            flush=True,
        )
        for attempt in range(1, max(1, MAX_CALIBRATION_RETRY_PASSES) + 1):
            complete, _ = calibration_experiment_complete("alns", number)
            if complete:
                break
            print(
                f"[ALNS DOE] resume attempt {attempt}/{MAX_CALIBRATION_RETRY_PASSES}",
                flush=True,
            )
            run(calibration_runner_command(config), allowed_codes={0, 2})
        complete, detail = calibration_experiment_complete("alns", number)
        if not complete:
            fail(
                f"{detail}. Run this same file again after inspecting that "
                "experiment's status.json/job.log; completed tasks will be skipped."
            )


def validate_calibration_evidence() -> None:
    """Verify both equal-budget OA(27) calibrations after selective recovery."""
    run_missing_alns_calibration()
    problems = calibration_problems("pbc") + calibration_problems("alns")
    if problems:
        shown = "\n  - ".join(detail for _, detail in problems[:20])
        fail(f"Calibration remains incomplete:\n  - {shown}")
    print(
        "[calibration] verified 54/54 OA configurations "
        "(27 PBC + 27 Random-ALNS; 1,944/1,944 tasks).",
        flush=True,
    )


def validate_selection() -> dict[str, Any]:
    if not SELECTION.exists():
        print(
            "\n[selection] No frozen selection file; selecting from existing "
            "27 PBC and 27 Random-ALNS calibration result folders.",
            flush=True,
        )
        result = subprocess.run(
            [PYTHON, str(ROOT / "scripts" / "eo_doe.py"), "select"],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src")
                + os.pathsep
                + os.environ.get("PYTHONPATH", ""),
            },
        )
        if result.returncode != 0:
            fail(
                "Independent calibration selection could not be completed. "
                "This runner will not use uncalibrated/default Random-ALNS "
                "parameters. Confirm that results/eo_cal_pbc_d01..d27 and "
                "results/eo_cal_alns_d01..d27 are all complete, then run this "
                "same file again. Already-complete calibration tasks do not "
                "need to be rerun."
            )
    try:
        selected = json.loads(SELECTION.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Cannot read {SELECTION}: {exc}")
    if set(selected) != {"pbc", "alns"}:
        fail("selected_parameters.json must contain exactly 'pbc' and 'alns'.")
    for algorithm in ("pbc", "alns"):
        row = selected.get(algorithm, {})
        if not row.get("design_id") or not isinstance(row.get("parameters"), dict):
            fail(f"Frozen {algorithm} selection is incomplete.")
    print(
        "[selection] frozen designs: "
        f"PBC={selected['pbc']['design_id']}, "
        f"Random-ALNS={selected['alns']['design_id']}",
        flush=True,
    )
    return selected


def ensure_config(selected: dict[str, Any]) -> dict[str, Any]:
    if not CONFIG.exists():
        run(
            [
                PYTHON,
                str(ROOT / "scripts" / "eo_doe.py"),
                "confirmatory-config",
            ]
        )
    try:
        config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Cannot read {CONFIG}: {exc}")
    if not isinstance(config, dict):
        fail("The confirmatory YAML is not a mapping.")
    errors: list[str] = []
    if config.get("experiment_name") != EXPERIMENT_NAME:
        errors.append(f"experiment_name must be {EXPERIMENT_NAME!r}")
    if config.get("suite") != "reviewer_confirmatory":
        errors.append("suite must be 'reviewer_confirmatory'")
    if config.get("instances") != ["*"]:
        errors.append("instances must be ['*'] so the verified 120-instance set is used")
    seeds = [int(value) for value in config.get("seeds", [])]
    if seeds != EXPECTED_SEEDS:
        errors.append("seeds must be the predeclared 20271001..20271030 sequence")
    profiles = config.get("budget_profiles", [])
    if len(profiles) != 1:
        errors.append("exactly one strict_time_300s profile is required")
    else:
        profile = profiles[0]
        if profile.get("name") != "strict_time_300s":
            errors.append("profile name must be 'strict_time_300s'")
        if profile.get("mode") != "time" or float(profile.get("seconds", 0)) != 300.0:
            errors.append("the method budget must be time mode with exactly 300 seconds")
        if float(profile.get("hard_timeout_seconds", 0)) < 900.0:
            errors.append("hard_timeout_seconds is too short for three sequential methods")
    parameters = config.get("parameters", {})
    if not isinstance(parameters, dict):
        errors.append("parameters must be a mapping")
    else:
        # PBC calibration manifests contain the complete base-parameter map,
        # including default values for Random-ALNS. In the confirmatory design,
        # those four ALNS-only defaults must be replaced by the independently
        # calibrated Random-ALNS values. They therefore must not be validated as
        # PBC parameters here.
        for key, value in selected["pbc"]["parameters"].items():
            if key in ALNS_ONLY_PARAMETERS:
                continue
            if not same_config_value(parameters.get(key), value):
                errors.append(f"PBC parameter {key!r} differs from the frozen selection")
        for key in sorted(ALNS_ONLY_PARAMETERS):
            if key not in selected["alns"]["parameters"]:
                errors.append(f"Frozen Random-ALNS selection is missing {key!r}")
                continue
            if not same_config_value(
                parameters.get(key),
                selected["alns"]["parameters"].get(key),
            ):
                errors.append(f"Random-ALNS parameter {key!r} differs from the frozen selection")
    if errors:
        fail("Unsafe confirmatory configuration:\n  - " + "\n  - ".join(errors))
    return config


def archive_stale_plan_only(config: dict[str, Any]) -> None:
    """Archive only an empty, mismatched plan; never move real run evidence."""
    sys.path.insert(0, str(ROOT / "src"))
    import benchmark_runner  # type: ignore

    manifest_path = EXPERIMENT_ROOT / "experiment_manifest.json"
    if not manifest_path.exists():
        return
    dataset = benchmark_runner.prepare_dataset(config, NO_DOWNLOAD, None)
    dataset_sha256, _ = benchmark_runner.dataset_fingerprint(dataset)
    code_sha256, _ = benchmark_runner.hash_inventory(
        benchmark_runner.source_inventory()
    )
    current_design = benchmark_runner.sha256_bytes(
        benchmark_runner.canonical_json(
            {
                "config": config,
                "code_sha256": code_sha256,
                "dataset_sha256": dataset_sha256,
            }
        ).encode("utf-8")
    )
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    if existing.get("design_sha256") == current_design:
        return
    allowed = {"experiment_manifest.json", "tasks.jsonl", "frozen_source"}
    observed = {path.name for path in EXPERIMENT_ROOT.iterdir()}
    if not observed <= allowed or (EXPERIMENT_ROOT / "shards").exists():
        fail(
            "The existing confirmatory directory belongs to a different "
            "config/code/dataset design and contains run evidence. It was not "
            f"moved or overwritten:\n{EXPERIMENT_ROOT}"
        )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = EXPERIMENT_ROOT.with_name(
        f"{EXPERIMENT_NAME}_PLAN_ONLY_ARCHIVE_{timestamp}"
    )
    suffix = 1
    while destination.exists():
        destination = EXPERIMENT_ROOT.with_name(
            f"{EXPERIMENT_NAME}_PLAN_ONLY_ARCHIVE_{timestamp}_{suffix:02d}"
        )
        suffix += 1
    EXPERIMENT_ROOT.rename(destination)
    print(
        f"[safe recovery] archived stale plan-only directory: {destination.name}",
        flush=True,
    )


def runner_command(*extra: str) -> list[str]:
    command = [
        PYTHON,
        str(ROOT / "scripts" / "run_experiments.py"),
        "--watchdog-seconds",
        str(max(1, WATCHDOG_SECONDS)),
        "--config",
        str(CONFIG),
        "--workers",
        str(max(1, WORKERS)),
        *extra,
    ]
    if NO_DOWNLOAD:
        command.append("--no-download")
    return command


def prepare_plan() -> list[dict[str, Any]]:
    run(runner_command("--prepare-only"))
    manifest_path = EXPERIMENT_ROOT / "experiment_manifest.json"
    if not manifest_path.exists() or not TASKS_JSONL.exists():
        fail("The immutable confirmatory manifest/task plan was not created.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "instance_count": 120,
        "seed_count": 30,
        "task_count": 3600,
        "expected_rows": 10800,
    }
    errors = [
        f"{key}: expected {value}, observed {manifest.get(key)!r}"
        for key, value in expected.items()
        if int(manifest.get(key, -1)) != value
    ]
    if errors:
        fail("Manifest is not the required 120 x 30 design:\n  - " + "\n  - ".join(errors))
    tasks = [
        json.loads(line)
        for line in TASKS_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(tasks) != 3600:
        fail(f"Expected 3,600 tasks, observed {len(tasks)}.")
    instances = {str(task["instance"]) for task in tasks}
    seeds = {int(task["seed"]) for task in tasks}
    methods = {method for task in tasks for method in task["expected_methods"]}
    if len(instances) != 120 or seeds != set(EXPECTED_SEEDS) or methods != EXPECTED_METHODS:
        fail("Task identities/methods differ from the preregistered design.")
    return tasks


def task_is_complete(task: dict[str, Any]) -> bool:
    result_path = (
        Path(task["experiment_root"])
        / "shards"
        / str(task["profile"])
        / str(task["instance"])
        / f"seed_{int(task['seed'])}"
        / "run"
        / "results.csv"
    )
    if not result_path.exists():
        return False
    try:
        with result_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return False
    expected_methods = set(map(str, task["expected_methods"]))
    if len(rows) != len(expected_methods):
        return False
    if {str(row.get("method")) for row in rows} != expected_methods:
        return False
    for row in rows:
        try:
            identity_ok = (
                row.get("instance") == str(task["instance"])
                and int(row.get("seed", -1)) == int(task["seed"])
            )
        except (TypeError, ValueError):
            return False
        provenance_ok = (
            row.get("code_sha256") == task["code_sha256"]
            and row.get("design_sha256") == task["design_sha256"]
            and row.get("dataset_manifest_sha256") == task["dataset_sha256"]
        )
        audits_ok = all(parse_bool(row.get(column)) for column in REQUIRED_AUDITS)
        try:
            timing_ok = (
                parse_bool(row.get("strict_deadline_enforced"))
                and math.isclose(
                    float(row.get("search_budget_seconds", "nan")),
                    300.0,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            )
        except (TypeError, ValueError):
            timing_ok = False
        if not identity_ok or not provenance_ok or not audits_ok or not timing_ok:
            return False
    return True


def completion(tasks: list[dict[str, Any]]) -> tuple[int, list[int]]:
    missing: list[int] = []
    for index, task in enumerate(tasks):
        if not task_is_complete(task):
            missing.append(index)
    return len(tasks) - len(missing), missing


def verify_final_aggregate() -> None:
    """Fail closed unless the aggregate is exactly the preregistered design."""
    integrity_path = EXPERIMENT_ROOT / "analysis" / "task_integrity.csv"
    merged_path = EXPERIMENT_ROOT / "analysis" / "merged_run_results.csv"
    try:
        with integrity_path.open("r", encoding="utf-8-sig", newline="") as handle:
            integrity = list(csv.DictReader(handle))
        with merged_path.open("r", encoding="utf-8-sig", newline="") as handle:
            merged = list(csv.DictReader(handle))
    except Exception as exc:
        fail(f"Cannot read the final aggregate evidence: {exc}")

    errors: list[str] = []
    complete_tasks = sum(parse_bool(row.get("complete")) for row in integrity)
    if len(integrity) != 3600 or complete_tasks != 3600:
        errors.append(
            f"task_integrity: expected 3,600/3,600 complete, observed "
            f"{complete_tasks}/{len(integrity)}"
        )
    if len(merged) != 10800:
        errors.append(f"merged rows: expected 10,800, observed {len(merged)}")

    instances = {str(row.get("instance", "")) for row in merged}
    methods = {str(row.get("method", "")) for row in merged}
    seeds: set[int] = set()
    identities: set[tuple[str, str, int, str]] = set()
    duplicate_rows = 0
    audit_failures = 0
    timing_failures = 0
    overshoots: list[float] = []
    for row in merged:
        try:
            seed = int(row.get("seed", -1))
        except (TypeError, ValueError):
            seed = -1
        seeds.add(seed)
        identity = (
            str(row.get("profile", "")),
            str(row.get("instance", "")),
            seed,
            str(row.get("method", "")),
        )
        if identity in identities:
            duplicate_rows += 1
        identities.add(identity)
        if not all(parse_bool(row.get(column)) for column in REQUIRED_AUDITS):
            audit_failures += 1
        try:
            strict_ok = parse_bool(row.get("strict_deadline_enforced"))
            budget_ok = math.isclose(
                float(row.get("search_budget_seconds", "nan")),
                300.0,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            overshoots.append(float(row.get("budget_overshoot_seconds", 0.0)))
        except (TypeError, ValueError):
            strict_ok = False
            budget_ok = False
        if not strict_ok or not budget_ok:
            timing_failures += 1

    if len(instances) != 120:
        errors.append(f"instances: expected 120, observed {len(instances)}")
    if seeds != set(EXPECTED_SEEDS):
        errors.append("seeds differ from the frozen 20271001..20271030 sequence")
    if methods != EXPECTED_METHODS:
        errors.append(
            f"methods: expected {sorted(EXPECTED_METHODS)}, observed {sorted(methods)}"
        )
    if duplicate_rows:
        errors.append(f"duplicate instance-seed-method rows: {duplicate_rows}")
    if audit_failures:
        errors.append(f"rows failing feasibility/route/cost audits: {audit_failures}")
    if timing_failures:
        errors.append(f"rows failing strict-deadline/budget audit: {timing_failures}")
    if errors:
        fail("Final aggregate failed the 120 x 30 audit:\n  - " + "\n  - ".join(errors))

    max_overshoot = max(overshoots, default=0.0)
    print(
        "[final audit] 3,600 tasks, 10,800 method rows, 120 instances, "
        "30 seeds and all required audits verified.",
        flush=True,
    )
    print(
        f"[final audit] maximum recorded budget overshoot: {max_overshoot:.6f}s",
        flush=True,
    )


def aggregate_and_analyse() -> None:
    # A final normal run sees all 3,600 tasks as skipped_complete and creates
    # the complete aggregate tables. No method is rerun.
    run(runner_command())
    integrity = EXPERIMENT_ROOT / "analysis" / "task_integrity.csv"
    merged = EXPERIMENT_ROOT / "analysis" / "merged_run_results.csv"
    if not integrity.exists() or not merged.exists():
        fail("Complete raw tasks exist, but aggregate files were not created.")
    verify_final_aggregate()
    if RUN_REVIEWER_STATISTICS:
        run(
            [
                PYTHON,
                str(ROOT / "scripts" / "reviewer_statistics.py"),
                "--experiment-root",
                str(EXPERIMENT_ROOT),
            ]
        )
        reviewer_output = EXPERIMENT_ROOT / "reviewer_analysis"
        required_statistics = [
            "instance_method_30run_summary.csv",
            "per_instance_30seed_tests.csv",
            "normality_tests.csv",
            "omnibus_tests.csv",
            "posthoc_tests.csv",
            "method_summary.csv",
            "response_values.json",
            "STATISTICAL_REPORT.md",
        ]
        missing = [
            name
            for name in required_statistics
            if not (reviewer_output / name).is_file()
            or (reviewer_output / name).stat().st_size == 0
        ]
        if missing:
            fail(
                "Reviewer statistics finished without all required outputs:\n  - "
                + "\n  - ".join(missing)
            )
        print("[statistics] all reviewer-analysis outputs verified.", flush=True)


def main() -> None:
    print("=" * 78)
    print("ENGINEERING OPTIMIZATION: STRICT-DEADLINE 120 x 30 CONFIRMATORY RUN")
    print("=" * 78)
    print(f"Project: {ROOT}")
    print(f"Python : {PYTHON}")
    print(f"Workers: {WORKERS}")
    require_project()
    run([PYTHON, "-m", "compileall", "-q", str(ROOT / "src"), str(ROOT / "scripts"), str(Path(__file__).resolve())])
    run([PYTHON, str(ROOT / "src" / "pbc_xlns_mcf_v10_1.py"), "--self-test"])
    validate_calibration_evidence()
    selected = validate_selection()
    config = ensure_config(selected)
    archive_stale_plan_only(config)
    tasks = prepare_plan()
    completed, missing = completion(tasks)
    print(
        f"\n[resume] verified complete tasks: {completed}/3600; "
        f"remaining: {len(missing)}",
        flush=True,
    )
    if missing:
        theoretical_hours = len(missing) * 3 * 300 / max(1, WORKERS) / 3600
        print(
            f"[estimate] method-budget lower bound: about {theoretical_hours:.1f} "
            "worker-adjusted hours, plus construction/audit/output overhead.",
            flush=True,
        )
    previous_completed = completed
    for pass_number in range(1, max(1, MAX_RETRY_PASSES) + 1):
        if completed == 3600:
            break
        print(
            f"\n[execution pass {pass_number}/{MAX_RETRY_PASSES}] "
            "The runner will skip every verified complete task.",
            flush=True,
        )
        # Return code 2 means at least one task failed/timed out; recount below
        # and retry only those incomplete tasks in the next pass.
        run(runner_command("--no-aggregate"), allowed_codes={0, 2})
        completed, missing = completion(tasks)
        print(
            f"[progress] verified complete tasks: {completed}/3600; "
            f"remaining: {len(missing)}",
            flush=True,
        )
        if completed == previous_completed and missing:
            sample = ", ".join(str(index) for index in missing[:20])
            fail(
                "No progress was made in the last pass. Inspect the status.json "
                f"and job.log files for task indices: {sample}"
            )
        previous_completed = completed
    if completed != 3600:
        sample = ", ".join(str(index) for index in missing[:20])
        fail(
            f"After {MAX_RETRY_PASSES} passes, {len(missing)} tasks remain. "
            "Run this same file again to continue; completed tasks will not be "
            f"rerun. First incomplete task indices: {sample}"
        )
    print("\n[complete] 3,600/3,600 immutable tasks verified.", flush=True)
    aggregate_and_analyse()
    print("\n" + "=" * 78)
    print("120 x 30 CONFIRMATORY EXPERIMENT COMPLETE")
    print(f"Raw/aggregate results: {EXPERIMENT_ROOT}")
    if RUN_REVIEWER_STATISTICS:
        print(f"Reviewer statistics : {EXPERIMENT_ROOT / 'reviewer_analysis'}")
    print("=" * 78)


if __name__ == "__main__":
    main()
