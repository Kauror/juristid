"""Reconciling the current portfolio against the final approved snapshot.

The interim cutover promoted a *year*: everything the 2026 sheet named became
current work, everything before it became history. That was the best rule
available when the only question anybody could answer was "which sheet is this
row on", and it is wrong in both directions. Sixty 2025 files are still live
work — a proceeding does not end because a calendar year did — and fifty-five
2026 rows are finished. This operation replaces the approximation with what the
maintained register actually says, row by row (ADR 0021).

It is a reconciliation, not an import. No Matter is created here, no title is
rewritten, and no source row is touched: the snapshot is catalogued by the
ordinary Stage-2A importer first, which writes immutable
``MatterSourceReference`` rows, and this reads them back.

Six outcomes, one per Matter, and the order they are decided in is the safety
story
--------------------------------------------------------------------------
``NATIVE_SKIP`` first, because a natively created Matter is somebody's own work
and the register has no authority over it whatever a row appears to say about
the same subject.

``REVIEW_REQUIRED`` next, and it absorbs four different kinds of "a person has
already decided something here": a real recorded closure, continuation wording
that does not say uniquely where the work went, and — only where the snapshot
would *retire* a Matter — authored entries, an open instruction or a native
submission. Erasing current work because a spreadsheet disagrees with it is the
one failure this operation must not have, so the tie always goes to the person.

Then ``ACTIVATE``, ``KEEP_CURRENT``, ``RETIRE`` and ``ALREADY_RETIRED``, which
are the four ordinary answers.

What it refuses to do
---------------------
No ``NextAction`` from ``JÄRGMISEKS``, no ``Submission`` from ``VÄLJA``, no
disposition, no closure date and no closing person. Each of those would be a
plausible-looking fact nobody could later tell from a recorded one, and the
register holds none of them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.dates import parse_date
from app.legacy_import.models import MatterSourceReference
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.register_semantics import (
    detect_continuation,
    is_real_row,
    is_terminal_status,
)
from app.legacy_import.resolution import (
    KnownPeople,
    MappingTables,
    resolve_organisation,
    resolve_owner,
    resolve_status,
)
from app.legacy_import.source_cells import contracts_by_sheet, source_cell
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import (
    promote_matter_to_full,
    reactivate_historical_matter,
    refresh_matter_from_register,
    retire_from_current_register,
)
from app.search.indexing import indexable_matters, refresh_matters
from app.workflow.enums import ActionStatus

#: Bumped when this operation's rules change. Recorded on every row it touches.
CUTOVER_VERSION = "2J.1.0"

#: The snapshots a person has approved as authoritative for current state.
#:
#: A byte-exact identity rather than a filename or a date, and a reviewed code
#: change rather than a flag, for the reason `REVIEWED_CURRENT_YEARS` and
#: `REVIEWED_HISTORICAL_CUTOVER_YEARS` are both lists in source: retiring or
#: activating the department's whole portfolio from whatever workbook happened
#: to be on somebody's desktop is not something a command-line argument should
#: be able to do.
REVIEWED_SNAPSHOT_SHA256: tuple[str, ...] = (
    # Tööd eelnõudega 21.08.26.xlsx — the final maintained Excel-era snapshot.
    "f38906c255f5ad6a58711ce833dd61da5fad7ce7ffd74fb8d2b057c6e8a58df2",
)


class UnreviewedSnapshot(Exception):
    """Applying a snapshot nobody has approved as authoritative."""


class Action:
    """What the reconciliation would do with one Matter."""

    ACTIVATE = "ACTIVATE"
    KEEP_CURRENT = "KEEP_CURRENT"
    RETIRE = "RETIRE"
    ALREADY_RETIRED = "ALREADY_RETIRED"
    NATIVE_SKIP = "NATIVE_SKIP"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


ACTIONS: tuple[str, ...] = (
    Action.ACTIVATE,
    Action.KEEP_CURRENT,
    Action.RETIRE,
    Action.ALREADY_RETIRED,
    Action.NATIVE_SKIP,
    Action.REVIEW_REQUIRED,
)


class ReviewReason:
    """Why a Matter was held back. Aggregate reasons only, never row content."""

    RECORDED_CLOSURE = "RECORDED_CLOSURE"
    AMBIGUOUS_CONTINUATION = "AMBIGUOUS_CONTINUATION"
    AUTHORED_ENTRIES = "AUTHORED_ENTRIES"
    OPEN_NEXT_ACTION = "OPEN_NEXT_ACTION"
    NATIVE_SUBMISSION = "NATIVE_SUBMISSION"


@dataclass(frozen=True)
class Observation:
    """One Matter's row in the approved snapshot, read through its contract."""

    reference: MatterSourceReference
    sheet: str
    row_number: int | None
    title: str
    status_label: str
    opinion_sent_raw: str
    next_action_text: str
    owner_raw: str
    addressee_raw: str
    received_raw: str
    deadline_raw: str

    @property
    def is_real_row(self) -> bool:
        return is_real_row(self.reference.matter.display_reference, self.title)


