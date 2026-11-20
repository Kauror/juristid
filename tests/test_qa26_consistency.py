"""Quality audit 2026-11, package I: one answer where two surfaces disagreed.

QA26-DUP-03  a deactivated colleague's step is the Teema owner's on every work
             surface, as the bell already said (owner's decision 19.11.2026).
QA26-DUP-05  «30 päeva» is 30 dates, today included, for deadlines as for
             commencements (owner's decision 2026-10-14, ADR 0158 §4).
QA26-DUP-06  a two-digit year is this century in a form field too.
QA26-DUP-08  Minu asjad's two filter boxes match by one rule.
QA26-ARCH-09 a Teema is found by its current vastutaja, and list filters fold
             diacritics.

Every name is invented.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from django.utils import timezone

from app.core.widgets import EstonianDateField
from app.matters import work_items as wi
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

STATIC = Path(__file__).resolve().parent.parent / "static" / "js"


# ---------------------------------------------------------------------------
# DUP-03: whose step
# ---------------------------------------------------------------------------


def _step(matter, responsible, actor, *, target_date):
    dated = {"date_precision": DatePrecision.EXACT} if target_date else {}
    set_next_action(
        matter=matter,
        text="Saata kiri",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=target_date,
        responsible=responsible,
        actor=actor,
        **dated,
    )


@pytest.mark.django_db
def test_a_deactivated_colleagues_step_is_the_owners_on_every_work_surface(
    specialist, other_specialist
) -> None:
    from app.notifications.categories import CATEGORIES
    from app.notifications.enums import NotificationCategory

    today = timezone.localdate()
    owner, carrier = specialist, other_specialist
    matter = factories.MatterFactory(owner=owner, title="Sünteetiline sammu teema")
    _step(matter, carrier, owner, target_date=today)
    carrier.is_active = False
    carrier.save(update_fields=["is_active"])

    bell = CATEGORIES[NotificationCategory.ACTION_DEADLINE].sources(owner)
    owner_desk = [
        i for i in wi.work_items(owner, today=today, responsible=owner) if i.matter_id == matter.pk
    ]
    carrier_desk = [
        i
        for i in wi.work_items(owner, today=today, responsible=carrier)
        if i.matter_id == matter.pk
    ]
    department = [i for i in wi.work_items(owner, today=today) if i.matter_id == matter.pk]

    assert bell.filter(matter=matter).exists()
    assert len(owner_desk) == 1
    assert carrier_desk == []
    # The calendar and the register's ?vastutaja= read `item.responsible`.
    assert [i.responsible for i in department] == [owner]
    assert wi.dated_actions(owner, responsible=owner).filter(matter=matter).exists()


@pytest.mark.django_db
def test_an_undated_step_of_a_deactivated_colleague_is_on_the_owners_kuupaevata(
    specialist, other_specialist
) -> None:
    owner, carrier = specialist, other_specialist
    matter = factories.MatterFactory(owner=owner, title="Sünteetiline kuupäevata teema")
    _step(matter, carrier, owner, target_date=None)
    carrier.is_active = False
    carrier.save(update_fields=["is_active"])

    assert wi.undated_actions(owner, responsible=owner).filter(matter=matter).exists()
    assert not wi.undated_actions(owner, responsible=carrier).filter(matter=matter).exists()
    assert matter.pk in wi.work_population_ids(owner, wi.WORK_UNDATED, responsible=owner)


@pytest.mark.django_db
def test_an_active_colleagues_step_stays_theirs_and_an_unassigned_one_stays_vastutajata(
    specialist, other_specialist
) -> None:
    today = timezone.localdate()
    owner, carrier = specialist, other_specialist
    theirs = factories.MatterFactory(owner=owner, title="Sünteetiline kolleegi samm")
    _step(theirs, carrier, owner, target_date=today)
    nobody = factories.MatterFactory(owner=owner, title="Sünteetiline vastutajata samm")
    _step(nobody, owner, owner, target_date=today)
    NextAction.objects.filter(matter=nobody).update(responsible=None)

    owner_desk = {i.matter_id for i in wi.work_items(owner, today=today, responsible=owner)}
    carrier_desk = {i.matter_id for i in wi.work_items(owner, today=today, responsible=carrier)}
    assert theirs.pk not in owner_desk
    assert theirs.pk in carrier_desk
    assert nobody.pk not in owner_desk
    [item] = [i for i in wi.work_items(owner, today=today) if i.matter_id == nobody.pk]
    assert item.responsible is None


# ---------------------------------------------------------------------------
# DUP-05: «30 päeva»
# ---------------------------------------------------------------------------

FIXED = dt.date(2026, 11, 18)


def test_thirty_days_are_thirty_dates_today_included() -> None:
    from app.core.dates import last_of_n_days

    assert last_of_n_days(FIXED, 30) == dt.date(2026, 12, 17)
    assert last_of_n_days(FIXED, 1) == FIXED


@pytest.mark.django_db
def test_day_thirty_is_later_for_deadlines_as_for_commencements(specialist) -> None:
    from app.intelligence import commencements

    inside, outside = FIXED + dt.timedelta(days=29), FIXED + dt.timedelta(days=30)
    bands = {}
    for day in (inside, outside):
        matter = factories.MatterFactory(owner=specialist, title=f"Sünteetiline {day}")
        _step(matter, specialist, specialist, target_date=day)
        [item] = [i for i in wi.work_items(specialist, today=FIXED) if i.matter_id == matter.pk]
        bands[day] = wi.band_of(item, FIXED, wi.end_of_iso_week(FIXED), None)

    assert bands == {inside: wi.BAND_NEXT_30, outside: wi.BAND_LATER}
    assert commencements.soon_last_day(FIXED) == inside
    assert wi.deadline_window(wi.WORK_DEADLINE_30_DAYS, FIXED)[1] == inside
    assert wi.deadline_window(wi.WORK_DEADLINE_BEYOND, FIXED)[0] == outside


# ---------------------------------------------------------------------------
# DUP-06: two-digit years
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("1.1.99", dt.date(2099, 1, 1)),
        ("5.3.70", dt.date(2070, 3, 5)),
        ("7.9.26", dt.date(2026, 9, 7)),
        ("07.09.2026", dt.date(2026, 9, 7)),
        ("2026-09-07", dt.date(2026, 9, 7)),
    ],
)
def test_a_date_box_reads_a_two_digit_year_as_the_picker_does(typed, expected) -> None:
    assert EstonianDateField().clean(typed) == expected


@pytest.mark.parametrize("typed", ["31.02.26", "1.1.1899", "7/9/2026"])
def test_a_date_box_still_refuses_what_is_not_a_day(typed) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        EstonianDateField().clean(typed)


def test_the_period_preview_reads_two_digit_years_as_the_server_does() -> None:
    source = (STATIC / "ux.js").read_text(encoding="utf-8")
    assert "1900 + y" not in source
    assert "y += 2000;" in source


# ---------------------------------------------------------------------------
# DUP-08: one row-filter rule
# ---------------------------------------------------------------------------


def test_minu_asjad_band_filter_uses_the_shared_folding_rule() -> None:
    band = (STATIC / "work-filter.js").read_text(encoding="utf-8")
    shared = (STATIC / "ux.js").read_text(encoding="utf-8")
    assert "toLocaleLowerCase" not in band
    assert "rule.matches(" in band and "rule.words(" in band
    assert "window.JuristidRowFilter = { words: filterWords, matches: rowMatches };" in shared
    # The portfolio box matches every word, not the literal phrase.
    assert "text.indexOf(needle)" not in shared


# ---------------------------------------------------------------------------
# ARCH-09: search by vastutaja, folded list filters
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_finds_a_teema_by_its_current_vastutaja(specialist) -> None:
    from app.search.indexing import indexed_text_for, refresh_matter
    from app.search.services import search_matters

    owner = factories.UserFactory(display_name="Kristofer Praeguvastutaja")
    matter = factories.MatterFactory(
        title="Sünteetiline teema ilma nimedeta",
        owner=owner,
        former_owner_name="Malle Endinevastutaja",
    )
    refresh_matter(matter)

    people = indexed_text_for(matter)["people_text"]
    assert "Kristofer Praeguvastutaja" in people
    assert "Malle Endinevastutaja" in people
    for term in ("Praeguvastutaja", "Endinevastutaja"):
        assert matter.pk in {r.matter.pk for r in search_matters(query=term, user=specialist)}, term


@pytest.mark.django_db
def test_saadetud_filter_folds_diacritics(specialist) -> None:
    from app.submissions.workspace import SentFilters, sent_queryset
    from tests.follow_ups import send_koja_arvamus

    owner = factories.UserFactory(display_name="Andrus Šmigunõun")
    matter = factories.MatterFactory(title="Sünteetiline õigusakt", owner=owner)
    addressee = factories.OrganisationFactory(name="Sünteetiline ministeerium")
    send_koja_arvamus(matter, specialist, [addressee], dt.date(2026, 11, 2))

    for term in ("Smigunoun", "šmigunõun", "oigusakt"):
        found = sent_queryset(specialist, SentFilters(query=term)).filter(matter=matter)
        assert found.exists(), term
