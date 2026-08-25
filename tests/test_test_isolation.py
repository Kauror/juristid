"""The 2026-08-24 incident, turned into tests.

A pytest run inside a container built from the production image wrote 63
synthetic fixtures into the Chamber's real evidence store. Nothing was corrupted
and nothing was lost — the payloads were 8 to 33 byte strings such as
``%PDF-1.4 test`` — but the route was open and would have been open to a larger
file, and to a management command talking to the production *database* rather
than to a test one.

Four things had to be true at once:

1. the production image sets ``DJANGO_SETTINGS_MODULE=config.settings``;
2. pytest-django reads that environment variable *before* the value in
   ``pyproject.toml``, so production settings won;
3. those settings point ``EVIDENCE_ROOT`` at ``/app/evidence``, which the
   deployment bind-mounts to the real store;
4. the fixture that redirects evidence storage was opt-in, and the tests doing
   the writing did not opt in.

Each of the four now has a test here, plus one that the ordinary path still
works. The subprocess tests are the load-bearing ones: a fixture cannot prove
anything about how the process it lives in was started, so those run a real
pytest with a real hostile environment and read what it did.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from config import test_safety
from config import test_settings as test_settings_module

ROOT = Path(__file__).resolve().parent.parent

#: The process-wide evidence root the test settings mint at import time. Read
#: from the module rather than from ``django.conf.settings``, because the
#: ``settings`` fixture patches the latter — this is the value a test would have
#: written to if the autouse fixture were removed, and the assertions below need
#: to name it.
PROCESS_EVIDENCE_ROOT = Path(test_settings_module.EVIDENCE_ROOT)

PDF = b"%PDF-1.4 test"


# ---------------------------------------------------------------------------
# 1. Evidence isolation is automatic
# ---------------------------------------------------------------------------


def test_evidence_written_without_asking_for_the_fixture_stays_in_this_test(
    normal_matter, tmp_path_factory
) -> None:
    """The incident itself, in one test.

    Note what this test does *not* request: ``evidence_root``, ``capture_evidence``,
    or anything else that redirects storage. It is written the way the tests
    that caused the incident were written — straight through the capture
    service — and it is isolated anyway, because the fixture is autouse now.

    Removing ``autouse=True`` from ``tests/conftest.py`` makes this fail.
    """
    from app.documents.services import add_evidence_version, create_document

    document = create_document(
        matter=normal_matter,
        title="Isolatsioonikatse",
        role="INCOMING_AUTHORITY",
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="katse.pdf",
        mime_type="application/pdf",
    )

    basetemp = tmp_path_factory.getbasetemp()
    stored = Path(django_settings.EVIDENCE_ROOT) / version.storage_key
    assert stored.exists(), "the capture service wrote nothing at all"
    assert stored.is_relative_to(basetemp), (
        f"{stored} is outside this run's temporary directory {basetemp}"
    )
    # Not the root the *settings module* minted, which is the one a test writes
    # to when no fixture has redirected it — so this is the assertion that
    # `autouse=True` is doing the work rather than the test settings.
    assert not stored.is_relative_to(PROCESS_EVIDENCE_ROOT)
    assert test_safety.forbidden_root(stored) is None


def test_the_derivative_and_source_classes_are_isolated_too(evidence_root) -> None:
    """All three writable classes, not just the one that was noticed.

    ``/app/derivatives`` and ``/app/legacy-source`` are bind-mounted by the same
    Compose file as the evidence store, so a test writing a thumbnail or a page
    of OneNote XML had exactly the same route out.
    """
    for name in test_safety.WRITABLE_ROOT_VARIABLES:
        root = Path(getattr(django_settings, name))
        assert root.is_relative_to(evidence_root), f"{name} is not this test's own directory"
        assert root.is_dir(), f"{name} was named but never created"


def test_asking_for_the_fixture_by_name_still_works(evidence_root) -> None:
    """Every test that already named it keeps the layout it always got."""
    assert Path(django_settings.EVIDENCE_ROOT) == evidence_root / "evidence"
    assert Path(django_settings.DERIVATIVE_ROOT) == evidence_root / "derivatives"
    assert Path(django_settings.LEGACY_SOURCE_ROOT) == evidence_root / "legacy-source"


def test_the_storage_roots_are_not_planted_in_the_tests_own_tmp_path(tmp_path) -> None:
    """The thing making a fixture autouse changes that nothing warns about.

    While it was opt-in, only a test that asked for storage got three
    directories appearing in its ``tmp_path``. Autouse would have put them in
    every test's — including
    ``test_deployment_scripts.py::test_the_backup_refuses_a_data_root_with_no_evidence_tree``,
    which hands its ``tmp_path`` to the backup script *because* it holds no
    ``evidence`` directory, and which consequently stopped testing what it says
    it tests. CI caught it; this keeps it caught.
    """
    assert list(tmp_path.iterdir()) == [], "the storage fixture wrote into tmp_path"
    for name in test_safety.WRITABLE_ROOT_VARIABLES:
        root = Path(getattr(django_settings, name))
        assert not root.is_relative_to(tmp_path), f"{name} is inside the test's tmp_path"


# ---------------------------------------------------------------------------
# 2. The settings actually in force are the test ones
# ---------------------------------------------------------------------------


def test_the_settings_in_force_are_the_test_settings() -> None:
    """The sentinel's own assertion, as a test that names it.

    ``IS_TEST_SETTINGS`` rather than ``settings.SETTINGS_MODULE``: the latter
    reads ``None`` inside any test, because the autouse fixture overrides
    settings and Django's override holder does not carry a module name. This is
    a value the settings module itself declares, so it survives an override and
    cannot be satisfied by a module that merely happens to be spelled
    ``config.test_settings``.
    """
    assert getattr(django_settings, "IS_TEST_SETTINGS", False) is True
    assert os.environ["DJANGO_SETTINGS_MODULE"] == test_safety.TEST_SETTINGS_MODULE
    assert django_settings.REAL_DATA_ALLOWED is False


def test_the_writable_roots_are_not_a_deployments() -> None:
    roots = {name: getattr(django_settings, name) for name in test_safety.WRITABLE_ROOT_VARIABLES}
    assert test_safety.storage_refusals(roots) == []


# ---------------------------------------------------------------------------
# 3. The refusals, unit by unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on"],
)
def test_real_data_in_the_environment_is_refused_not_overridden(value: str) -> None:
    """Fail closed. The test settings *do* set ``REAL_DATA_ALLOWED = False``, and
    that is exactly the problem: it made a process started in the production
    environment look safe from the inside."""
    refusals = test_safety.environment_refusals({"REAL_DATA_ALLOWED": value})
    assert len(refusals) == 1
    assert "REAL_DATA_ALLOWED" in refusals[0]


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_an_environment_without_real_data_is_accepted(value: str) -> None:
    assert test_safety.environment_refusals({"REAL_DATA_ALLOWED": value}) == []


@pytest.mark.parametrize(
    ("variable", "path"),
    [
        ("EVIDENCE_ROOT", "/app/evidence"),
        ("EVIDENCE_ROOT", "/app/evidence/1c9f/2a"),
        ("DERIVATIVE_ROOT", "/app/derivatives"),
        ("LEGACY_SOURCE_ROOT", "/app/legacy-source"),
    ],
)
def test_a_deployments_storage_root_is_refused(variable: str, path: str) -> None:
    refusals = test_safety.environment_refusals({variable: path})
    assert len(refusals) == 1
    assert variable in refusals[0]


def test_the_checkout_is_a_forbidden_root_too() -> None:
    """A suite that filled these would be writing into somebody's working copy."""
    assert test_safety.forbidden_root(ROOT / "evidence") is not None
    assert test_safety.forbidden_root(ROOT / "evidence" / "deep" / "key") is not None


