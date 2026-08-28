"""One reviewed snapshot, refreshed into current work — planned, then applied.

The register is a living file. The department edits it every week, and Juristid
has so far been able to read it exactly once: the final cutover reconciled the
portfolio against one approved workbook, the enrichment converted that
workbook's readable instructions, and everything after that was frozen. A review
date typed in August stayed August into October, an owner who handed a file over
kept it, and a ``HETKESEIS`` that moved to *riigikogus* did not.

This module is what makes the same operation repeatable. It is not a second
importer and it introduces no new provenance: it composes the three operations
that already exist — catalogue, reconcile, enrich — behind one plan, one digest
and one apply, so that an operator sees the whole consequence of a newer
workbook before any of it happens.

Three phases, and their order is a dependency rather than a preference
---------------------------------------------------------------------
**Catalogue** is not here. ``import_legacy_register`` writes the immutable
``MatterSourceReference`` rows and must have run for the snapshot already; this
reads them back. A refresh that could also create Matters would be an importer,
and the reason the reconciliation is safe is that it cannot create anything.

**Reconcile** decides currency and refreshes the source-authoritative fields —
owner, stage, received date, response deadline, addressee — through
``final_cutover``, which is where those rules live and stay.

**Enrich** reads ``JÄRGMISEKS`` and moves the structured work queue, through
``next_action_enrichment``, which is where the parser's output is allowed to
become an action.

**Outreach** is a fourth phase and an optional one, because the only thing that
may write a ``Kaasamine`` pointer is a reviewed mapping file a person prepared
(``register_outreach``). Without one the refresh reports campaign candidates and
writes none of them.

The report has to be complete before anything is written
--------------------------------------------------------
Which is the one hard problem here. The enrichment reads the derived state
table, and at the moment an operator reads the plan that table still describes
the *previous* workbook — so a report built from it would answer confidently
about the wrong snapshot. So the plan projects the rows the reconciliation would
write, in memory, through the same function that writes them, and plans the
enrichment over those. Nothing is saved, and the two halves cannot drift because
there is one derivation.

Four pins, and applying needs all of them
-----------------------------------------
The workbook digest says which bytes were catalogued and reviewed. The plan
digest says nothing in the database moved between deciding and writing. The
campaign digest says which export the candidates came from. The mapping digest
says which links a person actually approved. Any mismatch is a refusal, and the
refusal is total (brief 32).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Count, Sum

from app.legacy_import import next_action_enrichment as enrichment
from app.legacy_import import register_outreach as outreach
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.dates import parse_date
from app.legacy_import.final_cutover import (
    Action,
    CutoverPlan,
    Observation,
    apply_cutover_plan,
    build_cutover_plan,
    projected_state_rows,
    resolved_fields,
    reviewed_snapshot,
)
from app.legacy_import.models import ImportBatch, MatterSourceReference
from app.legacy_import.parser import SOURCE_SYSTEM

# Imported by name as well as by module. `RefreshPlan.outreach` is a field, and
# inside a class body that name shadows the module — so every annotation on a
# sibling field has to reach these types directly or resolve to the field.
from app.legacy_import.register_outreach import Campaign, OutreachPlan, OutreachTarget
from app.legacy_import.register_semantics import (
    AddresseeCardinality,
    OpinionSentState,
    parse_member_count,
)
from app.legacy_import.resolution import (
    KnownPeople,
    MappingTables,
    resolve_organisation,
    resolve_owner,
    resolve_status,
)
from app.matters.enums import MatterDataClass
from app.matters.models import Matter

#: Bumped when this operation's composition changes — a phase added, a field
#: allowed to refresh, a different order. Inside the plan digest, so a plan
#: approved under one version cannot be applied under another.
REFRESH_VERSION = "1.0"


class PlanChanged(Exception):
    """The database moved between planning and applying. Nothing was written."""


class UnreviewedSnapshot(Exception):
    """Applying a workbook nobody approved as authoritative."""


class SnapshotNotCatalogued(Exception):
    """The reviewed workbook's rows are not in this database yet."""


