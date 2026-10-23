"""A backup set cannot restore a link, a FIFO or a path outside its tree (#14).

The evidence tree is the immutable store the application serves bytes from. A
backup writes regular files into it and nothing else, and before this the
restore did not insist on that: verification tested members with `[ -f ]`,
which follows a symbolic link, and the copy was `rsync -a`, which preserves
one. A pool holding a link named like a member therefore verified — the link
pointed at a regular file somewhere on the host — and the restore put the link
into the evidence tree, where the application would follow it.

What these prove, each against the real scripts:

* verification refuses a symbolic link (to a file or to a directory), a FIFO,
  or a set file that is a link, and refuses a SHA256SUMS that names anything
  but plain files inside the set;
* the restore refuses the same objects **before anything is copied** and before
  any container is started, leaving the target exactly as it was;
* after the copy the restore proves the written trees hold regular files only —
  and exactly the members, at the sealed byte total — **before the database is
  touched**;
* a clean set still verifies and restores, and what it restores is regular
  files.

What they do not prove, because nothing here can: that the bytes are the ones
the backup wrote. SHA256SUMS sits inside the set it describes, so somebody able
to rewrite both can still forge content. Closing that needs a manifest signed
with a key kept elsewhere, which the owner has deferred (RECOVERY.md, "What a
restore refuses to copy").

Links and FIFOs need a POSIX filesystem, and the end-to-end restore needs a real
rsync; both are on the Linux runner, neither is on the Windows development
machine, so those tests skip there and only there. Everything else runs
everywhere.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_backup_verification_health import (
    BASH,
    COMPOSE,
    REHEARSAL_PROJECT,
    RESTORE,
    VERIFY,
    _backup_root,
    _empty_target,
    _seal_v3,
    run,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="symbolic links, FIFOs and rsync need a POSIX filesystem; CI runs on Linux",
)

#: What a tampered set's links point at. Outside the backup root, the way a
#: real attack's target would be.
OUTSIDE_CONTENT = b"bytes that never belonged to any backup"

Tamper = Callable[[Path, Path], None]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    return outside


def _member_link(root: Path, tmp_path: Path) -> None:
    """`aa/two.bin` is a link to a regular file outside the backup root."""
    target = _outside(tmp_path) / "secret.bin"
    target.write_bytes(OUTSIDE_CONTENT)
    member = root / "evidence" / "aa" / "two.bin"
    member.unlink()
    member.symlink_to(target)


def _directory_link(root: Path, tmp_path: Path) -> None:
    """`aa` is a link to a directory outside, holding files named like members.

    Every member path then resolves, as a regular file, to something the backup
    never wrote — and nothing about any single member is a link.
    """
    elsewhere = _outside(tmp_path) / "aa"
    elsewhere.mkdir()
    (elsewhere / "one.bin").write_bytes(b"hello-evidence")
    (elsewhere / "two.bin").write_bytes(b"more")
    shutil.rmtree(root / "evidence" / "aa")
    (root / "evidence" / "aa").symlink_to(elsewhere, target_is_directory=True)


def _fifo(root: Path, tmp_path: Path) -> None:
    """A FIFO in the pool. Not a member; the pool-wide copy would still take it,
    and opening one blocks whatever reads it."""
    os.mkfifo(root / "evidence" / "aa" / "pipe")


TAMPERINGS: dict[str, Tamper] = {
    "a member that is a symbolic link to a file outside": _member_link,
    "a directory that is a symbolic link": _directory_link,
    "a FIFO in the pool": _fifo,
}


def _tampered_set(tmp_path: Path, tamper: Tamper) -> tuple[Path, Path]:
    """A pool tampered with *before* the set was sealed.

    So every checksum, count and byte total agrees with the tampered state, the
    way it would for somebody who rewrote the set as well as the pool. Only the
    kind of object is wrong.
    """
    root = _backup_root(tmp_path)
    tamper(root, tmp_path)
    return root, _seal_v3(root)


def _stub_docker(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A `docker` that answers the scripts' questions and records every call.

    The database half of a restore is the `recovery` job's to prove against a
    real PostgreSQL. What matters here is *whether* the restore reaches the
    database, so the stub logs its arguments and the tests read the log: a
    `pg_restore --dbname=` line is the database being touched.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$JURISTID_STUB_DOCKER_LOG"\n'
        'case "$*" in\n'
        # The tables level 2 requires, so verification reaches its end.
        "  *'pg_restore --list'*)"
        " printf 'matters_matter\\ndocuments_documentversion\\ndjango_migrations\\n' ;;\n"
        "  *psql*) printf '0\\n' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "JURISTID_STUB_DOCKER_LOG": str(log),
    }
    return env, log


def _restore(
    set_dir: Path, root: Path, target: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            BASH,
            str(RESTORE),
            "--project",
            REHEARSAL_PROJECT,
            "--compose-file",
            str(COMPOSE),
            "--set",
            str(set_dir),
            "--backup-root",
            str(root),
            "--data-root",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def _touched_the_database(log: Path) -> bool:
    return log.exists() and "--dbname=" in log.read_text(encoding="utf-8")


def _entries(directory: Path) -> list[str]:
    """Everything under a directory, links and FIFOs included, without
    following anything."""
    found = []
    for parent, directories, files in os.walk(directory, followlinks=False):
        found.extend(os.path.join(parent, name) for name in (*directories, *files))
    return sorted(found)


def _assert_untouched(target: Path) -> None:
    assert _entries(target / "evidence") == [], "the refused restore wrote evidence"
    assert _entries(target / "legacy-source") == [], "the refused restore wrote page XML"
    assert not (target / "derivatives").exists(), "the refused restore reached the copy step"


def _verify(set_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run(VERIFY, "--set", str(set_dir), "--level", "2", *arguments)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@posix_only
@pytest.mark.parametrize("tamper", TAMPERINGS.values(), ids=TAMPERINGS.keys())
def test_verification_refuses_a_pool_holding_anything_but_regular_files(
    tmp_path: Path, tamper: Tamper
) -> None:
    _, set_dir = _tampered_set(tmp_path, tamper)

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "which is not a regular file or a directory" in result.stderr
    assert "a restore must not carry it into the evidence store" in result.stderr


@posix_only
def test_verification_refuses_a_pool_that_is_itself_a_link(tmp_path: Path) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    moved = _outside(tmp_path) / "evidence"
    (root / "evidence").rename(moved)
    (root / "evidence").symlink_to(moved, target_is_directory=True)

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "byte pool at" in result.stderr
    assert "is a symbolic link" in result.stderr


@posix_only
def test_verification_refuses_a_set_file_that_is_a_link(tmp_path: Path) -> None:
    """`sha256sum` follows a link, so a checksummed link vouches for whatever it
    points at today. Level 1, because the set's own files are level 1's."""
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    inventory = set_dir / "evidence.files0"
    moved = _outside(tmp_path) / "evidence.files0"
    inventory.rename(moved)
    inventory.symlink_to(moved)

    result = run(VERIFY, "--set", str(set_dir), "--level", "1")

    assert result.returncode != 0
    assert "the set holds" in result.stderr
    assert "which is not a regular file" in result.stderr


def _rewrite_sums(set_dir: Path, text: str) -> None:
    # Bytes, so Windows does not turn the backup's LF into CRLF on the way.
    (set_dir / "SHA256SUMS").write_bytes(text.encode("utf-8"))


def _sums(set_dir: Path) -> str:
    return (set_dir / "SHA256SUMS").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name",
    ["/etc/hostname", "../outside.bin", "sub/database.dump", ".."],
)
def test_verification_refuses_a_checksum_list_that_names_a_path(tmp_path: Path, name: str) -> None:
    """SHA256SUMS is read for shape before it is trusted.

    The backup writes plain names inside the set, nothing else. An absolute
    path or a `..` makes the check vouch for a file the set does not contain.
    """
    set_dir = _seal_v3(_backup_root(tmp_path))
    _rewrite_sums(set_dir, _sums(set_dir) + f"{'0' * 64}  {name}\n")

    result = run(VERIFY, "--set", str(set_dir), "--level", "1")

    assert result.returncode != 0
    assert "SHA256SUMS" in result.stderr
    assert "checksums do not match" not in result.stderr, "refused for shape, before any hashing"


@pytest.mark.parametrize("dropped", ["database.dump", "manifest.json"])
def test_verification_refuses_a_checksum_list_that_leaves_out_the_set(
    tmp_path: Path, dropped: str
) -> None:
    set_dir = _seal_v3(_backup_root(tmp_path))
    kept = [line for line in _sums(set_dir).splitlines() if not line.endswith(dropped)]
    _rewrite_sums(set_dir, "\n".join(kept) + "\n")

    result = run(VERIFY, "--set", str(set_dir), "--level", "1")

    assert result.returncode != 0
    assert f"SHA256SUMS does not cover {dropped}" in result.stderr


def test_verification_refuses_an_inventory_the_checksums_do_not_cover(tmp_path: Path) -> None:
    """The inventory decides what a restore writes. One SHA256SUMS does not
    name can be edited in place without any checksum noticing."""
    set_dir = _seal_v3(_backup_root(tmp_path))
    kept = [line for line in _sums(set_dir).splitlines() if "evidence.files0" not in line]
    _rewrite_sums(set_dir, "\n".join(kept) + "\n")

    result = _verify(set_dir)

    assert result.returncode != 0
    assert "'evidence.files0' is not covered by SHA256SUMS" in result.stderr


def test_a_clean_set_still_verifies_and_says_what_its_pools_hold(tmp_path: Path) -> None:
    result = _verify(_seal_v3(_backup_root(tmp_path)))

    assert "evidence: regular files and directories only" in result.stdout
    assert "legacy-source: regular files and directories only" in result.stdout
    assert "evidence: 2 member(s), 18 byte(s), every one present." in result.stdout
    # It stops where it always did without a compose file: the pg_restore pass.
    assert "--compose-file is required" in result.stderr


# ---------------------------------------------------------------------------
# The restore, before it copies anything
# ---------------------------------------------------------------------------


@posix_only
@pytest.mark.parametrize("tamper", TAMPERINGS.values(), ids=TAMPERINGS.keys())
def test_the_restore_refuses_a_tampered_pool_before_copying_anything(
    tmp_path: Path, tamper: Tamper
) -> None:
    root, set_dir = _tampered_set(tmp_path, tamper)
    target = _empty_target(tmp_path)
    env, log = _stub_docker(tmp_path)

    result = _restore(set_dir, root, target, env)

    assert result.returncode != 0
    assert "which is not a regular file or a directory" in result.stderr
    assert "Nothing has been written" in result.stderr
    _assert_untouched(target)
    assert not log.exists(), "the refusal must come before any container is asked anything"


@pytest.mark.parametrize(
    "entry",
    [b"/etc/hostname\0", b"aa/../../escape.bin\0", b"../escape.bin\0"],
    ids=["absolute", "dot-dot inside", "dot-dot first"],
)
def test_the_restore_refuses_an_inventory_path_outside_its_tree(
    tmp_path: Path, entry: bytes
) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root, evidence_inventory=entry, recorded_files=1)
    target = _empty_target(tmp_path)
    env, log = _stub_docker(tmp_path)

    result = _restore(set_dir, root, target, env)

    assert result.returncode != 0
    assert "not a relative path inside the tree" in result.stderr
    _assert_untouched(target)
    assert not _touched_the_database(log)


@posix_only
def test_the_restore_refuses_a_link_already_in_the_target(tmp_path: Path) -> None:
    """`count_files` counts regular files, so a link alone reads as empty
    storage — and a link to a directory is a place a copy could write through."""
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    target = _empty_target(tmp_path)
    planted = target / "evidence" / "aa"
    planted.symlink_to(_outside(tmp_path), target_is_directory=True)
    env, log = _stub_docker(tmp_path)

    result = _restore(set_dir, root, target, env)

    assert result.returncode != 0
    assert "which is not a regular file or a directory" in result.stderr
    assert "Nothing has been changed" in result.stderr
    assert planted.is_symlink(), "the refusal must leave the operator's storage as it was"
    assert _entries(_outside(tmp_path)) == [], "nothing may have been written through it"
    assert not log.exists()


# ---------------------------------------------------------------------------
# The restore, end to end, with a real rsync
# ---------------------------------------------------------------------------


@posix_only
def test_a_clean_set_restores_regular_files_and_proves_them_before_the_database(
    tmp_path: Path,
) -> None:
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    target = _empty_target(tmp_path)
    env, log = _stub_docker(tmp_path)

    result = _restore(set_dir, root, target, env)

    assert result.returncode == 0, result.stderr
    restored = {
        Path(entry).relative_to(target).as_posix()
        for entry in _entries(target)
        if not stat.S_ISDIR(os.lstat(entry).st_mode)
    }
    assert restored == {"evidence/aa/one.bin", "evidence/aa/two.bin", "legacy-source/page.xml"}
    for name in restored:
        mode = os.lstat(target / name).st_mode
        assert stat.S_ISREG(mode), f"{name} was restored as something other than a regular file"
        assert (target / name).read_bytes() == (root / name).read_bytes()

    assert "evidence: 2 regular file(s), 18 byte(s), no links or special files" in result.stdout
    proof = result.stdout.index("Proving what was written, before the database")
    database = result.stdout.index("== Database")
    assert proof < database
    assert _touched_the_database(log)


@posix_only
def test_a_link_that_appears_during_the_copy_is_refused_before_the_database(
    tmp_path: Path,
) -> None:
    """The proof after the copy is its own line of defence, not a repeat.

    The walk before the copy cannot see what appears after it, and rsync skips a
    link with a warning and exit 0 rather than failing. So an `rsync` that does
    its job and then leaves a link behind stands in for anything that gets one
    into the tree between the checks: the restore must stop there, with the
    database untouched.
    """
    real_rsync = shutil.which("rsync")
    assert real_rsync, "rsync is on the Linux runner; without it this proves nothing"
    root = _backup_root(tmp_path)
    set_dir = _seal_v3(root)
    target = _empty_target(tmp_path)
    env, log = _stub_docker(tmp_path)
    secret = _outside(tmp_path) / "secret.bin"
    secret.write_bytes(OUTSIDE_CONTENT)
    wrapper = Path(env["PATH"].split(os.pathsep, 1)[0]) / "rsync"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'"{real_rsync}" "$@" || exit $?\n'
        'for last in "$@"; do :; done\n'
        f'ln -s "{secret}" "${{last%/}}/planted.bin"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = _restore(set_dir, root, target, env)

    assert result.returncode != 0
    assert "the restored evidence tree holds" in result.stderr
    assert "The database has not been touched" in result.stderr
    assert not _touched_the_database(log)


def test_the_copy_never_preserves_a_link_or_a_special_file() -> None:
    """The flags are the last line, behind the walks: what holds if something
    appears between the check and the copy."""
    text = RESTORE.read_text(encoding="utf-8")
    rsync_lines = [line for line in text.splitlines() if line.strip().startswith("rsync ")]

    assert rsync_lines
    for line in rsync_lines:
        assert "--no-links" in line
        assert "--no-specials" in line
        assert "--no-devices" in line
        assert "--copy-links" not in line and " -L" not in line, "never follow a link either"


def test_the_restore_proves_the_trees_before_it_restores_the_database() -> None:
    """Ordering, read from the script: the proof is between the copy and the
    database, so a failed proof leaves an empty database behind."""
    text = RESTORE.read_text(encoding="utf-8")
    copy = text.index('restore_tree "evidence"')
    proof = text.index('prove_restored_tree "evidence"')
    # The restoring call, not step 3's read-only `pg_restore --schema-only`
    # listing of the roles the dump needs.
    database = text.index("juristid_compose exec -T db pg_restore \\\n")

    assert copy < proof < database
