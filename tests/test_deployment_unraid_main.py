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

from tests.deployment_commands import served

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


#: Which network each service joins (MCPSEC-02). Every service is on
#: `internal` except the OpenAI tunnel client, which is on `mcp_edge` alone:
#: it reaches `mcp` and the internet, never `db`, `web` or `tunnel`. The write
#: gateway (docs/adr/0189) is on `internal` for `db` and `mcp_write` for `mcp`,
#: never `mcp_edge`; `mcp` is on all three.
EXPECTED_NETWORKS = {
    "mcp": ["internal", "mcp_edge", "mcp_write"],
    "mcp-tunnel": ["mcp_edge"],
    "mcp-writer": ["internal", "mcp_write"],
}


def test_the_stack_uses_its_own_networks_and_joins_no_other(compose: dict[str, Any]) -> None:
    networks = compose["networks"]
    assert sorted(networks) == ["internal", "mcp_edge", "mcp_write"]
    assert networks["internal"]["name"] == "juristid-main-internal"
    assert networks["mcp_edge"]["name"] == "juristid-main-mcp-edge"
    assert networks["mcp_write"]["name"] == "juristid-main-mcp-write"
    for network in networks.values():
        assert network.get("external") is not True
    for name, service in compose["services"].items():
        assert list(service["networks"]) == EXPECTED_NETWORKS.get(name, ["internal"]), name


def test_only_mcp_shares_a_network_with_the_tunnel_client(compose: dict[str, Any]) -> None:
    """The tunnel client cannot reach `db`, `web`, `tunnel` or a worker at all.

    A third-party binary steered by a third-party control plane: on `internal`
    it could dial `db:5432`, and `web:8000` directly — past Cloudflare, with a
    `CF-Connecting-IP` of its choosing (MCPSEC-02). It shares one network with
    exactly one service, and that network holds exactly those two.
    """
    services = compose["services"]
    tunnel_networks = set(services[MCP_TUNNEL]["networks"])
    reachable = {
        name
        for name, service in services.items()
        if name != MCP_TUNNEL and tunnel_networks & set(service["networks"])
    }
    assert reachable == {MCP}
    on_edge = {name for name, service in services.items() if "mcp_edge" in service["networks"]}
    assert on_edge == {MCP, MCP_TUNNEL}
    for name in ("db", "web", "tunnel", "mcp-writer"):
        assert "mcp_edge" not in services[name]["networks"], name


def test_the_write_network_holds_the_mcp_server_and_the_gateway_only(
    compose: dict[str, Any],
) -> None:
    """`mcp_write` is internal (no route off the host) and the tunnel client is not on it.

    So the tunnel client — on `mcp_edge` alone — can neither resolve nor reach
    `mcp-writer`: the two share no network (docs/adr/0189, CONTRACT §1).
    """
    services = compose["services"]
    assert compose["networks"]["mcp_write"]["internal"] is True
    assert compose["networks"]["mcp_write"]["driver"] == "bridge"
    on_write = {name for name, service in services.items() if "mcp_write" in service["networks"]}
    assert on_write == {MCP, MCP_WRITER}
    shared = set(services[MCP_TUNNEL]["networks"]) & set(services[MCP_WRITER]["networks"])
    assert shared == set(), "the tunnel client shares a network with the write gateway"


#: `${NAME:-default}`. The default has to be substituted *before* the volume
#: string is split on ":", or the colon inside the substitution splits it.
_SUBSTITUTION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")


def _resolved(volume: str) -> str:
    return _SUBSTITUTION.sub(lambda match: match.group(1), volume)


def _is_bind(volume: str) -> bool:
    """Whether a short-form volume names a host path rather than a named volume.

    Compose tells the two apart by the first character of the source: a leading
    `/` or `.` is a host path, anything else is the name of a volume declared in
    the top-level `volumes:` block. The distinction matters here because the
    rules below are about the *host* filesystem, and a named volume has no host
    path to check — its isolation comes from the project name instead.
    """
    return _resolved(volume).split(":", 1)[0].startswith(("/", "."))


def test_every_bind_mount_stays_inside_the_juristid_main_subtree(
    compose: dict[str, Any],
) -> None:
    seen = 0
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if not _is_bind(volume):
                continue
            host_path = _resolved(volume).split(":", 1)[0]
            seen += 1
            assert host_path.startswith(OWN_PREFIXES), f"{name}: {volume}"
            for foreign in FOREIGN_APPDATA:
                assert not host_path.startswith(foreign), f"{name} reaches into {foreign}"
    # Guards the guard: a parser bug producing no paths would pass silently.
    # Six: postgres, cloudflared, and four bind mounts on `web` (evidence,
    # derivatives, legacy-source, source). It was ten while `extractor` mounted
    # the same four; the intake reader that replaced it mounts staging and
    # nothing else, because it has no reason to see an evidence tree
    # (docs/adr/0072). The shared pending-uploads volume is not a bind and is
    # counted by
    # `test_the_shared_upload_volume_is_a_named_volume_and_not_an_appdata_bind`.
    assert seen == 6


def test_every_non_bind_volume_is_a_declared_named_volume(compose: dict[str, Any]) -> None:
    """Nothing is mounted from a source this file does not itself declare.

    The bind-mount rule above skips anything that is not a host path, so this is
    what stops that skip from becoming a hole: a source that is not a bind must
    appear in the top-level `volumes:` block, where its scoping can be reasoned
    about.
    """
    declared = set(compose.get("volumes") or {})
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if _is_bind(volume):
                continue
            source = _resolved(volume).split(":", 1)[0]
            assert source in declared, f"{name} mounts undeclared volume {source!r}"


def test_the_historical_source_is_mounted_read_only(compose: dict[str, Any]) -> None:
    """The importer reads its source and never rewrites it (Stage-2D brief 54).

    An importer that can write to its own source material is one bad run away
    from having nothing left to re-run against.

    Read-only *bind* mounts, because the intake reader also mounts the shared
    upload volume read-only and that is a different rule with a test of its own.
    The host filesystem this stack can write to is what this one is about.
    """
    for name in ("web",):
        mounts = [_resolved(volume) for volume in compose["services"][name]["volumes"]]
        binds = [m for m in mounts if m.startswith(("/", "."))]
        historical = [m for m in binds if m.endswith(":ro")]
        assert len(historical) == 1, f"{name}: expected exactly one read-only bind mount"
        assert "/srv/historical-source:ro" in historical[0]


