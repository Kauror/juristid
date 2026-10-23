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
from typing import Any, NamedTuple

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


def resolve(
    compose: dict[str, Any], interpolation: dict[str, str], profiles: tuple[str, ...] = ()
) -> dict[str, Any]:
    """What `docker compose [--profile …] config` would print for each service's environment.

    A service behind a profile is in the model only when one of its profiles
    is active, as in Compose: `mcp` and `mcp-tunnel` (docs/adr/0185) appear
    with `profiles=("mcp",)` and not otherwise, so their environment files are
    read only then — exactly why a host without them still resolves.
    """
    services: dict[str, Any] = {}
    for name, raw in compose["services"].items():
        gated = raw.get("profiles") or []
        if gated and not set(gated) & set(profiles):
            continue
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


class McpFiles(NamedTuple):
    """The two environment files ADR 0185 added, as synthetic sentinel files."""

    mcp_path: Path
    mcp: dict[str, str]
    tunnel_path: Path
    tunnel: dict[str, str]


@pytest.fixture
def mcp_files(tmp_path: Path) -> McpFiles:
    written: list[tuple[Path, dict[str, str]]] = []
    for name, template, tag in (
        ("juristid-mcp.env", checker.MCP_ENV_EXAMPLE, "m"),
        ("juristid-mcp-tunnel.env", checker.MCP_TUNNEL_ENV_EXAMPLE, "t"),
    ):
        values = checker.sentinel_values(checker.template_keys(template), tag)
        path = tmp_path / name
        path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        written.append((path, values))
    (mcp_path, mcp), (tunnel_path, tunnel) = written
    return McpFiles(mcp_path, mcp, tunnel_path, tunnel)


