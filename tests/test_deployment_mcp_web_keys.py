"""Switching AI access on can never leave `web` unable to start (security review NEW-09).

`web` refuses to start on real data with `MCP_ENABLED=1` and no proper
`MCP_AUTHENTICATOR_KEY` (mcp_access.E004). The runbook used to generate the key
with a `sed` that rewrote the template's commented line — a silent no-op on a
juristid.env without that line, and then all of Juristid was down. Now:

- `scripts/deploy/juristid-mcp-web-keys.sh` writes each key web needs and
  proves the write (one line, `KEY=` and 40+ characters), keeps a key that is
  already set (never rotates it), and prints no value;
- the preflight refuses a juristid.env that has `MCP_ENABLED` on without them;
- the runbook runs the helper and the `grep -c` check *before* it sets
  `MCP_ENABLED=1`;
- with `MCP_ENABLED` off, `web` needs no key at all.

Run by the shell that runs them in production, on files in a temporary
directory.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

from app.mcp_access import checks

ROOT = Path(settings.BASE_DIR)
LIB = ROOT / "scripts" / "deploy" / "lib.sh"
HELPER = ROOT / "scripts" / "deploy" / "juristid-mcp-web-keys.sh"
PREFLIGHT = ROOT / "scripts" / "deploy" / "juristid-deploy-preflight.sh"
OPERATIONS = ROOT / "docs" / "mcp" / "OPERATIONS.md"
BASH = shutil.which("bash") or "bash"

#: The keys `web` must hold before AI access is switched on.
WEB_KEYS = ("MCP_AUTHENTICATOR_KEY", "MCP_TOKEN_DERIVATION_KEY")
STRONG = "s" * 64
TEMPLATE_LINE = "# MCP_AUTHENTICATOR_KEY=replace-me-with-openssl-rand-base64-48"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and fixed scripts
        [BASH, *args], cwd=ROOT, capture_output=True, text=True, timeout=60, env=dict(os.environ)
    )


def _helper(env_file: Path) -> subprocess.CompletedProcess[str]:
    return _run([HELPER.as_posix(), "--env-file", env_file.as_posix()])


def _lib(call: str) -> subprocess.CompletedProcess[str]:
    return _run(["-c", f'set -euo pipefail\n. "{LIB.as_posix()}"\n{call}\n'])


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "juristid.env"
    path.write_bytes(text.encode("utf-8"))
    return path


def _values(path: Path, key: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(rf"(?m)^{key}=(.*)$", text)


def _names_a_key(line: str) -> bool:
    return any(key in line for key in WEB_KEYS)


def _verified(path: Path, key: str) -> int:
    """The runbook's own check: `grep -c '^KEY=[^[:space:]]\\{40,\\}$' file`."""
    text = path.read_bytes().decode("utf-8")
    return len(re.findall(rf"(?m)^{key}=\S{{40,}}$", text))


# -- the helper -------------------------------------------------------------------


@pytest.mark.parametrize(
    "original",
    [
        # The template, with its commented line.
        f"DJANGO_SECRET_KEY=x\n{TEMPLATE_LINE}\nMCP_ENABLED=0\n",
        # No line at all — the case the old `sed` silently did nothing for —
        # and no newline after the last line either.
        "DJANGO_SECRET_KEY=x\nMCP_ENABLED=0",
        # A placeholder somebody uncommented, a short value, and a duplicate.
        "MCP_AUTHENTICATOR_KEY=replace-me-with-openssl-rand-base64-48-xxxxxx\nMCP_ENABLED=0\n",
        "MCP_AUTHENTICATOR_KEY=short\nMCP_ENABLED=0\n",
        f"MCP_AUTHENTICATOR_KEY={STRONG}\nMCP_AUTHENTICATOR_KEY={STRONG}\nMCP_ENABLED=0\n",
    ],
    ids=["template", "no-line-no-newline", "placeholder", "short", "duplicate"],
)
def test_the_helper_writes_one_verified_key_and_prints_no_value(tmp_path, original):
    path = _write(tmp_path, original)
    result = _helper(path)
    assert result.returncode == 0, result.stderr
    for key in WEB_KEYS:
        assert _verified(path, key) == 1
        [value] = _values(path, key)
        assert len(value) >= 40 and not value.startswith("replace-me")
        assert value not in result.stdout + result.stderr
    assert "Only now set MCP_ENABLED=1." in result.stdout
    # Every other line is kept, the last one whole, and MCP_ENABLED untouched.
    kept = [
        line for line in path.read_text(encoding="utf-8").splitlines() if not _names_a_key(line)
    ]
    assert kept == [line for line in original.splitlines() if not _names_a_key(line)]
    assert kept[-1] == "MCP_ENABLED=0"