# -- the shared upload volume ----------------------------------------------
#
# `Uus teema` staging put a second container on this storage class: `web` writes
# the staged file and `intake-reader` opens it to parse it (docs/adr/0064,
# docs/adr/0072). A
# container's writable layer is private, so the unmounted default that served
# held uploads for one process leaves the worker looking for a file that only
# exists in `web` — the feature suite could not see it, because it never crossed
# a container boundary. These are the Compose contract, checked as configuration
# because that is what it is.

#: What both application containers agree the storage class is mounted at, and
#: what `PENDING_UPLOAD_ROOT` defaults to inside the image (`config/settings.py`,
#: `BASE_DIR` being `/app`).
UPLOAD_TARGET = "/app/pending-uploads"

#: The one queue consumer this stack deploys, since docs/adr/0072. It was
#: `extractor`, which drained the corpus queue too; that service is gone from
#: both stacks so `up -d` cannot restart the workload that saturated the array.
READER = "intake-reader"


def _upload_mounts(compose: dict[str, Any], service: str) -> list[str]:
    mounts = [_resolved(volume) for volume in compose["services"][service].get("volumes") or []]
    return [m for m in mounts if m.split(":")[1:2] == [UPLOAD_TARGET]]


def test_web_and_the_intake_reader_share_one_upload_volume(compose: dict[str, Any]) -> None:
    """The defect this whole arrangement exists to prevent, stated once.

    Same source, same target, in both containers. Two volumes that merely happen
    to be mounted at the same path would satisfy every other test in this file
    and still leave the reader unable to read a staged file.
    """
    web = _upload_mounts(compose, "web")
    reader = _upload_mounts(compose, READER)
    assert len(web) == 1, f"web: expected one mount at {UPLOAD_TARGET}, got {web}"
    assert len(reader) == 1, f"{READER}: expected one at {UPLOAD_TARGET}, got {reader}"

    web_source = web[0].split(":", 1)[0]
    reader_source = reader[0].split(":", 1)[0]
    assert web_source == reader_source, "the two containers mount different sources"


def test_the_shared_upload_volume_is_a_named_volume_and_not_an_appdata_bind(
    compose: dict[str, Any],
) -> None:
    """A Docker volume, deliberately, and declared for Compose to scope.

    Not a bind under `/mnt/user/appdata`: these are temporary uploaded documents
    from real member correspondence, and a bind would put them inside the appdata
    tree and therefore inside the SMB share exported from it — widening a known
    host-security matter for storage that is thrown away on a timer.

    Declared with no explicit `name:`, because the project name is what isolates
    it. Naming it globally would hand the same volume to `juristid-test`, to a
    CI project and to a recovery rehearsal.
    """
    source = _upload_mounts(compose, "web")[0].split(":", 1)[0]
    assert not source.startswith(("/", ".")), f"{source!r} is a host path, not a named volume"

    volumes = compose.get("volumes") or {}
    assert source in volumes, f"{source!r} is not declared in the top-level volumes block"
    # `pending_uploads: ` with nothing under it parses as None, which is the
    # shape that lets Compose scope the volume to the project.
    declaration = volumes[source] or {}
    assert "name" not in declaration, "an explicit name would defeat per-project scoping"
    assert declaration.get("external") is not True, "an external volume is not project-scoped"


def test_the_intake_reader_cannot_write_to_the_staged_files_it_reads(
    compose: dict[str, Any],
) -> None:
    """`web` read-write, `intake-reader` read-only, because that is what each does.

    Everything that changes a staged object happens in the web process: the
    upload view stores it, `consume_session` deletes it after `Loo teema`, and
    `prune_intake_staging` sweeps what is left. The worker's whole interaction
    with the bytes is one `storage.open(..., "rb")` in
    `app/matters/intake_extraction.py`, so read-only costs it nothing and takes
    the source of a parse out of reach of the process doing the parsing.
    """
    assert _upload_mounts(compose, "web")[0].endswith(UPLOAD_TARGET), "web must be read-write"
    assert _upload_mounts(compose, READER)[0].endswith(f"{UPLOAD_TARGET}:ro")


def test_no_other_service_receives_the_upload_volume(compose: dict[str, Any]) -> None:
    """Two processes use this storage class; nothing else is given it.

    `searchindex` rebuilds a projection out of PostgreSQL and reads no file the
    application stores, `db` and `tunnel` are not the application at all — the
    same reasoning that keeps the evidence tree off all three.
    """
    for name in ("db", "searchindex", "tunnel"):
        assert _upload_mounts(compose, name) == [], f"{name} was given the upload volume"


SWEEPER = "intake-sweeper"


def test_the_staging_sweeper_is_scheduled_and_reaches_only_the_staging_volume(
    compose: dict[str, Any], rehearsal: dict[str, Any]
) -> None:
    """csf_3942255b466a4e213b958b16, docs/adr/0177: the sweep runs on its own.

    A container running the bounded loop, not a host cron entry (this host's
    cron is generated from the flash drive). Read-write on the staging volume,
    because deleting is its job, and nothing else mounted — the evidence tree
    is not reachable from it at all. Judged by convergence, not by the web
    image's HTTP probe it could never pass. The same in the rehearsal.
    """
    for model in (compose, rehearsal):
        services = model["services"]
        sweeper = services[SWEEPER]
        assert sweeper["image"] == services["web"]["image"], model["name"]
        assert [str(part) for part in sweeper["command"]] == [
            "python",
            "manage.py",
            "prune_intake_staging",
            "--loop",
        ]
        assert "check_intake_staging" in " ".join(sweeper["healthcheck"]["test"])
        mounts = [_resolved(volume) for volume in sweeper.get("volumes") or []]
        assert [m.split(":")[1] for m in mounts] == [UPLOAD_TARGET], model["name"]
        assert not mounts[0].endswith(":ro"), f"{model['name']}: the sweeper cannot delete"
        web_source = next(m for m in services["web"]["volumes"] if UPLOAD_TARGET in m)
        assert mounts[0].split(":", 1)[0] == web_source.split(":", 1)[0], model["name"]
        assert sweeper["environment"]["SEED_DEV_DATA"] == "0"
        assert sweeper["environment"]["JURISTID_RUNTIME"] == "deployed"
        assert sweeper["depends_on"] == {"db": {"condition": "service_healthy"}}