@dataclass(frozen=True)
class Candidate:
    """One Matter, what the snapshot says about it, and what follows."""

    matter: Matter
    observation: Observation
    currency: str
    action: str
    reason: str
    review_reason: str = ""
    continues_under: str = ""

    def provenance(self, snapshot_sha256: str) -> dict[str, Any]:
        """Audit payload. Identifiers and interpretation, never source text.

        The status label and the ``JÄRGMISEKS`` sentence stay on the immutable
        source reference this points at, exactly as the Stage-2F promotion
        settled.
        """
        return {
            "operation": "final_register_cutover",
            "operation_version": CUTOVER_VERSION,
            "source_system": SOURCE_SYSTEM,
            "source_snapshot_sha256": snapshot_sha256,
            "source_sheet": self.observation.sheet,
            "source_reference": str(self.observation.reference.pk),
            "currency": self.currency,
            "rule": self.reason,
        }


@dataclass
class CutoverPlan:
    snapshot_sha256: str
    candidates: list[Candidate] = field(default_factory=list)
    #: Rows in the snapshot that name no Matter this database holds. Reported
    #: rather than acted on: creating Matters is the importer's job.
    unmatched_rows: int = 0

    @property
    def is_reviewed(self) -> bool:
        return self.snapshot_sha256 in REVIEWED_SNAPSHOT_SHA256

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(candidate.action for candidate in self.candidates)
        return {name: tally.get(name, 0) for name in ACTIONS}

    @property
    def review_reasons(self) -> dict[str, int]:
        tally = Counter(c.review_reason for c in self.candidates if c.review_reason)
        return dict(sorted(tally.items()))

    @property
    def current_after(self) -> list[Candidate]:
        """The Matters this plan leaves as current work."""
        return [c for c in self.candidates if c.action in {Action.ACTIVATE, Action.KEEP_CURRENT}]

    @property
    def current_by_sheet(self) -> dict[str, int]:
        tally = Counter(c.observation.sheet for c in self.current_after)
        return dict(sorted(tally.items()))

    @property
    def drafting_after(self) -> list[Candidate]:
        """Current work with no recorded send date — ``Arvamusi koostamisel``."""
        return [c for c in self.current_after if not c.observation.opinion_sent_raw.strip()]

    @property
    def source_responsibility(self) -> dict[str, int]:
        """Named responsibility across the resulting current set.

        The raw register name, not the resolved account: two of these Matters
        name somebody with no account, and reporting them as *Määramata* would
        discard the one thing the register is certain about.
        """
        tally = Counter(c.observation.owner_raw.strip() or "" for c in self.current_after)
        return dict(sorted(tally.items(), key=lambda item: (-item[1], item[0])))


