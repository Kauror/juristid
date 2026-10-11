"""Two shards on one runner are as separate as two runners (ci_slices.py).

A runner hosts more than one shard at the same time, and the whole safety
argument is that the shards share the machine and nothing else: no database,
no port, no writable directory, no report file. These prove that property about
the environment each shard is given, about the commands the real workflow runs,
and about the runner script that starts them — without a database or a browser,
so they run in every PostgreSQL shard.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import pathlib
import re
import shlex
import signal
import sys

import pytest
import yaml

import ci_slices
import ci_workflow_selection

ROOT = pathlib.Path(ci_workflow_selection.REPOSITORY_ROOT)
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "ci" / "run_slices.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_slices", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner_script = _load_runner()


# -- which runner hosts which shard -----------------------------------------------


@pytest.mark.parametrize(("runners", "shard_count"), [(1, 1), (1, 3), (7, 7), (7, 14), (5, 15)])
def test_every_shard_is_hosted_by_exactly_one_runner(runners: int, shard_count: int) -> None:
    hosted = [
        shard
        for runner in range(1, runners + 1)
        for shard in ci_slices.slices_for(runner, runners, shard_count)
    ]
    assert sorted(hosted) == list(range(1, shard_count + 1))
    assert len(hosted) == len(set(hosted))


@pytest.mark.parametrize(
    ("runner", "runners", "shard_count"),
    [(1, 7, 15), (1, 7, 6), (0, 7, 14), (8, 7, 14), (1, 0, 0)],
)
def test_a_layout_that_leaves_a_shard_unhosted_is_refused(
    runner: int, runners: int, shard_count: int
) -> None:
    """Seven runners cannot share fifteen shards equally; rounding would drop one."""
    with pytest.raises(ValueError):
        ci_slices.slices_for(runner, runners, shard_count)


# -- what each shard is given -------------------------------------------------------


def _neighbours(suite: str, per_runner: int = 2) -> list[dict[str, str]]:
    shards = ci_slices.slices_for(3, 7, 7 * per_runner)
    return [
        ci_slices.shard_environment(suite, shard, position)
        for position, shard in enumerate(shards, start=1)
    ]


@pytest.mark.parametrize("suite", ci_slices.SUITES)
@pytest.mark.parametrize("per_runner", [2, 3])
def test_shards_on_one_runner_share_no_per_shard_value(suite: str, per_runner: int) -> None:
    environments = _neighbours(suite, per_runner)
    for name in ci_slices.PER_SHARD_VARIABLES[suite]:
        values = [environment[name] for environment in environments]
        assert len(set(values)) == len(values), f"{name} is shared: {values}"


@pytest.mark.parametrize("suite", ci_slices.SUITES)
def test_no_shard_writes_inside_another_shards_directories(suite: str) -> None:
    """Distinct is not enough: a directory inside a neighbour's would still be shared."""
    first, second = _neighbours(suite)
    paths = {
        name: pathlib.PurePosixPath(value)
        for environment, side in ((first, "a"), (second, "b"))
        for name, value in ((f"{side}:{key}", val) for key, val in environment.items())
        if value.startswith(("/", "artifacts/"))
    }
    for (name_a, a), (name_b, b) in itertools.combinations(paths.items(), 2):
        if name_a[0] == name_b[0]:
            continue
        assert not (a == b or a.is_relative_to(b) or b.is_relative_to(a)), (name_a, name_b)


def test_browser_shards_on_one_runner_listen_on_different_ports() -> None:
    ports = [
        int(environment[name].rsplit(":", 1)[1])
        for environment in _neighbours("browser", per_runner=3)
        for name in ("E2E_BASE_URL", "E2E_GATE_BASE_URL", "E2E_LOCAL_AUTH_BASE_URL")
    ]
    assert len(ports) == len(set(ports))


