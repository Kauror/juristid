"""The controlled historical backfill (brief §5 A4): proven-absent, non-operational
Lovable topics become closed, historical Teemad through the existing importer.

**What is selected.** Exactly the ledger rows (`lovable_inventory.build_ledger`)
that are ``ABSENT``, not operational, carry a valid reference and no tombstone
note. Everything else is left out and listed with its reason:

* an operational absent topic → the nightly new-topic synchronisation (agent B),
  never the backfill (``OPERATIONAL``);
* a UUID or reference that lives on a tombstone (``TOMBSTONE_HELD``): the
  importer counts a deleted Teema's reference as taken (docs/adr/0191 §5) and a
  re-creation is an owner decision;
* a topic the machine reader could not read into a topic, or whose topic the
  importer's own validation refuses (``READER_REFUSED``, ``NOT_HISTORICAL``,
  ``VALIDATION``) — counted and visible, never forced;
* every other category (``EXACT``, ``LINK_ENRICHMENT``, ``ARCHIVE_UNLINKED``,
  ``POSSIBLE_DUPLICATE``, ``DELETED_OR_SUPERSEDED``, ``EXCLUDED``).

**How each topic is read.** The shared machine reader
`app.legacy_import.lovable_reading.read_topic(bundle, as_of=, people=,
historical=True)` (COMMON interface 1) — a closed ARCHIVE topic as ADR 0202 §1
creates finished topics, no open step, nothing it cannot read safely (held for
review). This module adds a second, independent check (`history_problems`): no
ACTIVE lifecycle, no current step, no open request, no written commencement, no
operational topic — and **a closure reason only where the source states one**
(`lovable_reading.closure_reading` on the topic's own source copy): «jõustunud»
→ COMPLETED, «rohkem ei tegele» / «rohkem pole tegevusi plaanis» →
MONITORING_STOPPED, a structured stage link → SUPERSEDED, otherwise a **blank**
disposition. Being outside Lovable's operational scope and «ei saatnud» are
never a reason (docs/adr/0213 §2 as amended 2026-11-09).

**Stale readings are refused.** A package names the reader version that read it
(``mapping_version``); `validate` and `plan` refuse one read by another version
than this candidate's (`STALE_READER`), so a package built before a reading rule
changed can never be applied — build it again from the reading.

**For a person.** The report (``backfill-report.json``) lists, besides every row
left out, the rows only a person can settle (``review``: ARCHIVE_UNLINKED,
POSSIBLE_DUPLICATE, LINK_ENRICHMENT without a decision, with their candidates and
archive pages — never matched here) and the package's ``distribution`` (closure
basis, disposition, lifecycle, source status, deadline treatment, held columns).

**How it is written.** Only through `lovable_pilot`: a format-2 CREATE package
with cohort ``lovable-history`` — `validate` → `plan` (plan digest) → `dry-run`
(writes, verifies, rolls back) → `apply` (``JURISTID_LOVABLE_PILOT``,
``REAL_DATA_ALLOWED``, operator intent ``lovable-history``, package and plan
digests, a named verified backup set, the plan rebuilt under the lock, every
pre-existing Teema digested before and after, verify before commit) → `verify`.
It creates Teemad only: it never retires, releases, rebases or changes an
existing Teema, and a second apply writes nothing.

Enrolment afterwards is `lovable_sync enroll --cohort lovable-history` (agent B
adds the key), after `lovable_pilot preserved --before`.
"""

from __future__ import annotations

import datetime as dt
import importlib
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.legacy_import import lovable_pilot
from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.lovable_inventory import (
    READING_MODULE,
    TOMBSTONE_REFERENCE,
    TOMBSTONE_UUID,
    Category,
    Ledger,
    LedgerRow,
    decision_index,
)
from app.legacy_import.lovable_pilot import (
    ACTIVE_FORMAT_VERSION,
    CREATE,
    HISTORY_COHORT,
    PACKAGE_FORMAT,
    READY,
    LovablePilotError,
    LovablePlan,
    parse_reference,
    topic_action,
    validate_package,
)

