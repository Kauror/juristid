"""r96 final review of the parser boundary: the SERVICE path, end to end.

`tests/test_parser_isolation.py` proves the kernel guarantees on the
subprocess path (`run_child`) and the socket protocol with one quick file. This
module runs the real thing production runs — `python -m
app.documents.extraction.isolation_server` as its own process — and asks it,
through `parse_isolated` in `service` mode (the code `intake-reader` and `web`
run), for the files that go wrong:

* every kind of stop — deadline, CPU, output cap, memory ceiling, native crash,
  a forked tree — arrives as the named failure, and the NEXT, normal file is
  read by the same service (a poisoned file does not stop the queue);
* the child's whole tree is gone afterwards and the scratch directory and the
  socket directory hold nothing but the socket;
* SIGTERM (what `docker stop` sends) in the middle of a file kills the tree
  and ends the service promptly; a service that was killed is "unavailable"
  (no verdict on the file) until it is started again, and then works;
* a caller that gives up mid-file does not wedge the queue;
* the socket is owner-only.

Linux only: setrlimit, process groups and Unix sockets are what is tested.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.documents.extraction import isolation
from app.documents.extraction.base import SourceFile, registry
from app.documents.extraction.errors import ExtractionFailed

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="setrlimit, process groups and Unix sockets are Linux semantics",
)

TEXT = "text/plain"
HOOK = "juristid-parser-test-hook-"
ROOT = Path(__file__).resolve().parent.parent


class RunningService:
    """The parser service as its own process, the way the container runs it."""

    def __init__(self, directory: Path) -> None:
        self.socket_path = directory / "run" / "parser.sock"
        self.scratch = directory / "scratch"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "JURISTID_PARSER_TEST_HOOKS": "1",
            "JURISTID_PARSER_MAX_WALL_SECONDS": "30",
            "JURISTID_PARSER_MAX_CPU_SECONDS": "30",
        }
        self.process = subprocess.Popen(  # noqa: S603 - fixed argv
            [
                sys.executable,
                "-m",
                "app.documents.extraction.isolation_server",
                "--socket",
                str(self.socket_path),
                "--scratch",
                str(self.scratch),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        from app.documents.extraction.isolation_server import check

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if check(self.socket_path) == 0:
                return
            if self.process.poll() is not None:
                raise AssertionError("the parser service exited while starting")
            time.sleep(0.1)
        raise AssertionError("the parser service never listened")

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=10)
        self.process = None

    def leftovers(self) -> tuple[list[str], list[str]]:
        scratch = sorted(p.name for p in self.scratch.iterdir()) if self.scratch.is_dir() else []
        run = sorted(p.name for p in self.socket_path.parent.iterdir())
        return scratch, run


@pytest.fixture
def service(tmp_path, settings):
    running = RunningService(tmp_path)
    running.start()
    settings.DOCUMENT_PARSER_ISOLATION = "service"
    settings.DOCUMENT_PARSER_SOCKET = str(running.socket_path)
    settings.EXTRACTION_PARSER_TEST_HOOKS = True
    try:
        yield running
    finally:
        running.stop()


def read(name: str, content: bytes = b"tavaline tekst", **kwargs):
    parser = registry.for_mime_type(TEXT)
    return isolation.parse_isolated(
        parser, SourceFile(content, name, TEXT), budget="intake", **kwargs
    )


def text_of(result) -> str:
    return "".join(f.text for payload in result.derivatives for f in payload.fragments)


def sleepers(service: RunningService) -> set[int]:
    """`sleep 600` processes started by THIS service's children.

    Told apart by the job directory in their environment (HOME is the child's
    private directory under this service's scratch), so another test's
    sleepers, in another worker, are never counted.
    """
    marker = str(service.scratch).encode()
    found = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if cmdline[:2] != [b"sleep", b"600"]:
            continue
        try:
            environment = (entry / "environ").read_bytes()
        except OSError:
            continue
        if marker in environment:
            found.add(int(entry.name))
    return found


def assert_normal_file_still_read(service: RunningService) -> None:
    assert "Järgmine fail" in text_of(read("jargmine.txt", "Järgmine fail".encode()))
    scratch, run = service.leftovers()
    assert scratch == []
    assert run == ["parser.sock"]


@pytest.mark.parametrize(
    ("hook", "code", "overrides"),
    [
        ("hang", isolation.TIMEOUT, {"wall_seconds": 3}),
        ("spin", isolation.CPU_LIMIT, {"wall_seconds": 2}),
        ("crash", isolation.CRASHED, {}),
        ("flood", isolation.OUTPUT_LIMIT, {"output": 1024**2}),
        ("memory", isolation.MEMORY_LIMIT, {"memory": 1024**3}),
    ],
)
def test_a_poisoned_file_is_stopped_with_its_name_and_the_next_file_is_read(
    service, settings, hook, code, overrides
):
    """The deadline, CPU, output and memory limits through the socket service,
    each followed by a normal file read by the same service process."""
    if "output" in overrides:
        settings.DOCUMENT_PARSER_INTAKE_OUTPUT_BYTES = overrides["output"]
    if "memory" in overrides:
        settings.DOCUMENT_PARSER_MEMORY_BYTES = overrides["memory"]
    pid = service.process.pid
    started = time.monotonic()
    with pytest.raises(ExtractionFailed) as stopped:
        read(f"{HOOK}{hook}.txt", wall_seconds=overrides.get("wall_seconds"))
    assert stopped.value.code == code
    assert "Originaal on alles" in stopped.value.detail
    if "wall_seconds" in overrides:
        # Stopped at its own deadline, not at the client's socket budget.
        assert time.monotonic() - started < overrides["wall_seconds"] + 12
    assert service.process.poll() is None and service.process.pid == pid
    assert_normal_file_still_read(service)


def test_the_whole_tree_of_a_file_that_ran_out_of_time_is_gone(service):
    before = sleepers(service)
    with pytest.raises(ExtractionFailed) as stopped:
        read(f"{HOOK}fork-hang.txt", wall_seconds=3)
    assert stopped.value.code == isolation.TIMEOUT
    deadline = time.monotonic() + 5
    while sleepers(service) - before and time.monotonic() < deadline:
        time.sleep(0.1)
    assert sleepers(service) - before == set()
    assert_normal_file_still_read(service)


def test_sigterm_in_the_middle_of_a_file_kills_its_tree_and_ends_the_service(service, settings):
    """`docker stop`: the child tree dies with the service, the caller is told
    there was no verdict (so the file is handed back, not failed)."""
    before = sleepers(service)
    outcome: list[BaseException] = []

    def ask() -> None:
        try:
            read(f"{HOOK}fork-hang.txt", wall_seconds=25)
        except BaseException as error:
            outcome.append(error)

    caller = threading.Thread(target=ask)
    caller.start()
    deadline = time.monotonic() + 10
    while len(sleepers(service) - before) < 2 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert len(sleepers(service) - before) == 2, "the file never reached the parser"
    started = time.monotonic()
    service.process.send_signal(signal.SIGTERM)
    service.process.wait(timeout=15)
    assert time.monotonic() - started < 10
    caller.join(timeout=15)
    assert [type(e) for e in outcome] == [isolation.ParserUnavailable]
    deadline = time.monotonic() + 5
    while sleepers(service) - before and time.monotonic() < deadline:
        time.sleep(0.1)
    assert sleepers(service) - before == set()


def test_a_killed_service_is_no_verdict_on_the_file_and_works_again_when_restarted(service):
    assert "före" in text_of(read("a.txt", "före".encode()))
    service.process.send_signal(signal.SIGKILL)
    service.process.wait(timeout=10)
    # The stale socket file is still there: connecting is refused, which is
    # "unavailable" — never a failure written against the file.
    assert service.socket_path.exists()
    with pytest.raises(isolation.ParserUnavailable):
        read("b.txt")
    # What the container's restart policy does: a new process, same path.
    service.start()
    assert "efter" in text_of(read("c.txt", b"efter"))


def test_a_caller_that_gives_up_mid_file_does_not_wedge_the_queue(service):
    header = {
        "op": "parse",
        "filename": f"{HOOK}hang.txt",
        "mime_type": TEXT,
        "settings": {**isolation.extraction_settings(), "EXTRACTION_PARSER_TEST_HOOKS": True},
        "text_only": True,
        "limits": isolation.ChildLimits(
            wall_seconds=2,
            cpu_seconds=2,
            memory_bytes=2 * 1024**3,
            file_bytes=64 * 1024**2,
            output_bytes=1024**2,
        ).as_dict(),
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as abandoned:
        abandoned.connect(str(service.socket_path))
        isolation.send_request(abandoned, header, b"x")
        time.sleep(0.5)  # the child is running; now the caller walks away
    started = time.monotonic()
    # Queued behind the abandoned file for at most its own deadline, then read.
    assert "efter" in text_of(read("efter.txt", b"efter"))
    assert time.monotonic() - started < 15
    assert_normal_file_still_read(service)


def test_the_socket_belongs_to_its_owner_alone(service):
    mode = stat.S_IMODE(service.socket_path.lstat().st_mode)
    assert stat.S_ISSOCK(service.socket_path.lstat().st_mode)
    assert mode & 0o077 == 0, oct(mode)
    assert service.socket_path.lstat().st_uid == os.geteuid()