def test_the_first_shard_on_a_runner_keeps_the_single_shard_ports() -> None:
    environment = ci_slices.shard_environment("browser", 5, 1)
    assert environment["E2E_BASE_URL"] == "http://127.0.0.1:8000"
    assert environment["E2E_GATE_BASE_URL"] == "http://127.0.0.1:8001"
    assert environment["E2E_LOCAL_AUTH_BASE_URL"] == "http://127.0.0.1:8002"


@pytest.mark.parametrize("suite", ci_slices.SUITES)
def test_database_names_cannot_collide_across_suites_or_shards(suite: str) -> None:
    names = {
        ci_slices.database_name(each, shard) for each in ci_slices.SUITES for shard in range(1, 30)
    }
    assert len(names) == len(ci_slices.SUITES) * 29
    # The personal sign-in server's database is the shard's name plus a suffix
    # (config/e2e_local_auth_settings.py); it must not be another shard's name.
    assert not {f"{name}_localauth" for name in names} & names


def test_coverage_fragments_cannot_swallow_each_other() -> None:
    """pytest-cov erases and combines `<COVERAGE_FILE>.*` (2026-10-11 audit)."""
    files = [
        ci_slices.shard_environment("tests", shard, 1)["COVERAGE_FILE"] for shard in range(1, 40)
    ]
    for a, b in itertools.permutations(files, 2):
        assert not b.startswith(f"{a}."), (a, b)
    assert all(name.startswith(".coverage.shard-") for name in files)


def test_every_writable_path_the_settings_read_is_given_to_each_browser_shard() -> None:
    """A storage root added to the settings later must not quietly be shared.

    Every `*_ROOT`, `*_PATH` and `*_DIR` variable the settings modules read is
    either set per shard or named here as read-only input that CI leaves unset.
    """
    read_only_inputs = {
        # A corpus mount the application only reads; unset in CI.
        "HISTORICAL_SOURCE_ROOT",
        # A breached-password list the application only reads; unset in CI.
        "LOCAL_AUTH_BREACHED_PASSWORDS_PATH",
    }
    pattern = re.compile(r'(?:env|os\.environ\.get)\(\s*"([A-Z0-9_]+_(?:ROOT|PATH|DIR))"')
    read = {
        name
        for module in (ROOT / "config").glob("*.py")
        for name in pattern.findall(module.read_text(encoding="utf-8"))
    }
    assert read, "no path variables were found, so this proves nothing"
    unhandled = read - set(ci_slices.PER_SHARD_VARIABLES["browser"]) - read_only_inputs
    assert not unhandled, f"give each shard its own {sorted(unhandled)} in ci_slices.py"


def test_shard_directories_are_outside_the_checkouts_storage_roots() -> None:
    """config/test_safety.py refuses a test process pointed at the checkout's roots."""
    for environment in _neighbours("browser"):
        for name in (
            "EVIDENCE_ROOT",
            "DERIVATIVE_ROOT",
            "LEGACY_SOURCE_ROOT",
            "PENDING_UPLOAD_ROOT",
        ):
            assert pathlib.PurePosixPath(environment[name]).is_absolute()
            assert environment[name].startswith(str(ci_slices.SLICE_ROOT))


# -- the commands the real workflow runs ---------------------------------------------


def _hosted_commands(job: str) -> list[str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        step["run"]
        for step in workflow["jobs"][job]["steps"]
        if "run_slices.py run " in step.get("run", "")
    ]


def _option(words: list[str], name: str) -> str:
    for index, word in enumerate(words):
        if word.startswith(f"{name}="):
            return word.split("=", 1)[1]
        if word == name:
            return words[index + 1]
    raise AssertionError(f"{name} is not in {words}")


