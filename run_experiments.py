#!/usr/bin/env python3
"""Stable command-line entry point for the immutable benchmark runner.

This wrapper intentionally keeps execution-only recovery controls outside the
scientific design.  In particular, ``--watchdog-seconds`` changes only how
long the parent process is willing to wait for one task process.  It does NOT
change ``--budget-seconds`` passed to any optimization method.

That distinction is important for recovery of a partially completed pilot:
the original task plan, design hash, random seeds, method budgets, frozen
solver source, and already-complete shards remain untouched.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import benchmark_runner


def _install_single_method_figure_guard() -> None:
    """Skip a pairwise-only plot when a calibration has one method.

    OA calibration configurations intentionally run exactly one method.  The
    tabular aggregation correctly returns an empty pairwise table, but the
    original win/tie/loss plot accessed ``pairwise.profile`` before checking
    emptiness.  This wrapper-level compatibility guard changes presentation
    only; it is outside the frozen scientific source inventory and does not
    alter results, budgets, methods, seeds, or design hashes.
    """
    import make_figures

    original = make_figures.win_tie_loss

    def guarded_win_tie_loss(pairwise, output_dir, profile):
        if pairwise is None or pairwise.empty or "profile" not in pairwise.columns:
            print(
                f"[figures] skipped win/tie/loss for {profile}: "
                "single-method calibration has no pairwise contrasts",
                flush=True,
            )
            return None
        return original(pairwise, output_dir, profile)

    make_figures.win_tie_loss = guarded_win_tie_loss


def _parse_wrapper_args(argv: Sequence[str]) -> tuple[float | None, list[str]]:
    """Remove wrapper-only options before benchmark_runner parses argv."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--watchdog-seconds",
        type=float,
        default=None,
        help=(
            "Execution-only parent-process watchdog for each task. "
            "Does not alter the per-method optimization budget."
        ),
    )
    wrapper_args, remaining = parser.parse_known_args(list(argv))
    watchdog = wrapper_args.watchdog_seconds
    if watchdog is not None and watchdog <= 0:
        parser.error("--watchdog-seconds must be > 0")
    return watchdog, remaining


def _install_watchdog_override(watchdog_seconds: float) -> None:
    """Install recovery controls without changing the scientific task design."""
    original_run_task = benchmark_runner.run_task

    def run_task_with_watchdog(
        task: benchmark_runner.Task,
        force: bool = False,
        dry_run: bool = False,
    ):
        # benchmark_runner normally copies an incomplete ``run`` tree under
        # ``attempt_NNN/preexisting_incomplete_run`` before retrying.  On
        # Windows that extra nesting can exceed MAX_PATH and raise WinError
        # 206.  Preserve the old evidence by an atomic sibling rename instead:
        # it is reversible, much shorter, and avoids recursively copying long
        # paths.  Complete tasks are never moved unless the caller explicitly
        # requested --force.
        if not dry_run and task.run_dir.exists():
            complete = benchmark_runner.task_complete(task)
            if force or not complete:
                archive_index = 1
                while True:
                    archive = task.task_root / f"oldrun_{archive_index:03d}"
                    if not archive.exists():
                        break
                    archive_index += 1
                task.run_dir.replace(archive)
                print(
                    f"[runner] archived incomplete run for {task.task_id}: "
                    f"{archive.name}",
                    flush=True,
                )

        execution_task = replace(task, hard_timeout_seconds=watchdog_seconds)
        return original_run_task(execution_task, force=force, dry_run=dry_run)

    benchmark_runner.run_task = run_task_with_watchdog


def main() -> None:
    watchdog_seconds, remaining = _parse_wrapper_args(sys.argv[1:])
    sys.argv = [sys.argv[0], *remaining]
    _install_single_method_figure_guard()

    if watchdog_seconds is not None:
        _install_watchdog_override(watchdog_seconds)
        print(
            "[runner] execution watchdog override: "
            f"{watchdog_seconds:g}s per task; method budgets are unchanged",
            flush=True,
        )

    benchmark_runner.main()


if __name__ == "__main__":
    main()