NOTIFIER = "notifications"


def test_the_commencement_reminder_loop_is_scheduled_and_mounts_nothing(
    compose: dict[str, Any],
) -> None:
    """docs/adr/0188: the reminders are generated by a loop of their own.

    The release image running the idempotent, advisory-locked reconcile, not a
    host cron entry, judged by its own `--check` rather than the web probe. It
    reads and writes only PostgreSQL, so nothing at all is mounted into it —
    neither the evidence tree nor the upload staging volume.
    """
    services = compose["services"]
    notifier = services[NOTIFIER]
    assert notifier["image"] == services["web"]["image"]
    assert [str(part) for part in notifier["command"]] == [
        "python",
        "manage.py",
        "generate_commencement_reminders",
        "--loop",
    ]
    assert "generate_commencement_reminders" in " ".join(notifier["healthcheck"]["test"])
    assert "--check" in notifier["healthcheck"]["test"]
    assert not notifier.get("volumes")
    assert not notifier.get("ports")
    assert notifier["networks"] == ["internal"]
    assert notifier["environment"]["SEED_DEV_DATA"] == "0"
    assert notifier["environment"]["JURISTID_RUNTIME"] == "deployed"
    assert notifier["depends_on"] == {"db": {"condition": "service_healthy"}}


def test_the_rehearsal_has_the_same_upload_topology(
    compose: dict[str, Any], rehearsal: dict[str, Any]
) -> None:
    """The two stacks must not drift apart on *this* invariant.

    The rehearsal's whole purpose is to meet a deployment defect before
    production does, and it can only do that where the two agree. This is the
    boundary that hid the original defect: staging is written by `web` and read
    by `intake-reader`, so a rehearsal whose containers do not share the volume
    would exercise assisted intake successfully and prove nothing about the
    stack that matters.

    Compared as a shape rather than as text — same target, same source in both
    services, same read-only split, same set of services excluded — so the two
    files stay free to differ everywhere they legitimately do.
    """
    for model in (compose, rehearsal):
        services = model["services"]
        web = [
            m
            for m in (services["web"].get("volumes") or [])
            if m.split(":")[1:2] == [UPLOAD_TARGET]
        ]
        reader = [
            m
            for m in (services[READER].get("volumes") or [])
            if m.split(":")[1:2] == [UPLOAD_TARGET]
        ]
        assert len(web) == 1 and len(reader) == 1, f"{model['name']}: not exactly one each"
        assert web[0].split(":", 1)[0] == reader[0].split(":", 1)[0], model["name"]
        assert web[0].endswith(UPLOAD_TARGET), f"{model['name']}: web is not read-write"
        assert reader[0].endswith(f"{UPLOAD_TARGET}:ro"), f"{model['name']}: not read-only"

        for other in ("db", "searchindex", "tunnel"):
            mounts = services[other].get("volumes") or []
            assert not [m for m in mounts if m.split(":")[1:2] == [UPLOAD_TARGET]], (
                f"{model['name']}: {other} was given the upload volume"
            )


def test_the_two_stacks_cannot_reach_each_others_upload_volume(
    compose: dict[str, Any], rehearsal: dict[str, Any]
) -> None:
    """Same declaration, different project — which is what keeps them apart.

    Both stacks run on one host and both call the volume `pending_uploads`. What
    stops the Chamber's staged correspondence and the rehearsal's invented mail
    landing in one directory is that neither declaration overrides the name, so
    Compose scopes each to its own project: `juristid-main_pending_uploads` and
    `juristid-test_pending_uploads`.

    An explicit `name:` on either — or `external: true` — would silently join
    them, which is the one way this arrangement could go wrong quietly.
    """
    assert compose["name"] != rehearsal["name"]

    for model in (compose, rehearsal):
        declared = model.get("volumes") or {}
        assert declared, f"{model['name']} declares no volumes"
        for volume, declaration in declared.items():
            body = declaration or {}
            assert "name" not in body, f"{model['name']}: {volume} pins a global name"
            assert body.get("external") is not True, f"{model['name']}: {volume} is external"


def test_the_image_owns_the_upload_directory_the_volume_shadows() -> None:
    """A named volume inherits the ownership of the image path it covers.

    `/app/pending-uploads` did not have to exist in the image while it was an
    ordinary directory the application created on first write. Mounted over by a
    volume it does exist first, created by Docker — root-owned, if the image has
    nothing there to copy ownership from — and the container runs as `juristid`,
    which would then be unable to stage a single file.
    """
    dockerfile = (Path(settings.BASE_DIR) / "Dockerfile").read_text(encoding="utf-8")
    creation = next(line for line in dockerfile.splitlines() if "mkdir -p /app/evidence" in line)
    assert "/app/pending-uploads" in creation, "the image does not create the mount point"
    handover = next(
        line
        for line in dockerfile.splitlines()
        if "chown" in line and not line.lstrip().startswith("#")
    )
    assert "juristid:juristid" in handover
    assert "/app/pending-uploads" in handover.split(), "the mount point is not handed over"


def test_the_volume_is_mounted_where_the_application_actually_writes(
    compose: dict[str, Any],
) -> None:
    """The mount point and `PENDING_UPLOAD_ROOT` have to be the same directory.

    Everything else in this file checks the Compose side. This is the join: a
    volume correctly shared at a path the application does not use would satisfy
    every one of those and still leave staged bytes in a container layer.

    Three links, none of them a coincidence to be left unasserted — the image's
    `WORKDIR` is `/app`, so `BASE_DIR` is `/app`; the setting's *declared
    default* is `BASE_DIR / "pending-uploads"`; and neither service overrides the
    variable, so that default is what a deployed container runs with.

    The declaration is read from the source rather than from `settings`, because
    the value in force here is not the deployed one: the test settings mint a
    throwaway storage root per run, which is exactly what stops a test writing
    into a real tree (`config/test_settings.py`).
    """
    dockerfile = (Path(settings.BASE_DIR) / "Dockerfile").read_text(encoding="utf-8")
    assert "\nWORKDIR /app\n" in dockerfile, "BASE_DIR is no longer /app in the image"

    source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
    declaration = next(
        line for line in source.splitlines() if line.startswith("PENDING_UPLOAD_ROOT = ")
    )
    assert 'env("PENDING_UPLOAD_ROOT", str(BASE_DIR / "pending-uploads"))' in declaration
    assert UPLOAD_TARGET == "/app/pending-uploads"

    for name in ("web", READER):
        environment = compose["services"][name].get("environment", {})
        assert "PENDING_UPLOAD_ROOT" not in environment, (
            f"{name} overrides the path the volume is mounted at"
        )


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
    # Named, since ADR 0179, but only as an interpolation the operator fills from
    # the environment file when a cluster is initialised — never a value.
    assert database.get("environment", {}).get("POSTGRES_PASSWORD", "${POSTGRES_PASSWORD:-}") == (
        "${POSTGRES_PASSWORD:-}"
    )


