"""Run the shards a CI runner hosts, at the same time and each in its own world.

Two subcommands, both driven by ``ci_slices.py`` so that the workflow states
nothing a test cannot check:

``browser-world``
    For each browser shard this runner hosts: create its two databases, migrate
    and seed the deterministic world, migrate the personal sign-in database, and
    start its three servers (application, shared gate, personal sign-in) on its
    own ports. The shards are prepared concurrently, and inside a shard the two
    databases migrate concurrently — they are separate databases, so neither
    waits on the other. The servers outlive this command, exactly as the
    ``nohup … &`` servers of a single-shard runner outlived their step.

``run``
    Start the given pytest command once per hosted shard, with ``{shard}``
    replaced by the shard's index and the shard's environment laid over the
    job's. Output is streamed live with a ``[suite N]`` prefix and kept in a
    log per shard. The command fails if any shard fails, and says which.

Usage, as the workflow writes it::

    uv run python scripts/ci/run_slices.py browser-world \\
        --runner 3 --runners 7 --shard-count 14
    uv run python scripts/ci/run_slices.py run --suite browser \\
        --runner 3 --runners 7 -- uv run pytest e2e ... --shard-count=14 --shard-index={shard}

Every shard also leaves ``artifacts/shard-<suite>-<n>.json``: when it started
and finished, how long its setup took and how it exited. The shard-balance
report reads those beside the JUnit reports, so the setup a runner pays and the
tests it runs are reported apart.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ci_slices  # noqa: E402  (after sys.path, so this runs from anywhere)

ARTIFACTS = ROOT / "artifacts"
MANAGE = [sys.executable, str(ROOT / "manage.py")]

#: How long a freshly started server may take to answer its health probe; the
#: single-shard runner waited forty seconds too.
SERVER_START_SECONDS = 40

_print_lock = threading.Lock()


def say(prefix: str, message: str) -> None:
    with _print_lock:
        print(f"[{prefix}] {message}", flush=True)


def shard_count_of(command: Sequence[str]) -> int:
    """The ``--shard-count`` the pytest command states."""
    for index, word in enumerate(command):
        if word.startswith("--shard-count="):
            return int(word.split("=", 1)[1])
        if word == "--shard-count" and index + 1 < len(command):
            return int(command[index + 1])
    raise SystemExit("the command states no --shard-count, so there is nothing to divide")


def environment_for(suite: str, shard: int, position: int) -> dict[str, str]:
    """The job's environment with this shard's own variables laid over it."""
    own = ci_slices.shard_environment(suite, shard, position)
    for directory in ci_slices.directories_to_create(own):
        path = Path(directory)
        # A relative one is relative to the checkout, which is every command's cwd.
        (path if path.is_absolute() else ROOT / path).mkdir(parents=True, exist_ok=True)
    return {**os.environ, **own}


def ensure_database(name: str, environment: dict[str, str]) -> None:
    """Create ``name`` on the runner's server, empty, as the service container did."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        host=environment.get("POSTGRES_HOST", "localhost"),
        port=environment.get("POSTGRES_PORT", "5432"),
        user=environment.get("POSTGRES_USER", "juristid"),
        password=environment.get("POSTGRES_PASSWORD", ""),
        dbname="postgres",
        autocommit=True,
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def machine() -> dict[str, object]:
    """Which hardware this runner drew. Hosted runners differ by up to ~1.4x in
    the same run (2026-10-11), and a report that cannot say which runner was a
    slow one reads that as imbalance."""
    model = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"cpu": model, "cpus": os.cpu_count()}


def write_record(suite: str, shard: int, record: dict[str, object]) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"shard-{suite}-{shard}.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = {**existing, **machine(), **record}
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# browser-world
# ---------------------------------------------------------------------------


