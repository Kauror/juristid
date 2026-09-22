"""The lawyers' eight worked examples, read off `Menetluse kulg` and one list.

Sections 3 and 13 of the brief. Every scenario here is one the department wrote
down, rebuilt from canonical records through the ordinary services, and read back
through the ordinary projection. The dates are this file's own — the source's
were illustrative, and copying an accidentally reversed pair and then teaching
the renderer to reproduce it would be encoding a typo as a requirement.

**`Teema käik` no longer groups itself into phases** (docs/adr/0105 §1). It drew a
heading per recorded phase, with `Etapiga sidumata` at its foot holding everything
nobody had placed, and the cost was that the list stopped being chronological —
the newest phase, then an older one, then a group filed under no phase at all. The
phases were already drawn, in order and with their dates, in `Menetluse kulg`
inches above. So the scenarios below are read where each answer now lives: the
rail says where the procedure is and which phases the file recorded, and the
chronology says what happened, in the order it happened.

What is being protected, in one list:

* the rail shows **only what the records support** — no phase marked because a
  later one exists, and no phase marked by a bare `Hetkeseis` edit;
* guidance may show **unrecorded future phases**, undated and labelled possible,
  and writes nothing;
* a phase **occurs more than once**, and the rail says so once rather than
  claiming two different states for one node;
* `Teema käik` is **one chronological list** — every row in business-date order,
  no headings, no `Etapiga sidumata`, and the same list after «Näita varasemaid»;
* a restricted record leaks **no row, no date, no count and no gap**.
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
from app.matters.legal_process import (
    legal_process_rail,
    phase_context,
    recorded_phase_keys,
)
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


def _rail_states(matter, user) -> dict[str, str]:
    """Each rail node's state, keyed by its label.

    The rail is where a phase is read now, so most of the scenarios below assert
    here rather than on the chronology (docs/adr/0105 §1).
    """
    rail = legal_process_rail(matter=matter, user=user)
    assert rail is not None, "this file's instruments should choose a pattern"
    return {node.label: node.state for node in rail.nodes}


def _recorded(matter, user) -> list[str]:
    """The phase keys this file recorded a step in, as this reader may see them.

    The canonical column rather than a node *state*: the node the file is standing
    on reads `Praegu`, which answers a different question — a phase can be both
    recorded and current, and asking the rail's state would call that one «not
    recorded» (`app.matters.legal_process.recorded_phase_keys`).
    """
    return sorted(recorded_phase_keys(matter=matter, user=user))


def _node_labels(matter, user) -> list[str]:
    """Every node the rail draws, in order."""
    rail = legal_process_rail(matter=matter, user=user)
    assert rail is not None, "this file's instruments should choose a pattern"
    return [node.label for node in rail.nodes]


def _record_rows(matter, user) -> list[object]:
    """The chronology rows standing for a canonical record, in reading order.

    Filtered to the records the scenario wrote, so an assertion about ordering is
    not also an assertion about which audit events happen to project a row.
    """
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    return [item for item in items if item.record is not None]


def _headlines(matter, user) -> list[str]:
    """What each canonical row's headline says, in the order the page reads them."""
    return [row.milestone.what for row in _record_rows(matter, user) if row.milestone is not None]


def _is_chronological(matter, user) -> bool:
    """Whether every row on the page sits after the row below it in time.

    The claim docs/adr/0105 §1 makes about this list, and the one the grouping
    could not keep: rows arrived newest-*phase* first, so a September row could
    read above a row from the following spring.
    """
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    stamps = [(item.occurred_at, item.created_at, item.sort_key) for item in items]
    return stamps == sorted(stamps, reverse=True)


def _rendered_for(matter, user) -> str:
    """Everything a reader could read off the chronology, as one string.

    The permission assertions have to prove a *negative* — that no headline, no
    sub-line and no date anywhere on the page mentions the record this reader may
    not see.
    """
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    parts: list[str] = []
    for item in items:
        if item.milestone is not None:
            parts += [item.milestone.what, item.milestone.display_date, item.milestone.sub]
        parts.append(item.summary_sentence or "")
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# §3 A — a VTK, and then a bill
# ---------------------------------------------------------------------------


