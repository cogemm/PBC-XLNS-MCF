#!/usr/bin/env python3
"""AE Comment 2: verify benchmark URL, local files, and SHA-256 evidence.

The default run verifies all 120 local instance hashes and the bundled source
metadata.  Set CHECK_REMOTE_REFERENCE/ARCHIVE below to test the live upstream
URLs as well.  No benchmark file is modified.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any


CHECK_REMOTE_REFERENCE = True
CHECK_REMOTE_ARCHIVE = False  # True downloads the full MCPP.zip for hash verification.
REMOTE_TIMEOUT_SECONDS = 180


def locate_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent, *Path(__file__).resolve().parents]
    for root in candidates:
        if (root / "src" / "corberan_data.py").is_file() and (root / "data" / "reference").is_dir():
            return root.resolve()
    raise SystemExit("Project root not found; use the PBC_XLNS_MCF_EO_Revision directory as working directory.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download_hash(url: str) -> tuple[str, int]:
    request = urllib.request.Request(url, headers={"User-Agent": "EO-revision-benchmark-audit/1.0"})
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=REMOTE_TIMEOUT_SECONDS) as response:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            total += len(block)
            digest.update(block)
    return digest.hexdigest(), total


def load_constants(module_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("eo_corberan_data_audit", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    root = locate_root()
    module = load_constants(root / "src" / "corberan_data.py")
    dataset = root / "data" / "corberan_mcpp"
    reference = root / "data" / "reference" / "official_instance_sha256.csv"
    out = root / "results" / "revision_evidence"
    out.mkdir(parents=True, exist_ok=True)

    with reference.open("r", encoding="utf-8-sig", newline="") as handle:
        expected_rows = list(csv.DictReader(handle))
    expected = {row["instance"]: row for row in expected_rows}
    local_rows = []
    failures = []
    for name, row in sorted(expected.items()):
        path = dataset / name
        observed_size = path.stat().st_size if path.exists() else -1
        observed_hash = sha256_file(path) if path.exists() else "MISSING"
        passed = observed_size == int(row["bytes"]) and observed_hash == row["sha256"]
        if not passed:
            failures.append(name)
        local_rows.append({
            "instance": name,
            "exists": path.exists(),
            "expected_bytes": int(row["bytes"]),
            "observed_bytes": observed_size,
            "expected_sha256": row["sha256"],
            "observed_sha256": observed_hash,
            "passed": passed,
        })

    remote: list[dict[str, Any]] = []
    if CHECK_REMOTE_REFERENCE:
        for name, (url, expected_hash) in module.OFFICIAL_REFERENCE.items():
            try:
                observed_hash, size = download_hash(url)
                remote.append({
                    "name": name, "url": url, "bytes": size,
                    "expected_sha256": expected_hash, "observed_sha256": observed_hash,
                    "passed": observed_hash == expected_hash,
                })
            except Exception as exc:
                remote.append({"name": name, "url": url, "passed": False, "error": repr(exc)})
    if CHECK_REMOTE_ARCHIVE:
        for name, url, expected_hash in (
            ("official_MCPP.zip", module.OFFICIAL_ARCHIVE_URL, module.OFFICIAL_ARCHIVE_SHA256),
            ("pinned_OARLib.zip", module.OARLIB_ARCHIVE_URL, module.OARLIB_ARCHIVE_SHA256),
        ):
            try:
                observed_hash, size = download_hash(url)
                remote.append({
                    "name": name, "url": url, "bytes": size,
                    "expected_sha256": expected_hash, "observed_sha256": observed_hash,
                    "passed": observed_hash == expected_hash,
                })
            except Exception as exc:
                remote.append({"name": name, "url": url, "passed": False, "error": repr(exc)})

    manifest_path = dataset / "benchmark_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    report = {
        "official_archive_url": module.OFFICIAL_ARCHIVE_URL,
        "official_archive_sha256": module.OFFICIAL_ARCHIVE_SHA256,
        "pinned_fallback_url": module.OARLIB_ARCHIVE_URL,
        "pinned_fallback_sha256": module.OARLIB_ARCHIVE_SHA256,
        "pinned_fallback_commit": module.OARLIB_COMMIT,
        "expected_instances": len(expected),
        "local_instances_passed": len(expected) - len(failures),
        "local_failures": failures,
        "installed_manifest": manifest,
        "remote_checks": remote,
        "submission_ready_for_comment_2": (
            len(expected) == 120
            and not failures
            and all(item.get("passed", False) for item in remote)
        ),
    }
    json_path = out / "AE02_benchmark_url_and_hash_audit.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# AE Comment 2 benchmark-source audit",
        "",
        f"Local files: **{len(expected) - len(failures)}/{len(expected)} passed**.",
        "",
        f"- Official archive: {module.OFFICIAL_ARCHIVE_URL}",
        f"- Official archive SHA-256: `{module.OFFICIAL_ARCHIVE_SHA256}`",
        f"- Pinned fallback: {module.OARLIB_ARCHIVE_URL}",
        f"- Pinned commit: `{module.OARLIB_COMMIT}`",
        f"- Pinned fallback SHA-256: `{module.OARLIB_ARCHIVE_SHA256}`",
        "",
    ]
    if remote:
        lines += ["## Live URL checks", ""]
        for item in remote:
            lines.append(f"- {'PASS' if item.get('passed') else 'FAIL'} — {item['name']}: {item['url']}")
        lines.append("")
    if failures:
        lines += ["## Local failures", "", ", ".join(failures), ""]
    md_path = out / "AE02_benchmark_url_and_hash_audit.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(md_path)
    print(json_path)
    if failures or any(not item.get("passed", False) for item in remote):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
