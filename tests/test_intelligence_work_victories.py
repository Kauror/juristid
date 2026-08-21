"""`Töövõit`: a claim, a review, and a period nobody guessed.

Three properties matter and each has been got wrong by somebody's spreadsheet:
a claim does not become a confirmed victory by being edited, the reporting period
is a fact somebody entered rather than a timestamp, and "we do not know when"
stays unknown instead of being counted into whichever year is on screen.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.intelligence import selectors
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterWorkVictory
from app.intelligence.services import (
    add_work_victory_candidate,
    confirm_work_victory,
    reject_work_victory,
    update_work_victory,
)
from app.workflow.dates import bounds_for, year_bounds
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _year(year: int) -> dict:
    start, end = year_bounds(year)
    return {"period_date": start, "period_end": end, "date_precision": DatePrecision.YEAR}


# -- the claim --------------------------------------------------------------


def test_a_new_record_is_always_a_candidate(normal_matter, specialist):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Ettepanek võeti arvesse", actor=specialist, **_year(2026)
    )

    assert record.status == WorkVictoryStatus.CANDIDATE
    assert record.is_confirmed is False
    assert record.confirmed_at is None
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.WORK_VICTORY_PROPOSED).exists()


def test_several_victories_may_belong_to_one_matter(normal_matter, specialist):
    """Two achievements are two records, never a quantity of two.

    A count cannot be reviewed, cannot be described and cannot be linked to
    evidence later (Stage-2G brief 23).
    """
    add_work_victory_candidate(
        matter=normal_matter, title="Rakendusaeg pikenes", actor=specialist, **_year(2026)
    )
    add_work_victory_candidate(
        matter=normal_matter, title="Erisus jäi sisse", actor=specialist, **_year(2026)
    )

    assert MatterWorkVictory.objects.filter(matter=normal_matter).count() == 2
    assert not hasattr(MatterWorkVictory, "quantity")


def test_a_candidate_needs_a_description(normal_matter, specialist):
    with pytest.raises(DomainError):
        add_work_victory_candidate(matter=normal_matter, title="", actor=specialist)


# -- the period -------------------------------------------------------------


def test_the_period_is_entered_rather_than_inferred(normal_matter, specialist):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Ettepanek arvestati", actor=specialist, **_year(2026)
    )

    assert record.period_date == date(2026, 1, 1)
    assert record.display_period == "2026"
    # The Matter's own reference year is 2026 in the factory too, so the
    # assertion that matters is the negative one: nothing reads either of the
    # other two facts to fill this in.
    assert record.period_date.year == 2026
    assert normal_matter.reference_year is not None


def test_a_day_precise_period_is_kept_as_a_day(normal_matter, specialist):
    start, end = bounds_for(DatePrecision.EXACT, exact_date=date(2026, 7, 13))
    record = add_work_victory_candidate(
        matter=normal_matter,
        title="Riigikogu hääletus",
        period_date=start,
        period_end=end,
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )
    assert record.display_period == "13.07.2026"


def test_a_candidate_may_have_no_period_at_all(normal_matter, specialist):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Millalgi varem", actor=specialist
    )

    assert record.period_date is None
    assert record.period_end is None
    assert record.has_period is False
    assert record.display_period == ""


def test_the_database_refuses_half_a_period(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterWorkVictory.objects.create(
            matter=normal_matter, title="Pool perioodi", period_date=date(2026, 1, 1)
        )


# -- review -----------------------------------------------------------------


def test_confirmation_records_who_decided_and_when(normal_matter, specialist, department_head):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Ettepanek arvestati", actor=specialist, **_year(2026)
    )
    confirm_work_victory(record=record, actor=department_head)

    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CONFIRMED
    assert record.confirmed_by == department_head
    assert record.confirmed_at is not None

    event = ChangeEvent.objects.get(event_type=ChangeEventType.WORK_VICTORY_CONFIRMED)
    assert event.payload["from_status"] == WorkVictoryStatus.CANDIDATE.value
    assert event.payload["to_status"] == WorkVictoryStatus.CONFIRMED.value
    assert event.actor == department_head


def test_editing_the_wording_never_promotes_a_candidate(normal_matter, specialist):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Esialgne sõnastus", actor=specialist, **_year(2026)
    )
    update_work_victory(record=record, title="Parem sõnastus", actor=specialist, **_year(2026))

    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.CANDIDATE
    assert record.title == "Parem sõnastus"


def test_a_rejected_claim_is_kept_and_loses_its_confirmation(
    normal_matter, specialist, department_head
):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Ei tulnud välja", actor=specialist, **_year(2026)
    )
    confirm_work_victory(record=record, actor=department_head)
    reject_work_victory(record=record, actor=department_head, reason="Eelnõu kukkus välja")

    record.refresh_from_db()
    assert record.status == WorkVictoryStatus.NOT_REALIZED
    assert record.confirmed_at is None
    assert record.confirmed_by is None
    assert MatterWorkVictory.objects.filter(pk=record.pk).exists()


def test_a_status_cannot_be_set_twice(normal_matter, specialist, department_head):
    record = add_work_victory_candidate(
        matter=normal_matter, title="Ettepanek arvestati", actor=specialist, **_year(2026)
    )
    confirm_work_victory(record=record, actor=department_head)

    with pytest.raises(DomainError):
        confirm_work_victory(record=record, actor=department_head)


def test_the_database_refuses_a_confirmed_victory_with_no_timestamp(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterWorkVictory.objects.create(
            matter=normal_matter, title="Kinnitatud eikuskil", status=WorkVictoryStatus.CONFIRMED
        )


# -- filtering --------------------------------------------------------------


def test_the_year_filter_never_absorbs_an_unknown_period(specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    dated = add_work_victory_candidate(
        matter=matter, title="2026. aasta võit", actor=specialist, **_year(2026)
    )
    add_work_victory_candidate(matter=matter, title="Teadmata aeg", actor=specialist)
    confirm_work_victory(record=dated, actor=department_head)

    in_2026 = selectors.work_victories(
        user=specialist, status=WorkVictoryStatus.CONFIRMED, year=2026
    )
    assert [record.title for record in in_2026] == ["2026. aasta võit"]

    unknown = selectors.work_victories(user=specialist, year=selectors.UNKNOWN_PERIOD)
    assert [record.title for record in unknown] == ["Teadmata aeg"]


def test_the_unknown_period_sentinel_never_reaches_a_year_lookup(specialist):
    """`period_date__year="teadmata"` is a ValidationError, not an empty page."""
    matter = factories.MatterFactory(owner=specialist)
    add_work_victory_candidate(matter=matter, title="Teadmata aeg", actor=specialist)

    assert selectors.work_victories(user=specialist, year=selectors.UNKNOWN_PERIOD).count() == 1


def test_the_count_and_the_rows_come_from_the_same_population(specialist, department_head):
    matter = factories.MatterFactory(owner=specialist)
    for index in range(3):
        record = add_work_victory_candidate(
            matter=matter, title=f"Võit {index}", actor=specialist, **_year(2026)
        )
        if index == 0:
            confirm_work_victory(record=record, actor=department_head)

    queryset = selectors.work_victories(
        user=specialist, status=WorkVictoryStatus.CANDIDATE, year=2026
    )
    counts = selectors.work_victory_counts(specialist)

    assert queryset.count() == len(list(queryset)) == 2
    assert counts[WorkVictoryStatus.CANDIDATE.value] == 2
    assert counts[WorkVictoryStatus.CONFIRMED.value] == 1


def test_an_unknown_status_is_refused_rather_than_ignored(specialist):
    with pytest.raises(ValueError, match="Unknown work victory status"):
        selectors.work_victories(user=specialist, status="VOITSIME")
