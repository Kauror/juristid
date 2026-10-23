"""A release moves a running MCP server with it, and proves it (MCPSEC-13).

`mcp` is behind the `mcp` profile, so the release's `up -d --no-build` leaves a
running one on the previous image — old code against a migrated schema, and a
security fix in `app.mcp_server` that never takes effect — unless somebody
remembers a README line. The preflight now looks at `juristid-main-mcp` and
the plan it prints says what to do: running, it names `mcp` (`--no-deps`, so
`db` and `tunnel` are never recreated) and asserts that `mcp` and `web` both
run the release tag; stopped, it says to leave it stopped; absent, nothing.

Rendered by the shell that renders it in production (`scripts/deploy/lib.sh`),
with a stand-in `docker` on PATH for the state probe.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
LIB = ROOT / "scripts" / "deploy" / "lib.sh"
PREFLIGHT = ROOT / "scripts" / "deploy" / "juristid-deploy-preflight.sh"
BASH = shutil.which("bash") or "bash"
TARGET = "b" * 40
TAG = TARGET[:12]
COMPOSE = "docker compose -p juristid-main -f deploy/unraid-main/compose.yml"
MCP_UP = f"{COMPOSE} up -d --no-build --no-deps mcp"


def _bash(script: str, *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a generated literal
        [BASH, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **(env or {})},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _plan(state: str | None) -> list[str]:
    extra = f' "{state}"' if state is not None else ""
    out = _bash(
        f'. "{LIB.as_posix()}"\n'
        "deployment_plan juristid-main deploy/unraid-main/compose.yml /srv/repo "
        f'"{TARGET}"{extra}\n'
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _commands(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith("#")]


def test_a_running_mcp_is_named_after_the_release_up_and_checked():
    lines = _commands(_plan("running"))
    release_up = lines.index(f"{COMPOSE} up -d --no-build")
    mcp_up = lines.index(MCP_UP)
    readiness = next(i for i, line in enumerate(lines) if "deployment_readiness" in line)
    assert release_up < mcp_up < readiness
    [check] = [line for line in lines if line.startswith("test ") and "juristid-main-mcp" in line]
    assert f'= "juristid-main-web:{TAG}"' in check
    assert "juristid-main-web)" in check, "web's image is asserted too"
    assert "STOP:" in check
    assert lines.index(check) == mcp_up + 1


@pytest.mark.parametrize("state", ["stopped", "absent", None])
def test_a_stopped_or_absent_mcp_is_never_started_by_a_release(state):
    lines = _plan(state)
    for line in _commands(lines):
        assert " mcp" not in line.replace("juristid-main-mcp", ""), line
    if state == "stopped":
        assert any("leave it stopped" in line for line in lines)


def test_the_mcp_line_never_recreates_db_or_tunnel():
    [line] = [line for line in _commands(_plan("running")) if line.endswith(" mcp")]
    assert "--no-deps" in line and "--no-build" in line


def _fake_docker(tmp_path: Path, state: str) -> dict[str, str]:
    """A `docker` that answers the one probe with ``state`` (empty: no container)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = ps ]; then\n'
        f'  [ -n "{state}" ] && printf "%s\\n" "{state}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    path = os.environ.get("PATH", "")
    return {"PATH": f"{bin_dir.as_posix()}{os.pathsep}{path}"}


@pytest.mark.parametrize(
    ("docker_state", "expected"),
    [("running", "running"), ("exited", "stopped"), ("created", "stopped"), ("", "absent")],
)
def test_the_preflight_reads_the_containers_state(tmp_path, docker_state, expected):
    env = _fake_docker(tmp_path, docker_state)
    out = _bash(f'. "{LIB.as_posix()}"\nmcp_container_state juristid-main\n', env=env)
    assert out.strip() == expected


def test_only_the_production_project_has_an_mcp(tmp_path):
    env = _fake_docker(tmp_path, "running")
    out = _bash(f'. "{LIB.as_posix()}"\nmcp_container_state juristid-recovery-rehearsal\n', env=env)
    assert out.strip() == "absent"


def test_the_preflight_hands_the_state_to_the_plan():
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert '"$(mcp_container_state "$JURISTID_PROJECT")"' in text
