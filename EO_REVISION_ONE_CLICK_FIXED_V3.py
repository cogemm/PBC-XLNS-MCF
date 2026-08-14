#!/usr/bin/env python3
"""PyCharm single-entry workflow for the Engineering Optimization revision.

Edit only ACTION and WORKERS below, then press Run.  Every long phase is
resumable: completed immutable tasks are skipped.  The script intentionally
does not launch the multi-day confirmatory run by default.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


# ------------------------------ USER SETTINGS ------------------------------
ACTION = "RUN_PBC_DOE"
# PREFLIGHT | GENERATE_DOE | RUN_PBC_DOE | RUN_ALNS_DOE | SELECT_DOE |
# PREPARE_CONFIRMATORY | RUN_CONFIRMATORY | ANALYSE | BUILD_PUBLIC_RELEASE
WORKERS = 4
NO_DOWNLOAD = True  # Keep True because the verified 120-instance data are included.
# Execution-only parent watchdog.  This does not change the declared 300 s
# optimization budget or any design hash.  It only leaves enough time for the
# shared 12-start construction, final audit and file output on large instances.
WATCHDOG_SECONDS = 2400
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


def archive_stale_plan_only(config_path: Path) -> None:
    """Recover a mismatched PREPARE-only snapshot without touching evidence.

    PREPARE_ONLY creates a manifest, task plan and frozen source before any
    shard is run.  If the user then receives an updated source/config, that
    plan-only snapshot can legitimately have a stale design hash.  We archive
    it by rename only when its top level contains no shard or analysis output.
    Any real run evidence keeps the original fail-closed behaviour.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import benchmark_runner

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    experiment = str(config.get("experiment_name", config_path.stem))
    results_root = benchmark_runner.resolve_project_path(config.get("results_root", "results"))
    experiment_root = (results_root / experiment).resolve()
    manifest_path = experiment_root / "experiment_manifest.json"
    if not manifest_path.exists():
        return

    dataset = benchmark_runner.prepare_dataset(config, NO_DOWNLOAD, None)
    dataset_sha256, _ = benchmark_runner.dataset_fingerprint(dataset)
    code_sha256, _ = benchmark_runner.hash_inventory(benchmark_runner.source_inventory())
    current_design = benchmark_runner.sha256_bytes(
        benchmark_runner.canonical_json({
            "config": config,
            "code_sha256": code_sha256,
            "dataset_sha256": dataset_sha256,
        }).encode("utf-8")
    )
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    if existing.get("design_sha256") == current_design:
        return

    allowed = {"experiment_manifest.json", "tasks.jsonl", "frozen_source"}
    observed = {path.name for path in experiment_root.iterdir()}
    if not observed <= allowed or (experiment_root / "shards").exists():
        raise RuntimeError(
            f"Design mismatch for {experiment}. The directory contains run evidence and "
            "will not be moved automatically. Inspect it manually: {experiment_root}"
        )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = experiment_root.with_name(f"{experiment}_PLAN_ONLY_ARCHIVE_{timestamp}")
    counter = 1
    while archive.exists():
        archive = experiment_root.with_name(
            f"{experiment}_PLAN_ONLY_ARCHIVE_{timestamp}_{counter:02d}"
        )
        counter += 1
    experiment_root.rename(archive)
    print(
        f"[safe recovery] archived stale plan-only snapshot: {archive.name}\n"
        "[safe recovery] no shard, result row, or completed task was removed.",
        flush=True,
    )


def run(*arguments: str) -> None:
    command = [PYTHON, *arguments]
    print("\n[command]", subprocess.list2cmdline(command), flush=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    completed = subprocess.run(command, cwd=ROOT, env=environment)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def runner(config: Path, *extra: str) -> None:
    archive_stale_plan_only(config)
    arguments = [
        str(ROOT / "scripts" / "run_experiments.py"),
        "--watchdog-seconds", str(max(1, WATCHDOG_SECONDS)),
        "--config", str(config),
        "--workers", str(max(1, WORKERS)), *extra,
    ]
    if NO_DOWNLOAD:
        arguments.append("--no-download")
    run(*arguments)


def generated_configs(prefix: str) -> list[Path]:
    paths = sorted((ROOT / "configs" / "generated_doe").glob(f"{prefix}_d*.yaml"))
    if len(paths) != 27:
        raise RuntimeError("Run GENERATE_DOE first; exactly 27 configs are required")
    return paths


def main() -> None:
    action = ACTION.strip().upper()
    if action == "PREFLIGHT":
        run("-m", "compileall", "-q", str(ROOT / "src"), str(ROOT / "scripts"), str(Path(__file__).resolve()))
        run(str(ROOT / "src" / "pbc_xlns_mcf_v10_1.py"), "--self-test")
        run(str(ROOT / "scripts" / "eo_doe.py"), "generate")
        runner(generated_configs("pbc")[0], "--prepare-only")
        print("\n[PREFLIGHT PASS] Code, solver audit, OA design and dataset manifest are valid.")
        print("Next set ACTION='RUN_PBC_DOE', then ACTION='RUN_ALNS_DOE'.")
    elif action == "GENERATE_DOE":
        run(str(ROOT / "scripts" / "eo_doe.py"), "generate")
    elif action in {"RUN_PBC_DOE", "RUN_ALNS_DOE"}:
        prefix = "pbc" if action == "RUN_PBC_DOE" else "alns"
        for number, config in enumerate(generated_configs(prefix), start=1):
            print(f"\n[DOE {prefix.upper()} {number}/27] {config.name}", flush=True)
            runner(config)
    elif action == "SELECT_DOE":
        run(str(ROOT / "scripts" / "eo_doe.py"), "select")
        print("\n[SELECTION FROZEN] configs/eo_confirmatory_120x30_strict.yaml was generated.")
    elif action == "PREPARE_CONFIRMATORY":
        config = ROOT / "configs" / "eo_confirmatory_120x30_strict.yaml"
        if not config.exists():
            run(str(ROOT / "scripts" / "eo_doe.py"), "confirmatory-config")
        runner(config, "--prepare-only")
        print("\n[PLAN READY] 3,600 tasks = 120 instances x 30 seeds; three matched methods per task.")
    elif action == "RUN_CONFIRMATORY":
        config = ROOT / "configs" / "eo_confirmatory_120x30_strict.yaml"
        if not config.exists():
            raise RuntimeError("Run SELECT_DOE before the confirmatory experiment")
        runner(config)
    elif action == "ANALYSE":
        run(str(ROOT / "scripts" / "reviewer_statistics.py"))
        run(str(ROOT / "scripts" / "fill_response_placeholders.py"))
        run(str(ROOT / "scripts" / "build_response_docx.py"))
    elif action == "BUILD_PUBLIC_RELEASE":
        run(str(ROOT / "scripts" / "build_eo_public_release.py"))
    else:
        raise ValueError(f"Unknown ACTION={ACTION!r}")


if __name__ == "__main__":
    main()
