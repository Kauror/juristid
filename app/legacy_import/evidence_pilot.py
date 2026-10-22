"""The evidence pilot: a few Teemad with their state and their real history.

A reviewed **package** — built outside the repository from a source system's
current state (the topic's owner, `Hetkeseis`, deadlines, next step) and the
professional history reconstructed from the original OneNote dossier and its
attachments — becomes native Juristid records through the ordinary services:
the Teema, its current step, consultation rounds, received feedback, other
organisations' positions, the Chamber's sent opinions **with the real signed
letters**, koda.ee overviews and news, procedural developments, notes,
expected commencement, and every original file (docs/adr/0173).

**What it never does.** It writes no audit event of its own and backdates none:
every service it calls writes its ordinary change event, stamped with the moment
of the import and with no actor, and a business date lives where the domain
keeps it (`sent_at`, `occurred_on`, `stated_on`, `published_on`). It invents no
stage history, no closure, no follow-up check and no person, and no organisation
except one the package carries as a reviewed APPROVED entry (§6). A record the
package marks `NEEDS_REVIEW` or `REJECTED` is not written; the plan lists it.

**What says "imported".** `EvidencePilotRecord`: one row per record written,
naming the package digest, the record's key and the evidence it rests on.

**One reading, three steps** (the shape of `excel_pilot`, docs/adr/0148):
`validate_package` checks the package and every byte it points at without a
database; `build_plan` resolves people, stages, instruments and organisations
and says what the database holds; `apply_plan` writes everything in one
transaction, refusing unless the environment, the operator, the digest, the
backup set and an empty database all say yes. Run again, it writes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.legacy_import import safe_paths
from app.legacy_import.integrity import (
    IntegrityError,
    SourceManifest,
    build_manifest,
    canonical_sha256,
    require_digest,
)
from app.legacy_import.limits import (
    SourceLimitExceeded,
    WorkBudget,
    current_limits,
    load_json_document,
)
from app.legacy_import.onenote_archive import ArchiveError, ArchivePage, OneNoteArchive

PILOT = "evidence-pilot"
PILOT_VERSION = "1.1"
PACKAGE_FORMAT = "juristid.evidence-pilot"
PACKAGE_FORMAT_VERSION = 1
PACKAGE_FILE = "package.json"
ARCHIVE_DIRECTORY = "onenote-desktop-archive"
#: Typed by the operator at `apply`, the same discipline as `excel_pilot_2026`.
OPERATOR_INTENT = "evidence-pilot"
#: `MATTER_CREATED`, `NEXT_ACTION_SET` and the security event name this source.
PROVENANCE_SOURCE = "EVIDENCE_PILOT"
SOURCE_SYSTEM = "ONENOTE_DESKTOP+PACKAGE"

READY = "READY"
NEEDS_REVIEW = "NEEDS_REVIEW"
#: Reviewed and decided against for import: kept in the package with its
#: reason, never written. Different from NEEDS_REVIEW, which still waits.
REJECTED = "REJECTED"
REVIEW_STATES = frozenset({READY, NEEDS_REVIEW, REJECTED})

#: Package precision → the domain's. A month stays a month (docs/adr/0079).
PRECISION = {"DAY": "EXACT", "MONTH": "MONTH", "QUARTER": "QUARTER", "YEAR": "YEAR"}

#: An organisation entry the package asks the import to create is the owner's
#: reviewed decision (APPROVED) or still waiting for one (PROPOSED).
ORGANISATION_STATUSES = frozenset({"APPROVED", "PROPOSED"})
#: `EvidencePilotRecord.record_key` and `Organisation.source_reference` lengths,
#: checked at validate so a long value refuses the package, not the apply.
ORGANISATION_KEY_LENGTH = 120
#: The provenance kinds that belong to the catalogue, not to one Teema.
ORGANISATION_RECORD_KINDS = ("ORGANISATION", "ORGANISATION_ALIAS", "ORGANISATION_RELATIONSHIP")
#: The one institutional relationship a package may state: the same body under a
#: new name or remit (an `ümberkorraldamine`), recorded as `Organisation.predecessor`
#: — the link the catalogue keeps for «genuinely a continuation»
#: (app/organisations/reference_data.py). A split, a merger or a transfer of one
#: task is **not** representable this way and is refused, never approximated.
ORGANISATION_RELATIONSHIPS = frozenset({"CONTINUATION"})
#: How a reviewed source step's date reads when the package does not say. DO is
#: every package before v2.3; WAIT and MONITOR are never deadlines.
DEFAULT_STEP_SEMANTICS = {"DO": "DEADLINE", "WAIT": "EXPECTED_AROUND", "MONITOR": "REVIEW_ON"}
ORGANISATION_SOURCE_REFERENCE_LENGTH = 200

T_DEVELOPMENT = "ProceduralDevelopment"
T_ENGAGEMENT = "MatterEngagement"
T_POSITION_RECEIVED = "MatterExternalPosition.RECEIVED"
T_POSITION_DISCOVERED = "MatterExternalPosition.DISCOVERED"
T_SUBMISSION = "Submission.SENT"
T_OVERVIEW = "MatterWebsiteOverview.OVERVIEW"
T_NEWS = "MatterWebsiteOverview.NEWS"
T_NOTE = "Entry.NOTE"
T_MEETING = "Entry.MEETING"
T_EFFECTIVE_DATE = "MatterEffectiveDate"
T_IMPORTANT_DATE = "MatterImportantDate"
#: Targets whose date is when something is *planned* to happen, so a date after
#: the snapshot is ordinary rather than a newer fact.
PROSPECTIVE_TARGETS = frozenset({"MatterEffectiveDate", "MatterImportantDate"})
WRITABLE_TARGETS = frozenset(
    {
        T_DEVELOPMENT,
        T_ENGAGEMENT,
        T_POSITION_RECEIVED,
        T_POSITION_DISCOVERED,
        T_SUBMISSION,
        T_OVERVIEW,
        T_NEWS,
        T_NOTE,
        T_MEETING,
        T_EFFECTIVE_DATE,
        T_IMPORTANT_DATE,
    }
)
#: Targets a package may carry for the owner but the pilot never writes.
REVIEW_ONLY_TARGETS = frozenset({"ReviewOnly", "WorkVictoryCandidate", "MatterField"})

_REFERENCE = re.compile(r"^(\d{4})_(\d{1,6})$")


class EvidencePilotError(Exception):
    """The evidence pilot refused. The message says what to fix; nothing was written."""


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------


def package_digest(package: dict[str, Any]) -> str:
    """SHA-256 of the package as canonical JSON — the value `apply` demands."""
    canonical = json.dumps(package, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_package(
    root: str | Path,
    *,
    manifest: SourceManifest | None = None,
    budget: WorkBudget | None = None,
) -> tuple[dict[str, Any], OneNoteArchive]:
    """`package.json` and the archive beside it, read within limits.

    With a `manifest` (docs/adr/0184), `package.json` and every archive file
    read later must be exactly the bytes it lists: the decisions parsed are the
    decisions that were hashed.
    """
    root = Path(root)
    limits = current_limits()
    budget = budget or WorkBudget(limits)
    try:
        if safe_paths.lookup(root, PACKAGE_FILE) is not safe_paths.Kind.FILE:
            raise EvidencePilotError(f"{root} holds no {PACKAGE_FILE}.")
        data = safe_paths.read_bytes(
            root, PACKAGE_FILE, max_bytes=limits.max_json_bytes, budget=budget
        )
        if manifest is not None:
            manifest.verify(PACKAGE_FILE, data)
        package = load_json_document(data, what=PACKAGE_FILE, limits=limits)
    except json.JSONDecodeError as error:
        raise EvidencePilotError(f"{PACKAGE_FILE} is not valid JSON: {error}") from error
    except (safe_paths.UnsafeSourcePath, SourceLimitExceeded, IntegrityError) as error:
        raise EvidencePilotError(str(error)) from error
    if not isinstance(package, dict):
        raise EvidencePilotError(f"{PACKAGE_FILE} is not a JSON object.")
    try:
        archive = OneNoteArchive(
            root / ARCHIVE_DIRECTORY,
            manifest=manifest.subtree(ARCHIVE_DIRECTORY) if manifest is not None else None,
            budget=budget,
        )
    except (ArchiveError, IntegrityError, SourceLimitExceeded) as error:
        raise EvidencePilotError(str(error)) from error
    return package, archive


def package_manifest(root: str | Path, *, budget: WorkBudget | None = None) -> SourceManifest:
    """Every file of the package directory: path, size, SHA-256 (docs/adr/0184).

    The package digest covers `package.json` only; this covers what it is read
    with — page metadata, text, blocks, links, source XML, resource records and
    originals, and anything else in the directory, so an added, removed or
    changed file is a different package.
    """
    try:
        return build_manifest(Path(root), budget=budget)
    except (IntegrityError, SourceLimitExceeded, safe_paths.UnsafeSourcePath) as error:
        raise EvidencePilotError(str(error)) from error


def parse_reference(reference: str) -> tuple[int, int] | None:
    match = _REFERENCE.match(reference or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _day(raw: Any) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(raw))
    except ValueError:
        return None


def _anchor(raw: Any, precision: str) -> dt.date | None:
    """A period's first day from `YYYY-MM-DD`, `YYYY-MM`, `YYYY-Qn` or `YYYY`, as precision says."""
    text = str(raw or "")
    try:
        if precision == "DAY":
            return dt.date.fromisoformat(text)
        if precision == "MONTH":
            year, month = text[:7].split("-")
            return dt.date(int(year), int(month), 1)
        if precision == "QUARTER":
            year, quarter = text.split("-Q")
            if quarter not in {"1", "2", "3", "4"}:
                return None
            return dt.date(int(year), 3 * int(quarter) - 2, 1)
        if precision == "YEAR":
            return dt.date(int(text[:4]), 1, 1)
    except ValueError:
        return None
    return None


