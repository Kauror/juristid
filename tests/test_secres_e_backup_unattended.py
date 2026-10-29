"""Security and resilience review 2026-10-29, assessor E (workstream 2: DR).

Is `scripts/deploy/juristid-backup.sh` safe to run from cron, unattended?

Nothing on the production host schedules it today (coordinator's read-only
survey, 2026-10-29: every one of the 165 sets was taken by hand at a release).
The owner decisions that would put it on a schedule (RPO, retention) are open,
so the question these answer is what the script would do the night it is
scheduled — with no operator reading its output.

These run the real backup and verify scripts end to end with a `docker` and an
`rsync` that answer the scripts' questions without a daemon: the stub `docker`
emits a custom-format-shaped dump for `pg_dump`, a table of contents for
`pg_restore --list`, a revision for `cat /app/GIT_SHA`; the stub `rsync`
handles the two call shapes the backup uses (whole tree, and `--files-from`).
The real thing — a real pg_dump, a real deletion during the dump, a real
restore — is the `recovery` job in CI. What this harness adds is the ability to
make the dump *fail* in the two ways the script's own header describes, on a
laptop, in seconds, and to see what is left on disk afterwards.

What is pinned, and the finding each belongs to:

* a full run seals a five-file set and leaves no `.partial` (the harness works);
* `pg_dump` failing at once, or dying halfway after the archive magic was
  written, leaves a `.partial` and no set, exit 1 (the shell sees pg_dump's own
  status — no pipeline);
* the structural check failing after a full dump keeps the `.partial` *with*
  its dump, exit 1;
* **E-05 (P2)**: stale `.partial` directories from earlier failed runs never
  block the next run — correct for cron — and are never removed, so an
  unattended regime accumulates one full-size partial dump per failed night
  and nothing but the age check's NOTE line mentions them;
* the free-space refusal fires before `sets/` or any pool is created;
* **E-04 (P2)**: the script takes no lock — not the release lock the Lovable
  sync honours (`/tmp/juristid-host-lock`), not a lock of its own — so a
  scheduled run can overlap a release's `migrate`/`up`, a restore, or another
  backup. Marked xfail(strict) so the marker is removed with the fix.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPTS = ROOT / "scripts" / "deploy"
BACKUP = SCRIPTS / "juristid-backup.sh"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"
BASH = shutil.which("bash") or "bash"

DOCKER_STUB = r"""#!/usr/bin/env bash
# A docker that answers the backup and verify scripts without a daemon.
[ -n "${STUB_LOG:-}" ] && printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in
  *" pg_dump "*)
    if [ "${STUB_PG_DUMP:-ok}" = fail ]; then
      echo "pg_dump: error: connection refused" >&2; exit 1
    fi
    printf 'PGDMP'; head -c 2048 /dev/zero | tr '\0' 'x'
    if [ "${STUB_PG_DUMP:-ok}" = die-halfway ]; then
      echo "pg_dump: error: lost connection" >&2; exit 1
    fi
    exit 0 ;;
  *" pg_restore --list "*)
    if [ "${STUB_TOC:-ok}" = fail ]; then echo "pg_restore: error: not an archive" >&2; exit 1; fi
    printf ';\n; Archive created\n;\n'
    printf '1; 1259 TABLE public matters_matter juristid\n'
    printf '2; 1259 TABLE public documents_documentversion juristid\n'
    printf '3; 1259 TABLE public django_migrations juristid\n'
    exit 0 ;;
  *" pg_restore --file=/dev/null "*) exit 0 ;;
  *GIT_SHA*) printf 'stub0000000000000000000000000000000000000'; exit 0 ;;
  *server_version_num*) printf '180006\n'; exit 0 ;;
  *) exit 0 ;;
esac
"""

RSYNC_STUB = r"""#!/usr/bin/env bash
# Enough of rsync for the backup's two call shapes: whole tree, and --files-from.
files_from=""; ignore_existing=0; args=(); status=0
for a in "$@"; do
  case "$a" in
    --files-from=*) files_from="${a#--files-from=}" ;;
    --ignore-existing) ignore_existing=1 ;;
    -*) ;;
    *) args+=("$a") ;;
  esac