def test_a_key_already_set_is_kept_never_rotated(tmp_path):
    original = (
        f"MCP_AUTHENTICATOR_KEY={STRONG}\n{TEMPLATE_LINE}\n"
        f"MCP_TOKEN_DERIVATION_KEY={'d' * 50}\nMCP_ENABLED=1\n"
    )
    path = _write(tmp_path, original)
    result = _helper(path)
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == original
    assert "kept" in result.stdout


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    path = _write(tmp_path, f"{TEMPLATE_LINE}\n")
    assert _helper(path).returncode == 0
    first = path.read_text(encoding="utf-8")
    assert _helper(path).returncode == 0
    assert path.read_text(encoding="utf-8") == first


@pytest.mark.parametrize("name", ["juristid-mcp.env", "juristid-mcp-tunnel.env"])
def test_the_helper_refuses_an_ai_access_services_file(tmp_path, name):
    path = tmp_path / name
    path.write_text("POSTGRES_USER=juristid_mcp\n", encoding="utf-8")
    result = _helper(path)
    assert result.returncode != 0
    assert path.read_text(encoding="utf-8") == "POSTGRES_USER=juristid_mcp\n"


def test_the_helper_refuses_a_missing_file(tmp_path):
    result = _helper(tmp_path / "juristid.env")
    assert result.returncode != 0
    assert not (tmp_path / "juristid.env").exists()


# -- the library the preflight uses ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "enabled"),
    [
        ("MCP_ENABLED=1\n", True),
        ("MCP_ENABLED=true\n", True),
        ("MCP_ENABLED=On\n", True),
        ('MCP_ENABLED="yes"\n', True),
        ("MCP_ENABLED=0\n", False),
        ("MCP_ENABLED=\n", False),
        ("# MCP_ENABLED=1\n", False),
        ("MCP_ENABLED=1\nMCP_ENABLED=0\n", False),
        ("", False),
    ],
    ids=["1", "true", "On", "quoted-yes", "0", "empty", "commented", "last-wins", "absent"],
)
def test_the_library_reads_mcp_enabled_as_the_settings_do(tmp_path, text, enabled):
    path = _write(tmp_path, text)
    result = _lib(f'if mcp_enabled_in "{path.as_posix()}"; then echo on; else echo off; fi')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("on" if enabled else "off")


@pytest.mark.parametrize(
    ("text", "state"),
    [
        (f"MCP_AUTHENTICATOR_KEY={STRONG}\n", "ok"),
        (f"{TEMPLATE_LINE}\n", "missing"),
        ("", "missing"),
        ("MCP_AUTHENTICATOR_KEY=short\n", "weak"),
        ("MCP_AUTHENTICATOR_KEY=replace-me-" + "x" * 40 + "\n", "weak"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG}\n MCP_AUTHENTICATOR_KEY={STRONG}\n", "duplicated"),
    ],
    ids=["ok", "commented", "absent", "short", "placeholder", "duplicated"],
)
def test_the_library_names_each_keys_state(tmp_path, text, state):
    path = _write(tmp_path, text)
    result = _lib(f'mcp_web_key_state "{path.as_posix()}" MCP_AUTHENTICATOR_KEY')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == state


