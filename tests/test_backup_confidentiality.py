"""A backup set is private when it is written, and still restores (SEC-011).

`juristid-backup.sh` used to create every artifact with the caller's umask —
022 under cron — so the dump of the whole register was 0644 in a 0755 set
directory and every pool object kept the evidence tree's 0644. Now:

* the script sets `umask 077` before its first write: the set directory is
  0700, the dump, manifest, checksums and membership inventories 0600;
* a NEW pool object is copied with `--no-perms` (the source's mode under that
  umask: 0600, directories 0700); an object already in the pool keeps its mode,
  because tightening the existing corpus is a deliberate operator step, not a
  side effect of a nightly run;
* the destination is inspected before anything is written: access beyond the
  owner is reported on every run, and `--require-private-destination` refuses.

The full run below is the production script against a fake `docker` (the
database is not the subject here: the CI recovery rehearsal restores a real
`pg_dump` end to end) and a real `rsync`, on a real POSIX filesystem. On a
system without POSIX modes or rsync it is skipped with the reason; the Linux CI
runner has both, so the proof runs there.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPTS = ROOT / "scripts" / "deploy"
LIB = SCRIPTS / "lib.sh"
BACKUP = SCRIPTS / "juristid-backup.sh"
RESTORE = SCRIPTS / "juristid-restore.sh"
VERIFY = SCRIPTS / "juristid-verify-backup.sh"

BASH = shutil.which("bash") or "bash"

posix_modes = pytest.mark.skipif(
    os.name != "posix", reason="needs POSIX file modes; the Linux CI runner has them"
)
needs_rsync = pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("flock") is None,
    reason="needs rsync and flock, as the host and the Linux CI runner have",
)

#: Stands in for `docker compose … exec/cp` against the db and web services. It
#: answers exactly the calls the backup, the verifier and the restore make, and
#: fails loudly on anything else so a new call cannot slip past unnoticed.
FAKE_DOCKER = r"""#!/usr/bin/env bash
joined=" $* "
case "$joined" in
  *" version "*) echo "Docker Compose version v2-fake" ;;
  *" exec -T db pg_dump "*) printf 'PGDMP\001\016\000synthetic register dump' ;;
  *" exec -T db pg_restore --list "*)
    printf '; Archive created by a fake\n'
    printf '1; 1259 1 TABLE public matters_matter juristid\n'
    printf '2; 1259 2 TABLE public documents_documentversion juristid\n'
    printf '3; 1259 3 TABLE public django_migrations juristid\n' ;;
  *" exec -T db pg_restore --file=/dev/null "*) : ;;
  *" exec -T db pg_restore --schema-only "*) : ;;
  *" exec -T db pg_restore "*) : ;;
  *" exec -T db psql "*"server_version_num"*) echo 180007 ;;
  *" exec -T db psql "*"information_schema.tables"*) echo 0 ;;
  *" exec -T db psql "*"pg_roles"*) echo juristid ;;
  *" exec -T db rm "*) : ;;
  *" exec -T web "*) printf 'fakerevision' ;;
  *" cp "*) : ;;
  *) echo "fake docker: unexpected call:$joined" >&2; exit 97 ;;
