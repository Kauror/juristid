"""«Laadi Excel vana Exceli formaadis» — the register in its 2026 column layout.

One workbook a lawyer can open next to the old «Tööd eelnõudega» file and read
in the columns they know: ``NR`` … ``JÄRGMISEKS``, one worksheet per register
year. The full rationale and the column-by-column mapping are in
``docs/data-contracts/legacy-register-export.md``; the short version is the
four rules below, and each is a way this file could otherwise lie.

**One layout, not five.** The historical workbooks had five column eras
(2011–17, 2018–19, 2020–22, 2023–24, 2025–26). This export does not reproduce
any of them except the 2026 one: every sheet carries the reviewed 2026
contract's headers A–L, read from ``excel-era-2026.toml`` rather than retyped,
so a later correction to the contract moves the export with it. Round-trip or
era-faithful compatibility is not claimed, and the ``Teave`` sheet says so.

**G is KELLELE everywhere.** Before 2020 the register's G column was KELLELT —
the *sender* — and the two meanings are opposite. Here G is always the
addressee, and an imported register value reaches it only from a sheet whose
era contract has an ``addressee_organisation`` column. A pre-2020 sender is
never written as an addressee.

**Canonical first, the register's own words second, nothing invented.**
``VÄLJA`` is the earliest *historically sent* Submission (withdrawn and
superseded sends still went out) the reader may see, as a real date. With no
such Submission it is the register's own mark, written as **text** — «ei
saatnud» stays «ei saatnud», and a register date is printed as text so it can
never be mistaken for a recorded send. Member counts are only the imported
values; a blank is not a zero.

**Authorization is the population.** ``Matter.objects.visible_to(user)
.real_data()`` — the same population the Statistika CSV exports start from
(``app.reporting.selectors.base.reporting_population``); the manager already
excludes deleted Teemad. Child rows (Submissions, NextActions) are scoped with
their own ``visible_to``, so a restricted opinion on a readable Matter cannot
leak its date or its addressee to a reader who may not see it.

Query count is constant in the number of rows: the Matters with their
one-to-one and foreign-key rows in one query, the legal instruments, the sends
with their addressees, the open steps and the fallback source rows each in one
more. Nothing is fetched per row.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from django.db.models import Case, F, IntegerField, Prefetch, QuerySet, Value, When
from django.utils import timezone
from openpyxl import Workbook

from app.core.xlsx_safety import Column, SheetWriter, info_sheet, new_workbook
from app.legacy_import.contracts import EraContract, contract_for_year
from app.legacy_import.current_state import CurrentRegisterState
from app.legacy_import.dates import parse_date
from app.legacy_import.models import ConflictState, MatterSourceReference
from app.legacy_import.register_semantics import OpinionSentState, parse_member_count
from app.legacy_import.source_cells import contracts_by_sheet, source_cell
from app.matters.models import Matter
from app.matters.selectors import unknown_register_year_q
from app.submissions.models import ADDRESSEE_ROWS, Submission, addressee_prefetch
from app.taxonomy.models import LegalInstrumentType
from app.workflow.models import NextAction

#: The reviewed contract whose layout every sheet uses.
LAYOUT_YEAR = 2026

INFO_SHEET = "Teave"
UNKNOWN_SHEET = "Teadmata"

#: The historical column eras, for the information sheet.
HISTORICAL_ERAS = ("2011–2017", "2018–2019", "2020–2022", "2023–2024", "2025–2026")

#: Source rows a reviewer has not accepted as describing this Matter. Their
#: cells are evidence of a disagreement, not of the Matter, so they feed nothing.
_UNSETTLED_CONFLICTS = (ConflictState.CONFLICTING_EVIDENCE, ConflictState.UNRESOLVED)

#: Widths per contract letter, in characters.
_WIDTHS = {
    "A": 11,
    "B": 60,
    "C": 22,
    "D": 12,
    "E": 14,
    "F": 14,
    "G": 34,
    "H": 22,
    "I": 14,
    "J": 14,
    "K": 28,
    "L": 50,
}
_WRAPPED = {"B", "C", "G", "K", "L"}

_REGISTER_DATE_TEXT = "%d.%m.%Y"


def layout_contract() -> EraContract:
    contract = contract_for_year(LAYOUT_YEAR)
    if contract is None:  # pragma: no cover - the contract set is validated on load
        raise LookupError(f"The {LAYOUT_YEAR} era contract is missing.")
    return contract


def layout_columns() -> list[Column]:
    """The 2026 contract's columns A–L, in letter order, with its exact headers."""
    columns = sorted(layout_contract().columns, key=lambda column: column.index)
    return [
        Column(column.header, width=_WIDTHS.get(column.letter, 18), wrap=column.letter in _WRAPPED)
        for column in columns
    ]


