"""The lawyers' eight worked examples, and the claims the grouping must refuse.

Sections 3 and 13 of the brief. Every scenario here is one the department wrote
down, rebuilt from canonical records through the ordinary services, and read back
through the ordinary projection. The dates are this file's own — the source's
were illustrative, and copying an accidentally reversed pair and then teaching
the renderer to reproduce it would be encoding a typo as a requirement.

What is being protected, in one list:

* history shows **only what the records support** — no empty earlier sections, no
  phase ticked because a later one exists, no event dated by a system clock;
* guidance may show **unrecorded future phases**, undated and labelled possible,
  and writes nothing;
* a phase **occurs more than once**, and two consultation rounds a year apart are
  two sections;
* an ambiguous interval is **unplaced**, which is an ordinary answer;
* a restricted record leaks **no heading, no date, no count and no gap**.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.intelligence.enums import ImportantDateKind
from app.intelligence.services import add_effective_date, add_important_date
from app.matters.legal_process import legal_process_rail, phase_context
from app.matters.phase_history import UNPLACED_KEY
from app.matters.process_phases import (
    PATTERN_DIRECTIVE,
    PATTERN_EU_REGULATION,
    PATTERN_KOJA_ETTEPANEK,
    PATTERN_MAARUS,
    PATTERN_VTK,
    PHASE_ELI_KONSULTATSIOON,
    PHASE_ELI_MENETLUS,
    PHASE_JOUSTUMINE,
    PHASE_KOJA_ETTEPANEK,
    PHASE_KOOSKOLASTUS,
    PHASE_RIIGIKOGU,
    PHASE_VALITSUS,
    PHASE_VTK,
)
from app.matters.process_timeline import (
    STATE_AHEAD,
    TRANSPOSITION_DEADLINE_LABEL,
    process_steps,
)
from app.matters.services import change_stage, close_matter
from app.matters.timeline import matter_timeline
from app.matters.workspace import add_matter_koda_opinion, add_procedural_development
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import DatePrecision, Disposition
from app.workflow.models import StageVocabulary
from tests import factories
from tests.test_substantive_matter_history import _pdf

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _instrument(key: str) -> LegalInstrumentType:
    return LegalInstrumentType.objects.get(key=key)


def _matter(owner, *, instruments: tuple[str, ...] = (), **kwargs):
    matter = factories.MatterFactory(owner=owner, track="", **kwargs)
    if instruments:
        matter.legal_instruments.set([_instrument(key) for key in instruments])
    return matter


def _step(matter, actor, title: str, on: date, phase: str, *, stage: str | None = None):
    """One `Menetluse areng`, through the ordinary use case.

    The *only* way a phase enters the system: a person recorded a step and said
    which part of the procedure it belongs to.
    """
    return add_procedural_development(
        matter=matter,
        author=actor,
        title=title,
        occurred_on=on,
        process_phase=phase,
        stage=_stage(stage) if stage else None,
    ).record


def _opinion(matter, actor, organisation, on: date, title: str = "Koja arvamus"):
    return add_matter_koda_opinion(
        matter=matter,
        author=actor,
        upload=_pdf(f"{on.isoformat()}.pdf"),
        recipients=[organisation],
        sent_on=on,
        title=title,
    ).record


def _sections(matter, user) -> list[tuple[str, date | None]]:
    """The phase sections a reader sees, in reading order, as (label, start)."""
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    seen: list[tuple[str, date | None]] = []
    for item in items:
        if item.opens_phase and item.phase is not None:
            seen.append((item.phase.label, item.phase.started_on))
    history = items.history
    if history.current_without_rows is not None:
        seen.insert(0, (history.current_without_rows.label, None))
    return seen


def _labels(matter, user) -> list[str]:
    return [label for label, _start in _sections(matter, user)]


def _grouped(matter, user) -> dict[str, list[str]]:
    """Section label -> the headlines under it, for the placed sections."""
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    out: dict[str, list[str]] = {}
    for item in items:
        if item.phase is None:
            continue
        what = item.milestone.what if item.milestone is not None else (item.summary_sentence or "")
        out.setdefault(item.phase.label, []).append(what)
    return out


def _rendered_for(matter, user) -> str:
    """Everything a reader could read off the grouped page, as one string.

    The permission assertions have to prove a *negative* — that no headline, no
    sub-line, no date, no phase heading and no phase start date anywhere on the
    page mentions the record this reader may not see.
    """
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    parts: list[str] = []
    for item in items:
        if item.milestone is not None:
            parts += [item.milestone.what, item.milestone.display_date, item.milestone.sub]
        if item.phase is not None:
            parts.append(item.phase.label)
            if item.phase.started_on is not None:
                parts += [
                    item.phase.started_on.isoformat(),
                    item.phase.started_on.strftime("%d.%m.%Y"),
                ]
        parts.append(item.summary_sentence or "")
    return " ".join(part for part in parts if part)


def _phase_of(matter, user, record) -> str:
    """Which phase one canonical record's own row was grouped into.

    Matched on the record rather than on a headline: a sent opinion's chronology
    row is headed «Arvamus välja», which is what happened to the letter, and a
    test asserting on those words would break the first time they are reworded.
    """
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    for item in items:
        if item.record is not None and item.record.pk == record.pk:
            return item.phase.phase_key if item.phase is not None else ""
    raise AssertionError(f"no row for {record!r}")


# ---------------------------------------------------------------------------
# §3 A — a VTK, and then a bill
# ---------------------------------------------------------------------------


def test_scenario_a_a_vtk_followed_by_a_bill(specialist, organisation):
    """The full example, and the one it was written to prove: **two consultation
    rounds, a year apart, are two sections and not one.**

    Grouping by phase *key* would have put the bill's round inside the VTK's,
    which is the defect that makes «which round did we answer» unanswerable on
    exactly the files a lawyer needs it for.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(matter, specialist, "VTK saadeti kooskõlastusringile", date(2025, 2, 10), PHASE_VTK)
    vtk_opinion = _opinion(matter, specialist, organisation, date(2025, 3, 5))
    _step(
        matter,
        specialist,
        "Eelnõu saadeti kooskõlastusringile",
        date(2025, 9, 1),
        PHASE_KOOSKOLASTUS,
    )
    bill_opinion = _opinion(matter, specialist, organisation, date(2025, 9, 30))
    _step(
        matter,
        specialist,
        "Valitsus saatis eelnõu Riigikogule",
        date(2026, 1, 15),
        PHASE_VALITSUS,
    )
    _step(
        matter,
        specialist,
        "Riigikogu võttis seaduse vastu",
        date(2026, 4, 2),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )

    # Newest phase first, and the lawyers' own words.
    assert _labels(matter, specialist) == [
        "Riigikogus",
        "Valitsuses",
        "Kooskõlastusring",
        "VTK",
    ]

    # The two opinions belong to the rounds they answered, and neither was
    # grouped by the file's *current* `Hetkeseis`, which is `Riigikogus`.
    assert _phase_of(matter, specialist, vtk_opinion) == PHASE_VTK
    assert _phase_of(matter, specialist, bill_opinion) == PHASE_KOOSKOLASTUS


