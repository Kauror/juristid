"""The production stack's database address is written once (audit 2026-10-28, F-17).

`deploy/unraid-main/compose.yml` used to repeat the same three `POSTGRES_*`
lines in every application service. They are one `x-db-env` anchor now, merged
with `<<: *db-env`. What a service receives is the *resolved* mapping, so that
is what these hold: every application service resolves exactly the address it
had, and the three lines are not written out again beside the anchor.

The CI compose smoke job additionally resolves the file with Docker Compose
itself (`docker compose --profile mcp config`) and runs
`scripts/ci/assert_service_env_isolation.py` over the result — the check that a
merge key behaves the same under Compose's YAML loader as under PyYAML's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "unraid-main" / "compose.yml"

#: The address the application containers reach PostgreSQL at.
DB_ENV = {"POSTGRES_HOST": "db", "POSTGRES_PORT": "5432", "POSTGRES_SSLMODE": "disable"}

#: Every service that talks to the database through Django, and so needs it.
APPLICATION_SERVICES = (
    "web",
    "intake-reader",
    "searchindex",
    "intake-sweeper",
    "notifications",
    "mcp",
    "mcp-writer",
)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_the_anchor_is_the_database_address(compose) -> None:
    assert compose["x-db-env"] == DB_ENV


@pytest.mark.parametrize("service", APPLICATION_SERVICES)
def test_every_application_service_resolves_the_address(compose, service) -> None:
    environment = compose["services"][service]["environment"]
    assert {key: environment[key] for key in DB_ENV} == DB_ENV


def test_no_other_service_receives_it(compose) -> None:
    for name, service in compose["services"].items():
        if name in APPLICATION_SERVICES:
            continue
        environment = service.get("environment") or {}
        assert not set(DB_ENV) & set(environment), name


def test_the_address_is_written_once() -> None:
    lines = [
        line.strip()
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert lines.count("POSTGRES_HOST: db") == 1
    assert lines.count("<<: *db-env") == len(APPLICATION_SERVICES)
