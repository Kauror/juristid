"""The Q builders behind Täpsem otsing, tested where the rule lives.

Selector tests rather than view tests wherever the selector expresses the rule
more clearly. "Both ends of a range are inclusive" is a statement about a Q
object; asserting it through a rendered page would prove the same thing more
slowly and blame the wrong module when it broke.
"""

from __future__ import annotations

import datetime

import pytest

from app.core.enums import Visibility
from app.matters import selectors
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db

JANUARY_FIRST = datetime.date(2024, 1, 1)
JANUARY_LAST = datetime.date(2024, 1, 31)


def matched(condition) -> set[str]:
    return set(Matter.objects.filter(condition).values_list("title", flat=True))


# -- date ranges -------------------------------------------------------------


def test_both_ends_of_a_range_are_inclusive():
    """01.01–31.01 means January, the 31st included.

    A `kuni` that excluded its own day would drop the busiest day of the month
    from a deadline report without saying so.
    """
    factories.MatterFactory(title="Esimesel", received_date=JANUARY_FIRST)
    factories.MatterFactory(title="Viimasel", received_date=JANUARY_LAST)
    factories.MatterFactory(title="Enne", received_date=datetime.date(2023, 12, 31))
    factories.MatterFactory(title="Pärast", received_date=datetime.date(2024, 2, 1))

    condition = selectors.date_range_q("received_date", start=JANUARY_FIRST, end=JANUARY_LAST)
    assert matched(condition) == {"Esimesel", "Viimasel"}


def test_one_open_end_is_allowed():
    factories.MatterFactory(title="Hiljem", received_date=datetime.date(2024, 6, 1))
    factories.MatterFactory(title="Varem", received_date=datetime.date(2019, 6, 1))

    since = selectors.date_range_q("received_date", start=JANUARY_FIRST, end=None)
    until = selectors.date_range_q("received_date", start=None, end=JANUARY_FIRST)
    assert matched(since) == {"Hiljem"}
    assert matched(until) == {"Varem"}


def test_no_ends_at_all_narrows_nothing():
    """An empty pair of date boxes is not a filter that matches nothing."""
    factories.MatterFactory(title="Kuupäevaga", received_date=JANUARY_FIRST)
    factories.MatterFactory(title="Kuupäevata", received_date=None)

    condition = selectors.date_range_q("received_date", start=None, end=None)
    assert matched(condition) == {"Kuupäevaga", "Kuupäevata"}


def test_a_reversed_range_matches_nothing_rather_than_everything():
    factories.MatterFactory(title="Jaanuaris", received_date=datetime.date(2024, 1, 15))
    condition = selectors.date_range_q("received_date", start=JANUARY_LAST, end=JANUARY_FIRST)
    assert matched(condition) == set()


def test_a_row_with_no_date_falls_outside_every_range():
    """`NULL` is not "unknown, so include it". An archive row with no arrival
    date is not evidence that it arrived in January."""
    factories.MatterFactory(title="Kuupäevata", received_date=None)
    condition = selectors.date_range_q("received_date", start=JANUARY_FIRST, end=JANUARY_LAST)
    assert matched(condition) == set()


def test_the_two_date_columns_stay_separate():
    factories.MatterFactory(
        title="Saabus jaanuaris",
        received_date=JANUARY_FIRST,
        response_deadline=datetime.date(2025, 9, 9),
    )
    factories.MatterFactory(
        title="Tähtaeg jaanuaris",
        received_date=datetime.date(2025, 9, 9),
        response_deadline=JANUARY_FIRST,
    )

    arrived = selectors.date_range_q("received_date", start=JANUARY_FIRST, end=JANUARY_LAST)
    due = selectors.date_range_q("response_deadline", start=JANUARY_FIRST, end=JANUARY_LAST)
    assert matched(arrived) == {"Saabus jaanuaris"}
    assert matched(due) == {"Tähtaeg jaanuaris"}


# -- the organisation convenience filter -------------------------------------


def test_either_direction_counts_as_involvement():
    ministry = factories.OrganisationFactory()
    other = factories.OrganisationFactory()

    factories.MatterFactory(title="Nemad saatsid", source_organisations=[ministry])
    factories.MatterFactory(title="Meie vastasime", addressee_organisation=ministry)
    factories.MatterFactory(
        title="Mõlemat pidi", source_organisations=[ministry], addressee_organisation=ministry
    )
    factories.MatterFactory(title="Kõrvaline", source_organisations=[other])

    condition = selectors.organisation_involved_q(ministry.pk)
    assert matched(condition) == {"Nemad saatsid", "Meie vastasime", "Mõlemat pidi"}


def test_a_matter_involving_a_body_twice_is_still_one_row():
    """The OR must not turn into a join that duplicates the row."""
    ministry = factories.OrganisationFactory()
    factories.MatterFactory(
        title="Mõlemat pidi",
        source_organisations=[ministry],
        addressee_organisation=ministry,
    )
    assert Matter.objects.filter(selectors.organisation_involved_q(ministry.pk)).count() == 1


def test_the_convenience_filter_reads_and_never_writes():
    """A query convenience. The stored distinction is untouched (brief 11F)."""
    ministry = factories.OrganisationFactory()
    sent = factories.MatterFactory(title="Nemad saatsid", source_organisations=[ministry])

    list(Matter.objects.filter(selectors.organisation_involved_q(ministry.pk)))

    sent.refresh_from_db()
    assert list(sent.source_organisations.all()) == [ministry]
    assert sent.addressee_organisation_id is None


# -- materials ---------------------------------------------------------------


def test_materials_present_and_absent_partition_the_register(specialist):
    with_file = factories.MatterFactory(title="Failiga")
    factories.DocumentFactory(matter=with_file)
    factories.MatterFactory(title="Failita")

    base = Matter.objects.all()
    present = selectors.filter_by_materials(base, specialist, selectors.MATERIALS_PRESENT)
    absent = selectors.filter_by_materials(base, specialist, selectors.MATERIALS_ABSENT)

    assert set(present.values_list("title", flat=True)) == {"Failiga"}
    assert set(absent.values_list("title", flat=True)) == {"Failita"}
    assert present.count() + absent.count() == base.count()


def test_a_restricted_document_does_not_make_a_matter_look_material(reader, specialist):
    """Answering from the raw table would tell somebody that material they
    cannot open exists (docs/adr/0014)."""
    matter = factories.MatterFactory(title="Nähtav teema", owner=specialist)
    factories.DocumentFactory(matter=matter, visibility_override=Visibility.RESTRICTED)

    present = selectors.filter_by_materials(
        Matter.objects.all(), reader, selectors.MATERIALS_PRESENT
    )
    assert list(present) == []


def test_an_unknown_value_matches_nothing():
    factories.MatterFactory()
    empty = selectors.filter_by_materials(Matter.objects.all(), None, "vahest")
    assert empty.count() == 0


def test_several_files_still_mean_one_row(specialist):
    """`EXISTS`, not a join: three attachments are one Matter, not three."""
    matter = factories.MatterFactory(title="Kolme failiga")
    for _ in range(3):
        factories.DocumentFactory(matter=matter)

    present = selectors.filter_by_materials(
        Matter.objects.all(), specialist, selectors.MATERIALS_PRESENT
    )
    assert list(present.values_list("title", flat=True)) == ["Kolme failiga"]