def test_a_repeated_phase_is_a_second_occurrence_and_not_a_merge(specialist):
    """§8. Kooskõlastusring → Valitsuses → Kooskõlastusring, all three drawn.

    The rounds are not reordered into one earlier bucket because their keys
    match, and the second one is not swallowed by the first.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Esimene kooskõlastusring", date(2026, 1, 10), PHASE_KOOSKOLASTUS)
    _step(matter, specialist, "Valitsus arutas", date(2026, 3, 1), PHASE_VALITSUS)
    _step(matter, specialist, "Teine kooskõlastusring", date(2026, 5, 4), PHASE_KOOSKOLASTUS)

    sections = _sections(matter, specialist)
    assert [label for label, _start in sections] == [
        "Kooskõlastusring",
        "Valitsuses",
        "Kooskõlastusring",
    ]
    # Two occurrences, two different beginnings — the later one is the later run.
    starts = [start for label, start in sections if label == "Kooskõlastusring"]
    assert starts == [date(2026, 5, 4), date(2026, 1, 10)]


# ---------------------------------------------------------------------------
# §3 B and C — a bill with no VTK, and a late entry
# ---------------------------------------------------------------------------


def test_scenario_b_a_bill_with_no_vtk_invents_no_vtk_section(specialist):
    matter = _matter(specialist, instruments=("seadus",))
    _step(
        matter,
        specialist,
        "Eelnõu saadeti kooskõlastusringile",
        date(2026, 2, 3),
        PHASE_KOOSKOLASTUS,
    )
    _step(matter, specialist, "Valitsus kiitis heaks", date(2026, 5, 6), PHASE_VALITSUS)

    assert _labels(matter, specialist) == ["Valitsuses", "Kooskõlastusring"]
    assert "VTK" not in _labels(matter, specialist)


def test_scenario_c_a_late_entry_shows_no_earlier_history(specialist, organisation):
    """Koda first meets the file in the Riigikogu. Recorded history begins there.

    **No empty earlier phases**, and — the sharper half — none of them ticked. A
    section that is absent says «Koda was not there, or nobody wrote it down»;
    a section drawn as complete would be three milestones manufactured out of one.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(
        matter,
        specialist,
        "Eelnõu jõudis Riigikokku",
        date(2026, 6, 1),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )
    _opinion(matter, specialist, organisation, date(2026, 6, 20), title="Koja arvamus Riigikogule")

    assert _labels(matter, specialist) == ["Riigikogus"]
    for absent in ("VTK", "Kooskõlastusring", "Valitsuses"):
        assert absent not in _labels(matter, specialist)

    # And the road ahead still works from there: adoption and commencement may
    # follow, undated and labelled possible.
    rail = legal_process_rail(matter=matter, user=specialist)
    assert [node.label for node in rail.ahead] == ["Jõustumine"]
    assert all(node.state == "possible" for node in rail.ahead)


# ---------------------------------------------------------------------------
# §3 D and E — the two regulations, without guessing the authority
# ---------------------------------------------------------------------------


def test_scenarios_d_and_e_render_from_their_facts_without_guessing_whose(specialist):
    """A Government regulation and a ministerial one, on one pattern.

    They differ by exactly one recorded phase, and the rail guesses neither: the
    reviewed `Õigusakt` value is `Määrus`, which does not say whose. `Valitsuses`
    is offered as a phase that **may not apply**, and the absence of a Government
    record on the second file is not read as evidence that it is a minister's.
    """
    government = _matter(specialist, instruments=("maarus",))
    _step(
        government, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 8), PHASE_KOOSKOLASTUS
    )
    _step(
        government,
        specialist,
        "Valitsus kiitis heaks",
        date(2026, 3, 9),
        PHASE_VALITSUS,
        stage="government",
    )

    ministerial = _matter(specialist, instruments=("maarus",))
    _step(
        ministerial, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 8), PHASE_KOOSKOLASTUS
    )
    _step(
        ministerial,
        specialist,
        "Määrus jõustus",
        date(2026, 4, 1),
        PHASE_JOUSTUMINE,
        stage="in_force",
    )

    assert _labels(government, specialist) == ["Valitsuses", "Kooskõlastusring"]
    assert _labels(ministerial, specialist) == ["Jõustumine", "Kooskõlastusring"]
    # Neither file was given a Parliament anywhere: a regulation is not adopted
    # by the Riigikogu, and suggesting it would teach a reader something false.
    for matter in (government, ministerial):
        rail = legal_process_rail(matter=matter, user=specialist)
        assert rail.pattern.key == PATTERN_MAARUS
        assert "Riigikogus" not in [node.label for node in rail.nodes]

    # And `Valitsuses` is drawn as conditional rather than as the next step.
    rail = legal_process_rail(matter=government, user=specialist)
    valitsus = next(node for node in rail.nodes if node.label == "Valitsuses")
    assert valitsus.conditional is True


# ---------------------------------------------------------------------------
# §3 F and G — Koda's own proposal
# ---------------------------------------------------------------------------


def test_scenario_f_a_proposal_that_progresses(specialist):
    matter = _matter(specialist, instruments=("koja-ettepanek",))
    _step(matter, specialist, "Koja ettepanek", date(2025, 11, 4), PHASE_KOJA_ETTEPANEK)
    vastus = _step(
        matter, specialist, "Ministeeriumi vastus", date(2026, 1, 20), PHASE_KOJA_ETTEPANEK
    )
    _step(
        matter,
        specialist,
        "Kooskõlastusringile tuli eelnõu, kus ettepanekut käsitletakse",
        date(2026, 4, 7),
        PHASE_KOOSKOLASTUS,
        stage="consultation",
    )

    assert _labels(matter, specialist) == ["Kooskõlastusring", "Koja ettepanek"]
    # The ministry's answer sits in the proposal's own phase, not in the round
    # that came afterwards.
    assert _phase_of(matter, specialist, vastus) == PHASE_KOJA_ETTEPANEK


