"""The owner's Teema cleanup round: one rail, and it is editable.

Four layout corrections and one product change, and the product change is the
reason the round exists: `Menetluse kulg` drew a procedure it had read off the
`Õigusakt` and a lawyer could do nothing about it. Now they can take off the
phases this file will never see and date the ones they know.

What is being protected:

* the rail is **one** row, carrying phases and the dates the file holds;
* the explanatory labels are gone and nothing that was said only in words is
  now said only in colour;
* a phase the file has **recorded** is dated by that record, never by a second
  value typed over it;
* hiding stores a preference and nothing else — no `Hetkeseis`, no event, no
  work, and no row at all for the ordinary answer.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.legal_process import (
    KIND_MILESTONE,
    KIND_PHASE,
    legal_process_rail,
    matter_rail,
    phase_context,
)
from app.matters.models import MatterTimelineStep
from app.matters.process_phases import (
    PHASE_JOUSTUMINE,
    PHASE_KOOSKOLASTUS,
    PHASE_VTK,
)
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


def _page(signed_in, matter) -> str:
    return signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()


def _section(page: str) -> str:
    return page[page.index('class="lprail"') : page.index('id="ajajoon"')]


# ---------------------------------------------------------------------------
# A + B — the actions moved
# ---------------------------------------------------------------------------


def test_kustuta_sits_beside_muuda_in_the_header(signed_in, specialist):
    """Both answer «this record is wrong», so they are one errand.

    `Kustuta` sat at the foot of the page in a section of its own, which put the
    one irreversible action furthest from the one a person goes looking for when
    they realise a Matter is filed wrongly.
    """
    matter = _matter(specialist)
    page = _page(signed_in, matter)
    head = page[: page.index('id="lisa-teemale"')]

    assert "Muuda teemat" in head
    assert "matterhead__delete" in head
    # Still a link to the confirmation page, never an inline button: a deletion
    # has to be read before it is agreed to (docs/adr/0096 §4).
    assert reverse("matters:matter_delete", kwargs={"pk": matter.pk}) in head
    assert "hx-confirm" not in head


def test_lopeta_is_a_peer_chip_that_closes_whatever_was_open(signed_in, specialist):
    """§B. Back in the row, and in the row's own exclusive group.

    A second radio group would mean two panels standing open at once, and the
    row's whole contract is that it is a choice until one is picked
    (docs/adr/0075 §2).
    """
    matter = _matter(specialist)
    page = _page(signed_in, matter)
    zone = page[page.index('id="lisa-teemale"') : page.index('id="menetluse-kulg-heading"')]

    assert "+ Lõpeta teema" in zone
    assert 'name="lisa-valik" id="teema-lopeta-valik"' in zone
    # Visibly last, which is what keeps closure from looking like capture.
    assert "disclosure-chip--last" in zone


def test_the_teema_toimingud_section_is_gone(signed_in, specialist):
    """A section holding one control under a heading of its own."""
    page = _page(signed_in, _matter(specialist))

    assert "Teema toimingud" not in page
    assert 'id="teema-toimingud"' not in page


# ---------------------------------------------------------------------------
# C — `Liige`
# ---------------------------------------------------------------------------


def test_liige_sits_in_the_organisation_pickers_footer(signed_in, specialist):
    """§C. Compact, and still the same control with the same refusals.

    It is a second fact about the *same* answer — who this came from — and it
    had a full-width row of its own between the picker and the date.
    """
    page = _page(signed_in, _matter(specialist))

    assert "orgpick__mark" in page
    # The field is still there and still named the same thing: this is a move,
    # not a rebuild. (`member_mark` is the form's property; `source_is_member`
    # is what the control posts.)
    assert 'name="source_is_member"' in page
    # It belongs to the feedback panel and to no other.
    panel = page[page.index('id="arvamus-tagasiside"') :][:6000]
    assert "orgpick__mark" in panel


# ---------------------------------------------------------------------------
# D + E — one rail, and the noise is gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "retired",
    [
        "Kirjas olevad kuupäevad",
        "Ees võib olla",
        "Kogu võimalik teekond",
        "Menetluse tähtajad",
    ],
)
def test_the_retired_text_is_gone_from_the_page(signed_in, specialist, retired):
    """§D. Seven labels explaining a picture a lawyer reads in a second."""
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    assert retired not in _page(signed_in, matter)


def test_the_state_words_are_gone_but_nothing_is_colour_alone(signed_in, specialist):
    """§D, and the line it must not cross.

    `Teadmata` and `Võimalik` were words on the page. Removing them is the
    owner's call; removing them *and* leaving the difference in the stylesheet
    alone would make the rail say nothing with the stylesheet off, nothing to a
    screen reader and nothing on a printout (docs/adr/0074 §12.2).
    """
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    section = _section(_page(signed_in, matter))

    for word in ("Teadmata", "Võimalik", "Kirjas"):
        assert word not in section
    # What the words said, still reachable without seeing the colours.
    assert 'aria-current="step"' in section
    assert "Tulevikus" in section


def test_there_is_exactly_one_rail_on_the_page(signed_in, specialist):
    """§E. One `.tl-strip`, and no second timeline anywhere."""
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    page = _page(signed_in, matter)

    assert page.count('class="tl-strip"') == 1
    assert page.count('aria-label="Menetluse kulg"') == 1
    # And the phase rail's own node markup is gone with the second rail.
    assert "lprail__nodes" not in page
    assert "lprail__node" not in page


def test_the_rail_carries_arvamuse_tahtaeg_beside_the_phases(specialist):
    """§E. The practical date the brief names, on the one timeline.

    `Arvamuse tähtaeg` is `Matter.response_deadline` and is not a phase — it is
    what this office owes, during whichever round the file is on. It reads on
    the rail because that is where a reader looks for the file's dates now.
    """
    ahead = timezone.localdate() + timedelta(days=30)
    matter = _matter(specialist, instruments=("seadus",), response_deadline=ahead)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    steps = _rail(matter, specialist)
    deadline = next(step for step in steps if step.label == "Arvamuse tähtaeg")
    assert deadline.kind == KIND_MILESTONE
    assert deadline.state == "future", "a deadline 30 days out must not read as reached"
    # And the phases are on the same rail, as phases.
    assert any(step.kind == KIND_PHASE and step.label == "Kooskõlastusring" for step in steps)


def test_a_milestone_is_slotted_by_its_date_among_the_phases(specialist):
    """§E. Two orderings reconciled: the pattern's, with dates slotted into it."""
    matter = _matter(specialist, instruments=("seadus",))
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Eelnõu kooskõlastusringile",
        occurred_on=date(2026, 1, 9),
        process_phase=PHASE_KOOSKOLASTUS,
        stage=_stage("consultation"),
    )

    labels = _labels(matter, specialist)
    # `Alustatud` is today; the consultation round began in January. The
    # milestone sorts after the phase it is not earlier than.
    assert "Kooskõlastusring" in labels
    assert "Alustatud" in labels
    assert labels.index("Kooskõlastusring") < labels.index("Alustatud")