#: The command that puts them there. Named once, so the refusal below can tell
#: an operator what to run rather than what went wrong.
CATALOGUE_COMMAND = "import_legacy_register"


@dataclass(frozen=True)
class CatalogueState:
    """What this database holds of one workbook, before a refresh reads it."""

    snapshot_sha256: str
    #: Source references carrying this exact digest.
    references: int
    #: Of those, the ones that describe a Matter rather than a pre-numbered
    #: padding row. A workbook catalogued as nothing but empty rows is a
    #: workbook nobody catalogued.
    real_rows: int
    #: Import batches recording this digest, and the rows they say they read.
    #: Reported rather than required: nothing in this schema guarantees that a
    #: reference's snapshot digest matches its batch's, so a *missing* batch is
    #: not evidence of a missing catalogue — but a batch that read far more rows
    #: than this database holds references for is worth an operator's eye.
    batches: int
    batch_row_count: int

    @property
    def is_catalogued(self) -> bool:
        return self.real_rows > 0

    @property
    def looks_partial(self) -> bool:
        return bool(self.batches) and self.batch_row_count > self.references


def catalogue_state(snapshot_sha256: str) -> CatalogueState:
    """How much of this workbook the database holds. Two counts, no writes."""
    digest = (snapshot_sha256 or "").strip().lower()
    references = MatterSourceReference.objects.filter(
        source_system=SOURCE_SYSTEM, source_snapshot_sha256=digest
    )
    batches = ImportBatch.objects.filter(
        source_system=SOURCE_SYSTEM, source_snapshot_sha256=digest
    ).aggregate(count=Count("id"), rows=Sum("source_row_count"))
    return CatalogueState(
        snapshot_sha256=digest,
        references=references.count(),
        real_rows=references.exclude(source_title="").count(),
        batches=batches["count"] or 0,
        batch_row_count=batches["rows"] or 0,
    )


def require_catalogued(snapshot_sha256: str) -> CatalogueState:
    """Refuse a workbook whose rows this database has never seen.

    A reviewed digest says a person approved these bytes. It says nothing about
    whether anybody ran the importer that turns them into
    ``MatterSourceReference`` rows — and the reconciliation reads only those.

    Without this check a forgotten catalogue step produced a plan with no
    observations, which prints as a clean report full of zeros: no Matter
    activated, no field moved, no instruction converted, no campaign placed.
    That is indistinguishable on the page from "the newer workbook changes
    nothing", and it is the one wrong answer an operator would believe, because
    it is the answer they were hoping for.

    Refusing is the only safe reading. A refresh that has nothing to reconcile
    against has not been given its source.
    """
    state = catalogue_state(snapshot_sha256)
    if not state.is_catalogued:
        raise SnapshotNotCatalogued(
            f"Reviewed workbook {state.snapshot_sha256[:16]}… is not catalogued in this "
            f"database: it holds {state.references} source reference(s) for that snapshot "
            f"and {state.real_rows} of them describe a Matter. Run the approved register "
            f"catalogue step first — `manage.py {CATALOGUE_COMMAND} <workbook> --apply` — "
            "and then plan the refresh again. A refresh cannot reconcile against rows "
            "nobody has imported, and a plan over none of them reports zero changes "
            "rather than an error."
        )
    return state


# ---------------------------------------------------------------------------
# What the newer workbook changes about a Matter's canonical fields
# ---------------------------------------------------------------------------

#: The fields the source is allowed to refresh, and the order the report lists
#: them in. Every one is written by ``refresh_matter_from_register``; nothing
#: here assigns to a model attribute (brief 5).
REFRESHABLE_FIELDS: tuple[str, ...] = (
    "owner",
    "stage",
    "received_date",
    "response_deadline",
    "addressee_organisation",
)


@dataclass(frozen=True)
class FieldChange:
    """One canonical field the newer workbook would move on one Matter."""

    matter_id: Any
    reference: str
    field: str
    #: Identities and dates only. A name is a person and a stage is a code;
    #: neither is register prose, and both are what an operator needs to judge
    #: whether a change is right.
    before: str
    after: str


