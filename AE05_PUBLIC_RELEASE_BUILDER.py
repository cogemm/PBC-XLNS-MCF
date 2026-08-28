#!/usr/bin/env python3
"""AE Comment 5: build a deterministic local draft or submission release.

``local_draft`` mode writes a clearly watermarked local ZIP without requiring
a repository, DOI, or licence.  It never changes publication_metadata.json or
the response letter and therefore cannot be mistaken for evidence that AE
Comment 5 has been satisfied.

``submission_release`` mode is fail-closed: it requires a real public
repository URL, reserved/minted DOI, author-approved software licence, clean
confirmatory timing, and a project-root LICENSE before it creates the final
deposit and fills response placeholders.

Raw third-party Corberan instance files are excluded.  The official URLs,
downloader, reference tables, and per-instance SHA-256 manifest are included.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable


# ============================== USER SETTINGS ==============================
# Use "local_draft" now.  Change to "submission_release" only after the public
# repository, DOI and licence genuinely exist.
BUILD_MODE = "local_draft"  # local_draft | submission_release

# This is the only setting required in local_draft mode.  A raw Windows path is
# intentional so backslashes are not interpreted as escape sequences.
LOCAL_OUTPUT_DIR = Path(
    r"E:\PythonProject8\PBC_XLNS_MCF_EO_Revision\release_local_draft"
)

# The following settings are used only in submission_release mode.
REPOSITORY_URL = "REPLACE_WITH_PUBLIC_REPOSITORY_URL"
ARCHIVE_DOI = "REPLACE_WITH_RESERVED_OR_MINTED_DOI"
CODE_LICENSE = "REPLACE_WITH_LICENSE_IDENTIFIER"  # e.g. MIT, BSD-3-Clause
AUTHORS = "Ku Junhua et al."
# ===========================================================================

MAX_ALLOWED_CONFIRMATORY_OVERSHOOT_SECONDS = 5.0
FIXED_ZIP_TIMESTAMP = (2026, 8, 28, 0, 0, 0)
INSTANCE_RE = re.compile(r"^M[AB]\d{4}$")


def locate_root() -> Path:
    for root in [Path.cwd(), Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (root / "src").is_dir() and (root / "results" / "eo_confirmatory_120x30_strict").is_dir():
            return root.resolve()
    raise SystemExit("Project root not found.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_real_metadata() -> None:
    values = {
        "REPOSITORY_URL": REPOSITORY_URL,
        "ARCHIVE_DOI": ARCHIVE_DOI,
        "CODE_LICENSE": CODE_LICENSE,
    }
    missing = [name for name, value in values.items() if (not value.strip()) or "REPLACE_WITH" in value]
    if missing:
        raise SystemExit("Edit USER SETTINGS first: " + ", ".join(missing))
    if not REPOSITORY_URL.startswith("https://"):
        raise SystemExit("REPOSITORY_URL must be a public https:// URL")
    if not (ARCHIVE_DOI.startswith("10.") or ARCHIVE_DOI.startswith("https://doi.org/10.")):
        raise SystemExit("ARCHIVE_DOI must be a real DOI or https://doi.org/ URL")


def should_include(root: Path, path: Path) -> bool:
    if not path.is_file():
        return False
    relative = path.relative_to(root)
    if any(part in {"__pycache__", ".git", ".cache", "matplotlib_cache"} for part in relative.parts):
        return False
    if path.suffix.lower() in {".pyc", ".tmp"}:
        return False
    if any(part.startswith("oldrun_") for part in relative.parts):
        return False
    if "preexisting_incomplete_run" in relative.parts:
        return False
    if len(relative.parts) >= 3 and relative.parts[0:2] == ("data", "corberan_mcpp"):
        if INSTANCE_RE.fullmatch(path.name):
            return False
    if relative.parts and relative.parts[0] == "release":
        return False
    return True


def source_paths(root: Path) -> Iterable[Path]:
    top_files = [
        "README.md", "README_EO_REVISION.md", "README.txt", "CITATION.cff", "LICENSE",
        "Dockerfile", "environment.yml", "pyproject.toml", "requirements.txt",
        "requirements-lock.txt", "publication_metadata.json", "EO_REVISION_ONE_CLICK.py",
    ]
    for name in top_files:
        path = root / name
        if path.is_file() and should_include(root, path):
            yield path
    for directory_name in ("src", "scripts", "configs", "calibration", "docs", "tests", "data"):
        directory = root / directory_name
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if should_include(root, path):
                    yield path
    result_roots = sorted((root / "results").glob("eo_cal_*"))
    result_roots.append(root / "results" / "eo_confirmatory_120x30_strict")
    evidence = root / "results" / "revision_evidence"
    if evidence.is_dir():
        result_roots.append(evidence)
    for directory in result_roots:
        if not directory.is_dir():
            raise RuntimeError(f"missing required result directory: {directory}")
        for path in sorted(directory.rglob("*")):
            if should_include(root, path):
                yield path


def verify_confirmatory(root: Path, require_clean_timing: bool) -> dict[str, object]:
    import csv
    merged = root / "results" / "eo_confirmatory_120x30_strict" / "analysis" / "merged_run_results.csv"
    with merged.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 10800:
        raise RuntimeError(f"expected 10,800 confirmatory method rows, observed {len(rows)}")
    maximum = max(float(row.get("budget_overshoot_seconds") or 0.0) for row in rows)
    contaminated = [
        row for row in rows
        if float(row.get("budget_overshoot_seconds") or 0.0)
        > MAX_ALLOWED_CONFIRMATORY_OVERSHOOT_SECONDS
    ]
    contaminated_tasks = sorted({(row["instance"], int(row["seed"])) for row in contaminated})
    if require_clean_timing and contaminated:
        raise RuntimeError(
            f"timing-contaminated tasks remain (max overshoot {maximum:.3f} s); run AE04 first"
        )
    return {
        "method_rows": len(rows),
        "maximum_overshoot_seconds": maximum,
        "contaminated_method_rows": len(contaminated),
        "contaminated_task_groups": len(contaminated_tasks),
        "timing_is_submission_clean": not contaminated,
    }


def main() -> None:
    if BUILD_MODE not in {"local_draft", "submission_release"}:
        raise SystemExit('BUILD_MODE must be "local_draft" or "submission_release"')

    root = locate_root()
    submission = BUILD_MODE == "submission_release"
    timing = verify_confirmatory(root, require_clean_timing=submission)

    if submission:
        ensure_real_metadata()
        if not (root / "LICENSE").is_file():
            raise SystemExit(
                "Add the author-approved software licence text as project-root LICENSE first."
            )
        metadata = {
            "build_mode": BUILD_MODE,
            "repository_url": REPOSITORY_URL,
            "archive_doi": ARCHIVE_DOI,
            "code_license": CODE_LICENSE,
            "authors": AUTHORS,
            "confirmatory_timing": timing,
        }
        (root / "publication_metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        filler = root / "scripts" / "fill_response_placeholders.py"
        completed = subprocess.run([sys.executable, str(filler)], cwd=root)
        if completed.returncode != 0:
            raise SystemExit("The AE response could not be finalized; inspect the error above.")
        output_dir = root / "release"
        archive_name = "PBC_XLNS_MCF_Engineering_Optimization_evidence.zip"
    else:
        output_dir = LOCAL_OUTPUT_DIR.expanduser().resolve()
        if output_dir == root:
            raise SystemExit("LOCAL_OUTPUT_DIR must not be the project root.")
        metadata = {
            "build_mode": BUILD_MODE,
            "submission_ready": False,
            "warning": (
                "LOCAL DRAFT ONLY. No public repository, DOI, or code licence "
                "has been asserted. Do not cite or submit this archive."
            ),
            "local_output_directory": str(output_dir),
            "confirmatory_timing": timing,
        }
        archive_name = "DRAFT_NOT_FOR_SUBMISSION_PBC_XLNS_MCF_evidence.zip"
        if not timing["timing_is_submission_clean"]:
            print(
                "[DRAFT WARNING] Timing-contaminated tasks remain: "
                f"{timing['contaminated_task_groups']} task groups; "
                f"maximum overshoot={timing['maximum_overshoot_seconds']:.3f} s."
            )

    files = sorted(set(source_paths(root)), key=lambda path: path.relative_to(root).as_posix())
    if not submission:
        # Do not embed stale public placeholders in a local draft.  The draft
        # metadata written inside the ZIP is the only authoritative metadata.
        files = [path for path in files if path.name != "publication_metadata.json"]
    manifest = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in files
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / archive_name
    top = Path("PBC_XLNS_MCF_EO_evidence")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = top / path.relative_to(root)
            info = zipfile.ZipInfo(relative.as_posix(), FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        info = zipfile.ZipInfo((top / "RELEASE_MANIFEST.json").as_posix(), FIXED_ZIP_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(
            info,
            json.dumps({"metadata": metadata, "files": manifest}, indent=2, ensure_ascii=False).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )
        if not submission:
            warning = (
                "LOCAL DRAFT ONLY -- NOT FOR SUBMISSION\n\n"
                "This archive does not demonstrate public availability. It has "
                "no asserted repository URL, DOI, or software licence. Run AE04, "
                "create the public records, add LICENSE, change BUILD_MODE to "
                "submission_release, and rebuild before citing the archive.\n"
            )
            info = zipfile.ZipInfo((top / "DRAFT_WARNING.txt").as_posix(), FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, warning.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    summary = {
        "archive": str(output),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "files": len(files),
        "raw_corberan_instances_included": False,
        "build_mode": BUILD_MODE,
        "submission_ready": submission,
        "repository_url": REPOSITORY_URL if submission else None,
        "archive_doi": ARCHIVE_DOI if submission else None,
        "confirmatory_timing": timing,
    }
    summary_name = "PUBLIC_RELEASE_SUMMARY.json" if submission else "LOCAL_DRAFT_SUMMARY.json"
    summary_path = output_dir / summary_name
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if submission:
        print("Upload the ZIP to the declared repository/archive, verify public access, then rerun AE06.")
    else:
        print("Local draft created. It does NOT satisfy AE Comment 5 and must not be submitted.")


if __name__ == "__main__":
    main()
