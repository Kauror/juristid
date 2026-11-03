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

**Two cohorts, one importer** (docs/adr/0202). A version-1 package is the
pilot's (``lovable-pilot``) and reads exactly as it always did. A version-2
package names ``"cohort": "lovable-operational"``: the active register's ongoing
topics, read by 0202 §3's rules (a live stage with «ei saatnud» stays open, an
unreadable step is held rather than guessed, a continuation needs Lovable's
structured stage link). The cohort is the `EvidencePilotRecord.pilot` of every
record, the intent typed at `apply`, and what the nightly synchronisation
enrols. Besides the fingerprint, every pre-existing Teema and the records
hanging off it are digested before and after the writes, inside the
transaction (`preserved_digests`), and any difference refuses the apply.

`validate` (no database) → `plan` (resolves people, stages, instruments and
organisations; checks every reference; prints the plan digest) → `dry_run`
(writes everything, verifies it, rolls back) → `apply` (one transaction, every
gate) → `verify` (reads back and compares, record by record).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.legacy_import.evidence_pilot import step_shape
from app.legacy_import.integrity import IntegrityError, canonical_sha256, require_digest
from app.legacy_import.limits import load_json_document
from app.workflow.services import OPINION_PREPARATION_TEXT

PILOT = "lovable-pilot"
#: The active register's second cohort (docs/adr/0202 §1). The same importer;
#: the cohort is the `EvidencePilotRecord.pilot` of everything a package writes,
#: the intent typed at `apply` and the key the nightly synchronisation enrols.
OPERATIONAL_COHORT = "lovable-operational"
COHORTS = (PILOT, OPERATIONAL_COHORT)
PILOT_VERSION = "1.0"
#: The importer version each cohort's records carry. The pilot's stays "1.0" so
#: that a version-1 package writes exactly the records it always wrote.
COHORT_VERSIONS = {PILOT: PILOT_VERSION, OPERATIONAL_COHORT: "2.0"}
PACKAGE_FORMAT = "juristid.lovable-pilot"
PACKAGE_FORMAT_VERSION = 1
#: Version 2 is the active cohort's reading (docs/adr/0202 §3); version 1 reads
#: exactly as before and means `lovable-pilot`.
ACTIVE_FORMAT_VERSION = 2
FORMAT_COHORTS = {PACKAGE_FORMAT_VERSION: PILOT, ACTIVE_FORMAT_VERSION: OPERATIONAL_COHORT}
#: Typed by the operator at `apply`, the same discipline as the other pilots.
#: It is the package's cohort; this is the pilot's.
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
HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
#: The Lovable columns a version-2 package may hold for review, and the
#: synchronised field each feeds. One set for the importer and the nightly
#: synchronisation (`lovable_sync.IMPORT_HELD_COLUMNS` is this): a held column
#: outside it would be held by nobody, so `validate_package` refuses it.
HELD_FIELD_COLUMNS: dict[str, str] = {
    "jargmiseks": "next_action_text",
    "tegevuse_tahtaeg": "next_action_date",
    "hetkeseis": "stage",
    "arvamuse_tahtaeg": "opinion_deadline",
    "valja": "sent",
    "valja_kuupaev": "sent_date",
    "kellele": "addressee",
    "oigusakt": "legal_act",
    "sisse": "received",
    "koja_ettepanek": "koda_initiative",
    "teema": "title",
    "vastutaja": "owner",
    "uus_vastutaja": "owner",
    "joustumised": "commencements",
    "onenote_link": "onenote_link",
}
#: Lovable's `hetkeseis` values that end a topic's work. Anything else is a live
#: stage, on which «ei saatnud» concludes nothing (docs/adr/0202 §3).
TERMINAL_STATUSES = frozenset({"jõustunud", "rohkem ei tegele", "lõpetatud"})
NOT_SENT = "ei saatnud"
#: Columns a version-2 package's source row must not carry: identity or raw
#: import data and the source's own notes, which no reader of 0201/0202 reads.
EXCLUDED_SOURCE_FIELDS = frozenset(
    {"markmed", "mitteametlik_arvamus", "raw_hyperlink", "allikas", "vastutaja_user_id"}
)

#: No leading zero: `2026_05` would be stored, and displayed, as `2026_5`.
_REFERENCE = re.compile(r"^(\d{4})_([1-9]\d{0,5})$")
#: `EvidencePilotRecord.record_key`.
RECORD_KEY_LENGTH = 120
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


def package_cohort(package: dict[str, Any]) -> str:
    """The cohort a package writes: its own ``cohort``, or the pilot's when it names
    none (every version-1 package). `validate_package` refuses anything else."""
    return str(package.get("cohort") or PILOT)


def _is_v2(package: dict[str, Any]) -> bool:
    return package.get("format_version") == ACTIVE_FORMAT_VERSION


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
    version = package.get("format_version")
    if version not in FORMAT_COHORTS:
        out.append(f"format_version {version!r} is not supported.")
    cohort = package.get("cohort")
    if cohort is not None and cohort not in COHORTS:
        out.append(f"cohort {cohort!r} is unknown; a package is one of {', '.join(COHORTS)}.")
    elif version in FORMAT_COHORTS and package_cohort(package) != FORMAT_COHORTS[version]:
        out.append(
            f"format_version {version} is the {FORMAT_COHORTS[version]} cohort's, not "
            f"{package_cohort(package)!r}."
        )
    v2 = _is_v2(package)
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
    identities: dict[str, str] = {}
    keys: set[str] = set()
    highest: dict[str, int] = {}
    for topic in topics:
        reference = str(topic.get("reference") or "")
        report.topics += 1
        action = topic_action(topic)
        if not v2 and "action" in topic:
            problems = ["a topic action is a version-2 reading; version 1 only creates."]
        elif action not in TOPIC_ACTIONS:
            problems = [f"action {action!r} is neither CREATE nor REBASE."]
        elif action == REBASE:
            problems = _validate_rebase(topic, people=people, as_of=as_of)
            problems += _validate_active_topic(topic, as_of=as_of, rebase=True)
        else:
            problems = _validate_topic(topic, people=people, as_of=as_of)
            if v2:
                problems += _validate_active_topic(topic, as_of=as_of)
        out.extend(f"{reference}: {p}" for p in problems)
        parsed = parse_reference(reference)
        if parsed is None:
            continue
        if reference in seen:
            out.append(f"{reference}: listed twice.")
        seen.add(reference)
        source_id = str((topic.get("source") or {}).get("id") or "")
        if source_id and source_id in identities:
            out.append(
                f"{reference}: its Lovable identity is also {identities[source_id]}'s; one "
                "topic is one Teema."
            )
        identities.setdefault(source_id, reference)
        highest[str(parsed[0])] = max(highest.get(str(parsed[0]), 0), parsed[1])
        # Every provenance key the writer will use, once each and short enough.
        for key in _rebase_record_keys(topic) if action == REBASE else _record_keys(topic):
            if not key or len(key) > RECORD_KEY_LENGTH:
                out.append(f"{reference}: record key {key!r} is empty or too long.")
            elif key in keys:
                out.append(f"{reference}: record key {key!r} is used twice.")
            keys.add(key)
        if topic.get("lifecycle") == "ACTIVE":
            report.open_topics += 1
        if topic.get("next_action"):
            report.steps += 1
        for record in topic.get("commencements") or []:
            if record.get("review") == READY:
                report.commencements_ready += 1
            else:
                report.commencements_source_only += 1
    successors = {str(t.get("reference") or ""): str(t.get("successor") or "") for t in topics}
    for reference, successor in successors.items():
        if successor == reference:
            out.append(f"{reference}: a topic cannot continue under itself.")
        elif successor and successors.get(successor) == reference:
            out.append(f"{reference}: continues under {successor}, which continues under it.")
        elif _cycle_length(reference, successors) > 2:
            # A longer loop is as impossible as a two-topic one; an acyclic package
            # never reaches this, so no version-1 result changes.
            out.append(f"{reference}: its continuations lead back to it (a cycle).")
    out.extend(_validate_reserve(package, highest))
    return report


def _cycle_length(start: str, successors: dict[str, str]) -> int:
    """How many continuations lead from `start` back to it; 0 when they never do."""
    current, steps = start, 0
    while steps <= len(successors):
        current = successors.get(current, "")
        steps += 1
        if not current:
            return 0
        if current == start:
            return steps
    return 0


def _source_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


#: Stripped from both ends before a source wording is compared: «Ei saatnud.»
#: is «ei saatnud».
_WORDING_PUNCTUATION = " .,;:!?…\"'«»„“”"


def _wording(value: Any) -> str:
    return _source_text(value).strip(_WORDING_PUNCTUATION)


def _excluded(name: Any) -> bool:
    return str(name) in EXCLUDED_SOURCE_FIELDS or str(name).endswith("_by")


def _excluded_source_columns(source: dict[str, Any]) -> list[str]:
    """Every excluded column the package's source copy carries, wherever it is:
    the row, the Excel-source row, the change history (its entries' keys and the
    field names they list) and the responsible changes."""
    found: set[str] = set()

    def keys(where: str, value: Any) -> None:
        if isinstance(value, dict):
            found.update(f"{where}.{k}" for k in value if _excluded(k))

    keys("row", source.get("row"))
    keys("excel_source_row", source.get("excel_source_row"))
    for entry in source.get("responsible_changes") or []:
        keys("responsible_changes", entry)
    for entry in source.get("history") or []:
        keys("history", entry)
        if not isinstance(entry, dict):
            continue
        for item in entry.get("fields") or []:
            name = item.get("field") or item.get("name") if isinstance(item, dict) else item
            if _excluded(name):
                found.add(f"history.fields.{name}")
            keys("history.fields", item)
    return sorted(found)


def _validate_active_topic(
    topic: dict[str, Any], *, as_of: dt.date | None, rebase: bool = False
) -> list[str]:
    """The version-2 reading rules (docs/adr/0202 §3), on top of every version-1 one.

    What version 1 already allows stays allowed: an open topic with no current
    step (a step the reviewer could not read is held, not guessed), a WAIT or
    MONITOR whose review day has passed (a review that has come round, never
    overdue). What is added is refused here.
    """
    out: list[str] = []
    source = topic.get("source") or {}
    raw = source.get("row")
    row: dict[str, Any] = raw if isinstance(raw, dict) else {}
    excluded = _excluded_source_columns(source)
    if excluded:
        out.append(
            f"source.{', source.'.join(excluded)} is carried, which a version-2 package never "
            "copies (identity, raw import data or the source's own notes)."
        )

    decisions = topic.get("field_decisions") or {}
    notes = topic.get("review_notes")
    held = sorted(name for name, decision in decisions.items() if decision == HELD_FOR_REVIEW)
    if held and not (isinstance(notes, list) and notes and all(notes)):
        out.append(
            f"field(s) {', '.join(held)} are HELD_FOR_REVIEW without the reviewer's reason in "
            "review_notes."
        )
    unheld = [name for name in held if name not in HELD_FIELD_COLUMNS]
    if unheld:
        out.append(
            f"field(s) {', '.join(unheld)} cannot be HELD_FOR_REVIEW: no synchronised field "
            "would hold them (HELD_FIELD_COLUMNS)."
        )
    if rebase:
        # A REBASE states its readings in `changes`; the rest concerns CREATE.
        return out

    statuses = {_source_text(topic.get("status_raw")), _source_text(row.get("hetkeseis"))}
    live = not statuses & TERMINAL_STATUSES
    sent_wording = _wording(row.get("valja"))
    sent = bool(sent_wording and sent_wording != NOT_SENT) or bool(row.get("valja_kuupaev"))
    if live and sent_wording == NOT_SENT:
        # «ei saatnud» is a source assertion: on a live stage it concludes nothing
        # (0148's rule is for a finished stage) and is never a SENT opinion.
        if topic.get("lifecycle") != "ACTIVE":
            out.append(
                "«ei saatnud» on a live stage concludes nothing: the topic is ACTIVE at its stage."
            )
        if decisions.get("valja") != HELD_FOR_REVIEW:
            out.append("«ei saatnud» on a live stage is held for review (field_decisions.valja).")

    deadline = topic.get("deadline") or {}
    day = _day(deadline.get("deadline"))
    if deadline.get("treatment") == "OPEN_REQUEST":
        if as_of and day and day < as_of:
            out.append(
                "an open request whose day is before the snapshot is history, held for review — "
                "never an overdue obligation."
            )
        if sent:
            out.append(
                "the source records a send (valja / valja_kuupaev); an answered request is not "
                "an open one."
            )
    requested = _day(row.get("arvamuse_tahtaeg"))
    # Only an open file can carry an obligation; a finished one keeps its old
    # deadline as history (0191 §4) and has nothing to hold.
    if (
        topic.get("lifecycle") == "ACTIVE"
        and not sent
        and as_of
        and requested
        and requested < as_of
        and decisions.get("arvamuse_tahtaeg") != HELD_FOR_REVIEW
    ):
        out.append(
            "an opinion deadline before the snapshot with nothing sent is held for review "
            "(field_decisions.arvamuse_tahtaeg), never an overdue obligation."
        )

    if topic.get("successor") and not source.get("successor_id"):
        out.append(
            "a continuation needs source.successor_id, the successor's Lovable identity from "
            "the structured stage link."
        )
    return out