def test_a_temporary_directory_is_not_refused(tmp_path) -> None:
    """The check has to be strict without being useless."""
    assert test_safety.forbidden_root(tmp_path) is None
    assert test_safety.environment_refusals({"EVIDENCE_ROOT": str(tmp_path)}) == []


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs a privilege on Windows")
def test_a_symlink_does_not_get_past_the_check(tmp_path) -> None:
    """Path equality alone would be defeated by one ``ln -s``."""
    link = tmp_path / "innocent"
    link.symlink_to("/app/evidence")
    assert test_safety.forbidden_root(link) is not None


def test_the_deployment_marker_is_refused() -> None:
    refusals = test_safety.environment_refusals({test_safety.RUNTIME_MARKER: "deployed"})
    assert len(refusals) == 1
    assert test_safety.RUNTIME_MARKER in refusals[0]


def test_the_refusal_says_what_to_do_about_it() -> None:
    """A guard nobody can act on gets deleted by the third person who hits it."""
    with pytest.raises(test_safety.UnsafeTestEnvironment) as raised:
        test_safety.assert_environment_is_safe_for_tests({"REAL_DATA_ALLOWED": "1"})

    message = str(raised.value)
    assert "REAL_DATA_ALLOWED" in message
    assert "DJANGO_SETTINGS_MODULE" in message
    assert "config.test_settings" in message
    assert "uv run pytest" in message