class UnresolvedKind:
    OWNER = "OWNER"
    ORGANISATION = "ORGANISATION"


@dataclass(frozen=True)
class Unresolved:
    """A source value the resolver refused to turn into a record.

    Reported rather than guessed at, and reported *with its source text*,
    because the only fix is a person adding a reviewed mapping and they cannot
    do that from a count. These are organisation and given names the department
    writes about itself — not member data and not case prose (brief 6, 20).
    """

    kind: str
    raw: str
    rows: int


def _observed_changes(
    observation: Observation, matter: Matter, *, people: KnownPeople, mappings: MappingTables
) -> list[tuple[str, str, str]]:
    """``(field, before, after)`` for every field this row would move.

    Mirrors ``final_cutover._resolved_fields`` exactly — same resolvers, same
    refusals, same single-addressee rule — and differs only in reporting the
    comparison instead of performing it. A field the source cannot settle is
    absent here for the same reason it is absent there: *do not touch* and
    *set to nothing* are different instructions, and only one of them is safe.
    """
    changes: list[tuple[str, str, str]] = []

    owner = resolve_owner(observation.owner_raw, mappings, people)
    if owner.value is not None and owner.value.pk != matter.owner_id:
        held = matter.owner
        changes.append(("owner", held.get_full_name() if held else "", owner.value.get_full_name()))

    status = resolve_status(observation.status_label, observation.reference.source_era)
    if status.stage is not None and status.stage != matter.stage_id:
        changes.append(("stage", str(matter.stage_id or ""), str(status.stage)))

    for name, raw in (
        ("received_date", observation.received_raw),
        ("response_deadline", observation.deadline_raw),
    ):
        if not raw:
            continue
        parsed = parse_date(raw, raw=raw)
        current = getattr(matter, name)
        if parsed.value is not None and parsed.value != current:
            changes.append((name, current.isoformat() if current else "", parsed.value.isoformat()))

    if observation.names_one_addressee:
        addressee = resolve_organisation(observation.addressees[0], mappings)
        if addressee.value is not None and addressee.value.pk != matter.addressee_organisation_id:
            recorded = matter.addressee_organisation
            changes.append(
                ("addressee_organisation", recorded.name if recorded else "", addressee.value.name)
            )

    return changes


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class RefreshPlan:
    """Everything one newer workbook would do, decided and written nowhere."""

    snapshot_sha256: str
    today: dt.date
    cutover: CutoverPlan
    #: The rows the reconciliation would write, projected. Never saved here.
    projected: list[CurrentRegisterState]
    next_actions: enrichment.EnrichmentPlan
    field_changes: list[FieldChange] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    multi_addressee: list[str] = field(default_factory=list)
    outreach: OutreachPlan | None = None
    #: The state rows this database held *before* the refresh, by Matter, so
    #: the report can say what current work looked like beforehand.
    current_before: int = 0
    #: The campaign input this plan considered, kept so the apply can re-derive
    #: the identical plan rather than a campaign-less one.
    campaigns: list[Campaign] = field(default_factory=list)
    campaign_window: tuple[dt.date, dt.date] | None = None

    @property
    def campaign_set_sha256(self) -> str:
        """The identity of the campaigns this plan matched against, or "".

        Empty when no campaign input participated, which is itself a fact the
        digest carries: a plan approved with campaigns cannot be applied without
        them, and a plan approved without them cannot quietly acquire some.
        """
        return outreach.campaign_set_digest(self.campaigns) if self.campaigns else ""

    @property
    def policy(self) -> Any:
        return reviewed_snapshot(self.snapshot_sha256)

    @property
    def is_reviewed(self) -> bool:
        return self.policy is not None

    @property
    def digest(self) -> str:
        """One deterministic fingerprint of the whole refresh.

        Everything an apply would do, and nothing about how the report is
        presented. Sorted before hashing so two runs over an unchanged database
        agree whatever order the rows came back in, and versioned twice — this
        module's composition and the parser's grammar — because the same rows
        read by different rules are a different plan, and an apply guarded only
        by row identity would not notice (brief 4, 32).
        """
        body = {
            "refresh_version": REFRESH_VERSION,
            "parser_version": self.next_actions.parser_version,
            "snapshot_sha256": self.snapshot_sha256,
            "current_scope_years": sorted(self.cutover.current_years),
            "actions": sorted(
                [str(candidate.matter.pk), candidate.action, candidate.rule]
                for candidate in self.cutover.candidates
                if candidate.action in _WRITING_ACTIONS
            ),
            "field_changes": sorted(
                [str(change.matter_id), change.field, change.before, change.after]
                for change in self.field_changes
            ),
            "next_actions": sorted(
                (proposal.digest_row() for proposal in self.next_actions.writing),
                key=lambda row: (row["matter_id"], row["outcome"]),
            ),
            # A hard pin, not a reported figure. The campaign export decides
            # which candidates an operator reviewed, so an apply run against a
            # different set of campaigns is approving one plan and performing
            # another — and the mapping digest cannot catch it, because a
            # reviewed mapping is a list of links and says nothing about the
            # candidates it was chosen from (brief 10).
            #
            # Empty string when no campaigns participated, so "planned without
            # campaigns" and "planned with campaigns" are different plans.
            "campaign_set_sha256": self.campaign_set_sha256,
        }
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


