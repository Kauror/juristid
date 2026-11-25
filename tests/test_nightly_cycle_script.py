"""`scripts/deploy/juristid-nightly-cycle.sh`, run for real against a synthetic host.

The coordinated nightly cycle (docs/adr/0227) starts the night's jobs that cron
used to start one by one. These run the script itself with a fake `docker`,
`flock` and Lovable sync script on PATH / under the synthetic appdata, which
record every step, and a fixed clock (`JURISTID_NIGHTLY_NOW`). The host paths come
from the script's test seams; cron sets none of them. Nothing reaches Docker,
Lovable, a register or a database. All data here is synthetic.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import stat
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
SCRIPT = ROOT / "scripts" / "deploy" / "juristid-nightly-cycle.sh"
CRON = ROOT / "deploy" / "unraid-main" / "cron" / "juristid-nightly.cron"
CRON_EXAMPLE = ROOT / "deploy" / "unraid-main" / "cron" / "juristid.cron.example"
BASH = shutil.which("bash") or "bash"
TALLINN = ZoneInfo("Europe/Tallinn")

# Records `docker exec … manage.py <command>` and answers with the exit code the
# test asks for (`rc-<command>`). `watch_registers` can be told to wait until the
# agenda has started (`wait-for-agenda`): it then proves the two lanes overlap.
FAKE_DOCKER = r"""#!/usr/bin/env bash
args=("$@"); cmd=""
for ((i = 0; i < ${#args[@]}; i++)); do
  if [ "${args[$i]}" = manage.py ]; then cmd="${args[$((i + 1))]}"; break; fi
done
[ "$1" = exec ] || exit 97
echo "start $cmd" >> "$FAKE_DIR/calls"
if [ "$cmd" = watch_government_agenda ]; then touch "$FAKE_DIR/agenda-started"; fi
if [ "$cmd" = watch_registers ] && [ -e "$FAKE_DIR/wait-for-agenda" ]; then
  for _ in $(seq 1 100); do [ -e "$FAKE_DIR/agenda-started" ] && break; sleep 0.1; done
  [ -e "$FAKE_DIR/agenda-started" ] || { echo "end $cmd"; exit 3; }
fi
echo "ran $cmd"
echo "end $cmd" >> "$FAKE_DIR/calls"
if [ -e "$FAKE_DIR/rc-$cmd" ]; then exit "$(cat "$FAKE_DIR/rc-$cmd")"; fi
exit 0
"""
# `flock` is not on every development machine; the fake answers from a file per
# file descriptor: `busy-8` is the cycle's lock, `busy-7` the Lovable runner's.
FAKE_FLOCK = r"""#!/usr/bin/env bash
for last; do :; done
[ -e "$FAKE_DIR/busy-$last" ] && exit 1
exit 0
"""
FAKE_SYNC = r"""#!/usr/bin/env bash
echo "start lovable $*" >> "$FAKE_DIR/calls"
echo "end lovable" >> "$FAKE_DIR/calls"
if [ -e "$FAKE_DIR/rc-lovable" ]; then exit "$(cat "$FAKE_DIR/rc-lovable")"; fi
exit 0
"""
FAKE_NOTIFY = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$FAKE_DIR/notified"\n'

# 2026-01-14 is a Wednesday in winter (UTC+2); 2026-07-01 a Wednesday in summer.
WINTER_MIDNIGHT = "2026-01-14T22:00:00Z"
WINTER_OTHER = "2026-01-14T21:00:00Z"
SUMMER_MIDNIGHT = "2026-07-01T21:00:00Z"
SUMMER_OTHER = "2026-07-01T22:00:00Z"


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
    _executable(tmp_path / "notify", FAKE_NOTIFY)
    fake = tmp_path / "fake"
    fake.mkdir()
    return {
        "tmp": tmp_path,
        "appdata": appdata,
        "bin": bin_dir,
        "fake": fake,
        "nightly": appdata / "nightly",
        "lock": tmp_path / "host-lock",
    }


def run(
    host: dict[str, Path], *arguments: str, now: str = WINTER_MIDNIGHT
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{host['bin']}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DIR"] = str(host["fake"])
    env["JURISTID_NIGHTLY_APPDATA"] = str(host["appdata"])
    env["JURISTID_NIGHTLY_NOW"] = _epoch(now)
    env["JURISTID_NIGHTLY_LOCK"] = str(host["tmp"] / "cycle.lock")
    env["JURISTID_SYNC_LOCK"] = str(host["tmp"] / "sync.lock")
    env["JURISTID_HOST_LOCK"] = str(host["lock"])
    env["JURISTID_NIGHTLY_LOCK_WAIT_SECONDS"] = "2"
    env["JURISTID_NIGHTLY_LOCK_RETRY_SECONDS"] = "1"
    env["JURISTID_NOTIFY"] = str(host["tmp"] / "notify")
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository script
        [BASH, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def calls(host: dict[str, Path]) -> list[str]:
    path = host["fake"] / "calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def started(host: dict[str, Path]) -> list[str]:
    return [line.removeprefix("start ") for line in calls(host) if line.startswith("start ")]


def summary(host: dict[str, Path]) -> dict[str, str]:
    """`last-cycle.txt`: step name → the rest of its line."""
    lines = (host["nightly"] / "last-cycle.txt").read_text(encoding="utf-8").splitlines()[1:]
    return {line.split()[0]: " ".join(line.split()[1:]) for line in lines}


def log_text(host: dict[str, Path]) -> str:
    logs = host["nightly"] / "logs"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(logs.iterdir()))


def tree(path: Path) -> set[str]:
    return {str(p.relative_to(path)) for p in path.rglob("*")}


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


# -- the Tallinn clock ------------------------------------------------------------


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
    """The host has no Tallinn zone; the script's EU rule must equal tzdata's."""
    moments = _moments()
    epochs = " ".join(str(int(m.timestamp())) for m in moments)
    result = subprocess.run(  # noqa: S603
        [
            BASH,
            "-c",
            f'source "$1"; for t in {epochs}; do tallinn_clock "$t"; done',
            "tallinn-clock",
            str(SCRIPT),
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
    for day in {m.astimezone(TALLINN).date() for m in _moments()}:
        assert len(by_night.get(day, [])) <= 1
    # The two changes of offset themselves: 29.03.2026 and 25.10.2026.
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


def test_the_summer_firing_runs_the_cycle(host) -> None:
    result = run(host, now=SUMMER_MIDNIGHT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (host["nightly"] / "state" / "2026-07-02.done").exists()
    assert started(host)[0] == "lovable "


# -- order and isolation ----------------------------------------------------------


def test_a_night_runs_every_step_in_order_and_once(host) -> None:
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    order = started(host)
    assert order[0] == "lovable ", "the Lovable synchronisation runs first, alone"
    assert calls(host)[1] == "end lovable", "nothing starts before it has ended"
    assert set(order[1:]) == {"watch_registers", "sweep_registers", "watch_government_agenda"}
    assert order.index("watch_registers") < order.index("sweep_registers")
    steps = summary(host)
    assert all(steps[name].startswith("OK") for name in steps if name != "riigikogu")
    assert steps["riigikogu"].startswith("SKIPPED") and "NOT_INSTALLED" in steps["riigikogu"]
    assert (host["nightly"] / "state" / "2026-01-15.done").exists()
    assert not (host["fake"] / "notified").exists()
    # The commands still write to their own logs, as their cron lines did.
    assert "ran watch_registers" in (host["appdata"] / "register-watch.log").read_text()
    assert "ran sweep_registers" in (host["appdata"] / "register-sweep.log").read_text()
    assert "ran watch_government_agenda" in (host["appdata"] / "government-agenda.log").read_text()

    again = run(host)
    assert again.returncode == 0
    assert len(started(host)) == 4, "a second start of the same night refuses (ALREADY_RAN)"


def test_the_agenda_lane_runs_beside_the_registers_lane(host) -> None:
    (host["fake"] / "wait-for-agenda").write_text("")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary(host)["register-watch"].startswith("OK")


def test_a_failing_step_does_not_stop_the_independent_ones(host) -> None:
    (host["fake"] / "rc-watch_government_agenda").write_text("1")
    result = run(host)
    assert result.returncode == 1
    steps = summary(host)
    assert steps["government-agenda"].startswith("FAILED rc=1")
    assert steps["register-watch"].startswith("OK") and steps["register-sweep"].startswith("OK")
    notified = (host["fake"] / "notified").read_text(encoding="utf-8")
    assert "government-agenda" in notified and "REGISTER=x" not in notified


def test_a_failed_lovable_night_still_lets_the_monitoring_run(host) -> None:
    (host["fake"] / "rc-lovable").write_text("1")
    result = run(host)
    assert result.returncode == 1
    steps = summary(host)
    assert steps["lovable"].startswith("FAILED rc=1")
    for name in ("register-watch", "register-sweep", "government-agenda"):
        assert steps[name].startswith("OK"), name


def test_a_failed_watch_skips_the_sweep_and_says_why(host) -> None:
    (host["fake"] / "rc-watch_registers").write_text("1")
    result = run(host)
    assert result.returncode == 1
    steps = summary(host)
    assert steps["register-watch"].startswith("FAILED rc=1")
    assert steps["register-sweep"].startswith("SKIPPED")
    assert "NEEDS register-watch, which was FAILED" in steps["register-sweep"]
    assert "sweep_registers" not in started(host)
    assert steps["government-agenda"].startswith("OK")
    assert "STEP register-sweep SKIPPED NEEDS register-watch" in log_text(host)


def test_the_riigikogu_step_runs_between_the_watch_and_the_sweep_when_asked(host) -> None:
    result = run(host, "--with-riigikogu")
    assert result.returncode == 0, result.stdout + result.stderr
    order = started(host)
    assert order.index("watch_registers") < order.index("watch_riigikogu")
    assert order.index("watch_riigikogu") < order.index("sweep_registers")
    assert summary(host)["riigikogu"].startswith("OK")


def test_a_failed_riigikogu_step_does_not_skip_the_sweep(host) -> None:
    (host["fake"] / "rc-watch_riigikogu").write_text("1")
    result = run(host, "--with-riigikogu")
    assert result.returncode == 1
    assert summary(host)["register-sweep"].startswith("OK")


# -- locks and pauses -------------------------------------------------------------


def test_a_held_release_lock_skips_the_steps_by_its_holder(host) -> None:
    host["lock"].mkdir()
    (host["lock"] / "owner").write_text("owner=release-d2 pid=1 2026-01-14T21:59:00Z\n")
    result = run(host)
    assert result.returncode == 1
    assert started(host) == [], "nothing runs under a release"
    steps = summary(host)
    assert "HOST_LOCK held after 2s by: owner=release-d2" in steps["lovable"]
    assert "HOST_LOCK" in steps["register-watch"] and "HOST_LOCK" in steps["government-agenda"]
    assert "NEEDS register-watch" in steps["register-sweep"]
    assert host["lock"].exists(), "the release's lock is never touched"


def test_a_busy_lovable_runner_skips_only_the_lovable_step(host) -> None:
    (host["fake"] / "busy-7").write_text("")
    result = run(host)
    assert result.returncode == 1
    steps = summary(host)
    assert steps["lovable"].startswith("SKIPPED") and "SYNC_BUSY" in steps["lovable"]
    assert "lovable " not in started(host)
    assert steps["register-watch"].startswith("OK")


def test_a_cycle_already_running_makes_the_second_leave_nothing(host) -> None:
    (host["fake"] / "busy-8").write_text("")
    result = run(host)
    assert result.returncode == 0
    assert calls(host) == []
    assert not (host["nightly"] / "logs").exists()


def test_the_pause_file_pauses_the_night(host) -> None:
    host["nightly"].mkdir()
    (host["nightly"] / "PAUSED").write_text("")
    result = run(host)
    assert result.returncode == 0
    assert calls(host) == []
    assert "SKIP paused on the host" in log_text(host)


def test_a_step_pause_is_deliberate_not_a_failure(host) -> None:
    host["nightly"].mkdir()
    (host["nightly"] / "PAUSED-government-agenda").write_text("")
    result = run(host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "watch_government_agenda" not in started(host)
    assert summary(host)["government-agenda"].startswith("SKIPPED")
    assert "PAUSED" in summary(host)["government-agenda"]
    assert not (host["fake"] / "notified").exists()


# -- the plan ---------------------------------------------------------------------


def test_the_plan_writes_nothing_and_runs_nothing(host) -> None:
    before = tree(host["tmp"])
    result = run(host, "--plan", "--with-riigikogu")
    assert result.returncode == 0, result.stderr
    assert tree(host["tmp"]) == before, "no log, no lock file, no marker"
    assert calls(host) == []
    lines = result.stdout.splitlines()
    names = [line.split()[0] for line in lines if line.startswith("  ")]
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
    assert "SKIPPED NOT_INSTALLED" in result.stdout


def test_at_is_refused_outside_the_plan(host) -> None:
    result = run(host, "--at", WINTER_MIDNIGHT)
    assert result.returncode == 64
    assert calls(host) == []


# -- the cron file the deployment installs ----------------------------------------


def _schedule(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_the_cron_file_fires_the_cycle_at_both_possible_midnights_only() -> None:
    lines = _schedule(CRON)
    assert lines == [
        "0 21,22 * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/"
        "juristid-nightly-cycle.sh &> /dev/null"
    ]


def test_the_example_no_longer_schedules_what_the_cycle_runs() -> None:
    scheduled = "\n".join(_schedule(CRON_EXAMPLE))
    for command in ("watch_registers", "sweep_registers", "watch_government_agenda"):
        assert command not in scheduled, command
    assert "juristid-nightly-cycle.sh" in scheduled
