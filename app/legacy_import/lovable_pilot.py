"""The Lovable pilot: real current Teemad, added beside the ones already there.

A reviewed **package** — built outside the repository from a read-only snapshot
of the department's operational register in Lovable (`public.topic` and its
commencement, change-history and source-row tables) — becomes native Juristid
Teemad through the ordinary services: the Teema with its owner, stage,
instrument and track, its current step, an outstanding opinion request, and its
commencements (docs/adr/0191).

**Nothing is read off the wording at apply.** Lovable's `jargmiseks`, `valja`,
`valja_kuupaev` and `hetkeseis` are never turned into a step, a send or a stage
on their own (docs/adr/0157 §3). The package states, per topic, the reviewed
reading — kind, date meaning, date, lifecycle, disposition — and the reviewer's
reason; this module writes exactly that and refuses anything it cannot write
honestly.

**What it never does.** No SENT opinion (the letters are in OneNote, which this
round does not import), no document, no follow-up check, no stage history, no
closure event, no person and no organisation. A finished topic is created
closed the way the Excel pilot creates one (ARCHIVE, a disposition, no
`closed_at`, no `MATTER_CLOSED`). Lovable's change history and every source
value travel in `MatterSourceReference.source_row_raw`, labelled as the
source's, never as Juristid events. The writes are stamped with the import's own
moment and no actor.

**What says "imported".** `EvidencePilotRecord` rows with ``pilot =
"lovable-pilot"`` — one per record written — and one immutable
`MatterSourceReference` per Teema naming the snapshot and keeping the OneNote
link exactly as Lovable held it.

**It appends; it never replaces.** Unlike the Excel and evidence pilots it does
not need an empty database: it needs every one of its references to be free.
A reference that exists — native, imported, archived or deleted — stops the
whole apply; nothing already there is read for writing, updated or linked to.

`validate` (no database) → `plan` (resolves people, stages, instruments and
organisations; checks every reference; prints the plan digest) → `dry_run`
(writes everything, verifies it, rolls back) → `apply` (one transaction, every
gate) → `verify` (reads back and compares, record by record).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.legacy_import.evidence_pilot import step_shape
from app.legacy_import.integrity import IntegrityError, canonical_sha256, require_digest
from app.legacy_import.limits import load_json_document

PILOT = "lovable-pilot"
PILOT_VERSION = "1.0"
PACKAGE_FORMAT = "juristid.lovable-pilot"
PACKAGE_FORMAT_VERSION = 1
#: Typed by the operator at `apply`, the same discipline as the other pilots.
OPERATOR_INTENT = "lovable-pilot"
#: `MATTER_CREATED`, `NEXT_ACTION_SET` and the security event name this source.
PROVENANCE_SOURCE = "LOVABLE_PILOT"
SOURCE_SYSTEM = "LOVABLE"
#: Not a worksheet: the table the row came from, partitioned by its year so that
#: `(sheet, row_number)` is the row's real identity (`2026_5` ≠ `2025_5`).
SOURCE_TABLE = "public.topic"
PLAN_DIGEST_VERSION = 1

READY = "READY"
SOURCE_ONLY = "SOURCE_ONLY"

LIFECYCLES = frozenset({"ACTIVE", "INACTIVE", "CONCLUDED", "CONTINUES"})
#: Every lifecycle but ACTIVE is a finished file: a disposition and nothing current.
DISPOSITIONS = frozenset({"COMPLETED", "SUPERSEDED", "MONITORING_STOPPED", "NO_POSITION_FORMED"})
STEP_TREATMENTS = frozenset({"SOURCE_NEXT_STEP", "OPINION_DRAFTING"})
DEADLINE_TREATMENTS = frozenset({"NONE", "OPEN_REQUEST", "SOURCE_ONLY"})
T_EFFECTIVE_DATE = "MatterEffectiveDate"
T_IMPORTANT_DATE = "MatterImportantDate"
EFFECTIVE_KINDS = frozenset({"KNOWN_DATE", "GENERAL_ORDER"})
IMPORTANT_KINDS = frozenset({"PLANNED_COMMENCEMENT", "ESTIMATED_COMMENCEMENT"})
#: The commencement titles the Teema shows, in the department's words.
COMMENCEMENT_TITLES = {
    "PLANNED_COMMENCEMENT": "Eelnõus kavandatud jõustumine",
    "ESTIMATED_COMMENCEMENT": "Eeldatav jõustumine (allika hinnang)",
}
FIELD_DECISIONS = frozenset(
    {"APPLIED", "PRESERVED_AS_SOURCE_ONLY", "HELD_FOR_REVIEW", "NOT_PRESENT"}
)

_REFERENCE = re.compile(r"^(\d{4})_(\d{1,6})$")
_BACKUP_SET = re.compile(r"^sets/\d{8}T\d{6}Z$")


class LovablePilotError(Exception):
    """The Lovable pilot refused. The message says what to fix; nothing was written."""


def parse_reference(reference: str) -> tuple[int, int] | None:
    match = _REFERENCE.match(reference or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _day(raw: Any) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------


def load_package(path: str | Path) -> dict[str, Any]:
    """The package file, bounded by size before it is parsed (docs/adr/0184)."""
    target = Path(path)
    if target.is_dir():
        target = target / "package.json"
    try:
        data = target.read_bytes()
    except OSError as error:
        raise LovablePilotError(f"The package cannot be read: {error}") from error
    package = load_json_document(data, what="The Lovable-pilot package")
    if not isinstance(package, dict):
        raise LovablePilotError("The package is not a JSON object.")
    return package


def package_digest(package: dict[str, Any]) -> str:
    """The package's identity: its canonical JSON, independent of whitespace."""
    return canonical_sha256(package)


