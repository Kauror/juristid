"""Teema käik, walked page by page, is the whole chronology — once (ENG-018).

The chronology used to cap the change events it read at three per row it meant
to show, ordered by write time, before it knew which of them would become rows.
Some events never do: the row saying which operation wrote a record, a file
already shown on its record, a stage move or next step folded into the Märge
that caused it. On a file-heavy Matter the cap ran out before the rows did, so
early history vanished — «Teema loodud» first — with no «Näita varasemaid» to
reach it, folded clauses were lost, a split operation left a stray «määras
järgmise sammu» row, and consecutive pages used different caps and repeated or
skipped rows.

The contract these tests hold is structural rather than numeric: for every
shape of event density, the pages a reader can walk — first page, then each
«Näita varasemaid» — concatenate to exactly the unbounded projection. Same
rows, same order, same folded clauses, no row twice, and `has_more` false only
on the last page.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.matters.services import create_matter
from app.matters.timeline import matter_timeline
from app.matters.views import TIMELINE_PAGE_SIZE
from app.matters.workspace import (
    add_engagement_feedback,
    add_matter_engagement,
    add_matter_note,
    add_procedural_development,
)
from app.workflow.models import StageVocabulary

pytestmark = pytest.mark.django_db

EVERYTHING = 100_000


def _new_matter(owner):
    """Through the service, so «Teema loodud» is on the chronology to lose."""
    return create_matter(title="Tihe teema", actor=owner, owner=owner)


def _file(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _signature(item: Any) -> tuple:
    """Everything a reader sees a row by: its place, what it is, what it folded."""
    return (
        item.occurred_at,
        item.created_at,
        item.sort_key,
        item.item_type,
        getattr(item.entry, "pk", None),
        getattr(getattr(item, "record", None), "pk", None),
        tuple(event.pk for event in (item.events or ())),
        item.stage_effect,
        tuple(item.summary_verbs or ()),
        getattr(item.milestone, "what", None),
    )


def _walk(matter, user, *, limit: int = TIMELINE_PAGE_SIZE, only: str | None = None):
    kwargs = {"only": only} if only else {}
    pages: list[list[Any]] = []
    offset = 0
    while True:
        page, more = matter_timeline(matter=matter, user=user, limit=limit, offset=offset, **kwargs)
        pages.append(page)
        if not more:
            return pages
        assert page, "a page that says more follows must not be empty"
        offset += limit
        assert len(pages) < 200, "the walk does not end"


def _assert_walk_is_the_whole_chronology(matter, user, **kwargs) -> list[Any]:
    full, more = matter_timeline(matter=matter, user=user, limit=EVERYTHING, **kwargs)
    assert more is False
    pages = _walk(matter, user, **kwargs)
    walked = [item for page in pages for item in page]

    expected = [_signature(item) for item in full]
    actual = [_signature(item) for item in walked]
    assert len(set(actual)) == len(actual), "a row was shown twice"
    assert actual == expected, (len(actual), len(expected))
    # Every page but the last is full, and only the last says nothing follows.
    assert all(len(page) == TIMELINE_PAGE_SIZE for page in pages[:-1])
    return full


def _created_is_reachable(items) -> None:
    labels = [getattr(item.milestone, "what", None) for item in items]
    assert labels.count("Teema loodud") == 1, labels


def _stages() -> tuple[StageVocabulary, StageVocabulary]:
    return (
        StageVocabulary.objects.get(key="consultation"),
        StageVocabulary.objects.get(key="in_force"),
    )


# -- dense shapes -------------------------------------------------------------


def test_one_kaasamine_with_many_reply_files(specialist):
    """The audit's first reproduction: 95 reply files on one round, five rows,
    one of them reachable, «Teema loodud» never."""
    matter = _new_matter(specialist)
    add_matter_note(matter=matter, author=specialist, body="Enne kaasamist")
    engagement = add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        occurred_on=date(2026, 5, 4),
        feedback_deadline=date(2026, 5, 20),
    ).record
    add_engagement_feedback(
        engagement=engagement,
        author=specialist,
        feedback_received="Vastused",
        uploads=[_file(f"vastus-{index:03d}.pdf") for index in range(95)],
    )
    add_matter_note(matter=matter, author=specialist, body="Pärast kaasamist")

    full = _assert_walk_is_the_whole_chronology(matter, specialist)
    _created_is_reachable(full)


def test_many_marge_saves_each_with_files_a_stage_and_a_next_step(specialist):
    """rules1-0: at 24 such saves «Teema loodud» went missing and a stray row appeared."""
    matter = _new_matter(specialist)
    first, second = _stages()
    day = date(2026, 1, 5)
    for index in range(40):
        day += timedelta(days=3)
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Samm {index:02d}",
            occurred_on=day,
            stage=first if index % 2 else second,
            next_text=f"Järgmine {index:02d}",
            next_date=day + timedelta(days=10),
            uploads=[_file(f"s{index:02d}-a.pdf"), _file(f"s{index:02d}-b.pdf")],
        )

    full = _assert_walk_is_the_whole_chronology(matter, specialist)
    _created_is_reachable(full)
    # Folded clauses survived: every step moved the stage, so every step row
    # says where to — and none became a stray row of its own.
    steps = [item for item in full if getattr(item, "record", None) is not None]
    assert len(steps) == 40
    assert all(item.stage_effect for item in steps[:-1]), [item.stage_effect for item in steps]
    stray = [
        item
        for item in full
        if item.record is None and item.entry is None and item.milestone is None
    ]
    assert stray == [], [item.item_type for item in stray]


def test_backdated_records_are_neither_repeated_nor_skipped(specialist):
    """40 backdated Märge: 4 rows appeared on both pages and 4 on neither."""
    matter = _new_matter(specialist)
    first, _second = _stages()
    for index in range(40):
        # Written in one order, dated in another, several to a day.
        on = date(2025, 1, 1) + timedelta(days=(index * 37) % 90)
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Tagantjärele {index:02d}",
            occurred_on=on,
            next_text=f"Siis {index:02d}",
            uploads=[_file(f"b{index:02d}.pdf")],
        )
        if index % 5 == 0:
            add_matter_note(matter=matter, author=specialist, body=f"Märkus {index:02d}")
    add_procedural_development(matter=matter, author=specialist, title="Liigub edasi", stage=first)

    full = _assert_walk_is_the_whole_chronology(matter, specialist)
    _created_is_reachable(full)


def test_a_mixed_file_over_many_pages(specialist):
    """Notes with files, steps with effects, a busy round — more than two pages."""
    matter = _new_matter(specialist)
    first, second = _stages()
    day = date(2025, 3, 3)
    for index in range(30):
        day += timedelta(days=2)
        add_matter_note(
            matter=matter,
            author=specialist,
            body=f"Märge {index:02d}",
            uploads=[_file(f"m{index:02d}.pdf")] if index % 3 == 0 else [],
        )
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Areng {index:02d}",
            occurred_on=day,
            stage=first if index % 4 == 0 else (second if index % 4 == 2 else None),
            next_text=f"Edasi {index:02d}" if index % 2 else "",
            uploads=[_file(f"a{index:02d}-{n}.pdf") for n in range(index % 3)],
        )
    engagement = add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        occurred_on=day,
        feedback_deadline=day + timedelta(days=14),
    ).record
    add_engagement_feedback(
        engagement=engagement,
        author=specialist,
        uploads=[_file(f"v{index:02d}.pdf") for index in range(20)],
    )

    full = _assert_walk_is_the_whole_chronology(matter, specialist)
    assert len(full) > 2 * TIMELINE_PAGE_SIZE
    _created_is_reachable(full)


@pytest.mark.parametrize("only", ["sissekanded", "sundmused"])
def test_the_filtered_views_walk_the_same_way(specialist, only):
    from app.matters import timeline

    value = {
        "sissekanded": timeline.TIMELINE_FILTER_ENTRIES,
        "sundmused": timeline.TIMELINE_FILTER_EVENTS,
    }[only]
    matter = _new_matter(specialist)
    first, second = _stages()
    for index in range(35):
        add_matter_note(matter=matter, author=specialist, body=f"Märge {index:02d}")
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Areng {index:02d}",
            occurred_on=date(2025, 6, 1) + timedelta(days=index),
            stage=first if index % 2 else second,
            next_text=f"Edasi {index:02d}",
            uploads=[_file(f"f{index:02d}.pdf")],
        )

    _assert_walk_is_the_whole_chronology(matter, specialist, only=value)


def test_a_reader_walks_the_whole_chronology_too(specialist, reader):
    matter = _new_matter(specialist)
    first, second = _stages()
    for index in range(30):
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Samm {index:02d}",
            stage=first if index % 2 else second,
            next_text=f"Järgmine {index:02d}",
            uploads=[_file(f"r{index:02d}-a.pdf"), _file(f"r{index:02d}-b.pdf")],
        )

    full = _assert_walk_is_the_whole_chronology(matter, reader)
    _created_is_reachable(full)


# -- over HTTP -------------------------------------------------------------------


ROW = re.compile(r'<article class="uxtl__item[ "]')


def test_the_pages_a_browser_walks_hold_every_row_once(client, specialist):
    """The same walk through `?nihe=`, as «Näita varasemaid» makes it."""
    matter = _new_matter(specialist)
    first, second = _stages()
    for index in range(40):
        add_procedural_development(
            matter=matter,
            author=specialist,
            title=f"Samm {index:02d}",
            stage=first if index % 2 else second,
            next_text=f"Järgmine {index:02d}",
            uploads=[_file(f"h{index:02d}-a.pdf"), _file(f"h{index:02d}-b.pdf")],
        )
    full, _more = matter_timeline(matter=matter, user=specialist, limit=EVERYTHING)
    client.force_login(specialist)

    url = reverse("matters:timeline_page", kwargs={"pk": matter.pk})
    rows = 0
    created = 0
    offset = 0
    while True:
        body = client.get(url, {"nihe": offset}).content.decode()
        rows += len(ROW.findall(body))
        created += body.count("Teema loodud")
        if f"nihe={offset + TIMELINE_PAGE_SIZE}" not in body:
            break
        offset += TIMELINE_PAGE_SIZE
    assert rows == len(full)
    assert created == 1


# -- bounded, not merely correct -------------------------------------------------


def test_the_first_page_does_not_read_the_whole_history(specialist, monkeypatch):
    """Correct by reading everything would be easy and would break §13.4.

    The first page of a 300-note file reads about a page of entries and the
    events that assemble them — not 300 of each.
    """
    from app.matters import timeline

    matter = _new_matter(specialist)
    for index in range(300):
        add_matter_note(matter=matter, author=specialist, body=f"Märge {index:03d}")

    loaded: list[tuple[int, int]] = []
    original = timeline._ChronologySources.load

    def counting(self, bound):
        entries, events, projected = original(self, bound)
        loaded.append((len(entries), len(events)))
        return entries, events, projected

    monkeypatch.setattr(timeline._ChronologySources, "load", counting)
    page, more = matter_timeline(matter=matter, user=specialist, limit=TIMELINE_PAGE_SIZE)

    assert len(page) == TIMELINE_PAGE_SIZE and more is True
    entries, events = loaded[-1]
    assert entries <= 2 * (TIMELINE_PAGE_SIZE + 1), loaded
    assert events <= 4 * (TIMELINE_PAGE_SIZE + 1), loaded
