#!/usr/bin/env python3
"""Build a deterministic, checksum-manifested Engineering Optimization deposit."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release" / "PBC_XLNS_MCF_Engineering_Optimization_evidence.zip"
TOP_LEVEL = [
    "src", "scripts", "configs", "calibration", "docs", "tests",
    "README.md", "EO_REVISION_ONE_CLICK.py", "publication_metadata.json",
    "pyproject.toml", "requirements.txt", "requirements-lock.txt", "environment.yml",
    "Dockerfile", "CITATION.cff", "LICENSE",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def include(path: Path) -> bool:
    return path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"


def main() -> None:
    metadata = json.loads((ROOT / "publication_metadata.json").read_text(encoding="utf-8"))
    for key in ("repository_url", "archive_doi", "code_license"):
        if str(metadata.get(key, "")).startswith("REPLACE_WITH"):
            raise RuntimeError(f"public release blocked: set {key} in publication_metadata.json")
    if not (ROOT / "LICENSE").exists():
        raise RuntimeError("public release blocked: add the author-selected LICENSE file")
    completed = ROOT / "docs" / "RESPONSE_TO_AE_COMPLETED.md"
    statistics = ROOT / "results" / "eo_confirmatory_120x30_strict" / "reviewer_analysis" / "response_values.json"
    selected = ROOT / "calibration" / "selected_parameters.json"
    for path in (completed, statistics, selected):
        if not path.exists():
            raise RuntimeError(f"public release blocked: missing {path}")

    sources: list[tuple[Path, Path]] = []
    for name in TOP_LEVEL:
        item = ROOT / name
        if item.is_file():
            sources.append((item, Path(name)))
        elif item.is_dir():
            for path in sorted(item.rglob("*")):
                if include(path):
                    sources.append((path, Path(name) / path.relative_to(item)))
    # Complete calibration and confirmatory evidence; the third-party raw
    # benchmark is intentionally excluded and recreated by the pinned downloader.
    for result_root in sorted((ROOT / "results").glob("eo_cal_*")) + [ROOT / "results" / "eo_confirmatory_120x30_strict"]:
        if not result_root.is_dir():
            raise RuntimeError(f"missing result directory: {result_root}")
        for path in sorted(result_root.rglob("*")):
            if include(path):
                sources.append((path, Path("results") / result_root.name / path.relative_to(result_root)))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    manifest = [{"path": relative.as_posix(), "bytes": source.stat().st_size, "sha256": sha256(source)}
                for source, relative in sources]
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, relative in sources:
            info = zipfile.ZipInfo((Path("PBC_XLNS_MCF_EO_evidence") / relative).as_posix(), (2026, 8, 10, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        manifest_info = zipfile.ZipInfo("PBC_XLNS_MCF_EO_evidence/RELEASE_MANIFEST.json", (2026, 8, 10, 0, 0, 0))
        manifest_info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(manifest_info, json.dumps(manifest, indent=2).encode("utf-8"))
    print(json.dumps({"archive": str(OUTPUT), "sha256": sha256(OUTPUT), "files": len(sources)}, indent=2))


if __name__ == "__main__":
    main()
