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


def write_manifest(
    snapshot_dir: Path, label: str, extra: dict[str, Any] | None = None
) -> Path:
    """Hash every artefact in ``snapshot_dir`` and write ``manifest.json``."""
    snapshot_dir = Path(snapshot_dir)
    files = []
    for p in sorted(snapshot_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.json" and ".cache" not in p.parts:
            files.append(
                {
                    "path": str(p.relative_to(snapshot_dir)),
                    "bytes": p.stat().st_size,
                    "sha256": sha256_file(p),
                }
            )

    manifest = {
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
    out = snapshot_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


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