def test_scenario_a_a_vtk_followed_by_a_bill(specialist, organisation):
    """The full example, read where each of its two questions now lives.

    `Menetluse kulg` says the file recorded four phases and is in the Riigikogu;
    `Teema käik` says what happened, all six rows, newest first and nothing
    between them. Neither of them answers the other's question, which is what
    docs/adr/0105 §1 settled after a round of the history trying to answer both.
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

    # The rail: four phases the file can prove it was in, and where it is now.
    states = _rail_states(matter, specialist)
    assert states["VTK"] == "recorded"
    assert states["Kooskõlastusring"] == "recorded"
    assert states["Valitsuses"] == "recorded"
    # `Riigikogus` is both recorded *and* where the file is, and the node says the
    # second: `Praegu` is the more specific answer and the one a reader needs.
    assert states["Riigikogus"] == "current"
    assert _recorded(matter, specialist) == sorted(
        [PHASE_VTK, PHASE_KOOSKOLASTUS, PHASE_VALITSUS, PHASE_RIIGIKOGU]
    )

    # The chronology: one list, newest first, both opinions in their own place in
    # time rather than in a bucket under a heading.
    assert _is_chronological(matter, specialist)
    assert _headlines(matter, specialist) == [
        "Märge: Riigikogu võttis seaduse vastu",
        "Märge: Valitsus saatis eelnõu Riigikogule",
        "Arvamus välja",
        "Märge: Eelnõu saadeti kooskõlastusringile",
        "Arvamus välja",
        "Märge: VTK saadeti kooskõlastusringile",
    ]
    rows = {row.record.pk: index for index, row in enumerate(_record_rows(matter, specialist))}
    # The VTK round's answer reads below the bill's, because it is older — and
    # the file's own `Hetkeseis` moved neither of them.
    assert rows[bill_opinion.pk] < rows[vtk_opinion.pk]


def test_a_repeated_phase_keeps_both_rounds_readable(specialist):
    """§8. Kooskõlastusring → Valitsuses → Kooskõlastusring, and neither is lost.

    The rail is a pattern and has one node per phase, so a round that happened
    twice marks that node once — a second `Kooskõlastusring` node would be the
    rail claiming the procedure has two of them. What must survive is the pair of
    *rounds*, and they survive where rounds are: two rows in the chronology, in
    the order they happened, with the Government step between them.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Esimene kooskõlastusring", date(2026, 1, 10), PHASE_KOOSKOLASTUS)
    _step(matter, specialist, "Valitsus arutas", date(2026, 3, 1), PHASE_VALITSUS)
    _step(matter, specialist, "Teine kooskõlastusring", date(2026, 5, 4), PHASE_KOOSKOLASTUS)

    assert _recorded(matter, specialist) == sorted([PHASE_KOOSKOLASTUS, PHASE_VALITSUS])
    assert _headlines(matter, specialist) == [
        "Märge: Teine kooskõlastusring",
        "Märge: Valitsus arutas",
        "Märge: Esimene kooskõlastusring",
    ]


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

    assert _recorded(matter, specialist) == sorted([PHASE_KOOSKOLASTUS, PHASE_VALITSUS])
    # A bill with no `VTK` instrument is not offered a VTK node at all: the
    # pattern is the road this file could take, and that one is not on it.
    assert "VTK" not in _node_labels(matter, specialist)


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

    assert _recorded(matter, specialist) == [PHASE_RIIGIKOGU]
    # Nothing earlier is claimed, and nothing earlier is *ticked*: `Teadmata` is
    # the honest answer for a phase Koda either did not see or nobody wrote down.
    states = _rail_states(matter, specialist)
    for absent in ("Kooskõlastusring", "Valitsuses"):
        assert states[absent] == "unknown"

    # And what may still follow reads from where the file actually is, rather
    # than from the beginning of a procedure Koda never saw: the rail draws
    # commencement as possible, undated (docs/adr/0099 §2).
    rail = legal_process_rail(matter=matter, user=specialist)
    assert [node.label for node in rail.nodes if node.state == "possible"] == ["Jõustumine"]


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

    assert _recorded(government, specialist) == sorted([PHASE_KOOSKOLASTUS, PHASE_VALITSUS])
    assert _recorded(ministerial, specialist) == sorted([PHASE_JOUSTUMINE, PHASE_KOOSKOLASTUS])
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

    assert _recorded(matter, specialist) == sorted([PHASE_KOJA_ETTEPANEK, PHASE_KOOSKOLASTUS])
    # The ministry's answer is filed under the proposal's own phase, which is what
    # marks that node — and it reads in the chronology between the two steps
    # either side of it, because that is when it happened.
    assert vastus.process_phase == PHASE_KOJA_ETTEPANEK
    assert _headlines(matter, specialist) == [
        "Märge: Kooskõlastusringile tuli eelnõu, kus ettepanekut käsitletakse",
        "Märge: Ministeeriumi vastus",
        "Märge: Koja ettepanek",
    ]


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
    # Not a node, not a state, and not a claim that the procedure ended: what
    # is still ahead on the rail is exactly what it was.
    assert "Koda" not in " ".join(node.label for node in rail.nodes)
    assert [node.label for node in rail.nodes if node.state == "possible"], (
        "Koda stopping must not empty what is still ahead"
    )


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
    # `nodes` is the whole drawn pattern — there is no second, shorter list to
    # check since docs/adr/0099 §2 — so this is the claim in full.
    assert "Ülevõtmine" not in [node.label for node in rail.nodes]


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

    # The mistake marked no node. `Jõustumine` reads as something that may still
    # happen, not as something that did — a bare `Hetkeseis` edit proves a value
    # was recorded, and its only date is this application's own clock.
    states = _rail_states(matter, specialist)
    assert states["Jõustumine"] == "possible"
    assert _recorded(matter, specialist) == [PHASE_KOOSKOLASTUS]

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