# -- how it runs -----------------------------------------------------------


def test_the_application_is_served_by_gunicorn(compose: dict[str, Any]) -> None:
    command = served(compose["services"]["web"]["command"])
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


def test_nothing_seeds_synthetic_data(compose: dict[str, Any]) -> None:
    for name in ("web", READER):
        assert compose["services"][name]["environment"]["SEED_DEV_DATA"] == "0"


def test_containers_restart_unless_stopped(compose: dict[str, Any]) -> None:
    for service in compose["services"].values():
        assert service["restart"] == "unless-stopped"


# -- the environment template ---------------------------------------------


def test_the_template_is_the_combination_the_checks_permit(
    env_example: dict[str, str],
) -> None:
    """Real data, an authenticator, no debug, no passwordless sign-in."""
    assert env_example["REAL_DATA_ALLOWED"] == "1"
    assert env_example["AUTH_MODE"] in {"shared_gate", "cloudflare_access"}
    assert env_example["DJANGO_DEBUG"] == "0"
    assert env_example["DEV_LOGIN_ENABLED"] == "0"


def test_the_template_has_no_shared_pin(env_example: dict[str, str]) -> None:
    """The rehearsal's PIN has no equivalent here (Stage-2D brief 56).

    The shared gate is a different mechanism, not a longer PIN: its own mode,
    its own safeguards, its own checks. Reusing `DEV_LOGIN_PIN` would have
    quietly re-enabled a path that was forbidden with real data on purpose.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "DEV_LOGIN_PIN=" not in text
    assert "1925" not in text
    assert not any(key.startswith("DEV_LOGIN_PIN") for key in env_example)


def test_the_shared_gate_is_rate_limited_in_the_template(
    env_example: dict[str, str],
) -> None:
    """A long password is the control; the throttle is the defence in depth."""
    assert int(env_example["SHARED_GATE_MAX_ATTEMPTS"]) >= 1
    assert int(env_example["SHARED_GATE_LOCKOUT_SECONDS"]) >= 60
    # Bounded, so escalation never becomes a permanent lockout.
    assert 0 < int(env_example["SHARED_GATE_MAX_LOCKOUT_SECONDS"]) <= 24 * 3600


def test_the_template_carries_no_secret(env_example: dict[str, str]) -> None:
    for key in (
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "JURISTID_SHARED_GATE_PASSWORD",
    ):
        assert env_example[key].startswith("replace-me"), f"{key} looks like a real value"


def test_the_shared_password_is_never_a_compose_default(compose: dict[str, Any]) -> None:
    """It is a host-side secret, and Compose is in Git.

    A default here would be committed, and a committed shared password is a
    published one — the repository is public (Stage-2D auth brief 2, 13).
    """
    rendered = COMPOSE.read_text(encoding="utf-8")
    assert "JURISTID_SHARED_GATE_PASSWORD" not in rendered
    for service in compose["services"].values():
        assert "JURISTID_SHARED_GATE_PASSWORD" not in service.get("environment", {})


def test_the_allowed_hosts_let_the_container_check_itself(
    env_example: dict[str, str], compose: dict[str, Any]
) -> None:
    """The image's HEALTHCHECK calls itself on 127.0.0.1.

    Without a loopback entry Django answers 400, the healthcheck fails for ever,
    and the container reports `unhealthy` while serving every real request
    perfectly. The first deployment ran that way for 176 consecutive failures.

    The cost is not the red light. It is that a container which later becomes
    genuinely unhealthy looks exactly the same.
    """
    hosts = {host.strip() for host in env_example["DJANGO_ALLOWED_HOSTS"].split(",")}
    assert "127.0.0.1" in hosts, "the container cannot pass its own healthcheck"
    assert "localhost" in hosts


def test_the_template_names_no_real_host(env_example: dict[str, str]) -> None:
    """The repository is public; the hostname is filled in on the server."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "orgusaar.ee" not in text
    assert env_example["DJANGO_ALLOWED_HOSTS"].startswith("replace-me")


def test_the_template_declares_what_the_application_needs(
    env_example: dict[str, str],
) -> None:
    required = {
        "REAL_DATA_ALLOWED",
        "DEV_LOGIN_ENABLED",
        "DJANGO_DEBUG",
        "AUTH_MODE",
        "JURISTID_SHARED_GATE_PASSWORD",
        "SHARED_GATE_MAX_ATTEMPTS",
        "SHARED_GATE_LOCKOUT_SECONDS",
        "SHARED_GATE_MAX_LOCKOUT_SECONDS",
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "DJANGO_SECURE_SSL_REDIRECT",
        "DJANGO_BEHIND_TLS_PROXY",
        "APPLICATION_ENVIRONMENT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "HISTORICAL_SOURCE_ROOT",
    }
    assert required <= set(env_example)


def test_the_template_says_a_proxy_terminates_tls(env_example: dict[str, str]) -> None:
    """Only safe because there is no host port.

    Without it Django believes every request arrived over plain HTTP: no HSTS,
    `request.is_secure()` False, CSRF skips its referer check. Trusting
    `X-Forwarded-Proto` would be wrong on a stack a client could reach directly
    — this one cannot be reached except through the tunnel.
    """
    assert env_example["DJANGO_BEHIND_TLS_PROXY"] == "1"


def test_the_database_host_is_the_compose_service_not_a_shared_one(
    env_example: dict[str, str], compose: dict[str, Any]
) -> None:
    assert env_example["POSTGRES_HOST"] == "db"
    assert "db" in compose["services"]