def _record_keys(topic: dict[str, Any]) -> list[str]:
    reference = str(topic.get("reference") or "")
    keys = [reference]
    if topic.get("next_action"):
        keys.append(f"{reference}-NEXT")
    if (topic.get("deadline") or {}).get("treatment") == "OPEN_REQUEST":
        keys.append(f"{reference}-REQUEST")
    if topic.get("successor"):
        keys.append(f"{reference}-CONTINUES")
    keys.extend(
        str(r.get("key") or "")
        for r in topic.get("commencements") or []
        if r.get("review") == READY
    )
    return keys


def _validate_reserve(package: dict[str, Any], highest: dict[str, int]) -> list[str]:
    """`reserve_through` moves a reference sequence for good, so it must be exactly
    the source's own highest number, stated in the snapshot, and never below the
    package's."""
    out: list[str] = []
    stated = (package.get("snapshot") or {}).get("highest_reference") or {}
    for year, through in (package.get("reserve_through") or {}).items():
        if not re.fullmatch(r"20\d{2}", str(year)) or not isinstance(through, int):
            out.append(f"reserve_through {year!r}: {through!r} is not a year and a number.")
        elif stated.get(str(year)) != through:
            out.append(
                f"reserve_through {year}: {through} is not the snapshot's highest_reference "
                f"for that year ({stated.get(str(year))!r})."
            )
        elif through < highest.get(str(year), 0):
            out.append(f"reserve_through {year}: {through} is below the package's own references.")
    return out


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
    if active and not str(people.get(owner_raw) or "").strip():
        out.append(f"owner {owner_raw!r} has no reviewed person; an open file needs one.")
    if topic.get("successor") and (active or disposition != "SUPERSEDED"):
        out.append("only a finished, SUPERSEDED topic continues under another.")

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
        out.extend(_validate_step(step, as_of=as_of))

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
        if topic.get("koda_initiative"):
            out.append("a Chamber initiative takes no new request (docs/adr/0151 §2).")
        if not (
            step
            and step.get("treatment") == "OPINION_DRAFTING"
            and step.get("text") == OPINION_PREPARATION_TEXT
            and step.get("kind") == "DO"
            and step.get("date_semantics") == "DEADLINE"
            and _day(step.get("target_date")) == day
        ):
            out.append("an open request is drafted: the step is DO «Koostan arvamuse» on its day.")
    elif step and step.get("treatment") == "OPINION_DRAFTING":
        out.append("a drafting step needs the open request it answers.")

    for record in topic.get("commencements") or []:
        key = record.get("key") or "?"
        if not record.get("key"):
            out.append("a commencement needs its key.")
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
        out.extend(_validate_commencement(record, key))

    url = topic.get("onenote_url") or ""
    if url and not url.startswith("https://"):
        out.append("onenote_url is not an https address; fix the package.")
    for name, decision in (topic.get("field_decisions") or {}).items():
        if decision not in FIELD_DECISIONS:
            out.append(f"field_decisions[{name}] = {decision!r} is unknown.")
    return out


def _validate_step(step: dict[str, Any], *, as_of: dt.date | None) -> list[str]:
    """A reviewed step's own shape: what CREATE and a REBASE reading share."""
    out: list[str] = []
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
    for name in ("kind", "date_semantics"):
        # The package states the reading; a default never stands in for it.
        if not step.get(name):
            out.append(f"next_action.{name} is required.")
    _shape, shape_problems = step_shape(shape_input)
    out.extend(shape_problems)
    if step.get("kind") in {"WAIT", "MONITOR"} and step.get("date_semantics") != "REVIEW_ON":
        out.append("a WAIT or MONITOR step is a review date (REVIEW_ON).")
    step_day = _day(step.get("target_date"))
    if step.get("date_semantics") == "DEADLINE" and as_of and step_day and step_day < as_of:
        out.append("a deadline before the snapshot would be overdue on arrival; not current.")
    if not step.get("reason"):
        out.append("next_action needs the reviewer's reason.")
    return out


def _validate_commencement(record: dict[str, Any], key: str) -> list[str]:
    """A READY commencement record's target, kind and day."""
    out: list[str] = []
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
    return out


# ---------------------------------------------------------------------------
# REBASE: a pilot Teema brought to the snapshot in place (docs/adr/0202 §3)
# ---------------------------------------------------------------------------

CREATE = "CREATE"
REBASE = "REBASE"
TOPIC_ACTIONS = frozenset({CREATE, REBASE})
#: What a REBASE may change, each through its own service.
REBASE_FIELDS = (
    "title",
    "owner",
    "next_action",
    "stage",
    "open_request",
    "commencement_add",
)
KEEP_LOCAL = "KEEP_LOCAL"
TAKE_SOURCE = "TAKE_SOURCE"
HOLD = "HOLD"
REBASE_DECISIONS = frozenset({KEEP_LOCAL, TAKE_SOURCE, HOLD})
#: A change's outcome in the plan. WRITE and TAKE_SOURCE write C; the rest nothing.
NOTHING = "NOTHING"
WRITE = "WRITE"
WRITING = frozenset({WRITE, TAKE_SOURCE})
#: The writes a REBASE makes, as `EvidencePilotRecord.record_kind` and key suffix.
REBASE_RECORDS = {
    "title": ("TITLE", "TITLE"),
    "owner": ("OWNER", "OWNER"),
    "next_action": ("NEXT_ACTION", "NEXT"),
    "stage": ("STAGE", "STAGE"),
    "open_request": ("RESPONSE_REQUEST", "REQUEST"),
}
#: Provenance of every service call a REBASE makes.
REBUILD_PROVENANCE = "LOVABLE_REBUILD"
#: Keys a REBASE topic never carries: it states its changes in ``changes``, and
#: nothing it writes is a link (a rebased Teema's continuation is never touched).
_CREATE_ONLY = ("successor", "next_action", "deadline", "commencements", "lifecycle")


#: The Lovable-derived values `derived_values` reads, and which of them each
#: written field changes.
_DERIVED_FIELDS = (
    "title",
    "owner",
    "stage",
    "next_action",
    "next_action_id",
    "open_request",
    "commencements",
    "superseded_by",
)
_CHANGES_DERIVED = {
    "title": {"title"},
    "owner": {"owner"},
    "stage": {"stage"},
    "next_action": {"next_action", "next_action_id"},
    "open_request": {"open_request"},
    "commencement_add": {"commencements"},
}


def _declared_fields(topic_plan: Any) -> set[str]:
    return {name for e in topic_plan.writes() for name in _CHANGES_DERIVED[e["field"]]}


def _change_reason(topic_plan: Any, name: str) -> str:
    for change in topic_plan.topic.get("changes") or []:
        if change.get("field") == name:
            return str(change.get("reason") or "")
    return ""


def topic_action(topic: dict[str, Any]) -> str:
    return str(topic.get("action") or CREATE)


def _validate_rebase(
    topic: dict[str, Any], *, people: dict[str, Any], as_of: dt.date | None
) -> list[str]:
    """A REBASE entry, without a database: what it names and the readings it states."""
    from app.legacy_import.lovable_source import TOPIC_COLUMNS
    from app.workflow.stage_flow import TERMINAL_STAGE_KEYS

    out: list[str] = []
    reference = str(topic.get("reference") or "")
    if parse_reference(reference) is None:
        return [f"reference {reference!r} is not YYYY_N."]
    if not (topic.get("title") or "").strip():
        out.append("no title.")
    source = topic.get("source") or {}
    row = source.get("row")
    if not source.get("id") or not isinstance(row, dict):
        out.append("source.id and source.row (the Lovable row as read) are required.")
    else:
        extra = sorted(set(row) - set(TOPIC_COLUMNS))
        if extra:
            out.append(f"source.row carries {', '.join(extra)}, which is not a topic column read.")
    carried = [key for key in _CREATE_ONLY if topic.get(key)]
    if carried:
        out.append(
            f"a REBASE carries no {', '.join(carried)}; it states its readings in `changes`."
        )
    url = topic.get("onenote_url") or ""
    if url and not url.startswith("https://"):
        out.append("onenote_url is not an https address; fix the package.")
    for name, decision in (topic.get("field_decisions") or {}).items():
        if decision not in FIELD_DECISIONS:
            out.append(f"field_decisions[{name}] = {decision!r} is unknown.")

    changes = topic.get("changes")
    if not isinstance(changes, list):
        return [*out, "changes must be a list (empty when nothing differs)."]
    seen: set[str] = set()
    decisions = topic.get("field_decisions") or {}
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            out.append(f"changes[{index}] is not an object.")
            continue
        name = str(change.get("field") or "")
        where = f"changes[{index}] ({name or '?'})"
        if name not in REBASE_FIELDS:
            out.append(f"{where}: {name!r} is not a field a REBASE may change.")
            continue
        if name in seen and name != "commencement_add":
            out.append(f"{where}: listed twice.")
        seen.add(name)
        decision = change.get("decision")
        if decision is not None and decision not in REBASE_DECISIONS:
            out.append(f"{where}: decision {decision!r} is not KEEP_LOCAL, TAKE_SOURCE or HOLD.")
        if not str(change.get("reason") or "").strip():
            out.append(f"{where}: the reviewer's reason is required.")
        reading = change.get("reading")
        held = decision == HOLD
        if decision in {KEEP_LOCAL, HOLD}:
            columns = REBASE_HELD_COLUMNS[name]
            if not any(decisions.get(column) == HELD_FOR_REVIEW for column in columns):
                out.append(
                    f"{where}: {decision} keeps Juristid's value; field_decisions marks "
                    f"{' or '.join(columns)} HELD_FOR_REVIEW."
                )
            if reading is None and name != "commencement_add":
                continue  # nothing is written, so nothing need be read
        if name == "title" and not str(reading or "").strip():
            out.append(f"{where}: a title reading is the title.")
        elif name == "title" and reading != topic.get("title"):
            out.append(f"{where}: the title reading is the entry's reviewed title, exactly.")
        elif name == "owner":
            if not str(reading or "").strip():
                out.append(f"{where}: an owner reading names a person.")
            elif reading not in people and reading not in people.values():
                out.append(f"{where}: {reading!r} is not in the reviewed people map.")
        elif name == "next_action":
            if reading is None and not held:
                out.append(f"{where}: no readable step is HOLD, never a cleared step.")
            elif isinstance(reading, dict):
                out.extend(f"{where}: {p}" for p in _validate_step(reading, as_of=as_of))
            elif reading is not None:
                out.append(f"{where}: a step reading is a next_action object.")
        elif name == "stage":
            if not isinstance(reading, str) or not reading:
                out.append(f"{where}: a stage reading is a vocabulary key.")
            elif reading in TERMINAL_STAGE_KEYS and not held:
                out.append(
                    f"{where}: {reading!r} ends the work and would close the Teema; a closure is "
                    "never inferred — the decision is HOLD (docs/adr/0202 §3)."
                )
        elif name == "open_request":
            day = _day((reading or {}).get("deadline")) if isinstance(reading, dict) else None
            if reading is None and not held:
                out.append(f"{where}: no request is HOLD, never a cleared one.")
            elif reading is not None and day is None:
                out.append(f"{where}: an open request reading is {{deadline}}.")
            elif day and as_of and day < as_of:
                out.append(
                    f"{where}: an open request whose day is before the snapshot is history, "
                    "held for review — never an overdue obligation."
                )
        elif name == "commencement_add":
            if not isinstance(reading, dict) or not reading.get("key"):
                out.append(f"{where}: a commencement reading is a record with its key.")
            else:
                out.extend(_validate_commencement(reading, str(reading["key"])))
    return out