@dataclass
class ValidationReport:
    topics: int = 0
    open_topics: int = 0
    steps: int = 0
    commencements_ready: int = 0
    commencements_source_only: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_package(package: dict[str, Any]) -> ValidationReport:
    """Everything that can be checked without a database. Never writes."""
    report = ValidationReport()
    out = report.problems
    if package.get("format") != PACKAGE_FORMAT:
        out.append(f"format is {package.get('format')!r}, not {PACKAGE_FORMAT!r}.")
    if package.get("format_version") != PACKAGE_FORMAT_VERSION:
        out.append(f"format_version {package.get('format_version')!r} is not supported.")
    snapshot = package.get("snapshot") or {}
    as_of = _day(snapshot.get("as_of"))
    if as_of is None:
        out.append("snapshot.as_of is missing.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("snapshot_sha256") or "")):
        out.append("snapshot.snapshot_sha256 must be the SHA-256 of the exported snapshot.")
    topics = package.get("topics")
    if not isinstance(topics, list) or not topics:
        out.append("The package has no topics.")
        return report
    expected = package.get("expected_topics")
    if expected != len(topics):
        out.append(f"expected_topics is {expected!r} but the package holds {len(topics)}.")
    people = package.get("people") or {}

    seen: set[str] = set()
    for topic in topics:
        reference = str(topic.get("reference") or "")
        report.topics += 1
        problems = _validate_topic(topic, people=people, as_of=as_of)
        out.extend(f"{reference}: {p}" for p in problems)
        if parse_reference(reference) is None:
            continue
        if reference in seen:
            out.append(f"{reference}: listed twice.")
        seen.add(reference)
        if topic.get("lifecycle") == "ACTIVE":
            report.open_topics += 1
        if topic.get("next_action"):
            report.steps += 1
        for record in topic.get("commencements") or []:
            if record.get("review") == READY:
                report.commencements_ready += 1
            else:
                report.commencements_source_only += 1
    return report


def _validate_topic(topic: dict[str, Any], *, people: dict, as_of: dt.date | None) -> list[str]:
    out: list[str] = []
    reference = str(topic.get("reference") or "")
    if parse_reference(reference) is None:
        return [f"reference {reference!r} is not YYYY_N."]
    if not (topic.get("title") or "").strip():
        out.append("no title.")
    source = topic.get("source") or {}
    if not source.get("id") or not isinstance(source.get("row"), dict):
        out.append("source.id and source.row (the Lovable row as read) are required.")
    elif (source["row"].get("nr") or "") != reference:
        out.append("source.row.nr does not match the reference.")

    lifecycle = topic.get("lifecycle")
    if lifecycle not in LIFECYCLES:
        out.append(f"lifecycle {lifecycle!r} is unknown.")
    active = lifecycle == "ACTIVE"
    disposition = topic.get("disposition") or ""
    if active and disposition:
        out.append("an active topic has no disposition.")
    if not active and disposition not in DISPOSITIONS:
        out.append(f"a finished topic needs a disposition, not {disposition!r}.")

    owner_raw = topic.get("owner_raw") or ""
    if active and owner_raw not in people:
        out.append(f"owner {owner_raw!r} has no reviewed person; an open file needs one.")

    received = topic.get("received_date")
    if received and _day(received) is None:
        out.append(f"received_date {received!r} is not a date.")
    received_day = _day(received)
    if as_of and received_day and received_day > as_of:
        out.append("received_date is after the snapshot.")

    step = topic.get("next_action")
    if step:
        if not active:
            out.append("a finished topic carries no current step.")
        if step.get("review") != READY:
            out.append("next_action must be READY; a held step is left out of the package.")
        if step.get("treatment") not in STEP_TREATMENTS:
            out.append(f"next_action.treatment {step.get('treatment')!r} is unknown.")
        if not (step.get("text") or "").strip():
            out.append("next_action has no text.")
        shape_input = {
            "kind": step.get("kind"),
            "date_semantics": step.get("date_semantics"),
            "date_precision": step.get("date_precision") or None,
        }
        if step.get("date_semantics") == "REVIEW_ON":
            shape_input["review_date"] = step.get("target_date")
        else:
            shape_input["target_date"] = step.get("target_date")
        _shape, shape_problems = step_shape(shape_input)
        out.extend(shape_problems)
        if not step.get("reason"):
            out.append("next_action needs the reviewer's reason.")

    deadline = topic.get("deadline") or {"treatment": "NONE"}
    treatment = deadline.get("treatment")
    if treatment not in DEADLINE_TREATMENTS:
        out.append(f"deadline.treatment {treatment!r} is unknown.")
    if treatment == "OPEN_REQUEST":
        day = _day(deadline.get("deadline"))
        if not active:
            out.append("only an open topic has an outstanding opinion request.")
        if day is None:
            out.append("an open request needs its deadline.")
        if not (
            step
            and step.get("treatment") == "OPINION_DRAFTING"
            and step.get("kind") == "DO"
            and step.get("date_semantics") == "DEADLINE"
            and _day(step.get("target_date")) == day
        ):
            out.append("an open request is drafted: the step is DO «Koostan arvamuse» on its day.")
    elif step and step.get("treatment") == "OPINION_DRAFTING":
        out.append("a drafting step needs the open request it answers.")

    for record in topic.get("commencements") or []:
        key = record.get("key") or "?"
        review = record.get("review")
        if review == SOURCE_ONLY:
            if not record.get("reason"):
                out.append(f"{key}: a source-only commencement says why.")
            continue
        if review != READY:
            out.append(f"{key}: review {review!r} is neither READY nor SOURCE_ONLY.")
            continue
        if not active:
            out.append(f"{key}: a finished topic's dates cannot be written; keep them as source.")
        target, kind = record.get("target"), record.get("kind")
        if target == T_EFFECTIVE_DATE and kind not in EFFECTIVE_KINDS:
            out.append(f"{key}: effective-date kind {kind!r} is not one this pilot writes.")
        elif target == T_IMPORTANT_DATE and kind not in IMPORTANT_KINDS:
            out.append(f"{key}: important-date kind {kind!r} is not a commencement.")
        elif target not in {T_EFFECTIVE_DATE, T_IMPORTANT_DATE}:
            out.append(f"{key}: target {target!r} cannot be written.")
        dated = kind != "GENERAL_ORDER"
        if dated and (_day(record.get("date")) is None or record.get("date_precision") != "EXACT"):
            out.append(f"{key}: a dated commencement needs its exact day.")
        if not dated and record.get("date"):
            out.append(f"{key}: «üldises korras» carries no date; none is invented.")

    url = topic.get("onenote_url") or ""
    if url and not url.startswith("https://"):
        out.append("onenote_url is not an https address; it is kept as source only.")
    for name, decision in (topic.get("field_decisions") or {}).items():
        if decision not in FIELD_DECISIONS:
            out.append(f"field_decisions[{name}] = {decision!r} is unknown.")
    return out


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class TopicPlan:
    topic: dict[str, Any]
    owner: Any = None
    stage: Any = None
    instruments: list[Any] = field(default_factory=list)
    addressee: Any = None
    successor_in_batch: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def reference(self) -> str:
        return str(self.topic["reference"])

    @property
    def is_open(self) -> bool:
        return self.topic["lifecycle"] == "ACTIVE"


@dataclass
class LovablePlan:
    package: dict[str, Any]
    digest: str
    topics: list[TopicPlan] = field(default_factory=list)
    #: `ready` (every reference free, nothing of this package written),
    #: `applied` (exactly this package) or `conflict`.
    database_state: str = ""
    existing_matters: int = 0
    collisions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def expected_writes(self) -> dict[str, int]:
        counts = {"MATTER": len(self.topics)}
        for topic_plan in self.topics:
            topic = topic_plan.topic
            if topic.get("next_action"):
                counts["NEXT_ACTION"] = counts.get("NEXT_ACTION", 0) + 1
            if (topic.get("deadline") or {}).get("treatment") == "OPEN_REQUEST":
                counts["RESPONSE_REQUEST"] = counts.get("RESPONSE_REQUEST", 0) + 1
            for record in topic.get("commencements") or []:
                if record.get("review") == READY:
                    counts[record["target"]] = counts.get(record["target"], 0) + 1
            if topic_plan.successor_in_batch:
                counts["CONTINUATION"] = counts.get("CONTINUATION", 0) + 1
        return counts


def build_plan(path_or_package: str | Path | dict[str, Any]) -> LovablePlan:
    """Resolve everything and read the database. Never writes."""
    from app.accounts.models import User
    from app.legacy_import.register_semantics import split_addressees
    from app.legacy_import.resolution import MappingTables, resolve_organisation
    from app.taxonomy.models import LegalInstrumentType
    from app.workflow.models import StageVocabulary

    package = (
        path_or_package if isinstance(path_or_package, dict) else load_package(path_or_package)
    )
    plan = LovablePlan(package=package, digest=package_digest(package))
    report = validate_package(package)
    plan.problems.extend(report.problems)
    if report.problems:
        return plan

    people = package.get("people") or {}
    stages = {item.key: item for item in StageVocabulary.objects.all()}
    instruments = {item.key: item for item in LegalInstrumentType.objects.all()}
    mappings = MappingTables.empty()
    references = {t["reference"] for t in package["topics"]}

    for topic in package["topics"]:
        topic_plan = TopicPlan(topic=topic)
        reference = topic_plan.reference

        owner_raw = topic.get("owner_raw") or ""
        name = people.get(owner_raw)
        if name:
            matches = list(User.objects.filter(display_name=name))
            if len(matches) != 1 or not matches[0].is_active:
                plan.problems.append(
                    f"{reference}: owner {owner_raw!r} → {name!r} is not exactly one active "
                    "account."
                )
            else:
                topic_plan.owner = matches[0]
        elif owner_raw:
            topic_plan.warnings.append(
                f"VASTUTAJA {owner_raw!r} has no Juristid account; kept as source only."
            )

        if topic.get("stage"):
            topic_plan.stage = stages.get(topic["stage"])
            if topic_plan.stage is None:
                plan.problems.append(
                    f"{reference}: stage {topic['stage']!r} is not in the vocabulary."
                )

        for key in topic.get("instruments") or []:
            instrument = instruments.get(key)
            if instrument is None:
                plan.problems.append(f"{reference}: Õigusakt {key!r} is not in the vocabulary.")
            else:
                topic_plan.instruments.append(instrument)

        resolved = []
        for part in split_addressees(topic.get("addressee_raw")):
            resolution = resolve_organisation(part, mappings)
            if resolution.value is None:
                topic_plan.warnings.append(f"KELLELE {part!r} does not resolve; kept as source.")
            else:
                resolved.append(resolution.value)
        if len(resolved) == 1:
            topic_plan.addressee = resolved[0]

        successor = topic.get("successor") or ""
        if successor and successor in references:
            topic_plan.successor_in_batch = successor
        elif successor:
            topic_plan.warnings.append(
                f"continues under {successor}, which this package does not hold; the link is "
                "kept as source text and nothing outside the package is touched."
            )
        plan.topics.append(topic_plan)

    _read_database(plan)
    return plan


def _read_database(plan: LovablePlan) -> None:
    """Which of the package's references the database already holds, and what that means."""
    from django.db.models import Q

    from app.matters.models import Matter

    from .models import EvidencePilotRecord

    plan.existing_matters = Matter.all_objects.count()
    pairs = [parse_reference(t.reference) for t in plan.topics]
    query = Q()
    for year, number in pairs:  # type: ignore[misc]
        query |= Q(reference_year=year, reference_number=number)
    taken = Matter.all_objects.filter(query) if pairs else Matter.all_objects.none()
    mine = taken.filter(
        evidence_pilot_records__pilot=PILOT,
        evidence_pilot_records__package_sha256=plan.digest,
        evidence_pilot_records__record_kind="MATTER",
    ).distinct()
    written = EvidencePilotRecord.objects.filter(pilot=PILOT, package_sha256=plan.digest).count()
    taken_refs = sorted(m.display_reference for m in taken)
    if not taken_refs and written == 0:
        plan.database_state = "ready"
    elif mine.count() == len(plan.topics) == len(taken_refs) and written == sum(
        plan.expected_writes().values()
    ):
        plan.database_state = "applied"
    else:
        mine_refs = {m.display_reference for m in mine}
        plan.collisions = [ref for ref in taken_refs if ref not in mine_refs] or taken_refs
        plan.database_state = "conflict"
        plan.problems.append(
            "These references already exist in Juristid and are never overwritten; replace them "
            f"in the selection and plan again: {', '.join(plan.collisions)}"
        )


def plan_digest(plan: LovablePlan) -> str:
    """What a person approves: the package and everything resolved against the database.

    Deliberately not the total number of Matters: lawyers keep working while an
    import is planned, and a new unrelated Teema is not a reason to re-approve.
    Every one of the package's references being free is, and is in here.
    """
    return canonical_sha256(
        {
            "version": PLAN_DIGEST_VERSION,
            "package_sha256": plan.digest,
            "database_state": plan.database_state,
            "collisions": plan.collisions,
            "topics": [
                {
                    "reference": t.reference,
                    "owner": str(t.owner.pk) if t.owner else "",
                    "stage": t.stage.key if t.stage else "",
                    "instruments": sorted(i.key for i in t.instruments),
                    "addressee": str(t.addressee.pk) if t.addressee else "",
                    "successor": t.successor_in_batch,
                }
                for t in plan.topics
            ],
        }
    )


def existing_fingerprint(*, exclude_references: set[str] | None = None) -> str:
    """One hash over every Matter's identity and last change, for "nothing else was touched"."""
    from app.matters.models import Matter

    exclude = exclude_references or set()
    rows = [
        f"{m['pk']}|{m['updated_at'].isoformat()}"
        for m in Matter.all_objects.order_by("pk").values(
            "pk", "updated_at", "reference_year", "reference_number"
        )
        if f"{m['reference_year']}_{m['reference_number']}" not in exclude
    ]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@dataclass
class ApplyReport:
    already_applied: bool = False
    dry_run: bool = False
    batch_id: Any = None
    written: dict[str, int] = field(default_factory=dict)
    matters_before: int = 0
    matters_after: int = 0
    fingerprint_before: str = ""
    fingerprint_after: str = ""
    verify: Any = None

    def count(self, kind: str) -> None:
        self.written[kind] = self.written.get(kind, 0) + 1


def _refuse_unless_authorised(
    plan: LovablePlan,
    *,
    operator_intent: str,
    expect_package_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
) -> None:
    if not getattr(settings, "LOVABLE_PILOT_ENABLED", False):
        raise LovablePilotError(
            "The Lovable pilot is off in this environment (JURISTID_LOVABLE_PILOT). It runs only "
            "on the one-off container that applies it."
        )
    if not getattr(settings, "REAL_DATA_ALLOWED", False):
        raise LovablePilotError(
            "REAL_DATA_ALLOWED is off. Real Chamber content may only enter an environment that "
            "has passed the Secure Pilot Gate (docs/secure-pilot-gate.md)."
        )
    if operator_intent != OPERATOR_INTENT:
        raise LovablePilotError(f"Type --operator-intent {OPERATOR_INTENT} to apply the package.")
    if (expect_package_sha256 or "").strip().lower() != plan.digest:
        raise LovablePilotError(
            f"The package digest is {plan.digest}; pass exactly that as --expect-package-sha256."
        )
    if not (expect_plan_sha256 or "").strip():
        raise LovablePilotError(
            f"The plan digest is {plan_digest(plan)}; review the plan and pass it as "
            "--expect-plan-sha256."
        )
    if not _BACKUP_SET.match((backup_set or "").strip()):
        raise LovablePilotError(
            "Name the verified backup set taken before the import (--backup-set "
            "sets/YYYYMMDDTHHMMSSZ). The package is applied only with a recovery point named."
        )
    if plan.problems:
        raise LovablePilotError(
            "The plan has problems; nothing was written:\n  " + "\n  ".join(plan.problems)
        )


def apply_plan(
    plan: LovablePlan,
    *,
    operator_intent: str,
    expect_package_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
) -> ApplyReport:
    """Write the package. One transaction; refuses without every authorisation.

    The plan is built again inside the transaction, after the lock, and must hash
    to the digest the operator approved (docs/adr/0184). An applied package
    writes nothing and says so.
    """
    _refuse_unless_authorised(
        plan,
        operator_intent=operator_intent,
        expect_package_sha256=expect_package_sha256,
        expect_plan_sha256=expect_plan_sha256,
        backup_set=backup_set,
    )
    if plan.database_state == "applied":
        return ApplyReport(already_applied=True)

    from app.search.indexing import indexable_matters, refresh_matters

    with transaction.atomic():
        current = _hold_and_replan(plan, expect_plan_sha256)
        writer = _Writer(current, backup_set=backup_set.strip(), plan_sha256=expect_plan_sha256)
        report, touched = writer.write()
        refresh_matters(indexable_matters().filter(pk__in=touched))
    return report


class _RolledBack(Exception):
    def __init__(self, report: ApplyReport) -> None:
        self.report = report


def dry_run(plan: LovablePlan) -> ApplyReport:
    """Everything `apply` writes, verified record by record, then rolled back.

    Gated by the pilot flag only: nothing it writes survives the transaction, so
    the database it runs against keeps no real content. Run it against an
    isolated copy, never the shared development database.
    """
    if not getattr(settings, "LOVABLE_PILOT_ENABLED", False):
        raise LovablePilotError(
            "The Lovable pilot is off in this environment (JURISTID_LOVABLE_PILOT)."
        )
    if plan.problems:
        raise LovablePilotError(
            "The plan has problems; nothing was written:\n  " + "\n  ".join(plan.problems)
        )
    if plan.database_state != "ready":
        raise LovablePilotError(f"The database is {plan.database_state!r}, not ready.")
    try:
        with transaction.atomic():
            current = _hold_and_replan(plan, plan_digest(plan))
            writer = _Writer(current, backup_set="dry-run", plan_sha256=plan_digest(plan))
            report, _touched = writer.write()
            report.verify = verify_applied(build_plan(plan.package))
            raise _RolledBack(report)
    except _RolledBack as rolled:
        rolled.report.dry_run = True
        return rolled.report


def _hold_and_replan(plan: LovablePlan, expect_plan_sha256: str) -> LovablePlan:
    """Lock out other Matter writers, then require the plan to be what was approved."""
    from django.db import connection

    from app.matters.models import Matter

    with connection.cursor() as cursor:
        cursor.execute(f'LOCK TABLE "{Matter._meta.db_table}" IN SHARE ROW EXCLUSIVE MODE')
    current = build_plan(plan.package)
    try:
        require_digest(
            plan_digest(current),
            expect_plan_sha256,
            flag="--expect-plan-sha256",
            what="The Lovable-pilot plan",
        )
    except IntegrityError as error:
        raise LovablePilotError(str(error)) from error
    if current.problems or current.database_state != "ready":
        raise LovablePilotError(
            "The plan rebuilt under the lock is not writable; nothing was written:\n  "
            + "\n  ".join(current.problems or [current.database_state])
        )
    return current


class _Writer:
    """Everything `apply_plan` writes, in the order the services require."""

    def __init__(self, plan: LovablePlan, *, backup_set: str, plan_sha256: str) -> None:
        self.plan = plan
        self.package = plan.package
        self.backup_set = backup_set
        self.plan_sha256 = plan_sha256
        self.report = ApplyReport()
        self.base = {
            "source": PROVENANCE_SOURCE,
            "pilot": PILOT,
            "pilot_version": PILOT_VERSION,
            "package_sha256": plan.digest,
        }

    def remember(self, *, matter: Any, key: str, kind: str, target: Any, evidence: Any) -> None:
        from .models import EvidencePilotRecord

        EvidencePilotRecord.objects.create(
            pilot=PILOT,
            pilot_version=PILOT_VERSION,
            package_sha256=self.plan.digest,
            record_key=key,
            record_kind=kind,
            matter=matter,
            target_model=type(target)._meta.label if target is not None else "",
            target_id=getattr(target, "pk", None),
            evidence=evidence or {},
        )
        self.report.count(kind)

    def write(self) -> tuple[ApplyReport, list[Any]]:
        from app.audit.enums import SecurityEventType
        from app.audit.services import record_security_event
        from app.legacy_import.models import ImportBatch, ReconciliationStatus
        from app.matters.models import Matter
        from app.matters.services import reserve_matter_reference

        references = {t.reference for t in self.plan.topics}
        self.report.matters_before = Matter.all_objects.count()
        self.report.fingerprint_before = existing_fingerprint()
        snapshot = self.package["snapshot"]
        self.batch = ImportBatch.objects.create(
            source_system=SOURCE_SYSTEM,
            source_file_name=SOURCE_TABLE,
            source_snapshot_sha256=snapshot["snapshot_sha256"],
            importer_version=f"{PILOT}/{PILOT_VERSION}",
            contract_version=f"{PACKAGE_FORMAT}/{PACKAGE_FORMAT_VERSION}",
            started_at=timezone.now(),
            source_row_count=len(self.plan.topics),
            reconciliation_status=ReconciliationStatus.RUNNING,
            notes=(
                f"{PILOT}: {len(self.plan.topics)} teemat Lovable'i hetktõmmisest "
                f"{snapshot['snapshot_sha256']} (seis {snapshot.get('as_of')}); pakett "
                f"{self.plan.digest}; plan-sha256 {self.plan_sha256}; varukoopia "
                f"{self.backup_set}; teemasid enne {self.report.matters_before} "
                f"(docs/adr/0191)."
            ),
        )
        self.report.batch_id = self.batch.pk

        created: dict[str, Any] = {}
        for topic_plan in self.plan.topics:
            created[topic_plan.reference] = self.write_topic(topic_plan)

        # Successors exist now; only a successor inside this package is linked.
        for topic_plan in self.plan.topics:
            if topic_plan.successor_in_batch:
                matter = created[topic_plan.reference]
                matter.superseded_by = created[topic_plan.successor_in_batch]
                matter.save(update_fields=["superseded_by", "updated_at"])
                self.remember(
                    matter=matter,
                    key=f"{topic_plan.reference}-CONTINUES",
                    kind="CONTINUATION",
                    target=matter.superseded_by,
                    evidence={"successor": topic_plan.successor_in_batch},
                )

        for year, through in sorted((self.package.get("reserve_through") or {}).items()):
            reserve_matter_reference(int(year), int(through))

        self.report.matters_after = Matter.all_objects.count()
        self.report.fingerprint_after = existing_fingerprint(exclude_references=references)
        if self.report.fingerprint_after != self.report.fingerprint_before:
            raise LovablePilotError(
                "A Matter that was already in the database changed during the import; "
                "nothing was written."
            )
        self.batch.finished_at = timezone.now()
        self.batch.created_matter_count = len(created)
        self.batch.reconciliation_status = ReconciliationStatus.COMPLETED
        self.batch.notes += (
            f" Teemasid pärast {self.report.matters_after}; olemasolevate sõrmejälg "
            f"{self.report.fingerprint_before} muutumatu."
        )
        self.batch.save(
            update_fields=[
                "finished_at",
                "created_matter_count",
                "reconciliation_status",
                "notes",
                "updated_at",
            ]
        )
        record_security_event(
            event_type=SecurityEventType.IMPORT_RUN,
            actor=None,
            subject=self.batch,
            detail={
                **self.base,
                "plan_sha256": self.plan_sha256,
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "topics": len(created),
                "backup_set": self.backup_set,
                "written": self.report.written,
                "matters_before": self.report.matters_before,
                "matters_after": self.report.matters_after,
            },
        )
        return self.report, [m.pk for m in created.values()]

    def write_topic(self, topic_plan: TopicPlan) -> Any:
        from app.legacy_import.enums import OneNoteContentStatus
        from app.legacy_import.models import MatchMethod, MatterSourceReference
        from app.matters.enums import DataQualityTier, RecordMode
        from app.matters.response_deadlines import request_response_deadline
        from app.matters.services import create_imported_matter
        from app.workflow.enums import Track

        topic = topic_plan.topic
        year, number = parse_reference(topic_plan.reference)  # type: ignore[misc]
        active = topic_plan.is_open
        fields: dict[str, Any] = {
            "owner": topic_plan.owner,
            "stage": topic_plan.stage,
            "received_date": _day(topic.get("received_date")),
            "addressee_organisation": topic_plan.addressee,
            "legal_instruments": topic_plan.instruments,
            "track": Track.KODA_INITIATIVE if topic.get("koda_initiative") else "",
            "source_era": str(year),
            "data_quality_tier": (
                DataQualityTier.TIER_1_VERIFIED_ACTIVE
                if active
                else DataQualityTier.TIER_2_RICH_HISTORY
            ),
            "provenance": {**self.base, "reference": topic_plan.reference},
        }
        if not active:
            # Closed the way a register closes a finished file: its reason, and
            # no date nobody wrote down (`matters_closure_fields_consistent`).
            fields["is_open"] = False
            fields["disposition"] = topic["disposition"]
        matter = create_imported_matter(
            title=topic["title"].strip(),
            reference_year=year,
            reference_number=number,
            record_mode=RecordMode.FULL if active else RecordMode.ARCHIVE,
            **fields,
        )
        source = topic["source"]
        snapshot = self.package["snapshot"]
        url = topic.get("onenote_url") or ""
        MatterSourceReference.objects.create(
            matter=matter,
            import_batch=self.batch,
            source_system=SOURCE_SYSTEM,
            source_file_name=SOURCE_TABLE,
            source_snapshot_sha256=snapshot["snapshot_sha256"],
            source_sheet=f"{SOURCE_TABLE}:{year}",
            source_row_number=number,
            source_row_raw={
                "lovable": {
                    "project": snapshot.get("project", ""),
                    "id": source["id"],
                    "version": source.get("version"),
                    "updated_at": source.get("updated_at"),
                    "as_of": snapshot.get("as_of"),
                },
                "row": source["row"],
                "excel_source_row": source.get("excel_source_row"),
                "history": source.get("history") or [],
                "commencements": source.get("commencements") or [],
                "responsible_changes": source.get("responsible_changes") or [],
                "field_decisions": topic.get("field_decisions") or {},
                "review_notes": topic.get("review_notes") or [],
            },
            source_title=topic["title"],
            source_date_raw=str(source["row"].get("sisse") or "")[:200],
            onenote_url=url,
            onenote_content_status=(
                OneNoteContentStatus.NOT_IMPORTED if url else OneNoteContentStatus.NOT_APPLICABLE
            ),
            source_era=str(year),
            source_contract_version=f"{PACKAGE_FORMAT}/{PACKAGE_FORMAT_VERSION}",
            # The parser only proposed; a person decided (the package's
            # `mapping_version` names the review). 50 characters, so the short form.
            source_parser_version=f"{PILOT}/{PILOT_VERSION}+reviewed",
            match_method=MatchMethod.REFERENCE_TOKEN,
            conflict_state="NONE",
            reviewed_by=str(self.package.get("reviewer") or "")[:200],
        )
        self.remember(
            matter=matter,
            key=topic_plan.reference,
            kind="MATTER",
            target=matter,
            evidence={
                "lovable_id": source["id"],
                "lovable_version": source.get("version"),
                "group": topic.get("group", ""),
                "lifecycle": topic["lifecycle"],
                "warnings": topic_plan.warnings,
            },
        )
        if not active:
            return matter

        deadline = topic.get("deadline") or {}
        if deadline.get("treatment") == "OPEN_REQUEST":
            # The current request, recorded as one, so only an answer that names it
            # discharges it — never a historical send (response_deadlines.py).
            day = _day(deadline["deadline"])
            if day is None:  # validate_package refuses this; never reached
                raise LovablePilotError(f"{topic_plan.reference}: the open request has no date.")
            request_response_deadline(matter=matter, deadline=day, actor=None)
            self.remember(
                matter=matter,
                key=f"{topic_plan.reference}-REQUEST",
                kind="RESPONSE_REQUEST",
                target=matter,
                evidence={"deadline": deadline["deadline"]},
            )
        self.write_next_action(matter, topic_plan)
        for record in topic.get("commencements") or []:
            if record.get("review") == READY:
                self.write_commencement(matter, topic_plan, record)
        return matter

    def write_next_action(self, matter: Any, topic_plan: TopicPlan) -> None:
        from app.workflow.services import set_next_action

        step = topic_plan.topic.get("next_action")
        if not step:
            return
        action = set_next_action(
            matter=matter,
            text=step["text"].strip(),
            kind=step["kind"],
            date_semantics=step["date_semantics"],
            target_date=_day(step.get("target_date")),
            date_precision=step.get("date_precision") or "EXACT",
            source_text=step.get("source_text") or step["text"],
            responsible=topic_plan.owner,
            actor=None,
            provenance={
                **self.base,
                "reference": topic_plan.reference,
                "treatment": step["treatment"],
                # The reviewer's reading, stated in the package with its reason —
                # never derived from the wording at apply (docs/adr/0157 §3).
                "kind_basis": "REVIEWED_PACKAGE",
            },
        )
        self.remember(
            matter=matter,
            key=f"{topic_plan.reference}-NEXT",
            kind="NEXT_ACTION",
            target=action,
            evidence={"reason": step.get("reason", ""), "source_text": step.get("source_text", "")},
        )

    def write_commencement(
        self, matter: Any, topic_plan: TopicPlan, record: dict[str, Any]
    ) -> None:
        from app.intelligence.services import add_effective_date, add_important_date

        day = _day(record.get("date"))
        note = (record.get("note") or "").strip()
        created: Any
        if record["target"] == T_EFFECTIVE_DATE:
            created = add_effective_date(
                matter=matter,
                kind=record["kind"],
                date_value=day,
                period_end=day,
                date_precision="EXACT",
                description=note,
                note="",
                source_url="",
                actor=None,
                source_text=record.get("source_text") or "",
            )
        else:
            if day is None:  # validate_package refuses this; never reached
                raise LovablePilotError(f"{record['key']}: an important date needs its day.")
            created = add_important_date(
                matter=matter,
                title=COMMENCEMENT_TITLES[record["kind"]],
                date_value=day,
                period_end=day,
                date_precision="EXACT",
                kind=record["kind"],
                note=note,
                actor=None,
                source_text=record.get("source_text") or "",
            )
        self.remember(
            matter=matter,
            key=record["key"],
            kind=record["target"],
            target=created,
            evidence={"liik": record.get("liik", ""), "source_id": record.get("source_id", "")},
        )


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


@dataclass
class VerifyReport:
    checked: int = 0
    discrepancies: list[str] = field(default_factory=list)
    held: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.discrepancies


def verify_applied(plan: LovablePlan) -> VerifyReport:
    """Every topic against what the database holds. Read-only; fails on any difference."""
    from app.documents.models import Document
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.legacy_import.models import MatterSourceReference
    from app.matters.models import Matter
    from app.submissions.models import Submission
    from app.workflow.models import NextAction, OpinionFollowUp

    report = VerifyReport()
    out = report.discrepancies
    for topic_plan in plan.topics:
        topic = topic_plan.topic
        ref = topic_plan.reference
        report.checked += 1
        year, number = parse_reference(ref)  # type: ignore[misc]
        matters = list(Matter.all_objects.filter(reference_year=year, reference_number=number))
        if len(matters) != 1:
            out.append(f"{ref}: {len(matters)} Matters hold this reference, not 1.")
            continue
        m = matters[0]
        marked = m.evidence_pilot_records.filter(
            pilot=PILOT, package_sha256=plan.digest, record_kind="MATTER"
        ).count()
        if marked != 1:
            out.append(f"{ref}: not marked as this package's Matter.")
        if m.title != topic["title"].strip():
            out.append(f"{ref}: title differs.")
        if m.owner_id != (topic_plan.owner.pk if topic_plan.owner else None):
            out.append(f"{ref}: owner differs.")
        if m.stage_id != (topic_plan.stage.pk if topic_plan.stage else None):
            out.append(f"{ref}: stage differs.")
        if m.is_open != topic_plan.is_open:
            out.append(f"{ref}: open/closed differs.")
        if m.closed_at is not None:
            out.append(f"{ref}: a closure date was written; none is in the source.")
        if not topic_plan.is_open and m.disposition != topic["disposition"]:
            out.append(f"{ref}: disposition {m.disposition!r} ≠ {topic['disposition']!r}.")
        if sorted(i.key for i in m.legal_instruments.all()) != sorted(
            topic.get("instruments") or []
        ):
            out.append(f"{ref}: legal instruments differ.")
        expected_successor = topic_plan.successor_in_batch
        actual_successor = m.superseded_by.display_reference if m.superseded_by else ""
        if actual_successor != expected_successor:
            out.append(f"{ref}: successor {actual_successor!r} ≠ {expected_successor!r}.")

        sources = list(MatterSourceReference.objects.filter(matter=m, source_system=SOURCE_SYSTEM))
        if len(sources) != 1:
            out.append(f"{ref}: {len(sources)} Lovable source references, not 1.")
        else:
            src = sources[0]
            if src.onenote_url != (topic.get("onenote_url") or ""):
                out.append(f"{ref}: OneNote link not preserved exactly.")
            if src.source_snapshot_sha256 != plan.package["snapshot"]["snapshot_sha256"]:
                out.append(f"{ref}: snapshot digest not recorded.")
            if (src.source_row_raw.get("lovable") or {}).get("id") != topic["source"]["id"]:
                out.append(f"{ref}: Lovable identity not recorded.")

        current = list(NextAction.objects.filter(matter=m, status="OPEN"))
        step = topic.get("next_action")
        if step is None and current:
            out.append(f"{ref}: a current step exists; the package has none.")
        if step is not None:
            if len(current) != 1:
                out.append(f"{ref}: {len(current)} current steps, not 1.")
            else:
                a = current[0]
                wanted = (
                    step["kind"],
                    step["date_semantics"],
                    _day(step.get("target_date")),
                    step["text"].strip(),
                    topic_plan.owner.pk if topic_plan.owner else None,
                )
                actual = (a.kind, a.date_semantics, a.target_date, a.text, a.responsible_id)
                if actual != wanted:
                    out.append(f"{ref}: current step {actual[:3]} ≠ {wanted[:3]}.")
        deadline = topic.get("deadline") or {}
        if deadline.get("treatment") == "OPEN_REQUEST":
            if m.response_deadline != _day(deadline["deadline"]) or m.response_requested_at is None:
                out.append(f"{ref}: the open opinion request is not recorded as one.")
        elif m.response_deadline is not None:
            out.append(f"{ref}: a response deadline was written; the source's is history only.")

        ready = [r for r in topic.get("commencements") or [] if r.get("review") == READY]
        effective = MatterEffectiveDate.objects.filter(matter=m).count()
        important = MatterImportantDate.objects.filter(matter=m).count()
        if effective != sum(1 for r in ready if r["target"] == T_EFFECTIVE_DATE):
            out.append(f"{ref}: {effective} effective dates, package says otherwise.")
        if important != sum(1 for r in ready if r["target"] == T_IMPORTANT_DATE):
            out.append(f"{ref}: {important} important dates, package says otherwise.")
        for r in topic.get("commencements") or []:
            if r.get("review") != READY:
                report.held["commencement"] = report.held.get("commencement", 0) + 1

        if Submission.objects.filter(matter=m).exists():
            out.append(f"{ref}: a Submission exists; this pilot writes none.")
        if Document.objects.filter(matter=m).exists():
            out.append(f"{ref}: a Document exists; this pilot writes none.")
        if OpinionFollowUp.objects.filter(submission__matter=m).exists():
            out.append(f"{ref}: a follow-up check exists; this pilot schedules none.")
        if deadline.get("treatment") == "SOURCE_ONLY":
            report.held["historical_deadline"] = report.held.get("historical_deadline", 0) + 1
    return report