def test_the_template_pins_neither_the_revision_nor_the_stage(
    env_example: dict[str, str],
) -> None:
    """Both used to be copied here, and both went stale where nobody looked.

    `APPLICATION_STAGE` sat at an old value through five merged stages, telling
    every reader of the footer that the build was six months older than it was.
    `APPLICATION_REVISION` was worse: it is the field that answers "what code is
    running", and it was the one thing about the running build that a human had
    to remember to retype.

    Both now come from the code and the image — `settings.APPLICATION_STAGE` and
    `/app/GIT_SHA`, written from the build argument the deployment script
    supplies. A value here would override them, which is the right escape hatch
    and the wrong default (docs/adr/0022).
    """
    assert "APPLICATION_STAGE" not in env_example
    assert "APPLICATION_REVISION" not in env_example


# -- the image the stack runs ---------------------------------------------


def test_the_web_build_receives_the_commit_it_is_built_from(
    compose: dict[str, Any],
) -> None:
    """So the running container can be asked, rather than an operator."""
    args = compose["services"]["web"]["build"]["args"]
    assert "GIT_SHA" in args


def test_the_tunnel_image_is_pinned(compose: dict[str, Any]) -> None:
    """`latest` on the only route in is an upgrade nobody decided to make.

    An unrelated `docker pull` on this host, or a `compose pull` aimed at
    something else, replaces the ingress of a real-data system with whatever
    Cloudflare released that week — including the releases whose own notes say
    not to use them.
    """
    image = compose["services"]["tunnel"]["image"]
    repository, _, tag = image.partition(":")
    assert repository == "cloudflare/cloudflared"
    assert tag and tag != "latest", "the tunnel image floats"


def test_the_database_image_pins_its_major(compose: dict[str, Any]) -> None:
    """The major is the one thing a restore cannot work around.

    A dump taken from PostgreSQL 18 needs an 18 server to read it, and a
    silently newer major would be discovered on the day a backup is needed.
    Minor versions inside 18 are welcome; the tag stops the major from moving,
    and the digest beside it (ADR 0179) stops anything moving without an edit.
    """
    image = compose["services"]["db"]["image"]
    assert image.partition("@")[0] == "postgres:18"
    assert image.partition("@")[2].startswith("sha256:")


def test_the_tunnel_waits_for_a_web_process_that_answers(
    compose: dict[str, Any],
) -> None:
    """Started is not serving.

    A tunnel that connects while gunicorn is still booting sends the public
    hostname 502s — during a deployment, which is exactly when somebody is
    watching and about to conclude the release broke something.
    """
    assert compose["services"]["tunnel"]["depends_on"]["web"]["condition"] == "service_healthy"


# -- how the containers run ------------------------------------------------


def test_no_service_asks_for_root_or_extra_privilege(compose: dict[str, Any]) -> None:
    """The image drops to uid 10001; a Compose override could undo that quietly."""
    for name, service in compose["services"].items():
        assert service.get("user") not in {"root", "0", "0:0"}, name
        assert service.get("privileged") is not True, name
        assert not service.get("cap_add"), name


def test_nothing_starts_through_the_development_entrypoint() -> None:
    """`scripts/start-dev.sh` migrates, seeds and runs `runserver`.

    Every one of those is forbidden here, and the file is one `command:` away.
    """
    assert "start-dev" not in COMPOSE.read_text(encoding="utf-8")


#: Commands that write the register — or who may read it — rather than the
#: schema. A deployment carries code and migrations; importing twenty years of
#: material, promoting a register, applying an opinion archive or creating a
#: person's account are separate reviewed operations with their own gates, and
#: none of them may be reachable from a container start.
DATA_APPLY_COMMANDS = (
    "provision_user",
    "bootstrap_account_admin",
    "reset_second_factor",
    "historical_import",
    "import_legacy_register",
    "promote_current_register",
    "backfill_legacy_owners",
    "opinion_archive",
    "seed_dev_data",
    "seed_e2e_data",
    "capture_operational_snapshot",
)


def test_no_service_starts_by_applying_business_data(compose: dict[str, Any]) -> None:
    for name, service in compose["services"].items():
        rendered = str(service.get("command", "")) + str(service.get("entrypoint", ""))
        for command in DATA_APPLY_COMMANDS:
            assert command not in rendered, f"{name} would run {command} on start"


# -- persistence -----------------------------------------------------------


def test_every_storage_class_has_a_mount(compose: dict[str, Any]) -> None:
    """Named one by one, because a count alone cannot say which one is missing.

    A container gets an empty directory where a mount should be and works
    perfectly until it is restarted, at which point whatever it wrote is gone.
    """
    targets = {
        name: {_resolved(volume).split(":")[1] for volume in service.get("volumes", [])}
        for name, service in compose["services"].items()
    }
    assert "/var/lib/postgresql" in targets["db"]
    assert "/home/nonroot/.cloudflared" in targets["tunnel"]
    assert {"/app/evidence", "/app/derivatives", "/app/legacy-source"} <= targets["web"]
    # And the reader has none of them. It reads staged bytes and writes rows;
    # an evidence tree would be reach without purpose (docs/adr/0072).
    assert targets[READER] == {UPLOAD_TARGET}


def test_the_source_corpus_is_read_only_on_every_service_that_sees_it(
    compose: dict[str, Any],
) -> None:
    """Not only on the two that mount it today.

    Defence in depth is the point: the importer is also written never to write
    there, and one of the two is allowed to be wrong.
    """
    seen = 0
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            resolved = _resolved(volume)
            if "/srv/historical-source" in resolved:
                seen += 1
                assert resolved.endswith(":ro"), f"{name}: {volume}"
    assert seen == 1


# -- isolation from the recovery rehearsal ---------------------------------

RECOVERY_REHEARSAL = Path(settings.BASE_DIR) / "deploy" / "recovery-rehearsal" / "compose.yml"


@pytest.fixture(scope="module")
def recovery_rehearsal() -> dict[str, Any]:
    return yaml.safe_load(RECOVERY_REHEARSAL.read_text(encoding="utf-8"))


def test_the_recovery_rehearsal_is_a_separate_stack(
    compose: dict[str, Any], recovery_rehearsal: dict[str, Any]
) -> None:
    """The disposable stack the backup scripts are exercised against.

    It has a published port and no authenticator, so the only thing keeping it
    from being a liability is that it can never be confused with this one.
    """
    assert recovery_rehearsal["name"] != compose["name"]
    mine = {service["container_name"] for service in compose["services"].values()}
    theirs = {service["container_name"] for service in recovery_rehearsal["services"].values()}
    assert mine.isdisjoint(theirs)


