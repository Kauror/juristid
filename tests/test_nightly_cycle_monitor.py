"""The daily monitor reads the nightly cycle's state (ADR 0229 §6).

Driven like tests/test_secres_monitor.py (stub `docker`, `curl`, `notify`), with
`MONITOR_NIGHTLY_DIR` pointing at a synthetic `nightly/` and the clock fixed by
`JURISTID_MONITOR_NOW`. Synthetic state only.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from tests.test_secres_monitor import (  # noqa: F401
    BASH,
    _fresh_set,
    _run,
    _sent,
    _stamp,
    world,
)

#: 04:05 UTC on 15.01.2026 — the daily tier's time — judges the night of 15.01
#: (its cycle began at 00:00 Tallinn = 22:00Z on the 14th).
MORNING = dt.datetime(2026, 1, 15, 4, 5, tzinfo=dt.UTC)
NIGHT = "2026-01-15"


@pytest.fixture
def nightly(world: dict, tmp_path: Path) -> dict:  # noqa: F811 (the imported fixture)
    directory = tmp_path / "nightly"
    (directory / "state").mkdir(parents=True)
    with world["env_file"].open("a", encoding="utf-8", newline="\n") as env:
        env.write(f"MONITOR_NIGHTLY_DIR={directory}\n")
    world["nightly"] = directory
    _fresh_set(world, _stamp(1))
    return world


def _daily(mon: dict, now: dt.datetime = MORNING) -> subprocess.CompletedProcess[str]:
    return _run(mon, "--tier", "daily", JURISTID_MONITOR_NOW=str(int(now.timestamp())))


def _state(mon: dict, name: str, text: str) -> None:
    (mon["nightly"] / "state" / name).write_text(text, encoding="utf-8", newline="\n")


def _nightly_lines(mon: dict) -> list[str]:
    return [line for line in _sent(mon) if " nightly | " in line]


GOOD = (
    f"cycle {NIGHT} (Tallinn night {NIGHT}), started 2026-01-14T22:00:01Z, ended x\n"
    "lovable            SUCCEEDED_NO_NEW    rc=0 300s -\n"
    "register-watch     SUCCEEDED           rc=0 700s -\n"
    "riigikogu          SKIPPED_DELIBERATE  rc=- 0s NOT_INSTALLED\n"
    "register-sweep     SUCCEEDED_NO_NEW    rc=0 60s -\n"
    "government-agenda  SUCCEEDED_NO_NEW    rc=0 5s -\n"
    "verdict ok\n"
)


def test_a_good_night_is_ok_and_sends_nothing(nightly: dict) -> None:
    _state(nightly, f"{NIGHT}.started", "started=x pid=1\n")
    _state(nightly, f"{NIGHT}.completed", GOOD)
    result = _daily(nightly)
    assert result.returncode == 0, result.stderr
    assert f"ok nightly: night {NIGHT} completed" in result.stdout
    assert _nightly_lines(nightly) == []


def test_a_night_that_never_started_is_reported(nightly: dict) -> None:
    _daily(nightly)
    sent = _nightly_lines(nightly)
    assert len(sent) == 1 and "never started" in sent[0] and NIGHT in sent[0]


def test_an_interrupted_night_is_reported_with_how_far_it_got(nightly: dict) -> None:
    _state(nightly, f"{NIGHT}.started", "started=2026-01-14T22:00:01Z pid=999999\n")
    _state(nightly, f"{NIGHT}.outcomes", "lovable            SUCCEEDED rc=0 300s -\n")
    _daily(nightly)
    sent = _nightly_lines(nightly)
    assert len(sent) == 1
    assert "interrupted" in sent[0] and "lovable SUCCEEDED" in sent[0]


def test_a_night_still_running_past_its_time_is_overdue(nightly: dict) -> None:
    # A living process as the shell sees it (the cycle, still going).
    alive = subprocess.run(  # noqa: S603
        [BASH, "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    _state(nightly, f"{NIGHT}.started", f"started=2026-01-14T22:00:01Z pid={alive}\n")
    _daily(nightly)
    sent = _nightly_lines(nightly)
    # 04:05 UTC in winter is 6 h 05 after the cycle began: past the 6-hour
    # completion (the sweep's finish-by, after the Riigikogu step).
    assert len(sent) == 1 and "overdue" in sent[0] and "past 6h" in sent[0]


@pytest.mark.parametrize(
    ("step_line", "named"),
    [
        (
            "register-watch     PARTIAL             rc=0 700s source errors",
            "register-watch PARTIAL",
        ),
        ("register-sweep     SKIPPED_UNEXPECTED  rc=- 0s NEEDS register-watch", "register-sweep"),
        ("lovable            FAILED              rc=1 30s exit 1", "lovable FAILED"),
        ("register-watch     INTERRUPTED         rc=- 0s did not report", "INTERRUPTED"),
    ],
)
def test_a_completed_night_with_a_problem_step_is_reported(nightly, step_line, named) -> None:
    _state(nightly, f"{NIGHT}.completed", GOOD.replace("verdict ok\n", step_line + "\nverdict x\n"))
    _daily(nightly)
    sent = _nightly_lines(nightly)
    assert len(sent) == 1 and named in sent[0]


def test_a_refused_night_names_the_release(nightly: dict) -> None:
    _state(nightly, f"{NIGHT}.refused", "refused 2026-01-14T22:00:01Z held by: owner=release-d2\n")
    _daily(nightly)
    sent = _nightly_lines(nightly)
    assert len(sent) == 1 and "refused" in sent[0] and "release-d2" in sent[0]


def test_a_paused_cycle_and_a_manual_recovery_are_ok(nightly: dict) -> None:
    (nightly["nightly"] / "PAUSED").write_text("", encoding="utf-8")
    assert "ok nightly: the nightly cycle is paused" in _daily(nightly).stdout
    (nightly["nightly"] / "PAUSED").unlink()
    _state(nightly, f"{NIGHT}.started", "started=x pid=999999\n")
    _state(nightly, f"{NIGHT}.recovered", GOOD)
    assert "recovered by an authorised manual run" in _daily(nightly).stdout
    assert _nightly_lines(nightly) == []


def test_the_summer_night_is_judged_by_tallinn_time(nightly: dict) -> None:
    # 04:05 UTC on 02.07.2026 is 07:05 in Tallinn: the night of 02.07 (21:00Z on 01.07).
    _state(nightly, "2026-07-02.completed", GOOD)
    result = _daily(nightly, dt.datetime(2026, 7, 2, 4, 5, tzinfo=dt.UTC))
    assert "ok nightly: night 2026-07-02 completed" in result.stdout


def test_without_the_setting_the_check_is_silent(world: dict) -> None:  # noqa: F811
    _fresh_set(world, _stamp(1))
    result = _run(world, "--tier", "daily")
    assert " nightly:" not in result.stdout


@pytest.mark.parametrize(
    ("morning", "night"),
    [
        # Clocks forward on 29.03.2026 at 01:00Z: the cycle began 22:00Z on the 28th
        # (UTC+2) and was due complete at 04:00Z; 04:05Z is 07:05 in Tallinn.
        (dt.datetime(2026, 3, 29, 4, 5, tzinfo=dt.UTC), "2026-03-29"),
        # Clocks back on 25.10.2026 at 01:00Z: the cycle began 21:00Z on the 24th
        # (UTC+3) and was due complete at 03:00Z; 04:05Z is 06:05 in Tallinn.
        (dt.datetime(2026, 10, 25, 4, 5, tzinfo=dt.UTC), "2026-10-25"),
        # The first winter and summer nights after each change.
        (dt.datetime(2026, 10, 26, 4, 5, tzinfo=dt.UTC), "2026-10-26"),
        (dt.datetime(2026, 3, 30, 4, 5, tzinfo=dt.UTC), "2026-03-30"),
    ],
)
def test_the_night_a_clock_changes_is_judged_as_that_night(nightly, morning, night) -> None:
    _state(nightly, f"{night}.completed", GOOD)
    result = _daily(nightly, morning)
    assert f"ok nightly: night {night} completed" in result.stdout
    assert _nightly_lines(nightly) == []


def test_a_cycle_still_inside_its_six_hours_is_not_judged_yet(nightly: dict) -> None:
    """A look before the night is due complete (here 03:30Z in winter, 5 h 30 in)
    judges the night before, never tonight's cycle as overdue."""
    alive = subprocess.run(  # noqa: S603
        [BASH, "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    _state(nightly, f"{NIGHT}.started", f"started=2026-01-14T22:00:01Z pid={alive}\n")
    _state(nightly, "2026-01-14.completed", GOOD)
    result = _daily(nightly, dt.datetime(2026, 1, 15, 3, 30, tzinfo=dt.UTC))
    assert "ok nightly: night 2026-01-14 completed" in result.stdout
    assert _nightly_lines(nightly) == []
