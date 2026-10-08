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
  path that has since been closed or by a synthetic generator. A `Kaasamine`,
  `Väline seisukoht`, `Märge` or published `Ülevaade` dated after today is **not**
  a finding any more: ENG-004's refusal was withdrawn by docs/adr/0121 §3, and
  such a row is valid planned data that Teema käik marks `Eesolev`.
  The three `closed-matter-*` kinds are the same shape for ENG-006: live work a
  closure path left owed on a Matter that is no longer current. The last four
  are ENG-144's: a record joined to or described under another Matter's file,
  and a person's closure with no event behind it.

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
from typing import Any

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Exists, F, OuterRef, Q, Subquery
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
        "document-link-crosses-matter",
        "external-position-crosses-matter",
        "change-event-on-another-matter",
        "response-deadline-submission-crosses-matter",
        "closed-matter-without-closure-event",
        "stage-episode-stage-mismatch",
        "stage-episode-missing",
        "stage-episode-without-matter-stage",
        "terminal-stage-on-open-matter",
        "change-event-episode-on-another-matter",
        "follow-up-check-crosses-matter",
        "follow-up-live-check-after-monitoring-ended",
        "follow-up-monitoring-without-live-check",
        "follow-up-live-check-on-unsent-opinion",
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
    "closed-matter-open-next-action": ("An OPEN or PLANNED NextAction belongs to a closed Matter"),
    "closed-matter-planned-website-overview": (
        "A PLANNED MatterWebsiteOverview belongs to a closed Matter"
    ),
    "closed-matter-open-feedback-wait": (
        "A MatterEngagement feedback wait is still open on a closed Matter"
    ),
    # ENG-144. The audit's reproduced HARD queries no check above covered; each
    # is a rule every service keeps and no constraint can.
    "document-link-crosses-matter": (
        "A DocumentLink joins a document to a record on a different Matter"
    ),
    "external-position-crosses-matter": (
        "A live MatterExternalPosition answers a MatterEngagement of a different Matter"
    ),
    "change-event-on-another-matter": (
        "A ChangeEvent about a Matter's record is filed under a different Matter"
    ),
    "response-deadline-submission-crosses-matter": (
        "An ended Arvamuse tähtaeg names an answering Submission of a different Matter"
    ),
    "closed-matter-without-closure-event": (
        "A closed FULL Matter has no MATTER_CLOSED event: it was closed around close_matter"
    ),
    # docs/adr/0131. At most one current period per Matter, a start before an
    # end and a stage on every period are CHECK/UNIQUE constraints; these are the
    # rules only the services keep.
    "stage-episode-stage-mismatch": (
        "A Matter's current MatterStageEpisode names a different stage than Matter.stage"
    ),
    "stage-episode-missing": (
        "An open FULL Matter holds a stage but has no current MatterStageEpisode"
    ),
    "stage-episode-without-matter-stage": (
        "A Matter with no stage (Määramata) still has a current MatterStageEpisode"
    ),
    "terminal-stage-on-open-matter": (
        "An open Matter's recorded current period is a stage that ends a Matter"
    ),
    "change-event-episode-on-another-matter": (
        "A ChangeEvent is bound to a MatterStageEpisode of a different Matter"
    ),
    # docs/adr/0146. One planned or current check per follow-up is a UNIQUE
    # index; these are the rules only the services keep.
    "follow-up-check-crosses-matter": (
        "An Arvamuse järelkontroll check is on a different Matter than its opinion"
    ),
    "follow-up-live-check-after-monitoring-ended": (
        "A planned or current check belongs to a follow-up that is no longer MONITORING"
    ),
    "follow-up-monitoring-without-live-check": (
        "A follow-up is MONITORING but has no planned or current check: the check was lost"
    ),
    "follow-up-live-check-on-unsent-opinion": (
        "A planned or current check is about an opinion that is no longer SENT"
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
        for pk, matter in next_action.objects.filter(
            status__in=("OPEN", "PLANNED"), matter__is_open=False
        )
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
            lifecycle_tracked=True,
            feedback_closed_at__isnull=True,
        )
        .order_by("pk")
        .values_list("pk", "matter_id")
    )
    return findings


def _event_subjects() -> list[tuple[Any, str]]:
    """Every model a `ChangeEvent` can be about that belongs to exactly one Matter.

    Derived from the registry rather than listed, so a Matter-owned model added
    later is checked without anybody remembering to add it here — the audit's
    own caution about its hand-written list (ENG-144). A nullable `matter` is a
    staging row whose Matter is chosen later, and is left out: its events are
    not about a record on a file. A version belongs to its document's Matter.
    """
    subjects: list[tuple[Any, str]] = []
    for model in apps.get_models():
        if model._meta.label == "audit.ChangeEvent":
            continue
        try:
            owner = model._meta.get_field("matter")
        except FieldDoesNotExist:
            continue
        related = getattr(owner, "related_model", None)
        if owner.concrete and not owner.null and related is not None:
            if related._meta.label == "matters.Matter":
                subjects.append((model, "matter_id"))
    subjects.append((apps.get_model("documents", "DocumentVersion"), "document__matter_id"))
    return subjects


