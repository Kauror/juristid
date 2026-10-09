"""Osakond's two new strip figures and the one definition of «koostamisel» (docs/adr/0149).

* «N arvamust välja sel nädalal» — SENT opinions from Monday of the current ISO
  week to today, on the Tallinn calendar; recalculated on every read.
* «N arvamust koostamisel» — `drafting_matters`, the same population as the team
  table's ARVAMUS KOOSTAMISEL column and the register's `?arvamus=koostamisel`.
  Besides a readable DRAFT and the register's blank `VÄLJA`, it now recognises
  the native workflow: the open opinion step — «Koostan arvamuse» or the work
  plan's opinion step — while an `Arvamuse tähtaeg` is still owed.

Every title and organisation here is invented.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.matters import department_dashboard as dd
from app.matters import work_items as wi
from app.matters.dashboard import drafting_matters
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter
from app.matters.register_filters import OPINION_DRAFTING, register_population
from app.matters.response_deadlines import (
    deadline_revision,
    request_response_deadline,
    resolve_response_deadline,
)
from app.matters.services import close_matter, create_matter
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import create_submission
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DateSemantics,
    Disposition,
    PlanStepOperation,
)
from app.workflow.models import NextAction
from app.workflow.plan import activate_plan_step, plan_steps_of, seed_standard_plan
from app.workflow.services import (
    OPINION_PREPARATION_TEXT,
    establish_opinion_preparation_action,
    set_next_action,
)
from tests import factories
from tests.follow_ups import send_koja_arvamus

TALLINN = ZoneInfo("Europe/Tallinn")


def local(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=TALLINN)


@pytest.fixture
def send(capture_evidence, specialist):
    """A SENT Submission with the evidence the database insists on (ADR 0011)."""

    def make(matter, when, *, precision=SentAtPrecision.TIMESTAMP, **kwargs):
        version = capture_evidence(
            matter,
            b"%PDF-1.4 synthetic",
            "arvamus.pdf",
            "application/pdf",
            visibility_override=kwargs.get("visibility_override", ""),
        )
        return factories.SubmissionFactory(
            matter=matter,
            title=kwargs.pop("title", "Sünteetiline arvamus"),
            status=SubmissionStatus.SENT,
            sent_at=when,
            sent_at_precision=precision,
            final_version=version,
            **kwargs,
        )

    return make


@pytest.fixture
def filed(specialist):
    return create_matter(title="Sünteetiline eelnõu", actor=specialist, owner=specialist)


def week_count(user, today: dt.date) -> int:
    return dd.sent_this_week(user, today).count()


# ---------------------------------------------------------------------------
# «arvamust välja sel nädalal» — the current ISO week, Monday to today
# ---------------------------------------------------------------------------


def test_the_week_runs_from_monday_to_today(department_head, filed, send):
    """Friday 9 October 2026: the week is 5–11 October, counted up to the 9th."""
    send(filed, local(2026, 10, 4, 23, 30))  # Sunday before: out
    send(filed, local(2026, 10, 5, 0, 30))  # Monday just after midnight: in
    send(filed, local(2026, 10, 9, 23, 59))  # today, late: in
    send(filed, local(2026, 10, 11, 10))  # Sunday of this week, not yet come: out
    send(filed, local(2026, 10, 12, 10))  # next Monday: out
    assert week_count(department_head, dt.date(2026, 10, 9)) == 2


def test_a_date_precision_send_counts_on_its_own_day(department_head, filed, send):
    """A DATE send is stored at local midnight — Sunday evening in UTC."""
    send(filed, local(2026, 10, 5, 0, 0), precision=SentAtPrecision.DATE)
    assert week_count(department_head, dt.date(2026, 10, 5)) == 1
    assert week_count(department_head, dt.date(2026, 10, 4)) == 0


def test_sunday_is_the_last_day_of_the_week(department_head, filed, send):
    for day in (5, 9, 11):
        send(filed, local(2026, 10, day))
    assert week_count(department_head, dt.date(2026, 10, 11)) == 3


def test_monday_starts_from_the_new_week(department_head, filed, send):
    send(filed, local(2026, 10, 5))
    send(filed, local(2026, 10, 11, 23, 30))  # Sunday night, 20:30 UTC
    assert week_count(department_head, dt.date(2026, 10, 12)) == 0
    send(filed, local(2026, 10, 12, 0, 10))  # Monday just after midnight, Sunday in UTC
    assert week_count(department_head, dt.date(2026, 10, 12)) == 1


def test_a_week_that_crosses_a_month(department_head, filed, send):
    """1 October 2026 is a Thursday: its week began on Monday 28 September."""
    send(filed, local(2026, 9, 27))
    send(filed, local(2026, 9, 28))
    send(filed, local(2026, 9, 30))
    send(filed, local(2026, 10, 1))
    assert wi.start_of_iso_week(dt.date(2026, 10, 1)) == dt.date(2026, 9, 28)
    assert week_count(department_head, dt.date(2026, 10, 1)) == 3


def test_a_week_that_crosses_a_year(department_head, filed, send):
    """1 January 2027 is a Friday in the ISO week that began on 28 December 2026."""
    send(filed, local(2026, 12, 27))
    send(filed, local(2026, 12, 28))
    send(filed, local(2026, 12, 31, 23, 50))
    send(filed, local(2027, 1, 1))
    assert week_count(department_head, dt.date(2027, 1, 1)) == 3


def test_the_week_across_the_autumn_clock_change(department_head, filed, send):
    """Summer time ends on Sunday 25 October 2026; the local date still decides."""
    send(filed, local(2026, 10, 19, 0, 5))  # Monday, UTC+3
    send(filed, local(2026, 10, 25, 23, 30))  # Sunday after the change, UTC+2
    send(filed, local(2026, 10, 26, 0, 30))  # next Monday — still the 25th in UTC
    assert week_count(department_head, dt.date(2026, 10, 25)) == 2
    assert week_count(department_head, dt.date(2026, 10, 26)) == 1


def test_only_sent_opinions_the_reader_may_see_are_counted(department_head, reader, filed, send):
    send(filed, local(2026, 10, 6))
    send(filed, local(2026, 10, 7), visibility_override=Visibility.RESTRICTED)
    create_submission(matter=filed, title="Koostamisel arvamus")  # a DRAFT is not a send
    assert week_count(reader, dt.date(2026, 10, 9)) == 1
    assert week_count(department_head, dt.date(2026, 10, 9)) == 2


# ---------------------------------------------------------------------------
# «koostamisel» — one definition, three sources
# ---------------------------------------------------------------------------


def drafting(user) -> set:
    return set(drafting_matters(user).values_list("pk", flat=True))


def prepare(matter, actor, deadline: dt.date):
    """The native start of an opinion: a request with a deadline, and the step."""
    request_response_deadline(matter=matter, deadline=deadline, actor=actor)
    matter.refresh_from_db()
    return establish_opinion_preparation_action(matter=matter, prepare_by=deadline, actor=actor)


@pytest.fixture
def addressee(db):
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


def test_the_open_opinion_step_with_a_deadline_owed_is_drafting(department_head, filed, specialist):
    prepare(filed, specialist, dt.date(2026, 11, 2))
    assert drafting(department_head) == {filed.pk}


def test_the_step_alone_is_not_drafting(department_head, filed, specialist):
    """A plan with no deadline owed is not an opinion anybody is waiting for."""
    set_next_action(matter=filed, text=OPINION_PREPARATION_TEXT, actor=specialist)
    assert drafting(department_head) == set()


def test_an_unanswered_deadline_alone_is_not_drafting(department_head, filed, specialist):
    """A file being waited on or watched has a deadline and is not being written."""
    request_response_deadline(matter=filed, deadline=dt.date(2026, 11, 2), actor=specialist)
    filed.refresh_from_db()
    set_next_action(
        matter=filed,
        text="Jälgin eelnõu menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=dt.date(2026, 11, 20),
        actor=specialist,
    )
    assert drafting(department_head) == set()


def test_the_work_plans_opinion_step_counts_by_its_operation(department_head, filed, specialist):
    request_response_deadline(matter=filed, deadline=dt.date(2026, 11, 2), actor=specialist)
    filed.refresh_from_db()
    seed_standard_plan(matter=filed, actor=specialist)
    opinion_step = next(
        step for step in plan_steps_of(filed) if step.operation == PlanStepOperation.SUBMISSION
    )
    activate_plan_step(matter=filed, step=opinion_step, actor=specialist, text="Kirjutan arvamuse")
    assert drafting(department_head) == {filed.pk}


def test_sending_the_opinion_ends_drafting_even_with_the_step_left_open(
    department_head, filed, specialist, addressee
):
    """`Lisa teemale → Koja arvamus`, answering the deadline: the count drops at once."""
    step = prepare(filed, specialist, dt.date(2026, 11, 2))
    filed.refresh_from_db()
    before = dd.seis_figures(department_head, dt.date(2026, 10, 9))
    assert {f.key: f.value for f in before}["drafting"] == 1

    send_koja_arvamus(
        filed,
        specialist,
        [addressee],
        dt.date(2026, 10, 9),
        answers_deadline=deadline_revision(filed),
    )
    assert NextAction.objects.get(pk=step.pk).status == ActionStatus.OPEN, "nobody closed it"
    assert drafting(department_head) == set()
    after = dd.seis_figures(department_head, dt.date(2026, 10, 9))
    assert {f.key: f.value for f in after}["drafting"] == 0


def test_a_send_that_does_not_answer_the_current_request_leaves_the_work_standing(
    department_head, filed, specialist, addressee
):
    prepare(filed, specialist, dt.date(2026, 11, 2))
    send_koja_arvamus(filed, specialist, [addressee], dt.date(2026, 10, 9))
    assert drafting(department_head) == {filed.pk}


def test_a_deadline_from_before_requests_were_tracked_is_ended_by_any_send(
    department_head, specialist, addressee
):
    """The importers' deadlines (no request time) keep ADR 0059's reading."""
    matter = create_matter(
        title="Imporditud eelnõu",
        actor=specialist,
        owner=specialist,
        response_deadline=dt.date(2026, 10, 20),
    )
    assert matter.response_requested_at is None
    set_next_action(
        matter=matter,
        text=OPINION_PREPARATION_TEXT,
        target_date=dt.date(2026, 10, 20),
        actor=None,
    )
    assert drafting(department_head) == {matter.pk}
    send_koja_arvamus(matter, specialist, [addressee], dt.date(2026, 10, 9))
    assert drafting(department_head) == set()


def test_a_later_request_after_an_earlier_opinion_is_drafting_again(
    department_head, filed, specialist, addressee
):
    """Several opinions on one Matter: the first send never answers the next request."""
    prepare(filed, specialist, dt.date(2026, 10, 15))
    filed.refresh_from_db()
    send_koja_arvamus(
        filed,
        specialist,
        [addressee],
        dt.date(2026, 10, 9),
        answers_deadline=deadline_revision(filed),
    )
    assert drafting(department_head) == set()

    filed.refresh_from_db()
    prepare(filed, specialist, dt.date(2026, 12, 1))
    assert drafting(department_head) == {filed.pk}
    assert Submission.objects.filter(matter=filed, status=SubmissionStatus.SENT).count() == 1


def test_a_decision_not_to_answer_ends_drafting(department_head, filed, specialist):
    """«ei saatnud» as a native fact: the deadline ends as not answering."""
    prepare(filed, specialist, dt.date(2026, 11, 2))
    filed.refresh_from_db()
    resolve_response_deadline(
        matter=filed, outcome=ResponseDeadlineOutcome.NOT_ANSWERING, actor=specialist
    )
    assert drafting(department_head) == set()


def test_a_concluded_matter_is_not_drafting(department_head, filed, specialist):
    prepare(filed, specialist, dt.date(2026, 11, 2))
    filed.refresh_from_db()
    close_matter(matter=filed, disposition=Disposition.MONITORING_STOPPED, actor=specialist)
    assert drafting(department_head) == set()


def test_a_restricted_step_does_not_reveal_the_work(reader, filed, specialist):
    step = prepare(filed, specialist, dt.date(2026, 11, 2))
    NextAction.objects.filter(pk=step.pk).update(visibility_override=Visibility.RESTRICTED)
    assert drafting(reader) == set()
    assert drafting(specialist) == {filed.pk}


def test_historical_drafts_still_count_and_one_matter_is_one_row(department_head, reader, filed):
    create_submission(matter=filed, title="Esimene kavand")
    create_submission(matter=filed, title="Teine kavand")
    assert list(drafting_matters(department_head).values_list("pk", flat=True)) == [filed.pk]

    hidden = create_matter(title="Piiratud kavandiga eelnõu")
    create_submission(
        matter=hidden, title="Piiratud kavand", visibility_override=Visibility.RESTRICTED
    )
    assert hidden.pk not in drafting(reader)


def test_a_sent_opinion_with_no_draft_and_no_open_opinion_step_is_not_drafting(
    department_head, filed, specialist, send
):
    send(filed, local(2026, 10, 6))
    set_next_action(
        matter=filed,
        text="Jälgin, kas eelnõu liigub",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=dt.date(2026, 11, 20),
        actor=specialist,
    )
    assert drafting(department_head) == set()


def test_a_new_teema_gets_no_drafting_from_its_deadline_alone(department_head, specialist):
    """docs/adr/0133 §8 is unchanged: no step is made from the deadline."""
    native = create_matter(
        title="Uus konsultatsioon",
        actor=specialist,
        owner=specialist,
        response_deadline=dt.date(2026, 11, 30),
    )
    assert not NextAction.objects.filter(matter=native).exists()
    assert drafting(department_head) == set()


def test_the_outstanding_obligation_reads_the_response_obligation_rule(
    department_head, filed, specialist, send
):
    """The condition is `response_obligations`' own population, not a copy beside it."""
    prepare(filed, specialist, dt.date(2026, 11, 2))
    other = create_matter(title="Vastatud eelnõu", response_deadline=dt.date(2026, 10, 1))
    send(other, local(2026, 10, 2))
    rule = set(
        Matter.objects.visible_to(department_head)
        .filter(is_open=True)
        .filter(wi.response_obligation_outstanding_q(department_head))
        .values_list("pk", flat=True)
    )
    listed = set(wi.response_obligations(department_head).values_list("pk", flat=True))
    assert rule == listed == {filed.pk}


