"""`Menetluse kulg` — where the external legal procedure stands, and what may
come next.

Two questions were being answered by one list, and the lawyers' second feedback
round named both separately:

* `Teema käik` — **what actually happened on this file**. Evidence and history:
  the ministry sent a draft, the Chamber asked its members, feedback came back,
  Koda's opinion went out. That is the chronology, projected from canonical
  records by `app/matters/timeline.py` and grouped into phases by
  `app/matters/phase_history.py`.
* `Menetluse kulg` — **where the procedure is now**, which stages are actually
  recorded, what may plausibly come later, and which earlier parts nobody knows
  about.

This module answers the second, and it is deliberately the smaller of the two.

What it is
----------
A **deterministic read-only projection** over facts the domain already holds: the
Matter's own `Hetkeseis`, the explicit `MATTER_STAGE_CHANGED` history, and — to
choose which procedure to read the file against — `Menetlusliik` or the reviewed
`Õigusakt` grouping. Nothing here is stored, nothing is editable, nothing is
written, and drawing it writes nothing at all.

What it is deliberately not
---------------------------
**No workflow engine.** No `WorkflowStep` table, no state machine, no transition
rules, no configurable nodes, no drag-and-drop and no BPM graph. AGENTS.md lists
a generic workflow engine among the things this repository does not introduce,
and a rail answering «which of six phases» does not need one.

**Nothing is inferred.** Not from a title, not from a filename, not from an
organisation's name, not from a `Menetluse link`'s kind or hostname, not from
today's date and not from a node's position in the list. A node is `RECORDED`
because a stage was explicitly recorded and for no other reason
(docs/adr/0089 §2, docs/adr/0091 §5.6).

**A current stage proves the current stage.** It does not prove that everything
to its left happened. A Matter first created when the bill was already in the
Riigikogu genuinely does not know whether Koda saw the consultation round, and a
rail marking the first three nodes complete because the fourth is current would
be manufacturing three milestones out of one. That is :data:`STATE_UNKNOWN`, and
it is the whole reason this component has four states rather than the usual two
(docs/adr/0092 §13).

**`Rohkem ei tegele` is not a node.** It is `Disposition.MONITORING_STOPPED` —
*Koda* stopped watching — and the external procedure carries on wherever it was.
The rail says where the procedure stands and a separate sentence says what Koda
is doing about it, which is the separation ADR 0032 made and this does not
reopen.

**No dates on a node.** A node carries a label and a state.
`MATTER_STAGE_CHANGED` proves a stage was recorded and its `occurred_at` is the
moment somebody typed it in — so printing that beside `Kooskõlastusring` would
date a step of somebody else's procedure to a day in this application's own life.
The dates that *are* real read beside the rail under `Kirjas olevad kuupäevad`,
off the records that own them (docs/adr/0092 §4, §13).

Amended: instrument-aware patterns, and a road ahead
----------------------------------------------------
V1 drew two generic rails, one domestic and one European, and deferred anything
instrument-specific until lawyers had used them (docs/adr/0092 §14, §17). They
have, and the second round asked for two things the two generic rails cannot give:

* a **road ahead** — the lawyer should be reminded of the route without having to
  memorise it, so the rail now names the next one to three phases in words and
  says in words that they are possible rather than promised;
* **patterns that do not lie about the instrument** — the single European rail
  ended every file on `Ülevõtmine / jõustumine`, which on an `EL määrus` asserted
  a transposition obligation that by definition does not exist. A regulation
  applies directly. That is not a harmless extra node; it is the rail inventing
  law.

The patterns themselves live in `app/matters/process_phases.py`, beside the phase
vocabulary the grouped history reads, because one vocabulary used twice is the
point: a section headed `Kooskõlastusring` under a node called `Kooskõlastus`
would leave a reader working out whether those are the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.matters.models import Matter
from app.matters.process_phases import (
    PHASE_JOUSTUMINE,
    PHASE_ULEVOTMINE,
    ProcessPattern,
    pattern_for,
)
from app.matters.process_timeline import PHASE_EFFECTIVE, PHASE_TRANSPOSITION
from app.workflow.enums import Disposition

# ---------------------------------------------------------------------------
# The four states
# ---------------------------------------------------------------------------
#
# Four, because there are four honest answers and the usual two would force a lie
# for two of them. `done`/`todo` cannot say «this may have happened and nobody
# recorded it», which is the ordinary state of every node to the left of where an
# archive Matter was first filed.

#: Where the file stands now. The Matter's own `Hetkeseis`, mapped to a node.
STATE_CURRENT = "current"
#: This node was reached, and something the file records says so.
STATE_RECORDED = "recorded"
#: This node may be in the past and there is **no evidence** that it happened.
#: Never «completed»: a later current stage is not proof of an earlier one.
STATE_UNKNOWN = "unknown"
#: A later step that may follow and has not been recorded.
STATE_POSSIBLE = "possible"

#: What each state is called, in words a reader sees.
#:
#: **Words, not colour.** A rail whose four states were four hues would say
#: nothing at all with the stylesheet off, to a screen reader, or on a printout —
#: and «this may have happened, we do not know» is precisely the state that cannot
#: survive being a shade of grey. Each node prints its state, and the stylesheet
#: decorates what the text already says.
STATE_LABELS: dict[str, str] = {
    STATE_CURRENT: "Praegu",
    STATE_RECORDED: "Kirjas",
    STATE_UNKNOWN: "Teadmata",
    STATE_POSSIBLE: "Võimalik",
}

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
    """One node as a reader sees it: a name, one of four states, and — on the
    current node only — which `Hetkeseis` the file actually holds.

    ``stage_label`` exists because a node is deliberately broader than a stage.
    `Jõustumine` takes both `awaiting_entry` and `in_force`; those are the same
    *point of the procedure* read from different sides, which is why they share a
    node — but «waiting for the act to come into force» and «it is in force» are
    not the same answer to «where is this», and a rail printing `Jõustumine ·
    Praegu` for both destroyed a distinction the vocabulary had already made.

    So the broad node stays broad and the canonical stage label rides on it,
    naming which side of that node the file occupies. **No new node and no new
    `StageVocabulary` value**: the label is the reviewed stage's own words, read
    from the same snapshot the rail was built from (docs/adr/0092 §13, amended).

    It is empty on every other node, and empty on the current node when the
    stage's words and the node's are the same — `Kooskõlastusring · Praegu ·
    Kooskõlastusringil` states one thing twice and tells a reader nothing.

    ``conditional`` marks a node the pattern says **may not apply to this file at
    all**. It is a different claim from `Võimalik`, which means «not yet», and the
    two are separated because a reader planning work needs to know which.
    """

    key: str
    label: str
    state: str
    stage_label: str = ""
    conditional: bool = False

    @property
    def state_label(self) -> str:
        return STATE_LABELS[self.state]


@dataclass(frozen=True)
class LegalProcessRail:
    """One Matter's `Menetluse kulg`, or nothing at all.

    **There is no separate «road ahead» list.** There was one — `Ees võib olla`,
    with the rest of the pattern behind a disclosure — and it said what the rail
    beside it was already drawing: a phase nobody has reached is muted, carries no
    date and says «Tulevikus». Two renderings of one fact is one of them too many,
    and the second was the wordier (docs/adr/0099).

    ``unplaced_stage`` is the current `Hetkeseis` when it cannot honestly be
    placed on the chosen pattern — `Muu`, or `ELi õiguse ülevõtmise ootel` on a
    file about a directly-applicable EU regulation. It reads beside the rail
    rather than being forced onto a node, because a node is a claim about which
    step of a known procedure the file is on and that is exactly what those values
    decline to say.

    ``koda_stopped`` is `Disposition.MONITORING_STOPPED` and says nothing about
    the procedure. It travels here rather than being looked up by the template,
    for the reason `ChronologyMilestone.own_note_label` travels on the milestone:
    a second surface rendering this rail must not be able to render it without.
    """

    pattern: ProcessPattern
    nodes: tuple[ProcessNode, ...]
    current_label: str = ""
    unplaced_stage: str = ""
    koda_stopped: bool = False

    @property
    def label(self) -> str:
        return self.pattern.label


@dataclass(frozen=True)
class PhaseContext:
    """What both the rail and the grouped history read, resolved once.

    The Matter page draws `Menetluse kulg` and `Teema käik` from one request, and
    they must not resolve the file's pattern and stage separately: two reads is
    two chances to disagree, and the pair sits inches apart on the page.

    **Read as they stand, in one query, rather than off the instance handed in.**
    The Matter page's own save path is the case that proves the instance cannot
    answer it: `+ Märge` moves the stage through `change_stage` on a row it locked
    for itself, then re-renders the column from the `Matter` the request fetched
    **before** the POST — which still holds the stage the file was on when the page
    was drawn. A rail built from that says the ministry sent a new version and the
    file is still on the round it just left, one line apart, which is the
    contradiction an HTMX swap exists to avoid (docs/adr/0092 §12).
    """

    pattern: ProcessPattern | None = None
    stage_key: str = ""
    stage_label: str = ""
    track: str = ""
    disposition: str = ""

    @property
    def current_phase(self) -> str:
        """Which phase of the chosen pattern the file's `Hetkeseis` places it on."""
        if self.pattern is None:
            return ""
        return self.pattern.phase_for_stage(self.stage_key)