def test_scenario_g_koda_stopping_is_not_the_procedure_stopping(specialist):
    """§10. `Rohkem ei tegele` is what **Koda** decided and never a phase.

    The ministry does not stop drafting because this office stopped reading, so
    the rail stays where the file's last recorded stage left it and the decision
    reads beside it as a separate sentence.
    """
    matter = _matter(specialist, instruments=("koja-ettepanek",))
    _step(
        matter,
        specialist,
        "Koja ettepanek",
        date(2026, 2, 2),
        PHASE_KOJA_ETTEPANEK,
        stage="consultation",
    )
    _step(matter, specialist, "Ministeeriumi vastus", date(2026, 3, 3), PHASE_KOJA_ETTEPANEK)
    close_matter(
        matter=matter,
        disposition=Disposition.MONITORING_STOPPED.value,
        actor=specialist,
    )

    rail = legal_process_rail(matter=matter, user=specialist)
    assert rail.koda_stopped is True
    assert rail.pattern.key == PATTERN_KOJA_ETTEPANEK
    # Not a node, not a state, and not a claim that the procedure ended: the
    # road ahead is exactly what it was.
    assert "Koda" not in " ".join(node.label for node in rail.nodes)
    assert rail.ahead, "Koda stopping must not empty the road ahead"


# ---------------------------------------------------------------------------
# §3 H — the European examples
# ---------------------------------------------------------------------------


def test_scenario_h_a_directive_keeps_joustumine_and_ulevotmine_apart(specialist, organisation):
    """An EU act being in force does not mean Estonia has transposed it.

    The two used to share one node — `Ülevõtmine / jõustumine` — so a reader
    could not tell an act that is in force from a directive somebody still has to
    transpose. They are two ends of this rail.
    """
    matter = _matter(specialist, instruments=("direktiiv",))
    _step(
        matter,
        specialist,
        "Euroopa Komisjoni avalik konsultatsioon",
        date(2025, 1, 15),
        PHASE_ELI_KONSULTATSIOON,
    )
    _opinion(matter, specialist, organisation, date(2025, 2, 10), title="Koja vastus")
    _step(
        matter,
        specialist,
        "EL võttis õigusakti vastu",
        date(2026, 3, 1),
        PHASE_ELI_MENETLUS,
        stage="awaiting_transposition",
    )

    rail = legal_process_rail(matter=matter, user=specialist)
    assert rail.pattern.key == PATTERN_DIRECTIVE
    labels = [node.label for node in rail.nodes]
    assert "Jõustumine" in labels
    assert "Ülevõtmine" in labels
    assert labels.index("Jõustumine") < labels.index("Ülevõtmine")
    # Transposition is the obligation a directive carries, not a possibility.
    ulevotmine = next(node for node in rail.nodes if node.label == "Ülevõtmine")
    assert ulevotmine.conditional is False


def test_an_eu_regulation_acquires_no_transposition_obligation(specialist):
    """Test 6. A regulation applies directly and nobody transposes it.

    The generic European rail suggested `Ülevõtmine` for every European file,
    which on this instrument is not a harmless extra step — it is the rail
    inventing a legal obligation that by definition does not exist.
    """
    matter = _matter(specialist, instruments=("el-maarus",))
    _step(
        matter,
        specialist,
        "EL võttis määruse vastu",
        date(2026, 3, 1),
        PHASE_ELI_MENETLUS,
        stage="eu_procedure",
    )

    rail = legal_process_rail(matter=matter, user=specialist)
    assert rail.pattern.key == PATTERN_EU_REGULATION
    assert "Ülevõtmine" not in [node.label for node in rail.nodes]
    assert "Ülevõtmine" not in [node.label for node in rail.ahead]
    assert "Ülevõtmine" not in [node.label for node in rail.ahead_rest]


def test_a_transposition_stage_on_a_regulation_reads_beside_the_rail(specialist):
    """A file stating a contradiction has it shown, never resolved for it.

    `ELi õiguse ülevõtmise ootel` on an instrument that is never transposed is
    somebody's answer and the pattern cannot hold it. It reads in its own words
    beside the rail rather than being pushed onto the nearest node — which would
    resolve the contradiction by quietly picking a side.
    """
    matter = _matter(specialist, instruments=("el-maarus",))
    _step(
        matter,
        specialist,
        "EL menetluses",
        date(2026, 1, 5),
        PHASE_ELI_MENETLUS,
        stage="eu_procedure",
    )
    change_stage(matter=matter, stage=_stage("awaiting_transposition"), actor=specialist)

    rail = legal_process_rail(matter=matter, user=specialist)
    assert rail.unplaced_stage == "ELi õiguse ülevõtmise ootel"
    assert all(node.state != "current" for node in rail.nodes)


def test_a_mixed_eu_and_domestic_file_is_given_no_road(specialist):
    """`direktiiv ja seadus` is a real register value and not a road.

    There is no reading of it that picks one procedure over the other, and
    choosing arbitrarily is worse than saying nothing: the header's `Hetkeseis`
    goes on answering «where is this».
    """
    matter = _matter(specialist, instruments=("seadus", "direktiiv"))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    assert phase_context(matter=matter).pattern is None
    assert legal_process_rail(matter=matter, user=specialist) is None


# ---------------------------------------------------------------------------
# §9 — dates, and what may never date an event
# ---------------------------------------------------------------------------


def test_a_known_future_commencement_and_transposition_deadline_stay_future(specialist):
    """Test 2. Visible, and clearly not completed.

    A recorded commencement in October and a transposition deadline next year are
    facts the file holds and a reader plans around. Neither creates a phase, ticks
    a node, advances `Hetkeseis` or makes the file read as late.
    """
    matter = _matter(specialist, instruments=("direktiiv",))
    _step(
        matter,
        specialist,
        "EL võttis direktiivi vastu",
        date(2026, 3, 1),
        PHASE_ELI_MENETLUS,
        stage="awaiting_transposition",
    )
    ahead = timezone.localdate() + timedelta(days=200)
    add_effective_date(
        matter=matter,
        date_value=ahead,
        period_end=ahead,
        description="Põhiosa",
        actor=specialist,
    )
    deadline = add_important_date(
        matter=matter,
        title="Direktiivi ülevõtmise tähtaeg",
        date_value=ahead,
        period_end=ahead,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE.value,
        actor=specialist,
    )
    assert deadline.kind == ImportantDateKind.TRANSPOSITION_DEADLINE

    # Neither has happened, so neither is a chronology row yet …
    items, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    headlines = [item.milestone.what for item in items if item.milestone is not None]
    assert "Jõustus" not in headlines
    # … and the rail has not ticked anything on the strength of them.
    rail = legal_process_rail(matter=matter, user=specialist)
    assert {node.state for node in rail.nodes} <= {"current", "recorded", "unknown", "possible"}
    assert matter.procedural_developments.get().process_phase == PHASE_ELI_MENETLUS
    matter.refresh_from_db()
    assert matter.stage.key == "awaiting_transposition"


