"""A period has taken effect once it has ended, on every surface (docs/adr/0123).

docs/adr/0122 §2 audited and left out two readings that still compared a
period's **anchor** — its first day — with today:

* a `Jõustumine` recorded as a month, a quarter or a year read «Jõustus» in
  `Teema käik`, reached on the `Menetluse kulg` strip and happened on the rail
  from the day after the period *began*, while `MatterEffectiveDate.has_passed`
  and Statistika's «teemat jõustunud aktiga» said it had not taken effect until
  the period *ended*. In mid-October an «oktoober 2026» commencement read
  reached beside an «oktoober 2026» deadline reading ahead;
* a step added to the rail and dated as a period carried `aria-current="date"`
  on its anchor day — a day nobody named.

What is protected here, at `MONTH`, `QUARTER` and `YEAR` on the period's first
day, its middle, its last day and the day after (and the day before, which
draws nothing in the chronology):

* the four surfaces give **one** answer for each day — the chronology's tense,
  the strip's state, the rail's state and Statistika's count, with
  `has_passed` as the model's own statement of the rule;
* an exact day is unchanged on all of them: «Jõustub» / today on the day,
  «Jõustus» / reached from the next;
* no period is ever today, on the strip or on the rail;
* the rail places a commencement among what has happened only once it has
  ended, and the fill running into a column still ahead is never solid.
"""

from __future__ import annotations

import datetime
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import EffectiveDateKind, ImportantDateKind
from app.intelligence.models import MatterEffectiveDate
from app.intelligence.services import add_effective_date, add_important_date
from app.matters.legal_process import (
    KIND_MILESTONE,
    KIND_STEP,
    legal_process_rail,
    matter_rail,
    phase_context,
)
from app.matters.models import MatterTimelineStep
from app.matters.process_phases import PHASE_JOUSTUMINE
from app.matters.process_timeline import (
    EFFECTIVE_LABEL,
    SENT_LABEL,
    STATE_AHEAD,
    STATE_REACHED,
    STATE_TODAY,
    TRANSPOSITION_DEADLINE_LABEL,
    dated_state,
    process_steps,
)
from app.matters.services import set_timeline_steps
from app.matters.timeline import matter_timeline
from app.reporting import overview_strip
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.dates import format_at_precision, period_bounds
from app.workflow.enums import DatePrecision
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")

#: Statistika's caption for the figure this file cross-checks.
IN_FORCE_CAPTION = "teemat jõustunud aktiga"

#: One period per precision, each ending **before** 31 December where it can,
#: so the day after it is still in the same reporting year and Statistika's
#: «Aruandlus <aasta>» figure is asked the same question as the other surfaces.
#: A year cannot: the day after 2026 is in 2027, and that case says so below.
PERIODS = {
    DatePrecision.MONTH: date(2026, 10, 1),
    DatePrecision.QUARTER: date(2026, 7, 1),
    DatePrecision.YEAR: date(2026, 1, 1),
}

#: The days each period is read on.
MOMENTS = ("day before", "first day", "middle", "last day", "day after")


def _day(precision: str, moment: str) -> date:
    start, end = period_bounds(PERIODS[precision], precision)
    return {
        "day before": start - timedelta(days=1),
        "first day": start,
        "middle": start + timedelta(days=(end - start).days // 2),
        "last day": end,
        "day after": end + timedelta(days=1),
    }[moment]


@pytest.fixture
def pin_day(monkeypatch):
    """Pin the application's own day, which the rail and the facts read for themselves."""

    def pin(day: date) -> None:
        monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: day)

    return pin


@pytest.fixture
def send(capture_evidence):
    """A SENT `Submission` dated ``on`` — the `Koja arvamus` on the strip."""

    def _send(matter, on: date):
        version = capture_evidence(
            matter, b"%PDF-1.4 synthetic sent opinion", "arvamus.pdf", "application/pdf"
        )
        return factories.SubmissionFactory(
            matter=matter,
            status=SubmissionStatus.SENT,
            sent_at=datetime.datetime.combine(on, datetime.time(11, 0), tzinfo=TALLINN),
            final_version=version,
        )

    return _send


def _matter(owner, *, instruments: tuple[str, ...] = (), stage: str = "", **kwargs):
    matter = factories.MatterFactory(owner=owner, track="", **kwargs)
    if instruments:
        matter.legal_instruments.set(
            [LegalInstrumentType.objects.get(key=key) for key in instruments]
        )
    if stage:
        matter.stage = StageVocabulary.objects.get(key=stage)
        matter.save(update_fields=["stage", "updated_at"])
    return matter


