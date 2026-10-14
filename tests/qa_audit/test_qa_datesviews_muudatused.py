"""QA audit 2026-10-14 (area `datesviews`) — *Muudatused* (docs/adr/0159).

Independent probes of the department-wide change view at source 81a7d6e4:

* window edges on the Europe/Tallinn calendar at the two 2026 clock changes
  (29 March, 25 October) and across the year boundary, for every named window;
* one act is one row: changes generated through the real services and counted
  per category, including the opinion follow-up check (docs/adr/0146) whose
  audit rows `NEXT_ACTION_SET` shares with a person setting a step;
* the head-only gate for every other persona, by URL;
* TEST Matters are counted, per ADR 0159 «populations are not narrowed to
  real_data()».

Probes that pass pin behaviour the suite did not cover; the ``xfail(strict)``
ones are confirmed defects (see FINDINGS.md, QA-DATESVIEWS-NN).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters import department_changes as dc
from app.matters import department_dashboard as dd
from app.matters.enums import MatterDataClass
from app.matters.services import change_stage, close_matter, create_matter, reopen_matter
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import (
    add_planned_action,
    complete_next_action,
    set_next_action,
    set_next_action_for_new_work,
)
from tests import factories
from tests.follow_ups import active_check, mark_sent

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")
UTC = ZoneInfo("UTC")
PAGE = "/osakond/muudatused/"


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=TALLINN)


def _stage_event(matter, actor, moment: datetime, label: str) -> None:
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_STAGE_CHANGED,
        matter=matter,
        actor=actor,
        occurred_at=moment,
        summary=label,
        payload={"from_label": "Idee", "to_label": label},
    )


def _labels(changes: dc.DepartmentChanges) -> set[str]:
    result = next(r for r in changes.categories if r.key == "hetkeseis")
    return {row.detail.split(" → ", 1)[1] for row in result.rows}


def _category(changes: dc.DepartmentChanges, key: str) -> dc.CategoryResult:
    return next(result for result in changes.categories if result.key == key)


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


# ---------------------------------------------------------------------------
# Window edges at the clock changes and the year boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transition",
    [date(2026, 3, 29), date(2026, 10, 25)],
    ids=["spring-forward", "fall-back"],
)
def test_a_one_day_window_on_a_clock_change_day_holds_exactly_that_local_day(
    department_head, specialist, transition
):
    matter = factories.MatterFactory(owner=specialist)
    moments = {
        "eve 23:59": _at(transition - timedelta(days=1), time(23, 59)),
        "00:00": _at(transition, time(0, 0)),
        "repeated hour": _at(transition, time(3, 30)),
        "23:59": _at(transition, time(23, 59)),
        "next 00:00": _at(transition + timedelta(days=1), time(0, 0)),
    }
    for label, moment in moments.items():
        _stage_event(matter, specialist, moment, label)
    stamp = f"{transition.day}.{transition.month}.{transition.year}"

    changes = dc.build_changes(
        department_head,
        {"periood": "vahemik", "alates": stamp, "kuni": stamp},
        today=transition + timedelta(days=3),
    )

    assert _labels(changes) == {"00:00", "repeated hour", "23:59"}
    # Every displayed row is dated, in Tallinn, on the day the window names.
    assert {row.when for row in _category(changes, "hetkeseis").rows} == {transition}


@pytest.mark.parametrize("key,days", [("7", 7), ("14", 14), ("30", 30), ("kvartal", 91)])
def test_named_windows_ending_the_day_after_fall_back_hold_n_dates(
    department_head, specialist, key, days
):
    """«N päeva» across the 25-hour day is still N calendar dates."""
    today = date(2026, 10, 26)
    first = today - timedelta(days=days - 1)
    matter = factories.MatterFactory(owner=specialist)
    _stage_event(matter, specialist, _at(first - timedelta(days=1), time(23, 59, 59)), "before")
    _stage_event(matter, specialist, _at(first, time(0, 0)), "first")
    _stage_event(matter, specialist, _at(today, time(23, 59, 59)), "last")

    changes = dc.build_changes(department_head, {"periood": key}, today=today)

    assert (changes.period.start, changes.period.end) == (first, today)
    assert (changes.period.end - changes.period.start).days + 1 == days
    assert _labels(changes) == {"first", "last"}


def test_year_to_date_starts_on_tallinn_new_year_not_utc(department_head, specialist):
    """«Aasta» on 1 January is that one date; 31 December 23:30 Tallinn is
    already 1 January in no zone the window reads, and 00:30 Tallinn on
    1 January is still 31 December in UTC."""
    new_year = date(2027, 1, 1)
    matter = factories.MatterFactory(owner=specialist)
    _stage_event(matter, specialist, _at(date(2026, 12, 31), time(23, 30)), "old year")
    _stage_event(matter, specialist, _at(new_year, time(0, 30)), "new year")
    assert _at(new_year, time(0, 30)).astimezone(UTC).date() == date(2026, 12, 31)

    changes = dc.build_changes(department_head, {"periood": "aasta"}, today=new_year)

    assert (changes.period.start, changes.period.end) == (new_year, new_year)
    assert _labels(changes) == {"new year"}
    # The year now differs from the previous one: row dates carry no year only
    # when the window is this year's alone.
    assert changes.period.bounds == "1.1.2027–1.1.2027"


def test_a_custom_range_across_the_year_boundary_keeps_both_ends(department_head, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _stage_event(matter, specialist, _at(date(2026, 12, 31), time(23, 59)), "31.12")
    _stage_event(matter, specialist, _at(date(2027, 1, 1), time(0, 0)), "1.1")
    _stage_event(matter, specialist, _at(date(2027, 1, 2), time(0, 0)), "2.1")

    changes = dc.build_changes(
        department_head,
        {"periood": "vahemik", "alates": "31.12.2026", "kuni": "1.1.2027"},
        today=date(2027, 1, 5),
    )

    assert changes.period_error == ""
    assert _labels(changes) == {"31.12", "1.1"}
    assert changes.row_date_format == "d.m.Y"


def test_a_leap_day_range_is_one_date():
    period, error = dc.changes_period(
        {"periood": "vahemik", "alates": "29.02.2028", "kuni": "29.2.2028"}, date(2028, 3, 1)
    )
    assert error == ""
    assert (period.start, period.end) == (date(2028, 2, 29), date(2028, 2, 29))

    period, error = dc.changes_period(
        {"periood": "vahemik", "alates": "29.02.2027", "kuni": "1.3.2027"}, date(2027, 3, 5)
    )
    assert error == dc.CUSTOM_RANGE_UNREADABLE


def test_the_page_prints_the_bounds_of_the_window_it_counted(client, department_head):
    client.force_login(department_head)
    response = client.get(PAGE, {"periood": "kvartal"})
    page = response.context["page"]
    assert response.status_code == 200
    assert (page.period.end - page.period.start).days + 1 == 91
    assert page.period.bounds in response.content.decode()
    assert "Viimased 13 nädalat" in response.content.decode()


# ---------------------------------------------------------------------------
# One act, one row — through the real services
# ---------------------------------------------------------------------------


def test_distinct_changes_in_one_minute_are_not_merged(department_head, specialist):
    matter = create_matter(title="Kiire päev", owner=specialist, actor=specialist)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    reopen_matter(matter=matter, actor=specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    reopen_matter(matter=matter, actor=specialist)

    changes = dc.build_changes(department_head)

    assert _category(changes, "hetkeseis").count == 2
    assert _category(changes, "taasavatud").count == 2
    # Every displayed row is one canonical event: the rows equal the events.
    events = ChangeEvent.objects.filter(
        matter=matter, event_type__in=dc.CURATED_EVENT_TYPES, actor__isnull=False
    ).count()
    assert sum(r.count for r in changes.categories) == events


def test_a_test_matter_is_counted_as_the_adr_says(department_head, specialist):
    """ADR 0159 consequences: «Like Tehtud, populations are not narrowed to
    real_data()». A TEST Matter's changes therefore count — pinned so a later
    narrowing is a decision, not an accident."""
    matter = create_matter(title="TEST-teema", owner=specialist, actor=specialist)
    matter.data_class = MatterDataClass.TEST
    matter.save(update_fields=["data_class"])
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)

    changes = dc.build_changes(department_head)

    assert _category(changes, "uued").count == 1
    assert _category(changes, "hetkeseis").count == 1


def _set_rows(changes) -> list[dc.ChangeRow]:
    return [
        row
        for row in _category(changes, "tegevused").rows
        if row.event_type == ChangeEventType.NEXT_ACTION_SET
    ]


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium QA")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-DATESVIEWS-01: recording an opinion as sent schedules the ADR 0146 "
        "follow-up check automatically; Muudatused counts that system-scheduled "
        "NEXT_ACTION_SET (payload follow_up.automatic=true) as the sender's own "
        "«määras järgmise tegevuse»"
    ),
)
def test_sending_an_opinion_is_not_a_person_setting_a_next_step(
    department_head, specialist, ministry
):
    matter = create_matter(title="Saadetud arvamus", owner=specialist, actor=specialist)
    mark_sent(matter, specialist, [ministry])
    # Nobody chose a step: the check is the system's (ADR 0146 §10 «scheduled
    # by the system»), and the send itself is Tehtud's (ADR 0159 §2).
    assert _set_rows(dc.build_changes(department_head)) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-DATESVIEWS-01: new work written over a current follow-up check writes "
        "two NEXT_ACTION_SET events (the new step, and the check 'returned_to_plan'); "
        "Muudatused shows one act as two «määras järgmise tegevuse» rows"
    ),
)
def test_one_new_step_over_a_current_check_is_one_row(department_head, specialist, ministry):
    matter = create_matter(title="Kontroll ees", owner=specialist, actor=specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    check = active_check(opinion)
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)
    before = {row.at for row in _set_rows(dc.build_changes(department_head))}

    set_next_action_for_new_work(
        matter=matter,
        text="Uus kiireloomuline töö",
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )

    after = [row for row in _set_rows(dc.build_changes(department_head)) if row.at not in before]
    assert len(after) == 1


def test_finishing_a_step_that_promotes_a_planned_one_counts_the_planned_step_twice(
    department_head, specialist
):
    """Observation probe (passes on 81a7d6e4): a planned step is «määras» when
    it is planned and again when it is promoted. Pinned as current behaviour;
    see FINDINGS.md DESIGN CONCERNS."""
    matter = create_matter(title="Plaaniga teema", owner=specialist, actor=specialist)
    current = set_next_action(matter=matter, text="Praegune", actor=specialist)
    add_planned_action(
        matter=matter,
        text="Plaanitud",
        target_date=timezone.localdate() + timedelta(days=20),
        actor=specialist,
    )
    complete_next_action(action=current, actor=specialist)

    breakdown = dict(_category(dc.build_changes(department_head), "tegevused").breakdown)
    # set(current) + set(planned) + set(promoted) = 3 for two steps.
    assert breakdown["määras järgmise tegevuse"] == 3
    assert breakdown["lõpetas järgmise tegevuse"] == 1


# ---------------------------------------------------------------------------
# Head-only, by URL guessing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["specialist", "other_specialist", "reader", "administrator"])
@pytest.mark.parametrize(
    "query",
    [
        {},
        {"periood": "kvartal"},
        {"periood": "vahemik", "alates": "1.1.2026", "kuni": "31.12.2026"},
    ],
)
def test_every_other_persona_gets_a_404_on_every_window(client, request, who, query):
    client.force_login(request.getfixturevalue(who))
    assert client.get(PAGE, query).status_code == 404


def test_anonymous_is_sent_to_sign_in_not_shown_the_page(client):
    response = client.get(PAGE)
    assert response.status_code in (302, 404)
    assert b"Muudatused" not in response.content


def test_the_osakond_page_does_not_link_muudatused_for_a_specialist(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:department")).content.decode()
    assert PAGE not in body


def test_tehtud_and_muudatused_agree_on_every_named_window():
    today = date(2026, 10, 25)
    for key, _label in dd.PERIOD_NAMES:
        tehtud = dd.period_from({"periood": key}, today)
        muudatused, error = dc.changes_period({"periood": key}, today)
        assert error == ""
        assert (tehtud.start, tehtud.end) == (muudatused.start, muudatused.end), key


def test_an_opinion_restricted_after_sending_hides_its_checks_events(
    department_head, reader, specialist, ministry
):
    """ADR 0146 §10: a check's events reach exactly the readers its opinion
    does, read live — here through Muudatused for a reader lent the view."""
    from app.core.enums import Visibility
    from app.submissions.models import Submission

    matter = create_matter(title="Piiratud arvamus", owner=specialist, actor=specialist)
    opinion = mark_sent(matter, specialist, [ministry])
    reader_before = _category(dc.build_changes(reader), "tegevused").count
    from app.documents.models import Document

    opinion.refresh_from_db()
    Document.objects.filter(pk=opinion.final_version.document_id).update(
        visibility_override=Visibility.RESTRICTED
    )
    Submission.objects.filter(pk=opinion.pk).update(visibility_override=Visibility.RESTRICTED)

    assert _category(dc.build_changes(reader), "tegevused").count == reader_before - 1
    assert _category(dc.build_changes(department_head), "tegevused").count >= 1


def test_one_closure_reads_as_one_cancellation_per_open_or_planned_step(
    department_head, specialist
):
    """Observation probe (passes on 81a7d6e4): «Sulge» on a Teema with a current
    step and two planned ones writes three NEXT_ACTION_CANCELLED rows with the
    closer as actor, so Muudatused prints three «tühistas järgmise tegevuse»
    for one closure — which ADR 0159 §2 says is Tehtud's. See FINDINGS.md
    DESIGN CONCERNS (DC-2)."""
    matter = create_matter(title="Suletav", owner=specialist, actor=specialist)
    set_next_action(matter=matter, text="Praegune", actor=specialist)
    for offset in (10, 20):
        add_planned_action(
            matter=matter,
            text=f"Plaan {offset}",
            target_date=timezone.localdate() + timedelta(days=offset),
            actor=specialist,
        )
    before = dict(_category(dc.build_changes(department_head), "tegevused").breakdown)

    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )

    after = dict(_category(dc.build_changes(department_head), "tegevused").breakdown)
    assert after["tühistas järgmise tegevuse"] - before["tühistas järgmise tegevuse"] == 3
