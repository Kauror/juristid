"""Do the database and the tunnel receive nothing of the application's secrets?

ADR 0179, Codex finding csf_14aa9cd147aa48f44b141d39. The `db` service used to
load the application's whole environment file — the Django signing key, the
shared-gate password, the mail and MFA settings — although PostgreSQL reads
none of them. It now receives exactly `POSTGRES_DB`, `POSTGRES_USER` and
`POSTGRES_PASSWORD`, and the tunnel receives nothing at all.

Run against `docker compose config --format json` for
`deploy/unraid-main/compose.yml` from the compose smoke job in CI, where Compose
itself resolves `env_file:` and `${VAR}` interpolation; and imported by
`tests/test_service_secret_isolation.py`, which feeds it the same structure from
a parsed file. Two shapes of invocation, both with a synthetic environment file
whose every value is a unique sentinel:

    assert_service_env_isolation.py routine.json sentinels.env
        the day-to-day shape: no `--env-file`, so the database's
        `POSTGRES_PASSWORD` resolves empty (the cluster already holds it).

    assert_service_env_isolation.py initialising.json sentinels.env --initialising
        the disaster-recovery shape: `--env-file sentinels.env` feeds
        interpolation, so the database's password is the file's — and still no
        other value of the file reaches it.

Either way `web` must hold the file's `DJANGO_SECRET_KEY` sentinel: that proves
the sentinel file was actually read, so an absence elsewhere means something.

    assert_service_env_isolation.py --write-sentinels sentinels.env
        writes the synthetic file: every key `deploy/unraid-main/.env.example`
        names, commented-out dormant ones included, each with a value that
        occurs nowhere else.

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

ALLOWED = {"db": DB_ALLOWED, "tunnel": TUNNEL_ALLOWED}

#: A key the positive control looks for in `web`.
CONTROL_KEY = "DJANGO_SECRET_KEY"

_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = _ROOT / "deploy" / "unraid-main" / ".env.example"

#: `KEY=value` and `# KEY=value` — the template names its dormant keys commented out.
_TEMPLATE_KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def template_keys(path: Path = ENV_EXAMPLE) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _TEMPLATE_KEY.match(line.strip())
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    return keys


def sentinel_values(keys: list[str]) -> dict[str, str]:
    """One value per key, unique and not a substring of any other."""
    return {key: f"sentinel-{index:03d}-{key.lower()}-x" for index, key in enumerate(keys)}


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


def violations(
    services: dict[str, Any], sentinels: dict[str, str], *, initialising: bool
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
        rendered = json.dumps(service, sort_keys=True)
        for key, value in sentinels.items():
            if key in allowed:
                continue
            if value in rendered:
                found.append(f"{name}: the value of {key} reaches it")

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
    return found


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--write-sentinels":
        values = sentinel_values(template_keys())
        Path(argv[1]).write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
        )
        print(f"wrote {len(values)} synthetic keys")
        return 0
    initialising = "--initialising" in argv
    paths = [arg for arg in argv if arg != "--initialising"]
    if len(paths) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    services = json.loads(Path(paths[0]).read_text(encoding="utf-8"))["services"]
    sentinels = read_env_file(Path(paths[1]))
    found = violations(services, sentinels, initialising=initialising)
    for line in found:
        print(f"::error::{line}")
    if found:
        return 1
    shape = "initialising" if initialising else "routine"
    print(
        f"{shape}: db receives only {', '.join(sorted(_environment(services['db'])))}; "
        f"tunnel receives nothing; {len(sentinels)} synthetic values checked"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
