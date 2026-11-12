"""Lovable's unclassified step (`ActionKind.SOURCE`) in notifications and the work lists.

The r54 synchronisation writes Lovable's `jargmiseks` as the current step even
when the wording states no kind («Liik määramata», docs/adr/0213). Such a step
is never late and comes round for a look on its day (`REVIEW_KINDS`). Two
readers had not followed it (2026-11-11 audit, §C.1):

* **DL-01** — the `REVIEW_DATE` notification population listed WAIT and MONITOR
  by hand, so an unclassified step was never notified at all.
* **DL-02** — `outstanding_response_deadlines` let any open step but a WAIT
  outrank the `Arvamuse tähtaeg`. An unclassified step says neither that the
  lawyer did the work nor that somebody else took it on, and it is never late,
  so a passed opinion deadline beside one left every work list, the on-the-day
  notice and the escalation. ADR 0050's amendment of 2026-11-12 makes it a
  `DEADLINE_KEEPING_KINDS` step, like a WAIT.

DO, WAIT and MONITOR keep exactly the semantics they had; the parametrised
cases say so beside each SOURCE case.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest

from app.matters import department_dashboard as dd
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.register_filters import register_population
from app.matters.selectors import response_deadline_of
from app.notifications.enums import NotificationCategory, NotificationKind, NotificationPurpose
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    Disposition,
)
from tests import factories
from tests.factories import send_opinion_through_services
from tests.test_deadline_notifications import (
    TODAY,
    _feature_started,
    _opinion,
    _personal,
    _run,
    _set,
    _step,
)

pytestmark = pytest.mark.django_db

REVIEW = NotificationCategory.REVIEW_DATE
OPINION = NotificationCategory.OPINION_DEADLINE
ACTION = NotificationCategory.ACTION_DEADLINE

#: Every review kind, each with the date meaning the review population reads.
REVIEW_CASES = [
    (ActionKind.SOURCE, DateSemantics.REVIEW_ON),
    (ActionKind.SOURCE, DateSemantics.EXPECTED_AROUND),
    (ActionKind.WAIT, DateSemantics.REVIEW_ON),
    (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
    (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
    (ActionKind.MONITOR, DateSemantics.EXPECTED_AROUND),
]


def _review_step(owner, *, on, kind=ActionKind.SOURCE, semantics=DateSemantics.REVIEW_ON, **matter):
    return _step(
        factories.MatterFactory(owner=owner, **matter),
        on=on,
        kind=kind,
        date_semantics=semantics,
        text="Eelnõu arutelu komisjonis",
    )


def _review_rows(**filters):
    return _personal(category=REVIEW, **filters)


# ---------------------------------------------------------------------------
# DL-01: an unclassified step is a review date like any other
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "semantics"), REVIEW_CASES)
def test_a_review_step_dated_today_is_told_on_the_day(specialist, kind, semantics):
    """Regression: DL-01 — the SOURCE cases; WAIT and MONITOR unchanged beside them."""
    _set(specialist, REVIEW, enabled=True)
    step = _review_step(specialist, on=TODAY, kind=kind, semantics=semantics)

    _run(TODAY)

    (row,) = _review_rows(recipient=specialist)
    assert (row.source_id, row.purpose, row.kind) == (
        step.pk,
        NotificationPurpose.ON_DAY,
        NotificationKind.INFO,
    )


@pytest.mark.parametrize("kind", [ActionKind.SOURCE, ActionKind.WAIT, ActionKind.MONITOR])
def test_an_approaching_review_step_gets_the_advance_notice(specialist, kind):
    """Regression: DL-01 — the advance interval the person chose, for every review kind."""
    _set(specialist, REVIEW, enabled=True, offsets=(3,))
    step = _review_step(specialist, on=TODAY + timedelta(days=3), kind=kind)

    _run(TODAY)

    (row,) = _review_rows(recipient=specialist)
    assert (row.source_id, row.purpose, row.offset_days) == (
        step.pk,
        NotificationPurpose.ADVANCE,
        3,
    )


@pytest.mark.parametrize("kind", [ActionKind.SOURCE, ActionKind.WAIT, ActionKind.MONITOR])
def test_a_passed_review_step_is_caught_up_and_never_late_or_escalated(
    specialist, department_head, kind
):
    """Regression: DL-01 — a review two days ago is still told (INFO), never escalated."""
    _feature_started(TODAY - timedelta(days=10))
    _set(specialist, REVIEW, enabled=True)
    step = _review_step(specialist, on=TODAY - timedelta(days=2), kind=kind)

    _run(TODAY)

    (row,) = _review_rows(recipient=specialist)
    assert (row.source_id, row.purpose, row.kind) == (
        step.pk,
        NotificationPurpose.ON_DAY,
        NotificationKind.INFO,
    )
    assert _personal(recipient=department_head) == []
    assert _personal(category=ACTION) == []


def test_an_overdue_do_deadline_is_unchanged(specialist, department_head):
    """Regression: DL-01 — the fix widens the review population only."""
    _feature_started(TODAY - timedelta(days=10))
    late = _step(factories.MatterFactory(owner=specialist), on=TODAY - timedelta(days=1))

    _run(TODAY)

    (escalation,) = _personal(recipient=department_head)
    assert (escalation.category, escalation.source_id, escalation.purpose) == (
        ACTION,
        late.pk,
        NotificationPurpose.ESCALATION,
    )
    assert _review_rows() == []


def test_a_second_reconcile_creates_no_second_review_row(specialist):
    """Regression: DL-01 — idempotent like every other population."""
    _set(specialist, REVIEW, enabled=True, offsets=(3,))
    _review_step(specialist, on=TODAY)
    _review_step(specialist, on=TODAY + timedelta(days=3))

    first = _run(TODAY)
    again = _run(TODAY)

    assert first.created == 2
    assert (again.created, again.retired) == (0, 0)
    assert len(_review_rows(recipient=specialist)) == 2


def test_a_closed_or_unreadable_teema_tells_nobody_of_its_review(specialist, administrator):
    """Regression: DL-01 — the wider population is still read as the recipient.

    A closed Teema's step produces nothing, and a step whose named person may
    not read business content (an administrator without a business role,
    docs/adr/0181) reaches nobody. (A deleted Teema's steps are purged with it,
    `matters.deletion.delete_matter`, so there is no tombstoned step to read.)
    """
    for person in (specialist, administrator):
        _set(person, REVIEW, enabled=True)
    # Closed directly, so its step stays open: the population, not the closure
    # service, is what must leave it out.
    _review_step(
        specialist,
        on=TODAY,
        is_open=False,
        closed_at=datetime(2026, 11, 1, tzinfo=UTC),
        disposition=Disposition.COMPLETED,
    )
    unreadable = factories.MatterFactory(owner=specialist)
    _step(
        unreadable,
        on=TODAY,
        kind=ActionKind.SOURCE,
        date_semantics=DateSemantics.REVIEW_ON,
        responsible=administrator,
    )
    assert not Matter.objects.visible_to(administrator).filter(pk=unreadable.pk).exists()

    _run(TODAY)

    assert _review_rows(recipient=specialist) == []
    assert _review_rows(recipient=administrator) == []


# ---------------------------------------------------------------------------
# DL-02: an unclassified step keeps the Arvamuse tähtaeg live
# ---------------------------------------------------------------------------


def _source_step(matter, *, on=None, status=ActionStatus.OPEN):
    return factories.NextActionFactory(
        matter=matter,
        text="Eelnõu arutelu komisjonis",
        kind=ActionKind.SOURCE,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=on,
        date_precision=DatePrecision.EXACT,
        status=status,
    )


def _deadline_rows(user, matter, today=TODAY):
    return [
        item
        for item in wi.work_items(user, today=today)
        if item.matter_id == matter.pk and item.source_type == wi.SOURCE_RESPONSE_DEADLINE
    ]


def _figure_ids(user, key):
    """The Osakond Seis figure ``key``, and the Matters its link opens."""
    (figure,) = [f for f in dd.seis_figures(user, TODAY) if f.key == key]
    params = dict(parse_qsl(urlsplit(figure.url).query))
    return figure.value, set(
        register_population(user, params, today=TODAY).values_list("pk", flat=True)
    )


@pytest.mark.parametrize("days", [10, 0, -3], ids=["future", "today", "passed"])
@pytest.mark.parametrize("dated", [True, False], ids=["dated", "undated"])
def test_an_unclassified_step_leaves_the_deadline_on_the_work_lists(specialist, days, dated):
    """Regression: DL-02 — Minu asjad's work model and the header read it as live."""
    matter = _opinion(specialist, TODAY + timedelta(days=days))
    _source_step(matter, on=TODAY + timedelta(days=30) if dated else None)

    assert wi.outstanding_response_deadlines(specialist).filter(pk=matter.pk).exists()
    assert wi.response_deadline_is_outstanding(matter, specialist)
    (deadline,) = _deadline_rows(specialist, matter)
    assert deadline.is_overdue == (days < 0)
    header = response_deadline_of(matter, specialist, today=TODAY)
    assert header is not None and header.is_overdue == (days < 0)


