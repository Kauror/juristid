"""Does every service receive only the secrets it needs?

ADR 0179, Codex finding csf_14aa9cd147aa48f44b141d39. The `db` service used to
load the application's whole environment file — the Django signing key, the
shared-gate password, the mail and MFA settings — although PostgreSQL reads
none of them. It now receives exactly `POSTGRES_DB`, `POSTGRES_USER` and
`POSTGRES_PASSWORD`, and the tunnel receives nothing at all.

ADR 0185 added two services with environments of their own, held to the same
rule. `mcp`, the read-only MCP server, reads `juristid-mcp.env` and never
`juristid.env`: it is given no gate password, no MFA key and no mail
credential, and nothing of `juristid.env` reaches it. `mcp-tunnel`, OpenAI's
tunnel client, reads `juristid-mcp-tunnel.env` — its runtime key, its tunnel id
and the origin it may trust — and nothing of either application file. And no
other service receives a value from either of the two new files.

ADR 0189 added the write gateway, `mcp-writer`, reading
`juristid-mcp-writer.env`: its own signing key and database role, the gateway
key it shares with `mcp`, and nothing of `juristid.env`, `juristid-mcp.env` or
`juristid-mcp-tunnel.env` — so no gate password, no MFA, authenticator or
derivation key, no mail credential, and not `mcp`'s database password. `mcp`
in turn receives nothing of the writer's file (its key is the same *setting*,
generated into both files; the sentinel files keep the two values distinct, so
a value that crosses is still seen), and no other service anything of it.

Run against `docker compose --profile mcp config --format json` for
`deploy/unraid-main/compose.yml` from the compose smoke job in CI, where Compose
itself resolves `env_file:` and `${VAR}` interpolation; and imported by
`tests/test_service_secret_isolation.py`, which feeds it the same structure from
a parsed file. Two shapes of invocation, both with synthetic environment files
whose every value is a unique sentinel:

    assert_service_env_isolation.py [--mcp M --mcp-tunnel T] routine.json sentinels.env
        the day-to-day shape: no `--env-file`, so the database's
        `POSTGRES_PASSWORD` resolves empty (the cluster already holds it).

    assert_service_env_isolation.py [--mcp M --mcp-tunnel T] initialising.json sentinels.env \
        --initialising
        the disaster-recovery shape: `--env-file sentinels.env` feeds
        interpolation, so the database's password is the file's — and still no
        other value of the file reaches it.

Either way `web` must hold the main file's `DJANGO_SECRET_KEY` sentinel, `mcp`
its own file's and `mcp-tunnel` its own tunnel id: that proves each sentinel
file was actually read, so an absence elsewhere means something. `--mcp` and
`--mcp-tunnel` go together, and are required exactly when the resolved model
holds the two services — a resolution without `--profile mcp` leaves them out,
and the check says so rather than passing over them.

    assert_service_env_isolation.py --write-sentinels sentinels.env \
        [mcp.env mcp-tunnel.env [mcp-writer.env]]
        writes the synthetic files: every key `deploy/unraid-main/.env.example`
        (and, with three paths, `juristid-mcp.env.example` and
        `juristid-mcp-tunnel.env.example`) names, commented-out dormant ones
        included, each with a value that occurs nowhere else.

Only key and service names are ever printed — never a value — so the same
script is safe to point at a real resolution, though CI never does.
No Docker and no network of its own: it reads files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

#: Everything the official PostgreSQL image is given. The image reads more
#: (`POSTGRES_INITDB_ARGS`, `PGDATA`, …) but this deployment sets none of them.
DB_ALLOWED = frozenset({"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"})

#: The tunnel is locally managed: its credential is a file in its own volume.
TUNNEL_ALLOWED: frozenset[str] = frozenset()

#: The services that take no environment file at all.
ALLOWED = {"db": DB_ALLOWED, "tunnel": TUNNEL_ALLOWED}

#: What the MCP server may hold (ADR 0185 §6), listed rather than derived from
#: its template, so widening it is an edit somebody reviews here. Two sources:
#:
#: * set on the service by compose.yml — where the database is, the process
#:   role that makes its connections read-only, where it listens;
#: * read from `juristid-mcp.env` — the real-data flag and the deployment's
#:   authentication mode (a name, not a secret: real data with no mode is
#:   refused, E006, and no gate password comes with it), its own signing key,
#:   the database credentials (the same role: read-only transactions are the
#:   lock, ADR 0185 §1), the MCP settings and the log level.
#:
#: Not `DJANGO_DEBUG` or `DEV_LOGIN_ENABLED`: off by default and never named
#: for this process (a debug MCP process is refused, E044).
MCP_ALLOWED = frozenset(
    {
        # compose.yml
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_SSLMODE",
        "JURISTID_PROCESS_ROLE",
        "JURISTID_RUNTIME",
        "MCP_LISTEN_HOST",
        "MCP_LISTEN_PORT",
        "SEED_DEV_DATA",
        # juristid-mcp.env
        "REAL_DATA_ALLOWED",
        "AUTH_MODE",
        "DJANGO_SECRET_KEY",
        "DJANGO_LOG_LEVEL",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "MCP_ENABLED",
        "JURISTID_PUBLIC_ORIGIN",
        "MCP_RESOURCE_URLS",
        "MCP_ALLOWED_HOSTS",
        "MCP_ALLOWED_ORIGINS",
        "MCP_MAX_CONCURRENT_CALLS",
        "MCP_CALL_TIMEOUT_SECONDS",
        "MCP_FETCH_MAX_CHARS",
        "MCP_MAX_REQUEST_BYTES",
        # ADR 0189: the write tools forward to the gateway. The URL is set by
        # compose.yml; the switch and the key are in juristid-mcp.env. The
        # writer's database credentials are not (juristid.E058).
        "MCP_WRITES_ENABLED",
        "MCP_WRITE_GATEWAY_URL",
        "MCP_WRITE_GATEWAY_KEY",
    }
)

#: What the write gateway may hold (ADR 0189), listed for the same reason:
#:
#: * set on the service by compose.yml — the database, its process role, where
#:   it listens;
#: * read from `juristid-mcp-writer.env` — the real-data flag and the mode's
#:   name, its own signing key, its own database role and password, the three
#:   values token verification needs (MCP_ENABLED, the origin, the audiences),
#:   the write switch, the gateway key and the write limits.
MCP_WRITER_ALLOWED = frozenset(
    {
        # compose.yml
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_SSLMODE",
        "JURISTID_PROCESS_ROLE",
        "JURISTID_RUNTIME",
        "MCP_WRITE_LISTEN_HOST",
        "MCP_WRITE_LISTEN_PORT",
        "SEED_DEV_DATA",
        # juristid-mcp-writer.env
        "REAL_DATA_ALLOWED",
        "AUTH_MODE",
        "DJANGO_SECRET_KEY",
        "DJANGO_LOG_LEVEL",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "MCP_ENABLED",
        "JURISTID_PUBLIC_ORIGIN",
        "MCP_RESOURCE_URLS",
        "MCP_WRITES_ENABLED",
        "MCP_WRITE_GATEWAY_KEY",
        "MCP_WRITE_MAX_CONCURRENT_CALLS",
        "MCP_WRITE_CALL_TIMEOUT_SECONDS",
        "MCP_WRITE_MAX_REQUEST_BYTES",
        "MCP_WRITE_PROPOSAL_SECONDS",
        "MCP_WRITE_APPROVAL_SECONDS",
        "MCP_WRITE_MAX_TEXT_CHARS",
    }
)

#: The browser's secrets and settings, which the MCP server must never hold
#: whatever its template is later edited to say: the shared gate, personal
#: sign-in's MFA key, account mail, Cloudflare Access. Named separately from
#: the allowlist so a violation says *which* rule it broke.
BROWSER_ONLY = frozenset(
    {
        "JURISTID_SHARED_GATE_PASSWORD",
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
        "MCP_AUTHENTICATOR_KEY",
        "MCP_TOKEN_DERIVATION_KEY",
        "DJANGO_EMAIL_HOST",
        "DJANGO_EMAIL_HOST_USER",
        "DJANGO_EMAIL_HOST_PASSWORD",
        "DJANGO_EMAIL_BACKEND",
        "ACCOUNT_EMAIL_DELIVERY_ENABLED",
        "ACCOUNT_EMAIL_FROM",
        "ACCOUNT_LINK_BASE_URL",
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUDIENCE",
    }
)

#: What OpenAI's tunnel client may hold: how it reaches `mcp` and reports its
#: health (compose.yml), and its runtime key, tunnel id and the one origin its
#: OAuth discovery may follow (`juristid-mcp-tunnel.env`). Nothing of Juristid.
MCP_TUNNEL_ALLOWED = frozenset(
    {
        # compose.yml
        "MCP_SERVER_URL",
        "HARPOON_ALLOW_PLAINTEXT_HTTP",
        "HEALTH_LISTEN_ADDR",
        "LOG_FORMAT",
        "LOG_LEVEL",
        # juristid-mcp-tunnel.env
        "CONTROL_PLANE_API_KEY",
        "CONTROL_PLANE_TUNNEL_ID",
        "MCP_OAUTH_TRUSTED_ORIGINS",
    }
)

#: The keys the positive controls look for.
CONTROL_KEY = "DJANGO_SECRET_KEY"
TUNNEL_CONTROL_KEY = "CONTROL_PLANE_TUNNEL_ID"

_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = _ROOT / "deploy" / "unraid-main" / ".env.example"
MCP_ENV_EXAMPLE = _ROOT / "deploy" / "unraid-main" / "juristid-mcp.env.example"
MCP_TUNNEL_ENV_EXAMPLE = _ROOT / "deploy" / "unraid-main" / "juristid-mcp-tunnel.env.example"
MCP_WRITER_ENV_EXAMPLE = _ROOT / "deploy" / "unraid-main" / "juristid-mcp-writer.env.example"

#: `KEY=value` and `# KEY=value` — the template names its dormant keys commented out.
_TEMPLATE_KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def template_keys(path: Path = ENV_EXAMPLE) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _TEMPLATE_KEY.match(line.strip())
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    return keys


def sentinel_values(keys: list[str], tag: str = "") -> dict[str, str]:
    """One value per key, unique and not a substring of any other.

    `tag` keeps the four files apart: `sentinel-000-…` and `sentinel-m000-…`
    differ at the character after the first dash, so neither is inside the
    other and a value found in a service names the file it came from.
    """
    return {key: f"sentinel-{tag}{index:03d}-{key.lower()}-x" for index, key in enumerate(keys)}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _environment(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment") or {}
    if isinstance(environment, list):
        pairs = (item.partition("=") for item in environment)
        return {key: value for key, _, value in pairs}
    return {key: "" if value is None else str(value) for key, value in environment.items()}


def _env_file_paths(service: dict[str, Any]) -> list[str]:
    """`env_file` as Compose prints it: strings, or `{path, required}` entries."""
    entries = service.get("env_file") or []
    if isinstance(entries, (str, dict)):
        entries = [entries]
    return [str(entry["path"] if isinstance(entry, dict) else entry) for entry in entries]


def _leaks(
    name: str,
    service: dict[str, Any],
    values: dict[str, str],
    allowed: frozenset[str] = frozenset(),
    source: str | None = None,
) -> list[str]:
    """Every sentinel value of one file found anywhere in a service's definition.

    `source` names the file in the message; the application's own file, the
    one every rule here started from, is left unnamed as it always was.
    """
    rendered = json.dumps(service, sort_keys=True)
    origin = f" from {source}" if source else ""
    return [
        f"{name}: the value of {key}{origin} reaches it"
        for key, value in values.items()
        if key not in allowed and value in rendered
    ]


def _own_file_problems(
    name: str,
    service: dict[str, Any],
    allowed: frozenset[str],
    own: dict[str, str],
    control: str,
    foreign: dict[str, dict[str, str]],
    main_file: str | None,
) -> list[str]:
    """A service with an environment file of its own, and only that file.

    `docker compose config` resolves `env_file` into `environment` and drops
    the key, so what is checked is the result: the allowlist, no value of any
    other file, and its own file's sentinel present. Where an `env_file` entry
    does survive (an emulation, an older Compose), naming the application's
    file is a violation by itself.
    """
    found: list[str] = []
    application_files = {"juristid.env"} | ({Path(main_file).name} if main_file else set())
    for path in _env_file_paths(service):
        if Path(path).name in application_files:
            found.append(f"{name}: reads the application's environment file")
    environment = _environment(service)
    for key in sorted(set(environment) & BROWSER_ONLY):
        found.append(f"{name}: receives {key}, a secret of the browser's that it must never hold")
    for key in sorted(set(environment) - allowed - BROWSER_ONLY):
        found.append(f"{name}: receives {key}, which is not on its allowlist")
    for source, values in foreign.items():
        found.extend(_leaks(name, service, values, source=source))
    if control in own and environment.get(control) != own[control]:
        found.append(
            f"{name}: does not hold the sentinel {control} of its own file, so that file was "
            "not read and every absence above proves nothing"
        )
    return found


def violations(
    services: dict[str, Any],
    sentinels: dict[str, str],
    *,
    initialising: bool,
    mcp_sentinels: dict[str, str] | None = None,
    mcp_tunnel_sentinels: dict[str, str] | None = None,
    main_file: str | None = None,
    mcp_writer_sentinels: dict[str, str] | None = None,
) -> list[str]:
    """Every way the resolution breaks the rule, by key and service name only."""
    found: list[str] = []
    for name, allowed in ALLOWED.items():
        if name not in services:
            found.append(f"{name}: not in the resolved model — is this the production file?")
            continue
        service = services[name]
        if service.get("env_file"):
            found.append(f"{name}: has an env_file, so it receives a whole file it was not given")
        environment = _environment(service)
        for key in sorted(set(environment) - allowed):
            found.append(f"{name}: receives {key}, which is not on its allowlist")
        found.extend(_leaks(name, service, sentinels, allowed))
        for source, values in (
            ("juristid-mcp.env", mcp_sentinels or {}),
            ("juristid-mcp-tunnel.env", mcp_tunnel_sentinels or {}),
            ("juristid-mcp-writer.env", mcp_writer_sentinels or {}),
        ):
            found.extend(_leaks(name, service, values, source=source))

    database = _environment(services.get("db", {}))
    password = database.get("POSTGRES_PASSWORD")
    expected = sentinels.get("POSTGRES_PASSWORD", "") if initialising else ""
    if password != expected:
        shape = "the environment file's" if initialising else "empty"
        found.append(f"db: POSTGRES_PASSWORD is not {shape} in this shape of invocation")

    web = _environment(services.get("web", {}))
    if CONTROL_KEY in sentinels and web.get(CONTROL_KEY) != sentinels[CONTROL_KEY]:
        found.append(
            f"web: does not hold the sentinel {CONTROL_KEY}, so the sentinel file was "
            "not read and every absence above proves nothing"
        )

    found.extend(
        _mcp_violations(
            services,
            sentinels,
            mcp_sentinels,
            mcp_tunnel_sentinels,
            main_file,
            mcp_writer_sentinels,
        )
    )
    found.extend(
        _mcp_writer_violations(
            services,
            sentinels,
            mcp_sentinels,
            mcp_tunnel_sentinels,
            mcp_writer_sentinels,
            main_file,
        )
    )
    return found


def _mcp_violations(
    services: dict[str, Any],
    sentinels: dict[str, str],
    mcp_sentinels: dict[str, str] | None,
    mcp_tunnel_sentinels: dict[str, str] | None,
    main_file: str | None,
    mcp_writer_sentinels: dict[str, str] | None = None,
) -> list[str]:
    """ADR 0185's two services, and the two new files reaching nobody else."""
    found: list[str] = []
    present = {"mcp", "mcp-tunnel"} & set(services)
    if (mcp_sentinels is None) != (mcp_tunnel_sentinels is None):
        return ["--mcp and --mcp-tunnel go together"]
    if mcp_sentinels is None or mcp_tunnel_sentinels is None:
        if present:
            return [
                f"{', '.join(sorted(present))}: in the resolved model, but no sentinel files "
                "were given for them (--mcp, --mcp-tunnel), so nothing about them was checked"
            ]
        return []
    for name in ("mcp", "mcp-tunnel"):
        if name not in services:
            found.append(
                f"{name}: not in the resolved model — resolve with `--profile mcp`, or the "
                "check passes over the very services it was given files for"
            )
    if "mcp" in services:
        found.extend(
            _own_file_problems(
                "mcp",
                services["mcp"],
                MCP_ALLOWED,
                mcp_sentinels,
                CONTROL_KEY,
                {
                    "the environment file": sentinels,
                    "juristid-mcp-tunnel.env": mcp_tunnel_sentinels,
                    "juristid-mcp-writer.env": mcp_writer_sentinels or {},
                },
                main_file,
            )
        )
    if "mcp-tunnel" in services:
        found.extend(
            _own_file_problems(
                "mcp-tunnel",
                services["mcp-tunnel"],
                MCP_TUNNEL_ALLOWED,
                mcp_tunnel_sentinels,
                TUNNEL_CONTROL_KEY,
                {"the environment file": sentinels, "juristid-mcp.env": mcp_sentinels},
                main_file,
            )
        )
    for name, service in sorted(services.items()):
        if name in ("mcp", "mcp-tunnel", "mcp-writer", *ALLOWED):
            continue
        found.extend(_leaks(name, service, mcp_sentinels, source="juristid-mcp.env"))
        found.extend(_leaks(name, service, mcp_tunnel_sentinels, source="juristid-mcp-tunnel.env"))
    return found