def test_a_backdated_opinion_reads_on_its_own_date(specialist, organisation):
    """Test 8. Entry time places nothing.

    An opinion sent during the consultation round and typed up months later, once
    the file has moved on, reads **where its own date puts it** — between the two
    steps either side of it — and not at the top of the list where it was typed.
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

    assert _is_chronological(matter, specialist)
    assert _headlines(matter, specialist) == [
        "Märge: Eelnõu jõudis Riigikokku",
        "Arvamus välja",
        "Märge: Eelnõu kooskõlastusringile",
    ]
    assert _record_rows(matter, specialist)[1].record.pk == opinion.pk


def test_same_day_records_read_the_same_way_whatever_order_they_were_typed(
    specialist, organisation
):
    """Test 12. The list is ordered on business dates, and typing order breaks no tie.

    Two records sharing a day, entered in either order, reach the same reading
    order — and the whole list is still descending, so neither of them jumps
    above a newer row for having been typed later.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    _step(matter, specialist, "Valitsus arutas", date(2026, 4, 1), PHASE_VALITSUS)
    # Dated the same day as the Government step above it.
    _opinion(matter, specialist, organisation, date(2026, 4, 1))

    assert _is_chronological(matter, specialist)
    forward = _headlines(matter, specialist)

    # The same facts recorded in the other order reach the same reading order.
    mirror = _matter(specialist, instruments=("seadus",))
    _opinion(mirror, specialist, organisation, date(2026, 4, 1))
    _step(mirror, specialist, "Valitsus arutas", date(2026, 4, 1), PHASE_VALITSUS)
    _step(mirror, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)

    assert _is_chronological(mirror, specialist)
    assert sorted(_headlines(mirror, specialist)) == sorted(forward)
    assert _headlines(mirror, specialist)[-1] == "Märge: Eelnõu kooskõlastusringile"


