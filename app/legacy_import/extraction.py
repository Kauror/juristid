"""Turning one source row into structured observations.

Still no database. This layer applies a year's contract to a row and produces
what the row *says*, together with everything questionable about it. It does not
decide what to do — that is the planner's job, and keeping the two apart is what
lets the offline inspector run on a laptop with no PostgreSQL.

The distinction that matters throughout: a value is either present, absent, or
unreadable, and those are three different facts. Most historical data loss
happens when the second and third collapse into the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.legacy_import.contracts import ColumnContract, EraContract
from app.legacy_import.dates import ParsedDate, parse_date, response_interval_days
from app.legacy_import.enums import Anomaly
from app.legacy_import.parser import SourceRow
from app.legacy_import.references import MatterReference, parse_matter_reference
from app.workflow.vocabulary import CONTROLLED_LABELS


@dataclass(frozen=True)
class Count:
    """A member-feedback count, deferred to Stage 2C but read carefully now.

    ``raw`` empty means the cell was blank, which is **not** zero. A measured
    zero is a fact about an outreach that happened and produced nothing; a blank
    is the absence of a record. Any later response-rate calculation that treats
    them alike would be wrong in the direction that flatters the department.
    """

    raw: str
    value: int | None
    readable: bool

    @property
    def is_blank(self) -> bool:
        return not self.raw.strip()


BLANK_COUNT = Count(raw="", value=None, readable=True)


def read_count(raw: str) -> Count:
    text = raw.strip()
    if not text:
        return BLANK_COUNT
    try:
        return Count(raw=raw, value=int(float(text)), readable=True)
    except ValueError:
        return Count(raw=raw, value=None, readable=False)


@dataclass
class ExtractedRow:
    """What one source row says, plus every doubt about it."""

    sheet: str
    year: int
    row_number: int
    era: str
    contract_version: str

    reference_raw: str = ""
    reference: MatterReference | None = None
    title: str = ""
    legal_instrument_raw: str = ""

    received: ParsedDate | None = None
    deadline: ParsedDate | None = None
    sent: ParsedDate | None = None

    counterparty_raw: str = ""
    counterparty_direction: str = ""
    owner_raw: str = ""

    feedback_responded: Count = BLANK_COUNT
    feedback_requested: Count = BLANK_COUNT

    status_raw: str = ""
    next_action_raw: str = ""

    onenote_url: str = ""
    other_hyperlinks: dict[str, str] = field(default_factory=dict)
    unknown_values: dict[str, str] = field(default_factory=dict)

    raw_row: dict[str, str] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    is_blank: bool = False

    #: Which column carried the reference, so a reference-only row can be
    #: recognised without assuming it is always column A.
    _reference_letter: str = ""

    def note(self, anomaly: str) -> None:
        if anomaly not in self.anomalies:
            self.anomalies.append(anomaly)

    @property
    def display_reference(self) -> str:
        return str(self.reference) if self.reference else ""

    @property
    def is_matter_row(self) -> bool:
        """A row that carries a policy matter.

        Deliberately generous about what claims to be one — anything with a
        reference *or* a title — but a reserved number carries nothing, so it is
        excluded here and counted on its own.
        """
        if self.is_reserved_reference:
            return False
        return bool(self.reference_raw.strip() or self.title.strip())

    @property
    def is_reserved_reference(self) -> bool:
        """A number the register has claimed but not yet used.

        The current year's sheet is pre-numbered well past the last real matter:
        in the supplied snapshot 2026 runs to ``2026_300`` while only the first
        192 rows carry anything. Those trailing rows are not defective matters
        and putting a hundred of them in front of a reviewer as "missing title"
        would bury the handful of rows that genuinely need a decision.

        They are not ignorable either. The department considers those numbers
        spoken for, so the reference sequence has to know about them or the
        first natively created 2026 Matter will be handed a number a lawyer has
        already written on a file (Stage-2A brief 11).
        """
        if self.reference is None:
            return False
        return not any(
            value.strip()
            for letter, value in self.raw_row.items()
            if letter != self._reference_letter
        )

    @property
    def response_interval_days(self) -> int | None:
        return response_interval_days(
            self.received.value if self.received else None,
            self.deadline.value if self.deadline else None,
        )


def _date_field(row: SourceRow, column: ColumnContract | None) -> ParsedDate | None:
    if column is None:
        return None
    cell = row.cell(column.letter)
    if cell is None:
        return None
    return parse_date(cell.value, raw=cell.raw)


def extract_row(row: SourceRow, contract: EraContract) -> ExtractedRow:
    """Read one row through its era's contract."""
    extracted = ExtractedRow(
        sheet=row.sheet,
        year=contract.year,
        row_number=row.row_number,
        era=contract.era,
        contract_version=contract.contract_version,
        raw_row=row.raw_mapping(),
        is_blank=row.is_blank,
    )
    if row.is_blank:
        return extracted

    reference_column = contract.column_for("matter_reference")
    if reference_column is not None:
        extracted._reference_letter = reference_column.letter
        extracted.reference_raw = row.text(reference_column.letter)
        extracted.reference = parse_matter_reference(extracted.reference_raw)

    title_column = contract.column_for("title")
    if title_column is not None:
        extracted.title = row.text(title_column.letter).strip()
        title_cell = row.cell(title_column.letter)
        # The register's only surviving pointer to the case notebook lives on
        # the title cell, as a hyperlink rather than as text.
        if title_cell is not None and title_cell.hyperlink:
            extracted.onenote_url = title_cell.hyperlink

    for letter, link in row.hyperlinks().items():
        if title_column is None or letter != title_column.letter:
            extracted.other_hyperlinks[letter] = link

    if (column := contract.column_for("legal_instrument")) is not None:
        extracted.legal_instrument_raw = row.text(column.letter)

    extracted.received = _date_field(row, contract.column_for("received_date"))
    extracted.deadline = _date_field(row, contract.column_for("response_deadline"))
    extracted.sent = _date_field(row, contract.column_for("opinion_sent_date"))

    # Direction comes from the contract, never from the header text. This is the
    # single most important line in the module: KELLELT and KELLELE look alike
    # and mean opposite things.
    for field_name, direction in (
        ("source_organisation", "source"),
        ("addressee_organisation", "addressee"),
    ):
        if (column := contract.column_for(field_name)) is not None:
            extracted.counterparty_raw = row.text(column.letter).strip()
            extracted.counterparty_direction = direction

    if (column := contract.column_for("owner_name")) is not None:
        extracted.owner_raw = row.text(column.letter).strip()

    if (column := contract.column_for("member_feedback_responded")) is not None:
        extracted.feedback_responded = read_count(row.text(column.letter))
    if (column := contract.column_for("member_feedback_requested")) is not None:
        extracted.feedback_requested = read_count(row.text(column.letter))

    if (column := contract.column_for("legacy_status")) is not None:
        extracted.status_raw = row.text(column.letter).strip()

    if (column := contract.column_for("next_action_text")) is not None:
        extracted.next_action_raw = row.text(column.letter).strip()

    for column in contract.columns_for("unknown"):
        value = row.text(column.letter)
        if value.strip():
            extracted.unknown_values[column.letter] = value

    _note_anomalies(extracted, contract)
    return extracted


