"""`scripts/deploy/juristid-monitor.sh` (security review 2026-10-29, F-01…F-09).

Driven under bash with a stub `docker`, `curl` and `notify` on PATH, so the
wrapper's own logic is what is tested: which checks fail, that a failure is
sent once and its recovery once, that a dry run sends nothing and writes no
state, that the daily tier refuses to run without an RPO, and that a message
names counts and containers and never a value from the environment file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
MONITOR = ROOT / "scripts" / "deploy" / "juristid-monitor.sh"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"
BASH = shutil.which("bash") or "bash"

DOCKER_STUB = r"""#!/usr/bin/env bash
# `inspect` answers for every container unless STUB_DOWN names it; `exec`,
# `logs` and `compose exec` answer the monitor's questions from variables.
case "$1 $2" in
  "inspect -f")
    name="${@: -1}"
    case " ${STUB_DOWN:-} " in
      *" ${name#juristid-main-} "*) printf 'exited none\n'; exit 0 ;;
    esac
    printf 'running healthy\n'; exit 0 ;;
  "exec "*) exit "${STUB_PG_ISREADY:-0}" ;;
  "logs "*) printf '%s\n' "${STUB_LOG_LINES:-}"; exit 0 ;;
  "compose "*)
    case "$*" in
      *production_status*)
        if [ "${STUB_STATUS:-ok}" = fail ]; then
          printf 'Deployment readiness   PASS\nSearch freshness       FAIL\n\n'
          printf 'Overall                FAIL\n'; exit 1
        fi
        printf 'Deployment readiness   PASS\n\nOverall                PASS\n'; exit 0 ;;
      *audit_failures*)
        printf 'gate_failed=%s\ngate_locked_out=%s\nadmin_step_up_failed=0\n' \
          "${STUB_GATE_FAILED:-0}" "${STUB_LOCKED_OUT:-0}"
        printf 'mcp_refresh_reuse=%s\nmcp_suspended=0\nemergency_disabled=0\n' "${STUB_REUSE:-0}"
        printf 'write_commit_refused=0\nindicators=\n'; exit 0 ;;
      *check_evidence_integrity*) exit "${STUB_EVIDENCE:-0}" ;;
      *version*) printf 'Docker Compose version stub\n'; exit 0 ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
"""

CURL_STUB = r"""#!/usr/bin/env bash
# `-o FILE -w '%{http_code}'`: write the body, print the code.
out=""; code="${STUB_HTTP_CODE:-200}"
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done
[ -n "$out" ] && printf '{"status": "%s"}' "${STUB_HTTP_STATUS:-ok}" > "$out"
printf '%s' "$code"
"""

NOTIFY_STUB = r"""#!/usr/bin/env bash
# Append one line per call: importance | subject | description.
subject=""; description=""; importance=""
while [ $# -gt 0 ]; do
  case "$1" in
    -s) subject="$2"; shift 2 ;;
    -d) description="$2"; shift 2 ;;
    -i) importance="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf '%s | %s | %s\n' "$importance" "$subject" "$description" >> "$STUB_NOTIFY_LOG"