#: The Lovable columns behind each REBASE field: a KEEP_LOCAL or HOLD holds one of
#: them for review, so the synchronisation reports it every night.
REBASE_HELD_COLUMNS = {
    "title": ("teema",),
    "owner": ("vastutaja", "uus_vastutaja"),
    "next_action": ("jargmiseks", "tegevuse_tahtaeg"),
    "stage": ("hetkeseis",),
    "open_request": ("arvamuse_tahtaeg",),
    "commencement_add": ("joustumised",),
}


def _rebase_record_keys(topic: dict[str, Any]) -> list[str]:
    reference = str(topic.get("reference") or "")
    keys = [reference]
    keys += [f"{reference}-{suffix}" for _kind, suffix in REBASE_RECORDS.values()]
    keys += [
        str((change.get("reading") or {}).get("key") or "")
        for change in topic.get("changes") or []
        if isinstance(change, dict) and change.get("field") == "commencement_add"
    ]
    return keys


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
    #: CREATE or REBASE (docs/adr/0202 §1).
    action: str = CREATE
    #: REBASE: the pilot Teema it brings to the snapshot.
    matter: Any = None
    #: REBASE: one entry per change — field, record key, outcome (NOTHING, WRITE,
    #: TAKE_SOURCE, KEEP_LOCAL, HOLD), the decision, A, B and C, and the reading.
    resolution: list[dict[str, Any]] = field(default_factory=list)
    #: REBASE: B for what every outcome relies on beyond its own field — the open
    #: step's identity and the continuation, which the rebase never touches.
    relied: dict[str, Any] = field(default_factory=dict)
    #: REBASE: whether the Teema has a current step after the rebase, which then
    #: gets a NEXT_ACTION record (the synchronisation's baseline finds it there).
    next_record: bool = False
    #: REBASE: this package's REBASE record is already there (read back, not re-planned).
    rebased: bool = False

    @property
    def reference(self) -> str:
        return str(self.topic["reference"])

    @property
    def is_open(self) -> bool:
        if self.action == REBASE:
            return bool(
                self.matter is not None
                and self.matter.is_open
                and getattr(self.matter, "deleted_at", None) is None
            )
        return self.topic["lifecycle"] == "ACTIVE"

    def writes(self) -> list[dict[str, Any]]:
        return [entry for entry in self.resolution if entry["action"] in WRITING]


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
    #: Package references whose Lovable identity Juristid already holds.
    identity_collisions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def cohort(self) -> str:
        return package_cohort(self.package)

    def expected_writes(self) -> dict[str, int]:
        creates = sum(1 for t in self.topics if t.action == CREATE)
        counts = {"MATTER": creates} if creates else {}
        for topic_plan in self.topics:
            topic = topic_plan.topic
            if topic_plan.action == REBASE:
                counts["REBASE"] = counts.get("REBASE", 0) + 1
                for entry in topic_plan.writes():
                    kind = _write_kind(entry)
                    if kind != "NEXT_ACTION":
                        counts[kind] = counts.get(kind, 0) + 1
                if topic_plan.next_record:
                    counts["NEXT_ACTION"] = counts.get("NEXT_ACTION", 0) + 1
                continue
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
    from app.accounts.selectors import is_assignable_business_user
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
    v2 = _is_v2(package)
    source_ids = {t["reference"]: str(t["source"]["id"]) for t in package["topics"]}

    for topic in package["topics"]:
        topic_plan = TopicPlan(topic=topic, action=topic_action(topic))
        reference = topic_plan.reference
        if topic_plan.action == REBASE:
            # Resolved against the Teema it names, in `_read_database`.
            plan.topics.append(topic_plan)
            continue

        owner_raw = topic.get("owner_raw") or ""
        name = people.get(owner_raw)
        if name:
            matches = list(User.objects.filter(display_name=name))
            if len(matches) != 1 or not matches[0].is_active:
                plan.problems.append(
                    f"{reference}: owner {owner_raw!r} → {name!r} is not exactly one active "
                    "account."
                )
            elif not is_assignable_business_user(matches[0]):
                plan.problems.append(
                    f"{reference}: owner {name!r} is not somebody business work may be "
                    "assigned to (docs/adr/0181)."
                )
            else:
                topic_plan.owner = matches[0]
        if topic_plan.is_open and topic_plan.owner is None:
            plan.problems.append(f"{reference}: an open file needs its owner's account.")
        elif not name and owner_raw:
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
        if successor and successor in references and v2:
            # Version 2 links only what Lovable's structured stage link proves:
            # the successor's identity, written by this same package.
            if source_ids.get(successor) == str(topic["source"].get("successor_id") or ""):
                topic_plan.successor_in_batch = successor
            else:
                topic_plan.warnings.append(
                    f"continues under {successor}, but the package's {successor} is not the "
                    "topic the stage link names; the link is kept as source text only."
                )
        elif successor and successor in references:
            topic_plan.successor_in_batch = successor
        elif successor:
            topic_plan.warnings.append(
                f"continues under {successor}, which this package does not hold; the link is "
                "kept as source text and nothing outside the package is touched."
            )
        if v2:
            plan.problems.extend(f"{reference}: {p}" for p in _overdue_on_arrival(topic, _today()))
        plan.topics.append(topic_plan)

    _read_database(plan)
    return plan


def _today() -> dt.date:
    """The apply's own day, in Tallinn (``TIME_ZONE``)."""
    return timezone.localdate()


def _overdue_on_arrival(topic: dict[str, Any], today: dt.date) -> list[str]:
    """Version 2: a deadline that has passed by the day the plan is made — and the
    plan is made again under the lock at apply — would arrive overdue. The
    snapshot's own day is checked by `validate_package`; this is the gap between
    the snapshot and the apply (docs/adr/0202 §3)."""
    out: list[str] = []
    step = topic.get("next_action") or {}
    step_day = _day(step.get("target_date"))
    if step.get("date_semantics") == "DEADLINE" and step_day and step_day < today:
        out.append(
            f"the step's deadline {step_day} is before today; it would be overdue on arrival."
        )
    deadline = topic.get("deadline") or {}
    day = _day(deadline.get("deadline"))
    if deadline.get("treatment") == "OPEN_REQUEST" and day and day < today:
        out.append(f"the open request's day {day} is before today; it would be overdue on arrival.")
    return out


def _read_database(plan: LovablePlan) -> None:
    """Which of the package's references the database already holds, and what that means.

    CREATE references must be free; a REBASE names a pilot Teema that must be
    exactly what the pilot wrote it as, and is resolved three ways against it."""
    from django.db.models import Q

    from app.matters.models import Matter

    from .models import EvidencePilotRecord

    creates = [t for t in plan.topics if t.action == CREATE]
    rebases = [t for t in plan.topics if t.action == REBASE]
    plan.existing_matters = Matter.all_objects.count()
    pairs = [parse_reference(t.reference) for t in creates]
    query = Q()
    for year, number in pairs:  # type: ignore[misc]
        query |= Q(reference_year=year, reference_number=number)
    taken = Matter.all_objects.filter(query) if pairs else Matter.all_objects.none()
    mine = taken.filter(
        evidence_pilot_records__pilot=plan.cohort,
        evidence_pilot_records__package_sha256=plan.digest,
        evidence_pilot_records__record_kind="MATTER",
    ).distinct()
    written = EvidencePilotRecord.objects.filter(
        pilot=plan.cohort, package_sha256=plan.digest
    ).count()

    rebase_problems = _plan_rebases(plan, rebases)
    rebased = sum(1 for t in rebases if t.rebased)

    taken_refs = sorted({m.display_reference for m in taken} | _deleted_references(creates))
    if not taken_refs and written == 0:
        plan.database_state = "ready"
    elif (
        mine.count() == len(creates) == len(taken_refs)
        and rebased == len(rebases)
        and written == sum(plan.expected_writes().values())
    ):
        plan.database_state = "applied"
    else:
        mine_refs = {m.display_reference for m in mine}
        plan.collisions = [ref for ref in taken_refs if ref not in mine_refs]
        plan.database_state = "conflict"
        if plan.collisions:
            plan.problems.append(
                "These references already exist in Juristid and are never overwritten; replace "
                f"them in the selection and plan again: {', '.join(plan.collisions)}"
            )
        else:
            plan.problems.append(
                f"The database holds {written} record(s) of this package but not the whole of "
                "it — a partial or removed import. Nothing is repaired here; investigate first."
            )
    plan.problems.extend(rebase_problems)
    held = _identities_held(plan, creates, exclude_matters=[m.pk for m in mine])
    if held:
        plan.identity_collisions = [f"{ref} (Juristid {held[ref]})" for ref in sorted(held)]
        plan.database_state = "conflict"
        plan.problems.append(
            "These topics are already in Juristid under their Lovable identity — renumbered "
            "in the source or imported before; a topic is never imported twice: "
            + ", ".join(plan.identity_collisions)
        )


def _identity_holders(topics: list[TopicPlan]) -> dict[str, set[Any]]:
    """Package reference → every Matter whose sync state or Lovable source reference
    records that topic's Lovable identity (deleted Teemad included)."""
    from .lovable_sync_models import LovableSyncState
    from .models import MatterSourceReference

    wanted = {str(t.topic["source"]["id"]): t.reference for t in topics}
    if not wanted:
        return {}
    holders: dict[str, set[Any]] = {}
    states = LovableSyncState._base_manager.filter(source_id__in=list(wanted))
    for source_id, matter_id in states.values_list("source_id", "matter_id"):
        if str(source_id) in wanted:
            holders.setdefault(wanted[str(source_id)], set()).add(matter_id)
    sources = MatterSourceReference._base_manager.filter(
        source_system=SOURCE_SYSTEM, source_row_raw__lovable__id__in=list(wanted)
    )
    for raw, matter_id in sources.values_list("source_row_raw", "matter_id"):
        found = str((raw.get("lovable") or {}).get("id"))
        if found in wanted:
            holders.setdefault(wanted[found], set()).add(matter_id)
    return holders


def _reference_names(pks: Any) -> dict[Any, str]:
    from app.matters.models import Matter

    return {
        m["pk"]: f"{m['reference_year']}_{m['reference_number']}"
        for m in Matter.all_objects.filter(pk__in=list(pks)).values(
            "pk", "reference_year", "reference_number"
        )
    }


def _identities_held(
    plan: LovablePlan, topics: list[TopicPlan], *, exclude_matters: list[Any]
) -> dict[str, str]:
    """The CREATE topics whose Lovable identity a Juristid record already holds,
    other than this package's own Teemad. Package reference → Juristid reference."""
    exclude = set(exclude_matters)
    holders = {
        ref: found - exclude for ref, found in _identity_holders(topics).items() if found - exclude
    }
    names = _reference_names({pk for found in holders.values() for pk in found})
    return {
        ref: ", ".join(sorted(names.get(pk, str(pk)) for pk in found))
        for ref, found in holders.items()
    }


# -- REBASE: A, B and C ---------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _step_value(step: Any) -> dict[str, Any] | None:
    """A step as the three-way plan compares it: what it says and when."""
    from app.legacy_import.lovable_sync import text_value

    if step is None:
        return None
    if isinstance(step, dict):
        day = step.get("target_date")
        return {
            "text": text_value(step.get("text")),
            "kind": step.get("kind") or "",
            "date_semantics": step.get("date_semantics") or "",
            "target_date": str(day)[:10] if day else None,
        }
    return {
        "text": text_value(step.text),
        "kind": step.kind,
        "date_semantics": step.date_semantics,
        "target_date": _iso(step.target_date),
    }


def _commencement_value(target: str, kind: Any, day: Any) -> list[Any]:
    return [target, str(kind or ""), str(day)[:10] if day else None]


