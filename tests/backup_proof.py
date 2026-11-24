"""A synthetic backup set with the host's verification proof (audit 2026-11-11 DR-05).

The shape `lovable_sync.backup_proof_problem` reads: a set directory named
``YYYYMMDDTHHMMSSZ`` holding ``database.dump``, ``SHA256SUMS`` and the
``VERIFIED-L2`` marker that the host's ``juristid-verify-backup.sh --record``
writes. Synthetic bytes only; nothing is backed up or restored.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pytest

from app.legacy_import import lovable_sync

STAMP = "%Y%m%dT%H%M%SZ"


def set_name(taken: dt.datetime) -> str:
    return "sets/" + taken.astimezone(dt.UTC).strftime(STAMP)


def write_backup_set(
    root: Path,
    *,
    taken: dt.datetime,
    verified: dt.datetime | None = None,
    name: str | None = None,
    level: int = lovable_sync.BACKUP_PROOF_LEVEL,
    dump: bytes = b"PGDMP naidis",
    recorded: str | None = None,
    marker: bool = True,
) -> Path:
    """The set taken at ``taken`` and verified at ``verified`` (default: two
    minutes later), in ``root``/<stamp>. ``name`` writes a proof naming another
    set; ``recorded`` a SHA256SUMS line for another dump."""
    stamp = taken.astimezone(dt.UTC).strftime(STAMP)
    directory = root / stamp
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "database.dump").write_bytes(dump)
    sha = hashlib.sha256(dump).hexdigest()
    (directory / "SHA256SUMS").write_text(
        f"{recorded or sha}  database.dump\n{'0' * 64}  manifest.json\n", encoding="utf-8"
    )
    if marker:
        at = (verified or taken + dt.timedelta(minutes=2)).astimezone(dt.UTC)
        (directory / f"VERIFIED-L{lovable_sync.BACKUP_PROOF_LEVEL}").write_text(
            f"set={name or stamp}\nlevel={level}\n"
            f"verified_at={at.strftime('%Y-%m-%dT%H:%M:%SZ')}\ndatabase_sha256={sha}\n",
            encoding="utf-8",
        )
    return directory


def mount_backup_set(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Mount ``directory`` the way the host runner does (``-v SET:/backup-set:ro``)."""
    monkeypatch.setenv(lovable_sync.HOST_BACKUP_DIR_ENV, str(directory))


def mount_fresh_verified_set(monkeypatch: pytest.MonkeyPatch, root: Path) -> str:
    """A set taken five minutes ago, verified to level 2, mounted, and named in
    ``JURISTID_ONENOTE_BACKUP_SET`` as the host's OneNote runner names it.
    Returns the set's name."""
    taken = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(minutes=5)
    mount_backup_set(monkeypatch, write_backup_set(root, taken=taken))
    name = set_name(taken)
    monkeypatch.setenv("JURISTID_ONENOTE_BACKUP_SET", name)
    return name