@dataclass
class ValidationReport:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    topics: int = 0
    records_ready: int = 0
    records_held: int = 0
    documents_ready: int = 0
    documents_held: int = 0
    resources_accounted: int = 0
    resources_on_pages: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def _topic_resources(archive: OneNoteArchive, page_key: str) -> tuple[ArchivePage | None, dict]:
    try:
        page = archive.page(page_key)
    except (ArchiveError, FileNotFoundError, json.JSONDecodeError):
        return None, {}
    return page, {resource.resource_key: resource for resource in page.resources}


@dataclass
class _PageAccounting:
    """Which of one page's files the package uses, and where — each exactly once."""

    ref: str
    page: ArchivePage
    resources: dict[str, Any]
    archive: OneNoteArchive
    used: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def use(self, key: str, where: str, *, ready: bool, sha: str | None) -> None:
        ref = self.ref
        resource = self.resources.get(key)
        if resource is None:
            self.problems.append(f"{ref}: {where} names {key!r}, which is not on the page.")
            return
        if key in self.used:
            self.problems.append(f"{ref}: {key} is used by {self.used[key]} and {where}.")
            return
        self.used[key] = where
        if not sha:
            # The digest covers package.json, not the archive: the package
            # itself has to pin every file it accounts for, or a re-exported
            # archive could change the bytes under an unchanged digest.
            self.problems.append(f"{ref}: {where} names {key} without its SHA-256.")
        elif sha != resource.sha256:
            self.problems.append(f"{ref}: {key} SHA-256 in the package differs from the archive.")
        if not ready:
            return
        if not resource.is_captured or resource.size_bytes == 0:
            self.problems.append(f"{ref}: {where} imports {key}, which has no bytes.")
            return
        content = self.archive.read_resource(self.page.page_key, resource)
        if hashlib.sha256(content).hexdigest() != resource.sha256:
            self.problems.append(f"{ref}: {key} bytes do not hash to the archive's SHA-256.")


def validate_package(package: dict[str, Any], archive: OneNoteArchive) -> ValidationReport:
    """Every structural rule the package must meet, and every byte re-hashed.

    Pure: reads the package and the archive, touches no database. A problem
    blocks `apply`; a warning is for the reviewer.
    """
    from app.documents.enums import DocumentRole
    from app.workflow.vocabulary import CURRENT_LABEL_TO_STAGE, RAW_LABEL_TO_STAGE

    report = ValidationReport()
    problems = report.problems
    if package.get("format") != PACKAGE_FORMAT:
        problems.append(f"format is {package.get('format')!r}, not {PACKAGE_FORMAT!r}.")
    if package.get("format_version") != PACKAGE_FORMAT_VERSION:
        problems.append(f"format_version must be {PACKAGE_FORMAT_VERSION}.")
    if package.get("data_class") not in {"REAL", "SYNTHETIC"}:
        problems.append("data_class must be REAL or SYNTHETIC.")
    snapshot_day = _day((package.get("snapshot") or {}).get("as_of"))
    if snapshot_day is None:
        problems.append("snapshot.as_of must name the source snapshot's last day (YYYY-MM-DD).")
    year = package.get("reference_year")
    topics = package.get("topics") or []
    report.topics = len(topics)
    expected = package.get("expected") or {}
    if expected.get("topics") is not None and expected["topics"] != len(topics):
        problems.append(f"expected {expected['topics']} topics, the package holds {len(topics)}.")

    problems.extend(_organisation_problems(package.get("organisations") or []))
    problems.extend(_relationship_problems(package))

    stage_labels = set(RAW_LABEL_TO_STAGE) | set(CURRENT_LABEL_TO_STAGE)
    roles = set(DocumentRole.values)
    references: set[str] = set()
    record_keys: set[str] = set()
    owners: dict[str, int] = {}
    for topic in topics:
        ref = topic.get("reference", "")
        parsed = parse_reference(ref)
        if parsed is None:
            problems.append(f"{ref!r}: not a YYYY_N reference.")
            continue
        if parsed[0] != year:
            problems.append(f"{ref}: year {parsed[0]} is not the package's reference_year {year}.")
        if ref in references:
            problems.append(f"{ref}: appears twice.")
        references.add(ref)
        owners[topic.get("owner", "")] = owners.get(topic.get("owner", ""), 0) + 1
        if not (topic.get("title") or "").strip():
            problems.append(f"{ref}: title is empty.")
        if topic.get("stage_label") not in stage_labels:
            problems.append(f"{ref}: Hetkeseis {topic.get('stage_label')!r} is not a known label.")
        for name in ("received_date", "response_deadline"):
            if topic.get(name) and _day(topic[name]) is None:
                problems.append(f"{ref}: {name} {topic[name]!r} is not a date.")
        step = topic.get("next_action")
        if step:
            if not (step.get("text") or "").strip():
                problems.append(f"{ref}: next_action has no text.")
            problems.extend(f"{ref}: {problem}" for problem in step_shape(step)[1])
            if step.get("review") not in REVIEW_STATES:
                problems.append(
                    f"{ref}: next_action.review must be READY, NEEDS_REVIEW or REJECTED."
                )

        source = topic.get("source_page") or {}
        page, resources = _topic_resources(archive, source.get("page_key", ""))
        if page is None:
            problems.append(f"{ref}: source page {source.get('page_key')!r} is not in the archive.")
            continue
        report.resources_on_pages += len(resources)
        try:
            archive.read_page_xml(page)
        except ArchiveError as error:
            problems.append(f"{ref}: {error}")
        accounting = _PageAccounting(ref=ref, page=page, resources=resources, archive=archive)
        use = accounting.use

        engagement_keys: dict[str, bool] = {}
        #: READY positions that carry their own files, which a joint letter's
        #: other signatories may share (`shared_documents_from`).
        position_files: dict[str, bool] = {}
        submissions: dict[str, bool] = {}
        for record in topic.get("records") or []:
            if record.get("target") == T_ENGAGEMENT:
                engagement_keys[record.get("key", "")] = record.get("review") == READY
            if record.get("target") in {T_POSITION_RECEIVED, T_POSITION_DISCOVERED}:
                position_files[record.get("key", "")] = record.get("review") == READY and bool(
                    record.get("documents")
                )
            if record.get("target") == T_SUBMISSION:
                submissions[record.get("key", "")] = record.get("review") == READY
        answered_by = topic.get("answered_by")
        if answered_by:
            if not submissions.get(answered_by):
                problems.append(
                    f"{ref}: answered_by {answered_by!r} is not a READY sent opinion of this topic."
                )
            if not topic.get("response_deadline"):
                problems.append(f"{ref}: answered_by needs a response_deadline to answer.")

        for record in topic.get("records") or []:
            key = record.get("key", "")
            where = f"record {key}"
            if not key.startswith(ref + "-"):
                problems.append(f"{ref}: record key {key!r} does not start with the reference.")
            if key in record_keys:
                problems.append(f"{key}: record key appears twice.")
            record_keys.add(key)
            target = record.get("target")
            review = record.get("review")
            if review not in REVIEW_STATES:
                problems.append(f"{key}: review must be READY, NEEDS_REVIEW or REJECTED.")
            if review == REJECTED and not (record.get("decision_reason") or "").strip():
                problems.append(f"{key}: a rejected record says why (decision_reason).")
            if target in REVIEW_ONLY_TARGETS:
                if review == READY:
                    problems.append(
                        f"{key}: {target} is never imported; mark it NEEDS_REVIEW or REJECTED."
                    )
            elif target not in WRITABLE_TARGETS:
                problems.append(f"{key}: target {target!r} is unknown.")
                continue
            ready = review == READY and target in WRITABLE_TARGETS
            report.records_ready += int(ready)
            report.records_held += int(not ready)
            if not record.get("evidence"):
                problems.append(f"{key}: names no evidence.")
            precision = record.get("date_precision")
            if record.get("date") not in (None, ""):
                if precision not in PRECISION:
                    problems.append(f"{key}: date_precision must be DAY, MONTH, QUARTER or YEAR.")
                else:
                    day = _anchor(record["date"], precision)
                    if day is None:
                        problems.append(
                            f"{key}: date {record['date']!r} does not read as {precision}."
                        )
                    elif (
                        ready
                        and snapshot_day
                        and day > snapshot_day
                        # A commencement or a planned milestone is when something
                        # is to happen, not when it happened: usually the future.
                        and target not in PROSPECTIVE_TARGETS
                    ):
                        problems.append(
                            f"{key}: dated {record['date']}, after the snapshot "
                            f"({snapshot_day}). Newer facts are reported, not imported."
                        )
            if ready:
                problems.extend(
                    _target_problems(key, target, record, engagement_keys, position_files)
                )
            for doc in record.get("documents") or []:
                if doc.get("role") not in roles:
                    problems.append(f"{key}: document role {doc.get('role')!r} is unknown.")
                use(doc.get("resource_key", ""), where, ready=ready, sha=doc.get("sha256"))
                report.documents_ready += int(ready)
                report.documents_held += int(not ready)

        for doc in topic.get("documents_unlinked") or []:
            ready = doc.get("review") == READY
            if doc.get("review") not in REVIEW_STATES:
                problems.append(f"{ref}: unlinked {doc.get('resource_key')} needs a review state.")
            if doc.get("role") not in roles:
                problems.append(f"{ref}: document role {doc.get('role')!r} is unknown.")
            use(
                doc.get("resource_key", ""),
                "documents_unlinked",
                ready=ready,
                sha=doc.get("sha256"),
            )
            report.documents_ready += int(ready)
            report.documents_held += int(not ready)

        for item in topic.get("resources_not_imported") or []:
            if not (item.get("reason") or "").strip():
                problems.append(
                    f"{ref}: {item.get('resource_key')} is not imported without a reason."
                )
            use(
                item.get("resource_key", ""),
                "resources_not_imported",
                ready=False,
                sha=item.get("sha256"),
            )

        problems.extend(accounting.problems)
        missing = sorted(set(resources) - set(accounting.used))
        if missing:
            problems.append(f"{ref}: resources not accounted for: {', '.join(missing)}.")
        report.resources_accounted += len(accounting.used)

    for owner, count in (expected.get("owners") or {}).items():
        if owners.get(owner, 0) != count:
            problems.append(
                f"owner {owner!r}: expected {count} topics, found {owners.get(owner, 0)}."
            )
    return report


