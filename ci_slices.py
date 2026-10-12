"""Several shards on one CI runner, each as isolated as a runner of its own.

A GitHub-hosted runner for this public repository has four vCPUs, and a shard
used about one of them: one pytest process, waiting on PostgreSQL or on a
browser. The account runs twenty jobs at once and the workflow already used all
twenty, so more runners were not available — but the cores inside each one were.

So a runner now runs more than one shard at the same time. What makes that the
same test run rather than a parallel one is that **nothing is shared between
the shards on a runner except the machine**:

* each shard has its own database on the runner's PostgreSQL server, under a
  name derived from its shard index, so pytest-django creates
  ``test_juristid_tests_<shard>`` and the browser servers serve
  ``juristid_browser_<shard>`` (and ``…_localauth``);
* each browser shard has its own three servers on its own ports, and its own
  evidence, derivative, legacy-source and pending-upload roots, mail
  directory, screenshot directory and Playwright output;
* each shard has its own ``TMPDIR``, so everything that defaults to the system
  temporary directory — the heartbeat files, ``mkdtemp`` roots, pytest's
  ``tmp_path`` base, Chromium's profiles — is its own as well;
* each shard writes its own coverage fragment and its own JUnit report.

Which tests a shard runs is unchanged: it is still ``ci_sharding.partition``
over the files pytest collected, so a shard is still an order-preserving
subsequence of the serial suite and its contents are still a pure function of
(file set, test counts, shard count, timing table). A runner only decides
*which* shards it hosts — consecutive ones, ``slices_for`` below — never what is
in them. Inside a database, tests still run one at a time in collection order.

Pure functions, standard library only, so ``tests/test_ci_slices.py`` proves
the isolation property without starting anything and the runner script
(``scripts/ci/run_slices.py``) has nothing to decide on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

#: The suites a runner can host more than one shard of. The keys name the
#: workflow jobs; the browser one needs servers, the other only a database.
SUITES = ("tests", "browser")

#: Where each shard's private writable state lives on the runner. Outside the
#: checkout, so nothing a shard writes can be mistaken for repository content.
SLICE_ROOT = PurePosixPath("/tmp/juristid-slices")  # noqa: S108 - a CI runner's own scratch

#: The first browser shard on a runner serves on 8000/8001/8002, exactly the
#: ports a single-shard runner used; the next one ten higher, and so on.
PORT_BASE = 8000
PORT_STRIDE = 10

#: Variables a shard's environment must define for itself. A test proves that
#: two shards on one runner differ in every one of them, so a variable that
#: names something writable cannot be added here and then left shared.
PER_SHARD_VARIABLES: dict[str, tuple[str, ...]] = {
    "tests": ("POSTGRES_DB", "TMPDIR", "COVERAGE_FILE"),
    "browser": (
        "POSTGRES_DB",
        "TMPDIR",
        "EVIDENCE_ROOT",
        "DERIVATIVE_ROOT",
        "LEGACY_SOURCE_ROOT",
        "PENDING_UPLOAD_ROOT",
        "EXTRACTION_WORKER_HEARTBEAT_PATH",
        "INTAKE_READER_HEARTBEAT_PATH",
        "E2E_BASE_URL",
        "E2E_GATE_BASE_URL",
        "E2E_LOCAL_AUTH_BASE_URL",
        "E2E_LOCAL_AUTH_MAIL_DIR",
        "E2E_SCREENSHOT_DIR",
    ),
}

#: The token in a workflow command that each shard replaces with its own index.
SHARD_PLACEHOLDER = "{shard}"


def slices_for(runner: int, runners: int, shard_count: int) -> list[int]:
    """The 1-based shard indexes runner ``runner`` of ``runners`` hosts.

    Consecutive and equal in number on every runner, so the union over all
    runners is exactly ``1..shard_count`` and no shard is hosted twice. A shard
    count the runners cannot share equally is refused rather than rounded: a
    runner left one shard short is a slice of the suite nobody runs.
    """
    if runners < 1:
        raise ValueError(f"a suite cannot run on {runners} runners")
    if not 1 <= runner <= runners:
        raise ValueError(f"runner {runner} does not exist among {runners}")
    if shard_count < runners or shard_count % runners:
        raise ValueError(
            f"{shard_count} shards cannot be shared equally by {runners} runners; "
            "make the shard count a multiple of the runner count"
        )
    per_runner = shard_count // runners
    first = (runner - 1) * per_runner + 1
    return list(range(first, first + per_runner))


def shard_ports(position: int) -> tuple[int, int, int]:
    """(application, shared gate, personal sign-in) ports for the n-th shard on a runner."""
    if position < 1:
        raise ValueError(f"a runner has no shard at position {position}")
    base = PORT_BASE + PORT_STRIDE * (position - 1)
    return base, base + 1, base + 2


def database_name(suite: str, shard: int) -> str:
    """The database a shard's servers use, or pytest-django prefixes with ``test_``."""
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}")
    return f"juristid_{suite}_{shard}"


