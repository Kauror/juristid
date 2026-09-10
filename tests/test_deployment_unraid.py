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


def _is_bind(volume: str) -> bool:
    """Whether a short-form volume names a host path rather than a named volume.

    Compose decides on the first character of the source: a leading `/` or `.`
    is a host path, anything else names a volume from the top-level block. The
    rule below is about the host filesystem, and a named volume has no host path
    to place — its isolation comes from the project name instead.
    """
    return _host_path(volume).startswith(("/", "."))


def test_every_bind_mount_stays_inside_the_juristid_subtree(compose: dict[str, Any]) -> None:
    seen = 0
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if not _is_bind(volume):
                continue
            resolved = _host_path(volume)
            seen += 1
            assert resolved.startswith(OWN_PREFIX), f"{name}: {volume}"
            for foreign in FOREIGN_APPDATA:
                assert not resolved.startswith(foreign), f"{name} reaches into {foreign}"
    # Guards the guard: a parser bug that produced no paths would pass silently.
    # Five: postgres, cloudflared, and evidence, derivatives and legacy-source
    # on `web`. The shared upload volume is not a bind — that is the point of it
    # — and has its own tests below, and the intake reader mounts nothing else
    # at all (docs/adr/0072).
    #
    # It was eight while a second application container mounted the same three
    # trees. The count is what has to be maintained by hand, and it is worth
    # keeping rather than deriving: every one of these paths is asserted to stay
    # inside this deployment's own subtree, and a mount that stopped being
    # parsed as a bind would silently leave that assertion. A number somebody
    # has to change deliberately is the cheapest way to notice.
    assert seen == 5, (
        "expected the postgres, evidence, derivative, legacy-source and cloudflared mounts"
    )


def test_every_non_bind_volume_is_a_declared_named_volume(compose: dict[str, Any]) -> None:
    """What keeps the skip above from becoming a hole."""
    declared = set(compose.get("volumes") or {})
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if _is_bind(volume):
                continue
            source = _host_path(volume)
            assert source in declared, f"{name} mounts undeclared volume {source!r}"


# -- the shared upload volume ----------------------------------------------
#
# The rehearsal earns its keep by meeting deployment defects before production
# does, which it can only do where the two stacks agree. `Uus teema` staging is
# written by `web` and read by `intake-reader` (docs/adr/0064, docs/adr/0072),
# so a rehearsal without this volume would report assisted intake working while
# production could not do it at all. `tests/test_deployment_unraid_main.py` holds the two
# files to the same shape.

UPLOAD_TARGET = "/app/pending-uploads"

#: The one queue consumer this stack deploys. It was `extractor`, which drained
#: the corpus queue as well and is why production stalled on 2026-09-10; the
#: service is gone from both Compose files so that `up -d` cannot start it
#: (docs/adr/0072).
READER = "intake-reader"


def _upload_mounts(compose: dict[str, Any], service: str) -> list[str]:
    mounts = compose["services"][service].get("volumes") or []
    return [m for m in mounts if m.split(":")[1:2] == [UPLOAD_TARGET]]


def test_web_and_the_intake_reader_share_one_upload_volume(compose: dict[str, Any]) -> None:
    web = _upload_mounts(compose, "web")
    reader = _upload_mounts(compose, READER)
    assert len(web) == 1, f"web: expected one mount at {UPLOAD_TARGET}, got {web}"
    assert len(reader) == 1, f"{READER}: expected one, got {reader}"
    assert web[0].split(":", 1)[0] == reader[0].split(":", 1)[0]


def test_the_upload_volume_is_project_scoped_and_writable_only_by_web(
    compose: dict[str, Any],
) -> None:
    """A named volume, scoped by the project, `web` RW and `intake-reader` RO.

    No explicit `name:`, so this stack's is `juristid-test_pending_uploads` and
    cannot be the volume production writes into. Not a bind either: a staged
    file is correspondence even when the correspondence is invented, and appdata
    is a share.
    """
    volumes = compose.get("volumes") or {}
    source = _upload_mounts(compose, "web")[0].split(":", 1)[0]
    assert not source.startswith(("/", ".")), f"{source!r} is a host path"
    assert source in volumes, "not declared in the top-level volumes block"

    declaration = volumes[source] or {}
    assert "name" not in declaration, "an explicit name would defeat project scoping"
    assert declaration.get("external") is not True

    assert _upload_mounts(compose, "web")[0].endswith(UPLOAD_TARGET), "web must be read-write"
    assert _upload_mounts(compose, READER)[0].endswith(f"{UPLOAD_TARGET}:ro")


def test_no_other_service_receives_the_upload_volume(compose: dict[str, Any]) -> None:
    for name in ("db", "searchindex", "tunnel"):
        assert _upload_mounts(compose, name) == [], f"{name} was given the upload volume"


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
        "juristid-test-intake-reader",
        "juristid-test-searchindex",
        "juristid-test-tunnel",
    }


def test_the_worker_is_not_judged_by_a_healthcheck_it_cannot_pass(
    compose: dict[str, Any],
) -> None:
    """The worker has no HTTP port, so the image's probe could only be red.

    A container that is always red makes one that *becomes* unhealthy
    indistinguishable — the signal is gone rather than merely wrong. The
    rehearsal ran that way for 28 hours.
    """
    check = compose["services"][READER].get("healthcheck")
    assert check is not None, "the intake reader inherits the web healthcheck"
    assert "check_intake_reader" in " ".join(check["test"])
    assert "healthz" not in " ".join(check["test"])


def test_the_web_service_keeps_the_image_healthcheck(compose: dict[str, Any]) -> None:
    """It does serve HTTP, so the inherited probe is the right one."""
    assert "healthcheck" not in compose["services"]["web"]


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


