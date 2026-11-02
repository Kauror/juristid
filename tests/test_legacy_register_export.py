"""«Laadi Excel vana Exceli formaadis»: the register in the 2026 layout (Export 3).

Every assertion reads the finished file back with openpyxl, so what is tested is
what a lawyer opens — sheet names, the contract's exact headers, typed cells —
and not an intermediate list.
"""

from __future__ import annotations

import io
import itertools
from datetime import UTC, date, datetime, time
from typing import Any

import openpyxl
import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import DEPARTMENT_VIEWER, Scope
from app.core.enums import Visibility
from app.core.xlsx_safety import XLSX_CONTENT_TYPE, workbook_bytes
from app.legacy_import.contracts import contract_for_year
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.register_semantics import OpinionSentState
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.reporting.legacy_workbook import (
    INFO_SHEET,
    UNKNOWN_SHEET,
    legacy_filename,
    legacy_register_workbook,
)
from app.submissions.enums import RecipientRole, SubmissionStatus
from app.submissions.models import SubmissionRecipient
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import Disposition
from tests import factories
from tests.break_glass import grant_to
from tests.gate import PASSWORD, apply_shared_gate
from tests.synthetic_portfolio import raw_row

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 10, 15, tzinfo=UTC)
HEADERS = [
    column.header
    for column in sorted(contract_for_year(2026).columns, key=lambda column: column.index)
]
SNAPSHOT = "a" * 64


def _book(user: Any) -> openpyxl.Workbook:
    workbook = legacy_register_workbook(user, today=NOW.date(), now=NOW)
    return openpyxl.load_workbook(io.BytesIO(workbook_bytes(workbook)))


def _rows(sheet: Any) -> list[tuple[Any, ...]]:
    return [tuple(cell for cell in row) for row in sheet.iter_rows(min_row=2)]


def _values(sheet: Any) -> list[tuple[Any, ...]]:
    return [tuple(cell.value for cell in row) for row in sheet.iter_rows(min_row=2)]


def _row_for(book: openpyxl.Workbook, sheet: str, title: str) -> dict[str, Any]:
    """One data row as {header letter: cell}."""
    for row in book[sheet].iter_rows(min_row=2):
        if row[1].value == title:
            return {cell.column_letter: cell for cell in row}
    raise AssertionError(f"{title!r} is not on sheet {sheet!r}")


def _matter(year: int | None = 2026, **fields: Any) -> Any:
    fields.setdefault("reporting_year", year)
    if year is not None:
        fields.setdefault("reference_year", year)
    return factories.MatterFactory(**fields)


def _sent(capture_evidence, matter, *, on: date, status=SubmissionStatus.SENT, to=(), **kw):
    version = capture_evidence(
        matter,
        b"%PDF-1.4 arvamus",
        "arvamus.pdf",
        "application/pdf",
        visibility_override=kw.get("visibility_override", ""),
    )
    submission = factories.SubmissionFactory(
        matter=matter,
        status=status,
        sent_at=timezone.make_aware(datetime.combine(on, time.min)),
        final_version=version,
        **kw,
    )
    for organisation in to:
        SubmissionRecipient.objects.create(
            submission=submission, organisation=organisation, role=RecipientRole.ADDRESSEE
        )
    return submission


_ROWS = itertools.count(2)


def _reference(matter, *, sheet: int, **cells: str):
    return factories.MatterSourceReferenceFactory(
        matter=matter,
        source_sheet=str(sheet),
        source_row_number=next(_ROWS),
        source_snapshot_sha256=SNAPSHOT,
        source_row_raw=raw_row(sheet, matter_reference=matter.display_reference, **cells),
    )


def _state(matter, *, sheet: int = 2026, sent_cell: str = "", **fields: Any):
    reference = _reference(matter, sheet=sheet, opinion_sent_date=sent_cell)
    if sent_cell == "ei saatnud":
        fields.setdefault("opinion_sent_state", OpinionSentState.NOT_SENT)
    fields.setdefault("opinion_sent_state", OpinionSentState.BLANK)
    fields["opinion_sent_recorded"] = fields["opinion_sent_state"] != OpinionSentState.BLANK
    return CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=SNAPSHOT,
        source_sheet=str(sheet),
        source_row_number=reference.source_row_number,
        currency=RegisterCurrency.CURRENT,
        observed_at=timezone.now(),
        **fields,
    )


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_sheets_are_info_then_years_newest_first_then_unknown(department_head):
    _matter(2019)
    _matter(2026)
    _matter(2026)
    _matter(None, origin=MatterOrigin.LEGACY_IMPORT, reference_year=None)

    book = _book(department_head)

    assert book.sheetnames == [INFO_SHEET, "2026", "2019", UNKNOWN_SHEET]
    assert [len(_rows(book[name])) for name in ("2026", "2019", UNKNOWN_SHEET)] == [2, 1, 1]