def test_an_undated_record_reads_in_full_and_says_the_day_is_unknown(specialist):
    """Test 9 and test 13. «Kuupäev teadmata» is not «file it under today».

    A development whose date nobody knows still reads, in full, with its own
    headline — and the date cell says the day is unknown rather than printing the
    day somebody typed it in. There is nowhere for such a row to be filed *away*
    to any more, which is one of the things docs/adr/0105 §1 removed.
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

    headlines = _headlines(matter, specialist)
    assert "Märge: Ministeerium saatis uue versiooni" in headlines
    row = next(
        item
        for item in _record_rows(matter, specialist)
        if item.milestone is not None and "uue versiooni" in item.milestone.what
    )
    assert row.milestone.display_date == "Kuupäev teadmata"
    # And it marked no node: a step nobody placed is evidence of nothing.
    assert _recorded(matter, specialist) == [PHASE_KOOSKOLASTUS]


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
    assert "aprill" in row.milestone.display_date
    # The anchor sorts the row; the period is what it prints. `Valitsuses` is
    # marked on the rail from the record's own column either way.
    assert _recorded(matter, specialist) == sorted([PHASE_KOOSKOLASTUS, PHASE_VALITSUS])


# ---------------------------------------------------------------------------
# One list — docs/adr/0105 §1
# ---------------------------------------------------------------------------


def test_the_chronology_is_one_chronological_list_whatever_the_phases_say(specialist, organisation):
    """The claim the grouping could not keep.

    A file with a recorded round, a `Hetkeseis` that moved without a step to date
    it, and an opinion sent afterwards. Under the grouping the opinion fell into
    `Etapiga sidumata` and read at the **foot** of the page, below the January
    step that happened four months before it. Now every row sits where its date
    puts it and the list is one list.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    _opinion(matter, specialist, organisation, date(2026, 5, 20))

    assert _is_chronological(matter, specialist)
    assert _headlines(matter, specialist) == [
        "Arvamus välja",
        "Märge: Eelnõu kooskõlastusringile",
    ]
    # The file's own phase is still readable — on the rail, which is where the
    # question «where is this procedure» belongs.
    assert _rail_states(matter, specialist)["Riigikogus"] == "current"


def test_no_phase_heading_and_no_etapiga_sidumata_reach_the_page(
    signed_in, specialist, organisation
):
    """Read off the rendered page, because that is where the headings used to be.

    Both halves matter. `Etapiga sidumata` is gone as a word — it was the heading
    a reader took for a queue of records somebody owed work on — and so is the
    markup that drew any phase heading at all, so a file with several recorded
    rounds gets one list rather than a stack of sections.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(matter, specialist, "VTK kooskõlastusringile", date(2025, 2, 10), PHASE_VTK)
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2025, 9, 1), PHASE_KOOSKOLASTUS)
    _opinion(matter, specialist, organisation, date(2025, 9, 30))
    add_procedural_development(
        matter=matter,
        author=specialist,
        title="Midagi juhtus, kuupäev teadmata",
        occurred_on=None,
        process_phase="",
    )

    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    body = response.content.decode()

    assert "Etapiga sidumata" not in body
    assert "uxtl__phase" not in body
    # Every row is still there, the undated one included.
    assert "Midagi juhtus, kuupäev teadmata" in body
    assert "VTK kooskõlastusringile" in body
    # And the phases are drawn where they belong, one section up.
    assert "Menetluse kulg" in body


def test_recording_the_missing_step_marks_the_node_it_proves(specialist, organisation):
    """Test 13's other half, on the surface that still answers it.

    A `Hetkeseis` naming `Riigikogus` with no step behind it marks nothing: the
    file demonstrably moved and nothing says when or on whose record. One
    `+ Märge` filed under that phase is the statement by a person that marks it.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    _opinion(matter, specialist, organisation, date(2026, 5, 20))

    assert _recorded(matter, specialist) == [PHASE_KOOSKOLASTUS]

    _step(matter, specialist, "Eelnõu jõudis Riigikokku", date(2026, 4, 1), PHASE_RIIGIKOGU)

    assert _recorded(matter, specialist) == sorted([PHASE_KOOSKOLASTUS, PHASE_RIIGIKOGU])