# -- one rule for every MCP key (HR-18) -------------------------------------------
#
# `mcp_key_state FILE KEY MIN` in lib.sh is the only definition of a valid key:
# web's keys (40, through mcp_web_key_state and the preflight) and the write
# gateway's secrets (juristid-mcp-writer-keys.sh, 50/32/40) both use it. Before,
# web's check was `^KEY=.{40,}` and accepted whitespace and a carriage return.


@pytest.mark.parametrize(
    ("text", "minimum", "state"),
    [
        (f"MCP_AUTHENTICATOR_KEY={STRONG}\n", 40, "ok"),
        (f"MCP_AUTHENTICATOR_KEY={'a' * 40}", 40, "ok"),
        (f"MCP_AUTHENTICATOR_KEY={'a' * 39}\n", 40, "weak"),
        (f"MCP_AUTHENTICATOR_KEY={'a' * 49}\n", 50, "weak"),
        (f"MCP_AUTHENTICATOR_KEY={'a' * 32}\n", 32, "ok"),
        ("MCP_AUTHENTICATOR_KEY=\n", 40, "weak"),
        (f'MCP_AUTHENTICATOR_KEY="{STRONG}"\n', 40, "ok"),
        (f"MCP_AUTHENTICATOR_KEY= {STRONG}\n", 40, "malformed"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG} \n", 40, "malformed"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG[:20]} {STRONG[20:]}\n", 40, "malformed"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG}\t\n", 40, "malformed"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG[:20]}\t{STRONG[20:]}\n", 40, "malformed"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG}\r\n", 40, "malformed"),
        (f"OTHER=1\r\nMCP_AUTHENTICATOR_KEY={STRONG}\r\n", 40, "malformed"),
        (f"  MCP_AUTHENTICATOR_KEY={STRONG}\n", 40, "malformed"),
        ("MCP_AUTHENTICATOR_KEY=short value\n", 40, "malformed"),
        ("OTHER=1\n", 40, "missing"),
        (f"# MCP_AUTHENTICATOR_KEY={STRONG}\n", 40, "missing"),
        (f"MCP_AUTHENTICATOR_KEY={STRONG}\nMCP_AUTHENTICATOR_KEY= x\n", 40, "duplicated"),
    ],
    ids=[
        "ok",
        "exactly-min-no-newline",
        "short",
        "short-for-50",
        "ok-for-32",
        "empty",
        "quoted-as-before",
        "leading-space-in-value",
        "trailing-space",
        "inner-space",
        "trailing-tab",
        "inner-tab",
        "crlf",
        "crlf-file",
        "indented-line",
        "short-with-space",
        "missing-line",
        "commented",
        "duplicated",
    ],
)
def test_one_rule_names_each_keys_state(tmp_path, text, minimum, state):
    path = _write(tmp_path, text)
    result = _lib(f'mcp_key_state "{path.as_posix()}" MCP_AUTHENTICATOR_KEY {minimum}')
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{state}\n"
    if minimum == 40:
        web = _lib(f'mcp_web_key_state "{path.as_posix()}" MCP_AUTHENTICATOR_KEY')
        assert web.stdout == f"{state}\n"


def test_one_rule_refuses_a_missing_file(tmp_path):
    absent = (tmp_path / "absent.env").as_posix()
    result = _lib(f'mcp_key_state "{absent}" MCP_AUTHENTICATOR_KEY 40')
    assert result.returncode != 0
    assert result.stdout == ""
    assert "cannot read" in result.stderr


@pytest.mark.parametrize("minimum", ["", "0", "forty", "-1"])
def test_one_rule_refuses_a_bad_minimum(tmp_path, minimum):
    path = _write(tmp_path, f"MCP_AUTHENTICATOR_KEY={STRONG}\n")
    result = _lib(f'mcp_key_state "{path.as_posix()}" MCP_AUTHENTICATOR_KEY "{minimum}"')
    assert result.returncode != 0
    assert result.stdout == ""


