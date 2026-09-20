"""The presentation phases a proceeding's history is read in, and the patterns
that say what may come next.

Two things live here because they are one vocabulary used twice:

* `Teema käik` groups what actually happened into **phase occurrences** —
  `VTK`, `Kooskõlastusring`, `Valitsuses`, `Riigikogus`, `Jõustumine` — so that a
  lawyer reads a year of a file in the shape the procedure actually had rather
  than as one flat list of dates.
* `Menetluse kulg` draws the same vocabulary as a **pattern**: where the file is
  now, and which one to three phases may plausibly follow.

They must be one vocabulary. Two would mean a section headed `Kooskõlastusring`
sitting under a rail node called `Kooskõlastus`, and a reader working out for
themselves whether those are the same thing.

A phase is a **presentation** concept and never a `Hetkeseis`
-------------------------------------------------------------
`StageVocabulary` says where the external procedure stands *now*, it is answered
by a person, and it has ten reviewed values. A phase is which section of the
history an act belongs to, it is decided per act, and the same phase occurs more
than once on an ordinary file. The two are related — a phase names the stage keys
that place a file on it — and they are not the same question:

* **`VTK` is not another spelling of `Idee`.** A väljatöötamiskavatsus is a
  document with its own consultation round, and a file can be on `Idee` without
  one ever existing. `vtk` therefore maps **no stage key** and is reached only by
  a recorded act.
* **`Kooskõlastusring` happens more than once.** A VTK's own round and the bill's
  later round are two occurrences of one phase, and the lawyers' first example is
  precisely a file that has both. Grouping by phase *key* would merge them; this
  module groups by **occurrence**.
* **`Jõustumise ootel` and `Jõustunud` share the `Jõustumine` phase** and are not
  the same answer. The distinction rides on the canonical stage label, exactly as
  it does on the rail today (`app/matters/legal_process.py` `ProcessNode`).

Nothing here is a workflow engine
---------------------------------
No state machine, no transition table, no configurable nodes, no drag-and-drop
and no `WorkflowStep` row. A pattern is a reviewed reading of the lawyers' own
worked examples, held as code and versioned like every other reference list in
this repository. Selecting one is a **projection**: it writes no `Matter.track`,
no `Õigusakt` and no `Hetkeseis`, and merely drawing it writes nothing at all
(AGENTS.md, docs/adr/0092 §12).

Nothing here is inferred from prose
-----------------------------------
Not from a title, not from a filename, not from an organisation's name, not from
a `Menetluse link`'s host, not from an upload time and not from two records
having been typed on the same afternoon. A phase is known because somebody
recorded it on a `Menetluse areng`, or because a business-dated interval between
two such records contains the act — and for no other reason (docs/adr/0092 §6,
§13).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.taxonomy.legal_instruments import (
    DOMESTIC_LEGAL_INSTRUMENT_KEYS,
    EU_LEGAL_INSTRUMENT_KEYS,
)
from app.workflow.enums import Track

# ---------------------------------------------------------------------------
# The phases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessPhase:
    """One phase, as a reader sees it: a stable key and the lawyers' own word."""

    key: str
    label: str


#: The vocabulary, in the lawyers' own words.
#:
#: Ten of the eleven are the list the second feedback round supplied verbatim.
#: `Algus` is the eleventh and is this module's own: `Hetkeseis` offers `Idee`,
#: a real value a real file holds, and a vocabulary with nowhere to put it would
#: leave those files unable to say where they are. It is deliberately the blandest
#: word available — it names the opening of a procedure and claims nothing about
#: what kind of document is coming.
PHASE_ALGUS = "algus"
PHASE_VTK = "vtk"
PHASE_KOJA_ETTEPANEK = "koja-ettepanek"
PHASE_KOOSKOLASTUS = "kooskolastus"
PHASE_VALITSUS = "valitsus"
PHASE_RIIGIKOGU = "riigikogu"
PHASE_JOUSTUMINE = "joustumine"
PHASE_ELI_KONSULTATSIOON = "eli-konsultatsioon"
PHASE_EESTI_SEISUKOHT = "eesti-seisukoht"
PHASE_ELI_MENETLUS = "eli-menetlus"
PHASE_ULEVOTMINE = "ulevotmine"

