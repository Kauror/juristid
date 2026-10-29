"""`manage.py audit_failures`: the reader the audit tables never had (review 2026-10-29, F-07).

Counts, never rows; a window that refuses a typo; three output shapes that
agree; and a run that writes nothing.
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.management.commands.audit_failures import failures_since, window_of
from app.audit.models import SecurityAuditEvent
from app.audit.services import record_security_event
from app.mcp_access.models import McpAccessEvent
from app.mcp_writes.models import McpWriteEvent

pytestmark = pytest.mark.django_db


def _gate_failure(reason: str, address: str, *, age: timedelta = timedelta(0)) -> None:
    # Written directly rather than through `record_security_event`, so the row
    # can carry an earlier `occurred_at`: the table is append-only (no UPDATE).
    SecurityAuditEvent.objects.create(
        event_type=SecurityEventType.AUTHENTICATION_FAILED,
        occurred_at=timezone.now() - age,
        succeeded=False,
        ip_address=address,
        user_agent="probe/1.0",
        detail={"path": "shared_gate", "reason": reason},
    )


def _world() -> None:
    _gate_failure("bad_password", "203.0.113.5")
    _gate_failure("bad_password", "203.0.113.5")
    _gate_failure("locked_out", "203.0.113.5")
    _gate_failure("bad_password", "203.0.113.9")
    _gate_failure("bad_password", "203.0.113.7", age=timedelta(days=3))  # outside 24h
    record_security_event(
        event_type=SecurityEventType.AUTHENTICATION_FAILED,
        succeeded=False,
        ip_address="203.0.113.5",
        detail={"path": "administrator_session", "reason": "bad_password"},
    )
    record_security_event(
        event_type=SecurityEventType.ACCESS_REFUSED, succeeded=False, detail={"path": "x"}
    )
    record_security_event(
        event_type=SecurityEventType.SESSION_ENDED,
        detail={"path": "shared_gate", "reason": "shared_password_changed"},
    )
    McpAccessEvent.objects.create(event_type="REFRESH_REUSE_DETECTED", succeeded=False)
    McpAccessEvent.objects.create(event_type="GRANT_LOCKED", succeeded=False)
    McpAccessEvent.objects.create(event_type="TOKEN_ISSUED", succeeded=True)  # not watched
    McpWriteEvent.objects.create(event_type="COMMIT_REFUSED", succeeded=False)


def test_the_window_accepts_hours_and_days_and_nothing_else() -> None:
    assert window_of("24h") == timedelta(hours=24)
    assert window_of("7d") == timedelta(days=7)
    for bad in ("", "24", "0h", "1w", "24 h", "-1h"):
        with pytest.raises(CommandError):
            window_of(bad)


def test_counts_are_by_window_path_and_reason() -> None:
    _world()
    found = failures_since(timedelta(hours=24))
    assert found.gate_failed == 4
    assert found.gate_locked_out == 1
    assert found.gate_distinct_clients == 2
    assert found.admin_step_up_failed == 1
    assert found.personal_sign_in_failed == 0
    assert found.access_refused == 1
    assert found.sessions_ended_by_rotation == 1
    assert found.mcp_access == {"REFRESH_REUSE_DETECTED": 1, "GRANT_LOCKED": 1}
    assert found.mcp_write == {"COMMIT_REFUSED": 1}
    assert found.indicators == ["mcp_refresh_reuse", "write_commit_refused"]
    assert failures_since(timedelta(days=7)).gate_failed == 5


def test_the_three_shapes_agree_and_carry_no_address() -> None:
    _world()
    text, as_json, monitor = StringIO(), StringIO(), StringIO()
    call_command("audit_failures", "--since", "24h", stdout=text)
    call_command("audit_failures", "--since", "24h", "--json", stdout=as_json)
    call_command("audit_failures", "--since", "24h", "--format", "monitor", stdout=monitor)

    body = json.loads(as_json.getvalue())
    assert body["gate"] == {"failed": 4, "locked_out": 1, "distinct_clients": 2}
    assert body["indicators"] == ["mcp_refresh_reuse", "write_commit_refused"]

    lines = dict(line.split("=", 1) for line in monitor.getvalue().splitlines())
    assert lines["gate_failed"] == "4"
    assert lines["gate_locked_out"] == "1"
    assert lines["admin_step_up_failed"] == "1"
    assert lines["mcp_refresh_reuse"] == "1"
    assert lines["write_commit_refused"] == "1"
    assert lines["indicators"] == "mcp_refresh_reuse,write_commit_refused"

    assert "Shared gate: failed attempts" in text.getvalue()
    assert "Look at: mcp_refresh_reuse, write_commit_refused" in text.getvalue()
    for output in (text.getvalue(), as_json.getvalue(), monitor.getvalue()):
        assert "203.0.113" not in output, "an address reached the output"
        assert "probe/1.0" not in output, "a user agent reached the output"


def test_a_run_writes_nothing() -> None:
    _world()
    before = (
        SecurityAuditEvent.objects.count(),
        McpAccessEvent.objects.count(),
        McpWriteEvent.objects.count(),
    )
    call_command("audit_failures", "--since", "24h", "--format", "monitor", stdout=StringIO())
    assert (
        SecurityAuditEvent.objects.count(),
        McpAccessEvent.objects.count(),
        McpWriteEvent.objects.count(),
    ) == before


def test_an_empty_window_is_quiet() -> None:
    monitor = StringIO()
    call_command("audit_failures", "--since", "1h", "--format", "monitor", stdout=monitor)
    lines = dict(line.split("=", 1) for line in monitor.getvalue().splitlines())
    assert lines["gate_failed"] == "0"
    assert lines["indicators"] == ""