def _cross_matter_findings() -> list[Finding]:
    """A record joined to, or described under, another Matter's file (ENG-144).

    The engineering audit's Q29, Q43 and Q46, each reproduced there through a
    path since closed (ENG-008's admin, ENG-047's removal). Every service that
    writes one of these refuses the cross-Matter case — `link_document_to_record`,
    the `Väline seisukoht` form's own list of rounds, `record_change_event`
    called with the record's own Matter — and nothing in the schema can, so a
    row here arrived around them. A position answering a round *taken off the
    file* is not one of them: that link is kept on purpose (ENG-047).
    """
    from app.documents.links import TARGET_FIELDS

    link = apps.get_model("documents", "DocumentLink")
    position = apps.get_model("matters", "MatterExternalPosition")
    event = apps.get_model("audit", "ChangeEvent")

    findings: list[Finding] = []
    for field_name in TARGET_FIELDS:
        rows = (
            link._base_manager.filter(**{f"{field_name}__isnull": False})
            .exclude(**{f"{field_name}__matter_id": F("document__matter_id")})
            .order_by("pk")
            .values_list("pk", "document__matter_id", f"{field_name}__matter_id")
        )
        findings.extend(
            Finding(
                kind="document-link-crosses-matter",
                subject=str(pk),
                detail=f"{field_name}: document matter={document}, record matter={record}",
            )
            for pk, document, record in rows
        )

    findings.extend(
        Finding(
            kind="external-position-crosses-matter",
            subject=str(pk),
            detail=f"matter={matter}, engagement matter={engagement}",
        )
        for pk, matter, engagement in position._base_manager.filter(
            engagement__isnull=False, removed_at__isnull=True
        )
        .exclude(engagement__matter_id=F("matter_id"))
        .order_by("pk")
        .values_list("pk", "matter_id", "engagement__matter_id")
    )

    ended_deadline = apps.get_model("matters", "MatterResponseDeadline")
    findings.extend(
        Finding(
            kind="response-deadline-submission-crosses-matter",
            subject=str(pk),
            detail=f"deadline matter={matter}, submission matter={submission_matter}",
        )
        for pk, matter, submission_matter in ended_deadline._base_manager.filter(
            submission__isnull=False
        )
        .exclude(submission__matter_id=F("matter_id"))
        .order_by("pk")
        .values_list("pk", "matter_id", "submission__matter_id")
    )

    for model, owner_path in _event_subjects():
        owner = model._base_manager.filter(pk=OuterRef("object_id")).values(owner_path)[:1]
        events = (
            event.objects.filter(object_type=model._meta.label, object_id__isnull=False)
            .annotate(owner=Subquery(owner))
            .filter(owner__isnull=False)
            .filter(Q(matter__isnull=True) | ~Q(matter_id=F("owner")))
            .order_by("occurred_at", "pk")
            .values_list("pk", "event_type", "matter_id", "owner")
        )
        findings.extend(
            Finding(
                kind="change-event-on-another-matter",
                subject=str(pk),
                detail=(
                    f"{event_type} about {model._meta.label}: "
                    f"filed under matter={matter}, record matter={owner_id}"
                ),
            )
            for pk, event_type, matter, owner_id in events
        )
    return findings


def _unrecorded_closure_findings() -> list[Finding]:
    """A person's closure with no `MATTER_CLOSED` behind it (ENG-144, the audit's Q59).

    `close_matter` is the only path that leaves a FULL Matter closed — the
    register's retirements and the historical default leave it ARCHIVE, the
    importer creates FULL Matters open, and promotion refuses a closed one — and
    it writes the event in the same transaction. So a closed FULL Matter without
    one was closed around the service, which is how the audit made it: the
    admin that ENG-008 has since made read-only. Tombstones are Q14's business.
    """
    from app.audit.enums import ChangeEventType
    from app.matters.enums import RecordMode

    matter = apps.get_model("matters", "Matter")
    event = apps.get_model("audit", "ChangeEvent")
    closed = event.objects.filter(
        matter_id=OuterRef("pk"), event_type=ChangeEventType.MATTER_CLOSED
    )
    return [
        Finding(kind="closed-matter-without-closure-event", subject=str(pk), detail="")
        for pk in matter._base_manager.filter(
            is_open=False, record_mode=RecordMode.FULL, deleted_at__isnull=True
        )
        .exclude(Exists(closed))
        .order_by("pk")
        .values_list("pk", flat=True)
    ]