# ---------------------------------------------------------------------------
# Reading the snapshot back
# ---------------------------------------------------------------------------


def approved_references(snapshot_sha256: str) -> list[MatterSourceReference]:
    """Every register observation belonging to one snapshot.

    Ordered so that a Matter appearing on two sheets of the same workbook
    resolves to its latest sheet deterministically rather than by whichever row
    the database returned first.
    """
    return list(
        MatterSourceReference.objects.filter(
            source_system=SOURCE_SYSTEM,
            source_snapshot_sha256=snapshot_sha256,
        )
        .select_related("matter", "matter__stage", "matter__owner")
        .order_by("matter_id", "source_sheet", "source_row_number")
    )


def _observation(reference: MatterSourceReference, contracts: Any) -> Observation:
    def cell(name: str) -> str:
        return (source_cell(reference, contracts, name) or "").strip()

    return Observation(
        reference=reference,
        sheet=reference.source_sheet,
        row_number=reference.source_row_number,
        title=cell("title") or reference.source_title,
        status_label=cell("legacy_status"),
        opinion_sent_raw=cell("opinion_sent_date"),
        next_action_text=cell("next_action_text"),
        owner_raw=cell("owner_name"),
        addressee_raw=cell("addressee_organisation"),
        received_raw=cell("received_date"),
        deadline_raw=cell("response_deadline"),
    )


def latest_observations(snapshot_sha256: str) -> dict[Any, Observation]:
    """One observation per Matter: the last sheet the snapshot names it on."""
    contracts = contracts_by_sheet()
    observations: dict[Any, Observation] = {}
    for reference in approved_references(snapshot_sha256):
        observations[reference.matter_id] = _observation(reference, contracts)
    return observations


# ---------------------------------------------------------------------------
# Native activity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NativeActivity:
    """Signals that a person has worked on a Matter inside Juristid.

    Every one of these is native by construction. No importer in this codebase
    creates an ``Entry`` or a ``NextAction`` — deliberately, and stated in three
    module docstrings — and the only importer that creates a ``Submission`` is
    the opinion archive, whose submissions each carry an
    ``OpinionSubmissionImport`` row (``archive_imports``) and are excluded here.
    """

    entries: frozenset[Any]
    open_actions: frozenset[Any]
    native_submissions: frozenset[Any]

    def reason_for(self, matter_id: Any) -> str:
        if matter_id in self.open_actions:
            return ReviewReason.OPEN_NEXT_ACTION
        if matter_id in self.entries:
            return ReviewReason.AUTHORED_ENTRIES
        if matter_id in self.native_submissions:
            return ReviewReason.NATIVE_SUBMISSION
        return ""


def native_activity(matter_ids: list[Any]) -> NativeActivity:
    """Which of these Matters carry work a person did here. Three queries."""
    from app.matters.models import Entry
    from app.submissions.models import Submission
    from app.workflow.models import NextAction

    entries = set(
        Entry.objects.filter(matter_id__in=matter_ids)
        .values_list("matter_id", flat=True)
        .distinct()
    )
    actions = set(
        NextAction.objects.filter(matter_id__in=matter_ids, status=ActionStatus.OPEN)
        .values_list("matter_id", flat=True)
        .distinct()
    )
    submissions = set(
        Submission.objects.filter(matter_id__in=matter_ids, archive_imports__isnull=True)
        .values_list("matter_id", flat=True)
        .distinct()
    )
    return NativeActivity(
        entries=frozenset(entries),
        open_actions=frozenset(actions),
        native_submissions=frozenset(submissions),
    )


# ---------------------------------------------------------------------------
# Classifying
# ---------------------------------------------------------------------------


