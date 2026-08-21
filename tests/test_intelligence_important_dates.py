"""`Oluline tähtaeg`: capture, correction, cancellation and the generated view.

The rules under test are the ones that make the department-wide page trustworthy:
a Matter may carry several milestones, a change leaves a trace, a cancelled
expectation stays visible, and nothing is ever invented from an approximate
period.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.intelligence import selectors
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.intelligence.services import (
    add_important_date,
    cancel_important_date,
    supersede_important_date,
    update_important_date,
)
from app.workflow.dates import bounds_for, quarter_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _exact(value: date) -> dict:
    start, end = bounds_for(DatePrecision.EXACT, exact_date=value)
    return {"date_value": start, "period_end": end, "date_precision": DatePrecision.EXACT}


def _quarter(year: int, quarter: int) -> dict:
    start, end = quarter_bounds(year, quarter)
    return {"date_value": start, "period_end": end, "date_precision": DatePrecision.QUARTER}


# -- capture ----------------------------------------------------------------


def test_a_milestone_is_recorded_with_its_precision(normal_matter, specialist):
    record = add_important_date(
        matter=normal_matter,
        title="Eelnõu kooskõlastusring",
        actor=specialist,
        **_quarter(2027, 2),
    )

    assert record.date_precision == DatePrecision.QUARTER
    assert record.display_date == "II kvartal 2027"
    assert record.date_value == date(2027, 4, 1)
    assert record.period_end == date(2027, 6, 30)
    assert record.created_by == specialist
    assert record.status == FactStatus.ACTIVE


def test_one_matter_may_carry_several_milestones(normal_matter, specialist):
    add_important_date(
        matter=normal_matter, title="Esimene", actor=specialist, **_exact(date(2027, 3, 1))
    )
    add_important_date(matter=normal_matter, title="Teine", actor=specialist, **_quarter(2027, 3))

    assert MatterImportantDate.objects.filter(matter=normal_matter).count() == 2


def test_a_milestone_needs_a_description(normal_matter, specialist):
    with pytest.raises(DomainError):
        add_important_date(
            matter=normal_matter, title="   ", actor=specialist, **_exact(date(2027, 3, 1))
        )


def test_the_service_refuses_bounds_that_contradict_the_precision(normal_matter, specialist):
    """A QUARTER row spanning one day would read as an exact date downstream."""
    with pytest.raises(DomainError):
        add_important_date(
            matter=normal_matter,
            title="Vale periood",
            actor=specialist,
            date_value=date(2027, 4, 1),
            period_end=date(2027, 4, 1),
            date_precision=DatePrecision.QUARTER,
        )


def test_the_database_refuses_a_period_that_ends_before_it_starts(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterImportantDate.objects.create(
            matter=normal_matter,
            title="Tagurpidi",
            date_value=date(2027, 6, 30),
            period_end=date(2027, 4, 1),
        )


def test_adding_a_milestone_is_audited(normal_matter, specialist):
    record = add_important_date(
        matter=normal_matter, title="Kooskõlastusring", actor=specialist, **_exact(date(2027, 3, 1))
    )
    event = ChangeEvent.objects.get(event_type=ChangeEventType.IMPORTANT_DATE_ADDED)

    assert event.matter == normal_matter
    assert event.actor == specialist
    assert event.object_id == record.pk
    assert event.payload["precision"] == DatePrecision.EXACT


def test_no_entry_is_created_for_a_structured_fact(normal_matter, specialist):
    """These are facts, not authored chronology (Stage-2G brief 37)."""
    add_important_date(
        matter=normal_matter, title="Kooskõlastusring", actor=specialist, **_exact(date(2027, 3, 1))
    )
    assert normal_matter.entries.count() == 0


def test_the_structured_fact_events_stay_out_of_the_professional_timeline():
    from app.matters.timeline import TIMELINE_EVENT_TYPES

    for event_type in (
        ChangeEventType.IMPORTANT_DATE_ADDED,
        ChangeEventType.EFFECTIVE_DATE_ADDED,
        ChangeEventType.WORK_VICTORY_PROPOSED,
    ):
        assert event_type not in TIMELINE_EVENT_TYPES


# -- correction and cancellation --------------------------------------------


def test_moving_a_date_keeps_the_old_one_in_the_audit_trail(normal_matter, specialist):
    record = add_important_date(
        matter=normal_matter,
        title="Kooskõlastusring",
        actor=specialist,
        **_exact(date(2026, 9, 27)),
    )
    update_important_date(
        record=record, title="Kooskõlastusring", actor=specialist, **_exact(date(2026, 11, 1))
    )

    event = ChangeEvent.objects.get(event_type=ChangeEventType.IMPORTANT_DATE_CHANGED)
    assert event.payload["from"]["date"] == "2026-09-27"
    assert event.payload["to"]["date"] == "2026-11-01"


def test_a_cancelled_milestone_is_kept_and_marked(normal_matter, specialist):
    record = add_important_date(
        matter=normal_matter, title="Ärajäänud ring", actor=specialist, **_exact(date(2027, 3, 1))
    )
    cancel_important_date(record=record, actor=specialist, reason="Ministeerium loobus")

    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED
    assert MatterImportantDate.objects.filter(pk=record.pk).exists()
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.IMPORTANT_DATE_CANCELLED).exists()


def test_a_cancelled_milestone_cannot_be_edited_or_cancelled_twice(normal_matter, specialist):
    record = add_important_date(
        matter=normal_matter, title="Ärajäänud", actor=specialist, **_exact(date(2027, 3, 1))
    )
    cancel_important_date(record=record, actor=specialist)

    with pytest.raises(DomainError):
        cancel_important_date(record=record, actor=specialist)
    with pytest.raises(DomainError):
        update_important_date(
            record=record, title="Uus", actor=specialist, **_exact(date(2027, 4, 1))
        )


def test_superseding_links_the_old_record_to_its_replacement(normal_matter, specialist):
    original = add_important_date(
        matter=normal_matter, title="Esialgne plaan", actor=specialist, **_quarter(2027, 1)
    )
    replacement = supersede_important_date(
        record=original, title="Uus plaan", actor=specialist, **_quarter(2027, 3)
    )

    original.refresh_from_db()
    assert original.status == FactStatus.SUPERSEDED
    assert original.replaced_by == replacement
    assert replacement.status == FactStatus.ACTIVE
    assert replacement.display_date == "III kvartal 2027"


# -- past and future --------------------------------------------------------


def test_a_period_is_past_only_once_its_last_day_has_gone(normal_matter, specialist):
    """II poolaasta 2027 has not passed on 2 July 2027."""
    start, end = bounds_for(DatePrecision.HALF_YEAR, year=2027, half=2)
    record = add_important_date(
        matter=normal_matter,
        title="Ülevõtmise tähtaeg",
        actor=specialist,
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.HALF_YEAR,
    )

    assert record.has_passed(date(2027, 7, 2)) is False
    assert record.has_passed(date(2027, 12, 31)) is False
    assert record.has_passed(date(2028, 1, 1)) is True


def test_the_matter_page_splits_upcoming_from_past(normal_matter, specialist):
    today = timezone.localdate()
    add_important_date(
        matter=normal_matter,
        title="Tulevik",
        actor=specialist,
        **_exact(today + timedelta(days=10)),
    )
    add_important_date(
        matter=normal_matter,
        title="Minevik",
        actor=specialist,
        **_exact(today - timedelta(days=10)),
    )

    view = selectors.matter_intelligence(normal_matter, specialist, today)
    assert [record.title for record in view.upcoming_dates] == ["Tulevik"]
    assert [record.title for record in view.past_dates] == ["Minevik"]


def test_the_matter_page_keeps_showing_a_cancelled_milestone(normal_matter, specialist):
    record = add_important_date(
        matter=normal_matter,
        title="Ärajäänud",
        actor=specialist,
        **_exact(timezone.localdate() + timedelta(days=5)),
    )
    cancel_important_date(record=record, actor=specialist)

    view = selectors.matter_intelligence(normal_matter, specialist)
    assert [item.title for item in view.upcoming_dates] == ["Ärajäänud"]
    assert view.upcoming_dates[0].is_cancelled is True


# -- the generated calendar -------------------------------------------------


def test_the_calendar_orders_by_period_start_then_by_precision(specialist):
    matter = factories.MatterFactory(owner=specialist)
    year_start, year_end = bounds_for(DatePrecision.YEAR, year=2030)
    quarter_start, quarter_end = quarter_bounds(2030, 3)

    add_important_date(
        matter=matter,
        title="Aasta",
        actor=specialist,
        date_value=year_start,
        period_end=year_end,
        date_precision=DatePrecision.YEAR,
    )
    add_important_date(
        matter=matter,
        title="Kvartal",
        actor=specialist,
        date_value=quarter_start,
        period_end=quarter_end,
        date_precision=DatePrecision.QUARTER,
    )
    add_important_date(matter=matter, title="Täpne", actor=specialist, **_exact(date(2030, 7, 1)))

    rows = list(
        selectors.calendar_rows(user=specialist, today=date(2030, 1, 1), direction=selectors.ALL)
    )
    titles = [entry.title for entry in selectors.hydrate_calendar(rows, specialist)]
    # 2030 starts first; among the two beginning on 1 July, the quarter is
    # narrower than the year and the exact day narrower still.
    assert titles == ["Aasta", "Täpne", "Kvartal"]


def test_the_calendar_groups_approximate_periods_under_their_own_heading(specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(matter=matter, title="Kvartal", actor=specialist, **_quarter(2030, 3))

    rows = list(
        selectors.calendar_rows(user=specialist, today=date(2030, 1, 1), direction=selectors.ALL)
    )
    groups = selectors.group_by_period(selectors.hydrate_calendar(rows, specialist))
    assert [group.label for group in groups] == ["III kvartal 2030"]


def test_a_cancelled_milestone_leaves_the_department_calendar(specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_important_date(
        matter=matter, title="Ärajäänud", actor=specialist, **_quarter(2030, 3)
    )
    cancel_important_date(record=record, actor=specialist)

    rows = selectors.calendar_rows(user=specialist, today=date(2030, 1, 1), direction=selectors.ALL)
    assert rows.count() == 0


def test_the_year_filter_places_an_approximate_period_in_its_own_year(specialist):
    matter = factories.MatterFactory(owner=specialist)
    add_important_date(matter=matter, title="Poolaasta", actor=specialist, **_quarter(2031, 4))

    assert (
        selectors.calendar_rows(
            user=specialist, today=date(2030, 1, 1), direction=selectors.ALL, year=2031
        ).count()
        == 1
    )
    assert (
        selectors.calendar_rows(
            user=specialist, today=date(2030, 1, 1), direction=selectors.ALL, year=2030
        ).count()
        == 0
    )