def test_every_data_sheet_carries_the_2026_contract_headers_exactly(department_head):
    _matter(2014)
    _matter(2026)

    book = _book(department_head)

    assert len(HEADERS) == 12
    assert HEADERS[0] == "NR" and HEADERS[6] == "KELLELE" and HEADERS[-1] == "JÄRGMISEKS"
    for name in ("2026", "2014", UNKNOWN_SHEET):
        assert [cell.value for cell in book[name][1]] == HEADERS


def test_rows_are_ordered_by_reference_then_title_with_unnumbered_rows_last(department_head):
    late = _matter(2026, title="B teema", reference_number=14)
    early = _matter(2026, title="Z teema", reference_number=2)
    _matter(2026, title="A viiteta", reference_year=None, reference_number=None)
    _matter(2026, title="C viiteta", reference_year=None, reference_number=None)

    sheet = _book(department_head)["2026"]

    assert [row[0] for row in _values(sheet)] == [
        early.display_reference,
        late.display_reference,
        None,
        None,
    ]
    assert [row[1] for row in _values(sheet)] == ["Z teema", "B teema", "A viiteta", "C viiteta"]


def test_reference_is_text_and_dates_are_real_dates(department_head):
    matter = _matter(
        2026,
        reference_number=14,
        received_date=date(2026, 3, 4),
        response_deadline=date(2026, 3, 25),
    )

    row = _row_for(_book(department_head), "2026", matter.title)

    assert (row["A"].value, row["A"].data_type) == ("2026_14", "s")
    assert row["D"].is_date and row["D"].value.date() == date(2026, 3, 4)
    assert row["E"].is_date and row["E"].value.date() == date(2026, 3, 25)


def test_unknown_year_goes_to_teadmata_and_never_to_a_created_at_year(department_head):
    onenote = _matter(
        None,
        origin=MatterOrigin.LEGACY_ONENOTE,
        reporting_year=2020,
        reference_year=None,
        title="OneNote teema",
    )
    yearless = _matter(None, reference_year=None, title="Aastata teema")

    book = _book(department_head)

    titles = {row[1] for row in _values(book[UNKNOWN_SHEET])}
    assert titles == {onenote.title, yearless.title}
    # Both rows were created today (2026), and neither is filed under it — nor
    # under the OneNote row's non-register 2020.
    assert book.sheetnames == [INFO_SHEET, UNKNOWN_SHEET]


def test_population_is_open_closed_and_archive_real_data_only(department_head):
    _matter(2026, title="Avatud")
    _matter(
        2026,
        title="Suletud",
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now(),
    )
    _matter(2015, title="Arhiiv", record_mode=RecordMode.ARCHIVE, origin=MatterOrigin.LEGACY_IMPORT)
    _matter(2026, title="Testkirje", data_class=MatterDataClass.TEST)
    _matter(2026, title="Kustutatud", deleted_at=timezone.now())

    book = _book(department_head)

    titles = {row[1] for name in book.sheetnames[1:] for row in _values(book[name])}
    assert titles == {"Avatud", "Suletud", "Arhiiv"}


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def test_legal_instruments_canonical_first_then_the_register_text(department_head):
    chosen = _matter(2026, title="Valitud", legal_instrument_other="Koja oma liik")
    chosen.legal_instruments.set(LegalInstrumentType.objects.filter(key__in=["seadus", "maarus"]))
    imported = _matter(2026, title="Imporditud")
    _state(imported, legal_instrument_raw="direktiiv ja määrus")

    book = _book(department_head)

    expected = "; ".join(
        [
            *LegalInstrumentType.objects.filter(key__in=["seadus", "maarus"])
            .order_by("sort_order", "label_et")
            .values_list("label_et", flat=True),
            "Koja oma liik",
        ]
    )
    assert _row_for(book, "2026", "Valitud")["C"].value == expected
    assert _row_for(book, "2026", "Imporditud")["C"].value == "direktiiv ja määrus"