@dataclass(frozen=True)
class StepShape:
    """A reviewed source step as the workflow service takes it."""

    kind: str
    date_semantics: str
    target_date: dt.date | None
    date_precision: str


def step_shape(step: dict[str, Any]) -> tuple[StepShape, list[str]]:
    """The step's kind, date meaning, date and precision, and what is wrong with them.

    Read from the package's explicit, reviewed fields — `kind`, `date_semantics`,
    `target_date` or `review_date`, `date_precision` — never from the wording:
    «Ootan …» is not parsed into a WAIT. A package without them (every one before
    v2.3) gets v2.2's shape: DO, DEADLINE, exact. A WAIT or a MONITOR is never a
    deadline, so it can never be invented into one; `review_date` is the day a
    wait or a watch is looked at again (REVIEW_ON).
    """
    from app.workflow.enums import ActionKind, DatePrecision, DateSemantics

    problems: list[str] = []
    kind = step.get("kind") or ActionKind.DO.value
    if kind not in ActionKind.values:
        problems.append(f"next_action.kind {kind!r} is not DO, WAIT or MONITOR.")
        kind = ActionKind.DO.value
    target, review = step.get("target_date"), step.get("review_date")
    if target and review:
        problems.append("next_action has both a target_date and a review_date.")
    if review and kind == ActionKind.DO:
        problems.append("next_action.review_date belongs to a WAIT or MONITOR step, not a DO.")
    semantics = step.get("date_semantics") or (
        DateSemantics.REVIEW_ON.value if review else DEFAULT_STEP_SEMANTICS[kind]
    )
    if semantics not in DateSemantics.values:
        problems.append(f"next_action.date_semantics {semantics!r} is unknown.")
        semantics = DEFAULT_STEP_SEMANTICS[kind]
    if review and semantics != DateSemantics.REVIEW_ON:
        problems.append("next_action.review_date needs date_semantics REVIEW_ON.")
    if kind != ActionKind.DO and semantics == DateSemantics.DEADLINE:
        problems.append("only a DO step has a deadline; a WAIT or MONITOR is never missed.")
    raw = review or target
    day = _day(raw) if raw else None
    if raw and day is None:
        problems.append(f"next_action date {raw!r} is not a date.")
    precision = step.get("date_precision") or DatePrecision.EXACT.value
    if precision not in DatePrecision.values or precision == DatePrecision.INFERRED:
        problems.append(f"next_action.date_precision {precision!r} is not a reviewed precision.")
        precision = DatePrecision.EXACT.value
    if day is None and precision != DatePrecision.EXACT:
        problems.append("an undated next_action has no precision.")
    return StepShape(kind, semantics, day, precision), problems


def _relationship_problems(package: dict[str, Any]) -> list[str]:
    """Every stated institutional relationship is a reviewed CONTINUATION with evidence."""
    out: list[str] = []
    approved = approved_organisations(package)
    successors: set[str] = set()
    for relation in package.get("organisation_relationships") or []:
        before = (relation.get("predecessor") or "").strip()
        after = (relation.get("successor") or "").strip()
        label = f"organisation relationship {before!r} → {after!r}"
        if not before or not after:
            out.append("an organisation relationship needs a predecessor and a successor.")
            continue
        if before == after:
            out.append(f"{label}: an organisation cannot succeed itself.")
        if relation.get("relationship") not in ORGANISATION_RELATIONSHIPS:
            out.append(
                f"{label}: relationship {relation.get('relationship')!r} is not CONTINUATION; "
                "a split, merger or transferred task is never recorded as a predecessor."
            )
        effective = _day(relation.get("effective_from"))
        if effective is None:
            out.append(f"{label}: effective_from is not a date.")
        if not relation.get("evidence"):
            out.append(f"{label} names no evidence.")
        if after in successors:
            out.append(f"{label}: {after!r} already has a predecessor in this package.")
        successors.add(after)
        ended = _day((approved.get(before) or {}).get("valid_to"))
        if effective and ended and ended >= effective:
            out.append(f"{label}: the predecessor's valid_to must end before effective_from.")
        if len(f"REL:{before}>{after}") > ORGANISATION_KEY_LENGTH:
            out.append(f"{label}: the names are too long for a provenance key.")
    return out


def _organisation_problems(entries: list[dict[str, Any]]) -> list[str]:
    """An organisation the package asks the import to create is a reviewed entry."""
    from app.organisations.models import AliasType, OrganisationType

    out: list[str] = []
    seen: set[str] = set()
    codes: dict[str, str] = {}
    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            out.append("an organisation entry has no name.")
            continue
        if name in seen:
            out.append(f"organisation {name!r} appears twice.")
        seen.add(name)
        if len(f"ORG:{name}") > ORGANISATION_KEY_LENGTH:
            out.append(f"organisation {name!r}: the name is too long for its provenance key.")
        code = (entry.get("registry_code") or "").strip()
        if code:
            # The resolver reuses a row by registry code, so two entries on one
            # code would be filed as one body — a historical name merged into
            # its successor (which usually keeps the code).
            if code in codes:
                out.append(
                    f"organisation {name!r}: registry code {code} is also {codes[code]!r}'s."
                )
            codes[code] = name
        if len(entry.get("source_reference") or "") > ORGANISATION_SOURCE_REFERENCE_LENGTH:
            out.append(f"organisation {name!r}: source_reference is longer than 200 characters.")
        bounds = [_day(entry.get(b)) for b in ("valid_from", "valid_to")]
        if bounds[0] and bounds[1] and bounds[0] > bounds[1]:
            out.append(f"organisation {name!r}: valid_from is after valid_to.")
        if entry.get("organisation_type") not in OrganisationType.values:
            out.append(
                f"organisation {name!r}: type {entry.get('organisation_type')!r} is unknown."
            )
        if entry.get("status") not in ORGANISATION_STATUSES:
            out.append(f"organisation {name!r}: status must be APPROVED or PROPOSED.")
        if not entry.get("evidence"):
            out.append(f"organisation {name!r} names no evidence for its identity.")
        for bound in ("valid_from", "valid_to"):
            if entry.get(bound) and _day(entry[bound]) is None:
                out.append(f"organisation {name!r}: {bound} is not a date.")
        for alias in entry.get("aliases") or []:
            text = (alias.get("alias") or "").strip()
            if not text:
                out.append(f"organisation {name!r}: an alias is empty.")
                continue
            if text.casefold() == name.casefold():
                out.append(f"organisation {name!r}: alias {text!r} repeats the name.")
            if alias.get("alias_type") not in AliasType.values:
                out.append(
                    f"organisation {name!r}: alias type {alias.get('alias_type')!r} is unknown."
                )
            if not alias.get("evidence"):
                out.append(f"organisation {name!r}: alias {text!r} names no evidence.")
            if len(f"ALIAS:{name}:{text}") > ORGANISATION_KEY_LENGTH:
                out.append(f"organisation {name!r}: alias {text!r} is too long for its key.")
    return out


def _target_problems(
    key: str,
    target: str,
    record: dict,
    engagements: dict[str, bool],
    position_files: dict[str, bool] | None = None,
) -> list[str]:
    from app.intelligence.enums import ImportantDateKind
    from app.submissions.enums import SubmissionKind

    out: list[str] = []
    documents = record.get("documents") or []
    shared = record.get("shared_documents_from")
    if (
        target in {T_DEVELOPMENT, T_ENGAGEMENT, T_OVERVIEW, T_NEWS, T_SUBMISSION, T_IMPORTANT_DATE}
        and not (record.get("title") or "").strip()
    ):
        out.append(f"{key}: {target} needs a title.")
    if target in {T_NOTE, T_MEETING} and not (record.get("body") or "").strip():
        out.append(f"{key}: {target} needs a body.")
    if target == T_ENGAGEMENT:
        if record.get("feedback_deadline") and _day(record["feedback_deadline"]) is None:
            out.append(f"{key}: feedback_deadline is not an exact day.")
        if record.get("history") and record.get("feedback_deadline"):
            # add_engagement tracks any round given a reply-by day, so a past
            # round would open a wait nobody is in. Its date goes in the note.
            out.append(f"{key}: a past round carries no reply-by day; keep it in the note.")
        if record.get("kind") not in {"WEB_CALL", "EMAIL_CAMPAIGN", "SURVEY", "MEETING", "OTHER"}:
            out.append(f"{key}: engagement kind {record.get('kind')!r} is unknown.")
    if target in {T_POSITION_RECEIVED, T_POSITION_DISCOVERED}:
        if target == T_POSITION_DISCOVERED and not record.get("organisation"):
            out.append(f"{key}: a discovered position names an organisation.")
        if target == T_POSITION_RECEIVED and not (
            record.get("organisation") or (record.get("source_label") or "").strip()
        ):
            out.append(f"{key}: received feedback names an organisation or a source label.")
        if shared:
            # A joint letter: one position per signatory, the letter filed once
            # and supporting each of them (a document may support several
            # records, documents.links).
            if documents:
                out.append(f"{key}: a position sharing another's files lists none of its own.")
            if not (position_files or {}).get(shared):
                out.append(
                    f"{key}: shared_documents_from {shared!r} is not a READY position "
                    "of this topic with files."
                )
        if not (documents or shared or (record.get("summary") or "").strip() or record.get("url")):
            out.append(f"{key}: a position needs a document, a summary or a URL.")
        engagement = record.get("engagement_key")
        if engagement and not engagements.get(engagement):
            out.append(f"{key}: engagement {engagement!r} is not a READY round of this topic.")
        if engagement and target == T_POSITION_DISCOVERED:
            out.append(f"{key}: a discovered position is not an answer to a round.")
    if target == T_SUBMISSION:
        finals = [d for d in documents if d.get("final")]
        if len(finals) != 1 or finals[0].get("role") != "KODA_SUBMISSION_FINAL":
            out.append(f"{key}: a sent opinion needs exactly one final KODA_SUBMISSION_FINAL file.")
        if record.get("date_precision") != "DAY" or not record.get("date"):
            out.append(f"{key}: a sent opinion needs its sending day.")
        if not record.get("recipients"):
            out.append(f"{key}: a sent opinion needs at least one recipient.")
        if record.get("submission_kind") and record["submission_kind"] not in SubmissionKind.values:
            out.append(f"{key}: submission kind {record['submission_kind']!r} is unknown.")
    if target == T_IMPORTANT_DATE:
        if not record.get("date") or record.get("date_precision") not in PRECISION:
            out.append(f"{key}: an important date needs its date and precision.")
        if record.get("kind", "OTHER") not in ImportantDateKind.values:
            out.append(f"{key}: important-date kind {record.get('kind')!r} is unknown.")
        if documents:
            out.append(f"{key}: an important date carries no files; list them as unlinked.")
    if target in {T_OVERVIEW, T_NEWS}:
        if not (record.get("url") or "").startswith("https://"):
            out.append(f"{key}: a published overview needs its https address.")
        if record.get("date") and record.get("date_precision") != "DAY":
            # `published_on` is a day with no precision column: a month would be
            # stored as its first day. Undated reads «Kuupäev teadmata».
            out.append(f"{key}: a publication date is an exact day; leave a month undated.")
        if documents:
            # A file cannot be linked to an overview (documents.LINK_FIELD_BY_MODEL);
            # refused here rather than half-way through the apply.
            out.append(f"{key}: an overview carries no files; list them as unlinked documents.")
    if target == T_EFFECTIVE_DATE and record.get("kind") not in {
        "KNOWN_DATE",
        "GENERAL_ORDER",
        "UNKNOWN",
    }:
        out.append(f"{key}: effective-date kind {record.get('kind')!r} is unknown.")
    if target in {T_NOTE, T_MEETING} and (
        not record.get("date") or record.get("date_precision") != "DAY"
    ):
        # `add_entry` dates an undated entry at the moment of the call — here,
        # the import — and `occurred_at` cannot hold a month.
        out.append(
            f"{key}: a {target} needs an exact day; undated it would read as the import day."
        )
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
    organisations: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def reference(self) -> str:
        return self.topic["reference"]

    def ready_records(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.topic.get("records") or []
            if r.get("review") == READY and r.get("target") in WRITABLE_TARGETS
        ]

    def held_records(self) -> list[dict[str, Any]]:
        """Everything not written: waiting for review, or rejected for import."""
        ready = {r["key"] for r in self.ready_records()}
        return [r for r in self.topic.get("records") or [] if r.get("key") not in ready]