def test_the_deployed_stacks_carry_the_marker() -> None:
    """The control behind "do not run the suite through the production stack".

    A runbook sentence is advice. This is what makes it a refusal: `docker
    compose run` hands the service's environment to the process it starts, so a
    pytest run through either deployed project sees the marker and stops.
    """
    import yaml

    for path in (
        ROOT / "deploy" / "unraid-main" / "compose.yml",
        ROOT / "deploy" / "unraid-test" / "compose.yml",
    ):
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name in ("web", "extractor"):
            environment = compose["services"][name]["environment"]
            assert environment.get(test_safety.RUNTIME_MARKER), (
                f"{path.parent.name}/{name} carries no {test_safety.RUNTIME_MARKER}"
            )


# ---------------------------------------------------------------------------
# 4. A real pytest, in a real hostile environment
# ---------------------------------------------------------------------------
#
# Everything above runs inside a process that is already safe. These start a new
# one, because the question — "what happens when the environment was inherited
# from a production image?" — is a question about process start-up that no
# fixture inside the process can answer.

#: One cheap, database-free test to run in the child. Naming a single test keeps
#: the child's collection to one module.
INNER_TEST = "tests/test_test_isolation.py::test_the_settings_in_force_are_the_test_settings"

#: What the production image bakes in, and the whole reason the ini key was not
#: enough.
HOSTILE_SETTINGS = {"DJANGO_SETTINGS_MODULE": "config.settings"}


def _run_pytest(target: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, **overrides}
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            sys.executable,
            "-m",
            "pytest",
            target,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_an_inherited_production_settings_module_does_not_decide_the_suite() -> None:
    """The precedence defect, end to end.

    With only the ini key, this child would have loaded ``config.settings`` and
    passed anyway — the inner test asserts which settings are in force, so it
    would have failed. `--ds` in addopts is what makes it pass.
    """
    result = _run_pytest(INNER_TEST, **HOSTILE_SETTINGS)
    report = _output(result)
    assert result.returncode == 0, report
    assert "1 passed" in result.stdout, report


def test_real_data_in_the_inherited_environment_stops_the_suite_before_it_starts() -> None:
    """Outcome B of the acceptance test: an immediate, explained refusal."""
    result = _run_pytest(INNER_TEST, **HOSTILE_SETTINGS, REAL_DATA_ALLOWED="1")
    report = _output(result)
    assert result.returncode != 0, report
    assert "JURISTID TEST SAFETY" in report
    assert "REAL_DATA_ALLOWED" in report
    # Refused before collection, not after a fixture eventually redirected
    # something. If any test ran, the guard is in the wrong place.
    assert "passed" not in result.stdout, report


def test_a_production_evidence_root_in_the_inherited_environment_stops_the_suite() -> None:
    """And outcome B again for the mount that actually received the 63 objects."""
    result = _run_pytest(INNER_TEST, **HOSTILE_SETTINGS, EVIDENCE_ROOT="/app/evidence")
    report = _output(result)
    assert result.returncode != 0, report
    assert "JURISTID TEST SAFETY" in report
    assert "EVIDENCE_ROOT" in report
    assert "passed" not in result.stdout, report


def test_the_deployment_marker_in_the_inherited_environment_stops_the_suite() -> None:
    result = _run_pytest(INNER_TEST, **{test_safety.RUNTIME_MARKER: "deployed"})
    report = _output(result)
    assert result.returncode != 0, report
    assert "JURISTID TEST SAFETY" in report
    assert test_safety.RUNTIME_MARKER in report
    assert "passed" not in result.stdout, report


def test_the_ordinary_path_still_runs() -> None:
    """Test 5: no hostile environment, nothing special, it simply works.

    Worth its subprocess. Every other test in this section proves a refusal, and
    a guard that refuses everything would satisfy all of them.
    """
    result = _run_pytest(INNER_TEST)
    report = _output(result)
    assert result.returncode == 0, report
    assert "1 passed" in result.stdout, report