#: The reconciliation outcomes that leave a Matter as current work. A campaign
#: is placed against the portfolio the refresh *produces*, so a Matter the newer
#: workbook retires is not a target however well its old dates fit.
_CURRENT_AFTER: frozenset[str] = frozenset({Action.ACTIVATE, Action.KEEP_CURRENT})


def outreach_targets(
    plan: CutoverPlan, *, people: KnownPeople, mappings: MappingTables
) -> list[OutreachTarget]:
    """The portfolio the reconciliation would leave, as the matcher's input.

    Every field is the post-refresh answer, and each comes from the same place
    the apply gets it:

    * **currency** from the reconciliation's own action, not from a stored row;
    * **owner** from the newer ``VASTUTAJA`` cell, as a given name — which is
      what a campaign template names, and what the previous implementation read
      from a derived row describing the *previous* workbook;
    * **window** from ``resolved_fields``, which is the function the apply
      spreads onto the Matter. Where the source settles a date the projection
      uses the new one; where it cannot, the Matter keeps what it has and so
      does this. Those two branches are the apply's behaviour restated as a
      question rather than reimplemented as a rule.

    Nothing here writes, and nothing here mutates a ``Matter``: the resolved
    values are read into a frozen value class and the model objects are left
    exactly as they were loaded.
    """
    targets: list[OutreachTarget] = []
    for candidate in plan.candidates:
        if candidate.action not in _CURRENT_AFTER:
            continue
        matter = candidate.matter
        if matter.data_class == MatterDataClass.TEST:
            continue

        resolved = resolved_fields(candidate.observation, people=people, mappings=mappings)
        targets.append(
            OutreachTarget(
                matter_id=matter.pk,
                reference=matter.display_reference,
                # The register's title for this row where it has one, so the
                # subject overlap is computed against what the newer workbook
                # says rather than a title a previous import stored.
                title=candidate.observation.title or matter.title,
                owner_raw=candidate.observation.owner_raw.strip(),
                received_date=resolved.get("received_date", matter.received_date),
                response_deadline=resolved.get("response_deadline", matter.response_deadline),
            )
        )
    return targets


def _action_signature(plan: enrichment.EnrichmentPlan) -> list[dict[str, Any]]:
    """The writing set of one enrichment plan, comparably.

    Sorted and stripped of nothing: two plans that would write the same actions
    to the same Matters for the same reasons are the same plan, whichever route
    reached them.
    """
    return sorted(
        (proposal.digest_row() for proposal in plan.writing),
        key=lambda row: (row["matter_id"], row["outcome"]),
    )


