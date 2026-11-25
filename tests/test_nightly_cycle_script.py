"""`scripts/deploy/juristid-nightly-cycle.sh`, run for real against a synthetic host.

The coordinated nightly cycle (docs/adr/0229) starts the night's jobs that cron
used to start one by one. These run the script itself with a fake `docker` (which
records every step and answers `nightly_outcome` from small files), a fake Lovable
sync script under the synthetic appdata, and a fake `flock` that really excludes:
it keys a lock directory on the file behind the descriptor and its holder's pid, so
two processes — the cycle and a release — contend as they would on the host. The
clock is fixed (`JURISTID_NIGHTLY_NOW`). Nothing reaches Docker, Lovable, a register
or a database. All data here is synthetic.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPT = ROOT / "scripts" / "deploy" / "juristid-nightly-cycle.sh"
LIB = ROOT / "scripts" / "deploy" / "lib.sh"
BASH = shutil.which("bash") or "bash"
TALLINN = ZoneInfo("Europe/Tallinn")

# `docker exec … manage.py <command>`: recorded, answered with `rc-<command>`;
# `nightly_outcome --step S` prints `outcome-<S>` (a JSON object) or SUCCEEDED.
# `block-<command>` makes a command wait until that file is removed (≤ 20 s).
FAKE_DOCKER = r"""#!/usr/bin/env bash
[ "$1" = exec ] || exit 97
args=("$@"); cmd=""; step=""
for ((i = 0; i < ${#args[@]}; i++)); do
  [ "${args[$i]}" = manage.py ] && cmd="${args[$((i + 1))]}"
  [ "${args[$i]}" = --step ] && step="${args[$((i + 1))]}"
done
if [ "$cmd" = nightly_outcome ]; then
  echo "ask $step" >> "$FAKE_DIR/calls"
  echo "$step ${args[*]}" >> "$FAKE_DIR/asks"
  [ -e "$FAKE_DIR/no-outcome" ] && exit 1
  if [ -e "$FAKE_DIR/outcome-$step" ]; then body="$(cat "$FAKE_DIR/outcome-$step")"
  else body='{"outcome": "SUCCEEDED", "reason": "", "step": "'"$step"'"}'; fi
  echo "NIGHTLY-OUTCOME $body"
  exit 0
fi
echo "start $cmd" >> "$FAKE_DIR/calls"
# The lane that started this command dies with it (the host killed it).
[ -e "$FAKE_DIR/kill-lane-$cmd" ] && kill -9 "$PPID"
[ "$cmd" = watch_government_agenda ] && touch "$FAKE_DIR/agenda-started"
if [ "$cmd" = watch_registers ] && [ -e "$FAKE_DIR/wait-for-agenda" ]; then
  for _ in $(seq 1 100); do [ -e "$FAKE_DIR/agenda-started" ] && break; sleep 0.1; done
  [ -e "$FAKE_DIR/agenda-started" ] || exit 3
fi
for _ in $(seq 1 200); do [ -e "$FAKE_DIR/block-$cmd" ] || break; sleep 0.1; done
echo "ran $cmd"
[ "$cmd" = watch_government_agenda ] && echo 'OUTCOME-JSON {"errors": 0, "judged": 0, "stored": 0}'
echo "end $cmd" >> "$FAKE_DIR/calls"
if [ -e "$FAKE_DIR/rc-$cmd" ]; then exit "$(cat "$FAKE_DIR/rc-$cmd")"; fi
exit 0
"""
# A flock that excludes across processes: the lock is a directory beside the file
# behind the descriptor, owned by the pid that holds the descriptor (our parent).
# A dead holder's lock is free, as the kernel's would be. -n, -w SECONDS and -u.
FAKE_FLOCK = r"""#!/usr/bin/env bash
nb=0; wait=0; unlock=0
while [ $# -gt 1 ]; do
  case "$1" in -n) nb=1 ;; -w) wait="$2"; shift ;; -u) unlock=1 ;; esac; shift
done
path="$(readlink "/proc/self/fd/$1")" || exit 2
held="$path.held"
if [ "$unlock" = 1 ]; then rm -rf "$held"; exit 0; fi
deadline=$((SECONDS + ${wait%.*}))
while :; do
  if mkdir "$held" 2>/dev/null; then echo "$PPID" > "$held/pid"; exit 0; fi
  holder="$(cat "$held/pid" 2>/dev/null || true)"
  [ "$holder" = "$PPID" ] && exit 0
  # A dead holder (or a zombie nobody has reaped yet) holds nothing.
  if [ -n "$holder" ] && { ! kill -0 "$holder" 2>/dev/null ||
    [ "$(awk '{print $3}' "/proc/$holder/stat" 2>/dev/null)" = Z ]; }; then
    rm -rf "$held"; continue
  fi
  [ "$nb" = 1 ] && exit 1
  [ "$SECONDS" -ge "$deadline" ] && exit 1
  sleep 0.2
done
"""
FAKE_SYNC = r"""#!/usr/bin/env bash
echo "start lovable $*" >> "$FAKE_DIR/calls"
for _ in $(seq 1 200); do [ -e "$FAKE_DIR/block-lovable" ] || break; sleep 0.1; done
echo "end lovable" >> "$FAKE_DIR/calls"
if [ -e "$FAKE_DIR/rc-lovable" ]; then exit "$(cat "$FAKE_DIR/rc-lovable")"; fi
exit 0
"""

# 2026-01-14 is a Wednesday in winter (UTC+2); 2026-07-01 a Wednesday in summer.
WINTER_MIDNIGHT = "2026-01-14T22:00:00Z"
WINTER_OTHER = "2026-01-14T21:00:00Z"
SUMMER_MIDNIGHT = "2026-07-01T21:00:00Z"
SUMMER_OTHER = "2026-07-01T22:00:00Z"
WINTER_NIGHT = "2026-01-15"


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _epoch(moment: str) -> str:
    return str(int(dt.datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp()))


@pytest.fixture
def host(tmp_path: Path) -> dict[str, Path]:
    appdata = tmp_path / "appdata"
    (appdata / "config").mkdir(parents=True)
    (appdata / "config" / "register-watch.env").write_text("REGISTER=x\n", encoding="utf-8")
    _executable(appdata / "repo" / "scripts" / "deploy" / "juristid-lovable-sync.sh", FAKE_SYNC)
    bin_dir = tmp_path / "bin"
    _executable(bin_dir / "docker", FAKE_DOCKER)
    _executable(bin_dir / "flock", FAKE_FLOCK)
    fake = tmp_path / "fake"
    fake.mkdir()
    return {
        "tmp": tmp_path,
        "appdata": appdata,
        "bin": bin_dir,
        "fake": fake,
        "nightly": appdata / "nightly",
        "state": appdata / "nightly" / "state",
        "lock": tmp_path / "host-lock",
        "cycle_lock": tmp_path / "cycle.lock",
    }


def _env(host: dict[str, Path], now: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{host['bin']}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DIR"] = str(host["fake"])
    env["JURISTID_NIGHTLY_APPDATA"] = str(host["appdata"])
    env["JURISTID_NIGHTLY_NOW"] = _epoch(now)
    env["JURISTID_CYCLE_LOCK"] = str(host["cycle_lock"])
    env["JURISTID_SYNC_LOCK"] = str(host["tmp"] / "sync.lock")
    env["JURISTID_HOST_LOCK"] = str(host["lock"])
    env["JURISTID_NIGHTLY_LOCK_WAIT_SECONDS"] = "2"
    env["JURISTID_NIGHTLY_LOCK_RETRY_SECONDS"] = "1"
    return env


def run(
    host: dict[str, Path], *arguments: str, now: str = WINTER_MIDNIGHT, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository script
        [BASH, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**_env(host, now), **extra},
    )


def start(host: dict[str, Path], *arguments: str, now: str = WINTER_MIDNIGHT) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603
        [BASH, str(SCRIPT), *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(host, now),
    )


def release_takes_the_cycle_lock(host: dict[str, Path], hold_until: Path | None = None):
    """What a release kit does (ADR 0229, kit snippet): `take_cycle_lock release`."""
    hold = f'while [ -e "{hold_until.as_posix()}" ]; do sleep 0.1; done' if hold_until else ":"
    script = (
        f'. "{LIB.as_posix()}"; rc=0; take_cycle_lock release-d2 || rc=$?; '
        f'echo "rc=$rc owner=$(cycle_lock_owner)"; {hold}; exit $rc'
    )
    return subprocess.Popen(  # noqa: S603
        [BASH, "-c", script],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(host, WINTER_MIDNIGHT),
    )


def _wait_for(path: Path, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.1)


def calls(host: dict[str, Path]) -> list[str]:
    path = host["fake"] / "calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def started(host: dict[str, Path]) -> list[str]:
    return [line.removeprefix("start ") for line in calls(host) if line.startswith("start ")]


def outcomes(host: dict[str, Path], night: str = WINTER_NIGHT) -> dict[str, str]:
    """`<night>.completed`: step → "OUTCOME rc=… …s reason"."""
    lines = (host["state"] / f"{night}.completed").read_text(encoding="utf-8").splitlines()
    return {
        line.split()[0]: " ".join(line.split()[1:])
        for line in lines
        if not line.startswith(("cycle ", "verdict "))
    }


def verdict(host: dict[str, Path], night: str = WINTER_NIGHT) -> str:
    text = (host["state"] / f"{night}.completed").read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("verdict "))


def _outcome(host: dict[str, Path], step: str, outcome: str, reason: str = "") -> None:
    (host["fake"] / f"outcome-{step}").write_text(
        json.dumps({"outcome": outcome, "reason": reason, "step": step}, sort_keys=True)
    )


# -- usage ------------------------------------------------------------------------


def test_the_script_explains_itself() -> None:
    result = subprocess.run(  # noqa: S603
        [BASH, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout


def test_an_unknown_argument_stops_the_script(host) -> None:
    result = run(host, "--with-riigkogu")
    assert result.returncode == 64
    assert "unknown argument" in result.stderr
    assert calls(host) == [] and not host["nightly"].exists()


def test_at_is_refused_outside_the_plan(host) -> None:
    result = run(host, "--at", WINTER_MIDNIGHT)
    assert result.returncode == 64
    assert calls(host) == []


# -- summer and winter -----------------------------------------------------------


def _moments() -> list[dt.datetime]:
    """Both daily firings on every day within three days of each change of
    offset in 2026-2030, and on the 1st and 15th of every month."""
    days: set[dt.date] = set()
    for year in range(2026, 2031):
        for month in (3, 10):
            last_sunday = max(
                dt.date(year, month, d)
                for d in range(25, 32)
                if dt.date(year, month, d).weekday() == 6
            )
            days.update(last_sunday + dt.timedelta(days=k) for k in range(-3, 4))
        days.update(dt.date(year, m, d) for m in range(1, 13) for d in (1, 15))
    return [
        dt.datetime.combine(day, dt.time(hour), tzinfo=dt.UTC)
        for day in sorted(days)
        for hour in (21, 22)
    ]


def test_the_tallinn_clock_agrees_with_the_zone_database() -> None:
    """The host has no Tallinn zone; lib.sh's EU rule must equal tzdata's."""
    moments = _moments()
    epochs = " ".join(str(int(m.timestamp())) for m in moments)
    result = subprocess.run(  # noqa: S603
        [
            BASH,
            "-c",
            f'. "$1"; for t in {epochs}; do tallinn_clock "$t"; done',
            "tallinn-clock",
            str(LIB),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    expected = []
    for moment in moments:
        local = moment.astimezone(TALLINN)
        offset = int(local.utcoffset().total_seconds() // 3600)  # type: ignore[union-attr]
        expected.append(f"{local:%Y-%m-%d %H:%M} +{offset}")
    assert result.stdout.splitlines() == expected


def test_exactly_one_firing_a_night_is_midnight_in_tallinn() -> None:
    by_night: dict[dt.date, list[dt.datetime]] = {}
    for moment in _moments():
        local = moment.astimezone(TALLINN)
        if local.hour == 0:
            by_night.setdefault(local.date(), []).append(moment)
    assert all(len(firings) == 1 for firings in by_night.values())
    assert by_night[dt.date(2026, 3, 29)] == [dt.datetime(2026, 3, 28, 22, tzinfo=dt.UTC)]
    assert by_night[dt.date(2026, 3, 30)] == [dt.datetime(2026, 3, 29, 21, tzinfo=dt.UTC)]
    assert by_night[dt.date(2026, 10, 25)] == [dt.datetime(2026, 10, 24, 21, tzinfo=dt.UTC)]
    assert by_night[dt.date(2026, 10, 26)] == [dt.datetime(2026, 10, 25, 22, tzinfo=dt.UTC)]


@pytest.mark.parametrize(
    ("moment", "decision"),
    [
        (WINTER_MIDNIGHT, "DUE"),
        (WINTER_OTHER, "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),
        (SUMMER_MIDNIGHT, "DUE"),
        (SUMMER_OTHER, "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),
        ("2026-03-28T22:00:00Z", "DUE"),  # the night the clocks go forward
        ("2026-03-29T22:00:00Z", "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),
        ("2026-10-24T22:00:00Z", "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),  # 01:00 summer time
        ("2026-10-25T21:00:00Z", "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"),  # 23:00 winter time
    ],
)
def test_the_plan_says_which_firing_is_the_night(host, moment, decision) -> None:
    result = run(host, "--plan", "--at", moment)
    assert result.returncode == 0, result.stderr
    assert f"Decision: {decision}" in result.stdout


@pytest.mark.parametrize("moment", [WINTER_OTHER, SUMMER_OTHER])
def test_the_firing_that_is_not_midnight_does_nothing_and_leaves_no_trace(host, moment) -> None:
    result = run(host, now=moment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(host) == []
    assert not host["nightly"].exists()
    assert not host["cycle_lock"].exists()


def test_the_summer_firing_runs_the_cycle(host) -> None:
    result = run(host, now=SUMMER_MIDNIGHT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (host["state"] / "2026-07-02.completed").exists()
    assert started(host)[0] == "lovable "


# -- order and isolation ----------------------------------------------------------


def test_a_night_runs_every_step_in_order_and_records_it(host) -> None:
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    order = started(host)
    assert order[0] == "lovable ", "the Lovable synchronisation runs first, alone"
    assert calls(host)[1] == "end lovable", "nothing starts before it has ended"
    assert set(order[1:]) == {"watch_registers", "sweep_registers", "watch_government_agenda"}
    assert order.index("watch_registers") < order.index("sweep_registers")
    steps = outcomes(host)
    assert all(steps[name].startswith("SUCCEEDED") for name in steps if name != "riigikogu")
    assert (
        steps["riigikogu"].startswith("SKIPPED_DELIBERATE")
        and "NOT_INSTALLED" in steps["riigikogu"]
    )
    assert verdict(host) == "verdict ok"
    assert (host["state"] / f"{WINTER_NIGHT}.started").exists()
    # Every step was classified from its record, not from its exit code.
    asked = [line.removeprefix("ask ") for line in calls(host) if line.startswith("ask ")]
    assert sorted(asked) == ["government-agenda", "lovable", "register-sweep", "register-watch"]
    # The commands still write to their own logs, as their cron lines did.
    assert "ran watch_registers" in (host["appdata"] / "register-watch.log").read_text()
    assert "ran sweep_registers" in (host["appdata"] / "register-sweep.log").read_text()
    assert "OUTCOME-JSON" in (host["appdata"] / "government-agenda.log").read_text()


def test_a_duplicate_firing_of_the_same_night_runs_nothing(host) -> None:
    assert run(host).returncode == 0
    before = len(calls(host))
    again = run(host)
    assert again.returncode == 0
    assert len(calls(host)) == before, "a second firing of the night refuses (ALREADY_STARTED)"


def test_two_firings_at_once_run_one_cycle(host) -> None:
    (host["fake"] / "block-lovable").write_text("")
    first = start(host)
    _wait_for(host["state"] / f"{WINTER_NIGHT}.started")
    # The marker is there, so a second firing leaves at once; and even one that
    # raced past it would find the cycle lock held by a cycle and leave quietly.
    (host["state"] / f"{WINTER_NIGHT}.started").rename(host["tmp"] / "aside")
    second = run(host)
    (host["tmp"] / "aside").rename(host["state"] / f"{WINTER_NIGHT}.started")
    (host["fake"] / "block-lovable").unlink()
    assert first.wait(timeout=120) == 0
    assert second.returncode == 0
    assert started(host).count("lovable ") == 1
    assert not (host["state"] / f"{WINTER_NIGHT}.refused").exists()


def test_the_agenda_lane_runs_beside_the_registers_lane(host) -> None:
    (host["fake"] / "wait-for-agenda").write_text("")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert outcomes(host)["register-watch"].startswith("SUCCEEDED")


def test_a_failing_step_does_not_stop_the_independent_ones(host) -> None:
    (host["fake"] / "rc-watch_government_agenda").write_text("1")
    _outcome(host, "government-agenda", "FAILED", "exit 1")
    result = run(host)
    assert result.returncode == 1
    steps = outcomes(host)
    assert steps["government-agenda"].startswith("FAILED") and "rc=1" in steps["government-agenda"]
    assert steps["register-watch"].startswith("SUCCEEDED")
    assert steps["register-sweep"].startswith("SUCCEEDED")
    assert verdict(host) == "verdict problems: government-agenda"


def test_source_errors_with_exit_zero_are_partial_not_success(host) -> None:
    _outcome(host, "register-watch", "PARTIAL", "source errors or model failures")
    result = run(host)
    assert result.returncode == 1
    steps = outcomes(host)
    assert steps["register-watch"].startswith("PARTIAL") and "rc=0" in steps["register-watch"]
    # A watch that finished with some registers unread still lets the sweep run.
    assert steps["register-sweep"].startswith("SUCCEEDED")


def test_a_lovable_exit_zero_without_a_run_is_unexpected(host) -> None:
    _outcome(host, "lovable", "SKIPPED_UNEXPECTED", "no run record")
    result = run(host)
    assert result.returncode == 1
    assert outcomes(host)["lovable"].startswith("SKIPPED_UNEXPECTED")
    assert outcomes(host)["register-watch"].startswith("SUCCEEDED")


def test_an_unanswered_outcome_question_is_a_failure(host) -> None:
    (host["fake"] / "no-outcome").write_text("")
    result = run(host)
    assert result.returncode == 1
    assert "OUTCOME_UNKNOWN" in outcomes(host)["register-watch"]


def test_a_failed_lovable_night_still_lets_the_monitoring_run(host) -> None:
    (host["fake"] / "rc-lovable").write_text("1")
    _outcome(host, "lovable", "FAILED", "exit 1")
    result = run(host)
    assert result.returncode == 1
    steps = outcomes(host)
    assert steps["lovable"].startswith("FAILED")
    for name in ("register-watch", "register-sweep", "government-agenda"):
        assert steps[name].startswith("SUCCEEDED"), name


def test_a_failed_watch_skips_the_sweep_and_says_why(host) -> None:
    (host["fake"] / "rc-watch_registers").write_text("1")
    _outcome(host, "register-watch", "FAILED", "exit 1")
    result = run(host)
    assert result.returncode == 1
    steps = outcomes(host)
    assert steps["register-sweep"].startswith("SKIPPED_UNEXPECTED")
    assert "NEEDS register-watch, which was FAILED" in steps["register-sweep"]
    assert "sweep_registers" not in started(host)
    assert steps["government-agenda"].startswith("SUCCEEDED")


def test_a_watch_off_on_purpose_skips_the_sweep_on_purpose(host) -> None:
    _outcome(host, "register-watch", "SKIPPED_DELIBERATE", "reading is off")
    result = run(host)
    assert result.returncode == 0
    assert outcomes(host)["register-sweep"].startswith("SKIPPED_DELIBERATE")


def test_the_riigikogu_step_runs_between_the_watch_and_the_sweep_when_asked(host) -> None:
    result = run(host, "--with-riigikogu")
    assert result.returncode == 0, result.stdout + result.stderr
    order = started(host)
    assert order.index("watch_registers") < order.index("watch_riigikogu")
    assert order.index("watch_riigikogu") < order.index("sweep_registers")


def test_a_failed_riigikogu_step_does_not_skip_the_sweep(host) -> None:
    (host["fake"] / "rc-watch_riigikogu").write_text("1")
    _outcome(host, "riigikogu", "FAILED", "exit 1")
    result = run(host, "--with-riigikogu")
    assert result.returncode == 1
    assert outcomes(host)["register-sweep"].startswith("SUCCEEDED")


# -- interrupted cycles ------------------------------------------------------------


def test_a_lane_that_dies_mid_step_is_interrupted_never_a_success(host) -> None:
    (host["fake"] / "kill-lane-watch_registers").write_text("")
    result = run(host)
    assert result.returncode == 1
    steps = outcomes(host)
    for name in ("register-watch", "riigikogu", "register-sweep"):
        assert steps[name].startswith("INTERRUPTED"), name
    assert steps["government-agenda"].startswith("SUCCEEDED")
    assert "register-watch" in verdict(host)


def test_a_night_that_started_and_never_completed_is_never_rerun(host) -> None:
    """The cycle died with the host: D.started and part of D.outcomes, no D.completed.
    Neither firing of that night, nor a later cron start, re-runs it over its
    partial writes; recovery is a person's --manual run."""
    host["state"].mkdir(parents=True)
    (host["state"] / f"{WINTER_NIGHT}.started").write_text("started=x pid=999999\n")
    (host["state"] / f"{WINTER_NIGHT}.outcomes").write_text("lovable SUCCEEDED rc=0 5s -\n")
    for moment in (WINTER_MIDNIGHT, "2026-01-14T22:30:00Z"):
        assert run(host, now=moment).returncode == 0
    assert calls(host) == []
    assert not (host["state"] / f"{WINTER_NIGHT}.completed").exists()


def test_a_manual_recovery_records_the_night_as_recovered(host) -> None:
    (host["state"]).mkdir(parents=True)
    (host["state"] / f"{WINTER_NIGHT}.started").write_text("started=x pid=1\n")
    result = run(host, "--manual", now="2026-01-15T06:00:00Z")
    assert result.returncode == 0, result.stdout + result.stderr
    assert started(host)[0] == "lovable --manual"
    asks = (host["fake"] / "asks").read_text(encoding="utf-8").splitlines()
    lovable = [line for line in asks if line.startswith("lovable ")]
    assert lovable and all(line.endswith("--manual") for line in lovable), lovable
    assert not [line for line in asks if not line.startswith("lovable ") and "--manual" in line]
    recovered = (host["state"] / f"{WINTER_NIGHT}.recovered").read_text(encoding="utf-8")
    assert "verdict ok" in recovered


# -- locks: deployments, the runner, the release lock -------------------------------


def test_a_release_cannot_start_while_the_cycle_runs_even_when_it_overruns(host) -> None:
    (host["fake"] / "block-watch_registers").write_text("")
    cycle = start(host)
    _wait_for(host["state"] / f"{WINTER_NIGHT}.started")
    # Long past the nominal window: the cycle still holds its lock.
    release = release_takes_the_cycle_lock(host)
    out, _ = release.communicate(timeout=60)
    assert release.returncode == 1, out
    assert "owner=nightly-cycle" in out
    (host["fake"] / "block-watch_registers").unlink()
    assert cycle.wait(timeout=120) == 0
    # Once the cycle has ended, the same release takes the lock.
    later = release_takes_the_cycle_lock(host)
    out, _ = later.communicate(timeout=60)
    assert later.returncode == 0, out


def test_the_cycle_refuses_to_start_under_a_release(host) -> None:
    hold = host["tmp"] / "release-running"
    hold.write_text("")
    release = release_takes_the_cycle_lock(host, hold_until=hold)
    _wait_for(host["cycle_lock"])
    deadline = time.monotonic() + 30
    while "release-d2" not in host["cycle_lock"].read_text(encoding="utf-8"):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    result = run(host)
    hold.unlink()
    release.communicate(timeout=60)
    assert result.returncode == 1
    assert calls(host) == [], "nothing runs under a release"
    refused = (host["state"] / f"{WINTER_NIGHT}.refused").read_text(encoding="utf-8")
    assert "release-d2" in refused
    assert not (host["state"] / f"{WINTER_NIGHT}.started").exists()


def test_a_held_release_lock_skips_the_steps_by_its_holder(host) -> None:
    host["lock"].mkdir()
    (host["lock"] / "owner").write_text("owner=release-d2 pid=1 2026-01-14T21:59:00Z\n")
    result = run(host)
    assert result.returncode == 1
    assert started(host) == [], "nothing runs under a release"
    steps = outcomes(host)
    assert steps["lovable"].startswith("SKIPPED_UNEXPECTED")
    assert "HOST_LOCK held after 2s by: owner=release-d2" in steps["lovable"]
    assert "HOST_LOCK" in steps["register-watch"] and "HOST_LOCK" in steps["government-agenda"]
    assert host["lock"].exists(), "the release's lock is never touched"


def test_a_release_kits_file_lock_holds_the_steps_too(host) -> None:
    """A release kit writes the host lock as a plain file (noclobber), not lib.sh's
    directory; the cycle waited only on a directory and walked past it."""
    host["lock"].write_text("owner=r93-d2@host kit=r93 token=abc\n", encoding="utf-8")
    result = run(host)
    assert result.returncode == 1
    assert started(host) == [], "nothing runs under a release"
    steps = outcomes(host)
    assert "HOST_LOCK held after 2s by: owner=r93-d2@host" in steps["lovable"]
    assert host["lock"].is_file(), "the release's lock is never touched"


def test_an_on_demand_run_finishes_before_the_night_sync_starts(host) -> None:
    """An administrator's request is being served by the 5-minute processor when the
    night begins: the cycle waits for the runner's lock, so the two never overlap."""
    sync_lock = host["tmp"] / "sync.lock"
    hold = host["tmp"] / "on-demand-running"
    hold.write_text("")
    on_demand = subprocess.Popen(  # noqa: S603 - the processor holds the runner's lock
        [
            BASH,
            "-c",
            f'exec 9<>"{sync_lock.as_posix()}"; flock -n 9 || exit 1; '
            f'echo "start on-demand" >> "$FAKE_DIR/calls"; '
            f'while [ -e "{hold.as_posix()}" ]; do sleep 0.1; done; '
            f'echo "end on-demand" >> "$FAKE_DIR/calls"',
        ],
        env=_env(host, WINTER_MIDNIGHT),
    )
    _wait_for(Path(str(sync_lock) + ".held"))

    def finish() -> None:
        hold.unlink()
        on_demand.wait(timeout=30)  # reaped: its lock is free, as the kernel's would be

    threading.Timer(1.0, finish).start()
    result = run(host, JURISTID_NIGHTLY_LOCK_WAIT_SECONDS="20")
    on_demand.wait(timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    sequence = calls(host)
    assert sequence.index("end on-demand") < sequence.index("start lovable ")


def test_an_on_demand_run_that_outlasts_the_wait_skips_only_the_sync(host) -> None:
    sync_lock = host["tmp"] / "sync.lock"
    hold = host["tmp"] / "on-demand-running"
    hold.write_text("")
    on_demand = subprocess.Popen(  # noqa: S603
        [
            BASH,
            "-c",
            f'exec 9<>"{sync_lock.as_posix()}"; flock -n 9 || exit 1; '
            f'while [ -e "{hold.as_posix()}" ]; do sleep 0.1; done',
        ],
        env=_env(host, WINTER_MIDNIGHT),
    )
    _wait_for(Path(str(sync_lock) + ".held"))
    result = run(host)
    hold.unlink()
    on_demand.wait(timeout=30)
    assert result.returncode == 1
    steps = outcomes(host)
    assert steps["lovable"].startswith("SKIPPED_UNEXPECTED") and "SYNC_BUSY" in steps["lovable"]
    assert "lovable " not in started(host)
    assert steps["register-watch"].startswith("SUCCEEDED")


# -- pauses -----------------------------------------------------------------------


def test_the_pause_file_pauses_the_night(host) -> None:
    host["nightly"].mkdir()
    (host["nightly"] / "PAUSED").write_text("")
    result = run(host)
    assert result.returncode == 0
    assert calls(host) == []
    assert (host["state"] / f"{WINTER_NIGHT}.completed").read_text().startswith("paused ")


def test_a_step_pause_and_the_lovable_pause_are_deliberate(host) -> None:
    host["nightly"].mkdir()
    (host["nightly"] / "PAUSED-government-agenda").write_text("")
    (host["appdata"] / "l100" / "sync").mkdir(parents=True)
    (host["appdata"] / "l100" / "sync" / "PAUSED").write_text("")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "watch_government_agenda" not in started(host)
    assert "lovable " not in started(host)
    steps = outcomes(host)
    assert steps["government-agenda"].startswith("SKIPPED_DELIBERATE")
    assert steps["lovable"].startswith("SKIPPED_DELIBERATE")
    assert verdict(host) == "verdict ok"


# -- the plan ---------------------------------------------------------------------


def _tree(path: Path) -> set[str]:
    return {str(p.relative_to(path)) for p in path.rglob("*")}


def test_the_plan_writes_nothing_and_runs_nothing(host) -> None:
    before = _tree(host["tmp"])
    result = run(host, "--plan", "--with-riigikogu")
    assert result.returncode == 0, result.stderr
    assert _tree(host["tmp"]) == before, "no log, no lock file, no marker"
    assert calls(host) == []
    names = [line.split()[0] for line in result.stdout.splitlines() if line.startswith("  ")]
    assert names == [
        "lovable",
        "register-watch",
        "riigikogu",
        "register-sweep",
        "government-agenda",
    ]
    assert "Decision: DUE" in result.stdout
    assert "register-watch.env" in result.stdout and "REGISTER=x" not in result.stdout


def test_the_plan_marks_the_riigikogu_step_left_out_by_default(host) -> None:
    result = run(host, "--plan")
    assert "SKIPPED_DELIBERATE NOT_INSTALLED" in result.stdout


# -- 01:00 Tallinn: the lanes after Lovable (owner, 25.11.2026; ADR 0229 §3) ---------


def _cycle_log(host: dict[str, Path]) -> str:
    logs = sorted((host["appdata"] / "nightly" / "logs").glob("*.log"))
    assert logs, "the cycle wrote its log"
    return logs[-1].read_text(encoding="utf-8")


def _lanes_after(now: str, seconds_from_real_now: float) -> str:
    """JURISTID_NIGHTLY_LANES_AFTER that puts 01:00 at real time + ``seconds``."""
    return str(int(time.time() + seconds_from_real_now) - int(_epoch(now)))


@pytest.mark.parametrize(
    ("midnight", "one_oclock"),
    [
        (WINTER_MIDNIGHT, "2026-01-14T23:00:00Z"),
        (SUMMER_MIDNIGHT, "2026-07-01T22:00:00Z"),
        # The spring night: 00:00 and 01:00 are still UTC+2 (the change is at 03:00).
        ("2026-03-28T22:00:00Z", "2026-03-28T23:00:00Z"),
        # The autumn night: 00:00 and 01:00 are still UTC+3 (the change is at 04:00).
        ("2026-10-24T21:00:00Z", "2026-10-24T22:00:00Z"),
    ],
    ids=["winter", "summer", "spring-change", "autumn-change"],
)
def test_the_lanes_are_not_before_one_oclock_tallinn(host, midnight, one_oclock) -> None:
    result = run(host, now=midnight)
    assert result.returncode == 0, result.stdout + result.stderr
    log = _cycle_log(host)
    assert f"LANES start (not before {one_oclock})" in log
    assert log.index("LANES start") < log.index("STEP register-watch start")
    assert log.index("STEP lovable start") < log.index("LANES start")


def test_lovable_done_before_one_oclock_the_lanes_wait_under_the_cycle_lock(host) -> None:
    began = time.monotonic()
    result = run(host, JURISTID_NIGHTLY_LANES_AFTER=_lanes_after(WINTER_MIDNIGHT, 4))
    assert result.returncode == 0, result.stdout + result.stderr
    assert time.monotonic() - began >= 3, "the lanes waited for 01:00"
    log = _cycle_log(host)
    assert log.index("WAIT the other lanes start at") < log.index("STEP register-watch start")
    assert log.index("WAIT the other lanes start at") < log.index("STEP government-agenda start")
    assert all(
        outcome.startswith("SUCCEEDED")
        for name, outcome in outcomes(host).items()
        if name != "riigikogu"
    )


def test_lovable_still_running_at_one_oclock_is_waited_for_not_raced(host) -> None:
    (host["fake"] / "block-lovable").write_text("")
    threading.Timer(3, lambda: (host["fake"] / "block-lovable").unlink()).start()
    result = run(host, JURISTID_NIGHTLY_LANES_AFTER=_lanes_after(WINTER_MIDNIGHT, 1))
    assert result.returncode == 0, result.stdout + result.stderr
    log = _cycle_log(host)
    assert "WAIT the other lanes" not in log, "01:00 had passed when Lovable ended"
    order = calls(host)
    assert order.index("end lovable") < order.index("start watch_registers")
    assert order.index("end lovable") < order.index("start watch_government_agenda")


def test_a_manual_recovery_does_not_wait_for_one_oclock(host) -> None:
    began = time.monotonic()
    result = run(
        host,
        "--manual",
        now="2026-01-15T06:00:00Z",
        JURISTID_NIGHTLY_LANES_AFTER=_lanes_after(WINTER_MIDNIGHT, 600),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert time.monotonic() - began < 60
    log = _cycle_log(host)
    assert "WAIT the other lanes" not in log and "LANES start" not in log