def legacy_filename(today: date) -> str:
    return f"Juristid_vana_Exceli_formaat_{today.isoformat()}.xlsx"


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------


def legacy_population(user: object | None) -> QuerySet[Matter]:
    """Every Teema ``user`` may read, real data only, open, closed and archive.

    The Statistika exports' population (``reporting_population``), without a
    period or any other filter: the old workbook was the whole register.
    """
    return Matter.objects.visible_to(user).real_data()


def _matters(population: QuerySet[Matter]) -> QuerySet[Matter]:
    return (
        population.annotate(
            # `unknown_register_year_q` is the one definition of «no register
            # year»; its complement is `register_year_q`. Never `created_at`.
            register_year=Case(
                When(unknown_register_year_q(), then=Value(None)),
                default=F("reporting_year"),
                output_field=IntegerField(),
            )
        )
        .select_related(
            "owner",
            "stage",
            "addressee_organisation",
            "current_register_state",
            "current_register_state__source_reference",
        )
        .prefetch_related(
            Prefetch(
                "legal_instruments",
                queryset=LegalInstrumentType.objects.order_by("sort_order", "label_et", "pk"),
            )
        )
        .order_by(
            F("reference_year").asc(nulls_last=True),
            F("reference_number").asc(nulls_last=True),
            "title",
            "pk",
        )
    )


@dataclass
class _Send:
    sent_on: date
    addressees: list[str] = field(default_factory=list)


def _earliest_sends(user: object | None, population: QuerySet[Matter]) -> dict[Any, _Send]:
    """The earliest historically sent Submission per Matter, as this reader sees it."""
    submissions = (
        Submission.objects.visible_to(user)
        .historically_sent()
        .filter(matter__in=population.values("pk"))
        .order_by("matter_id", "sent_at", "created_at", "pk")
        .prefetch_related(addressee_prefetch())
    )
    earliest: dict[Any, _Send] = {}
    for submission in submissions:
        if submission.matter_id in earliest:
            continue
        rows = getattr(submission, ADDRESSEE_ROWS, [])
        earliest[submission.matter_id] = _Send(
            # A date-only send is stored as Tallinn midnight and a timestamped
            # one is read as the Tallinn day it went (`sent_on_of`).
            sent_on=timezone.localdate(submission.sent_at),
            addressees=[row.organisation.name for row in rows],
        )
    return earliest


def _open_steps(user: object | None, population: QuerySet[Matter]) -> dict[Any, str]:
    """The current open `Järgmiseks` text per Matter, as this reader sees it."""
    steps: dict[Any, str] = {}
    rows = (
        NextAction.objects.visible_to(user)
        .open()
        .filter(matter__in=population.values("pk"))
        .order_by("matter_id", "-created_at", "-pk")
        .values_list("matter_id", "text")
    )
    for matter_id, text in rows:
        steps.setdefault(matter_id, text)
    return steps


def _fallback_references(
    population: QuerySet[Matter], contracts: dict[str, EraContract]
) -> dict[Any, MatterSourceReference]:
    """The latest register row per Matter that has no derived register state.

    Only rows read under a reviewed era contract (so a OneNote or Lovable
    provenance row never answers a register question) and only rows no
    reviewer left in conflict. «Latest» is the newest contract year, then the
    lowest row number.
    """
    references = (
        MatterSourceReference.objects.filter(
            matter__in=population.filter(current_register_state__isnull=True).values("pk"),
            source_sheet__in=list(contracts),
        )
        .exclude(conflict_state__in=_UNSETTLED_CONFLICTS)
        .only("pk", "matter_id", "source_sheet", "source_row_number", "source_row_raw")
    )
    chosen: dict[Any, MatterSourceReference] = {}

    def rank(reference: MatterSourceReference) -> tuple[int, int, str]:
        year = contracts[reference.source_sheet].year
        row = reference.source_row_number or 0
        return (year, -row, str(reference.pk))

    for reference in references:
        current = chosen.get(reference.matter_id)
        if current is None or rank(reference) > rank(current):
            chosen[reference.matter_id] = reference
    return chosen


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RegisterRow:
    """What the imported register says about one Matter, read once."""

    sent_text: str = ""
    addressee: str = ""
    legal_instrument: str = ""
    responded: int | None = None
    requested: int | None = None


def _has_addressee_column(sheet: str, contracts: dict[str, EraContract]) -> bool:
    """Whether the sheet's G-side column is KELLELE (2020+) rather than KELLELT."""
    contract = contracts.get(sheet)
    if contract is None:
        return False
    column = contract.column_for("addressee_organisation")
    return column is not None and column.direction == "addressee"


def _register_date_text(value: date) -> str:
    return value.strftime(_REGISTER_DATE_TEXT)