def _manage(prefix: str, arguments: list[str], environment: dict[str, str], log: Path) -> float:
    """Run one management command to completion; its output goes to ``log``."""
    started = time.monotonic()
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"$ manage.py {' '.join(arguments)}\n")
        handle.flush()
        completed = subprocess.run(  # noqa: S603 - fixed arguments, this repository's manage.py
            [*MANAGE, *arguments],
            cwd=ROOT,
            env=environment,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    took = time.monotonic() - started
    if completed.returncode != 0:
        say(prefix, f"manage.py {' '.join(arguments)} failed after {took:.1f}s; log follows")
        with _print_lock:
            print(log.read_text(encoding="utf-8"), flush=True)
        raise RuntimeError(f"{prefix}: manage.py {arguments[0]} exited {completed.returncode}")
    say(prefix, f"manage.py {' '.join(arguments)} took {took:.1f}s")
    return took


def _answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _start_server(
    prefix: str, port: int, environment: dict[str, str], log: Path
) -> subprocess.Popen[bytes]:
    # A server already answering here would answer every health probe below
    # and every test after it, from somebody else's database. Ports are
    # disjoint by construction; this makes a broken construction loud.
    if _answers(port):
        raise RuntimeError(f"{prefix}: something already answers on port {port}")
    handle = log.open("w", encoding="utf-8")
    return subprocess.Popen(  # noqa: S603 - this repository's manage.py on a loopback port
        [*MANAGE, "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=ROOT,
        env=environment,
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        # Its own session, so it outlives this command and the step, exactly as
        # the `nohup … &` servers did; the runner reaps it when the job ends.
        start_new_session=True,
    )


def _await_health(prefix: str, port: int, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + SERVER_START_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"{prefix}: the server for port {port} exited ({server.returncode})")
        if _answers(port):
            # Answered — and by this process: one that failed to bind has
            # exited by now, and nothing else may listen here (checked above).
            if server.poll() is not None:
                raise RuntimeError(f"{prefix}: port {port} answered, but not from this server")
            return
        time.sleep(0.5)
    raise RuntimeError(f"{prefix}: the server on port {port} did not answer its health probe")


def prepare_browser_shard(shard: int, position: int) -> dict[str, float]:
    """One shard's world: databases, migrations, the seed, three servers."""
    prefix = f"browser {shard}"
    environment = environment_for("browser", shard, position)
    own = Path(ARTIFACTS / f"browser-{shard}")
    own.mkdir(parents=True, exist_ok=True)
    application, gate, local_auth = ci_slices.shard_ports(position)
    started = time.monotonic()
    timings: dict[str, float] = {}

    main_database = environment["POSTGRES_DB"]
    ensure_database(main_database, environment)
    ensure_database(f"{main_database}_localauth", environment)

    gate_environment = {**environment, "DJANGO_SETTINGS_MODULE": "config.e2e_gate_settings"}
    local_auth_environment = {
        **environment,
        "DJANGO_SETTINGS_MODULE": "config.e2e_local_auth_settings",
    }

    servers: dict[int, subprocess.Popen[bytes]] = {}

    def seeded_world() -> None:
        timings["migrate"] = _manage(
            prefix, ["migrate", "--noinput"], environment, own / "setup-world.log"
        )
        timings["seed"] = _manage(prefix, ["seed_e2e_data"], environment, own / "setup-world.log")
        servers[application] = _start_server(prefix, application, environment, own / "server.log")
        servers[gate] = _start_server(prefix, gate, gate_environment, own / "server-gate.log")

    def local_auth_world() -> None:
        timings["migrate_local_auth"] = _manage(
            prefix, ["migrate", "--noinput"], local_auth_environment, own / "setup-local-auth.log"
        )
        servers[local_auth] = _start_server(
            prefix, local_auth, local_auth_environment, own / "server-local-auth.log"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(seeded_world), pool.submit(local_auth_world)]:
            future.result()
    for port in (application, gate, local_auth):
        _await_health(prefix, port, servers[port])
    timings["total"] = time.monotonic() - started
    say(
        prefix, f"world ready on ports {application}/{gate}/{local_auth} in {timings['total']:.1f}s"
    )
    write_record("browser", shard, {"setup_seconds": timings})
    return timings


def browser_world(arguments: argparse.Namespace) -> int:
    shards = ci_slices.slices_for(arguments.runner, arguments.runners, arguments.shard_count)
    print(f"runner {arguments.runner} of {arguments.runners} prepares browser shards {shards}")
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        futures = {
            shard: pool.submit(prepare_browser_shard, shard, position)
            for position, shard in enumerate(shards, start=1)
        }
        for shard, future in futures.items():
            try:
                future.result()
            except Exception as error:
                failures.append(f"shard {shard}: {error}")
    if failures:
        print("a browser world could not be prepared:\n  " + "\n  ".join(failures))
        return 1
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _stream(prefix: str, process: subprocess.Popen[str], log: Path) -> None:
    if process.stdout is None:
        raise RuntimeError(f"{prefix}: the shard has no output to stream")
    with log.open("w", encoding="utf-8") as handle:
        for line in process.stdout:
            handle.write(line)
            with _print_lock:
                sys.stdout.write(f"[{prefix}] {line}")
                sys.stdout.flush()


def run(arguments: argparse.Namespace) -> int:
    command = list(arguments.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("no command to run")
    if not any(ci_slices.SHARD_PLACEHOLDER in word for word in command):
        raise SystemExit(
            f"the command never says {ci_slices.SHARD_PLACEHOLDER}, "
            "so every shard would run the same tests"
        )
    shard_count = shard_count_of(command)
    shards = ci_slices.slices_for(arguments.runner, arguments.runners, shard_count)
    print(
        f"runner {arguments.runner} of {arguments.runners} runs {arguments.suite} shards "
        f"{shards} of {shard_count}, concurrently"
    )
    hardware = machine()
    print(f"on {hardware['cpus']} x {hardware['cpu']}")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    processes: dict[int, subprocess.Popen[str]] = {}
    streams: list[threading.Thread] = []
    started: dict[int, float] = {}
    started_at: dict[int, float] = {}
    for position, shard in enumerate(shards, start=1):
        environment = environment_for(arguments.suite, shard, position)
        if arguments.suite == "tests":
            # The service container created one empty database named after
            # POSTGRES_DB; each shard gets the same, under its own name.
            ensure_database(environment["POSTGRES_DB"], environment)
        prefix = f"{arguments.suite} {shard}"
        started[shard] = time.monotonic()
        started_at[shard] = time.time()
        process = subprocess.Popen(  # noqa: S603 - the workflow's own pytest command
            ci_slices.substitute(command, shard),
            cwd=ROOT,
            # Unbuffered so the job log shows each shard's progress as it
            # happens; a pipe would otherwise hold it back in blocks.
            env={**environment, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        processes[shard] = process
        stream = threading.Thread(
            target=_stream,
            args=(prefix, process, ARTIFACTS / f"{arguments.suite}-{shard}.log"),
            daemon=True,
        )
        stream.start()
        streams.append(stream)

    def forward(signum: int, _frame: object) -> None:
        for process in processes.values():
            if process.poll() is None:
                process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    results: dict[int, int] = {}
    durations: dict[int, float] = {}
    for shard, process in processes.items():
        results[shard] = process.wait()
        durations[shard] = time.monotonic() - started[shard]
    for stream in streams:
        stream.join()

    print()
    print(f"{arguments.suite} shards on runner {arguments.runner}:")
    for shard in shards:
        verdict = "passed" if results[shard] == 0 else f"FAILED (exit {results[shard]})"
        print(f"  shard {shard:>3} of {shard_count}: {verdict} in {durations[shard]:.0f}s")
        write_record(
            arguments.suite,
            shard,
            {
                "suite": arguments.suite,
                "shard": shard,
                "shard_count": shard_count,
                "runner": arguments.runner,
                "runners": arguments.runners,
                "started_at": started_at[shard],
                "pytest_seconds": round(durations[shard], 3),
                "exit_code": results[shard],
            },
        )
    failed = [shard for shard in shards if results[shard] != 0]
    if failed:
        print(f"shard(s) {failed} failed; their output above is prefixed [{arguments.suite} N]")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="action", required=True)

    world = commands.add_parser("browser-world", help="prepare each hosted browser shard's world")
    world.add_argument("--runner", type=int, required=True)
    world.add_argument("--runners", type=int, required=True)
    world.add_argument("--shard-count", type=int, required=True)

    each = commands.add_parser("run", help="run a command once per hosted shard, concurrently")
    each.add_argument("--suite", choices=ci_slices.SUITES, required=True)
    each.add_argument("--runner", type=int, required=True)
    each.add_argument("--runners", type=int, required=True)
    each.add_argument("command", nargs=argparse.REMAINDER)

    arguments = parser.parse_args(argv)
    # Line by line into the job log, interleaved correctly with the shards'.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if arguments.action == "browser-world":
        return browser_world(arguments)
    return run(arguments)


if __name__ == "__main__":
    sys.exit(main())
