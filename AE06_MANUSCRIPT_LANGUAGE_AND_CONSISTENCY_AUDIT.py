#!/usr/bin/env python3
"""AE Comment 6: fail-closed audit of the revised manuscript and response.

The script reads DOCX XML directly (no Word automation required), checks for
stale 10-run/ACO-era claims, unresolved placeholders, title mismatches,
machine-generated statistical values, and coloured revision text.  It cannot
replace a genuine line-by-line English edit; set LANGUAGE_EDIT_CONFIRMED=True
only after that edit has actually been completed.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


# ============================== USER SETTINGS ==============================
MANUSCRIPT_FILE = "Manuscript.docx"
RESPONSE_FILE = "Response_to_AE_GENO-2026-1699_WORKING_DRAFT.docx"
LANGUAGE_EDIT_CONFIRMED = False
REVISION_COLOUR_HEX = "0000FF"  # Change only if another clearly declared colour is used.
# ===========================================================================

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
EXPECTED_TITLE = "Persistent Benders-Cut Large-Neighborhood Search for the Mixed Chinese Postman Problem"
REQUIRED_ABSTRACT_SENTENCE = (
    "When the master lower bound equals the exact recourse value, the current "
    "restricted neighborhood is certified as solved to optimality."
)


def locate_root() -> Path:
    for root in [Path.cwd(), Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (root / "results" / "eo_confirmatory_120x30_strict" / "reviewer_analysis").is_dir():
            return root.resolve()
    raise SystemExit("Project root not found.")


def docx_paragraphs(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs = []
    for paragraph in root.iter(W + "p"):
        runs = []
        for run in paragraph.findall(".//" + W + "r"):
            text = "".join(node.text or "" for node in run.findall(".//" + W + "t"))
            if not text:
                continue
            color_node = run.find("./" + W + "rPr/" + W + "color")
            color = color_node.get(W + "val") if color_node is not None else None
            runs.append({"text": text, "color": color})
        text = "".join(run["text"] for run in runs)
        if text.strip():
            paragraphs.append({"text": text, "runs": runs})
    return paragraphs


def joined(paragraphs: list[dict[str, Any]]) -> str:
    return "\n".join(paragraph["text"] for paragraph in paragraphs)


def target_has_colour(paragraphs: list[dict[str, Any]], target: str, colour: str) -> bool:
    normalized_target = re.sub(r"\s+", " ", target).strip()
    for paragraph in paragraphs:
        normalized = re.sub(r"\s+", " ", paragraph["text"]).strip()
        if normalized_target in normalized:
            relevant = [run for run in paragraph["runs"] if run["text"].strip()]
            return bool(relevant) and all((run["color"] or "").upper() == colour.upper() for run in relevant)
    return False


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> None:
    root = locate_root()
    manuscript_path = root / MANUSCRIPT_FILE
    response_path = root / RESPONSE_FILE
    if not manuscript_path.exists() or not response_path.exists():
        raise SystemExit(f"Missing DOCX: {manuscript_path if not manuscript_path.exists() else response_path}")
    manuscript_paragraphs = docx_paragraphs(manuscript_path)
    response_paragraphs = docx_paragraphs(response_path)
    manuscript = joined(manuscript_paragraphs)
    response = joined(response_paragraphs)
    normalized_manuscript = re.sub(r"\s+", " ", manuscript)
    normalized_response = re.sub(r"\s+", " ", response)

    statistics_path = root / "results" / "eo_confirmatory_120x30_strict" / "reviewer_analysis" / "response_values.json"
    statistics = json.loads(statistics_path.read_text(encoding="utf-8"))
    metadata = json.loads((root / "publication_metadata.json").read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []

    add(checks, "title_matches_editor_letter", EXPECTED_TITLE in normalized_manuscript, f"Required title: {EXPECTED_TITLE}")
    add(checks, "abstract_sentence_corrected", REQUIRED_ABSTRACT_SENTENCE in normalized_manuscript, REQUIRED_ABSTRACT_SENTENCE)
    add(
        checks,
        "abstract_correction_highlighted",
        target_has_colour(manuscript_paragraphs, REQUIRED_ABSTRACT_SENTENCE, REVISION_COLOUR_HEX),
        f"The corrected sentence must be directly formatted in revision colour #{REVISION_COLOUR_HEX}.",
    )

    stale_patterns = {
        "ten random seeds": r"\bten random seeds\b|\b10 seeds\b",
        "1,200-task old block": r"\b1,?200 tasks\b",
        "6,000 old method records": r"\b6,?000 (?:main |method )?records\b",
        "old ACO main comparison": r"ACO-family comparator|PBC-versus-ACO|relative to ACO",
    }
    for label, pattern in stale_patterns.items():
        found = bool(re.search(pattern, normalized_manuscript, flags=re.IGNORECASE))
        add(checks, f"stale_claim_absent:{label}", not found, f"Pattern: {pattern}")

    required_counts = {
        "30 independent matched seeds": ("30", "matched seed"),
        "3,600 tasks": ("3,600", "3600"),
        "10,800 method rows": ("10,800", "10800"),
        "120 instances": ("120 instances",),
    }
    combined = normalized_manuscript + "\n" + normalized_response
    for label, alternatives in required_counts.items():
        passed = any(value.lower() in combined.lower() for value in alternatives)
        add(checks, f"new_design_present:{label}", passed, f"Expected one of {alternatives}")

    # Require the machine-generated pairwise values at four-decimal precision.
    for row in statistics["posthoc"]:
        effect = f"{float(row['mean_relative_improvement_pct']):.4f}"
        p_value = f"{float(row['p_holm_3']):.6g}"
        wtl_ascii = f"{row['wins']}-{row['ties']}-{row['losses']}"
        wtl_en = f"{row['wins']}–{row['ties']}–{row['losses']}"
        label = f"{row['reference']}_vs_{row['comparator']}"
        add(checks, f"statistical_effect_present:{label}", effect in combined, f"Required effect {effect}%")
        add(checks, f"statistical_p_present:{label}", p_value in combined, f"Required Holm p {p_value}")
        add(checks, f"statistical_wtl_present:{label}", wtl_ascii in combined or wtl_en in combined, f"Required W-T-L {wtl_ascii}")

    placeholders = ("{{", "}}", "REPLACE_WITH", "WORKING DRAFT", "Author check before submission")
    unresolved = [token for token in placeholders if token.lower() in normalized_response.lower()]
    add(checks, "response_has_no_placeholders", not unresolved, f"Unresolved markers: {unresolved}")

    repo = str(metadata.get("repository_url", ""))
    doi = str(metadata.get("archive_doi", ""))
    licence = str(metadata.get("code_license", ""))
    metadata_real = all(value and "REPLACE_WITH" not in value for value in (repo, doi, licence))
    add(checks, "public_metadata_is_real", metadata_real, f"repository={repo}; DOI={doi}; licence={licence}")
    if metadata_real:
        add(checks, "repository_url_in_manuscript", repo in normalized_manuscript, repo)
        add(checks, "doi_in_manuscript", doi in normalized_manuscript or doi.replace("https://doi.org/", "") in normalized_manuscript, doi)

    add(
        checks,
        "manual_line_by_line_language_edit_confirmed",
        LANGUAGE_EDIT_CONFIRMED,
        "This cannot be inferred safely from keyword checks. Set LANGUAGE_EDIT_CONFIRMED=True only after a real full-manuscript edit.",
    )

    # Exact code/prose corrections for AE 1.2 are required in both files.
    required_rng_phrases = (
        "every scoring call",
        "zero random weight",
        "advance"  # as in advance the seeded generator / RNG state
    )
    rng_correction_present = all(phrase in combined.lower() for phrase in required_rng_phrases)
    add(
        checks,
        "rng_consumption_wording_corrected",
        rng_correction_present,
        "State that every scoring call consumes the uniform vector; a zero Random weight removes its numerical contribution but still advances RNG state.",
    )
    false_log_claim = "records every selection" in combined.lower()
    add(
        checks,
        "false_neighborhood_log_claim_removed",
        not false_log_claim,
        "neighborhood_log.csv contains summaries, not selected edge IDs or per-row seeds.",
    )

    failures = [row for row in checks if not row["passed"]]
    report = {
        "manuscript": str(manuscript_path),
        "response": str(response_path),
        "checks": checks,
        "failures": len(failures),
        "submission_ready_for_comment_6": len(failures) == 0,
    }
    out = root / "results" / "revision_evidence"
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "AE06_manuscript_and_response_audit.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# AE Comment 6 manuscript/response audit",
        "",
        f"Overall: **{'PASS' if not failures else 'NOT READY'}** ({len(failures)} failure(s)).",
        "",
    ]
    for row in checks:
        lines.append(f"- [{'x' if row['passed'] else ' '}] {row['name']} — {row['detail']}")
    lines += [
        "",
        "## Non-negotiable scientific interpretation",
        "",
        "The current confirmatory evidence shows that PBC-XLNS beats Random ALNS, but the no-persistence variant beats full PBC-XLNS. Do not describe persistent-cut retention as a demonstrated quality benefit. Report it as a statistically significant quality loss under the tested 300 s protocol, while noting any verified reduction in exact-flow work separately.",
        "",
    ]
    md_path = out / "AE06_manuscript_and_response_audit.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(md_path)
    print(json_path)
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
