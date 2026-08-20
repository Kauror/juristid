"""The real-data stack is configuration, so it is checked like code.

Same shape as `tests/test_deployment_unraid.py`, different question. The
rehearsal's tests ask whether a synthetic instance stays isolated. These ask
whether the one environment that holds the Chamber's actual register can be
reached by anybody who has not been authenticated, and whether it can be started
in a configuration that makes that possible.

The two assertions that carry the most weight are the ones about ports: this
stack publishes none, so Cloudflare Access cannot be walked around by typing the
server's LAN address (Stage-2D brief 58).

No Docker and no network: the file is parsed as YAML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings

DEPLOY = Path(settings.BASE_DIR) / "deploy" / "unraid-main"
COMPOSE = DEPLOY / "compose.yml"
ENV_EXAMPLE = DEPLOY / ".env.example"

REHEARSAL = Path(settings.BASE_DIR) / "deploy" / "unraid-test" / "compose.yml"

#: Appdata subtrees belonging to other services on the same host — including the
#: rehearsal, which must survive this deployment untouched (Stage-2D brief 51).
FOREIGN_APPDATA = (
    "/mnt/user/appdata/juristid-test",
    "/mnt/user/appdata/immich",
    "/mnt/user/appdata/PostgreSQL_Immich",
    "/mnt/user/appdata/Plex-Media-Server",
    "/mnt/user/appdata/dashkoda",
    "/mnt/user/appdata/koda",
    "/mnt/user/appdata/beszel",
    "/mnt/user/appdata/hermes",
)

OWN_PREFIXES = ("/mnt/user/appdata/juristid-main", "/mnt/user/juristid-main")


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rehearsal() -> dict[str, Any]:
    return yaml.safe_load(REHEARSAL.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_the_deployment_package_exists() -> None:
    assert COMPOSE.exists()
    assert ENV_EXAMPLE.exists()
    assert (DEPLOY / "README.md").exists()


# -- the only way in -------------------------------------------------------


def test_nothing_publishes_a_host_port(compose: dict[str, Any]) -> None:
    """The assertion this whole environment rests on.

    One published port and Cloudflare Access becomes advisory: anybody on the
    LAN types the server's address and is inside (Stage-2D brief 58).
    """
    publishing = [name for name, service in compose["services"].items() if service.get("ports")]
    assert publishing == []


def test_the_stack_does_not_use_host_networking(compose: dict[str, Any]) -> None:
    """`network_mode: host` publishes every listening socket, silently."""
    for service in compose["services"].values():
        assert service.get("network_mode") != "host"


def test_the_tunnel_is_not_optional(compose: dict[str, Any]) -> None:
    """With no host port, a profile would make the only route in look optional."""
    assert "profiles" not in compose["services"]["tunnel"]


def test_the_tunnel_carries_no_credential(compose: dict[str, Any]) -> None:
    """A tunnel token is a bearer credential: whoever reads it can run it."""
    rendered = str(compose["services"]["tunnel"])
    assert "--token" not in rendered
    assert "eyJ" not in rendered, "that looks like a base64 tunnel token"


# -- isolation from everything else on the host ----------------------------


def test_the_project_name_is_its_own(compose: dict[str, Any]) -> None:
    assert compose["name"] == "juristid-main"


def test_it_shares_nothing_with_the_rehearsal(
    compose: dict[str, Any], rehearsal: dict[str, Any]
) -> None:
    """`juristid-test` must survive this deployment, running and untouched."""
    assert compose["name"] != rehearsal["name"]
    assert compose["networks"]["internal"]["name"] != rehearsal["networks"]["internal"]["name"]

    mine = {service["container_name"] for service in compose["services"].values()}
    theirs = {service["container_name"] for service in rehearsal["services"].values()}
    assert mine.isdisjoint(theirs)


def test_the_stack_uses_its_own_network_and_joins_no_other(compose: dict[str, Any]) -> None:
    networks = compose["networks"]
    assert list(networks) == ["internal"]
    assert networks["internal"]["name"] == "juristid-main-internal"
    assert networks["internal"].get("external") is not True
    for service in compose["services"].values():
        assert service["networks"] == ["internal"]


#: `${NAME:-default}`. The default has to be substituted *before* the volume
#: string is split on ":", or the colon inside the substitution splits it.
_SUBSTITUTION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


def _resolved(volume: str) -> str:
    return _SUBSTITUTION.sub(lambda match: match.group(1), volume)


def test_every_bind_mount_stays_inside_the_juristid_main_subtree(
    compose: dict[str, Any],
) -> None:
    seen = 0
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            host_path = _resolved(volume).split(":", 1)[0]
            seen += 1
            assert host_path.startswith(OWN_PREFIXES), f"{name}: {volume}"
            for foreign in FOREIGN_APPDATA:
                assert not host_path.startswith(foreign), f"{name} reaches into {foreign}"
    # Guards the guard: a parser bug producing no paths would pass silently.
    # Ten: postgres, cloudflared, and four mounts on each of the two
    # application containers (evidence, derivatives, legacy-source, source).
    assert seen == 10


def test_the_historical_source_is_mounted_read_only(compose: dict[str, Any]) -> None:
    """The importer reads its source and never rewrites it (Stage-2D brief 54).

    An importer that can write to its own source material is one bad run away
    from having nothing left to re-run against.
    """
    for name in ("web", "extractor"):
        mounts = [_resolved(volume) for volume in compose["services"][name]["volumes"]]
        historical = [m for m in mounts if m.endswith(":ro")]
        assert len(historical) == 1, f"{name}: expected exactly one read-only mount"
        assert "/srv/historical-source:ro" in historical[0]


def test_evidence_and_derivatives_stay_separate_directories(compose: dict[str, Any]) -> None:
    """One must be backed up; the other may be deleted and rebuilt from it."""
    mounts = [_resolved(volume) for volume in compose["services"]["web"]["volumes"]]
    evidence = next(m for m in mounts if m.endswith(":/app/evidence"))
    derivatives = next(m for m in mounts if m.endswith(":/app/derivatives"))
    assert not derivatives.startswith(evidence.split(":")[0] + "/")


def test_postgres_persists_at_the_path_the_18_image_actually_uses(
    compose: dict[str, Any],
) -> None:
    mounts = [volume.split(":")[-1] for volume in compose["services"]["db"]["volumes"]]
    assert "/var/lib/postgresql" in mounts
    assert "/var/lib/postgresql/data" not in mounts


def test_the_database_publishes_nothing_and_has_no_password_in_the_file(
    compose: dict[str, Any],
) -> None:
    database = compose["services"]["db"]
    assert "ports" not in database
    assert "POSTGRES_PASSWORD" not in database.get("environment", {})


# -- how it runs -----------------------------------------------------------


def test_the_application_is_served_by_gunicorn(compose: dict[str, Any]) -> None:
    command = compose["services"]["web"]["command"]
    assert command[0] == "gunicorn"
    assert "runserver" not in " ".join(command)


def test_nothing_starts_by_running_migrations_or_importing(compose: dict[str, Any]) -> None:
    """Migrations and the importer are controlled steps.

    On boot they would run on every restart, and one of them writes 10,916
    files.
    """
    for service in compose["services"].values():
        rendered = str(service.get("command", "")) + str(service.get("entrypoint", ""))
        assert "migrate" not in rendered
        assert "historical_import" not in rendered


def test_nothing_seeds_synthetic_data(compose: dict[str, Any]) -> None:
    for name in ("web", "extractor"):
        assert compose["services"][name]["environment"]["SEED_DEV_DATA"] == "0"


def test_containers_restart_unless_stopped(compose: dict[str, Any]) -> None:
    for service in compose["services"].values():
        assert service["restart"] == "unless-stopped"


# -- the environment template ---------------------------------------------


def test_the_template_is_the_combination_the_checks_permit(
    env_example: dict[str, str],
) -> None:
    """Real data, an authenticator, no debug, no synthetic sign-in."""
    assert env_example["REAL_DATA_ALLOWED"] == "1"
    assert env_example["CF_ACCESS_ENABLED"] == "1"
    assert env_example["DJANGO_DEBUG"] == "0"
    assert env_example["DEV_LOGIN_ENABLED"] == "0"


def test_the_template_has_no_shared_pin(env_example: dict[str, str]) -> None:
    """The rehearsal's PIN has no equivalent here (Stage-2D brief 56)."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "DEV_LOGIN_PIN=" not in text
    assert "1925" not in text
    assert not any(key.startswith("DEV_LOGIN_PIN") for key in env_example)