def _note_anomalies(extracted: ExtractedRow, contract: EraContract) -> None:
    if not extracted.is_matter_row:
        return

    if extracted.reference is None:
        extracted.note(Anomaly.INVALID_REFERENCE.value)
    elif extracted.reference.year != contract.year:
        # A 2019 reference on the 2020 sheet is a real occurrence and always
        # means something — a carried-over file, or a copy-paste error. Either
        # way a person decides, not the parser.
        extracted.note(Anomaly.REFERENCE_YEAR_MISMATCH.value)

    if not extracted.title:
        extracted.note(Anomaly.MISSING_TITLE.value)

    for parsed in (extracted.received, extracted.deadline, extracted.sent):
        if parsed is not None and parsed.failed:
            extracted.note(Anomaly.INVALID_DATE.value)

    interval = extracted.response_interval_days
    if interval is not None and interval < 0:
        extracted.note(Anomaly.NEGATIVE_RESPONSE_INTERVAL.value)

    for count in (extracted.feedback_responded, extracted.feedback_requested):
        if not count.readable:
            extracted.note(Anomaly.UNREADABLE_COUNT.value)

    responded = extracted.feedback_responded.value
    requested = extracted.feedback_requested.value
    if responded is not None and requested is not None and responded > requested:
        # Legitimate: members answer through channels other than the direct ask.
        # Recorded so nobody later "fixes" it into a response rate.
        extracted.note(Anomaly.FEEDBACK_RESPONDED_EXCEEDS_REQUESTED.value)

    if extracted.unknown_values:
        extracted.note(Anomaly.UNKNOWN_COLUMN_VALUE.value)

    # Offline, the reviewed vocabulary is the best available authority. A run
    # with a database re-derives this from LegacyStatusMapping, which is
    # era-aware and can therefore disagree — in the direction of knowing more.
    # Neither path ever maps a label by similarity: the workbook holds
    # 'rohkem tegevusi pole' beside the controlled 'rohkem pole tegevusi
    # plaanis', and deciding those are the same value is a lawyer's call.
    if extracted.status_raw and extracted.status_raw not in CONTROLLED_LABELS:
        extracted.note(Anomaly.UNMAPPED_STATUS.value)


