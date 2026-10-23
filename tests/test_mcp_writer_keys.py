"""The write gateway's secrets are generated into its files, equal where shared, never printed.

`scripts/deploy/juristid-mcp-writer-keys.sh` (docs/mcp/OPERATIONS.md, Phase 2)
puts the writer's signing key and database password into
juristid-mcp-writer.env and one gateway key into both that file and
juristid-mcp.env. Run by the shell that runs it in production, on the
templates copied into a temporary directory.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
HELPER = ROOT / "scripts" / "deploy" / "juristid-mcp-writer-keys.sh"
DEPLOY = ROOT / "deploy" / "unraid-main"
BASH = shutil.which("bash") or "bash"
STRONG = "s" * 64


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and a fixed script
        [BASH, HELPER.as_posix(), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ),
    )


def _files(tmp_path: Path) -> tuple[Path, Path]:
    writer = tmp_path / "juristid-mcp-writer.env"
    mcp = tmp_path / "juristid-mcp.env"
    shutil.copyfile(DEPLOY / "juristid-mcp-writer.env.example", writer)
    shutil.copyfile(DEPLOY / "juristid-mcp.env.example", mcp)
    return writer, mcp


def _helper(writer: Path, mcp: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return _run("--writer-env-file", writer.as_posix(), "--mcp-env-file", mcp.as_posix(), *extra)


def _values(path: Path, key: str) -> list[str]:
    return re.findall(rf"(?m)^{key}=(.*)$", path.read_text(encoding="utf-8"))


def test_the_templates_get_every_secret_generated_and_the_gateway_key_shared(tmp_path):
    writer, mcp = _files(tmp_path)
    result = _helper(writer, mcp)
    assert result.returncode == 0, result.stderr
    printed = result.stdout + result.stderr
    for key, minimum in (("DJANGO_SECRET_KEY", 50), ("POSTGRES_PASSWORD", 32)):
        [value] = _values(writer, key)
        assert len(value) >= minimum and not value.startswith("replace-me"), key
        assert value not in printed
    [writer_key] = _values(writer, "MCP_WRITE_GATEWAY_KEY")
    [mcp_key] = _values(mcp, "MCP_WRITE_GATEWAY_KEY")
    assert writer_key == mcp_key and len(writer_key) >= 40
    assert writer_key not in printed
    # mcp's own secrets are not this script's business, and writes stay off.
    assert _values(mcp, "DJANGO_SECRET_KEY") == ["replace-me-with-openssl-rand-base64-64"]
    assert _values(mcp, "MCP_WRITES_ENABLED") == ["0"]
    assert _values(writer, "POSTGRES_USER") == ["juristid_mcp_writer"]


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    writer, mcp = _files(tmp_path)
    assert _helper(writer, mcp).returncode == 0
    before = (writer.read_text(encoding="utf-8"), mcp.read_text(encoding="utf-8"))
    result = _helper(writer, mcp)
    assert result.returncode == 0, result.stderr
    assert (writer.read_text(encoding="utf-8"), mcp.read_text(encoding="utf-8")) == before
    assert "kept" in result.stdout


def test_one_good_gateway_key_is_copied_to_the_other_file(tmp_path):
    writer, mcp = _files(tmp_path)
    text = mcp.read_text(encoding="utf-8").replace(
        "MCP_WRITE_GATEWAY_KEY=replace-me-with-openssl-rand-base64-48",
        f"MCP_WRITE_GATEWAY_KEY={STRONG}",
    )
    mcp.write_text(text, encoding="utf-8")
    result = _helper(writer, mcp)
    assert result.returncode == 0, result.stderr
    assert _values(writer, "MCP_WRITE_GATEWAY_KEY") == [STRONG]
    assert _values(mcp, "MCP_WRITE_GATEWAY_KEY") == [STRONG]
    assert STRONG not in result.stdout + result.stderr


def test_two_different_good_keys_are_refused_and_nothing_changes(tmp_path):
    writer, mcp = _files(tmp_path)
    assert _helper(writer, mcp).returncode == 0
    text = mcp.read_text(encoding="utf-8")
    [current] = _values(mcp, "MCP_WRITE_GATEWAY_KEY")
    mcp.write_text(text.replace(current, "t" * 64), encoding="utf-8")
    before = (writer.read_text(encoding="utf-8"), mcp.read_text(encoding="utf-8"))
    result = _helper(writer, mcp)
    assert result.returncode != 0
    assert "differs" in result.stderr
    assert (writer.read_text(encoding="utf-8"), mcp.read_text(encoding="utf-8")) == before

    rotated = _helper(writer, mcp, "--rotate-gateway-key")
    assert rotated.returncode == 0, rotated.stderr
    [writer_key] = _values(writer, "MCP_WRITE_GATEWAY_KEY")
    assert _values(mcp, "MCP_WRITE_GATEWAY_KEY") == [writer_key]
    assert writer_key not in (current, "t" * 64)


@pytest.mark.parametrize(
    ("writer_name", "mcp_name"),
    [
        ("juristid.env", "juristid-mcp.env"),
        ("juristid-mcp.env", "juristid-mcp-2.env"),
        ("juristid-mcp-writer.env", "juristid.env"),
        ("juristid-mcp-writer.env", "juristid-mcp-tunnel.env"),
    ],
)
def test_the_wrong_files_are_refused_untouched(tmp_path, writer_name, mcp_name):
    writer = tmp_path / writer_name
    mcp = tmp_path / mcp_name
    writer.write_text("POSTGRES_USER=juristid_mcp_writer\n", encoding="utf-8")
    mcp.write_text("POSTGRES_USER=juristid_mcp\n", encoding="utf-8")
    result = _helper(writer, mcp)
    assert result.returncode != 0
    assert writer.read_text(encoding="utf-8") == "POSTGRES_USER=juristid_mcp_writer\n"
    assert mcp.read_text(encoding="utf-8") == "POSTGRES_USER=juristid_mcp\n"


def test_the_writer_file_must_name_the_writer_role(tmp_path):
    writer, mcp = _files(tmp_path)
    writer.write_text(
        writer.read_text(encoding="utf-8").replace(
            "POSTGRES_USER=juristid_mcp_writer", "POSTGRES_USER=juristid"
        ),
        encoding="utf-8",
    )
    result = _helper(writer, mcp)
    assert result.returncode != 0
    assert "juristid_mcp_writer" in result.stderr


def test_the_mcp_file_must_not_name_the_writer_role(tmp_path):
    writer, mcp = _files(tmp_path)
    mcp.write_text(
        mcp.read_text(encoding="utf-8").replace(
            "POSTGRES_USER=juristid_mcp", "POSTGRES_USER=juristid_mcp_writer"
        ),
        encoding="utf-8",
    )
    result = _helper(writer, mcp)
    assert result.returncode != 0
    assert "E058" in result.stderr


def test_a_web_file_holding_the_gateway_key_is_refused(tmp_path):
    writer, mcp = _files(tmp_path)
    web = tmp_path / "juristid.env"
    web.write_text(f"MCP_WRITES_ENABLED=1\nMCP_WRITE_GATEWAY_KEY={STRONG}\n", encoding="utf-8")
    result = _helper(writer, mcp, "--web-env-file", web.as_posix())
    assert result.returncode != 0
    assert "E057" in result.stderr
    assert STRONG not in result.stdout + result.stderr
    web.write_text("MCP_WRITES_ENABLED=1\n", encoding="utf-8")
    assert _helper(writer, mcp, "--web-env-file", web.as_posix()).returncode == 0


def test_a_missing_file_or_unknown_argument_stops_it(tmp_path):
    writer, _ = _files(tmp_path)
    assert _helper(writer, tmp_path / "juristid-mcp-missing.env").returncode != 0
    result = _run("--writer-env-file", writer.as_posix(), "--typo", "x")
    assert result.returncode != 0
    assert "unknown argument" in result.stderr
    assert "Usage:" in _run("--help").stdout


def test_the_script_is_executable_in_git():
    listed = subprocess.run(
        ["git", "ls-files", "-s", "scripts/deploy/juristid-mcp-writer-keys.sh"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.startswith("100755"), listed or "not tracked yet"