def phase_context(*, matter: Matter, instrument_keys: frozenset[str] | None = None) -> PhaseContext:
    """Resolve one Matter's pattern and current stage. **A read, never a write.**

    Choosing a pattern from `Õigusakt` is a *presentation* decision and must never
    write `Matter.track`: that column has seven values, it is answered by a
    person, and no instrument entails one — a `Seadus` transposing a directive is
    a domestic instrument on a `NATIONAL_TRANSPOSITION` track, which is precisely
    the file a rule writing `DOMESTIC` from `seadus` would be wrong about. Nothing
    in this module has a write path (docs/adr/0092 §12).

    ``instrument_keys`` is passed in by a caller that has already read them, so
    the page does not ask twice.
    """
    snapshot = (
        Matter.objects.filter(pk=matter.pk)
        .values_list("stage__key", "stage__label_et", "track", "disposition")
        .first()
    )
    if snapshot is None:  # pragma: no cover - the caller holds a saved Matter
        return PhaseContext()
    stage_key, stage_label, track, disposition = snapshot
    track = track or ""
    # `Õigusakt` is a many-to-many and therefore a query of its own. It is read
    # only when it can still change the answer.
    keys = instrument_keys
    if keys is None:
        keys = frozenset(matter.legal_instruments.values_list("key", flat=True))
    return PhaseContext(
        pattern=pattern_for(track=track, instrument_keys=keys),
        stage_key=stage_key or "",
        stage_label=stage_label or "",
        track=track,
        disposition=disposition or "",
    )