def derived_values(matter: Any) -> dict[str, Any]:
    """B: the Lovable-derived values of a Teema as Juristid holds them now."""
    from app.intelligence.enums import FactStatus
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.legacy_import.lovable_sync import text_value
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    step = (
        NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
        .order_by("created_at")
        .first()
    )
    dates = [
        _commencement_value(T_EFFECTIVE_DATE, kind, day)
        for kind, day in MatterEffectiveDate.objects.filter(
            matter=matter, status=FactStatus.ACTIVE
        ).values_list("kind", "date_value")
    ] + [
        _commencement_value(T_IMPORTANT_DATE, kind, day)
        for kind, day in MatterImportantDate.objects.filter(
            matter=matter, status=FactStatus.ACTIVE
        ).values_list("kind", "date_value")
    ]
    stage = matter.stage.key if matter.stage_id else ""
    return {
        "title": text_value(matter.title),
        "owner": str(matter.owner_id) if matter.owner_id else None,
        "stage": stage,
        "next_action": _step_value(step),
        "next_action_id": str(step.pk) if step is not None else None,
        "open_request": _iso(matter.response_deadline),
        "commencements": sorted(dates, key=lambda d: (d[0], d[1], d[2] or "")),
        "superseded_by": str(matter.superseded_by_id) if matter.superseded_by_id else None,
    }


def _pilot_values(matter: Any, state: Any, source: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """A — what the pilot import wrote, from its own immutable provenance, never
    from the synchronisation's baseline (a resolved or converged field moves that
    to the lawyer's value): the pilot source reference's title, the owner and
    step the pilot's own events record, the stage the Teema was created in, the
    pilot's request record and the commencements the pilot added.

    Also what B must satisfy to count as A for a field (``meta``): ``diverged``
    names the fields whose sync baseline no longer says what the pilot wrote, and
    ``step_id`` is the pilot's step."""
    from app.audit.models import ChangeEvent
    from app.legacy_import.lovable_sync import _imported_action, _imported_owner_id, text_value
    from app.matters.enums import StageEpisodeOrigin
    from app.matters.models import MatterStageEpisode

    from .models import EvidencePilotRecord

    first = (
        MatterStageEpisode.objects.filter(matter=matter)
        .select_related("stage")
        .order_by("sequence")
    ).first()
    # The pilot created the Teema in its stage, an IMPORTED period with no start;
    # a first period somebody recorded later means the pilot wrote no stage.
    stage = first.stage.key if first and first.origin == StageEpisodeOrigin.IMPORTED else ""
    records = list(EvidencePilotRecord.objects.filter(pilot=PILOT, matter=matter))
    request = next((r for r in records if r.record_kind == "RESPONSE_REQUEST"), None)
    dated = {
        str(r.target_id): r.record_kind
        for r in records
        if r.record_kind in {T_EFFECTIVE_DATE, T_IMPORTANT_DATE} and r.target_id
    }
    added = ChangeEvent.objects.filter(
        matter=matter,
        object_id__in=list(dated),
        event_type__in=("EFFECTIVE_DATE_ADDED", "IMPORTANT_DATE_ADDED"),
    ).values_list("object_id", "payload")
    commencements = [
        _commencement_value(
            dated[str(oid)], (payload or {}).get("kind"), (payload or {}).get("date")
        )
        for oid, payload in added
    ]
    action, _treatment = _imported_action(matter, PILOT)
    owner = _imported_owner_id(matter)
    a = {
        "title": text_value(source.source_title),
        "owner": owner,
        "stage": stage,
        "next_action": _step_value(action),
        "open_request": (
            str((request.evidence or {}).get("deadline") or "")[:10] or None if request else None
        ),
        "commencements": sorted(commencements, key=lambda d: (d[0], d[1], d[2] or "")),
    }
    applied = state.applied or {}
    baseline_owner = (applied.get("owner") or {}).get("target")
    baseline_step = (applied.get("next_action") or {}).get("target")
    diverged = set()
    if text_value((applied.get("title") or {}).get("target")) != a["title"]:
        diverged.add("title")
    if (str(baseline_owner) if baseline_owner else None) != owner:
        diverged.add("owner")
    if _step_value(baseline_step) != a["next_action"] or (baseline_step or {}).get("id") != (
        action or {}
    ).get("id"):
        diverged.add("next_action")
    meta = {
        "diverged": sorted(diverged),
        "step_id": (action or {}).get("id"),
        "step_edited": (action or {}).get("date_precision") == "?",
    }
    return a, meta


def _step_is_pilots(
    matter: Any, a: dict[str, Any], meta: dict[str, Any], b: dict[str, Any]
) -> bool:
    """Whether the open step is still exactly the pilot's: the same row, never
    edited in place (no event on it but the one that set it — a review, a moved
    day, a reworded check), its words what the setting event said, the pilot's
    owner responsible and no restriction of its own (docs/adr/0202 §3)."""
    from app.audit.models import ChangeEvent
    from app.workflow.models import NextAction

    if b["next_action_id"] is None or meta["step_id"] is None:
        return b["next_action_id"] is None and meta["step_id"] is None
    if b["next_action_id"] != meta["step_id"] or meta["step_edited"]:
        return False
    if "next_action" in meta["diverged"] or b["next_action"] != a["next_action"]:
        return False
    step = NextAction.objects.filter(pk=b["next_action_id"]).first()
    if step is None:
        return False
    events = list(
        ChangeEvent.objects.filter(matter=matter, object_id=step.pk).order_by("occurred_at")
    )
    if [e.event_type for e in events] != ["NEXT_ACTION_SET"]:
        return False
    if (events[0].summary or "") != step.text[:200]:
        return False
    return str(step.responsible_id) == str(a["owner"]) and not step.visibility_override


def _require_unchanged_since_plan(t: TopicPlan) -> None:
    """Just before a REBASE writes: every B its resolution relied on is still B."""
    from app.matters.models import Matter

    now = derived_values(Matter.all_objects.get(pk=t.matter.pk))
    moved = [
        e["field"]
        for e in t.resolution
        if now["commencements" if e["field"] == "commencement_add" else e["field"]] != e["b"]
    ]
    for name in ("next_action_id", "superseded_by"):
        if now[name] != t.relied.get(name):
            moved.append(name)
    if moved:
        raise LovablePilotError(
            f"{t.reference} changed since it was approved ({', '.join(moved)}); nothing was "
            "written. Plan again and review it."
        )


def _write_kind(entry: dict[str, Any]) -> str:
    if entry["field"] == "commencement_add":
        return str(entry["reading"]["target"])
    return REBASE_RECORDS[entry["field"]][0]


def _plan_rebases(plan: LovablePlan, rebases: list[TopicPlan]) -> list[str]:
    """Locate every REBASE's pilot Teema and resolve its changes. Problems name
    references and fields only."""
    if not rebases:
        return []
    from app.matters.models import Matter

    from .lovable_sync_models import LovableSyncState
    from .models import EvidencePilotRecord, MatterSourceReference

    problems: list[str] = []
    holders = _identity_holders(rebases)
    done = {
        str(r.matter_id): r
        for r in EvidencePilotRecord.objects.filter(
            pilot=plan.cohort, package_sha256=plan.digest, record_kind="REBASE"
        )
    }
    for t in rebases:
        ref, uuid = t.reference, str(t.topic["source"]["id"])
        year, number = parse_reference(ref)  # type: ignore[misc]
        found = list(
            Matter.all_objects.select_related("stage").filter(
                reference_year=year, reference_number=number
            )
        )
        if len(found) != 1:
            problems.append(f"{ref}: a REBASE names a pilot Teema; Juristid holds none at {ref}.")
            continue
        m = t.matter = found[0]
        record = done.get(str(m.pk))
        if record is not None:
            # Already rebased by this package: read back what was decided.
            stored = record.evidence or {}
            t.rebased = True
            t.resolution = list(stored.get("resolution") or [])
            t.relied = dict(stored.get("relied") or {})
            t.next_record = bool(stored.get("next_record"))
            continue
        pilot_matter = EvidencePilotRecord.objects.filter(
            pilot=PILOT, matter=m, record_kind="MATTER"
        ).count()
        pilot_refs = list(
            MatterSourceReference.objects.filter(
                matter=m, source_system=SOURCE_SYSTEM, source_parser_version__startswith=f"{PILOT}/"
            )
        )
        pilot_sources = [(s.source_row_raw or {}).get("lovable", {}).get("id") for s in pilot_refs]
        state = LovableSyncState._base_manager.filter(matter=m).first()
        if pilot_matter != 1:
            problems.append(f"{ref}: not a Teema the pilot (lovable-pilot) created; no REBASE.")
            continue
        if pilot_sources != [uuid]:
            problems.append(
                f"{ref}: its pilot source reference does not record this Lovable identity "
                "(exactly one is required)."
            )
            continue
        if state is None or str(state.source_id) != uuid:
            problems.append(
                f"{ref}: its synchronisation state does not hold this Lovable identity."
            )
            continue
        others = (holders.get(ref) or set()) - {m.pk}
        if others:
            names = ", ".join(sorted(_reference_names(others).values()))
            problems.append(f"{ref}: its Lovable identity is also recorded on {names}.")
            continue
        if m.deleted_at is not None:
            problems.append(f"{ref}: the pilot Teema is deleted; nothing is written to it.")
            continue
        a, meta = _pilot_values(m, state, pilot_refs[0])
        problems += _resolve_rebase(plan, t, a, meta, derived_values(m))
    return problems


def _resolve_rebase(
    plan: LovablePlan,
    t: TopicPlan,
    a: dict[str, Any],
    meta: dict[str, Any],
    b: dict[str, Any],
) -> list[str]:
    """The three-way table of docs/adr/0202 §3, one change at a time.

    B counts as A only when Juristid holds exactly what the pilot wrote: never
    for a field whose sync baseline has moved off the pilot's value, and for the
    step only when it is still the pilot's own row, unedited (`_step_is_pilots`)."""
    from app.accounts.models import User
    from app.accounts.selectors import is_assignable_business_user
    from app.legacy_import.lovable_sync import text_value
    from app.matters.initiative import is_koda_initiative
    from app.workflow.models import StageVocabulary

    problems: list[str] = []
    ref = t.reference
    people = plan.package.get("people") or {}
    pilots_step = _step_is_pilots(t.matter, a, meta, b)
    t.relied = {
        "next_action_id": b["next_action_id"],
        "superseded_by": b["superseded_by"],
        "pilots_step": pilots_step,
        "diverged": meta["diverged"],
    }
    today = _today()
    for change in t.topic.get("changes") or []:
        name, reading, decision = change["field"], change.get("reading"), change.get("decision")
        key = (
            str(reading["key"])
            if name == "commencement_add"
            else f"{ref}-{REBASE_RECORDS[name][1]}"
        )
        if reading is None and decision in {KEEP_LOCAL, HOLD} and name != "commencement_add":
            # Kept or held without a reading: nothing to compare, nothing written.
            b_value = b["commencements"] if name == "commencement_add" else b[name]
            t.resolution.append(
                {
                    "field": name,
                    "key": key,
                    "action": decision,
                    "decision": decision,
                    "a": a[name],
                    "b": b_value,
                    "c": None,
                    "reading": None,
                }
            )
            continue
        if name == "title":
            c: Any = text_value(reading)
        elif name == "owner":
            display = people.get(reading, reading)
            users = list(User.objects.filter(display_name=display, is_active=True)[:2])
            if len(users) != 1 or not is_assignable_business_user(users[0]):
                problems.append(f"{ref}: owner is not exactly one assignable active account.")
                continue
            c = str(users[0].pk)
        elif name == "next_action":
            c = _step_value(reading)
        elif name == "stage":
            if not StageVocabulary.objects.filter(key=reading).exists():
                problems.append(f"{ref}: stage {reading!r} is not in the vocabulary.")
                continue
            c = reading
        elif name == "open_request":
            c = str(reading["deadline"])[:10] if reading else None
        else:
            c = _commencement_value(reading["target"], reading.get("kind"), reading.get("date"))
        if name == "commencement_add":
            a_value, b_value = a["commencements"], b["commencements"]
            same_as_a, same_as_b = c in a_value, c in b_value
        else:
            a_value, b_value = a[name], b[name]
            same_as_a, same_as_b = c == a_value, c == b_value
        b_is_a = b_value == a_value and name not in meta["diverged"]
        if name == "next_action":
            b_is_a = b_is_a and pilots_step
        if same_as_a or same_as_b:
            # Nothing to write: the snapshot says what the pilot wrote, or what
            # Juristid already holds (a lawyer's value equal to the source's).
            action = NOTHING
        elif b_is_a:
            action = decision if decision in {KEEP_LOCAL, HOLD, TAKE_SOURCE} else WRITE
        elif decision is None:
            problems.append(
                f"{ref}: {name} was changed in Juristid and in Lovable; the package needs a "
                "decision (KEEP_LOCAL, TAKE_SOURCE or HOLD)."
            )
            continue
        else:
            action = decision
        if action == WRITE and name == "owner":
            problems.append(
                f"{ref}: an owner change moves its steps and notices; the package writes it "
                "only with TAKE_SOURCE."
            )
        if action in WRITING and name == "open_request" and is_koda_initiative(t.matter):
            problems.append(
                f"{ref}: a Chamber initiative takes no new request (docs/adr/0151 §2); "
                "decide HOLD or KEEP_LOCAL."
            )
        if action in WRITING:
            if not t.is_open and name != "title":
                problems.append(
                    f"{ref}: {name} cannot be written on a closed Teema; decide HOLD or KEEP_LOCAL."
                )
            day = None
            if name == "next_action" and (reading or {}).get("date_semantics") == "DEADLINE":
                day = _day((reading or {}).get("target_date"))
            elif name == "open_request":
                day = _day(c)
            if day and day < today:
                problems.append(
                    f"{ref}: {name} {day} is before today; it would be overdue on arrival."
                )
        t.resolution.append(
            {
                "field": name,
                "key": key,
                "action": action,
                "decision": decision,
                "a": a_value,
                "b": b_value,
                "c": c,
                "reading": reading,
            }
        )
    step_written = any(e["field"] == "next_action" for e in t.writes())
    # A NEXT_ACTION record names the rebuild's step: one it wrote, or the pilot's
    # own still untouched. A lawyer's step gets none and stays local.
    t.next_record = t.is_open and (step_written or (b["next_action"] is not None and pilots_step))
    return problems


def _deleted_references(topics: list[TopicPlan]) -> set[str]:
    """The package's references a permanently deleted Matter once held.

    `permanently_delete_matter` removes the row and keeps the reference only in
    its security event; a reference the department has used is never reused.
    """
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    wanted = [t.reference for t in topics]
    return set(
        SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.MATTER_DELETED, detail__reference__in=wanted
        ).values_list("detail__reference", flat=True)
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
            "reserve_through": plan.package.get("reserve_through") or {},
            # Only a package that rebases has this key, so a version-1 (and a
            # CREATE-only) plan hashes exactly as it always did.
            **(
                {
                    "rebase": [
                        {
                            "reference": t.reference,
                            "matter": str(t.matter.pk) if t.matter is not None else "",
                            "rebased": t.rebased,
                            "relied": t.relied,
                            "next_record": t.next_record,
                            "resolution": [
                                {k: entry[k] for k in ("field", "key", "action", "b", "c")}
                                for entry in t.resolution
                            ],
                        }
                        for t in plan.topics
                        if t.action == REBASE
                    ]
                }
                if any(t.action == REBASE for t in plan.topics)
                else {}
            ),
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
# Nothing existing changes, proven per Teema (docs/adr/0202 §4)
# ---------------------------------------------------------------------------

PRESERVED_FORMAT = "juristid.lovable-preserved"
PRESERVED_FORMAT_VERSION = 1


@dataclass(frozen=True)
class _Covered:
    """One kind of record a Teema holds, as the per-Teema digest reads it.

    ``mode`` is what the digest inside the apply transaction takes: ``whole``
    rows where the row is the fact, ``ids`` where the row is an append-only
    record or somebody's own content. ``settled`` is what `preserved --before`
    takes afterwards, outside any import: the nightly synchronisation and
    ordinary reading go on, so what they legitimately move (a state's last
    observation, a run's decisions, a notification being read) is left out
    there, and only there.
    """

    label: str
    model: Any
    column: str
    mode: str = "whole"
    settled: str = "whole"
    filters: tuple[tuple[str, Any], ...] = ()
    settled_drop: frozenset[str] = frozenset()


def _preserved_sources() -> list[_Covered]:
    """Everything hanging off a Teema that the digest covers, one query each."""
    from app.audit.models import ChangeEvent, SecurityAuditEvent
    from app.documents.models import Document
    from app.intelligence.models import (
        MatterEffectiveDate,
        MatterImportantDate,
        MatterWorkVictory,
    )
    from app.matters.models import (
        Entry,
        Matter,
        MatterAssignmentNotice,
        MatterEngagement,
        MatterExternalPosition,
        MatterPersonalNote,
        MatterProceduralDevelopment,
        MatterProceduralLink,
        MatterResponseDeadline,
        MatterSourceOrganisation,
        MatterStageEpisode,
        MatterTimelineStep,
        MatterWebsiteOverview,
        TagAssignment,
    )
    from app.notifications.models import DeadlineNotification
    from app.related_materials.models import (
        MatterBackgroundMaterial,
        MatterRelation,
        RelatedSuggestionDismissal,
    )
    from app.submissions.models import Submission
    from app.workflow.models import MatterPlanStep, NextAction

    from .lovable_sync_models import LovableSyncDecision, LovableSyncState
    from .models import (
        CurrentRegisterState,
        EvidencePilotRecord,
        ExcelPilotImport,
        HistoricalMatchCandidate,
        ImportRowLedger,
        MatterSourcePage,
        MatterSourceReference,
        OpinionArchiveMatterLink,
        OpinionMatchCandidate,
        RegisterEngagementImport,
    )

    m = "matter_id"
    return [
        _Covered("legal_instruments", Matter.legal_instruments.through, m),
        _Covered("policy_areas", Matter.policy_areas.through, m),
        _Covered("collaborators", Matter.collaborators.through, m),
        _Covered("tags", TagAssignment, m),
        _Covered("source_organisations", MatterSourceOrganisation, m),
        # A Matter continuing under this one: a new Teema linking to an existing
        # one changes the existing one's record, even when not its row.
        _Covered(
            "continued_by",
            Matter,
            "superseded_by_id",
            filters=(("superseded_by__isnull", False),),
            mode="ids",
            settled="ids",
        ),
        _Covered("next_actions", NextAction, m),
        _Covered("plan_steps", MatterPlanStep, m),
        _Covered("effective_dates", MatterEffectiveDate, m),
        _Covered("important_dates", MatterImportantDate, m),
        _Covered("work_victories", MatterWorkVictory, m),
        _Covered("response_deadlines", MatterResponseDeadline, m),
        _Covered("stage_episodes", MatterStageEpisode, m),
        _Covered("engagements", MatterEngagement, m),
        _Covered("website_overviews", MatterWebsiteOverview, m),
        _Covered("external_positions", MatterExternalPosition, m),
        _Covered("procedural_developments", MatterProceduralDevelopment, m),
        _Covered("procedural_links", MatterProceduralLink, m),
        _Covered("timeline_steps", MatterTimelineStep, m),
        _Covered("assignment_notices", MatterAssignmentNotice, m),
        _Covered("relations_a", MatterRelation, "matter_a_id"),
        _Covered("relations_b", MatterRelation, "matter_b_id"),
        _Covered("background_materials", MatterBackgroundMaterial, m),
        _Covered("dismissed_suggestions", RelatedSuggestionDismissal, m),
        _Covered("dismissed_as_candidate", RelatedSuggestionDismissal, "candidate_matter_id"),
        _Covered("deadline_notifications", DeadlineNotification, m, settled="ids"),
        _Covered("source_references", MatterSourceReference, m),
        _Covered("register_state", CurrentRegisterState, m),
        _Covered("pilot_records", EvidencePilotRecord, m),
        _Covered("excel_pilot", ExcelPilotImport, m),
        _Covered("source_pages", MatterSourcePage, m, mode="ids", settled="ids"),
        _Covered("row_ledger", ImportRowLedger, m, mode="ids", settled="ids"),
        _Covered("opinion_candidates", OpinionMatchCandidate, m, mode="ids", settled="ids"),
        _Covered("opinion_links", OpinionArchiveMatterLink, m, mode="ids", settled="ids"),
        _Covered("historical_candidates", HistoricalMatchCandidate, m, mode="ids", settled="ids"),
        _Covered(
            "historical_results",
            HistoricalMatchCandidate,
            "resulting_matter_id",
            mode="ids",
            settled="ids",
        ),
        _Covered("engagement_imports", RegisterEngagementImport, m, mode="ids", settled="ids"),
        _Covered(
            "sync_state",
            LovableSyncState,
            m,
            settled_drop=frozenset(
                {"observed", "observed_version", "observed_run_id", "updated_at"}
            ),
        ),
        _Covered("sync_decisions", LovableSyncDecision, "state__matter_id", settled="skip"),
        _Covered("change_events", ChangeEvent, m, mode="ids", settled="ids"),
        _Covered(
            "security_events",
            SecurityAuditEvent,
            "subject_id",
            filters=(("subject_type", "matters.Matter"),),
            mode="ids",
            settled="ids",
        ),
        _Covered("submissions", Submission, m, mode="ids", settled="ids"),
        _Covered("documents", Document, m, mode="ids", settled="ids"),
        _Covered("entries", Entry, m, mode="ids", settled="ids"),
        _Covered("personal_notes", MatterPersonalNote, m, mode="ids", settled="ids"),
    ]


def _digest_value(value: Any) -> Any:
    """A column value in a form `canonical_sha256` takes, whatever its type."""
    if isinstance(value, (bytes, memoryview)):
        return hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (list, tuple)):
        return [_digest_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _digest_value(v) for k, v in value.items()}
    return value


