"""Quality audit 2026-11, package H: what watches production, and what it misses.

* QA26-ARCH-04 — the daily Lovable check read the newest scheduled log, which
  in summer time (and whenever the sync is off) is the firing that was not due:
  a false «did not finish» every morning.
* QA26-OPS-04 — application errors were counted from `docker logs`, which start
  at the last release; the monitor now keeps the logs (30 days, on the host).
* QA26-OPS-05 — a monitor that could not run was silent, and nothing outside it
  could tell; it now says so, and leaves a stamp per run for the host watchdog.
* QA26-OPS-06 — an unhealthy `mcp-tunnel` waited for a person (up to 12 h); the
  monitor now restarts a tunnel after two unhealthy checks, once in six hours.
* QA26-ARCH-05 — a killed backup or sync left the host lock until a person
  removed it, and a backup that found the lock held never tried again.

Driven like tests/test_secres_monitor.py: bash with stub `docker`, `curl` and
`notify` on PATH.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_secres_monitor import (  # noqa: F401
    BASH,
    ROOT,
    _fresh_set,
    _run,
    _sent,
    _stamp,
    world,
)

LIB = ROOT / "scripts" / "deploy" / "lib.sh"
NIGHTLY = ROOT / "deploy" / "unraid-main" / "cron" / "juristid-backup-nightly.sh"

#: tests/test_secres_monitor.py's stub, plus: STUB_UNHEALTHY names containers that
#: run unhealthy, `restart` is recorded in STUB_DOCKER_LOG, and `logs --timestamps`
#: prefixes each line with STUB_LOG_TS.
DOCKER_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "${STUB_DOCKER_LOG:-/dev/null}"
case "$1 $2" in
  "inspect -f")
    name="${@: -1}"
    case " ${STUB_DOWN:-} " in
      *" ${name#juristid-main-} "*) printf 'exited none\n'; exit 0 ;;
    esac
    case " ${STUB_UNHEALTHY:-} " in
      *" ${name#juristid-main-} "*) printf 'running unhealthy\n'; exit 0 ;;
    esac
    printf 'running healthy\n'; exit 0 ;;
  "restart "*) exit 0 ;;
  "exec "*) exit "${STUB_PG_ISREADY:-0}" ;;
  "logs "*)
    case "$*" in
      *--timestamps*)
        [ -n "${STUB_LOG_LINES:-}" ] || exit 0
        ts="${STUB_LOG_TS:-2026-01-01T00:00:00.000000000Z}"
        printf '%s\n' "$STUB_LOG_LINES" | sed "s/^/$ts /"; exit 0 ;;
    esac
    printf '%s\n' "${STUB_LOG_LINES:-}"; exit 0 ;;
  "compose "*)
    case "$*" in
      *production_status*)
        printf 'Deployment readiness   PASS\n\nOverall   PASS\n'; exit 0 ;;
      *audit_failures*)
        printf 'gate_failed=0\ngate_locked_out=0\nadmin_step_up_failed=0\n'
        printf 'mcp_refresh_reuse=0\nmcp_suspended=0\nemergency_disabled=0\n'
        printf 'write_commit_refused=0\nindicators=\n'; exit 0 ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture
def mon(world: dict, tmp_path: Path) -> dict:  # noqa: F811 (the imported fixture)
    stub = Path(world["env"]["PATH"].split(os.pathsep)[0]) / "docker"
    stub.write_text(DOCKER_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    world["docker_log"] = tmp_path / "docker.log"
    world["env"]["STUB_DOCKER_LOG"] = str(world["docker_log"])
    world["app_logs"] = tmp_path / "appdata" / "app-logs"
    return world


def _now_ts(hours_ago: float = 0) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime(time.time() - hours_ago * 3600)
    )


def _sync_log(mon: dict, name: str, text: str, hours_ago: float) -> None:
    path = mon["sync_logs"] / name
    path.write_text(text, encoding="utf-8", newline="\n")
    moment = time.time() - hours_ago * 3600
    os.utime(path, (moment, moment))


# -- ARCH-04: the Lovable night ------------------------------------------------------


def test_a_not_due_firing_after_a_good_night_is_not_a_warning(mon: dict) -> None:
    """Summer time: 21:15 UTC ran and ended rc=0, 22:15 UTC was not due."""
    _fresh_set(mon, _stamp(1))
    _sync_log(
        mon,
        "20270601T211502Z-scheduled.log",
        "2027-06-01T21:15:02Z DUE APPLY (image 0123456789ab)\n2027-06-01T21:15:40Z run\n"
        "2027-06-01T21:16:10Z END rc=0\n2027-06-01T21:16:12Z observe END ok\n",
        hours_ago=8,
    )
    _sync_log(
        mon,
        "20270601T221502Z-scheduled.log",
        "2027-06-01T22:15:02Z NOT_DUE NOT_MIDNIGHT_IN_TALLINN (image 0123456789ab)\n",
        hours_ago=7,
    )
    result = _run(mon, "--tier", "daily")
    assert result.returncode == 0, result.stderr
    assert "ok lovable: last scheduled night finished, rc=0" in result.stdout
    assert not [line for line in _sent(mon) if "lovable" in line]


def test_a_paused_or_off_sync_says_so_and_does_not_warn(mon: dict) -> None:
    _fresh_set(mon, _stamp(1))
    _sync_log(
        mon,
        "20261120T221502Z-scheduled.log",
        "2026-11-20T22:15:02Z NOT_DUE PAUSED (image 0123456789ab)\n",
        hours_ago=2,
    )
    result = _run(mon, "--tier", "daily")
    assert result.returncode == 0, result.stderr
    assert "ok lovable: the scheduled sync is paused or off on purpose" in result.stdout
    assert not [line for line in _sent(mon) if "lovable" in line]


def test_a_failed_night_before_a_not_due_firing_is_still_a_warning(mon: dict) -> None:
    _fresh_set(mon, _stamp(1))
    _sync_log(
        mon,
        "20270601T211502Z-scheduled.log",
        "2027-06-01T21:15:02Z DUE APPLY (image 0123456789ab)\n2027-06-01T21:15:40Z run\n"
        "2027-06-01T21:16:10Z END rc=3\n",
        hours_ago=8,
    )
    _sync_log(
        mon,
        "20270601T221502Z-scheduled.log",
        "2027-06-01T22:15:02Z NOT_DUE NOT_MIDNIGHT_IN_TALLINN (image 0123456789ab)\n",
        hours_ago=7,
    )
    _run(mon, "--tier", "daily")
    assert any("WARN lovable" in line and "did not finish" in line for line in _sent(mon))


# -- OPS-04: the application's errors outlive a release ---------------------------------


def test_logs_are_kept_and_counted_after_the_container_is_recreated(mon: dict) -> None:
    lines = "ERROR app.core something broke\nTraceback (most recent call last):\nok line"
    result = _run(mon, "--tier", "fast", STUB_LOG_LINES=lines, STUB_LOG_TS=_now_ts(2))
    assert result.returncode == 0, result.stderr
    kept = sorted(mon["app_logs"].glob("web-*.log"))
    assert kept, list(mon["app_logs"].glob("*"))
    assert "ERROR app.core something broke" in kept[0].read_text(encoding="utf-8")
    assert "--timestamps --since 1h --until" in mon["docker_log"].read_text(encoding="utf-8")

    # A release recreated every container: its own log is empty now.
    _fresh_set(mon, _stamp(1))
    daily = _run(mon, "--tier", "daily", STUB_LOG_LINES="")
    assert daily.returncode == 0, daily.stderr
    assert any("WARN errors" in line and "web=2" in line for line in _sent(mon)), _sent(mon)


def test_the_database_check_names_its_role_and_database(mon: dict) -> None:
    """N3 (first-night verification, 26.11.2026): `pg_isready` without -U connected as
    root, which PostgreSQL logged as «role "root" does not exist» every 15 minutes."""
    result = _run(mon, "--tier", "fast")
    assert result.returncode == 0, result.stderr
    calls = [
        c for c in mon["docker_log"].read_text(encoding="utf-8").splitlines() if "pg_isready" in c
    ]
    assert calls, "the fast tier no longer checks the database"
    assert all("pg_isready -q -U juristid -d juristid" in c for c in calls), calls


def test_kept_lines_older_than_a_day_are_not_counted(mon: dict) -> None:
    _run(mon, "--tier", "fast", STUB_LOG_LINES="ERROR app.core old", STUB_LOG_TS=_now_ts(30))
    _fresh_set(mon, _stamp(1))
    daily = _run(mon, "--tier", "daily", STUB_LOG_LINES="")
    assert "ok errors: log error lines in the last 24h: web=0" in daily.stdout


def test_the_logs_tier_only_collects(mon: dict) -> None:
    result = _run(mon, "--tier", "logs", STUB_LOG_LINES="hello", STUB_LOG_TS=_now_ts())
    assert result.returncode == 0, result.stderr
    assert sorted(p.name.split("-")[0] for p in mon["app_logs"].glob("*.log"))
    assert "inspect -f {{.State.Status}} {{if" not in mon["docker_log"].read_text(encoding="utf-8")
    assert _sent(mon) == []


# -- OPS-05: the monitor itself ---------------------------------------------------------


def test_every_finished_run_leaves_its_stamp(mon: dict) -> None:
    _run(mon, "--tier", "fast")
    stamp = mon["state"] / "last-run-fast"
    assert stamp.exists()
    assert stamp.read_text(encoding="utf-8").startswith(time.strftime("%Y-%m-%d", time.gmtime()))
    dry = _run(mon, "--tier", "weekly", "--dry-run")
    assert dry.returncode == 0
    assert not (mon["state"] / "last-run-weekly").exists()


def test_a_monitor_that_cannot_run_says_so_once(mon: dict) -> None:
    notify = Path(mon["env"]["PATH"].split(os.pathsep)[0]) / "notify"
    missing = mon["env_file"].with_name("absent.env")
    for _ in range(2):
        result = subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
            [
                BASH,
                str(ROOT / "scripts" / "deploy" / "juristid-monitor.sh"),
                "--tier",
                "fast",
                "--env-file",
                str(missing),
                "--state-dir",
                str(mon["state"]),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            env={**mon["env"], "JURISTID_MONITOR_NOTIFY": str(notify)},
            check=False,
        )
        assert result.returncode == 1
    sent = _sent(mon)
    assert len(sent) == 1, sent
    assert sent[0].startswith("alert | CRIT monitor | the fast check could not run")
    assert str(missing) in sent[0]  # a path, never a value from the file


# -- OPS-06: an unhealthy tunnel is restarted -------------------------------------------


def test_an_unhealthy_tunnel_is_restarted_after_two_checks_once(mon: dict) -> None:
    for _ in range(3):
        assert _run(mon, "--tier", "fast", STUB_UNHEALTHY="mcp-tunnel").returncode == 0
    calls = mon["docker_log"].read_text(encoding="utf-8").splitlines()
    assert calls.count("restart juristid-main-mcp-tunnel") == 1, calls
    sent = _sent(mon)
    assert (
        sent[0].startswith("alert | CRIT containers") and "mcp-tunnel(running/unhealthy)" in sent[0]
    )
    assert any("INFO restarted" in line and "mcp-tunnel" in line for line in sent), sent


def test_an_unhealthy_application_container_is_never_restarted(mon: dict) -> None:
    for _ in range(3):
        _run(mon, "--tier", "fast", STUB_UNHEALTHY="web")
    assert "restart" not in mon["docker_log"].read_text(encoding="utf-8")


# -- ARCH-05: the host lock -------------------------------------------------------------


def _lock(tmp_path: Path, owner_line: str, minutes_old: int) -> Path:
    lock = tmp_path / "host-lock"
    lock.mkdir()
    (lock / "owner").write_text(owner_line + "\n", encoding="utf-8", newline="\n")
    moment = time.time() - minutes_old * 60
    os.utime(lock, (moment, moment))
    return lock


def _take(lock: Path, owner: str = "backup") -> subprocess.CompletedProcess[str]:
    script = f'source "{LIB.as_posix()}"; take_host_lock {owner} && cat "$JURISTID_HOST_LOCK/owner"'
    return subprocess.run(  # noqa: S603
        [BASH, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "JURISTID_HOST_LOCK": lock.as_posix()},
        check=False,
    )


def _dead_pid() -> str:
    out = subprocess.run(  # noqa: S603 - a fixed interpreter
        [BASH, "-c", "true & echo $!; wait"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def test_a_lock_left_by_a_killed_sync_is_taken_over(tmp_path: Path) -> None:
    lock = _lock(tmp_path, f"owner=lovable-sync pid={_dead_pid()} 2026-11-19T21:15:02Z", 45)
    result = _take(lock)
    assert result.returncode == 0, result.stderr
    assert "took over a stale host lock (owner=lovable-sync" in result.stdout
    assert result.stdout.strip().splitlines()[-1].startswith("owner=backup pid=")


def test_a_young_lock_or_a_living_holder_is_not_taken_over(tmp_path: Path) -> None:
    young = _lock(tmp_path, f"owner=backup pid={_dead_pid()} now", 5)
    assert _take(young).returncode == 1
    (young / "owner").unlink()
    young.rmdir()
    holder = subprocess.Popen(  # noqa: S603 - a fixed interpreter
        [BASH, "-c", "echo $$; exec sleep 30"], stdout=subprocess.PIPE, text=True
    )
    try:
        pid = holder.stdout.readline().strip()  # type: ignore[union-attr]
        alive = _lock(tmp_path, f"owner=lovable-sync pid={pid} then", 45)
        assert _take(alive).returncode == 1
    finally:
        holder.kill()


def test_a_release_lock_is_never_taken_over(tmp_path: Path) -> None:
    lock = _lock(tmp_path, f"owner=r78 d2 pid={_dead_pid()} 2026-11-19T17:21:00Z", 600)
    assert _take(lock).returncode == 1
    assert (lock / "owner").read_text(encoding="utf-8").startswith("owner=r78")


# -- ARCH-05: the nightly backup tries again --------------------------------------------


def test_the_nightly_backup_retries_a_held_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    deploy = repo / "scripts" / "deploy"
    deploy.mkdir(parents=True)
    counter = tmp_path / "attempts"
    (deploy / "juristid-backup.sh").write_text(
        "#!/usr/bin/env bash\n"
        f'c="{counter.as_posix()}"\n'
        'n=$(( $(cat "$c" 2>/dev/null || echo 0) + 1 )); echo $n > "$c"\n'
        'if [ "$n" -lt 3 ]; then\n'
        '  echo "ERROR: another operation holds /tmp/x (owner=lovable-sync)"; exit 1\n'
        "fi\n"
        "echo sealed\n",
        encoding="utf-8",
        newline="\n",
    )
    (deploy / "juristid-check-backup-age.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n"
    )
    backups = tmp_path / "backups"
    (backups / "sets").mkdir(parents=True)
    logs = tmp_path / "logs"
    result = subprocess.run(  # noqa: S603
        [
            BASH,
            str(NIGHTLY),
            "--repo",
            repo.as_posix(),
            "--data-root",
            tmp_path.as_posix(),
            "--backup-root",
            backups.as_posix(),
            "--log-dir",
            logs.as_posix(),
            "--rpo-hours",
            "24",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={
            **os.environ,
            "JURISTID_BACKUP_LOCK_RETRY_SECONDS": "0",
            "JURISTID_NOTIFY": "/nonexistent",
        },
    )
    assert result.returncode == 0, result.stderr
    log = next(logs.glob("*-backup.log")).read_text(encoding="utf-8")
    assert log.count("WAIT the host lock is held") == 2, log
    assert "OK set sealed" in log
    assert counter.read_text(encoding="utf-8").strip() == "3"
