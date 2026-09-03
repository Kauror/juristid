"""The real-data recovery rehearsal stack, and why every affordance is absent.

`deploy/recovery-rehearsal/compose.yml` is the *synthetic* rehearsal: it
publishes a host port and embeds a password, and its header says nothing real
may ever be pointed at it. `compose.real-data.yml` beside it is the other half —
the stack a genuine production backup set is restored into — and it holds the
Chamber's register, its evidence and its members' material for as long as the
rehearsal runs.

That inverts every convenience. A rehearsal stack is short-lived, it is built
under time pressure, and on 03.09.2026 the first one had to be invented from
scratch outside the repository because no template existed. The next person to
do it will be in a hurry too, so the properties that keep it safe have to be
properties of the file rather than things they remember.

These tests are that file's contract. They are cheap, they need no Docker, and
each one names the failure it exists to prevent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
REHEARSAL = ROOT / "deploy" / "recovery-rehearsal"
REAL_DATA = REHEARSAL / "compose.real-data.yml"
SOURCE_OVERLAY = REHEARSAL / "compose.real-data.source.yml"
SYNTHETIC = REHEARSAL / "compose.yml"
ENV_EXAMPLE = REHEARSAL / "real-data.env.example"

#: The project names `scripts/deploy/lib.sh` accepts. Duplicated here on
#: purpose: if somebody renames one, this test and the script disagree loudly
#: rather than the rehearsal silently becoming unrunnable.
PRODUCTION_PROJECT = "juristid-main"
REHEARSAL_PROJECT = "juristid-recovery-rehearsal"

#: Paths a rehearsal may never bind writable. The source corpus is absent from
#: this list because the overlay mounts it read-only, which is checked
#: separately.
PRODUCTION_PATHS = (
    "/mnt/user/appdata/juristid-main",
    "/mnt/user/backups/juristid-main",
)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(REAL_DATA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def overlay() -> dict[str, Any]:
    return yaml.safe_load(SOURCE_OVERLAY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return compose["services"]


def _split_volume(volume: str) -> list[str]:
    """Split a bind mount into `host`, `container` and optional `mode`.

    Not `str.split(":")`. A required-variable interpolation — `${VAR:?why}` —
    carries colons of its own, so the naive split hands back half an
    interpolation as the host path. Every substring check downstream then looks
    at the wrong string and passes, which is the worst way for a guard to fail.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in volume:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == ":" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _host_path(volume: str) -> str:
    """The host side of a `host:container[:mode]` bind mount."""
    return _split_volume(volume)[0]


# -- it exists, and it says what it is -------------------------------------


def test_the_template_exists() -> None:
    """The whole point: the next rehearsal does not invent a compose file.

    The 03.09.2026 one did, outside the repository, under time pressure, and
    the result was correct only because it was checked line by line afterwards.
    """
    assert REAL_DATA.exists()
    assert SOURCE_OVERLAY.exists()
    assert ENV_EXAMPLE.exists()


@pytest.mark.parametrize("path", [REAL_DATA, SOURCE_OVERLAY, ENV_EXAMPLE], ids=lambda p: p.name)
def test_every_real_data_file_announces_what_it_is_for(path: Path) -> None:
    """A file that holds member material must say so in its first lines.

    Somebody skimming `deploy/` looking for a stack to reuse should not have to
    read the whole header to find out that this one is not a deployment.
    """
    head = path.read_text(encoding="utf-8")[:600].upper()
    assert "FOR ISOLATED RECOVERY REHEARSAL ONLY" in head, f"{path.name}: unmarked"


def test_the_synthetic_rehearsal_still_says_it_is_synthetic() -> None:
    """Adding a real-data sibling must not blur what the original was for.

    The two files now live in the same directory and differ by one filename
    fragment. The older one's warning is the thing that stops somebody pointing
    a production set at the stack with a published port.
    """
    assert "SYNTHETIC DATA ONLY" in SYNTHETIC.read_text(encoding="utf-8")