PHASES: tuple[ProcessPhase, ...] = (
    ProcessPhase(PHASE_ALGUS, "Algus"),
    ProcessPhase(PHASE_VTK, "VTK"),
    ProcessPhase(PHASE_KOJA_ETTEPANEK, "Koja ettepanek"),
    ProcessPhase(PHASE_KOOSKOLASTUS, "Kooskõlastusring"),
    ProcessPhase(PHASE_VALITSUS, "Valitsuses"),
    ProcessPhase(PHASE_RIIGIKOGU, "Riigikogus"),
    ProcessPhase(PHASE_JOUSTUMINE, "Jõustumine"),
    ProcessPhase(PHASE_ELI_KONSULTATSIOON, "ELi konsultatsioon"),
    ProcessPhase(PHASE_EESTI_SEISUKOHT, "Eesti seisukoht"),
    ProcessPhase(PHASE_ELI_MENETLUS, "ELi menetlus"),
    ProcessPhase(PHASE_ULEVOTMINE, "Ülevõtmine"),
)

PHASES_BY_KEY: dict[str, ProcessPhase] = {phase.key: phase for phase in PHASES}

#: Every key, for the database `CHECK` and for a form's choices.
PHASE_KEYS: tuple[str, ...] = tuple(phase.key for phase in PHASES)


def phase_label(key: str) -> str:
    """The reader's word for a phase key, or ``""`` for a key nothing knows.

    An unknown key is **not** an error and must not raise: a row written under a
    vocabulary a later version retired is still a row, and a projection that
    crashed on it would take the whole history down with it. It reads as
    unplaced, which is what an unreadable association means.
    """
    phase = PHASES_BY_KEY.get(key)
    return phase.label if phase is not None else ""


#: The heading an act reads under when no phase can be established for it.
#:
#: **Unplaced is not erroneous**, and the word has to say so. These are ordinary
#: records — an opinion sent before the file recorded its first procedural step,
#: a note with no date, a whole archive Matter nobody will ever reconstruct — and
#: a heading like «Määramata» or «Puudulik» would read as a queue of defects
#: somebody is expected to clear. Nothing here asks for historical cleanup.
UNPLACED_LABEL = "Etapiga sidumata"


# ---------------------------------------------------------------------------
# The patterns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternNode:
    """One phase inside one pattern.

    ``stage_keys`` is **per pattern**, not per phase, and that is the whole
    reason this type exists beside :class:`ProcessPhase`. `consultation` places a
    domestic bill on `Kooskõlastusring` and a European file on
    `ELi konsultatsioon`; one global map would have to pick one of those and be
    wrong about the other half of the register.

    ``conditional`` marks a phase that **may not apply to this file at all**, as
    opposed to one that simply has not happened yet. A VTK does not have to
    become a law, a `Määrus` may be a minister's and never reach the Government,
    and a Koja ettepanek may be answered and go no further. The reader is told so
    in words rather than being shown a step that looks inevitable.
    """

    phase_key: str
    stage_keys: frozenset[str] = frozenset()
    conditional: bool = False

    @property
    def label(self) -> str:
        return phase_label(self.phase_key)


@dataclass(frozen=True)
class ProcessPattern:
    """One reviewed reading of a procedure, as an ordered list of phases."""

    key: str
    label: str
    nodes: tuple[PatternNode, ...]

    def index_of_phase(self, phase_key: str) -> int | None:
        for index, node in enumerate(self.nodes):
            if node.phase_key == phase_key:
                return index
        return None

    def phase_for_stage(self, stage_key: str) -> str:
        """Which phase of *this* pattern a `Hetkeseis` places the file on.

        ``""`` when the pattern cannot hold it — `Muu`, or `ELi õiguse ülevõtmise
        ootel` on a file about an EU regulation that is never transposed. That is
        an honest answer and it reads beside the rail rather than being pushed
        onto the nearest node (docs/adr/0092 §13).
        """
        if not stage_key:
            return ""
        for node in self.nodes:
            if stage_key in node.stage_keys:
                return node.phase_key
        return ""