@dataclass
class EvidencePlan:
    root: Path
    package: dict[str, Any]
    digest: str
    archive: OneNoteArchive
    validation: ValidationReport
    topics: list[TopicPlan] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    database_state: str = "fresh"
    existing_matters: int = 0
    #: Every file of the package as read (docs/adr/0184); the archive reads
    #: through it.
    manifest: SourceManifest | None = None
    #: The catalogue rows each approved organisation name (and each name in a
    #: relationship) matched when the plan was made, as primary keys.
    catalogue: dict[str, list[str]] = field(default_factory=dict)

    def expected_writes(self) -> dict[str, int]:
        counts: dict[str, int] = {"MATTER": len(self.topics), "NEXT_ACTION": 0, "DOCUMENT": 0}
        organisations = approved_organisations(self.package)
        if organisations:
            counts["ORGANISATION"] = len(organisations)
        aliases = sum(len(entry.get("aliases") or []) for entry in organisations.values())
        if aliases:
            counts["ORGANISATION_ALIAS"] = aliases
        relationships = len(self.package.get("organisation_relationships") or [])
        if relationships:
            counts["ORGANISATION_RELATIONSHIP"] = relationships
        for topic in self.topics:
            step = topic.topic.get("next_action")
            if step and step.get("review") == READY:
                counts["NEXT_ACTION"] += 1
            if topic.topic.get("answered_by"):
                counts["DEADLINE_ANSWERED"] = counts.get("DEADLINE_ANSWERED", 0) + 1
            for record in topic.ready_records():
                counts[record["target"]] = counts.get(record["target"], 0) + 1
                counts["DOCUMENT"] += len(record.get("documents") or [])
            counts["DOCUMENT"] += sum(
                1 for d in topic.topic.get("documents_unlinked") or [] if d.get("review") == READY
            )
        return counts


@dataclass(frozen=True)
class PendingOrganisation:
    """An approved package organisation the apply will create (or find) by name."""

    name: str