#: The reconciliation outcomes that change something. ``KEEP_CURRENT`` is in
#: here because it is the branch that also refreshes fields — a Matter that
#: stays current is exactly the one whose owner and deadline may have moved.
_WRITING_ACTIONS: frozenset[str] = frozenset({Action.ACTIVATE, Action.RETIRE, Action.KEEP_CURRENT})


def build_refresh_plan(
    *,
    snapshot_sha256: str,
    today: dt.date | None = None,
    campaigns: list[Campaign] | None = None,
    campaign_window: tuple[dt.date, dt.date] | None = None,
) -> RefreshPlan:
    """Decide everything the newer workbook would do. Writes nothing.

    Bounded queries throughout: the reconciliation reads its references once,
    the field comparison runs against Matters already loaded by it, and the
    enrichment's precedence question is two queries for the whole set. Nothing
    here is per-row (brief 38).
    """
    digest = (snapshot_sha256 or "").strip().lower()
    today = today or dt.date.today()

    cutover = build_cutover_plan(snapshot_sha256=digest)
    projected = projected_state_rows(cutover)

    # The projection carries unsaved rows whose `matter` is already loaded, so
    # the enrichment planner needs no further query to reach it.
    next_actions = enrichment.build_plan(snapshot_sha256=digest, today=today, states=projected)

    plan = RefreshPlan(
        snapshot_sha256=digest,
        today=today,
        cutover=cutover,
        projected=projected,
        next_actions=next_actions,
        current_before=CurrentRegisterState.objects.filter(
            currency=RegisterCurrency.CURRENT
        ).count(),
        campaigns=list(campaigns or []),
        campaign_window=campaign_window,
    )

    people = KnownPeople.load()
    mappings = MappingTables.empty()
    unresolved_owners: Counter[str] = Counter()
    unresolved_orgs: Counter[str] = Counter()
    multi: Counter[str] = Counter()

    for candidate in cutover.candidates:
        observation = candidate.observation
        matter = candidate.matter

        if (
            observation.owner_raw
            and resolve_owner(observation.owner_raw, mappings, people).value is None
        ):
            unresolved_owners[observation.owner_raw.strip()] += 1

        cardinality = observation.addressee_cardinality
        if cardinality == AddresseeCardinality.MULTIPLE:
            multi[observation.addressee_raw.strip()] += 1
        for name in observation.addressees:
            if resolve_organisation(name, mappings).value is None:
                unresolved_orgs[name] += 1

        # Fields are refreshed only where the reconciliation keeps or makes the
        # Matter current, exactly as the apply does. Reporting a change on a row
        # about to be retired would describe work that will not happen.
        if candidate.action not in {Action.ACTIVATE, Action.KEEP_CURRENT}:
            continue
        for name, before, after in _observed_changes(
            observation, matter, people=people, mappings=mappings
        ):
            plan.field_changes.append(
                FieldChange(
                    matter_id=matter.pk,
                    reference=matter.display_reference,
                    field=name,
                    before=before,
                    after=after,
                )
            )

    plan.unresolved = [
        Unresolved(kind=UnresolvedKind.OWNER, raw=raw, rows=count)
        for raw, count in sorted(unresolved_owners.items(), key=lambda item: (-item[1], item[0]))
    ] + [
        Unresolved(kind=UnresolvedKind.ORGANISATION, raw=raw, rows=count)
        for raw, count in sorted(unresolved_orgs.items(), key=lambda item: (-item[1], item[0]))
    ]
    plan.multi_addressee = [raw for raw, _ in sorted(multi.items())]

    if campaigns is not None and campaign_window is not None:
        since, until = campaign_window
        plan.outreach = outreach.build_outreach_plan(
            snapshot_sha256=digest,
            campaigns=campaigns,
            # The portfolio this very plan would produce, not the one the
            # database still holds. Derived from the reconciliation above, so an
            # operator approving campaign candidates is approving them against
            # the same owners, dates and currency they are approving everywhere
            # else on the page.
            targets=outreach_targets(cutover, people=people, mappings=mappings),
            since=since,
            until=until,
        )

    return plan


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _feedback_tally(rows: list[CurrentRegisterState], attribute: str) -> dict[str, int]:
    """Populated, explicitly zero, and blank — counted as three things.

    The distinction the whole feature turns on. A blank is not a zero, so this
    never reports "0" as a count of anything except rows that genuinely say
    zero (brief 10).
    """
    tally: Counter[str] = Counter()
    for row in rows:
        value = getattr(row, attribute)
        if value is None:
            tally["blank"] += 1
        elif value == 0:
            tally["explicit_zero"] += 1
        else:
            tally["populated"] += 1
    return {name: tally.get(name, 0) for name in ("populated", "explicit_zero", "blank")}