def test_valja_is_the_earliest_historical_send_even_when_withdrawn(
    department_head, capture_evidence
):
    first, second = (
        factories.OrganisationFactory(name="Rahandusministeerium"),
        (factories.OrganisationFactory(name="Justiitsministeerium")),
    )
    matter = _matter(2026, addressee_organisation=factories.OrganisationFactory(name="Muu"))
    _sent(capture_evidence, matter, on=date(2026, 5, 20), to=[second])
    _sent(
        capture_evidence,
        matter,
        on=date(2026, 4, 1),
        status=SubmissionStatus.WITHDRAWN,
        to=[second, first],
    )
    factories.SubmissionFactory(matter=matter, status=SubmissionStatus.DRAFT)
    _state(matter, sent_cell="ei saatnud")

    row = _row_for(_book(department_head), "2026", matter.title)

    assert row["F"].is_date and row["F"].value.date() == date(2026, 4, 1)
    assert row["G"].value == "Justiitsministeerium; Rahandusministeerium"


def test_valja_ei_saatnud_stays_text_and_is_never_a_date(department_head):
    matter = _matter(2026)
    _state(matter, sent_cell="ei saatnud")

    cell = _row_for(_book(department_head), "2026", matter.title)["F"]

    assert cell.value == "ei saatnud"
    assert cell.data_type == "s"
    assert not cell.is_date


def test_a_register_date_in_valja_is_written_as_text_not_as_a_send(department_head):
    matter = _matter(2026)
    _state(
        matter,
        sent_cell="2026-03-12 00:00:00",
        opinion_sent_state=OpinionSentState.DATE,
        opinion_sent_date=date(2026, 3, 12),
    )
    other = _matter(2026, title="Muu märge")
    _state(other, sent_cell="vt OneNote", opinion_sent_state=OpinionSentState.RECORDED_OTHER)

    book = _book(department_head)

    cell = _row_for(book, "2026", matter.title)["F"]
    assert (cell.value, cell.data_type, cell.is_date) == ("12.03.2026", "s", False)
    assert _row_for(book, "2026", "Muu märge")["F"].value == "vt OneNote"


def test_valja_falls_back_to_the_source_cell_when_there_is_no_derived_state(department_head):
    matter = _matter(2018, origin=MatterOrigin.LEGACY_IMPORT)
    _reference(matter, sheet=2018, opinion_sent_date="ei saatnud", member_feedback_responded="3")

    row = _row_for(_book(department_head), "2018", matter.title)

    assert (row["F"].value, row["F"].data_type) == ("ei saatnud", "s")
    assert row["I"].value == 3


def _narrower_child_scope(monkeypatch, reader):
    """A reader who sees NORMAL children only, unless they take part.

    Since docs/adr/0181 every request-reachable business reader sees
    RESTRICTED children too, so no ordinary account can exercise this. The
    child scope is narrowed by hand — the shape `child_visibility_q` still
    builds for such a scope — while the Matter scope stays the real one, so
    what is tested is that the export reads Submissions and next steps
    through *their own* `visible_to` and not through the Matter's.
    """
    narrow = Scope(
        user=reader, is_authenticated=True, sees_restricted_by_role=False, break_glass_grant_id=None
    )
    for module in ("app.submissions.models", "app.workflow.models"):
        monkeypatch.setattr(f"{module}.scope_for_user", lambda user: narrow)


def test_a_restricted_send_reaches_neither_valja_nor_kellele_for_a_reader_who_cannot_see_it(
    department_head, other_specialist, capture_evidence, monkeypatch
):
    hidden = factories.OrganisationFactory(name="Salajane adressaat")
    matter = _matter(2026)
    _sent(
        capture_evidence,
        matter,
        on=date(2026, 6, 1),
        to=[hidden],
        visibility_override=Visibility.RESTRICTED,
    )
    factories.NextActionFactory(
        matter=matter, text="Salajane samm", visibility_override=Visibility.RESTRICTED
    )

    seen = _row_for(_book(department_head), "2026", matter.title)
    _narrower_child_scope(monkeypatch, other_specialist)
    unseen = _row_for(_book(other_specialist), "2026", matter.title)

    assert seen["F"].value.date() == date(2026, 6, 1)
    assert seen["G"].value == "Salajane adressaat"
    assert seen["L"].value == "Salajane samm"
    assert (unseen["F"].value, unseen["G"].value, unseen["L"].value) == (None, None, None)