# -- the intake reader -----------------------------------------------------
#
# A second container running the same image. Everything asserted below is a
# containment property: what it can reach, what it can write, and what it
# cannot touch (Stage-2B brief 92, 93; docs/adr/0072).


def test_the_corpus_extractor_is_not_a_service_on_this_stack(compose: dict[str, Any]) -> None:
    """The trap §25 of the brief asks to be closed, asserted where it lived.

    A corpus-wide extraction run saturated the production array on 2026-09-10.
    Leaving the service defined — even stopped — means the next
    `docker compose up -d` starts it again, and nobody decided that. So there
    is no such service, and this test fails the day somebody adds one back.
    """
    assert "extractor" not in compose["services"]
    commands = [" ".join(service.get("command") or []) for service in compose["services"].values()]
    assert not any("run_extraction_worker" in command for command in commands)


def test_the_intake_reader_runs_the_same_image_as_the_web_process(
    compose: dict[str, Any],
) -> None:
    """One build, one version to reason about.

    A separately built worker image drifts from the application it is supposed
    to be part of, and the drift shows up as "reading works in one place and
    not the other".
    """
    services = compose["services"]
    assert services[READER]["image"] == services["web"]["image"]


def test_the_intake_reader_runs_the_intake_reader(compose: dict[str, Any]) -> None:
    assert "run_intake_reader" in " ".join(compose["services"][READER]["command"])


def test_the_intake_reader_publishes_no_host_port(compose: dict[str, Any]) -> None:
    """It answers no requests. There is nothing to reach it for."""
    assert "ports" not in compose["services"][READER]


def test_the_intake_reader_joins_only_this_projects_network(compose: dict[str, Any]) -> None:
    """The host runs two dozen other containers, and this one needs none of them."""
    assert compose["services"][READER]["networks"] == ["internal"]


def test_the_intake_reader_reaches_no_evidence_at_all(compose: dict[str, Any]) -> None:
    """It reads staged bytes and writes to PostgreSQL. Nothing else is its business.

    The old `extractor` mounted evidence read-write, and correctly: it unpacked
    an email's attachments, which are themselves new evidence. This process
    does not — a staged message's attachments are deliberately not unpacked,
    because a Document needs a Matter — so the mount would be reach without
    purpose (app/matters/intake_extraction.py).
    """
    mounts = compose["services"][READER].get("volumes") or []
    assert len(mounts) == 1, f"the reader should mount only staging, got {mounts}"
    assert mounts[0].endswith(f"{UPLOAD_TARGET}:ro")


def test_evidence_and_derivatives_are_different_directories(compose: dict[str, Any]) -> None:
    """One must survive and be backed up; the other may be deleted and rebuilt.

    A derivatives directory nested inside evidence would make "is this backup
    complete" impossible to answer by looking, and would put an operator one
    deletion away from destroying the half that cannot be regenerated.
    """
    # `_host_path` substitutes `${VAR:-default}` before splitting, because
    # the default itself contains colons. Written once, above.
    hosts = [_host_path(mount) for mount in compose["services"]["web"]["volumes"]]

    assert len(set(hosts)) == len(hosts), hosts
    for host in hosts:
        others = [other for other in hosts if other != host]
        assert not any(other.startswith(host.rstrip("/") + "/") for other in others)


def test_the_intake_reader_restarts_by_itself(compose: dict[str, Any]) -> None:
    """A reader that stays down after a host reboot is a form nobody fills in."""
    assert compose["services"][READER]["restart"] == "unless-stopped"


def test_the_search_worker_is_not_judged_by_a_healthcheck_it_cannot_pass(
    compose: dict[str, Any],
) -> None:
    """Same reasoning as the intake reader's, different question asked.

    This probe reads the outstanding refresh obligations rather than a
    heartbeat file, so it measures whether the index is converging rather than
    whether a process is breathing. A stopped worker with nothing owed is green
    and correctly so; the moment a rename lands it goes red
    (app/search/management/commands/check_search_freshness.py).
    """
    check = compose["services"]["searchindex"].get("healthcheck")
    assert check is not None, "the search worker inherits the web healthcheck"
    assert "check_search_freshness" in " ".join(check["test"])


def test_the_search_worker_runs_the_same_image_and_no_evidence_mount(
    compose: dict[str, Any],
) -> None:
    """It rebuilds a projection out of PostgreSQL and reads no stored file.

    Mounting the evidence tree into it would widen what a compromised container
    can reach in exchange for nothing.
    """
    services = compose["services"]
    assert services["searchindex"]["image"] == services["web"]["image"]
    assert "volumes" not in services["searchindex"]
    assert "ports" not in services["searchindex"]
    assert services["searchindex"]["networks"] == ["internal"]


def test_the_rehearsal_template_pins_no_stage_number(env_example: dict[str, str]) -> None:
    """A label may say which instance this is; it may not say which stage.

    This file read `Stage 2A rehearsal` from 2A through 2I. A stage copied into a
    `.env` is a stage that goes stale where nobody looks — the same defect
    `tests/test_deployment_unraid_main.py` already refuses for the real-data
    stack, which pins no stage at all. The rehearsal keeps a label because
    distinguishing the synthetic instance from the real one is worth a word; the
    number comes from `config/settings.py`.
    """
    stage = env_example.get("APPLICATION_STAGE", "")

    assert stage, "the rehearsal instance should still say which instance it is"
    assert not re.search(r"Stage|\d", stage), (
        f"APPLICATION_STAGE={stage!r} pins a stage number; it will be wrong by the "
        "next merge and nobody reads a footer to check"
    )