esac
"""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _lib(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, "-c", f'set -euo pipefail; . "{LIB.as_posix()}"; {script}'],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


class World:
    """A data root, a backup root and a fake docker, all under one tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.data = tmp_path / "data"
        self.backups = tmp_path / "backups"
        self.compose = tmp_path / "compose.yml"
        self.bin = tmp_path / "bin"
        for directory in (
            self.data / "evidence" / "ab",
            self.data / "legacy-source",
            self.backups,
            self.bin,
        ):
            directory.mkdir(parents=True)
        self.compose.write_text("services: {}\n", encoding="utf-8")
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        # The evidence tree as the application writes it: world-readable files
        # in world-searchable directories, which is the exposure being closed.
        for path, body in (
            (self.data / "evidence" / "ab" / "first.pdf", b"synthetic evidence one"),
            (self.data / "legacy-source" / "page.xml", b"<page>synthetic</page>"),
        ):
            path.write_bytes(body)
            path.chmod(0o644)
        for directory in (self.data, self.data / "evidence", self.data / "evidence" / "ab"):
            directory.chmod(0o755)
        self.backups.chmod(0o700)

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["JURISTID_HOST_LOCK"] = str(self.tmp / "host-lock")
        env["JURISTID_CYCLE_LOCK"] = str(self.tmp / "cycle.lock")
        env.pop("JURISTID_BACKUP_REQUIRE_PRIVATE", None)
        env.update(extra)
        return env

    def run(
        self, script: Path, *args: str, umask: int = 0o022, **extra_env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run a production script as cron would: a permissive umask, unless told otherwise."""
        command = f'umask {umask:03o}; exec "{BASH}" "$@"'
        return subprocess.run(  # noqa: S603 - a fixed interpreter and repository scripts
            [BASH, "-c", command, "run", str(script), *args],
            cwd=self.tmp,
            env=self.env(**extra_env),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def backup(self, *extra: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return self.run(
            BACKUP,
            "--project",
            "juristid-recovery-rehearsal",
            "--compose-file",
            str(self.compose),
            "--data-root",
            str(self.data),
            "--backup-root",
            str(self.backups),
            "--minimum-free-mib",
            "1",
            *extra,
            **extra_env,
        )

    def sets(self) -> list[Path]:
        return sorted((self.backups / "sets").glob("*"))


# -- reading a mode ------------------------------------------------------------------


@posix_modes
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (0o700, ""),
        (0o750, "group-read"),
        (0o770, "group-read group-write"),
        (0o755, "group-read other-read other-search"),
        (0o711, "other-search"),
        (0o777, "group-read group-write other-read other-write other-search"),
        (0o1777, "group-read group-write other-read other-write other-search"),
    ],
)
def test_who_besides_the_owner_can_reach_a_directory(tmp_path: Path, mode: int, expected: str):
    target = tmp_path / "target"
    target.mkdir()
    target.chmod(mode)

    result = _lib("access_beyond_owner target", tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@posix_modes
def test_a_file_is_never_searchable(tmp_path: Path):
    target = tmp_path / "dump"
    target.write_bytes(b"x")
    target.chmod(0o645)

    result = _lib("access_beyond_owner dump", tmp_path)

    assert result.stdout.strip() == "group-read other-read"


def test_only_the_other_bits_and_an_acl_reach_beyond_the_group(tmp_path: Path):
    for words, reaches in (
        ("", False),
        ("group-read", False),
        ("group-read group-write", False),
        ("other-search", True),
        ("group-read other-read", True),
        ("acl", True),
    ):
        result = _lib(f'reaches_others "{words}"', tmp_path)
        assert (result.returncode == 0) is reaches, words


# -- the whole backup, private by construction ------------------------------------------


@posix_modes
@needs_rsync
def test_a_set_written_under_a_permissive_umask_is_private(tmp_path: Path):
    world = World(tmp_path)

    result = world.backup()

    assert result.returncode == 0, result.stdout + result.stderr
    [set_dir] = world.sets()
    assert not set_dir.name.endswith(".partial")
    assert _mode(set_dir) == 0o700
    for name in (
        "database.dump",
        "manifest.json",
        "SHA256SUMS",
        "evidence.files0",
        "legacy-source.files0",
    ):
        assert _mode(set_dir / name) == 0o600, name
    # New pool objects: private, their bytes exactly the source's.
    pooled = world.backups / "evidence" / "ab" / "first.pdf"
    assert pooled.read_bytes() == b"synthetic evidence one"
    assert _mode(pooled) == 0o600
    assert _mode(world.backups / "evidence" / "ab") == 0o700
    assert _mode(world.backups / "legacy-source" / "page.xml") == 0o600
    # The live tree is not this script's to change.
    assert _mode(world.data / "evidence" / "ab" / "first.pdf") == 0o644
    assert "CONFIDENTIALITY WARNING" not in result.stdout


@posix_modes
@needs_rsync
def test_an_object_already_in_the_pool_keeps_its_mode(tmp_path: Path):
    """Tightening the existing corpus is an operator's deliberate step, not a side effect."""
    world = World(tmp_path)
    assert world.backup().returncode == 0
    existing = world.backups / "evidence" / "ab" / "first.pdf"
    existing.chmod(0o644)  # as every pool object written before 2026-11-26 is
    (world.data / "evidence" / "ab" / "second.pdf").write_bytes(b"synthetic evidence two")

    again = world.backup()

    assert again.returncode == 0, again.stdout + again.stderr
    assert _mode(existing) == 0o644
    assert _mode(world.backups / "evidence" / "ab" / "second.pdf") == 0o600


@posix_modes
@needs_rsync
def test_an_open_destination_is_reported_on_every_run(tmp_path: Path):
    world = World(tmp_path)
    world.backups.chmod(0o755)

    result = world.backup()

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"CONFIDENTIALITY WARNING: {world.backups} allows:" in result.stdout
    # The set itself is still private, whatever its parent allows.
    [set_dir] = world.sets()
    assert _mode(set_dir) == 0o700


@posix_modes
@needs_rsync
def test_a_group_only_destination_is_a_note_not_a_warning(tmp_path: Path):
    world = World(tmp_path)
    world.backups.chmod(0o750)

    result = world.backup("--require-private-destination")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "confidentiality note:" in result.stdout
    assert "CONFIDENTIALITY WARNING" not in result.stdout


@posix_modes
@needs_rsync
@pytest.mark.parametrize("how", ["flag", "environment"])
def test_a_required_private_destination_refuses_an_open_one(tmp_path: Path, how: str):
    world = World(tmp_path)
    world.backups.chmod(0o755)
    if how == "flag":
        result = world.backup("--require-private-destination")
    else:
        result = world.backup(JURISTID_BACKUP_REQUIRE_PRIVATE="1")

    assert result.returncode != 0
    assert "--require-private-destination is set. Nothing has been written." in result.stderr
    assert world.sets() == []
    assert not any((world.backups / "evidence").rglob("*.pdf"))


# -- what the policy must not cost ---------------------------------------------------------


@posix_modes
@needs_rsync
def test_a_private_set_still_verifies_and_restores(tmp_path: Path):
    """Integrity, membership, level-2 verification and the restore of the trees.

    The restore runs the production script as the operator does: the bytes
    come back exactly, and the objects keep the pool's private mode (owner
    10001 on the host, which is the account the application reads them as).
    """
    world = World(tmp_path)
    assert world.backup().returncode == 0
    [set_dir] = world.sets()

    verified = world.run(
        VERIFY,
        "--project",
        "juristid-recovery-rehearsal",
        "--compose-file",
        str(world.compose),
        "--set",
        str(set_dir),
        "--backup-root",
        str(world.backups),
        "--level",
        "2",
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr

    target = tmp_path / "restored"
    target.mkdir()  # an empty data root, as the runbook prepares one
    restored = world.run(
        RESTORE,
        "--project",
        "juristid-recovery-rehearsal",
        "--compose-file",
        str(world.compose),
        "--set",
        str(set_dir),
        "--backup-root",
        str(world.backups),
        "--data-root",
        str(target),
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    evidence = target / "evidence" / "ab" / "first.pdf"
    assert evidence.read_bytes() == b"synthetic evidence one"
    assert (target / "legacy-source" / "page.xml").read_bytes() == b"<page>synthetic</page>"
    assert _mode(evidence) == 0o600