def shard_root(suite: str, shard: int) -> PurePosixPath:
    """The directory that holds everything a shard writes outside the checkout."""
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}")
    return SLICE_ROOT / f"{suite}-{shard}"


def shard_environment(suite: str, shard: int, position: int) -> dict[str, str]:
    """The variables that make one shard its own, to lay over the job's environment.

    ``position`` is the shard's place on its runner (1 for the first it hosts),
    which only decides ports: two runners never share a machine, so the same
    ports on different runners are different sockets.
    """
    root = shard_root(suite, shard)
    environment = {
        "POSTGRES_DB": database_name(suite, shard),
        "TMPDIR": str(root / "tmp"),
    }
    if suite == "tests":
        # The fragment the coverage gate combines. One per shard, as it always
        # was; it just used to be one per runner too.
        environment["COVERAGE_FILE"] = f".coverage.shard-{shard}"
        return environment

    application, gate, local_auth = shard_ports(position)
    environment.update(
        {
            "EVIDENCE_ROOT": str(root / "evidence"),
            "DERIVATIVE_ROOT": str(root / "derivatives"),
            "LEGACY_SOURCE_ROOT": str(root / "legacy-source"),
            "PENDING_UPLOAD_ROOT": str(root / "pending-uploads"),
            "EXTRACTION_WORKER_HEARTBEAT_PATH": str(root / "extraction-worker.heartbeat"),
            "INTAKE_READER_HEARTBEAT_PATH": str(root / "intake-reader.heartbeat"),
            "E2E_BASE_URL": f"http://127.0.0.1:{application}",
            "E2E_GATE_BASE_URL": f"http://127.0.0.1:{gate}",
            "E2E_LOCAL_AUTH_BASE_URL": f"http://127.0.0.1:{local_auth}",
            "E2E_LOCAL_AUTH_MAIL_DIR": str(root / "mail"),
            # Relative to the checkout, like the single-shard runner's, so the
            # upload step finds every shard's renderings under `artifacts/`.
            "E2E_SCREENSHOT_DIR": f"artifacts/browser-{shard}/screenshots",
        }
    )
    return environment


def directories_to_create(environment: Mapping[str, str]) -> list[str]:
    """The private directories a shard's environment names, which must exist first."""
    names = (
        "TMPDIR",
        "EVIDENCE_ROOT",
        "DERIVATIVE_ROOT",
        "LEGACY_SOURCE_ROOT",
        "PENDING_UPLOAD_ROOT",
        "E2E_LOCAL_AUTH_MAIL_DIR",
        "E2E_SCREENSHOT_DIR",
    )
    return [environment[name] for name in names if name in environment]


def substitute(command: list[str], shard: int) -> list[str]:
    """``command`` with every ``{shard}`` replaced by this shard's index."""
    return [word.replace(SHARD_PLACEHOLDER, str(shard)) for word in command]
