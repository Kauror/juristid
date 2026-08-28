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

**Row by row, within the years the snapshot was approved for.** The workbook
holds every sheet back to 2011, and the department stopped recording
``HETKESEIS`` before 2025 — the column is not in those era contracts at all. A
blank status reads as *not terminal*, which is the right default for live work
and the wrong question to ask of a 2014 row, so run over the whole workbook the
operation proposed activating two thousand historical Matters. Each approved
snapshot therefore carries the years it speaks for, and outside them the
historical cutover's answer stands. That is retirement **by scope**, recorded as
such: nothing here invents a terminal status the register never wrote.

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

import datetime as dt
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
    AddresseeCardinality,
    addressee_cardinality,
    detect_continuation,
    has_send_date,
    is_real_row,
    is_terminal_status,
    opinion_sent_state,
    parse_member_count,
    split_addressees,
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
CUTOVER_VERSION = "2J.3.0"


@dataclass(frozen=True)
class ReviewedSnapshot:
    """One approved workbook, and the current scope approved *with* it.

    The two halves travel together on purpose. A digest says which bytes were
    reviewed; the scope says which of that workbook's sheets the reviewer was
    speaking for. Recording only the digest is what let a snapshot approved for
    the maintained years be applied to sixteen years of history.
    """

    sha256: str
    #: The workbook a person actually looked at. Operator output only.
    label: str
    #: The sheet years this snapshot may make current. Every other real row it
    #: contains is still classified, and classified as history.
    current_years: frozenset[int]
    #: The day the workbook was taken off somebody's desktop.
    #:
    #: Recorded because one rule needs it and nothing else can supply it: a
    #: ``JÄRGMISEKS`` date written without a year means the sheet's year, and
    #: that reading is only available where the sheet year and the snapshot year
    #: are the same. Deriving it from a filename or a file timestamp would make
    #: the reading depend on how the file reached us
    #: (``register_next_actions.ParseContext``).
    snapshot_date: dt.date | None = None


#: The snapshots a person has approved as authoritative for current state, each
#: with the years they approved it for.
#:
#: A byte-exact identity rather than a filename or a date, and a reviewed code
#: change rather than a flag, for the reason `REVIEWED_CURRENT_YEARS` and
#: `REVIEWED_HISTORICAL_CUTOVER_YEARS` are both lists in source: retiring or
#: activating the department's whole portfolio from whatever workbook happened
#: to be on somebody's desktop is not something a command-line argument should
#: be able to do. The scope is here for the same reason and not on the command
#: line: turning 2014 back into current work must take a reviewed change.
REVIEWED_SNAPSHOTS: tuple[ReviewedSnapshot, ...] = (
    ReviewedSnapshot(
        sha256="f38906c255f5ad6a58711ce833dd61da5fad7ce7ffd74fb8d2b057c6e8a58df2",
        label="Tööd eelnõudega 21.08.26.xlsx",
        # The maintained half of the register. 2024 and earlier are historical:
        # the department stopped recording HETKESEIS for them, and a column the
        # source never had cannot be read as "still live" (docs/adr/0021).
        current_years=frozenset({2025, 2026}),
        snapshot_date=dt.date(2026, 8, 21),
    ),
    # The refresh snapshot. Added beside the one before it rather than in place
    # of it: the earlier digest is the identity of an interpretation this
    # database may still be holding, and a state row that named a snapshot the
    # reviewed list had forgotten would be evidence with no provenance. Both
    # stay; which one is authoritative is decided by which one was applied last,
    # and that is recorded per row (brief 2).
    #
    # Same scope, and that is the reviewed decision, not an inherited default:
    # 2025 and 2026 are the years the department maintains, 2024 and earlier are
    # history, and this refresh does not move that line in either direction.
    ReviewedSnapshot(
        sha256="89495f825b8d48cbcb08fb5f6b4074c6c9f973888611433979b5a80fbe38a678",
        label="Tööd eelnõudega28.08.xlsx",
        current_years=frozenset({2025, 2026}),
        snapshot_date=dt.date(2026, 8, 28),
    ),
)


def reviewed_snapshot(sha256: str) -> ReviewedSnapshot | None:
    """The approved policy for this digest, or ``None`` if nobody approved it."""
    digest = (sha256 or "").strip().lower()
    for snapshot in REVIEWED_SNAPSHOTS:
        if snapshot.sha256 == digest:
            return snapshot
    return None


#: Derived, so the digest list and the scope policy cannot drift apart.
REVIEWED_SNAPSHOT_SHA256: tuple[str, ...] = tuple(item.sha256 for item in REVIEWED_SNAPSHOTS)


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


