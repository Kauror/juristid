"""SEC-010: a nightly step inside the container really stops at its budget (ADR 0234).

`app.core.nightly_bounded` runs for real here, as a process tree on this machine,
with Django's own ``shell -c`` as the step: no database, no register, no Docker.
What is proved is what killing the host's ``docker exec`` could not do — the work
itself (the step, and anything it started) is terminated, verified gone, and only
then answered for; a step whose previous run is still alive is not started twice;
and a supervisor that is itself killed leaves something ``status`` finds and
``stop`` ends. Linux only (``/proc``, process groups); CI runs it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from django.conf import settings

from app.core import nightly_bounded

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="process groups and /proc: Linux only"
)

ROOT = Path(settings.BASE_DIR)


@pytest.fixture
def pid_dir(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "pids"
    monkeypatch.setenv(nightly_bounded.PID_DIR_ENV, str(directory))
    return directory


def _env(pid_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env[nightly_bounded.PID_DIR_ENV] = str(pid_dir)
    env.setdefault("DJANGO_SECRET_KEY", "nightly-bounded-test")
    return env


def _bounded(pid_dir: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own module
        [sys.executable, "-m", "app.core.nightly_bounded", *args],
        cwd=ROOT,
        env=_env(pid_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _start(pid_dir: Path, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - our own module
        [sys.executable, "-m", "app.core.nightly_bounded", *args],
        cwd=ROOT,
        env=_env(pid_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _line(output: str) -> dict:
    lines = [line for line in output.splitlines() if line.startswith(nightly_bounded.PREFIX)]
    assert lines, output
    return json.loads(lines[-1].removeprefix(nightly_bounded.PREFIX))


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state not in ("Z", "X")


def _wait_for(path: Path, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path} never appeared"
        time.sleep(0.05)


def _shell(code: str) -> list[str]:
    return ["shell", "-c", code]


def test_a_step_that_finishes_answers_with_its_own_exit_code(pid_dir):
    result = _bounded(
        pid_dir,
        "run",
        "--step",
        "register-watch",
        "--seconds",
        "30",
        "--",
        *_shell("import sys; sys.exit(3)"),
    )
    assert result.returncode == 3, result.stderr
    assert _line(result.stdout)["result"] == "finished"
    assert not (pid_dir / "register-watch.json").exists()


def test_the_step_gets_the_soft_deadline_before_its_budget(pid_dir, tmp_path):
    seen = tmp_path / "deadline"
    before = time.time()
    _bounded(
        pid_dir,
        "run",
        "--step",
        "register-sweep",
        "--seconds",
        "100",
        "--grace",
        "30",
        "--",
        *_shell(f"import os; open({str(seen)!r}, 'w').write(os.environ['JURISTID_STEP_DEADLINE'])"),
    )
    deadline = float(seen.read_text())
    assert before + 69 <= deadline <= time.time() + 71


def test_a_step_past_its_budget_is_terminated_with_what_it_started(pid_dir, tmp_path):
    pids = tmp_path / "pids.txt"
    code = (
        "import os, subprocess, time\n"
        "child = subprocess.Popen(['sleep', '120'])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "time.sleep(120)\n"
    )
    start = time.monotonic()
    result = _bounded(
        pid_dir,
        "run",
        "--step",
        "riigikogu",
        "--seconds",
        "2",
        "--grace",
        "1",
        "--kill-after",
        "2",
        "--",
        *_shell(code),
    )
    elapsed = time.monotonic() - start

    assert result.returncode == nightly_bounded.TIMEOUT, result.stderr
    assert _line(result.stdout)["result"] == "timeout"
    assert elapsed < 2 + 2 + 5
    step, grandchild = (int(one) for one in pids.read_text().split())
    assert not _alive(step) and not _alive(grandchild)
    assert not (pid_dir / "riigikogu.json").exists()


def test_a_step_that_ignores_sigterm_is_killed(pid_dir, tmp_path):
    pid_file = tmp_path / "pid"
    code = (
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n"
    )
    result = _bounded(
        pid_dir,
        "run",
        "--step",
        "government-agenda",
        "--seconds",
        "2",
        "--grace",
        "1",
        "--kill-after",
        "1",
        "--",
        *_shell(code),
    )
    assert result.returncode == nightly_bounded.TIMEOUT
    assert not _alive(int(pid_file.read_text()))


def test_sigterm_unwinds_the_step_so_its_record_is_closed(pid_dir, tmp_path):
    """`StepTerminated` reaches ``except BaseException`` / ``finally`` (watch.run's ``katkes``)."""
    seen = tmp_path / "seen"
    code = (
        "import time\n"
        "try:\n"
        "    time.sleep(120)\n"
        "except BaseException as error:\n"
        f"    open({str(seen)!r}, 'w').write(type(error).__name__)\n"
        "    raise\n"
    )
    result = _bounded(
        pid_dir,
        "run",
        "--step",
        "register-watch",
        "--seconds",
        "2",
        "--grace",
        "1",
        "--kill-after",
        "5",
        "--",
        *_shell(code),
    )
    assert result.returncode == nightly_bounded.TIMEOUT
    assert seen.read_text() == "StepTerminated"


def test_a_finished_step_leaves_nothing_behind(pid_dir, tmp_path):
    pid_file = tmp_path / "pid"
    code = (
        "import subprocess\n"
        "child = subprocess.Popen(['sleep', '120'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
    )
    result = _bounded(
        pid_dir,
        "run",
        "--step",
        "register-sweep",
        "--seconds",
        "30",
        "--kill-after",
        "1",
        "--",
        *_shell(code),
    )
    assert result.returncode == 0
    assert not _alive(int(pid_file.read_text()))


def test_a_step_still_running_is_not_started_twice_and_stop_ends_it(pid_dir, tmp_path):
    ready = tmp_path / "ready"
    first = _start(
        pid_dir,
        "run",
        "--step",
        "register-watch",
        "--seconds",
        "60",
        "--kill-after",
        "2",
        "--",
        *_shell(f"import time; open({str(ready)!r}, 'w').close(); time.sleep(120)"),
    )
    try:
        _wait_for(ready)
        second = _bounded(
            pid_dir,
            "run",
            "--step",
            "register-watch",
            "--seconds",
            "60",
            "--",
            *_shell("print('should not run')"),
        )
        assert second.returncode == nightly_bounded.BUSY
        assert "should not run" not in second.stdout

        assert _bounded(pid_dir, "status", "--step", "register-watch").returncode == (
            nightly_bounded.ALIVE
        )
        stopped = _bounded(pid_dir, "stop", "--step", "register-watch", "--kill-after", "2")
        assert stopped.returncode == 0, stopped.stdout
        assert _bounded(pid_dir, "status", "--step", "register-watch").returncode == 0
        assert first.wait(timeout=30) == nightly_bounded.TIMEOUT
    finally:
        if first.poll() is None:
            first.kill()


def test_a_killed_supervisor_leaves_work_that_status_finds_and_stop_ends(pid_dir, tmp_path):
    """What a host-side kill can leave behind: the step keeps running without its
    supervisor. `status` sees it; `stop` ends it and verifies it gone."""
    pid_file = tmp_path / "pid"
    supervisor = _start(
        pid_dir,
        "run",
        "--step",
        "riigikogu",
        "--seconds",
        "60",
        "--",
        *_shell(
            f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
            "time.sleep(120)"
        ),
    )
    _wait_for(pid_file)
    _wait_for(pid_dir / "riigikogu.json")
    supervisor.send_signal(signal.SIGKILL)
    supervisor.wait(timeout=10)
    step = int(pid_file.read_text())
    assert _alive(step), "the step outlives its killed supervisor"

    assert _bounded(pid_dir, "status", "--step", "riigikogu").returncode == nightly_bounded.ALIVE
    assert _bounded(pid_dir, "stop", "--step", "riigikogu", "--kill-after", "2").returncode == 0
    assert not _alive(step)
    assert _bounded(pid_dir, "status", "--step", "riigikogu").returncode == 0


def test_a_stale_pid_file_never_names_somebody_elses_process(pid_dir):
    """A record from another boot, or for a process started before it, is not «alive»."""
    pid_dir.mkdir(parents=True)
    own = os.getpgrp()
    record = {"pgid": own, "start_ticks": 1, "boot": "another-boot", "supervisor": 0}
    (pid_dir / "register-watch.json").write_text(json.dumps(record))
    assert _bounded(pid_dir, "status", "--step", "register-watch").returncode == 0
    record["boot"] = nightly_bounded._boot_id()
    record["start_ticks"] = 10**15  # «started» after everything alive now
    (pid_dir / "register-watch.json").write_text(json.dumps(record))
    assert _bounded(pid_dir, "stop", "--step", "register-watch").returncode == 0
    assert _alive(os.getpid())