def _sent_text_from_cell(raw: str | None) -> str:
    """``VÄLJA`` from a raw cell: a date as text, anything else verbatim."""
    text = (raw or "").strip()
    if not text:
        return ""
    parsed = parse_date(text, raw=text)
    if parsed.value is not None:
        return _register_date_text(parsed.value)
    return text


def _from_state(state: CurrentRegisterState, contracts: dict[str, EraContract]) -> _RegisterRow:
    reference = state.source_reference
    sheet = state.source_sheet or reference.source_sheet
    raw_sent = source_cell(reference, contracts, "opinion_sent_date")

    if state.opinion_sent_state == OpinionSentState.DATE and state.opinion_sent_date is not None:
        sent = _register_date_text(state.opinion_sent_date)
    elif state.opinion_sent_state == OpinionSentState.BLANK:
        sent = ""
    elif raw_sent is not None and raw_sent.strip():
        sent = raw_sent.strip()
    elif state.opinion_sent_state == OpinionSentState.NOT_SENT:
        # The derived state's own reading, when the raw cell is unreadable here.
        sent = "ei saatnud"
    else:
        sent = ""

    addressee = state.addressee_raw.strip() if _has_addressee_column(sheet, contracts) else ""
    return _RegisterRow(
        sent_text=sent,
        addressee=addressee,
        legal_instrument=state.legal_instrument_raw.strip(),
        responded=state.member_feedback_responded,
        requested=state.member_feedback_requested,
    )


def _from_reference(
    reference: MatterSourceReference, contracts: dict[str, EraContract]
) -> _RegisterRow:
    def cell(name: str) -> str | None:
        return source_cell(reference, contracts, name)

    addressee = ""
    if _has_addressee_column(reference.source_sheet, contracts):
        addressee = (cell("addressee_organisation") or "").strip()
    return _RegisterRow(
        sent_text=_sent_text_from_cell(cell("opinion_sent_date")),
        addressee=addressee,
        legal_instrument=(cell("legal_instrument") or "").strip(),
        responded=parse_member_count(cell("member_feedback_responded")),
        requested=parse_member_count(cell("member_feedback_requested")),
    )


def _legal_instruments(matter: Matter, register: _RegisterRow) -> str:
    names = [instrument.label_et for instrument in matter.legal_instruments.all()]
    other = matter.legal_instrument_other.strip()
    if other:
        names.append(other)
    if names:
        return "; ".join(names)
    return register.legal_instrument


def _addressee(matter: Matter, send: _Send | None, register: _RegisterRow) -> str:
    if send is not None and send.addressees:
        return "; ".join(send.addressees)
    if matter.addressee_organisation is not None:
        return matter.addressee_organisation.name
    return register.addressee


def _row(
    matter: Matter,
    *,
    send: _Send | None,
    step: str,
    register: _RegisterRow,
) -> list[Any]:
    sent: date | str | None = send.sent_on if send is not None else (register.sent_text or None)
    return [
        matter.display_reference or None,  # A NR — text
        matter.title,  # B TEEMA
        _legal_instruments(matter, register),  # C ÕIGUSAKT
        matter.received_date,  # D SISSE
        matter.response_deadline,  # E ARVAMUSE TÄHTAEG
        sent,  # F VÄLJA — a date (canonical) or the register's text
        _addressee(matter, send, register),  # G KELLELE
        matter.owner.display_name if matter.owner is not None else None,  # H VASTUTAJA
        register.responded,  # I
        register.requested,  # J
        matter.stage.label_et if matter.stage is not None else None,  # K HETKESEIS
        step or None,  # L JÄRGMISEKS
    ]


# ---------------------------------------------------------------------------
# The workbook
# ---------------------------------------------------------------------------


def _viewer_name(user: object | None) -> str:
    name = getattr(user, "display_name", "") or ""
    return str(name).strip() or "—"


