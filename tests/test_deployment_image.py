"""The image is the deployment unit, so what it promises is checked like code.

CI builds it and runs a stack against it, which proves it works. These tests ask
a different question: whether it still has the properties the deployment depends
on. A build that succeeds while running as root, or while its healthcheck calls
a binary that is no longer installed, is a working image and a broken deployment.

The Dockerfile is read as text. No Docker, no build, no network — the whole file
runs on a laptop in milliseconds, which is the only reason anybody keeps it
green.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings

DOCKERFILE = Path(settings.BASE_DIR) / "Dockerfile"
PRODUCTION_COMPOSE = Path(settings.BASE_DIR) / "deploy" / "unraid-main" / "compose.yml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def instructions(dockerfile: str) -> list[tuple[str, str]]:
    """Every instruction, as (verb, argument), with comments and continuations gone."""
    joined = re.sub(r"\\\s*\n\s*", " ", dockerfile)
    parsed: list[tuple[str, str]] = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        verb, _, argument = stripped.partition(" ")
        parsed.append((verb.upper(), argument.strip()))
    return parsed


def _last(instructions: list[tuple[str, str]], verb: str) -> str:
    values = [argument for name, argument in instructions if name == verb]
    assert values, f"the Dockerfile has no {verb}"
    return values[-1]


# -- who it runs as --------------------------------------------------------


def test_the_runtime_stage_drops_out_of_root(instructions: list[tuple[str, str]]) -> None:
    """A web process that can write to its own code can be made to.

    The evidence tree is mounted into this container; the difference between an
    application bug and a destroyed evidence store is often only which uid the
    process had.
    """
    user = _last(instructions, "USER")
    assert user not in {"root", "0", "0:0"}
    assert user == "juristid"


def test_the_application_user_has_a_fixed_uid(dockerfile: str) -> None:
    """10001, and it has to be stable.

    The evidence and derivative trees are bind mounts on the host, owned by that
    uid. A uid allocated by the base image at build time would change under a
    rebuild and lock the application out of its own storage — a failure that
    looks like a permissions bug and is really a version skew.
    """
    assert "--uid 10001" in dockerfile
    assert "--gid 10001" in dockerfile


def test_the_storage_directories_belong_to_the_application_user(dockerfile: str) -> None:
    """A named volume inherits the ownership of the image path it shadows."""
    assert "chown -R juristid:juristid /app" in dockerfile


# -- what it does on start -------------------------------------------------


def test_the_image_starts_gunicorn_and_nothing_else(
    instructions: list[tuple[str, str]],
) -> None:
    command = json.loads(_last(instructions, "CMD"))
    assert command[0] == "gunicorn"
    assert "runserver" not in " ".join(command)


def test_the_image_never_migrates_on_start(dockerfile: str) -> None:
    """Migrations are a controlled deployment step (master specification 24.2).

    On container start they would run on every restart — including the restart
    that happens at three in the morning because a host rebooted, with nobody
    watching and no backup taken.
    """
    for verb in ("CMD", "ENTRYPOINT"):
        for line in dockerfile.splitlines():
            if line.strip().startswith(verb):
                assert "migrate" not in line
                assert "manage.py" not in line


def test_the_image_has_no_entrypoint_that_could_wrap_the_command(
    instructions: list[tuple[str, str]],
) -> None:
    """An ENTRYPOINT is where migrate-on-boot comes back without anyone noticing."""
    assert not [argument for verb, argument in instructions if verb == "ENTRYPOINT"]


# -- the healthcheck -------------------------------------------------------


def test_the_healthcheck_uses_a_binary_the_image_actually_has(
    instructions: list[tuple[str, str]], dockerfile: str
) -> None:
    """The classic version of this defect is `curl` in an image without curl.

    A healthcheck whose command cannot run marks the container unhealthy for
    ever, and a container that is always red makes one that *becomes* red
    indistinguishable — the signal is gone rather than merely wrong.
    """
    healthcheck = _last(instructions, "HEALTHCHECK")
    assert "python" in healthcheck
    for absent in ("curl", "wget", "nc "):
        assert absent not in healthcheck
    # python is the interpreter the application runs on, so it is present by
    # construction; apt installs nothing that provides curl or wget.
    assert "curl" not in dockerfile


def test_the_healthcheck_asks_the_liveness_endpoint(
    instructions: list[tuple[str, str]],
) -> None:
    """`/healthz` is cheap and says "this process and its database answer".

    Deliberately not the deep check: `manage.py deployment_readiness` loads the
    migration graph and probes every mount, which has no business running every
    fifteen seconds, and reports things a public endpoint should not publish.
    """
    healthcheck = _last(instructions, "HEALTHCHECK")
    assert "/healthz" in healthcheck
    assert "127.0.0.1" in healthcheck


# -- build identity --------------------------------------------------------


def test_the_image_records_the_commit_it_was_built_from(dockerfile: str) -> None:
    """The one fact that answers "what code is this".

    A build time says when, an image tag says what somebody called it, and
    neither survives being wrong. Before this, the revision came from an
    environment variable a human had to remember to update.
    """
    assert "ARG GIT_SHA" in dockerfile
    assert "/app/GIT_SHA" in dockerfile


def test_the_image_records_when_it_was_built(dockerfile: str) -> None:
    assert "/app/BUILD_STAMP" in dockerfile


def test_static_assets_are_baked_with_the_manifest_forced_on(dockerfile: str) -> None:
    """The runtime runs with DEBUG off and therefore expects a hashed manifest.

    Collected at build time so a missing asset fails the build, loudly, in front
    of whoever caused it — rather than on the first production request for a
    page that references it.
    """
    assert "collectstatic" in dockerfile
    assert "DJANGO_STATIC_MANIFEST=1" in dockerfile


def test_dependencies_are_installed_from_the_lock_file(dockerfile: str) -> None:
    """`--frozen`, so the image cannot quietly resolve something newer."""
    assert "uv sync --frozen" in dockerfile
    assert "uv.lock" in dockerfile


# -- the image and the production stack agree ------------------------------


def _gunicorn_flag(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_the_production_command_matches_the_image_default(
    instructions: list[tuple[str, str]],
) -> None:
    """The production Compose file repeats the gunicorn flags to add access logs.

    A command that overrides one thing overrides all of them, so the two are
    written twice and can drift apart in silence. They did: the image said
    `--timeout 60` and production said 120, with nothing recording which number
    was the decision and which was the leftover.
    """
    compose: dict[str, Any] = yaml.safe_load(PRODUCTION_COMPOSE.read_text(encoding="utf-8"))
    production = [str(part) for part in compose["services"]["web"]["command"]]
    image = json.loads(_last(instructions, "CMD"))

    assert production[0] == image[0] == "gunicorn"
    for flag in ("--workers", "--timeout", "--bind"):
        assert _gunicorn_flag(production, flag) == _gunicorn_flag(image, flag), flag

    # What production adds, and the only thing it should be adding.
    assert "--access-logfile" in production
    assert "--access-logfile" not in image


def test_the_build_context_excludes_the_storage_roots_and_git() -> None:
    """The production image is built from a checkout on the server.

    A checkout that has ever run the importer has real source material sitting
    beside the code, and `COPY . .` would bake 4 GiB of members' legal work into
    an image. `.git` is excluded too, which is why the commit arrives as a build
    argument rather than being read out of the repository.
    """
    ignored = (Path(settings.BASE_DIR) / ".dockerignore").read_text(encoding="utf-8").split()
    for entry in (".git/", "evidence/", "derivatives/", "legacy-source/", ".env"):
        assert entry in ignored
