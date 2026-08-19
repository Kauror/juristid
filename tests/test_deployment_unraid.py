"""The Unraid rehearsal stack is configuration, so it is checked like code.

Deliberately small. These assert the handful of properties whose violation
would be either dangerous or silent — a published database port, a default that
lets real data in, a storage path that reaches into somebody else's service —
and nothing about whether the deployment is *good*. Compose files invite
elaborate validation suites that mostly restate the file; this does not.

No Docker and no network: the file is parsed as YAML.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings

DEPLOY = Path(settings.BASE_DIR) / "deploy" / "unraid-test"
COMPOSE = DEPLOY / "compose.yml"
ENV_EXAMPLE = DEPLOY / ".env.example"

#: Appdata subtrees belonging to other services on the same host. Juristid must
#: not name any of them.
FOREIGN_APPDATA = (
    "/mnt/user/appdata/immich",
    "/mnt/user/appdata/PostgreSQL_Immich",
    "/mnt/user/appdata/Plex-Media-Server",
    "/mnt/user/appdata/dashkoda",
    "/mnt/user/appdata/koda",
    "/mnt/user/appdata/jellyfin",
    "/mnt/user/appdata/qbittorrent",
    "/mnt/user/appdata/sonarr",
    "/mnt/user/appdata/radarr",
)

OWN_PREFIX = "/mnt/user/appdata/juristid-test"


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


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


def test_the_project_name_is_its_own(compose: dict[str, Any]) -> None:
    """A shared project name is how one stack's `down` stops another's."""
    assert compose["name"] == "juristid-test"


def test_the_database_publishes_no_host_port(compose: dict[str, Any]) -> None:
    """The one that would quietly expose PostgreSQL to the whole LAN."""
    assert "ports" not in compose["services"]["db"]


def test_only_the_web_service_publishes_anything(compose: dict[str, Any]) -> None:
    publishing = [name for name, service in compose["services"].items() if service.get("ports")]
    assert publishing == ["web"]


def test_the_web_service_publishes_exactly_one_port(compose: dict[str, Any]) -> None:
    ports = compose["services"]["web"]["ports"]
    assert len(ports) == 1
    assert ports[0].endswith(":8000")


def test_the_application_is_served_by_gunicorn(compose: dict[str, Any]) -> None:
    """Never `runserver`: it is single-threaded and not a production server."""
    command = compose["services"]["web"]["command"]
    assert command[0] == "gunicorn"
    assert "runserver" not in " ".join(command)


def test_nothing_starts_by_running_migrations(compose: dict[str, Any]) -> None:
    """Migrations are a controlled step. On boot they run on every restart."""
    for service in compose["services"].values():
        rendered = str(service.get("command", "")) + str(service.get("entrypoint", ""))
        assert "migrate" not in rendered


def test_the_stack_uses_its_own_network_and_joins_no_other(compose: dict[str, Any]) -> None:
    networks = compose["networks"]
    assert list(networks) == ["internal"]
    assert networks["internal"]["name"] == "juristid-test-internal"
    assert networks["internal"].get("external") is not True
    for service in compose["services"].values():
        assert service["networks"] == ["internal"]


def test_the_stack_does_not_use_host_networking(compose: dict[str, Any]) -> None:
    for service in compose["services"].values():
        assert service.get("network_mode") != "host"


#: `${NAME:-default}`. The default has to be substituted *before* the volume
#: string is split on ":", or the colon inside the substitution splits it.
_SUBSTITUTION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


def _host_path(volume: str) -> str:
    return _SUBSTITUTION.sub(lambda match: match.group(1), volume).split(":", 1)[0]


def test_every_bind_mount_stays_inside_the_juristid_subtree(compose: dict[str, Any]) -> None:
    seen = 0
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            resolved = _host_path(volume)
            seen += 1
            assert resolved.startswith(OWN_PREFIX), f"{name}: {volume}"
            for foreign in FOREIGN_APPDATA:
                assert not resolved.startswith(foreign), f"{name} reaches into {foreign}"
    # Guards the guard: a parser bug that produced no paths would pass silently.
    # Six: postgres, cloudflared, and evidence plus derivatives on each of the
    # two application containers.
    assert seen == 6, "expected the postgres, evidence, derivative and cloudflared mounts"


def test_postgres_persists_at_the_path_the_18_image_actually_uses(
    compose: dict[str, Any],
) -> None:
    """PostgreSQL 18 moved its cluster into a major-version subdirectory.

    Mounting `/var/lib/postgresql/data` — right for every image up to 17 —
    produces a container that starts cleanly and stores nothing durable, which
    is discovered on the first restart and not before.
    """
    mounts = [volume.split(":")[-1] for volume in compose["services"]["db"]["volumes"]]
    assert "/var/lib/postgresql" in mounts
    assert "/var/lib/postgresql/data" not in mounts


def test_the_container_names_do_not_collide_with_anything_on_the_host(
    compose: dict[str, Any],
) -> None:
    names = {service["container_name"] for service in compose["services"].values()}
    assert names == {
        "juristid-test-web",
        "juristid-test-db",
        "juristid-test-extractor",
        "juristid-test-tunnel",
    }


def test_containers_restart_unless_stopped(compose: dict[str, Any]) -> None:
    for service in compose["services"].values():
        assert service["restart"] == "unless-stopped"


# -- the environment template ---------------------------------------------


def test_real_data_is_off_in_the_template(env_example: dict[str, str]) -> None:
    """The single most important line in the file."""
    assert env_example["REAL_DATA_ALLOWED"] == "0"


def test_the_template_never_ships_real_data_enabled() -> None:
    """Belt and braces: no commented-out or alternate spelling turns it on."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "REAL_DATA_ALLOWED=1" not in text


