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
    CONFIRMABLE_STAGE_KEYS,
    PHASE_JOUSTUMINE,
    PHASE_KEYS,
    PHASE_ULEVOTMINE,
    ProcessPattern,
    confirmable_phase,
    pattern_for,
    phase_date_bounds,
    phase_label,
)
from app.matters.process_timeline import (
    PHASE_EFFECTIVE,
    PHASE_TRANSPOSITION,
    STATE_REACHED,
    STATE_TODAY,
    dated_state,
)
from app.workflow.dates import period_starts_after
from app.workflow.enums import Disposition
from app.workflow.lateness import period_end_for

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
    #: Every phase the file recorded a step in, whether or not this pattern
    #: draws it — read once here and handed on, so `matter_rail` can keep a
    #: recorded phase the pattern no longer has without asking again
    #: (docs/adr/0119 §4).
    recorded_phases: frozenset[str] = frozenset()

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


@dataclass(frozen=True)
class PhaseDateOffer:
    """`Märgi ka menetluse kulgu: <phase> <day>` — what one stage move may also date.

    One per stage the `+ Märge` select offers whose move would date an undated
    phase (docs/adr/0128 §1). ``earliest`` and ``latest`` are the days the
    roadmap's order rule allows, ``None`` where nothing bounds a side; the day
    itself must also not be after today, which the form and the service check on
    their own clock.
    """

    stage_key: str
    phase_key: str
    phase_label: str
    earliest: date | None = None
    latest: date | None = None

    def allows(self, day: date | None, today: date) -> bool:
        if day is None or day > today:
            return False
        if self.earliest is not None and day < self.earliest:
            return False
        return not (self.latest is not None and day > self.latest)


def phase_date_offers(*, phases: PhaseContext, rows: dict[str, Any]) -> dict[str, PhaseDateOffer]:
    """For each stage a `+ Märge` could move this file to, the phase it may also date.

    A read over the pattern, the stage the file stands on, and the phase rows
    already read (:func:`timeline_step_rows`). A stage is offered only where
    :func:`~app.matters.process_phases.confirmable_phase` names a phase **and that
    phase carries no date yet** — an existing date is never overwritten, and a
    date is never offered as though it were unset (docs/adr/0128 §1).
    """
    pattern = phases.pattern
    if pattern is None:
        return {}
    dated = {key: row.occurs_on for key, row in rows.items() if row.occurs_on is not None}
    offers: dict[str, PhaseDateOffer] = {}
    for stage_key in sorted(CONFIRMABLE_STAGE_KEYS):
        phase_key = confirmable_phase(pattern, from_stage=phases.stage_key, to_stage=stage_key)
        if not phase_key or phase_key in dated:
            continue
        earliest, latest = phase_date_bounds(pattern, phase_key, dated)
        offers[stage_key] = PhaseDateOffer(
            stage_key=stage_key,
            phase_key=phase_key,
            phase_label=phase_label(phase_key),
            earliest=earliest,
            latest=latest,
        )
    return offers


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

    **Only a step that has happened.** A `Märge` may be dated ahead of today since
    docs/adr/0121 §3 — «istung 12.11» written down before the sitting — and a
    plan is not evidence that a phase was reached. So a development whose period
    begins after today marks nothing until its day comes; an undated one still
    counts, as it always has.
    """
    from app.matters.models import MatterProceduralDevelopment

    today = timezone.localdate()
    return {
        phase
        for phase, occurred_on, precision in MatterProceduralDevelopment.objects.filter(
            matter=matter
        )
        .visible_to(user)
        .exclude(process_phase="")
        .values_list("process_phase", "occurred_on", "occurred_on_precision")
        if not period_starts_after(occurred_on, precision, day=today)
    }


def legal_process_rail(
    *,
    matter: Matter,
    user: Any,
    instrument_keys: frozenset[str] | None = None,
    context: PhaseContext | None = None,
    step_rows: tuple[dict[str, Any], list[Any]] | None = None,
) -> LegalProcessRail | None:
    """One Matter's `Menetluse kulg`, or ``None`` when nothing can be said.

    ``None`` — and therefore no section at all — where no pattern can be chosen,
    or where one can be chosen and the file records nothing that places it on one.
    Six nodes all reading `Teadmata` is a heading spent announcing that the
    application knows nothing, which is the standing empty section the approved
    target removed from this page everywhere else (docs/adr/0074 §15).

    ``context`` is the resolved pattern and stage, passed in by the Matter page so
    the rail and the grouped history below it cannot disagree about one file.
    ``step_rows`` is :func:`timeline_step_rows`, likewise read once by the page
    and handed to `matter_rail` too.
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
    phase_rows, _added = (
        step_rows if step_rows is not None else timeline_step_rows(matter=matter, user=user)
    )
    today = timezone.localdate()
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
    # **A third kind: a phase somebody dated, on a day that has come.** An
    # explicit roadmap date is a person stating when this part of the procedure
    # happened — the same statement `_keep_recorded_phases` already accepts for a
    # phase the pattern no longer draws (docs/adr/0119 §4). Reading it as
    # anything but reached put «VTK 1.9 · Tulevikus» on a file whose
    # väljatöötamiskavatsus went out on the first: `VTK` maps no stage, so on a
    # file still on `Idee` nothing else could ever mark it (JUR-CASE-10, UQ-05,
    # docs/adr/0128 §3). Like a recorded step it is an act, not a column, so the
    # stage demotion below does not touch it — and it is not the current node:
    # the file's position is still `Hetkeseis`. A hidden row is not evidence, and
    # a date still ahead is a plan.
    step_indexes |= {
        index
        for index, node in enumerate(nodes)
        if _reached_by_its_date(phase_rows.get(node.phase_key), today)
    }
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
        recorded_phases=frozenset(recorded_phases),
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
#: A step a person added to this file's rail (`+ Lisa samm`): a name, maybe a
#: date, and a place they chose. Stored as an added `MatterTimelineStep`
#: (docs/adr/0119).
KIND_STEP = "step"