TALLINN = ZoneInfo("Europe/Tallinn")
OPERATIONAL = "OPERATIONAL"
TOMBSTONE_HELD = "TOMBSTONE_HELD"
NOT_ABSENT = "NOT_ABSENT"
NO_BUNDLE = "NO_BUNDLE"
READER_REFUSED = "READER_REFUSED"
NOT_HISTORICAL = "NOT_HISTORICAL"
VALIDATION = "VALIDATION"
INVALID_REFERENCE = "INVALID_REFERENCE"
#: Decisions that keep a topic out of the backfill whatever its category.
DECIDED_OUT = frozenset({"LINK", "EXCLUDE"})
DECISIONS_CHANGED = "the inventory decisions changed since the package was built"
STALE_READER = "the package was read by another reader version than this candidate's"
#: Ledger categories that only a person settles (docs/adr/0213 §1): listed in the
#: report's ``review`` with their candidates, never matched or created here.
REVIEW_CATEGORIES = (
    Category.ARCHIVE_UNLINKED,
    Category.POSSIBLE_DUPLICATE,
    Category.LINK_ENRICHMENT,
)

Reader = Callable[..., dict[str, Any]]


class BackfillError(LovablePilotError):
    """The backfill refused. Nothing was written."""


@dataclass
class Selection:
    package: dict[str, Any] | None
    selected: list[str] = field(default_factory=list)
    left_out: list[dict[str, Any]] = field(default_factory=list)
    reader_version: str = ""
    review: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, Any]:
        return {
            "selected": len(self.selected),
            "left_out": len(self.left_out),
            "left_out_by_reason": dict(sorted(Counter(i["reason"] for i in self.left_out).items())),
        }

    def report(self) -> dict[str, Any]:
        return {
            "cohort": HISTORY_COHORT,
            "reader_version": self.reader_version,
            "package_sha256": (
                lovable_pilot.package_digest(self.package) if self.package else None
            ),
            **self.counts(),
            "selected_references": self.selected,
            "left_out": self.left_out,
            "review": self.review,
            "review_by_category": dict(sorted(Counter(i["category"] for i in self.review).items())),
            "distribution": distribution(self.package),
        }


def _default_reader() -> tuple[Reader, str]:
    """Agent B's machine reader (COMMON interface 1)."""
    lovable_reading = importlib.import_module(READING_MODULE)
    return lovable_reading.read_topic, str(lovable_reading.READER_VERSION)


def _left(row: LedgerRow, reason: str, detail: str = "") -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "reference": row.reference,
        "category": row.category,
        "reason": reason,
        "detail": detail[:300],
    }


def _review(row: LedgerRow) -> dict[str, Any]:
    """One row a person settles: codes, references and ids only."""
    return {
        "source_id": row.source_id,
        "reference": row.reference,
        "category": row.category,
        "reason": row.reason,
        "operational": row.operational,
        "notes": list(row.notes),
        "archive_pages": list(row.archive_pages),
        "candidates": [
            {k: c.get(k) for k in ("basis", "reference", "source_id", "matter_id")}
            for c in row.candidates
        ],
    }


