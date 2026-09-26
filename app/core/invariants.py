"""Rows that already break a rule the services now enforce (ENG-043).

The question a constraint migration has to have answered before it is run: *does
any row in this database break the rule I am about to install?* PostgreSQL
answers it by failing the migration, which is the right answer at the wrong
moment — mid-deployment, with the old release half replaced. This answers it
first, read-only, from the target release against the still-unmigrated database
(deploy/unraid-main/README.md, step 7).

Two kinds of finding, and they are kept apart because they call for different
things:

* **Blocking.** A `NextAction` whose `kind`, `date_semantics`, `date_precision`
  or `status` is outside its vocabulary, or which has no `target_date` and a
  precision other than `EXACT`. `workflow.0009` installs a `CHECK` for each, so
  any one of these makes `migrate` fail.
* **Service rules.** A `Kaasamine` whose reply-by date falls before its round's
  whole period began, and an opinion recorded as sent after today. No `CHECK`
  guards either — the send date is a business-day question and the engagement
  rule is a period question — so they do not stop the migration. The services
  refuse to *write* them since ENG-043; these are rows written before that, by a
  path that has since been closed or by a synthetic generator.
  The three `closed-matter-*` kinds are the same shape for ENG-006: live work a
  closure path left owed on a Matter that is no longer current.

**Nothing is repaired.** A finding is a question about what a record was meant
to say, and only somebody with the register in front of them can answer it. The
report carries primary keys and nothing else: in this corpus a title or a
summary can be the confidential part.

`app.core` does not import the business apps at module level; the models are
read through the app registry, and the one shared rule — the engagement's period
comparison — is imported from the service that owns it, so the report and the
refusal cannot disagree.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from django.apps import apps
from django.db.models import F, Q
from django.utils import timezone

#: The migration that installs the `NextAction` constraints, named in the report
#: so an operator reading a blocking finding knows which step it would fail.
NEXT_ACTION_CONSTRAINT_MIGRATION = "workflow.0009"

#: Finding kinds that make `NEXT_ACTION_CONSTRAINT_MIGRATION` fail.
BLOCKING = frozenset(
    {
        "next-action-kind",
        "next-action-date-semantics",
        "next-action-precision",
        "next-action-status",
        "next-action-undated-period",
    }
)

#: Finding kinds the services refuse but no constraint does.
SERVICE_RULES = frozenset(
    {
        "engagement-deadline-before-round",
        "submission-sent-in-future",
        "closed-matter-open-next-action",
        "closed-matter-planned-website-overview",
        "closed-matter-open-feedback-wait",
    }
)

#: What each kind means, printed once above its rows.
EXPLANATIONS: dict[str, str] = {
    "next-action-kind": "NextAction.kind is not DO, WAIT or MONITOR",
    "next-action-date-semantics": "NextAction.date_semantics is not a DateSemantics value",
    "next-action-precision": "NextAction.date_precision is not a DatePrecision value",
    "next-action-status": "NextAction.status is not an ActionStatus value",
    "next-action-undated-period": "NextAction has no target_date but a precision other than EXACT",
    "engagement-deadline-before-round": (
        "MatterEngagement.feedback_deadline falls before the whole period of occurred_on"
    ),
    "submission-sent-in-future": "Submission.sent_at is after today in Europe/Tallinn",
    # ENG-006. Every path that makes a Matter inactive ends these through
    # `end_live_work_for_closure`; a row here was left by one that did not.
    "closed-matter-open-next-action": "An OPEN NextAction belongs to a closed Matter",
    "closed-matter-planned-website-overview": (
        "A PLANNED MatterWebsiteOverview belongs to a closed Matter"
    ),
    "closed-matter-open-feedback-wait": (
        "A MatterEngagement feedback wait is still open on a closed Matter"
    ),
}


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str
    detail: str


@dataclass
class InvariantReport:
    today: datetime.date
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def by_kind(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault(finding.kind, []).append(finding)
        return grouped

    @property
    def blocking(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.kind in BLOCKING]


def _next_action_findings() -> list[Finding]:
    from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics

    next_action = apps.get_model("workflow", "NextAction")
    rows = next_action.objects.order_by("created_at", "pk")
    checks = (
        ("next-action-kind", ~Q(kind__in=ActionKind.values), "kind"),
        (
            "next-action-date-semantics",
            ~Q(date_semantics__in=DateSemantics.values),
            "date_semantics",
        ),
        ("next-action-precision", ~Q(date_precision__in=DatePrecision.values), "date_precision"),
        ("next-action-status", ~Q(status__in=ActionStatus.values), "status"),
        (
            "next-action-undated-period",
            Q(target_date__isnull=True) & ~Q(date_precision=DatePrecision.EXACT),
            "date_precision",
        ),
    )
    findings = []
    for kind, condition, column in checks:
        for pk, value in rows.filter(condition).values_list("pk", column):
            findings.append(Finding(kind=kind, subject=str(pk), detail=f"{column}={value!r}"))
    return findings


def _engagement_findings() -> list[Finding]:
    from app.matters.services import feedback_deadline_precedes_engagement

    engagement = apps.get_model("matters", "MatterEngagement")
    # A deadline before the whole period is always before the stored value too,
    # so SQL narrows to candidates and the service's own rule decides.
    candidates = (
        engagement.objects.filter(
            occurred_on__isnull=False,
            feedback_deadline__isnull=False,
            feedback_deadline__lt=F("occurred_on"),
        )
        .order_by("created_at", "pk")
        .values_list("pk", "occurred_on", "occurred_on_precision", "feedback_deadline")
    )
    return [
        Finding(
            kind="engagement-deadline-before-round",
            subject=str(pk),
            # `str()` of a date is its ISO form; both are non-null by the filter.
            detail=f"occurred_on={occurred_on} ({precision}), feedback_deadline={deadline}",
        )
        for pk, occurred_on, precision, deadline in candidates
        if feedback_deadline_precedes_engagement(occurred_on, precision, deadline)
    ]


def _submission_findings(today: datetime.date) -> list[Finding]:
    submission = apps.get_model("submissions", "Submission")
    # The first instant of tomorrow in Tallinn: anything at or after it is a
    # send dated after today, whatever zone it was stored in.
    tomorrow = timezone.make_aware(
        datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min)
    )
    rows = (
        submission.objects.filter(sent_at__gte=tomorrow)
        .order_by("sent_at", "pk")
        .values_list("pk", "status", "sent_at")
    )
    return [
        Finding(
            kind="submission-sent-in-future",
            subject=str(pk),
            detail=f"status={status}, sent_on={timezone.localtime(sent_at).date().isoformat()}",
        )
        for pk, status, sent_at in rows
    ]


def _closed_matter_findings() -> list[Finding]:
    """Live work still owed on a Matter that is no longer current (ENG-006).

    The engineering audit's Q02, Q16 and Q17. A closure — by a person, by the
    final register or by the historical default — ends the open step, cancels a
    planned write-up and closes a feedback wait; each row reported here is one a
    closure path left behind before they all went through one helper.
    """
    next_action = apps.get_model("workflow", "NextAction")
    overview = apps.get_model("matters", "MatterWebsiteOverview")
    engagement = apps.get_model("matters", "MatterEngagement")
    findings = [
        Finding(kind="closed-matter-open-next-action", subject=str(pk), detail=f"matter={matter}")
        for pk, matter in next_action.objects.filter(status="OPEN", matter__is_open=False)
        .order_by("pk")
        .values_list("pk", "matter_id")
    ]
    findings.extend(
        Finding(
            kind="closed-matter-planned-website-overview",
            subject=str(pk),
            detail=f"matter={matter}",
        )
        for pk, matter in overview.objects.filter(status="PLANNED", matter__is_open=False)
        .order_by("pk")
        .values_list("pk", "matter_id")
    )
    findings.extend(
        Finding(kind="closed-matter-open-feedback-wait", subject=str(pk), detail=f"matter={matter}")
        for pk, matter in engagement.objects.filter(
            matter__is_open=False,
            feedback_deadline__isnull=False,
            feedback_closed_at__isnull=True,
        )
        .order_by("pk")
        .values_list("pk", "matter_id")
    )
    return findings


def check_domain_invariants(*, today: datetime.date | None = None) -> InvariantReport:
    """Every row breaking one of the rules above. Reads; never writes."""
    day = today or timezone.localdate()
    report = InvariantReport(today=day)
    report.findings.extend(_next_action_findings())
    report.findings.extend(_engagement_findings())
    report.findings.extend(_submission_findings(day))
    report.findings.extend(_closed_matter_findings())
    return report
