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


# --- the overdue cap ------------------------------------------------------
#
# *Üle tähtaja* is capped at ten like every other band. It used to render
# everything it held, and a fortnight of late work then pushed the rest of the
# timeline off the screen. The approved rule is: oldest first, ten rows on
# screen, the remainder inline behind «Näita veel N ▾».
#
# There is a second, larger cap under that one — `BAND_LIMIT`, on how many rows
# the band materialises at all — and it used to be applied to the list the band
# then counted, so the heading described the slice rather than the population.
# The tests at the foot of this section are about that (UX-002).
#
# The population is deliberately mixed. A cap proved against thirteen
# NextActions would also pass if the slice happened to live in the action
# query; these fixtures put the three sources a work row can come from into one
# band, so what is being asserted is the shared WorkBand behaviour.


def _overdue_population(owner, today, count):
    """``count`` overdue work items on distinct days, across all three sources.

    Day *n* back from today is item *n*, so the expected order is simply the
    reverse of the construction order and every assertion about "the ten
    oldest" can name exact dates rather than trusting the same sort it is
    checking.
    """
    made = []
    for index in range(count):
        days = index + 2
        when = today - timedelta(days=days)
        source = index % 3
        if source == 0:
            matter = _matter(owner, title=f"Tähtaeg {days:03d}")
            set_next_action(
                matter=matter,
                text=f"Hilinenud tegevus {days:03d}",
                kind=ActionKind.DO,
                date_semantics=DateSemantics.DEADLINE,
                target_date=when,
                actor=owner,
            )
        elif source == 1:
            matter = _matter(owner, title=f"Verstapost {days:03d}")
            add_important_date(
                matter=matter,
                title=f"Hilinenud verstapost {days:03d}",
                date_value=when,
                period_end=when,
                actor=owner,
            )
        else:
            # No Järgmiseks on this one: an open next action suppresses the
            # Arvamuse tähtaeg as operational work (ADR 0050), so a Matter
            # carrying both would contribute one row rather than the two this
            # helper is counting.
            matter = _matter(owner, title=f"Arvamus {days:03d}")
            matter.response_deadline = when
            matter.save(update_fields=["response_deadline"])
        made.append(when)
    return sorted(made)


def _overdue(user, today):
    return _bands(user, today).get(wi.BAND_OVERDUE)


def test_the_overdue_band_caps_its_preview_at_ten(specialist, today):
    """Thirteen late rows: ten on screen, three behind the disclosure."""
    _overdue_population(specialist, today, 13)

    band = _overdue(specialist, today)

    assert band is not None
    assert band.count == 13
    assert len(band.preview) == 10
    assert len(band.rest) == 3
    assert band.remaining == 3


def test_the_visible_ten_are_the_ten_oldest(specialist, today):
    """Oldest deadline first: 01.01 before 15.01 before 01.02, never the reverse."""
    expected = _overdue_population(specialist, today, 13)

    band = _overdue(specialist, today)

    assert [item.when for item in band.preview] == expected[:10]
    assert band.rest[0].when == expected[10]
    assert [item.when for item in band.rest] == expected[10:]


def test_the_disclosure_holds_the_rest_of_the_same_list(specialist, today):
    """Preview + rest is the whole ordered population, with nothing repeated.

    This is the property that makes the cap safe: the rows behind «Näita veel»
    are a slice of the list the heading counted, not a second query, so no work
    can disappear because only ten rows are initially visible.
    """
    _overdue_population(specialist, today, 13)

    band = _overdue(specialist, today)

    assert band.preview + band.rest == band.items
    keys = [(item.source_type, item.object_id) for item in band.items]
    assert len(set(keys)) == len(keys)


@pytest.mark.parametrize(
    ("total", "preview", "remaining"),
    [(9, 9, 0), (10, 10, 0), (11, 10, 1)],
)
def test_the_boundary_around_ten(specialist, today, total, preview, remaining):
    """Nine and ten need no disclosure; eleven needs one, for exactly one row."""
    _overdue_population(specialist, today, total)

    band = _overdue(specialist, today)

    assert band.count == total
    assert len(band.preview) == preview
    assert band.remaining == remaining
    assert len(band.rest) == remaining


def _overdue_section(body):
    """The rendered `<section class="workband workband--ule_tahtaja">`."""
    start = body.index('class="workband workband--ule_tahtaja"')
    return body[start : body.index("</section>", start)]


def test_the_page_renders_ten_rows_and_puts_three_behind_the_disclosure(client, specialist, today):
    """The markup, not just the read model: ten rows, then «Näita veel 3».

    Split at the disclosure rather than counted over the whole section, because
    counting rows would find thirteen either way — the rest are in the HTML by
    design, which is what makes them a slice of the list the heading counted
    rather than a second query. What the cap decides is which side of
    `<details class="pw-more">` each row is written on.
    """
    _overdue_population(specialist, today, 13)
    client.force_login(specialist)

    section = _overdue_section(client.get(reverse("matters:my_work")).content.decode())
    before, _, behind = section.partition('<details class="pw-more">')

    assert "Üle tähtaja" in before
    # The count in the heading is the whole population, not the visible slice.
    assert '<span class="workband__count">13</span>' in before
    assert before.count("data-workrow") == 10
    assert "Näita veel 3" in behind
    assert behind.count("data-workrow") == 3


