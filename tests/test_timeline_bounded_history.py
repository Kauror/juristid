"""A chronology page costs a page, not the whole Matter (ENG-125).

The Matter page, every `?nihe=` page and every POST that re-renders the overview
built Teema käik from the Matter's *whole* structured history — every
engagement, external position, procedural development, sent opinion and
overview — then sorted it in Python and kept thirty rows. ENG-018 bounded the
entries and change events; this bounds the record families by the same day the
page is read from, in SQL, by the rule each row is placed by.

Two things are held here, and the first is the one that matters: every page
walked is still exactly the unbounded chronology, now with every record family
in play — including the three places a bounded read could have lied: a round
answered by a position read from a later day, a sent opinion's file that is not
a row, and a stage move saved with a development older than the page. Then:
the first page of a long history reads about a page of each source, and costs
the same number of queries as a short one.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.matters import timeline
from app.matters.timeline import matter_timeline
from app.matters.views import TIMELINE_PAGE_SIZE
from app.matters.workspace import (
    add_matter_engagement,
    add_matter_external_position,
    add_matter_koda_opinion,
    add_matter_note,
    add_matter_website_overview,
    add_procedural_development,
    cancel_matter_website_overview,
)
from app.workflow.enums import DatePrecision
from tests import factories
from tests.test_timeline_pagination_walk import (
    _assert_walk_is_the_whole_chronology,
    _created_is_reachable,
    _file,
    _new_matter,
    _stages,
    _walk,
)

pytestmark = pytest.mark.django_db


def _every_family(matter, author, *, rounds: int, start: date) -> None:
    """Each record family, spread over time and written out of date order."""
    organisation = factories.OrganisationFactory()
    first, second = _stages()
    for index in range(rounds):
        on = start + timedelta(days=(index * 11) % (rounds * 3))
        engagement = add_matter_engagement(
            matter=matter,
            author=author,
            audience=f"Ring {index:02d}",
            occurred_on=on,
            feedback_deadline=on + timedelta(days=14),
        ).record
        # A position answering that round, stated weeks later: read from a day
        # the round itself is before.
        add_matter_external_position(
            matter=matter,
            author=author,
            organisation=organisation,
            stated_on=on + timedelta(days=40),
            summary=f"Seisukoht {index:02d}",
            engagement=engagement,
        )
        add_procedural_development(
            matter=matter,
            author=author,
            title=f"Areng {index:02d}",
            occurred_on=on - timedelta(days=30),
            stage=first if index % 2 else second,
            next_text=f"Edasi {index:02d}",
            uploads=[_file(f"d{index:02d}.pdf")],
        )
        add_matter_note(matter=matter, author=author, body=f"Märge {index:02d}")
        if index % 3 == 0:
            add_matter_koda_opinion(
                matter=matter,
                author=author,
                upload=_file(f"arvamus-{index:02d}.pdf"),
                recipients=[organisation],
                sent_on=on + timedelta(days=5),
                title=f"Arvamus {index:02d}",
            )
        if index % 4 == 0:
            add_matter_website_overview(
                matter=matter,
                author=author,
                url=f"https://koda.ee/uudis-{index:02d}",
                published_on=on + timedelta(days=9) if index % 8 else None,
            )
        if index % 5 == 0:
            planned = add_matter_website_overview(matter=matter, author=author).record
            cancel_matter_website_overview(matter=matter, author=author, overview=planned)
        if index % 6 == 0:
            from app.matters.workspace import add_matter_important_date

            add_matter_important_date(
                matter=matter,
                author=author,
                title=f"Tähtaeg {index:02d}",
                date_value=on,
                period_end=on,
                date_precision=DatePrecision.EXACT.value,
            )


def test_every_record_family_walks_to_the_whole_chronology(specialist):
    matter = _new_matter(specialist)
    _every_family(matter, specialist, rounds=24, start=date(2024, 2, 1))

    full = _assert_walk_is_the_whole_chronology(matter, specialist)
    assert len(full) > 3 * TIMELINE_PAGE_SIZE
    _created_is_reachable(full)
    kinds = {item.item_type for item in full}
    for kind in (
        "MatterEngagement",
        "MatterExternalPosition",
        "MatterProceduralDevelopment",
        "Submission",
        "MatterWebsiteOverview",
    ):
        assert kind in kinds, kinds


def test_a_reader_walks_every_family_too(specialist, reader):
    matter = _new_matter(specialist)
    _every_family(matter, specialist, rounds=12, start=date(2025, 1, 6))

    _assert_walk_is_the_whole_chronology(matter, reader)


def _dense(specialist, count: int):
    """`count` developments and as many notes: two rows each, ~4 events each."""
    matter = _new_matter(specialist)
    first, second = _stages()
    day = date(2021, 1, 4)
    for index in range(count):
        day += timedelta(days=2)
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Samm {index:03d}",
            occurred_on=day,
            stage=first if index % 2 else second,
            next_text=f"Järgmine {index:03d}",
        )
        add_matter_note(matter=matter, author=specialist, body=f"Märge {index:03d}")
    return matter


@pytest.fixture
def loads(monkeypatch):
    """What each `load` actually read: entries, events, projected record rows."""
    seen: list[tuple[int, int, int]] = []
    original = timeline._ChronologySources.load

    def counting(self, since):
        entries, events, projected = original(self, since)
        seen.append((len(entries), len(events), len(projected) - len(self.fact_rows)))
        return entries, events, projected

    monkeypatch.setattr(timeline._ChronologySources, "load", counting)
    return seen


def test_first_middle_and_last_pages_of_a_long_history(specialist, loads):
    """~600 rows: the walk is exact, and the first page reads about a page."""
    matter = _dense(specialist, 300)

    full = _assert_walk_is_the_whole_chronology(matter, specialist)
    assert len(full) >= 600
    pages = _walk(matter, specialist)
    middle = len(pages) // 2
    assert pages[middle] and pages[-1]

    loads.clear()
    first, more = matter_timeline(matter=matter, user=specialist, limit=TIMELINE_PAGE_SIZE)
    assert len(first) == TIMELINE_PAGE_SIZE and more is True
    entries, events, records = loads[-1]
    window = TIMELINE_PAGE_SIZE + 1
    # About a page of each source, never the 300 of each the file holds.
    assert records <= 2 * window, loads
    assert entries <= 2 * window, loads
    assert events <= 8 * window, loads


def test_the_first_page_costs_the_same_on_a_long_history(specialist):
    """Four times the history, the same queries — for first pages of one shape.

    Both files hold more notes than a page, so both first pages are notes: the
    page's own decorations (`_with_next_steps` and the like) ask the same
    questions, and any difference is the history's cost.
    """
    short = _dense(specialist, 60)
    long = _dense(specialist, 240)

    with CaptureQueriesContext(connection) as few:
        matter_timeline(matter=short, user=specialist, limit=TIMELINE_PAGE_SIZE)
    with CaptureQueriesContext(connection) as many:
        page, more = matter_timeline(matter=long, user=specialist, limit=TIMELINE_PAGE_SIZE)

    assert len(page) == TIMELINE_PAGE_SIZE and more is True
    assert len(many.captured_queries) == len(few.captured_queries)
