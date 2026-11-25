"""The host locks' inherited-lock contract (docs/adr/0229 §5), run for real.

Two defects this pins:

1. **The Lovable lock hand-off.** The nightly cycle used to wait for the Lovable
   runner's flock, drop it, and start `juristid-lovable-sync.sh`, which took it
   again: an on-demand firing (`2-59/5`) could take it in between and the night's
   sync refused. Now the cycle keeps the lock and hands the descriptor down
   (`JURISTID_SYNC_LOCK_FD=5`); the sync script accepts it only once lib.sh
   `lock_fd_proven` has proved it.
2. **The preflight's lock ownership.** A release takes the cycle lock (fd 7)
   before it runs anything, so the preflight it calls saw the lock held — by its
   own caller. A caller now hands fd 7 down (`JURISTID_CYCLE_LOCK_FD=7`) and the
   preflight passes only on proof; standalone it still refuses while anyone holds
   the lock.

The race is made deterministic, not sequential: the cycle is paused inside its
`flock -w` right after it acquired the runner's lock (a test `flock` that pauses
while `$FAKE_DIR/pause-after-wait` exists), an on-demand firing runs in exactly
that window, and only then does the cycle go on.

Every test runs with a fake `flock` that keeps the kernel's rule as far as a
script can see it (a lock belongs to the holder's descriptor, which its children
inherit); where `flock` is real (Linux, CI's Ubuntu) every test runs again with
the REAL `flock(1)`, so kernel locks are exercised too. The real scripts run —
the cycle, the sync, lib.sh, the preflight — against a fake `docker`. Nothing
reaches Docker, Lovable, a register or a database. All data here is synthetic.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
DEPLOY = ROOT / "scripts" / "deploy"
CYCLE = DEPLOY / "juristid-nightly-cycle.sh"
SYNC = DEPLOY / "juristid-lovable-sync.sh"
LIB = DEPLOY / "lib.sh"
PREFLIGHT = DEPLOY / "juristid-deploy-preflight.sh"
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"
BASH = shutil.which("bash") or "bash"
REAL_FLOCK = shutil.which("flock") if sys.platform == "linux" else None

WINTER_MIDNIGHT = "2026-01-14T22:00:00Z"
WINTER_NIGHT = "2026-01-15"

# Pauses the caller right after a successful `flock -w` (the cycle's wait for the
# runner's lock — nothing else uses -w) while $FAKE_DIR/pause-after-wait exists.
PAUSE_HOOK = r"""
if [ "$rc" = 0 ] && [ "$first" = -w ] && [ -e "$FAKE_DIR/pause-after-wait" ]; then
  : > "$FAKE_DIR/paused"
  while [ -e "$FAKE_DIR/pause-after-wait" ]; do sleep 0.05; done
fi
exit "$rc"
"""

# A flock that excludes across processes and keeps the kernel's ownership rule
# as far as a script can see it: the lock belongs to the descriptor it was taken
# on (pid + fd number), and a child that inherited that descriptor shares it; the
# same file opened again — another fd, another process — does not. (A child that
# reopens the SAME fd number cannot be told apart here; that case runs only with
# the real flock.) A dead holder holds nothing. -n, -w SECONDS and -u.
FAKE_FLOCK = (
    r"""#!/usr/bin/env bash
first="${1:-}"
nb=0; wait=0; unlock=0
while [ $# -gt 1 ]; do
  case "$1" in -n) nb=1 ;; -w) wait="$2"; shift ;; -u) unlock=1 ;; esac; shift