# ---------------------------------------------------------------------------
# F + G — the editor
# ---------------------------------------------------------------------------


def test_hiding_a_phase_removes_it_from_this_files_rail(specialist):
    """§F. The point of the round: a phase this file will never see comes off."""
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    assert "VTK" in _labels(matter, specialist)

    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VTK, True, None, "EXACT")],
        actor=specialist,
    )

    assert "VTK" not in _labels(matter, specialist)
    # Hidden, never deleted: the pattern still has it and it can come back.
    assert MatterTimelineStep.objects.get(matter=matter, phase_key=PHASE_VTK).hidden is True


def test_putting_a_phase_back_stores_nothing_at_all(specialist):
    """The default is the absence of a row, which is what makes «put it back» work."""
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    set_timeline_steps(matter=matter, steps=[(PHASE_VTK, True, None, "EXACT")], actor=specialist)
    assert MatterTimelineStep.objects.filter(matter=matter).count() == 1

    set_timeline_steps(matter=matter, steps=[(PHASE_VTK, False, None, "EXACT")], actor=specialist)

    assert MatterTimelineStep.objects.filter(matter=matter).count() == 0
    assert "VTK" in _labels(matter, specialist)


def test_a_date_can_be_put_on_a_phase_and_reads_on_the_rail(specialist):
    """§F. «If Jõustumine is known, the user can add that date directly there.»"""
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    expected = date(2027, 1, 1)

    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_JOUSTUMINE, False, expected, "EXACT")],
        actor=specialist,
    )

    step = next(s for s in _rail(matter, specialist) if s.label == "Jõustumine")
    assert step.display_date == "1.1.2027"
    assert step.sort_on == expected


def test_a_recorded_phase_is_dated_by_its_record_and_not_by_an_expectation(specialist):
    """§G. Reuse rather than duplicate, and the fact wins over the plan.

    A phase the file has recorded is dated by the `Menetluse areng` filed in it —
    the same record the chronology groups on. An expectation somebody typed
    earlier does not overwrite it and is simply no longer interesting.
    """
    matter = _matter(specialist, instruments=("seadus",))
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_KOOSKOLASTUS, False, date(2027, 6, 1), "EXACT")],
        actor=specialist,
    )
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Eelnõu kooskõlastusringile",
        occurred_on=date(2026, 1, 9),
        process_phase=PHASE_KOOSKOLASTUS,
        stage=_stage("consultation"),
    )

    step = next(s for s in _rail(matter, specialist) if s.label == "Kooskõlastusring")
    assert step.display_date == "9.1.2026"
    assert step.sort_on == date(2026, 1, 9)


