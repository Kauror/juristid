"""`Menetluse kulg` keeps the procedure's order when `Õigusakt` grows a `VTK`.

The historical replay of 2026-10-04 found two shapes of one defect:

* **Case 03.** A file created as `Muu siseriiklik` later also carried `VTK` and
  `Seadus`. Three instruments, two of them naming different roads, read as
  «mixed», so the rail fell back to the generic domestic bill — which has no
  VTK node — and the VTK the person had dated in 2021 was kept only as a
  recorded extra, drawn *just before the present*: after a Riigikogu reading
  from 2023.
* **Case 04.** A file created as Koda's own proposal later also carried `VTK`.
  `Koja ettepanek` + `VTK` also read as «mixed»: the rail lost the proposal's
  node and offered no VTK at all, so «Muuda kulgu» could not date it either.

What is protected here: a `VTK` beside a law, or beside an instrument with no
road of its own, reads on the VTK pattern; a `VTK` beside Koda's proposal reads
after the proposal; a phase the pattern does not draw is placed in the
procedure's order rather than at the end; saving the same `Õigusakt` again adds
nothing; and a person's own hidden phase, added step and dates stay theirs.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.matters.legal_process import (
    KIND_PHASE,
    KIND_STEP,
    STATE_RECORDED,
    legal_process_rail,
    matter_rail,
    phase_context,
)
from app.matters.models import MatterProceduralDevelopment
from app.matters.process_phases import (
    PATTERN_KOJA_ETTEPANEK_VTK,
    PATTERN_VTK,
    PHASE_KOJA_ETTEPANEK,
    PHASE_KOOSKOLASTUS,
    PHASE_RIIGIKOGU,
    PHASE_VALITSUS,
    PHASE_VTK,
    pattern_for,
)
from app.matters.process_timeline import process_steps
from app.matters.services import change_stage, set_legal_instruments, set_timeline_steps
from app.matters.workspace import add_procedural_development
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _instruments(*keys: str) -> list[LegalInstrumentType]:
    return [LegalInstrumentType.objects.get(key=key) for key in keys]


def _rail(matter, user):
    context = phase_context(matter=matter)
    return matter_rail(
        matter=matter,
        user=user,
        rail=legal_process_rail(matter=matter, user=user, context=context),
        milestones=process_steps(matter=matter, user=user),
    )


def _phases(matter, user) -> list[str]:
    return [step.key for step in _rail(matter, user) if step.kind == KIND_PHASE]


def _case_03(specialist):
    """The TAKS → TAIKS shape: «Muu siseriiklik», then VTK and Seadus, then Riigikogu."""
    matter = factories.MatterFactory(owner=specialist, track="")
    set_legal_instruments(matter=matter, legal_instruments=_instruments("muu-siseriiklik"))
    set_legal_instruments(
        matter=matter,
        legal_instruments=_instruments("muu-siseriiklik", "vtk", "seadus"),
        actor=specialist,
    )
    set_timeline_steps(
        matter=matter,
        steps=[
            (PHASE_VTK, False, date(2021, 6, 14), "EXACT"),
            (PHASE_KOOSKOLASTUS, False, date(2022, 12, 1), "EXACT"),
            (PHASE_VALITSUS, False, date(2023, 1, 3), "EXACT"),
            (PHASE_RIIGIKOGU, False, date(2023, 1, 16), "EXACT"),
        ],
        actor=specialist,
    )
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    return matter


def test_a_vtk_added_to_a_muu_siseriiklik_file_reads_on_the_vtk_pattern():
    keys = frozenset({"muu-siseriiklik", "vtk", "seadus"})
    assert pattern_for(track="", instrument_keys=keys).key == PATTERN_VTK
    # A VTK alone with an instrument that has no road of its own, too.
    assert pattern_for(track="", instrument_keys=frozenset({"muu-siseriiklik", "vtk"})).key == (
        PATTERN_VTK
    )


def test_case_03_draws_the_vtk_before_the_bill_it_became(specialist):
    matter = _case_03(specialist)

    phases = _phases(matter, specialist)

    assert phases.index(PHASE_VTK) < phases.index(PHASE_KOOSKOLASTUS)
    assert phases.index(PHASE_KOOSKOLASTUS) < phases.index(PHASE_VALITSUS)
    assert phases.index(PHASE_VALITSUS) < phases.index(PHASE_RIIGIKOGU)
    vtk = next(step for step in _rail(matter, specialist) if step.key == PHASE_VTK)
    assert vtk.display_date == "14.6.2021"


def test_saving_the_same_oigusakt_again_draws_nothing_twice(specialist):
    matter = _case_03(specialist)
    before = _phases(matter, specialist)

    for _ in range(2):
        set_legal_instruments(
            matter=matter,
            legal_instruments=_instruments("muu-siseriiklik", "vtk", "seadus"),
            actor=specialist,
        )

    after = _phases(matter, specialist)
    assert after == before
    assert len(after) == len(set(after))


def test_case_04_a_proposal_answered_by_a_vtk_reads_both_in_order(specialist):
    matter = factories.MatterFactory(owner=specialist, track="")
    set_legal_instruments(matter=matter, legal_instruments=_instruments("koja-ettepanek"))
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_KOJA_ETTEPANEK, False, date(2024, 10, 11), "EXACT")],
        actor=specialist,
    )

    set_legal_instruments(
        matter=matter, legal_instruments=_instruments("koja-ettepanek", "vtk"), actor=specialist
    )

    assert (
        pattern_for(track="", instrument_keys=frozenset({"koja-ettepanek", "vtk"})).key
        == PATTERN_KOJA_ETTEPANEK_VTK
    )
    phases = _phases(matter, specialist)
    assert phases[:2] == [PHASE_KOJA_ETTEPANEK, PHASE_VTK]
    # The proposal keeps the date the person gave it, and the VTK can now be
    # dated through «Muuda kulgu», whose choices are the pattern's own nodes.
    proposal = next(step for step in _rail(matter, specialist) if step.key == PHASE_KOJA_ETTEPANEK)
    assert proposal.display_date == "11.10.2024"
    set_timeline_steps(
        matter=matter, steps=[(PHASE_VTK, False, date(2026, 6, 8), "EXACT")], actor=specialist
    )
    vtk = next(step for step in _rail(matter, specialist) if step.key == PHASE_VTK)
    assert vtk.display_date == "8.6.2026"


def test_a_phase_the_pattern_lacks_is_drawn_in_the_procedures_order(specialist):
    """A recorded VTK on a plain bill: before its consultation, not after Riigikogu."""
    matter = factories.MatterFactory(owner=specialist, track="")
    set_legal_instruments(matter=matter, legal_instruments=_instruments("seadus"))
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    add_procedural_development(matter=matter, author=specialist, title="VTK avaldati")
    MatterProceduralDevelopment.objects.filter(matter=matter).update(process_phase=PHASE_VTK)

    rail = _rail(matter, specialist)
    phases = [step.key for step in rail if step.kind == KIND_PHASE]

    assert phases.index(PHASE_VTK) < phases.index(PHASE_KOOSKOLASTUS)
    assert next(step for step in rail if step.key == PHASE_VTK).state == STATE_RECORDED


def test_a_kept_phase_still_never_goes_past_the_present(specialist):
    """Riigikogu recorded on a regulation stays behind where the file stands."""
    matter = factories.MatterFactory(owner=specialist, track="")
    set_legal_instruments(matter=matter, legal_instruments=_instruments("maarus"))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    add_procedural_development(matter=matter, author=specialist, title="Riigikogu arutelu")
    MatterProceduralDevelopment.objects.filter(matter=matter).update(process_phase=PHASE_RIIGIKOGU)

    rail = _rail(matter, specialist)
    keys = [step.key for step in rail if step.kind == KIND_PHASE]
    current = next(index for index, step in enumerate(rail) if step.key == PHASE_KOOSKOLASTUS)
    kept = next(index for index, step in enumerate(rail) if step.key == PHASE_RIIGIKOGU)

    assert kept < current
    assert keys.count(PHASE_RIIGIKOGU) == 1


def test_a_hidden_phase_and_an_added_step_stay_the_persons(specialist):
    matter = _case_03(specialist)
    set_timeline_steps(
        matter=matter,
        steps=[(PHASE_VTK, True, None, "EXACT")],
        added=[(None, "Komisjoni istung", date(2023, 2, 1), "EXACT", PHASE_RIIGIKOGU, False)],
        actor=specialist,
    )

    rail = _rail(matter, specialist)

    # Hidden and not reached by a recorded act, so it is not drawn.
    assert PHASE_VTK not in [step.key for step in rail if step.kind == KIND_PHASE]
    added = [step for step in rail if step.kind == KIND_STEP]
    assert [step.label for step in added] == ["Komisjoni istung"]
    riigikogu = next(index for index, step in enumerate(rail) if step.key == PHASE_RIIGIKOGU)
    assert rail.index(added[0]) == riigikogu + 1
