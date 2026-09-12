"""What ordinary backup verification proves, and whether one was taken lately.

Three gaps, none of them in the backup itself. The first two came from the pilot
backup/DR audit; the third from the operational reset of 12.09.2026.

**The manifest recorded the mirrors and nothing read it back.** Every set names
how many files each mirror held and how many bytes they came to. A set could
therefore pass every check it had while the evidence it depends on had been
emptied — and the way that gets discovered is by needing it.

**Nothing could say when the last backup was.** 34 proper sets on the host, no
schedule behind any of them, every one taken by hand before a deployment, worst
observed gap about 41 hours. A backup regime nobody measures is
indistinguishable, from outside, from one that stopped last week.

**No set said which objects were its own.** The mirrors are a shared,
append-only byte pool holding everything that has ever existed, so a restore
that copied them reconstructed the *pool* rather than the set. That is the same
thing only while nothing has ever left the active tree, and on 12.09.2026
something did.

These run the real scripts against real directories. The successful *backup*
path still belongs to the `recovery` job in CI, which has Docker; everything
here needs nothing but a filesystem, which is the point — a check that cannot
run on a laptop is a check that runs once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPTS = ROOT / "scripts" / "deploy"
VERIFY = SCRIPTS / "juristid-verify-backup.sh"
BACKUP = SCRIPTS / "juristid-backup.sh"
AGE = SCRIPTS / "juristid-check-backup-age.sh"

#: Not skipped when absent, for the same reason `tests/test_deployment_scripts.py`
#: does not skip: bash is on the Linux runner and in the Git for Windows
#: toolchain, so a missing one is a broken environment rather than a reason to
#: pass quietly.
BASH = shutil.which("bash") or "bash"

RESTORE = SCRIPTS / "juristid-restore.sh"
COMPOSE = ROOT / "deploy" / "recovery-rehearsal" / "compose.yml"
REHEARSAL_PROJECT = "juristid-recovery-rehearsal"

#: The manifest version from which a set names its own objects rather than
#: leaning on whatever the shared pool happens to hold.
MEMBERSHIP_VERSION = 3


def run(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(script), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# The mirror check
# ---------------------------------------------------------------------------


def _tree_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _tree_files(directory: Path) -> int:
    return len([path for path in directory.rglob("*") if path.is_file()])


def _backup_root(tmp_path: Path) -> Path:
    """A backup root shaped exactly like the one `juristid-backup.sh` writes."""
    root = tmp_path / "backups"
    (root / "sets").mkdir(parents=True)
    evidence = root / "evidence" / "aa"
    evidence.mkdir(parents=True)
    (evidence / "one.bin").write_bytes(b"hello-evidence")
    (evidence / "two.bin").write_bytes(b"more")
    legacy = root / "legacy-source"
    legacy.mkdir(parents=True)
    (legacy / "page.xml").write_bytes(b"<page/>")
    return root


def _manifest_text(root: Path, *, version: int, evidence_files: int, evidence_bytes: int) -> str:
    legacy = root / "legacy-source"
    return (
        "{\n"
        f'  "manifest_version": {version},\n'
        '  "database": {\n'
        '    "file": "database.dump",\n'
        '    "size_bytes": 15,\n'
        '    "sha256": "not-read-by-these-tests"\n'
        "  },\n"
        '  "evidence_mirror": {\n'
        '    "path_relative_to_backup_root": "evidence",\n'
        f'    "file_count": {evidence_files},\n'
        f'    "total_bytes": {evidence_bytes}\n'
        "  },\n"
        '  "legacy_source_mirror": {\n'
        '    "path_relative_to_backup_root": "legacy-source",\n'
        f'    "file_count": {_tree_files(legacy)},\n'
        f'    "total_bytes": {_tree_bytes(legacy)}\n'
        "  }\n"
        "}\n"
    )


def _seal(
    root: Path,
    *,
    version: int = 2,
    evidence_files: int | None = None,
    evidence_bytes: int | None = None,
    stamp: str = "20260901T000000Z",
) -> Path:
    set_dir = root / "sets" / stamp
    set_dir.mkdir(parents=True, exist_ok=True)
    # `PGDMP` is the five bytes the verifier reads to tell a real archive from
    # the two things a failed dump actually produces: an empty file, and a file
    # holding an error message.
    (set_dir / "database.dump").write_bytes(b"PGDMP0123456789")
    mirror = root / "evidence"
    (set_dir / "manifest.json").write_text(
        _manifest_text(
            root,
            version=version,
            evidence_files=_tree_files(mirror) if evidence_files is None else evidence_files,
            evidence_bytes=_tree_bytes(mirror) if evidence_bytes is None else evidence_bytes,
        ),
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603 - a fixed interpreter and a temporary directory
        [BASH, "-c", "sha256sum database.dump manifest.json > SHA256SUMS"],
        cwd=set_dir,
        check=True,
        capture_output=True,
    )
    return set_dir


def _verify(set_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run(VERIFY, "--set", str(set_dir), "--level", "2", *arguments)


def test_a_mirror_matching_its_manifest_is_reported_and_reached_without_docker(
    tmp_path: Path,
) -> None:
    """The mirror check runs before the compose file is even required.

    There is no reason to make somebody start a container to be told the
    evidence is missing, so the cheap check that needs nothing comes first and
    the `pg_restore` pass comes after.
    """
    set_dir = _seal(_backup_root(tmp_path))

    result = _verify(set_dir)

    assert "evidence: 2 file(s), exactly as recorded." in result.stdout
    assert "legacy-source: 1 file(s), exactly as recorded." in result.stdout
    assert "--compose-file is required" in result.stderr


def test_a_mirror_with_fewer_files_than_the_manifest_is_refused(tmp_path: Path) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal(root)
    (root / "evidence" / "aa" / "two.bin").unlink()

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "evidence mirror holds 1 file(s); this set was sealed against 2" in result.stderr
    assert "Objects are missing" in result.stderr


def test_a_mirror_truncated_in_place_is_refused(tmp_path: Path) -> None:
    """The file count is right and the bytes are not, which a count cannot see."""
    root = _backup_root(tmp_path)
    set_dir = _seal(root)
    (root / "evidence" / "aa" / "two.bin").write_bytes(b"")

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "truncated in place rather than removed" in result.stderr


def test_a_mirror_that_has_grown_is_reported_and_is_not_a_failure(tmp_path: Path) -> None:
    """Evidence is append-only and the mirrors are shared between sets.

    An older set verified today is *supposed* to find more than it recorded, so
    treating growth as a failure would make every historical set unverifiable.
    """
    root = _backup_root(tmp_path)
    set_dir = _seal(root)
    (root / "evidence" / "aa" / "three.bin").write_bytes(b"later")

    result = _verify(set_dir)

    assert "1 more than when this set was sealed" in result.stdout
    assert "--compose-file is required" in result.stderr


def test_a_missing_mirror_is_refused_and_says_what_to_do(tmp_path: Path) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal(root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _verify(set_dir, "--backup-root", str(elsewhere))

    assert result.returncode != 0
    assert "evidence mirror not found" in result.stderr
    assert "--no-mirror-check" in result.stderr


def test_the_mirror_check_can_be_skipped_and_says_what_that_leaves(tmp_path: Path) -> None:
    """A set copied without its mirrors is not a complete backup, and the run
    has to say so rather than reporting a clean verification."""
    set_dir = _seal(_backup_root(tmp_path))

    result = _verify(set_dir, "--no-mirror-check")

    assert "as a file, not as a backup" in result.stdout


def test_a_version_one_manifest_is_checked_on_count_and_not_on_bytes(tmp_path: Path) -> None:
    """Version 1 recorded `du -sk` — allocated blocks, which is a property of
    the filesystem rather than of the data. Comparing that against a copy would
    fail on a good off-host set, so the verifier names the comparison it
    skipped instead of making one that does not hold."""
    set_dir = _seal(_backup_root(tmp_path), version=1, evidence_bytes=999_999)

    result = _verify(set_dir)

    assert "evidence: 2 file(s), exactly as recorded." in result.stdout
    assert "byte total not compared" in result.stdout
    assert "manifest version 1" in result.stdout


def test_the_backup_writes_a_versioned_manifest_measured_the_portable_way() -> None:
    """The version is what says `total_bytes` means the sum of file sizes.

    Changing the field's meaning without changing the version is how a verifier
    ends up comparing two different measurements of the same tree.
    """
    text = BACKUP.read_text(encoding="utf-8")
    assert f'"manifest_version": {MEMBERSHIP_VERSION}' in text
    assert "tree_bytes" in text
    assert "du -sk" not in text


def test_level_one_does_not_read_the_mirrors() -> None:
    """The public contract of the levels is preserved.

    Level 1 is "the files in this set are intact" and costs seconds on one file.
    Walking two mirrors is a level-2 cost and belongs there.
    """
    text = VERIFY.read_text(encoding="utf-8")
    body = text.split("# -- level 1 ", 1)[1]
    before_level_two = body.split("# -- level 2 ", 1)[0]
    assert "check_mirrors" not in before_level_two
    assert "exit 0" in before_level_two, "level 1 must still be able to stop here"


# ---------------------------------------------------------------------------
# Set-local membership
# ---------------------------------------------------------------------------
#
# The pool under a backup root is shared and append-only: it holds every object
# any set has ever named. Until manifest version 3 that was the only record of
# what a set's filesystem half contained, which made "restore this set" mean
# "copy the pool" — the same thing only while nothing has ever left the active
# tree.
#
# On 12.09.2026 something did. The reset emptied active evidence and the pool
# kept 20068 pre-reset objects, so the set taken that day — a true record of a
# deployment with no evidence at all — restored twenty thousand orphans.
#
# These prove the two halves of the fix: a set now says which objects are its
# own, and both the verifier and the restore obey that rather than the pool.


def _inventory(*paths: str) -> bytes:
    """A membership inventory as the backup writes one: NUL-delimited, sorted."""
    return b"".join(path.encode("utf-8") + b"\0" for path in sorted(paths))


def _seal_v3(
    root: Path,
    *,
    evidence: tuple[str, ...] = ("aa/one.bin", "aa/two.bin"),
    legacy: tuple[str, ...] = ("page.xml",),
    stamp: str = "20260912T000000Z",
    evidence_inventory: bytes | None = None,
    recorded_files: int | None = None,
    recorded_bytes: int | None = None,
    recorded_inventory_name: str = "evidence.files0",
) -> Path:
    """A version 3 set over the pool `_backup_root` built.

    Every default is the honest one, so each test states the single thing it is
    making wrong.
    """
    pool = root / "evidence"
    set_dir = root / "sets" / stamp
    set_dir.mkdir(parents=True, exist_ok=True)
    (set_dir / "database.dump").write_bytes(b"PGDMP0123456789")
    (set_dir / "evidence.files0").write_bytes(
        _inventory(*evidence) if evidence_inventory is None else evidence_inventory
    )
    (set_dir / "legacy-source.files0").write_bytes(_inventory(*legacy))

    members = sum((pool / member).stat().st_size for member in evidence if (pool / member).exists())
    legacy_dir = root / "legacy-source"
    legacy_bytes = sum(
        (legacy_dir / member).stat().st_size for member in legacy if (legacy_dir / member).exists()
    )
    (set_dir / "manifest.json").write_text(
        "{\n"
        f'  "manifest_version": {MEMBERSHIP_VERSION},\n'
        '  "database": {\n'
        '    "file": "database.dump",\n'
        '    "size_bytes": 15,\n'
        '    "sha256": "not-read-by-these-tests"\n'
        "  },\n"
        '  "evidence_snapshot": {\n'
        f'    "inventory_file": "{recorded_inventory_name}",\n'
        f'    "file_count": {len(evidence) if recorded_files is None else recorded_files},\n'
        f'    "total_bytes": {members if recorded_bytes is None else recorded_bytes}\n'
        "  },\n"
        '  "legacy_source_snapshot": {\n'
        '    "inventory_file": "legacy-source.files0",\n'
        f'    "file_count": {len(legacy)},\n'
        f'    "total_bytes": {legacy_bytes}\n'
        "  },\n"
        '  "evidence_mirror": {\n'
        '    "path_relative_to_backup_root": "evidence",\n'
        f'    "file_count": {_tree_files(pool)},\n'
        f'    "total_bytes": {_tree_bytes(pool)}\n'
        "  },\n"
        '  "legacy_source_mirror": {\n'
        '    "path_relative_to_backup_root": "legacy-source",\n'
        f'    "file_count": {_tree_files(legacy_dir)},\n'
        f'    "total_bytes": {_tree_bytes(legacy_dir)}\n'
        "  },\n"
        '  "empty_source_allowed_for": []\n'
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603 - a fixed interpreter and a temporary directory
        [
            BASH,
            "-c",
            "sha256sum database.dump manifest.json evidence.files0 legacy-source.files0"
            " > SHA256SUMS",
        ],
        cwd=set_dir,
        check=True,
        capture_output=True,
    )
    return set_dir


def test_a_set_that_names_its_objects_says_so_and_passes(tmp_path: Path) -> None:
    result = _verify(_seal_v3(_backup_root(tmp_path)))

    assert "evidence: 2 member(s), 18 byte(s), every one present." in result.stdout
    assert "legacy-source: 1 member(s), 7 byte(s), every one present." in result.stdout


def test_a_set_that_names_nothing_verifies_beside_a_pool_full_of_history(
    tmp_path: Path,
) -> None:
    """Zero is a real production state, and it needs no sentinel to express it.

    This is the clean reset: the deployment held no evidence at all, the pool
    still holds every pre-reset object, and the set is complete.
    """
    result = _verify(_seal_v3(_backup_root(tmp_path), evidence=()))

    assert result.returncode != 0  # only because --compose-file was not given
    assert "evidence: 0 member(s), 0 byte(s), every one present." in result.stdout
    assert "--compose-file is required" in result.stderr


def test_a_pool_that_satisfies_the_counts_with_other_objects_is_not_a_complete_set(
    tmp_path: Path,
) -> None:
    """The point of membership, in one assertion.

    The pool has as many files as the manifest recorded and more bytes than it
    recorded, so every check that reads the pool as a whole passes. One of this
    set's own objects is gone, and only a check that reads the set's list can
    see it.
    """
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    (root / "evidence" / "aa" / "two.bin").unlink()
    (root / "evidence" / "aa" / "historic-z.bin").write_bytes(b"unrelated and larger")

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "evidence: 2 file(s), exactly as recorded." in result.stdout
    assert "evidence member 'aa/two.bin' belongs to this set" in result.stderr
    assert "is not a regular file" in result.stderr


def test_a_member_truncated_in_place_is_refused(tmp_path: Path) -> None:
    """Membership is fixed, so its byte total is an equality rather than a floor
    — which is what lets level 2 catch a truncation without hashing anything.

    The pool is left larger than it was sealed against, so the whole-pool
    comparison is satisfied and only the members' own arithmetic is not.
    """
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    (root / "evidence" / "aa" / "two.bin").write_bytes(b"")
    (root / "evidence" / "aa" / "historic-z.bin").write_bytes(b"unrelated")

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "at least the 18 recorded" in result.stdout, "the pool check has to pass here"
    assert "come to 14 byte(s)" in result.stderr
    assert "sealed against 18" in result.stderr
    assert "changed in place rather than removed" in result.stderr


@pytest.mark.parametrize(
    "entry",
    [b"/absolute/path.bin\0", b"../escape.bin\0", b"aa/../../escape.bin\0", b"\0"],
)
def test_an_inventory_that_leaves_its_own_tree_is_refused(tmp_path: Path, entry: bytes) -> None:
    """A restore turns each of these strings into a path it writes to."""
    set_dir = _seal_v3(_backup_root(tmp_path), evidence_inventory=entry, recorded_files=1)

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "not a relative path inside the tree" in result.stderr


def test_an_inventory_that_names_one_object_twice_is_refused(tmp_path: Path) -> None:
    """Membership is a set, and the backup writes each path once."""
    set_dir = _seal_v3(
        _backup_root(tmp_path),
        evidence_inventory=_inventory("aa/one.bin") + _inventory("aa/one.bin"),
        recorded_files=2,
    )

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "more than once" in result.stderr
    assert "not written by the backup" in result.stderr


def test_an_inventory_the_manifest_miscounts_is_refused(tmp_path: Path) -> None:
    set_dir = _seal_v3(_backup_root(tmp_path), recorded_files=3)

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "names 2 object(s); the manifest says this set has 3" in result.stderr


def test_an_edited_inventory_fails_level_one(tmp_path: Path) -> None:
    """The inventory decides which objects a restore writes, so a set where the
    dump is protected and the membership is not is a set whose restore can be
    steered without breaking a checksum."""
    set_dir = _seal_v3(_backup_root(tmp_path))
    (set_dir / "evidence.files0").write_bytes(_inventory("aa/one.bin"))

    result = run(VERIFY, "--set", str(set_dir), "--level", "1")

    assert result.returncode != 0
    assert "checksums do not match" in result.stderr


def test_a_manifest_that_names_a_path_as_its_inventory_is_refused(tmp_path: Path) -> None:
    set_dir = _seal_v3(_backup_root(tmp_path), recorded_inventory_name="../evidence.files0")

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "That is a path, and a membership inventory is a plain file" in result.stderr


def test_a_set_from_before_membership_is_verified_without_it_and_says_so(
    tmp_path: Path,
) -> None:
    """Version 1 and 2 sets are real production artifacts and stay verifiable.

    What they cannot do is claim a membership they never recorded, so the
    verifier names the thing it is not proving rather than passing quietly.
    """
    result = _verify(_seal(_backup_root(tmp_path), version=2))

    assert "evidence: 2 file(s), exactly as recorded." in result.stdout
    assert "Manifest version 2 records no membership" in result.stdout
    assert "member(s)" not in result.stdout


# ---------------------------------------------------------------------------
# What the restore does with a membership
# ---------------------------------------------------------------------------
#
# Every test here is a refusal, and deliberately so: they run before the script
# requires Docker or rsync, which is what makes them runnable on a laptop. The
# copy itself is the `recovery` job's — it needs a real rsync and a real
# database, and a filesystem restore proved against a stub proves the stub.


def _restore(set_dir: Path, root: Path, data_root: Path, *arguments: str):
    return run(
        RESTORE,
        "--project",
        REHEARSAL_PROJECT,
        "--compose-file",
        str(COMPOSE),
        "--set",
        str(set_dir),
        "--backup-root",
        str(root),
        "--data-root",
        str(data_root),
        *arguments,
    )


def _empty_target(tmp_path: Path) -> Path:
    target = tmp_path / "restored"
    (target / "evidence").mkdir(parents=True)
    (target / "legacy-source").mkdir(parents=True)
    return target


def test_counting_a_tree_that_is_not_there_is_zero_rather_than_a_silent_exit(
    tmp_path: Path,
) -> None:
    """The restore asks this about storage a destroyed deployment has not
    recreated, which is the ordinary case for a recovery.

    `find missing | wc -l` prints `0` and *fails*; `pipefail` carries that out
    to the command substitution the caller assigns from, and `set -e` then ends
    the script with no message. It killed the rehearsal's own restore step —
    after the header, before any check could say anything.
    """
    script = f'set -euo pipefail; . "{SCRIPTS / "lib.sh"}"; count_files "{tmp_path / "gone"}"'
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a temporary path
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_the_restore_refuses_storage_that_is_not_empty_and_leaves_it_alone(
    tmp_path: Path,
) -> None:
    """Exact membership is only exact onto empty storage, and the way to get
    there is not `rsync --delete`: a recovery script that deletes what it finds
    in a tree it did not put there is the accident this whole file is about."""
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    target = _empty_target(tmp_path)
    intruder = target / "evidence" / "unexpected.txt"
    intruder.write_text("not from any backup", encoding="utf-8")

    result = _restore(set_dir, root, target)

    assert result.returncode != 0
    assert "already holds 1 file(s)" in result.stderr
    assert "will not delete them for you" in result.stderr
    assert intruder.read_text(encoding="utf-8") == "not from any backup"
    assert _tree_files(target / "evidence") == 1, "nothing may have been copied in"


def test_the_restore_refuses_an_inventory_that_leaves_its_tree_before_writing(
    tmp_path: Path,
) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root, evidence_inventory=b"../../escape.bin\0", recorded_files=1)
    target = _empty_target(tmp_path)

    result = _restore(set_dir, root, target)

    assert result.returncode != 0
    assert "not a relative path inside the tree" in result.stderr
    assert "Nothing has been written" in result.stderr
    assert _tree_files(target / "evidence") == 0


def test_the_restore_refuses_a_manifest_that_names_a_path_as_its_inventory(
    tmp_path: Path,
) -> None:
    """The field decides which file the restore opens, so it is checked.

    It also pins a subtlety: the refusal happens inside a command substitution,
    where `die` exits only the subshell — the script stops because `set -e`
    fails the assignment around it. Move that call into a condition and this
    refusal silently becomes a warning.
    """
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root, recorded_inventory_name="../evidence.files0")
    target = _empty_target(tmp_path)

    result = _restore(set_dir, root, target)

    assert result.returncode != 0
    assert "That is a path, and a membership inventory is a plain file" in result.stderr
    assert _tree_files(target / "evidence") == 0


def test_the_restore_refuses_an_inventory_that_names_one_object_twice(tmp_path: Path) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(
        root,
        evidence_inventory=_inventory("aa/one.bin") + _inventory("aa/one.bin"),
        recorded_files=2,
    )

    result = _restore(set_dir, root, _empty_target(tmp_path))

    assert result.returncode != 0
    assert "more than once" in result.stderr


def test_a_legacy_set_that_relaxed_the_empty_guard_is_not_restored_as_if_exact(
    tmp_path: Path,
) -> None:
    """The one case where the old whole-pool copy is *known* to be wrong.

    `empty_source_allowed_for` is an audit note about a flag, not a measurement
    — a stale `--allow-empty` left in a runbook is recorded identically — so it
    is never read as proof the tree was empty. What it does mark is a set whose
    filesystem restore cannot be trusted, and the script says so rather than
    quietly copying twenty thousand objects.
    """
    root = _backup_root(tmp_path)
    set_dir = _seal(root, version=2)
    manifest = set_dir / "manifest.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '  "legacy_source_mirror"',
            '  "empty_source_allowed_for": ["evidence"],\n  "legacy_source_mirror"',
        ),
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603 - a fixed interpreter and a temporary directory
        [BASH, "-c", "sha256sum database.dump manifest.json > SHA256SUMS"],
        cwd=set_dir,
        check=True,
        capture_output=True,
    )

    result = _restore(set_dir, root, _empty_target(tmp_path))

    assert result.returncode != 0
    assert "carries no membership" in result.stderr
    assert "--accept-legacy-pool-restore" in result.stderr
    assert "--database-only" in result.stderr


def test_the_restore_reads_the_membership_before_it_needs_a_container() -> None:
    """Ordering, read from the script rather than from an environment.

    Whether Docker happens to be installed decides which error a wrong call
    produces, so the property is asserted where it is written: the refusals that
    need nothing but the filesystem come before the tools are required. The
    backup states the same rule as "paths before tools".
    """
    text = RESTORE.read_text(encoding="utf-8")
    membership = text.index("# 1. What this set says it contains")
    target_guard = text.index("already holds $present file(s)")
    needs_docker = text.index('require_command docker "the database is restored')

    assert membership < target_guard < needs_docker
    assert text.index('require_command rsync "the evidence tree') > target_guard


def test_the_restore_never_reaches_for_delete_to_make_a_target_exact() -> None:
    """Exactness by deletion would be exactness at the price of the one thing a
    recovery script must not do to files it did not put there."""
    text = RESTORE.read_text(encoding="utf-8")
    rsync_lines = [line for line in text.splitlines() if line.strip().startswith("rsync ")]

    assert rsync_lines, "the restore no longer copies anything"
    assert all("--delete" not in line for line in rsync_lines)
    assert all("--ignore-existing" in line for line in rsync_lines)


def test_the_restore_drives_the_copy_from_the_set_rather_than_the_pool() -> None:
    """`--files-from` with `--from0` is what makes the copy obey the set.

    Without it the command is "copy this directory", and the directory is shared
    history rather than this set's contents.
    """
    text = RESTORE.read_text(encoding="utf-8")

    assert '--from0 --files-from="$inventory"' in text


# ---------------------------------------------------------------------------
# The backup-age check
# ---------------------------------------------------------------------------


def _stamp(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%SZ")


def _complete_set(root: Path, stamp: str) -> Path:
    set_dir = root / "sets" / stamp
    set_dir.mkdir(parents=True)
    for name in ("database.dump", "manifest.json", "SHA256SUMS"):
        (set_dir / name).write_text("x", encoding="utf-8")
    return set_dir


def _age(*arguments: str) -> subprocess.CompletedProcess[str]:
    return run(AGE, *arguments)


def test_a_backup_root_that_does_not_exist_is_an_argument_failure(tmp_path: Path) -> None:
    result = _age("--backup-root", str(tmp_path / "nowhere"), "--max-age-hours", "24")

    assert result.returncode == 1
    assert "backup root not found" in result.stderr


def test_a_root_with_no_sets_directory_says_nothing_was_ever_taken(tmp_path: Path) -> None:
    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 2
    assert "No Juristid backup has ever been taken here" in result.stdout


def test_an_empty_sets_directory_is_not_a_backup(tmp_path: Path) -> None:
    (tmp_path / "sets").mkdir()

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 2
    assert "no complete backup set" in result.stdout


def test_a_partial_directory_never_counts_as_a_set(tmp_path: Path) -> None:
    """A `.partial` is what a crashed run leaves. Reported, because an
    unfinished run is worth looking at, and never satisfying the check."""
    (tmp_path / "sets" / f"{_stamp(0)}.partial").mkdir(parents=True)

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 2
    assert ".partial" in result.stdout
    assert "no complete backup set" in result.stdout


def test_a_directory_named_like_a_set_but_missing_a_file_does_not_count(tmp_path: Path) -> None:
    """Not a `.partial`, so the rename happened and something else went wrong.
    Not a backup either."""
    incomplete = tmp_path / "sets" / _stamp(0)
    incomplete.mkdir(parents=True)
    (incomplete / "database.dump").write_text("x", encoding="utf-8")

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 2
    assert "missing one of" in result.stdout


def test_a_fresh_complete_set_passes(tmp_path: Path) -> None:
    _complete_set(tmp_path, _stamp(2))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 0
    assert result.stdout.startswith("OK:")


def test_a_stale_set_fails_with_its_own_exit_code(tmp_path: Path) -> None:
    _complete_set(tmp_path, _stamp(48))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 3
    assert "CRITICAL" in result.stdout
    assert "48h old (limit 24h)" in result.stdout


def test_the_boundary_belongs_to_the_good_side(tmp_path: Path) -> None:
    """A check running on the hour against a backup taken on the hour must not
    alarm on arithmetic."""
    _complete_set(tmp_path, _stamp(23.99))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 0


def test_the_newest_complete_set_is_the_one_that_counts(tmp_path: Path) -> None:
    _complete_set(tmp_path, _stamp(72))
    _complete_set(tmp_path, _stamp(1))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 0
    assert "2 complete set(s)" in result.stdout


def test_a_directory_that_is_not_a_set_is_simply_not_one(tmp_path: Path) -> None:
    """An operator is allowed to keep something beside the sets. It is not an
    error, and it is not a backup."""
    (tmp_path / "sets" / "old-stuff").mkdir(parents=True)
    (tmp_path / "sets" / "notes.txt").write_text("hello", encoding="utf-8")
    _complete_set(tmp_path, _stamp(1))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 0
    assert "1 complete set(s)" in result.stdout


def test_the_age_comes_from_the_name_and_not_from_the_mtime(tmp_path: Path) -> None:
    """Copying a tree rewrites mtimes. A freshness check that trusted one would
    call a set from March "taken today" the moment somebody moved it."""
    stale = _complete_set(tmp_path, _stamp(200))
    now = time.time()
    os.utime(stale, (now, now))

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    assert result.returncode == 3


def test_the_limit_is_required_and_is_not_a_constant_in_the_script(tmp_path: Path) -> None:
    """The RPO is a business decision, and a number written into a script
    becomes policy the day somebody reads it as one."""
    (tmp_path / "sets").mkdir()

    result = _age("--backup-root", str(tmp_path))

    assert result.returncode == 1
    assert "no default" in result.stderr


@pytest.mark.parametrize("value", ["a day", "24h", "-1", "", "2.5"])
def test_a_limit_that_is_not_a_whole_number_of_hours_is_refused(tmp_path: Path, value: str) -> None:
    (tmp_path / "sets").mkdir()

    result = _age("--backup-root", str(tmp_path), "--max-age-hours", value)

    assert result.returncode == 1


def test_the_check_is_read_only(tmp_path: Path) -> None:
    """It answers a question about backups. Nothing it can do may change one."""
    _complete_set(tmp_path, _stamp(1))
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    _age("--backup-root", str(tmp_path), "--max-age-hours", "24")

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    text = AGE.read_text(encoding="utf-8")
    for forbidden in ("rm ", "mv ", "rsync", "docker", ">"):
        assert forbidden not in text.split("EXIT STATUS", 1)[1].split("usage()", 1)[0], forbidden


# ---------------------------------------------------------------------------
# A tree emptied on purpose


def _empty_source_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A source whose evidence tree is empty beside a mirror that still holds it.

    The shape an operational reset leaves behind: the mirrors never delete, so
    yesterday's corpus is still in the backup root while today's tree has
    nothing in it.
    """
    data_root = tmp_path / "appdata"
    backup_root = tmp_path / "backups"
    for tree in ("evidence", "legacy-source"):
        (data_root / tree).mkdir(parents=True)
        (backup_root / tree).mkdir(parents=True)
    (data_root / "legacy-source" / "page.xml").write_text("<page/>", encoding="utf-8")
    (backup_root / "legacy-source" / "page.xml").write_text("<page/>", encoding="utf-8")
    (backup_root / "evidence" / "old.bin").write_bytes(b"kept")
    return data_root, backup_root


