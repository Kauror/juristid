"""The nightly cycle's cron change and its rollback, offline (ADR 0229 §7, §8).

`deploy/unraid-main/cron/juristid-nightly-transition.sh` runs on a copy of the
host's Juristid cron files as they stood on 25.11.2026
(`tests/fixtures/nightly_cycle/dynamix/`: read from the host, paths only, no
secret; `other.cron` is a synthetic neighbour). Never the host.

The failure-injection matrix stops the script after every filesystem operation
(`JURISTID_TRANSITION_FAIL_AFTER=N`) and proves that the state it leaves is named
by the journal, that a second `--apply` finishes it, and that `--rollback`
restores the originals byte for byte, with their mode and mtime.
"""

from __future__ import annotations

import hashlib
import os
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
SYNC = "juristid-lovable-sync.cron"
WATCH = "juristid-register-watch.cron"
CYCLE = "juristid-nightly.cron"
JOURNAL = "juristid-nightly-transition.journal"
UNRELATED = ("juristid.cron", "other.cron")
# A distinctive past mtime, so "the rollback keeps the mtime" means something.
FIXTURE_MTIME = 1_700_000_000

# Every filesystem operation of --apply, in order (JURISTID_TRANSITION_FAIL_AFTER=N
# stops right after the N-th). The journal step each interruption leaves behind.
APPLY_OPERATIONS = [
    ("journal STEP=PREPARING", "PREPARING"),
    (f"copy {SYNC}.pre-0229", "PREPARING"),
    (f"copy {WATCH}.pre-0229", "PREPARING"),
    (f"stage {SYNC}.new-0229", "PREPARING"),
    (f"stage {CYCLE}.new-0229", "PREPARING"),
    ("journal STEP=BACKED_UP", "BACKED_UP"),
    (f"replace {SYNC}", "BACKED_UP"),
    ("journal STEP=SYNC_REPLACED", "SYNC_REPLACED"),
    (f"remove {WATCH}", "SYNC_REPLACED"),
    ("journal STEP=WATCH_REMOVED", "WATCH_REMOVED"),
    (f"install {CYCLE}", "WATCH_REMOVED"),
    ("journal STEP=APPLIED", "APPLIED"),
]
# Every filesystem operation of --rollback after a complete apply.
ROLLBACK_OPERATIONS = [
    "journal STEP=ROLLING_BACK",
    f"remove {CYCLE}",
    f"copy {WATCH}.pre-0229 to {WATCH}.restore-0229",
    f"restore {WATCH}",
    f"copy {SYNC}.pre-0229 to {SYNC}.restore-0229",
    f"restore {SYNC}",
    f"remove {SYNC}.pre-0229",
    f"remove {WATCH}.pre-0229",
]


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


def _metadata(directory: Path) -> dict[str, tuple[str, int, int, int]]:
    """sha256, size, mode and mtime of every file — what "byte for byte" means here."""
    out = {}
    for p in sorted(directory.iterdir()):
        st = p.stat()
        out[p.name] = (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            st.st_size,
            st.st_mode,
            int(st.st_mtime),
        )
    return out