def test_a_file_with_no_phase_anywhere_reads_as_one_list(specialist, organisation):
    """The whole register on the day this ships: no recorded phase anywhere.

    Nothing is backfilled, so most Matters have no placed row at all — and the
    chronology reads exactly the same for them as for a file with four recorded
    rounds, because it no longer asks.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _opinion(matter, specialist, organisation, date(2026, 5, 20))

    assert _is_chronological(matter, specialist)
    assert _headlines(matter, specialist) == ["Arvamus välja"]
    assert _recorded(matter, specialist) == []


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


def test_a_recorded_phase_is_never_drawn_as_one_that_may_be_ahead(specialist):
    """What §10 rationed is simply all there now; the other half still holds.

    §10 asked for one to three phases and the remainder behind a disclosure,
    because a *second* list of six speculative steps beside the rail read as a
    schedule. With that list gone the rail is the only rendering and it shows
    the route in full (docs/adr/0099 §2) — so what has to survive is the claim
    the horizon was also making: a phase the file can prove it reached is not
    something that «may be ahead», however the pattern sorts it.
    """
    matter = _matter(specialist, instruments=("vtk", "seadus"))
    _step(matter, specialist, "VTK kooskõlastusringile", date(2026, 1, 9), PHASE_VTK, stage="idea")

    rail = legal_process_rail(matter=matter, user=specialist)
    assert rail.pattern.key == PATTERN_VTK
    # The file reads as being at the beginning, and the one phase it can prove
    # it reached sorts *after* that — and is still not drawn as a possibility.
    assert {node.label for node in rail.nodes if node.state == "recorded"} == {"VTK"}
    assert {node.label for node in rail.nodes if node.state == "current"} == {"Algus"}
    # The remaining route is on the rail rather than behind a disclosure, and
    # every step of it is merely possible …
    assert [node.label for node in rail.nodes if node.state == "possible"] == [
        "Kooskõlastusring",
        "Valitsuses",
        "Riigikogus",
        "Jõustumine",
    ]
    # … which accounts for the whole rail: nothing else is drawn as reached.
    assert len(rail.nodes) == 6


# ---------------------------------------------------------------------------
# §12 — permission before everything, and no leak through the rail
# ---------------------------------------------------------------------------


def test_a_restricted_step_leaks_no_row_date_count_or_node(specialist, reader, organisation):
    """Test 14, and the rule the whole module is scoped for.

    A `Menetluse areng` is what marks a node on `Menetluse kulg`, so it is the one
    record whose leakage would be structural rather than textual: a reader who may
    not see it must not learn of it from a marked node, from its headline, from
    its date, from a row count, or from a hole in the list where a row would have
    been.

    They see one row fewer and one node fewer, and nothing anywhere says so.
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Eelnõu kooskõlastusringile", date(2026, 1, 9), PHASE_KOOSKOLASTUS)
    secret = _step(
        matter, specialist, "Valitsus arutas salajast versiooni", date(2026, 4, 1), PHASE_VALITSUS
    )
    secret.visibility_override = Visibility.RESTRICTED
    secret.save(update_fields=["visibility_override"])
    opinion = _opinion(matter, specialist, organisation, date(2026, 5, 2))

    # The owner sees the restricted step, its row and the node it marks.
    assert _recorded(matter, specialist) == sorted([PHASE_KOOSKOLASTUS, PHASE_VALITSUS])
    assert "Märge: Valitsus arutas salajast versiooni" in _headlines(matter, specialist)

    # The reader sees neither the step nor its node — and the opinion still reads
    # where its own date puts it, with no gap, no `Valitsuses` marked and no 01.04
    # anywhere.
    assert _recorded(matter, reader) == [PHASE_KOOSKOLASTUS]
    assert _headlines(matter, reader) == ["Arvamus välja", "Märge: Eelnõu kooskõlastusringile"]
    assert _record_rows(matter, reader)[0].record.pk == opinion.pk
    assert "salajast" not in _rendered_for(matter, reader)
    assert "2026-04-01" not in _rendered_for(matter, reader)
    assert "01.04.2026" not in _rendered_for(matter, reader)