def test_a_standalone_stage_edit_places_nothing_and_dates_nothing(specialist, organisation):
    """Tests 10 and 11, which are one rule read twice.

    A bare `Hetkeseis` change proves that somebody **recorded a value**. Its
    timestamp is the moment they typed it, so it dates no external legislative
    step — and correcting a mistaken stage therefore leaves no phase behind that
    the procedure never visited.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(
        matter,
        specialist,
        "Eelnõu kooskõlastusringile",
        date(2026, 1, 9),
        PHASE_KOOSKOLASTUS,
        stage="consultation",
    )
    # The mistake, and the correction — both through the header, both undated.
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    # No `Jõustumine` section was created by the mistake. The two bare stage
    # edits read under `Etapiga sidumata`, which is exactly what they are: proof
    # that a value was recorded, dated by nothing but this application's own
    # clock, and therefore placing nothing.
    assert _labels(matter, specialist) == ["Kooskõlastusring", "Etapiga sidumata"]
    assert "Jõustumine" not in _labels(matter, specialist)
    # … and the rail does not leave `Jõustumine` reading as a completed step.
    rail = legal_process_rail(matter=matter, user=specialist)
    joustumine = next(node for node in rail.nodes if node.label == "Jõustumine")
    assert joustumine.state == "possible"

    # The audit history still holds both writes; nothing was deleted to achieve
    # any of the above.
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
        ).count()
        == 3
    )


def test_a_backdated_opinion_is_placed_by_its_own_date(specialist, organisation):
    """Test 8. Entry time places nothing.

    An opinion sent during the consultation round and typed up months later, once
    the file has moved on, belongs to the round it answered.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(
        matter,
        specialist,
        "Eelnõu kooskõlastusringile",
        date(2026, 1, 9),
        PHASE_KOOSKOLASTUS,
    )
    _step(
        matter,
        specialist,
        "Eelnõu jõudis Riigikokku",
        date(2026, 6, 1),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )
    # Written up today, dated February.
    opinion = _opinion(matter, specialist, organisation, date(2026, 2, 14))

    assert _phase_of(matter, specialist, opinion) == PHASE_KOOSKOLASTUS


def test_same_day_records_do_not_manufacture_membership_from_entry_order(specialist, organisation):
    """Test 12. The interval is decided on business dates and nothing else.

    Two records written on the same afternoon, in either order, group the same
    way — and a record that merely shares a *recording* day with a phase change
    gains nothing from it.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    _step(matter, specialist, "Valitsus arutas", date(2026, 4, 1), PHASE_VALITSUS)
    # Dated the day the Government phase opened: the boundary is `[start, next)`,
    # so it is in the phase that began that day, whatever order things were typed.
    opinion = _opinion(matter, specialist, organisation, date(2026, 4, 1))

    assert _phase_of(matter, specialist, opinion) == PHASE_VALITSUS

    # The same facts recorded in the other order reach the same answer.
    mirror = _matter(specialist, instruments=("seadus",))
    mirrored = _opinion(mirror, specialist, organisation, date(2026, 4, 1))
    _step(mirror, specialist, "Valitsus arutas", date(2026, 4, 1), PHASE_VALITSUS)
    _step(mirror, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)

    assert _phase_of(mirror, specialist, mirrored) == PHASE_VALITSUS


def test_an_undated_record_is_unplaced_and_still_visible(specialist):
    """Test 9 and test 13. «Kuupäev teadmata» is not «file it under today».

    A development whose date nobody knows opens no interval — there is no day for
    one to begin on. It is placed by its own explicit phase, because somebody
    said so; a record with neither reads under `Etapiga sidumata`, in full.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Ministeerium saatis uue versiooni",
        occurred_on=None,
        process_phase="",
    )

    labels = _labels(matter, specialist)
    assert "Etapiga sidumata" in labels
    grouped = _grouped(matter, specialist)
    assert any("uue versiooni" in what for what in grouped["Etapiga sidumata"])


def test_an_approximate_date_places_a_row_on_its_anchor_and_still_prints_the_period(
    specialist,
):
    """Test 9. A month is a month wherever it lands in a sort."""
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Valitsus arutas",
        occurred_on=date(2026, 4, 1),
        occurred_on_precision=DatePrecision.MONTH.value,
        process_phase=PHASE_VALITSUS,
    )

    items, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    row = next(item for item in items if item.milestone and "Valitsus" in item.milestone.what)
    assert row.phase.phase_key == PHASE_VALITSUS
    assert "aprill" in row.milestone.display_date


# ---------------------------------------------------------------------------
# The contested tail — §7's «unambiguous, or it does not exist»
# ---------------------------------------------------------------------------


def test_a_current_stage_that_contradicts_the_last_phase_leaves_the_tail_unplaced(
    specialist, organisation
):
    """The file left the phase; nothing dated says when, so nothing is claimed.

    The interval after the last recorded step runs to the present only while
    nothing contradicts it. A `Hetkeseis` naming a different phase does: it proves
    the file moved on without saying on what day, so an opinion sent afterwards
    could belong to either. §7 answers that with «unplaced», not with a guess.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    opinion = _opinion(matter, specialist, organisation, date(2026, 5, 20))

    assert _phase_of(matter, specialist, opinion) == UNPLACED_KEY
    # And the phase the file *is* on reads as a section of its own, with no date
    # and no invented event in it.
    items, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    current = items.history.current_without_rows
    assert current is not None
    assert current.label == "Riigikogus"
    assert current.started_on is None


def test_recording_the_missing_step_places_the_tail(specialist, organisation):
    """Test 13's other half: an unplaced row is **correctable**.

    The correction is the one that fixes a section rather than a row — recording
    the step that moved the file dates the phase, and everything inside it groups.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    opinion = _opinion(matter, specialist, organisation, date(2026, 5, 20))
    assert _phase_of(matter, specialist, opinion) == UNPLACED_KEY

    _step(matter, specialist, "Eelnõu jõudis Riigikokku", date(2026, 4, 1), PHASE_RIIGIKOGU)

    assert _phase_of(matter, specialist, opinion) == PHASE_RIIGIKOGU