# The stage keys, once, so a typo in one pattern cannot quietly differ from the
# same concept in another. These are `StageVocabulary` keys and never labels:
# version 2.0 reworded three labels without moving a row
# (app/workflow/reference_stages.py).
_IDEA = frozenset({"idea"})
_CONSULTATION = frozenset({"consultation"})
_GOVERNMENT = frozenset({"government"})
_PARLIAMENT = frozenset({"parliament"})
_IN_FORCE = frozenset({"awaiting_entry", "in_force"})
_EU_POSITION = frozenset({"estonian_eu_position"})
_EU_PROCEDURE = frozenset({"eu_procedure"})
_TRANSPOSITION = frozenset({"awaiting_transposition"})

PATTERN_DOMESTIC = "domestic"
PATTERN_VTK = "vtk"
PATTERN_MAARUS = "maarus"
PATTERN_KOJA_ETTEPANEK = "koja-ettepanek"
PATTERN_EU = "eu"
PATTERN_EU_CONSULTATION = "eli-konsultatsioon"
PATTERN_DIRECTIVE = "direktiiv"
PATTERN_EU_REGULATION = "el-maarus"

#: The domestic bill. The lawyers' example B, which is example A without a VTK.
DOMESTIC_PATTERN = ProcessPattern(
    PATTERN_DOMESTIC,
    "Riigisisene menetlus",
    (
        PatternNode(PHASE_ALGUS, _IDEA),
        PatternNode(PHASE_KOOSKOLASTUS, _CONSULTATION),
        PatternNode(PHASE_VALITSUS, _GOVERNMENT),
        PatternNode(PHASE_RIIGIKOGU, _PARLIAMENT),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE),
    ),
)

#: Example A — a VTK, and then a bill.
#:
#: **`VTK` maps no stage key**, deliberately. There is no `Hetkeseis` meaning «a
#: väljatöötamiskavatsus is out», and `Idee` is a different statement: a file can
#: sit on `Idee` for a year with no VTK in existence. So a file reaches this phase
#: by recording an act on it and by nothing else, which is also what stops
#: `Kooskõlastusring` below from swallowing the VTK's own consultation round —
#: those are two occurrences of two different phases and the lawyers' example
#: shows both (§3 A).
#:
#: **Everything from `Valitsuses` on is conditional.** A VTK does not have to
#: become a law and does not have to reach Parliament; drawing those as the
#: ordinary next steps would be this module promising an outcome on behalf of a
#: ministry that has not decided one.
VTK_PATTERN = ProcessPattern(
    PATTERN_VTK,
    "VTK ja eelnõu",
    (
        PatternNode(PHASE_ALGUS, _IDEA),
        PatternNode(PHASE_VTK),
        PatternNode(PHASE_KOOSKOLASTUS, _CONSULTATION),
        PatternNode(PHASE_VALITSUS, _GOVERNMENT, conditional=True),
        PatternNode(PHASE_RIIGIKOGU, _PARLIAMENT, conditional=True),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE, conditional=True),
    ),
)

#: Examples D and E — a regulation, without guessing whose.
#:
#: **One pattern, not two**, and `Valitsuses` is conditional on it. The reviewed
#: `Õigusakt` value is `Määrus`, which does not say whether the Government or a
#: minister adopts it, and the two example histories differ by exactly that one
#: phase. Splitting the pattern would need a `Määruse liik` nobody has been asked
#: for; asserting `Valitsuses` would be wrong for every ministerial regulation;
#: omitting it would be wrong for every Government one. A phase marked *may not
#: apply* is the only one of the four that is true either way
#: (docs/adr/0090 §4, docs/adr/0092 §14).
#:
#: **No `Riigikogus`.** A regulation is not adopted by Parliament, and a rail
#: suggesting it as the routine next step would be teaching a reader something
#: false about the procedure.
#:
#: The **absence** of a Government record is not evidence of a ministerial
#: regulation, and nothing here reads it as one: a file with no `Valitsuses`
#: occurrence simply has no such occurrence, which is the same thing this module
#: says about every phase nobody recorded.
MAARUS_PATTERN = ProcessPattern(
    PATTERN_MAARUS,
    "Määruse menetlus",
    (
        PatternNode(PHASE_ALGUS, _IDEA),
        PatternNode(PHASE_KOOSKOLASTUS, _CONSULTATION),
        PatternNode(PHASE_VALITSUS, _GOVERNMENT, conditional=True),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE),
    ),
)