def _commencement(matter, actor, precision: str, anchor: date) -> MatterEffectiveDate:
    start, end = period_bounds(anchor, precision)
    return add_effective_date(
        matter=matter,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=start,
        period_end=end,
        date_precision=precision,
        description="põhiosa",
        actor=actor,
    )


def _rail(matter, user):
    context = phase_context(matter=matter)
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user, context=context),
        milestones=process_steps(matter=matter, user=user),
    )


def _chronology_word(matter, user) -> str | None:
    """«Jõustub» / «Jõustus» in `Teema käik`, or ``None`` for no row at all."""
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    words = [
        item.milestone.what
        for item in items
        if item.item_type == "MatterEffectiveDate" and item.milestone is not None
    ]
    assert len(words) <= 1
    return words[0] if words else None


def _strip_state(matter, user) -> str:
    [column] = [
        step for step in process_steps(matter=matter, user=user) if step.label == EFFECTIVE_LABEL
    ]
    return column.state


def _rail_state(matter, user) -> str:
    [point] = [step for step in _rail(matter, user) if step.label == EFFECTIVE_LABEL]
    assert point.kind == KIND_MILESTONE
    return point.state


def _in_force_figure(viewer, day: date) -> int:
    blocks = overview_strip.rail(viewer, day, {})
    [figure] = [
        figure for block in blocks for figure in block.rows if figure.caption == IN_FORCE_CAPTION
    ]
    return figure.value


# ---------------------------------------------------------------------------
# 1 — one answer per day, on every surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("moment", MOMENTS)
@pytest.mark.parametrize("precision", list(PERIODS))
def test_a_period_commencement_takes_effect_once_the_period_has_ended_everywhere(
    specialist, pin_day, precision, moment
):
    """The chronology, the strip, the rail, Statistika and `has_passed` agree.

    No pattern, so the rail draws the commencement as a point of its own and
    its state is the rail's own reading rather than a phase's.
    """
    matter = _matter(specialist)
    record = _commencement(matter, specialist, precision, PERIODS[precision])
    day = _day(precision, moment)
    pin_day(day)

    ended = moment == "day after"
    assert record.has_passed(day) is ended

    # `Teema käik`: nothing before the period begins, «Jõustub» while it runs —
    # its last day included — and «Jõustus» only once it is over.
    expected_word = None if moment == "day before" else ("Jõustus" if ended else "Jõustub")
    assert _chronology_word(matter, specialist) == expected_word

    # The strip and the rail: ahead until the period has ended, never today.
    expected_state = STATE_REACHED if ended else STATE_AHEAD
    assert _strip_state(matter, specialist) == expected_state
    assert _rail_state(matter, specialist) == expected_state

    # Statistika counts this year's commencements that have taken effect. The
    # day after a year is the next reporting year, whose figure a 2026
    # commencement is not part of — the one place the answers may differ, and
    # it is the figure's year, not the rule.
    same_year = PERIODS[precision].year == day.year
    assert _in_force_figure(specialist, day) == (1 if ended and same_year else 0)


@pytest.mark.parametrize(
    ("offset", "word", "state", "passed"),
    [
        (-1, None, STATE_AHEAD, False),
        (0, "Jõustub", STATE_TODAY, False),
        (1, "Jõustus", STATE_REACHED, True),
    ],
    ids=["day before", "the day", "day after"],
)
def test_an_exact_commencement_reads_as_it_always_has(
    specialist, pin_day, offset, word, state, passed
):
    """The control: a day's reading is unchanged on every surface."""
    matter = _matter(specialist)
    on = date(2026, 10, 15)
    record = _commencement(matter, specialist, DatePrecision.EXACT, on)
    day = on + timedelta(days=offset)
    pin_day(day)

    assert record.has_passed(day) is passed
    assert _chronology_word(matter, specialist) == word
    assert _strip_state(matter, specialist) == state
    assert _rail_state(matter, specialist) == state
    assert _in_force_figure(specialist, day) == (1 if passed else 0)


