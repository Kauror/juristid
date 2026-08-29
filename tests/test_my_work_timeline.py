"""Minu töö: one chronological list, and the distinctions inside it.

The dangerous failure on this page is not a missing row — it is a row in the
wrong band. A ministry that has not replied rendered as a missed deadline makes
the whole queue untrustworthy, and a dated OOTAN exiled to a rail makes a lawyer
read two lists and merge them in their head.

Every fixture date is relative to today. A test written around a production date
passes for a fortnight and then starts failing for reasons nobody can reproduce.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.services import add_important_date
from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.matters.services import assign_matter, create_matter
from app.workflow.dates import period_bounds
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db


@pytest.fixture
def today():
    return timezone.localdate()


def _matter(owner, title="Näidisteema", **kwargs):
    return create_matter(title=title, owner=owner, reference_year=2026, **kwargs)


def _bands(user, today):
    return {band.key: band for band in build_my_work(user, today=today).bands}


def _texts(band):
    return [item.text for item in band.items] if band else []


# --- A: an overdue DO is late, and only there ------------------------------


def test_an_overdue_deadline_lands_in_ule_tahtaja(specialist, today):
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=1),
        actor=specialist,
    )

    bands = _bands(specialist, today)

    assert "Esitan arvamuse" in _texts(bands.get(wi.BAND_OVERDUE))
    for key, band in bands.items():
        if key != wi.BAND_OVERDUE:
            assert "Esitan arvamuse" not in _texts(band)


# --- B and C: a passed review date is ripe, never late ---------------------


@pytest.mark.parametrize(
    ("kind", "semantics"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
    ],
)
def test_a_passed_review_date_is_ripe_and_never_overdue(specialist, today, kind, semantics):
    """The single most important rule on the page.

    A lawyer who waits correctly must never be shown as failing
    (master specification 18.8).
    """
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi vastust",
        kind=kind,
        date_semantics=semantics,
        target_date=today - timedelta(days=30),
        actor=specialist,
    )

    bands = _bands(specialist, today)

    # The v2 banding merged the reviews into *Sel nädalal* — they are ordinary
    # dated work, not a category of their own. What did not change, and is the
    # whole point of this test, is that a passed review is never late: it is not
    # in the red band, it is not counted as overdue, and its row prints a
    # neutral «N p» rather than «N p üle» (design handoff 03 §1).
    assert "Ootan ministeeriumi vastust" in _texts(bands.get(wi.BAND_WEEK))
    assert wi.BAND_OVERDUE not in bands
    item = next(
        row for row in bands[wi.BAND_WEEK].items if row.text == "Ootan ministeeriumi vastust"
    )
    assert item.is_review_ripe and not item.is_overdue
    assert item.short_date == "30 p"
    work = build_my_work(specialist, today=today)
    assert work.overdue == 0


# --- D: every mode shares one chronological timeline ----------------------


def test_future_work_of_every_kind_shares_one_timeline(specialist, today):
    """TEEN, OOTAN, JÄLGIN and an Oluline tähtaeg, all inside this week.

    They must land in the same bands as each other, ordered by date. There is
    no separate future Ootan or Jälgin rail — that split is exactly what this
    round removed.
    """
    # A Monday-anchored week so "+1..+4 days" cannot spill past Sunday and turn
    # this into a test of the ISO-week boundary rather than of the merge.
    monday = today - timedelta(days=today.weekday())

    teen = _matter(specialist, title="Teen")
    set_next_action(
        matter=teen,
        text="TEEN homme",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=monday + timedelta(days=1),
        actor=specialist,
    )
    ootan = _matter(specialist, title="Ootan")
    set_next_action(
        matter=ootan,
        text="OOTAN ülehomme",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=monday + timedelta(days=2),
        actor=specialist,
    )
    jalgin = _matter(specialist, title="Jälgin")
    set_next_action(
        matter=jalgin,
        text="JÄLGIN kolme päeva pärast",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=monday + timedelta(days=3),
        actor=specialist,
    )
    milestone_matter = _matter(specialist, title="Tähtaeg")
    add_important_date(
        matter=milestone_matter,
        title="OLULINE nelja päeva pärast",
        date_value=monday + timedelta(days=4),
        period_end=monday + timedelta(days=4),
        actor=specialist,
    )

    items = wi.work_items(specialist, today=monday, responsible=specialist)
    bands = wi.band_items(items, monday, horizon=monday + timedelta(days=60))
    dated = {text for band in bands for text in _texts(band)}

    assert dated == {
        "TEEN homme",
        "OOTAN ülehomme",
        "JÄLGIN kolme päeva pärast",
        "OLULINE nelja päeva pärast",
    }
    # And in one band, in date order — not four lists to be merged by eye.
    week = next(band for band in bands if band.key == wi.BAND_WEEK)
    assert _texts(week) == [
        "TEEN homme",
        "OOTAN ülehomme",
        "JÄLGIN kolme päeva pärast",
        "OLULINE nelja päeva pärast",
    ]


# --- E: an important deadline is a real deadline --------------------------


def test_a_passed_important_deadline_is_overdue(specialist, today):
    matter = _matter(specialist)
    add_important_date(
        matter=matter,
        title="Ülevõtmise tähtaeg",
        date_value=today - timedelta(days=1),
        period_end=today - timedelta(days=1),
        actor=specialist,
    )

    bands = _bands(specialist, today)

    assert "Ülevõtmise tähtaeg" in _texts(bands.get(wi.BAND_OVERDUE))
    assert build_my_work(specialist, today=today).overdue == 1


def test_an_important_deadline_period_is_not_late_until_its_last_day(specialist, today):
    """A quarter that has barely started has not been missed.

    Recording *III kvartal 2026* and then calling it late on 2 July would be
    manufacturing a failure out of a precision the source never claimed.
    """
    matter = _matter(specialist)
    # A real quarter, not an invented span: the service validates that the
    # period matches the precision, and a test that worked around that would be
    # asserting against a shape the product cannot create.
    start, end = period_bounds(today, DatePrecision.QUARTER)
    add_important_date(
        matter=matter,
        title="Konsultatsiooniring",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )

    assert start <= today <= end
    assert build_my_work(specialist, today=today).overdue == 0


# --- F: the deadline follows the Matter's owner ---------------------------


def test_an_important_deadline_moves_with_the_matter_owner(specialist, other_specialist, today):
    """Reassign the Matter and the milestone moves. Nothing edits the deadline.

    ``ImportantDeadline`` carries no responsible column, and this round does not
    add one — the read model reads the Matter's *current* owner, which is what
    makes a handover work without anybody touching the milestone (§4.2).
    """
    matter = _matter(specialist)
    add_important_date(
        matter=matter,
        title="Kliimaministeeriumi tähtaeg",
        date_value=today + timedelta(days=3),
        period_end=today + timedelta(days=3),
        actor=specialist,
    )

    assert "Kliimaministeeriumi tähtaeg" in [
        item.text for item in wi.work_items(specialist, today=today, responsible=specialist)
    ]

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    assert "Kliimaministeeriumi tähtaeg" not in [
        item.text for item in wi.work_items(specialist, today=today, responsible=specialist)
    ]
    assert "Kliimaministeeriumi tähtaeg" in [
        item.text
        for item in wi.work_items(other_specialist, today=today, responsible=other_specialist)
    ]


def test_an_ownerless_matters_deadline_is_on_nobodys_minu_too(specialist, today):
    matter = create_matter(title="Jaotamata teema", reference_year=2026, actor=specialist)
    add_important_date(
        matter=matter,
        title="Jaotamata tähtaeg",
        date_value=today + timedelta(days=2),
        period_end=today + timedelta(days=2),
        actor=specialist,
    )

    mine = wi.work_items(specialist, today=today, responsible=specialist)

    assert "Jaotamata tähtaeg" not in [item.text for item in mine]
    # But it is still visible work, so the department scope keeps it.
    assert "Jaotamata tähtaeg" in [item.text for item in wi.work_items(specialist, today=today)]


# --- G: a NextAction belongs to its responsible, not to the owner ---------


def test_an_action_belongs_to_its_responsible_not_the_matter_owner(
    specialist, other_specialist, today
):
    matter = _matter(specialist, title="Kauri teema")
    set_next_action(
        matter=matter,
        text="Marko esitab märkused",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=1),
        responsible=other_specialist,
        actor=specialist,
    )

    owner_items = [
        item.text for item in wi.work_items(specialist, today=today, responsible=specialist)
    ]
    doer_items = [
        item.text
        for item in wi.work_items(other_specialist, today=today, responsible=other_specialist)
    ]

    assert "Marko esitab märkused" not in owner_items
    assert "Marko esitab märkused" in doer_items


# --- H and I: what has no place on a timeline ----------------------------


def test_a_matter_with_no_next_action_reaches_the_rail(specialist, today):
    _matter(specialist, title="Vaikiv teema")

    work = build_my_work(specialist, today=today)

    assert [row.matter.title for row in work.quiet] == ["Vaikiv teema"]
    assert work.quiet_total == 1


@pytest.mark.parametrize(
    ("kind", "semantics"),
    [
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
    ],
)
def test_an_undated_action_reaches_the_rail_and_not_the_timeline(
    specialist, today, kind, semantics
):
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Millalgi",
        kind=kind,
        date_semantics=semantics,
        target_date=None,
        actor=specialist,
    )

    work = build_my_work(specialist, today=today)

    assert [item.text for item in work.undated] == ["Millalgi"]
    assert not any("Millalgi" in _texts(band) for band in work.bands)


# --- J: nothing of anybody else's ----------------------------------------


def test_another_persons_work_never_appears(specialist, other_specialist, today):
    theirs = _matter(other_specialist, title="Kolleegi teema")
    set_next_action(
        matter=theirs,
        text="Kolleegi tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=other_specialist,
    )
    add_important_date(
        matter=theirs,
        title="Kolleegi tähtaeg",
        date_value=today,
        period_end=today,
        actor=other_specialist,
    )

    work = build_my_work(specialist, today=today)
    rendered = {text for band in work.bands for text in _texts(band)}

    assert rendered == set()
    assert work.quiet == []


# --- the vocabulary a lawyer reads ---------------------------------------


def test_the_date_meanings_are_the_agreed_words(specialist, today):
    """Never *Oodatav umbes*. The stored enum keeps its name; the page does not."""
    matter = _matter(specialist)
    set_next_action(
        matter=matter,
        text="Ootan vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=today + timedelta(days=2),
        actor=specialist,
    )

    item = wi.work_items(specialist, today=today, responsible=specialist)[0]

    assert item.meaning == "OODATAV AEG"
    assert "umbes" not in item.meaning.lower()


def test_a_fuzzy_date_is_not_coerced_to_a_day(specialist, today):
    """A month-precision expectation renders as a month, not as its first day."""
    matter = _matter(specialist)
    anchor = (today.replace(day=1) + timedelta(days=62)).replace(day=1)
    set_next_action(
        matter=matter,
        text="Jälgin ajakava",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=anchor,
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    item = wi.work_items(specialist, today=today, responsible=specialist)[0]

    assert "." not in item.display_date
    assert str(anchor.year) in item.display_date
    assert item.short_date == item.display_date


# --- the page itself ------------------------------------------------------


def test_the_page_renders_and_omits_empty_bands(client, specialist, today):
    client.force_login(specialist)
    matter = _matter(specialist, title="Ainus teema")
    set_next_action(
        matter=matter,
        text="Ainus tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )

    body = client.get(reverse("matters:my_work")).content.decode()

    assert "Ainus tegevus" in body
    # Today is the first day of this week, not a band of its own (03 §1).
    assert "Sel nädalal" in body
    # An empty band is omitted entirely rather than rendered as an empty state.
    assert "Üle tähtaja" not in body
    assert "Järgmised 30 päeva" not in body


def test_an_empty_day_says_so_once(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    assert "Täna ei ole ühtegi tähtajalist tegevust." in body