#: Examples F and G — Koda's own proposal.
#:
#: The proposal and an answer to it are the part that is not conditional; a draft
#: taking the proposal up, and the legislative process after it, are possibilities
#: and are marked as such. **Koda deciding not to pursue it is not a phase**: it is
#: `Disposition.MONITORING_STOPPED`, it says what *this office* is doing, and the
#: ministry does not stop drafting because this office stopped reading. It reads
#: beside the rail, where it has read since docs/adr/0032 (example G, §10).
KOJA_ETTEPANEK_PATTERN = ProcessPattern(
    PATTERN_KOJA_ETTEPANEK,
    "Koja ettepaneku menetlus",
    (
        PatternNode(PHASE_KOJA_ETTEPANEK, _IDEA),
        PatternNode(PHASE_KOOSKOLASTUS, _CONSULTATION, conditional=True),
        PatternNode(PHASE_VALITSUS, _GOVERNMENT, conditional=True),
        PatternNode(PHASE_RIIGIKOGU, _PARLIAMENT, conditional=True),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE, conditional=True),
    ),
)

#: The European file, where the instrument does not say which kind.
#:
#: `Ülevõtmine` is **conditional** here and separate from `Jõustumine`, which is
#: the correction this pattern exists to make: the generic European rail used to
#: end on one node holding `Jõustumise ootel`, `Jõustunud` *and* `ELi õiguse
#: ülevõtmise ootel` together, so a reader could not tell an act that is in force
#: from a directive somebody still has to transpose. An EU act being in force does
#: not mean Estonia has transposed it, and those are two ends of the rail (§10).
EU_PATTERN = ProcessPattern(
    PATTERN_EU,
    "ELi menetlus",
    (
        PatternNode(PHASE_ELI_KONSULTATSIOON, _IDEA | _CONSULTATION),
        PatternNode(PHASE_EESTI_SEISUKOHT, _EU_POSITION),
        PatternNode(PHASE_ELI_MENETLUS, _EU_PROCEDURE),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE),
        PatternNode(PHASE_ULEVOTMINE, _TRANSPOSITION, conditional=True),
    ),
)

#: Example H, read from its own end — a consultation Koda answered.
#:
#: Everything after `Eesti seisukoht` is conditional: a Commission consultation
#: routinely produces no legislative proposal at all, and a rail promising a
#: directive because a consultation exists would be inventing an outcome.
EU_CONSULTATION_PATTERN = ProcessPattern(
    PATTERN_EU_CONSULTATION,
    "ELi konsultatsioon",
    (
        PatternNode(PHASE_ELI_KONSULTATSIOON, _IDEA | _CONSULTATION),
        PatternNode(PHASE_EESTI_SEISUKOHT, _EU_POSITION),
        PatternNode(PHASE_ELI_MENETLUS, _EU_PROCEDURE, conditional=True),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE, conditional=True),
        PatternNode(PHASE_ULEVOTMINE, _TRANSPOSITION, conditional=True),
    ),
)

#: Example H in full — a directive, which Estonia has to transpose.
#:
#: The only pattern on which `Ülevõtmine` is **not** conditional, because for a
#: directive it is not a possibility but the obligation the instrument carries.
DIRECTIVE_PATTERN = ProcessPattern(
    PATTERN_DIRECTIVE,
    "Direktiivi menetlus",
    (
        PatternNode(PHASE_ELI_KONSULTATSIOON, _IDEA | _CONSULTATION),
        PatternNode(PHASE_EESTI_SEISUKOHT, _EU_POSITION),
        PatternNode(PHASE_ELI_MENETLUS, _EU_PROCEDURE),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE),
        PatternNode(PHASE_ULEVOTMINE, _TRANSPOSITION),
    ),
)