def test_the_editor_offers_no_date_box_for_a_recorded_phase(signed_in, specialist):
    """The other half of the same rule, in the panel.

    A box over a day the record already proves is a second place to type it.
    """
    matter = _matter(specialist, instruments=("seadus",))
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Eelnõu kooskõlastusringile",
        occurred_on=date(2026, 1, 9),
        process_phase=PHASE_KOOSKOLASTUS,
        stage=_stage("consultation"),
    )

    panel = signed_in.get(
        reverse("matters:timeline_steps", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "9.1.2026 · kirja pandud" in panel
    assert f'name="{PHASE_KOOSKOLASTUS}__date"' not in panel
    # A phase with nothing recorded still gets its box.
    assert f'name="{PHASE_JOUSTUMINE}__date"' in panel


def test_the_editor_offers_only_this_files_own_procedure(signed_in, specialist):
    """A `Määrus` is not offered `Riigikogus`: a regulation is not adopted there."""
    matter = _matter(specialist, instruments=("maarus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    panel = signed_in.get(
        reverse("matters:timeline_steps", kwargs={"pk": matter.pk})
    ).content.decode()

    assert 'name="kooskolastus__shown"' in panel
    assert 'name="riigikogu__shown"' not in panel


def test_the_box_means_show_rather_than_hide(signed_in, specialist):
    """A ticked box that removes a step is the one shape somebody gets backwards.

    Posted with the box absent — which is what an unticked checkbox sends — the
    phase comes off the rail.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    before = [step.label for step in _rail(matter, specialist) if step.kind == KIND_PHASE]
    assert before, "the file must start with phases for this to prove anything"

    # An unticked checkbox posts nothing at all, so an empty body is «none of
    # them shown» — the strongest form of the claim.
    signed_in.post(reverse("matters:timeline_steps", kwargs={"pk": matter.pk}), {})

    assert MatterTimelineStep.objects.filter(matter=matter, hidden=True).count() == len(before)
    assert [step.label for step in _rail(matter, specialist) if step.kind == KIND_PHASE] == []
    # The dated points are not phases and are untouched by the panel.
    assert any(step.kind == KIND_MILESTONE for step in _rail(matter, specialist))


def test_editing_the_rail_writes_one_audit_row_and_changes_nothing_else(specialist):
    """§G. A preference is not a business event, and it creates no work."""
    from app.workflow.models import NextAction

    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    matter.refresh_from_db()
    before_stage = matter.stage_id
    before_actions = NextAction.objects.filter(matter=matter).count()
    before_events = ChangeEvent.objects.filter(matter=matter).count()

    set_timeline_steps(matter=matter, steps=[(PHASE_VTK, True, None, "EXACT")], actor=specialist)

    events = ChangeEvent.objects.filter(matter=matter).count()
    assert events == before_events + 1
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.TIMELINE_STEPS_CHANGED
        ).count()
        == 1
    )
    matter.refresh_from_db()
    assert matter.stage_id == before_stage
    assert NextAction.objects.filter(matter=matter).count() == before_actions
    # And no chronology row: tailoring a roadmap is not something that happened
    # to the proceeding.
    from app.matters.timeline import matter_timeline

    page, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    assert not any("kulg" in (item.summary_sentence or "").lower() for item in page)


def test_a_save_that_moves_nothing_records_nothing(specialist):
    """An audit row for a button press is a history of somebody pressing a button."""
    matter = _matter(specialist, instruments=("seadus",))
    before = ChangeEvent.objects.filter(matter=matter).count()

    changed = set_timeline_steps(
        matter=matter,
        steps=[(PHASE_KOOSKOLASTUS, False, None, "EXACT")],
        actor=specialist,
    )

    assert changed == 0
    assert ChangeEvent.objects.filter(matter=matter).count() == before


def test_a_crafted_phase_outside_the_vocabulary_stores_nothing(specialist):
    """A presentation preference may never block a business write."""
    matter = _matter(specialist, instruments=("seadus",))

    set_timeline_steps(
        matter=matter,
        steps=[("riigikohus", True, None, "EXACT")],
        actor=specialist,
    )

    assert MatterTimelineStep.objects.filter(matter=matter).count() == 0


def test_a_restricted_step_row_hides_no_phase_for_a_reader_who_cannot_see_it(specialist, reader):
    """Scoped before it is drawn, like every other source on this rail."""
    from app.core.enums import Visibility

    matter = _matter(specialist, instruments=("vtk", "seadus"))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    set_timeline_steps(matter=matter, steps=[(PHASE_VTK, True, None, "EXACT")], actor=specialist)
    row = MatterTimelineStep.objects.get(matter=matter, phase_key=PHASE_VTK)
    row.visibility_override = Visibility.RESTRICTED
    row.save(update_fields=["visibility_override"])

    assert "VTK" not in _labels(matter, specialist)
    assert "VTK" in _labels(matter, reader)
