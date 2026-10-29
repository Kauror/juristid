"""Security and resilience review 2026-10-29, assessor F (workstream 3: monitoring).

These tests pin the facts the WS3 monitoring inventory relies on, so that the
inventory can be re-derived from the repository rather than from memory:

* how often each production container is probed (PR #533 moved every
  application worker to a six-hour interval, and nothing acts on the status);
* what the two things an external probe can ask — ``/healthz`` and ``/`` —
  actually answer, with and without a database;
* that a failed shared-gate attempt leaves a security-audit row a per-day
  count can read, which is the data a "failed logins" alert would be built on;
* that ``production_status --json`` has the shape a cron wrapper can parse;
* that the Lovable cron wrapper keeps its own log, so the host cron line's
  ``&> /dev/null`` discards nothing but the exit code.

Read-only against the repository and a test database; no host facts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings as django_settings
from django.core.management import call_command
from django.db import DatabaseError
from django.urls import reverse

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core import production_status as status_module
from tests.gate import PASSWORD, apply_shared_gate

DEPLOY = Path(django_settings.BASE_DIR) / "deploy" / "unraid-main"
COMPOSE = DEPLOY / "compose.yml"
LOVABLE_CRON = Path(django_settings.BASE_DIR) / "scripts" / "deploy" / "juristid-lovable-sync.sh"

#: The services whose health is a `manage.py` command on the release image.
#: Since PR #533 (c3260815) each is probed every six hours.
SIX_HOURLY_WORKERS = (
    "intake-reader",
    "searchindex",
    "intake-sweeper",
    "notifications",
    "mcp",
    "mcp-writer",
)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# -- 1. What the container runtime knows, and how late -------------------------


def test_every_application_worker_is_probed_only_every_six_hours(compose: dict[str, Any]) -> None:
    """A worker can be unhealthy for up to six hours before `docker ps` says so.

    The inventory marks worker liveness as "check exists, result six hours
    stale, nobody alerted"; this is the number that claim rests on.
    """
    for name in SIX_HOURLY_WORKERS:
        check = compose["services"][name]["healthcheck"]
        assert check["interval"] == "6h", f"{name}: probed every {check['interval']}"
        assert check["start_interval"] == "5s", f"{name}: startup still needs a fast probe"


def test_only_the_database_and_the_mcp_tunnel_are_probed_quickly(compose: dict[str, Any]) -> None:
    db = compose["services"]["db"]["healthcheck"]
    assert db["interval"] == "10s"
    mcp_tunnel = compose["services"]["mcp-tunnel"]["healthcheck"]
    assert mcp_tunnel["interval"] == "30s"


def test_the_public_tunnel_has_no_healthcheck_at_all(compose: dict[str, Any]) -> None:
    """`running` is all Docker can say about the Cloudflare tunnel.

    RECOVERY.md ("What container health can and cannot prove") says the only
    proof the public route works is external. The inventory therefore marks
    tunnel connectivity as NO CHECK on the host side.
    """
    assert "healthcheck" not in compose["services"]["tunnel"]


def test_nothing_in_the_compose_file_acts_on_an_unhealthy_container(
    compose: dict[str, Any],
) -> None:
    """No autoheal label, no restart-on-unhealthy: the status is recorded, not acted on."""
    for name, service in compose["services"].items():
        labels = service.get("labels") or {}
        assert not any("autoheal" in str(label).lower() for label in labels), name
        assert service.get("restart") == "unless-stopped", name


# -- 2. What an external probe sees ------------------------------------------


def test_healthz_reports_degraded_with_503_when_the_database_does_not_answer(
    client, monkeypatch
) -> None:
    """The one endpoint a host-side probe can use to tell "web up" from "web up, database gone"."""

    class _Broken:
        def cursor(self):
            raise DatabaseError("connection refused")

    monkeypatch.setattr("app.core.views.connection", _Broken())
    response = client.get("/healthz")
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body["status"] == "degraded"
    assert body["database"].startswith("error: ")


@pytest.mark.django_db
def test_healthz_answers_without_the_gate_and_names_no_secret(client, settings) -> None:
    apply_shared_gate(settings)
    response = client.get("/healthz")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert set(body) == {"status", "database", "environment", "stage", "revision"}
    assert body["status"] == "ok"


@pytest.mark.django_db
def test_an_anonymous_root_request_is_a_redirect_to_the_gate(client, settings) -> None:
    """The host watchdog probes `/`. Without `-L` it sees a 302; with it, the gate page.

    Either way the probe proves the tunnel and gunicorn, not the database: the
    gate page itself is a template with no business query. A database outage
    is `/healthz`'s to report.
    """
    apply_shared_gate(settings)
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:shared_gate"))
    gate = client.get(reverse("accounts:shared_gate"))
    assert gate.status_code == 200


# -- 3. The data a failed-login alert would read ----------------------------


@pytest.mark.django_db
def test_a_failed_gate_attempt_is_an_audit_row_a_daily_count_can_read(client, settings) -> None:
    """Failed gate attempts are `AUTHENTICATION_FAILED` rows with `succeeded=False`.

    Nothing in the repository counts them today (WS3 finding); this proves the
    rows an `audit_failures --since` command or a monitor query would aggregate
    are there, carry the refusal reason, and carry no password.
    """
    apply_shared_gate(settings)
    gate_url = reverse("accounts:shared_gate")
    for _ in range(2):
        assert client.post(gate_url, {"password": "vale-parool"}).status_code == 400

    failures = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.AUTHENTICATION_FAILED, succeeded=False
    )
    assert failures.count() == 2
    reasons = {row.detail.get("reason") for row in failures}
    assert reasons == {"bad_password"}
    assert all("vale-parool" not in json.dumps(row.detail) for row in failures)

    # The per-day aggregate a monitor would run: one bucket for today, count 2.
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    per_day = (
        failures.annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(n=Count("id"))
        .order_by("day")
    )
    assert [row["n"] for row in per_day] == [2]


@pytest.mark.django_db
def test_a_lockout_is_recorded_with_its_own_reason(client, settings) -> None:
    """A brute-force burst shows up as `locked_out` rows after the fifth miss."""
    apply_shared_gate(settings)
    gate_url = reverse("accounts:shared_gate")
    for _ in range(settings.SHARED_GATE_MAX_ATTEMPTS + 1):
        client.post(gate_url, {"password": "vale-parool"})
    reasons = [
        row.detail.get("reason")
        for row in SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.AUTHENTICATION_FAILED
        ).order_by("occurred_at", "created_at")
    ]
    assert "locked_out" in reasons
    assert reasons[-1] == "locked_out"
    # A correct password while locked out is still refused (429, which is also
    # what the access log shows during a burst) and still an audit row.
    before = SecurityAuditEvent.objects.count()
    assert client.post(gate_url, {"password": PASSWORD}).status_code == 429
    assert SecurityAuditEvent.objects.count() == before + 1


# -- 4. What a cron wrapper would parse ---------------------------------------


def _stub(monkeypatch, *results: status_module.CheckResult) -> None:
    monkeypatch.setattr(status_module, "CHECKS", tuple((lambda r=r: r) for r in results))


@pytest.mark.django_db
def test_production_status_json_has_the_shape_a_monitor_can_parse(capsys, monkeypatch) -> None:
    _stub(
        monkeypatch,
        status_module.CheckResult("deployment", "Deployment readiness", True),
        status_module.CheckResult("lovable_sync", "Lovable sync", False, "last run failed (X)"),
    )
    with pytest.raises(SystemExit) as stop:
        call_command("production_status", "--json")
    assert stop.value.code == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert [check["key"] for check in body["checks"]] == ["deployment", "lovable_sync"]
    failing = [check for check in body["checks"] if not check["ok"]]
    assert failing == [
        {
            "key": "lovable_sync",
            "label": "Lovable sync",
            "ok": False,
            "detail": "last run failed (X)",
        }
    ]


def test_the_real_roll_up_includes_the_rows_the_inventory_cites() -> None:
    """The six rows a monitor would get: readiness, search ×2, era, archive, Lovable."""
    names = [check.__name__ for check in status_module.CHECKS]
    assert names == [
        "_deployment_readiness",
        "_search_integrity",
        "_search_freshness",
        "_era_contracts",
        "_archive_projection",
        "_lovable_sync",
    ]
    # And what it deliberately leaves out, which a monitor must schedule separately.
    excluded = {name for name, _reason in status_module.EXCLUDED}
    assert excluded == {
        "check_evidence_integrity",
        "migration_plan",
        "/healthz and container state",
    }


# -- 5. The nightly Lovable cron ---------------------------------------------


def test_the_lovable_cron_wrapper_keeps_its_own_log() -> None:
    """The host cron line ends in `&> /dev/null`; the script logs to its own file first.

    So the redirect loses only the exit code — which is the one thing nobody
    reads (WS3 finding). The script must never `set -x`: the environment it
    passes carries the Lovable service login.
    """
    text = LOVABLE_CRON.read_text(encoding="utf-8")
    assert 'exec >>"$LOG" 2>&1' in text
    assert "LOG_DIR=$SYNC_DIR/logs" in text
    assert "set -x" not in text.replace("Never `set -x`", "")
    # Every failure path says FAIL/WARN into that log and exits non-zero or
    # proceeds read-only; a monitor can grep the newest log for `FAIL`.
    assert text.count('say "FAIL') >= 4
    assert text.count('say "WARN') >= 2
