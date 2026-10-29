"""Security review 2026-10-29, assessor D — finding D-01 (P2, CONFIRMED).

The production Compose file hardened the three `mcp`-profile services
(`read_only`, `no-new-privileges`, `cap_drop: ALL`, `mem_limit`, `pids_limit`)
and left every always-on service without any of it. The coordinator's
read-only `docker inspect` on the host on 2026-10-29 confirmed what the file
said: `CapDrop=[]`, no `no-new-privileges`, no PidsLimit on `web`.

`web` parses every upload and serves every request, `intake-reader` runs
Tesseract on member files, and all of them share one host with `db`. A runaway
or exploited process kept Docker's default capability set (CHOWN, SETUID,
NET_RAW, …), could gain privileges through a setuid binary, and could take the
host's process table with it. The `mcp` services showed the same image already
ran without these.

What landed with the review: the five application services refuse new
privileges, drop every capability and carry a PID limit. What is deliberately
left to the owner (docs/open-decisions.md, the review report §8): a memory
limit per service (a sizing decision against the host's RAM), a read-only root
with a tmpfs `/tmp` for `web` (rehearse first — gunicorn and the upload path
write there), and the same for `db` and `tunnel`, whose recreation is the
announced maintenance step of docs/adr/0179.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "unraid-main" / "compose.yml"

#: Every application service that `docker compose up -d` starts without a
#: profile. `db` and `tunnel` run third-party images and are the owner's step.
APPLICATION = (
    "web",
    "intake-reader",
    "searchindex",
    "intake-sweeper",
    "notifications",
)


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    found = compose["services"]
    assert set(APPLICATION) <= set(found), sorted(found)
    for name in APPLICATION:
        assert not found[name].get("profiles"), f"{name} is expected to be always-on"
    return found


@pytest.mark.parametrize("name", APPLICATION)
def test_every_application_service_refuses_new_privileges(
    services: dict[str, Any], name: str
) -> None:
    assert services[name].get("security_opt") == ["no-new-privileges:true"], (
        f"{name}: a setuid binary inside the container can still raise privileges"
    )


@pytest.mark.parametrize("name", APPLICATION)
def test_every_application_service_drops_every_capability(
    services: dict[str, Any], name: str
) -> None:
    service = services[name]
    assert service.get("cap_drop") == ["ALL"], f"{name}: keeps Docker's default capability set"
    assert not service.get("cap_add"), f"{name}: adds a capability back"


@pytest.mark.parametrize("name", APPLICATION)
def test_every_application_service_bounds_its_process_table(
    services: dict[str, Any], name: str
) -> None:
    assert 0 < int(services[name].get("pids_limit") or 0) <= 256, (
        f"{name}: a fork bomb or a runaway worker can exhaust the host's PID table"
    )


def test_the_mcp_services_keep_the_fuller_shape(services: dict[str, Any]) -> None:
    """The review added to the application services; it took nothing from the MCP ones."""
    for name in ("mcp", "mcp-writer", "mcp-tunnel"):
        service = services[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["mem_limit"]