def currency_of(observation: Observation) -> tuple[str, str, str]:
    """``(currency, reason, continues_under)`` for one observed row.

    Status before continuation, and the measured snapshot is why: twenty-four
    rows carry continuation wording and twenty-two of them are already terminal,
    so evaluating continuation first would answer a question that has already
    been answered and would report the wrong reason for twenty-two Matters.
    """
    if is_terminal_status(observation.status_label):
        return (
            RegisterCurrency.RETIRED,
            "Lõppenud HETKESEIS lõpliku registri järgi.",
            "",
        )

    continuation = detect_continuation(observation.next_action_text)
    if continuation.needs_review:
        return (RegisterCurrency.REVIEW_REQUIRED, continuation.reason, "")
    if continuation.supersedes:
        return (
            RegisterCurrency.SUPERSEDED,
            f"Töö jätkub teema {continuation.reference} all.",
            continuation.reference,
        )
    return (RegisterCurrency.CURRENT, "Lõpliku registri järgi jooksev töö.", "")


def _classify(matter: Matter, observation: Observation, activity: NativeActivity) -> Candidate:
    currency, reason, continues = currency_of(observation)

    def candidate(action: str, why: str, review: str = "") -> Candidate:
        return Candidate(
            matter=matter,
            observation=observation,
            currency=currency,
            action=action,
            reason=why,
            review_reason=review,
            continues_under=continues,
        )

    # A natively created Matter is not the register's to move, whatever a row
    # that looks like it appears to say.
    if matter.origin not in {MatterOrigin.LEGACY_IMPORT, MatterOrigin.PROMOTED_LEGACY}:
        return candidate(Action.NATIVE_SKIP, "Kohapeal loodud teema; registrit ei rakendata.")

    if currency == RegisterCurrency.REVIEW_REQUIRED:
        return candidate(Action.REVIEW_REQUIRED, reason, ReviewReason.AMBIGUOUS_CONTINUATION)

    wants_current = currency == RegisterCurrency.CURRENT
    is_current = matter.record_mode == RecordMode.FULL and matter.is_open

    if wants_current:
        if is_current:
            return candidate(Action.KEEP_CURRENT, "Juba jooksev töö.")
        # A genuine professional closure is a decision; the register does not
        # reverse it. Stage 2I's default invented nothing, which is exactly what
        # makes it recognisable and safe to undo here.
        if matter.disposition or matter.closed_at is not None:
            return candidate(
                Action.REVIEW_REQUIRED,
                "Teemal on tegelik salvestatud sulgemine; registri põhjal ei taasavata.",
                ReviewReason.RECORDED_CLOSURE,
            )
        return candidate(Action.ACTIVATE, "Lõpliku registri järgi jooksev töö.")

    # The snapshot retires it.
    if not is_current:
        return candidate(Action.ALREADY_RETIRED, "Ei ole jooksev töö; muudatust ei vaja.")
    if matter.disposition or matter.closed_at is not None:
        return candidate(
            Action.REVIEW_REQUIRED,
            "Teemal on tegelik salvestatud sulgemine.",
            ReviewReason.RECORDED_CLOSURE,
        )
    conflict = activity.reason_for(matter.pk)
    if conflict:
        return candidate(
            Action.REVIEW_REQUIRED,
            "Teemal on hilisem kohapealne töö; registri põhjal ei arhiveerita.",
            conflict,
        )
    return candidate(Action.RETIRE, reason)


def build_cutover_plan(*, snapshot_sha256: str) -> CutoverPlan:
    """Decide what the reconciliation would do. Writes nothing."""
    plan = CutoverPlan(snapshot_sha256=snapshot_sha256)
    observations = latest_observations(snapshot_sha256)
    if not observations:
        return plan

    matters = {
        matter.pk: matter
        for matter in Matter.objects.filter(pk__in=list(observations)).select_related(
            "stage", "owner"
        )
    }
    activity = native_activity(list(observations))

    for matter_id, observation in observations.items():
        matter = matters.get(matter_id)
        if matter is None:  # pragma: no cover - referential integrity holds
            plan.unmatched_rows += 1
            continue
        if not observation.is_real_row:
            # A pre-numbered row that never became work. The importer already
            # refuses to create one; this refuses to act on one.
            continue
        plan.candidates.append(_classify(matter, observation, activity))

    plan.candidates.sort(key=lambda c: (c.observation.sheet, c.observation.row_number or 0))
    return plan


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CutoverResult:
    snapshot_sha256: str
    activated: int
    retired: int
    kept: int
    refreshed: int
    state_rows: int
    examined: int


