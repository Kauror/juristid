"""`Menetluse kulg` — where the external legal procedure stands, and nothing else.

Two questions were being answered by one list, and the lawyers' second feedback
round named both of them separately:

* `Teema käik` — **what actually happened on this file**. Evidence and history:
  the ministry sent a draft, the Chamber asked its members, feedback came back,
  Koda's opinion went out. That is the chronology, and it is projected from
  canonical records by `app/matters/timeline.py`.
* `Menetluse kulg` — **where the procedure is now**, which stages are actually
  recorded, what may come later, and which earlier parts nobody knows about.

This module answers the second, and it is deliberately the smaller of the two.

What it is
----------
A **deterministic read-only projection** over facts the domain already holds:
the Matter's own `Hetkeseis`, the explicit `MATTER_STAGE_CHANGED` history, and —
for choosing which of two generic rails to draw — `Menetlusliik` or the reviewed
`Õigusakt` grouping. Nothing here is stored, nothing is editable, nothing is
written and no migration accompanies it.

What it is deliberately not
---------------------------
**No workflow engine.** No `WorkflowStep` table, no state machine, no transition
rules, no configurable nodes, no drag-and-drop and no BPM graph. AGENTS.md lists
a generic workflow engine among the things this repository does not introduce,
and a rail that answered «which of five generic steps» does not need one.

**No instrument-specific process.** V1 draws two rails — a domestic one and a
European one — and no more. A separate government-regulation flow beside a
minister-regulation flow was the first draft and it cannot be built from what
the file records: the reviewed `Õigusakt` value is `Määrus`, which does not say
whose. Adding a subtype for the sake of a prettier rail would be inventing a
classification nobody chose (docs/adr/0090 §4, docs/adr/0092 §14).

**Nothing is inferred.** Not from a title, not from a filename, not from an
organisation's name, not from a `Menetluse link`'s kind or hostname, not from
today's date and not from a node's position in the list. A node is `RECORDED`
because a stage was explicitly recorded and for no other reason
(docs/adr/0089 §2, docs/adr/0091 §5.6).

**A current stage proves the current stage.** It does not prove that everything
to its left happened. A Matter first created when the bill was already in the
Riigikogu genuinely does not know whether Koda saw the consultation round, and
a rail that marked the first three nodes complete because the fourth is current
would be manufacturing three milestones out of one. That is
:data:`STATE_UNKNOWN`, and it is the whole reason this component has four states
rather than the usual two (docs/adr/0092 §13).

**`Rohkem ei tegele` is not a node.** It is `Disposition.MONITORING_STOPPED` —
*Koda* stopped watching — and the external procedure carries on wherever it was.
The rail says where the procedure stands and a separate sentence says what Koda
is doing about it, which is the separation ADR 0032 made and this does not
reopen.

**No dates.** A node carries a label and a state. `MATTER_STAGE_CHANGED` proves
that a stage was recorded and its `occurred_at` is the moment somebody typed it
in — so printing that beside `Kooskõlastus` would date a step of somebody else's
procedure to a day in this application's own life, which is exactly the
substitution docs/adr/0092 §4 refuses on the chronology (docs/adr/0092 §13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.matters.models import Matter
from app.taxonomy.legal_instruments import (
    DOMESTIC_LEGAL_INSTRUMENT_KEYS,
    EU_LEGAL_INSTRUMENT_KEYS,
)
from app.workflow.enums import Disposition, Track

# ---------------------------------------------------------------------------
# The four states
# ---------------------------------------------------------------------------
#
# Four, because there are four honest answers and the usual two would force a
# lie for two of them. `done`/`todo` cannot say «this may have happened and
# nobody recorded it», which is the ordinary state of every node to the left of
# where an archive Matter was first filed.

#: Where the file stands now. The Matter's own `Hetkeseis`, mapped to a node.
STATE_CURRENT = "current"
#: This node was reached, and something the file records says so.
STATE_RECORDED = "recorded"
#: This node may be in the past and there is **no evidence** that it happened.
#: Never «completed»: a later current stage is not proof of an earlier one.
STATE_UNKNOWN = "unknown"
#: A generic later step that may follow and has not been recorded.
STATE_POSSIBLE = "possible"

#: What each state is called, in words a reader sees.
#:
#: **Words, not colour.** A rail whose four states were four hues would say
#: nothing at all with the stylesheet off, to a screen reader, or on a printout
#: — and «this may have happened, we do not know» is precisely the state that
#: cannot survive being a shade of grey. Each node prints its state, and the
#: stylesheet decorates what the text already says.
STATE_LABELS: dict[str, str] = {
    STATE_CURRENT: "Praegu",
    STATE_RECORDED: "Kirjas",
    STATE_UNKNOWN: "Teadmata",
    STATE_POSSIBLE: "Võimalik",
}


# ---------------------------------------------------------------------------
# The two V1 templates
# ---------------------------------------------------------------------------

TEMPLATE_DOMESTIC = "domestic"
TEMPLATE_EU = "eu"


@dataclass(frozen=True)
class ProcessTemplateNode:
    """One node of a rail: a stable key, a label, and which stages reach it."""

    key: str
    label: str
    #: The reviewed `StageVocabulary` keys that map here. Keys, never labels:
    #: version 2.0 reworded three labels without moving a row, and a mapping
    #: written against the words would have silently stopped matching.
    stage_keys: frozenset[str]


#: The domestic rail. Five nodes, and the vocabulary is the procedure's own.
#:
#: `Jõustumine` takes both `awaiting_entry` and `in_force`, which are the same
#: point of the procedure read from two sides — waiting for it and past it. The
#: node's *state* is what tells them apart, not a sixth node.
DOMESTIC_TEMPLATE: tuple[ProcessTemplateNode, ...] = (
    ProcessTemplateNode("algus", "Algus", frozenset({"idea"})),
    ProcessTemplateNode("kooskolastus", "Kooskõlastus", frozenset({"consultation"})),
    ProcessTemplateNode("valitsus", "Valitsus", frozenset({"government"})),
    ProcessTemplateNode("riigikogu", "Riigikogu", frozenset({"parliament"})),
    ProcessTemplateNode("joustumine", "Jõustumine", frozenset({"awaiting_entry", "in_force"})),
)

#: The European rail.
#:
#: `Vastu võetud` maps **no stage key**, and that is deliberate rather than an
#: omission: the reviewed `Hetkeseis` vocabulary has no value for «the EU
#: institutions adopted it», so the node can honestly only ever read `Teadmata`
#: or `Võimalik`. Inventing a stage to fill it, or quietly dropping the step
#: from the rail, would both be this module deciding something the department
#: has not (docs/adr/0092 §14).
#:
#: `awaiting_entry` and `in_force` join `awaiting_transposition` on the last
#: node: a directive that has been transposed and the act that transposed it
#: coming into force are the same end of this rail.
EU_TEMPLATE: tuple[ProcessTemplateNode, ...] = (
    ProcessTemplateNode("algus", "Algus / konsultatsioon", frozenset({"idea", "consultation"})),
    ProcessTemplateNode("eesti-seisukoht", "Eesti seisukoht", frozenset({"estonian_eu_position"})),
    ProcessTemplateNode("el-menetlus", "EL menetlus", frozenset({"eu_procedure"})),
    ProcessTemplateNode("vastu-voetud", "Vastu võetud", frozenset()),
    ProcessTemplateNode(
        "ulevotmine",
        "Ülevõtmine / jõustumine",
        frozenset({"awaiting_transposition", "awaiting_entry", "in_force"}),
    ),
)

TEMPLATES: dict[str, tuple[ProcessTemplateNode, ...]] = {
    TEMPLATE_DOMESTIC: DOMESTIC_TEMPLATE,
    TEMPLATE_EU: EU_TEMPLATE,
}

#: What each rail is called above itself.
TEMPLATE_LABELS: dict[str, str] = {
    TEMPLATE_DOMESTIC: "Riigisisene menetlus",
    TEMPLATE_EU: "ELi menetlus",
}

#: `other` — «Muu» — maps to no node on either rail, and must not be forced onto
#: one.
#:
#: It is a real answer somebody gave: this proceeding is not one of the nine
#: shapes the vocabulary names. Placing it on `Algus` because it sorts first, or
#: on the current node because something has to be current, would both be the
#: rail asserting a position in a procedure that the person explicitly declined
#: to give. It reads as :attr:`LegalProcessRail.unplaced_stage` instead — beside
#: the rail, in its own words (docs/adr/0092 §13).
UNPLACEABLE_STAGE_KEYS: frozenset[str] = frozenset({"other"})

#: What the rail says beside itself when Koda has stopped following the file.
#:
#: `Disposition.MONITORING_STOPPED`'s own words on `Lõpeta teema`. It is not a
#: node and it is not a state: the ministry does not stop drafting because this
#: office stopped reading, and a terminal node here would say it did
#: (docs/adr/0032, docs/adr/0092 §15).
KODA_STOPPED_LABEL = "Koda ei tegele edasi"


# ---------------------------------------------------------------------------
# The read model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessNode:
    """One node as a reader sees it: a name and one of four states."""

    key: str
    label: str
    state: str

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


@dataclass(frozen=True)
class LegalProcessRail:
    """One Matter's `Menetluse kulg`, or nothing at all.

    ``unplaced_stage`` is the current `Hetkeseis` when it cannot honestly be
    placed on the chosen rail — `Muu`, or a European stage on a domestic file.
    It reads beside the rail rather than being forced onto a node, because a
    node is a claim about which step of a known procedure the file is on and
    that is exactly what those values decline to say.

    ``koda_stopped`` is `Disposition.MONITORING_STOPPED` and says nothing about
    the procedure. It travels here rather than being looked up by the template,
    for the reason `ChronologyMilestone.own_note_label` travels on the milestone:
    a second surface rendering this rail must not be able to render it without.
    """

    template: str
    nodes: tuple[ProcessNode, ...]
    unplaced_stage: str = ""
    koda_stopped: bool = False

    @property
    def label(self) -> str:
        return TEMPLATE_LABELS[self.template]


# ---------------------------------------------------------------------------
# Choosing a rail — a reading of stored facts, and never a writer
# ---------------------------------------------------------------------------

#: The two `Menetlusliik` values that safely choose a presentation, and the only
#: two.
#:
#: `Matter.track` is canonical **where it is known** and it is optional; an empty
#: value means nobody has said, and nothing here backfills, infers or writes one.
#:
#: The other five are deliberately absent. `NATIONAL_TRANSPOSITION` — «ELi õiguse
#: ülevõtmine» — is the clearest case: a `Seadus` transposing a directive runs
#: through kooskõlastus, valitsus and Riigikogu like any other domestic bill,
#: *and* the file is about a European instrument, so the track alone does not
#: choose between the two rails. It falls through to the instrument grouping
#: below, which answers from what the Matter actually holds.
#: `STRATEGY`, `KODA_INITIATIVE`, `IMPLEMENTATION` and `OTHER` say nothing about
#: either procedure.
TRACK_TEMPLATES: dict[str, str] = {
    Track.DOMESTIC.value: TEMPLATE_DOMESTIC,
    Track.EU_INITIATIVE.value: TEMPLATE_EU,
}


def template_for(*, track: str, instrument_keys: frozenset[str]) -> str:
    """Which rail to draw, or ``""`` for none. **A projection, never a write.**

    Order of preference, and it stops at the first safe answer:

    1. `Matter.track`, for the two values whose semantics choose a rail;
    2. the reviewed `Õigusakt` grouping — `DOMESTIC_LEGAL_INSTRUMENT_KEYS` and
       `EU_LEGAL_INSTRUMENT_KEYS`, which is how the siseriiklik/ELiga-seotud
       distinction stays answerable from stored data since docs/adr/0090 §4;
    3. nothing, which is the honest answer for a file that says neither.

    **Using `Õigusakt` to choose a coarse display template is a projection, and
    it must never write `Matter.track`.** That column has seven values, it is
    answered by a person, and no instrument type entails one — a `Seadus`
    transposing a directive is a domestic instrument on a
    `NATIONAL_TRANSPOSITION` track, which is precisely the file a rule writing
    `DOMESTIC` from `seadus` would be wrong about. Nothing in this module has a
    write path (app/taxonomy/legal_instruments.py, docs/adr/0092 §12).

    **A mixed file draws nothing.** A Matter carrying both `seadus` and
    `direktiiv` is a real and ordinary combination, and there is no reading of it
    that picks one rail over the other — so it gets neither, and `Hetkeseis` in
    the header goes on answering «where is this» as it always has.
    """
    chosen = TRACK_TEMPLATES.get(track)
    if chosen is not None:
        return chosen
    if not instrument_keys:
        return ""
    if instrument_keys <= DOMESTIC_LEGAL_INSTRUMENT_KEYS:
        return TEMPLATE_DOMESTIC
    if instrument_keys <= EU_LEGAL_INSTRUMENT_KEYS:
        return TEMPLATE_EU
    return ""


# ---------------------------------------------------------------------------
# Reading the evidence
# ---------------------------------------------------------------------------


def _label_to_key(labels: set[str]) -> dict[str, str]:
    """Which reviewed stage each historical label names.

    Needed only for `MATTER_STAGE_CHANGED` rows written before the payload
    carried `to_key`. The live vocabulary answers for every label in use, and
    `REFERENCE_STAGES_V1` answers for the three the lawyers reworded in version
    2.0 — which together is every label this product has ever written
    (app/workflow/reference_stages.py).

    One query, and only when there is an unresolved label to ask about.
    """
    from app.workflow.models import StageVocabulary
    from app.workflow.reference_stages import REFERENCE_STAGES_V1

    if not labels:
        return {}
    resolved = {
        stage.label_et: stage.key
        for stage in StageVocabulary.objects.filter(label_et__in=labels).only("key", "label_et")
    }
    for stage in REFERENCE_STAGES_V1:
        resolved.setdefault(stage.label_et, stage.key)
    return resolved


def recorded_stage_keys(*, matter: Matter, user: Any) -> set[str]:
    """Every stage this file explicitly recorded having been at.

    **Explicit canonical evidence only.** The `MATTER_STAGE_CHANGED` history and
    nothing else: not a title, not a filename, not an organisation, not a
    `Menetluse link`'s kind, not a URL host, not the current date and not a
    node's position in the list (docs/adr/0092 §13).

    Read through `scope_change_events` like every other audit read on this page.
    A stage change is genuinely a fact about the Matter rather than about a
    child, so the scope changes nothing here today — which is the point of
    calling it anyway: the day this reads a second event family, the filter is
    already where it belongs (AUTH-003).
    """
    events = list(
        scope_change_events(
            ChangeEvent.objects.filter(
                matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED
            ),
            user,
        ).values_list("payload", flat=True)
    )
    keys: set[str] = set()
    unresolved: set[str] = set()
    for payload in events:
        payload = payload or {}
        for key_field, label_field in (("to_key", "to_label"), ("from_key", "from_label")):
            key = payload.get(key_field)
            if key:
                keys.add(str(key))
                continue
            label = payload.get(label_field)
            if label:
                unresolved.add(str(label))
    if unresolved:
        by_label = _label_to_key(unresolved)
        keys |= {by_label[label] for label in unresolved if label in by_label}
    return keys


def legal_process_rail(
    *, matter: Matter, user: Any, instrument_keys: frozenset[str] | None = None
) -> LegalProcessRail | None:
    """One Matter's `Menetluse kulg`, or ``None`` when nothing can be said.

    ``None`` — and therefore no section at all — where no rail can be chosen, or
    where a rail can be chosen and the file records nothing that places it on
    one. Five nodes all reading `Teadmata` is a heading spent announcing that
    the application knows nothing, which is the standing empty section the
    approved target removed from this page everywhere else (docs/adr/0074 §15).

    ``instrument_keys`` is the reviewed `Õigusakt` keys, passed in by the page
    that has already read them so this does not ask a second time.
    """
    stage = matter.stage
    stage_key = getattr(stage, "key", "") or ""
    keys = instrument_keys
    if keys is None:
        keys = frozenset(matter.legal_instruments.values_list("key", flat=True))

    template = template_for(track=matter.track or "", instrument_keys=keys)
    if not template:
        return None
    nodes = TEMPLATES[template]

    recorded = recorded_stage_keys(matter=matter, user=user)
    # The stage the file is standing on is evidence for its own node and for no
    # other. It is removed from `recorded` so that the current node reads
    # `Praegu` rather than `Kirjas` — one node, one state, and the strongest
    # true one.
    current_index = next(
        (index for index, node in enumerate(nodes) if stage_key in node.stage_keys), None
    )
    recorded_indexes = {
        index
        for index, node in enumerate(nodes)
        if node.stage_keys & recorded and index != current_index
    }

    # Where «earlier» stops and «later» begins. The current node when there is
    # one; otherwise the furthest node the file can prove it reached. Without
    # either there is nothing to position anything against, and the rail is not
    # drawn at all.
    anchor = current_index if current_index is not None else max(recorded_indexes, default=None)
    if anchor is None:
        return None

    drawn: list[ProcessNode] = []
    for index, node in enumerate(nodes):
        if index == current_index:
            state = STATE_CURRENT
        elif index in recorded_indexes:
            state = STATE_RECORDED
        elif index < anchor:
            # **The late-entry rule.** A Matter first filed when the bill was
            # already in the Riigikogu proves the Riigikogu and nothing to its
            # left. Never `completed` (docs/adr/0092 §13).
            state = STATE_UNKNOWN
        else:
            state = STATE_POSSIBLE
        drawn.append(ProcessNode(key=node.key, label=node.label, state=state))

    # A `Hetkeseis` the chosen rail cannot honestly hold — `Muu`, or a European
    # stage on a domestic file — reads beside the rail in its own words rather
    # than being pushed onto the nearest node.
    unplaced = ""
    if stage is not None and current_index is None:
        unplaced = stage.label_et

    return LegalProcessRail(
        template=template,
        nodes=tuple(drawn),
        unplaced_stage=unplaced,
        koda_stopped=matter.disposition == Disposition.MONITORING_STOPPED,
    )


#: Kept out of the query above on purpose: this module reads and never filters a
#: population, so it has no `Q` of its own to export.
__all__ = [
    "DOMESTIC_TEMPLATE",
    "EU_TEMPLATE",
    "KODA_STOPPED_LABEL",
    "STATE_CURRENT",
    "STATE_LABELS",
    "STATE_POSSIBLE",
    "STATE_RECORDED",
    "STATE_UNKNOWN",
    "TEMPLATE_DOMESTIC",
    "TEMPLATE_EU",
    "LegalProcessRail",
    "ProcessNode",
    "legal_process_rail",
    "recorded_stage_keys",
    "template_for",
]