# ---------------------------------------------------------------------------
# Reading the evidence
# ---------------------------------------------------------------------------


def _label_to_key(labels: set[str]) -> dict[str, str]:
    """Which reviewed stage each historical label names.

    Needed only for `MATTER_STAGE_CHANGED` rows written before the payload carried
    `to_key`. The live vocabulary answers for every label in use, and
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
    `Menetluse link`'s kind, not a URL host, not the current date and not a node's
    position in the list (docs/adr/0092 §13).

    Read through `scope_change_events` like every other audit read on this page. A
    stage change is genuinely a fact about the Matter rather than about a child, so
    the scope changes nothing here today — which is the point of calling it
    anyway: the day this reads a second event family, the filter is already where
    it belongs (AUTH-003).
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


def recorded_phase_keys(*, matter: Matter, user: Any) -> set[str]:
    """Every phase this file explicitly recorded a step in.

    **The second explicit canonical source, and the more direct of the two.** A
    `MATTER_STAGE_CHANGED` row says somebody moved a column; a
    `MatterProceduralDevelopment` carrying a phase says somebody recorded a step
    and stated which part of the procedure it belonged to. Both are explicit
    statements by a person, and neither is inferred from a title, a filename, an
    organisation or a date.

    Without this, `VTK` and `Koja ettepanek` could never read `Kirjas` at all:
    they map **no stage key** on purpose — a file sits on `Idee` with no
    väljatöötamiskavatsus in existence — so the stage history has nothing to say
    about them. The page showed the result plainly: a `VTK` section in the
    history with two rows under it, and a rail reading `VTK · Teadmata` three
    inches above. One screen cannot say both.

    Scoped like every other read on this page, so a step a reader may not see
    marks no node for them (AUTH-003).
    """
    from app.matters.models import MatterProceduralDevelopment

    return {
        phase
        for phase in MatterProceduralDevelopment.objects.filter(matter=matter)
        .visible_to(user)
        .values_list("process_phase", flat=True)
        .distinct()
        if phase
    }


def legal_process_rail(
    *,
    matter: Matter,
    user: Any,
    instrument_keys: frozenset[str] | None = None,
    context: PhaseContext | None = None,
) -> LegalProcessRail | None:
    """One Matter's `Menetluse kulg`, or ``None`` when nothing can be said.

    ``None`` — and therefore no section at all — where no pattern can be chosen,
    or where one can be chosen and the file records nothing that places it on one.
    Six nodes all reading `Teadmata` is a heading spent announcing that the
    application knows nothing, which is the standing empty section the approved
    target removed from this page everywhere else (docs/adr/0074 §15).

    ``context`` is the resolved pattern and stage, passed in by the Matter page so
    the rail and the grouped history below it cannot disagree about one file.
    """
    facts = (
        context
        if context is not None
        else phase_context(matter=matter, instrument_keys=instrument_keys)
    )
    pattern = facts.pattern
    if pattern is None:
        return None
    nodes = pattern.nodes
    stage_key = facts.stage_key

    recorded = recorded_stage_keys(matter=matter, user=user)
    recorded_phases = recorded_phase_keys(matter=matter, user=user)
    # The stage the file is standing on is evidence for its own node and for no
    # other. It is removed from `recorded` so the current node reads `Praegu`
    # rather than `Kirjas` — one node, one state, and the strongest true one.
    current_index = next(
        (index for index, node in enumerate(nodes) if stage_key in node.stage_keys), None
    )
    # **Two kinds of evidence, and only one of them can be an artefact.**
    #
    # A recorded *step* is an act somebody filed under a phase: it happened, and
    # where the file's `Hetkeseis` sits today says nothing about whether it did.
    # A recorded *stage* is a column having been moved, which is the thing a
    # person can get wrong and correct — see the demotion below.
    step_indexes = {index for index, node in enumerate(nodes) if node.phase_key in recorded_phases}
    stage_indexes = {
        index
        for index, node in enumerate(nodes)
        if node.stage_keys & recorded and index not in step_indexes
    }
    step_indexes.discard(current_index)
    stage_indexes.discard(current_index)
    if current_index is not None:
        # **`Kirjas` means evidence on the way *here*, not evidence anywhere.**
        #
        # A stage recorded and then corrected — somebody picked `Jõustunud` by
        # mistake and moved the file back to `Kooskõlastusring`, or the procedure
        # genuinely went backwards — leaves a `MATTER_STAGE_CHANGED` row for a
        # node to the right of where the file now stands. Read literally, that row
        # drew `Algus · Kirjas … Kooskõlastusring · Praegu … Jõustumine · Kirjas`,
        # which tells a reader the act is both in force and out for consultation.
        #
        # On a rail this broad the honest reading of a node ahead of the current
        # one is `Võimalik`: it may still be coming. The event is not deleted, not
        # rewritten and not hidden — the detailed history is where a correction
        # belongs. This is a projection rule for the overview and nothing more
        # (docs/adr/0092 §13, amended).
        #
        # **It demotes stage evidence only.** A step filed under `VTK` on a file
        # whose `Hetkeseis` is still `Idee` is an ordinary, correct file — the
        # väljatöötamiskavatsus went out and the column has not moved — and
        # reading it as `Võimalik` would put `VTK · Võimalik` directly above a
        # `VTK` section of the history holding the step. One screen cannot say
        # both, and it is the act rather than the column that is the fact.
        stage_indexes = {index for index in stage_indexes if index < current_index}

    # Where «earlier» stops and «later» begins. The current node when there is
    # one; otherwise the furthest node the file can prove it reached. Without
    # either there is nothing to position anything against, and the rail is not
    # drawn at all.
    recorded_indexes = step_indexes | stage_indexes
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
        # The explicit `Hetkeseis` rides on the current node and nowhere else, and
        # only when it adds a word the node has not already said. Read from the
        # snapshot the context already holds rather than fetched again.
        on_node = ""
        if state == STATE_CURRENT and facts.stage_label and facts.stage_label != node.label:
            on_node = facts.stage_label
        drawn.append(
            ProcessNode(
                key=node.phase_key,
                label=node.label,
                state=state,
                stage_label=on_node,
                conditional=node.conditional,
            )
        )

    # A `Hetkeseis` the chosen pattern cannot honestly hold — `Muu`, or a European
    # stage on a file the pattern says is never transposed — reads beside the rail
    # in its own words rather than being pushed onto the nearest node.
    unplaced = ""
    if facts.stage_label and current_index is None:
        unplaced = facts.stage_label

    return LegalProcessRail(
        pattern=pattern,
        nodes=tuple(drawn),
        current_label=(drawn[current_index].label if current_index is not None else ""),
        unplaced_stage=unplaced,
        koda_stopped=facts.disposition == Disposition.MONITORING_STOPPED,
    )


# ---------------------------------------------------------------------------
# One rail
# ---------------------------------------------------------------------------
#
# `Menetluse kulg` drew two things: the phase rail, and a second strip of the
# dated points the file holds. Two rails one above the other, both called a
# timeline, answering «where could this go» and «what dates are written down» —
# and a reader had to work out that those were different questions before either
# answer was any use.
#
# They are one rail now. A phase and a dated milestone are both *a named thing
# on this file's course*, they draw identically, and the difference between them
# is what a reader does with them rather than how they are shown.

#: A phase: a step of the procedure this file may pass through.
KIND_PHASE = "phase"
#: A dated point the file actually holds — `Arvamuse tähtaeg`, `Koja arvamus`,
#: `Jõustumine`, `Lõpetatud`. Read off its own canonical record by
#: `app/matters/process_timeline.py`, which stays the one place those are read.
KIND_MILESTONE = "milestone"


@dataclass(frozen=True)
class RailStep:
    """One item on the single rail: a name, maybe a date, and how to draw it.

    ``kind`` is what the editor acts on and nothing else. A phase can be hidden
    and given an expected date; a milestone cannot, because each one *is* a
    canonical record with its own editor, and a second place to change
    `Matter.response_deadline` is how two screens start disagreeing.
    """

    key: str
    label: str
    kind: str
    state: str
    display_date: str = ""
    sort_on: date | None = None
    #: Secondary information, read as the item's `title`: a closure's
    #: `Disposition`. Short and supplementary; a 150px column cannot carry a
    #: sentence without pushing its neighbours' dates out of alignment.
    detail: str = ""
    #: The canonical facts folded onto a phase node, as lines a reader can see.
    #:
    #: A commencement used to be drawn as a *second* node beside the
    #: `Jõustumine` phase, so the rail read `Jõustumine · Jõustumine 1.1.2027`
    #: — two adjacent columns with one name, both saying `Tulevikus` — and on a
    #: file with two commencement dates it read three times. The description
    #: that told them apart lived only in a `title` tooltip, which a touch
    #: screen, a printout and a screen reader all fail to deliver (QA-005).
    #:
    #: Folded onto the phase they belong to, they are one column with its dates
    #: under it, and the text is text (docs/adr/0100 §3).
    notes: tuple[str, ...] = ()
    #: How much of the connector running from this item to the next is behind
    #: us. A milestone measures it against today; a phase has no measurement
    #: because it is not a date, and takes what its state implies.
    reach: float = 1.0

    @property
    def is_phase(self) -> bool:
        return self.kind == KIND_PHASE

    @property
    def reach_percent(self) -> str:
        """``reach`` as a CSS length — ``0%``, ``37.2%``, ``100%``.

        Formatted here rather than interpolated as a number, for the reason
        `ProcessStep.reach_percent` gives: the application runs in Estonian and
        Django would localise ``0.372`` to ``0,372``, which is not a CSS parse
        error anywhere a person would see — the gradient simply stops being
        drawn.
        """
        return f"{round(self.reach * 100, 1):g}%"


def _step_rows(*, matter: Matter, user: Any) -> dict[str, Any]:
    """This Matter's own timeline steps, by phase key. Scoped like everything."""
    from app.matters.models import MatterTimelineStep

    return {
        row.phase_key: row
        for row in MatterTimelineStep.objects.filter(matter=matter).visible_to(user)
    }