def test_a_file_with_no_phase_anywhere_reads_exactly_as_it_always_has(specialist, organisation):
    """The whole register on the day this ships: no phases, and no headings.

    Nothing is backfilled, so every existing Matter has no placed row. A heading
    saying «none of this could be placed» above every row of every file would be
    the application announcing a gap nobody can close.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _opinion(matter, specialist, organisation, date(2026, 5, 20))

    items, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    assert items.history.grouped is False
    assert all(item.phase is None for item in items)
    assert all(item.opens_phase is False for item in items)


# ---------------------------------------------------------------------------
# §5 and §10 — guidance writes nothing
# ---------------------------------------------------------------------------


def test_reading_the_roadmap_writes_nothing_and_creates_no_work(specialist):
    """Test 17 and test 18. A projection with a side effect is not a projection."""
    from app.audit.models import ChangeEvent
    from app.workflow.models import NextAction

    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(matter, specialist, "VTK kooskõlastusringile", date(2026, 1, 9), PHASE_VTK, stage="idea")

    before_events = ChangeEvent.objects.filter(matter=matter).count()
    before_actions = NextAction.objects.filter(matter=matter).count()
    matter.refresh_from_db()
    before_track, before_stage = matter.track, matter.stage_id

    for _ in range(3):
        legal_process_rail(matter=matter, user=specialist)
        matter_timeline(matter=matter, user=specialist, limit=200)

    assert ChangeEvent.objects.filter(matter=matter).count() == before_events
    assert NextAction.objects.filter(matter=matter).count() == before_actions
    matter.refresh_from_db()
    assert matter.track == before_track == ""
    assert matter.stage_id == before_stage


def test_the_horizon_is_short_and_the_rest_is_behind_a_disclosure(specialist):
    """§10. One to three phases, and the remainder available rather than listed."""
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(matter, specialist, "VTK kooskõlastusringile", date(2026, 1, 9), PHASE_VTK, stage="idea")

    rail = legal_process_rail(matter=matter, user=specialist)
    assert rail.pattern.key == PATTERN_VTK
    assert len(rail.ahead) <= 3
    # Nothing in the horizon is dated, ticked or counted.
    assert all(node.state in {"possible", "unknown"} for node in rail.ahead)
    # **`VTK` is not on the horizon**, because the file recorded a step in it.
    # A phase it can prove it reached is not something that «may be ahead»,
    # however it sorts — and the rail says `Kirjas` on the same node.
    assert {node.label for node in rail.nodes if node.state == "recorded"} == {"VTK"}
    # And the whole remaining route is reachable without being on screen.
    assert [node.label for node in (*rail.ahead, *rail.ahead_rest)] == [
        "Kooskõlastusring",
        "Valitsuses",
        "Riigikogus",
        "Jõustumine",
    ]


# ---------------------------------------------------------------------------
# §12 — permission before grouping, and no leak through a heading
# ---------------------------------------------------------------------------


def test_a_restricted_step_leaks_no_heading_date_count_or_gap(specialist, reader, organisation):
    """Test 14, and the rule the whole module is scoped for.

    A `Menetluse areng` restricted below its Matter is the **anchor** of a phase,
    so it is the one record whose leakage would be structural rather than
    textual: a reader who may not see it must not learn of it from a section
    heading, from the day that heading is dated to, from a row count, or from a
    hole in the list where a section would have been.

    They see fewer sections, and nothing anywhere says that a section is missing.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    secret = _step(
        matter, specialist, "Valitsus arutas salajast versiooni", date(2026, 4, 1), PHASE_VALITSUS
    )
    secret.visibility_override = Visibility.RESTRICTED
    secret.save(update_fields=["visibility_override"])
    opinion = _opinion(matter, specialist, organisation, date(2026, 5, 2))

    # The owner sees the `Valitsuses` section the restricted step opened, and the
    # opinion sent after it belongs there.
    assert _labels(matter, specialist) == ["Valitsuses", "Kooskõlastusring"]
    assert _phase_of(matter, specialist, opinion) == PHASE_VALITSUS

    # The reader sees neither the step nor the section it opened — and the
    # opinion reads under the phase their own evidence supports, with no gap, no
    # `Valitsuses` heading and no 01.04 anywhere.
    assert "Valitsuses" not in _labels(matter, reader)
    assert _phase_of(matter, reader, opinion) == PHASE_KOOSKOLASTUS
    assert "salajast" not in _rendered_for(matter, reader)
    assert "2026-04-01" not in _rendered_for(matter, reader)
    assert "01.04.2026" not in _rendered_for(matter, reader)


def test_the_visible_current_stage_does_not_expose_the_record_that_caused_it(specialist, reader):
    """§12. `Hetkeseis` may be shown; the restricted step behind it may not.

    A restricted development that also moved the file leaves the Matter openly on
    `Riigikogus` — that column is the Matter's own and is not restricted. What
    must not follow it is the step's headline, its date, or a dated section
    announcing when the file got there.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    secret = _step(
        matter,
        specialist,
        "Eelnõu jõudis Riigikokku",
        date(2026, 6, 1),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )
    secret.visibility_override = Visibility.RESTRICTED
    secret.save(update_fields=["visibility_override"])

    rail = legal_process_rail(matter=matter, user=reader)
    assert rail.current_label == "Riigikogus"
    # For this reader the section exists only as «where the file is now», with no
    # date and no rows — never as a dated `Riigikogus` beginning on 01.06.
    items, _more = matter_timeline(matter=matter, user=reader, limit=200)
    current = items.history.current_without_rows
    assert current is not None
    assert current.label == "Riigikogus"
    assert current.started_on is None
    assert "Riigikokku" not in _rendered_for(matter, reader)


def test_a_deleted_matter_leaves_no_grouped_history_behind(specialist, signed_in):
    """Test 15. A tombstoned Matter's roadmap goes with the rest of it."""
    from app.matters.deletion import delete_matter

    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    delete_matter(matter=matter, actor=specialist)

    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 404
    body = response.content.decode()
    assert "Kooskõlastusring" not in body
    assert "Ees võib olla" not in body