done
fd="$1"
path="$(readlink "/proc/self/fd/$fd")" || exit 2
held="$path.held"
if [ "$unlock" = 1 ]; then rm -rf "$held"; exit 0; fi
ppid_of() {
  if [ -r "/proc/$1/ppid" ]; then cat "/proc/$1/ppid" 2>/dev/null
  else sed -E 's/^.*\) [A-Za-z] ([0-9]+).*$/\1/' "/proc/$1/stat" 2>/dev/null; fi
}
shared() {  # the holder's own descriptor: the holder itself or an ancestor, same fd
  local p=$PPID n
  [ "$(cat "$held/fd" 2>/dev/null)" = "$fd" ] || return 1
  for n in $(seq 1 64); do
    [ "$p" = "$1" ] && return 0
    { [ -n "$p" ] && [ "$p" -gt 1 ]; } || return 1
    p="$(ppid_of "$p")"
  done
  return 1
}
deadline=$((SECONDS + ${wait%.*}))
rc=1
while :; do
  if mkdir "$held" 2>/dev/null; then
    echo "$fd" > "$held/fd"; echo "$PPID" > "$held/pid"; rc=0; break
  fi
  holder="$(cat "$held/pid" 2>/dev/null || true)"
  if [ -n "$holder" ] && shared "$holder"; then rc=0; break; fi
  if [ -n "$holder" ] && { ! kill -0 "$holder" 2>/dev/null ||
    [ "$(awk '{print $3}' "/proc/$holder/stat" 2>/dev/null)" = Z ]; }; then
    rm -rf "$held"; continue
  fi
  [ "$nb" = 1 ] && break
  [ "$SECONDS" -ge "$deadline" ] && break
  sleep 0.1
done
"""
    + PAUSE_HOOK
)


def _real_flock_wrapper(real: str) -> str:
    return f'#!/usr/bin/env bash\nfirst="${{1:-}}"\nrc=0\n"{real}" "$@" || rc=$?\n' + PAUSE_HOOK


# `docker inspect|run` for the sync script, `docker exec` for the cycle. Every
# manage.py command is recorded in $FAKE_DIR/calls. A Lovable request waits while
# $FAKE_DIR/request-pending exists; `run-next` claims it. `lovable_sync run`
# blocks while $FAKE_DIR/block-sync-run exists.
FAKE_DOCKER = r"""#!/usr/bin/env bash
case "$1" in
  inspect)
    echo "inspect" >> "$FAKE_DIR/calls"
    if [[ "$*" == *Health* ]]; then echo healthy; else echo "sha256:0123456789abcdef0123"; fi
    exit 0 ;;
  exec | run) ;;
  *) exit 97 ;;
esac
args=("$@"); cmd=""; step=""
for ((i = 0; i < ${#args[@]}; i++)); do
  [ "${args[$i]}" = --step ] && step="${args[$((i + 1))]}"
  if [ "${args[$i]}" = manage.py ] && [ -z "$cmd" ]; then cmd="${args[*]:$((i + 1))}"; fi
done
case "$cmd" in
  nightly_outcome*)
    echo "NIGHTLY-OUTCOME {\"outcome\": \"SUCCEEDED\", \"reason\": \"\", \"step\": \"$step\"}"
    exit 0 ;;
esac
echo "$cmd" >> "$FAKE_DIR/calls"
case "$cmd" in
  "data_sync_requests poll --kind onenote"*) echo "NOT_DUE NO_REQUEST" ;;
  "data_sync_requests poll --kind lovable"*)
    if [ -e "$FAKE_DIR/request-pending" ]; then echo "DUE COMPARE"
    else echo "NOT_DUE NO_REQUEST"; fi ;;
  "data_sync_requests run-next --kind lovable"*)
    rm -f "$FAKE_DIR/request-pending"; echo '{"status": "DONE"}' ;;
  "lovable_sync due"*) echo "DUE COMPARE" ;;
  "lovable_sync run"*)
    : > "$FAKE_DIR/sync-running"
    for _ in $(seq 1 400); do [ -e "$FAKE_DIR/block-sync-run" ] || break; sleep 0.05; done ;;
  "lovable_sync observe"*) exit 2 ;;
  watch_government_agenda*) echo 'OUTCOME-JSON {"errors": 0, "judged": 0, "stored": 0}' ;;
