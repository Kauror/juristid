"""Security review 2026-10-29, assessor D — finding D-05 (P3, CONFIRMED on the host).

The production host held nine `*.bak-*` copies of earlier environment files
beside the live ones — each 0600, each holding the secrets of the day it was
taken, including the shared-gate password a rotation was meant to retire (the
README says never to rotate back). The preflight checked the live file's mode
and a stray `.env` beside the Compose file, and never looked sideways.

It now warns, by name only, about any `*.bak*`, `*~` or `*.orig` beside the
environment file. A warning rather than a refusal: deleting or escrowing the
copies is the owner's step, not the release's.

The host step sits after the Compose step, which needs a `docker` on PATH to
get past `require_command`. A stub `docker` that answers `compose config` with
the Compose file itself stands in for it, so the test runs the same on a
developer machine without Docker and on a CI runner with one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_deployment_scripts import BASH, COMPOSE, PREFLIGHT, ROOT


@pytest.fixture
def env_dir(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    live = config / "juristid.env"
    live.write_text("DJANGO_SECRET_KEY=not-a-real-value\n", encoding="utf-8")
    os.chmod(live, 0o600)
    return config


@pytest.fixture
def stub_docker(tmp_path: Path) -> Path:
    """A `docker` whose `compose … config` prints the Compose file; the rest is silent."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = compose ]; then\n'
        "  while [ $# -gt 0 ]; do\n"
        '    if [ "$1" = -f ]; then cat "$2"; exit 0; fi\n'
        "    shift\n"
        "  done\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o700)
    return bin_dir


def _preflight(tmp_path: Path, env_file: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a repository path
        [
            BASH,
            str(PREFLIGHT),
            "--repo",
            str(tmp_path),
            "--target",
            "0" * 40,
            "--compose-file",
            str(COMPOSE),
            "--env-file",
            str(env_file),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_a_backup_copy_beside_the_environment_file_is_named_in_a_warning(
    tmp_path: Path, env_dir: Path, stub_docker: Path
) -> None:
    copy = env_dir / "juristid.env.bak-pre-rotation-20261001T120000Z"
    copy.write_text("DJANGO_SECRET_KEY=the-old-value\n", encoding="utf-8")
    (env_dir / "juristid.env~").write_text("x=1\n", encoding="utf-8")
    result = _preflight(tmp_path, env_dir / "juristid.env", stub_docker)
    assert "== The host" in result.stdout, result.stdout + result.stderr
    named = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("WARN") and "beside the live one" in line
    ]
    assert len(named) == 2, result.stdout
    assert any(copy.name in line for line in named)
    assert any("juristid.env~" in line for line in named)
    assert "the-old-value" not in result.stdout + result.stderr, "names only, never a value"


def test_no_warning_when_only_the_live_file_is_there(
    tmp_path: Path, env_dir: Path, stub_docker: Path
) -> None:
    result = _preflight(tmp_path, env_dir / "juristid.env", stub_docker)
    assert "== The host" in result.stdout, result.stdout + result.stderr
    assert "beside the live one" not in result.stdout, result.stdout
