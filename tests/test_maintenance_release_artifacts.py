"""`scripts/maintenance/juristid-release-artifacts.sh`, run against a synthetic host.

The host's `prune_docker.sh` keeps the newest four `juristid-main-web` images,
but the `.tar.gz` archive each one was loaded from was never pruned: 118 of them
(about 18 GiB) had accumulated by November 2026. The script applies the image
policy to the archives, and the part worth proving is what it refuses: it must
never plan the running release, a kept image or a rollback target, never follow
a symlink, never touch a file outside the release directories, and never act on
a plan that was edited, or whose files changed, after it was reviewed.

These run the real script against a directory laid out like
`/mnt/user/appdata/juristid-main`, with a fake `docker` on PATH that answers
`docker ps` and `docker images` from two text files.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPT = ROOT / "scripts" / "maintenance" / "juristid-release-artifacts.sh"
BASH = shutil.which("bash") or "bash"

CURRENT = "2a873242cd26"
PREVIOUS = "1c104efa103b"
KEPT = "9a66756f5ad8"
OLD_A = "0b035b16590c"
OLD_B = "fea7c1112fa3"
OLD_C = "096bcd9cc26f"

FAKE_DOCKER = """#!/usr/bin/env bash
case "$1" in
  ps) cat "$FAKE_DOCKER_DIR/ps" ;;
  images) cat "$FAKE_DOCKER_DIR/images" ;;
  *) echo "fake docker: refusing $*" >&2; exit 97 ;;