def _mcp_writer_violations(
    services: dict[str, Any],
    sentinels: dict[str, str],
    mcp_sentinels: dict[str, str] | None,
    mcp_tunnel_sentinels: dict[str, str] | None,
    mcp_writer_sentinels: dict[str, str] | None,
    main_file: str | None,
) -> list[str]:
    """ADR 0189's write gateway: its own file only, and its file reaching nobody else."""
    present = "mcp-writer" in services
    if mcp_writer_sentinels is None:
        if present:
            return [
                "mcp-writer: in the resolved model, but no sentinel file was given for it "
                "(--mcp-writer), so nothing about it was checked"
            ]
        return []
    if not present:
        return [
            "mcp-writer: not in the resolved model — resolve with `--profile mcp`, or the "
            "check passes over the very service it was given a file for"
        ]
    found = _own_file_problems(
        "mcp-writer",
        services["mcp-writer"],
        MCP_WRITER_ALLOWED,
        mcp_writer_sentinels,
        CONTROL_KEY,
        {
            "the environment file": sentinels,
            "juristid-mcp.env": mcp_sentinels or {},
            "juristid-mcp-tunnel.env": mcp_tunnel_sentinels or {},
        },
        main_file,
    )
    for path in _env_file_paths(services["mcp-writer"]):
        if Path(path).name in ("juristid-mcp.env", "juristid-mcp-tunnel.env"):
            found.append(f"mcp-writer: reads {Path(path).name}")
    for name, service in sorted(services.items()):
        if name == "mcp-writer" or name in ALLOWED:
            continue
        found.extend(_leaks(name, service, mcp_writer_sentinels, source="juristid-mcp-writer.env"))
    return found