def test_one_rule_never_prints_a_value_even_under_xtrace(tmp_path):
    """The rule only counts lines: no value is ever held in a variable a trace could show."""
    first, second = "Qz7Lm2Xc9Rt4" * 2, "Wp3Hk8Nd5Bv1" * 2
    for value in (f"{first}{second}", f"{first} {second}", f"{first}{second}\r", "short"):
        path = _write(tmp_path, f"MCP_AUTHENTICATOR_KEY={value}\n")
        result = _lib(f'set -x; mcp_key_state "{path.as_posix()}" MCP_AUTHENTICATOR_KEY 40')
        assert result.returncode == 0, result.stderr
        # The trace really ran — every grep of the rule is in it — and holds no value.
        assert result.stderr.count("grep -UcE") == 4
        for part in (first, second, "short"):
            assert part not in result.stdout + result.stderr


def test_the_writer_script_has_no_private_copy_of_the_rule():
    writer = (ROOT / "scripts" / "deploy" / "juristid-mcp-writer-keys.sh").read_text(
        encoding="utf-8"
    )
    assert "mcp_key_state" in writer
    assert "key_state() {" not in writer
    assert "[^[:space:]]{" not in writer


@pytest.mark.parametrize(
    "line",
    [
        f"MCP_AUTHENTICATOR_KEY={STRONG} ",
        f"MCP_AUTHENTICATOR_KEY={STRONG}\r",
        f" MCP_AUTHENTICATOR_KEY={STRONG}",
    ],
    ids=["trailing-space", "crlf", "indented"],
)
def test_the_helper_never_replaces_a_malformed_key(tmp_path, line):
    """It may be the key web runs with: rotating it would lock every authenticator out."""
    original = f"{line}\nMCP_ENABLED=0\n"
    path = _write(tmp_path, original)
    result = _helper(path)
    assert result.returncode != 0
    assert "Nothing was changed" in result.stderr
    assert "MCP_AUTHENTICATOR_KEY" in result.stderr
    # Deleting the line is not offered as free: it costs every enrolment.
    assert "every enrolled authenticator becomes unreadable" in result.stderr
    assert STRONG not in result.stdout + result.stderr
    assert path.read_bytes() == original.encode("utf-8")


def test_the_preflight_names_a_malformed_key_and_the_fix(tmp_path):
    env_file = (tmp_path / "juristid.env").as_posix()
    malformed = _lib(f'mcp_web_key_problem MCP_AUTHENTICATOR_KEY malformed "{env_file}"')
    assert malformed.returncode == 0, malformed.stderr
    assert malformed.stdout.count("\n") == 1
    assert "MCP_AUTHENTICATOR_KEY is malformed" in malformed.stdout
    assert "Fix that line by hand" in malformed.stdout
    assert "web would refuse to start" not in malformed.stdout
    for state in ("missing", "duplicated", "weak"):
        other = _lib(f'mcp_web_key_problem MCP_AUTHENTICATOR_KEY {state} "{env_file}"')
        assert f"MCP_AUTHENTICATOR_KEY is {state}: web would refuse to start" in other.stdout
        assert f"juristid-mcp-web-keys.sh --env-file {env_file}" in other.stdout
    # The preflight's own loop says exactly what the library composes.
    assert 'fail "$(mcp_web_key_problem "$key" "$state" "$ENV_FILE")"' in PREFLIGHT.read_text(
        encoding="utf-8"
    )


