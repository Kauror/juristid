"""The nightly cycle's cron change and its rollback, offline (ADR 0229 §7, §8).

`deploy/unraid-main/cron/juristid-nightly-transition.sh` runs on a copy of the
host's Juristid cron files as they stood on 25.11.2026
(`tests/fixtures/nightly_cycle/dynamix/`: read from the host, paths only, no
secret; `other.cron` is a synthetic neighbour). Never the host.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPT = ROOT / "deploy" / "unraid-main" / "cron" / "juristid-nightly-transition.sh"
NIGHTLY_CRON = ROOT / "deploy" / "unraid-main" / "cron" / "juristid-nightly.cron"
FIXTURE = ROOT / "tests" / "fixtures" / "nightly_cycle" / "dynamix"
BASH = shutil.which("bash") or "bash"
CYCLE_LINE = (
    "0 21,22 * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/"
    "juristid-nightly-cycle.sh &> /dev/null"
)


def _schedule_lines(*paths: Path) -> list[str]:
    lines = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                lines.append(line)
    return lines


def _cron_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.cron"))


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def _run(directory: Path, action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository script
        [
            BASH,
            str(SCRIPT),
            "--cron-dir",
            str(directory),
            "--nightly-cron",
            str(NIGHTLY_CRON),
            f"--{action}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def dynamix(tmp_path: Path) -> Path:
    directory = tmp_path / "dynamix"
    shutil.copytree(FIXTURE, directory)
    return directory


def test_the_fixture_is_the_host_schedule_before_the_cycle(dynamix: Path) -> None:
    lines = "\n".join(_schedule_lines(*_cron_files(dynamix)))
    for expected in (
        "15 21,22 * * * bash",
        "2-59/5 * * * *",
        "30 3 * * * docker exec",
        "20 4 * * * docker exec",
        "*/30 10-23 * * 3",
        "*/30 0-9 * * 4",
        "45 20 * * *",
    ):
        assert expected in lines, expected
    assert "juristid-nightly-cycle.sh" not in lines


def test_the_transition_leaves_exactly_one_nightly_schedule(dynamix: Path) -> None:
    result = _run(dynamix, "apply")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = _schedule_lines(*_cron_files(dynamix))
    assert lines.count(CYCLE_LINE) == 1
    text = "\n".join(lines)
    # The replaced daytime and per-job lines are gone ...
    for gone in (
        "15 21,22 * * * bash",
        "watch_registers",
        "sweep_registers",
        "watch_government_agenda",
    ):
        assert gone not in text, gone
    # ... the backup, the read-only monitors, the on-demand processor and the
    # neighbour stay.
    for kept in (
        "2-59/5 * * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/"
        "juristid-lovable-sync.sh --on-demand",
        "45 20 * * * bash /mnt/user/appdata/juristid-main/repo/deploy/unraid-main/cron/"
        "juristid-backup-nightly.sh",
        "--tier fast",
        "--tier daily",
        "--tier weekly",
        "/mnt/user/appdata/other/backup.sh",
    ):
        assert kept in text, kept
    # update_cron reads only *.cron: the kept copies are not schedules.
    assert (dynamix / "juristid-lovable-sync.cron.pre-0229").exists()
    assert (dynamix / "juristid-register-watch.cron.pre-0229").exists()
    assert (dynamix / "juristid.cron").read_bytes() == (FIXTURE / "juristid.cron").read_bytes()


def test_the_rollback_restores_the_host_schedule_byte_for_byte(dynamix: Path) -> None:
    before = _snapshot(dynamix)
    assert _run(dynamix, "apply").returncode == 0
    assert _snapshot(dynamix) != before
    result = _run(dynamix, "rollback")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _snapshot(dynamix) == before


def test_applying_twice_is_refused_and_changes_nothing(dynamix: Path) -> None:
    assert _run(dynamix, "apply").returncode == 0
    after_first = _snapshot(dynamix)
    again = _run(dynamix, "apply")
    assert again.returncode == 1
    assert "REFUSED" in again.stderr
    assert _snapshot(dynamix) == after_first


def test_a_rollback_without_the_copies_is_refused(dynamix: Path) -> None:
    before = _snapshot(dynamix)
    result = _run(dynamix, "rollback")
    assert result.returncode == 1 and "REFUSED" in result.stderr
    assert _snapshot(dynamix) == before


def test_a_schedule_that_already_runs_the_cycle_is_refused(dynamix: Path) -> None:
    (dynamix / "copied-by-hand.cron").write_text(CYCLE_LINE + "\n", encoding="utf-8")
    before = _snapshot(dynamix)
    result = _run(dynamix, "apply")
    assert result.returncode == 1 and "already runs the cycle" in result.stderr
    assert _snapshot(dynamix) == before


def test_every_cron_source_in_the_repository_holds_one_cycle_line() -> None:
    """Following the instructions cannot install the cycle twice: the example only
    points to the standalone file."""
    sources = [
        p
        for p in (ROOT / "deploy").rglob("*")
        if p.is_file() and (p.suffix == ".cron" or p.name.endswith(".cron.example"))
    ]
    assert NIGHTLY_CRON in sources
    lines = [line for line in _schedule_lines(*sources) if "juristid-nightly-cycle.sh" in line]
    assert lines == [CYCLE_LINE]


def test_the_example_no_longer_schedules_what_the_cycle_runs() -> None:
    example = ROOT / "deploy" / "unraid-main" / "cron" / "juristid.cron.example"
    scheduled = "\n".join(_schedule_lines(example))
    for command in (
        "watch_registers",
        "sweep_registers",
        "watch_government_agenda",
        "juristid-nightly-cycle.sh",
    ):
        assert command not in scheduled, command