def test_a_passed_deadline_beside_an_unclassified_step_is_overdue_everywhere(
    specialist, department_head
):
    """Regression: DL-02 — Minu asjad, the register drill-down and Osakond agree."""
    matter = _opinion(specialist, TODAY - timedelta(days=3))
    _source_step(matter, on=TODAY + timedelta(days=5))

    assert build_my_work(specialist, today=TODAY).overdue == 1
    overdue = register_population(
        specialist, {"olek": "avatud", "liik": "FULL", "too": wi.WORK_OVERDUE}, today=TODAY
    )
    assert overdue.filter(pk=matter.pk).exists()
    assert matter.pk in wi.work_population_ids(department_head, wi.WORK_OVERDUE, today=TODAY)
    value, opened = _figure_ids(department_head, "overdue")
    assert matter.pk in opened
    assert value == len(opened)


def test_an_unclassified_step_retires_nothing_and_a_sent_answer_still_does(specialist):
    """Regression: DL-02 — nothing discharges an obligation but the answer itself."""
    matter = _opinion(specialist, TODAY - timedelta(days=3))
    _source_step(matter, on=TODAY + timedelta(days=5))
    assert _deadline_rows(specialist, matter)

    send_opinion_through_services(matter, specialist)

    assert _deadline_rows(specialist, matter) == []
    assert not wi.outstanding_response_deadlines(specialist).filter(pk=matter.pk).exists()