def extract_sheet(rows: list[SourceRow], contract: EraContract) -> list[ExtractedRow]:
    """Read a whole sheet, then mark duplicate references across it.

    Duplicates can only be seen with the sheet in hand, so the check happens
    here rather than per row. Every member of a duplicate group is flagged: the
    first occurrence is not automatically the right one.
    """
    extracted = [extract_row(row, contract) for row in rows]

    seen: dict[str, list[ExtractedRow]] = {}
    for row in extracted:
        if row.reference is not None:
            seen.setdefault(str(row.reference), []).append(row)
    for group in seen.values():
        if len(group) > 1:
            for row in group:
                row.note(Anomaly.DUPLICATE_REFERENCE.value)
    return extracted


def summarize(rows: list[ExtractedRow]) -> dict[str, Any]:
    """Aggregate counts for one sheet. Row content never appears here."""
    matter_rows = [row for row in rows if row.is_matter_row]
    reserved = [row for row in rows if row.is_reserved_reference]
    numbers = [
        row.reference.number
        for row in rows
        if row.reference is not None and row.reference.year == row.year
    ]
    statuses: dict[str, int] = {}
    for row in matter_rows:
        if row.status_raw:
            statuses[row.status_raw] = statuses.get(row.status_raw, 0) + 1

    anomalies: dict[str, int] = {}
    for row in rows:
        for anomaly in row.anomalies:
            anomalies[anomaly] = anomalies.get(anomaly, 0) + 1

    return {
        "rows_below_header": len(rows),
        "blank_rows": sum(1 for row in rows if row.is_blank),
        "matter_rows": len(matter_rows),
        "reserved_references": len(reserved),
        "valid_references": sum(1 for row in matter_rows if row.reference is not None),
        # The sequence must clear this, not the highest imported number.
        "highest_reference_number": max(numbers, default=0),
        "hyperlinks": sum(1 for row in matter_rows if row.onenote_url),
        "other_hyperlinks": sum(len(row.other_hyperlinks) for row in matter_rows),
        "next_action_populated": sum(1 for row in matter_rows if row.next_action_raw),
        "status_values": dict(sorted(statuses.items())),
        "status_populated": sum(statuses.values()),
        "feedback_responded_present": sum(
            1 for row in matter_rows if not row.feedback_responded.is_blank
        ),
        "feedback_requested_present": sum(
            1 for row in matter_rows if not row.feedback_requested.is_blank
        ),
        "feedback_measured_zero": sum(
            1 for row in matter_rows if row.feedback_responded.value == 0
        ),
        "unknown_column_values": sum(len(row.unknown_values) for row in matter_rows),
        "anomalies": dict(sorted(anomalies.items())),
    }