# ---------------------------------------------------------------------------
# §14 — the grouping costs no queries of its own
# ---------------------------------------------------------------------------


def test_a_long_grouped_history_costs_no_query_per_row(specialist, organisation):
    """Test 20 and §14. Thirty-plus activities, repeated phases, flat cost.

    The grouping reads the list the projection has already built, so it must add
    no query at all — and a file with many times the history must not cost many
    times the queries. A phase heading that asked the database which phase it was
    would be one query per row by another name.
    """
    small = _matter(specialist, instruments=("seadus",))
    _step(small, specialist, "Kooskõlastusring", date(2024, 2, 1), PHASE_KOOSKOLASTUS)
    _opinion(small, specialist, organisation, date(2024, 3, 1))

    big = _matter(specialist, instruments=("vtk", "seadus"))
    phases = (PHASE_VTK, PHASE_KOOSKOLASTUS, PHASE_VALITSUS, PHASE_KOOSKOLASTUS, PHASE_RIIGIKOGU)
    day = date(2022, 1, 3)
    for index in range(35):
        day = day + timedelta(days=21)
        _step(big, specialist, f"Samm {index}", day, phases[index % len(phases)])
    for index in range(6):
        _opinion(big, specialist, organisation, date(2023, 2, 1) + timedelta(days=90 * index))

    with CaptureQueriesContext(connection) as few:
        matter_timeline(matter=small, user=specialist, limit=30)
    with CaptureQueriesContext(connection) as many:
        page, more = matter_timeline(matter=big, user=specialist, limit=30)

    assert len(page) == 30 and more is True
    assert page.history.grouped is True
    # Five recorded occurrences at least, and the repeated `Kooskõlastusring` is
    # two of them rather than one merged bucket.
    assert len({occurrence.index for occurrence in page.history.occurrences}) >= 5
    assert len(many.captured_queries) == len(few.captured_queries), (
        "grouping a long history must cost exactly what grouping a short one does"
    )


def test_a_section_running_past_the_fold_carries_its_own_heading(specialist):
    """§6. Pagination is unchanged, and a split section says so.

    «Näita varasemaid» swaps its own button for the next batch in place, so a
    phase longer than one page continues as siblings of the rows above it. The
    continuation carries the heading again, marked `jätkub`, because the
    alternative is rows reading under whichever heading happened to be last on
    the page above — which on a grouped history is a different phase.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Kooskõlastusring algas", date(2024, 1, 2), PHASE_KOOSKOLASTUS)
    day = date(2024, 1, 3)
    for index in range(25):
        day = day + timedelta(days=7)
        _step(matter, specialist, f"Samm {index}", day, PHASE_KOOSKOLASTUS)

    first, more = matter_timeline(matter=matter, user=specialist, limit=10)
    assert more is True
    assert first[0].opens_phase is True
    assert first[0].phase_continues is False

    second, _more = matter_timeline(matter=matter, user=specialist, limit=10, offset=10)
    assert second[0].opens_phase is True
    assert second[0].phase_continues is True
    assert second[0].phase.label == "Kooskõlastusring"


def test_a_recorded_step_marks_its_node_kirjas_even_with_no_stage_key(specialist):
    """The rail and the history must not contradict each other on one screen.

    `VTK` and `Koja ettepanek` map **no stage key** on purpose, so the stage
    history has nothing to say about them — and before this the browser showed
    the result plainly: a `VTK` section in `Teema käik` with two rows under it,
    and a rail reading `VTK · Teadmata` three inches above.

    A recorded step carrying a phase is an explicit statement by a person, and a
    more direct one than a column having been moved. Nothing is inferred here
    either: a step with no phase marks nothing.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(matter, specialist, "VTK saadeti kooskõlastusringile", date(2025, 2, 10), PHASE_VTK)
    _step(
        matter,
        specialist,
        "Eelnõu jõudis Riigikokku",
        date(2026, 2, 3),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )

    rail = legal_process_rail(matter=matter, user=specialist)
    states = {node.label: node.state for node in rail.nodes}
    assert states["VTK"] == "recorded"
    assert states["Riigikogus"] == "current"
    # And the history says the same thing, which is the whole point.
    assert "VTK" in _labels(matter, specialist)


def test_an_unplaced_step_marks_no_node(specialist):
    """A step nobody placed is evidence of nothing, and says so."""
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Midagi juhtus",
        occurred_on=date(2025, 2, 10),
        process_phase="",
    )
    _step(
        matter,
        specialist,
        "Eelnõu jõudis Riigikokku",
        date(2026, 2, 3),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )

    rail = legal_process_rail(matter=matter, user=specialist)
    states = {node.label: node.state for node in rail.nodes}
    assert states["VTK"] == "unknown"
    assert states["Kooskõlastusring"] == "unknown"


def test_a_restricted_step_marks_no_node_for_a_reader_who_may_not_see_it(specialist, reader):
    """The rail is scoped before it is drawn, like everything else on this page."""
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    secret = _step(matter, specialist, "VTK kooskõlastusringile", date(2025, 2, 10), PHASE_VTK)
    secret.visibility_override = Visibility.RESTRICTED
    secret.save(update_fields=["visibility_override"])
    _step(
        matter,
        specialist,
        "Eelnõu jõudis Riigikokku",
        date(2026, 2, 3),
        PHASE_RIIGIKOGU,
        stage="parliament",
    )

    owner_states = {
        n.label: n.state for n in legal_process_rail(matter=matter, user=specialist).nodes
    }
    reader_states = {n.label: n.state for n in legal_process_rail(matter=matter, user=reader).nodes}
    assert owner_states["VTK"] == "recorded"
    assert reader_states["VTK"] == "unknown"


# ---------------------------------------------------------------------------
# §9 — `Kirjas olevad kuupäevad`, and the one future date that had nowhere to go
# ---------------------------------------------------------------------------


