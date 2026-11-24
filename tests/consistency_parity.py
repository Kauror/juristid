"""Parity checks for the batched docs/adr/0161 rule (amendment of 2026-11-25).

Two references, both called from every scenario in
`tests/test_workflow_consistency.py` through its `_alert` helper, so each of
those fixtures is also a parity case:

* `reference_recorded_work` is `RecordedWork.of` **as it was before the batched
  form**, copied verbatim — one SQL query per fact, ``since`` narrowed in SQL.
  `RecordedWork.of` (now `for_matters` asked about one Matter, the narrowing
  decided in Python) must give the same answer.
* `batched_alert` asks the rule the way Minu asjad does — the reader's own work
  rows, every one of them at once (`with_consistency_alerts`) — and returns
  what it said about one step. It must equal the Teema page's single answer.

Synthetic data only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db.models import Q
from django.utils import timezone

from app.matters import work_items as wi
from app.matters.enums import WebsiteOverviewStatus
from app.matters.plan_view import RecordedWork
from app.matters.publication_kind import overview_fulfils_step
from app.workflow.consistency import with_consistency_alerts


def reference_recorded_work(
    matter: Any, viewer: Any, *, since: datetime | None = None
) -> RecordedWork:
    """`RecordedWork.of` at e344a474, verbatim apart from the name."""
    from app.matters.models import MatterEngagement, MatterWebsiteOverview
    from app.matters.work_items import response_obligation_of
    from app.submissions.enums import SubmissionStatus
    from app.submissions.models import Submission

    overviews = MatterWebsiteOverview.objects.visible_to(viewer).filter(
        matter=matter, status=WebsiteOverviewStatus.PUBLISHED
    )
    engagements = MatterEngagement.objects.visible_to(viewer).filter(matter=matter)
    filed = engagements
    feedback = engagements.filter(feedback_closed_at__isnull=False)
    sent = Submission.objects.visible_to(viewer).filter(matter=matter, status=SubmissionStatus.SENT)
    if since is not None:
        overviews = overviews.filter(
            Q(created_at__gt=since) | Q(published_on__gt=timezone.localtime(since).date())
        )
        filed = engagements.filter(created_at__gt=since)
        feedback = engagements.filter(feedback_closed_at__gt=since)
        sent = sent.filter(Q(created_at__gt=since) | Q(sent_at__gt=since))
    published = list(overviews.only("status", "kind", "url"))
    return RecordedWork(
        published_overview=any(overview_fulfils_step(overview) for overview in published),
        published_any=bool(published),
        engagement=filed.exists(),
        feedback_recorded=feedback.exists(),
        opinion_sent=sent.exists(),
        opinion_owed=response_obligation_of(matter, viewer).is_outstanding,
    )


def work_rows(viewer: Any) -> list[wi.WorkItem]:
    """Every live step row this reader may see, dated and undated, as Minu asjad reads them."""
    today = timezone.localdate()
    rows = [wi.action_item(action, today) for action in wi.dated_actions(viewer)]
    rows += [wi.action_item(action, today) for action in wi.undated_actions(viewer)]
    return rows


def batched_alert(matter: Any, action: Any, viewer: Any) -> Any:
    """What the batched rule says about ``action``, asked of every row at once.

    ``None`` when no row was flagged for it — and, with no action, asserts that
    no row on ``matter`` was flagged at all.
    """
    flagged = with_consistency_alerts(work_rows(viewer), viewer)
    if action is None:
        assert not [
            row for row in flagged if row.matter.pk == matter.pk and row.consistency_alert
        ], "a Matter with no current step had a flagged row"
        return None
    hits = [
        row.consistency_alert for row in flagged if row.is_action and row.object_id == action.pk
    ]
    assert len(hits) <= 1
    return hits[0] if hits else None


def assert_parity(matter: Any, current: Any, viewer: Any, single: Any) -> None:
    """The batched readings equal the single one for this scenario."""
    assert batched_alert(matter, current, viewer) == single, "batched rule disagreed with Teema"
    for since in (None, getattr(current, "created_at", None)):
        assert RecordedWork.of(matter, viewer, since=since) == reference_recorded_work(
            matter, viewer, since=since
        ), f"RecordedWork drifted from its reference (since={since})"
