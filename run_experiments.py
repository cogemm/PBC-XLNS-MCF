#!/usr/bin/env python3
"""Stable command-line entry point for the immutable benchmark runner."""

# ruff: noqa: E402

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from benchmark_runner import main


if __name__ == "__main__":
    main()
