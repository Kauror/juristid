"""MCP keys: the read-only state check, and no valid key ever refused (HR-18).

`scripts/deploy/juristid-mcp-key-check.sh` reports each MCP key slot as `ok`,
`weak`, `missing`, `duplicated` or `malformed` — never a value, a part of one,
its length or a digest — and writes nothing. It is what the operator runs on the
host before a deploy that carries the stricter rule.

The compatibility half proves the stricter rule (`mcp_key_state` in lib.sh)
still says `ok` for every key the old rules accepted cleanly, and that the
helpers keep such a key byte for byte. The formats come from the code that
writes or reads the keys:

- the helpers: `openssl rand -base64 N | tr -d '\\r\\n/+='` (alphanumeric);
- the runbook before NEW-09: `$(openssl rand -base64 48)` straight into the
  line, so `/`, `+` and `=` padding;
- hand-set keys the process checks accept (mcp_access.E004/E005, juristid.E017,
  E019, E052 count characters only): hex, punctuation, quotes kept as
  characters, non-ASCII letters, exactly the minimum, the last line without a
  newline, among other lines.

The old rules, from main before this change: web `^KEY=.{40,}` (lib.sh
`mcp_web_key_state`); writer `^KEY=[^[:space:]]{N,}$`
(`juristid-mcp-writer-keys.sh` `key_state`).

Synthetic values only, in temporary files.
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
CHECK = ROOT / "scripts" / "deploy" / "juristid-mcp-key-check.sh"
WEB_HELPER = ROOT / "scripts" / "deploy" / "juristid-mcp-web-keys.sh"
BASH = shutil.which("bash") or "bash"

STATES = {"ok", "weak", "missing", "duplicated", "malformed"}

#: Slot -> (key, minimum), as the helpers hold them.
WEB_SLOTS = (("MCP_AUTHENTICATOR_KEY", 40), ("MCP_TOKEN_DERIVATION_KEY", 40))
WRITER_SLOTS = (
    ("DJANGO_SECRET_KEY", 50),
    ("POSTGRES_PASSWORD", 32),
    ("MCP_WRITE_GATEWAY_KEY", 40),
)
MCP_SLOTS = (("MCP_WRITE_GATEWAY_KEY", 40),)

#: Distinct, recognisable synthetic values (none is a real secret).
A = "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll2Mm3Nn4Oo5Pp6Qq7Rr8Ss9Tt0Uu1V"  # 64
B = "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp9Oo8Nn7Mm6Ll5Kk4Jj3Ii2Hh1Gg0Ff9E"  # 64
C = "Mn4Bv7Cx2Za9Lk3Jh6Gf1Ds8Ap5Oi0Uy7Tr4Ew2Qp9Lo3Ki6Ju1Hy8Gt5Fr0De3S"  # 64
D = "Qw1Er2Ty3Ui4Op5As6Df7Gh8Jk9Lz0Xc1Vb2Nm3Qa4Ws5Ed6Rf7Tg8Yh9Uj0Ik1O"  # 64
E = "Pl0Ok9Ij8Uh7Yg6Tf5Rd4Es3Wa2Qz1Xs0Cd9Vf8Bg7Nh6Mj5Ki4Lo3Pa2Sd1Fg0H"  # 64
F = "Hg5Fd4Sa3Qw2Er1Ty0Ui9Op8Lk7Jh6Gf5Ds4Az3Xc2Vb1Nm0Mq9Wn8Eb7Rv6Tc5X"  # 64
SYNTHETIC = (A, B, C, D, E, F)


def _bash(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed interpreter and fixed scripts
        [BASH, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(os.environ),
        check=False,
    )


def _check(*args: str, trace: bool = False) -> subprocess.CompletedProcess[str]:
    return _bash([*(["-x"] if trace else []), CHECK.as_posix(), *args])


def _files(tmp_path: Path, web: str, writer: str, mcp: str) -> tuple[Path, Path, Path]:
    paths = (
        tmp_path / "juristid.env",
        tmp_path / "juristid-mcp-writer.env",
        tmp_path / "juristid-mcp.env",
    )
    for path, text in zip(paths, (web, writer, mcp), strict=True):
        path.write_bytes(text.encode("utf-8"))
    return paths


def _all_ok(tmp_path: Path) -> tuple[Path, Path, Path]:
    return _files(
        tmp_path,
        f"MCP_ENABLED=1\nMCP_AUTHENTICATOR_KEY={A}\nMCP_TOKEN_DERIVATION_KEY={B}\n",
        f"POSTGRES_USER=juristid_mcp_writer\nDJANGO_SECRET_KEY={C}\n"
        f"POSTGRES_PASSWORD={D}\nMCP_WRITE_GATEWAY_KEY={E}\n",
        f"MCP_WRITES_ENABLED=1\nMCP_WRITE_GATEWAY_KEY={E}\n",
    )


def _args(web: Path, writer: Path, mcp: Path) -> list[str]:
    return [
        "--web-env-file",
        web.as_posix(),
        "--writer-env-file",
        writer.as_posix(),
        "--mcp-env-file",
        mcp.as_posix(),
    ]


def _leaks(output: str, values: tuple[str, ...]) -> list[str]:
    """Any value, or any 6-character slice of one (a prefix, suffix or middle), in the output."""
    found = []
    for value in values:
        for start in range(len(value) - 5):
            if value[start : start + 6] in output:
                found.append(value[start : start + 6])
    return found


# -- the read-only check ----------------------------------------------------------


def test_every_slot_ok_prints_six_states_and_exits_0(tmp_path):
    web, writer, mcp = _all_ok(tmp_path)
    result = _check(*_args(web, writer, mcp))
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "juristid.env MCP_AUTHENTICATOR_KEY: ok\n"
        "juristid.env MCP_TOKEN_DERIVATION_KEY: ok\n"
        "juristid-mcp-writer.env DJANGO_SECRET_KEY: ok\n"
        "juristid-mcp-writer.env POSTGRES_PASSWORD: ok\n"
        "juristid-mcp-writer.env MCP_WRITE_GATEWAY_KEY: ok\n"
        "juristid-mcp.env MCP_WRITE_GATEWAY_KEY: ok\n"
    )
    assert result.stderr == ""


def test_each_state_is_named_per_slot_and_exits_1(tmp_path):
    web, writer, mcp = _files(
        tmp_path,
        # malformed (export), weak (a real value under 40)
        f"export MCP_AUTHENTICATOR_KEY={A}\nMCP_TOKEN_DERIVATION_KEY={B[:39]}\n",
        # duplicated, missing (commented), malformed (colon)
        f"DJANGO_SECRET_KEY={C}\nDJANGO_SECRET_KEY={D}\n# POSTGRES_PASSWORD={D}\n"
        f"MCP_WRITE_GATEWAY_KEY: {E}\n",
        # weak (placeholder)
        "MCP_WRITE_GATEWAY_KEY=replace-me-with-openssl-rand-base64-48\n",
    )
    result = _check(*_args(web, writer, mcp))
    assert result.returncode == 1, result.stderr
    assert result.stdout == (
        "juristid.env MCP_AUTHENTICATOR_KEY: malformed\n"
        "juristid.env MCP_TOKEN_DERIVATION_KEY: weak\n"
        "juristid-mcp-writer.env DJANGO_SECRET_KEY: duplicated\n"
        "juristid-mcp-writer.env POSTGRES_PASSWORD: missing\n"
        "juristid-mcp-writer.env MCP_WRITE_GATEWAY_KEY: malformed\n"
        "juristid-mcp.env MCP_WRITE_GATEWAY_KEY: weak\n"
    )
    for line in result.stdout.splitlines():
        assert line.rsplit(": ", 1)[1] in STATES


def test_only_the_files_passed_are_checked(tmp_path):
    web, _, _ = _all_ok(tmp_path)
    result = _check("--web-env-file", web.as_posix())
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("\n") == 2
    assert "juristid-mcp" not in result.stdout


@pytest.mark.parametrize("trace", [False, True], ids=["plain", "xtrace"])
def test_no_value_part_or_length_ever_reaches_the_output(tmp_path, trace):
    """Not a value, not 6 characters of one, not a length: states and names only."""
    web, writer, mcp = _files(
        tmp_path,
        f"MCP_AUTHENTICATOR_KEY = {A} \nMCP_TOKEN_DERIVATION_KEY={B[:33]}\n",
        f"DJANGO_SECRET_KEY={C}\r\nPOSTGRES_PASSWORD={D}\nPOSTGRES_PASSWORD={F}\n"
        f"MCP_WRITE_GATEWAY_KEY={E}\n",
        f"MCP_WRITE_GATEWAY_KEY={E}\n",
    )
    result = _check(*_args(web, writer, mcp), trace=trace)
    assert result.returncode == 1
    printed = result.stdout + result.stderr
    if trace:
        assert "mcp_key_state" in result.stderr  # the trace really ran
    assert _leaks(printed, SYNTHETIC) == []
    # No number beyond the minimums the rule is asked for (no length, no count).
    assert set(re.findall(r"\d+", result.stdout)) == set()


def test_it_writes_nothing(tmp_path):
    web, writer, mcp = _files(
        tmp_path,
        f"MCP_AUTHENTICATOR_KEY={A} \n",
        "DJANGO_SECRET_KEY=\n",
        f"MCP_WRITE_GATEWAY_KEY={E}\nMCP_WRITE_GATEWAY_KEY={E}\n",
    )
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (web, writer, mcp)
    }
    listing = sorted(path.name for path in tmp_path.iterdir())
    result = _check(*_args(web, writer, mcp))
    assert result.returncode == 1
    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (web, writer, mcp)}
    assert after == before
    assert sorted(path.name for path in tmp_path.iterdir()) == listing
    text = CHECK.read_text(encoding="utf-8")
    for writer_call in ("write_key", "mktemp", '>"$', '> "$', "sed -i", "tee "):
        assert writer_call not in text


def test_an_unreadable_file_stops_it_with_exit_2(tmp_path):
    web, _, _ = _all_ok(tmp_path)
    absent = tmp_path / "absent.env"
    result = _check("--web-env-file", web.as_posix(), "--mcp-env-file", absent.as_posix())
    assert result.returncode == 2
    assert "cannot read" in result.stderr and "absent.env" in result.stderr
    assert "absent.env MCP_WRITE_GATEWAY_KEY" not in result.stdout


@pytest.mark.parametrize(
    "args", [[], ["--web-env-file"], ["--unknown"]], ids=["none", "no-path", "unknown"]
)
def test_bad_arguments_exit_2(args):
    result = _check(*args)
    assert result.returncode == 2
    assert result.stdout == ""


def test_lines_compose_reads_as_a_good_key_are_ok_and_other_forms_malformed(tmp_path):
    """CRLF, trailing blanks and a comment are not the key; `export`/`:`/blanks name it."""
    web, writer, mcp = _files(
        tmp_path,
        f"MCP_AUTHENTICATOR_KEY={A}\r\nMCP_TOKEN_DERIVATION_KEY={B}   # set 24.11\n",
        f"DJANGO_SECRET_KEY={C}\t\nPOSTGRES_PASSWORD = {D}\nMCP_WRITE_GATEWAY_KEY: {E}\n",
        f"  export MCP_WRITE_GATEWAY_KEY={E}\n",
    )
    result = _check(*_args(web, writer, mcp))
    assert result.returncode == 1
    assert result.stdout == (
        "juristid.env MCP_AUTHENTICATOR_KEY: ok\n"
        "juristid.env MCP_TOKEN_DERIVATION_KEY: ok\n"
        "juristid-mcp-writer.env DJANGO_SECRET_KEY: ok\n"
        "juristid-mcp-writer.env POSTGRES_PASSWORD: malformed\n"
        "juristid-mcp-writer.env MCP_WRITE_GATEWAY_KEY: malformed\n"
        "juristid-mcp.env MCP_WRITE_GATEWAY_KEY: malformed\n"
    )


def test_the_check_is_executable_in_git():
    listed = subprocess.run(
        ["git", "ls-files", "-s", "scripts/deploy/juristid-mcp-key-check.sh"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.startswith("100755"), listed or "not tracked yet"


def test_the_check_uses_the_helpers_rule_and_minimums():
    text = CHECK.read_text(encoding="utf-8")
    assert "mcp_key_state" in text
    assert "[^[:space:]]" not in text  # no private copy of the rule
    lib = LIB.read_text(encoding="utf-8")
    for line in (
        "MCP_WEB_KEY_MIN_LENGTH=40",
        "MCP_WRITER_SECRET_KEY_MIN_LENGTH=50",
        "MCP_WRITER_DB_PASSWORD_MIN_LENGTH=32",
        "MCP_GATEWAY_KEY_MIN_LENGTH=40",
    ):
        assert line in lib.splitlines()
    writer = (ROOT / "scripts" / "deploy" / "juristid-mcp-writer-keys.sh").read_text(
        encoding="utf-8"
    )
    assert 'SECRET_KEY_MIN="$MCP_WRITER_SECRET_KEY_MIN_LENGTH"' in writer
    assert 'DB_PASSWORD_MIN="$MCP_WRITER_DB_PASSWORD_MIN_LENGTH"' in writer
    assert 'GATEWAY_KEY_MIN="$MCP_GATEWAY_KEY_MIN_LENGTH"' in writer


# -- no valid key is refused --------------------------------------------------------

#: Every value form a consumer writes or accepts (see the module docstring).
VALID_VALUES = {
    "helper-web": A,  # base64 48 bytes with /+= stripped
    "helper-writer-85": (A + B)[:85],  # base64 64 bytes, stripped
    "runbook-base64-slash-plus": "q3+/Zk9Lm2Xc8Rt4Wp3Hk8Nd5Bv1Qz7Lm2Xc9Rt4Wp3Hk8Nd5Bv1Qz7Lm2X/+c9R",
    "base64-padded": "Zk9Lm2Xc8Rt4Wp3Hk8Nd5Bv1Qz7Lm2Xc9Rt4Wp3Hk8Nd5Bv1Qz7Lm2Xc9R+/w==",
    "hex-64": "0123456789abcdef" * 4,
    "punctuation": "!#$%&()*,-.:;<>?@[]^_{|}~" * 2 + "A1b2C3d4",
    "double-quoted": f'"{B[:58]}"',
    "single-quoted": f"'{C[:58]}'",
    "equals-inside": "abc=def==" * 6,
    "non-ascii": "õäöüšž" * 10,
    "exactly-50": D[:50],
}


def _old_web_ok(text: str, key: str, minimum: int) -> bool:
    """main before HR-18, lib.sh (and so main's preflight): named once, `^KEY=.{MIN,}`."""
    lines = text.split("\n")
    named = [line for line in lines if re.match(rf"^\s*{key}=", line)]
    strong = [line for line in lines if re.match(rf"^{key}=.{{{minimum},}}", line)]
    placeholder = [line for line in lines if line.startswith(f"{key}=replace-me")]
    return len(named) == 1 and len(strong) == 1 and not placeholder


def _old_writer_ok(text: str, key: str, minimum: int) -> bool:
    """main before HR-18, juristid-mcp-writer-keys.sh: `^KEY=[^[:space:]]{MIN,}$`."""
    lines = text.split("\n")
    named = [line for line in lines if re.match(rf"^\s*{key}=", line)]
    strong = [line for line in lines if re.fullmatch(rf"{key}=\S{{{minimum},}}", line)]
    placeholder = [line for line in lines if line.startswith(f"{key}=replace-me")]
    return len(named) == 1 and len(strong) == 1 and not placeholder


def _layouts(key: str, value: str) -> dict[str, str]:
    return {
        "alone": f"{key}={value}\n",
        "no-final-newline": f"OTHER=1\n{key}={value}",
        "among-others": f"# {key}=replace-me-template\nA=1\n{key}={value}\nZ= spaced value \n",
        # Main's preflight passed these too (`.` takes the CR, the blank, the
        # comment); Compose reads the key itself.
        "crlf-line": f"{key}={value}\r\n",
        "crlf-file": f"A=1\r\n{key}={value}\r\nZ=2\r\n",
        "trailing-blanks": f"{key}={value} \t \n",
        "inline-comment": f"{key}={value}  # generated 24.11\n",
        "leading-blank-in-value": f"{key}= {value}\n",
    }


#: Layouts only main's preflight (web's rule) took; the writer's old rule did not.
PREFLIGHT_ONLY = {
    "crlf-line",
    "crlf-file",
    "trailing-blanks",
    "inline-comment",
    "leading-blank-in-value",
}


def _new_states(cases: list[tuple[Path, str, int]]) -> list[str]:
    """mcp_key_state for many files in one shell (each call alone is slow on Windows).

    The calls go in a script file: a long `bash -c` would pass the Windows
    command-line limit.
    """
    script = ["set -euo pipefail", f'. "{LIB.as_posix()}"']
    for path, key, minimum in cases:
        script.append(f'mcp_key_state "{path.as_posix()}" {key} {minimum}')
    runner = cases[0][0].parent / "states.sh"
    runner.write_text("\n".join(script) + "\n", encoding="utf-8")
    result = _bash([runner.as_posix()], timeout=900)  # ~800 greps: slow on Windows
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_every_value_the_old_rules_accepted_is_still_ok(tmp_path):
    cases: list[tuple[Path, str, int]] = []
    n = 0
    for name, value in VALID_VALUES.items():
        for key, minimum in (*WEB_SLOTS, *WRITER_SLOTS):
            if len(value) < minimum:
                continue
            for layout, text in _layouts(key, value).items():
                n += 1
                path = tmp_path / f"{n}-{name}-{key}-{layout}.env"
                path.write_bytes(text.encode("utf-8"))
                # Main's preflight took each of these; the writer's old rule
                # took the plain layouts too.
                assert _old_web_ok(text, key, minimum), (name, key, layout)
                if layout not in PREFLIGHT_ONLY:
                    assert _old_writer_ok(text, key, minimum), (name, key, layout)
                cases.append((path, key, minimum))
    states = _new_states(cases)
    assert len(states) == len(cases) > 400
    refused = [
        str(case[0].name) for case, state in zip(cases, states, strict=True) if state != "ok"
    ]
    assert refused == []


def test_exactly_the_minimum_is_ok_and_one_less_is_weak_for_every_slot(tmp_path):
    cases = []
    for key, minimum in (*WEB_SLOTS, *WRITER_SLOTS):
        for length in (minimum, minimum - 1):
            path = tmp_path / f"{key}-{length}.env"
            path.write_bytes(f"{key}={'x' * length}\n".encode())
            cases.append((path, key, minimum))
    states = _new_states(cases)
    assert states == ["ok", "weak"] * (len(WEB_SLOTS) + len(WRITER_SLOTS))


def test_the_process_checks_accept_the_valid_web_values(settings):
    """What web itself takes (mcp_access.E004/E005) — so `ok` above is a real key."""
    from tests import mcp_access_support as support

    support.enable(settings)
    settings.REAL_DATA_ALLOWED = True
    settings.JURISTID_PROCESS_ROLE = ""
    for value in VALID_VALUES.values():
        settings.MCP_AUTHENTICATOR_KEY = value
        settings.MCP_TOKEN_DERIVATION_KEY = value[::-1] + "-derivation"
        problems = [e.id for e in checks.mcp_access_settings()]
        assert "mcp_access.E004" not in problems, value
        assert "mcp_access.E005" not in problems, value


@pytest.mark.parametrize("name", list(VALID_VALUES))
def test_the_web_helper_keeps_a_valid_key_byte_for_byte(tmp_path, name):
    value = VALID_VALUES[name]
    if len(value) < 40:
        pytest.skip("shorter than web's minimum")
    original = f"MCP_ENABLED=1\nMCP_AUTHENTICATOR_KEY={value}\nMCP_TOKEN_DERIVATION_KEY={A}\n"
    path = tmp_path / "juristid.env"
    path.write_bytes(original.encode("utf-8"))
    result = _bash([WEB_HELPER.as_posix(), "--env-file", path.as_posix()])
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == original.encode("utf-8")
    assert result.stdout.count("already set, kept") == 2
    assert _leaks(result.stdout + result.stderr, (A,)) == []


# -- generation: only where nothing can be running with the key --------------------


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("OTHER=1\n", "generate"),  # missing
        ("# MCP_AUTHENTICATOR_KEY=replace-me-with-openssl-rand-base64-48\n", "generate"),
        ("MCP_AUTHENTICATOR_KEY=\n", "generate"),  # empty: env() reads it as unset
        ("MCP_AUTHENTICATOR_KEY=replace-me-with-openssl-rand-base64-48\n", "generate"),
        (f"MCP_AUTHENTICATOR_KEY={A}\n", "keep"),
        (f"MCP_AUTHENTICATOR_KEY={A[:39]}\n", "refuse"),  # E004 accepts 32+
        (f"MCP_AUTHENTICATOR_KEY={A[:5]}\n", "refuse"),
        (f"MCP_AUTHENTICATOR_KEY={A}\nMCP_AUTHENTICATOR_KEY={B}\n", "refuse"),
        (f"MCP_AUTHENTICATOR_KEY={A}\nMCP_AUTHENTICATOR_KEY=\n", "refuse"),
        (f"MCP_AUTHENTICATOR_KEY={A} \n", "keep"),  # Compose reads the key itself
        (f"MCP_AUTHENTICATOR_KEY={A}\r\n", "keep"),
        (f"MCP_AUTHENTICATOR_KEY={A} # note\n", "keep"),
        (f" MCP_AUTHENTICATOR_KEY={A}\n", "refuse"),
        (f"export MCP_AUTHENTICATOR_KEY={A}\n", "refuse"),  # Compose reads it: never append
        (f"MCP_AUTHENTICATOR_KEY = {A}\n", "refuse"),
        (f"MCP_AUTHENTICATOR_KEY: {A}\n", "refuse"),
        ("export MCP_AUTHENTICATOR_KEY=\n", "refuse"),
        ("MCP_AUTHENTICATOR_KEY= \n", "generate"),  # Compose reads it as empty
        ('MCP_AUTHENTICATOR_KEY=""\n', "generate"),
        ("MCP_AUTHENTICATOR_KEY=''\n", "generate"),
        ("MCP_AUTHENTICATOR_KEY= # nothing yet\n", "generate"),
    ],
    ids=[
        "missing",
        "commented-template",
        "empty",
        "placeholder",
        "ok",
        "short-39",
        "short-5",
        "duplicated",
        "duplicated-one-empty",
        "trailing-space",
        "crlf",
        "inline-comment",
        "indented",
        "export",
        "blanks-around-equals",
        "colon",
        "export-empty",
        "only-a-space",
        "double-quoted-empty",
        "single-quoted-empty",
        "only-a-comment",
    ],
)
def test_the_helpers_act_only_where_nothing_can_be_running(tmp_path, text, action):
    path = tmp_path / "juristid.env"
    path.write_bytes(text.encode("utf-8"))
    result = _bash(
        [
            "-c",
            f'set -euo pipefail\n. "{LIB.as_posix()}"\n'
            f'mcp_key_action "{path.as_posix()}" MCP_AUTHENTICATOR_KEY 40',
        ]
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{action}\n"
    assert _leaks(result.stdout + result.stderr, (A, B)) == []


def test_the_runbooks_check_command_runs_as_written(tmp_path):
    text = (ROOT / "docs" / "mcp" / "OPERATIONS.md").read_text(encoding="utf-8")
    [command] = re.findall(r"(?m)^scripts/deploy/juristid-mcp-key-check\.sh .*$", text)
    web, writer, mcp = _all_ok(tmp_path)
    config = "/mnt/user/appdata/juristid-main/config/"
    for path in (web, writer, mcp):
        command = command.replace(config + path.name, path.as_posix())
    assert config not in command
    result = _bash(["-c", command])
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(": ok\n") == 6