def _run(
    directory: Path, action: str, *extra: str, fail_after: int | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("JURISTID_TRANSITION_FAIL_AFTER", None)
    if fail_after is not None:
        env["JURISTID_TRANSITION_FAIL_AFTER"] = str(fail_after)
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository script
        [
            BASH,
            str(SCRIPT),
            "--cron-dir",
            str(directory),
            "--nightly-cron",
            str(NIGHTLY_CRON),
            f"--{action}",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _journal(directory: Path) -> dict[str, str]:
    text = (directory / JOURNAL).read_text(encoding="utf-8")
    return dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#")
    )


def _fp(path: Path) -> str:
    data = path.read_bytes()
    return f"{hashlib.sha256(data).hexdigest()} {len(data)}"


@pytest.fixture
def dynamix(tmp_path: Path) -> Path:
    directory = tmp_path / "dynamix"
    shutil.copytree(FIXTURE, directory)
    for p in directory.iterdir():
        os.utime(p, (FIXTURE_MTIME, FIXTURE_MTIME))
    return directory


@pytest.fixture(scope="module")
def applied_reference(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """The directory after one uninterrupted apply, without the journal (its time differs)."""
    directory = tmp_path_factory.mktemp("reference") / "dynamix"
    shutil.copytree(FIXTURE, directory)
    assert _run(directory, "apply").returncode == 0
    snap = _snapshot(directory)
    snap.pop(JOURNAL)
    return snap


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
    unrelated_before = {k: v for k, v in _metadata(dynamix).items() if k in UNRELATED}
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
    # update_cron reads only *.cron: the kept copies and the journal are not schedules.
    assert (dynamix / f"{SYNC}.pre-0229").read_bytes() == (FIXTURE / SYNC).read_bytes()
    assert (dynamix / f"{WATCH}.pre-0229").read_bytes() == (FIXTURE / WATCH).read_bytes()
    assert (dynamix / "juristid.cron").read_bytes() == (FIXTURE / "juristid.cron").read_bytes()
    assert {k: v for k, v in _metadata(dynamix).items() if k in UNRELATED} == unrelated_before
    # No staged or half-restored file is left behind.
    assert not list(dynamix.glob("*.new-0229")) and not list(dynamix.glob("*.restore-0229"))


def test_the_journal_records_the_originals_and_the_installed_files(dynamix: Path) -> None:
    originals = {SYNC: _fp(dynamix / SYNC), WATCH: _fp(dynamix / WATCH)}
    assert _run(dynamix, "apply").returncode == 0
    journal = _journal(dynamix)
    assert journal["VERSION"] == "1"
    assert journal["STEP"] == "APPLIED"
    assert journal["ORIG_SYNC"] == originals[SYNC]
    assert journal["ORIG_WATCH"] == originals[WATCH]
    assert journal["NEW_SYNC"] == _fp(dynamix / SYNC)
    assert journal["NEW_CYCLE"] == _fp(dynamix / CYCLE) == _fp(NIGHTLY_CRON)


def test_the_rollback_restores_the_host_schedule_byte_for_byte(dynamix: Path) -> None:
    before = _metadata(dynamix)
    assert _run(dynamix, "apply").returncode == 0
    assert _metadata(dynamix) != before
    result = _run(dynamix, "rollback")
    assert result.returncode == 0, result.stdout + result.stderr
    # sha256, size, mode and mtime of every file; no copy, journal or staged file left.
    assert _metadata(dynamix) == before


def test_applying_twice_is_refused_and_changes_nothing(dynamix: Path) -> None:
    assert _run(dynamix, "apply").returncode == 0
    after_first = _snapshot(dynamix)
    again = _run(dynamix, "apply")
    assert again.returncode == 1
    assert "REFUSED" in again.stderr and "already applied" in again.stderr
    assert _snapshot(dynamix) == after_first


def test_a_rollback_without_the_copies_is_refused(dynamix: Path) -> None:
    before = _snapshot(dynamix)
    result = _run(dynamix, "rollback")
    assert (
        result.returncode == 1
        and "REFUSED" in result.stderr
        and "nothing to roll back" in result.stderr
    )
    assert _snapshot(dynamix) == before


def test_copies_without_a_journal_are_refused_both_ways(dynamix: Path) -> None:
    shutil.copy2(dynamix / SYNC, dynamix / f"{SYNC}.pre-0229")
    before = _snapshot(dynamix)
    for action in ("apply", "rollback"):
        result = _run(dynamix, action)
        assert result.returncode == 1 and "REFUSED" in result.stderr, action
        assert "not left by this script" in result.stderr, action
        assert _snapshot(dynamix) == before


def test_force_recover_belongs_to_the_rollback_only(dynamix: Path) -> None:
    before = _snapshot(dynamix)
    result = _run(dynamix, "apply", "--force-recover")
    assert result.returncode == 64
    assert _snapshot(dynamix) == before


def test_a_schedule_that_already_runs_the_cycle_is_refused(dynamix: Path) -> None:
    (dynamix / "copied-by-hand.cron").write_text(CYCLE_LINE + "\n", encoding="utf-8")
    before = _snapshot(dynamix)
    result = _run(dynamix, "apply")
    assert result.returncode == 1 and "already runs the cycle" in result.stderr
    assert _snapshot(dynamix) == before


# ---- rollback refuses unrelated later edits ----


def test_the_rollback_refuses_a_later_edit_of_the_sync_file_and_says_what_changed(
    dynamix: Path,
) -> None:
    assert _run(dynamix, "apply").returncode == 0
    with (dynamix / SYNC).open("a", encoding="utf-8") as fh:
        fh.write("7 7 * * * echo added-after-the-apply\n")
    before = _snapshot(dynamix)
    result = _run(dynamix, "rollback")
    assert result.returncode == 1
    assert "REFUSED" in result.stderr and "--force-recover" in result.stderr
    assert f"{SYNC} is neither the original nor as the apply installed it" in result.stderr
    assert "+7 7 * * * echo added-after-the-apply" in result.stderr  # the diff
    assert _snapshot(dynamix) == before


@pytest.mark.parametrize("edited", [WATCH, CYCLE])
def test_the_rollback_refuses_a_recreated_watch_or_an_edited_cycle_file(
    dynamix: Path, edited: str
) -> None:
    assert _run(dynamix, "apply").returncode == 0
    (dynamix / edited).write_text("5 5 * * * echo by-hand\n", encoding="utf-8")
    before = _snapshot(dynamix)
    result = _run(dynamix, "rollback")
    assert result.returncode == 1 and "REFUSED" in result.stderr
    changed = [
        line for line in result.stderr.splitlines() if line.startswith("CHANGED SINCE THE APPLY:")
    ]
    assert len(changed) == 1 and f"/{edited} " in changed[0]
    assert _snapshot(dynamix) == before


def test_force_recover_keeps_the_edit_beside_and_restores_the_originals(dynamix: Path) -> None:
    original = _metadata(dynamix)
    assert _run(dynamix, "apply").returncode == 0
    with (dynamix / SYNC).open("a", encoding="utf-8") as fh:
        fh.write("7 7 * * * echo added-after-the-apply\n")
    edited_bytes = (dynamix / SYNC).read_bytes()
    result = _run(dynamix, "rollback", "--force-recover")
    assert result.returncode == 0, result.stdout + result.stderr
    kept = list(dynamix.glob(f"{SYNC}.edited-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == edited_bytes
    now = _metadata(dynamix)
    del now[kept[0].name]
    assert now == original


# ---- failure injection ----


def test_the_injection_matrix_covers_every_operation(dynamix: Path) -> None:
    """One more than the listed operations runs to the end: the lists are complete."""
    result = _run(dynamix, "apply", fail_after=len(APPLY_OPERATIONS) + 1)
    assert result.returncode == 0 and "INJECTED" not in result.stderr
    result = _run(dynamix, "rollback", fail_after=len(ROLLBACK_OPERATIONS) + 1)
    assert result.returncode == 0 and "INJECTED" not in result.stderr


def _interrupt_apply(dynamix: Path, n: int) -> None:
    name, step = APPLY_OPERATIONS[n - 1]
    result = _run(dynamix, "apply", fail_after=n)
    assert result.returncode == 99, result.stdout + result.stderr
    assert f"INJECTED FAILURE after operation {n} ({name})" in result.stderr
    # Identifiable: the journal names the step reached, and the message says what to run.
    assert "INTERRUPTED: --apply stopped" in result.stderr and "Re-run --apply" in result.stderr
    assert _journal(dynamix)["STEP"] == step


@pytest.mark.parametrize(
    "n", range(1, len(APPLY_OPERATIONS) + 1), ids=[op for op, _ in APPLY_OPERATIONS]
)
def test_an_interrupted_apply_is_finished_by_applying_again(
    dynamix: Path, n: int, applied_reference: dict[str, bytes]
) -> None:
    original = _metadata(dynamix)
    unrelated = {k: v for k, v in original.items() if k in UNRELATED}
    _interrupt_apply(dynamix, n)
    assert {k: v for k, v in _metadata(dynamix).items() if k in UNRELATED} == unrelated
    # No live file changes before the backup set is complete and verified.
    if APPLY_OPERATIONS[n - 1][1] == "PREPARING":
        for name in (SYNC, WATCH):
            assert _metadata(dynamix)[name] == original[name]
        assert not (dynamix / CYCLE).exists()
    again = _run(dynamix, "apply")
    if n == len(APPLY_OPERATIONS):  # the journal already says APPLIED
        assert again.returncode == 1 and "already applied" in again.stderr
    else:
        assert again.returncode == 0, again.stdout + again.stderr
    snap = _snapshot(dynamix)
    assert _journal(dynamix)["STEP"] == "APPLIED"
    snap.pop(JOURNAL)
    assert snap == applied_reference
    assert _schedule_lines(*_cron_files(dynamix)).count(CYCLE_LINE) == 1
    assert _run(dynamix, "rollback").returncode == 0
    assert _metadata(dynamix) == original


@pytest.mark.parametrize(
    "n", range(1, len(APPLY_OPERATIONS) + 1), ids=[op for op, _ in APPLY_OPERATIONS]
)
def test_an_interrupted_apply_is_undone_by_the_rollback_even_when_that_is_interrupted(
    dynamix: Path, n: int
) -> None:
    original = _metadata(dynamix)
    _interrupt_apply(dynamix, n)
    # The rollback itself stops after its second operation, then finishes on a re-run.
    first = _run(dynamix, "rollback", fail_after=2)
    if first.returncode == 99:
        assert (
            "INTERRUPTED: --rollback stopped" in first.stderr
            and "Re-run --rollback" in first.stderr
        )
        assert _journal(dynamix)["STEP"] == "ROLLING_BACK"
        refused = _run(dynamix, "apply")
        assert refused.returncode == 1 and "finish it first: re-run --rollback" in refused.stderr
        second = _run(dynamix, "rollback")
        assert second.returncode == 0, second.stdout + second.stderr
    else:
        assert first.returncode == 0, first.stdout + first.stderr
    assert _metadata(dynamix) == original


@pytest.mark.parametrize("n", range(1, len(ROLLBACK_OPERATIONS) + 1), ids=ROLLBACK_OPERATIONS)
def test_an_interrupted_rollback_is_finished_by_rolling_back_again(dynamix: Path, n: int) -> None:
    original = _metadata(dynamix)
    assert _run(dynamix, "apply").returncode == 0
    result = _run(dynamix, "rollback", fail_after=n)
    assert result.returncode == 99
    assert f"INJECTED FAILURE after operation {n} ({ROLLBACK_OPERATIONS[n - 1]})" in result.stderr
    assert _journal(dynamix)["STEP"] == "ROLLING_BACK"
    assert {k: v for k, v in _metadata(dynamix).items() if k in UNRELATED} == {
        k: v for k, v in original.items() if k in UNRELATED
    }
    refused = _run(dynamix, "apply")
    assert refused.returncode == 1 and "finish it first" in refused.stderr
    again = _run(dynamix, "rollback")
    assert again.returncode == 0, again.stdout + again.stderr
    assert _metadata(dynamix) == original


def test_a_partial_copy_before_the_backup_is_verified_is_made_again(dynamix: Path) -> None:
    original = _metadata(dynamix)
    _interrupt_apply(dynamix, 2)  # the sync copy written, the journal still PREPARING
    (dynamix / f"{SYNC}.pre-0229").write_bytes((dynamix / SYNC).read_bytes()[:100])  # a torn copy
    assert _run(dynamix, "apply").returncode == 0
    assert (dynamix / f"{SYNC}.pre-0229").read_bytes() == (FIXTURE / SYNC).read_bytes()
    assert _run(dynamix, "rollback").returncode == 0
    assert _metadata(dynamix) == original


def test_a_damaged_verified_copy_blocks_both_ways_without_changing_anything(dynamix: Path) -> None:
    _interrupt_apply(dynamix, 7)  # the sync file replaced; its original is only the copy
    (dynamix / f"{SYNC}.pre-0229").write_bytes(b"damaged\n")
    before = _snapshot(dynamix)
    resumed = _run(dynamix, "apply")
    assert resumed.returncode == 1 and "no longer matches the journal" in resumed.stderr
    rolled = _run(dynamix, "rollback")
    assert rolled.returncode == 1 and f"cannot restore {SYNC}" in rolled.stderr
    assert _snapshot(dynamix) == before


def test_a_live_edit_after_an_interrupted_apply_is_refused_on_resume(dynamix: Path) -> None:
    _interrupt_apply(dynamix, 3)
    with (dynamix / SYNC).open("a", encoding="utf-8") as fh:
        fh.write("# edited\n")
    before = _snapshot(dynamix)
    result = _run(dynamix, "apply")
    assert result.returncode == 1 and "edited since" in result.stderr
    assert _snapshot(dynamix) == before


def test_a_draft_journal_alone_is_cleared_by_the_rollback(dynamix: Path) -> None:
    original = _metadata(dynamix)
    (dynamix / f"{JOURNAL}.tmp").write_text("VERSION=1\n", encoding="utf-8")
    result = _run(dynamix, "rollback")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _metadata(dynamix) == original


# ---- the repository's cron sources ----


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