esac
exit 0
"""
SYNC_START = "set -euo pipefail\n"
SYNC_PAUSE = (
    'while [ "${1:-}" != --on-demand ] && [ -e "$FAKE_DIR/pause-sync-start" ]; do\n'
    '  : > "$FAKE_DIR/sync-paused"; sleep 0.05\n'
    "done\n"
)
# The credentials file counts as root-owned, mode 600 (the sync script's only `stat`).
FAKE_STAT = '#!/usr/bin/env bash\necho "0 600"\n'


def _executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _epoch(moment: str) -> str:
    return str(int(dt.datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp()))


FLOCKS = [
    pytest.param("fake", id="fake-flock"),
    pytest.param(
        "real",
        id="real-flock",
        marks=pytest.mark.skipif(
            REAL_FLOCK is None,
            reason="the real flock(1) exists only on Linux (CI's Ubuntu); "
            "this host has none, so only the fake flock runs here",
        ),
    ),
]


@pytest.fixture(params=FLOCKS)
def host(request, tmp_path: Path) -> dict:
    appdata = tmp_path / "appdata"
    config = appdata / "config"
    config.mkdir(parents=True)
    (config / "register-watch.env").write_text("REGISTER=x\n", encoding="utf-8")
    (config / "juristid.env").write_text("DJANGO_SECRET_KEY=x\n", encoding="utf-8")
    (config / "lovable-sync.env").write_text("LOVABLE_SYNC_PASSWORD=x\n", encoding="utf-8")
    deploy = appdata / "repo" / "scripts" / "deploy"
    deploy.mkdir(parents=True)
    # The real sync script and the real lib.sh, where the cycle runs them from.
    # The sync's copy gets one test line before its first gate: it pauses while
    # $FAKE_DIR/pause-sync-start exists — the exact gap between the cycle's wait
    # for the runner's lock and the sync script's own hold of it.
    _executable(deploy / LIB.name, LIB.read_text(encoding="utf-8"))
    sync_text = SYNC.read_text(encoding="utf-8")
    assert sync_text.count(SYNC_START) == 1
    _executable(deploy / SYNC.name, sync_text.replace(SYNC_START, SYNC_START + SYNC_PAUSE, 1))
    (tmp_path / "capture").mkdir()
    bin_dir = tmp_path / "bin"
    if request.param == "real":
        assert REAL_FLOCK is not None
        _executable(bin_dir / "flock", _real_flock_wrapper(REAL_FLOCK))
    else:
        _executable(bin_dir / "flock", FAKE_FLOCK)
    _executable(bin_dir / "docker", FAKE_DOCKER)
    _executable(bin_dir / "stat", FAKE_STAT)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "calls").write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.pop("JURISTID_SYNC_LOCK_FD", None)
    env.pop("JURISTID_CYCLE_LOCK_FD", None)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "FAKE_DIR": str(fake),
            "JURISTID_NIGHTLY_APPDATA": str(appdata),
            "JURISTID_NIGHTLY_NOW": _epoch(WINTER_MIDNIGHT),
            "JURISTID_NIGHTLY_LOCK_WAIT_SECONDS": "2",
            "JURISTID_NIGHTLY_LOCK_RETRY_SECONDS": "1",
            "JURISTID_SYNC_APPDATA": str(appdata),
            "JURISTID_SYNC_BACKUP_ROOT": str(tmp_path / "backups"),
            "JURISTID_SYNC_CAPTURE_SOURCE": str(tmp_path / "capture"),
            "JURISTID_CYCLE_LOCK": str(tmp_path / "cycle.lock"),
            "JURISTID_SYNC_LOCK": str(tmp_path / "sync.lock"),
            "JURISTID_HOST_LOCK": str(tmp_path / "host-lock"),
        }
    )
    return {
        "kind": request.param,
        "tmp": tmp_path,
        "appdata": appdata,
        "sync_script": deploy / SYNC.name,
        "fake": fake,
        "env": env,
        "sync_lock": tmp_path / "sync.lock",
        "cycle_lock": tmp_path / "cycle.lock",
        "host_lock": tmp_path / "host-lock",
        "sync_logs": appdata / "l100" / "sync" / "logs",
        "state": appdata / "nightly" / "state",
    }


def bash(host: dict, script: str, timeout: float = 90, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and the test's own script
        [BASH, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**host["env"], **extra},
    )


def background(host: dict, script: str) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603
        [BASH, "-c", script],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=host["env"],
    )


def _q(path: Path) -> str:
    return '"' + path.as_posix() + '"'


def _wait_for(condition, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)


def calls(host: dict) -> list[str]:
    return (host["fake"] / "calls").read_text(encoding="utf-8").splitlines()


def sync_log(host: dict, label: str) -> str:
    logs = sorted(host["sync_logs"].glob(f"*-{label}.log"))
    return "\n".join(p.read_text(encoding="utf-8") for p in logs)


def hold(host: dict, lock: Path, owner_line: str | None = None) -> tuple[subprocess.Popen, Path]:
    """Another process holds LOCK (fd 9) until the returned file is removed."""
    until = host["tmp"] / f"hold-{time.monotonic_ns()}"
    until.write_text("", encoding="utf-8")
    ready = Path(str(until) + ".ready")
    write = f"printf '%s\\n' '{owner_line}' > {_q(lock)}; " if owner_line else ""
    proc = background(
        host,
        f"exec 9<>{_q(lock)}; flock -n 9 || exit 1; {write}: > {_q(ready)}; "
        f"while [ -e {_q(until)} ]; do sleep 0.05; done",
    )
    _wait_for(lambda: ready.exists() or proc.poll() is not None)
    assert proc.poll() is None, "the holder could not take the lock"
    return proc, until


def let_go(proc: subprocess.Popen, until: Path) -> None:
    until.unlink(missing_ok=True)
    proc.wait(timeout=30)


def run_sync(
    host: dict, *arguments: str, prelude: str = "", **extra: str
) -> subprocess.CompletedProcess:
    args = " ".join(arguments)
    return bash(host, f"{prelude} bash {_q(host['sync_script'])} {args}", **extra)


# -- lib.sh `lock_fd_proven` ----------------------------------------------------------


def _proven(host: dict, lock: Path, setup: str, fd: str) -> int:
    script = f". {_q(LIB)}; {setup} lock_fd_proven {_q(lock)} '{fd}'; echo rc=$?"
    out = bash(host, script).stdout
    return int(out.strip().rsplit("rc=", 1)[-1])


def test_a_descriptor_inherited_from_the_holder_is_proven(host) -> None:
    lock = host["sync_lock"]
    # The holder takes the lock on fd 5 and starts a child with the descriptor.
    script = (
        f"exec 5<>{_q(lock)}; flock -n 5 || exit 9; "
        f"bash -c '. {_q(LIB)}; lock_fd_proven {_q(lock)} 5; echo rc=$?'"
    )
    assert bash(host, script).stdout.strip() == "rc=0"


@pytest.mark.parametrize("fd", ["", "x", "5; true", "1", "2", "0", "-5", "1000"])
def test_a_descriptor_name_that_is_not_a_descriptor_is_refused(host, fd) -> None:
    setup = f"exec 5<>{_q(host['sync_lock'])}; flock -n 5;"
    assert _proven(host, host["sync_lock"], setup, fd) == 1


def test_a_variable_with_no_open_descriptor_is_refused(host) -> None:
    proc, until = hold(host, host["sync_lock"])
    try:
        assert _proven(host, host["sync_lock"], "", "5") == 1
    finally:
        let_go(proc, until)


def test_a_descriptor_on_another_file_is_refused(host) -> None:
    proc, until = hold(host, host["sync_lock"])
    other = host["tmp"] / "other.lock"
    try:
        assert _proven(host, host["sync_lock"], f"exec 5<>{_q(other)}; flock -n 5;", "5") == 1
    finally:
        let_go(proc, until)


def test_the_same_file_opened_again_by_the_claimant_is_refused(host) -> None:
    proc, until = hold(host, host["sync_lock"])
    try:
        assert _proven(host, host["sync_lock"], f"exec 5<>{_q(host['sync_lock'])};", "5") == 1
    finally:
        let_go(proc, until)


def test_a_descriptor_on_a_free_lock_is_not_proof_and_takes_nothing(host) -> None:
    lock = host["sync_lock"]
    assert _proven(host, lock, f"exec 5<>{_q(lock)};", "5") == 1
    # Nothing was left held: another process takes the lock at once.
    proc, until = hold(host, lock)
    let_go(proc, until)


def test_a_lock_file_made_again_is_refused(host) -> None:
    lock = host["sync_lock"]
    script = (
        f". {_q(LIB)}; exec 5<>{_q(lock)}; flock -n 5 || exit 9; "
        f"rm -f {_q(lock)}; : > {_q(lock)}; lock_fd_proven {_q(lock)} 5; echo rc=$?"
    )
    assert bash(host, script).stdout.strip().endswith("rc=1")


@pytest.mark.skipif(REAL_FLOCK is None, reason="needs the kernel's flock: Linux only (CI)")
def test_a_child_that_reopens_the_holders_descriptor_number_is_refused(host) -> None:
    if host["kind"] != "real":
        pytest.skip("the fake flock cannot tell a reopened fd number from an inherited one")
    lock = host["sync_lock"]
    script = (
        f"exec 5<>{_q(lock)}; flock -n 5 || exit 9; "
        f"bash -c 'exec 5<>{_q(lock)}; . {_q(LIB)}; lock_fd_proven {_q(lock)} 5; echo rc=$?'"
    )
    assert bash(host, script).stdout.strip() == "rc=1"


# -- defect 1: the Lovable hand-off, the race made deterministic ------------------------


def start_cycle(host: dict) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603
        [BASH, str(CYCLE)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=host["env"],
    )


def on_demand(host: dict, prelude: str = "", **extra: str) -> subprocess.CompletedProcess:
    return run_sync(host, "--on-demand", prelude=prelude, **extra)


def outcomes(host: dict) -> dict[str, str]:
    text = (host["state"] / f"{WINTER_NIGHT}.outcomes").read_text(encoding="utf-8")
    return {line.split()[0]: " ".join(line.split()[1:]) for line in text.splitlines()}


def test_an_on_demand_firing_in_the_hand_off_window_cannot_take_the_nights_lock(host) -> None:
    """The old gap: between the cycle's wait for the runner's lock and the sync
    script's own `flock`. The night is paused exactly there — the sync script has
    started and has not reached its first gate — and an on-demand firing runs."""
    fake = host["fake"]
    (fake / "pause-sync-start").write_text("", encoding="utf-8")
    (fake / "request-pending").write_text("", encoding="utf-8")
    cycle = start_cycle(host)
    try:
        _wait_for(lambda: (fake / "sync-paused").exists() or cycle.poll() is not None)
        assert cycle.poll() is None, cycle.communicate()
        before = calls(host)

        # The 5-minute firing lands in the window: it finds the runner busy and
        # leaves at once — no container, no poll, the request still waiting.
        firing = on_demand(host)
        assert firing.returncode == 0, firing.stdout + firing.stderr
        # A firing that claims the hand-off variable gets nothing either.
        forged = on_demand(
            host, prelude=f"exec 5<>{_q(host['sync_lock'])};", JURISTID_SYNC_LOCK_FD="5"
        )
        assert forged.returncode == 0, forged.stdout + forged.stderr
        assert calls(host) == before, "the on-demand firing ran nothing"
        assert (fake / "request-pending").exists(), "the request stays queued"
    finally:
        (fake / "pause-sync-start").unlink(missing_ok=True)
    out, err = cycle.communicate(timeout=120)
    assert cycle.returncode == 0, out + err

    # The night's sync ran under the lock it was handed.
    assert "lovable_sync run --trigger scheduled" in calls(host)
    assert "runner lock held by the caller on fd 5 (inherited, proven)" in sync_log(
        host, "scheduled"
    )
    assert "SKIP another synchronisation is running" not in sync_log(host, "scheduled")
    assert outcomes(host)["lovable"].startswith("SUCCEEDED")

    # The next firing after the night serves the request that waited.
    after = on_demand(host)
    assert after.returncode == 0, after.stdout + after.stderr
    assert "data_sync_requests run-next --kind lovable" in calls(host)
    assert not (fake / "request-pending").exists()


def test_an_on_demand_firing_while_the_nights_sync_runs_leaves_its_request_queued(host) -> None:
    fake = host["fake"]
    (fake / "block-sync-run").write_text("", encoding="utf-8")
    (fake / "request-pending").write_text("", encoding="utf-8")
    cycle = start_cycle(host)
    try:
        _wait_for(lambda: (fake / "sync-running").exists() or cycle.poll() is not None)
        assert cycle.poll() is None, cycle.communicate()
        before = calls(host)
        firing = on_demand(host)
        assert firing.returncode == 0
        assert calls(host) == before
        assert (fake / "request-pending").exists()
    finally:
        (fake / "block-sync-run").unlink(missing_ok=True)
    assert cycle.wait(timeout=120) == 0
    assert on_demand(host).returncode == 0
    assert not (fake / "request-pending").exists(), "served by the next firing"


def test_the_cycle_releases_the_runner_lock_when_its_step_ends(host) -> None:
    result = subprocess.run(  # noqa: S603
        [BASH, str(CYCLE)], cwd=ROOT, capture_output=True, text=True, timeout=120, env=host["env"]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    proc, until = hold(host, host["sync_lock"])  # free again
    let_go(proc, until)


def test_a_release_lock_taken_during_the_runner_wait_skips_the_sync_by_name(host) -> None:
    """While the cycle waited for the runner's lock, a release (an older kit that
    does not take the cycle lock) took the release lock: the cycle looks again
    after the runner's lock, never starts the sync under it, and lets the runner's
    lock go."""
    fake = host["fake"]
    (fake / "pause-after-wait").write_text("", encoding="utf-8")
    cycle = start_cycle(host)
    try:
        _wait_for(lambda: (fake / "paused").exists() or cycle.poll() is not None)
        assert cycle.poll() is None, cycle.communicate()
        host["host_lock"].mkdir()
        (host["host_lock"] / "owner").write_text("owner=release-d2 pid=1 x\n", encoding="utf-8")
    finally:
        (fake / "pause-after-wait").unlink(missing_ok=True)
    cycle.wait(timeout=120)
    steps = outcomes(host)
    assert steps["lovable"].startswith("SKIPPED_UNEXPECTED")
    assert "owner=release-d2" in steps["lovable"]
    assert "lovable_sync run --trigger scheduled" not in calls(host)
    assert host["host_lock"].exists(), "the release's lock is never touched"
    proc, until = hold(host, host["sync_lock"])  # the runner's lock was let go
    let_go(proc, until)


# -- defect 1: the sync script refuses a claim it cannot prove ---------------------------


def _scheduled_ran(host: dict) -> bool:
    return "lovable_sync run --trigger scheduled" in calls(host)


def test_a_forged_variable_alone_does_not_get_the_runner_lock(host) -> None:
    proc, until = hold(host, host["sync_lock"])
    try:
        result = run_sync(host, JURISTID_SYNC_LOCK_FD="5")
    finally:
        let_go(proc, until)
    assert result.returncode == 0
    assert not _scheduled_ran(host)
    log = sync_log(host, "scheduled")
    assert "is not a proven hold" in log and "SKIP another synchronisation is running" in log


def test_a_descriptor_on_the_wrong_file_does_not_get_the_runner_lock(host) -> None:
    other = host["tmp"] / "other.lock"
    proc, until = hold(host, host["sync_lock"])
    try:
        result = run_sync(
            host, prelude=f"exec 5<>{_q(other)}; flock -n 5 || exit 9;", JURISTID_SYNC_LOCK_FD="5"
        )
    finally:
        let_go(proc, until)
    assert result.returncode == 0
    assert not _scheduled_ran(host)
    assert "SKIP another synchronisation is running" in sync_log(host, "scheduled")


def test_the_lock_file_opened_by_the_claimant_does_not_get_the_runner_lock(host) -> None:
    proc, until = hold(host, host["sync_lock"])
    try:
        result = run_sync(
            host, prelude=f"exec 5<>{_q(host['sync_lock'])};", JURISTID_SYNC_LOCK_FD="5"
        )
    finally:
        let_go(proc, until)
    assert result.returncode == 0
    assert not _scheduled_ran(host)
    assert "SKIP another synchronisation is running" in sync_log(host, "scheduled")


def test_an_unproven_claim_on_a_free_lock_takes_the_lock_as_before(host) -> None:
    result = run_sync(host, prelude=f"exec 5<>{_q(host['sync_lock'])};", JURISTID_SYNC_LOCK_FD="5")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _scheduled_ran(host)
    assert "is not a proven hold" in sync_log(host, "scheduled")


def test_a_proven_hand_off_runs_the_sync_and_keeps_the_callers_lock(host) -> None:
    lock = host["sync_lock"]
    result = run_sync(
        host,
        prelude=f"exec 5<>{_q(lock)}; flock -n 5 || exit 9;",
        JURISTID_SYNC_LOCK_FD="5",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _scheduled_ran(host)
    assert "(inherited, proven)" in sync_log(host, "scheduled")


# -- defect 2: the preflight and the cycle lock ----------------------------------------


def preflight(host: dict, prelude: str = "", **extra: str) -> str:
    """The preflight's «The nightly cycle» line (the rest fails on this fake host)."""
    repo = host["tmp"] / "not-a-checkout"
    repo.mkdir(exist_ok=True)
    command = (
        f"{prelude} bash {_q(PREFLIGHT)} --repo {_q(repo)} --target {'0' * 40} "
        f"--compose-file {_q(COMPOSE)}"
    )
    result = subprocess.run(  # noqa: S603 - FAIL lines go to stderr: keep the order
        [BASH, "-c", command],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=90,
        env={**host["env"], **extra},
    )
    text = result.stdout
    section = text.split("== The nightly cycle", 1)[1].split("== Verdict", 1)[0]
    lines = [line for line in section.splitlines() if line.strip()]
    assert len(lines) == 1, text
    return lines[0]