def distribution(package: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    """What the package would create, counted: the owner's before/after view."""
    topics = (package or {}).get("topics") or []

    def count(values: Any) -> dict[str, int]:
        return dict(sorted(Counter(str(v) for v in values).items()))

    return {
        "closure_basis": count(t.get("closure_basis") or "" for t in topics),
        "disposition": count(t.get("disposition") or "" for t in topics),
        "lifecycle": count(t.get("lifecycle") or "" for t in topics),
        "status_raw": count(t.get("status_raw") or "" for t in topics),
        "status_without_stage": count(
            t.get("status_raw") or "" for t in topics if t.get("status_raw") and not t.get("stage")
        ),
        "deadline_treatment": count(
            (t.get("deadline") or {}).get("treatment") or "NONE" for t in topics
        ),
        "held_columns": count(
            name
            for t in topics
            for name, decision in (t.get("field_decisions") or {}).items()
            if decision == "HELD_FOR_REVIEW"
        ),
    }


def eligible(row: LedgerRow) -> str:
    """``""`` when the row may be backfilled, else the reason it is left out."""
    if row.decision and row.decision.get("kind") in DECIDED_OUT:
        # A person linked or excluded it (`LovableInventoryDecision`).
        return f"DECIDED_{row.decision['kind']}"
    if row.category != Category.ABSENT:
        return NOT_ABSENT
    if row.operational:
        return OPERATIONAL
    if {TOMBSTONE_UUID, TOMBSTONE_REFERENCE} & set(row.notes):
        return TOMBSTONE_HELD
    if parse_reference(row.reference) is None:
        return INVALID_REFERENCE
    return ""


def history_problems(topic: dict[str, Any]) -> list[str]:
    """The backfill's own historical rules, on top of the importer's validation:
    a closed topic, nothing current, nothing operational."""
    out: list[str] = []
    if topic_action(topic) != CREATE or topic.get("action") != CREATE:
        out.append("a historical topic is an explicit CREATE.")
    if topic.get("lifecycle") == "ACTIVE":
        out.append("a historical topic is created closed (not ACTIVE).")
    if topic.get("next_action"):
        out.append("a historical topic carries no current step.")
    if (topic.get("deadline") or {}).get("treatment") == "OPEN_REQUEST":
        out.append("a historical topic carries no open opinion request.")
    if any(r.get("review") == READY for r in topic.get("commencements") or []):
        out.append("a historical topic's commencements stay source only.")
    source = topic.get("source") or {}
    row = source.get("row") or {}
    if isinstance(row, dict) and row.get("operatiivne_ulatus"):
        out.append("an operational topic is the nightly synchronisation's, not the backfill's.")
    if isinstance(row, dict) and topic.get("lifecycle") != "ACTIVE":
        # The closure is read again from the topic's own source copy: only a
        # stated reason, never one inferred from the scope or «ei saatnud».
        closure_reading = importlib.import_module(READING_MODULE).closure_reading
        lifecycle, disposition, _basis = closure_reading(
            {"row": row, "stage_links": source.get("stage_links") or []}
        )
        stated = (topic.get("lifecycle"), topic.get("disposition") or "")
        if stated != (lifecycle, disposition):
            out.append(
                f"the closure {stated[0]}/{stated[1] or '(blank)'} is not what the source "
                f"states ({lifecycle}/{disposition or '(blank)'}); a historical topic's reason "
                "is read only from Lovable's own status or stage link."
            )
    return out


def reader_problems(package: dict[str, Any]) -> list[str]:
    """A package read by another reader version than this candidate's is stale."""
    _reader, current = _default_reader()
    named = str(package.get("mapping_version") or "")
    out: list[str] = []
    if named != current:
        out.append(
            f"{STALE_READER} ({named or 'none'} ≠ {current}); build it again from the "
            "reading (lovable_inventory backfill --step package)."
        )
    for topic in package.get("topics") or []:
        stamped = (topic.get("reading") or {}).get("reader_version")
        if stamped is not None and stamped != named:
            out.append(f"{topic.get('reference')}: read by {stamped}, not {named}.")
    return out


def package_problems(package: dict[str, Any]) -> list[str]:
    """A backfill package is exactly a CREATE-only, historical ``lovable-history``
    package; anything else is refused before the importer is asked."""
    out: list[str] = []
    if package.get("format") != PACKAGE_FORMAT or package.get("format_version") != (
        ACTIVE_FORMAT_VERSION
    ):
        out.append("a backfill package is format 2 of the Lovable package.")
    if package.get("cohort") != HISTORY_COHORT:
        out.append(f"a backfill package names the {HISTORY_COHORT} cohort.")
    if package.get("replace") is not None:
        out.append("a backfill never replaces or retires anything.")
    if package.get("reserve_through"):
        out.append("a backfill moves no reference sequence beyond what its Teemad take.")
    for topic in package.get("topics") or []:
        out.extend(f"{topic.get('reference')}: {p}" for p in history_problems(topic))
    return out


def _header(document: dict[str, Any], ledger: Ledger, people: Mapping[str, str], version: str):
    return {
        "format": PACKAGE_FORMAT,
        "format_version": ACTIVE_FORMAT_VERSION,
        "cohort": HISTORY_COHORT,
        "reviewer": f"machine reader {version}"[:200],
        "mapping_version": version,
        "snapshot": {
            "as_of": document.get("taken_at"),
            "snapshot_sha256": document.get("sha256"),
            "project": document.get("project", ""),
            "highest_reference": {},
        },
        "selection": {
            "ledger_sha256": ledger.sha256(),
            "inventory_version": ledger.version,
            "criteria": "ABSENT, non-operational, valid reference, no tombstone note",
            # Every person's decision the ledger applied: a later decision makes
            # a different package (and `plan` refuses this one).
            "decisions": ledger.decisions,
            "decisions_sha256": ledger.decisions_sha256,
        },
        "people": dict(people),
    }


def build_package(
    document: dict[str, Any],
    ledger: Ledger,
    *,
    people: Mapping[str, str] | None = None,
    reader: Reader | None = None,
    reader_version: str = "",
) -> Selection:
    """The CREATE package for the eligible rows, and every row left out with its
    reason. Reads the reading and the ledger only; writes nothing."""
    if ledger.source_sha256 != document.get("sha256"):
        raise BackfillError("The ledger was not built from this reading (digests differ).")
    if reader is None:
        reader, reader_version = _default_reader()
    people = dict(people or {})
    bundles = document.get("bundles") or {}
    taken = dt.datetime.fromisoformat(str(document["taken_at"]))
    as_of = taken.astimezone(TALLINN).date() if taken.tzinfo else taken.date()
    header = _header(document, ledger, people, reader_version)
    selection = Selection(package=None, reader_version=reader_version)

    # Lovable id → reference for every live topic, so a stage link can name its
    # successor; the importer links it only inside this package (`build_plan`).
    references = {
        str(entry.get("id")): str(entry.get("nr") or "")
        for entry in document.get("index") or []
        if entry.get("id") and entry.get("nr")
    }
    topics: list[dict[str, Any]] = []
    for row in sorted(ledger.rows, key=lambda r: r.reference):
        if not row.in_source:
            continue
        if row.category in REVIEW_CATEGORIES and not row.decision:
            selection.review.append(_review(row))
        reason = eligible(row)
        if reason:
            if reason != NOT_ABSENT:
                selection.left_out.append(_left(row, reason))
            continue
        bundle = bundles.get(row.source_id)
        if bundle is None:
            selection.left_out.append(_left(row, NO_BUNDLE, "the reading holds no bundle"))
            continue
        partners = {
            str(link.get("next")): references[str(link.get("next"))]
            for link in bundle.get("stage_links") or []
            if str(link.get("next")) in references
        }
        try:
            topic = reader(
                {**bundle, "references": partners}, as_of=as_of, people=people, historical=True
            )
        except Exception as error:  # the reader refusing one topic never stops the rest
            selection.left_out.append(_left(row, READER_REFUSED, type(error).__name__))
            continue
        if not isinstance(topic, dict):
            selection.left_out.append(_left(row, READER_REFUSED, "not a topic"))
            continue
        if topic.get("successor") and topic.get("successor") == topic.get("reference"):
            _unlink(topic, "the stage link names the topic itself")
        problems = history_problems(topic)
        if str((topic.get("source") or {}).get("id") or "").lower() != row.source_id:
            problems.append("the topic's source.id is not the ledger row's Lovable id.")
        if str(topic.get("reference") or "") != row.reference:
            problems.append("the topic's reference is not the ledger row's.")
        if problems:
            selection.left_out.append(_left(row, NOT_HISTORICAL, "; ".join(problems)))
            continue
        single = {**header, "expected_topics": 1, "topics": [topic]}
        report = validate_package(single)
        if report.problems:
            selection.left_out.append(_left(row, VALIDATION, "; ".join(report.problems)))
            continue
        topics.append(topic)
        selection.selected.append(row.reference)

    if not topics:
        return selection
    _unlink_loops(topics)
    package = {**header, "expected_topics": len(topics), "topics": topics}
    problems = validate_package(package).problems + package_problems(package)
    if problems:
        raise BackfillError(
            "The assembled backfill package does not validate:\n  " + "\n  ".join(problems)
        )
    selection.package = package
    return selection


def _unlink(topic: dict[str, Any], why: str) -> None:
    """Keep a continuation as source only: still SUPERSEDED (the stage link is the
    source's), but no `superseded_by` the importer would write."""
    topic["successor"] = ""
    (topic.get("source") or {}).pop("successor_id", None)
    topic.setdefault("review_notes", []).append(
        f"CONTINUATION_NOT_LINKED: {why}; the stage link is kept as source only"
    )


def _unlink_loops(topics: list[dict[str, Any]]) -> None:
    """Stage links that lead back to where they started cannot all be written
    (`validate_package` refuses a cycle and would refuse the whole package):
    every topic on such a loop is kept unlinked, nothing else changes."""
    successors = {str(t.get("reference")): str(t.get("successor") or "") for t in topics}
    looped: set[str] = set()
    for start in successors:
        current, seen = successors[start], 0
        while current and seen <= len(successors):
            if current == start:
                looped.add(start)
                break
            current, seen = successors.get(current, ""), seen + 1
    for topic in topics:
        if str(topic.get("reference")) in looped:
            _unlink(topic, "the stage links form a loop")


# ---------------------------------------------------------------------------
# The gated steps — only the importer's own machinery
# ---------------------------------------------------------------------------


def _load(path_or_package: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(path_or_package, dict):
        return path_or_package
    return lovable_pilot.load_package(path_or_package)


def validate(path_or_package: str | Path | dict[str, Any]) -> list[str]:
    package = _load(path_or_package)
    return validate_package(package).problems + package_problems(package) + reader_problems(package)


def plan(path_or_package: str | Path | dict[str, Any]) -> LovablePlan:
    """`lovable_pilot.build_plan`, with the backfill's own rules as plan problems,
    so `dry_run` and `apply_plan` refuse a package that is not a historical
    CREATE-only one."""
    package = _load(path_or_package)
    built = lovable_pilot.build_plan(package)
    built.problems.extend(
        p for p in package_problems(package) + reader_problems(package) if p not in built.problems
    )
    bound = (package.get("selection") or {}).get("decisions_sha256")
    if bound != canonical_sha256(decision_index()):
        built.problems.append(
            f"{DECISIONS_CHANGED}; build the package again from the reading "
            "(lovable_inventory backfill --step package)."
        )
    return built


def dry_run(path_or_package: str | Path | dict[str, Any]) -> lovable_pilot.ApplyReport:
    return lovable_pilot.dry_run(plan(path_or_package))


def apply(
    path_or_package: str | Path | dict[str, Any],
    *,
    operator_intent: str,
    expect_package_sha256: str,
    expect_plan_sha256: str,
    backup_set: str,
) -> lovable_pilot.ApplyReport:
    """Every gate is `lovable_pilot.apply_plan`'s; nothing here relaxes one."""
    if operator_intent != HISTORY_COHORT:
        raise BackfillError(f"Type --operator-intent {HISTORY_COHORT} to apply the backfill.")
    return lovable_pilot.apply_plan(
        plan(path_or_package),
        operator_intent=operator_intent,
        expect_package_sha256=expect_package_sha256,
        expect_plan_sha256=expect_plan_sha256,
        backup_set=backup_set,
    )


def verify(path_or_package: str | Path | dict[str, Any]) -> lovable_pilot.VerifyReport:
    return lovable_pilot.verify_applied(plan(path_or_package))
