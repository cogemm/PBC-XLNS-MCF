#!/usr/bin/env python3
"""Fail-closed gate for a public code-and-results deposit."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aggregate_results import IncompleteExperimentError, aggregate_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, action="append", required=True)
    parser.add_argument("--allow-license-review", action="store_true")
    parser.add_argument("--allow-metadata-review", action="store_true")
    args = parser.parse_args()
    failures = []
    if not (ROOT / "LICENSE").exists() and not args.allow_license_review:
        failures.append("Code owner has not selected a LICENSE file")
    citation = ROOT / "CITATION.cff"
    if (
        citation.exists()
        and "REPLACE_WITH_" in citation.read_text(encoding="utf-8")
        and not args.allow_metadata_review
    ):
        failures.append("CITATION.cff still contains placeholder author/repository/license metadata")
    for root in args.experiment_root:
        root = root.expanduser().resolve()
        try:
            aggregate_experiment(root, allow_partial=False)
        except (IncompleteExperimentError, FileNotFoundError, RuntimeError) as exc:
            failures.append(f"{root}: {exc}")
        environment = root / "environment" / "environment.json"
        if not environment.exists():
            failures.append(f"{root}: missing captured environment")
    report = {"passed": not failures, "failures": failures}
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
