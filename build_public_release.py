#!/usr/bin/env python3
"""Build a deterministic public archive after the publication gate passes."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aggregate_results import aggregate_experiment


CODE_ITEMS = [
    "src", "scripts", "configs", "tests", "docs", "README.md", "pyproject.toml",
    "requirements.txt", "requirements-lock.txt", "environment.yml", "Dockerfile",
    "CITATION.cff", "LICENSE", "LICENSE_NOT_PROVIDED.md", ".gitignore",
]
TEXT_SUFFIXES = {".json", ".jsonl", ".txt", ".csv", ".md", ".yaml", ".yml", ".log", ".tex", ".cff"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_item(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    elif source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def redact_local_paths(release: Path) -> list[dict[str, str]]:
    """Redact only local project-root strings and record a reversible audit trail."""
    replacements = {str(ROOT): "${PROJECT_ROOT}"}
    changed = []
    for path in sorted(release.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        original = path.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            continue
        redacted = text
        for source, target in replacements.items():
            redacted = redacted.replace(source, target)
        if redacted == text:
            continue
        updated = redacted.encode("utf-8")
        path.write_bytes(updated)
        changed.append(
            {
                "path": path.relative_to(release).as_posix(),
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "published_sha256": hashlib.sha256(updated).hexdigest(),
                "transformation": "project_root_to_${PROJECT_ROOT}",
            }
        )
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, action="append", required=True)
    parser.add_argument("--output-zip", type=Path, required=True)
    parser.add_argument("--include-benchmark", action="store_true")
    parser.add_argument("--allow-license-review", action="store_true")
    parser.add_argument("--allow-metadata-review", action="store_true")
    args = parser.parse_args()
    if not (ROOT / "LICENSE").exists() and not args.allow_license_review:
        raise RuntimeError("Select a code LICENSE before creating a public deposit")
    citation = ROOT / "CITATION.cff"
    if (
        citation.exists()
        and "REPLACE_WITH_" in citation.read_text(encoding="utf-8")
        and not args.allow_metadata_review
    ):
        raise RuntimeError("Replace placeholder citation metadata before public deposit")
    experiments = [path.expanduser().resolve() for path in args.experiment_root]
    for experiment in experiments:
        aggregate_experiment(experiment, allow_partial=False)

    output = args.output_zip.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pbc_release_") as temporary:
        release = Path(temporary) / "pbc_xlns_mcf_axioms_release"
        release.mkdir()
        for item in CODE_ITEMS:
            copy_item(ROOT / item, release / item)
        copy_item(ROOT / "data" / "reference", release / "data" / "reference")
        if args.include_benchmark:
            copy_item(ROOT / "data" / "corberan_mcpp", release / "data" / "corberan_mcpp")
        for experiment in experiments:
            target = release / "results" / experiment.name
            copy_item(experiment, target)
        transformations = redact_local_paths(release)
        (release / "PUBLICATION_TRANSFORM.json").write_text(
            json.dumps(
                {
                    "scope": "text metadata only; numeric results, arrays, code, and design hashes unchanged",
                    "files": transformations,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        files = [path for path in sorted(release.rglob("*")) if path.is_file()]
        manifest = [
            {
                "path": path.relative_to(release).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in files
        ]
        (release / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(release.rglob("*")):
                if not path.is_file():
                    continue
                relative = Path(release.name) / path.relative_to(release)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(2026, 7, 22, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(json.dumps({"archive": str(output), "sha256": sha256_file(output)}, indent=2))


if __name__ == "__main__":
    main()