@pytest.mark.parametrize("job", ["tests", "browser"])
def test_each_hosted_shard_writes_its_own_report_and_output(job: str) -> None:
    (command,) = _hosted_commands(job)
    words = shlex.split(command.replace("${{ matrix.runner }}", "1"))
    pytest_words = words[words.index("--") + 1 :]
    shape = ci_workflow_selection.topology(WORKFLOW)[job]

    reports = []
    outputs = []
    for shard in range(1, shape.shard_count + 1):
        mine = ci_slices.substitute(pytest_words, shard)
        reports.append(_option(mine, "--junitxml"))
        if job == "browser":
            outputs.append(pathlib.PurePosixPath(_option(mine, "--output")))
    assert len(set(reports)) == len(reports)
    # pytest-playwright deletes its --output directory when a session starts,
    # so one shard's output must not contain, or sit inside, another's.
    for a, b in itertools.combinations(outputs, 2):
        assert not (a.is_relative_to(b) or b.is_relative_to(a)), (a, b)


def test_slice_options_travel_on_the_command_line_never_in_pytest_addopts() -> None:
    """Child pytest processes inherit the environment (2026-10-11 audit)."""
    for suite in ci_slices.SUITES:
        assert "PYTEST_ADDOPTS" not in ci_slices.shard_environment(suite, 1, 1)
    assert "PYTEST_ADDOPTS" not in WORKFLOW.read_text(encoding="utf-8")


# -- the runner script ----------------------------------------------------------------