class Rule:
    """Which rule decided a row's currency, as a stable identifier.

    Separate from the Estonian sentence beside it because the sentence is for a
    person reading a report and this is for a person reading an audit row two
    years later. Retirement in particular has two entirely different causes —
    the register said the work ended, or the row is outside the years this
    snapshot was approved for — and a provenance payload that spelled both
    "retired" would lose the distinction that matters.
    """

    CURRENT = "CURRENT_BY_SOURCE"
    RETIRED_BY_TERMINAL_STATUS = "RETIRED_BY_TERMINAL_STATUS"
    RETIRED_BY_SCOPE = "RETIRED_BY_SCOPE"
    SUPERSEDED_BY_CONTINUATION = "SUPERSEDED_BY_CONTINUATION"
    REVIEW_AMBIGUOUS_CONTINUATION = "REVIEW_AMBIGUOUS_CONTINUATION"


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
    #: ``ÕIGUSAKT``, ``LIIKMETE ARV …`` — source facts with no canonical home,
    #: carried so the derived table can hold them without a second read of the
    #: same row through the same contract (brief 10, 28).
    legal_instrument_raw: str = ""
    feedback_responded_raw: str = ""
    feedback_requested_raw: str = ""

    @property
    def is_real_row(self) -> bool:
        return is_real_row(self.reference.matter.display_reference, self.title)

    @property
    def addressees(self) -> tuple[str, ...]:
        """The organisations ``KELLELE`` names, in source order."""
        return split_addressees(self.addressee_raw)

    @property
    def addressee_cardinality(self) -> str:
        return addressee_cardinality(self.addressee_raw)

    @property
    def names_one_addressee(self) -> bool:
        """Whether the canonical singular field may be written from this cell.

        Exactly one organisation, or nothing. Thirteen cells in the 28.08
        workbook name two or three — *Rahandusministeerium, Kaitseministeerium,
        Kliimaministeerium* — and taking the first would record, with no trace
        of the choice, that Koda wrote to one body when it wrote to three
        (brief 7).
        """
        return self.addressee_cardinality == AddresseeCardinality.SINGLE


@dataclass(frozen=True)
class Candidate:
    """One Matter, what the snapshot says about it, and what follows."""

    matter: Matter
    observation: Observation
    currency: str
    action: str
    reason: str
    #: Which rule decided the currency, as a stable identifier (see `Rule`).
    rule: str = ""
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
            # The identifier, not the sentence. `reason` is Estonian prose for a
            # person reading a report today; this is what an audit query two
            # years from now can group by.
            "rule": self.rule,
            "reason": self.reason,
        }


