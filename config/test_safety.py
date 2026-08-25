"""Refusals that keep a test process away from a deployment's real data.

This lives in ``config`` rather than in ``tests`` because the first of these
checks has to run while ``config/test_settings.py`` is still importing — before
Django is set up, before a system check could speak, and long before a fixture
could redirect storage. The ``juristid.E00x`` family guards a *running*
application and runs after settings are already in force; by then a test has had
its chance to write a file, which is exactly what happened on 2026-08-24.

What went wrong, once, so the shape of these checks is readable:

    pytest ran inside a container built from the production image. That image
    sets ``DJANGO_SETTINGS_MODULE=config.settings``, and pytest-django reads the
    environment *before* the value in ``pyproject.toml`` — so production
    settings won, ``EVIDENCE_ROOT`` still pointed at the production evidence
    bind mount, and 63 synthetic fixtures were written into the Chamber's
    evidence store. Django isolated the *database* and dropped it afterwards.
    Nothing isolated the filesystem.

So the rule here is not "the settings module is spelled correctly". It is: a
test process must refuse to start in an environment a deployment set up.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

#: The settings module the suite must be running under.
TEST_SETTINGS_MODULE = "config.test_settings"

#: Writable data roots a test may never be pointed at. The three ``/app`` paths
#: are where the deployment's Compose files bind-mount the evidence, derivative
#: and OneNote-source trees; the three under the checkout are the defaults
#: ``config/settings.py`` falls back to, and a suite that filled those would be
#: writing into somebody's working copy.
#:
#: Deliberately not a general "must be under the system temporary directory"
#: rule: a legitimate temporary directory can live wherever ``TMPDIR`` says, and
#: a check that guessed wrong about that would be switched off within a week.
FORBIDDEN_ROOTS: tuple[Path, ...] = (
    Path("/app/evidence"),
    Path("/app/derivatives"),
    Path("/app/legacy-source"),
    REPOSITORY_ROOT / "evidence",
    REPOSITORY_ROOT / "derivatives",
    REPOSITORY_ROOT / "legacy-source",
)

#: Environment variables that name a writable storage root. Read-only source
#: material (``HISTORICAL_SOURCE_ROOT``) is deliberately absent: a test that
#: reads the corpus is doing something legitimate, and nothing it does there can
#: leave an object behind.
WRITABLE_ROOT_VARIABLES: tuple[str, ...] = (
    "EVIDENCE_ROOT",
    "DERIVATIVE_ROOT",
    "LEGACY_SOURCE_ROOT",
)

#: Marker a deployed stack sets on itself. Nothing else sets it, so any value at
#: all means "this environment belongs to a running deployment" — which is the
#: one place the suite must never run.
RUNTIME_MARKER = "JURISTID_RUNTIME"

_HEADING = "JURISTID TEST SAFETY — refusing to start the test suite."

_CORRECTIVE_ACTION = """
Run the suite in CI, or in a development environment of its own, or in a
Compose project created for testing. Never through a deployed stack:
`docker compose -p juristid-main run ... pytest` inherits that stack's
environment and its evidence bind mount, so a test that writes a file
writes it into the real evidence store.

The canonical command is `uv run pytest` from the repository root. It
selects config.test_settings explicitly (pyproject.toml, addopts), so an
inherited DJANGO_SETTINGS_MODULE cannot decide it.
""".strip()


class UnsafeTestEnvironment(RuntimeError):
    """A test process was started against configuration that can reach real data."""


def _is_true(raw: str | None) -> bool:
    # Mirrors config.env.env_bool without importing it: this module is imported
    # before the settings package has finished loading.
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolved(value: Any) -> Path | None:
    """A path, resolved through symlinks, or ``None`` if it is not one.

    ``resolve()`` rather than string comparison, because a symlink pointing at
    the evidence mount is the obvious way past an equality test, and because a
    relative path is otherwise not comparable at all.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Path(text).resolve()
    except OSError:  # pragma: no cover - a path the platform cannot even parse
        return None


def forbidden_root(value: Any) -> Path | None:
    """The forbidden root ``value`` falls inside, if any."""
    candidate = _resolved(value)
    if candidate is None:
        return None
    for root in FORBIDDEN_ROOTS:
        resolved_root = root.resolve()
        if candidate == resolved_root or candidate.is_relative_to(resolved_root):
            return root
    return None