def test_a_break_glass_administrator_reads_the_register(administrator):
    _matter(2026, title="Hädaolukorras nähtav")

    book = _book(grant_to(administrator))

    assert [row[1] for row in _values(book["2026"])] == ["Hädaolukorras nähtav"]


def test_kellele_comes_from_the_matter_then_only_from_a_kellele_era_column(department_head):
    own = _matter(2026, title="Teema adressaat")
    own.addressee_organisation = factories.OrganisationFactory(name="Kliimaministeerium")
    own.save()
    modern = _matter(2021, title="2021 rida", origin=MatterOrigin.LEGACY_IMPORT)
    _reference(modern, sheet=2021, addressee_organisation="Majandusministeerium")
    sender = _matter(2015, title="2015 rida", origin=MatterOrigin.LEGACY_IMPORT)
    _reference(sender, sheet=2015, source_organisation="Saatjaministeerium")
    # A derived state built from a pre-2020 sheet must not lend its text either.
    stray = _matter(2016, title="2016 seis", origin=MatterOrigin.LEGACY_IMPORT)
    _state(stray, sheet=2016, addressee_raw="Saatjaministeerium")

    book = _book(department_head)

    assert _row_for(book, "2026", "Teema adressaat")["G"].value == "Kliimaministeerium"
    assert _row_for(book, "2021", "2021 rida")["G"].value == "Majandusministeerium"
    assert _row_for(book, "2015", "2015 rida")["G"].value is None
    assert _row_for(book, "2016", "2016 seis")["G"].value is None


def test_member_counts_are_recorded_values_and_blank_is_not_zero(department_head):
    blank = _matter(2026, title="Tühi")
    _state(blank)
    zero = _matter(2026, title="Null")
    _state(zero, member_feedback_responded=0, member_feedback_requested=220)
    native = _matter(2026, title="Uus")

    book = _book(department_head)

    assert (
        _row_for(book, "2026", "Tühi")["I"].value,
        _row_for(book, "2026", "Tühi")["J"].value,
    ) == (
        None,
        None,
    )
    row = _row_for(book, "2026", "Null")
    assert (row["I"].value, row["I"].data_type, row["J"].value) == (0, "n", 220)
    assert _row_for(book, "2026", native.title)["I"].value is None


def test_owner_stage_and_next_step_are_the_current_canonical_values(department_head):
    owner = factories.UserFactory(display_name="Mari Õunapuu")
    stage = factories.StageFactory(label_et="Kooskõlastusringil")
    matter = _matter(2026, owner=owner, stage=stage)
    factories.NextActionFactory(matter=matter, text="Saada arvamus ministeeriumile")
    _state(matter, next_action_text="vana registritekst", status_label="muu")
    bare = _matter(2026, title="Ilma", owner=None)

    book = _book(department_head)

    row = _row_for(book, "2026", matter.title)
    assert row["H"].value == "Mari Õunapuu"
    assert row["K"].value == "Kooskõlastusringil"
    assert row["L"].value == "Saada arvamus ministeeriumile"
    empty = _row_for(book, "2026", bare.title)
    assert (empty["H"].value, empty["K"].value, empty["L"].value) == (None, None, None)


def test_a_formula_looking_title_is_inert_and_estonian_text_survives(department_head):
    _matter(2026, title='=HYPERLINK("https://example.invalid","klõps")')
    _matter(2026, title="Õigusakti šõäöü muutmine")

    book = _book(department_head)

    formula = _row_for(book, "2026", '=HYPERLINK("https://example.invalid","klõps")')["B"]
    assert formula.data_type == "s"
    assert formula.quotePrefix
    assert _row_for(book, "2026", "Õigusakti šõäöü muutmine")["B"].value == (
        "Õigusakti šõäöü muutmine"
    )