#: An EU regulation, which **has no `Ülevõtmine` node at all**.
#:
#: A regulation applies directly and nobody transposes it. The generic European
#: rail suggested transposition for every European file, which on these is not a
#: harmless extra step — it is the rail inventing a legal obligation that does not
#: exist, on exactly the instrument whose defining property is that it does not
#: (§10, the reviewed `EL määrus` description in app/taxonomy/legal_instruments.py).
#:
#: `ELi õiguse ülevõtmise ootel` therefore maps nowhere on this pattern. A file
#: carrying both that stage and this instrument is stating a contradiction, and
#: the stage reads beside the rail in its own words rather than being given a node
#: that would resolve the contradiction by picking a side.
EU_REGULATION_PATTERN = ProcessPattern(
    PATTERN_EU_REGULATION,
    "ELi määruse menetlus",
    (
        PatternNode(PHASE_ELI_KONSULTATSIOON, _IDEA | _CONSULTATION),
        PatternNode(PHASE_EESTI_SEISUKOHT, _EU_POSITION),
        PatternNode(PHASE_ELI_MENETLUS, _EU_PROCEDURE),
        PatternNode(PHASE_JOUSTUMINE, _IN_FORCE),
    ),
)

PATTERNS: dict[str, ProcessPattern] = {
    pattern.key: pattern
    for pattern in (
        DOMESTIC_PATTERN,
        VTK_PATTERN,
        MAARUS_PATTERN,
        KOJA_ETTEPANEK_PATTERN,
        EU_PATTERN,
        EU_CONSULTATION_PATTERN,
        DIRECTIVE_PATTERN,
        EU_REGULATION_PATTERN,
    )
}

#: Which reviewed `Õigusakt` key names a pattern of its own, within its family.
#:
#: Only the six the lawyers supplied a worked example for.
#: `strateegia-arengukava-tegevuskava`, `muu-siseriiklik` and `muu-eli-dokument`
#: are deliberately absent: the department gave no route for them, and inventing
#: a plausible-looking one to fill the section would be this module deciding
#: something nobody has. They fall through to their family's generic pattern,
#: which is the most that can honestly be said about them.
_INSTRUMENT_PATTERNS: dict[str, str] = {
    "vtk": PATTERN_VTK,
    "seadus": PATTERN_DOMESTIC,
    "maarus": PATTERN_MAARUS,
    "koja-ettepanek": PATTERN_KOJA_ETTEPANEK,
    "eli-konsultatsioon": PATTERN_EU_CONSULTATION,
    "direktiiv": PATTERN_DIRECTIVE,
    "el-maarus": PATTERN_EU_REGULATION,
}

#: `VTK` + `Seadus` is one file, not two roadmaps.
#:
#: The commonest real combination on the lawyers' first example: a VTK that
#: became a bill, carrying both instruments because both are true of it. Two
#: instrument patterns would otherwise read as «mixed» and draw nothing, which
#: would withdraw the roadmap from precisely the file the full example was
#: written about. The VTK pattern already contains the whole law path, so it is
#: the superset and the honest choice (§10, LAW / VTK).
_VTK_AND_LAW: frozenset[str] = frozenset({"vtk", "seadus"})

#: The two `Menetlusliik` values that choose a *family*, and the only two.
#:
#: The other five say nothing about which procedure a file is on.
#: `NATIONAL_TRANSPOSITION` is the sharpest case and is deliberately absent: a
#: `Seadus` transposing a directive runs through kooskõlastus, valitsus and
#: Riigikogu like any other bill *and* is about a European instrument, so the
#: track alone cannot choose (docs/adr/0092 §12).
_TRACK_FAMILIES: dict[str, str] = {
    Track.DOMESTIC.value: PATTERN_DOMESTIC,
    Track.EU_INITIATIVE.value: PATTERN_EU,
}

#: Which family each pattern belongs to, so a `Menetlusliik` and an `Õigusakt`
#: that disagree cannot produce a rail neither of them asked for.
_PATTERN_FAMILY: dict[str, str] = {
    PATTERN_DOMESTIC: PATTERN_DOMESTIC,
    PATTERN_VTK: PATTERN_DOMESTIC,
    PATTERN_MAARUS: PATTERN_DOMESTIC,
    PATTERN_KOJA_ETTEPANEK: PATTERN_DOMESTIC,
    PATTERN_EU: PATTERN_EU,
    PATTERN_EU_CONSULTATION: PATTERN_EU,
    PATTERN_DIRECTIVE: PATTERN_EU,
    PATTERN_EU_REGULATION: PATTERN_EU,
}


