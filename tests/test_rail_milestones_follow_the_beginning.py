"""`Menetluse kulg` never draws an act on the file before the procedure began.

Found in production during the owner's UI acceptance, on a native file whose
`Hetkeseis` was still `Idee` and whose record below the rail read

    21.9.2026  Teema loodud
    22.9.2026  Arvamus välja
    23.9.2026  Arvamus välja

The rail above it read

    Koja arvamus 22.9 → Koja arvamus 23.9 → Algus → Arvamuse tähtaeg 30.9 → VTK → …

— two opinions drawn before the beginning of the procedure they answered.

**The mechanism.** `matter_rail` places a dated point that has already happened
among the phases up to the current one and, where none of them carries a date,
falls back to *just before the current phase*. On a file standing on its first
phase, «just before the current phase» is index 0: before `Algus`.

**The invariant these tests hold** (docs/adr/0100, amended 2026-09-27):

1. A phase node marks where its phase begins. A dated point reads after every
   phase it is known to follow and before every phase it is not.
2. The pattern's first phase is the beginning the procedure has (0100 §1), and
   every act on the file belongs to a procedure that has begun. So a dated point
   is known to follow an *undated* first phase and never reads before it.
3. No other undated phase dates anything. A point that has happened still reads
   immediately before the current phase — the file may have reached that phase
   after the act — unless the current phase *is* the beginning, where it reads
   inside it, after the node.
4. Dated points never overtake each other: two sent opinions read in the order
   they were sent.
5. An explicit roadmap date is still an anchor and still sorts by date, and a
   point still ahead of us is placed exactly as before.

Placement only. No node changes state because of any of this, so the late-entry
rule (`Teadmata`, never completed) is untouched.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from app.matters.legal_process import (
    KIND_MILESTONE,
    KIND_PHASE,
    STATE_CURRENT,
    STATE_RECORDED,
    STATE_UNKNOWN,
    legal_process_rail,
    matter_rail,
    phase_context,
)
from app.matters.process_phases import PATTERNS
from app.matters.process_timeline import SENT_LABEL, process_steps
from app.matters.services import set_timeline_steps
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import DatePrecision
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")

#: The day the owner read the rail.
TODAY = datetime.date(2026, 9, 27)
BEGUN = datetime.date(2026, 9, 21)
FIRST_SENT = datetime.date(2026, 9, 22)
SECOND_SENT = datetime.date(2026, 9, 23)
DEADLINE = datetime.date(2026, 9, 30)


@pytest.fixture(autouse=True)
def _the_owners_day(monkeypatch):
    """Pin the application's own day, which `matter_rail` reads for itself."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)


def _matter(owner, *, instruments: tuple[str, ...], stage: str, **kwargs):
    matter = factories.MatterFactory(owner=owner, track="", **kwargs)
    matter.legal_instruments.set([LegalInstrumentType.objects.get(key=key) for key in instruments])
    matter.stage = StageVocabulary.objects.get(key=stage)
    matter.save(update_fields=["stage", "updated_at"])
    return matter


@pytest.fixture
def send(capture_evidence):
    """A SENT `Submission` dated ``on``, with the final evidence the database insists on."""

    def _send(matter, on: datetime.date):
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


def _rail(matter, user):
    context = phase_context(matter=matter)
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user, context=context),
        milestones=process_steps(matter=matter, user=user, today=TODAY),
    )


def _opinion_indexes(steps) -> list[int]:
    return [index for index, step in enumerate(steps) if step.label == SENT_LABEL]


@pytest.fixture
def production_shape(specialist, send):
    """The file the owner found: a VTK and a bill, native, still on `Idee`.

    Created on the 21st, two opinions sent on the 22nd and the 23rd, an answer
    due on the 30th, and every later domestic phase ahead of it.
    """
    matter = _matter(
        specialist, instruments=("vtk", "seadus"), stage="idea", response_deadline=DEADLINE
    )
    type(matter).objects.filter(pk=matter.pk).update(
        created_at=datetime.datetime.combine(BEGUN, datetime.time(9, 0), tzinfo=TALLINN)
    )
    send(matter, FIRST_SENT)
    send(matter, SECOND_SENT)
    return matter


# ---------------------------------------------------------------------------
# A — the production shape
# ---------------------------------------------------------------------------


def test_the_production_shape_reads_from_the_beginning(production_shape, specialist):
    steps = _rail(production_shape, specialist)

    assert [(step.label, step.display_date) for step in steps] == [
        ("Algus", ""),
        ("Koja arvamus", "22.9.2026"),
        ("Koja arvamus", "23.9.2026"),
        ("Arvamuse tähtaeg", "30.9.2026"),
        ("VTK", ""),
        ("Kooskõlastusring", ""),
        ("Valitsuses", ""),
        ("Riigikogus", ""),
        ("Jõustumine", ""),
    ]