@pytest.mark.parametrize(
    ("kind", "semantics", "keeps"),
    [
        (ActionKind.SOURCE, DateSemantics.REVIEW_ON, True),
        (ActionKind.WAIT, DateSemantics.REVIEW_ON, True),
        (ActionKind.DO, DateSemantics.DEADLINE, False),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON, False),
    ],
)
def test_which_steps_keep_the_deadline(specialist, kind, semantics, keeps):
    """Regression: DL-02 — SOURCE joins WAIT; DO and MONITOR still outrank (ADR 0050)."""
    matter = _opinion(specialist, TODAY - timedelta(days=3))
    _step(matter, on=TODAY + timedelta(days=10), kind=kind, date_semantics=semantics)

    assert bool(_deadline_rows(specialist, matter)) is keeps
    assert wi.response_deadline_is_outstanding(matter, specialist) is keeps
    # Still owed in every case — the precedence never says it was met.
    assert wi.response_obligation_of(matter, specialist, TODAY).is_outstanding


@pytest.mark.parametrize(
    ("kind", "semantics", "keeps"),
    [
        (ActionKind.SOURCE, DateSemantics.REVIEW_ON, True),
        (ActionKind.WAIT, DateSemantics.REVIEW_ON, True),
        (ActionKind.DO, DateSemantics.DEADLINE, False),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON, False),
    ],
)
def test_the_opinion_deadline_is_told_on_the_day_and_escalated_the_next(
    specialist, department_head, kind, semantics, keeps
):
    """Regression: DL-02 — the on-the-day notice and the morning-after escalation."""
    _feature_started(TODAY - timedelta(days=10))
    matter = _opinion(specialist, TODAY)
    _step(matter, on=TODAY + timedelta(days=10), kind=kind, date_semantics=semantics)

    _run(TODAY)
    on_day = _personal(recipient=specialist, category=OPINION, purpose=NotificationPurpose.ON_DAY)
    assert bool(on_day) is keeps

    _run(TODAY + timedelta(days=1))
    escalated = _personal(
        recipient=department_head, category=OPINION, purpose=NotificationPurpose.ESCALATION
    )
    assert [row.matter_id for row in escalated] == ([matter.pk] if keeps else [])


def test_an_answered_deadline_beside_an_unclassified_step_is_told_to_nobody(
    specialist, department_head
):
    """Regression: DL-02 — negative: a sent answer ends the obligation, step or no step."""
    _feature_started(TODAY - timedelta(days=10))
    matter = _opinion(specialist, TODAY - timedelta(days=1))
    _source_step(matter, on=TODAY + timedelta(days=10))
    send_opinion_through_services(matter, specialist)

    _run(TODAY)

    assert _personal(category=OPINION) == []
    assert not wi.outstanding_response_deadlines(specialist).filter(pk=matter.pk).exists()