def test_the_synthetic_sign_in_combination_is_the_one_the_checks_permit(
    env_example: dict[str, str],
) -> None:
    """DEV_LOGIN needs DEBUG (juristid.E002) and forbids real data (E003/E004)."""
    assert env_example["DEV_LOGIN_ENABLED"] == "1"
    assert env_example["DJANGO_DEBUG"] == "1"
    assert env_example["REAL_DATA_ALLOWED"] == "0"


def test_the_template_carries_no_secret(env_example: dict[str, str]) -> None:
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"):
        assert env_example[key].startswith("replace-me"), f"{key} looks like a real value"


def test_the_template_declares_what_the_application_needs(
    env_example: dict[str, str],
) -> None:
    required = {
        "REAL_DATA_ALLOWED",
        "DEV_LOGIN_ENABLED",
        "DJANGO_DEBUG",
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_SECURE_SSL_REDIRECT",
        "APPLICATION_ENVIRONMENT",
        "APPLICATION_REVISION",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
    }
    assert required <= set(env_example)


def test_the_database_host_is_the_compose_service_not_a_shared_one(
    env_example: dict[str, str], compose: dict[str, Any]
) -> None:
    """Pointing at an existing PostgreSQL is how a rehearsal writes into
    somebody's production database."""
    assert env_example["POSTGRES_HOST"] == "db"
    assert "db" in compose["services"]


# -- the public tunnel -----------------------------------------------------
#
# The application behind it has no authentication of its own beyond the shared
# sign-in PIN, so the tunnel is safe only while that PIN is set and the data
# behind it stays invented. These assert the properties of the *connector*; the
# PIN lives in the host's environment file and is verified by the deployment,
# not here.


def test_the_tunnel_is_opt_in(compose: dict[str, Any]) -> None:
    """The LAN deployment must work without publishing anything."""
    assert compose["services"]["tunnel"]["profiles"] == ["tunnel"]
    for name in ("web", "db"):
        assert "profiles" not in compose["services"][name]


def test_the_tunnel_publishes_no_host_port(compose: dict[str, Any]) -> None:
    """Publishing the site must not widen the host's port surface."""
    assert "ports" not in compose["services"]["tunnel"]


def test_the_tunnel_carries_no_credential(compose: dict[str, Any]) -> None:
    """A locally-managed tunnel keeps its credential on the host.

    A token in the compose file would be committed, and a tunnel token is a
    bearer credential: whoever reads it can run the tunnel.
    """
    rendered = str(compose["services"]["tunnel"])
    assert "--token" not in rendered
    assert "eyJ" not in rendered, "that looks like a base64 tunnel token"


def test_the_tunnel_joins_only_this_project(compose: dict[str, Any]) -> None:
    assert compose["services"]["tunnel"]["networks"] == ["internal"]


# -- the extraction worker -------------------------------------------------
#
# A second container running the same image. Everything asserted below is a
# containment property: what it can reach, what it can write, and what it
# cannot touch (Stage-2B brief 92, 93).


def test_the_extractor_runs_the_same_image_as_the_web_process(compose: dict[str, Any]) -> None:
    """One build, one version to reason about.

    A separately built worker image drifts from the application it is supposed
    to be part of, and the drift shows up as "extraction works in one place and
    not the other".
    """
    services = compose["services"]
    assert services["extractor"]["image"] == services["web"]["image"]


def test_the_extractor_publishes_no_host_port(compose: dict[str, Any]) -> None:
    """It answers no requests. There is nothing to reach it for."""
    assert "ports" not in compose["services"]["extractor"]


def test_the_extractor_joins_only_this_projects_network(compose: dict[str, Any]) -> None:
    """The host runs two dozen other containers, and this one needs none of them."""
    assert compose["services"]["extractor"]["networks"] == ["internal"]


def test_the_extractor_reads_evidence_and_cannot_write_it(compose: dict[str, Any]) -> None:
    """The load-bearing containment property.

    This process opens untrusted files with half a dozen parsers. It only ever
    *reads* the original bytes, so mounting evidence read-only means a parser
    that goes wrong — or a file that makes it go wrong — cannot alter the one
    thing in this system that must not change.
    """
    mounts = compose["services"]["extractor"]["volumes"]
    evidence = [mount for mount in mounts if "/app/evidence" in mount]
    derivatives = [mount for mount in mounts if "/app/derivatives" in mount]

    assert evidence and all(mount.endswith(":ro") for mount in evidence)
    assert derivatives and not any(mount.endswith(":ro") for mount in derivatives)


def test_evidence_and_derivatives_are_different_directories(compose: dict[str, Any]) -> None:
    """One must survive and be backed up; the other may be deleted and rebuilt.

    A derivatives directory nested inside evidence would make "is this backup
    complete" impossible to answer by looking, and would put an operator one
    deletion away from destroying the half that cannot be regenerated.
    """
    for service in ("web", "extractor"):
        # `_host_path` substitutes `${VAR:-default}` before splitting, because
        # the default itself contains colons. Written once, above.
        hosts = [_host_path(mount) for mount in compose["services"][service]["volumes"]]

        assert len(set(hosts)) == len(hosts), hosts
        for host in hosts:
            others = [other for other in hosts if other != host]
            assert not any(other.startswith(host.rstrip("/") + "/") for other in others)


def test_the_extractor_restarts_by_itself(compose: dict[str, Any]) -> None:
    """A worker that stays down after a host reboot is a queue nobody drains."""
    assert compose["services"]["extractor"]["restart"] == "unless-stopped"