@dataclass
class _PreservedState:
    #: Per Matter pk: the digest the apply transaction compares (everything).
    full: dict[str, str]
    #: The same, less what ordinary activity moves afterwards (`_Covered.settled`).
    settled: dict[str, str]
    #: Per Matter pk: its reference, to name it; never its title.
    references: dict[str, str]
    #: Per rebased Matter pk (`local_for`): the local digest, everything the rebase
    #: never writes (docs/adr/0202 §4) — in the transaction's scope and settled.
    local: dict[str, str] = field(default_factory=dict)
    local_settled: dict[str, str] = field(default_factory=dict)


#: The Matter columns each REBASE field's service writes. A rebased Teema's local
#: digest leaves out only the columns of the fields it actually writes (and
#: `updated_at` when it writes anything).
REBASE_MATTER_COLUMNS: dict[str, frozenset[str]] = {
    "title": frozenset({"title"}),
    "owner": frozenset({"owner_id"}),
    "stage": frozenset({"stage_id"}),
    "open_request": frozenset({"response_deadline", "response_requested_at"}),
    "next_action": frozenset(),
    "commencement_add": frozenset(),
}
#: Related tables a rebase may add rows to (steps, periods, request history,
#: dates, its source reference and records, notices); a row created at or after
#: the apply's own moment (the spec's ``cutoff``) is the rebase's, every older
#: row stays covered.
REBASE_NEW_ROW_LABELS = frozenset(
    {
        "next_actions",
        "stage_episodes",
        "response_deadlines",
        "effective_dates",
        "important_dates",
        "source_references",
        "pilot_records",
        "assignment_notices",
    }
)


def local_spec(*, fields: Any, step: Any, episode: Any, cutoff: Any) -> dict[str, Any]:
    """What a rebased Teema's local digest leaves out, as JSON: the fields it writes,
    the B step a step write supersedes, the current stage period a stage write
    ends, and the moment from which rows are the rebase's own."""
    return {
        "fields": sorted(fields),
        "step": str(step) if step else None,
        "episode": str(episode) if episode else None,
        "cutoff": cutoff.isoformat() if hasattr(cutoff, "isoformat") else cutoff,
    }


