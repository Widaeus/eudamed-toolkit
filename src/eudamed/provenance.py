"""Provenance: content hashing, snapshot manifests, legal-notice archiving.

Every extraction produces a manifest recording what was fetched, when, with
which filters, and the SHA-256 of each artefact. The manifest is what a
reviewer or a replication attempt is pointed at.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEGAL_NOTICE_URL = "https://ec.europa.eu/tools/eudamed/#/screen/info/legal-notice"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_commit() -> str | None:
    """Return the current commit hash, or ``None`` outside a git repository.

    Extraction code does not always run from a checkout — a packaged install
    or a plain script directory has no ``.git`` to ask. That is a normal
    condition, not an error, so it is not raised as one.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _is_manifest(path: Path) -> bool:
    return path.name == "manifest.json" or path.name.endswith(".manifest.json")


def _file_entry(path: Path, root: Path) -> dict[str, Any]:
    """One artefact's record: its path relative to the manifest, size and hash."""
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        relative = str(path)
    return {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _manifest_body(
    label: str, files: list[dict[str, Any]], extra: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "snapshot_label": label,
        "created_utc": utc_now(),
        "source": "EUDAMED, the EU database on medical devices (https://ec.europa.eu/tools/eudamed)",
        "legal_basis": (
            "Public data published under MDR Art. 33 transparency provisions; "
            "reused for non-commercial academic research under the Commission "
            "reuse policy (Decision 2011/833/EU)."
        ),
        "legal_notice_url": LEGAL_NOTICE_URL,
        "code_commit": git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "n_files": len(files),
        "files": files,
        **(extra or {}),
    }


def _write(path: Path, manifest: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_manifest(
    snapshot_dir: Path, label: str, extra: dict[str, Any] | None = None
) -> Path:
    """Hash every artefact in ``snapshot_dir`` and write ``manifest.json``.

    For a directory that *is* the snapshot -- a crawl's output tree, where
    every file in it belongs to the same extraction. Do not point it at a
    working directory that merely happens to contain an output file: it
    recurses, hashes everything it finds, and records the paths, which is slow
    on a large tree and discloses filenames that have nothing to do with the
    extract. Use `write_file_manifest` to record a known set of artefacts.
    """
    snapshot_dir = Path(snapshot_dir)
    files = [
        _file_entry(p, snapshot_dir)
        for p in sorted(snapshot_dir.rglob("*"))
        if p.is_file() and not _is_manifest(p) and ".cache" not in p.parts
    ]
    return _write(
        snapshot_dir / "manifest.json", _manifest_body(label, files, extra)
    )


def write_file_manifest(
    paths: Sequence[Path],
    manifest_path: Path,
    label: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Hash exactly ``paths`` and write the manifest to ``manifest_path``.

    The manifest names only the artefacts it was given, so two extracts
    written into the same directory keep two separate, accurate provenance
    records, and neither one hashes or names a file that is not part of it.
    """
    root = Path(manifest_path).parent
    files = [_file_entry(Path(p), root) for p in paths if Path(p).is_file()]
    return _write(Path(manifest_path), _manifest_body(label, files, extra))


# Actor fields that identify natural persons. Stripped before any deposit.
PERSONAL_DATA_FIELDS = ("electronicMail", "telephone", "contactName", "email")


def strip_personal_data(obj: Any) -> Any:
    """Recursively drop actor contact fields (GDPR minimisation before deposit)."""
    if isinstance(obj, dict):
        return {
            k: strip_personal_data(v)
            for k, v in obj.items()
            if k not in PERSONAL_DATA_FIELDS
        }
    if isinstance(obj, list):
        return [strip_personal_data(v) for v in obj]
    return obj