def summary(plan: RefreshPlan) -> dict[str, Any]:
    """The complete dry-run report. Aggregates and identities, never prose.

    Every figure the operator brief asks for, in one document with one digest.
    It carries no register sentence, no Matter title and no member data: a
    report is a file somebody may e-mail, and the wording of a consultation
    instruction is content the Matter page already shows to people authorised
    to see it (brief 20).

    The unresolved owner and organisation *names* are the deliberate exception,
    and they are not case content: they are the department's own colleagues and
    the ministries it writes to, and the only action the report can prompt —
    adding a reviewed mapping — is impossible from a count.
    """
    rows = plan.projected
    changes = Counter(change.field for change in plan.field_changes)
    sent_states = Counter(row.opinion_sent_state for row in rows)
    current_rows = [row for row in rows if row.currency == RegisterCurrency.CURRENT]
    by_sheet = Counter(row.source_sheet for row in rows)

    report: dict[str, Any] = {
        "operation": "refresh_current_register",
        "refresh_version": REFRESH_VERSION,
        "snapshot_sha256": plan.snapshot_sha256,
        "snapshot_label": plan.policy.label if plan.is_reviewed else "",
        "snapshot_date": (
            plan.policy.snapshot_date.isoformat()
            if plan.is_reviewed and plan.policy.snapshot_date
            else ""
        ),
        "reviewed_snapshot": plan.is_reviewed,
        "current_scope_years": sorted(plan.cutover.current_years),
        "evaluated_on": plan.today.isoformat(),
        "plan_sha256": plan.digest,
        "examined_rows": len(plan.cutover.candidates),
        "examined_by_sheet": dict(sorted(by_sheet.items())),
        # --- currency -----------------------------------------------------
        "current_before": plan.current_before,
        "current_after": len(current_rows),
        "actions": plan.cutover.counts,
        "review_reasons": plan.cutover.review_reasons,
        "current_by_sheet": plan.cutover.current_by_sheet,
        # --- canonical fields ---------------------------------------------
        "field_changes": {name: changes.get(name, 0) for name in REFRESHABLE_FIELDS},
        "field_changes_total": len(plan.field_changes),
        "unresolved_owners": [
            {"value": item.raw, "rows": item.rows}
            for item in plan.unresolved
            if item.kind == UnresolvedKind.OWNER
        ],
        "unresolved_organisations": [
            {"value": item.raw, "rows": item.rows}
            for item in plan.unresolved
            if item.kind == UnresolvedKind.ORGANISATION
        ],
        "multi_addressee_rows": sum(
            1 for row in rows if row.addressee_cardinality == AddresseeCardinality.MULTIPLE
        ),
        "multi_addressee_values": plan.multi_addressee,
        # --- VÄLJA ---------------------------------------------------------
        "opinion_sent": {
            "date": sent_states.get(OpinionSentState.DATE, 0),
            "not_sent": sent_states.get(OpinionSentState.NOT_SENT, 0),
            "recorded_other": sent_states.get(OpinionSentState.RECORDED_OTHER, 0),
            "blank": sent_states.get(OpinionSentState.BLANK, 0),
        },
        # Stated rather than implied. It is the invariant this operation is most
        # likely to be suspected of breaking, and the answer is always this.
        "submissions_created_from_valja": 0,
        # --- member feedback ------------------------------------------------
        "member_feedback_requested": _feedback_tally(rows, "member_feedback_requested"),
        "member_feedback_responded": _feedback_tally(rows, "member_feedback_responded"),
        # --- JÄRGMISEKS -----------------------------------------------------
        "next_actions": enrichment.summary(plan.next_actions),
    }

    if plan.outreach is not None:
        report["outreach"] = outreach.summary(plan.outreach)
    return report