def _local_row(
    label: str, ident: str, value: dict[str, Any], spec: dict[str, Any]
) -> tuple[bool, frozenset[str]]:
    """Whether a related row of a rebased Teema is in its local digest, and which of
    its columns are not: exactly the rows and columns its declared writes change."""
    from app.workflow.enums import ActionStatus

    fields = set(spec.get("fields") or ())
    cutoff = spec.get("cutoff")
    created = value.get("created_at")
    if label in REBASE_NEW_ROW_LABELS and cutoff and created is not None:
        moment = dt.datetime.fromisoformat(cutoff) if isinstance(cutoff, str) else cutoff
        if created >= moment:
            return False, frozenset()
    if label == "next_actions":
        if "next_action" in fields and ident == spec.get("step"):
            return False, frozenset()  # the B step a step write supersedes
        if "owner" in fields and value.get("status") in {ActionStatus.OPEN, ActionStatus.PLANNED}:
            # `assign_matter` hands the open and planned steps to the new owner.
            return True, frozenset({"responsible_id", "updated_at"})
    if label == "stage_episodes" and "stage" in fields and ident == spec.get("episode"):
        return False, frozenset()  # the current period a stage write ends
    if label == "assignment_notices" and "owner" in fields:
        return False, frozenset()  # `assign_matter` retires the previous owner's notice
    return True, frozenset()


def _preserved_state(
    exclude_matter_ids: Any = None, *, local_for: Any = None, drop_ids: Any = None
) -> _PreservedState:
    """Per Matter: the full and settled digests; for each Matter of ``local_for``
    also its local digest (``drop_ids``: Matters of the package that may continue
    under it, which a rebased Teema's local digest does not count)."""
    from app.matters.models import Matter

    exclude = {str(pk) for pk in exclude_matter_ids or ()}
    specs: dict[str, dict[str, Any]] = (
        {str(pk): dict(spec) for pk, spec in local_for.items()}
        if isinstance(local_for, dict)
        else {str(pk): {} for pk in local_for or ()}
    )
    local_owners = set(specs)
    dropped = {str(pk) for pk in drop_ids or ()}
    columns = [f.attname for f in Matter._meta.concrete_fields]
    full: dict[str, dict[str, Any]] = {}
    settled: dict[str, dict[str, Any]] = {}
    local: dict[str, dict[str, Any]] = {}
    local_settled: dict[str, dict[str, Any]] = {}
    references: dict[str, str] = {}
    key = Matter._meta.pk.attname
    for row in Matter.all_objects.order_by("pk").values(*columns):
        pk = str(row[key])
        if pk in exclude:
            continue
        references[pk] = f"{row['reference_year']}_{row['reference_number']}"
        matter = {k: _digest_value(v) for k, v in row.items()}
        full[pk] = {"matter": matter}
        settled[pk] = {"matter": matter}
        if pk in local_owners:
            written = set(specs[pk].get("fields") or ())
            left_out = {c for name in written for c in REBASE_MATTER_COLUMNS.get(name, ())}
            if written:
                left_out.add("updated_at")
            kept = {k: v for k, v in matter.items() if k not in left_out}
            local[pk] = {"matter": kept}
            local_settled[pk] = {"matter": kept}
    for covered in _preserved_sources():
        model = covered.model
        whole = "whole" in (covered.mode, covered.settled)
        names = [f.attname for f in model._meta.concrete_fields] if whole else []
        names = [name for name in names if name != covered.column]
        human = covered.label == "change_events"
        if human and "actor_id" not in names:
            names.append("actor_id")
        manager: Any = Matter.all_objects if model is Matter else model._base_manager
        query = manager.filter(**dict(covered.filters)).order_by("pk")
        for row in query.values(covered.column, "pk", *names):
            owner = str(row.pop(covered.column))
            if owner not in full:
                continue
            ident = str(row.pop("pk"))
            actor = row.pop("actor_id", None) if human else None
            value = {k: _digest_value(v) for k, v in row.items()}
            targets = [
                (full, covered.mode, frozenset()),
                (settled, covered.settled, covered.settled_drop),
            ]
            if owner in local_owners:
                if human:
                    keep, unwritten = actor is not None, frozenset[str]()  # a person's events only
                elif covered.label == "continued_by":
                    keep, unwritten = ident not in dropped, frozenset[str]()
                else:
                    keep, unwritten = _local_row(covered.label, ident, row, specs[owner])
                if keep:
                    targets += [
                        (local, covered.mode, unwritten),
                        (local_settled, covered.settled, covered.settled_drop | unwritten),
                    ]
            for target, mode, drop in targets:
                if mode == "skip":
                    continue
                entry: Any = (
                    ident if mode == "ids" else {k: v for k, v in value.items() if k not in drop}
                )
                target[owner].setdefault(covered.label, []).append(entry)
    return _PreservedState(
        full={pk: canonical_sha256(value) for pk, value in full.items()},
        settled={pk: canonical_sha256(value) for pk, value in settled.items()},
        references=references,
        local={pk: canonical_sha256(value) for pk, value in local.items()},
        local_settled={pk: canonical_sha256(value) for pk, value in local_settled.items()},
    )


def preserved_digests(*, exclude_matter_ids: Any = None, settled: bool = False) -> dict[str, str]:
    """One SHA-256 per pre-existing Matter (deleted ones included) over the Matter
    row and everything hanging off it (`_preserved_sources`): instruments,
    Valdkond, collaborators, tags, senders, the Teemad continuing under it,
    steps, plan steps, dates, victories, deadlines, stage periods, engagements,
    positions, developments, links, timeline, notices, related materials,
    notifications, source and register records, pilot records, synchronisation
    state and decisions, and the identities of its events, submissions,
    documents, entries and personal notes. A fixed number of queries, whatever
    the number of Matters.

    ``settled`` leaves out what the nightly synchronisation and ordinary reading
    move after an import (a state's last observation, a run's decisions, a
    notification being read): what `preserved --before` compares afterwards.
    """
    state = _preserved_state(exclude_matter_ids)
    return state.settled if settled else state.full


def local_digests(specs: Any, *, drop_ids: Any = None, settled: bool = False) -> dict[str, str]:
    """A rebased Teema's local digest (docs/adr/0202 §4): Valdkond, collaborators,
    tags, documents, entries, notes, submissions, every change event a person
    wrote and every related row the rebase never writes, and the Matter row less
    the columns the rebase's services write."""
    state = _preserved_state(local_for=specs, drop_ids=drop_ids)
    return state.local_settled if settled else state.local


def preserved_document(
    digests: dict[str, str],
    references: dict[str, str],
    *,
    local: dict[str, str] | None = None,
    declared: dict[str, dict[str, Any]] | None = None,
    created: list[str] | None = None,
) -> dict[str, Any]:
    """What `apply --preserved-out` writes and `preserved --before` reads: the
    digests of every Teema the package did not touch, the local digests of the
    rebased ones with a digest of the values the rebase left (``declared``), the
    references that name them, and the SHA-256 of it all."""
    local = local or {}
    named = sorted(set(digests) | set(local))
    body = {
        "format": PRESERVED_FORMAT,
        "format_version": PRESERVED_FORMAT_VERSION,
        # The after-the-fact digests (`preserved_digests(settled=True)`).
        "scope": "settled",
        "matters": dict(sorted(digests.items())),
        "references": {pk: references.get(pk, "") for pk in named},
        "local": dict(sorted(local.items())),
        "declared": dict(sorted((declared or {}).items())),
        # The package's new Teemad, which may continue under a rebased one.
        "created": sorted(created or []),
    }
    return {**body, "sha256": canonical_sha256(body)}


def preserved_snapshot() -> dict[str, Any]:
    """`preserved --snapshot-out`: every Teema's after-the-fact digest now, in the
    `preserved_document` format, applying nothing. Taken at a backup, it proves a
    restore of that backup later (`preserved --before` against the restored copy)."""
    state = _preserved_state()
    return preserved_document(state.settled, state.references)


def read_preserved_document(document: Any) -> tuple[dict[str, str], dict[str, str]]:
    body = _checked_preserved_body(document)
    return dict(body["matters"]), dict(body.get("references") or {})


def _checked_preserved_body(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("format") != PRESERVED_FORMAT:
        raise LovablePilotError(f"The file is not a {PRESERVED_FORMAT} document.")
    body = {k: v for k, v in document.items() if k != "sha256"}
    if document.get("format_version") != PRESERVED_FORMAT_VERSION:
        raise LovablePilotError(f"format_version {document.get('format_version')!r} is unknown.")
    if document.get("scope") != "settled":
        raise LovablePilotError("The digest file does not hold the after-the-fact digests.")
    if canonical_sha256(body) != document.get("sha256"):
        raise LovablePilotError("The digest file's sha256 does not match its content.")
    return body


@dataclass
class PreservedComparison:
    checked: int = 0
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    #: Rebased Teemad: how many were checked, whose local digest differs, and
    #: whose Lovable-derived values are not exactly what the rebase left.
    local_checked: int = 0
    local_changed: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.changed or self.missing or self.added or self.local_changed or self.undeclared
        )

    def summary(self) -> str:
        parts = [
            f"{label}: {', '.join(sorted(refs))}"
            for label, refs in (
                ("changed", self.changed),
                ("missing", self.missing),
                ("appeared", self.added),
                ("local work changed", self.local_changed),
                ("undeclared changes", self.undeclared),
            )
            if refs
        ]
        return "; ".join(parts)


def compare_preserved(
    before: dict[str, str],
    after: dict[str, str],
    references: dict[str, str],
    *,
    new_allowed: bool = True,
) -> PreservedComparison:
    """Every Matter of `before` against `after`, named by reference only."""
    result = PreservedComparison(checked=len(before))
    for pk, digest in before.items():
        name = references.get(pk) or pk
        if pk not in after:
            result.missing.append(name)
        elif after[pk] != digest:
            result.changed.append(name)
    if not new_allowed:
        result.added = [references.get(pk) or pk for pk in after if pk not in before]
    return result


def check_preserved(document: Any) -> PreservedComparison:
    """`lovable_pilot preserved --before`: the digest file against the database now,
    outside any import. Matters created since are not in the file and not judged.

    Every Teema the package did not touch must hash as it did; every rebased one
    must keep its local digest and hold exactly the Lovable-derived values the
    rebase left (docs/adr/0202 §4)."""
    from app.matters.models import Matter

    body = _checked_preserved_body(document)
    before, references = dict(body["matters"]), dict(body.get("references") or {})
    local = dict(body.get("local") or {})
    declared = dict(body.get("declared") or {})
    specs = {pk: (declared.get(pk) or {}).get("local_spec") or {} for pk in local}
    now = _preserved_state(local_for=specs, drop_ids=body.get("created") or [])
    names: dict[str, str] = {**now.references, **references}
    result = compare_preserved(before, now.settled, names)
    result.local_checked = len(local)
    result.local_changed = [
        names.get(pk) or pk for pk, digest in local.items() if now.local_settled.get(pk) != digest
    ]
    present = {str(m.pk): m for m in Matter.all_objects.filter(pk__in=list(declared))}
    result.undeclared = [
        names.get(pk) or pk
        for pk, entry in declared.items()
        if pk not in present
        or canonical_sha256(derived_values(present[pk])) != entry.get("values_sha256")
    ]
    return result


# ---------------------------------------------------------------------------
# Source drift (docs/adr/0202 §5)
# ---------------------------------------------------------------------------

DRIFT_COLUMNS = ("id", "nr", "version", "updated_at")