def test_a_future_transposition_deadline_is_visible_and_drawn_as_future(specialist):
    """Test 2's second half, and the gap the `kind` column was added to close.

    A transposition deadline still ahead of us read **nowhere at all**: the
    chronology projects only what has happened, and the standing
    `Olulised tähtajad` section went with the approved target — so a directive's
    single most consequential future date was recorded and then invisible until
    the day it passed.

    It draws a column now, found by `kind` and never by its title, and the
    strip's own `ahead` grammar is what keeps it from reading as something that
    has already happened.
    """
    matter = _matter(specialist, instruments=("direktiiv",))
    ahead = timezone.localdate() + timedelta(days=400)
    add_important_date(
        matter=matter,
        title="Direktiivi ülevõtmise tähtaeg",
        date_value=ahead,
        period_end=ahead,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE.value,
        actor=specialist,
    )

    steps = process_steps(matter=matter, user=specialist)
    column = next(step for step in steps if step.label == TRANSPOSITION_DEADLINE_LABEL)
    assert column.state == STATE_AHEAD, "a deadline in 2027 must not read as reached"
    assert column.display == format_estonian_date(ahead)


def test_an_ordinary_future_deadline_still_draws_no_column(specialist):
    """§12.1's retirement stands. Only the one kind was added back, and only ahead."""
    matter = _matter(specialist, instruments=("seadus",))
    ahead = timezone.localdate() + timedelta(days=90)
    add_important_date(
        matter=matter,
        title="Komisjoni istung",
        date_value=ahead,
        period_end=ahead,
        actor=specialist,
    )

    labels = [step.label for step in process_steps(matter=matter, user=specialist)]
    assert TRANSPOSITION_DEADLINE_LABEL not in labels
    assert "Komisjoni istung" not in labels


def test_a_passed_transposition_deadline_reads_in_the_chronology_and_not_on_the_strip(
    specialist,
):
    """A deadline that has gone by is history, and history has one place.

    Drawing a column for it *and* a chronology row would be the duplication
    docs/adr/0074 §12.1 removed.
    """
    matter = _matter(specialist, instruments=("direktiiv",))
    gone = timezone.localdate() - timedelta(days=30)
    add_important_date(
        matter=matter,
        title="Direktiivi ülevõtmise tähtaeg",
        date_value=gone,
        period_end=gone,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE.value,
        actor=specialist,
    )

    labels = [step.label for step in process_steps(matter=matter, user=specialist)]
    assert TRANSPOSITION_DEADLINE_LABEL not in labels
    items, _more = matter_timeline(matter=matter, user=specialist, limit=200)
    assert any(
        item.milestone is not None and "ülevõtmise tähtaeg" in item.milestone.what for item in items
    )


def test_a_restricted_transposition_deadline_draws_no_column(specialist, reader):
    """Scoped before it is drawn, like every other source on this strip."""
    matter = _matter(specialist, instruments=("direktiiv",))
    ahead = timezone.localdate() + timedelta(days=400)
    record = add_important_date(
        matter=matter,
        title="Direktiivi ülevõtmise tähtaeg",
        date_value=ahead,
        period_end=ahead,
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE.value,
        actor=specialist,
    )
    record.visibility_override = Visibility.RESTRICTED
    record.save(update_fields=["visibility_override"])

    owner = [step.label for step in process_steps(matter=matter, user=specialist)]
    seen = [step.label for step in process_steps(matter=matter, user=reader)]
    assert TRANSPOSITION_DEADLINE_LABEL in owner
    assert TRANSPOSITION_DEADLINE_LABEL not in seen


def test_the_deadline_is_found_by_its_kind_and_never_by_its_title(specialist):
    """Prose matching would miss one wording and wrongly claim another."""
    matter = _matter(specialist, instruments=("direktiiv",))
    ahead = timezone.localdate() + timedelta(days=400)
    # Named for transposition, classified as something else: not a column.
    add_important_date(
        matter=matter,
        title="Ülevõtmise arutelu ministeeriumis",
        date_value=ahead,
        period_end=ahead,
        actor=specialist,
    )
    # Classified as transposition, named nothing like it: a column.
    add_important_date(
        matter=matter,
        title="Direktiivi rakendamise kuupäev",
        date_value=ahead + timedelta(days=1),
        period_end=ahead + timedelta(days=1),
        kind=ImportantDateKind.TRANSPOSITION_DEADLINE.value,
        actor=specialist,
    )

    columns = [
        step
        for step in process_steps(matter=matter, user=specialist)
        if step.label == TRANSPOSITION_DEADLINE_LABEL
    ]
    assert len(columns) == 1
    assert columns[0].detail == "Direktiivi rakendamise kuupäev"


def test_a_recorded_step_ahead_of_the_current_stage_stays_kirjas(specialist):
    """Two kinds of evidence, and only one of them can be a data-entry artefact.

    A `VTK` step on a file still sitting on `Idee` is an ordinary, correct file:
    the väljatöötamiskavatsus went out and nobody moved the column. Demoting it
    to `Võimalik` — which is right for a *stage* recorded and then corrected —
    would put `VTK · Võimalik` directly above a `VTK` section of the history
    holding the step, and one screen cannot say both.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(
        matter,
        specialist,
        "VTK saadeti kooskõlastusringile",
        date(2026, 4, 2),
        PHASE_VTK,
        stage="idea",
    )

    rail = legal_process_rail(matter=matter, user=specialist)
    states = {node.label: node.state for node in rail.nodes}
    assert states["Algus"] == "current"
    assert states["VTK"] == "recorded"
    assert states["Kooskõlastusring"] == "possible"
    # And the history agrees: a `VTK` section holding the step.
    assert "VTK" in _labels(matter, specialist)


def test_a_stage_recorded_ahead_and_then_corrected_is_still_demoted(specialist):
    """The 0092 §13 amendment is unchanged for the evidence it was written about.

    Somebody picked `Jõustunud` by mistake and moved the file back. Read
    literally, the stage history would draw `Jõustumine · Kirjas` beside
    `Kooskõlastusring · Praegu` and tell a reader the act is both in force and
    out for consultation.
    """
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    rail = legal_process_rail(matter=matter, user=specialist)
    states = {node.label: node.state for node in rail.nodes}
    assert states["Kooskõlastusring"] == "current"
    assert states["Jõustumine"] == "possible"


# ---------------------------------------------------------------------------
# §7 — the capture and correction controls, on their real routes
# ---------------------------------------------------------------------------


def test_the_marge_panel_offers_this_files_own_phases_preselected(signed_in, specialist):
    """§7. Proposed visibly, beside the date box, and only this file's procedure.

    A ministerial regulation is not offered `Riigikogus`, and a file already on
    `Kooskõlastusringil` opens with `Kooskõlastusring` chosen — so the ordinary
    save needs no answer and a backdated one is corrected by somebody who can see
    the word while they type the year.
    """
    matter = _matter(specialist, instruments=("maarus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    panel = body[body.index('id="id_marge_process_phase"') :][:1200]
    assert 'value="kooskolastus" selected' in panel
    assert "Etapp määramata" in panel
    # The pattern's own phases, and the one a regulation never reaches is absent.
    assert 'value="riigikogu"' not in panel
    assert 'value="valitsus"' in panel


def test_the_correction_form_opens_on_the_records_own_phase(signed_in, specialist):
    """`Muuda etappi`, through the edit pattern that already exists.

    It opens on **what the record stores**, never on the file's current phase:
    reopening a step filed under `VTK` on today's `Riigikogus` would be one
    `Salvesta` away from silently re-filing it. Reached through the real route,
    because the field being *declared* on the form proves nothing about the
    Matter page having handed the form its pattern.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    step = _step(matter, specialist, "VTK kooskõlastusringile", date(2025, 2, 10), PHASE_VTK)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    body = signed_in.get(
        reverse(
            "matters:update_development",
            kwargs={"pk": matter.pk, "development_id": step.pk},
        )
    ).content.decode()
    assert "process_phase" in body
    marker = f"id_menetluse_areng_{step.pk}_process_phase"
    panel = body[body.index(marker) :][:1200]
    assert 'value="vtk" selected' in panel
    assert 'value="riigikogu" selected' not in panel