def _stage_episode_findings() -> list[Finding]:
    """`Hetkeseis` periods out of step with `Matter.stage` (docs/adr/0131).

    The database already refuses two current periods, an end before a start and
    a period with no stage. What only the transition service keeps is that the
    current period *is* the Matter's stage, that an open Matter holding a stage
    has one, and that a recorded move to a stage which ends a Matter closed it.

    **A carried-over period is not a finding for the last rule.** An open Matter
    that already stood in «Jõustunud» before stages could close a file is
    truthful legacy data, carried over as it was; only a period this system
    *recorded* as terminal on a file still open is reported.
    """
    from app.matters.enums import RecordMode, StageEpisodeOrigin
    from app.workflow.stage_flow import TERMINAL_STAGE_KEYS

    matter = apps.get_model("matters", "Matter")
    episode = apps.get_model("matters", "MatterStageEpisode")
    event = apps.get_model("audit", "ChangeEvent")
    current = episode.objects.filter(is_current=True)

    findings = [
        Finding(kind="stage-episode-stage-mismatch", subject=str(pk), detail=f"matter={owner}")
        for pk, owner in current.filter(matter__stage__isnull=False)
        .exclude(stage_id=F("matter__stage_id"))
        .order_by("pk")
        .values_list("pk", "matter_id")
    ]
    findings.extend(
        Finding(
            kind="stage-episode-without-matter-stage", subject=str(pk), detail=f"matter={owner}"
        )
        for pk, owner in current.filter(matter__stage__isnull=True)
        .order_by("pk")
        .values_list("pk", "matter_id")
    )
    findings.extend(
        Finding(kind="stage-episode-missing", subject=str(pk), detail="")
        for pk in matter._base_manager.filter(
            deleted_at__isnull=True,
            is_open=True,
            record_mode=RecordMode.FULL,
            stage__isnull=False,
        )
        .exclude(Exists(current.filter(matter_id=OuterRef("pk"))))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    findings.extend(
        Finding(kind="terminal-stage-on-open-matter", subject=str(pk), detail=f"matter={owner}")
        for pk, owner in current.filter(
            matter__is_open=True,
            matter__deleted_at__isnull=True,
            origin=StageEpisodeOrigin.RECORDED,
            stage__key__in=TERMINAL_STAGE_KEYS,
        )
        .order_by("pk")
        .values_list("pk", "matter_id")
    )
    findings.extend(
        Finding(
            kind="change-event-episode-on-another-matter",
            subject=str(pk),
            detail=f"matter={owner}",
        )
        for pk, owner in event.objects.filter(stage_episode__isnull=False)
        .exclude(stage_episode__matter_id=F("matter_id"))
        .order_by("pk")
        .values_list("pk", "matter_id")
    )
    return findings


def _follow_up_findings() -> list[Finding]:
    """`Arvamuse järelkontroll` rows the services keep consistent (docs/adr/0146)."""
    next_action = apps.get_model("workflow", "NextAction")
    follow_up = apps.get_model("workflow", "OpinionFollowUp")
    live = ("OPEN", "PLANNED")
    checks = next_action.objects.filter(follow_up__isnull=False).order_by("created_at", "pk")
    findings = [
        Finding(kind="follow-up-check-crosses-matter", subject=str(pk), detail=f"matter={matter}")
        for pk, matter in checks.exclude(matter=F("follow_up__submission__matter")).values_list(
            "pk", "matter_id"
        )
    ]
    findings += [
        Finding(
            kind="follow-up-live-check-after-monitoring-ended",
            subject=str(pk),
            detail=f"follow_up_state={state}",
        )
        for pk, state in checks.filter(status__in=live)
        .exclude(follow_up__state="MONITORING")
        .values_list("pk", "follow_up__state")
    ]
    findings += [
        Finding(
            kind="follow-up-live-check-on-unsent-opinion",
            subject=str(pk),
            detail=f"submission_status={status}",
        )
        for pk, status in checks.filter(status__in=live)
        .exclude(follow_up__submission__status="SENT")
        .values_list("pk", "follow_up__submission__status")
    ]
    has_live = next_action.objects.filter(follow_up=OuterRef("pk"), status__in=live)
    findings += [
        Finding(
            kind="follow-up-monitoring-without-live-check",
            subject=str(pk),
            detail=f"submission={submission}",
        )
        for pk, submission in follow_up.objects.filter(state="MONITORING")
        .exclude(Exists(has_live))
        .order_by("created_at", "pk")
        .values_list("pk", "submission_id")
    ]
    return findings


def check_domain_invariants(*, today: datetime.date | None = None) -> InvariantReport:
    """Every row breaking one of the rules above. Reads; never writes."""
    day = today or timezone.localdate()
    report = InvariantReport(today=day)
    report.findings.extend(_next_action_findings())
    report.findings.extend(_engagement_findings())
    report.findings.extend(_submission_findings(day))
    report.findings.extend(_closed_matter_findings())
    report.findings.extend(_cross_matter_findings())
    report.findings.extend(_unrecorded_closure_findings())
    report.findings.extend(_stage_episode_findings())
    report.findings.extend(_follow_up_findings())
    return report