def _info_lines(
    *,
    user: object | None,
    now: datetime,
    counts: list[tuple[str, int]],
    columns: list[Column],
) -> list[str]:
    headers = [column.heading for column in columns]
    eras = ", ".join(HISTORICAL_ERAS)
    lines = [
        "Juristid — Tööd eelnõudega, vana Exceli formaadis",
        f"Koostatud: {timezone.localtime(now):%d.%m.%Y %H:%M} (Eesti aeg)",
        f"Koostaja: {_viewer_name(user)}",
        (
            "Ulatus: kõik teemad, mida koostaja tohib näha — avatud, suletud ja arhiiv. "
            "Ainult päris andmed: testkirjed ja kustutatud teemad ei ole kaasas."
        ),
        (
            f"See on ühtne {LAYOUT_YEAR}. aasta registri kujul eksport: igal lehel on "
            f"{LAYOUT_YEAR}. aasta veerud A–L ({'; '.join(headers)}). See EI OLE viie "
            f"ajaloolise töövihiku ajastu ({eras}) täpne taastamine — nende veerud "
            "erinesid. Faili ei saa Juristidesse tagasi importida ja algsete "
            "töövihikutega edasi-tagasi ühilduvust ei taotleta."
        ),
        (
            "Lehed: üks leht registri aruandlusaasta kohta, uuem aasta eespool. Lehel "
            f"«{UNKNOWN_SHEET}» on teemad, millel registriaasta puudub; loomise kuupäeva "
            "järgi aastat ei omistata. Read on viite (NR) järgi kasvavas järjekorras, "
            "viiteta read lõpus, siis pealkirja järgi."
        ),
        "NR on tekst (nt 2026_14). Viiteta teemal on NR tühi.",
        (
            "ÕIGUSAKT: Juristides valitud õigusakti liigid ja vabatekst; kui neid ei "
            "ole, imporditud registri algne tekst."
        ),
        (
            "VÄLJA: päris kuupäev on varaseima Juristidesse salvestatud saadetud "
            "arvamuse kuupäev, ka siis, kui arvamus hiljem tagasi võeti või asendati. "
            "Kui Juristides saadetud arvamust ei ole, on lahtris imporditud registri "
            "algne märge TEKSTINA (nt «ei saatnud» või registri kuupäev tekstina) — "
            "see on ajalooline registrikirje, mitte saadetud arvamus. Tühi: kumbagi ei ole."
        ),
        (
            "KELLELE (veerg G) tähendab igal lehel adressaati — kellele Koda arvamuse "
            "saatis —, ka aastatel, mille algses töövihikus oli G-veerus KELLELT "
            "(saatja, kuni 2019). Saatja teavet selles failis ei ole: see säilib "
            "Juristides allikaviidetes ja täielikus ekspordis. Allikas: varaseima "
            "saadetud arvamuse adressaadid, siis teema adressaat, siis imporditud "
            "registri KELLELE-tekst (ainult 2020+ ajastu veerust). Tühi: teadmata."
        ),
        "VASTUTAJA: teema praegune vastutaja Juristides.",
        (
            "LIIKMETE ARV (veerud I ja J): ainult imporditud registri väärtused. Tühi "
            "tähendab, et arvu ei ole kirjas — mitte nulli."
        ),
        (
            "HETKESEIS ja JÄRGMISEKS on PRAEGUSED väärtused Juristides, mitte tingimata "
            "seis selle ajaloolise aasta jooksul. JÄRGMISEKS on teema praegune avatud "
            "järgmine samm; registri vana JÄRGMISEKS-teksti siia ei kirjutata."
        ),
        "Lehtede ridade arv: " + ", ".join(f"{name} {count}" for name, count in counts) + ".",
    ]
    return lines


def legacy_register_workbook(
    user: object | None, *, today: date | None = None, now: datetime | None = None
) -> Workbook:
    """The whole register ``user`` may read, in the 2026 layout, one sheet a year.

    ``today`` is accepted for the view's symmetry and names nothing inside the
    file: the generated-at line reads ``now``. Sheet order: ``Teave`` first,
    then the register years newest first, then ``Teadmata`` (always present,
    possibly empty).
    """
    now = now or timezone.now()

    contracts = contracts_by_sheet()
    columns = layout_columns()
    population = legacy_population(user)

    sends = _earliest_sends(user, population)
    steps = _open_steps(user, population)
    fallbacks = _fallback_references(population, contracts)

    by_year: dict[int, list[list[Any]]] = defaultdict(list)
    unknown: list[list[Any]] = []
    for matter in _matters(population):
        state = getattr(matter, "current_register_state", None)
        if state is not None:
            register = _from_state(state, contracts)
        elif matter.pk in fallbacks:
            register = _from_reference(fallbacks[matter.pk], contracts)
        else:
            register = _RegisterRow()
        row = _row(
            matter, send=sends.get(matter.pk), step=steps.get(matter.pk, ""), register=register
        )
        year = matter.register_year  # type: ignore[attr-defined]
        if year is None:
            unknown.append(row)
        else:
            by_year[year].append(row)

    years = sorted(by_year, reverse=True)
    counts = [(str(year), len(by_year[year])) for year in years] + [(UNKNOWN_SHEET, len(unknown))]

    workbook = new_workbook()
    info_sheet(
        workbook, INFO_SHEET, _info_lines(user=user, now=now, counts=counts, columns=columns)
    )
    for year in years:
        _write_sheet(workbook, str(year), columns, by_year[year])
    _write_sheet(workbook, UNKNOWN_SHEET, columns, unknown)
    return workbook


def _write_sheet(
    workbook: Workbook, title: str, columns: list[Column], rows: list[list[Any]]
) -> None:
    writer = SheetWriter(workbook, title, columns)
    for row in rows:
        writer.append(row)
    writer.finish()