def anchored_phase_keys(*, matter: Matter, user: Any) -> frozenset[str]:
    """Phases this file has pinned a canonical dated fact to.

    A commencement is the `Jõustumine` part of the procedure and a transposition
    deadline is the `Ülevõtmine` part — the same two kinds `_MILESTONE_PHASE`
    names, read from the same place, so the rail and the panel cannot disagree
    about which phases carry one.

    Hiding such a phase used to be allowed, and the fact did not go with it: the
    commencement became an unanchored point and re-sorted to wherever its date
    fell among the remaining columns, so `Jõustumine 1.1.2027` drew *before*
    `Valitsuses` and `Riigikogus` — a rail claiming the act enters into force
    before the bill reaches government (QA-008). A phase holding a real fact is
    not an optional roadmap step, so the panel refuses to take it off.

    Scoped through each record's own `visible_to`, like everything else here: a
    restricted commencement pins nothing for a reader who may not see it.
    """
    from app.intelligence.enums import FactStatus, ImportantDateKind
    from app.intelligence.selectors import matter_intelligence

    facts = matter_intelligence(matter, user)
    keys: set[str] = set()
    if any(record.date_value is not None for record in facts.effective_dates):
        keys.add(PHASE_JOUSTUMINE)
    if any(
        record.kind == ImportantDateKind.TRANSPOSITION_DEADLINE
        and record.status == FactStatus.ACTIVE
        for record in facts.upcoming_dates
    ):
        keys.add(PHASE_ULEVOTMINE)
    return frozenset(keys)