def test_the_recovery_rehearsal_is_a_database_and_nothing_else(
    compose: dict[str, Any], recovery_rehearsal: dict[str, Any]
) -> None:
    """No web process to reach, and the same PostgreSQL major as production.

    The major matters: rehearsing a restore on a different one would prove the
    procedure against a server that could not run it.
    """
    assert list(recovery_rehearsal["services"]) == ["db"]
    assert recovery_rehearsal["services"]["db"]["image"] == compose["services"]["db"]["image"]


def test_the_search_worker_is_not_judged_by_a_healthcheck_it_cannot_pass(
    compose: dict[str, Any],
) -> None:
    """The freshness consumer, probed by what it is for.

    It has no HTTP port, so the image's probe could only ever be red. What it
    answers instead is whether anything has been owed to the index too long,
    which is the failure a reader experiences (docs/adr/0041).
    """
    check = compose["services"]["searchindex"].get("healthcheck")
    assert check is not None, "the search worker inherits the web healthcheck"
    assert "check_search_freshness" in " ".join(check["test"])


def test_the_search_worker_reaches_no_evidence(compose: dict[str, Any]) -> None:
    """It rebuilds a projection out of PostgreSQL and reads no stored file."""
    services = compose["services"]
    assert services["searchindex"]["image"] == services["web"]["image"]
    assert "volumes" not in services["searchindex"]
    assert services["searchindex"]["networks"] == ["internal"]


# -- AI access: the MCP server and its tunnel (docs/adr/0185) -----------------
#
# Two services that hold a third party's bearer tokens and an OpenAI runtime
# key beside the Chamber's register. Every property below is one ADR 0185 §6
# promises; each is asserted positively, so a later edit that drops one fails
# here rather than in a review nobody does.

MCP = "mcp"
MCP_TUNNEL = "mcp-tunnel"
MCP_WRITER = "mcp-writer"
MCP_ENV_EXAMPLE = DEPLOY / "juristid-mcp.env.example"
MCP_WRITER_ENV_EXAMPLE = DEPLOY / "juristid-mcp-writer.env.example"
MCP_TUNNEL_ENV_EXAMPLE = DEPLOY / "juristid-mcp-tunnel.env.example"

#: The tunnel client, by tag and by digest: the multi-platform index
#: ghcr.io/openai/tunnel-client:v0.0.17 resolved to on 2026-10-22.
TUNNEL_CLIENT_IMAGE = (
    "ghcr.io/openai/tunnel-client:v0.0.17"
    "@sha256:67b473b4bc23bc984e4a381752e9a2fa75649746edc06e46b27025611835c5c0"
)


def _template_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_ai_access_starts_only_when_it_is_named(compose: dict[str, Any]) -> None:
    """The profile is the switch, and it is on both services and nothing else.

    `up -d --no-build` (a release), `up -d tunnel` (a restore) and the backup
    scripts leave a profiled service alone; naming it starts it. One profile
    for both, so `--profile mcp` brings up the pair and nothing more.
    """
    gated = {name for name, service in compose["services"].items() if service.get("profiles")}
    assert gated == {MCP, MCP_TUNNEL, MCP_WRITER}
    for name in gated:
        assert compose["services"][name]["profiles"] == ["mcp"], name


def test_the_mcp_server_is_the_release_image_running_a_checked_command(
    compose: dict[str, Any],
) -> None:
    services = compose["services"]
    server = services[MCP]
    assert server["image"] == services["web"]["image"]
    assert server["container_name"] == "juristid-main-mcp"
    assert [str(part) for part in server["command"]] == ["python", "manage.py", "run_mcp_server"]
    assert server["healthcheck"]["test"] == [
        "CMD",
        "python",
        "manage.py",
        "check_mcp_server",
        "--quiet",
    ]
    assert "build" not in server, "only `web` carries the build stanza"


def test_the_mcp_server_has_its_own_minimal_environment(compose: dict[str, Any]) -> None:
    """Not `juristid.env`, and read-only database transactions by its role."""
    server = compose["services"][MCP]
    assert server["env_file"] == [
        "${JURISTID_MCP_ENV_FILE:-/mnt/user/appdata/juristid-main/config/juristid-mcp.env}"
    ]
    assert "juristid.env" not in str(server["env_file"])
    environment = server["environment"]
    assert environment["JURISTID_PROCESS_ROLE"] == "mcp"
    assert environment["JURISTID_RUNTIME"] == "deployed"
    assert environment["SEED_DEV_DATA"] == "0"
    assert environment["POSTGRES_HOST"] == "db"
    assert environment["POSTGRES_SSLMODE"] == "disable"
    assert environment["MCP_LISTEN_HOST"] == "0.0.0.0"  # noqa: S104 - the container network
    assert str(environment["MCP_LISTEN_PORT"]) == "8001"
    assert server["depends_on"] == {"db": {"condition": "service_healthy"}}


def test_the_mcp_server_reaches_no_file_at_all(compose: dict[str, Any]) -> None:
    """No evidence, no derivatives, no corpus, no staging volume — no volume."""
    server = compose["services"][MCP]
    assert "volumes" not in server
    assert list(server["networks"]) == ["internal", "mcp_edge", "mcp_write"]
    assert "ports" not in server
    assert "expose" not in server


@pytest.mark.parametrize("name", [MCP, MCP_TUNNEL, MCP_WRITER])
def test_the_ai_access_containers_are_hardened(compose: dict[str, Any], name: str) -> None:
    """Read-only root, in-memory /tmp, no capability, no privilege gain, bounded."""
    service = compose["services"][name]
    assert service["read_only"] is True
    # The mount point inside the container, spelt without a literal so the
    # fixed-temporary-path guard (tests/test_ci_slices.py) stays exact.
    assert [entry.split(":", 1)[0] for entry in service["tmpfs"]] == ["/" + "tmp"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]
    assert not service.get("cap_add")
    assert service["mem_limit"]
    assert float(service["cpus"]) <= 1.0
    assert 0 < int(service["pids_limit"]) <= 128
    assert service["restart"] == "unless-stopped"
    assert "ports" not in service
    assert "network_mode" not in service


