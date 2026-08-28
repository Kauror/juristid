"""Turning the deterministic half of ``JÄRGMISEKS`` into structured work.

:mod:`app.legacy_import.register_next_actions` reads one sentence. This module
decides which sentences may be read at all, what the whole set would produce,
and — behind two digests — writes it.

The population is narrow on purpose
-----------------------------------
Only the approved snapshot's ``CURRENT`` rows, on open ``FULL`` Matters, with
something written in the cell. A retired row's instruction is a note about
finished work; an archive row has no work queue to join; a closed Matter must
not acquire an open instruction, and the service would refuse it anyway.

Human work always wins — and only human work stops a refresh
-----------------------------------------------------------
The rule was "a Matter carrying **any** ``NextAction`` is skipped", and the
principle behind it is right: a completed action proves somebody worked this
file through the structured workflow, and replacing their decision with a
sentence from a spreadsheet would be the enrichment overruling the department.

What the rule could not survive was a *second* snapshot. Once this operation has
run, every Matter it converted carries an action — its own — so a refresh from a
newer workbook skipped precisely the rows it had most recently spoken about, and
the department was left with review dates from August in October with nothing
able to move them. A rule that makes an operation unrepeatable is not protecting
anybody's work; it is protecting the operation's own output from itself.

So the question is no longer *is there an action* but *did a person do anything
here*, and it is answered from provenance rather than from existence
(:func:`action_ownership`). An action is the register's own only when its
``NEXT_ACTION_SET`` event named ``CURRENT_REGISTER`` and carried no actor, and it
stays the register's own only while every later event about it also carried no
actor. One signed-in person anywhere in that history — creating, completing,
cancelling or superseding — and the whole Matter is theirs, permanently.

Four outcomes follow, and each names what provenance proved:

``NEW_AUTO``
    No action has ever existed. The original case, unchanged.
``REFRESH_IMPORTED``
    The open action is the register's own, from an older snapshot, untouched,
    and the newer workbook reads differently. It is superseded — the chain and
    the audit history stay — rather than edited.
``IMPORTED_UP_TO_DATE``
    The same, and the newer workbook reads *identically*. Nothing is written,
    which is what makes re-running the same snapshot free (brief 33).
``REMOVE_STALE_IMPORTED``
    The instruction that produced the open action is gone from the newer
    workbook, or no longer readable. Cancelled, with provenance, because the
    alternative is a machine-written instruction nobody can account for
    surviving on somebody's list forever (brief 19D).

``HUMAN_WINS`` covers every case where a person appears anywhere in the history,
and it is checked before all four.

Nothing is rewritten
--------------------
``CurrentRegisterState.next_action_text`` keeps the register's wording after an
action is created from it. The two are different claims — *the register says
this* and *Koda has decided this* — and the first stays true whatever happens to
the second. The created action carries the sentence verbatim in ``source_text``
as well, so the evidence travels with the interpretation (brief 7, 14, 73).

Two pins, not one
-----------------
A plan is computed against a named snapshot digest, and applied against a named
*plan* digest. The first says which workbook the interpretation came from; the
second says that nothing in the database moved between deciding and writing. A
mismatch on either is a refusal, and the refusal is total — no partial writes
(brief 29, 33, 82).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_next_actions import (
    NO_CONTEXT,
    REGISTER_NEXT_ACTION_PARSER_VERSION,
    ParseContext,
    ParsedInstruction,
    Verdict,
    parse_instruction,
)
from app.matters.enums import MatterDataClass, RecordMode
from app.matters.models import Matter
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import cancel_next_action, set_next_action

#: What the audit provenance calls this source. One token, so a future query for
#: "which actions did the register write" is exact rather than a text search.
ENRICHMENT_SOURCE = "CURRENT_REGISTER"


class Outcome:
    """What the planner decided about one register instruction.

    Every rejection has a name. A report that said only "40 of 134 converted"
    would leave the operator no way to tell a rule that is too strict from a
    corpus that is genuinely ambiguous (brief 11).
    """

    #: The register speaks and no action has ever existed here.
    AUTO = "AUTO"
    #: An action this operation wrote from an older snapshot, untouched by
    #: anybody, that the newer snapshot reads differently. Superseded.
    REFRESH_IMPORTED = "REFRESH_IMPORTED"
    #: The same action, and the newer snapshot reads it identically. No write,
    #: and the reason a second run of one snapshot changes nothing.
    IMPORTED_UP_TO_DATE = "IMPORTED_UP_TO_DATE"
    #: An untouched imported action whose instruction the newer snapshot no
    #: longer states, or no longer states readably. Cancelled with provenance.
    REMOVE_STALE_IMPORTED = "REMOVE_STALE_IMPORTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    #: Understood, and its whole period is already behind us. Creating it today
    #: would put a stale instruction on somebody's list as though it were new.
    STALE_SOURCE = "STALE_SOURCE"
    SKIP_EMPTY = "SKIP_EMPTY"
    SKIP_NOT_CURRENT = "SKIP_NOT_CURRENT"
    SKIP_CLOSED = "SKIP_CLOSED"
    SKIP_ARCHIVE_RECORD = "SKIP_ARCHIVE_RECORD"
    SKIP_TEST_DATA = "SKIP_TEST_DATA"
    #: A person appears somewhere in this Matter's action history. Named for
    #: what it protects rather than for what it looked at; the old
    #: ``SKIP_EXISTING_ACTION_HISTORY`` described a test that is no longer the
    #: one being run.
    HUMAN_WINS = "HUMAN_WINS"


#: Fixed order, for a report that reads the same way every run.
OUTCOMES: tuple[str, ...] = (
    Outcome.AUTO,
    Outcome.REFRESH_IMPORTED,
    Outcome.IMPORTED_UP_TO_DATE,
    Outcome.REMOVE_STALE_IMPORTED,
    Outcome.REVIEW_REQUIRED,
    Outcome.STALE_SOURCE,
    Outcome.HUMAN_WINS,
    Outcome.SKIP_NOT_CURRENT,
    Outcome.SKIP_CLOSED,
    Outcome.SKIP_ARCHIVE_RECORD,
    Outcome.SKIP_TEST_DATA,
    Outcome.SKIP_EMPTY,
)

#: The outcomes an apply actually writes. Everything else is a report line.
WRITING_OUTCOMES: frozenset[str] = frozenset(
    {Outcome.AUTO, Outcome.REFRESH_IMPORTED, Outcome.REMOVE_STALE_IMPORTED}
)


class MixedSnapshot(Exception):
    """The derived register state was built from more than one workbook.

    Fail closed. A plan half-derived from an older snapshot would carry
    instructions the approved workbook no longer contains, and no digest could
    tell afterwards which half was which.
    """


class UnknownSnapshot(Exception):
    """No derived register state carries the digest the operator named."""


class PlanChanged(Exception):
    """The database moved between planning and applying. Nothing was written."""


@dataclass(frozen=True)
class Proposal:
    """What the planner would do about one register instruction."""

    matter_id: UUID
    matter_reference: str
    source_reference_id: UUID
    snapshot_sha256: str
    #: The sentence's identity, not the sentence. A plan file an operator can
    #: keep may hold this; it may not hold the register's own prose (brief 31).
    source_text_sha256: str
    outcome: str
    review_reasons: tuple[str, ...] = field(default_factory=tuple)
    kind: str = ""
    date_semantics: str = ""
    target_date: dt.date | None = None
    date_precision: str = ""
    responsible_id: UUID | None = None
    #: The open action this proposal supersedes or cancels, when there is one
    #: and it is the register's own. ``None`` for a first conversion.
    existing_action_id: UUID | None = None

    @property
    def is_auto(self) -> bool:
        return self.outcome == Outcome.AUTO

    @property
    def writes(self) -> bool:
        """Whether applying this proposal changes anything."""
        return self.outcome in WRITING_OUTCOMES

    def digest_row(self) -> dict[str, Any]:
        """The fields that make two proposals the same proposal.

        Identity, source, reading and responsibility. Deliberately not the
        outcome counters or the report ordering: a digest has to change when the
        *work* changes and stay still when only the presentation does.
        """
        return {
            "matter_id": str(self.matter_id),
            # Deliberately absent: a plan projected before the cutover has run
            # names rows that do not exist yet, and a digest that moved when
            # they were finally written would make the dry-run unapprovable.
            # Identity here is the Matter and the sentence, both of which the
            # apply re-reads and re-checks.
            "source_reference_id": str(self.source_reference_id),
            "snapshot_sha256": self.snapshot_sha256,
            "source_text_sha256": self.source_text_sha256,
            "kind": self.kind,
            "date_semantics": self.date_semantics,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "date_precision": self.date_precision,
            "responsible_id": str(self.responsible_id) if self.responsible_id else None,
            # Two proposals that read the same sentence identically are still
            # different work when one creates an action and the other withdraws
            # one, so the outcome is inside the digest for the writing set.
            "outcome": self.outcome,
            "existing_action_id": (
                str(self.existing_action_id) if self.existing_action_id else None
            ),
        }


def source_text_digest(text: str) -> str:
    """A stable identity for one source sentence.

    The text itself is the register's content and stays where it is; this is
    what a plan, a digest and an operator report may carry instead (brief 31).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EnrichmentPlan:
    snapshot_sha256: str
    parser_version: str
    today: dt.date
    proposals: tuple[Proposal, ...]

    @property
    def auto(self) -> tuple[Proposal, ...]:
        return tuple(proposal for proposal in self.proposals if proposal.is_auto)

    @property
    def writing(self) -> tuple[Proposal, ...]:
        """Every proposal an apply would act on, in a stable order.

        Creations, refreshes and withdrawals together. The digest used to cover
        creations alone, which was complete when creating was the only thing
        this operation could do; a refresh that superseded forty actions under a
        digest that had never seen them would be approving a plan by looking at
        a different one.
        """
        return tuple(
            sorted(
                (proposal for proposal in self.proposals if proposal.writes),
                key=lambda proposal: str(proposal.matter_id),
            )
        )

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of the complete writing set.

        Sorted before hashing, so two runs over an unchanged database agree
        whatever order the rows came back in. The parser version is inside the
        digest rather than beside it: the same sentences read by different rules
        are a different plan, and an apply guarded only by row identity would
        not notice.
        """
        body = {
            "snapshot_sha256": self.snapshot_sha256,
            "parser_version": self.parser_version,
            "proposals": sorted(
                (proposal.digest_row() for proposal in self.writing),
                key=lambda row: (row["matter_id"], row["outcome"]),
            ),
        }
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Whose action is this?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionOwnership:
    """Who has touched one Matter's next-action history.

    Two facts, and the first is the gate. ``human_touched`` says a signed-in
    person appears somewhere in this Matter's action history, ever; when it is
    true nothing else here matters and the register may not speak. ``open_own``
    is the open action this operation wrote and nobody has since touched — the
    only action a refresh may supersede or withdraw.
    """

    human_touched: bool
    open_own: NextAction | None
    #: The snapshot the open action was written from, when it is our own.
    open_snapshot_sha256: str = ""
    #: The digest of the sentence it was written from.
    open_text_sha256: str = ""

    @property
    def refreshable(self) -> bool:
        return not self.human_touched and self.open_own is not None


_EMPTY_OWNERSHIP = ActionOwnership(human_touched=False, open_own=None)

#: The events that say something happened to an action. A non-null actor on any
#: of them is a person, and one person is enough.
_LIFECYCLE_EVENTS: tuple[str, ...] = (
    ChangeEventType.NEXT_ACTION_SET,
    ChangeEventType.NEXT_ACTION_COMPLETED,
    ChangeEventType.NEXT_ACTION_CANCELLED,
    ChangeEventType.NEXT_ACTION_REVIEWED,
)


def action_ownership(matter_ids: list[Any]) -> dict[Any, ActionOwnership]:
    """Who owns each Matter's action history. Two queries for the whole set.

    Existence is not the question and never was; *authorship* is. The audit log
    already answers it exactly — ``set_next_action`` records the enrichment's
    own token under ``payload.provenance.source`` and leaves ``actor`` null,
    and every surface a person uses passes ``actor`` — so this reads the answer
    rather than inferring one from ``created_by`` alone, which a future caller
    could set to null without meaning anything by it.

    Three rules, and each closes a case the others do not:

    * an action with **no** ``CURRENT_REGISTER`` set-event is not ours, whoever
      made it, and that includes a seed command and a future importer;
    * an action whose set-event carried an ``actor`` is a person's, even if the
      provenance is there — that combination cannot arise today and reading it
      as machine-made would be the wrong direction to be wrong in;
    * any later event with an ``actor`` — completed, cancelled, reviewed, or the
      supersession recorded when a person set a new action — makes the whole
      Matter a person's, permanently.
    """
    if not matter_ids:
        return {}

    actions = list(
        NextAction.objects.filter(matter_id__in=matter_ids).only(
            "id", "matter_id", "status", "ended_by_id", "created_by_id"
        )
    )
    if not actions:
        return {}

    events = ChangeEvent.objects.filter(
        object_id__in=[action.id for action in actions],
        event_type__in=_LIFECYCLE_EVENTS,
    ).values("object_id", "event_type", "actor_id", "payload")

    machine_set: set[Any] = set()
    person_touched: set[Any] = set()
    provenance: dict[Any, dict[str, str]] = {}
    for event in events:
        identifier = event["object_id"]
        if event["actor_id"] is not None:
            person_touched.add(identifier)
            continue
        if event["event_type"] != ChangeEventType.NEXT_ACTION_SET:
            continue
        payload = event["payload"] or {}
        source = (payload.get("provenance") or {}).get("source")
        if source == ENRICHMENT_SOURCE:
            machine_set.add(identifier)
            provenance[identifier] = payload.get("provenance") or {}

    by_matter: dict[Any, list[NextAction]] = {}
    for action in actions:
        by_matter.setdefault(action.matter_id, []).append(action)

    ownership: dict[Any, ActionOwnership] = {}
    for matter_id, rows in by_matter.items():
        human = False
        for action in rows:
            if action.id not in machine_set:
                human = True
                break
            if action.id in person_touched or action.ended_by_id is not None:
                human = True
                break
            if action.created_by_id is not None:  # pragma: no cover - belt and braces
                human = True
                break

        open_own = None
        if not human:
            open_own = next((action for action in rows if action.status == ActionStatus.OPEN), None)
        payload = provenance.get(open_own.id, {}) if open_own is not None else {}
        ownership[matter_id] = ActionOwnership(
            human_touched=human,
            open_own=open_own,
            open_snapshot_sha256=str(payload.get("source_snapshot_sha256") or ""),
            open_text_sha256=str(payload.get("source_text_sha256") or ""),
        )
    return ownership


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _matches(action: NextAction, parsed: ParsedInstruction, text: str) -> bool:
    """Whether the open imported action already says exactly this.

    Every field the apply would write, compared against what is stored. If they
    agree there is nothing to do, and doing it anyway would supersede a row,
    write an audit event and change a Matter's ``updated_at`` for no change at
    all — which is precisely the re-run safety the operation is required to have
    (brief 33).
    """
    return (
        action.text.strip() == text
        and action.kind == parsed.kind
        and action.date_semantics == parsed.date_semantics
        and action.target_date == parsed.target_date
        and action.date_precision == (parsed.date_precision or DatePrecision.EXACT.value)
    )


def _classify(
    state: CurrentRegisterState,
    matter: Matter,
    *,
    ownership: ActionOwnership,
    context: ParseContext,
    today: dt.date,
) -> tuple[str, ParsedInstruction]:
    """One instruction's outcome, in a fixed order.

    Eligibility before precedence before reading, and each step can only
    narrow. The order is not arbitrary: a closed Matter cannot take an action at
    all, so asking the parser about it would produce a proposal nothing could
    ever apply, and the report would count work that was never available.

    One exception to "empty means nothing to do", and it is the whole of case
    19D: a row whose instruction the newer workbook has *removed*, on a Matter
    still carrying the action the older workbook produced. Returning early there
    would leave a machine-written instruction on somebody's list with no source
    behind it and nothing that could ever take it off again.
    """
    text = state.next_action_text.strip()
    empty = ParsedInstruction(
        source_text=text,
        parser_version=REGISTER_NEXT_ACTION_PARSER_VERSION,
        verdict=Verdict.EMPTY,
    )

    # A person's decisions outrank the register everywhere, including on a row
    # the register no longer speaks about.
    if ownership.human_touched:
        return Outcome.HUMAN_WINS, empty

    ours = ownership.open_own
    eligible = (
        state.currency == RegisterCurrency.CURRENT
        and matter.data_class != MatterDataClass.TEST
        and matter.is_open
        and matter.record_mode == RecordMode.FULL
    )

    if not eligible:
        # Report why, and — where we put an action there and the Matter has
        # since left the population — withdraw it rather than leave it behind.
        if ours is not None:
            return Outcome.REMOVE_STALE_IMPORTED, empty
        if not text:
            return Outcome.SKIP_EMPTY, empty
        if state.currency != RegisterCurrency.CURRENT:
            return Outcome.SKIP_NOT_CURRENT, empty
        if matter.data_class == MatterDataClass.TEST:
            return Outcome.SKIP_TEST_DATA, empty
        if not matter.is_open:
            return Outcome.SKIP_CLOSED, empty
        return Outcome.SKIP_ARCHIVE_RECORD, empty

    if not text:
        return (Outcome.REMOVE_STALE_IMPORTED if ours is not None else Outcome.SKIP_EMPTY), empty

    parsed = parse_instruction(text, context=context)
    if not parsed.is_understood or parsed.is_stale(today):
        unreadable = Outcome.REVIEW_REQUIRED if not parsed.is_understood else Outcome.STALE_SOURCE
        # A sentence we can no longer read, on an action we wrote from a
        # sentence we could. The action is not evidence of anything any more.
        return (Outcome.REMOVE_STALE_IMPORTED if ours is not None else unreadable), parsed

    if ours is None:
        return Outcome.AUTO, parsed
    if _matches(ours, parsed, text):
        return Outcome.IMPORTED_UP_TO_DATE, parsed
    return Outcome.REFRESH_IMPORTED, parsed


def _context_for(sheet: str, snapshot_date: dt.date | None) -> ParseContext:
    """The parse context for one derived row.

    The sheet name is the year the row's work belongs to; the snapshot date is
    when the workbook was taken. A sheet that is not a year — and there is one
    in this workbook, *Hetkeseisu info* — yields no context at all, so a
    year-less date on it stays refused (``ParseContext.yearless_year``).
    """
    if snapshot_date is None:
        return NO_CONTEXT
    text = (sheet or "").strip()
    return ParseContext(
        sheet_year=int(text) if text.isdigit() else None, snapshot_date=snapshot_date
    )


def build_plan(
    *,
    snapshot_sha256: str,
    today: dt.date | None = None,
    states: list[CurrentRegisterState] | None = None,
) -> EnrichmentPlan:
    """Read the database and decide everything. Writes nothing.

    The snapshot pin is checked against the derived state table rather than
    trusted: ``CurrentRegisterState`` is rebuilt wholesale by the register
    cutover, so a row carrying a different digest means two cutovers left the
    table describing two workbooks at once, and no plan over it could say which
    one it spoke for.

    ``states`` lets the caller supply *projected* rows instead — the ones the
    cutover would write, computed by
    :func:`app.legacy_import.final_cutover.projected_state_rows` and saved
    nowhere. That is the only way a refresh can be reported in full before
    anything is applied: the table still describes the previous workbook at that
    moment, and a report built from it would answer about the wrong snapshot.
    Supplying rows skips the pin check because there is nothing yet to check it
    against, so the caller is responsible for the digest it names — the composed
    refresh plan does that in one place
    (:mod:`app.legacy_import.register_refresh`).
    """
    digest = (snapshot_sha256 or "").strip().lower()
    today = today or timezone.localdate()

    if states is None:
        present = set(
            CurrentRegisterState.objects.values_list("source_snapshot_sha256", flat=True).distinct()
        )
        if not present:
            raise UnknownSnapshot(
                "No derived register state exists. Run final_register_cutover first."
            )
        if digest not in present:
            raise UnknownSnapshot(
                f"No derived register state carries snapshot {digest[:16]}…; "
                f"the database holds {len(present)}."
            )
        if len(present) > 1:
            raise MixedSnapshot(
                "Derived register state carries more than one snapshot digest "
                f"({len(present)}). Rebuild it from a single approved workbook."
            )

        states = list(
            CurrentRegisterState.objects.filter(source_snapshot_sha256=digest)
            .select_related("matter")
            .order_by("matter_id")
        )
    else:
        states = sorted(states, key=lambda state: str(state.matter_id))
    matter_ids = [state.matter_id for state in states]
    # Two queries for the whole precedence question rather than two per Matter.
    ownership = action_ownership(matter_ids)

    # The snapshot's own date, so a year-less instruction on the sheet of the
    # same year can be read. Absent for a workbook nobody approved, which turns
    # that reading off rather than guessing at it — an unreviewed snapshot has
    # no date this codebase knows.
    from app.legacy_import.final_cutover import reviewed_snapshot

    approved = reviewed_snapshot(digest)
    snapshot_date = approved.snapshot_date if approved else None

    proposals: list[Proposal] = []
    for state in states:
        matter = state.matter
        own = ownership.get(state.matter_id, _EMPTY_OWNERSHIP)
        outcome, parsed = _classify(
            state,
            matter,
            ownership=own,
            context=_context_for(state.source_sheet, snapshot_date),
            today=today,
        )
        proposals.append(
            Proposal(
                matter_id=state.matter_id,
                matter_reference=matter.display_reference,
                # Deliberately no state id. A plan may be built over *projected*
                # rows, which the refresh derives twice — once to report and once
                # to write — so any id carried here would name a row that never
                # existed. The apply re-reads the real row by Matter and checks
                # its snapshot, which is the identity that actually holds.
                source_reference_id=state.source_reference_id,
                snapshot_sha256=state.source_snapshot_sha256,
                source_text_sha256=source_text_digest(state.next_action_text.strip()),
                outcome=outcome,
                review_reasons=parsed.review_reasons,
                kind=parsed.kind,
                date_semantics=parsed.date_semantics,
                target_date=parsed.target_date,
                date_precision=parsed.date_precision,
                responsible_id=matter.owner_id,
                existing_action_id=own.open_own.id if own.open_own is not None else None,
            )
        )

    return EnrichmentPlan(
        snapshot_sha256=digest,
        parser_version=REGISTER_NEXT_ACTION_PARSER_VERSION,
        today=today,
        proposals=tuple(proposals),
    )


def summary(plan: EnrichmentPlan) -> dict[str, Any]:
    """Aggregates only. No titles, no source sentences, no lawyer names.

    The instruction text is the register's content and the report is a file
    somebody may email; the two do not belong together (brief 31, 91).
    """
    outcomes = Counter(proposal.outcome for proposal in plan.proposals)
    reasons: Counter[str] = Counter()
    for proposal in plan.proposals:
        if proposal.outcome == Outcome.REVIEW_REQUIRED:
            reasons.update(proposal.review_reasons)

    auto = plan.auto
    return {
        "parser_version": plan.parser_version,
        "writes": len(plan.writing),
        "snapshot_sha256": plan.snapshot_sha256,
        "evaluated_on": plan.today.isoformat(),
        "plan_sha256": plan.digest,
        "register_rows": len(plan.proposals),
        "source_instructions": sum(
            1 for proposal in plan.proposals if proposal.outcome != Outcome.SKIP_EMPTY
        ),
        "outcomes": {name: outcomes.get(name, 0) for name in OUTCOMES},
        "review_reasons": dict(sorted(reasons.items())),
        "kinds": dict(sorted(Counter(item.kind for item in auto).items())),
        "date_semantics": dict(sorted(Counter(item.date_semantics for item in auto).items())),
        "date_precisions": dict(
            sorted(Counter(item.date_precision for item in auto if item.target_date).items())
        ),
        "auto_without_date": sum(1 for item in auto if item.target_date is None),
        "auto_without_responsible": sum(1 for item in auto if item.responsible_id is None),
    }


def protected_rows(plan: EnrichmentPlan) -> list[dict[str, Any]]:
    """Per-proposal detail for an operator review file.

    Carries the Matter's stable identity and the reading, and the sentence only
    as a hash. Somebody who has to see the wording opens the Matter, where the
    register's instruction is already displayed and already authorised.
    """
    return [
        {
            "matter_id": str(proposal.matter_id),
            "reference": proposal.matter_reference,
            "outcome": proposal.outcome,
            "review_reasons": list(proposal.review_reasons),
            "source_text_sha256": proposal.source_text_sha256,
            "kind": proposal.kind,
            "date_semantics": proposal.date_semantics,
            "target_date": proposal.target_date.isoformat() if proposal.target_date else None,
            "date_precision": proposal.date_precision,
            "responsible_resolved": proposal.responsible_id is not None,
            "supersedes_action_id": (
                str(proposal.existing_action_id) if proposal.existing_action_id else None
            ),
        }
        for proposal in plan.proposals
    ]


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplyResult:
    created: int
    snapshot_sha256: str
    plan_sha256: str
    parser_version: str
    #: Imported actions superseded by a newer reading of a newer workbook.
    refreshed: int = 0
    #: Imported actions withdrawn because the newer workbook no longer states
    #: a readable instruction behind them.
    withdrawn: int = 0

    @property
    def touched(self) -> int:
        return self.created + self.refreshed + self.withdrawn


def _provenance(proposal: Proposal) -> dict[str, Any]:
    """The audit payload for one write.

    Identifiers and the reading, never the sentence — that stays on the derived
    state row and on the action's own ``source_text``, both of which are already
    authorised through the Matter.

    ``outcome`` is in here because a refresh and a first conversion are
    indistinguishable from the event alone otherwise, and the difference is
    exactly what somebody auditing "why did this date move" needs to see.
    """
    return {
        "source": ENRICHMENT_SOURCE,
        "source_snapshot_sha256": proposal.snapshot_sha256,
        "source_reference_id": str(proposal.source_reference_id),
        "source_text_sha256": proposal.source_text_sha256,
        "parser_version": REGISTER_NEXT_ACTION_PARSER_VERSION,
        "outcome": proposal.outcome,
        "supersedes_action_id": (
            str(proposal.existing_action_id) if proposal.existing_action_id else None
        ),
    }


@transaction.atomic
def apply_plan(plan: EnrichmentPlan, *, expect_plan_sha256: str, actor: Any = None) -> ApplyResult:
    """Create the AUTO actions, or nothing at all.

    Everything is re-verified inside the transaction — the digest, then each
    row's eligibility and its source text — because a plan is a photograph and
    the apply happens later. A single changed row aborts the whole run rather
    than being skipped: a partial apply against a digest the operator approved
    would leave a state neither the plan nor the database describes (brief 82).

    ``actor`` is ``None`` and stays ``None``. Nobody signed in decided these,
    and attributing them to whoever ran the command would put a person's name
    on a machine's reading. What did decide them is in the provenance.
    """
    expected = (expect_plan_sha256 or "").strip().lower()
    if plan.digest != expected:
        raise PlanChanged(
            f"Plan digest {plan.digest[:16]}… does not match the approved "
            f"{expected[:16] or '(none)'}…. Nothing was written."
        )

    writing = plan.writing
    ownership = action_ownership([proposal.matter_id for proposal in writing])

    created = refreshed = withdrawn = 0
    for proposal in writing:
        matter = Matter.objects.select_for_update().get(pk=proposal.matter_id)
        state = CurrentRegisterState.objects.filter(matter_id=proposal.matter_id).first()
        if state is None:
            raise PlanChanged(f"{proposal.matter_reference}: register state row is gone.")
        if state.source_snapshot_sha256 != proposal.snapshot_sha256:
            raise PlanChanged(
                f"{proposal.matter_reference}: the derived state now describes another "
                "snapshot. Nothing was written."
            )
        text = state.next_action_text.strip()

        # The source has to still say what the plan read, whichever direction
        # the proposal goes. A withdrawal approved because the cell was emptied
        # must not run against a cell somebody has since refilled.
        if source_text_digest(text) != proposal.source_text_sha256:
            raise PlanChanged(
                f"{proposal.matter_reference}: the register text changed after the "
                "plan was made. Nothing was written."
            )

        # Re-read authorship inside the transaction. This is the check the old
        # `NextAction.objects.filter(...).exists()` was standing in for, and it
        # is now the same question the plan asked: not *is there an action* —
        # a refresh expects one — but *has a person appeared since*.
        own = ownership.get(proposal.matter_id, _EMPTY_OWNERSHIP)
        if own.human_touched:
            raise PlanChanged(
                f"{proposal.matter_reference}: somebody worked this file after the "
                "plan was made. Nothing was written."
            )
        existing_id = own.open_own.id if own.open_own is not None else None
        if existing_id != proposal.existing_action_id:
            raise PlanChanged(
                f"{proposal.matter_reference}: the open action changed after the "
                "plan was made. Nothing was written."
            )

        if proposal.outcome == Outcome.REMOVE_STALE_IMPORTED:
            if own.open_own is None:  # pragma: no cover - guarded by the identity check
                raise PlanChanged(f"{proposal.matter_reference}: nothing left to withdraw.")
            cancel_next_action(
                action=own.open_own,
                actor=actor,
                reason=(
                    "Uuendatud register ei ütle selle teema kohta enam loetavat järgmist sammu."
                ),
                provenance=_provenance(proposal),
            )
            withdrawn += 1
            continue

        # A creation and a refresh are the same write. `set_next_action`
        # supersedes whatever is open, records the chain in both directions and
        # raises one audit event — which is what a refresh is: the register
        # saying something new, with what it said before still readable.
        if (
            state.currency != RegisterCurrency.CURRENT
            or not matter.is_open
            or matter.record_mode != RecordMode.FULL
            or matter.data_class == MatterDataClass.TEST
        ):
            raise PlanChanged(
                f"{proposal.matter_reference}: the Matter left the current-register "
                "population after the plan was made. Nothing was written."
            )
        set_next_action(
            matter=matter,
            text=text,
            kind=proposal.kind,
            date_semantics=proposal.date_semantics,
            target_date=proposal.target_date,
            date_precision=proposal.date_precision or DatePrecision.EXACT.value,
            source_text=text,
            responsible=None,
            actor=actor,
            provenance=_provenance(proposal),
        )
        if proposal.outcome == Outcome.REFRESH_IMPORTED:
            refreshed += 1
        else:
            created += 1

    return ApplyResult(
        created=created,
        refreshed=refreshed,
        withdrawn=withdrawn,
        snapshot_sha256=plan.snapshot_sha256,
        plan_sha256=plan.digest,
        parser_version=plan.parser_version,
    )