"""


@pytest.fixture
def world(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB), ("notify", NOTIFY_STUB)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    data = tmp_path / "appdata"
    (data / "evidence").mkdir(parents=True)
    backups = tmp_path / "backups"
    (backups / "sets").mkdir(parents=True)
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    sync_logs = tmp_path / "sync-logs"
    sync_logs.mkdir()
    state = tmp_path / "state"
    notify_log = tmp_path / "notify.log"
    env_file = tmp_path / "monitor.env"
    env_file.write_text(
        "\n".join(
            [
                "MONITOR_PROJECT=juristid-main",
                f"MONITOR_COMPOSE_FILE={COMPOSE}",
                f"MONITOR_DATA_ROOT={data}",
                f"MONITOR_BACKUP_ROOT={backups}",
                "MONITOR_HEALTHZ_URL=https://example.test/healthz",
                f"MONITOR_DUMP_DIR={dumps}",
                f"MONITOR_SYNC_LOG_DIR={sync_logs}",
                "MONITOR_MCP_PROFILE_ON=1",
                "MONITOR_RPO_HOURS=24",
                "MONITOR_FREE_GIB_WARN=0",
                "MONITOR_FREE_GIB_CRIT=0",
                f"MONITOR_NOTIFY={bin_dir / 'notify'}",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_NOTIFY_LOG": str(notify_log),
    }
    return {
        "env": env,
        "env_file": env_file,
        "state": state,
        "notify_log": notify_log,
        "backups": backups,
        "dumps": dumps,
        "sync_logs": sync_logs,
    }


def _run(world: dict, *arguments: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            BASH,
            str(MONITOR),
            "--env-file",
            str(world["env_file"]),
            "--state-dir",
            str(world["state"]),
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**world["env"], **overrides},
        check=False,
    )


def _sent(world: dict) -> list[str]:
    log: Path = world["notify_log"]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _fresh_set(world: dict, stamp: str) -> None:
    directory = world["backups"] / "sets" / stamp
    directory.mkdir()
    for name in ("database.dump", "manifest.json", "SHA256SUMS"):
        (directory / name).write_text("x", encoding="utf-8")


def _stamp(hours_ago: int) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() - hours_ago * 3600))


# -- arguments ---------------------------------------------------------------


def test_it_refuses_without_a_tier_an_env_file_or_a_state_dir(world: dict) -> None:
    for arguments in (
        (),
        ("--tier", "fast"),
        ("--tier", "nightly", "--env-file", str(world["env_file"])),
    ):
        result = subprocess.run(  # noqa: S603
            [BASH, str(MONITOR), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            env=world["env"],
            check=False,
        )
        assert result.returncode == 1, (arguments, result.stderr)
    assert not _sent(world)


def test_the_daily_tier_refuses_without_an_rpo(world: dict) -> None:
    text = world["env_file"].read_text(encoding="utf-8").replace("MONITOR_RPO_HOURS=24\n", "")
    world["env_file"].write_text(text, encoding="utf-8", newline="\n")
    result = _run(world, "--tier", "daily")
    assert result.returncode == 1
    assert "MONITOR_RPO_HOURS" in result.stderr
    assert "decision" in result.stderr
    fast = _run(world, "--tier", "fast")
    assert fast.returncode == 0, fast.stderr


# -- fast tier -----------------------------------------------------------------


def test_a_healthy_stack_sends_nothing_and_records_ok(world: dict) -> None:
    result = _run(world, "--tier", "fast")
    assert result.returncode == 0, result.stderr
    assert _sent(world) == []
    # State is written on a transition only, so a check that was never
    # anything but ok has no file; the daily repeat of a persisting WARN
    # reads the file's age, which a write per run would reset.
    for key in ("containers", "healthz", "db", "space"):
        state = world["state"] / key
        assert not state.exists() or state.read_text(encoding="utf-8").strip() == "ok"


def test_a_dead_container_is_sent_once_and_its_recovery_once(world: dict) -> None:
    first = _run(world, "--tier", "fast", STUB_DOWN="notifications")
    assert first.returncode == 0, first.stderr
    second = _run(world, "--tier", "fast", STUB_DOWN="notifications")
    assert second.returncode == 0
    sent = _sent(world)
    assert len(sent) == 1, sent
    assert sent[0].startswith("alert | CRIT containers | ")
    assert "notifications(exited" in sent[0]

    third = _run(world, "--tier", "fast")
    assert third.returncode == 0
    sent = _sent(world)
    assert len(sent) == 2, sent
    assert sent[1].startswith("normal | recovered containers | ")


def test_the_mcp_trio_is_expected_only_while_the_profile_is_on(world: dict) -> None:
    result = _run(world, "--tier", "fast", STUB_DOWN="mcp-tunnel")
    assert result.returncode == 0
    assert any("mcp-tunnel(exited" in line for line in _sent(world))

    text = (
        world["env_file"]
        .read_text(encoding="utf-8")
        .replace("MONITOR_MCP_PROFILE_ON=1", "MONITOR_MCP_PROFILE_ON=0")
    )
    world["env_file"].write_text(text, encoding="utf-8", newline="\n")
    result = _run(world, "--tier", "fast", STUB_DOWN="mcp-tunnel")
    assert result.returncode == 0
    assert _sent(world)[-1].startswith("normal | recovered containers")


def test_a_degraded_healthz_and_a_dead_database_are_critical(world: dict) -> None:
    result = _run(
        world,
        "--tier",
        "fast",
        STUB_HTTP_CODE="503",
        STUB_HTTP_STATUS="degraded",
        STUB_PG_ISREADY="2",
    )
    assert result.returncode == 0, result.stderr
    sent = _sent(world)
    assert any(line.startswith("alert | CRIT healthz | ") and "HTTP 503" in line for line in sent)
    assert any(line.startswith("alert | CRIT db | ") for line in sent)


def test_a_dry_run_prints_and_neither_sends_nor_writes_state(world: dict) -> None:
    result = _run(world, "--tier", "fast", "--dry-run", STUB_DOWN="web")
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN notify [CRIT] containers" in result.stdout
    assert _sent(world) == []
    assert not (world["state"] / "containers").exists()


# -- daily tier ----------------------------------------------------------------


def test_a_stale_backup_set_is_a_warning_and_a_fresh_one_recovers_it(world: dict) -> None:
    _fresh_set(world, _stamp(100))
    result = _run(world, "--tier", "daily")
    assert result.returncode == 0, result.stderr
    sent = _sent(world)
    assert any(line.startswith("warning | WARN backup_age | ") for line in sent), sent

    _fresh_set(world, _stamp(1))
    result = _run(world, "--tier", "daily")
    assert result.returncode == 0
    assert any(line.startswith("normal | recovered backup_age | ") for line in _sent(world))


def test_no_set_at_all_is_critical_and_a_partial_is_a_warning(world: dict) -> None:
    (world["backups"] / "sets" / f"{_stamp(3)}.partial").mkdir()
    result = _run(world, "--tier", "daily")
    assert result.returncode == 0, result.stderr
    sent = _sent(world)
    assert any(line.startswith("alert | CRIT backup_age | ") for line in sent), sent
    assert any(
        line.startswith("warning | WARN partials | ") and "1 unfinished" in line for line in sent
    )


def test_the_dump_age_and_the_sync_log_are_read_from_the_files(world: dict) -> None:
    _fresh_set(world, _stamp(1))
    old = world["dumps"] / "juristid-main-db-juristid-20260101T230001Z.sql.gz"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 40 * 3600, time.time() - 40 * 3600))
    log = world["sync_logs"] / "20261029T211500Z-scheduled.log"
    log.write_text(
        "2026-10-29T21:15:00Z START\n"
        "2026-10-29T21:15:09Z FAIL web is not healthy (absent)\n"
        "2026-10-29T21:15:09Z END rc=1\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _run(world, "--tier", "daily")
    assert result.returncode == 0, result.stderr
    sent = _sent(world)
    assert any("WARN dump_age" in line and "40h old" in line for line in sent), sent
    assert any("WARN lovable" in line and "web is not healthy" in line for line in sent), sent


def test_status_errors_and_auth_thresholds(world: dict) -> None:
    _fresh_set(world, _stamp(1))
    result = _run(
        world,
        "--tier",
        "daily",
        STUB_STATUS="fail",
        STUB_LOG_LINES="2026-10-29 10:00:00 ERROR app.core something\nok line",
        STUB_GATE_FAILED="25",
        STUB_LOCKED_OUT="4",
        STUB_REUSE="1",
    )
    assert result.returncode == 0, result.stderr
    sent = _sent(world)
    assert any("WARN status" in line and "Search freshness" in line for line in sent), sent
    assert any("WARN errors" in line and "web=1" in line for line in sent), sent
    auth = [line for line in sent if "WARN auth" in line]
    assert auth, sent
    assert "gate failures 25" in auth[0]
    assert "gate lockouts 4" in auth[0]
    assert "refresh-token reuse 1" in auth[0]


def test_messages_name_checks_and_counts_only(world: dict) -> None:
    _fresh_set(world, _stamp(100))
    _run(world, "--tier", "fast", STUB_DOWN="web", STUB_HTTP_CODE="000")
    _run(world, "--tier", "daily", STUB_GATE_FAILED="99")
    for line in _sent(world):
        assert "example.test" not in line, "the URL from the environment file reached a message"
        assert str(world["env_file"]) not in line


# -- weekly tier ---------------------------------------------------------------


def test_the_weekly_tier_sends_a_digest_and_warns_on_integrity(world: dict) -> None:
    _fresh_set(world, _stamp(1))
    result = _run(world, "--tier", "weekly", STUB_EVIDENCE="1")
    assert result.returncode == 0, result.stderr
    sent = _sent(world)
    assert any(line.startswith("warning | WARN evidence | ") for line in sent), sent
    assert any(
        line.startswith("normal | INFO digest | ") and "1 backup sets" in line for line in sent
    )
