"""The measured window opens in the Tallinn year, not the UTC one.

``measured_window`` names the first year structured submission data can speak
for, and it derives that year from the earliest recorded ``sent_at``. That
timestamp is aware and PostgreSQL hands it back in UTC, so reading ``.year``
off it directly reads the UTC calendar year rather than the business one.

For three hours every summer evening and two every winter evening those are the
same year — except on 31 December, where they are not. An opinion sent at 00:30
on 1 January 2027 in Tallinn is 22:30 on 31 December 2026 in UTC, and the window
opened in 2026: a year the department had not yet started, and one the trend
would then draw an empty bar for.

This is the seam #202 named and deliberately left for its own round. The fix is
the same idiom that round used — ``timezone.localdate``, which reads
``Europe/Tallinn`` from ``settings.TIME_ZONE`` rather than an offset written
into the call site. Nothing about how ``sent_at`` is stored, what a Submission
means, or what the measured window is has changed.

Every timestamp below crosses the boundary on purpose. A test written at midday
passes against the defect and proves nothing.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from django.utils import timezone

from app.reporting.selectors.submissions import measured_window
from app.search.indexing import suspend_indexing
from app.submissions.models import Submission

pytestmark = pytest.mark.django_db


#: 00:30 on 1 January 2027 in Tallinn, stated in UTC exactly as PostgreSQL holds
#: it. Europe/Tallinn is UTC+2 in January, so every correct reading of this
#: moment is 2027 and every UTC reading of it is 2026.
BOUNDARY_UTC = dt.datetime(2026, 12, 31, 22, 30, tzinfo=dt.UTC)
TALLINN_YEAR = 2027
UTC_YEAR = 2026

#: The same winter, nowhere near either midnight. UTC and Tallinn agree here,
#: which is what makes it the control.
ORDINARY_UTC = dt.datetime(2027, 2, 11, 10, 15, tzinfo=dt.UTC)


def test_the_fixture_really_crosses_the_year_boundary():
    """The premise, pinned: without this the boundary test proves nothing."""
    assert BOUNDARY_UTC.year == UTC_YEAR
    assert BOUNDARY_UTC.date() == dt.date(2026, 12, 31)
    assert timezone.localdate(BOUNDARY_UTC) == dt.date(2027, 1, 1)
    assert timezone.localdate(BOUNDARY_UTC).year == TALLINN_YEAR
    assert timezone.get_current_timezone_name() == "Europe/Tallinn"

    # And the control really is a day both timezones read the same way.
    assert ORDINARY_UTC.year == timezone.localdate(ORDINARY_UTC).year == 2027


def _only_sent_submission_at(world, moment: dt.datetime) -> None:
    """Leave exactly one sent Submission in the world, sent at ``moment``.

    The window is a property of the *earliest* record, so a test that added a
    submission to the fixture's own would be measuring the fixture's 2025 one
    instead. Through ``suspend_indexing`` like every other bulk writer: the
    per-row signals reindex the Matter as each Submission goes, and the
    projection then points at a row the cascade is in the middle of deleting
    (app/search/indexing.py).
    """
    keep = world.submissions["first"]
    with suspend_indexing():
        Submission.objects.exclude(pk=keep.pk).delete()
    # `.update()` rather than a save: this changes when the opinion was sent and
    # nothing else about it, and the evidence the SENT state requires is already
    # attached.
    Submission.objects.filter(pk=keep.pk).update(sent_at=moment)


def _context_in(reporting_context, viewer, year: int):
    """A context whose *today* is inside the year under test.

    ``measured_window`` closes at ``context.today.year``, and the fixture's day
    is this year. A window that opened after it closed would be an artefact of
    the test rather than of the data.
    """
    return dataclasses.replace(reporting_context(viewer, period="koik"), today=dt.date(year, 6, 15))


def test_nothing_recorded_has_no_measured_window(world, reporting_context):
    """The `None` that makes the whole metric decline rather than show a zero."""
    with suspend_indexing():
        Submission.objects.all().delete()

    assert measured_window(reporting_context(world.martin)) is None


def test_an_ordinary_send_time_opens_the_window_in_its_own_year(world, reporting_context):
    """The control: away from midnight, UTC and Tallinn agree, and so must this."""
    _only_sent_submission_at(world, ORDINARY_UTC)

    assert measured_window(_context_in(reporting_context, world.martin, 2027)) == (2027, 2027)


def test_a_new_year_send_opens_the_window_in_the_tallinn_year(world, reporting_context):
    """22:30 UTC on 31 December is half past midnight on 1 January in Tallinn.

    So the first measured year is 2027. Reading `sent_at.year` directly answers
    2026 — a year in which this department sent nothing, and for which the trend
    would then draw a bar that means "measured, and none".
    """
    _only_sent_submission_at(world, BOUNDARY_UTC)

    window = measured_window(_context_in(reporting_context, world.martin, 2027))

    assert window == (TALLINN_YEAR, 2027)
    assert window is not None and window[0] != UTC_YEAR


def test_the_trend_counts_the_boundary_send_under_the_year_the_window_opens(
    world, reporting_context
):
    """The window and the bars have to name the same year, or the bar is empty.

    ``submissions_sent_by_period`` builds its labels from ``measured_window``
    and its counts from a ``sent_at__year`` grouping. Those are two different
    readings of one timestamp, and if they disagreed at the boundary the trend
    would open on a 2027 bar counting zero and quietly lose the opinion.

    They agree because Django's ``__year`` lookup applies ``AT TIME ZONE`` under
    ``USE_TZ`` and reads ``Europe/Tallinn`` from settings — the same source
    ``timezone.localdate`` reads. This is the #202 audit's conclusion about the
    ORM transforms, pinned against the one timestamp that could falsify it.
    """
    from app.reporting import metric_catalogue as keys
    from app.reporting.services import compute

    _only_sent_submission_at(world, BOUNDARY_UTC)
    context = _context_in(reporting_context, world.martin, 2027)

    assert Submission.objects.filter(sent_at__year=TALLINN_YEAR).count() == 1
    assert Submission.objects.filter(sent_at__year=UTC_YEAR).count() == 0

    result = compute(keys.SUBMISSIONS_SENT_BY_PERIOD, context)
    composition = {segment.label: segment.value for segment in result.segments}

    assert composition == {"2027": 1}