def test_the_visible_current_stage_does_not_expose_the_record_that_caused_it(specialist, reader):
    """§12. `Hetkeseis` may be shown; the restricted step behind it may not.

    A restricted development that also moved the file leaves the Matter openly on
    `Riigikogus` — that column is the Matter's own and is not restricted. What
    must not follow it is the step's headline, its date, or a `Kirjas` marking
    saying the file's arrival there is on the record.
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
    # For this reader `Riigikogus` is only «where the file is now». It is not
    # `Kirjas`, because the record that would prove it is one they may not see —
    # and neither the step's headline nor its date reaches the page.
    assert _rail_states(matter, reader)["Riigikogus"] == "current"
    assert _recorded(matter, reader) == [PHASE_KOOSKOLASTUS]
    assert "Riigikokku" not in _rendered_for(matter, reader)
    assert "01.06.2026" not in _rendered_for(matter, reader)


def test_a_deleted_matter_leaves_no_history_behind(specialist, signed_in):
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
# §14 — a long history costs no query per row
# ---------------------------------------------------------------------------


def test_a_long_history_costs_no_query_per_row(specialist, organisation):
    """Test 20 and §14. Thirty-plus activities, flat cost.

    A file with many times the history must not cost many times the queries: a row
    that asked the database anything of its own would be an N+1 by another name.
    The budget is asserted against a short file rather than against a number, so it
    holds while the page's own fixed cost changes.
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
    assert len(many.captured_queries) == len(few.captured_queries), (
        "a long history must cost exactly what a short one does"
    )


def test_the_next_page_continues_the_same_list(specialist):
    """§6. «Näita varasemaid» carries on down one list, with nothing between.

    The continuation swaps its own button for the next batch *in place* inside
    `#ajalugu-loend`, so the older rows arrive as siblings of the ones above them.
    That used to mean a phase heading had to be repeated on the next page, marked
    `jätkub`; with one list there is nothing to repeat, and what has to hold is
    that the pages do not overlap, skip or reorder (docs/adr/0105 §1).
    """
    matter = _matter(specialist, instruments=("seadus",))
    _step(matter, specialist, "Kooskõlastusring algas", date(2024, 1, 2), PHASE_KOOSKOLASTUS)
    day = date(2024, 1, 3)
    for index in range(25):
        day = day + timedelta(days=7)
        _step(matter, specialist, f"Samm {index}", day, PHASE_KOOSKOLASTUS)

    first, more = matter_timeline(matter=matter, user=specialist, limit=10)
    assert more is True
    second, _more = matter_timeline(matter=matter, user=specialist, limit=10, offset=10)

    def keys(page):
        return [(item.occurred_at, item.created_at, item.sort_key) for item in page]

    # One descending run across the fold, and no row on both pages.
    joined = keys(first) + keys(second)
    assert joined == sorted(joined, reverse=True)
    assert len(set(joined)) == len(joined)


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

    states = _rail_states(matter, specialist)
    assert states["VTK"] == "recorded"
    assert states["Riigikogus"] == "current"
    # And the step it is marked from reads in the chronology, which is the whole
    # point: one screen, two sections, no contradiction.
    assert "Märge: VTK saadeti kooskõlastusringile" in _headlines(matter, specialist)


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