@dataclass
class CutoverPlan:
    snapshot_sha256: str
    candidates: list[Candidate] = field(default_factory=list)
    #: Rows in the snapshot that name no Matter this database holds. Reported
    #: rather than acted on: creating Matters is the importer's job.
    unmatched_rows: int = 0

    @property
    def policy(self) -> ReviewedSnapshot | None:
        """The approved policy behind this plan, or ``None`` if unapproved."""
        return reviewed_snapshot(self.snapshot_sha256)

    @property
    def is_reviewed(self) -> bool:
        return self.policy is not None

    @property
    def current_years(self) -> frozenset[int]:
        """The years this snapshot may make current.

        Empty for a snapshot nobody approved, and that is the whole answer
        rather than a missing one: an unreviewed workbook carries no approved
        scope, so it cannot make anything current. The analysis still runs and
        still classifies every row — it simply reports that this snapshot
        activates nothing, which is true — and `apply_cutover` refuses it
        regardless.
        """
        policy = self.policy
        return policy.current_years if policy else frozenset()

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
        """Current work with no ``VÄLJA`` mark — ``Arvamusi koostamisel``.

        Through `has_send_date`, which is the one place the rule is written.
        This used to restate it as `not raw.strip()` — the same answer, but a
        second copy, and the derived table and the dashboard had each grown
        their own third version that answered a different question.
        """
        return [c for c in self.current_after if not has_send_date(c.observation.opinion_sent_raw)]

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

    # `None` and `""` are different answers and collapsing them would break the
    # padding rule in one direction or the other. `None` means the row cannot be
    # asked — no contract for the sheet, or an era with no such column — and the
    # importer's stored title is the best available answer. `""` means the
    # contract found column B and the cell was blank, which is exactly what a
    # pre-numbered row is, and must not fall back to anything
    # (app/legacy_import/source_cells.py).
    raw_title = source_cell(reference, contracts, "title")
    title = reference.source_title if raw_title is None else raw_title.strip()

    return Observation(
        reference=reference,
        sheet=reference.source_sheet,
        row_number=reference.source_row_number,
        title=title,
        status_label=cell("legacy_status"),
        opinion_sent_raw=cell("opinion_sent_date"),
        next_action_text=cell("next_action_text"),
        owner_raw=cell("owner_name"),
        addressee_raw=cell("addressee_organisation"),
        received_raw=cell("received_date"),
        deadline_raw=cell("response_deadline"),
        legal_instrument_raw=cell("legal_instrument"),
        feedback_responded_raw=cell("member_feedback_responded"),
        feedback_requested_raw=cell("member_feedback_requested"),
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


@dataclass(frozen=True)
class CurrencyVerdict:
    """What the snapshot says about one row, and which rule said it."""

    currency: str
    rule: str
    reason: str
    continues_under: str = ""


def sheet_year(sheet: str) -> int | None:
    """The year a source sheet names, or ``None`` if it does not name one.

    A sheet that cannot be read as a year is outside every reviewed scope,
    which is the safe direction: it means the row cannot become current work
    rather than that it silently does.
    """
    text = (sheet or "").strip()
    return int(text) if text.isdigit() else None


def currency_of(observation: Observation, *, current_years: frozenset[int]) -> CurrencyVerdict:
    """What the approved snapshot says about one observed row.

    **Scope first, and it is not a shortcut.** A snapshot is approved for the
    years its reviewer was speaking for, and this workbook carries sixteen
    sheets. For 2024 and earlier the register recorded no ``HETKESEIS`` at all —
    the column does not exist in those era contracts — so asking "is this status
    terminal" asks the source a question it never answered, and the answer
    defaults to *not terminal*, which is deliberately the open direction. Run
    over history that turns 2019 into current work. The scope check is what
    stops the open default from being asked outside the years it was designed
    for; it does not change what a blank status means inside them (ADR 0021).

    Within scope, status before continuation, and the measured snapshot is why:
    twenty-four rows carry continuation wording and twenty-two of them are
    already terminal, so evaluating continuation first would answer a question
    that has already been answered and would report the wrong reason for
    twenty-two Matters.
    """
    year = sheet_year(observation.sheet)
    if year is None or year not in current_years:
        # Retired by scope, and the reason says so. It is emphatically not a
        # terminal HETKESEIS: the source recorded no closure here and this
        # invents none. What it records is that the reviewed snapshot does not
        # speak for this year, so the earlier historical cutover's answer stands.
        return CurrencyVerdict(
            RegisterCurrency.RETIRED,
            Rule.RETIRED_BY_SCOPE,
            "Väljaspool lõpliku hetktõmmise ülevaadatud jooksva töö ulatust.",
        )

    if is_terminal_status(observation.status_label):
        return CurrencyVerdict(
            RegisterCurrency.RETIRED,
            Rule.RETIRED_BY_TERMINAL_STATUS,
            "Lõppenud HETKESEIS lõpliku registri järgi.",
        )

    continuation = detect_continuation(observation.next_action_text)
    if continuation.needs_review:
        return CurrencyVerdict(
            RegisterCurrency.REVIEW_REQUIRED,
            Rule.REVIEW_AMBIGUOUS_CONTINUATION,
            continuation.reason,
        )
    if continuation.supersedes:
        return CurrencyVerdict(
            RegisterCurrency.SUPERSEDED,
            Rule.SUPERSEDED_BY_CONTINUATION,
            f"Töö jätkub teema {continuation.reference} all.",
            continuation.reference,
        )
    return CurrencyVerdict(
        RegisterCurrency.CURRENT,
        Rule.CURRENT,
        "Lõpliku registri järgi jooksev töö.",
    )


def _classify(
    matter: Matter,
    observation: Observation,
    activity: NativeActivity,
    *,
    current_years: frozenset[int],
) -> Candidate:
    verdict = currency_of(observation, current_years=current_years)
    currency, continues = verdict.currency, verdict.continues_under

    def candidate(action: str, why: str, review: str = "") -> Candidate:
        return Candidate(
            matter=matter,
            observation=observation,
            currency=currency,
            action=action,
            reason=why,
            rule=verdict.rule,
            review_reason=review,
            continues_under=continues,
        )

    # A natively created Matter is not the register's to move, whatever a row
    # that looks like it appears to say.
    if matter.origin not in {MatterOrigin.LEGACY_IMPORT, MatterOrigin.PROMOTED_LEGACY}:
        return candidate(Action.NATIVE_SKIP, "Kohapeal loodud teema; registrit ei rakendata.")

    if currency == RegisterCurrency.REVIEW_REQUIRED:
        return candidate(
            Action.REVIEW_REQUIRED, verdict.reason, ReviewReason.AMBIGUOUS_CONTINUATION
        )

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
    return candidate(Action.RETIRE, verdict.reason)


def build_cutover_plan(*, snapshot_sha256: str) -> CutoverPlan:
    """Decide what the reconciliation would do. Writes nothing."""
    plan = CutoverPlan(snapshot_sha256=snapshot_sha256)
    observations = latest_observations(snapshot_sha256)
    if not observations:
        return plan

    # Resolved once. Every real row in the snapshot is still classified against
    # it — history included — because a reconciliation that quietly dropped the
    # rows it had nothing to say about would leave `CurrentRegisterState`
    # describing a fraction of the workbook and call it complete.
    current_years = plan.current_years

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
        plan.candidates.append(
            _classify(matter, observation, activity, current_years=current_years)
        )

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

    # Only from a cell naming exactly one organisation. A multi-addressee cell
    # leaves the canonical field alone and keeps its complete text in the
    # derived state, where the Matter page shows it: `Matter` is singular in
    # this release and pretending a three-ministry consultation was a
    # one-ministry one is worse than leaving the field as somebody set it
    # (brief 7).
    if observation.names_one_addressee:
        addressee = resolve_organisation(observation.addressees[0], mappings)
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


def projected_state_rows(plan: CutoverPlan) -> list[CurrentRegisterState]:
    """What the derived table *would* hold for this snapshot. Unsaved, no writes.

    Split out of :func:`rebuild_current_state` so the dry-run and the apply
    derive the same rows from the same code. The refresh report has to say what
    the next-action planner will decide, and that planner reads this table —
    which does not yet describe the new snapshot when the report is being read.
    Projecting it in memory is how the operator sees the whole operation before
    approving any of it; deriving it *twice*, once here and once in the writer,
    is how the report and the apply would quietly stop agreeing.
    """
    now = timezone.now()
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
                # Presence and parse, recorded separately and from different
                # things: the first from the raw cell, the second from what the
                # parser could make of it.
                opinion_sent_recorded=has_send_date(observation.opinion_sent_raw),
                opinion_sent_date=sent.value,
                # And the third derivation of the same cell: which of the four
                # things it is saying. Read from the raw text *and* the parse,
                # so "ei saatnud" is NOT_SENT rather than an unreadable date —
                # sixteen 2026 rows in the 28.08 workbook say exactly that, and
                # rendering them as a send date whose value was lost is the
                # opposite of what the register recorded (brief 9).
                opinion_sent_state=opinion_sent_state(
                    observation.opinion_sent_raw, parsed_date=sent.value
                ),
                next_action_text=observation.next_action_text,
                owner_raw=observation.owner_raw[:200],
                owner_resolved=owner.value is not None,
                # The complete KELLELE cell, however many bodies it names. The
                # canonical singular field was written only where it named one.
                addressee_raw=observation.addressee_raw[:500],
                addressee_cardinality=observation.addressee_cardinality,
                legal_instrument_raw=observation.legal_instrument_raw[:200],
                # Blank stays NULL and a written zero stays zero. They are
                # different answers and this is the only place that decides so.
                member_feedback_responded=parse_member_count(observation.feedback_responded_raw),
                member_feedback_requested=parse_member_count(observation.feedback_requested_raw),
                continues_under_reference=candidate.continues_under[:40],
                review_reason=candidate.review_reason,
                observed_at=now,
            )
        )
    return rows


