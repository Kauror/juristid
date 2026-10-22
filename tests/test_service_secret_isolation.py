"""The database and the tunnel receive nothing of the application's secrets.

ADR 0179, Codex finding csf_14aa9cd147aa48f44b141d39. `db` used to load the
application's whole environment file — the Django signing key, the shared-gate
password, the mail and MFA settings — although PostgreSQL reads none of them.

These tests resolve the production Compose file the way Compose does for the two
mechanisms that matter here — `env_file:` (a file's values become the
container's environment) and `${VAR}` interpolation (values fill the Compose
file's own text and reach a container only where the file puts them) — against a
synthetic environment file in which every value is a unique sentinel, and hand
the result to `scripts/ci/assert_service_env_isolation.py`, the same check CI
runs on `docker compose config` output. Docker is not needed for this half; the
compose smoke job in `ci.yml` is the half where Compose itself resolves.

No Docker, no network, no database.
"""

from __future__ import annotations

import copy
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"
RECOVERY = ROOT / "deploy" / "unraid-main" / "RECOVERY.md"
CHECKER = ROOT / "scripts" / "ci" / "assert_service_env_isolation.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("assert_service_env_isolation", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()

# -- a small, deliberately partial emulation of Compose ---------------------
#
# Only what this file uses: `${VAR}`, `${VAR:-default}`, `${VAR-default}`,
# `${VAR:?message}`, `${VAR?message}` and `$$`. Anything else fails loudly
# rather than resolving to something plausible.

_TOKEN = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}|\$[A-Za-z_{]")


def interpolate(text: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        whole = match.group(0)
        if whole == "$$":
            return "$"
        name, operator, argument = match.group(1), match.group(2), match.group(3)
        if name is None:
            raise AssertionError(f"interpolation this emulation does not know: {whole!r}")
        value = env.get(name)
        if operator in (":-", ":?"):
            missing = not value
        else:
            missing = value is None
        if operator is None:
            return value or ""
        if operator.endswith("-"):
            return argument if missing else str(value)
        if missing:
            raise AssertionError(f"required variable {name} is unset: {argument}")
        return str(value)

    return _TOKEN.sub(replace, text)


def _walk(node: Any, env: dict[str, str]) -> Any:
    if isinstance(node, str):
        return interpolate(node, env)
    if isinstance(node, list):
        return [_walk(item, env) for item in node]
    if isinstance(node, dict):
        return {key: _walk(value, env) for key, value in node.items()}
    return node


def resolve(compose: dict[str, Any], interpolation: dict[str, str]) -> dict[str, Any]:
    """What `docker compose config` would print for each service's environment."""
    services: dict[str, Any] = {}
    for name, raw in compose["services"].items():
        service = _walk(copy.deepcopy(raw), interpolation)
        environment: dict[str, str] = {}
        entries = service.pop("env_file", None) or []
        for entry in [entries] if isinstance(entries, (str, dict)) else entries:
            path = entry["path"] if isinstance(entry, dict) else entry
            environment.update(checker.read_env_file(Path(path)))
        declared = service.get("environment") or {}
        if isinstance(declared, list):
            declared = dict(item.partition("=")[::2] for item in declared)
        environment.update({k: "" if v is None else str(v) for k, v in declared.items()})
        if environment:
            service["environment"] = environment
        services[name] = service
    return services


# -- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture
def sentinels(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    values = checker.sentinel_values(checker.template_keys())
    path = tmp_path / "juristid.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path, values


def _routine(compose: dict[str, Any], env_file: Path) -> dict[str, Any]:
    """`docker compose -p juristid-main -f compose.yml …`, as every runbook command runs it."""
    return resolve(compose, {"JURISTID_ENV_FILE": str(env_file)})


def _initialising(
    compose: dict[str, Any], env_file: Path, values: dict[str, str]
) -> dict[str, Any]:
    """`… --env-file <the environment file> up -d db`, RECOVERY.md's empty-cluster step."""
    return resolve(compose, {**values, "JURISTID_ENV_FILE": str(env_file)})


# -- the sentinel file is a fair test ---------------------------------------


def test_the_sentinels_cover_every_secret_the_template_names(
    sentinels: tuple[Path, dict[str, str]],
) -> None:
    """Dormant keys too: they are commented out today and filled in at activation."""
    _, values = sentinels
    for key in (
        "DJANGO_SECRET_KEY",
        "JURISTID_SHARED_GATE_PASSWORD",
        "POSTGRES_PASSWORD",
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
        "DJANGO_EMAIL_HOST_PASSWORD",
        "DJANGO_EMAIL_HOST_USER",
        "CF_ACCESS_AUDIENCE",
    ):
        assert key in values
    rendered = list(values.values())
    for value in rendered:
        assert sum(value in other for other in rendered) == 1, "a sentinel hides inside another"


# -- the production file ------------------------------------------------------


def test_the_database_environment_is_exactly_the_postgresql_allowlist(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]]
) -> None:
    path, values = sentinels
    services = _routine(compose, path)
    assert set(services["db"]["environment"]) == checker.DB_ALLOWED
    assert checker.violations(services, values, initialising=False) == []


def test_no_application_secret_reaches_the_database_or_the_tunnel_day_to_day(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]]
) -> None:
    path, values = sentinels
    services = _routine(compose, path)
    for name in ("db", "tunnel"):
        rendered = repr(services[name])
        for key in (
            "DJANGO_SECRET_KEY",
            "JURISTID_SHARED_GATE_PASSWORD",
            "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
            "DJANGO_EMAIL_HOST_PASSWORD",
            "POSTGRES_PASSWORD",
        ):
            assert values[key] not in rendered, f"{name} receives {key}"
    # The positive control: the same file did reach the application.
    assert services["web"]["environment"]["DJANGO_SECRET_KEY"] == values["DJANGO_SECRET_KEY"]
    assert services["web"]["environment"]["POSTGRES_PASSWORD"] == values["POSTGRES_PASSWORD"]