def release(host: dict, name: str = "release-d2") -> str:
    """What a release kit does first (lib.sh `take_cycle_lock`, fd 7, to its exit)."""
    return f". {_q(LIB)}; take_cycle_lock {name} || exit 9;"


def test_preflight_with_no_lock_held_passes(host) -> None:
    assert preflight(host).startswith("ok   the nightly cycle is not running")


def test_preflight_refuses_while_the_nightly_cycle_holds_the_lock(host) -> None:
    proc, until = hold(host, host["cycle_lock"], "owner=nightly-cycle night=2026-01-15 pid=4242 x")
    try:
        assert preflight(host).startswith(
            "FAIL: the nightly cycle lock is held (owner=nightly-cycle"
        )
        # Claiming the lock without holding it changes nothing.
        claim = preflight(host, JURISTID_CYCLE_LOCK_FD="7")
        assert claim.startswith("FAIL: JURISTID_CYCLE_LOCK_FD=7 is not this release's hold"), claim
        opened = preflight(
            host, prelude=f"exec 7<>{_q(host['cycle_lock'])};", JURISTID_CYCLE_LOCK_FD="7"
        )
        assert opened.startswith("FAIL: JURISTID_CYCLE_LOCK_FD=7"), opened
    finally:
        let_go(proc, until)