def recorded_phase_dates(
    *, matter: Matter, user: Any, phase_keys: frozenset[str]
) -> dict[str, Any]:
    """The earliest recorded business date in each phase, from what the file has.

    **Reused, never duplicated.** A phase that actually happened is dated by the
    `Menetluse areng` the lawyer filed in it — the same record the chronology
    groups on — so the rail asks that record rather than storing the day a second
    time. Only a phase with no such record can carry an expectation of its own.

    Undated developments contribute nothing: «kuupäev teadmata» is an answer, and
    it is not a day to print beside a phase.
    """
    from app.matters.models import MatterProceduralDevelopment

    found: dict[str, Any] = {}
    rows = (
        MatterProceduralDevelopment.objects.filter(
            matter=matter, process_phase__in=phase_keys, occurred_on__isnull=False
        )
        .visible_to(user)
        .values_list("process_phase", "occurred_on", "occurred_on_precision")
        .order_by("occurred_on")
    )
    for phase_key, occurred_on, precision in rows:
        found.setdefault(phase_key, (occurred_on, precision))
    return found


def matter_rail(
    *,
    matter: Matter,
    user: Any,
    rail: LegalProcessRail | None,
    milestones: Any = (),
) -> list[RailStep]:
    """The one rail `Menetluse kulg` draws, phases and dated points together.

    ``milestones`` is `process_timeline.process_steps` — already built, already
    scoped — handed in rather than read again, so the seven dated points stay
    defined in exactly one module.

    **The phases hold the frame and the current node divides it.** A phase list
    has an order that is not chronological — a phase with no date sits where the
    procedure puts it — and a dated point has a date and no place in a pattern.
    Reconciling them by date alone does not work, because *the ordinary file
    dates none of its phases*: every dated point then sorts ahead of every
    phase, and a rail opens with commencement in 2027 and reaches `Algus` five
    columns later.

    So the one place both kinds agree on does the work: the phase the file is on
    now. A dated point that has **already happened** is looked for among the
    phases up to and including it, and one still **ahead** among the phases past
    it — the same reading of today the strip's own `--tl-reach` grammar makes
    (docs/adr/0074 §12.2). Inside that window a point still sorts against any
    phase that *is* dated, so a recorded `Kooskõlastusring` in January and a file
    opened in September read in the order they happened rather than in the order
    the pattern lists them.

    **Where a future point lands when nothing dates it** is the one rule that
    round got wrong. A phase nobody has dated is not a point in time, so a
    deadline placed *after* the undated rest of the pattern was being ordered by
    the pattern's shape rather than by anything about the file: `Arvamuse
    tähtaeg 30.09` read after `Valitsuses`, `Riigikogus` and `Jõustumine` on a
    bill still out for consultation. An unanchored future point now reads beside
    the phase the file is on, where the obligation actually falls due; one whose
    kind names a phase goes on reading with that phase (:data:`_MILESTONE_PHASE`).

    **Hidden steps are gone from the result, not marked.** A row a person removed
    from this file's rail is not a row drawn in grey — that would be the clutter
    they were removing.
    """
    rows = _step_rows(matter=matter, user=user)
    steps: list[RailStep] = []

    if rail is not None:
        for node in rail.nodes:
            row = rows.get(node.key)
            if row is not None and row.hidden:
                continue
            # **A phase node is dated by the roadmap, and by nothing else.**
            #
            # It used to take the date of the *first* recorded step filed under
            # that phase. On a file that had gone out for consultation twice,
            # the node then read `Kooskõlastusring 2.4.2026` an inch above a
            # `Teema käik` whose current section said `alates 20.08.2026` — two
            # answers to «since when is this file on the coordination round» on
            # one screen (QA-007). Worse, borrowing history in pattern order
            # made the dates non-monotonic: 5.3 → 2.4 → 3.6 → 17.6 read
            # left-to-right across a file that had gone forward and come back,
            # and the rail cannot draw a loop (QA-006).
            #
            # So the rail stops trying to answer historical repetition. That is
            # `Teema käik`'s question and `Teema käik` answers it properly, with
            # a section per occurrence. What this node carries is what somebody
            # deliberately put on the roadmap: an explicit `MatterTimelineStep`
            # date, and otherwise nothing.
            when, display = None, ""
            if row is not None and row.occurs_on is not None:
                when, display = row.occurs_on, row.display_date
            steps.append(
                RailStep(
                    key=node.key,
                    label=node.label,
                    kind=KIND_PHASE,
                    state=node.state,
                    display_date=display,
                    sort_on=when,
                    # A phase the file has reached joins the solid rail; one it
                    # has not does not. `Teadmata` — an earlier phase with no
                    # evidence — draws no solid connector either, because a
                    # connector behind it would be the completed milestone the
                    # late-entry rule refuses (docs/adr/0092 §13).
                    reach=1.0 if node.state in (STATE_CURRENT, STATE_RECORDED) else 0.0,
                )
            )

    today = timezone.localdate()
    for milestone in sorted(milestones, key=lambda one: one.sort_on):
        # **A fact whose phase is drawn is folded onto that phase, not beside
        # it.** A commencement used to be inserted as its own column next to
        # `Jõustumine`, so the rail read `Jõustumine · Jõustumine 1.1.2027` —
        # two adjacent columns with the same name and the same `Tulevikus`
        # under them — and a file with two commencement dates drew three. The
        # sentence telling them apart was in a `title` tooltip, which a touch
        # screen, a printout and a screen reader all fail to deliver (QA-005).
        #
        # Folded, it is one column carrying its own dates as visible lines.
        # Hiding the phase cannot strand the fact either, because
        # `anchored_phase_keys` is what stops the phase being hidden at all.
        folded = _fold_into_phase(steps, milestone)
        if folded:
            continue
        step = RailStep(
            key=f"milestone:{milestone.label}:{milestone.sort_on.isoformat()}",
            label=milestone.label,
            kind=KIND_MILESTONE,
            state=milestone.state,
            display_date=milestone.display,
            sort_on=milestone.sort_on,
            # The strip already measured this one against today.
            reach=milestone.reach,
            detail=milestone.detail,
        )
        # Recomputed rather than carried, because placing a dated point that has
        # already happened moves the current node one to the right.
        current = next(
            (index for index, placed in enumerate(steps) if placed.state == STATE_CURRENT),
            len(steps),
        )
        if milestone.sort_on <= today:
            window, default = (0, min(current + 1, len(steps))), current
        else:
            # **A future point with no phase of its own belongs beside the
            # current one, not at the end of the road.**
            #
            # The default used to be `len(steps)`, which on the ordinary file —
            # where no future phase carries a date — put an `Arvamuse tähtaeg`
            # three weeks out *after* `Valitsuses`, `Riigikogus` and
            # `Jõustumine`. A lawyer reading the rail saw this office's own
            # deadline drawn as the last thing that happens to a bill still out
            # for consultation, which is the opposite of what it is: an
            # obligation falling due during the round the file is on now.
            #
            # A milestone whose kind *does* name a phase keeps reading with that
            # phase — a commencement belongs with `Jõustumine` and a
            # transposition deadline with `Ülevõtmine`, however far off they
            # are. Decided on the milestone's stable kind
            # (`app/matters/process_timeline.py` `PHASE_*`) and never on its
            # translated label: `Jõustumine` is both a phase and a commencement,
            # and no list of words can tell those apart.
            #
            # Either way it is only the *default*. An explicitly dated future
            # phase is a real anchor and `_slot_for` still sorts against it, so
            # `Valitsuses 15.10` and a deadline on the 30th read in the order
            # somebody actually recorded.
            #
            # **The window starts at whichever phase the point belongs beside**,
            # and that is what makes the anchor do anything at all. `_slot_for`
            # scans backwards over every dated step in its window, and the
            # window running from the current phase holds the file's *past*
            # dated points — `Alustatud`, a sent opinion. A commencement in 2027
            # then anchored to whichever of those it was not earlier than, which
            # on the ordinary open file drew it between `Alustatud` and
            # `Valitsuses`: a date two years out, before two phases nobody has
            # reached. Narrowed to its own phase, the only things it can sort
            # against are that phase and the commencements already beside it.
            anchor = _phase_slot(steps, milestone)
            beside = current if anchor is None else max(anchor, current)
            window, default = (beside, len(steps)), min(beside + 1, len(steps))
        steps.insert(_slot_for(steps, milestone.sort_on, window, default), step)
    return steps