# -- it cannot be aimed at production --------------------------------------


def test_the_project_name_is_the_rehearsal_one(compose: dict[str, Any]) -> None:
    """`lib.sh` accepts exactly two projects and refuses everything else.

    Naming the rehearsal project in the file means an operator who forgets
    `-p` still cannot land on `juristid-main`.
    """
    assert compose["name"] == REHEARSAL_PROJECT
    assert compose["name"] != PRODUCTION_PROJECT


def test_the_deploy_scripts_still_accept_this_project_and_refuse_the_test_stack() -> None:
    """The rehearsal proves the production scripts, so they must take it.

    And `juristid-test` must stay refused by name: it holds the usability-test
    history, it is the stack most easily confused with this one, and a rehearsal
    that could reach it would destroy something irreplaceable to prove a backup.
    """
    lib = (ROOT / "scripts" / "deploy" / "lib.sh").read_text(encoding="utf-8")
    assert f'JURISTID_PRODUCTION_PROJECT="{PRODUCTION_PROJECT}"' in lib
    assert f'JURISTID_REHEARSAL_PROJECT="{REHEARSAL_PROJECT}"' in lib
    assert "juristid-test)" in lib, "the test stack is no longer refused by name"


def test_no_container_name_collides_with_another_stack(compose: dict[str, Any]) -> None:
    """Two stacks that agree on a container name cannot both run.

    The synthetic rehearsal already claims `juristid-recovery-rehearsal-db`, and
    a collision would either fail confusingly or attach to the wrong container.
    """
    names = {service["container_name"] for service in _services(compose).values()}
    synthetic = yaml.safe_load(SYNTHETIC.read_text(encoding="utf-8"))
    taken = {service["container_name"] for service in synthetic["services"].values()}
    assert not names & taken, f"collides with the synthetic rehearsal: {sorted(names & taken)}"
    for name in names:
        assert not name.startswith("juristid-main-"), f"{name} looks like production"
        assert not name.startswith("juristid-test-"), f"{name} looks like the test stack"


def test_no_mount_has_a_default_that_could_resolve_into_production(
    compose: dict[str, Any],
) -> None:
    """Every writable path must be one the operator typed.

    A `${VAR:-/mnt/user/appdata/juristid-main}` would make a forgotten export
    restore *over production*. `:?` makes Compose stop instead, which is the
    only outcome that is safe when the variable is missing.
    """
    for name, service in _services(compose).items():
        for volume in service.get("volumes", []):
            host = _host_path(volume)
            assert ":?" in host, (
                f"{name}: `{host}` has no required-variable guard; a missing "
                f"export must stop Compose, not fall back to a path"
            )
            assert ":-" not in host, f"{name}: `{host}` carries a default path"
            for forbidden in PRODUCTION_PATHS:
                assert forbidden not in host, f"{name}: mounts production at `{host}`"


def test_the_image_tag_has_no_fallback(compose: dict[str, Any]) -> None:
    """A rehearsal must restore into the release it intends to come back on.

    `:-local` would let one pass against an image nobody deployed, which is the
    single most misleading way for this exercise to succeed.
    """
    for name, service in _services(compose).items():
        image = service["image"]
        if not image.startswith("juristid-main-web:"):
            continue
        assert ":?" in image, f"{name}: the image tag needs a required variable"
        assert ":-" not in image, f"{name}: the image tag has a fallback"


# -- nothing can reach it, and it can reach nothing ------------------------


def test_no_service_publishes_a_host_port(compose: dict[str, Any]) -> None:
    """The synthetic stack publishes 5432 because nothing real is in it.

    This one holds the register. A published port on a stack with no
    authenticator in front of it is the register on the host's network.
    """
    publishing = [name for name, service in _services(compose).items() if service.get("ports")]
    assert not publishing, f"these publish a port: {publishing}"


