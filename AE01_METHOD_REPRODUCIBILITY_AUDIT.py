#!/usr/bin/env python3
"""AE Comment 1 (1.1-1.3): audit method reproducibility and wording.

Copy this file to the project root, or run it from its current folder while
the project is the working directory.  It does not alter source code or
experimental results.  It writes a machine-readable JSON report and a concise
Markdown report under ``results/revision_evidence``.

This audit deliberately checks the frozen implementation rather than trusting
the response-letter prose.  In particular it detects RNG-consumption details
that affect exact trajectory replay and checks whether the configured maximum
neighbourhood size is reachable before a stagnation restart.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc


# Set to True only if you want PyCharm to return a non-zero exit code when a
# blocker is found.  The report is written in either case.
FAIL_ON_BLOCKER = False


def locate_root() -> Path:
    anchors = [Path.cwd(), Path(__file__).resolve().parent]
    anchors += list(Path(__file__).resolve().parents)
    for candidate in anchors:
        if (
            (candidate / "src" / "pbc_xlns_mcf_v10_1.py").is_file()
            and (candidate / "calibration" / "selected_parameters.json").is_file()
            and (candidate / "configs" / "eo_confirmatory_120x30_strict.yaml").is_file()
        ):
            return candidate.resolve()
    raise SystemExit(
        "Project root not found. Put this script in PBC_XLNS_MCF_EO_Revision "
        "or set that directory as the PyCharm working directory."
    )


def same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def parse_weight_json(value: Any) -> dict[str, dict[str, float]]:
    payload = json.loads(value) if isinstance(value, str) else value
    if not isinstance(payload, dict):
        raise ValueError("scoring_weights_json is not a JSON object")
    modes = {"dual", "disagreement", "exposure", "mixed", "path", "explore"}
    components = {"dual", "disagreement", "exposure", "length", "random"}
    if set(payload) != modes:
        raise ValueError(f"expected six modes {sorted(modes)}, observed {sorted(payload)}")
    result: dict[str, dict[str, float]] = {}
    for mode in sorted(modes):
        row = payload[mode]
        if set(row) != components:
            raise ValueError(f"mode {mode} has components {sorted(row)}, expected {sorted(components)}")
        values = {name: float(row[name]) for name in components}
        if any((not math.isfinite(value)) or value < 0 for value in values.values()):
            raise ValueError(f"mode {mode} has a negative or non-finite weight")
        if not math.isclose(sum(values.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"mode {mode} weights sum to {sum(values.values())}, not 1")
        result[mode] = values
    return result


def stream_neighbourhood_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False}
    modes: Counter[str] = Counter()
    sizes: Counter[int] = Counter()
    rows = 0
    restarts = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            modes[str(row.get("mode", ""))] += 1
            try:
                sizes[int(float(row.get("neighborhood_size", 0)))] += 1
            except (TypeError, ValueError):
                pass
            if str(row.get("restart", "")).strip().lower() in {"1", "true", "yes", "t"}:
                restarts += 1
    return {
        "present": True,
        "rows": rows,
        "modes": dict(sorted(modes.items())),
        "sizes": {str(k): v for k, v in sorted(sizes.items())},
        "restarts": restarts,
    }


def main() -> None:
    root = locate_root()
    out = root / "results" / "revision_evidence"
    out.mkdir(parents=True, exist_ok=True)

    selected = json.loads((root / "calibration" / "selected_parameters.json").read_text(encoding="utf-8"))
    protocol = json.loads((root / "calibration" / "CALIBRATION_PROTOCOL.json").read_text(encoding="utf-8"))
    config = yaml.safe_load((root / "configs" / "eo_confirmatory_120x30_strict.yaml").read_text(encoding="utf-8"))
    params = dict(config["parameters"])
    source_path = root / "src" / "pbc_xlns_mcf_v10_1.py"
    source = source_path.read_text(encoding="utf-8")

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str, severity: str = "error") -> None:
        checks.append({"name": name, "passed": bool(passed), "severity": severity, "detail": detail})

    # 1.1: selected values and six-mode matrix.
    pbc_params = selected["pbc"]["parameters"]
    alns_params = selected["alns"]["parameters"]
    mismatches = []
    # The PBC calibration record also carries baseline Random-ALNS values
    # because all settings live in one SearchConfig.  Those four fields are
    # deliberately replaced by the independently selected ALNS row for the
    # confirmatory experiment, so compare them only against selected["alns"].
    alns_specific = {
        "random_alns_destroy_min", "random_alns_destroy_max",
        "sa_start_fraction", "sa_end_fraction",
    }
    for key, expected in pbc_params.items():
        if key in alns_specific:
            continue
        actual = params.get(key)
        if key == "scoring_weights_json":
            try:
                matches = parse_weight_json(actual) == parse_weight_json(expected)
            except Exception:
                matches = False
        else:
            matches = same_value(actual, expected)
        if not matches:
            mismatches.append(key)
    for key in sorted(alns_specific):
        if not same_value(params.get(key), alns_params.get(key)):
            mismatches.append(key)
    add(
        "selected_parameters_match_confirmatory_config",
        not mismatches,
        "all selected PBC and Random-ALNS values match" if not mismatches else f"mismatched keys: {mismatches}",
    )

    try:
        weights = parse_weight_json(params["scoring_weights_json"])
        add("six_mode_weight_matrix_valid", True, "six modes, five components per mode, nonnegative rows summing to one")
    except Exception as exc:
        weights = {}
        add("six_mode_weight_matrix_valid", False, repr(exc))

    add(
        "doe_protocol_declares_all_factors",
        protocol.get("design") == "OA(27,13,3,2)" and len(protocol.get("pbc_factors", {})) == 13,
        f"design={protocol.get('design')}; PBC factors={len(protocol.get('pbc_factors', {}))}",
    )

    # 1.2: exact implementation details that the current response draft omits.
    rng_all_modes = "row[\"random\"] * rng.random(prep.m)" in source
    frontier_shuffle = "rng.shuffle(frontier)" in source
    add(
        "rng_vector_is_drawn_on_every_scoring_call",
        rng_all_modes,
        "The frozen code consumes an m-vector of U(0,1) draws in every mode; zero random weight makes its numerical contribution zero but does not undo RNG consumption.",
    )
    add(
        "cluster_frontier_shuffle_is_part_of_rng_order",
        frontier_shuffle,
        "The frozen code calls rng.shuffle(frontier) before collecting graph-local candidates. The candidate set is later sorted, but the shuffle still changes downstream RNG state.",
    )
    response_template = (root / "docs" / "RESPONSE_TO_AE_TEMPLATE.md").read_text(encoding="utf-8")
    inaccurate_rng_sentence = "only in modes whose Random weight is nonzero" in response_template
    add(
        "response_rng_wording_matches_code",
        not inaccurate_rng_sentence,
        "Current response says draws occur only for nonzero Random weight; replace it with the exact all-mode RNG-consumption rule.",
    )
    log_claim = "records every selection" in (root / "docs" / "MANUSCRIPT_REVISION_TEXT.md").read_text(encoding="utf-8")
    add(
        "log_claim_is_truthful",
        not log_claim,
        "neighborhood_log.csv stores mode/size/rank summaries, not selected edge IDs or a per-row seed. Remove the claim that it records every selection.",
    )

    # 1.3: growth and restart reachability.
    k_min = int(params["neighborhood_min"])
    k_max = int(params["neighborhood_max"])
    k_step = int(params["neighborhood_step"])
    restart = int(params["restart_stagnation"])
    growth_period = max(1, restart // 3)
    max_stagnation_at_iteration_start = max(0, restart - 1)
    max_growth_level_before_restart = max_stagnation_at_iteration_start // growth_period
    maximum_reachable = min(k_max, k_min + max_growth_level_before_restart * k_step)
    max_is_reachable = maximum_reachable >= k_max
    add(
        "configured_neighborhood_max_is_reachable_before_restart",
        max_is_reachable,
        (
            f"k_min={k_min}, k_step={k_step}, R={restart}, growth period={growth_period}; "
            f"largest size reachable before restart is {maximum_reachable}, but configured k_max={k_max}. "
            "Therefore k_max was a nonbinding safety cap, not an empirically exercised maximum."
        ),
    )

    add(
        "mode_adaptation_rule_present",
        all(token in source for token in (
            "mode_success = {mode: 1.0", "mode_trials = {mode: 1", "mode_success[m] / max(1, mode_trials[m])",
            "rng.random() < cfg.mode_adaptation_probability", "rng.random(len(modes)) * 1e-6",
        )),
        "Laplace-style initialisation, q-gated selection, success/trial ratio, and seeded 1e-6 tie jitter are present in the frozen source.",
    )

    merged_log = root / "results" / "eo_confirmatory_120x30_strict" / "analysis" / "merged_neighborhood_log.csv"
    log_summary = stream_neighbourhood_log(merged_log)

    blockers = [row for row in checks if not row["passed"] and row["severity"] == "error"]
    report = {
        "root": str(root),
        "selected_pbc_design": selected["pbc"]["design_id"],
        "selected_alns_design": selected["alns"]["design_id"],
        "selected_scoring_weights": weights,
        "growth_reachability": {
            "k_min": k_min,
            "k_max_configured": k_max,
            "k_step": k_step,
            "restart_stagnation": restart,
            "growth_period": growth_period,
            "maximum_reachable_before_restart": maximum_reachable,
        },
        "neighborhood_log_summary": log_summary,
        "checks": checks,
        "blockers": len(blockers),
        "submission_ready_for_comment_1": len(blockers) == 0,
    }
    json_path = out / "AE01_method_reproducibility_audit.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# AE Comment 1 reproducibility audit",
        "",
        f"Overall: **{'PASS' if not blockers else 'NOT READY'}** ({len(blockers)} blocker(s)).",
        "",
    ]
    for row in checks:
        marker = "PASS" if row["passed"] else "FAIL"
        lines += [f"## [{marker}] {row['name']}", "", row["detail"], ""]
    lines += [
        "## Required wording corrections",
        "",
        "1. State that the implementation draws a length-m uniform vector on every scoring call. In modes with zero Random weight the contribution is zero, but the draws still advance the seeded generator.",
        "2. State that the graph-local frontier is shuffled before candidate collection. Although candidates are subsequently sorted, the shuffle is part of the exact RNG call order.",
        "3. Do not claim that neighborhood_log.csv stores selected edges or a per-iteration seed. It stores summary fields; exact selections follow from the archived task seed, code, configuration, and deterministic call order.",
        f"4. Do not describe k_max={k_max} as an exercised or identified maximum. Under the frozen restart rule, the largest reachable value is {maximum_reachable}. Either report k_max as a nonbinding cap or change the algorithm and rerun calibration plus confirmation.",
        "",
    ]
    md_path = out / "AE01_method_reproducibility_audit.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(md_path)
    print(json_path)
    if blockers and FAIL_ON_BLOCKER:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