def test_the_reported_contradiction_is_gone(specialist, pin_day):
    """In mid-October, «oktoober 2026» reads the same as a deadline and as a commencement.

    The case docs/adr/0122 §2 reported: on one strip, the commencement read
    reached and the deadline ahead.
    """
    matter = _matter(specialist)
    anchor = PERIODS[DatePrecision.MONTH]
    _commencement(matter, specialist, DatePrecision.MONTH, anchor)
    start, end = period_bounds(anchor, DatePrecision.MONTH)
    # A transposition deadline: the one `Oluline tähtaeg` the rail still draws
    # since docs/adr/0131 §13, so the two readings still meet on one strip.
    add_important_date(
        matter=matter,
        title="Ministeeriumi otsus",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.MONTH,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE,
        actor=specialist,
    )

    for day in (start, date(2026, 10, 15), end):
        pin_day(day)
        states = {step.label: step.state for step in process_steps(matter=matter, user=specialist)}
        assert states == {
            EFFECTIVE_LABEL: STATE_AHEAD,
            TRANSPOSITION_DEADLINE_LABEL: STATE_AHEAD,
        }, day

    # And once October is over the commencement is reached, while the deadline —
    # a past one — has left the strip for the chronology, as it always did.
    pin_day(end + timedelta(days=1))
    states = {step.label: step.state for step in process_steps(matter=matter, user=specialist)}
    assert states == {EFFECTIVE_LABEL: STATE_REACHED}


# ---------------------------------------------------------------------------
# 2 — a period is never today
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("precision", list(PERIODS))
def test_dated_state_never_calls_a_period_today(precision):
    """The one reading, at both ends of each period and read from either end."""
    start, end = period_bounds(PERIODS[precision], precision)
    for when in (start, end):
        assert dated_state(when, precision, start - timedelta(days=1)) == STATE_AHEAD
        assert dated_state(when, precision, start) == STATE_AHEAD
        assert dated_state(when, precision, end) == STATE_AHEAD
        assert dated_state(when, precision, end + timedelta(days=1)) == STATE_REACHED


def test_dated_state_reads_a_day_as_it_always_has():
    on = date(2026, 10, 15)
    for precision in (DatePrecision.EXACT, DatePrecision.INFERRED):
        assert dated_state(on, precision, on - timedelta(days=1)) == STATE_AHEAD
        assert dated_state(on, precision, on) == STATE_TODAY
        assert dated_state(on, precision, on + timedelta(days=1)) == STATE_REACHED


def test_a_deadline_period_is_not_today_on_its_last_day(specialist, pin_day):
    """The last day of «IV kvartal 2026» is not a day anybody named either."""
    matter = _matter(specialist)
    start, end = period_bounds(date(2026, 10, 1), DatePrecision.QUARTER)
    add_important_date(
        matter=matter,
        title="Ülevõtmise hindamine",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.QUARTER,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE,
        actor=specialist,
    )
    pin_day(end)

    [column] = process_steps(matter=matter, user=specialist)
    assert column.state == STATE_AHEAD


def _added(matter, actor, title: str, *, precision: str, anchor: date) -> MatterTimelineStep:
    set_timeline_steps(
        matter=matter,
        steps=[],
        added=[(None, title, anchor, precision, "", False)],
        actor=actor,
    )
    return MatterTimelineStep.objects.get(matter=matter, title=title)


@pytest.mark.parametrize("moment", MOMENTS)
@pytest.mark.parametrize("precision", list(PERIODS))
def test_an_added_step_dated_as_a_period_is_never_today(specialist, pin_day, precision, moment):
    """`+ Lisa samm` with a month, a quarter or a year reads as the strip's columns do."""
    matter = _matter(specialist)
    _added(matter, specialist, "Istung", precision=precision, anchor=PERIODS[precision])
    pin_day(_day(precision, moment))

    [step] = [step for step in _rail(matter, specialist) if step.kind == KIND_STEP]

    assert step.state == (STATE_REACHED if moment == "day after" else STATE_AHEAD)
    assert step.display_date == format_at_precision(PERIODS[precision], precision)


def test_an_added_step_dated_as_a_day_is_still_today_on_its_day(specialist, pin_day):
    matter = _matter(specialist)
    on = date(2026, 10, 15)
    _added(matter, specialist, "Istung", precision=DatePrecision.EXACT, anchor=on)
    pin_day(on)

    [step] = [step for step in _rail(matter, specialist) if step.kind == KIND_STEP]
    assert step.state == STATE_TODAY


def _rail_html(page: str) -> str:
    start = page.index('class="lprail')
    return page[start : page.index("</section>", start)]


@pytest.mark.parametrize(
    ("precision", "marked"),
    [
        (DatePrecision.MONTH, 0),
        (DatePrecision.QUARTER, 0),
        (DatePrecision.YEAR, 0),
        (DatePrecision.EXACT, 1),
    ],
)
def test_the_page_marks_no_period_as_today(signed_in, specialist, pin_day, precision, marked):
    """On the anchor day, the rendered rail carries `aria-current="date"` only for a day."""
    anchor = date(2026, 10, 1)
    matter = _matter(specialist)
    _added(matter, specialist, "Istung", precision=precision, anchor=anchor)
    pin_day(anchor)

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    rail = _rail_html(page.content.decode())

    assert "Istung" in rail
    assert rail.count('aria-current="date"') == marked