def test_the_information_sheet_states_the_limits(department_head):
    department_head.display_name = "Õie Täht"
    department_head.save()
    _matter(2026)

    lines = [row[0] for row in _values(_book(department_head)[INFO_SHEET])]
    text = "\n".join(lines)

    assert "Koostaja: Õie Täht" in text
    assert "Koostatud: 02.11.2026 12:15" in text
    assert "EI OLE viie" in text
    for era in ("2011–2017", "2018–2019", "2020–2022", "2023–2024", "2025–2026"):
        assert era in text
    assert "KELLELT" in text and "adressaati" in text
    assert "PRAEGUSED" in text
    assert "mitte nulli" in text
    assert "ei saatnud" in text
    assert "Ainult päris andmed" in text
    assert "edasi-tagasi ühilduvust ei taotleta" in text


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["administrator", "anonymous", "gate_without_persona"])
def test_nobody_without_a_business_reader_gets_a_row(request, who):
    _matter(2026, title="AVALIK-VANA-EXCEL-1")
    _matter(None, reference_year=None, title="AVALIK-VANA-EXCEL-2")
    user = {
        "administrator": lambda: request.getfixturevalue("administrator"),
        "anonymous": AnonymousUser,
        "gate_without_persona": lambda: DEPARTMENT_VIEWER,
    }[who]()

    book = _book(user)

    assert book.sheetnames == [INFO_SHEET, UNKNOWN_SHEET]
    assert _values(book[UNKNOWN_SHEET]) == []


def test_the_reader_role_reads_the_register(reader):
    _matter(2026, title="Lugeja näeb")

    assert [row[1] for row in _values(_book(reader)["2026"])] == ["Lugeja näeb"]


def test_the_view_serves_a_private_workbook(client, department_head):
    _matter(2026, title="Allalaaditav")
    client.force_login(department_head)

    response = client.get(reverse("reporting:export_legacy_xlsx"))

    assert response.status_code == 200
    assert response["Content-Type"] == XLSX_CONTENT_TYPE
    assert "private" in response["Cache-Control"] and "no-store" in response["Cache-Control"]
    assert legacy_filename(timezone.localdate()) in response["Content-Disposition"]
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert [row[1] for row in _values(book[str(2026)])] == ["Allalaaditav"]


def test_the_view_refuses_anonymous_and_persona_less_gate_sessions(client, settings):
    _matter(2026, title="AVALIK-VANA-EXCEL-3")
    url = reverse("reporting:export_legacy_xlsx")

    anonymous = client.get(url)
    assert anonymous.status_code == 302
    assert b"AVALIK-VANA-EXCEL-3" not in anonymous.content

    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    gated = client.get(url)
    assert gated.status_code == 302
    assert gated["Location"].startswith(reverse("accounts:choose_persona"))


def test_the_view_gives_an_administrator_an_empty_register(client, administrator):
    _matter(2026, title="AVALIK-VANA-EXCEL-4")
    client.force_login(administrator)

    response = client.get(reverse("reporting:export_legacy_xlsx"))

    assert response.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == [INFO_SHEET, UNKNOWN_SHEET]
    assert b"AVALIK-VANA-EXCEL-4" not in response.content


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def _populate(count: int, capture_evidence, *, offset: int) -> None:
    organisation = factories.OrganisationFactory()
    seadus = LegalInstrumentType.objects.get(key="seadus")
    for index in range(count):
        year = 2018 + (index % 9)
        matter = _matter(
            year,
            owner=factories.UserFactory(),
            stage=factories.StageFactory(),
            title=f"Kulu {offset + index}",
        )
        matter.legal_instruments.set([seadus])
        _sent(capture_evidence, matter, on=date(year, 3, 1), to=[organisation])
        factories.NextActionFactory(matter=matter)
        if index % 2:
            _state(matter, sheet=year, member_feedback_responded=1)
        else:
            _reference(matter, sheet=year, opinion_sent_date="ei saatnud")


def _queries(user) -> int:
    with CaptureQueriesContext(connection) as captured:
        workbook_bytes(legacy_register_workbook(user, now=NOW))
    return len(captured.captured_queries)


def test_the_query_count_does_not_grow_with_the_rows(department_head, capture_evidence):
    _populate(3, capture_evidence, offset=0)
    few = _queries(department_head)
    _populate(15, capture_evidence, offset=100)
    many = _queries(department_head)

    assert many == few
    assert few <= 10