#: The states that say a node has been reached — a phase that is current or
#: recorded, a dated point behind us or today. One set for every kind, because
#: no state is shared between kinds: a phase is never `past` and a dated point
#: is never `recorded`. An undated added step takes the phase words, because it
#: has no date to read against today.
_REACHED_STATES = frozenset({STATE_CURRENT, STATE_RECORDED, STATE_REACHED, STATE_TODAY})

#: The phase states a hide cannot take off the rail (docs/adr/0119 §3).
#:
#: A phase the file is standing on, or has recorded a step in, is part of what
#: happened. The panel refuses to hide the current one, and an old hide does not
#: outlive a later domain act either: once the file's `Hetkeseis` reaches a
#: phase somebody took off, or a step is recorded in it, the phase is drawn
#: again. Decided on read, so the stored preference is untouched and a page load
#: writes nothing.
_EVIDENCED_STATES = frozenset({STATE_CURRENT, STATE_RECORDED})


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
    #: us. Decided for the whole rail at once by `_one_backbone`, never per item:
    #: an item does not know which neighbour the merge will give it.
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


def _reached_by_its_date(row: Any, today: date) -> bool:
    """Whether a phase row is a person's statement that the phase has happened.

    Shown, dated, and that date has come — read through the strip's own
    `dated_state`, so a period is reached only once it has ended and a plan is
    never evidence (docs/adr/0123, docs/adr/0128 §3).
    """
    if row is None or row.hidden or row.occurs_on is None:
        return False
    return dated_state(row.occurs_on, row.occurs_on_precision, today) in (
        STATE_REACHED,
        STATE_TODAY,
    )


def _last_dated_phase_not_after(steps: list[RailStep], when: date, *, start: int) -> int | None:
    """The furthest phase from ``start`` on whose explicit date is not after ``when``.

    Only phases a person dated count — an undated one makes no claim about
    when anything happened. Explicit dates run in the procedure's order
    (docs/adr/0100 §5), so this is the last phase a point dated ``when`` is
    known to follow.
    """
    found = None
    for index in range(start, len(steps)):
        step = steps[index]
        if step.is_phase and step.sort_on is not None and step.sort_on <= when:
            found = index
    return found


def timeline_step_rows(*, matter: Matter, user: Any) -> tuple[dict[str, Any], list[Any]]:
    """This Matter's own timeline rows, read once for every surface that needs them.

    The rail's state (`legal_process_rail`), its placement (`matter_rail`) and
    the `+ Märge` offer to date a phase (`phase_date_offers`) all read the same
    rows; the Matter page reads them here once and hands them to each, so the
    page's query budget does not grow with the readers.
    """
    return _step_rows(matter=matter, user=user)