def approved_organisations(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The package's APPROVED organisation entries, by name. PROPOSED ones wait."""
    return {
        entry["name"].strip(): entry
        for entry in package.get("organisations") or []
        if entry.get("status") == "APPROVED" and (entry.get("name") or "").strip()
    }


def _organisation_mappings(package: dict[str, Any]) -> dict[str, str]:
    """Only APPROVED mappings count. A PROPOSED one is the owner's to decide."""
    return {
        source: item["target"]
        for source, item in (package.get("organisation_mappings") or {}).items()
        if item.get("status") == "APPROVED" and item.get("target")
    }


def build_plan(root: str | Path) -> EvidencePlan:
    """Validate the package, then resolve it against the database. Reads only."""
    from app.core.text import normalize_organisation_name
    from app.legacy_import.resolution import (
        KnownPeople,
        MappingTables,
        resolve_organisation,
        resolve_owner,
    )
    from app.taxonomy.legal_instruments import current_legal_instrument_keys
    from app.taxonomy.models import LegalInstrumentType
    from app.workflow.models import StageVocabulary
    from app.workflow.vocabulary import CURRENT_LABEL_TO_STAGE, RAW_LABEL_TO_STAGE

    budget = WorkBudget()
    manifest = package_manifest(root, budget=budget)
    package, archive = load_package(root, manifest=manifest, budget=budget)
    digest = package_digest(package)
    try:
        validation = validate_package(package, archive)
    except (IntegrityError, SourceLimitExceeded) as error:
        raise EvidencePilotError(str(error)) from error
    plan = EvidencePlan(
        root=Path(root),
        package=package,
        digest=digest,
        archive=archive,
        validation=validation,
        manifest=manifest,
    )
    plan.problems.extend(validation.problems)

    mappings = MappingTables(
        organisations={
            normalize_organisation_name(source): target
            for source, target in _organisation_mappings(package).items()
        }
    )
    people = KnownPeople.load()
    person_names = package.get("people") or {}
    stages = {stage.key: stage for stage in StageVocabulary.objects.all()}
    instruments = {item.key: item for item in LegalInstrumentType.objects.all()}
    label_to_stage = {**RAW_LABEL_TO_STAGE, **CURRENT_LABEL_TO_STAGE}

    from app.organisations.models import Organisation
    from app.organisations.services import find_matches

    approved = approved_organisations(package)
    for name, entry in approved.items():
        matches = find_matches(name)
        plan.catalogue[name] = sorted(str(found.pk) for found in matches)
        if len(matches) > 1:
            plan.problems.append(
                f"organisation {name!r} matches several catalogue rows; "
                "resolve the duplicate first."
            )
        code = (entry.get("registry_code") or "").strip()
        holder = (
            Organisation.objects.filter(registry_code=code).first()
            if code and not matches
            else None
        )
        if holder is not None:
            # get_or_create_organisation would reuse that row by its code and
            # file this name's records under another name — a historical name
            # merged into its successor. Map the name, or correct the entry.
            plan.problems.append(
                f"organisation {name!r}: registry code {code} already belongs to "
                f"{holder.name!r}; a name is never merged on a code."
            )
        for alias in entry.get("aliases") or []:
            # A spelling another body already answers to would make the name
            # ambiguous for every later resolution; it is never added.
            others = [
                found.name
                for found in find_matches((alias.get("alias") or "").strip())
                if found.name != name
            ]
            if others:
                plan.problems.append(
                    f"organisation {name!r}: alias {alias.get('alias')!r} already resolves to "
                    f"{others[0]!r}."
                )

    for relation in package.get("organisation_relationships") or []:
        before = (relation.get("predecessor") or "").strip()
        after = (relation.get("successor") or "").strip()
        existing = {}
        for name in (before, after):
            found = find_matches(name)
            plan.catalogue.setdefault(name, sorted(str(item.pk) for item in found))
            if len(found) == 1:
                existing[name] = found[0]
            elif not found and name not in approved:
                plan.problems.append(
                    f"organisation relationship {before!r} → {after!r}: {name!r} is neither in "
                    "the catalogue nor an approved package organisation."
                )
        successor = existing.get(after)
        if successor is not None and successor.predecessor_id is not None:
            current = successor.predecessor
            if current is None or current.name != before:
                plan.problems.append(
                    f"organisation relationship {before!r} → {after!r}: {after!r} already has "
                    f"the predecessor {getattr(current, 'name', '?')!r}; it is never replaced."
                )

    def organisation(name: str, topic_plan: TopicPlan) -> Any:
        if name in topic_plan.organisations:
            return topic_plan.organisations[name]
        value = resolve_organisation(name, mappings).value
        if value is None and name in approved:
            # Not in the catalogue yet: the apply creates it from the reviewed
            # entry (get_or_create_organisation), never by similarity.
            value = PendingOrganisation(name)
        topic_plan.organisations[name] = value
        return value

    for topic in package.get("topics") or []:
        topic_plan = TopicPlan(topic=topic)
        ref = topic.get("reference", "?")
        raw_owner = person_names.get(topic.get("owner", ""), topic.get("owner", ""))
        owner = resolve_owner(raw_owner, MappingTables.empty(), people)
        if owner.value is None:
            plan.problems.append(
                f"{ref}: owner {raw_owner!r} does not resolve to exactly one account "
                f"({owner.method})."
            )
        topic_plan.owner = owner.value

        stage_key = label_to_stage.get(topic.get("stage_label", ""))
        topic_plan.stage = stages.get(stage_key or "")
        if topic_plan.stage is None:
            plan.problems.append(f"{ref}: stage for {topic.get('stage_label')!r} is not seeded.")

        keys = current_legal_instrument_keys(topic.get("legal_instrument") or "")
        for key in keys:
            if key in instruments:
                topic_plan.instruments.append(instruments[key])
            else:
                plan.problems.append(f"{ref}: Õigusakt {key!r} is not in the vocabulary.")
        if topic.get("legal_instrument") and not keys:
            topic_plan.warnings.append(
                f"Õigusakt {topic['legal_instrument']!r} has no canonical reading; left empty."
            )

        if topic.get("addressee"):
            topic_plan.addressee = organisation(topic["addressee"], topic_plan)
            if topic_plan.addressee is None:
                topic_plan.warnings.append(
                    f"Addressee {topic['addressee']!r} is not in the catalogue; not recorded "
                    "(kept in provenance)."
                )

        for record in topic_plan.ready_records():
            names = list(record.get("recipients") or [])
            if record.get("organisation"):
                names.append(record["organisation"])
            for name in names:
                if organisation(name, topic_plan) is None:
                    plan.problems.append(
                        f"{record['key']}: organisation {name!r} is not in the catalogue and no "
                        "approved mapping names it. Approve a mapping or hold the record."
                    )
        plan.topics.append(topic_plan)

    plan.database_state, plan.existing_matters = _database_state(plan)
    return plan


#: Bumped when what `plan_digest` covers changes.
PLAN_DIGEST_VERSION = "evidence-pilot-plan/1"


def plan_digest(plan: EvidencePlan) -> str:
    """What an operator approves before `apply` (docs/adr/0184).

    The package digest (the decisions), the package manifest (every file the
    decisions are read with), and what the plan resolved them to in this
    database: owners, stages, instruments, addressees and organisations as row
    identities, the catalogue rows each approved name matched, and the
    database state. The package digest alone stays the identity of the import
    in provenance, so an applied package is recognised as before.
    """
    return canonical_sha256(
        {
            "version": PLAN_DIGEST_VERSION,
            "package_sha256": plan.digest,
            "manifest_sha256": plan.manifest.digest() if plan.manifest is not None else "",
            "database_state": plan.database_state,
            "existing_matters": plan.existing_matters,
            "catalogue": plan.catalogue,
            "topics": [
                {
                    "reference": topic.reference,
                    "owner": topic.owner,
                    "stage": topic.stage,
                    "instruments": topic.instruments,
                    "addressee": topic.addressee,
                    "organisations": topic.organisations,
                }
                for topic in plan.topics
            ],
        }
    )


def _database_state(plan: EvidencePlan) -> tuple[str, int]:
    """`fresh` (empty), `applied` (exactly this package) or `foreign`, and the Matter count."""
    from app.matters.models import Matter

    from .models import EvidencePilotRecord

    existing = Matter.all_objects.count()
    mine = (
        Matter.all_objects.filter(
            evidence_pilot_records__pilot=PILOT,
            evidence_pilot_records__package_sha256=plan.digest,
            evidence_pilot_records__record_kind="MATTER",
        )
        .distinct()
        .count()
    )
    written = EvidencePilotRecord.objects.filter(pilot=PILOT, package_sha256=plan.digest).count()
    if existing == 0 and written == 0:
        return "fresh", existing
    if mine == existing == len(plan.topics) and written == sum(plan.expected_writes().values()):
        return "applied", existing
    return "foreign", existing


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@dataclass
class ApplyReport:
    already_applied: bool = False
    batch_id: Any = None
    written: dict[str, int] = field(default_factory=dict)
    held: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    resources_skipped: list[str] = field(default_factory=list)
    organisations_created: list[str] = field(default_factory=list)

    def count(self, kind: str) -> None:
        self.written[kind] = self.written.get(kind, 0) + 1


def _refuse_unless_authorised(
    plan: EvidencePlan,
    *,
    operator_intent: str,
    expect_package_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
) -> None:
    if not getattr(settings, "EVIDENCE_PILOT_ENABLED", False):
        raise EvidencePilotError(
            "The evidence pilot is off in this environment (JURISTID_EVIDENCE_PILOT). It runs "
            "only on the one-off container that applies it."
        )
    if not getattr(settings, "REAL_DATA_ALLOWED", False):
        raise EvidencePilotError(
            "REAL_DATA_ALLOWED is off. Real Chamber and member content may only enter an "
            "environment that has passed the Secure Pilot Gate (docs/secure-pilot-gate.md)."
        )
    if operator_intent != OPERATOR_INTENT:
        raise EvidencePilotError(f"Type --operator-intent {OPERATOR_INTENT} to apply the package.")
    if (expect_package_sha256 or "").strip().lower() != plan.digest:
        raise EvidencePilotError(
            f"The package digest is {plan.digest}; pass exactly that as --expect-package-sha256."
        )
    if not (expect_plan_sha256 or "").strip():
        raise EvidencePilotError(
            f"The plan digest is {plan_digest(plan)}; review the plan and pass it as "
            "--expect-plan-sha256."
        )
    if not backup_set.strip():
        raise EvidencePilotError(
            "Name the verified backup set taken before the reset (--backup-set). The package is "
            "applied only with a recovery point named."
        )
    if plan.problems:
        raise EvidencePilotError(
            "The plan has problems; nothing was written:\n  " + "\n  ".join(plan.problems)
        )
    if plan.database_state == "foreign":
        raise EvidencePilotError(
            f"The database holds {plan.existing_matters} Matter(s) this package did not create. "
            "The evidence pilot imports only into an empty database and never mixes with other "
            "business data, nor repairs a half-written one."
        )


def apply_plan(
    plan: EvidencePlan,
    *,
    operator_intent: str,
    expect_package_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
) -> ApplyReport:
    """Write the package. One transaction; refuses without every authorisation.

    The plan is built again inside the transaction, after the lock — the
    package re-hashed file by file, the database re-resolved — and must hash to
    the digest the operator approved; the writer then reads only through that
    plan's manifest. An applied package writes nothing and is reported as such
    before any of this, whatever digest is passed (docs/adr/0184).
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
        _hold_the_database(plan)
        current = build_plan(plan.root)
        try:
            approved = require_digest(
                plan_digest(current),
                expect_plan_sha256,
                flag="--expect-plan-sha256",
                what="The evidence-pilot plan",
            )
        except IntegrityError as error:
            raise EvidencePilotError(str(error)) from error
        if current.problems or current.database_state != "fresh":
            raise EvidencePilotError(
                "The plan rebuilt for the apply is not writable; nothing was written."
            )
        writer = _Writer(current, backup_set=backup_set.strip(), plan_sha256=approved)
        try:
            report, touched = writer.write()
        except (IntegrityError, SourceLimitExceeded, ArchiveError) as error:
            raise EvidencePilotError(f"{error} Nothing was written.") from error
        refresh_matters(indexable_matters().filter(pk__in=touched))
    return report


def _hold_the_database(plan: EvidencePlan) -> None:
    """Keep anyone else from writing a Matter, then re-read what the plan saw.

    The plan read the database before the transaction opened; a Teema created
    in between would otherwise be written beside. The lock blocks other writers
    to `matters_matter` until this transaction ends and lets readers through.
    """
    from django.db import connection

    from app.matters.models import Matter

    with connection.cursor() as cursor:
        cursor.execute(f'LOCK TABLE "{Matter._meta.db_table}" IN SHARE ROW EXCLUSIVE MODE')
    state, _count = _database_state(plan)
    if state != plan.database_state:
        raise EvidencePilotError(
            f"The database changed after the plan ({plan.database_state} → {state}). "
            "Nothing was written; plan again."
        )


class _Writer:
    """Everything `apply_plan` writes, in the order the workflow expects."""

    def __init__(self, plan: EvidencePlan, *, backup_set: str, plan_sha256: str = "") -> None:
        self.plan = plan
        self.backup_set = backup_set
        #: The approved plan digest, recorded on the batch and its audit event.
        self.plan_sha256 = plan_sha256
        self.report = ApplyReport()
        self.package = plan.package
        self.base = {
            "source": PROVENANCE_SOURCE,
            "pilot": PILOT,
            "pilot_version": PILOT_VERSION,
            "package_sha256": plan.digest,
        }
        #: Approved package organisations, created or found once, by name.
        self.organisations: dict[str, Any] = {}
        #: The Documents each written record carries, for a joint letter's
        #: other signatories (`shared_documents_from`).
        self.record_documents: dict[str, list[Any]] = {}

    def org(self, value: Any) -> Any:
        """The Organisation a plan value means; a pending one is the created row."""
        if isinstance(value, PendingOrganisation):
            return self.organisations[value.name]
        return value

    # -- provenance -----------------------------------------------------------

    def remember(
        self, *, matter: Any, key: str, kind: str, target: Any, page: Any, evidence: Any
    ) -> None:
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
            source_page=page,
            evidence=evidence or {},
        )
        self.report.count(kind)

    # -- the run --------------------------------------------------------------

    def write(self) -> tuple[ApplyReport, list[Any]]:
        from app.audit.enums import SecurityEventType
        from app.audit.services import record_security_event
        from app.legacy_import.models import ImportBatch, ReconciliationStatus
        from app.matters.services import reserve_matter_reference

        snapshot = self.package.get("snapshot") or {}
        self.batch = ImportBatch.objects.create(
            source_system=SOURCE_SYSTEM,
            source_file_name=PACKAGE_FILE,
            source_snapshot_sha256=self.plan.digest,
            importer_version=f"{PILOT}/{PILOT_VERSION}",
            contract_version=f"{PACKAGE_FORMAT}/{PACKAGE_FORMAT_VERSION}",
            started_at=timezone.now(),
            source_row_count=len(self.plan.topics),
            reconciliation_status=ReconciliationStatus.RUNNING,
            notes=(
                f"{PILOT}: {len(self.plan.topics)} teemat paketist {self.plan.digest}; allika "
                f"seis {snapshot.get('as_of')}; varukoopia {self.backup_set} (docs/adr/0173); "
                f"plan-sha256 {self.plan_sha256}; manifest-sha256 "
                f"{self.plan.manifest.digest() if self.plan.manifest is not None else ''} "
                "(docs/adr/0184)."
            ),
        )
        self.report.batch_id = self.batch.pk
        self.write_organisations()
        self.write_organisation_continuity()
        touched = []
        for topic_plan in self.plan.topics:
            touched.append(self.write_topic(topic_plan).pk)

        through = self.package.get("reserve_through")
        if through:
            reserve_matter_reference(int(self.package["reference_year"]), int(through))

        self.batch.finished_at = timezone.now()
        self.batch.created_matter_count = len(touched)
        self.batch.reconciliation_status = ReconciliationStatus.COMPLETED
        self.batch.save(
            update_fields=[
                "finished_at",
                "created_matter_count",
                "reconciliation_status",
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
                "manifest_sha256": self.plan.manifest.digest() if self.plan.manifest else "",
                "topics": len(touched),
                "backup_set": self.backup_set,
                "written": self.report.written,
                "held": len(self.report.held),
                "rejected": len(self.report.rejected),
            },
        )
        return self.report, touched

    def write_organisations(self) -> None:
        """Create (or find) each APPROVED package organisation, once.

        Through `get_or_create_organisation` — the resolver every counterparty
        field uses: an exact or alias match is reused, two matches refuse, and
        nothing is merged on similarity. The descriptive fields the resolver
        does not take (notes, validity, source) are set only on a row this
        apply created, never on one that already existed.
        """
        from app.organisations.services import find_matches, get_or_create_organisation

        for name, entry in sorted(approved_organisations(self.package).items()):
            result = get_or_create_organisation(
                name=name,
                organisation_type=entry["organisation_type"],
                registry_code=(entry.get("registry_code") or "").strip(),
            )
            organisation = result.organisation
            if not result.created and organisation.pk not in {o.pk for o in find_matches(name)}:
                # Reused by registry code alone — the plan refuses this; a
                # catalogue change after the plan must not slip through.
                raise EvidencePilotError(
                    f"organisation {name!r} resolved to {organisation.name!r} by its registry "
                    "code; refused."
                )
            if result.created:
                changed = []
                for column in ("notes", "source_reference"):
                    if entry.get(column):
                        setattr(organisation, column, str(entry[column]))
                        changed.append(column)
                for column in ("valid_from", "valid_to"):
                    if entry.get(column):
                        setattr(organisation, column, _day(entry[column]))
                        changed.append(column)
                if changed:
                    organisation.save(update_fields=[*changed, "updated_at"])
                self.report.organisations_created.append(name)
            self.organisations[name] = organisation
            self.remember(
                matter=None,
                key=f"ORG:{name}",
                kind="ORGANISATION",
                target=organisation,
                page=None,
                evidence={**(entry.get("evidence") or {}), "created": result.created},
            )

    def write_organisation_continuity(self) -> None:
        """Reviewed aliases and predecessor links — added, never renamed or merged.

        An alias is a spelling the body really used (a historical name, an
        abbreviation), recorded on its own identity so search and resolution find
        it. A CONTINUATION sets the successor's `predecessor` — the catalogue's
        link for the same body under a new name or remit — and only when it is
        empty: an existing, different predecessor stops the apply rather than
        being overwritten. Neither touches a historical record's addressee: a
        2021 letter to «Justiitsministeerium» stays addressed to it.
        """
        from app.organisations.models import OrganisationAlias
        from app.organisations.services import find_exact

        for name, entry in sorted(approved_organisations(self.package).items()):
            organisation = self.organisations[name]
            for alias in entry.get("aliases") or []:
                text = alias["alias"].strip()
                row = next(
                    (
                        a
                        for a in organisation.aliases.all()
                        if a.alias.casefold() == text.casefold()
                    ),
                    None,
                )
                if row is None:
                    row = OrganisationAlias.objects.create(
                        organisation=organisation, alias=text, alias_type=alias["alias_type"]
                    )
                self.remember(
                    matter=None,
                    key=f"ALIAS:{name}:{text}",
                    kind="ORGANISATION_ALIAS",
                    target=row,
                    page=None,
                    evidence={"alias_type": alias["alias_type"], "evidence": alias["evidence"]},
                )

        for relation in self.package.get("organisation_relationships") or []:
            before, after = relation["predecessor"].strip(), relation["successor"].strip()
            predecessor = self.organisations.get(before) or find_exact(before)
            successor = self.organisations.get(after) or find_exact(after)
            if predecessor is None or successor is None:
                raise EvidencePilotError(
                    f"relationship {before!r} → {after!r}: a name did not resolve."
                )
            if successor.predecessor_id is None:
                successor.predecessor = predecessor
                successor.save(update_fields=["predecessor", "updated_at"])
            elif successor.predecessor_id != predecessor.pk:
                raise EvidencePilotError(
                    f"relationship {before!r} → {after!r}: {after!r} already has another "
                    "predecessor."
                )
            self.remember(
                matter=None,
                key=f"REL:{before}>{after}",
                kind="ORGANISATION_RELATIONSHIP",
                target=successor,
                page=None,
                evidence={
                    "relationship": relation["relationship"],
                    "effective_from": relation["effective_from"],
                    "evidence": relation["evidence"],
                },
            )

    def write_topic(self, topic_plan: TopicPlan) -> Any:
        from app.matters.enums import DataQualityTier, RecordMode
        from app.matters.response_deadlines import request_response_deadline
        from app.matters.services import create_imported_matter

        topic = topic_plan.topic
        year, number = parse_reference(topic["reference"])  # type: ignore[misc]
        source = topic.get("source_page") or {}
        alternate = [t for t in topic.get("alternate_titles") or [] if t and t != topic["title"]]
        matter = create_imported_matter(
            title=topic["title"],
            reference_year=year,
            reference_number=number,
            record_mode=RecordMode.FULL,
            owner=topic_plan.owner,
            stage=topic_plan.stage,
            received_date=_day(topic.get("received_date")),
            addressee_organisation=self.org(topic_plan.addressee),
            legal_instruments=topic_plan.instruments,
            source_era=str(year),
            data_quality_tier=DataQualityTier.TIER_1_VERIFIED_ACTIVE,
            alternate_titles=alternate,
            provenance={
                **self.base,
                "reference": topic["reference"],
                "page_key": source.get("page_key", ""),
            },
        )
        deadline = _day(topic.get("response_deadline"))
        if deadline is not None:
            # The source's deadline is the *current request*. Recorded as one, so
            # an opinion the package imports for an earlier round never answers
            # it — only an answer that names it does (response_deadlines.py). A
            # plain importer deadline would read as discharged by any sent
            # opinion on the Teema (work_items._discharge_exists).
            request_response_deadline(matter=matter, deadline=deadline, actor=None)
        page_record, link, page = self.write_source_page(matter, topic)
        self.remember(
            matter=matter,
            key=topic["reference"],
            kind="MATTER",
            target=matter,
            page=page_record,
            evidence={"source_state": topic.get("source_state") or {}, "page_key": page.page_key},
        )
        self.write_next_action(matter, topic_plan, page_record)

        # Rounds first, so the feedback that answers one can name it; a joint
        # letter's sharing signatories last, so the letter is already filed;
        # otherwise the package's own (chronological) order.
        records: dict[str, Any] = {}
        ready = topic_plan.ready_records()
        ordered = (
            [r for r in ready if r["target"] == T_ENGAGEMENT]
            + [
                r
                for r in ready
                if r["target"] != T_ENGAGEMENT and not r.get("shared_documents_from")
            ]
            + [r for r in ready if r.get("shared_documents_from")]
        )
        for record in ordered:
            written = self.write_record(
                matter, topic_plan, record, records, page, page_record, link
            )
            records[record["key"]] = written
        for record in topic_plan.held_records():
            if record.get("review") == REJECTED:
                self.report.rejected.append(record["key"])
            else:
                self.report.held.append(record["key"])

        answered_by = topic.get("answered_by")
        if answered_by:
            # A Chamber reply the package imports answers the source's current
            # request (the owner's reconciliation, recorded on the topic). The
            # deadline moves to history as ANSWERED, naming the opinion; its
            # business date stays the opinion's `sent_at`.
            from app.matters.enums import ResponseDeadlineOutcome
            from app.matters.response_deadlines import resolve_response_deadline

            resolve_response_deadline(
                matter=matter,
                outcome=ResponseDeadlineOutcome.ANSWERED,
                submission=records[answered_by],
                actor=None,
            )
            self.remember(
                matter=matter,
                key=f"{topic['reference']}-ANSWERED",
                kind="DEADLINE_ANSWERED",
                target=records[answered_by],
                page=page_record,
                evidence={"answered_by": answered_by, "deadline": topic.get("response_deadline")},
            )

        for doc in topic.get("documents_unlinked") or []:
            if doc.get("review") == READY:
                self.write_document(
                    matter, page, page_record, link, doc, record=None, key_prefix=topic["reference"]
                )
            else:
                self.skip_resource(
                    page,
                    page_record,
                    link,
                    doc["resource_key"],
                    "NOT_IN_REVIEWED_IMPORT",
                    doc.get("reason", ""),
                )
        for record in topic_plan.held_records():
            rejected = record.get("review") == REJECTED
            for doc in record.get("documents") or []:
                self.skip_resource(
                    page,
                    page_record,
                    link,
                    doc["resource_key"],
                    "RECORD_REJECTED_FOR_IMPORT" if rejected else "RECORD_HELD_FOR_REVIEW",
                    f"{record['key']}: {record.get('decision_reason', '')}"
                    if rejected
                    else record["key"],
                )
        for item in topic.get("resources_not_imported") or []:
            self.skip_resource(
                page,
                page_record,
                link,
                item["resource_key"],
                item.get("code") or "NOT_IMPORTED",
                item.get("reason", ""),
            )
        return matter

    # -- the source page ------------------------------------------------------

    def write_source_page(self, matter: Any, topic: dict[str, Any]) -> tuple[Any, Any, ArchivePage]:
        from app.legacy_import.historical_apply import _catalogue_resources, _store_page_xml
        from app.legacy_import.source_pages import (
            LegacySourcePage,
            MatterSourcePage,
            SourceMatchClass,
            SourceMatchMethod,
            SourcePageRole,
            SourceRelationshipKind,
            SourceSystem,
        )

        archive = self.plan.archive
        page = archive.page(topic["source_page"]["page_key"])
        # Through the archive's no-follow reader, never a bare path: the key is
        # the archive's own `archivePageKey` (docs/adr/0178).
        identity = archive.page_record(page.page_key)
        now = timezone.now()
        values = {
            "page_key": page.page_key,
            "source_notebook": page.notebook,
            "source_section": page.section,
            "source_section_group": page.section_group or "",
            "title": page.title,
            "page_level": page.level,
            "page_order": page.page_order,
            "page_role": SourcePageRole.MATTER_LIKE,
            "role_reason": f"{PILOT}: the reviewed dossier of {topic['reference']}.",
            "source_created_at": page.created_at,
            "source_modified_at": page.modified_at,
            "capture_id": page.capture_id,
            "source_xml_sha256": page.xml_sha256,
            "derived_text": page.derived_text,
            "blocks": [
                {
                    "ordinal": block.ordinal,
                    "kind": block.kind,
                    "text": block.text,
                    "resource_key": block.resource_key,
                    "depth": block.depth,
                }
                for block in page.blocks
            ],
            "links": list(page.links),
            "reading_order_strategy": page.reading_order_strategy,
            "reading_order_ambiguous": page.reading_order_ambiguous,
            "onenote_hyperlink": (identity.get("source") or {}).get("hyperlink", ""),
            "text_characters": len(page.derived_text),
            "block_count": len(page.blocks),
            "file_count": page.file_count,
            "file_bytes": page.file_bytes,
            "latest_imported_at": now,
            "import_batch": self.batch,
        }
        record = LegacySourcePage.objects.filter(
            source_system=SourceSystem.ONENOTE_DESKTOP, source_page_id=page.page_id
        ).first()
        if record is None:
            record = LegacySourcePage.objects.create(
                source_system=SourceSystem.ONENOTE_DESKTOP,
                source_page_id=page.page_id,
                first_imported_at=now,
                **values,
            )
        _store_page_xml(record, page, archive)
        _catalogue_resources(record, page)
        link = MatterSourcePage.objects.create(
            matter=matter,
            source_page=record,
            relationship_kind=SourceRelationshipKind.PRIMARY,
            match_method=SourceMatchMethod.REVIEWED_MATCH,
            match_class=SourceMatchClass.EXACT,
            source_audit_reference=f"{PILOT}:{self.plan.digest[:16]}:{topic['reference']}",
        )
        return record, link, page

    # -- the current step -----------------------------------------------------

    def write_next_action(self, matter: Any, topic_plan: TopicPlan, page_record: Any) -> None:
        from app.workflow.services import set_next_action

        step = topic_plan.topic.get("next_action")
        if not step:
            return
        key = f"{topic_plan.reference}-NEXT"
        if step.get("review") != READY:
            (self.report.rejected if step.get("review") == REJECTED else self.report.held).append(
                key
            )
            return
        shape, problems = step_shape(step)
        if problems:
            raise EvidencePilotError(f"{key}: {'; '.join(problems)}")
        action = set_next_action(
            matter=matter,
            text=step["text"].strip(),
            kind=shape.kind,
            date_semantics=shape.date_semantics,
            target_date=shape.target_date,
            date_precision=shape.date_precision,
            source_text=step.get("source_text") or step["text"],
            responsible=topic_plan.owner,
            actor=None,
            provenance={
                **self.base,
                "reference": topic_plan.reference,
                "treatment": "SOURCE_NEXT_STEP",
                # The kind is the reviewer's, stated in the package — never read
                # off the step's wording.
                "kind_basis": "REVIEWED_PACKAGE" if step.get("kind") else "DEFAULT_DO",
            },
        )
        self.remember(
            matter=matter,
            key=key,
            kind="NEXT_ACTION",
            target=action,
            page=page_record,
            evidence=step.get("evidence") or {},
        )

    # -- one record -----------------------------------------------------------

    def write_record(
        self,
        matter: Any,
        topic_plan: TopicPlan,
        record: dict[str, Any],
        written: dict[str, Any],
        page: ArchivePage,
        page_record: Any,
        link: Any,
    ) -> Any:
        from app.core.dates import start_of_local_day
        from app.intelligence.services import add_effective_date, add_important_date
        from app.matters.entry_enums import EntryKind
        from app.matters.services import (
            add_engagement,
            add_entry,
            plan_website_overview,
            publish_website_overview,
            record_external_position,
            record_external_position_document,
            record_procedural_development,
            record_procedural_development_document,
        )
        from app.workflow.dates import period_bounds

        target = record["target"]
        precision = record.get("date_precision") or "DAY"
        day = _anchor(record.get("date"), precision) if record.get("date") else None
        domain_precision = PRECISION.get(precision, "EXACT")
        documents = record.get("documents") or []
        organisation = (
            self.org(topic_plan.organisations.get(record["organisation"]))
            if record.get("organisation")
            else None
        )
        shared = self.record_documents.get(record.get("shared_documents_from") or "", [])

        if target == T_SUBMISSION:
            created = self.write_submission(
                matter, topic_plan, record, page, page_record, link, day
            )
        elif target == T_DEVELOPMENT:
            created = record_procedural_development(
                matter=matter,
                title=record["title"].strip(),
                occurred_on=day,
                occurred_on_precision=domain_precision,
                note=(record.get("note") or "").strip(),
                actor=None,
            )
        elif target == T_ENGAGEMENT:
            created = add_engagement(
                matter=matter,
                kind=record["kind"],
                title=record["title"].strip(),
                url=record.get("url") or "",
                note=(record.get("note") or "").strip(),
                occurred_on=day,
                occurred_on_precision=domain_precision,
                feedback_deadline=_day(record.get("feedback_deadline")),
                feedback_received=(record.get("feedback_received") or "").strip(),
                lifecycle_tracked=not record.get("history", False),
                actor=None,
            )
        elif target in {T_POSITION_RECEIVED, T_POSITION_DISCOVERED}:
            created = record_external_position(
                matter=matter,
                organisation=organisation,
                provenance="RECEIVED" if target == T_POSITION_RECEIVED else "DISCOVERED",
                source_label=(record.get("source_label") or "")
                if target == T_POSITION_RECEIVED
                else "",
                url=record.get("url") or "",
                stated_on=day,
                stated_on_precision=domain_precision,
                summary=(record.get("summary") or "").strip(),
                source_is_member=bool(record.get("source_is_member"))
                and target == T_POSITION_RECEIVED,
                engagement=written.get(record.get("engagement_key") or ""),
                attachment_count=len(documents) + len(shared),
                actor=None,
            )
        elif target in {T_OVERVIEW, T_NEWS}:
            kind = "OVERVIEW" if target == T_OVERVIEW else "NEWS"
            planned = plan_website_overview(matter=matter, actor=None, kind=kind)
            created = publish_website_overview(
                overview=planned,
                url=record["url"],
                published_on=day,
                actor=None,
                title=record["title"].strip(),
                kind=kind,
            )
        elif target in {T_NOTE, T_MEETING}:
            created = add_entry(
                matter=matter,
                body=record["body"].strip(),
                author=None,
                kind=EntryKind.NOTE if target == T_NOTE else EntryKind.MEETING,
                occurred_at=start_of_local_day(day) if day else None,
            )
        elif target == T_EFFECTIVE_DATE:
            first, last = period_bounds(day, domain_precision) if day else (None, None)
            created = add_effective_date(
                matter=matter,
                kind=record["kind"],
                date_value=first,
                period_end=last,
                date_precision=domain_precision,
                description=(record.get("title") or "").strip(),
                note=(record.get("note") or "").strip(),
                source_url=record.get("url") or "",
                actor=None,
                source_text=record.get("source_text") or "",
            )
        elif target == T_IMPORTANT_DATE:
            first, last = period_bounds(day, domain_precision)  # type: ignore[arg-type]
            created = add_important_date(
                matter=matter,
                title=record["title"].strip(),
                date_value=first,
                period_end=last,
                date_precision=domain_precision,
                kind=record.get("kind") or "OTHER",
                note=(record.get("note") or "").strip(),
                actor=None,
                source_text=record.get("source_text") or "",
            )
        else:  # validate_package refuses anything else
            raise EvidencePilotError(f"{record['key']}: target {target!r} cannot be written.")

        if target != T_SUBMISSION:
            filed = []
            for doc in documents:
                document = self.write_document(
                    matter, page, page_record, link, doc, record=created, key_prefix=record["key"]
                )
                filed.append(document)
                if target in {T_POSITION_RECEIVED, T_POSITION_DISCOVERED}:
                    record_external_position_document(
                        position=created, document=document, actor=None
                    )
                elif target == T_DEVELOPMENT:
                    record_procedural_development_document(
                        development=created, document=document, actor=None
                    )
            self.record_documents[record["key"]] = filed
            if shared:
                from app.documents.services import link_document_to_record

                for document in shared:
                    # The same letter, filed once, supports each co-signatory's
                    # position (a document may support several records).
                    link_document_to_record(document=document, record=created, actor=None)
                    record_external_position_document(
                        position=created, document=document, actor=None
                    )
        self.remember(
            matter=matter,
            key=record["key"],
            kind=target,
            target=created,
            page=page_record,
            evidence={
                **(record.get("evidence") or {}),
                "date_basis": record.get("date_basis", ""),
                "confidence": record.get("confidence", ""),
            },
        )
        return created

    def write_submission(
        self,
        matter: Any,
        topic_plan: TopicPlan,
        record: dict[str, Any],
        page: ArchivePage,
        page_record: Any,
        link: Any,
        day: dt.date | None,
    ) -> Any:
        from app.submissions.enums import SentAtPrecision, SubmissionKind
        from app.submissions.services import register_sent_opinion

        documents = record.get("documents") or []
        final = next(d for d in documents if d.get("final"))
        final_document = self.write_document(
            matter, page, page_record, link, final, record=None, key_prefix=record["key"]
        )
        further = []
        for doc in documents:
            if doc is final:
                continue
            further.append(
                self.write_document(
                    matter, page, page_record, link, doc, record=None, key_prefix=record["key"]
                ).current_version
            )
        sent_at = timezone.make_aware(dt.datetime.combine(day, dt.time.min))  # type: ignore[arg-type]
        return register_sent_opinion(
            document=final_document,
            version=final_document.current_version,
            title=record["title"].strip(),
            kind=record.get("submission_kind") or SubmissionKind.FORMAL_OPINION,
            actor=None,
            recipients=[self.org(topic_plan.organisations[name]) for name in record["recipients"]],
            reference=(record.get("reference") or "").strip(),
            summary=(record.get("summary") or "").strip(),
            sent_at=sent_at,
            sent_at_precision=SentAtPrecision.DATE,
            further_versions=tuple(further),
        )

    # -- files ----------------------------------------------------------------

    def _resource(self, page: ArchivePage, key: str) -> Any:
        for resource in page.resources:
            if resource.resource_key == key:
                return resource
        raise EvidencePilotError(f"{page.page_key}: no resource {key}.")

    def write_document(
        self,
        matter: Any,
        page: ArchivePage,
        page_record: Any,
        link: Any,
        doc: dict[str, Any],
        *,
        record: Any,
        key_prefix: str,
    ) -> Any:
        from app.documents.enums import ExtractionState
        from app.documents.models import DocumentVersion
        from app.documents.services import (
            add_evidence_version,
            create_document,
            evidence_visibility_override,
            link_document_to_record,
        )
        from app.legacy_import.historical_materials import (
            extraction_note_for,
            is_extractable,
            mime_type_for,
        )
        from app.legacy_import.source_pages import (
            LegacySourceResource,
            LegacySourceResourceImport,
            ResourceImportState,
        )

        resource = self._resource(page, doc["resource_key"])
        content = self.plan.archive.read_resource(page.page_key, resource)
        filename = resource.original_filename
        document = create_document(
            matter=matter,
            title=(doc.get("title") or filename)[:400],
            role=doc["role"],
            created_by=None,
            visibility_override=evidence_visibility_override(record) if record is not None else "",
            provenance_note=(
                f"OneNote: {page.section} → {page.title[:120]} "
                f"(plokk {resource.source_block_ordinal}); "
                f"tõendipiloodi pakett {self.plan.digest[:12]}"
            ),
        )
        version = add_evidence_version(
            document=document,
            content=content,
            original_filename=filename,
            mime_type=mime_type_for(filename),
            uploaded_by=None,
            acquired_at=page.modified_at,
            source_identifier=f"{page.page_key}/{resource.resource_key}",
        )
        if version.sha256 != resource.sha256 or version.sha256 != doc.get("sha256"):
            raise EvidencePilotError(
                f"{resource.resource_key}: stored SHA-256 {version.sha256} does not match the "
                f"archive's {resource.sha256} or the reviewed package's {doc.get('sha256')}."
            )
        if not is_extractable(Path(filename).suffix.lower()):
            DocumentVersion.objects.filter(pk=version.pk).update(
                extraction_state=ExtractionState.NOT_APPLICABLE,
                extraction_note=extraction_note_for(filename)[:300],
            )
        if record is not None:
            link_document_to_record(document=document, record=record, actor=None)
        catalogued = LegacySourceResource.objects.get(
            source_page=page_record, resource_key=resource.resource_key
        )
        LegacySourceResourceImport.objects.update_or_create(
            matter_source_page=link,
            resource=catalogued,
            defaults={
                "document": document,
                "document_version": version,
                "state": ResourceImportState.IMPORTED,
                "error_code": "",
                "error_detail": "",
                "import_batch": self.batch,
            },
        )
        self.remember(
            matter=matter,
            key=f"{key_prefix}/{resource.resource_key}",
            kind="DOCUMENT",
            target=document,
            page=page_record,
            evidence={
                "resource_key": resource.resource_key,
                "sha256": resource.sha256,
                "role": doc["role"],
            },
        )
        document.refresh_from_db()
        return document

    def skip_resource(
        self, page: ArchivePage, page_record: Any, link: Any, key: str, code: str, detail: str
    ) -> None:
        from app.legacy_import.source_pages import (
            LegacySourceResource,
            LegacySourceResourceImport,
            ResourceImportState,
        )

        catalogued = LegacySourceResource.objects.get(source_page=page_record, resource_key=key)
        LegacySourceResourceImport.objects.update_or_create(
            matter_source_page=link,
            resource=catalogued,
            defaults={
                "state": ResourceImportState.SKIPPED,
                "error_code": code[:64],
                "error_detail": (detail or "")[:2000],
                "import_batch": self.batch,
            },
        )
        self.report.resources_skipped.append(f"{page.page_key}/{key}: {code}")


# ---------------------------------------------------------------------------
# Verifying it
# ---------------------------------------------------------------------------


@dataclass
class VerifyReport:
    problems: list[str] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_applied(plan: EvidencePlan) -> VerifyReport:
    """Read back what `apply` wrote and compare it with the package. Reads only."""
    from django.apps import apps

    from app.audit.models import ChangeEvent
    from app.documents.models import Document
    from app.legacy_import.source_pages import LegacySourceResourceImport
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.models import Matter, MatterResponseDeadline
    from app.organisations.models import Organisation
    from app.submissions.models import Submission

    from .models import EvidencePilotRecord

    report = VerifyReport()
    rows = list(EvidencePilotRecord.objects.filter(pilot=PILOT, package_sha256=plan.digest))
    expected = plan.expected_writes()
    actual: dict[str, int] = {}
    for row in rows:
        actual[row.record_kind] = actual.get(row.record_kind, 0) + 1
        model = apps.get_model(row.target_model) if row.target_model else None
        if model is None or not model._default_manager.filter(pk=row.target_id).exists():
            report.problems.append(
                f"{row.record_key}: {row.target_model} {row.target_id} is missing."
            )
    if actual != expected:
        report.problems.append(f"written {actual} != expected {expected}.")
    report.checked["records"] = len(rows)

    by_ref = {t.reference: t for t in plan.topics}
    matters = Matter.all_objects.filter(
        evidence_pilot_records__package_sha256=plan.digest,
        evidence_pilot_records__record_kind="MATTER",
    )
    for matter in matters:
        topic_plan = by_ref.get(matter.display_reference)
        if topic_plan is None:
            report.problems.append(f"{matter.display_reference}: not in the package.")
            continue
        topic = topic_plan.topic
        if matter.owner_id != getattr(topic_plan.owner, "pk", None):
            report.problems.append(f"{topic['reference']}: owner differs.")
        if matter.title != topic["title"]:
            report.problems.append(f"{topic['reference']}: title differs from the source value.")
        deadline = _day(topic.get("response_deadline"))
        if topic.get("answered_by") and deadline is not None:
            # Answered by an imported reply: the deadline is history now,
            # ANSWERED and naming that opinion.
            answered = MatterResponseDeadline.objects.filter(
                matter=matter, deadline=deadline, outcome=ResponseDeadlineOutcome.ANSWERED
            ).exclude(submission=None)
            if matter.response_deadline is not None or not answered.exists():
                report.problems.append(
                    f"{topic['reference']}: the request is not answered by the imported reply."
                )
        elif matter.response_deadline != deadline:
            report.problems.append(f"{topic['reference']}: Arvamuse tähtaeg differs.")
        addressee = topic_plan.organisations.get(topic.get("addressee") or "")
        expected_addressee = getattr(addressee, "pk", None)
        if isinstance(addressee, PendingOrganisation):
            created = Organisation.objects.filter(name=addressee.name).first()
            expected_addressee = getattr(created, "pk", None)
        if topic.get("addressee") and matter.addressee_organisation_id != expected_addressee:
            report.problems.append(f"{topic['reference']}: addressee differs.")
        if matter.received_date != _day(topic.get("received_date")):
            report.problems.append(f"{topic['reference']}: received date differs.")
        if getattr(matter.stage, "pk", None) != getattr(topic_plan.stage, "pk", None):
            report.problems.append(f"{topic['reference']}: stage differs.")
    report.checked["matters"] = matters.count()

    archive_sha = {}
    for topic_plan in plan.topics:
        page = plan.archive.page(topic_plan.topic["source_page"]["page_key"])
        for resource in page.resources:
            archive_sha[f"{page.page_key}/{resource.resource_key}"] = resource.sha256
    for row in rows:
        if row.record_kind != "DOCUMENT" or row.target_id is None:
            continue
        document = (
            Document.objects.filter(pk=row.target_id).select_related("current_version").first()
        )
        version = document.current_version if document else None
        source = version.source_identifier if version else ""
        if version is None or archive_sha.get(source) != version.sha256:
            report.problems.append(f"{row.record_key}: stored bytes do not match the archive.")
    report.checked["documents"] = sum(1 for r in rows if r.record_kind == "DOCUMENT")

    matter_ids = [m.pk for m in matters]
    for submission in Submission.objects.filter(matter_id__in=matter_ids):
        final = submission.final_version
        if final is None or final.document.role != "KODA_SUBMISSION_FINAL":
            report.problems.append(
                f"{submission.pk}: a sent opinion without its real final letter."
            )
    acted = ChangeEvent.objects.filter(matter_id__in=matter_ids, actor__isnull=False).count()
    if acted:
        report.problems.append(f"{acted} change event(s) on pilot Matters carry an actor.")
    accounted = LegacySourceResourceImport.objects.filter(
        matter_source_page__matter_id__in=matter_ids
    ).count()
    if accounted != len(archive_sha):
        report.problems.append(f"{accounted} of {len(archive_sha)} page resources accounted for.")
    report.checked["resources_accounted"] = accounted
    for name, entry in approved_organisations(plan.package).items():
        listed = Organisation.objects.filter(name=name).first()
        if listed is None:
            report.problems.append(f"organisation {name!r} is missing.")
            continue
        spelled = {alias.alias.casefold() for alias in listed.aliases.all()}
        for alias in entry.get("aliases") or []:
            if alias["alias"].strip().casefold() not in spelled:
                report.problems.append(
                    f"organisation {name!r}: alias {alias['alias']!r} is missing."
                )
    for relation in plan.package.get("organisation_relationships") or []:
        before, after = relation["predecessor"].strip(), relation["successor"].strip()
        successor = Organisation.objects.filter(name=after).select_related("predecessor").first()
        if (
            successor is None
            or successor.predecessor is None
            or successor.predecessor.name != before
        ):
            report.problems.append(
                f"organisation {after!r} does not record {before!r} as its predecessor."
            )
    report.checked["organisations"] = len(approved_organisations(plan.package))
    return report