def test_an_ordinary_future_deadline_draws_a_column_under_its_own_name(specialist):
    """§12.1's retirement is now narrower than it was, and deliberately.

    It said a watched expectation is not a procedural act, which is true, and
    concluded that it draws no column — with one kind added back later. The
    conclusion held only because the sentence «and a *past* one already reads
    in the chronology» was doing the work: a *future* one read in the
    chronology either, so on the Matter that holds it, it read nowhere at all.

    So the rule is now the one §12.1 was reaching for: a deadline that has
    passed is history and draws no column, and one still ahead is part of what
    this file is heading into and draws one. The transposition kind keeps its
    short vocabulary name; every other is named by the lawyer who recorded it,
    because on those the name is the whole of the information (QA-001).
    """
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
    assert "Komisjoni istung" in labels


def test_a_passed_ordinary_deadline_still_draws_no_column(specialist):
    """The half of §12.1 that was always right, kept."""
    matter = _matter(specialist, instruments=("seadus",))
    gone = timezone.localdate() - timedelta(days=90)
    add_important_date(
        matter=matter,
        title="Möödunud istung",
        date_value=gone,
        period_end=gone,
        actor=specialist,
    )

    labels = [step.label for step in process_steps(matter=matter, user=specialist)]
    assert "Möödunud istung" not in labels


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

    states = _rail_states(matter, specialist)
    assert states["Algus"] == "current"
    assert states["VTK"] == "recorded"
    assert states["Kooskõlastusring"] == "possible"
    # And the step it is marked from reads in the chronology.
    assert "Märge: VTK saadeti kooskõlastusringile" in _headlines(matter, specialist)


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


def test_every_kind_of_content_reads_in_one_date_order(specialist, organisation):
    """§6. One list, over a Matter carrying one of everything the chronology draws.

    A step, a sent opinion, a consultation, somebody else's position, a reached
    deadline and a commencement — six different tables projecting six rows, and the
    order across all of them is the one order there is. This is the assertion the
    grouping made impossible to state: the rows used to arrive newest-phase-first,
    so «is the whole list descending» had no answer (docs/adr/0105 §1).

    It also guards what the grouping's own permutation test guarded — that the
    phase a record carries changes nothing about *whether* its row is drawn. The
    list is read twice, once with the explicit phase set and once with it cleared,
    and it has to be the same list.
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

    placed, _more = matter_timeline(matter=matter, user=specialist, limit=200)

    # The same Matter with its one explicit phase cleared, which is the state every
    # file in the register is in on the day this ships. The same Matter rather than
    # a second one, because `sort_key` is the record's own primary key.
    matter.procedural_developments.update(process_phase="")
    unplaced, _more = matter_timeline(matter=matter, user=specialist, limit=200)

    def identity(page):
        return [(item.item_type, item.sort_key) for item in page]

    # Same rows, same order, whatever the phase column says.
    assert identity(placed) == identity(unplaced)
    assert _is_chronological(matter, specialist)
    # And all six kinds are there, none of them folded into another.
    assert len(placed) >= 6


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

    # And the rail moved, which is the one thing the correction was for: `VTK` is
    # no longer `Kirjas` and `Kooskõlastusring` is.
    assert _recorded(matter, specialist) == [PHASE_KOOSKOLASTUS]


def test_the_ordinary_register_row_reads_as_one_list_and_a_current_node(specialist, organisation):
    """The commonest file in the register, and the one the grouping got wrong first.

    A Matter carrying an `Õigusakt` **and** a `Hetkeseis` and no recorded step is
    not a special case — it is nearly every row. For a while the current phase
    alone made the page grouped, so every one of those files put its whole history
    under an `Etapiga sidumata` heading with a sentence explaining itself, and
    `teema-ajajoon` grew 65 px on the seeded world. There is no heading to get
    wrong now: `Hetkeseis` is answered by the rail's current node, and the
    chronology is the rows.
    """
    matter = _matter(specialist, instruments=("seadus",))
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    _opinion(matter, specialist, organisation, date(2026, 5, 20))

    assert _headlines(matter, specialist) == ["Arvamus välja"]
    assert _recorded(matter, specialist) == []
    assert _rail_states(matter, specialist)["Kooskõlastusring"] == "current"
