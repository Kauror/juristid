"""Count the security events of the last while that somebody should look at. Reads only.

    python manage.py audit_failures --since 24h
    python manage.py audit_failures --since 7d --json
    python manage.py audit_failures --since 24h --format monitor

The audit tables record every failed gate attempt (with its reason), every
refused administrator step-up, every MCP lock, suspension and refresh-token
reuse, every refused write commit — and nothing reads them unless a person
opens a database shell (security review 2026-10-29, F-07; docs/mcp/OPERATIONS.md
"Nothing pages anybody"). This command is the reader: counts per event, never a
row. Thresholds are not its business — `scripts/deploy/juristid-monitor.sh`
applies the ones the owner chose — so the exit status is 0 whenever the counts
could be read.

What a line may carry: an event name, a reason, a count. Never an address, a
person, a user agent or a detail body; the `distinct_clients` figure is a
count of distinct client addresses, not the addresses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from app.audit.enums import SecurityEventType

#: `--since` accepts `<n>h` or `<n>d`; nothing else, so a typo is a refusal.
_WINDOW = re.compile(r"^(?P<amount>[1-9][0-9]*)(?P<unit>[hd])$")

#: MCP access events that are a refusal or an indicator, whatever `succeeded` says.
MCP_ACCESS_WATCHED = (
    "GRANT_REFUSED",
    "ENROLMENT_REFUSED",
    "AUTHORIZATION_REFUSED",
    "GRANT_LOCKED",
    "GRANT_SUSPENDED",
    "TOKEN_REFUSED",
    "REFRESH_REUSE_DETECTED",
    "WRITE_REFUSED",
    "WRITE_CONFIRMATION_REFUSED",
)

#: MCP write-chain events that are a refusal or a failure.
MCP_WRITE_WATCHED = (
    "PREPARE_REFUSED",
    "APPROVAL_REFUSED",
    "COMMIT_REFUSED",
    "FAILED",
    "EXPIRED",
)


def window_of(raw: str) -> timedelta:
    match = _WINDOW.match(raw.strip())
    if match is None:
        raise CommandError("--since must be a whole number of hours or days, like 24h or 7d")
    amount = int(match.group("amount"))
    return timedelta(hours=amount) if match.group("unit") == "h" else timedelta(days=amount)


@dataclass
class Failures:
    since_hours: int
    gate_failed: int = 0
    gate_locked_out: int = 0
    gate_distinct_clients: int = 0
    personal_sign_in_failed: int = 0
    admin_step_up_failed: int = 0
    access_refused: int = 0
    sessions_ended_by_rotation: int = 0
    emergency_disabled: int = 0
    mfa_recovery_codes_used: int = 0
    mcp_access: dict[str, int] = field(default_factory=dict)
    mcp_write: dict[str, int] = field(default_factory=dict)

    @property
    def indicators(self) -> list[str]:
        """The events whose single occurrence is worth a look, whatever the thresholds."""
        found = []
        if self.mcp_access.get("REFRESH_REUSE_DETECTED"):
            found.append("mcp_refresh_reuse")
        if self.mcp_access.get("GRANT_SUSPENDED"):
            found.append("mcp_suspended")
        if self.emergency_disabled:
            found.append("emergency_disabled")
        if self.mcp_write.get("COMMIT_REFUSED"):
            found.append("write_commit_refused")
        return found

    def as_dict(self) -> dict[str, Any]:
        return {
            "since_hours": self.since_hours,
            "gate": {
                "failed": self.gate_failed,
                "locked_out": self.gate_locked_out,
                "distinct_clients": self.gate_distinct_clients,
            },
            "personal_sign_in_failed": self.personal_sign_in_failed,
            "admin_step_up_failed": self.admin_step_up_failed,
            "access_refused": self.access_refused,
            "sessions_ended_by_rotation": self.sessions_ended_by_rotation,
            "emergency_disabled": self.emergency_disabled,
            "mfa_recovery_codes_used": self.mfa_recovery_codes_used,
            "mcp_access": dict(self.mcp_access),
            "mcp_write": dict(self.mcp_write),
            "indicators": self.indicators,
        }

    def as_monitor_lines(self) -> list[str]:
        """`key=value`, one per line, for a shell wrapper to read without a JSON parser."""
        return [
            f"since_hours={self.since_hours}",
            f"gate_failed={self.gate_failed}",
            f"gate_locked_out={self.gate_locked_out}",
            f"gate_distinct_clients={self.gate_distinct_clients}",
            f"personal_sign_in_failed={self.personal_sign_in_failed}",
            f"admin_step_up_failed={self.admin_step_up_failed}",
            f"access_refused={self.access_refused}",
            f"sessions_ended_by_rotation={self.sessions_ended_by_rotation}",
            f"emergency_disabled={self.emergency_disabled}",
            f"mfa_recovery_codes_used={self.mfa_recovery_codes_used}",
            f"mcp_refresh_reuse={self.mcp_access.get('REFRESH_REUSE_DETECTED', 0)}",
            f"mcp_locked={self.mcp_access.get('GRANT_LOCKED', 0)}",
            f"mcp_suspended={self.mcp_access.get('GRANT_SUSPENDED', 0)}",
            f"mcp_token_refused={self.mcp_access.get('TOKEN_REFUSED', 0)}",
            f"mcp_authorization_refused={self.mcp_access.get('AUTHORIZATION_REFUSED', 0)}",
            f"write_prepare_refused={self.mcp_write.get('PREPARE_REFUSED', 0)}",
            f"write_approval_refused={self.mcp_write.get('APPROVAL_REFUSED', 0)}",
            f"write_commit_refused={self.mcp_write.get('COMMIT_REFUSED', 0)}",
            f"write_failed={self.mcp_write.get('FAILED', 0)}",
            f"indicators={','.join(self.indicators)}",
        ]


def failures_since(window: timedelta) -> Failures:
    """The counts. Every query is a filter and a count or a distinct count; no row leaves."""
    from django.db.models import Count

    from app.audit.models import SecurityAuditEvent
    from app.mcp_access.models import McpAccessEvent
    from app.mcp_writes.models import McpWriteEvent

    start = timezone.now() - window
    found = Failures(since_hours=int(window.total_seconds() // 3600))

    failed = SecurityAuditEvent.objects.filter(
        occurred_at__gte=start,
        event_type=SecurityEventType.AUTHENTICATION_FAILED,
        succeeded=False,
    )
    gate = failed.filter(detail__path="shared_gate")
    found.gate_failed = gate.count()
    found.gate_locked_out = gate.filter(detail__reason="locked_out").count()
    found.gate_distinct_clients = (
        gate.exclude(ip_address=None).values("ip_address").distinct().count()
    )
    found.admin_step_up_failed = failed.filter(detail__path="administrator_session").count()
    found.personal_sign_in_failed = (
        failed.exclude(detail__path="shared_gate")
        .exclude(detail__path="administrator_session")
        .count()
    )
    found.access_refused = SecurityAuditEvent.objects.filter(
        occurred_at__gte=start, event_type=SecurityEventType.ACCESS_REFUSED
    ).count()
    found.sessions_ended_by_rotation = SecurityAuditEvent.objects.filter(
        occurred_at__gte=start,
        event_type=SecurityEventType.SESSION_ENDED,
        detail__reason="shared_password_changed",
    ).count()
    found.emergency_disabled = SecurityAuditEvent.objects.filter(
        occurred_at__gte=start, event_type=SecurityEventType.EMERGENCY_ACCOUNT_DISABLED
    ).count()
    found.mfa_recovery_codes_used = SecurityAuditEvent.objects.filter(
        occurred_at__gte=start, event_type=SecurityEventType.MFA_RECOVERY_CODE_USED
    ).count()

    for row in (
        McpAccessEvent.objects.filter(created_at__gte=start, event_type__in=MCP_ACCESS_WATCHED)
        .values("event_type")
        .annotate(n=Count("id"))
    ):
        found.mcp_access[row["event_type"]] = row["n"]
    for row in (
        McpWriteEvent.objects.filter(created_at__gte=start, event_type__in=MCP_WRITE_WATCHED)
        .values("event_type")
        .annotate(n=Count("id"))
    ):
        found.mcp_write[row["event_type"]] = row["n"]
    return found


class Command(BaseCommand):
    help = "Count recent security-relevant failures from the audit tables. Reads only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--since", default="24h", help="The window: <n>h or <n>d (default 24h)."
        )
        parser.add_argument(
            "--json", action="store_true", dest="as_json", help="Emit the counts as JSON."
        )
        parser.add_argument(
            "--format",
            choices=["text", "monitor"],
            default="text",
            help="`monitor` prints key=value lines for scripts/deploy/juristid-monitor.sh.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        found = failures_since(window_of(options["since"]))
        if options["as_json"]:
            self.stdout.write(json.dumps(found.as_dict(), indent=2, sort_keys=True))
            return
        if options["format"] == "monitor":
            for line in found.as_monitor_lines():
                self.stdout.write(line)
            return
        self.stdout.write(f"SECURITY FAILURES, last {found.since_hours}h")
        self.stdout.write("")
        rows = [
            ("Shared gate: failed attempts", found.gate_failed),
            ("Shared gate: refused while locked out", found.gate_locked_out),
            ("Shared gate: distinct client addresses", found.gate_distinct_clients),
            ("Personal sign-in: failed attempts", found.personal_sign_in_failed),
            ("Administrator step-up: failed attempts", found.admin_step_up_failed),
            ("Security-sensitive actions refused", found.access_refused),
            ("Sessions ended by a password rotation", found.sessions_ended_by_rotation),
            ("Emergency account disablements", found.emergency_disabled),
            ("Recovery codes used", found.mfa_recovery_codes_used),
        ]
        for label, count in rows:
            self.stdout.write(f"{label:<44}{count:>6}")
        for label, counts in (("MCP access", found.mcp_access), ("MCP writes", found.mcp_write)):
            self.stdout.write("")
            self.stdout.write(label)
            if not counts:
                self.stdout.write("  (nothing refused)")
            for event, count in sorted(counts.items()):
                self.stdout.write(f"  {event:<42}{count:>6}")
        if found.indicators:
            self.stdout.write("")
            self.stdout.write("Look at: " + ", ".join(found.indicators))