def test_no_opinion_reads_before_algus(production_shape, specialist):
    """The owner's sentence, as an assertion: never `Koja arvamus → Algus`."""
    steps = _rail(production_shape, specialist)
    labels = [step.label for step in steps]

    assert all(index > labels.index("Algus") for index in _opinion_indexes(steps)), labels


def test_the_backbone_runs_once_through_the_opinions_and_stops_at_today(
    production_shape, specialist
):
    """One continuous reached run: `Algus` → 22.9 → 23.9, then today's place.

    The connector leaving 23.9 is the segment today falls in — four of the seven
    days to the deadline — and nothing past the deadline is reached.
    """
    steps = _rail(production_shape, specialist)

    assert [step.reach for step in steps[:3]] == [1.0, 1.0, pytest.approx(4 / 7)]
    assert all(step.reach == 0.0 for step in steps[3:])
    assert steps[0].state == STATE_CURRENT


# ---------------------------------------------------------------------------
# B — sent opinions keep the order they were sent in
# ---------------------------------------------------------------------------


def test_opinions_recorded_out_of_order_still_read_in_date_order(specialist, send):
    """The later send registered first changes nothing: dates decide."""
    matter = _matter(specialist, instruments=("vtk", "seadus"), stage="idea")
    send(matter, SECOND_SENT)
    send(matter, FIRST_SENT)

    steps = _rail(matter, specialist)

    assert [steps[index].display_date for index in _opinion_indexes(steps)] == [
        "22.9.2026",
        "23.9.2026",
    ]


def test_a_dated_current_phase_does_not_reverse_the_opinions_after_it(specialist, send):
    """The same defect class on a later phase.

    With the current phase explicitly dated before both sends, each send sorts
    after it — and the second one used to be placed against the phase alone,
    in front of the first send it followed.
    """
    matter = _matter(specialist, instruments=("seadus",), stage="consultation")
    set_timeline_steps(
        matter=matter,
        steps=[("kooskolastus", False, BEGUN, DatePrecision.EXACT.value)],
        actor=specialist,
    )
    send(matter, FIRST_SENT)
    send(matter, SECOND_SENT)

    steps = _rail(matter, specialist)
    labels = [step.label for step in steps]
    opinions = _opinion_indexes(steps)

    assert [steps[index].display_date for index in opinions] == ["22.9.2026", "23.9.2026"]
    assert all(index > labels.index("Kooskõlastusring") for index in opinions), labels


# ---------------------------------------------------------------------------
# C — the deadline stays beside the current phase, after the sends
# ---------------------------------------------------------------------------


def test_the_deadline_follows_the_sends_and_precedes_every_later_phase(
    production_shape, specialist
):
    labels = [step.label for step in _rail(production_shape, specialist)]
    deadline = labels.index("Arvamuse tähtaeg")

    assert deadline > max(_opinion_indexes(_rail(production_shape, specialist)))
    for later in ("VTK", "Kooskõlastusring", "Valitsuses", "Riigikogus", "Jõustumine"):
        assert deadline < labels.index(later), labels


# ---------------------------------------------------------------------------
# The rule is about the beginning, on every pattern
# ---------------------------------------------------------------------------

_INSTRUMENT_FOR_PATTERN = {
    "domestic": ("seadus",),
    "vtk": ("vtk",),
    "maarus": ("maarus",),
    "koja-ettepanek": ("koja-ettepanek",),
    "eli-konsultatsioon": ("eli-konsultatsioon",),
    "direktiiv": ("direktiiv",),
    "el-maarus": ("el-maarus",),
}


@pytest.mark.parametrize("pattern_key", sorted(_INSTRUMENT_FOR_PATTERN))
def test_on_every_pattern_an_act_follows_the_first_phase(pattern_key, specialist, send):
    """`Idee` places a file on the first phase of every pattern that has one.

    An EU consultation Koda answered is the clearest case after `Algus`: the
    opinion *is* the answer to `ELi konsultatsioon` and cannot precede it.
    """
    matter = _matter(specialist, instruments=_INSTRUMENT_FOR_PATTERN[pattern_key], stage="idea")
    send(matter, FIRST_SENT)
    send(matter, SECOND_SENT)

    steps = _rail(matter, specialist)
    first = PATTERNS[pattern_key].nodes[0].phase_key

    assert steps[0].kind == KIND_PHASE and steps[0].key == first, [s.label for s in steps]
    assert steps[0].state == STATE_CURRENT
    assert [steps[index].display_date for index in _opinion_indexes(steps)] == [
        "22.9.2026",
        "23.9.2026",
    ]


# ---------------------------------------------------------------------------
# D — the late-entry rule is untouched
# ---------------------------------------------------------------------------


