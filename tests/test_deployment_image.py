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
from pathlib import Path, PurePosixPath
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


#: The only paths under `/app` the application user may write: the three
#: storage roots and the upload staging area (config/settings.py).
WRITABLE_DIRECTORIES = {
    "/app/evidence",
    "/app/derivatives",
    "/app/legacy-source",
    "/app/pending-uploads",
}

#: `name:tag@sha256:<64 hex>` — the tag for the reader, the digest for Docker.
PINNED_IMAGE = re.compile(r"[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}")


def _base_images(instructions: list[tuple[str, str]]) -> list[str]:
    return [argument.split()[0] for verb, argument in instructions if verb == "FROM"]


def _runtime_stage(instructions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    last_from = max(index for index, (verb, _) in enumerate(instructions) if verb == "FROM")
    return instructions[last_from + 1 :]


def _chowned_to_the_application_user(instructions: list[tuple[str, str]]) -> list[str]:
    """Every path a runtime-stage `chown juristid:juristid` names."""
    targets: list[str] = []
    for verb, argument in _runtime_stage(instructions):
        if verb != "RUN":
            continue
        for command in re.split(r"&&|;", argument):
            words = command.split()
            if not words or words[0] != "chown":
                continue
            operands = [word for word in words[1:] if not word.startswith("-")]
            assert operands[0] == "juristid:juristid", command
            targets.extend(operands[1:])
    return targets


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


def test_the_storage_directories_belong_to_the_application_user(
    instructions: list[tuple[str, str]],
) -> None:
    """A named volume inherits the ownership of the image path it shadows."""
    assert set(_chowned_to_the_application_user(instructions)) == WRITABLE_DIRECTORIES


def test_the_application_code_is_not_writable_by_its_runtime_user(
    instructions: list[tuple[str, str]],
) -> None:
    """Root owns the code; `juristid` owns only the directories it must write.

    The process that serves requests is the one an attacker gets. If it owns
    `/app` it can rewrite the code the next worker imports, and a file-write bug
    becomes code execution that survives a restart. So the copy into `/app`
    stays root's, and ownership is handed over directory by directory, only for
    the storage roots and the staging area (JOPS-006).
    """
    runtime = _runtime_stage(instructions)
    for verb, argument in runtime:
        if verb == "COPY":
            assert "--chown" not in argument, f"code copied as the application user: {argument}"
    targets = _chowned_to_the_application_user(instructions)
    assert targets, "nothing is handed to the application user at all"
    for target in targets:
        assert target in WRITABLE_DIRECTORIES, f"{target} is not a directory the app must write"


def test_every_writable_storage_path_is_one_the_image_hands_over() -> None:
    """The writable set is read off the settings and the production mounts.

    A storage root the image does not create and hand over is created by Docker
    or by the application on first write — root-owned in the first case and
    impossible in the second, now that `/app` is not the application's.
    """
    base = PurePosixPath("/app")
    defaults = {
        "EVIDENCE_ROOT": base / "evidence",
        "DERIVATIVE_ROOT": base / "derivatives",
        "LEGACY_SOURCE_ROOT": base / "legacy-source",
        "PENDING_UPLOAD_ROOT": base / "pending-uploads",
    }
    source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
    for name, path in defaults.items():
        declaration = next(line for line in source.splitlines() if line.startswith(f"{name} = "))
        assert f'BASE_DIR / "{path.name}"' in declaration, name
    assert {str(path) for path in defaults.values()} == WRITABLE_DIRECTORIES

    compose: dict[str, Any] = yaml.safe_load(PRODUCTION_COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            parts = str(volume).split(":")
            target = parts[-2] if parts[-1] in {"ro", "rw"} else parts[-1]
            if target.startswith("/app"):
                assert target in WRITABLE_DIRECTORIES, f"{name} mounts {target}"


# -- the base it is built on -----------------------------------------------


def test_both_stages_build_on_debian_13(instructions: list[tuple[str, str]]) -> None:
    """Debian 12 has no patch path for most of what it ships (JSEC-007).

    Builder and runtime move together: the virtual environment is built in one
    and copied into the other, so the two must agree on the interpreter.
    """
    bases = _base_images(instructions)
    assert len(bases) == 2
    for image in bases:
        assert "trixie" in image, image
        assert "bookworm" not in image, image


def test_both_base_images_are_pinned_by_digest(instructions: list[tuple[str, str]]) -> None:
    """A floating tag is a different image on every build.

    The tag stays for the reader; the digest is what Docker pulls. Moving to a
    newer base is then a reviewed one-line change rather than something a
    rebuild does on its own.
    """
    for image in _base_images(instructions):
        assert PINNED_IMAGE.fullmatch(image), f"not pinned by digest: {image}"


def test_the_builder_and_runtime_share_one_python(instructions: list[tuple[str, str]]) -> None:
    """`/opt/venv` points at the interpreter of the stage that built it."""
    builder, runtime = _base_images(instructions)
    assert re.search(r"python3\.13\b", builder), builder
    assert re.search(r":3\.13-", runtime), runtime


def test_the_runtime_python_carries_no_installer(instructions: list[tuple[str, str]]) -> None:
    """pip, setuptools and wheel are build tools, and the image does not build.

    They carried their own vendored libraries with advisories (JSEC-007), and an
    installer in a running container is a way to add code to it. Removed with
    the *system* interpreter, because `python` on `PATH` is the venv's, which
    never had them.
    """
    runtime = _runtime_stage(instructions)
    venv_copy = next(
        index
        for index, (verb, argument) in enumerate(runtime)
        if verb == "COPY" and "/opt/venv" in argument
    )
    removal = [
        (index, argument)
        for index, (verb, argument) in enumerate(runtime)
        if verb == "RUN" and "pip uninstall" in argument
    ]
    assert removal, "the runtime stage does not remove pip"
    index, command = removal[0]
    assert index > venv_copy
    assert "/usr/local/bin/python" in command
    for package in ("pip", "setuptools", "wheel"):
        assert re.search(rf"\b{package}\b", command.split("pip uninstall", 1)[1]), package


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


# -- what the build context carries ----------------------------------------


def _dockerignore() -> list[str]:
    text = (Path(settings.BASE_DIR) / ".dockerignore").read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _excluded_from_the_build_context(path: str, patterns: list[str]) -> bool:
    """Docker's rule, modelled: the last pattern matching the path or a parent wins.

    Patterns are relative to the context root, `**` crosses any number of
    directories (none included), `!` re-includes, and a pattern that matches a
    directory takes everything beneath it.
    """
    candidate = PurePosixPath(path)
    targets = [candidate, *[parent for parent in candidate.parents if str(parent) != "."]]
    excluded = False
    for raw in patterns:
        negated = raw.startswith("!")
        pattern = (raw[1:] if negated else raw).strip("/")
        if any(target.full_match(pattern) for target in targets):
            excluded = not negated
    return excluded


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "deploy/unraid-main/juristid.env",
        "deploy/unraid-main/.env",
        "deploy/unraid-test/.env.production",
        "config/server.env",
        "scripts/deploy/nested/deeper/.env",
    ],
)
def test_no_environment_file_anywhere_reaches_the_image(path: str) -> None:
    """Not only at the root (JSEC-004).

    The production Compose file builds from a checkout on the server, with
    `context: ../..` and `COPY . .`, and the server's own `juristid.env` is one
    misplaced copy away from sitting inside that checkout. `.gitignore` already
    refuses it everywhere; the build context has to as well.
    """
    assert _excluded_from_the_build_context(path, _dockerignore()), path


