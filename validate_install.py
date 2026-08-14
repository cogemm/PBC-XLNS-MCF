#!/usr/bin/env python3
"""Run dependency, reference-data, source, and optional integration checks."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-smoke", action="store_true")
    args = parser.parse_args()
    versions = {}
    for module in ["numpy", "pandas", "networkx", "scipy", "matplotlib", "yaml", "ortools"]:
        loaded = importlib.import_module(module)
        versions[module] = getattr(loaded, "__version__", "installed")
    run([sys.executable, "-m", "py_compile", *[str(path) for path in sorted((ROOT / "src").glob("*.py"))]])
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    run([sys.executable, "src/pbc_xlns_mcf_v10_1.py", "--self-test"])
    if args.with_smoke:
        run(
            [
                sys.executable, "scripts/run_experiments.py", "--config", "configs/smoke.yaml",
                "--workers", "1", "--no-download",
            ]
        )
    print(json.dumps({"validation": "passed", "versions": versions}, indent=2))


if __name__ == "__main__":
    main()

