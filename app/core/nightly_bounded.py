"""One nightly step, run inside the web container under a hard execution budget (ADR 0234).

    python -m app.core.nightly_bounded run --step register-watch --seconds 4200 \
        --grace 300 -- watch_registers
    python -m app.core.nightly_bounded status --step register-watch
    python -m app.core.nightly_bounded stop --step register-watch

The host's nightly cycle (`scripts/deploy/juristid-nightly-cycle.sh`) starts every
in-container step through ``run``. Killing the host's ``docker exec`` does not stop
the process it started inside the container, so the budget is enforced *here*,
where the work runs:

* the step runs as ``python -m app.core.nightly_bounded child -- <command>`` in a
  **new session** (its own process group), with ``JURISTID_STEP_DEADLINE`` set to
  the budget less ``--grace``: every register call (`app.register_watch.sources.http`)
  and every model request stops by then, and the run closes itself as partial;
* at the budget the whole group gets SIGTERM (the child turns it into
  `StepTerminated`, so a run record is closed as stopped and an open transaction
  rolls back), ``--kill-after`` seconds later SIGKILL, and the supervisor then
  **verifies** that no process of the group is left before it answers;
* a pid file (``/tmp/juristid-nightly/<step>.json``) names the group, its leader's
  start time and the boot, so ``status`` and ``stop`` can find it from a second
  ``docker exec`` — and never mistake a recycled pid for it;
* a step whose previous run is still alive is not started again (``busy``).

Exit codes of ``run``: the step's own code when it ended by itself; `TIMEOUT` (124,
as GNU ``timeout``) when it was stopped at its budget and is verifiably gone;
`BUSY` (75) when the previous run of the step is still alive; `STUCK` (70) when the
group could not be verified gone (the pid file stays, and the host keeps its lock).
``status``: 0 gone, 3 alive. ``stop``: 0 gone, 70 stuck.

Standard library only, no Django: supervising must not depend on the database or
the settings it may be waiting for. Linux only (``/proc``); the tests that run it
for real are skipped elsewhere.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

TIMEOUT = 124
BUSY = 75
STUCK = 70
ALIVE = 3
#: The child's exit code after SIGTERM was turned into `StepTerminated`.
TERMINATED = 143
PID_DIR_ENV = "JURISTID_NIGHTLY_PID_DIR"
DEFAULT_PID_DIR = "/tmp/juristid-nightly"  # noqa: S108 - the container's own /tmp, 0700
STEP_DEADLINE_ENV = "JURISTID_STEP_DEADLINE"  # = app.register_watch.sources.http.STEP_DEADLINE_ENV
#: The machine line every verb prints last.
PREFIX = "NIGHTLY-BOUNDED "
#: How long a SIGKILLed group may take to disappear before it is called stuck.
VANISH_SECONDS = 60.0
POLL_SECONDS = 0.2
#: POSIX signals, read by name so the module still imports where they do not exist.
SIGKILL: int = getattr(signal, "SIGKILL", 9)
SIGHUP: int = getattr(signal, "SIGHUP", 1)


class StepTerminated(BaseException):
    """SIGTERM in a supervised step: unwinds like an interrupt, so ``finally`` and
    ``except BaseException`` blocks close the run's record and roll back what is open."""


# -- the process group -----------------------------------------------------------------


def _pid_dir() -> Path:
    return Path(os.environ.get(PID_DIR_ENV) or DEFAULT_PID_DIR)