def test_the_network_is_internal_and_its_own(compose: dict[str, Any]) -> None:
    """`internal: true` removes the bridge's route off the host.

    Not defence in depth for its own sake: the stack holds member material and
    has no reason to talk to anything, so the failure this forecloses is an
    accidental egress nobody would have noticed.
    """
    networks = compose["networks"]
    assert list(networks) == ["rehearsal"]
    assert networks["rehearsal"]["internal"] is True
    assert networks["rehearsal"]["name"] == "juristid-recovery-rehearsal-internal"
    assert networks["rehearsal"].get("external") is not True
    for name, service in _services(compose).items():
        assert service.get("networks") == ["rehearsal"], f"{name}: not on the rehearsal network"


def test_the_stack_does_not_use_host_networking(compose: dict[str, Any]) -> None:
    """`network_mode: host` would undo `internal` and the absent ports at once."""
    for name, service in _services(compose).items():
        assert "network_mode" not in service, f"{name}: sets network_mode"


def test_there_is_no_tunnel(compose: dict[str, Any]) -> None:
    """A tunnel would publish the rehearsal to the internet under a real name."""
    for name, service in _services(compose).items():
        assert "cloudflare" not in service["image"], f"{name}: runs cloudflared"
    assert "tunnel" not in _services(compose)


def test_the_application_never_serves(compose: dict[str, Any]) -> None:
    """A stack that cannot answer a request cannot be reached by mistake.

    The rehearsal reads the restored data with `run --rm … manage.py …`, so
    gunicorn buys nothing and costs the one thing that matters here.
    """
    command = _services(compose)["web"]["command"]
    assert "gunicorn" not in " ".join(command), "the rehearsal must not run a server"


# -- nothing writes to the restored data unless asked ----------------------


def test_the_workers_are_behind_a_profile(compose: dict[str, Any]) -> None:
    """Both write to the restored database.

    The point of a restore rehearsal is to observe what came back. A worker that
    starts with `up -d` changes it before anybody looks, and the change is
    indistinguishable from something the backup got wrong.
    """
    for name in ("extractor", "searchindex"):
        service = _services(compose)[name]
        assert "workers" in service.get("profiles", []), f"{name}: would start on a plain `up`"


def test_the_database_and_the_application_are_not_behind_a_profile(
    compose: dict[str, Any],
) -> None:
    """Guards the guard above: profiling everything would make it vacuous."""
    for name in ("db", "web"):
        assert not _services(compose)[name].get("profiles"), f"{name}: unreachable by default"


def test_nothing_restarts_by_itself(compose: dict[str, Any]) -> None:
    """A rehearsal database that survives a reboot is a copy nobody deleted.

    The scratch tree holds real member material and its removal is manual. A
    `restart: unless-stopped` would quietly keep it serving that material for
    however long it takes somebody to notice.
    """
    for name, service in _services(compose).items():
        assert "restart" not in service, f"{name}: sets a restart policy"


def test_nothing_starts_by_running_migrations(compose: dict[str, Any]) -> None:
    """A restore is verified against the schema it came back with.

    A command that migrates on start would change that schema before
    `deployment_readiness` could report on it, and the report is the evidence.
    """
    for name, service in _services(compose).items():
        assert "migrate" not in " ".join(service.get("command", [])), f"{name}: migrates"


# -- the database is the one that can read the dump ------------------------


def test_the_database_is_postgresql_18(compose: dict[str, Any]) -> None:
    """A dump from 18 is restored by 18 (docs/adr/0022).

    An older major would prove the procedure against a server that could not run
    it; a newer one would prove a migration nobody has planned.
    """
    assert _services(compose)["db"]["image"] == "postgres:18"


def test_postgres_persists_where_the_18_image_actually_keeps_it(
    compose: dict[str, Any],
) -> None:
    """Production mounts the same parent, so the restore exercises the layout.

    A rehearsal that mounted a different path would restore into the image's
    own layer and lose the cluster on `down`, which looks like success right up
    until somebody trusts it.
    """
    targets = [volume.split(":")[-1] for volume in _services(compose)["db"]["volumes"]]
    assert "/var/lib/postgresql" in targets