done
src="${args[0]%/}"; dst="${args[1]%/}"
mkdir -p "$dst"
copy() {
  if [ "$ignore_existing" = 1 ] && [ -e "$dst/$1" ]; then return 0; fi
  mkdir -p "$(dirname "$dst/$1")" && cp -p "$src/$1" "$dst/$1"
}
if [ -n "$files_from" ]; then
  while IFS= read -r -d '' entry; do
    if [ -f "$src/$entry" ]; then copy "$entry"; else status=24; fi
  done <"$files_from"
  exit "$status"
fi
( cd "$src" && find . -type f -print0 ) |
  while IFS= read -r -d '' entry; do copy "${entry#./}"; done
"""


@pytest.fixture
def harness(tmp_path: Path) -> dict[str, Path | dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("rsync", RSYNC_STUB)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    data = tmp_path / "data"
    (data / "evidence" / "aa").mkdir(parents=True)
    (data / "legacy-source").mkdir()
    (data / "evidence" / "aa" / "one.bin").write_bytes(b"one")
    (data / "evidence" / "aa" / "two.bin").write_bytes(b"two2")
    (data / "legacy-source" / "page.xml").write_bytes(b"<p/>")
    backups = tmp_path / "backups"
    backups.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(tmp_path / "docker.log"),
        # The host lock, in this run's own directory: a shard beside this one
        # must not find a lock the other took, and a lock left by a killed run
        # must not refuse every later test.
        "JURISTID_HOST_LOCK": str(tmp_path / "host-lock"),
    }
    return {"data": data, "backups": backups, "env": env}


def _backup(
    harness: dict, *extra: str, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**harness["env"], **(env_overrides or {})}
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            BASH,
            str(BACKUP),
            "--project",
            "juristid-main",
            "--compose-file",
            str(COMPOSE),
            "--data-root",
            str(harness["data"]),
            "--backup-root",
            str(harness["backups"]),
            "--minimum-free-mib",
            "1",
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )


def _sets(harness: dict) -> list[str]:
    sets_dir = Path(harness["backups"]) / "sets"
    return sorted(p.name for p in sets_dir.iterdir()) if sets_dir.exists() else []


def _finished(harness: dict) -> list[str]:
    return [name for name in _sets(harness) if not name.endswith(".partial")]


def _partials(harness: dict) -> list[str]:
    return [name for name in _sets(harness) if name.endswith(".partial")]


# -- the harness itself ------------------------------------------------------------


def test_a_full_run_seals_a_five_file_set_and_passes_its_own_level_two(harness: dict) -> None:
    result = _backup(harness)

    assert result.returncode == 0, result.stderr
    assert len(_finished(harness)) == 1 and not _partials(harness)
    set_dir = Path(harness["backups"]) / "sets" / _finished(harness)[0]
    assert sorted(p.name for p in set_dir.iterdir()) == [
        "SHA256SUMS",
        "database.dump",
        "evidence.files0",
        "legacy-source.files0",
        "manifest.json",
    ]
    assert '"manifest_version": 3' in (set_dir / "manifest.json").read_text(encoding="utf-8")
    assert "the whole archive read cleanly" in result.stdout
    assert (Path(harness["backups"]) / "evidence" / "aa" / "two.bin").read_bytes() == b"two2"
    # The docker stub was asked the two things a sealed set records.
    log = Path(harness["env"]["STUB_LOG"]).read_text(encoding="utf-8")
    assert " pg_dump " in log and " pg_restore --list " in log


# -- the dump fails ------------------------------------------------------------------


def test_a_dump_that_fails_at_once_leaves_a_partial_and_no_set(harness: dict) -> None:
    result = _backup(harness, env_overrides={"STUB_PG_DUMP": "fail"})

    assert result.returncode == 1
    assert "pg_dump: error" in result.stderr
    assert not _finished(harness)
    assert len(_partials(harness)) == 1


def test_a_dump_that_dies_halfway_after_the_archive_magic_is_still_a_failure(
    harness: dict,
) -> None:
    """The failure the script's header exists for: bytes that begin `PGDMP`
    were written and then pg_dump died. With `pg_dump | gzip > file` the shell
    would report success; here pg_dump's own exit status is what the shell
    sees, and the magic check is never reached."""
    result = _backup(harness, env_overrides={"STUB_PG_DUMP": "die-halfway"})

    assert result.returncode == 1
    assert not _finished(harness)
    partial = Path(harness["backups"]) / "sets" / _partials(harness)[0]
    assert (partial / "database.dump").read_bytes().startswith(b"PGDMP")
    assert not (partial / "manifest.json").exists(), "the run must stop before the manifest"


def test_a_structural_check_failure_keeps_the_partial_with_its_full_dump(harness: dict) -> None:
    """`pg_restore --list` refuses the archive after everything was written:
    the directory is kept for investigation, dump and manifest included, and
    nothing is renamed into place."""
    result = _backup(harness, env_overrides={"STUB_TOC": "fail"})

    assert result.returncode == 1
    assert "pg_restore could not read the archive" in result.stderr
    assert not _finished(harness)
    partial = Path(harness["backups"]) / "sets" / _partials(harness)[0]
    assert (partial / "database.dump").stat().st_size > 0
    assert (partial / "manifest.json").exists()
    assert (partial / "SHA256SUMS").exists()


# -- E-05: what an unattended regime accumulates ----------------------------------


def test_stale_partials_from_earlier_failed_runs_never_block_the_next_run(harness: dict) -> None:
    """Right for cron — a failed night must not stop every later night — and
    the other half of E-05: nothing removes them, so each failed night leaves
    a `.partial` (with a full-size dump when the failure was late) that only
    the age check's NOTE line ever mentions."""
    for mode in ("fail", "fail"):
        assert _backup(harness, env_overrides={"STUB_PG_DUMP": mode}).returncode == 1
        time.sleep(1.1)  # sets are named to the second
    assert len(_partials(harness)) == 2

    result = _backup(harness)

    assert result.returncode == 0, result.stderr
    assert len(_finished(harness)) == 1
    assert len(_partials(harness)) == 2, "the script must not delete another run's work"