# ---------------------------------------------------------------------------
# 3 — where the rail puts it, and how the fill runs into it
# ---------------------------------------------------------------------------


def test_a_commencement_column_sits_at_the_end_of_its_period(specialist, pin_day):
    """Position and state are one date, as for a watched `Oluline tähtaeg`.

    A response deadline on 20 October comes before «oktoober 2026», which may
    commence as late as the 31st — and mid-period the fill into the
    commencement is part of a segment, not solid into a column still ahead.
    """
    today = date(2026, 10, 15)
    pin_day(today)
    matter = _matter(specialist, response_deadline=date(2026, 10, 20))
    _commencement(matter, specialist, DatePrecision.MONTH, date(2026, 10, 1))

    steps = process_steps(matter=matter, user=specialist)

    assert [step.label for step in steps] == ["Arvamuse tähtaeg", EFFECTIVE_LABEL]
    assert steps[1].sort_on == date(2026, 10, 31)
    assert [step.state for step in steps] == [STATE_AHEAD, STATE_AHEAD]
    assert steps[0].reach_percent == "0%"


@pytest.mark.parametrize("precision", list(PERIODS))
def test_the_fill_into_a_running_period_is_never_solid(specialist, pin_day, send, precision):
    """Reached opinion → running commencement: the connector is part of the way."""
    start, end = period_bounds(PERIODS[precision], precision)
    matter = _matter(specialist)
    send(matter, start - timedelta(days=10))
    _commencement(matter, specialist, precision, start)

    for day in (start, _day(precision, "middle")):
        pin_day(day)
        for steps in (process_steps(matter=matter, user=specialist), _rail(matter, specialist)):
            assert [step.label for step in steps] == [SENT_LABEL, EFFECTIVE_LABEL]
            assert steps[0].state == STATE_REACHED
            assert steps[1].state == STATE_AHEAD
            assert steps[0].reach_percent not in ("100%", "0%"), day

    pin_day(end + timedelta(days=1))
    steps = process_steps(matter=matter, user=specialist)
    assert [step.state for step in steps] == [STATE_REACHED, STATE_REACHED]


def test_the_rail_places_a_running_commencement_ahead_of_where_the_file_stands(specialist, pin_day):
    """The rail's placement reads the strip's state, not the anchor.

    A `Jõustumine` phase hidden before the commencement was recorded leaves the
    commencement a point of its own among the phases. Through October it is
    still to come, so it reads after `Kooskõlastusring`, where the file stands;
    once October is over it has happened, and reads before it. On the anchor it
    read as happened from 1 October — and a comparison of the column's own date
    would still say so on the 31st, the one day the end and today coincide.
    """
    pin_day(date(2026, 10, 15))
    matter = _matter(specialist, instruments=("seadus",), stage="consultation")
    set_timeline_steps(
        matter=matter, steps=[(PHASE_JOUSTUMINE, True, None, "EXACT")], actor=specialist
    )
    _commencement(matter, specialist, DatePrecision.MONTH, date(2026, 10, 1))

    def position() -> int:
        labels = [step.label for step in _rail(matter, specialist)]
        return labels.index(EFFECTIVE_LABEL) - labels.index("Kooskõlastusring")

    for day in (date(2026, 10, 1), date(2026, 10, 15), date(2026, 10, 31)):
        pin_day(day)
        assert position() == 1, day

    pin_day(date(2026, 11, 1))
    assert position() == -1


def test_a_folded_commencement_dates_its_phase_by_the_same_column(specialist, pin_day):
    """On a pattern with `Jõustumine`, the commencement is a line on that phase.

    It carries its period as written and dates the phase by the column the
    strip draws, so the phase and the strip cannot place it on two days.
    """
    pin_day(date(2026, 10, 15))
    matter = _matter(specialist, instruments=("seadus",), stage="consultation")
    _commencement(matter, specialist, DatePrecision.QUARTER, date(2026, 10, 1))

    [phase] = [step for step in _rail(matter, specialist) if step.key == PHASE_JOUSTUMINE]

    assert phase.notes == ("põhiosa IV kvartal 2026",)
    assert phase.display_date == "IV kvartal 2026"
    assert phase.sort_on == date(2026, 12, 31)
