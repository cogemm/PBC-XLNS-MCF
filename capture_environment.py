#!/usr/bin/env python3
"""Capture the exact software/hardware environment used for an experiment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "environment")
    parser.add_argument("--experiment-root", type=Path, default=None)
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    distributions = sorted(
        f"{dist.metadata['Name']}=={dist.version}"
        for dist in importlib.metadata.distributions()
        if dist.metadata.get("Name")
    )
    (output / "pip_freeze.txt").write_text("\n".join(distributions) + "\n", encoding="utf-8")
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            capture_output=True, check=True, timeout=10,
        ).stdout.strip()
        git_dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True,
                capture_output=True, check=True, timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        git_commit, git_dirty = None, None
    payload = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "command": sys.argv,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "python": sys.version,
        "python_executable": sys.executable,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "thread_environment": {
            key: os.environ.get(key)
            for key in [
                "PYTHONHASHSEED", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
            ]
        },
        "pip_freeze_sha256": sha256_file(output / "pip_freeze.txt"),
    }
    if args.experiment_root:
        experiment = args.experiment_root.expanduser().resolve()
        manifest = experiment / "experiment_manifest.json"
        if not manifest.exists():
            raise FileNotFoundError(manifest)
        payload["experiment_root"] = str(experiment)
        payload["experiment_manifest_sha256"] = sha256_file(manifest)
    (output / "environment.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(output / "environment.json")


if __name__ == "__main__":
    main()

