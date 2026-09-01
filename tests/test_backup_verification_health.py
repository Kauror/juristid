"""What ordinary backup verification proves, and whether one was taken lately.

Two gaps the pilot backup/DR audit found, neither of them in the backup itself:

**The manifest recorded the mirrors and nothing read it back.** Every set names
how many files each mirror held and how many bytes they came to. A set could
therefore pass every check it had while the evidence it depends on had been
emptied — and the way that gets discovered is by needing it.

**Nothing could say when the last backup was.** 34 proper sets on the host, no
schedule behind any of them, every one taken by hand before a deployment, worst
observed gap about 41 hours. A backup regime nobody measures is
indistinguishable, from outside, from one that stopped last week.

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


def test_the_backup_writes_a_version_two_manifest_measured_the_portable_way() -> None:
    """The version is what says `total_bytes` means the sum of file sizes.

    Changing the field's meaning without changing the version is how a verifier
    ends up comparing two different measurements of the same tree.
    """
    text = BACKUP.read_text(encoding="utf-8")
    assert '"manifest_version": 2' in text
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
