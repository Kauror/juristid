"""Security review 2026-10-29, assessor D — boundaries VERIFIED SOUND.

These tests pass on current code. They pin four properties the review checked
by hand so that the next change cannot quietly lose them (none duplicates
tests/test_browser_policy.py, tests/test_supply_chain_pinning.py or
tests/test_ci_supply_chain.py):

1. every outbound HTTP call the application makes carries a timeout, and the
   two that talk to third parties with credentials or on behalf of data
   (koda.ee title fetch, Lovable) run through an opener that refuses to follow
   redirects;
2. no template switches autoescaping off, and `|safe` appears only where the
   value is machine-generated (segno's QR SVG) or nh3-sanitised at the one
   service that writes it (`add_entry`);
3. no `run:` block in the three workflows interpolates attacker-influenced
   context (`github.event.*`, `github.head_ref`, `inputs.*`) — inputs reach
   the shell through `env:`;
4. the production Compose file puts no secret-shaped value in a literal
   `environment:` entry or a healthcheck command, so `docker inspect` and the
   process list show none.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TEMPLATES = ROOT / "templates"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
COMPOSE = ROOT / "deploy" / "unraid-main" / "compose.yml"


# -- 1. outbound HTTP ---------------------------------------------------------

#: A call that opens a connection: `urlopen(...)`, `<opener>.open(...)` or the
#: injected `self.opener(request, timeout)` of the Lovable transport. Not
#: `build_opener(...)`, which opens nothing.
_OPEN_CALL = re.compile(r"(?:\burlopen|\.open|self\.opener)\(")


def _python_sources(base: Path) -> list[Path]:
    return [p for p in base.rglob("*.py") if "tests" not in p.parts and "migrations" not in p.parts]


def test_every_outbound_http_call_in_the_application_has_a_timeout() -> None:
    callers = [p for p in _python_sources(APP) if "urllib.request" in p.read_text(encoding="utf-8")]
    assert callers, "the scan found no urllib callers; the pattern has drifted"
    missing: list[str] = []
    for path in callers:
        source = path.read_text(encoding="utf-8")
        for match in _OPEN_CALL.finditer(source):
            window = source[match.start() : match.start() + 240]
            if "timeout" not in window.lower():
                line = source.count("\n", 0, match.start()) + 1
                missing.append(f"{path.relative_to(ROOT)}:{line}")
    assert not missing, f"outbound call(s) without a timeout: {missing}"


def test_third_party_fetchers_refuse_redirects() -> None:
    for relative in ("app/matters/publication_title.py", "app/legacy_import/lovable_source.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "HTTPRedirectHandler" in source and "build_opener" in source, relative
        assert re.search(
            r"def redirect_request\([^)]*\)[^:]*:\s*(?:\"\"\"[\s\S]*?\"\"\"\s*)?return None", source
        ), f"{relative}: the redirect handler no longer hands every 3xx back"


# -- 2. templates -------------------------------------------------------------

#: `|safe` is allowed only here, and only for these values.
SAFE_ALLOWLIST = {
    "accounts/local/enrol.html": {"enrolment.qr_svg"},
    "haldus/enrol.html": {"enrolment.qr_svg"},
    "mcp_access/enrol.html": {"enrolment.qr_svg"},
    "matters/partials/entry_body.html": {"entry.body", "entry_conflict.body"},
}

_SAFE_FILTER = re.compile(r"\{\{\s*([\w.]+)(?:\|[\w:\"' ]+)*\|safe\s*\}\}")


def test_no_template_turns_autoescaping_off() -> None:
    offenders = [
        str(p.relative_to(TEMPLATES))
        for p in TEMPLATES.rglob("*.html")
        if re.search(r"\{%\s*autoescape\s+off", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_safe_is_used_only_on_generated_or_sanitised_values() -> None:
    found: dict[str, set[str]] = {}
    for path in TEMPLATES.rglob("*.html"):
        for match in _SAFE_FILTER.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(str(path.relative_to(TEMPLATES)).replace("\\", "/"), set()).add(
                match.group(1)
            )
    assert found == SAFE_ALLOWLIST, (
        "a `|safe` moved or was added; extend the allowlist only for a value that is "
        f"generated or sanitised before it is stored: {found}"
    )
    # The sanitiser still stands at the one writer of `Entry.body`.
    services = (APP / "matters" / "services.py").read_text(encoding="utf-8")
    assert "clean_body = sanitize_entry_html(body)" in services


# -- 3. workflow expression injection -------------------------------------------

_UNTRUSTED = re.compile(
    r"\$\{\{\s*(?:github\.event\.(?!pull_request\.draft\b)|github\.head_ref|inputs\.|"
    r"github\.event_name\s*==\s*'issue_comment')"
)


def _run_blocks(workflow: dict[str, Any]) -> list[tuple[str, str]]:
    found = []
    for job_name, job in workflow.get("jobs", {}).items():
        for step in job.get("steps") or []:
            if "run" in step:
                found.append((f"{job_name}/{step.get('name', '?')}", str(step["run"])))
    return found


def test_no_run_block_interpolates_untrusted_context() -> None:
    offenders = []
    for path in WORKFLOWS:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = workflow.get(True) or workflow.get("on") or {}
        assert "pull_request_target" not in triggers, f"{path.name}: pull_request_target"
        assert "issue_comment" not in triggers, f"{path.name}: issue_comment"
        assert "workflow_run" not in triggers, f"{path.name}: workflow_run"
        for where, script in _run_blocks(workflow):
            if _UNTRUSTED.search(script):
                offenders.append(f"{path.name}:{where}")
    assert not offenders, offenders


# -- 4. compose literals --------------------------------------------------------

_SECRET_SHAPED = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL)", re.IGNORECASE)


def test_compose_literal_environment_and_healthchecks_hold_no_secret_value() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment)
        for key, value in environment.items():
            value = str(value)
            if _SECRET_SHAPED.search(key):
                # Interpolated from the operator's file, or empty: never a literal.
                assert value == "" or value.startswith("${"), f"{name}: {key} is a literal"
        test = service.get("healthcheck", {}).get("test") or []
        joined = " ".join(test) if isinstance(test, list) else str(test)
        assert not re.search(r"PASSWORD|SECRET|TOKEN|Bearer", joined, re.IGNORECASE), (
            f"{name}: the healthcheck command carries a secret into the process list"
        )
        assert not service.get("ports"), f"{name}: publishes a host port"