def rebuild_current_state(plan: CutoverPlan) -> int:
    """Rewrite the derived state table for this snapshot. Idempotent.

    Delete-then-insert for the Matters this plan covers, rather than update: one
    shape of statement regardless of whether a row existed before, so a
    half-built table and a complete one converge — the same reasoning the search
    projection uses, and the same reason it is safe to do at all. Nothing
    canonical reads this table.
    """
    matter_ids = [candidate.matter.pk for candidate in plan.candidates]
    if not matter_ids:
        return 0

    rows = projected_state_rows(plan)
    CurrentRegisterState.objects.filter(matter_id__in=matter_ids).delete()
    CurrentRegisterState.objects.bulk_create(rows)
    return len(rows)


def summary(plan: CutoverPlan) -> dict[str, Any]:
    """Aggregate counts only. No titles, no names beyond the register's own."""
    return {
        "operation": "final_register_cutover",
        "operation_version": CUTOVER_VERSION,
        "snapshot_sha256": plan.snapshot_sha256,
        "reviewed_snapshot": plan.is_reviewed,
        # The years this snapshot may make current. Reported because it is the
        # decision an operator most needs to see beside the counts: it explains
        # why a sixteen-sheet workbook produced a two-year portfolio.
        "current_scope_years": sorted(plan.current_years),
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
    "REVIEWED_SNAPSHOTS",
    "REVIEWED_SNAPSHOT_SHA256",
    "Action",
    "Candidate",
    "CurrencyVerdict",
    "CutoverPlan",
    "CutoverResult",
    "Observation",
    "ReviewReason",
    "ReviewedSnapshot",
    "Rule",
    "UnreviewedSnapshot",
    "apply_cutover_plan",
    "build_cutover_plan",
    "currency_of",
    "native_activity",
    "projected_state_rows",
    "rebuild_current_state",
    "reviewed_snapshot",
    "sheet_year",
    "summary",
]
