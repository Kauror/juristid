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
import time
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


def _next_second(world: World) -> None:
    """Sets are named to the second, and a second run in the same one is refused."""
    taken = {path.name for path in world.sets()}
    while time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) in taken:
        time.sleep(0.05)


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
    _next_second(world)

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


# -- r96 integration: the Lovable/OneNote one-off reads its set's proof ------------------


@posix_modes
def test_a_private_set_opens_only_its_proof_to_the_applications_group(tmp_path: Path):
    """SEC-011 writes a set 0700/0600 as root; `lovable_sync.backup_proof_problem` reads
    VERIFIED-L2, SHA256SUMS and database.dump as the application's account (10001) in
    a one-off container. Without `share_set_proof` every Lovable APPLY and OneNote
    import would be refused (PROOF_MISSING). With it, exactly those three objects and
    the directory reach the group, read-only, and nothing reaches others."""
    group = os.getgid()  # a group the runner belongs to stands in for 10001
    set_dir = tmp_path / "sets" / "20261127T001500Z"
    set_dir.mkdir(parents=True)
    set_dir.chmod(0o700)
    proof = ("VERIFIED-L2", "SHA256SUMS", "database.dump")
    private = ("manifest.txt", "evidence.inventory")
    for name in (*proof, *private):
        (set_dir / name).write_bytes(b"synthetic")
        (set_dir / name).chmod(0o600)

    done = _lib(f'share_set_proof "{set_dir.as_posix()}" {group}', tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr

    assert _mode(set_dir) == 0o750 and set_dir.stat().st_gid == group
    for name in proof:
        assert _mode(set_dir / name) == 0o640, name
        assert (set_dir / name).stat().st_gid == group
    for name in private:
        assert _mode(set_dir / name) == 0o600, name
    beyond = _lib(f'reaches_others "$(access_beyond_owner "{set_dir.as_posix()}")"', tmp_path)
    assert beyond.returncode == 1  # nothing reaches outside the owner's group


@posix_modes
def test_a_proof_that_cannot_be_opened_is_reported_not_hidden(tmp_path: Path):
    missing = _lib(f'share_set_proof "{(tmp_path / "absent").as_posix()}" 0', tmp_path)
    assert missing.returncode != 0


def test_the_lovable_lane_opens_the_proof_after_verifying_and_before_mounting_it():
    text = (SCRIPTS / "juristid-lovable-sync.sh").read_text(encoding="utf-8")
    verified = text.index("--level 2 --record")
    shared = text.index('share_set_proof "$SET_PATH" "$APP_GID"')
    mounted = text.index('proof=(-v "$SET_DIR:/backup-set:ro"')
    assert verified < shared < mounted
    assert "APP_GID=${JURISTID_SYNC_APP_GID:-10001}" in text


# -- r96 final (K): exactly what the Lovable proof check opens, and who gains nothing ------

#: The three objects `lovable_sync.backup_proof_problem` opens, and nothing else.
PROOF_OBJECTS = ("VERIFIED-L2", "SHA256SUMS", "database.dump")
OTHER_OBJECTS = ("manifest.json", "evidence.files0", "legacy-source.files0")


def _proof_set(base: Path, *, minutes_ago: int = 10) -> Path:
    """A synthetic set with the three proof objects and some other objects."""
    import datetime as dt

    from tests.backup_proof import write_backup_set

    taken = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(minutes=minutes_ago)
    set_dir = write_backup_set(base, taken=taken, verified=taken + dt.timedelta(minutes=2))
    for extra in OTHER_OBJECTS:
        (set_dir / extra).write_bytes(b"synthetic, never read by the check")
    return set_dir


def test_the_proof_check_opens_exactly_the_three_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The list `share_set_proof` opens to the application's group is the list the
    engine reads: if the check ever reads another object of the set, this fails and
    the share must be widened on purpose (or the check changed back)."""
    import datetime as dt
    import pathlib

    from app.legacy_import import lovable_sync

    set_dir = _proof_set(tmp_path)
    opened: list[str] = []
    real_open = pathlib.Path.open

    def recording_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", recording_open)
    problem = lovable_sync.backup_proof_problem(
        f"sets/{set_dir.name}", dt.datetime.now(dt.UTC), set_dir=str(set_dir)
    )
    assert problem == ""
    assert sorted(set(opened)) == sorted(PROOF_OBJECTS)


@pytest.mark.parametrize("missing", PROOF_OBJECTS)
def test_a_set_whose_proof_object_is_not_there_is_refused_with_a_code(tmp_path: Path, missing: str):
    """The fail-closed side: when `share_set_proof` failed (or never ran) the run
    cannot open the object and refuses the set with a named code."""
    import datetime as dt

    from app.legacy_import import lovable_sync

    set_dir = _proof_set(tmp_path)
    (set_dir / missing).unlink()
    problem = lovable_sync.backup_proof_problem(
        f"sets/{set_dir.name}", dt.datetime.now(dt.UTC), set_dir=str(set_dir)
    )
    assert problem == ("PROOF_DUMP" if missing == "database.dump" else "PROOF_MISSING")


def _private_set(base: Path, name: str) -> Path:
    set_dir = base / "sets" / name
    set_dir.mkdir(parents=True)
    set_dir.chmod(0o700)
    for object_name in (*PROOF_OBJECTS, *OTHER_OBJECTS):
        (set_dir / object_name).write_bytes(b"synthetic " + object_name.encode())
        (set_dir / object_name).chmod(0o600)
    return set_dir


@posix_modes
def test_a_share_that_cannot_be_made_leaves_the_set_private(tmp_path: Path):
    """Fail closed: a group the caller can neither give nor belongs to changes
    nothing and returns non-zero, so `take_backup` logs the WARN and the engine
    refuses the set (PROOF_MISSING) rather than anything being half opened."""
    if os.geteuid() == 0:
        pytest.skip("root can give any group; the failure is only reachable unprivileged")
    set_dir = _private_set(tmp_path, "20261127T220008Z")
    stranger = 54321
    assert stranger not in os.getgroups()
    done = _lib(f'share_set_proof "{set_dir.as_posix()}" {stranger}', tmp_path)
    assert done.returncode != 0
    assert _mode(set_dir) == 0o700
    for name in PROOF_OBJECTS:
        assert _mode(set_dir / name) == 0o600, name


@posix_modes
def test_a_linked_proof_object_is_refused_and_its_target_untouched(tmp_path: Path):
    """chgrp and chmod follow links; a link standing in for a proof object must
    not open whatever it points at."""
    set_dir = _private_set(tmp_path, "20261127T220008Z")
    secret = tmp_path / "unrelated-secret"
    secret.write_bytes(b"synthetic")
    secret.chmod(0o600)
    (set_dir / "VERIFIED-L2").unlink()
    (set_dir / "VERIFIED-L2").symlink_to(secret)

    done = _lib(f'share_set_proof "{set_dir.as_posix()}" {os.getgid()}', tmp_path)
    assert done.returncode != 0
    assert _mode(secret) == 0o600


@posix_modes
def test_sharing_one_set_changes_no_other_set_pool_or_parent(tmp_path: Path):
    target = _private_set(tmp_path, "20261127T220008Z")
    _private_set(tmp_path, "20261126T220008Z")
    pool = tmp_path / "evidence"
    pool.mkdir()
    pool.chmod(0o700)
    (pool / "object").write_bytes(b"synthetic")
    (pool / "object").chmod(0o600)
    (tmp_path / "sets").chmod(0o700)

    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            str(p.relative_to(tmp_path)): (_mode(p), p.stat().st_gid)
            for p in sorted(tmp_path.rglob("*"))
            if target not in p.parents and p != target
        }

    before = snapshot()
    done = _lib(f'share_set_proof "{target.as_posix()}" {os.getgid()}', tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert snapshot() == before
    for name in OTHER_OBJECTS:
        assert _mode(target / name) == 0o600, name


SUDO = shutil.which("sudo") or "sudo"
SETPRIV = shutil.which("setpriv") or "setpriv"


def _sudo_can_become_the_application() -> bool:
    if os.name != "posix" or not shutil.which("sudo") or not shutil.which("setpriv"):
        return False
    if not Path("/usr/bin/python3").exists():
        return False
    probe = subprocess.run(  # noqa: S603 - fixed argv
        [SUDO, "-n", "true"], capture_output=True, check=False, timeout=30
    )
    return probe.returncode == 0


#: Run as the account under test. Reports what it can read, write and list.
ACCOUNT_PROBE = """
import json, os, sys
target, other, pool = sys.argv[1:4]
def ok(action, path):
    try:
        action(path)
        return True
    except OSError:
        return False
def read(path):
    open(path, "rb").read(1)
def write(path):
    open(path, "ab").close()
print(json.dumps({
    "proof": {n: ok(read, os.path.join(target, n)) for n in sys.argv[4].split(",")},
    "rest": {n: ok(read, os.path.join(target, n)) for n in sys.argv[5].split(",")},
    "other_set": ok(read, os.path.join(other, "database.dump")),
    "pool": ok(read, os.path.join(pool, "object")),
    "write_dump": ok(write, os.path.join(target, "database.dump")),
    "create": ok(write, os.path.join(target, "planted")),
    "list_set": ok(os.listdir, target),
    "list_sets": ok(os.listdir, os.path.dirname(target)),
}))
"""


@posix_modes
@pytest.mark.skipif(
    not _sudo_can_become_the_application(),
    reason="needs passwordless sudo, setpriv and /usr/bin/python3 (the Linux CI runner has them)",
)
def test_the_application_account_reads_the_proof_and_nothing_else(tmp_path: Path):
    """As the real uid/gid of the one-off container (10001) and as a stranger: the
    three proof objects open, nothing else of the set, no other set, no pool, and
    nothing can be written. `sets/` is searchable (0711) only to stand in for the
    bind mount, which hands the container the set directory itself."""
    import json
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="juristid-proof-"))
    try:
        base.chmod(0o711)
        target = _private_set(base, "20261127T220008Z")
        other = _private_set(base, "20261126T220008Z")
        (base / "sets").chmod(0o711)
        pool = base / "evidence"
        pool.mkdir()
        pool.chmod(0o700)
        (pool / "object").write_bytes(b"synthetic")
        (pool / "object").chmod(0o600)

        shared = subprocess.run(  # noqa: S603 - fixed argv; synthetic paths
            [
                SUDO,
                "-n",
                BASH,
                "-c",
                f'. "{LIB.as_posix()}"; share_set_proof "{target.as_posix()}" 10001',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert shared.returncode == 0, shared.stdout + shared.stderr

        def as_account(uid: int, gid: int) -> dict[str, object]:
            run = subprocess.run(  # noqa: S603 - fixed argv; synthetic paths
                [
                    SUDO,
                    "-n",
                    SETPRIV,
                    f"--reuid={uid}",
                    f"--regid={gid}",
                    "--clear-groups",
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    ACCOUNT_PROBE,
                    str(target),
                    str(other),
                    str(pool),
                    ",".join(PROOF_OBJECTS),
                    ",".join(OTHER_OBJECTS),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert run.returncode == 0, run.stdout + run.stderr
            return json.loads(run.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]

        application = as_account(10001, 10001)
        assert application["proof"] == dict.fromkeys(PROOF_OBJECTS, True)
        assert application["rest"] == dict.fromkeys(OTHER_OBJECTS, False)
        assert application["other_set"] is False
        assert application["pool"] is False
        assert application["write_dump"] is False
        assert application["create"] is False
        assert application["list_sets"] is False

        stranger = as_account(10002, 10002)
        assert stranger["proof"] == dict.fromkeys(PROOF_OBJECTS, False)
        assert stranger["rest"] == dict.fromkeys(OTHER_OBJECTS, False)
        assert stranger["other_set"] is False
        assert stranger["pool"] is False
        assert stranger["write_dump"] is False
        assert stranger["create"] is False
    finally:
        subprocess.run(  # noqa: S603 - fixed argv
            [SUDO, "-n", "rm", "-rf", str(base)], check=False, timeout=60
        )


@posix_modes
@needs_rsync
def test_a_set_taken_verified_shared_and_restored_end_to_end(tmp_path: Path):
    """The sync's own sequence on a private set: backup under umask 022, verify to
    level 2 with `--record`, `share_set_proof`, the engine's real proof check on the
    result (nothing mocked), then the production restore of that same set."""
    import datetime as dt

    from app.legacy_import import lovable_sync

    world = World(tmp_path)
    assert world.backup().returncode == 0
    [set_dir] = world.sets()
    assert _mode(set_dir) == 0o700

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
        "--record",
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert _mode(set_dir / "VERIFIED-L2") == 0o600  # private as written, before the share

    # Before the share the application's account (here: the same user, so only the
    # modes can be compared) would see a 0600 marker in a 0700 directory.
    shared = _lib(f'share_set_proof "{set_dir.as_posix()}" {os.getgid()}', tmp_path)
    assert shared.returncode == 0, shared.stdout + shared.stderr
    assert _mode(set_dir) == 0o750
    assert {n for n in os.listdir(set_dir) if _mode(set_dir / n) & 0o040} == set(PROOF_OBJECTS)
    assert not any(_mode(set_dir / n) & 0o007 for n in os.listdir(set_dir))

    problem = lovable_sync.backup_proof_problem(
        f"sets/{set_dir.name}",
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1),
        set_dir=str(set_dir),
    )
    assert problem == "", problem

    target = tmp_path / "restored"
    target.mkdir()
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
    assert (target / "evidence" / "ab" / "first.pdf").read_bytes() == b"synthetic evidence one"