#: Which phase of the pattern each *kind* of dated point belongs to.
#:
#: Keyed on `app/matters/process_timeline.py`'s own `PHASE_*` constants, which
#: are stable domain kinds rather than the labels a reader sees. Only two kinds
#: name a phase at all: a commencement is the `Jõustumine` part of the
#: procedure, and a transposition deadline is the `Ülevõtmine` part.
#:
#: `Arvamuse tähtaeg` and `Tagasiside tähtaeg` are deliberately absent. Neither
#: is a step of somebody else's procedure — they are dates this office owes and
#: is owed, during whichever phase the file happens to be on — so there is no
#: phase to put them beside and they read beside the current one.
_MILESTONE_PHASE: dict[int, str] = {
    PHASE_EFFECTIVE: PHASE_JOUSTUMINE,
    PHASE_TRANSPOSITION: PHASE_ULEVOTMINE,
}


def _fold_into_phase(steps: list[RailStep], milestone: Any) -> bool:
    """Put a dated fact onto the phase node it belongs to, if that node is drawn.

    Returns whether it was folded. A kind that names no phase, or whose phase
    this pattern does not have — an `Ülevõtmise tähtaeg` on an `EL määrus` —
    goes on reading as a point of its own, which is what an unanchored point
    is.

    The node keeps the *earliest* fact as its own date, because that is when
    the phase begins to be true, and lists every one of them as a line beneath.
    Each line carries its description where there is one: «põhiosa 27.9.2027»
    tells a reader what a bare second date cannot.
    """
    index = _phase_slot(steps, milestone)
    if index is None:
        return False
    node = steps[index]
    line = (
        f"{milestone.detail} {milestone.display}".strip() if milestone.detail else milestone.display
    )
    steps[index] = replace(
        node,
        display_date=node.display_date or milestone.display,
        sort_on=node.sort_on or milestone.sort_on,
        notes=(*node.notes, line),
    )
    return True