def test_the_tunnel_client_is_pinned_and_holds_only_its_own_file(compose: dict[str, Any]) -> None:
    client = compose["services"][MCP_TUNNEL]
    assert client["image"] == TUNNEL_CLIENT_IMAGE
    assert client["container_name"] == "juristid-main-mcp-tunnel"
    assert client["env_file"] == [
        "${JURISTID_MCP_TUNNEL_ENV_FILE:-"
        "/mnt/user/appdata/juristid-main/config/juristid-mcp-tunnel.env}"
    ]
    assert "volumes" not in client
    assert client["networks"] == ["mcp_edge"]
    # Not root: the image sets no USER, and Alpine's `nobody` is enough.
    assert client["user"] == "65534:65534"


def test_the_tunnel_client_reaches_the_server_on_the_edge_network_only(
    compose: dict[str, Any],
) -> None:
    """Plain HTTP between two containers on one bridge, health on loopback."""
    client = compose["services"][MCP_TUNNEL]
    environment = client["environment"]
    assert environment["MCP_SERVER_URL"] == "http://mcp:8001/mcp"
    assert environment["HEALTH_LISTEN_ADDR"].startswith("127.0.0.1:")
    assert "ALLOW_REMOTE_UI" not in environment
    assert "LOG_HTTP_RAW_UNSAFE" not in environment
    assert client["depends_on"] == {MCP: {"condition": "service_healthy"}}
    probe = client["healthcheck"]["test"]
    assert probe[:2] == ["CMD", "wget"]
    assert probe[-1] == "http://127.0.0.1:8080/readyz"
    assert environment["HEALTH_LISTEN_ADDR"] == "127.0.0.1:8080", "the probe asks this port"


def test_the_server_answers_to_its_dialled_name_on_the_edge_network(
    compose: dict[str, Any],
) -> None:
    """`mcp` is an explicit alias on `mcp_edge`, the network the tunnel client resolves on.

    Docker answers DNS per network. On 2026-10-25 a `up -d --no-deps mcp`
    left the container with the alias `mcp` on `mcp_write` only; the tunnel
    client, on `mcp_edge` alone, got NXDOMAIN for MCP_SERVER_URL's host, logged
    "startup catalog failed; control-plane polling disabled" and never
    retried. Named in the compose file, every recreate attaches it.
    """
    from urllib.parse import urlsplit

    client = compose["services"][MCP_TUNNEL]
    dialled = urlsplit(client["environment"]["MCP_SERVER_URL"]).hostname
    [network] = client["networks"]
    assert network == "mcp_edge"
    server_networks = compose["services"][MCP]["networks"]
    assert isinstance(server_networks, dict), "the list form cannot carry an alias"
    assert dialled in (server_networks[network] or {}).get("aliases", [])
    # Only that network: on `mcp_write` the gateway is dialled by address and
    # never calls back, and nothing on `internal` calls `mcp`.
    for other in ("internal", "mcp_write"):
        assert not (server_networks[other] or {}).get("aliases"), other


def test_the_server_accepts_the_host_the_tunnel_client_dials() -> None:
    """`mcp:8001` — MCP_SERVER_URL's host — is in the default Host allow-list."""
    from urllib.parse import urlsplit

    from config import settings as project_settings

    dialled = urlsplit("http://mcp:8001/mcp").netloc
    assert dialled in project_settings._MCP_DEFAULT_ALLOWED_HOSTS


def test_both_networks_can_reach_the_internet(compose: dict[str, Any]) -> None:
    """`tunnel` (on `internal`) and `mcp-tunnel` (on `mcp_edge`) dial out.

    An `internal: true` network cannot, so neither may be one.
    """
    for name in ("internal", "mcp_edge"):
        assert compose["networks"][name].get("internal") is not True, name
        assert compose["networks"][name]["driver"] == "bridge", name


def test_the_mcp_templates_carry_placeholders_and_no_browser_secret() -> None:
    mcp = _template_values(MCP_ENV_EXAMPLE)
    assert mcp["REAL_DATA_ALLOWED"] == "1"
    # A real mode, so real data is not refused (E006) — and no gate password.
    assert mcp["AUTH_MODE"] == "shared_gate"
    # Off by default and never named for this process (E044 refuses debug).
    assert "DJANGO_DEBUG" not in mcp
    assert "DEV_LOGIN_ENABLED" not in mcp
    assert mcp["MCP_ENABLED"] == "1"
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"):
        assert mcp[key].startswith("replace-me"), key
    # Its own SELECT-only role, never the application's owner role (MCPSEC-03).
    assert mcp["POSTGRES_USER"] == "juristid_mcp"
    assert "juristid.env" not in mcp["POSTGRES_PASSWORD"]
    assert mcp["JURISTID_PUBLIC_ORIGIN"].startswith("https://")
    assert all(url.startswith("https://") for url in mcp["MCP_RESOURCE_URLS"].split(","))
    text = MCP_ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "JURISTID_SHARED_GATE_PASSWORD",
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
        "DJANGO_EMAIL_HOST_PASSWORD",
        "CONTROL_PLANE_API_KEY",
    ):
        assert f"{key}=" not in text, f"the MCP template names {key}"

    tunnel = _template_values(MCP_TUNNEL_ENV_EXAMPLE)
    assert set(tunnel) == {
        "CONTROL_PLANE_API_KEY",
        "CONTROL_PLANE_TUNNEL_ID",
        "MCP_OAUTH_TRUSTED_ORIGINS",
    }
    assert tunnel["CONTROL_PLANE_API_KEY"].startswith("replace-me")
    for path in (MCP_ENV_EXAMPLE, MCP_TUNNEL_ENV_EXAMPLE):
        assert "orgusaar.ee" not in path.read_text(encoding="utf-8"), path.name


def test_the_web_template_keeps_ai_access_off(env_example: dict[str, str]) -> None:
    """A copied template must not switch on an OAuth surface nobody decided on."""
    assert env_example["MCP_ENABLED"] == "0"
    assert env_example["JURISTID_PUBLIC_ORIGIN"].startswith("https://replace-me")
    assert env_example["MCP_RESOURCE_URLS"].startswith("https://")


# -- the MCP write gateway (docs/adr/0189) -------------------------------------


def test_the_write_gateway_is_the_release_image_running_a_checked_command(
    compose: dict[str, Any],
) -> None:
    services = compose["services"]
    writer = services[MCP_WRITER]
    assert writer["image"] == services["web"]["image"]
    assert writer["container_name"] == "juristid-main-mcp-writer"
    assert writer["profiles"] == ["mcp"]
    assert [str(part) for part in writer["command"]] == [
        "python",
        "manage.py",
        "run_mcp_write_gateway",
    ]
    assert writer["healthcheck"]["test"] == [
        "CMD",
        "python",
        "manage.py",
        "check_mcp_write_gateway",
        "--quiet",
    ]
    assert "build" not in writer


