"""`Menetluse kulg` answers where the file is going, and only that.

The exploratory round found eight defects on this surface and they were one
defect: the rail carried the roadmap *and* borrowed history to date it, while
`Teema käik` already answered historical repetition properly — a section per
occurrence, in date order, with its own heading.

docs/adr/0100 states the division. These hold it:

* nothing manufactures a start from `created_at` (OWNER-03, QA-013);
* a phase node is dated by the roadmap and never by the first step filed under
  it (QA-006, QA-007);
* a canonical dated fact is folded onto its phase rather than drawn beside it
  (QA-005);
* the current phase and any phase holding such a fact cannot be taken off
  (QA-009, QA-008);
* explicit roadmap dates run in the procedure's order (QA-010);
* a closed file's own deadline stops counting down (QA-011).

What the rail must go on doing is asserted too, because a correction round is
where a rule gets lost by accident: the late-entry states, and the unanchored
deadline reading beside the current phase rather than after every speculative
one (docs/adr/0099).
"""

from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from app.intelligence.models import MatterEffectiveDate
from app.matters.forms import TimelineStepsForm
from app.matters.legal_process import anchored_phase_keys, legal_process_rail, matter_rail
from app.matters.process_timeline import process_steps
from app.matters.selectors import response_deadline_of
from app.matters.services import set_timeline_steps
from app.workflow.enums import DatePrecision, Disposition

pytestmark = pytest.mark.django_db


@pytest.fixture
def matter_with_pattern(db, specialist):
    """A `Seadus` out for consultation — the ordinary domestic file.

    A rail exists only where an `Õigusakt` chooses a pattern *and* something
    places the file on it, so both are set here; `legal_process_rail` returns
    `None` otherwise and the whole section is unrendered.
    """
    from app.taxonomy.models import LegalInstrumentType
    from app.workflow.models import StageVocabulary
    from tests import factories

    matter = factories.MatterFactory(owner=specialist, track="")
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key="seadus")])
    matter.stage = StageVocabulary.objects.get(key="consultation")
    matter.save(update_fields=["stage", "updated_at"])
    return matter


def _rail(matter, user):
    rail = legal_process_rail(matter=matter, user=user)
    steps = process_steps(matter=matter, user=user)
    return matter_rail(matter=matter, user=user, rail=rail, milestones=steps)


def _labels(matter, user) -> list[str]:
    return [step.label for step in _rail(matter, user)]


# ---------------------------------------------------------------------------
# Nothing is manufactured from a database timestamp
# ---------------------------------------------------------------------------


def test_no_step_is_invented_from_when_the_record_was_written(normal_matter, specialist):
    """OWNER-03, QA-013.

    `Alustatud` was `Matter.created_at`. A file entered a month after it
    arrived drew `Saabus 1.9` in its header and `Alustatud 21.9` on its rail,
    and a backdated opinion drew `Koja arvamus 20.9` before it.
    """
    assert "Alustatud" not in [
        step.label for step in process_steps(matter=normal_matter, user=specialist)
    ]


def test_the_matters_own_dates_are_untouched(normal_matter, specialist):
    """Removing the milestone removed a drawing, not a fact.

    `Saabus` is still the Matter's, still stated in the header, and still
    exactly what somebody entered.
    """
    normal_matter.received_date = datetime.date(2026, 9, 1)
    normal_matter.save(update_fields=["received_date", "updated_at"])
    normal_matter.refresh_from_db()

    assert normal_matter.received_date == datetime.date(2026, 9, 1)


# ---------------------------------------------------------------------------
# The roadmap dates the phases; history does not
# ---------------------------------------------------------------------------