def test_a_matter_with_no_procedure_is_offered_no_phase_control_at_all(signed_in, specialist):
    """No pattern, no vocabulary to answer from, no field.

    Removed rather than rendered empty, so a crafted `process_phase=riigikogu`
    on such a Matter reaches a form that never cleaned it and is discarded.
    """
    matter = _matter(specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert 'id="id_marge_process_phase"' not in body

    signed_in.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        # `auto_id` prefixes the *ids*, never the names: the field is posted as
        # `process_phase`, which is what a crafted request would send.
        {"title": "Midagi juhtus", "process_phase": "riigikogu"},
    )
    development = matter.procedural_developments.get()
    assert development.process_phase == ""


def test_a_crafted_phase_outside_the_vocabulary_stores_nothing(specialist):
    """A presentation association may never block a business write.

    A key this product does not know — crafted, or retired by a later vocabulary
    — places nothing and is not an error. The record still saves.
    """
    development = add_procedural_development(
        matter=_matter(specialist, instruments=("seadus",)),
        author=specialist,
        title="Midagi juhtus",
        occurred_on=date(2026, 1, 9),
        process_phase="riigikohus",
    ).record

    assert development.pk is not None
    assert development.process_phase == ""


# ---------------------------------------------------------------------------
# §6 and §12 — nothing is lost, and a phase correction corrects one thing
# ---------------------------------------------------------------------------


def test_grouping_reorders_the_history_and_never_loses_a_row(specialist, organisation):
    """§6. Every kind of chronology content survives being grouped.

    Grouping lifts the flat list one level; it adds nothing, drops nothing and
    de-duplicates nothing. A grouping that lost a row would be a history that
    lost a fact — so the grouped page is asserted to be a **permutation** of the
    ungrouped one, over a Matter carrying one of everything the chronology draws.
    """
    from app.matters.workspace import add_matter_engagement, add_matter_external_position

    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    _opinion(matter, specialist, organisation, date(2026, 2, 14))
    add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        occurred_on=date(2026, 1, 20),
    )
    add_matter_external_position(
        matter=matter,
        author=specialist,
        organisation=organisation,
        summary="Ministeerium ei nõustu",
        stated_on=date(2026, 2, 1),
    )
    gone = timezone.localdate() - timedelta(days=5)
    add_important_date(
        matter=matter, title="Komisjoni istung", date_value=gone, period_end=gone, actor=specialist
    )
    add_effective_date(
        matter=matter, date_value=gone, period_end=gone, description="Põhiosa", actor=specialist
    )

    grouped, _more = matter_timeline(matter=matter, user=specialist, limit=200)

    # The same Matter with its one explicit phase cleared, which is the state
    # every file in the register is in on the day this ships — and therefore the
    # flat list this has to be a permutation of. The same Matter rather than a
    # second one, because `sort_key` is the record's own primary key.
    matter.procedural_developments.update(process_phase="")
    ungrouped, _more = matter_timeline(matter=matter, user=specialist, limit=200)

    assert grouped.history.grouped is True
    assert ungrouped.history.grouped is False

    def identity(page):
        return sorted((item.item_type, item.sort_key) for item in page)

    assert identity(grouped) == identity(ungrouped)
    assert len(grouped) == len(ungrouped)


def test_correcting_a_phase_changes_the_phase_and_nothing_else(specialist, organisation):
    """§12. A correction may not silently move anything it was not asked to.

    Not the Matter's `Hetkeseis`, not the record's own business date, not the
    opinion that was sent, not the open step, and not the evidence attached to
    the row.
    """
    from app.matters.services import correct_procedural_development
    from app.workflow.models import NextAction

    matter = _matter(specialist, instruments=("vtk", "seadus"))
    step = _step(
        matter,
        specialist,
        "VTK saadeti kooskõlastusringile",
        date(2025, 2, 10),
        PHASE_VTK,
        stage="consultation",
    )
    opinion = _opinion(matter, specialist, organisation, date(2025, 3, 5))
    matter.refresh_from_db()
    before = (matter.stage_id, matter.disposition, matter.closed_at)
    actions = list(NextAction.objects.filter(matter=matter).values_list("pk", "status"))

    corrected = correct_procedural_development(
        development=step,
        title=step.title,
        occurred_on=step.occurred_on,
        occurred_on_precision=step.occurred_on_precision,
        note=step.note,
        process_phase=PHASE_KOOSKOLASTUS,
        actor=specialist,
        expected_revision=step.revision_token,
    )

    assert corrected.process_phase == PHASE_KOOSKOLASTUS
    # The record's own business date is untouched …
    assert corrected.occurred_on == date(2025, 2, 10)
    # … the Matter is where it was …
    matter.refresh_from_db()
    assert (matter.stage_id, matter.disposition, matter.closed_at) == before
    # … the opinion was not resent, withdrawn or moved …
    opinion.refresh_from_db()
    assert opinion.sent_at is not None
    # … and no step was created, completed or superseded.
    assert list(NextAction.objects.filter(matter=matter).values_list("pk", "status")) == actions

    # And the history moved, which is the one thing the correction was for.
    assert _phase_of(matter, specialist, step) == PHASE_KOOSKOLASTUS