def _resolved_fields(
    observation: Observation, *, people: KnownPeople, mappings: MappingTables
) -> dict[str, Any]:
    """The fields the source settles for one current Matter.

    A key is absent — not ``None`` — whenever the source cannot settle it.
    ``None`` is a value the register asserts ("no deadline is recorded"); an
    absent key means "do not touch this field", and conflating the two would let
    an unreadable cell erase a date somebody entered.
    """
    resolved: dict[str, Any] = {}

    owner = resolve_owner(observation.owner_raw, mappings, people)
    if owner.value is not None:
        resolved["owner"] = owner.value

    status = resolve_status(observation.status_label, observation.reference.source_era)
    if status.stage is not None:
        resolved["stage"] = status.stage

    for field_name, raw in (
        ("received_date", observation.received_raw),
        ("response_deadline", observation.deadline_raw),
    ):
        if not raw:
            continue
        parsed = parse_date(raw, raw=raw)
        if parsed.value is not None:
            resolved[field_name] = parsed.value

    if observation.addressee_raw:
        addressee = resolve_organisation(observation.addressee_raw, mappings)
        if addressee.value is not None:
            resolved["addressee_organisation"] = addressee.value

    return resolved


@transaction.atomic
def apply_cutover_plan(plan: CutoverPlan, *, actor: Any = None) -> CutoverResult:
    """Apply the reconciliation. One transaction, or none of it.

    Idempotent throughout. Every branch either performs a transition the Matter
    is not already in, or returns without writing — so a second run against the
    same snapshot creates no Matter change, no audit event and no duplicate
    state row.
    """
    if not plan.is_reviewed:
        raise UnreviewedSnapshot(
            f"Snapshot {plan.snapshot_sha256[:16]}… is not a reviewed authoritative source. "
            "Analyse it with --dry-run; applying needs the digest recorded in "
            "REVIEWED_SNAPSHOT_SHA256, not a flag."
        )

    people = KnownPeople.load()
    mappings = MappingTables.empty()

    activated = retired = kept = refreshed = 0
    touched: list[Any] = []

    for candidate in plan.candidates:
        matter = Matter.objects.select_for_update().get(pk=candidate.matter.pk)
        provenance = candidate.provenance(plan.snapshot_sha256)

        if candidate.action == Action.ACTIVATE:
            _activate(matter, candidate, provenance, actor)
            activated += 1
            touched.append(matter.pk)
        elif candidate.action == Action.RETIRE:
            if matter.record_mode == RecordMode.FULL and matter.is_open:
                retire_from_current_register(matter=matter, actor=actor, provenance=provenance)
                retired += 1
                touched.append(matter.pk)
        elif candidate.action == Action.KEEP_CURRENT:
            kept += 1

        if candidate.action in {Action.ACTIVATE, Action.KEEP_CURRENT}:
            _, changed = refresh_matter_from_register(
                matter=matter,
                actor=actor,
                provenance=provenance,
                # Spread, so a field the source could not settle is simply
                # absent and keeps its `_UNSET` default rather than arriving as
                # a `None` that would erase what somebody entered.
                **_resolved_fields(candidate.observation, people=people, mappings=mappings),
            )
            if changed:
                refreshed += 1
                touched.append(matter.pk)

    state_rows = rebuild_current_state(plan)

    if touched:
        refresh_matters(indexable_matters().filter(pk__in=list(set(touched))))

    return CutoverResult(
        snapshot_sha256=plan.snapshot_sha256,
        activated=activated,
        retired=retired,
        kept=kept,
        refreshed=refreshed,
        state_rows=state_rows,
        examined=len(plan.candidates),
    )