def test_a_phase_is_not_dated_by_the_step_filed_under_it(matter_with_pattern, specialist):
    """QA-007 — the rail and the history contradicting each other.

    A file consulted twice read `Kooskõlastusring 2.4.2026` on the rail, from
    the first occurrence, an inch above a `Teema käik` whose current section
    said `alates 20.08.2026`.
    """
    from app.matters.workspace import add_procedural_development

    add_procedural_development(
        matter=matter_with_pattern,
        author=specialist,
        title="Eelnõu saadeti kooskõlastusringile",
        occurred_on=datetime.date(2026, 4, 2),
        occurred_on_precision=DatePrecision.EXACT.value,
        process_phase="kooskolastus",
    )

    phase = next(
        step
        for step in _rail(matter_with_pattern, specialist)
        if step.is_phase and step.key == "kooskolastus"
    )
    assert phase.display_date == ""


def test_an_explicit_roadmap_date_does_show(matter_with_pattern, specialist):
    set_timeline_steps(
        matter=matter_with_pattern,
        steps=[("valitsus", False, datetime.date(2026, 10, 15), DatePrecision.EXACT.value)],
        actor=specialist,
    )

    phase = next(
        step
        for step in _rail(matter_with_pattern, specialist)
        if step.is_phase and step.key == "valitsus"
    )
    assert phase.display_date == "15.10.2026"


# ---------------------------------------------------------------------------
# A dated fact belongs to its phase
# ---------------------------------------------------------------------------


def _commencement(matter, specialist, when, description):
    return MatterEffectiveDate.objects.create(
        matter=matter,
        description=description,
        date_value=when,
        period_end=when,
        date_precision=DatePrecision.EXACT.value,
        created_by=specialist,
    )


def test_a_commencement_does_not_draw_a_second_joustumine(matter_with_pattern, specialist):
    """QA-005 — two adjacent columns with one name."""
    _commencement(matter_with_pattern, specialist, datetime.date(2027, 1, 1), "põhiosa")

    labels = _labels(matter_with_pattern, specialist)
    assert labels.count("Jõustumine") == 1


def test_every_commencement_reads_as_text_on_that_one_node(matter_with_pattern, specialist):
    """And not only in a `title`, which a touch screen never delivers."""
    _commencement(matter_with_pattern, specialist, datetime.date(2027, 9, 27), "põhiosa")
    _commencement(matter_with_pattern, specialist, datetime.date(2028, 1, 1), "osad sätted")

    node = next(
        step
        for step in _rail(matter_with_pattern, specialist)
        if step.is_phase and step.key == "joustumine"
    )
    assert node.notes == ("põhiosa 27.9.2027", "osad sätted 1.1.2028")


# ---------------------------------------------------------------------------
# Two phases cannot be taken off
# ---------------------------------------------------------------------------


def _steps_form(matter, user, data=None):
    from app.matters import legal_process
    from app.matters.models import MatterTimelineStep

    phases = legal_process.phase_context(matter=matter)
    rows = {
        row.phase_key: row
        for row in MatterTimelineStep.objects.filter(matter=matter).visible_to(user)
    }
    return TimelineStepsForm(
        data,
        phases=phases,
        rows=rows,
        current_phase=phases.current_phase,
        anchored=legal_process.anchored_phase_keys(matter=matter, user=user),
    )


def test_the_current_phase_cannot_be_hidden(matter_with_pattern, specialist):
    """QA-009 — the header naming a phase the rail no longer drew."""
    form = _steps_form(matter_with_pattern, specialist)
    current = next(row for row in form.rows if row["protected"])

    assert current["key"] == "kooskolastus"
    assert current["shown_field"].field.disabled is True


def test_a_crafted_post_cannot_hide_the_current_phase(matter_with_pattern, specialist):
    """The control not being rendered is never how a rule is kept here."""
    form = _steps_form(matter_with_pattern, specialist, {"algus__shown": "on"})
    assert form.is_valid(), form.errors

    hidden = {key for key, is_hidden, _when, _precision in form.steps() if is_hidden}
    assert "kooskolastus" not in hidden


def test_a_phase_holding_a_commencement_cannot_be_hidden(matter_with_pattern, specialist):
    """QA-008 — the fact stranded and re-sorted in front of the government."""
    _commencement(matter_with_pattern, specialist, datetime.date(2027, 1, 1), "põhiosa")

    assert anchored_phase_keys(matter=matter_with_pattern, user=specialist) == frozenset(
        {"joustumine"}
    )
    form = _steps_form(matter_with_pattern, specialist)
    node = next(row for row in form.rows if row["key"] == "joustumine")
    assert node["protected"]