def _instrument_pattern(instrument_keys: frozenset[str]) -> ProcessPattern | None:
    """The most specific pattern the stored `Õigusakt` supports, or ``None``."""
    if not instrument_keys:
        return None
    if "vtk" in instrument_keys and instrument_keys <= _VTK_AND_LAW:
        return VTK_PATTERN
    named = {_INSTRUMENT_PATTERNS[key] for key in instrument_keys if key in _INSTRUMENT_PATTERNS}
    if len(named) == 1:
        return PATTERNS[next(iter(named))]
    return None


def pattern_for(*, track: str, instrument_keys: frozenset[str]) -> ProcessPattern | None:
    """Which pattern to read this file against, or ``None`` for none.

    **A projection, never a write.** Reading `Õigusakt` to choose a *presentation*
    is not the same as deriving `Matter.track`, which is a seven-value answer a
    person gives and which no instrument entails — a `Seadus` transposing a
    directive is a domestic instrument on a `NATIONAL_TRANSPOSITION` track. This
    module has no write path (docs/adr/0092 §12).

    Order, and it stops at the first safe answer:

    1. the **family** — `Menetlusliik` where it decides one, otherwise the
       reviewed domestic/European `Õigusakt` grouping;
    2. within that family, the **instrument's own pattern** where the stored
       `Õigusakt` names exactly one;
    3. otherwise the family's generic pattern.

    A file whose instruments name two different patterns, or whose instruments
    straddle the two families, gets ``None``. There is no reading of a `Seadus`
    and a `Direktiiv` together that picks one road over the other, and choosing
    arbitrarily would be worse than saying nothing: `Hetkeseis` in the header goes
    on answering «where is this» exactly as it always has, and the recorded
    history below goes on reading in whatever phases the file actually recorded.
    """
    specific = _instrument_pattern(instrument_keys)
    family = _TRACK_FAMILIES.get(track or "")
    if family is None:
        if not instrument_keys:
            return None
        if instrument_keys <= DOMESTIC_LEGAL_INSTRUMENT_KEYS:
            family = PATTERN_DOMESTIC
        elif instrument_keys <= EU_LEGAL_INSTRUMENT_KEYS:
            family = PATTERN_EU
        else:
            # Instruments from both families on one file. Real, ordinary, and
            # not a road.
            return None
    if specific is not None and _PATTERN_FAMILY[specific.key] == family:
        return specific
    return PATTERNS[family]


#: The phases a form may offer for one pattern, as ``(key, label)`` pairs.
#:
#: **The pattern's own phases and no others.** A select offering `Riigikogus` on a
#: ministerial regulation invites somebody to record a phase the procedure does
#: not have, and a vocabulary that is the same everywhere is one a reader has to
#: filter in their head.
def phase_choices(pattern: ProcessPattern | None) -> list[tuple[str, str]]:
    if pattern is None:
        return []
    return [(node.phase_key, node.label) for node in pattern.nodes]


__all__ = [
    "DIRECTIVE_PATTERN",
    "DOMESTIC_PATTERN",
    "EU_CONSULTATION_PATTERN",
    "EU_PATTERN",
    "EU_REGULATION_PATTERN",
    "KOJA_ETTEPANEK_PATTERN",
    "MAARUS_PATTERN",
    "PATTERNS",
    "PHASES",
    "PHASES_BY_KEY",
    "PHASE_ALGUS",
    "PHASE_EESTI_SEISUKOHT",
    "PHASE_ELI_KONSULTATSIOON",
    "PHASE_ELI_MENETLUS",
    "PHASE_JOUSTUMINE",
    "PHASE_KEYS",
    "PHASE_KOJA_ETTEPANEK",
    "PHASE_KOOSKOLASTUS",
    "PHASE_RIIGIKOGU",
    "PHASE_ULEVOTMINE",
    "PHASE_VALITSUS",
    "PHASE_VTK",
    "UNPLACED_LABEL",
    "VTK_PATTERN",
    "PatternNode",
    "ProcessPattern",
    "ProcessPhase",
    "pattern_for",
    "phase_choices",
    "phase_label",
]
