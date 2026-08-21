"""`Jõustumine`: several per Matter, and never a fabricated date.

The two properties this model exists for are that one law can commence in
stages, and that "we do not know when" is recordable as itself. Both are easy to
lose to a well-meaning default, so both are held here by a database constraint
and a test.
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
from app.intelligence.enums import EffectiveDateKind, FactStatus
from app.intelligence.models import MatterEffectiveDate
from app.intelligence.services import (
    add_effective_date,
    cancel_effective_date,
    update_effective_date,
)
from app.workflow.dates import quarter_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _on(value: date) -> dict:
    return {
        "kind": EffectiveDateKind.KNOWN_DATE,
        "date_value": value,
        "period_end": value,
        "date_precision": DatePrecision.EXACT,
    }


# -- several per Matter -----------------------------------------------------


def test_one_matter_can_commence_in_stages(normal_matter, specialist):
    add_effective_date(
        matter=normal_matter, description="põhiosa", actor=specialist, **_on(date(2026, 6, 30))
    )
    add_effective_date(
        matter=normal_matter, description="osad sätted", actor=specialist, **_on(date(2028, 1, 1))
    )

    records = MatterEffectiveDate.objects.filter(matter=normal_matter).order_by("date_value")
    assert [record.description for record in records] == ["põhiosa", "osad sätted"]
    assert records.count() == 2


# -- what is known, and what is not -----------------------------------------


def test_a_known_commencement_requires_a_date(normal_matter, specialist):
    with pytest.raises(DomainError):
        add_effective_date(
            matter=normal_matter, kind=EffectiveDateKind.KNOWN_DATE, actor=specialist
        )


def test_general_order_records_the_statement_and_no_date(normal_matter, specialist):
    record = add_effective_date(
        matter=normal_matter,
        kind=EffectiveDateKind.GENERAL_ORDER,
        description="rakendusmäärus",
        actor=specialist,
    )

    assert record.date_value is None
    assert record.period_end is None
    assert record.display_when == "Jõustub üldises korras"


def test_an_unknown_commencement_is_a_state_rather_than_a_gap(normal_matter, specialist):
    record = add_effective_date(
        matter=normal_matter, kind=EffectiveDateKind.UNKNOWN, actor=specialist
    )

    assert record.date_value is None
    assert record.display_when == "Kuupäev täpsustamisel"


def test_a_dateless_kind_may_not_carry_a_date(normal_matter, specialist):
    """No placeholder day, at any layer. This is the whole point of the model."""
    with pytest.raises(DomainError):
        add_effective_date(
            matter=normal_matter,
            kind=EffectiveDateKind.GENERAL_ORDER,
            date_value=date(2026, 1, 1),
            period_end=date(2026, 1, 1),
            actor=specialist,
        )


def test_the_database_refuses_a_dateless_kind_with_a_date(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterEffectiveDate.objects.create(
            matter=normal_matter,
            kind=EffectiveDateKind.UNKNOWN,
            date_value=date(1970, 1, 1),
            period_end=date(1970, 1, 1),
        )


def test_the_database_refuses_a_known_date_without_one(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterEffectiveDate.objects.create(matter=normal_matter, kind=EffectiveDateKind.KNOWN_DATE)


def test_an_approximate_commencement_keeps_its_precision(normal_matter, specialist):
    start, end = quarter_bounds(2027, 1)
    record = add_effective_date(
        matter=normal_matter,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )
    assert record.display_when == "I kvartal 2027"


def test_a_source_url_is_optional_and_stored_verbatim(normal_matter, specialist):
    record = add_effective_date(
        matter=normal_matter,
        description="põhiosa",
        source_url="https://www.riigiteataja.ee/akt/000000000",
        actor=specialist,
        **_on(date(2026, 6, 30)),
    )
    assert record.source_url.startswith("https://")

    without = add_effective_date(
        matter=normal_matter, description="osad sätted", actor=specialist, **_on(date(2027, 1, 1))
    )
    assert without.source_url == ""


# -- the generated page moves with the record -------------------------------


def test_editing_the_date_moves_the_row_and_creates_no_copy(specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = add_effective_date(
        matter=matter, description="põhiosa", actor=specialist, **_on(date(2030, 11, 1))
    )

    november = selectors.effective_dates(
        user=specialist, today=date(2030, 1, 1), direction=selectors.ALL, year=2030
    )
    assert november.count() == 1

    update_effective_date(
        record=record, description="põhiosa", actor=specialist, **_on(date(2031, 1, 1))
    )

    assert (
        selectors.effective_dates(
            user=specialist, today=date(2030, 1, 1), direction=selectors.ALL, year=2030
        ).count()
        == 0
    )
    assert (
        selectors.effective_dates(
            user=specialist, today=date(2030, 1, 1), direction=selectors.ALL, year=2031
        ).count()
        == 1
    )
    assert MatterEffectiveDate.objects.filter(matter=matter).count() == 1


def test_the_change_records_where_the_date_moved_from(normal_matter, specialist):
    record = add_effective_date(
        matter=normal_matter, description="põhiosa", actor=specialist, **_on(date(2026, 9, 27))
    )
    update_effective_date(
        record=record, description="põhiosa", actor=specialist, **_on(date(2026, 11, 1))
    )

    event = ChangeEvent.objects.get(event_type=ChangeEventType.EFFECTIVE_DATE_CHANGED)
    assert event.payload["from"]["date"] == "2026-09-27"
    assert event.payload["to"]["date"] == "2026-11-01"


def test_the_default_window_looks_a_year_ahead_without_hiding_the_rest(specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    add_effective_date(
        matter=matter, description="peagi", actor=specialist, **_on(today + timedelta(days=30))
    )
    add_effective_date(
        matter=matter, description="kaugel", actor=specialist, **_on(today + timedelta(days=900))
    )

    horizon = selectors.effective_dates(user=specialist, today=today, direction=selectors.HORIZON)
    assert [record.description for record in horizon] == ["peagi"]

    everything = selectors.effective_dates(user=specialist, today=today, direction=selectors.ALL)
    assert everything.count() == 2


def test_past_commencements_are_their_own_view(specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    add_effective_date(
        matter=matter, description="möödas", actor=specialist, **_on(today - timedelta(days=10))
    )

    assert (
        selectors.effective_dates(user=specialist, today=today, direction=selectors.HORIZON).count()
        == 0
    )
    assert (
        selectors.effective_dates(user=specialist, today=today, direction=selectors.PAST).count()
        == 1
    )


def test_undated_commencements_are_grouped_apart_from_the_chronology(specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    add_effective_date(matter=matter, kind=EffectiveDateKind.GENERAL_ORDER, actor=specialist)
    add_effective_date(matter=matter, kind=EffectiveDateKind.UNKNOWN, actor=specialist)
    add_effective_date(
        matter=matter, description="teada", actor=specialist, **_on(today + timedelta(days=20))
    )

    dated = selectors.effective_dates(user=specialist, today=today, direction=selectors.HORIZON)
    assert dated.count() == 1

    undated = selectors.effective_dates(user=specialist, today=today, direction=selectors.UNDATED)
    assert undated.count() == 2
    assert selectors.undated_effective_count(specialist) == 2


def test_a_cancelled_commencement_leaves_the_generated_page(specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    record = add_effective_date(
        matter=matter, description="tühistatud", actor=specialist, **_on(today + timedelta(days=20))
    )
    cancel_effective_date(record=record, actor=specialist, reason="Eelnõu võeti tagasi")

    record.refresh_from_db()
    assert record.status == FactStatus.CANCELLED
    assert (
        selectors.effective_dates(user=specialist, today=today, direction=selectors.HORIZON).count()
        == 0
    )
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.EFFECTIVE_DATE_CANCELLED).exists()


def test_a_known_commencement_also_appears_in_the_combined_calendar(specialist):
    """One source of truth, two presentations (Stage-2G brief 47)."""
    matter = factories.MatterFactory(owner=specialist)
    add_effective_date(
        matter=matter, description="põhiosa", actor=specialist, **_on(date(2030, 11, 1))
    )

    rows = list(
        selectors.calendar_rows(user=specialist, today=date(2030, 1, 1), direction=selectors.ALL)
    )
    entries = selectors.hydrate_calendar(rows, specialist)
    assert len(entries) == 1
    assert entries[0].is_effective_date is True
    assert entries[0].title == "põhiosa"

    # And it can be filtered back out, without a second table existing.
    only_dates = selectors.calendar_rows(
        user=specialist,
        today=date(2030, 1, 1),
        direction=selectors.ALL,
        sources=selectors.SOURCE_IMPORTANT,
    )
    assert only_dates.count() == 0