# -- no secrets, anywhere --------------------------------------------------


def test_the_compose_file_embeds_no_secret(compose: dict[str, Any]) -> None:
    """The synthetic sibling embeds a password because it guards nothing.

    This file is committed and its stack holds the register, so the environment
    has to come from a file the operator generates and deletes.
    """
    for name, service in _services(compose).items():
        environment = service.get("environment", {}) or {}
        for key in environment:
            assert "PASSWORD" not in key.upper(), f"{name}: {key} is set in the file"
            assert "SECRET" not in key.upper(), f"{name}: {key} is set in the file"
        assert service.get("env_file"), f"{name}: no env_file, so where would settings come from"


def test_the_environment_template_carries_no_real_value(env_example: dict[str, str]) -> None:
    """Placeholders only, and obviously placeholders."""
    for key in ("POSTGRES_PASSWORD", "DJANGO_SECRET_KEY", "JURISTID_SHARED_GATE_PASSWORD"):
        assert env_example[key].startswith("replace-me"), f"{key} looks like a real value"


def test_the_environment_template_keeps_the_real_data_guards(
    env_example: dict[str, str],
) -> None:
    """The stack holds the real register, so production's four safety lines apply.

    `SEED_DEV_DATA` matters more here than anywhere: rows written after a
    restore are rows that did not come out of the backup set, and a seeded
    rehearsal reports a fingerprint that no set could ever produce.
    """
    assert env_example["REAL_DATA_ALLOWED"] == "1"
    assert env_example["DEV_LOGIN_ENABLED"] == "0"
    assert env_example["DJANGO_DEBUG"] == "0"
    assert env_example["SEED_DEV_DATA"] == "0"


def test_the_environment_template_names_no_real_host(env_example: dict[str, str]) -> None:
    """Nothing resolves to this stack and nothing should."""
    hosts = {host.strip() for host in env_example["DJANGO_ALLOWED_HOSTS"].split(",")}
    assert not any(host.endswith("orgusaar.ee") for host in hosts), hosts


# -- the source corpus is opt-in, and read-only ----------------------------


def test_the_base_stack_does_not_mount_the_source_corpus(compose: dict[str, Any]) -> None:
    """The corpus is not in a backup set, so a restore does not recover it.

    Mounting it by default would let a rehearsal report a working application
    without anybody noticing that one of its inputs came from production rather
    than from the set under test.
    """
    for name, service in _services(compose).items():
        for volume in service.get("volumes", []):
            assert "/srv/historical-source" not in volume, f"{name}: mounts the corpus"
        environment = service.get("environment", {}) or {}
        assert "HISTORICAL_SOURCE_ROOT" not in environment, f"{name}: declares the corpus"


def test_the_overlay_mounts_the_corpus_read_only(overlay: dict[str, Any]) -> None:
    """The one production path a rehearsal may touch, and only in this direction.

    Read-write would put the only copy of a 4 GiB manual export behind a stack
    that exists to be deleted.
    """
    volumes = overlay["services"]["web"]["volumes"]
    assert len(volumes) == 1
    volume = volumes[0]
    assert _split_volume(volume)[-1] == "ro", f"the corpus must be read-only: {volume}"
    assert ":?" in _host_path(volume), "the corpus path must be supplied, not defaulted"


def test_the_overlay_adds_nothing_but_the_corpus(overlay: dict[str, Any]) -> None:
    """An overlay that also changed ports or networks would be a second stack.

    Keeping it to one mount and one variable is what makes reading the base file
    enough to know what the rehearsal is.
    """
    assert list(overlay["services"]) == ["web"]
    assert set(overlay["services"]["web"]) <= {"environment", "volumes"}
    assert "networks" not in overlay
    assert overlay["services"]["web"]["environment"] == {
        "HISTORICAL_SOURCE_ROOT": "/srv/historical-source"
    }
