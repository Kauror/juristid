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
operational topic.

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

Reader = Callable[..., dict[str, Any]]


class BackfillError(LovablePilotError):
    """The backfill refused. Nothing was written."""


@dataclass
class Selection:
    package: dict[str, Any] | None
    selected: list[str] = field(default_factory=list)
    left_out: list[dict[str, Any]] = field(default_factory=list)
    reader_version: str = ""

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
    row = (topic.get("source") or {}).get("row") or {}
    if isinstance(row, dict) and row.get("operatiivne_ulatus"):
        out.append("an operational topic is the nightly synchronisation's, not the backfill's.")
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

    topics: list[dict[str, Any]] = []
    for row in sorted(ledger.rows, key=lambda r: r.reference):
        if not row.in_source:
            continue
        reason = eligible(row)
        if reason:
            if reason != NOT_ABSENT:
                selection.left_out.append(_left(row, reason))
            continue
        bundle = bundles.get(row.source_id)
        if bundle is None:
            selection.left_out.append(_left(row, NO_BUNDLE, "the reading holds no bundle"))
            continue
        try:
            topic = reader(bundle, as_of=as_of, people=people, historical=True)
        except Exception as error:  # the reader refusing one topic never stops the rest
            selection.left_out.append(_left(row, READER_REFUSED, type(error).__name__))
            continue
        if not isinstance(topic, dict):
            selection.left_out.append(_left(row, READER_REFUSED, "not a topic"))
            continue
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
    package = {**header, "expected_topics": len(topics), "topics": topics}
    problems = validate_package(package).problems + package_problems(package)
    if problems:
        raise BackfillError(
            "The assembled backfill package does not validate:\n  " + "\n  ".join(problems)
        )
    selection.package = package
    return selection


# ---------------------------------------------------------------------------
# The gated steps — only the importer's own machinery
# ---------------------------------------------------------------------------


def _load(path_or_package: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(path_or_package, dict):
        return path_or_package
    return lovable_pilot.load_package(path_or_package)


def validate(path_or_package: str | Path | dict[str, Any]) -> list[str]:
    package = _load(path_or_package)
    return validate_package(package).problems + package_problems(package)


def plan(path_or_package: str | Path | dict[str, Any]) -> LovablePlan:
    """`lovable_pilot.build_plan`, with the backfill's own rules as plan problems,
    so `dry_run` and `apply_plan` refuse a package that is not a historical
    CREATE-only one."""
    package = _load(path_or_package)
    built = lovable_pilot.build_plan(package)
    built.problems.extend(p for p in package_problems(package) if p not in built.problems)
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