def test_initialising_a_cluster_passes_the_password_and_nothing_else(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]]
) -> None:
    """`--env-file` feeds interpolation only: the database takes the one value it names."""
    path, values = sentinels
    services = _initialising(compose, path, values)
    assert services["db"]["environment"]["POSTGRES_PASSWORD"] == values["POSTGRES_PASSWORD"]
    assert checker.violations(services, values, initialising=True) == []
    assert values["POSTGRES_PASSWORD"] not in repr(services["tunnel"])


def test_the_database_and_the_tunnel_declare_no_env_file(compose: dict[str, Any]) -> None:
    """Stated on the source as well as on the resolution: an `env_file` is a whole file."""
    for name in ("db", "tunnel"):
        assert "env_file" not in compose["services"][name], name
    assert "environment" not in compose["services"]["tunnel"]


def test_the_database_password_is_an_interpolation_never_a_literal(
    compose: dict[str, Any],
) -> None:
    """The file is public; the value comes from the operator's environment file, once."""
    reference = "${POSTGRES_PASSWORD:-}"
    assert compose["services"]["db"]["environment"]["POSTGRES_PASSWORD"] == reference


def test_the_application_services_still_read_the_environment_file(
    compose: dict[str, Any],
) -> None:
    """The isolation narrowed `db`, not the application, which needs every key."""
    for name in ("web", "intake-reader", "searchindex"):
        assert compose["services"][name]["env_file"] == [
            "${JURISTID_ENV_FILE:-/mnt/user/appdata/juristid-main/config/juristid.env}"
        ], name


# -- the check is not blind -------------------------------------------------


def test_the_check_refuses_the_configuration_this_replaced(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]]
) -> None:
    """The db stanza as it stood at 4818203e: the whole file, password from it."""
    path, values = sentinels
    before = copy.deepcopy(compose)
    before["services"]["db"]["env_file"] = [
        "${JURISTID_ENV_FILE:-/mnt/user/appdata/juristid-main/config/juristid.env}"
    ]
    del before["services"]["db"]["environment"]["POSTGRES_PASSWORD"]
    found = checker.violations(_routine(before, path), values, initialising=False)
    assert any("db: receives DJANGO_SECRET_KEY" in line for line in found)
    assert any(
        "db: the value of JURISTID_SHARED_GATE_PASSWORD reaches it" in line for line in found
    )


def test_the_check_refuses_a_tunnel_given_an_application_value(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]]
) -> None:
    path, values = sentinels
    leaky = copy.deepcopy(compose)
    leaky["services"]["tunnel"]["environment"] = {"TUNNEL_TOKEN": "${DJANGO_SECRET_KEY:-}"}
    found = checker.violations(_initialising(leaky, path, values), values, initialising=True)
    assert "tunnel: receives TUNNEL_TOKEN, which is not on its allowlist" in found
    assert "tunnel: the value of DJANGO_SECRET_KEY reaches it" in found


def test_the_check_refuses_when_the_sentinel_file_was_never_read(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    _, values = sentinels
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    found = checker.violations(_routine(compose, empty), values, initialising=False)
    assert any("web: does not hold the sentinel DJANGO_SECRET_KEY" in line for line in found)


def test_the_check_refuses_a_password_that_does_not_match_the_shape(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]]
) -> None:
    path, values = sentinels
    found = checker.violations(_routine(compose, path), values, initialising=True)
    assert (
        "db: POSTGRES_PASSWORD is not the environment file's in this shape of invocation" in found
    )


# -- the runbook and CI keep their half ------------------------------------


def test_recovery_initialises_the_cluster_with_the_environment_file() -> None:
    """The one command that starts `db` on an empty `postgres/` must pass the password."""
    text = RECOVERY.read_text(encoding="utf-8")
    starts = [
        line
        for line in text.splitlines()
        if line.startswith("docker compose -p juristid-main") and line.rstrip().endswith("up -d db")
    ]
    assert starts, "RECOVERY.md no longer starts the database on its own"
    for line in starts:
        assert "--env-file /mnt/user/appdata/juristid-main/config/juristid.env" in line, line


def test_ci_resolves_both_shapes_with_compose_itself() -> None:
    """The emulation above is checked against the real resolver where Docker exists."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["container"]["steps"]
    scripts = [step.get("run", "") for step in steps]
    joined = "\n".join(scripts)
    assert "assert_service_env_isolation.py --write-sentinels" in joined
    assert re.search(r"assert_service_env_isolation\.py \S+ \"\$SENTINELS\"\n", joined)
    assert re.search(r"assert_service_env_isolation\.py \S+ \"\$SENTINELS\" --initialising", joined)