def test_an_ordinary_future_phase_is_still_removable(matter_with_pattern, specialist):
    """The tailoring docs/adr/0099 introduced is untouched."""
    form = _steps_form(matter_with_pattern, specialist)
    node = next(row for row in form.rows if row["key"] == "riigikogu")

    assert not node["protected"]
    assert node["shown_field"].field.disabled is False


# ---------------------------------------------------------------------------
# Roadmap dates run in the procedure's order
# ---------------------------------------------------------------------------


def test_an_earlier_phase_dated_after_a_later_one_is_refused(matter_with_pattern, specialist):
    """QA-010 — a left-to-right time rail reading December before September."""
    form = _steps_form(
        matter_with_pattern,
        specialist,
        {
            "algus__shown": "on",
            "kooskolastus__date": "01.12.2026",
            "valitsus__shown": "on",
            "valitsus__date": "25.09.2026",
            "riigikogu__shown": "on",
            "joustumine__shown": "on",
        },
    )

    assert not form.is_valid()
    assert "valitsus__date" in form.errors


def test_dates_in_the_procedures_own_order_are_accepted(matter_with_pattern, specialist):
    form = _steps_form(
        matter_with_pattern,
        specialist,
        {
            "algus__shown": "on",
            "kooskolastus__date": "25.09.2026",
            "valitsus__shown": "on",
            "valitsus__date": "01.12.2026",
            "riigikogu__shown": "on",
            "joustumine__shown": "on",
        },
    )

    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# What must not regress
# ---------------------------------------------------------------------------


def test_an_unanchored_deadline_still_reads_beside_the_current_phase(
    matter_with_pattern, specialist
):
    """docs/adr/0099's rule, which this round must not undo.

    The deadline falls due during the round the file is on; drawing it after
    every speculative future phase said the opposite.
    """
    matter_with_pattern.response_deadline = timezone.localdate() + datetime.timedelta(days=9)
    matter_with_pattern.save(update_fields=["response_deadline", "updated_at"])

    labels = _labels(matter_with_pattern, specialist)
    assert labels.index("Arvamuse tähtaeg") < labels.index("Riigikogus")


def _closed(matter, specialist, disposition):
    from app.matters.services import close_matter

    matter.response_deadline = timezone.localdate() + datetime.timedelta(days=24)
    matter.save(update_fields=["response_deadline", "updated_at"])
    return close_matter(matter=matter, disposition=disposition, actor=specialist)


def test_a_closed_matters_deadline_stops_counting_down(matter_with_pattern, specialist):
    """QA-011 — `· 24 p` in the header of an archived file."""
    closed = _closed(matter_with_pattern, specialist, Disposition.COMPLETED)

    deadline = response_deadline_of(closed, specialist)
    assert deadline is not None
    assert deadline.is_active is False
    # The date itself is part of the record and stays.
    assert deadline.display


def test_standing_down_stops_it_too(matter_with_pattern, specialist):
    """`Rohkem ei tegele` is this office's decision, and the clock is ours.

    It reaches the same rule by being a *disposition*: `Disposition` answers
    why a file is closed, and `matters_closure_fields_consistent` refuses one
    on an open row — so there is no second condition to write and none to test
    for separately. This asserts the shape rather than a clause.
    """
    closed = _closed(matter_with_pattern, specialist, Disposition.MONITORING_STOPPED)

    deadline = response_deadline_of(closed, specialist)
    assert deadline is not None
    assert deadline.is_active is False


def test_an_open_matters_deadline_still_counts(matter_with_pattern, specialist):
    matter_with_pattern.response_deadline = timezone.localdate() + datetime.timedelta(days=9)
    matter_with_pattern.save(update_fields=["response_deadline", "updated_at"])

    deadline = response_deadline_of(matter_with_pattern, specialist)
    assert deadline is not None
    assert deadline.is_active is True
    assert deadline.days_remaining == 9