def protected_rows(plan: RefreshPlan) -> list[dict[str, Any]]:
    """Per-Matter detail for an operator review file.

    Stable identity and interpretation. The register's own prose — the title,
    the ``HETKESEIS`` wording, the ``JÄRGMISEKS`` sentence — stays on the Matter
    page, where it is already authorised; here a sentence appears only as the
    hash the next-action plan carries (brief 20).
    """
    readings = {proposal.matter_id: proposal for proposal in plan.next_actions.proposals}
    moves: dict[Any, list[str]] = {}
    for change in plan.field_changes:
        moves.setdefault(change.matter_id, []).append(change.field)

    rows = []
    for candidate in plan.cutover.candidates:
        reading = readings.get(candidate.matter.pk)
        state = candidate.observation
        rows.append(
            {
                "matter_id": str(candidate.matter.pk),
                "reference": candidate.matter.display_reference,
                "sheet": state.sheet,
                "row": state.row_number,
                "action": candidate.action,
                "rule": candidate.rule,
                "review_reason": candidate.review_reason,
                "currency": candidate.currency,
                "continues_under": candidate.continues_under,
                "fields_changed": sorted(moves.get(candidate.matter.pk, [])),
                "addressee_cardinality": state.addressee_cardinality,
                "opinion_sent_state": _projected_sent_state(state),
                "member_feedback_requested": parse_member_count(state.feedback_requested_raw),
                "member_feedback_responded": parse_member_count(state.feedback_responded_raw),
                "next_action_outcome": reading.outcome if reading else "",
                "next_action_kind": reading.kind if reading else "",
                "next_action_date": (
                    reading.target_date.isoformat() if reading and reading.target_date else ""
                ),
                "next_action_review_reasons": list(reading.review_reasons) if reading else [],
                "source_text_sha256": reading.source_text_sha256 if reading else "",
            }
        )
    return rows


def _projected_sent_state(observation: Observation) -> str:
    from app.legacy_import.register_semantics import opinion_sent_state

    parsed = parse_date(observation.opinion_sent_raw, raw=observation.opinion_sent_raw)
    return opinion_sent_state(observation.opinion_sent_raw, parsed_date=parsed.value)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefreshResult:
    snapshot_sha256: str
    plan_sha256: str
    activated: int
    retired: int
    kept: int
    refreshed: int
    state_rows: int
    actions_created: int
    actions_refreshed: int
    actions_withdrawn: int
    engagements_created: int = 0
    engagements_updated: int = 0


