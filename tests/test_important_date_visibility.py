"""A recorded `Oluline tähtaeg` is visible on the Matter that holds it.

It was not. `+ Märge → Oluline tähtaeg` answered 200, closed its panel and
wrote a `MatterImportantDate` — and then the Matter showed no trace of it:

* `Teema käik` projects what has *happened*, so it skipped a deadline that had
  not arrived;
* the process strip had dropped `MatterImportantDate` altogether at
  docs/adr/0074 §12.1, with one exception added later for future transposition
  deadlines — and §12.1's own note had already predicted the rest of this
  defect in as many words: «a directive's single most consequential future date
  was recorded and then invisible until the day it passed»;
* `Minu asjad` shows a bounded horizon, so a date past it appeared nowhere in
  the working UI at all.

The only surface that could prove the save had worked was the technical audit
log — the page that tells its reader, in a line of its own, that the
substantive history is somewhere else. A save that looks successful and leaves
no record a lawyer can find invites the same deadline being recorded twice, or
the panel being stopped trusting (QA-001).

Both halves are asserted here: the roadmap draws it, and the chronology carries
it marked as something still ahead rather than merged into what happened.
"""

from __future__ import annotations

import datetime

import pytest

from app.intelligence.enums import FactStatus, ImportantDateKind
from app.intelligence.models import MatterImportantDate
from app.matters.process_timeline import process_steps
from app.matters.timeline import UPCOMING_DATE_LABEL, matter_timeline
from app.workflow.enums import DatePrecision

pytestmark = pytest.mark.django_db

TITLE = "Riigikogu majanduskomisjoni istung"


def _record(matter, *, when: datetime.date, kind=ImportantDateKind.OTHER, status=FactStatus.ACTIVE):
    return MatterImportantDate.objects.create(
        matter=matter,
        title=TITLE,
        kind=kind,
        date_value=when,
        period_end=when,
        date_precision=DatePrecision.EXACT.value,
        status=status,
    )


def _labels(matter, specialist) -> list[str]:
    return [step.label for step in process_steps(matter=matter, user=specialist)]


def _chronology(matter, specialist):
    items, _ = matter_timeline(matter=matter, user=specialist, limit=50)
    return items


def test_a_future_deadline_draws_an_event_marker(normal_matter, specialist):
    """On the rail as an event, not a stage (owner, 2026-10-19; docs/adr/0172 §6).

    docs/adr/0131 §13 had taken it off; the owner wants a file's dated events on
    its timeline, named by their own title and marked as what they are.
    """
    _record(normal_matter, when=datetime.date.today() + datetime.timedelta(days=60))

    marker = next(
        step for step in process_steps(matter=normal_matter, user=specialist) if step.label == TITLE
    )
    assert marker.is_event
    assert marker.event_label == "Eesolev tähtaeg"


def test_a_future_deadline_reads_in_the_chronology_as_still_ahead(normal_matter, specialist):
    when = datetime.date.today() + datetime.timedelta(days=60)
    _record(normal_matter, when=when)

    rows = [item for item in _chronology(normal_matter, specialist) if item.milestone]
    mine = [item for item in rows if item.milestone.what == TITLE]

    assert len(mine) == 1
    # Marked, so the chronology is not read as claiming this already happened.
    assert mine[0].milestone.sub == UPCOMING_DATE_LABEL


def test_a_deadline_beyond_every_horizon_is_still_on_its_own_matter(normal_matter, specialist):
    """The exact shape of the reported defect.

    `Minu asjad`'s horizon is bounded on purpose and stays bounded. What must
    not depend on it is whether the Matter itself shows the record.
    """
    _record(normal_matter, when=datetime.date.today() + datetime.timedelta(days=400))

    # On the chronology, marked as still ahead; the rail keeps the procedure
    # only since docs/adr/0131 §13.
    assert any(
        item.milestone and item.milestone.what == TITLE
        for item in _chronology(normal_matter, specialist)
    )


def test_a_passed_deadline_reads_as_history_and_stays_an_event(normal_matter, specialist):
    """A passed deadline is still a dated event on the file (docs/adr/0172 §6).

    It reads in the chronology as history, and on the rail as «Tähtaeg» rather
    than «Eesolev tähtaeg» — at its own day, behind today.
    """
    _record(normal_matter, when=datetime.date.today() - datetime.timedelta(days=10))

    marker = next(
        step for step in process_steps(matter=normal_matter, user=specialist) if step.label == TITLE
    )
    assert marker.is_event
    assert marker.event_label == "Tähtaeg"
    rows = [
        item
        for item in _chronology(normal_matter, specialist)
        if item.milestone and item.milestone.what == TITLE
    ]
    assert len(rows) == 1
    assert rows[0].milestone.sub != UPCOMING_DATE_LABEL


def test_a_cancelled_expectation_draws_no_column(normal_matter, specialist):
    """A called-off expectation is history, not somewhere the file is heading."""
    _record(
        normal_matter,
        when=datetime.date.today() + datetime.timedelta(days=60),
        status=FactStatus.CANCELLED,
    )

    assert TITLE not in _labels(normal_matter, specialist)


def test_the_transposition_deadline_keeps_its_short_name(normal_matter, specialist):
    """The one kind the vocabulary already names keeps that name.

    A 150 px column holds `Ülevõtmise tähtaeg`; it does not hold whatever
    sentence somebody typed into the title of a transposition record.
    """
    _record(
        normal_matter,
        when=datetime.date.today() + datetime.timedelta(days=60),
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE,
    )

    labels = _labels(normal_matter, specialist)
    assert "Ülevõtmise tähtaeg" in labels
    assert TITLE not in labels


def test_a_restricted_deadline_draws_a_column_for_a_reader_and_none_for_an_outsider(
    restricted_matter, reader, administrator
):
    """Scoped through the record's own `visible_to`, like every other source.

    Since docs/adr/0181 a READER reads a restricted Matter's dates; an
    administrator without a business role reads none.
    """
    _record(restricted_matter, when=datetime.date.today() + datetime.timedelta(days=60))

    assert TITLE in _labels(restricted_matter, reader)
    assert TITLE not in _labels(restricted_matter, administrator)