def test_the_runbooks_state_probe_names_each_key_and_prints_no_value(tmp_path):
    text = OPERATIONS.read_text(encoding="utf-8")
    [probe] = re.findall(r"(?m)^bash -c '\. scripts/deploy/lib\.sh; .*$", text)
    probe = probe.replace(
        "/mnt/user/appdata/juristid-main/config/juristid.env",
        (tmp_path / "juristid.env").as_posix(),
    )
    _write(tmp_path, f"MCP_AUTHENTICATOR_KEY={STRONG}\r\nMCP_TOKEN_DERIVATION_KEY=short\n")
    result = subprocess.run(  # noqa: S603 - the runbook's own command, on a temporary file
        [BASH, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "MCP_AUTHENTICATOR_KEY: malformed\nMCP_TOKEN_DERIVATION_KEY: weak\n"
    assert STRONG not in result.stdout + result.stderr


def test_the_library_lists_a_malformed_key_as_not_ready(tmp_path):
    path = _write(
        tmp_path,
        f"MCP_AUTHENTICATOR_KEY={STRONG} \nMCP_TOKEN_DERIVATION_KEY={STRONG}\n",
    )
    result = _lib(f'mcp_web_keys_not_ready "{path.as_posix()}"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "MCP_AUTHENTICATOR_KEY malformed\n"


def test_the_library_lists_every_key_that_is_not_ready(tmp_path):
    path = _write(tmp_path, f"{TEMPLATE_LINE}\n")
    result = _lib(f'mcp_web_keys_not_ready "{path.as_posix()}"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[:-1] == [f"{key} missing" for key in WEB_KEYS]
    ready = _write(tmp_path, "".join(f"{key}={STRONG}\n" for key in WEB_KEYS))
    result = _lib(f'mcp_web_keys_not_ready "{ready.as_posix()}"')
    assert result.returncode == 0 and result.stdout == ""


def test_the_library_and_the_settings_name_the_same_keys():
    lib = LIB.read_text(encoding="utf-8")
    [declared] = re.findall(r"(?m)^MCP_WEB_KEYS=\(([^)]*)\)$", lib)
    assert tuple(declared.split()) == WEB_KEYS


def test_the_preflight_refuses_mcp_enabled_without_the_keys():
    text = PREFLIGHT.read_text(encoding="utf-8")
    assert 'if mcp_enabled_in "$ENV_FILE"; then' in text
    assert 'mcp_web_keys_not_ready "$ENV_FILE"' in text
    # The message naming juristid-mcp-web-keys.sh lives in lib.sh, tested below.
    assert "mcp_web_key_problem" in text


# -- the runbook -------------------------------------------------------------------


def test_the_runbook_generates_and_verifies_the_keys_before_switching_on():
    text = OPERATIONS.read_text(encoding="utf-8")
    helper = text.index("scripts/deploy/juristid-mcp-web-keys.sh --env-file")
    for key in WEB_KEYS:
        check = text.index(f"grep -c '^{key}=[^[:space:]]\\{{40,\\}}$'")
        assert helper < check
    switch_on = text.index("| `MCP_ENABLED` | `1` |")
    assert helper < switch_on
    assert check < switch_on
    # The `sed` that silently did nothing on a file without the template line.
    assert "s|^# *MCP_AUTHENTICATOR_KEY=" not in text


# -- web without AI access ----------------------------------------------------------


def test_with_mcp_enabled_off_web_needs_no_key_on_real_data(settings):
    settings.MCP_ENABLED = False
    settings.REAL_DATA_ALLOWED = True
    settings.JURISTID_PROCESS_ROLE = ""
    settings.MCP_AUTHENTICATOR_KEY = ""
    settings.MCP_TOKEN_DERIVATION_KEY = ""
    assert checks.mcp_access_settings() == []


def test_with_mcp_enabled_on_real_data_the_keys_are_required(settings):
    from tests import mcp_access_support as support

    support.enable(settings)
    settings.REAL_DATA_ALLOWED = True
    settings.JURISTID_PROCESS_ROLE = ""
    settings.MCP_AUTHENTICATOR_KEY = ""
    settings.MCP_TOKEN_DERIVATION_KEY = ""
    found = {item.id for item in checks.mcp_access_settings()}
    assert {"mcp_access.E004", "mcp_access.E005"} <= found