def _step_rows(*, matter: Matter, user: Any) -> tuple[dict[str, Any], list[Any]]:
    """This Matter's own timeline rows: phase rows by key, and added steps.

    One scoped read for both kinds. Added steps come back in the order they
    were created, which is the order `_place_added_steps` places them in
    (docs/adr/0119 §2).
    """
    from app.matters.models import MatterTimelineStep

    phases: dict[str, Any] = {}
    added: list[Any] = []
    rows = (
        MatterTimelineStep.objects.filter(matter=matter)
        .visible_to(user)
        .order_by("created_at", "id")
    )
    for row in rows:
        if row.is_added:
            added.append(row)
        else:
            phases[row.phase_key] = row
    return phases, added


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
    today = timezone.localdate()
    for phase_key, occurred_on, precision in rows:
        # A `Märge` dated ahead of today dates no phase until its day comes
        # (docs/adr/0121 §3): the rail's phase date is when it happened.
        if period_starts_after(occurred_on, precision, day=today):
            continue
        found.setdefault(phase_key, (occurred_on, precision))
    return found


def matter_rail(
    *,
    matter: Matter,
    user: Any,
    rail: LegalProcessRail | None,
    milestones: Any = (),
    step_rows: tuple[dict[str, Any], list[Any]] | None = None,
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

    **A phase node marks where its phase begins, and nothing reads before the
    beginning.** A point that has happened, with nothing in its window to date
    it, reads immediately before the current phase: the file may have reached
    that phase after the act, and nothing says otherwise. The one phase every act
    is known to follow is the pattern's *first*, because it is the beginning the
    procedure has (docs/adr/0100 §1) and an act on the file belongs to a
    procedure that has begun. So on a file still standing on its first phase the
    point reads inside it, after the node — never `Koja arvamus → Algus` on a
    file whose opinions went out after it opened. Only an explicit roadmap date
    on that phase can put a point before it, because that date is a person
    stating when the procedure began (docs/adr/0100, amended 2026-09-27).

    **Dated points never overtake each other.** Past points are placed earliest
    first, and each one's window runs on over the points already placed after
    the current phase, so two sent opinions read in the order they were sent.

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
    they were removing. **Except a phase the file has reached**: current, or
    recorded, is part of what happened, and a hide does not take it off
    (docs/adr/0119 §3).

    **A recorded phase the pattern no longer draws stays on the rail.** A file
    reclassified from `Seadus` to `Määrus` still went through what it went
    through (docs/adr/0119 §4).

    **Added steps go where a person put them**, after everything the file itself
    places (docs/adr/0119 §2).
    """
    rows, added = (
        step_rows if step_rows is not None else timeline_step_rows(matter=matter, user=user)
    )
    steps: list[RailStep] = []

    if rail is not None:
        for node in rail.nodes:
            row = rows.get(node.key)
            if row is not None and row.hidden and node.state not in _EVIDENCED_STATES:
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
                    # No `reach` here: every connector on this rail is decided
                    # once, after the dated points are placed (`_one_backbone`).
                )
            )

    today = timezone.localdate()
    _keep_recorded_phases(steps, matter=matter, user=user, rail=rail, rows=rows, today=today)
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
            detail=milestone.detail,
        )
        # Recomputed rather than carried, because placing a dated point that has
        # already happened moves the current node one to the right.
        current = next(
            (index for index, placed in enumerate(steps) if placed.state == STATE_CURRENT),
            len(steps),
        )
        # «Already happened» is the strip's own reading of the point, not a
        # second comparison of its date: for a day the two agree, and a period
        # has happened only once it has ended (docs/adr/0123). A commencement
        # known to a month used to be placed among what had happened from the
        # month's first day, while the strip still read it ahead.
        if milestone.state in _REACHED_STATES:
            # Up to and including the current phase, and on over the dated
            # points already placed after it — without them in the window a
            # later send was scanned against the phase alone and inserted in
            # front of the earlier send it followed.
            high = min(current + 1, len(steps))
            while high < len(steps) and not steps[high].is_phase:
                high += 1
            # **And on over every phase a person dated no later than this
            # point.** An explicit roadmap date is an anchor that sorts by date
            # (docs/adr/0100, amended 2026-09-27, rule 1) — but the window used
            # to stop one past the current phase, so on a file still on `Idee`
            # an anchor `VTK 1.9` was never scanned and `Tagasiside tähtaeg 6.9`
            # and `Koja arvamus 8.9` were drawn *before* it (JUR-CASE-10,
            # docs/adr/0128 §4). The window now reaches the last such phase,
            # and the dated points already placed after it; past that, an
            # undated phase or one dated later bounds it, as before.
            dated_reach = _last_dated_phase_not_after(steps, milestone.sort_on, start=high)
            if dated_reach is not None:
                high = dated_reach + 1
                while high < len(steps) and not steps[high].is_phase:
                    high += 1
            # Just before the current phase, but never before the beginning:
            # on a file still on its first phase, the point reads inside it.
            beginning = _beginning_slot(steps, rail)
            default = current if beginning is None else max(current, beginning + 1)
            window = (0, high)
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
            # window running from the current phase can hold the file's *past*
            # dated points — a sent opinion read inside the first phase, or
            # after a dated current phase. A commencement in 2027 then anchored
            # to whichever of those it was not earlier than, drawing it before
            # `Valitsuses`: a date two years out, before two phases nobody has
            # reached. Narrowed to its own phase, the only things it can sort
            # against are that phase and the commencements already beside it.
            #
            # A deadline with no phase of its own keeps the current phase's
            # window, and that is right: scanning it finds the sends already
            # read inside that phase and places the deadline after them.
            anchor = _phase_slot(steps, milestone)
            beside = current if anchor is None else max(anchor, current)
            window, default = (beside, len(steps)), min(beside + 1, len(steps))
        steps.insert(_slot_for(steps, milestone.sort_on, window, default), step)
    _place_added_steps(steps, added, rail=rail, today=today)
    return _one_backbone(steps, today)


def _keep_recorded_phases(
    steps: list[RailStep],
    *,
    matter: Matter,
    user: Any,
    rail: LegalProcessRail | None,
    rows: dict[str, Any],
    today: date,
) -> None:
    """Draw the phases this file went through that its pattern does not.

    The pattern is chosen from the file's *present* `Õigusakt` and
    `Menetlusliik`, so a file reclassified after the fact used to lose every
    phase the new pattern lacks — including ones it had recorded a step in, or
    that a person dated in the past. That is a completed milestone disappearing
    because a field changed, which the rail must not do (docs/adr/0119 §4).

    Two kinds of evidence, both explicit: a `Menetluse areng` filed in the
    phase, and a phase row somebody dated on or before today and did not hide.
    Nothing else — not the stage history, whose mapping onto a phase *is* the
    pattern, and not a future date, which is a plan for a procedure the file no
    longer reads against.

    They read as recorded, in the vocabulary's order, just before where the
    file now stands: they happened, and they happened before the present.
    """
    drawn = {node.key for node in rail.nodes} if rail is not None else set()
    recorded = (
        rail.recorded_phases
        if rail is not None
        else frozenset(recorded_phase_keys(matter=matter, user=user))
    )
    dated = {key for key, row in rows.items() if _reached_by_its_date(row, today)}
    kept = [key for key in PHASE_KEYS if key in (recorded | dated) and key not in drawn]
    if not kept:
        return
    position = next(
        (
            index
            for index, step in enumerate(steps)
            if step.state in (STATE_CURRENT, STATE_POSSIBLE)
        ),
        len(steps),
    )
    for offset, key in enumerate(kept):
        row = rows.get(key)
        dated_row = row if row is not None and not row.hidden else None
        steps.insert(
            position + offset,
            RailStep(
                key=key,
                label=phase_label(key),
                kind=KIND_PHASE,
                state=STATE_RECORDED,
                display_date=dated_row.display_date if dated_row is not None else "",
                sort_on=dated_row.occurs_on if dated_row is not None else None,
            ),
        )


def _place_added_steps(
    steps: list[RailStep], added: list[Any], *, rail: LegalProcessRail | None, today: date
) -> None:
    """Put every added step where a person placed it: after its ``after_key``.

    **A stated place, not a derived one.** The rail's own items are placed by the
    rules above; an added step is placed by what somebody chose — immediately
    after the item it names, or at the start for ``""``. Several steps naming one
    anchor read newest-nearest, which is what «right after X» means the second
    time somebody says it. Placed last, so no added step ever moves a phase or a
    dated point the file placed itself.

    **An anchor that is gone never drops the step** (docs/adr/0119 §2):

    * a phase taken off this file's rail — the step reads where the phase would
      have been, before the next phase of the pattern that is still drawn;
    * an anchor that is itself an added step waits for that step to be placed;
    * anything else — a dated point that moved, a phase of a pattern the file no
      longer reads against, a loop — falls back to the step's own date among
      the dated items, and an undated step to the end.

    Deterministic and read-only: the stored anchor stays as the person left it.
    """
    if not added:
        return
    order = [node.key for node in rail.nodes] if rail is not None else []
    pending = list(added)
    while pending:
        waiting = {row.rail_key for row in pending}
        progressed = False
        for row in list(pending):
            index = _anchor_index(steps, row, order, waiting)
            if index is None:
                continue
            steps.insert(index, _added_step(row, today))
            pending.remove(row)
            waiting.discard(row.rail_key)
            progressed = True
        if not progressed:
            # A loop — two steps each placed after the other. The date decides.
            for row in pending:
                steps.insert(_fallback_index(steps, row), _added_step(row, today))
            break
    _state_undated_added_steps(steps)


def _anchor_index(
    steps: list[RailStep], row: Any, order: list[str], waiting: set[str]
) -> int | None:
    """Where ``row`` goes now, or ``None`` while its anchor is still to be placed."""
    anchor = row.after_key
    if anchor == "":
        return 0
    if anchor != row.rail_key:
        for index, placed in enumerate(steps):
            if placed.key == anchor:
                return index + 1
        if anchor in waiting:
            return None
    if anchor in order:
        # A phase of this pattern that is not drawn: where it would have been.
        drawn = {placed.key: index for index, placed in enumerate(steps) if placed.is_phase}
        position = order.index(anchor)
        for later in order[position + 1 :]:
            if later in drawn:
                return drawn[later]
        for earlier in reversed(order[:position]):
            if earlier in drawn:
                return drawn[earlier] + 1
    return _fallback_index(steps, row)


def _fallback_index(steps: list[RailStep], row: Any) -> int:
    """An added step whose anchor is gone: by its date, or at the end."""
    if row.occurs_on is None:
        return len(steps)
    return _slot_for(steps, row.occurs_on, (0, len(steps)), len(steps))


def _added_step(row: Any, today: date) -> RailStep:
    """One added `MatterTimelineStep`, as the rail draws it.

    A dated step reads against today exactly as a dated point does — through
    the strip's own `dated_state`, not a copy of it. An undated one is given its
    state once every step is placed, by where it sits
    (`_state_undated_added_steps`).

    **A step dated as a period is never today** (docs/adr/0123). It used to
    compare its anchor, so «Istung oktoober 2026» carried `aria-current="date"`
    on 1 October — a day nobody named — and read as reached from the 2nd. It is
    reached once October is over, as a period column of the strip is, and it
    sits at the period's last day for the same reason that column does: the
    fill running into a step still ahead must not already be solid. Where the
    step is *placed* is unchanged — the place a person chose, or its own date
    when that anchor is gone (`_fallback_index`).
    """
    when = row.occurs_on
    if when is None:
        state, sort_on = STATE_POSSIBLE, None
    else:
        state = dated_state(when, row.occurs_on_precision, today)
        sort_on = period_end_for(when, row.occurs_on_precision)
    return RailStep(
        key=row.rail_key,
        label=row.title,
        kind=KIND_STEP,
        state=state,
        display_date=row.display_date,
        sort_on=sort_on,
    )


def _state_undated_added_steps(steps: list[RailStep]) -> None:
    """An undated added step reads recorded behind the file's position, possible after.

    The position is the current phase, or — on a rail with none — just past the
    last dated item already behind us. A person put the step there, and a step
    placed behind where the file stands is one they are saying happened; nothing
    about it is inferred beyond the place they chose.
    """
    position = next(
        (index for index, step in enumerate(steps) if step.state == STATE_CURRENT), None
    )
    if position is None:
        position = max(
            (
                index + 1
                for index, step in enumerate(steps)
                if step.state in _REACHED_STATES and step.sort_on is not None
            ),
            default=0,
        )
    for index, step in enumerate(steps):
        if step.kind == KIND_STEP and step.sort_on is None:
            steps[index] = replace(
                step, state=STATE_RECORDED if index < position else STATE_POSSIBLE
            )


def _one_backbone(steps: list[RailStep], today: date) -> list[RailStep]:
    """Decide every connector on the merged rail at once: one solid run, then muted.

    **The solid run starts at the first node something proves was reached** — a
    `Kirjas` or `Praegu` phase, or a dated point already behind us — and **ends
    at the file's position**: the `Praegu` node, or a dated point that happened
    after it. Everything inside the run is solid, everything outside is muted,
    and only the connector leaving the last node of the run may be part-filled.
    So the rail reads reached → current → ahead exactly once, whatever it holds.

    It used to be decided per node, and on a file with several sent opinions
    that drew several runs. Each dated point carried the fill the strip had
    measured against *its own* next dated point — so the last `Koja arvamus`
    before the current phase was filled a third of the way towards a deadline
    three columns further on, stopped, and the current phase then started a
    second solid run into the phase after it. Four solid connectors, a gap, and
    accent leading into a node nobody has reached. The fill is a fraction of
    *that segment's* days (docs/adr/0074 §12.2), and after the merge a dated
    point's next neighbour is frequently a phase.

    Presentation only, as the strip's own reading is: no node changes state,
    label, date or position here, and the input is the already-scoped list, so a
    restricted child a reader may not see cannot move a single connector
    (docs/adr/0074 §13).

    **The late-entry rule is untouched** (docs/adr/0092 §13). An earlier phase
    with no evidence keeps its hollow, dashed `Teadmata` node. On the file that
    rule is about — first filed when the bill was already in the Riigikogu —
    nothing before `Praegu` is reached, so the run is empty and every connector
    before it stays muted, as it always did. A `Teadmata` node *between* two
    reached nodes sits on the run, because the line is the file's course
    through time and time did pass there; the node still says, in its own
    drawing and words, that nobody recorded it.

    A rail with no phases is the strip exactly as it was: the run ends at the
    last dated point behind us, and today's place in the next segment is its
    part-filled connector.
    """
    if not steps:
        return steps
    current = next((index for index, step in enumerate(steps) if step.state == STATE_CURRENT), None)
    reached = [index for index, step in enumerate(steps) if step.state in _REACHED_STATES]
    if not reached:
        return [replace(step, reach=0.0) for step in steps]
    # A `Kirjas` phase to the right of `Praegu` — a file that went back — keeps
    # its filled node but does not pull the run past where the file now stands.
    ends = [
        index
        for index in reached
        if current is None or index == current or not steps[index].is_phase
    ]
    start, end = reached[0], max(ends)
    decided: list[RailStep] = []
    for index, step in enumerate(steps):
        if start <= index < end:
            reach = 1.0
        elif index == end and index + 1 < len(steps):
            reach = _reach_between(step.sort_on, steps[index + 1].sort_on, today)
        else:
            reach = 0.0
        decided.append(replace(step, reach=reach))
    return decided


def _reach_between(start: date | None, end: date | None, today: date) -> float:
    """How far today has come along one segment, 0 to 1, if both ends are dated.

    An undated end is not a point in time, so it divides nothing and the segment
    leaving the position stays muted: the file is *at* its current phase, and a
    connector already coloured towards the next one would say it had moved on.
    The whole-segment answers come first, so a zero-length segment never
    divides (docs/adr/0074 §12.2).
    """
    if start is None or end is None:
        return 0.0
    if today >= end:
        return 1.0
    if today <= start:
        return 0.0
    return (today - start).days / (end - start).days


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


def _beginning_slot(steps: list[RailStep], rail: LegalProcessRail | None) -> int | None:
    """Where the pattern's first phase is drawn, or ``None`` where it is not.

    The first phase is the beginning the procedure has (docs/adr/0100 §1), and
    the one phase every act on the file is known to follow. Found by its key on
    the *pattern*, never by taking whatever happens to be leftmost: a first phase
    somebody took off this file's rail is not replaced by the next one, and a
    rail with no pattern has no beginning to respect.
    """
    if rail is None or not rail.nodes:
        return None
    first = rail.nodes[0].key
    return next(
        (index for index, placed in enumerate(steps) if placed.is_phase and placed.key == first),
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
    the window dates anything»: for a point already behind us, just before the
    current phase — or just after it, where the current phase is the pattern's
    first and therefore the beginning; for a point still ahead, the slot just
    past the current phase, or just past the point's own phase where its kind
    names one.
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
    "KIND_STEP",
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
