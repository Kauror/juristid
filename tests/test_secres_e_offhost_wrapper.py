"""Security and resilience review 2026-10-29, assessor E (workstream 2: DR).

`deploy/unraid-main/offhost-backup.sh` is a draft that has never pushed a byte
(owner decision 13.10.2026: off-host backup inactive this release cycle). What
can be proved about it without a destination is its refusals and its shape,
and one gap in the refusal that matters on this particular host:

* **E-10 (P3)**: the "not off-host" guard refuses `/mnt/user/*`, `/mnt/disk[0-9]*`,
  `/mnt/cache*`, `/boot/*` and `local:` — and accepts `/mnt/disks/*`, which is
  exactly where Unraid mounts unassigned devices, including the single USB
  disk (`/mnt/disks/KOCORPBKUP2`) that the host's own nightly snapshot writes
  to (coordinator's survey, 2026-10-29). A repository there would pass the
  guard and be a same-host, same-room copy. `/mnt/remotes/*`, `/tmp/*` and a
  relative path pass too. Marked xfail(strict); needs a POSIX `chmod 600`,
  so it is skipped where the mode check cannot be satisfied.
* the identity record is written (step 3) *before* the push (step 4), and a
  rerun refuses while it exists — so a transient push failure leaves a record
  that has to be removed by hand before the same set can be retried. Pinned
  as behaviour, not as a defect: for a nightly cadence the next set is new.
* the uploader mounts the backup root read-only, excludes `*.partial`, and
  joins no Docker network — which is the one line a CI harness against a
  local `rest-server --append-only` would need (see WS2 findings, repo-side
  improvement R-3).

The existing tests/test_deployment_runbooks.py already proves the wrapper
never calls `forget`/`prune`/`rm` and refuses without a destination.
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
WRAPPER = ROOT / "deploy" / "unraid-main" / "offhost-backup.sh"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"
BASH = shutil.which("bash") or "bash"
PINNED_IMAGE = "restic/restic@sha256:" + "0" * 64


def _run(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(WRAPPER), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **(env or {})},
        check=False,
    )


def _set(tmp_path: Path, name: str = "20261029T000000Z") -> Path:
    set_dir = tmp_path / "root" / "sets" / name
    set_dir.mkdir(parents=True)
    (set_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (set_dir / "SHA256SUMS").write_text("", encoding="utf-8")
    return set_dir


def _mode_600_works(tmp_path: Path) -> bool:
    probe = tmp_path / "probe"
    probe.write_text("x", encoding="utf-8")
    probe.chmod(0o600)
    return stat.S_IMODE(probe.stat().st_mode) == 0o600


def _secrets(tmp_path: Path, repository: str) -> dict[str, str]:
    env_file = tmp_path / "restic.env"
    env_file.write_text(f"RESTIC_REPOSITORY={repository}\n", encoding="utf-8")
    password = tmp_path / "restic-password"
    password.write_text("not-a-real-key\n", encoding="utf-8")
    for path in (env_file, password):
        path.chmod(0o600)
    return {
        "JURISTID_OFFHOST_ENV_FILE": str(env_file),
        "JURISTID_OFFHOST_PASSWORD_FILE": str(password),
        "JURISTID_OFFHOST_RESTIC_IMAGE": PINNED_IMAGE,
    }


# -- refusals that need no files ---------------------------------------------------------


def test_each_missing_destination_value_is_named_and_nothing_runs(tmp_path: Path) -> None:
    set_dir = _set(tmp_path)
    base = ("--set", str(set_dir), "--project", "juristid-main", "--compose-file", str(COMPOSE))

    no_env = _run(*base, env={"JURISTID_OFFHOST_ENV_FILE": ""})
    assert no_env.returncode == 2 and "JURISTID_OFFHOST_ENV_FILE is not set" in no_env.stderr

    no_password = _run(*base, env={"JURISTID_OFFHOST_ENV_FILE": str(tmp_path / "e")})
    assert no_password.returncode == 2
    assert "JURISTID_OFFHOST_PASSWORD_FILE is not set" in no_password.stderr

    tagged = _run(
        *base,
        env={
            "JURISTID_OFFHOST_ENV_FILE": str(tmp_path / "e"),
            "JURISTID_OFFHOST_PASSWORD_FILE": str(tmp_path / "p"),
            "JURISTID_OFFHOST_RESTIC_IMAGE": "restic/restic:latest",
        },
    )
    assert tagged.returncode == 2 and "pinned by digest" in tagged.stderr
    assert "restic backup" not in tagged.stdout + no_env.stdout + no_password.stdout


def test_the_secret_files_are_checked_before_the_set_is_even_looked_at(tmp_path: Path) -> None:
    """Order matters for an operator: the destination's own problems come first."""
    result = _run(
        "--set",
        str(tmp_path / "does-not-exist"),
        env={
            "JURISTID_OFFHOST_ENV_FILE": str(tmp_path / "missing.env"),
            "JURISTID_OFFHOST_PASSWORD_FILE": str(tmp_path / "missing.pw"),
            "JURISTID_OFFHOST_RESTIC_IMAGE": PINNED_IMAGE,
        },
    )
    assert result.returncode == 2
    assert "missing.env does not exist" in result.stderr
    assert "does-not-exist" not in result.stderr


# -- the shape of the upload -----------------------------------------------------------------


def test_the_uploader_cannot_change_a_local_set_and_skips_unfinished_runs() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert '-v "$BACKUP_ROOT:/data:ro"' in text, "the backup root must be read-only to restic"
    assert "--exclude '*.partial'" in text
    assert "--network" not in text, (
        "no network option today; a CI harness against a local rest-server needs one (R-3)"
    )


def test_the_identity_record_is_written_before_the_push_and_blocks_a_retry() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    record = text.index('[ ! -e "$RECORD" ] || die')
    push = text.index("restic backup --host")
    assert record < push
    assert "nothing is overwritten" in text[record : record + 300]


# -- E-10: the "not off-host" guard and this host's USB disk ----------------------------------


@pytest.mark.parametrize(
    "repository",
    [
        "/mnt/disks/KOCORPBKUP2/restic",
        "/mnt/remotes/KOCORP_backups/restic",
        "/tmp/restic",  # noqa: S108 - a repository path handed to the guard, never opened
        "restic-repo",
    ],
)
def test_a_repository_on_the_hosts_own_usb_disk_is_refused(tmp_path: Path, repository: str) -> None:
    if not _mode_600_works(tmp_path):
        pytest.skip("this filesystem cannot hold a mode-600 file, which the wrapper requires first")
    set_dir = _set(tmp_path)

    result = _run(
        "--set",
        str(set_dir),
        "--project",
        "juristid-main",
        "--compose-file",
        str(COMPOSE),
        env=_secrets(tmp_path, repository),
    )

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "this host's own storage" in result.stderr


def test_the_guard_does_refuse_the_array_and_the_flash_drive(tmp_path: Path) -> None:
    """The half of the guard that works, so E-10 is about coverage and not about
    the mechanism."""
    if not _mode_600_works(tmp_path):
        pytest.skip("this filesystem cannot hold a mode-600 file, which the wrapper requires first")
    set_dir = _set(tmp_path)
    for repository in ("/mnt/user/backups/restic", "/mnt/disk4/restic", "/boot/restic", "local:/x"):
        result = _run(
            "--set",
            str(set_dir),
            "--project",
            "juristid-main",
            "--compose-file",
            str(COMPOSE),
            env=_secrets(tmp_path, repository),
        )
        assert result.returncode == 2, repository
        assert "this host's own storage" in result.stderr, repository