def test_preflight_refuses_while_another_release_holds_the_lock(host) -> None:
    proc, until = hold(host, host["cycle_lock"], "owner=release-d1 pid=4242 x")
    try:
        assert preflight(host).startswith("FAIL: the nightly cycle lock is held (owner=release-d1")
        # A second release cannot take the lock, and its own open of the file is no proof.
        assert bash(host, release(host)).returncode == 9
        claim = preflight(
            host, prelude=f"exec 7<>{_q(host['cycle_lock'])};", JURISTID_CYCLE_LOCK_FD="7"
        )
        assert claim.startswith("FAIL: JURISTID_CYCLE_LOCK_FD=7"), claim
    finally:
        let_go(proc, until)


def test_preflight_called_by_the_release_that_holds_the_lock_passes(host) -> None:
    line = preflight(host, prelude=release(host), JURISTID_CYCLE_LOCK_FD="7")
    assert line.startswith(
        "ok   this release holds the nightly cycle lock (fd 7, inherited and proven)"
    ), line
    # Without the hand-off the same release would refuse itself — the defect.
    assert preflight(host, prelude=release(host)).startswith(
        "FAIL: the nightly cycle lock is held (owner=release-d2"
    )


def test_preflight_does_not_trust_stale_owner_metadata(host) -> None:
    lock = host["cycle_lock"]
    # A dead release's owner line, nobody holding the lock.
    lock.write_text("owner=release-d2 pid=999999 2026-01-14T10:00:00Z\n", encoding="utf-8")
    assert preflight(host).startswith("ok   the nightly cycle is not running")
    claim = preflight(host, prelude=f"exec 7<>{_q(lock)};", JURISTID_CYCLE_LOCK_FD="7")
    assert claim.startswith("FAIL: JURISTID_CYCLE_LOCK_FD=7"), claim
    proc, until = hold(host, lock)  # and the failed claim left nothing held
    let_go(proc, until)
    # A release- owner line while someone else (the cycle) holds the lock.
    proc, until = hold(host, lock, "owner=release-d2 pid=999999 2026-01-14T10:00:00Z")
    try:
        assert preflight(host).startswith("FAIL: the nightly cycle lock is held (owner=release-d2")
        claim = preflight(host, prelude=f"exec 7<>{_q(lock)};", JURISTID_CYCLE_LOCK_FD="7")
        assert claim.startswith("FAIL: JURISTID_CYCLE_LOCK_FD=7"), claim
    finally:
        let_go(proc, until)