def source_drift(package: dict[str, Any], transport: Any) -> list[dict[str, Any]]:
    """Every package topic Lovable changed, renumbered or no longer shows since the
    snapshot. Reads `topic` (``id, nr, version, updated_at`` only) through the
    GET-only snapshot path, in chunks; never writes, never reads a value."""
    from app.legacy_import import lovable_source

    expected: dict[str, tuple[str, Any]] = {}
    for topic in package.get("topics") or []:
        source = topic.get("source") or {}
        if source.get("id"):
            expected[str(source["id"])] = (str(topic.get("reference") or ""), source.get("version"))
    seen: dict[str, dict[str, Any]] = {}
    for chunk in lovable_source._chunks(sorted(expected)):
        for row in lovable_source._select(
            transport,
            "topic",
            DRIFT_COLUMNS,
            filters={"id": lovable_source._in(chunk)},
            order="id",
        ):
            seen[str(row.get("id"))] = row
    drift: list[dict[str, Any]] = []
    for source_id in sorted(expected, key=lambda i: expected[i][0]):
        reference, version = expected[source_id]
        found = seen.get(source_id)
        if found is None:
            drift.append({"reference": reference, "source_id": source_id, "kinds": ["MISSING"]})
            continue
        kinds = []
        if found.get("version") != version:
            kinds.append("VERSION")
        if str(found.get("nr") or "") != reference:
            kinds.append("RENUMBERED")
        if kinds:
            drift.append(
                {
                    "reference": reference,
                    "source_id": source_id,
                    "kinds": kinds,
                    "package_version": version,
                    "source_version": found.get("version"),
                    "source_nr": found.get("nr"),
                    "source_updated_at": found.get("updated_at"),
                }
            )
    return drift


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@dataclass
class ApplyReport:
    already_applied: bool = False
    dry_run: bool = False
    batch_id: Any = None
    cohort: str = ""
    written: dict[str, int] = field(default_factory=dict)
    matters_before: int = 0
    matters_after: int = 0
    fingerprint_before: str = ""
    fingerprint_after: str = ""
    #: Per pre-existing Matter, taken inside the transaction before the writes;
    #: the same digests after the writes matched them, or nothing was written.
    preserved_before: dict[str, str] = field(default_factory=dict)
    preserved_references: dict[str, str] = field(default_factory=dict)
    #: The same moment's after-the-fact digests, for `apply --preserved-out`.
    preserved_settled_before: dict[str, str] = field(default_factory=dict)
    #: Rebased Teemad: their local digests (settled), and per pk the fields the
    #: rebase declared and a digest of the Lovable-derived values it left.
    preserved_local_before: dict[str, str] = field(default_factory=dict)
    #: The same local digests in the transaction's scope, and what each leaves out.
    local_before: dict[str, str] = field(default_factory=dict)
    local_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    declared: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_ids: list[str] = field(default_factory=list)
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
    if operator_intent != plan.cohort:
        raise LovablePilotError(f"Type --operator-intent {plan.cohort} to apply the package.")
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

    with _no_synchronisation_run(), transaction.atomic():
        current = _hold_and_replan(plan, expect_plan_sha256)
        if current.database_state == "applied":  # another apply won the lock
            return ApplyReport(already_applied=True)
        _require_writable(current, expect_plan_sha256)
        writer = _Writer(current, backup_set=backup_set.strip(), plan_sha256=expect_plan_sha256)
        report, touched = writer.write()
        refresh_matters(indexable_matters().filter(pk__in=touched))
        # Read back before the commit: a difference rolls everything back.
        report.verify = verify_applied(build_plan(plan.package))
        if not report.verify.ok:
            raise LovablePilotError(
                "The written records do not match the package; nothing was written:\n  "
                + "\n  ".join(report.verify.discrepancies)
            )
    return report


@contextlib.contextmanager
def _no_synchronisation_run() -> Iterator[None]:
    """The synchronisation's own run lock, held for the whole apply: a nightly run
    never reads or writes a Teema the rebuild is changing, and an apply never
    starts while one is running."""
    from app.legacy_import.lovable_sync import SyncRefused, run_lock

    stack = contextlib.ExitStack()
    try:
        stack.enter_context(run_lock())
    except SyncRefused as error:
        raise LovablePilotError(
            "a synchronisation run is in progress; nothing was written. Apply when it has finished."
        ) from error
    with stack:
        yield


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
    from app.search.indexing import indexable_matters, refresh_matters

    try:
        with _no_synchronisation_run(), transaction.atomic():
            current = _hold_and_replan(plan, plan_digest(plan))
            _require_writable(current, plan_digest(plan))
            writer = _Writer(current, backup_set="dry-run", plan_sha256=plan_digest(plan))
            report, touched = writer.write()
            refresh_matters(indexable_matters().filter(pk__in=touched))
            report.verify = verify_applied(build_plan(plan.package))
            raise _RolledBack(report)
    except _RolledBack as rolled:
        rolled.report.dry_run = True
        return rolled.report


#: How long the apply waits for a lock before giving up, rather than queueing
#: every lawyer's save behind it.
LOCK_TIMEOUT = "10s"


def _hold_and_replan(plan: LovablePlan, expect_plan_sha256: str) -> LovablePlan:
    """Lock out other Matter writers, then re-read the database under the lock.

    **Lock order.** Native creation locks the year's `MatterReferenceSequence`
    row and then inserts a Matter; taking the table lock first and the sequence
    afterwards would be the opposite order and could deadlock a lawyer creating
    a Teema mid-import. So the sequence rows for every year this package writes
    or reserves are locked first, then the table, each with a timeout.
    """
    from django.db import connection

    from app.matters.models import Matter, MatterReferenceSequence

    years = {parse_reference(t.reference)[0] for t in plan.topics}  # type: ignore[index]
    years |= {int(y) for y in (plan.package.get("reserve_through") or {})}
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for year in sorted(years):
        MatterReferenceSequence.objects.get_or_create(year=year)
    list(
        MatterReferenceSequence.objects.select_for_update().filter(year__in=years).order_by("year")
    )
    # Every Teema a REBASE writes, row by row and in one order, before the table:
    # a lawyer's save on it (which locks the row first) waits for the apply, so
    # the B the re-plan reads is the B the writer writes over.
    rebased = [
        parse_reference(str(t.get("reference") or ""))
        for t in plan.package.get("topics") or []
        if topic_action(t) == REBASE
    ]
    if rebased:
        from django.db.models import Q

        query = Q()
        for year, number in [pair for pair in rebased if pair]:
            query |= Q(reference_year=year, reference_number=number)
        list(
            Matter.all_objects.select_for_update(no_key=True)
            .filter(query)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    with connection.cursor() as cursor:
        cursor.execute(f'LOCK TABLE "{Matter._meta.db_table}" IN SHARE ROW EXCLUSIVE MODE')
    return build_plan(plan.package)


def _require_writable(current: LovablePlan, expect_plan_sha256: str) -> None:
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


class _Writer:
    """Everything `apply_plan` writes, in the order the services require."""

    def __init__(self, plan: LovablePlan, *, backup_set: str, plan_sha256: str) -> None:
        self.plan = plan
        self.package = plan.package
        self.backup_set = backup_set
        self.plan_sha256 = plan_sha256
        self.cohort = plan.cohort
        self.version = COHORT_VERSIONS[self.cohort]
        self.format_version = int(self.package.get("format_version") or PACKAGE_FORMAT_VERSION)
        self.report = ApplyReport(cohort=self.cohort)
        self.base: dict[str, Any] = {
            "source": PROVENANCE_SOURCE,
            "pilot": self.cohort,
            "pilot_version": self.version,
            "package_sha256": plan.digest,
        }
        self.decisions = "docs/adr/0191"
        if self.cohort != PILOT:
            # The pilot's provenance stays exactly what version 1 always wrote.
            self.decisions = "docs/adr/0191, docs/adr/0202"
            self.base["cohort"] = self.cohort
            self.base["format_version"] = self.format_version

    def remember(self, *, matter: Any, key: str, kind: str, target: Any, evidence: Any) -> None:
        from .models import EvidencePilotRecord

        EvidencePilotRecord.objects.create(
            pilot=self.cohort,
            pilot_version=self.version,
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
        rebases = [t for t in self.plan.topics if t.action == REBASE]
        rebased_ids = [str(t.matter.pk) for t in rebases]
        self.report.matters_before = Matter.all_objects.count()
        # The rebased Teemad are judged by their local digest, not by their row.
        self.report.fingerprint_before = existing_fingerprint(
            exclude_references={t.reference for t in rebases}
        )
        from app.matters.models import MatterStageEpisode

        cutoff = timezone.now()
        self.report.local_specs = {
            str(t.matter.pk): local_spec(
                fields={e["field"] for e in t.writes()},
                step=t.relied.get("next_action_id"),
                episode=MatterStageEpisode.objects.filter(matter=t.matter, is_current=True)
                .values_list("pk", flat=True)
                .first(),
                cutoff=cutoff,
            )
            for t in rebases
        }
        state = _preserved_state(local_for=self.report.local_specs)
        before = {pk: v for pk, v in state.full.items() if pk not in rebased_ids}
        names = state.references
        self.report.preserved_before, self.report.preserved_references = before, names
        self.report.preserved_settled_before = {
            pk: v for pk, v in state.settled.items() if pk not in rebased_ids
        }
        self.report.preserved_local_before = state.local_settled
        self.report.local_before = state.local
        derived_before = {
            str(t.matter.pk): derived_values(Matter.all_objects.get(pk=t.matter.pk))
            for t in rebases
        }
        snapshot = self.package["snapshot"]
        self.batch = ImportBatch.objects.create(
            source_system=SOURCE_SYSTEM,
            source_file_name=SOURCE_TABLE,
            source_snapshot_sha256=snapshot["snapshot_sha256"],
            importer_version=f"{self.cohort}/{self.version}",
            contract_version=f"{PACKAGE_FORMAT}/{self.format_version}",
            started_at=timezone.now(),
            source_row_count=len(self.plan.topics),
            reconciliation_status=ReconciliationStatus.RUNNING,
            notes=(
                f"{self.cohort}: {len(self.plan.topics)} teemat Lovable'i hetktõmmisest "
                f"{snapshot['snapshot_sha256']} (seis {snapshot.get('as_of')}); pakett "
                f"{self.plan.digest}; plan-sha256 {self.plan_sha256}; varukoopia "
                f"{self.backup_set}; teemasid enne {self.report.matters_before} "
                f"({self.decisions})."
            ),
        )
        self.report.batch_id = self.batch.pk

        created: dict[str, Any] = {}
        for topic_plan in self.plan.topics:
            if topic_plan.action == CREATE:
                created[topic_plan.reference] = self.write_topic(topic_plan)
        # The rebased Teemad after the new ones, so a new predecessor's link to a
        # rebased successor resolves; the rebased Teema itself is not linked.
        rebased: dict[str, Any] = {}
        for topic_plan in rebases:
            _require_unchanged_since_plan(topic_plan)
            rebased[topic_plan.reference] = self.write_rebase(topic_plan)
        linkable = {**created, **rebased}

        # Successors exist now; only a successor inside this package is linked,
        # and only the new predecessor's row changes.
        for topic_plan in self.plan.topics:
            if topic_plan.successor_in_batch:
                matter = created[topic_plan.reference]
                matter.superseded_by = linkable[topic_plan.successor_in_batch]
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
        created_ids = [m.pk for m in created.values()]
        after = _preserved_state(
            exclude_matter_ids=created_ids,
            local_for=self.report.local_specs,
            drop_ids=created_ids,
        )
        untouched = {pk: v for pk, v in after.full.items() if pk not in rebased_ids}
        comparison = compare_preserved(before, untouched, names, new_allowed=False)
        comparison.local_changed = [
            names[pk] for pk, digest in state.local.items() if after.local.get(pk) != digest
        ]
        comparison.undeclared = self._undeclared(rebases, derived_before)
        if not comparison.ok:
            raise LovablePilotError(
                "A Teema that was already in Juristid, or a record of one, changed during the "
                f"import; nothing was written ({comparison.summary()})."
            )
        self.report.declared = {
            str(t.matter.pk): {
                "reference": t.reference,
                "fields": sorted(_declared_fields(t)),
                "local_spec": self.report.local_specs[str(t.matter.pk)],
                # A digest, never the values: they are the Teema's title and step
                # wording — a lawyer's own, where the rebase kept it — and the file
                # names Teemad by reference only.
                "values_sha256": canonical_sha256(
                    derived_values(Matter.all_objects.get(pk=t.matter.pk))
                ),
            }
            for t in rebases
        }
        self.report.created_ids = [str(pk) for pk in created_ids]
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
        if rebases:
            self.batch.notes += f" Pilootteemasid viidud hetktõmmisele {len(rebases)}."
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
                **({"rebased": len(rebases)} if rebases else {}),
                "backup_set": self.backup_set,
                "written": self.report.written,
                "matters_before": self.report.matters_before,
                "matters_after": self.report.matters_after,
            },
        )
        return self.report, [m.pk for m in [*created.values(), *rebased.values()]]

    def _undeclared(self, rebases: list[TopicPlan], before: dict[str, dict[str, Any]]) -> list[str]:
        """Rebased Teemad whose Lovable-derived values changed other than exactly as
        the plan declared."""
        from app.matters.models import Matter

        out = []
        for t in rebases:
            now = derived_values(Matter.all_objects.get(pk=t.matter.pk))
            was = before[str(t.matter.pk)]
            changed = {name for name in _DERIVED_FIELDS if now[name] != was[name]}
            if changed != _declared_fields(t):
                out.append(t.reference)
        return out

    def write_rebase(self, topic_plan: TopicPlan) -> Any:
        """Bring one pilot Teema to the snapshot: every WRITE through its service,
        then a new canonical source reference and the records that say so."""
        from app.accounts.models import User
        from app.matters.enums import ResponseDeadlineChange, StageEpisodeOrigin
        from app.matters.models import Matter
        from app.matters.response_deadlines import (
            change_response_deadline,
            request_response_deadline,
        )
        from app.matters.services import assign_matter, change_stage, set_matter_title
        from app.workflow.enums import ActionStatus
        from app.workflow.models import NextAction, StageVocabulary
        from app.workflow.services import set_next_action

        matter = Matter.all_objects.get(pk=topic_plan.matter.pk)
        ref = topic_plan.reference
        provenance = {
            "source": REBUILD_PROVENANCE,
            "cohort": self.cohort,
            "package_sha256": self.plan.digest,
            "plan": self.plan_sha256,
            "reference": ref,
        }
        order = {name: index for index, name in enumerate(REBASE_FIELDS)}
        step_written = None
        for entry in sorted(topic_plan.writes(), key=lambda e: order[e["field"]]):
            name, reading, c = entry["field"], entry["reading"], entry["c"]
            target: Any = matter
            extra: dict[str, Any] = {}
            if name == "title":
                set_matter_title(
                    matter=matter, value=str(reading), actor=None, provenance=provenance
                )
            elif name == "owner":
                owner = User.objects.get(pk=c, is_active=True)
                assign_matter(matter=matter, owner=owner, actor=None, provenance=provenance)
                target = owner
            elif name == "stage":
                extra = {"from": matter.stage.key if matter.stage is not None else "", "to": c}
                change_stage(
                    matter=matter,
                    stage=StageVocabulary.objects.get(key=c),
                    actor=None,
                    origin=StageEpisodeOrigin.IMPORTED,
                )
            elif name == "open_request":
                day = _day(c)
                if day is None:  # `_resolve_rebase` never writes an empty request
                    raise LovablePilotError(f"{ref}: the open request has no date.")
                if matter.response_deadline is None:
                    request_response_deadline(matter=matter, deadline=day, actor=None)
                else:
                    change_response_deadline(
                        matter=matter, deadline=day, actor=None, change=ResponseDeadlineChange.MOVED
                    )
            elif name == "next_action":
                matter.refresh_from_db()
                step_written = target = set_next_action(
                    matter=matter,
                    text=str(reading["text"]).strip(),
                    kind=reading["kind"],
                    date_semantics=reading["date_semantics"],
                    target_date=_day(reading.get("target_date")),
                    date_precision=reading.get("date_precision") or "EXACT",
                    source_text=reading.get("source_text") or reading["text"],
                    responsible=matter.owner,
                    actor=None,
                    provenance={
                        **provenance,
                        "treatment": reading.get("treatment", ""),
                        "kind_basis": "REVIEWED_PACKAGE",
                    },
                )
                continue  # its record is the Teema's NEXT_ACTION record, below
            else:
                self.write_commencement(matter, topic_plan, reading)
                continue  # `write_commencement` keeps its own record
            kind, _suffix = REBASE_RECORDS[name]
            self.remember(
                matter=matter,
                key=entry["key"],
                kind=kind,
                target=target,
                evidence={
                    "action": entry["action"],
                    "decision": entry["decision"],
                    "reason": _change_reason(topic_plan, name),
                    **extra,
                },
            )
            matter.refresh_from_db()

        matter.refresh_from_db()
        year, number = parse_reference(ref)  # type: ignore[misc]
        source = self._source_reference(matter, topic_plan.topic, year, number)
        if topic_plan.next_record:
            step = step_written or (
                NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
                .order_by("created_at")
                .first()
            )
            self.remember(
                matter=matter,
                key=f"{ref}-NEXT",
                kind="NEXT_ACTION",
                target=step,
                evidence={
                    "written": step_written is not None,
                    "reason": _change_reason(topic_plan, "next_action"),
                    "source_text": str((topic_plan.topic["source"]["row"]).get("jargmiseks") or ""),
                },
            )
        self.remember(
            matter=matter,
            key=ref,
            kind="REBASE",
            target=source,
            evidence={
                "lovable_id": topic_plan.topic["source"]["id"],
                "lovable_version": topic_plan.topic["source"].get("version"),
                "resolution": topic_plan.resolution,
                "relied": topic_plan.relied,
                "next_record": topic_plan.next_record,
            },
        )
        return matter

    def _source_reference(self, matter: Any, topic: dict[str, Any], year: int, number: int) -> Any:
        """The canonical source reference of a Teema the package wrote or rebased:
        the snapshot's row exactly as read, the reviewer's decisions, and the
        OneNote link byte for byte."""
        from app.legacy_import.enums import OneNoteContentStatus
        from app.legacy_import.models import MatchMethod, MatterSourceReference

        source = topic["source"]
        snapshot = self.package["snapshot"]
        url = topic.get("onenote_url") or ""
        return MatterSourceReference.objects.create(
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
                # Version 2 keeps Lovable's structured stage links as read.
                **(
                    {"stage_links": source.get("stage_links") or []}
                    if self.format_version == ACTIVE_FORMAT_VERSION
                    else {}
                ),
            },
            source_title=topic["title"],
            source_date_raw=str(source["row"].get("sisse") or "")[:200],
            onenote_url=url,
            onenote_content_status=(
                OneNoteContentStatus.NOT_IMPORTED if url else OneNoteContentStatus.NOT_APPLICABLE
            ),
            source_era=str(year),
            source_contract_version=f"{PACKAGE_FORMAT}/{self.format_version}",
            # The parser only proposed; a person decided (the package's
            # `mapping_version` names the review). 50 characters, so the short form.
            source_parser_version=f"{self.cohort}/{self.version}+reviewed",
            match_method=MatchMethod.REFERENCE_TOKEN,
            conflict_state="NONE",
            reviewed_by=str(self.package.get("reviewer") or "")[:200],
        )

    def write_topic(self, topic_plan: TopicPlan) -> Any:
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
        self._source_reference(matter, topic, year, number)
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