@transaction.atomic
def apply_refresh_plan(
    plan: RefreshPlan,
    *,
    expect_plan_sha256: str,
    links: tuple[outreach.ReviewedLink, ...] = (),
    expect_mapping_sha256: str = "",
    actor: Any = None,
) -> RefreshResult:
    """Apply the whole refresh, or none of it. One transaction.

    The order is the dependency order and cannot be varied: the reconciliation
    rebuilds the derived state, the enrichment reads it, and the outreach links
    hang off Matters the reconciliation may just have activated.

    **The plan is re-derived and re-compared before anything is written.** A
    plan is a photograph; between taking it and approving it somebody may have
    closed a Matter, set an action or corrected an owner. Re-deriving and
    demanding the same digest is what makes "I approved *this*" mean something,
    and a difference aborts everything rather than applying the part that still
    matches — a partial apply against an approved digest would leave a state
    neither the plan nor the database describes (brief 32).
    """
    if not plan.is_reviewed:
        raise UnreviewedSnapshot(
            f"Snapshot {plan.snapshot_sha256[:16]}… is not a reviewed authoritative source. "
            "Analyse it with --dry-run; applying needs the digest recorded in "
            "REVIEWED_SNAPSHOTS, not a flag."
        )

    require_catalogued(plan.snapshot_sha256)

    expected = (expect_plan_sha256 or "").strip().lower()
    if plan.digest != expected:
        raise PlanChanged(
            f"Plan digest {plan.digest[:16]}… does not match the approved "
            f"{expected[:16] or '(none)'}…. Nothing was written."
        )

    # Re-derived with the same campaign input, because the campaign set is
    # inside the digest: re-deriving without it would compute a different plan
    # and refuse every apply that included campaigns.
    fresh = build_refresh_plan(
        snapshot_sha256=plan.snapshot_sha256,
        today=plan.today,
        campaigns=plan.campaigns or None,
        campaign_window=plan.campaign_window,
    )
    if fresh.digest != expected:
        raise PlanChanged(
            "The database no longer matches the approved plan "
            f"({fresh.digest[:16]}… against {expected[:16]}…). Nothing was written."
        )

    cutover = apply_cutover_plan(fresh.cutover, actor=actor)

    # Re-planned against the state rows the cutover has now written, rather than
    # against the projection: the projection was the basis for approval, and
    # what is applied has to be what the database actually holds.
    #
    # The rows are read back and passed in rather than looked up by the planner
    # itself, and that is not a shortcut. `build_plan`'s own lookup fails closed
    # when the table carries more than one snapshot digest, which is the right
    # answer for somebody running the enrichment on its own and the wrong one
    # here: a Matter the previous workbook named and this one does not keeps its
    # old row, legitimately, and refusing the whole apply because of it would
    # make a workbook unusable for having lost a line.
    written = list(
        CurrentRegisterState.objects.filter(source_snapshot_sha256=plan.snapshot_sha256)
        .select_related("matter")
        .order_by("matter_id")
    )
    actions = enrichment.build_plan(
        snapshot_sha256=plan.snapshot_sha256, today=plan.today, states=written
    )

    # And then compared against what was approved, because "re-derive it" is
    # only a safety property if somebody checks the answer. Without this the
    # projection would be the basis for approval and the re-plan the basis for
    # writing, with nothing at all connecting the two — the exact shape of a
    # plan/apply gate that has stopped gating anything.
    if _action_signature(actions) != _action_signature(fresh.next_actions):
        raise PlanChanged(
            "The next-action set derived after the reconciliation is not the one "
            "the plan described. Nothing further was written."
        )

    action_result = enrichment.apply_plan(actions, expect_plan_sha256=actions.digest, actor=actor)

    engagements = outreach.OutreachResult(created=0, updated=0, unchanged=0, mapping_sha256="")
    if links:
        engagements = outreach.apply_mapping(
            links=links, expect_mapping_sha256=expect_mapping_sha256, actor=actor
        )

    return RefreshResult(
        snapshot_sha256=plan.snapshot_sha256,
        plan_sha256=plan.digest,
        activated=cutover.activated,
        retired=cutover.retired,
        kept=cutover.kept,
        refreshed=cutover.refreshed,
        state_rows=cutover.state_rows,
        actions_created=action_result.created,
        actions_refreshed=action_result.refreshed,
        actions_withdrawn=action_result.withdrawn,
        engagements_created=engagements.created,
        engagements_updated=engagements.updated,
    )


__all__ = [
    "CATALOGUE_COMMAND",
    "REFRESHABLE_FIELDS",
    "REFRESH_VERSION",
    "CatalogueState",
    "FieldChange",
    "PlanChanged",
    "RefreshPlan",
    "RefreshResult",
    "SnapshotNotCatalogued",
    "Unresolved",
    "UnresolvedKind",
    "UnreviewedSnapshot",
    "apply_refresh_plan",
    "build_refresh_plan",
    "catalogue_state",
    "outreach_targets",
    "protected_rows",
    "require_catalogued",
    "summary",
]