def test_preflight_after_an_interrupted_release(host) -> None:
    lock = host["cycle_lock"]
    until = host["tmp"] / "release-running"
    until.write_text("", encoding="utf-8")
    killed = background(host, f"{release(host)} while [ -e {_q(until)} ]; do sleep 0.05; done")
    _wait_for(lambda: lock.exists() and "release-d2" in lock.read_text(encoding="utf-8"))
    assert preflight(host).startswith("FAIL: the nightly cycle lock is held (owner=release-d2")
    if sys.platform == "win32":
        killed.kill()
    else:
        os.kill(killed.pid, signal.SIGKILL)
    killed.wait(timeout=30)
    until.unlink()
    # The holder is gone: the lock is free again (once a child it left behind has
    # ended), and its owner line is only history.
    _wait_for(lambda: preflight(host).startswith("ok   the nightly cycle is not running"))
    assert "release-d2" in lock.read_text(encoding="utf-8")
    claim = preflight(host, prelude=f"exec 7<>{_q(lock)};", JURISTID_CYCLE_LOCK_FD="7")
    assert claim.startswith("FAIL: JURISTID_CYCLE_LOCK_FD=7"), claim
    # The next release takes the lock properly and its preflight passes.
    line = preflight(host, prelude=release(host, "release-d2-again"), JURISTID_CYCLE_LOCK_FD="7")
    assert line.startswith("ok   this release holds the nightly cycle lock"), line