def _activate(matter: Matter, candidate: Candidate, provenance: dict[str, Any], actor: Any) -> None:
    """Bring one Matter to FULL/open, by whichever route its state needs.

    Two shapes reach here. A Matter Stage 2I retired is ARCHIVE **and closed**,
    and must be reopened before it can be promoted — ``promote_matter_to_full``
    refuses a closed record, correctly, because a closed FULL Matter would need
    a closure timestamp the register never recorded. A Matter that was simply
    never promoted is ARCHIVE and open, and needs only the promotion.
    """
    reason = "Lõpliku registri hetktõmmise järgi jooksev töö."
    if not matter.is_open:
        reactivate_historical_matter(matter=matter, actor=actor, attestation=reason)
    elif matter.record_mode != RecordMode.FULL:
        promote_matter_to_full(matter=matter, actor=actor, reason=reason, provenance=provenance)


def rebuild_current_state(plan: CutoverPlan) -> int:
    """Rewrite the derived state table for this snapshot. Idempotent.

    Delete-then-insert for the Matters this plan covers, rather than update: one
    shape of statement regardless of whether a row existed before, so a
    half-built table and a complete one converge — the same reasoning the search
    projection uses, and the same reason it is safe to do at all. Nothing
    canonical reads this table.
    """
    now = timezone.now()
    matter_ids = [candidate.matter.pk for candidate in plan.candidates]
    if not matter_ids:
        return 0

    CurrentRegisterState.objects.filter(matter_id__in=matter_ids).delete()
    people = KnownPeople.load()
    mappings = MappingTables.empty()

    rows = []
    for candidate in plan.candidates:
        observation = candidate.observation
        sent = parse_date(observation.opinion_sent_raw, raw=observation.opinion_sent_raw)
        owner = resolve_owner(observation.owner_raw, mappings, people)
        rows.append(
            CurrentRegisterState(
                matter=candidate.matter,
                source_reference=observation.reference,
                source_snapshot_sha256=plan.snapshot_sha256,
                source_sheet=observation.sheet,
                source_row_number=observation.row_number,
                currency=candidate.currency,
                status_label=observation.status_label[:200],
                opinion_sent_date=sent.value,
                next_action_text=observation.next_action_text,
                owner_raw=observation.owner_raw[:200],
                owner_resolved=owner.value is not None,
                continues_under_reference=candidate.continues_under[:40],
                review_reason=candidate.review_reason,
                observed_at=now,
            )
        )
    CurrentRegisterState.objects.bulk_create(rows)
    return len(rows)


def summary(plan: CutoverPlan) -> dict[str, Any]:
    """Aggregate counts only. No titles, no names beyond the register's own."""
    return {
        "operation": "final_register_cutover",
        "operation_version": CUTOVER_VERSION,
        "snapshot_sha256": plan.snapshot_sha256,
        "reviewed_snapshot": plan.is_reviewed,
        "examined": len(plan.candidates),
        "actions": plan.counts,
        "review_reasons": plan.review_reasons,
        "current_total": len(plan.current_after),
        "current_by_sheet": plan.current_by_sheet,
        "drafting_total": len(plan.drafting_after),
        "source_responsibility": plan.source_responsibility,
        "unmatched_rows": plan.unmatched_rows,
    }


__all__ = [
    "ACTIONS",
    "CUTOVER_VERSION",
    "REVIEWED_SNAPSHOT_SHA256",
    "Action",
    "Candidate",
    "CutoverPlan",
    "CutoverResult",
    "Observation",
    "ReviewReason",
    "UnreviewedSnapshot",
    "apply_cutover_plan",
    "build_cutover_plan",
    "currency_of",
    "native_activity",
    "rebuild_current_state",
    "summary",
]