def _phase_slot(steps: list[RailStep], milestone: Any) -> int | None:
    """Where this milestone's own phase is drawn, or ``None`` for no phase.

    ``None`` both for a kind that names no phase and for one whose phase this
    file does not draw — an `Ülevõtmise tähtaeg` on an `EL määrus`, whose
    pattern has no `Ülevõtmine` node at all, or a phase somebody took off this
    file's rail. A dated point is never dropped for want of an anchor; it simply
    has none, and reads where an unanchored point reads.
    """
    phase_key = _MILESTONE_PHASE.get(milestone.phase)
    if phase_key is None:
        return None
    return next(
        (
            index
            for index, placed in enumerate(steps)
            if placed.is_phase and placed.key == phase_key
        ),
        None,
    )


def _slot_for(steps: list[RailStep], when: date, window: tuple[int, int], default: int) -> int:
    """Where a dated point sits among steps that mostly have no date.

    An undated step does not constrain it: a phase with no date makes no claim
    about what preceded it, and on the ordinary file *no phase has one*. So the
    scan runs backwards over the dated steps in the window only — after the last
    one that is not later, before the first one that is — and a window holding no
    dated step at all falls back to ``default``.

    ``default`` is the caller's answer to «and where does it go when nothing in
    the window dates anything»: the current phase for a point already behind us,
    and the slot just past the current phase — or just past the point's own
    phase, where its kind names one — for a point still ahead.
    """
    low, high = window
    position = default
    for index in range(high - 1, low - 1, -1):
        placed = steps[index]
        if placed.sort_on is None:
            continue
        if placed.sort_on <= when:
            return index + 1
        position = index
    return position


#: Kept out of the query above on purpose: this module reads and never filters a
#: population, so it has no `Q` of its own to export.
__all__ = [
    "KIND_MILESTONE",
    "KIND_PHASE",
    "KODA_STOPPED_LABEL",
    "STATE_CURRENT",
    "STATE_LABELS",
    "STATE_POSSIBLE",
    "STATE_RECORDED",
    "STATE_UNKNOWN",
    "LegalProcessRail",
    "PhaseContext",
    "ProcessNode",
    "RailStep",
    "anchored_phase_keys",
    "legal_process_rail",
    "matter_rail",
    "phase_context",
    "recorded_phase_dates",
    "recorded_phase_keys",
    "recorded_stage_keys",
]