def _stub_tools(tmp_path: Path) -> dict[str, str]:
    """`docker` and `rsync` that exist and do nothing, on a PATH of our own.

    The script checks both are installed before it reaches `sync_tree`, so
    without these the guard is unreachable on a laptop and these tests would
    only ever prove that Docker is missing. Neither stub is *called* on the
    paths tested here — the script dies at the guard first — they only have to
    be findable by `command -v`.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("docker", "rsync"):
        stub = bin_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _run_backup(
    data_root: Path, backup_root: Path, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """The real script, far enough to reach the guard and no further."""
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            BASH,
            str(BACKUP),
            "--project",
            "juristid-main",
            "--compose-file",
            str(ROOT / "deploy" / "unraid-main" / "compose.yml"),
            "--data-root",
            str(data_root),
            "--backup-root",
            str(backup_root),
            "--minimum-free-mib",
            "1",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )


def test_an_empty_tree_beside_a_full_mirror_is_still_refused_by_default(tmp_path: Path) -> None:
    """The guard this flag relaxes has to keep working when nobody relaxes it.

    A missing mount and a deliberately emptied tree look identical from here,
    and the default reading stays "missing mount" — the expensive mistake is
    recording a backup that says the Chamber holds no evidence.
    """
    data_root, backup_root = _empty_source_fixture(tmp_path)
    result = _run_backup(data_root, backup_root, env=_stub_tools(tmp_path))
    assert result.returncode != 0
    assert "is a missing mount, not an empty tree" in result.stdout + result.stderr
    assert "--allow-empty evidence" in result.stdout + result.stderr, (
        "the refusal must name the way out, or the operator's only option is to edit the script"
    )


def test_allowing_one_tree_does_not_allow_the_other(tmp_path: Path) -> None:
    """The claim is per tree, because the knowledge behind it is per tree.

    On 2026-09-12 the reset emptied evidence and left legacy-source intact. An
    operator who knows that must not, by saying it, also promise that a
    legacy-source which goes missing next month was emptied on purpose.
    """
    data_root, backup_root = _empty_source_fixture(tmp_path)
    (data_root / "legacy-source" / "page.xml").unlink()
    result = _run_backup(
        data_root, backup_root, "--allow-empty", "evidence", env=_stub_tools(tmp_path)
    )
    assert result.returncode != 0
    assert "legacy-source" in result.stdout + result.stderr


def test_a_misspelled_tree_name_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """A flag that silently allows nothing reads exactly like one that works."""
    data_root, backup_root = _empty_source_fixture(tmp_path)
    result = _run_backup(data_root, backup_root, "--allow-empty", "evidnce")
    assert result.returncode != 0
    assert "--allow-empty takes" in result.stdout + result.stderr


def test_the_manifest_records_which_guards_were_relaxed() -> None:
    """A set taken with a guard relaxed says so, in the artifact that outlives the shell.

    The same reason the release manifest carries its note waiver: the reason a
    check was skipped is worth more later than it is at the time.
    """
    text = BACKUP.read_text(encoding="utf-8")
    assert '"empty_source_allowed_for": [$ALLOW_EMPTY_JSON]' in text
    assert "ALLOW_EMPTY_JSON=" in text