@pytest.fixture
def isolated_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_slices, "SLICE_ROOT", pathlib.PurePosixPath(tmp_path.as_posix()))
    monkeypatch.setattr(runner_script, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(runner_script, "ensure_database", lambda name, environment: None)
    return tmp_path


def _program(source: str) -> list[str]:
    """A shard command: a tiny Python program and the shard options it ignores."""
    return ["--", sys.executable, "-c", source, "--shard-count=4", "--shard-index={shard}"]


def test_the_runners_shards_run_at_the_same_time(isolated_runner) -> None:
    """Each shard waits to see the other has started, so serial running would time out."""
    marks = isolated_runner / "marks"
    marks.mkdir()
    source = (
        "import pathlib, sys, time\n"
        f"marks = pathlib.Path({str(marks)!r})\n"
        "(marks / '{shard}').touch()\n"
        "deadline = time.monotonic() + 30\n"
        "while len(list(marks.iterdir())) < 2 and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "sys.exit(0 if len(list(marks.iterdir())) == 2 else 3)\n"
    )
    code = runner_script.main(
        ["run", "--suite", "tests", "--runner", "2", "--runners", "2", *_program(source)]
    )
    assert code == 0
    assert sorted(path.name for path in marks.iterdir()) == ["3", "4"]


def test_one_failing_shard_fails_the_runner_and_is_named(isolated_runner, capsys) -> None:
    source = "import sys; sys.exit(5 if '{shard}' == '2' else 0)"
    code = runner_script.main(
        ["run", "--suite", "tests", "--runner", "1", "--runners", "2", *_program(source)]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "shard   2 of 4: FAILED (exit 5)" in out
    assert "shard   1 of 4: passed" in out


def test_each_shard_runs_in_its_own_environment(isolated_runner) -> None:
    source = (
        "import os, sys\n"
        "expected = {'POSTGRES_DB': 'juristid_tests_{shard}', "
        "'COVERAGE_FILE': '.coverage.shard-{shard}'}\n"
        "ok = all(os.environ[k] == v for k, v in expected.items())\n"
        "ok = ok and os.environ['TMPDIR'].endswith('tests-{shard}/tmp')\n"
        "ok = ok and os.path.isdir(os.environ['TMPDIR'])\n"
        "sys.exit(0 if ok else 9)\n"
    )
    code = runner_script.main(
        ["run", "--suite", "tests", "--runner", "1", "--runners", "2", *_program(source)]
    )
    assert code == 0


def test_a_command_without_the_placeholder_is_refused(isolated_runner) -> None:
    """Without `{shard}` every hosted shard would run the same tests."""
    with pytest.raises(SystemExit):
        runner_script.main(
            [
                "run",
                "--suite",
                "tests",
                "--runner",
                "1",
                "--runners",
                "2",
                "--",
                sys.executable,
                "-c",
                "pass",
                "--shard-count=4",
            ]
        )


# -- guards on the code, so the next change cannot quietly share ------------------------


def _string_literals(path: pathlib.Path):
    """(line, text) of every string constant in a module that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.lineno, node.value


def _python_sources() -> list[pathlib.Path]:
    return [
        path
        for directory in ("app", "config", "tests", "e2e")
        for path in sorted((ROOT / directory).rglob("*.py"))
        if path.resolve() != pathlib.Path(__file__).resolve()
    ]


def test_every_query_of_a_server_wide_view_is_scoped_to_its_own_database() -> None:
    """`pg_stat_activity` and `pg_locks` show every database on the server.

    Two shards share their runner's PostgreSQL, so an unscoped poll for a
    blocked backend is satisfied by the neighbour's, and the race the test
    exists to force is quietly not forced — green, alone or together. Six
    such reads were found and scoped on 2026-10-11; this keeps it that way.
    """
    unscoped = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in _python_sources()
        for line, text in _string_literals(path)
        if ("pg_stat_activity" in text or "pg_locks" in text)
        and ("FROM" in text.upper() or "JOIN" in text.upper())
        and "current_database()" not in text
    ]
    assert not unscoped, f"scope these to current_database(): {unscoped}"


def test_no_source_names_a_fixed_temporary_path() -> None:
    """A fixed `/tmp/...` would be one file for every shard on the runner.

    Everything temporary comes from `tempfile`, which honours each shard's own
    `TMPDIR`; the one deliberate exception is where those directories live.
    """
    fixed = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in _python_sources()
        for line, text in _string_literals(path)
        if text.startswith(("/tmp", "/var/tmp"))  # noqa: S108 - the paths it forbids
    ]
    assert not fixed, f"use tempfile, not a fixed temporary path: {fixed}"
    assert str(ci_slices.SLICE_ROOT).startswith("/tmp/")  # noqa: S108 - the one allowed


def test_every_path_variable_the_browser_suite_reads_is_given_to_each_shard() -> None:
    """The settings are checked above; the browser suite's own modules too."""
    pattern = re.compile(
        r'(?:env|os\.environ\.get|os\.getenv)\(\s*"([A-Z0-9_]+_(?:ROOT|PATH|DIR))"'
    )
    read = {
        name
        for module in (ROOT / "e2e").rglob("*.py")
        for name in pattern.findall(module.read_text(encoding="utf-8"))
    }
    assert read, "no path variables were found in e2e/, so this proves nothing"
    unhandled = read - set(ci_slices.PER_SHARD_VARIABLES["browser"])
    assert not unhandled, f"give each shard its own {sorted(unhandled)} in ci_slices.py"


# -- the runner leaves its process as it found it ---------------------------------


def test_the_runner_puts_the_signal_handlers_back(isolated_runner) -> None:
    """It runs in-process here, inside a shard: it must not keep that shard's handlers."""
    before = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    runner_script.main(
        ["run", "--suite", "tests", "--runner", "1", "--runners", "2", *_program("pass")]
    )
    assert {signum: signal.getsignal(signum) for signum in before} == before


def test_each_shard_is_timed_to_its_own_finish(isolated_runner) -> None:
    """Not to its sibling's: a quick shard beside a slow one reads as quick."""
    source = "import time; time.sleep(3 if '{shard}' == '1' else 0)"
    runner_script.main(
        ["run", "--suite", "tests", "--runner", "1", "--runners", "2", *_program(source)]
    )
    quick = json.loads((isolated_runner / "artifacts" / "shard-tests-2.json").read_text())
    slow = json.loads((isolated_runner / "artifacts" / "shard-tests-1.json").read_text())
    assert slow["pytest_seconds"] >= 3
    assert quick["pytest_seconds"] < slow["pytest_seconds"] - 1