# -- disk full ---------------------------------------------------------------------


def test_the_free_space_refusal_fires_before_anything_is_created(harness: dict) -> None:
    result = _backup(harness, "--minimum-free-mib", "999999999")

    assert result.returncode == 1
    assert "Refusing to start a backup that may not finish" in result.stderr
    assert list(Path(harness["backups"]).iterdir()) == [], "no sets/, no pool, nothing"


# -- E-04: no lock -------------------------------------------------------------------


def test_the_backup_honours_the_host_lock_the_other_scheduled_job_honours(
    harness: dict,
) -> None:
    """`juristid-lovable-sync.sh` takes `/tmp/juristid-host-lock` so that a
    release cannot start under it. A scheduled backup would run `pg_dump`
    beside a release's `migrate` (each waits on the other's locks), beside a
    restore's `pg_restore --single-transaction` (the dump then records the
    empty pre-restore database as that minute's backup), or beside a second
    backup. The backup now takes the same `mkdir` lock (`lib.sh`,
    `take_host_lock`): held by another, it refuses by name and writes nothing;
    free, it takes it, seals the set and releases it."""
    lock = Path(harness["env"]["JURISTID_HOST_LOCK"])
    lock.mkdir()
    (lock / "owner").write_text("owner=release pid=1 2026-10-29T12:00:00Z\n", encoding="utf-8")

    result = _backup(harness)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "another operation holds" in result.stderr
    assert "owner=release" in result.stderr
    assert list(Path(harness["backups"]).iterdir()) == [], "nothing written under a held lock"
    assert lock.is_dir(), "a lock somebody else holds is never released by the refused run"

    lock_owner = lock / "owner"
    lock_owner.unlink()
    lock.rmdir()
    result = _backup(harness)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert _finished(harness), "with the lock free the set is sealed"
    assert not lock.exists(), "the lock is released on exit"