def _write_sentinels(paths: list[str]) -> None:
    templates = [
        (ENV_EXAMPLE, ""),
        (MCP_ENV_EXAMPLE, "m"),
        (MCP_TUNNEL_ENV_EXAMPLE, "t"),
        (MCP_WRITER_ENV_EXAMPLE, "w"),
    ]
    for path, (template, tag) in zip(paths, templates, strict=False):
        values = sentinel_values(template_keys(template), tag)
        Path(path).write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
        )
        print(f"wrote {len(values)} synthetic keys for {template.name}")


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--write-sentinels" and len(argv) in (2, 4, 5):
        _write_sentinels(argv[1:])
        return 0
    arguments = list(argv)
    initialising = "--initialising" in arguments
    if initialising:
        arguments.remove("--initialising")
    options: dict[str, str] = {}
    for flag in ("--mcp", "--mcp-tunnel", "--mcp-writer"):
        if flag in arguments:
            index = arguments.index(flag)
            if index + 1 >= len(arguments):
                print(__doc__, file=sys.stderr)
                return 2
            options[flag] = arguments[index + 1]
            del arguments[index : index + 2]
    if len(arguments) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    services = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))["services"]
    sentinels = read_env_file(Path(arguments[1]))
    mcp = read_env_file(Path(options["--mcp"])) if "--mcp" in options else None
    tunnel = read_env_file(Path(options["--mcp-tunnel"])) if "--mcp-tunnel" in options else None
    writer = read_env_file(Path(options["--mcp-writer"])) if "--mcp-writer" in options else None
    found = violations(
        services,
        sentinels,
        initialising=initialising,
        mcp_sentinels=mcp,
        mcp_tunnel_sentinels=tunnel,
        main_file=arguments[1],
        mcp_writer_sentinels=writer,
    )
    for line in found:
        print(f"::error::{line}")
    if found:
        return 1
    shape = "initialising" if initialising else "routine"
    extra = "; mcp and mcp-tunnel read only their own files" if mcp is not None else ""
    if writer is not None:
        extra += "; mcp-writer reads only its own file"
    print(
        f"{shape}: db receives only {', '.join(sorted(_environment(services['db'])))}; "
        f"tunnel receives nothing{extra}; {len(sentinels)} synthetic values checked"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
