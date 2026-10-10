"""`Jõustumine`: several per Matter, and never a fabricated date.

The two properties this model exists for are that one law can commence in
stages, and that "we do not know when" is recordable as itself. Both are easy to
lose to a well-meaning default, so both are held here by a database constraint
and a test.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.enums import EffectiveDateKind
from app.intelligence.models import MatterEffectiveDate
from app.intelligence.services import (
    add_effective_date,
    update_effective_date,
)
from app.workflow.dates import quarter_bounds
from app.workflow.enums import DatePrecision
from tests import factories
from tests.refusals import refused

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
    with refused("Teadaoleva jõustumise puhul on kuupäev kohustuslik."):
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
    with refused("Sellel jõustumise liigil ei saa kuupäeva olla."):
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

    def in_year(year: int) -> int:
        return (
            MatterEffectiveDate.objects.visible_to(specialist).filter(date_value__year=year).count()
        )

    assert in_year(2030) == 1

    update_effective_date(
        record=record, description="põhiosa", actor=specialist, **_on(date(2031, 1, 1))
    )

    assert in_year(2030) == 0
    assert in_year(2031) == 1
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