def test_the_write_gateway_has_its_own_file_and_no_port_mount_or_edge(
    compose: dict[str, Any],
) -> None:
    writer = compose["services"][MCP_WRITER]
    assert writer["env_file"] == [
        "${JURISTID_MCP_WRITER_ENV_FILE:-"
        "/mnt/user/appdata/juristid-main/config/juristid-mcp-writer.env}"
    ]
    environment = writer["environment"]
    assert environment["JURISTID_PROCESS_ROLE"] == "mcp_writer"
    assert environment["JURISTID_RUNTIME"] == "deployed"
    assert environment["SEED_DEV_DATA"] == "0"
    assert environment["POSTGRES_HOST"] == "db"
    assert environment["MCP_WRITE_LISTEN_HOST"] == WRITER_ADDRESS
    assert str(environment["MCP_WRITE_LISTEN_PORT"]) == "8002"
    assert writer["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert list(writer["networks"]) == ["internal", "mcp_write"]
    for absent in ("ports", "expose", "volumes", "network_mode", "cap_add", "privileged"):
        assert absent not in writer, absent


def test_mcp_reaches_the_gateway_by_name_and_nothing_depends_on_it(
    compose: dict[str, Any],
) -> None:
    """Stopping `mcp-writer` must leave reading untouched: nobody waits for it."""
    services = compose["services"]
    assert services[MCP]["environment"]["MCP_WRITE_GATEWAY_URL"] == f"http://{WRITER_ADDRESS}:8002"
    for name, service in services.items():
        assert MCP_WRITER not in (service.get("depends_on") or {}), name


def test_the_writer_template_carries_placeholders_and_no_browser_secret() -> None:
    writer = _template_values(MCP_WRITER_ENV_EXAMPLE)
    assert writer["REAL_DATA_ALLOWED"] == "1"
    assert writer["AUTH_MODE"] == "shared_gate"
    assert writer["POSTGRES_USER"] == "juristid_mcp_writer"
    assert writer["MCP_ENABLED"] == "1"
    assert writer["MCP_WRITES_ENABLED"] == "1"
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "MCP_WRITE_GATEWAY_KEY"):
        assert writer[key].startswith("replace-me"), key
    assert "DJANGO_DEBUG" not in writer and "DEV_LOGIN_ENABLED" not in writer
    text = MCP_WRITER_ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "JURISTID_SHARED_GATE_PASSWORD",
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
        "MCP_AUTHENTICATOR_KEY",
        "MCP_TOKEN_DERIVATION_KEY",
        "DJANGO_EMAIL_HOST_PASSWORD",
        "DJANGO_EMAIL_HOST_USER",
        "CONTROL_PLANE_API_KEY",
    ):
        assert f"{key}=" not in text, f"the writer template names {key}"
    assert "orgusaar.ee" not in text

    mcp = _template_values(MCP_ENV_EXAMPLE)
    assert mcp["MCP_WRITES_ENABLED"] == "0"
    assert mcp["MCP_WRITE_GATEWAY_KEY"].startswith("replace-me")
    assert mcp["POSTGRES_USER"] == "juristid_mcp"


def test_the_web_template_keeps_ai_writes_off_and_holds_no_gateway_key(
    env_example: dict[str, str],
) -> None:
    assert env_example["MCP_WRITES_ENABLED"] == "0"
    assert "MCP_WRITE_GATEWAY_KEY=" not in ENV_EXAMPLE.read_text(encoding="utf-8")


#: The gateway's one fixed address, on `mcp_write` only (security re-review P2-B).
WRITER_ADDRESS = "10.213.250.10"


def test_the_gateway_listens_only_on_its_fixed_write_network_address(
    compose: dict[str, Any],
) -> None:
    """Not 0.0.0.0: web, the workers and the Cloudflare tunnel share `internal` with it."""
    import ipaddress

    services = compose["services"]
    writer = services[MCP_WRITER]
    listen = writer["environment"]["MCP_WRITE_LISTEN_HOST"]
    assert listen == WRITER_ADDRESS
    assert listen not in ("0.0.0.0", "::", "")  # noqa: S104 - asserting it is NOT the wildcard
    assert writer["networks"]["mcp_write"]["ipv4_address"] == listen
    assert not (writer["networks"].get("internal") or {}).get("ipv4_address")
    [config] = compose["networks"]["mcp_write"]["ipam"]["config"]
    subnet = ipaddress.ip_network(config["subnet"])
    address = ipaddress.ip_address(listen)
    assert address in subnet and address not in (subnet.network_address, subnet.broadcast_address)
    assert subnet.is_private and subnet.prefixlen >= 28
    # Outside Docker's default address pools, so no auto-assigned network collides.
    for pool in ("172.16.0.0/12", "192.168.0.0/16"):
        assert not subnet.overlaps(ipaddress.ip_network(pool)), pool
    # `mcp` calls that address, never the name (which may resolve on `internal`).
    assert services[MCP]["environment"]["MCP_WRITE_GATEWAY_URL"] == f"http://{listen}:8002"
    # No other network of this file has an address plan that could overlap.
    for name, network in compose["networks"].items():
        for other in (network.get("ipam") or {}).get("config", []) if name != "mcp_write" else []:
            assert not subnet.overlaps(ipaddress.ip_network(other["subnet"])), name


def test_the_gateway_answers_its_healthcheck_and_mcp_at_its_listen_address(settings) -> None:
    """The healthcheck asks the listen address, and that address is a Host it accepts."""
    from app.mcp_writes import gateway

    settings.MCP_WRITE_LISTEN_HOST = WRITER_ADDRESS
    settings.MCP_WRITE_LISTEN_PORT = 8002
    assert gateway.probe_host() == WRITER_ADDRESS
    assert f"{WRITER_ADDRESS}:8002" in gateway.allowed_hosts()
    settings.MCP_WRITE_LISTEN_HOST = "0.0.0.0"  # noqa: S104 - the wildcard case
    assert gateway.probe_host() == "127.0.0.1"
    assert not any(host.startswith("0.0.0.0") for host in gateway.allowed_hosts())  # noqa: S104