@pytest.mark.parametrize(
    "path",
    [".env.example", "deploy/recovery-rehearsal/real-data.env.example"],
)
def test_the_committed_examples_are_not_mistaken_for_secrets(path: str) -> None:
    assert not _excluded_from_the_build_context(path, _dockerignore()), path


def test_the_test_suites_and_the_prose_stay_out_of_the_image() -> None:
    """Nothing in the image runs them, and a test suite in a production image is
    an invitation to run it there (deploy/unraid-main/README.md, 2026-08-24)."""
    patterns = _dockerignore()
    root = Path(settings.BASE_DIR)
    for directory in ("tests", "e2e"):
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            assert _excluded_from_the_build_context(relative, patterns), relative
    for path in sorted((root / "docs" / "adr").glob("*.md")):
        relative = path.relative_to(root).as_posix()
        assert _excluded_from_the_build_context(relative, patterns), relative


def test_the_documents_the_application_reads_at_runtime_stay_in_the_image() -> None:
    """`docs/` is prose, except two trees the code opens while serving.

    `/uuendused/` renders the release notes, and the register importer refuses
    a sheet without its era contract — both read from `docs/` under `BASE_DIR`.
    """
    from app.core.release_notes import release_notes_path
    from app.legacy_import.contracts import contracts_directory

    patterns = _dockerignore()
    root = Path(settings.BASE_DIR)
    needed = [release_notes_path(), *sorted(contracts_directory().rglob("*"))]
    assert len(needed) > 1
    for path in needed:
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        assert not _excluded_from_the_build_context(relative, patterns), relative


def test_the_code_the_image_runs_is_in_the_build_context() -> None:
    patterns = _dockerignore()
    root = Path(settings.BASE_DIR)
    for directory in ("app", "config", "templates", "static", "scripts"):
        for path in (root / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(root).as_posix()
            assert not _excluded_from_the_build_context(relative, patterns), relative
    for single in ("manage.py", "pyproject.toml", "uv.lock"):
        assert not _excluded_from_the_build_context(single, patterns), single