def test_the_template_carries_no_secret(env_example: dict[str, str]) -> None:
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "CF_ACCESS_AUDIENCE"):
        assert env_example[key].startswith("replace-me"), f"{key} looks like a real value"


def test_the_template_names_no_real_host(env_example: dict[str, str]) -> None:
    """The repository is public; the hostname is filled in on the server."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "orgusaar.ee" not in text
    assert "cloudflareaccess.com" in env_example["CF_ACCESS_TEAM_DOMAIN"]
    assert env_example["CF_ACCESS_TEAM_DOMAIN"].startswith("replace-me")


def test_the_template_declares_what_the_application_needs(
    env_example: dict[str, str],
) -> None:
    required = {
        "REAL_DATA_ALLOWED",
        "DEV_LOGIN_ENABLED",
        "DJANGO_DEBUG",
        "CF_ACCESS_ENABLED",
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUDIENCE",
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "DJANGO_SECURE_SSL_REDIRECT",
        "APPLICATION_ENVIRONMENT",
        "APPLICATION_REVISION",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "HISTORICAL_SOURCE_ROOT",
    }
    assert required <= set(env_example)


def test_the_database_host_is_the_compose_service_not_a_shared_one(
    env_example: dict[str, str], compose: dict[str, Any]
) -> None:
    assert env_example["POSTGRES_HOST"] == "db"
    assert "db" in compose["services"]
