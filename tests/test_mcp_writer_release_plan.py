"""A release moves a running write gateway with it, and never starts a stopped one.

`mcp-writer` (docs/adr/0189) runs the release image behind the `mcp` profile,
like `mcp`: an unqualified `up -d --no-build` leaves it on the previous image.
The preflight reads `juristid-main-mcp-writer`'s state and the printed plan
names it (`--no-deps`, so `db` and `tunnel` are never recreated) and checks its
image when it runs, says to leave it stopped when it is stopped — AI writes
switched off stay off — and says nothing when it is absent.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tests.test_deployment_mcp_release import (
    BASH,
    COMPOSE,
    LIB,
    PREFLIGHT,
    ROOT,
    TAG,
    TARGET,
    _bash,
    _commands,
    _fake_docker,
)

WRITER_UP = f"{COMPOSE} up -d --no-build --no-deps mcp-writer"


def _plan(mcp: str, writer: str | None) -> list[str]:
    extra = f' "{writer}"' if writer is not None else ""
    out = _bash(
        f'. "{LIB.as_posix()}"\n'
        "deployment_plan juristid-main deploy/unraid-main/compose.yml /srv/repo "
        f'"{TARGET}" "{mcp}"{extra}\n'
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_a_running_writer_is_named_after_the_release_up_and_checked():
    lines = _commands(_plan("running", "running"))
    release_up = lines.index(f"{COMPOSE} up -d --no-build")
    writer_up = lines.index(WRITER_UP)
    readiness = next(i for i, line in enumerate(lines) if "deployment_readiness" in line)
    assert release_up < writer_up < readiness
    [check] = [line for line in lines if "juristid-main-mcp-writer)" in line]
    assert f'= "juristid-main-web:{TAG}"' in check and "STOP:" in check
    assert lines.index(check) == writer_up + 1


@pytest.mark.parametrize("writer", ["stopped", "absent", None])
def test_a_stopped_or_absent_writer_is_never_started_by_a_release(writer):
    lines = _plan("running", writer)
    for line in _commands(lines):
        assert "mcp-writer" not in line.replace("juristid-main-mcp-writer", ""), line
    if writer == "stopped":
        assert any("Leave it stopped" in line for line in lines)


def test_the_writer_line_never_recreates_db_or_tunnel():
    [line] = [line for line in _commands(_plan("absent", "running")) if line.endswith("mcp-writer")]
    assert "--no-deps" in line and "--no-build" in line


@pytest.mark.parametrize(
    ("docker_state", "expected"),
    [("running", "running"), ("exited", "stopped"), ("", "absent")],
)
def test_the_preflight_reads_the_writers_state(tmp_path, docker_state, expected):
    env = _fake_docker(tmp_path, docker_state)
    out = _bash(
        f'. "{LIB.as_posix()}"\nmcp_container_state juristid-main juristid-main-mcp-writer\n',
        env=env,
    )
    assert out.strip() == expected


def test_the_state_probe_names_only_the_two_mcp_containers(tmp_path):
    env = _fake_docker(tmp_path, "running")
    script = f'. "{LIB.as_posix()}"\nmcp_container_state juristid-main juristid-main-db\n'
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a generated literal
        [BASH, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **env},
    )
    assert result.returncode != 0
    assert "unknown container" in result.stderr


def test_the_preflight_hands_the_writers_state_to_the_plan():
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert '"$(mcp_container_state "$JURISTID_PROJECT" juristid-main-mcp-writer)"' in text