def test_the_bands_render_in_reading_order(client, specialist, today):
    """Chronology is the page's whole argument for being one list.

    Minu töö replaced three mode-named sections with one timeline, and the claim
    that makes it readable is that the page runs forwards: what is late, then
    this week, then the month, then later. Nothing asserted that against the
    rendered page — it was true by construction (`work_items.BAND_ORDER`, and a
    template that iterates `work.bands`), and true-by-construction is exactly
    what stops being true when somebody reorders a loop.

    Inherited from `e2e/test_teema_redesign.py::test_minu_too_is_one_dated_list`,
    which asserted it in a browser and had been skipping itself since 2026-08-24:
    its `.workrow` locator could not match `.workrow2`, so `rows.count()` was 0
    and the test reached `pytest.skip` on every run. Two of its three claims were
    already covered or since reversed by ADR 0054; this was the third, and it
    needs no browser — the order is in the markup.
    """
    # One dated obligation in each band, so every band actually renders and the
    # order is observable rather than vacuously true of a one-band page.
    for offset, title in (
        (-4, "Hilinenud"),
        (1, "Sel nädalal"),
        (20, "Kuu jooksul"),
        (45, "Hiljem"),
    ):
        matter = _matter(specialist, title=f"{title} teema")
        set_next_action(
            matter=matter,
            text=f"Tegevus — {title}",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today + timedelta(days=offset),
            actor=specialist,
        )

    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()

    positions = [
        (key, body.index(f'workband--{key}"'))
        for key in wi.BAND_ORDER
        if f'workband--{key}"' in body
    ]

    # All four, so the ordering assertion cannot pass by there being one band.
    assert [key for key, _ in positions] == list(wi.BAND_ORDER), (
        f"expected every band to render, got {[k for k, _ in positions]}"
    )
    assert [key for key, _ in positions] == sorted(
        (key for key, _ in positions), key=wi.BAND_ORDER.index
    ), f"the bands are out of chronological order: {[k for k, _ in positions]}"
    assert [where for _, where in positions] == sorted(where for _, where in positions)


# --- the render cap, and the heading above it (UX-002) ---------------------
#
# `band_items` slices to `BAND_LIMIT` and `WorkBand.count` read `len(items)`, so
# on a big enough band the heading counted the slice. Production showed it
# plainly: `64 üle tähtaja` on the strip, `Üle tähtaja 60` in the heading forty
# pixels below, and four genuinely late rows behind no control on the page —
# under a comment promising that nothing ever left it.
#
# One item per Matter here, which is what makes the strip and the band directly
# comparable: `work_population_ids` counts Matters and the band counts items, so
# a fixture with two overdue commitments on one Matter would make them differ
# for a legitimate reason and prove nothing about the truncation.


def _over_the_cap(specialist, today):
    _overdue_population(specialist, today, wi.BAND_LIMIT + 5)
    return build_my_work(specialist, today=today)


def test_the_heading_counts_the_population_and_not_the_rendered_rows(specialist, today):
    work = _over_the_cap(specialist, today)
    band = next(one for one in work.bands if one.key == wi.BAND_OVERDUE)

    assert band.count == wi.BAND_LIMIT + 5
    assert band.total == wi.BAND_LIMIT + 5
    # The cap is still a cap.
    assert len(band.items) == wi.BAND_LIMIT
    assert band.beyond_cap == 5


def test_the_inline_disclosure_is_unchanged_by_the_honest_total(specialist, today):
    """`Näita veel` still opens the rest of what this page holds, and no more."""
    work = _over_the_cap(specialist, today)
    band = next(one for one in work.bands if one.key == wi.BAND_OVERDUE)

    assert len(band.preview) == 10
    assert band.remaining == wi.BAND_LIMIT - 10
    assert band.preview + band.rest == band.items


def test_the_rows_past_the_cap_have_somewhere_to_be_read(specialist, today):
    """And it is the strip's own list, not a second one that resembles it."""
    work = _over_the_cap(specialist, today)
    band = next(one for one in work.bands if one.key == wi.BAND_OVERDUE)
    strip = next(figure for figure in work.seis if figure.key == "overdue")

    assert band.more_url
    assert band.more_url == strip.url


def test_the_strip_figure_and_the_band_heading_agree(specialist, today):
    """The contradiction, gone: one obligation per Matter, one number for both."""
    work = _over_the_cap(specialist, today)
    band = next(one for one in work.bands if one.key == wi.BAND_OVERDUE)
    strip = next(figure for figure in work.seis if figure.key == "overdue")

    assert strip.value == wi.BAND_LIMIT + 5
    assert band.count == strip.value


def test_capped_rows_are_not_reported_as_deadlines_beyond_the_horizon(specialist, today):
    """`beyond_horizon` counted whatever the bands did not render.

    So every row the cap dropped — late 2025 work, in the first band — was
    reported as a deadline *further away* than the window, which is the opposite
    of what it is. It reads the population now.
    """
    work = _over_the_cap(specialist, today)

    assert work.beyond_horizon == 0


def test_a_band_under_the_cap_offers_no_overflow_link(specialist, today):
    """Nothing new appears on an ordinary morning."""
    _overdue_population(specialist, today, 13)
    work = build_my_work(specialist, today=today)
    band = next(one for one in work.bands if one.key == wi.BAND_OVERDUE)

    assert band.count == 13
    assert band.beyond_cap == 0
    assert len(band.items) == 13


def test_the_page_prints_the_honest_total_and_the_way_to_the_rest(client, specialist, today):
    """The rendered page, because the heading is what a lawyer reads."""
    _overdue_population(specialist, today, wi.BAND_LIMIT + 5)
    client.force_login(specialist)

    body = client.get(reverse("matters:my_work")).content.decode()

    assert f"Näita kõiki {wi.BAND_LIMIT + 5} →" in body
    assert f'<span class="workband__count">{wi.BAND_LIMIT + 5}</span>' in body