# ---------------------------------------------------------------------------
# The strip: seven figures, the drafting one equal to its column and its list
# ---------------------------------------------------------------------------


def test_the_strip_ends_with_the_week_and_the_drafting_figures(department_head, filed, specialist):
    prepare(filed, specialist, dt.date(2026, 11, 2))
    figures = dd.seis_figures(department_head, dt.date(2026, 10, 9))
    assert [f.key for f in figures][-2:] == ["sent", "drafting"]
    sent, draft = figures[-2], figures[-1]
    assert (sent.caption, sent.url) == ("arvamust välja sel nädalal", "")
    assert draft.caption == "arvamust koostamisel"
    assert draft.tone == "", "information, not a warning"
    assert draft.value == 1


def test_the_drafting_figure_equals_its_column_and_opens_exactly_its_matters(
    client, department_head, specialist, other_specialist, send
):
    today = dt.date(2026, 10, 9)
    written = []
    for owner, deadline in ((specialist, 2), (specialist, 30), (other_specialist, 90)):
        matter = create_matter(title="Kirjutatav eelnõu", actor=owner, owner=owner)
        prepare(matter, owner, today + dt.timedelta(days=deadline))
        written.append(matter.pk)
    overdue = create_matter(title="Hilinenud eelnõu", actor=specialist, owner=specialist)
    prepare(overdue, specialist, today + dt.timedelta(days=1))
    Matter.objects.filter(pk=overdue.pk).update(response_deadline=today - dt.timedelta(days=3))
    written.append(overdue.pk)
    watched = create_matter(title="Jälgitav eelnõu", actor=specialist, owner=specialist)
    send(watched, local(2026, 10, 6))

    figure = next(f for f in dd.seis_figures(department_head, today) if f.key == "drafting")
    assert figure.value == 4

    total = next(row for row in dd.team_rows(department_head, today) if row.is_total)
    column = [key for key, *_ in dd.TEAM_COLUMNS].index("drafting")
    assert total.cells[column].value == figure.value

    params = {"olek": "avatud", "liik": "FULL", "arvamus": OPINION_DRAFTING}
    assert set(
        register_population(department_head, params, today=today).values_list("pk", flat=True)
    ) == set(written)

    client.force_login(department_head)
    response = client.get(figure.url)
    assert response.status_code == 200
    shown = {matter.pk for matter in response.context["page"].object_list}
    assert shown == set(written)
    assert figure.url.startswith(reverse("matters:matter_list"))
