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

Human work always wins
----------------------
A Matter carrying **any** ``NextAction`` — open, completed, cancelled or
superseded — is skipped. Not "any open one": a completed action proves somebody
has already worked this file through the structured workflow, and replacing
their decision with a sentence from a spreadsheet would be the enrichment
overruling the department. Production holds at least one hand-made action
today, and it must come through this untouched (brief 13).

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

from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_next_actions import (
    REGISTER_NEXT_ACTION_PARSER_VERSION,
    ParsedInstruction,
    Verdict,
    parse_instruction,
)
from app.matters.enums import MatterDataClass, RecordMode
from app.matters.models import Matter
from app.workflow.enums import DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import set_next_action

#: What the audit provenance calls this source. One token, so a future query for
#: "which actions did the register write" is exact rather than a text search.
ENRICHMENT_SOURCE = "CURRENT_REGISTER"


class Outcome:
    """What the planner decided about one register instruction.

    Every rejection has a name. A report that said only "40 of 134 converted"
    would leave the operator no way to tell a rule that is too strict from a
    corpus that is genuinely ambiguous (brief 11).
    """

    AUTO = "AUTO"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    #: Understood, and its whole period is already behind us. Creating it today
    #: would put a stale instruction on somebody's list as though it were new.
    STALE_SOURCE = "STALE_SOURCE"
    SKIP_EMPTY = "SKIP_EMPTY"
    SKIP_NOT_CURRENT = "SKIP_NOT_CURRENT"
    SKIP_CLOSED = "SKIP_CLOSED"
    SKIP_ARCHIVE_RECORD = "SKIP_ARCHIVE_RECORD"
    SKIP_TEST_DATA = "SKIP_TEST_DATA"
    SKIP_EXISTING_ACTION_HISTORY = "SKIP_EXISTING_ACTION_HISTORY"


