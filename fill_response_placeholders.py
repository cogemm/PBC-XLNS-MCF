#!/usr/bin/env python3
"""Fill the AE response only from verified machine-readable evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def compact_parameters(choice: dict[str, Any]) -> str:
    factors = choice["factor_values"]
    return "\n".join(f"- `{name}` = `{value}`" for name, value in factors.items())


def statistical_text(payload: dict[str, Any]) -> str:
    omnibus = next(row for row in payload["omnibus"] if row["primary"])
    lines = [
        f"The predeclared primary omnibus test was `{payload['omnibus_primary']}` "
        f"(statistic = {omnibus['statistic']:.6g}, p = {omnibus['p_value']:.6g}).",
        "",
        "| Reference | Comparator | Test | Mean improvement (95% CI), % | Holm p | W–T–L |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in payload["posthoc"]:
        lines.append(
            f"| {row['reference']} | {row['comparator']} | {row['selected_test']} | "
            f"{row['mean_relative_improvement_pct']:.4f} "
            f"[{row['ci95_low_pct']:.4f}, {row['ci95_high_pct']:.4f}] | "
            f"{row['p_holm_3']:.6g} | {row['wins']}–{row['ties']}–{row['losses']} |"
        )
    return "\n".join(lines)


def main() -> None:
    selections_path = ROOT / "calibration" / "selected_parameters.json"
    statistics_path = ROOT / "results" / "eo_confirmatory_120x30_strict" / "reviewer_analysis" / "response_values.json"
    metadata_path = ROOT / "publication_metadata.json"
    missing = [str(path) for path in (selections_path, statistics_path, metadata_path) if not path.exists()]
    if missing:
        raise RuntimeError(f"response filling is blocked; missing evidence: {missing}")
    selections = json.loads(selections_path.read_text(encoding="utf-8"))
    statistics = json.loads(statistics_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    for key in ("repository_url", "archive_doi", "code_license"):
        if str(metadata.get(key, "")).startswith("REPLACE_WITH"):
            raise RuntimeError(f"set a real {key} in publication_metadata.json before finalizing the response")
    template = (ROOT / "docs" / "RESPONSE_TO_AE_TEMPLATE.md").read_text(encoding="utf-8")
    replacements = {
        "{{SELECTED_PBC_PARAMETERS}}": compact_parameters(selections["pbc"]),
        "{{SELECTED_ALNS_PARAMETERS}}": compact_parameters(selections["alns"]),
        "{{STATISTICAL_RESULTS}}": statistical_text(statistics),
        "{{REPOSITORY_URL}}": metadata["repository_url"],
        "{{ARCHIVE_DOI}}": metadata["archive_doi"],
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    if "{{" in template or "REPLACE_WITH" in template:
        raise RuntimeError("unresolved response placeholder remains")
    output = ROOT / "docs" / "RESPONSE_TO_AE_COMPLETED.md"
    output.write_text(template, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