def _with_mcp(
    compose: dict[str, Any],
    env_file: Path,
    files: McpFiles,
    values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """`docker compose --profile mcp …`, routine or (with `values`) initialising."""
    interpolation = {
        **(values or {}),
        "JURISTID_ENV_FILE": str(env_file),
        "JURISTID_MCP_ENV_FILE": str(files.mcp_path),
        "JURISTID_MCP_TUNNEL_ENV_FILE": str(files.tunnel_path),
    }
    return resolve(compose, interpolation, profiles=("mcp",))


def _mcp_violations(
    services: dict[str, Any],
    values: dict[str, str],
    files: McpFiles,
    *,
    initialising: bool = False,
    main_file: Path | None = None,
) -> list[str]:
    return checker.violations(
        services,
        values,
        initialising=initialising,
        mcp_sentinels=files.mcp,
        mcp_tunnel_sentinels=files.tunnel,
        main_file=str(main_file) if main_file else None,
    )


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


# -- the MCP server and its tunnel (docs/adr/0185) --------------------------


def test_the_mcp_services_read_only_their_own_files_day_to_day(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    path, values = sentinels
    services = _with_mcp(compose, path, mcp_files)
    assert {"mcp", "mcp-tunnel"} <= set(services)
    assert _mcp_violations(services, values, mcp_files, main_file=path) == []
    # The positive controls, stated here as well as inside the checker.
    assert services["mcp"]["environment"]["DJANGO_SECRET_KEY"] == mcp_files.mcp["DJANGO_SECRET_KEY"]
    tunnel_id = services["mcp-tunnel"]["environment"]["CONTROL_PLANE_TUNNEL_ID"]
    assert tunnel_id == mcp_files.tunnel["CONTROL_PLANE_TUNNEL_ID"]


def test_initialising_a_cluster_hands_the_mcp_services_nothing_either(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    """`--env-file juristid.env` feeds interpolation; neither new service interpolates from it."""
    path, values = sentinels
    services = _with_mcp(compose, path, mcp_files, values)
    assert _mcp_violations(services, values, mcp_files, initialising=True, main_file=path) == []


def test_the_mcp_server_holds_none_of_the_browsers_secrets(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    """Named one by one: the four ADR 0185 §6 says the process is not given."""
    path, values = sentinels
    rendered = repr(_with_mcp(compose, path, mcp_files)["mcp"])
    for key in (
        "JURISTID_SHARED_GATE_PASSWORD",
        "LOCAL_AUTH_MFA_ENCRYPTION_KEY",
        "DJANGO_EMAIL_HOST_PASSWORD",
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
    ):
        assert values[key] not in rendered, f"mcp receives juristid.env's {key}"
    for key in ("JURISTID_SHARED_GATE_PASSWORD", "LOCAL_AUTH_MFA_ENCRYPTION_KEY"):
        assert key not in rendered, f"mcp is handed {key} at all"


def test_the_mcp_services_declare_their_own_files_and_never_the_applications(
    compose: dict[str, Any],
) -> None:
    services = compose["services"]
    assert services["mcp"]["env_file"] == [
        "${JURISTID_MCP_ENV_FILE:-/mnt/user/appdata/juristid-main/config/juristid-mcp.env}"
    ]
    assert services["mcp-tunnel"]["env_file"] == [
        "${JURISTID_MCP_TUNNEL_ENV_FILE:-"
        "/mnt/user/appdata/juristid-main/config/juristid-mcp-tunnel.env}"
    ]
    for name in ("mcp", "mcp-tunnel"):
        assert "JURISTID_ENV_FILE" not in str(services[name]), name
        assert "juristid.env" not in str(services[name]), name


def test_the_allowlists_are_what_the_templates_name_and_no_more() -> None:
    """The templates cannot quietly widen what a service receives.

    Every key the MCP template names is on the MCP allowlist, and none of the
    browser's; the tunnel template is exactly three keys. The allowlists are
    literal in the checker, so this is where a new key is noticed.
    """
    assert not checker.MCP_ALLOWED & checker.BROWSER_ONLY
    mcp_keys = set(checker.template_keys(checker.MCP_ENV_EXAMPLE))
    assert mcp_keys <= checker.MCP_ALLOWED, sorted(mcp_keys - checker.MCP_ALLOWED)
    assert not mcp_keys & checker.BROWSER_ONLY
    assert set(checker.template_keys(checker.MCP_TUNNEL_ENV_EXAMPLE)) == {
        "CONTROL_PLANE_API_KEY",
        "CONTROL_PLANE_TUNNEL_ID",
        "MCP_OAUTH_TRUSTED_ORIGINS",
    }
    assert set(checker.template_keys(checker.MCP_TUNNEL_ENV_EXAMPLE)) <= (
        checker.MCP_TUNNEL_ALLOWED
    )
    # And the application template names none of the tunnel's.
    assert not set(checker.template_keys()) & {"CONTROL_PLANE_API_KEY", "CONTROL_PLANE_TUNNEL_ID"}


def test_the_sentinels_of_the_three_files_never_contain_each_other(
    sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    _, values = sentinels
    everything = [*values.values(), *mcp_files.mcp.values(), *mcp_files.tunnel.values()]
    for value in everything:
        assert sum(value in other for other in everything) == 1, value


def test_the_check_refuses_an_mcp_server_given_the_application_file(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    """The mistake this whole arrangement is against: the one-line `env_file` copy."""
    path, values = sentinels
    leaky = copy.deepcopy(compose)
    leaky["services"]["mcp"]["env_file"] = [
        "${JURISTID_ENV_FILE:-/mnt/user/appdata/juristid-main/config/juristid.env}",
        *leaky["services"]["mcp"]["env_file"],
    ]
    found = _mcp_violations(_with_mcp(leaky, path, mcp_files), values, mcp_files, main_file=path)
    assert (
        "mcp: receives JURISTID_SHARED_GATE_PASSWORD, a secret of the browser's that it must "
        "never hold"
    ) in found
    assert (
        "mcp: the value of JURISTID_SHARED_GATE_PASSWORD from the environment file reaches it"
    ) in found


def test_the_check_refuses_a_tunnel_client_given_an_application_value(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    path, values = sentinels
    leaky = copy.deepcopy(compose)
    leaky["services"]["mcp-tunnel"]["environment"]["EXTRA"] = "${DJANGO_SECRET_KEY:-}"
    services = _with_mcp(leaky, path, mcp_files, values)
    found = _mcp_violations(services, values, mcp_files, initialising=True, main_file=path)
    assert "mcp-tunnel: receives EXTRA, which is not on its allowlist" in found
    assert (
        "mcp-tunnel: the value of DJANGO_SECRET_KEY from the environment file reaches it" in found
    )


def test_the_check_refuses_the_runtime_key_anywhere_but_the_tunnel_client(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    path, values = sentinels
    leaky = copy.deepcopy(compose)
    leaky["services"]["web"]["env_file"].append(
        "${JURISTID_MCP_TUNNEL_ENV_FILE:-/mnt/user/appdata/juristid-main/config/x.env}"
    )
    found = _mcp_violations(_with_mcp(leaky, path, mcp_files), values, mcp_files, main_file=path)
    assert (
        "web: the value of CONTROL_PLANE_API_KEY from juristid-mcp-tunnel.env reaches it" in found
    )


def test_the_check_refuses_an_mcp_file_that_was_never_read(
    compose: dict[str, Any],
    sentinels: tuple[Path, dict[str, str]],
    mcp_files: McpFiles,
    tmp_path: Path,
) -> None:
    path, values = sentinels
    empty = tmp_path / "empty-mcp.env"
    empty.write_text("", encoding="utf-8")
    services = _with_mcp(compose, path, mcp_files._replace(mcp_path=empty))
    found = _mcp_violations(services, values, mcp_files, main_file=path)
    control = "mcp: does not hold the sentinel DJANGO_SECRET_KEY"
    assert any(line.startswith(control) for line in found), found


def test_the_check_does_not_pass_over_services_it_was_not_told_about(
    compose: dict[str, Any], sentinels: tuple[Path, dict[str, str]], mcp_files: McpFiles
) -> None:
    """Either half without the other is a check that checked nothing."""
    path, values = sentinels
    with_profile = _with_mcp(compose, path, mcp_files)
    found = checker.violations(with_profile, values, initialising=False)
    assert found and found[0].startswith("mcp, mcp-tunnel: in the resolved model"), found

    without_profile = _routine(compose, path)
    assert "mcp" not in without_profile, "a resolution without --profile mcp includes it"
    found = _mcp_violations(without_profile, values, mcp_files, main_file=path)
    assert any(line.startswith("mcp: not in the resolved model") for line in found), found


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
    # Both shapes with the MCP files (docs/adr/0185): resolved with
    # `--profile mcp`, so the two new services are in the model to be checked.
    mcp = r"--mcp \"\$MCP_SENTINELS\" --mcp-tunnel \"\$TUNNEL_SENTINELS\""
    assert re.search(rf"assert_service_env_isolation\.py {mcp} \S+ \"\$SENTINELS\"\n", joined)
    assert re.search(
        rf"assert_service_env_isolation\.py {mcp} \S+ \"\$SENTINELS\" --initialising", joined
    )
    assert re.search(r"resolve\(\) \{ docker compose \"\$@\" --profile mcp ", joined)