def _pid_file(step: str) -> Path:
    if not step or not all(c.isalnum() or c in "-_" for c in step):
        raise SystemExit(f"not a step name: {step!r}")
    return _pid_dir() / f"{step}.json"


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _stat(pid: int) -> tuple[str, int, int] | None:
    """``(state, process group, start time in ticks)`` of ``pid``, or ``None`` if gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    # The command name is in parentheses and may hold spaces: split after it.
    fields = raw[raw.rfind(")") + 2 :].split()
    try:
        return fields[0], int(fields[2]), int(fields[19])
    except (IndexError, ValueError):
        return None


def members(record: dict[str, Any]) -> list[int]:
    """The live processes of the recorded group: same group, started no earlier than
    its leader, in this boot. Zombies are already dead and are not counted."""
    pgid = int(record.get("pgid") or 0)
    if pgid <= 1 or record.get("boot") != _boot_id():
        return []
    since = int(record.get("start_ticks") or 0)
    proc = Path("/proc")
    if not since or not proc.is_dir():
        return []
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        stat = _stat(int(entry.name))
        if stat is None:
            continue
        state, group, started = stat
        if group == pgid and started >= since and state not in ("Z", "X"):
            found.append(int(entry.name))
    return found


def _signal_all(pids: Sequence[int], sig: int) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)


def _wait_gone(record: dict[str, Any], seconds: float, reap: subprocess.Popen[Any] | None) -> bool:
    end = time.monotonic() + seconds
    while True:
        if reap is not None:
            reap.poll()
        if not members(record):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(POLL_SECONDS)


def terminate(
    record: dict[str, Any], kill_after: float, reap: subprocess.Popen[Any] | None = None
) -> bool:
    """SIGTERM the group, SIGKILL what is left after ``kill_after``; True once verifiably gone."""
    _signal_all(members(record), signal.SIGTERM)
    if _wait_gone(record, kill_after, reap):
        return True
    _signal_all(members(record), SIGKILL)
    return _wait_gone(record, VANISH_SECONDS, reap)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _emit(**fields: Any) -> None:
    print(PREFIX + json.dumps(fields, sort_keys=True), flush=True)


# -- the verbs -------------------------------------------------------------------------


def run(step: str, seconds: float, grace: float, kill_after: float, command: list[str]) -> int:
    pid_file = _pid_file(step)
    pid_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = _load(pid_file) if pid_file.exists() else None
    if previous is not None and members(previous):
        _emit(step=step, result="busy", rc=BUSY, pgid=previous.get("pgid"))
        return BUSY
    started = time.monotonic()
    hard = started + seconds
    soft_epoch = time.time() + max(seconds - grace, 0.0)
    env = dict(os.environ)
    env[STEP_DEADLINE_ENV] = f"{soft_epoch:.3f}"
    child = subprocess.Popen(  # noqa: S603 - our own module and the command the host named
        [sys.executable, "-m", "app.core.nightly_bounded", "child", "--", *command],
        start_new_session=True,
        env=env,
    )
    stat = _stat(child.pid) or _stat(os.getpid())  # ended already: bound by our own start
    record = {
        "step": step,
        "pgid": child.pid,
        "start_ticks": stat[2] if stat else 0,
        "boot": _boot_id(),
        "supervisor": os.getpid(),
        "command": command[:1],
        "budget_seconds": seconds,
    }
    tmp = pid_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    tmp.replace(pid_file)

    asked: list[int] = []

    def ask_to_stop(signum: int, _frame: Any) -> None:
        asked.append(signum)

    for sig in (signal.SIGTERM, signal.SIGINT, SIGHUP):
        signal.signal(sig, ask_to_stop)

    result = "finished"
    while child.poll() is None:
        if asked or time.monotonic() >= hard:
            result = "stopped" if asked else "timeout"
            break
        time.sleep(POLL_SECONDS)
    if asked:
        # Stopped from outside (`stop`, or the host): the step may have ended from
        # the same SIGTERM before this loop saw the request. Not its own ending.
        result = "stopped"
    gone = terminate(record, kill_after, reap=child)
    with contextlib.suppress(subprocess.TimeoutExpired):
        child.wait(timeout=1)
    elapsed = round(time.monotonic() - started, 1)
    if not gone:
        _emit(step=step, result="stuck", rc=STUCK, seconds=elapsed, pgid=record["pgid"])
        return STUCK
    pid_file.unlink(missing_ok=True)
    if result == "finished":
        rc = child.returncode if child.returncode is not None else STUCK
    else:
        rc = TIMEOUT
    _emit(step=step, result=result, rc=rc, seconds=elapsed, budget=seconds)
    return rc


def status(step: str) -> int:
    record = _load(_pid_file(step))
    alive = members(record) if record else []
    _emit(step=step, result="alive" if alive else "gone", processes=len(alive))
    return ALIVE if alive else 0


def stop(step: str, kill_after: float) -> int:
    pid_file = _pid_file(step)
    record = _load(pid_file)
    if record is None or not members(record):
        pid_file.unlink(missing_ok=True)
        _emit(step=step, result="gone")
        return 0
    # The supervisor first, so it cannot outlive what it supervised.
    supervisor = int(record.get("supervisor") or 0)
    if supervisor > 1:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(supervisor, signal.SIGTERM)
    if terminate(record, kill_after):
        pid_file.unlink(missing_ok=True)
        _emit(step=step, result="stopped")
        return 0
    _emit(step=step, result="stuck", processes=len(members(record)))
    return STUCK


def child(command: list[str]) -> int:
    """The step itself: SIGTERM becomes `StepTerminated`, then Django runs ``command``."""

    def terminated(_signum: int, _frame: Any) -> None:
        # Once: a second SIGTERM while unwinding takes the default action.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        raise StepTerminated("täitmise ajapiir ületatud; töö peatati")

    signal.signal(signal.SIGTERM, terminated)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    try:
        execute_from_command_line(["manage.py", *command])
    except StepTerminated:
        return TERMINATED
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.core.nightly_bounded")
    verbs = parser.add_subparsers(dest="verb", required=True)
    one = verbs.add_parser("run")
    one.add_argument("--step", required=True)
    one.add_argument("--seconds", type=float, required=True)
    one.add_argument("--grace", type=float, default=300.0)
    one.add_argument("--kill-after", type=float, default=30.0)
    one.add_argument("command", nargs=argparse.REMAINDER)
    for name in ("status", "stop"):
        verb = verbs.add_parser(name)
        verb.add_argument("--step", required=True)
        verb.add_argument("--kill-after", type=float, default=30.0)
    inner = verbs.add_parser("child")
    inner.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(getattr(args, "command", []) or [])
    if command[:1] == ["--"]:
        command = command[1:]
    if args.verb == "run":
        if not command or args.seconds <= 0:
            parser.error("run needs --seconds > 0 and a command after --")
        return run(args.step, args.seconds, args.grace, args.kill_after, command)
    if args.verb == "status":
        return status(args.step)
    if args.verb == "stop":
        return stop(args.step, args.kill_after)
    return child(command)


if __name__ == "__main__":
    sys.exit(main())