def _verify_rebase(plan: LovablePlan, topic_plan: TopicPlan, m: Any) -> list[str]:
    """A rebased Teema against its resolved plan: every write is now C, everything
    else is still B, the continuation and a kept step untouched, the snapshot's
    source reference there."""
    from app.legacy_import.models import MatterSourceReference

    out: list[str] = []
    ref = topic_plan.reference
    if m.deleted_at is not None:
        out.append(f"{ref}: the Matter is deleted.")
    marked = m.evidence_pilot_records.filter(
        pilot=plan.cohort, package_sha256=plan.digest, record_kind="REBASE"
    ).count()
    if marked != 1:
        out.append(f"{ref}: not marked as rebased by this package.")
    now = derived_values(m)
    dated: list[Any] | None = None
    for entry in topic_plan.resolution:
        name = entry["field"]
        writes = entry["action"] in WRITING
        if name == "commencement_add":
            dated = list(entry["b"]) if dated is None else dated
            if writes and entry["c"] not in dated:
                dated.append(entry["c"])
            continue
        wanted = entry["c"] if writes else entry["b"]
        if now[name] != wanted:
            out.append(f"{ref}: {name} is not what the plan left ({entry['action']}).")
    if dated is not None and now["commencements"] != sorted(
        dated, key=lambda d: (d[0], d[1], d[2] or "")
    ):
        out.append(f"{ref}: commencements are not what the plan left.")
    relied = topic_plan.relied
    if now["superseded_by"] != relied.get("superseded_by"):
        out.append(f"{ref}: its continuation changed; a rebase never touches it.")
    step_written = any(e["field"] == "next_action" for e in topic_plan.writes())
    if not step_written and now["next_action_id"] != relied.get("next_action_id"):
        out.append(f"{ref}: its current step changed; the plan kept it.")
    sources = list(
        MatterSourceReference.objects.filter(
            matter=m,
            source_system=SOURCE_SYSTEM,
            source_snapshot_sha256=plan.package["snapshot"]["snapshot_sha256"],
            source_parser_version__startswith=f"{plan.cohort}/",
        )
    )
    if len(sources) != 1:
        out.append(f"{ref}: {len(sources)} snapshot source references, not 1.")
    else:
        source = sources[0]
        if (source.source_row_raw.get("lovable") or {}).get("id") != topic_plan.topic["source"][
            "id"
        ]:
            out.append(f"{ref}: Lovable identity not recorded.")
        if source.onenote_url != (topic_plan.topic.get("onenote_url") or ""):
            out.append(f"{ref}: OneNote link not preserved exactly.")
    return out


def verify_applied(plan: LovablePlan) -> VerifyReport:
    """Every topic against what the database holds. Read-only; fails on any difference."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
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
        if topic_plan.action == REBASE:
            out.extend(_verify_rebase(plan, topic_plan, m))
            continue
        marked = m.evidence_pilot_records.filter(
            pilot=plan.cohort, package_sha256=plan.digest, record_kind="MATTER"
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
        if m.deleted_at is not None:
            out.append(f"{ref}: the Matter is deleted.")
        wanted_mode = "FULL" if topic_plan.is_open else "ARCHIVE"
        if m.record_mode != wanted_mode:
            out.append(f"{ref}: record mode {m.record_mode!r} ≠ {wanted_mode!r}.")
        if (m.track == "KODA_INITIATIVE") != bool(topic.get("koda_initiative")):
            out.append(f"{ref}: menetlusliik differs.")
        if m.received_date != _day(topic.get("received_date")):
            out.append(f"{ref}: Saabus differs.")
        if m.addressee_organisation_id != (
            topic_plan.addressee.pk if topic_plan.addressee else None
        ):
            out.append(f"{ref}: addressee differs.")
        if ChangeEvent.objects.filter(matter=m, event_type=ChangeEventType.MATTER_CLOSED).exists():
            out.append(f"{ref}: a closure event exists; none happened in Juristid.")
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
        effective = sorted(
            (e.kind, e.date_value) for e in MatterEffectiveDate.objects.filter(matter=m)
        )
        important = sorted(
            (i.kind, i.date_value) for i in MatterImportantDate.objects.filter(matter=m)
        )
        wanted_effective = sorted(
            (r["kind"], _day(r.get("date"))) for r in ready if r["target"] == T_EFFECTIVE_DATE
        )
        wanted_important = sorted(
            (r["kind"], _day(r.get("date"))) for r in ready if r["target"] == T_IMPORTANT_DATE
        )
        if effective != wanted_effective:
            out.append(f"{ref}: effective dates {effective} ≠ {wanted_effective}.")
        if important != wanted_important:
            out.append(f"{ref}: important dates {important} ≠ {wanted_important}.")
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

    # The provenance: exactly the records the package implies, each still there.
    from django.apps import apps

    from .models import EvidencePilotRecord

    rows = list(EvidencePilotRecord.objects.filter(pilot=plan.cohort, package_sha256=plan.digest))
    written: dict[str, int] = {}
    for row in rows:
        written[row.record_kind] = written.get(row.record_kind, 0) + 1
        if row.target_model and row.target_id:
            model = apps.get_model(row.target_model)
            if not model._default_manager.filter(pk=row.target_id).exists():
                out.append(f"{row.record_key}: its {row.target_model} no longer exists.")
    if written != plan.expected_writes():
        out.append(f"provenance rows {written} ≠ expected {plan.expected_writes()}.")
    return report
