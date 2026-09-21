"""The owner's follow-up to the one-rail round: where a deadline actually reads.

One rail was right and one thing on it was not. A future dated point with no
phase of its own fell to the far end of the whole pattern, so a file out for
consultation drew

    Kooskõlastusring → Valitsuses → Riigikogus → Jõustumine → Arvamuse tähtaeg

and this office's own deadline read as the last thing that happens to the bill.
It is not: it is an obligation falling due *during* the round the file is on.

What is being protected here:

* an **unanchored** future point reads beside the phase the file is on;
* a point whose kind names a phase goes on reading with that phase — a
  commencement is still the `Jõustumine` end of the road, however near;
* an explicitly dated future phase is a real anchor and still orders against it,
  while an undated one anchors nothing;
* nothing that has already happened moves.

`tests/test_teema_cleanup_round.py` holds the round this follows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.matters.legal_process import (
    KIND_PHASE,
    legal_process_rail,
    matter_rail,
    phase_context,
)
from app.matters.process_phases import PHASE_JOUSTUMINE, PHASE_KOOSKOLASTUS, PHASE_VALITSUS
from app.matters.process_timeline import process_steps
from app.matters.services import change_stage, set_timeline_steps
from app.matters.workspace import add_procedural_development
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _matter(owner, *, instruments: tuple[str, ...] = (), **kwargs):
    matter = factories.MatterFactory(owner=owner, track="", **kwargs)
    if instruments:
        matter.legal_instruments.set(
            [LegalInstrumentType.objects.get(key=key) for key in instruments]
        )
    return matter


def _rail(matter, user):
    context = phase_context(matter=matter)
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user, context=context),
        milestones=process_steps(matter=matter, user=user),
    )


def _labels(matter, user) -> list[str]:
    return [step.label for step in _rail(matter, user)]


def _consulting(specialist, **kwargs):
    """A domestic bill out for consultation — the ordinary shape of the defect."""
    matter = _matter(specialist, instruments=("seadus",), **kwargs)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    return matter


def _current(steps) -> int:
    return next(index for index, step in enumerate(steps) if step.state == "current")


# ---------------------------------------------------------------------------
# The correction
# ---------------------------------------------------------------------------


def test_an_unanchored_deadline_reads_immediately_after_the_current_phase(specialist):
    """The whole round, in one assertion.

    `Arvamuse tähtaeg` is not a step of the ministry's procedure. It is what
    this office owes, during whichever round the file happens to be on — so it
    belongs beside that round and not past three phases nobody has reached.
    """
    matter = _consulting(specialist, response_deadline=timezone.localdate() + timedelta(days=30))

    steps = _rail(matter, specialist)
    labels = [step.label for step in steps]
    current = _current(steps)

    assert labels[current] == "Kooskõlastusring"
    assert labels[current + 1] == "Arvamuse tähtaeg"
    # And the pattern still reads in its own order behind it.
    assert labels[current + 2 :] == ["Valitsuses", "Riigikogus", "Jõustumine"]


def test_the_speculative_phases_no_longer_push_the_deadline_to_the_end(specialist):
    """The literal complaint: it must not be the last thing on the rail.

    A year out or three weeks out — the distance changes nothing, because the
    phases it was being ordered against carry no dates at all.
    """
    matter = _consulting(specialist, response_deadline=timezone.localdate() + timedelta(days=400))

    labels = _labels(matter, specialist)

    assert labels[-1] == "Jõustumine", "the road still ends where the procedure does"
    for ahead in ("Valitsuses", "Riigikogus", "Jõustumine"):
        assert labels.index("Arvamuse tähtaeg") < labels.index(ahead)


def test_an_undated_future_phase_anchors_nothing(specialist):
    """A phase nobody has dated makes no claim about when anything happens.

    Which is why the deadline may not be ordered by it: on the ordinary file
    *no* phase carries a date, so ordering against the pattern's shape is
    ordering against nothing at all.
    """
    matter = _consulting(specialist, response_deadline=timezone.localdate() + timedelta(days=5))

    steps = _rail(matter, specialist)
    ahead = [step for step in steps if step.kind == KIND_PHASE and step.state == "possible"]

    assert ahead, "the pattern still draws what may come"
    assert all(step.sort_on is None for step in ahead)
    assert [step.label for step in steps].index("Arvamuse tähtaeg") < steps.index(ahead[0])


def test_a_feedback_deadline_reads_beside_the_current_phase_too(specialist):
    """`Tagasiside tähtaeg` is the same kind of fact and takes the same rule.

    What Koda asked its members to answer by is not a step of the ministry's
    procedure either. Two kinds share the rule, which is why it is written on
    the *absence* of a phase rather than as a special case for one label.
    """
    from app.matters.enums import EngagementKind
    from app.matters.services import add_engagement

    matter = _consulting(specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        feedback_deadline=timezone.localdate() + timedelta(days=12),
        actor=specialist,
    )

    steps = _rail(matter, specialist)
    labels = [step.label for step in steps]
    current = _current(steps)

    assert labels[current + 1] == "Tagasiside tähtaeg"


# ---------------------------------------------------------------------------
# … and the nuance it must not flatten
# ---------------------------------------------------------------------------


def test_an_explicitly_dated_future_phase_still_orders_the_deadline(specialist):
    """Somebody wrote a date on `Valitsuses`, so it is a real anchor.

    A deadline falling after it reads after it, exactly as two dated points do.
    """
    today = timezone.localdate()
    matter = _consulting(specialist, response_deadline=today + timedelta(days=30))
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VALITSUS, False, today + timedelta(days=15), "EXACT")],
        actor=specialist,
    )

    labels = _labels(matter, specialist)

    assert labels.index("Valitsuses") < labels.index("Arvamuse tähtaeg")
    assert labels.index("Arvamuse tähtaeg") < labels.index("Riigikogus")


def test_a_dated_future_phase_later_than_the_deadline_reads_after_it(specialist):
    """The other side of the same anchor, so the rule is chronology and not a side."""
    today = timezone.localdate()
    matter = _consulting(specialist, response_deadline=today + timedelta(days=30))
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VALITSUS, False, today + timedelta(days=45), "EXACT")],
        actor=specialist,
    )

    labels = _labels(matter, specialist)

    assert labels.index("Arvamuse tähtaeg") < labels.index("Valitsuses")


def test_a_future_commencement_still_reads_at_the_joustumine_end(specialist):
    """A dated point whose *kind* names a phase keeps reading with that phase.

    `Jõustumine` is the one milestone that is also a phase, and it is why the
    rule is written on the milestone's stable kind rather than on a list of
    labels: nothing about the words could tell the commencement record apart
    from the node it belongs to.
    """
    today = timezone.localdate()
    matter = _consulting(specialist)
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=60),
        period_end=today + timedelta(days=60),
    )

    steps = _rail(matter, specialist)
    labels = [step.label for step in steps]
    current = _current(steps)
    node = next(
        index
        for index, step in enumerate(steps)
        if step.kind == KIND_PHASE and step.key == PHASE_JOUSTUMINE
    )

    assert labels[current] == "Kooskõlastusring"
    # **One `Jõustumine`, not two.** docs/adr/0100 §3 folds a canonical dated
    # fact onto the phase whose name it carries: the rail used to draw the
    # commencement as its own column beside the node, so a file read
    # `Jõustumine · Jõustumine 1.1.2027` — two adjacent columns with one name,
    # both saying `Tulevikus` — and a file with two commencement dates drew
    # three (QA-005).
    assert labels.count("Jõustumine") == 1
    # What this test is actually for is untouched: the fact reads at the end of
    # the road, not beside the round the file is on.
    assert node > current + 1, "a commencement is not an obligation falling due now"
    assert steps[node].notes, "the commencement reads as text on the node it belongs to"


def test_a_past_dated_point_cannot_drag_a_commencement_in_front_of_the_phases(specialist):
    """The case the seeded open Matter actually has, and the defect it exposed.

    `Menetluse areng` filed in `Kooskõlastusring` today dates the current phase,
    which pushes `Alustatud` past it — so the file has a *past* dated point
    sitting between the current phase and everything ahead. A commencement in
    2027 was then placed relative to that, because the backward scan takes the
    last dated step it is not earlier than and `Alustatud` was one. The result
    on the rail was a date two years out drawn before `Valitsuses` and
    `Riigikogus`: exactly the reading this round exists to remove, on the other
    kind of milestone.

    So an anchored point is scanned from **its own phase** rather than from the
    current one.
    """
    today = timezone.localdate()
    matter = _matter(specialist, instruments=("seadus",))
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Eelnõu kooskõlastusringile",
        occurred_on=today,
        process_phase=PHASE_KOOSKOLASTUS,
        stage=_stage("consultation"),
    )
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=370),
        period_end=today + timedelta(days=370),
    )

    labels = _labels(matter, specialist)

    # **`Alustatud` is gone** (docs/adr/0100 §1) and so is the shape this test
    # was written against: it was `Matter.created_at`, so a file entered today
    # put a *past* dated point between the current phase and everything ahead,
    # and the backward scan then placed a 2027 commencement relative to it. The
    # pattern's own first phase is the beginning the procedure has.
    assert "Alustatud" not in labels
    assert labels.index("Algus") < labels.index("Valitsuses"), labels
    # And the commencement is still at the end of the road rather than in front
    # of the phases, which is what the round was for.
    assert labels.index("Riigikogus") < labels.index("Jõustumine"), labels


def test_a_commencement_and_a_deadline_do_not_collapse_onto_each_other(specialist):
    """Both future, both drawn, and each where its own kind says.

    The file that proves the two halves of the rule are one rule and not two
    passes over the same list.
    """
    today = timezone.localdate()
    matter = _consulting(specialist, response_deadline=today + timedelta(days=20))
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=200),
        period_end=today + timedelta(days=200),
    )

    steps = _rail(matter, specialist)
    labels = [step.label for step in steps]
    current = _current(steps)

    assert labels[current + 1] == "Arvamuse tähtaeg"
    assert labels.index("Riigikogus") < labels.index("Jõustumine")
    # One node carrying the fact, since docs/adr/0100 §3. It used to be two
    # adjacent columns with one name.
    assert labels.count("Jõustumine") == 1
    node = next(step for step in steps if step.kind == KIND_PHASE and step.key == PHASE_JOUSTUMINE)
    assert node.notes, "the commencement reads as text on the node it belongs to"


def test_a_past_deadline_is_untouched_by_the_rule(specialist):
    """Only the future branch moved. What has already happened reads where it did."""
    matter = _consulting(specialist, response_deadline=timezone.localdate() - timedelta(days=10))

    steps = _rail(matter, specialist)
    labels = [step.label for step in steps]
    current = _current(steps)

    assert labels.index("Arvamuse tähtaeg") <= current
    # `Alustatud` used to be the other past point here and is retired
    # (docs/adr/0100 §1). The pattern's first phase is what is behind the file
    # now, and it is behind it for the same reason.
    assert labels.index("Algus") <= current


def test_the_rule_writes_nothing(specialist):
    """A projection, still. Drawing the rail moves no record and no column."""
    from app.audit.models import ChangeEvent
    from app.matters.models import MatterTimelineStep

    matter = _consulting(specialist, response_deadline=timezone.localdate() + timedelta(days=30))
    before = (
        ChangeEvent.objects.filter(matter=matter).count(),
        MatterTimelineStep.objects.filter(matter=matter).count(),
        matter.stage_id,
        matter.response_deadline,
    )

    _rail(matter, specialist)
    _rail(matter, specialist)
    matter.refresh_from_db()

    assert before == (
        ChangeEvent.objects.filter(matter=matter).count(),
        MatterTimelineStep.objects.filter(matter=matter).count(),
        matter.stage_id,
        matter.response_deadline,
    )