def test_a_late_entry_manufactures_no_earlier_phase(specialist, send):
    """First filed in the Riigikogu: every earlier phase stays `Teadmata`.

    And the opinion still reads before the current phase, as it always did —
    nothing says the file was already in the Riigikogu when it went out.
    """
    matter = _matter(specialist, instruments=("seadus",), stage="parliament")
    send(matter, FIRST_SENT)

    steps = _rail(matter, specialist)
    phases = {step.key: step.state for step in steps if step.kind == KIND_PHASE}
    labels = [step.label for step in steps]

    assert phases["algus"] == STATE_UNKNOWN
    assert phases["kooskolastus"] == STATE_UNKNOWN
    assert phases["valitsus"] == STATE_UNKNOWN
    assert phases["riigikogu"] == STATE_CURRENT
    assert STATE_RECORDED not in phases.values()
    assert labels.index("Algus") < labels.index(SENT_LABEL) < labels.index("Riigikogus")
    # Nothing before the opinion is reached: the run starts at the first thing
    # something proves, and the `Teadmata` phases before it stay muted.
    first_reached = labels.index(SENT_LABEL)
    assert all(step.reach == 0.0 for step in steps[:first_reached])


# ---------------------------------------------------------------------------
# E — a historical record is not forced to the right
# ---------------------------------------------------------------------------


def test_a_backdated_opinion_on_a_later_phase_keeps_its_place(specialist, send):
    """An opinion a year old on a file now on the consultation round.

    Nothing says the file was on the round when it went out, so it reads before
    the current phase — after the beginning, and not dragged past `Praegu`.
    """
    matter = _matter(specialist, instruments=("seadus",), stage="consultation")
    send(matter, datetime.date(2025, 9, 22))

    labels = [step.label for step in _rail(matter, specialist)]

    assert labels.index("Algus") < labels.index(SENT_LABEL) < labels.index("Kooskõlastusring")


def test_a_backdated_opinion_still_follows_the_beginning(specialist, send):
    """Older than the file itself, and still an act inside the procedure."""
    matter = _matter(specialist, instruments=("seadus",), stage="idea")
    send(matter, datetime.date(2025, 9, 22))

    labels = [step.label for step in _rail(matter, specialist)]

    assert labels.index("Algus") < labels.index(SENT_LABEL)


# ---------------------------------------------------------------------------
# F — explicit roadmap dates still decide by date
# ---------------------------------------------------------------------------


def test_an_explicit_beginning_before_the_sends_reads_before_them(specialist, send):
    matter = _matter(specialist, instruments=("seadus",), stage="idea")
    set_timeline_steps(
        matter=matter,
        steps=[("algus", False, BEGUN, DatePrecision.EXACT.value)],
        actor=specialist,
    )
    send(matter, FIRST_SENT)

    labels = [step.label for step in _rail(matter, specialist)]

    assert labels.index("Algus") < labels.index(SENT_LABEL)


def test_an_explicit_beginning_after_a_send_is_believed(specialist, send):
    """The lawyer's own roadmap date is the one thing that can say otherwise.

    `Algus 25.9` is a statement that the procedure began on the 25th, and a
    point dated the 22nd is then earlier than it. The rail follows what a person
    wrote down (docs/adr/0099 §4, docs/adr/0100 §5).
    """
    matter = _matter(specialist, instruments=("seadus",), stage="idea")
    set_timeline_steps(
        matter=matter,
        steps=[("algus", False, datetime.date(2026, 9, 25), DatePrecision.EXACT.value)],
        actor=specialist,
    )
    send(matter, FIRST_SENT)

    labels = [step.label for step in _rail(matter, specialist)]

    assert labels.index(SENT_LABEL) < labels.index("Algus")


# ---------------------------------------------------------------------------
# G — a folded canonical fact stays folded
# ---------------------------------------------------------------------------


def test_a_commencement_stays_on_its_phase_at_the_end_of_the_road(production_shape, specialist):
    factories.EffectiveDateFactory(
        matter=production_shape,
        date_value=datetime.date(2027, 1, 1),
        period_end=datetime.date(2027, 1, 1),
    )

    steps = _rail(production_shape, specialist)
    labels = [step.label for step in steps]

    assert labels.count("Jõustumine") == 1
    assert labels[-1] == "Jõustumine"
    assert steps[-1].kind == KIND_PHASE and steps[-1].notes == ("põhiosa 1.1.2027",)
    assert not any(step.kind == KIND_MILESTONE and step.label == "Jõustumine" for step in steps)
    assert labels.index("Algus") < min(_opinion_indexes(steps))


# ---------------------------------------------------------------------------
# Still a projection
# ---------------------------------------------------------------------------


def test_placing_the_sends_writes_nothing(production_shape, specialist):
    from app.audit.models import ChangeEvent
    from app.matters.models import MatterTimelineStep
    from app.submissions.models import Submission

    def snapshot():
        production_shape.refresh_from_db()
        return (
            ChangeEvent.objects.filter(matter=production_shape).count(),
            MatterTimelineStep.objects.filter(matter=production_shape).count(),
            sorted(
                Submission.objects.filter(matter=production_shape).values_list("sent_at", flat=True)
            ),
            production_shape.stage_id,
            production_shape.response_deadline,
        )

    before = snapshot()
    _rail(production_shape, specialist)
    _rail(production_shape, specialist)
    assert snapshot() == before
