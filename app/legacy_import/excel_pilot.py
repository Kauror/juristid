"""The 2026 Excel operational pilot: a small, real dataset in the ordinary workflow.

A reviewed sample of the department's 2026 register — every Matter whose
opinion is still being written, five whose opinion went out and whose work
goes on, five that are finished — becomes **native Juristid records**: Matters
with their owner, `Hetkeseis`, `Õigusakt`, `Saabus` and `Arvamuse tähtaeg`;
canonical SENT opinions; ordinary next actions; ordinary notes. Nothing here
builds a register view beside the workflow: after the import every new thing
happens in Juristid, and there is no synchronisation back (docs/adr/0148).

**Three permissions no ordinary importer has**, and the reason this is its own,
gated operation rather than an option on the register importer:

* it records historical sends as SENT `Submission` rows whose final evidence is
  a generated placeholder (`placeholder_pdf`) — the evidence rule is unchanged,
  the placeholder satisfies it and says, in capitals, that it is not the
  Chamber's opinion;
* it schedules each active imported opinion's first `Arvamuse järelkontroll`
  through the one service that schedules them (`schedule_first_check`), which
  the importers deliberately never call (docs/adr/0146 §9);
* it closes finished files in the register's own undated shape — ARCHIVE, a
  `Disposition`, no `closed_at` — because the register never says when.

So `apply` refuses unless the environment says *this is the pilot*
(``JURISTID_EXCEL_PILOT``) and holds real data (``REAL_DATA_ALLOWED``), the
operator types the intent, names the reviewed manifest's digest and the backup
set taken before the reset, and the database holds no business Matter the pilot
did not create. Run again with the same manifest it writes nothing.

**One reading, three steps.** `build_manifest` turns the workbook and a
reviewed selection into a manifest without touching the database; `build_plan`
re-derives that manifest from the workbook and refuses if a byte of the row,
the rules or the selection moved, then resolves every person, organisation,
stage and instrument; `apply_plan` writes the plan in one transaction.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.legacy_import.contracts import EraContract, contract_for_year
from app.legacy_import.extraction import ExtractedRow, extract_row
from app.legacy_import.parser import RegisterWorkbook
from app.legacy_import.register_next_actions import (
    REGISTER_NEXT_ACTION_PARSER_VERSION,
    ParseContext,
    ReviewReason,
    Verdict,
    instruction_kinds,
    parse_instruction,
)
from app.legacy_import.register_semantics import (
    OpinionSentState,
    detect_continuation,
    normalise_status,
    opinion_sent_state,
    split_addressees,
)
from app.matters.work_status import ACTIVE, CONCLUDED, CONTINUES, INACTIVE
from app.taxonomy.legal_instruments import current_legal_instrument_keys
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics, Disposition
from app.workflow.vocabulary import CURRENT_LABEL_TO_STAGE, RAW_LABEL_TO_STAGE

PILOT = "excel-pilot-2026"
PILOT_VERSION = "1.0"
MANIFEST_VERSION = "1"
#: Typed by the operator at `apply`, the same discipline as
#: `seed_showcase_data --operator-intent showcase-world`.
OPERATOR_INTENT = "excel-pilot-2026"
SHEET_YEAR = 2026
#: `NEXT_ACTION_SET`, `MATTER_CREATED` and the security event name this source.
PROVENANCE_SOURCE = "EXCEL_PILOT_2026"
#: Prefix of every placeholder version's `source_identifier`.
PLACEHOLDER_SOURCE_PREFIX = "excel-pilot-2026-placeholder"
FOLLOW_UP_DAYS = 30

GROUP_A = "A"
GROUP_B = "B"
GROUP_C = "C"

#: What happens to a `JÄRGMISEKS` cell (docs/adr/0148 §6).
STEP_NONE = "NONE"
STEP_ACTION = "ACTION"
STEP_UNDATED_ACTION = "UNDATED_ACTION"
STEP_ENTRY = "ENTRY"

#: What happens to the row's current `Arvamuse tähtaeg`.
DEADLINE_NONE = "NONE"
DEADLINE_OPEN = "OPEN"
DEADLINE_ANSWERED = "ANSWERED"
DEADLINE_NOT_ANSWERING = "NOT_ANSWERING"
DEADLINE_UNCHANGED = "UNCHANGED"

#: The `HETKESEIS` labels that mean the Chamber decided to stop — the
#: department's current spelling and the historical one.
_STOP_LABELS = frozenset({"rohkem ei tegele", "rohkem pole tegevusi plaanis"})
_IN_FORCE_LABEL = "jõustunud"

#: The date meaning an undated step keeps, by kind: the meaning the parser gives
#: the same kind when it has a date, so a day added later reads the same way.
_UNDATED_SEMANTICS = {
    ActionKind.DO.value: DateSemantics.DEADLINE.value,
    ActionKind.WAIT.value: DateSemantics.EXPECTED_AROUND.value,
    ActionKind.MONITOR.value: DateSemantics.REVIEW_ON.value,
}


class PilotError(Exception):
    """The pilot refused. The message says what to fix; nothing was written."""


# ---------------------------------------------------------------------------
# Reading the one sheet
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    """What one titled 2026 row says, in the fields the pilot uses."""

    reference: str
    number: int
    row_number: int
    title: str
    instrument_raw: str
    received: dt.date | None
    deadline: dt.date | None
    valja_raw: str
    valja_state: str
    sent_on: dt.date | None
    addressee_raw: str
    owner_raw: str
    responded: int | None
    requested: int | None
    status_raw: str
    next_text: str
    has_link: bool
    row_sha256: str


@dataclass
class SheetReading:
    file_name: str
    sha256: str
    contract: EraContract
    rows: dict[str, SourceRow]
    extracted: dict[str, ExtractedRow]
    #: The highest reference the sheet speaks for, reserved numbers included.
    highest_number: int


def _row_digest(extracted: ExtractedRow, contract: EraContract) -> str:
    """SHA-256 of the row's contracted cells, as the parser serialised them."""
    cells = {column.letter: extracted.raw_row.get(column.letter, "") for column in contract.columns}
    payload = json.dumps(cells, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_pilot_sheet(path: str | Path) -> SheetReading:
    """Read worksheet `2026` and nothing else. Every other sheet stays unopened."""
    contract = contract_for_year(SHEET_YEAR)
    if contract is None:  # pragma: no cover - the contract ships with the code
        raise PilotError("No era contract for 2026.")
    rows: dict[str, SourceRow] = {}
    extracted_rows: dict[str, ExtractedRow] = {}
    highest = 0
    with RegisterWorkbook(path) as workbook:
        if contract.sheet not in workbook.sheet_names:
            raise PilotError(f"The workbook has no sheet {contract.sheet!r}.")
        file_name = workbook.path.name
        sha256 = workbook.sha256
        for source in workbook.rows(contract):
            extracted = extract_row(source, contract)
            if extracted.reference is not None and extracted.reference.year == SHEET_YEAR:
                highest = max(highest, extracted.reference.number)
            if (
                extracted.is_blank
                or not extracted.is_matter_row
                or extracted.reference is None
                or not extracted.title.strip()
            ):
                # Reserved numbers, the `KOKKU` totals row and padding: counted
                # for the reservation above, never a Matter.
                continue
            reference = str(extracted.reference)
            if reference in rows:
                raise PilotError(f"Reference {reference} appears twice on the 2026 sheet.")
            sent = extracted.sent
            valja_raw = (sent.raw if sent else "").strip()
            rows[reference] = SourceRow(
                reference=reference,
                number=extracted.reference.number,
                row_number=extracted.row_number,
                title=extracted.title.strip(),
                instrument_raw=extracted.legal_instrument_raw.strip(),
                received=extracted.received.value if extracted.received else None,
                deadline=extracted.deadline.value if extracted.deadline else None,
                valja_raw=valja_raw,
                valja_state=opinion_sent_state(valja_raw, parsed_date=sent.value if sent else None),
                sent_on=sent.value if sent else None,
                addressee_raw=extracted.counterparty_raw.strip(),
                owner_raw=extracted.owner_raw.strip(),
                responded=extracted.feedback_responded.value,
                requested=extracted.feedback_requested.value,
                status_raw=extracted.status_raw.strip(),
                next_text=extracted.next_action_raw.strip(),
                has_link=bool(extracted.onenote_url),
                row_sha256=_row_digest(extracted, contract),
            )
            extracted_rows[reference] = extracted
    return SheetReading(
        file_name=file_name,
        sha256=sha256,
        contract=contract,
        rows=rows,
        extracted=extracted_rows,
        highest_number=highest,
    )


# ---------------------------------------------------------------------------
# The rules — pure functions of one row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lifecycle:
    """Work status, closure and successor, as the register row decides them."""

    status: str  # a `work_status` key
    disposition: str = ""
    successor: str = ""
    reason: str = ""


def lifecycle_of(row: SourceRow) -> Lifecycle:
    """Whether the Chamber is still working on this row's Matter (docs/adr/0148 §5).

    Precedence, most specific first: a continuation to a named Matter; the
    Chamber's decision to stop («rohkem ei tegele»); a decision not to send
    («ei saatnud»); the act in force («jõustunud»), which ends active work
    without asserting a Chamber decision. Everything else — a blank `VÄLJA`
    included — is active: blank means the work is still going on.

    A sent opinion closes nothing: monitoring after a send is ordinary work.
    """
    continuation = detect_continuation(row.next_text)
    if continuation.needs_review:
        raise PilotError(
            f"{row.reference}: JÄRGMISEKS names several references after a continuation; "
            "it cannot be read as one successor."
        )
    status = normalise_status(row.status_raw)
    if continuation.supersedes:
        return Lifecycle(
            CONTINUES.key,
            Disposition.SUPERSEDED.value,
            continuation.reference,
            f"JÄRGMISEKS: töö jätkub teema {continuation.reference} all",
        )
    if status in _STOP_LABELS:
        return Lifecycle(
            CONCLUDED.key, Disposition.MONITORING_STOPPED.value, "", "HETKESEIS: rohkem ei tegele"
        )
    if row.valja_state == OpinionSentState.NOT_SENT:
        return Lifecycle(
            CONCLUDED.key,
            Disposition.NO_POSITION_FORMED.value,
            "",
            "VÄLJA: ei saatnud — töö lõpetati arvamust saatmata",
        )
    if status == _IN_FORCE_LABEL:
        return Lifecycle(INACTIVE.key, Disposition.COMPLETED.value, "", "HETKESEIS: jõustunud")
    if row.valja_state == OpinionSentState.RECORDED_OTHER:
        raise PilotError(
            f"{row.reference}: VÄLJA holds {row.valja_raw!r}, which is neither a date nor "
            "«ei saatnud»; the pilot does not guess what it means."
        )
    return Lifecycle(ACTIVE.key, reason="töö käib")


def stage_key_for(status_raw: str) -> str:
    """The reviewed stage a `HETKESEIS` label names, or "" — never invented.

    The department's current spellings first, then the historical ones; a
    blank or unknown label has no stage. «rohkem pole tegevusi plaanis» is a
    disposition, not a stage, exactly as docs/adr/0131 §9 keeps it.
    """
    label = status_raw.strip()
    return CURRENT_LABEL_TO_STAGE.get(label) or RAW_LABEL_TO_STAGE.get(label, "")


@dataclass(frozen=True)
class NextStep:
    """What the pilot does with the row's `JÄRGMISEKS` text."""

    treatment: str
    text: str = ""
    kind: str = ""
    date_semantics: str = ""
    target_date: dt.date | None = None
    date_precision: str = ""
    reasons: tuple[str, ...] = ()


def next_step_of(row: SourceRow, lifecycle: Lifecycle, snapshot_date: dt.date) -> NextStep:
    """One `JÄRGMISEKS` cell, as an ordinary action, an undated action or a note.

    * **Nothing written** — no step.
    * **A finished Matter** — never a task: the text is history and becomes a
      note (`Märkus`).
    * **Understood** by the register parser — a dated (or, where the sentence
      itself names no day, an undated) action of the kind it states.
    * **Refused only on its date** — the instruction is kept as an **undated**
      action of the kind its words name; the day stays in the text, unguessed.
    * **No kind at all** — a sentence that names nothing Koda does («jõustub
      eeldatavasti 1. aprillil») is information, not a task: a note.
    """
    text = row.next_text.strip()
    if not text:
        return NextStep(STEP_NONE)
    if lifecycle.status != ACTIVE.key:
        return NextStep(STEP_ENTRY, text=text, reasons=("matter-concluded",))

    context = ParseContext(sheet_year=SHEET_YEAR, snapshot_date=snapshot_date)
    parsed = parse_instruction(text, context=context)
    if parsed.verdict == Verdict.UNDERSTOOD:
        return NextStep(
            STEP_ACTION if parsed.target_date is not None else STEP_UNDATED_ACTION,
            text=text,
            kind=parsed.kind,
            date_semantics=parsed.date_semantics,
            target_date=parsed.target_date,
            date_precision=parsed.date_precision or DatePrecision.EXACT.value,
            reasons=(),
        )
    reasons = tuple(parsed.review_reasons)
    kinds = instruction_kinds(text)
    if ReviewReason.NO_KIND in reasons or not kinds:
        return NextStep(STEP_ENTRY, text=text, reasons=reasons)
    kind = kinds[0]
    return NextStep(
        STEP_UNDATED_ACTION,
        text=text,
        kind=kind,
        date_semantics=_UNDATED_SEMANTICS[kind],
        target_date=None,
        date_precision=DatePrecision.EXACT.value,
        reasons=reasons,
    )


def deadline_treatment(row: SourceRow, lifecycle: Lifecycle) -> str:
    """What becomes of the row's `Arvamuse tähtaeg` — answered, declined or open."""
    if row.deadline is None:
        return DEADLINE_NONE
    if row.valja_state == OpinionSentState.DATE:
        return DEADLINE_ANSWERED
    if row.valja_state == OpinionSentState.NOT_SENT:
        return DEADLINE_NOT_ANSWERING
    return DEADLINE_OPEN if lifecycle.status == ACTIVE.key else DEADLINE_UNCHANGED


def is_group_a(row: SourceRow) -> bool:
    """Every genuinely active row whose opinion is still being written."""
    if row.valja_state != OpinionSentState.BLANK:
        return False
    try:
        return lifecycle_of(row).status == ACTIVE.key
    except PilotError:
        return False


# ---------------------------------------------------------------------------
# The manifest — the reviewed, reproducible statement of what will be imported
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    """The hand-picked half of the sample; group A is decided by rule."""

    snapshot_date: dt.date
    group_b: list[tuple[str, str]] = field(default_factory=list)
    group_c: list[tuple[str, str]] = field(default_factory=list)
    #: Reviewed answers for organisation spellings the catalogue cannot read,
    #: in the import mapping file's shape (`MappingTables.organisations`).
    organisations: dict[str, str] = field(default_factory=dict)


def load_selection(path: str | Path) -> Selection:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    try:
        snapshot_date = dt.date.fromisoformat(str(raw["snapshot_date"]))
    except (KeyError, ValueError) as error:
        raise PilotError('The selection must state snapshot_date = "YYYY-MM-DD".') from error

    def entries(key: str) -> list[tuple[str, str]]:
        found = []
        for item in raw.get(key, []):
            reference = str(item.get("reference", "")).strip()
            reason = " ".join(str(item.get("reason", "")).split())
            if not reference or not reason:
                raise PilotError(f"Every [[{key}]] needs a reference and a reason.")
            found.append((reference, reason))
        return found

    organisations = raw.get("organisations", {})
    if not isinstance(organisations, dict):
        raise PilotError("[organisations] must map a source spelling to an organisation name.")
    return Selection(
        snapshot_date=snapshot_date,
        group_b=entries("group_b"),
        group_c=entries("group_c"),
        organisations={str(k): str(v) for k, v in organisations.items()},
    )


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None


def _source_block(row: SourceRow) -> dict[str, Any]:
    return {
        "hetkeseis": row.status_raw,
        "valja": row.valja_raw,
        "valja_state": row.valja_state,
        "sisse": _iso(row.received),
        "arvamuse_tahtaeg": _iso(row.deadline),
        "kellele": row.addressee_raw,
        "vastutaja": row.owner_raw,
        "oigusakt": row.instrument_raw,
        "jargmiseks": row.next_text,
        "andsid_tagasisidet": row.responded,
        "kusisime_tagasisidet": row.requested,
        "teema_link": row.has_link,
    }


def _expected_block(row: SourceRow, lifecycle: Lifecycle, snapshot_date: dt.date) -> dict[str, Any]:
    step = next_step_of(row, lifecycle, snapshot_date)
    submission = None
    follow_up_due = None
    if row.valja_state == OpinionSentState.DATE and row.sent_on is not None:
        submission = {
            "sent_on": row.sent_on.isoformat(),
            "addressees": list(split_addressees(row.addressee_raw)),
        }
        if lifecycle.status == ACTIVE.key:
            follow_up_due = (row.sent_on + dt.timedelta(days=FOLLOW_UP_DAYS)).isoformat()
    addressees = split_addressees(row.addressee_raw)
    return {
        "work_status": lifecycle.status,
        "record_mode": "FULL" if lifecycle.status == ACTIVE.key else "ARCHIVE",
        "disposition": lifecycle.disposition,
        "successor": lifecycle.successor,
        "lifecycle_reason": lifecycle.reason,
        "stage": stage_key_for(row.status_raw),
        "legal_instruments": list(current_legal_instrument_keys(row.instrument_raw)),
        "addressee": addressees[0] if len(addressees) == 1 else "",
        "submission": submission,
        "deadline": deadline_treatment(row, lifecycle),
        "follow_up_due": follow_up_due,
        "next_step": {
            "treatment": step.treatment,
            "kind": step.kind,
            "date_semantics": step.date_semantics,
            "target_date": _iso(step.target_date),
            "date_precision": step.date_precision,
            "reasons": list(step.reasons),
        },
    }


def _group_a_reason(row: SourceRow) -> str:
    return (
        f"VÄLJA tühi; HETKESEIS «{row.status_raw or '—'}» ei lõpeta tööd; "
        "jätkumisviidet pole — arvamus on koostamisel."
    )


def build_manifest(reading: SheetReading, selection: Selection) -> dict[str, Any]:
    """The reviewed statement of the sample, derived from the workbook alone.

    Pure: no database. Building it twice from the same workbook and selection
    gives the same manifest, and therefore the same digest — the property
    `build_plan` relies on to refuse a manifest the workbook no longer supports.
    """
    chosen: dict[str, tuple[str, str]] = {}
    for reference, row in reading.rows.items():
        if is_group_a(row):
            chosen[reference] = (GROUP_A, _group_a_reason(row))
    for group, entries in ((GROUP_B, selection.group_b), (GROUP_C, selection.group_c)):
        for reference, reason in entries:
            if reference not in reading.rows:
                raise PilotError(f"{reference} is not a titled row on the 2026 sheet.")
            if reference in chosen:
                raise PilotError(
                    f"{reference} is selected twice ({chosen[reference][0]}, {group})."
                )
            chosen[reference] = (group, reason)

    manifest_rows = []
    for reference in sorted(chosen, key=lambda ref: reading.rows[ref].number):
        group, reason = chosen[reference]
        row = reading.rows[reference]
        lifecycle = lifecycle_of(row)
        if group == GROUP_B and not (
            lifecycle.status == ACTIVE.key and row.valja_state == OpinionSentState.DATE
        ):
            raise PilotError(f"{reference}: group B needs a sent opinion on a still-active file.")
        if group == GROUP_C and lifecycle.status == ACTIVE.key:
            raise PilotError(f"{reference}: group C needs a finished or continued file.")
        if lifecycle.successor and lifecycle.successor not in chosen:
            raise PilotError(
                f"{reference} continues under {lifecycle.successor}, which is not in the "
                "sample; select it too, or choose another row."
            )
        manifest_rows.append(
            {
                "reference": reference,
                "row": row.row_number,
                "title": row.title,
                "group": group,
                "reason": reason,
                "row_sha256": row.row_sha256,
                "source": _source_block(row),
                "expected": _expected_block(row, lifecycle, selection.snapshot_date),
            }
        )

    counts: dict[str, int] = dict.fromkeys((GROUP_A, GROUP_B, GROUP_C), 0)
    for item in manifest_rows:
        counts[str(item["group"])] += 1
    return {
        "manifest_version": MANIFEST_VERSION,
        "pilot": PILOT,
        "pilot_version": PILOT_VERSION,
        "workbook": {
            "file_name": reading.file_name,
            "sha256": reading.sha256,
            "sheet": reading.contract.sheet,
            "contract_version": reading.contract.contract_version,
            "snapshot_date": selection.snapshot_date.isoformat(),
            "titled_rows": len(reading.rows),
            "reserve_through": reading.highest_number,
        },
        "parser_version": REGISTER_NEXT_ACTION_PARSER_VERSION,
        "organisation_mappings": dict(sorted(selection.organisations.items())),
        "counts": counts,
        "rows": manifest_rows,
    }


def manifest_digest(manifest: dict[str, Any]) -> str:
    """SHA-256 of the manifest's canonical JSON — key order and spacing fixed."""
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def selection_of(manifest: dict[str, Any]) -> Selection:
    """The hand-picked half a manifest was built from, read back out of it."""
    rows = manifest.get("rows", [])
    return Selection(
        snapshot_date=dt.date.fromisoformat(manifest["workbook"]["snapshot_date"]),
        group_b=[(r["reference"], r["reason"]) for r in rows if r["group"] == GROUP_B],
        group_c=[(r["reference"], r["reason"]) for r in rows if r["group"] == GROUP_C],
        organisations=dict(manifest.get("organisation_mappings", {})),
    )


def write_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_manifest(path: str | Path) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PilotError(f"The manifest cannot be read: {error}") from error
    if manifest.get("pilot") != PILOT or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise PilotError("This is not an excel-pilot-2026 manifest of a version this code reads.")
    return dict(manifest)


# ---------------------------------------------------------------------------
# The plan — the manifest re-derived, and every name resolved
# ---------------------------------------------------------------------------


@dataclass
class RowPlan:
    item: dict[str, Any]
    row: SourceRow
    owner: Any = None
    stage: Any = None
    instruments: list[Any] = field(default_factory=list)
    addressee: Any = None
    recipients: list[Any] = field(default_factory=list)
    responsible: Any = None
    warnings: list[str] = field(default_factory=list)

    @property
    def reference(self) -> str:
        return str(self.item["reference"])

    @property
    def expected(self) -> dict[str, Any]:
        return dict(self.item["expected"])


@dataclass
class PilotPlan:
    manifest: dict[str, Any]
    digest: str
    reading: SheetReading
    rows: list[RowPlan]
    problems: list[str]
    #: "fresh" — nothing there; "applied" — exactly this pilot is there;
    #: "foreign" — something else is, and the pilot will not mix with it.
    database_state: str
    existing_matters: int

    @property
    def can_apply(self) -> bool:
        return not self.problems and self.database_state == "fresh"


def _leading_colleague(text: str, people: Any) -> Any:
    """A colleague named as the sentence's first word, when exactly one carries it."""
    from app.core.text import normalize_for_matching

    first = text.strip().split(" ", 1)[0].strip(",.:;")
    if not first or not first[:1].isupper():
        return None
    candidates = people.by_given_name.get(normalize_for_matching(first), ())
    active = [person for person in candidates if person.is_active]
    return active[0] if len(active) == 1 else None


def build_plan(workbook_path: str | Path, manifest: dict[str, Any]) -> PilotPlan:
    """Re-derive the manifest from the workbook, then resolve it against the database.

    Refuses — as a problem, before anything is written — when the workbook is
    not the manifest's, when re-deriving gives a different manifest (a cell,
    the selection or a rule moved), or when a person, organisation, stage or
    instrument the plan needs cannot be resolved exactly.
    """
    from app.legacy_import.models import ExcelPilotImport
    from app.legacy_import.resolution import (
        KnownPeople,
        MappingFileError,
        MappingTables,
        resolve_organisation,
        resolve_owner,
    )
    from app.matters.models import Matter
    from app.taxonomy.models import LegalInstrumentType
    from app.workflow.models import StageVocabulary

    digest = manifest_digest(manifest)
    reading = read_pilot_sheet(workbook_path)
    problems: list[str] = []
    if reading.sha256 != manifest["workbook"]["sha256"]:
        raise PilotError(
            f"The workbook hashes to {reading.sha256[:16]}…, not the manifest's "
            f"{manifest['workbook']['sha256'][:16]}…. Use the exact snapshot the manifest names."
        )
    rebuilt = build_manifest(reading, selection_of(manifest))
    if manifest_digest(rebuilt) != digest:
        raise PilotError(
            "Re-deriving the manifest from this workbook gives a different manifest: a row, "
            "the selection or a rule changed. Rebuild and review the manifest."
        )

    from app.core.text import normalize_organisation_name

    # The import mapping file's own shape and keying (`MappingTables.load`), so
    # a reviewed spelling meets the catalogue exactly as it would there.
    mappings = MappingTables(
        organisations={
            normalize_organisation_name(source): target
            for source, target in rebuilt.get("organisation_mappings", {}).items()
        }
    )
    people = KnownPeople.load()
    stages = {stage.key: stage for stage in StageVocabulary.objects.all()}
    instruments = {item.key: item for item in LegalInstrumentType.objects.all()}

    rows: list[RowPlan] = []
    for item in manifest["rows"]:
        row = reading.rows[item["reference"]]
        expected = item["expected"]
        plan = RowPlan(item=item, row=row)

        owner = resolve_owner(row.owner_raw, MappingTables.empty(), people)
        if owner.value is None:
            problems.append(
                f"{plan.reference}: VASTUTAJA {row.owner_raw!r} does not resolve to exactly "
                f"one account ({owner.method})."
            )
        plan.owner = owner.value

        if expected["stage"]:
            plan.stage = stages.get(expected["stage"])
            if plan.stage is None:
                problems.append(
                    f"{plan.reference}: stage {expected['stage']!r} is not in the vocabulary."
                )
        elif row.status_raw:
            plan.warnings.append(f"HETKESEIS {row.status_raw!r} has no stage; left empty.")

        for key in expected["legal_instruments"]:
            instrument = instruments.get(key)
            if instrument is None:
                problems.append(f"{plan.reference}: Õigusakt {key!r} is not in the vocabulary.")
            else:
                plan.instruments.append(instrument)
        if row.instrument_raw and not expected["legal_instruments"]:
            plan.warnings.append(
                f"ÕIGUSAKT {row.instrument_raw!r} has no canonical reading; left empty."
            )

        resolved = []
        for part in split_addressees(row.addressee_raw):
            try:
                resolution = resolve_organisation(part, mappings)
            except MappingFileError as error:
                problems.append(f"{plan.reference}: {error}")
                continue
            if resolution.value is None:
                plan.warnings.append(f"KELLELE {part!r} does not resolve; not recorded.")
            else:
                resolved.append(resolution.value)
        if expected["addressee"] and len(resolved) == 1:
            plan.addressee = resolved[0]
        plan.recipients = list(dict.fromkeys(resolved))
        if expected["submission"] and not plan.recipients:
            problems.append(
                f"{plan.reference}: the sent opinion needs at least one resolved addressee."
            )

        if expected["next_step"]["treatment"] in {STEP_ACTION, STEP_UNDATED_ACTION}:
            plan.responsible = _leading_colleague(row.next_text, people) or plan.owner

        rows.append(plan)

    existing = Matter.all_objects.count()
    pilot_rows = ExcelPilotImport.objects.filter(manifest_sha256=digest).count()
    foreign = Matter.all_objects.exclude(pilot_imports__manifest_sha256=digest).count()
    if existing == 0:
        state = "fresh"
    elif foreign == 0 and pilot_rows == len(rows):
        state = "applied"
    else:
        state = "foreign"
    return PilotPlan(
        manifest=manifest,
        digest=digest,
        reading=reading,
        rows=rows,
        problems=problems,
        database_state=state,
        existing_matters=existing,
    )


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@dataclass
class ApplyReport:
    already_applied: bool = False
    matters: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    submissions: int = 0
    placeholders: int = 0
    follow_ups: list[tuple[str, dt.date | None]] = field(default_factory=list)
    actions: int = 0
    undated_actions: int = 0
    entries: int = 0
    deadlines_answered: int = 0
    deadlines_not_answering: int = 0
    continuations: int = 0
    reserved_through: int = 0
    batch_id: Any = None


def _refuse_unless_authorised(
    plan: PilotPlan, *, operator_intent: str, expect_manifest_sha256: str, backup_set: str
) -> None:
    if not getattr(settings, "EXCEL_PILOT_ENABLED", False):
        raise PilotError(
            "The pilot import is off in this environment (JURISTID_EXCEL_PILOT). It runs only "
            "against the authorised test-build, on the one-off container that applies it."
        )
    if not getattr(settings, "REAL_DATA_ALLOWED", False):
        raise PilotError(
            "REAL_DATA_ALLOWED is off. Real register content may only enter an environment "
            "that has passed the Secure Pilot Gate (docs/secure-pilot-gate.md)."
        )
    if operator_intent != OPERATOR_INTENT:
        raise PilotError(f"Type --operator-intent {OPERATOR_INTENT} to apply the pilot.")
    if (expect_manifest_sha256 or "").strip().lower() != plan.digest:
        raise PilotError(
            f"The manifest digest is {plan.digest}; pass exactly that as --expect-manifest-sha256."
        )
    if not backup_set.strip():
        raise PilotError(
            "Name the verified backup set taken before the reset (--backup-set). The pilot "
            "replaces business data and is applied only with a recovery point named."
        )
    if plan.problems:
        raise PilotError(
            "The plan has problems; nothing was written:\n  " + "\n  ".join(plan.problems)
        )
    if plan.database_state == "foreign":
        raise PilotError(
            f"The database holds {plan.existing_matters} Matter(s) the pilot did not create. The "
            "pilot imports only into a reset database and never mixes with other business data."
        )


def apply_plan(
    plan: PilotPlan,
    *,
    operator_intent: str,
    expect_manifest_sha256: str,
    backup_set: str,
) -> ApplyReport:
    """Write the pilot. One transaction; refuses without every authorisation."""
    _refuse_unless_authorised(
        plan,
        operator_intent=operator_intent,
        expect_manifest_sha256=expect_manifest_sha256,
        backup_set=backup_set,
    )
    if plan.database_state == "applied":
        return ApplyReport(already_applied=True, matters=len(plan.rows))

    from app.search.indexing import indexable_matters, refresh_matters, suspend_indexing

    with transaction.atomic(), suspend_indexing():
        report, touched = _write(plan, backup_set=backup_set)
    refresh_matters(indexable_matters().filter(pk__in=touched))
    return report


def _write(plan: PilotPlan, *, backup_set: str) -> tuple[ApplyReport, list[Any]]:
    from app.audit.enums import SecurityEventType
    from app.audit.services import record_security_event
    from app.legacy_import.enums import OneNoteContentStatus
    from app.legacy_import.models import (
        ExcelPilotImport,
        ImportBatch,
        MatchMethod,
        MatterSourceReference,
        ReconciliationStatus,
    )
    from app.legacy_import.parser import PARSER_VERSION, SOURCE_SYSTEM
    from app.matters.enums import DataQualityTier, RecordMode
    from app.matters.models import Matter
    from app.matters.services import create_imported_matter, reserve_matter_reference

    report = ApplyReport()
    manifest = plan.manifest
    workbook = manifest["workbook"]
    started = timezone.now()
    batch = ImportBatch.objects.create(
        source_system=SOURCE_SYSTEM,
        source_file_name=workbook["file_name"],
        source_snapshot_sha256=workbook["sha256"],
        importer_version=f"{PILOT}/{PILOT_VERSION}",
        contract_version=workbook["contract_version"],
        started_at=started,
        source_row_count=len(plan.rows),
        reconciliation_status=ReconciliationStatus.RUNNING,
        notes=(
            f"{PILOT}: {len(plan.rows)} valitud rida lehelt {workbook['sheet']}; manifest "
            f"{plan.digest}; varukoopia {backup_set.strip()} (docs/adr/0148)."
        ),
    )
    report.batch_id = batch.pk

    provenance_base = {
        "source": PROVENANCE_SOURCE,
        "pilot": PILOT,
        "pilot_version": PILOT_VERSION,
        "manifest_sha256": plan.digest,
        "workbook_sha256": workbook["sha256"],
    }
    created: dict[str, Matter] = {}
    references: dict[str, MatterSourceReference] = {}
    for row_plan in plan.rows:
        row, expected = row_plan.row, row_plan.expected
        active = expected["work_status"] == ACTIVE.key
        fields: dict[str, Any] = {
            "owner": row_plan.owner,
            "stage": row_plan.stage,
            "received_date": row.received,
            "response_deadline": row.deadline,
            "addressee_organisation": row_plan.addressee,
            "legal_instruments": row_plan.instruments,
            "source_era": str(SHEET_YEAR),
            "data_quality_tier": (
                DataQualityTier.TIER_1_VERIFIED_ACTIVE
                if active
                else DataQualityTier.TIER_2_RICH_HISTORY
            ),
            "provenance": {
                **provenance_base,
                "reference": row.reference,
                "group": row_plan.item["group"],
            },
        }
        if not active:
            # The register's own shape for a finished file: closed, with the
            # reason it gives, and no date nobody wrote down
            # (`matters_closure_fields_consistent`).
            fields["is_open"] = False
            fields["disposition"] = expected["disposition"]
        matter = create_imported_matter(
            title=row.title,
            reference_year=SHEET_YEAR,
            reference_number=row.number,
            record_mode=RecordMode.FULL if active else RecordMode.ARCHIVE,
            **fields,
        )
        extracted = plan.reading.extracted[row.reference]
        references[row.reference] = MatterSourceReference.objects.create(
            matter=matter,
            import_batch=batch,
            source_system=SOURCE_SYSTEM,
            source_file_name=workbook["file_name"],
            source_snapshot_sha256=workbook["sha256"],
            source_sheet=extracted.sheet,
            source_row_number=extracted.row_number,
            source_row_raw=extracted.raw_row,
            source_title=extracted.title,
            source_date_raw=(extracted.received.raw if extracted.received else "")[:200],
            onenote_url=extracted.onenote_url,
            onenote_page_id="",
            onenote_content_status=(
                OneNoteContentStatus.NOT_IMPORTED
                if extracted.onenote_url
                else OneNoteContentStatus.NOT_APPLICABLE
            ),
            source_era=extracted.era,
            source_contract_version=extracted.contract_version,
            source_parser_version=PARSER_VERSION,
            match_method=MatchMethod.REFERENCE_TOKEN,
            conflict_state="NONE",
        )
        created[row.reference] = matter
        report.by_status[expected["work_status"]] = (
            report.by_status.get(expected["work_status"], 0) + 1
        )

    # Successors exist now. The relation `Seotud` already reads both ways.
    for row_plan in plan.rows:
        successor = row_plan.expected["successor"]
        if successor:
            matter = created[row_plan.reference]
            matter.superseded_by = created[successor]
            matter.save(update_fields=["superseded_by", "updated_at"])
            report.continuations += 1

    for row_plan in plan.rows:
        record = _write_row(row_plan, created[row_plan.reference], plan, report)
        ExcelPilotImport.objects.create(
            matter=created[row_plan.reference],
            source_reference=references[row_plan.reference],
            pilot=PILOT,
            pilot_version=PILOT_VERSION,
            manifest_sha256=plan.digest,
            workbook_sha256=workbook["sha256"],
            source_sheet=str(workbook["sheet"]),
            source_row_number=row_plan.row.row_number,
            source_reference_label=row_plan.reference,
            selection_group=row_plan.item["group"],
            selection_reason=row_plan.item["reason"],
            work_status=row_plan.expected["work_status"],
            **record,
        )

    report.reserved_through = reserve_matter_reference(SHEET_YEAR, int(workbook["reserve_through"]))
    report.matters = len(created)

    batch.finished_at = timezone.now()
    batch.created_matter_count = len(created)
    batch.reconciliation_status = ReconciliationStatus.COMPLETED
    batch.save(
        update_fields=["finished_at", "created_matter_count", "reconciliation_status", "updated_at"]
    )
    record_security_event(
        event_type=SecurityEventType.IMPORT_RUN,
        actor=None,
        subject=batch,
        detail={
            **provenance_base,
            "rows": len(created),
            "backup_set": backup_set.strip(),
            "placeholders": report.placeholders,
            "follow_ups": len(report.follow_ups),
        },
    )
    return report, [matter.pk for matter in created.values()]


def _write_row(
    row_plan: RowPlan, matter: Any, plan: PilotPlan, report: ApplyReport
) -> dict[str, Any]:
    """Everything after the Matter itself, in the order the workflow expects."""
    from app.matters.entry_enums import EntryKind
    from app.matters.enums import ResponseDeadlineOutcome
    from app.matters.response_deadlines import resolve_response_deadline
    from app.matters.services import add_entry
    from app.workflow.follow_ups import schedule_first_check
    from app.workflow.services import set_next_action

    row, expected = row_plan.row, row_plan.expected
    step = expected["next_step"]
    interpretation: dict[str, Any] = {"next_step": step, "deadline": expected["deadline"]}
    record: dict[str, Any] = {"interpretation": interpretation}
    provenance = {
        "source": PROVENANCE_SOURCE,
        "pilot": PILOT,
        "manifest_sha256": plan.digest,
        "reference": row.reference,
        "parser_version": REGISTER_NEXT_ACTION_PARSER_VERSION,
        "treatment": step["treatment"],
        "reasons": step["reasons"],
    }

    # 1 — the current step, before anything is planned beside it.
    if step["treatment"] in {STEP_ACTION, STEP_UNDATED_ACTION}:
        target = dt.date.fromisoformat(step["target_date"]) if step["target_date"] else None
        set_next_action(
            matter=matter,
            text=row.next_text,
            kind=step["kind"],
            date_semantics=step["date_semantics"],
            target_date=target,
            date_precision=step["date_precision"],
            source_text=row.next_text,
            responsible=row_plan.responsible,
            actor=None,
            provenance=provenance,
        )
        if target is None:
            report.undated_actions += 1
        else:
            report.actions += 1
    elif step["treatment"] == STEP_ENTRY:
        add_entry(matter=matter, body=row.next_text, author=None, kind=EntryKind.NOTE)
        report.entries += 1

    # 2 — the historical send, with its placeholder evidence.
    submission = None
    if expected["submission"]:
        submission, version = _register_placeholder_send(row_plan, matter, plan)
        record["submission"] = submission
        record["placeholder_version"] = version
        report.submissions += 1
        report.placeholders += 1

    # 3 — what the send (or the decision not to send) did to the deadline.
    if expected["deadline"] == DEADLINE_ANSWERED and submission is not None:
        resolve_response_deadline(
            matter=matter, outcome=ResponseDeadlineOutcome.ANSWERED, submission=submission
        )
        report.deadlines_answered += 1
    elif expected["deadline"] == DEADLINE_NOT_ANSWERING:
        resolve_response_deadline(matter=matter, outcome=ResponseDeadlineOutcome.NOT_ANSWERING)
        report.deadlines_not_answering += 1

    # 4 — the first check, planned beside the current step, only on live work.
    if expected["follow_up_due"] and submission is not None:
        check = schedule_first_check(submission=submission, actor=None)
        if check is not None and check.follow_up is not None:
            record["follow_up"] = check.follow_up
            report.follow_ups.append((row.reference, check.target_date))
    return record


def _register_placeholder_send(row_plan: RowPlan, matter: Any, plan: PilotPlan) -> tuple[Any, Any]:
    """A canonical SENT opinion whose evidence says it is a placeholder."""
    from app.documents.enums import DocumentRole
    from app.documents.services import add_evidence_version, create_document
    from app.legacy_import.placeholder_pdf import (
        PlaceholderFacts,
        placeholder_filename,
        placeholder_title,
        render_placeholder_pdf,
    )
    from app.submissions.enums import SentAtPrecision
    from app.submissions.services import register_sent_opinion

    row = row_plan.row
    sent_on = dt.date.fromisoformat(row_plan.expected["submission"]["sent_on"])
    workbook = plan.manifest["workbook"]
    content = render_placeholder_pdf(
        PlaceholderFacts(
            reference=row.reference,
            title=row.title,
            sent_on=sent_on,
            addressee_raw=row.addressee_raw,
            workbook_name=workbook["file_name"],
            workbook_sha256=workbook["sha256"],
            sheet=str(workbook["sheet"]),
            row_number=row.row_number,
        )
    )
    document = create_document(
        matter=matter,
        title=placeholder_title(sent_on),
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=None,
        provenance_note=(
            "PROOVIMPORT — ASENDUSDOKUMENT. Loodud Exceli pilootimpordis (docs/adr/0148); "
            "see ei ole Koja tegelik arvamus."
        ),
    )
    version = add_evidence_version(
        document=document,
        content=content,
        original_filename=placeholder_filename(sent_on),
        mime_type="application/pdf",
        uploaded_by=None,
        acquired_at=timezone.now(),
        source_identifier=f"{PLACEHOLDER_SOURCE_PREFIX}:{row.reference}:{workbook['sha256'][:16]}",
    )
    submission = register_sent_opinion(
        document=document,
        version=version,
        title=document.title,
        actor=None,
        recipients=row_plan.recipients,
        sent_at=timezone.make_aware(dt.datetime.combine(sent_on, dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
    )
    return submission, version


def summarise_plan(plan: PilotPlan) -> Iterable[str]:
    """Operator-facing lines: counts, then one line per row, then problems."""
    manifest = plan.manifest
    yield f"Manifest   {plan.digest}"
    yield f"Workbook   {manifest['workbook']['file_name']} ({manifest['workbook']['sha256']})"
    yield (
        f"Sample     A {manifest['counts']['A']} · B {manifest['counts']['B']} · "
        f"C {manifest['counts']['C']} = {len(plan.rows)} Matters"
    )
    yield f"Reserve    {SHEET_YEAR}_{manifest['workbook']['reserve_through']}"
    yield f"Database   {plan.database_state} ({plan.existing_matters} Matter(s) now)"
    yield ""
    for row_plan in plan.rows:
        expected = row_plan.expected
        step = expected["next_step"]
        sub = expected["submission"]
        yield (
            f"  {row_plan.reference:<9} {row_plan.item['group']} {expected['work_status']:<10} "
            f"stage={expected['stage'] or '—':<22} send={sub['sent_on'] if sub else '—':<10} "
            f"check={expected['follow_up_due'] or '—':<10} step={step['treatment']}"
            f"{'/' + step['kind'] if step['kind'] else ''}"
            f"{'@' + step['target_date'] if step['target_date'] else ''}"
        )
        for warning in row_plan.warnings:
            yield f"      note: {warning}"
    if plan.problems:
        yield ""
        yield "Problems (nothing can be applied until these are resolved):"
        for problem in plan.problems:
            yield f"  - {problem}"