esac
"""


def _archive(directory: Path, tag: str, size: int = 64) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"juristid-main-web-{tag}.tar.gz"
    archive.write_bytes(b"x" * size)
    (directory / f"{archive.name}.sha256").write_text(f"{'0' * 64}  {archive.name}\n")
    old = 1_700_000_000
    os.utime(archive, (old, old))
    return archive


@pytest.fixture
def host(tmp_path: Path) -> dict[str, Path]:
    data = tmp_path / "appdata" / "juristid-main"
    releases = data / "releases"
    # Current kit names its rollback target; the previous kit is otherwise unprotected.
    current_kit = releases / CURRENT
    _archive(current_kit, CURRENT)
    (current_kit / "release.env").write_text(
        f"PREVIOUS_SHA={PREVIOUS}aaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    )
    (current_kit / "d1.log").write_text("kit log\n")
    _archive(releases / PREVIOUS, PREVIOUS)
    _archive(releases / KEPT, KEPT)
    _archive(releases / OLD_A, OLD_A)
    (releases / OLD_A / "d2.log").write_text("old kit log\n")
    _archive(data, OLD_B)  # top-level archive
    _archive(data / "deploy-staging", OLD_C)
    # Never in scope: evidence, backups, quarantine, and look-alike names.
    _archive(data / "evidence", OLD_A)
    _archive(data / "quarantine" / "x", OLD_B)
    (data / "releases" / OLD_A / "juristid-main-web-latest.tar.gz").write_bytes(b"y")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, newline="\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    fake = tmp_path / "fake-docker"
    fake.mkdir()
    (fake / "ps").write_text(
        f"juristid-main-web:{CURRENT}\npostgres:18\ncloudflare/cloudflared:2026.8.2\n"
    )
    (fake / "images").write_text(f"{CURRENT}\n{KEPT}\n<none>\n")
    return {"data": data, "bin": bin_dir, "fake": fake, "tmp": tmp_path}


def run(host: dict[str, Path], *arguments: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{host['bin']}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DOCKER_DIR"] = str(host["fake"])
    env["JURISTID_HOST_LOCK"] = str(host["tmp"] / "host-lock")
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [BASH, str(SCRIPT), *arguments, "--data-root", str(host["data"])],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _plan(host: dict[str, Path]) -> tuple[Path, str]:
    plan = host["tmp"] / "plan.tsv"
    result = run(host, "plan", "--out", str(plan))
    assert result.returncode == 0, result.stderr
    match = re.search(r"sha256: ([0-9a-f]{64})", result.stdout)
    assert match, result.stdout
    return plan, match.group(1)


def _remaining_archives(data: Path) -> set[str]:
    return {p.relative_to(data).as_posix() for p in data.rglob("juristid-main-web-*.tar.gz")}


def test_inventory_classifies_and_writes_nothing(host: dict[str, Path]) -> None:
    before = _snapshot(host["tmp"])
    result = run(host, "inventory")
    assert result.returncode == 0, result.stderr
    assert _snapshot(host["tmp"]) == before
    rows = {line.split("\t")[1]: line.split("\t")[0] for line in result.stdout.splitlines()[1:]}
    assert rows[CURRENT] == "KEEP-RUNNING"
    assert rows[PREVIOUS] == "KEEP-PROTECTED"  # rollback target named by the current kit
    assert rows[KEPT] == "KEEP-PROTECTED"  # still a Docker image
    assert {rows[OLD_A], rows[OLD_B], rows[OLD_C]} == {"REMOVE"}
    paths = result.stdout
    assert "/evidence/" not in paths and "/quarantine/" not in paths
    assert "juristid-main-web-latest" not in paths


def test_plan_and_apply_remove_only_superseded_archives(host: dict[str, Path]) -> None:
    plan, digest = _plan(host)
    planned = [line for line in plan.read_text().splitlines() if line.startswith("REMOVE")]
    assert sorted(line.split("\t")[1] for line in planned) == sorted(
        [OLD_A, OLD_B, OLD_C] * 2
    )  # archive + sidecar

    log = host["tmp"] / "apply.log"
    result = run(host, "apply", "--plan", str(plan), "--expect-sha256", digest, "--log", str(log))
    assert result.returncode == 0, result.stderr
    remaining = _remaining_archives(host["data"])
    for tag in (CURRENT, PREVIOUS, KEPT):
        assert f"releases/{tag}/juristid-main-web-{tag}.tar.gz" in remaining
    assert len(remaining) == 6  # three kept + evidence, quarantine and the look-alike name
    # Out-of-scope copies with the same names survive.
    assert (host["data"] / "evidence" / f"juristid-main-web-{OLD_A}.tar.gz").exists()
    assert (host["data"] / "quarantine" / "x" / f"juristid-main-web-{OLD_B}.tar.gz").exists()
    # Kit logs and the sidecars of kept archives survive; removed archives' sidecars go.
    assert (host["data"] / "releases" / OLD_A / "d2.log").exists()
    assert not (
        host["data"] / "releases" / OLD_A / f"juristid-main-web-{OLD_A}.tar.gz.sha256"
    ).exists()
    assert (
        host["data"] / "releases" / CURRENT / f"juristid-main-web-{CURRENT}.tar.gz.sha256"
    ).exists()
    assert log.read_text().count("\tREMOVED\t") == 6
    assert not (host["tmp"] / "host-lock").exists()

    again = run(host, "apply", "--plan", str(plan), "--expect-sha256", digest, "--log", str(log))
    assert again.returncode == 0, again.stderr
    assert "removed: 0" in again.stdout
    assert log.read_text().count("\tALREADY-GONE\t") == 6


def test_plan_refuses_without_a_running_release(host: dict[str, Path]) -> None:
    (host["fake"] / "ps").write_text("postgres:18\n")
    result = run(host, "plan", "--out", str(host["tmp"] / "plan.tsv"))
    assert result.returncode == 1
    assert "no running" in result.stderr


def _assert_refused_untouched(
    host: dict[str, Path], result: subprocess.CompletedProcess[str], why: str
) -> None:
    assert result.returncode == 1, result.stdout
    assert why in result.stderr, result.stderr
    assert len(_remaining_archives(host["data"])) == 9


def test_apply_refuses_an_edited_plan(host: dict[str, Path]) -> None:
    plan, digest = _plan(host)
    plan.write_text(plan.read_text() + f"REMOVE\t{CURRENT}\t64\t1700000000\t/somewhere\n")
    result = run(
        host,
        "apply",
        "--plan",
        str(plan),
        "--expect-sha256",
        digest,
        "--log",
        str(host["tmp"] / "l"),
    )
    _assert_refused_untouched(host, result, "plan changed")


def test_apply_refuses_a_file_changed_since_the_plan(host: dict[str, Path]) -> None:
    plan, digest = _plan(host)
    (host["data"] / f"juristid-main-web-{OLD_B}.tar.gz").write_bytes(b"z" * 10)
    result = run(
        host,
        "apply",
        "--plan",
        str(plan),
        "--expect-sha256",
        digest,
        "--log",
        str(host["tmp"] / "l"),
    )
    _assert_refused_untouched(host, result, "changed since the plan")


def test_apply_refuses_a_tag_that_became_protected(host: dict[str, Path]) -> None:
    plan, digest = _plan(host)
    (host["fake"] / "images").write_text(f"{CURRENT}\n{KEPT}\n{OLD_C}\n")
    result = run(
        host,
        "apply",
        "--plan",
        str(plan),
        "--expect-sha256",
        digest,
        "--log",
        str(host["tmp"] / "l"),
    )
    _assert_refused_untouched(host, result, "protected now")


def test_apply_refuses_a_symlink(host: dict[str, Path]) -> None:
    plan, digest = _plan(host)
    target = host["data"] / f"juristid-main-web-{OLD_B}.tar.gz"
    elsewhere = host["tmp"] / "precious.tar.gz"
    shutil.move(str(target), str(elsewhere))
    try:
        target.symlink_to(elsewhere)
    except OSError:
        pytest.skip("this filesystem cannot create symlinks")
    if not target.is_symlink():
        pytest.skip("symlink creation produced a copy on this platform")
    result = run(
        host,
        "apply",
        "--plan",
        str(plan),
        "--expect-sha256",
        digest,
        "--log",
        str(host["tmp"] / "l"),
    )
    assert result.returncode == 1
    assert "symlink" in result.stderr
    assert elsewhere.exists()


def test_apply_refuses_while_the_host_lock_is_held(host: dict[str, Path]) -> None:
    plan, digest = _plan(host)
    lock = host["tmp"] / "host-lock"
    lock.mkdir()
    (lock / "owner").write_text("owner=release-45\n")
    result = run(
        host,
        "apply",
        "--plan",
        str(plan),
        "--expect-sha256",
        digest,
        "--log",
        str(host["tmp"] / "l"),
    )
    _assert_refused_untouched(host, result, "host lock held")
    assert lock.exists()


def test_the_script_has_no_docker_mutation_or_prune() -> None:
    text = SCRIPT.read_text()
    for forbidden in (
        "docker rmi",
        "docker rm ",
        "prune",
        "docker volume",
        "docker compose",
        "rm -rf",
    ):
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert forbidden not in code, forbidden