#: Fixed order, for a report that reads the same way every run.
OUTCOMES: tuple[str, ...] = (
    Outcome.AUTO,
    Outcome.REVIEW_REQUIRED,
    Outcome.STALE_SOURCE,
    Outcome.SKIP_EXISTING_ACTION_HISTORY,
    Outcome.SKIP_NOT_CURRENT,
    Outcome.SKIP_CLOSED,
    Outcome.SKIP_ARCHIVE_RECORD,
    Outcome.SKIP_TEST_DATA,
    Outcome.SKIP_EMPTY,
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
    state_id: UUID
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

    @property
    def is_auto(self) -> bool:
        return self.outcome == Outcome.AUTO

    def digest_row(self) -> dict[str, Any]:
        """The fields that make two proposals the same proposal.

        Identity, source, reading and responsibility. Deliberately not the
        outcome counters or the report ordering: a digest has to change when the
        *work* changes and stay still when only the presentation does.
        """
        return {
            "matter_id": str(self.matter_id),
            "state_id": str(self.state_id),
            "source_reference_id": str(self.source_reference_id),
            "snapshot_sha256": self.snapshot_sha256,
            "source_text_sha256": self.source_text_sha256,
            "kind": self.kind,
            "date_semantics": self.date_semantics,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "date_precision": self.date_precision,
            "responsible_id": str(self.responsible_id) if self.responsible_id else None,
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
    def digest(self) -> str:
        """A deterministic fingerprint of the complete AUTO set.

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
                (proposal.digest_row() for proposal in self.auto),
                key=lambda row: row["matter_id"],
            ),
        }
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _classify(
    state: CurrentRegisterState,
    matter: Matter,
    *,
    has_action_history: bool,
    today: dt.date,
) -> tuple[str, ParsedInstruction]:
    """One instruction's outcome, in a fixed order.

    Eligibility before precedence before reading, and each step can only
    narrow. The order is not arbitrary: a closed Matter cannot take an action at
    all, so asking the parser about it would produce a proposal nothing could
    ever apply, and the report would count work that was never available.
    """
    text = state.next_action_text.strip()
    empty = ParsedInstruction(
        source_text=text,
        parser_version=REGISTER_NEXT_ACTION_PARSER_VERSION,
        verdict=Verdict.EMPTY,
    )
    if not text:
        return Outcome.SKIP_EMPTY, empty
    if state.currency != RegisterCurrency.CURRENT:
        return Outcome.SKIP_NOT_CURRENT, empty
    if matter.data_class == MatterDataClass.TEST:
        return Outcome.SKIP_TEST_DATA, empty
    if not matter.is_open:
        return Outcome.SKIP_CLOSED, empty
    if matter.record_mode != RecordMode.FULL:
        return Outcome.SKIP_ARCHIVE_RECORD, empty
    if has_action_history:
        return Outcome.SKIP_EXISTING_ACTION_HISTORY, empty

    parsed = parse_instruction(text)
    if not parsed.is_understood:
        return Outcome.REVIEW_REQUIRED, parsed
    if parsed.is_stale(today):
        return Outcome.STALE_SOURCE, parsed
    return Outcome.AUTO, parsed


def build_plan(*, snapshot_sha256: str, today: dt.date | None = None) -> EnrichmentPlan:
    """Read the database and decide everything. Writes nothing.

    The snapshot pin is checked against the derived state table rather than
    trusted: ``CurrentRegisterState`` is rebuilt wholesale by the register
    cutover, so a row carrying a different digest means two cutovers left the
    table describing two workbooks at once, and no plan over it could say which
    one it spoke for.
    """
    digest = (snapshot_sha256 or "").strip().lower()
    today = today or timezone.localdate()

    present = set(
        CurrentRegisterState.objects.values_list("source_snapshot_sha256", flat=True).distinct()
    )
    if not present:
        raise UnknownSnapshot("No derived register state exists. Run final_register_cutover first.")
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
    matter_ids = [state.matter_id for state in states]
    # One query for the whole precedence question rather than one per Matter.
    with_history = set(
        NextAction.objects.filter(matter_id__in=matter_ids)
        .values_list("matter_id", flat=True)
        .distinct()
    )

    proposals: list[Proposal] = []
    for state in states:
        matter = state.matter
        outcome, parsed = _classify(
            state,
            matter,
            has_action_history=state.matter_id in with_history,
            today=today,
        )
        proposals.append(
            Proposal(
                matter_id=state.matter_id,
                matter_reference=matter.display_reference,
                state_id=state.id,
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


def _provenance(proposal: Proposal) -> dict[str, Any]:
    return {
        "source": ENRICHMENT_SOURCE,
        "source_snapshot_sha256": proposal.snapshot_sha256,
        "source_reference_id": str(proposal.source_reference_id),
        "source_text_sha256": proposal.source_text_sha256,
        "parser_version": REGISTER_NEXT_ACTION_PARSER_VERSION,
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

    created = 0
    for proposal in plan.auto:
        matter = Matter.objects.select_for_update().get(pk=proposal.matter_id)
        state = CurrentRegisterState.objects.filter(pk=proposal.state_id).first()
        if state is None:
            raise PlanChanged(f"{proposal.matter_reference}: register state row is gone.")
        text = state.next_action_text.strip()

        if (
            state.currency != RegisterCurrency.CURRENT
            or source_text_digest(text) != proposal.source_text_sha256
            or not matter.is_open
            or matter.record_mode != RecordMode.FULL
            or matter.data_class == MatterDataClass.TEST
        ):
            raise PlanChanged(
                f"{proposal.matter_reference}: the source or the Matter changed "
                "after the plan was made. Nothing was written."
            )
        if NextAction.objects.filter(matter_id=proposal.matter_id).exists():
            # Somebody worked this file between the plan and now. Their decision
            # stands and the run stops rather than working around it.
            raise PlanChanged(
                f"{proposal.matter_reference}: a next action appeared after the "
                "plan was made. Nothing was written."
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
        created += 1

    return ApplyResult(
        created=created,
        snapshot_sha256=plan.snapshot_sha256,
        plan_sha256=plan.digest,
        parser_version=plan.parser_version,
    )