def environment_refusals(environ: Mapping[str, str] | None = None) -> list[str]:
    """Why this *environment* is not one a test process may start in.

    Read from the environment rather than from the settings, because
    ``config/test_settings.py`` overrides both ``REAL_DATA_ALLOWED`` and the
    storage roots — so by the time they are settings they look safe, and the
    only surviving evidence of where the process was started is here. Tests that
    flip ``settings.REAL_DATA_ALLOWED`` to exercise the gate are unaffected:
    that is a setting, and this reads the environment.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    refusals: list[str] = []

    if _is_true(source.get("REAL_DATA_ALLOWED")):
        refusals.append(
            f"REAL_DATA_ALLOWED={source.get('REAL_DATA_ALLOWED')!r} is set in the "
            "environment. That flag marks an environment permitted to hold the "
            "Chamber's register and member material, and the suite has no business "
            "running in one. It is refused here rather than silently overridden."
        )

    marker = (source.get(RUNTIME_MARKER) or "").strip()
    if marker:
        refusals.append(
            f"{RUNTIME_MARKER}={marker!r} says this environment belongs to a running "
            "deployment. The suite must not be run through a deployed stack."
        )

    for name in WRITABLE_ROOT_VARIABLES:
        offender = forbidden_root(source.get(name))
        if offender is not None:
            refusals.append(
                f"{name}={source.get(name)!r} resolves inside {offender.as_posix()} — a "
                "deployment's writable storage. An environment pointing there is a "
                "deployment's environment, whichever settings module ends up winning."
            )

    return refusals


def storage_refusals(roots: Mapping[str, Any]) -> list[str]:
    """Why these *effective* storage roots are not isolated.

    The environment check above is about where the process was started; this one
    is about where it would actually write, so an edit to the test settings that
    quietly reintroduced a real root fails here rather than in production.
    """
    refusals: list[str] = []
    for name, value in roots.items():
        offender = forbidden_root(value)
        if offender is not None:
            refusals.append(
                f"{name}={str(value)!r} resolves inside {offender.as_posix()}. Test storage "
                "must be temporary and its own."
            )
    return refusals


def _refuse(refusals: Iterable[str], *, context: Mapping[str, str] | None = None) -> None:
    lines = [_HEADING, ""]
    lines.extend(f"  * {refusal}" for refusal in refusals)
    if context:
        lines.append("")
        lines.extend(f"  {name}: {value}" for name, value in context.items())
    lines.extend(["", _CORRECTIVE_ACTION])
    raise UnsafeTestEnvironment("\n".join(lines))


def assert_environment_is_safe_for_tests(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed before the test settings have finished importing."""
    refusals = environment_refusals(environ)
    if refusals:
        source: Mapping[str, str] = os.environ if environ is None else environ
        _refuse(
            refusals,
            # The value pytest-django resolved, which is the module actually
            # being loaded — not necessarily the one the caller exported.
            context={
                "settings module being loaded": source.get("DJANGO_SETTINGS_MODULE", "(unset)")
            },
        )


def assert_storage_is_isolated(roots: Mapping[str, Any]) -> None:
    """Fail closed once the test settings have chosen their storage roots."""
    refusals = storage_refusals(roots)
    if refusals:
        _refuse(refusals)


def assert_test_settings_are_in_force() -> None:
    """The sentinel: prove, at test runtime, that all of the above actually held.

    Called from the repository-root ``conftest.py``, so it runs for every pytest
    invocation — including one whose rootdir was somewhere unexpected and which
    therefore never read ``pyproject.toml``. Everything it asserts is already
    guaranteed by the layers above; the point is that if one of them is ever
    removed, this says so before the first test rather than after the first
    written file.
    """
    from django.conf import settings

    # `SETTINGS_MODULE` reads None once anything has overridden settings, so the
    # environment variable pytest-django resolved is the honest answer here — it
    # is what Django was actually pointed at.
    module = os.environ.get("DJANGO_SETTINGS_MODULE") or getattr(settings, "SETTINGS_MODULE", None)
    refusals: list[str] = []

    if not getattr(settings, "IS_TEST_SETTINGS", False):
        refusals.append(
            f"the settings in force are {module!r}, which is not the test settings "
            f"module ({TEST_SETTINGS_MODULE}). pytest-django reads "
            "DJANGO_SETTINGS_MODULE from the environment before it reads "
            "pyproject.toml, so an inherited value can decide this."
        )

    present = {
        name: getattr(settings, name, None)
        for name in WRITABLE_ROOT_VARIABLES
        if getattr(settings, name, None) is not None
    }
    refusals.extend(storage_refusals(present))
    refusals.extend(environment_refusals())

    if refusals:
        _refuse(refusals, context={"effective settings module": str(module)})
