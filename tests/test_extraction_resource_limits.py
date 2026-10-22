"""Extraction stays bounded on hostile input, and ordinary files read as before.

Three findings of the 2026-10-22 security scan, and the second line behind all
of them (docs/adr/0176):

* csf_a74bfe6e — an XLSX declaring a huge used range made openpyxl walk every
  empty cell of it, and empty cells charged no budget;
* csf_8da83d23 — EML and MSG headers and bodies never met the character budget;
* csf_5fa01ed3 — a CSV was materialised whole (``list(csv.reader(...))``)
  before any limit applied;
* and ``guard_parse_result``, the postcondition every parser's output meets in
  ``parse_source`` whatever the parser did or forgot.

Every hostile file is generated here, small, in the test. Each refusal is
named by its code: a test that accepted *any* failure would keep passing after
the rule it is named for had gone.
"""

from __future__ import annotations

import csv
import io
import random
import re
import time
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.enums import DerivativeKind, ExtractionState, LocatorKind
from app.documents.extraction import email_common, email_eml, orchestrator
from app.documents.extraction import text as text_module
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParseResult,
    ParserRegistry,
    SourceFile,
    normalise,
    registry,
)
from app.documents.extraction.errors import ExtractionFailed
from app.matters import intake_extraction
from app.matters.staging import MatterIntakeFile
from tests import synthetic_corpus as corpus

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"
CSV = "text/csv"


def parse(mime_type: str, content: bytes, filename: str = "fail"):
    parser = registry.for_mime_type(mime_type)
    assert parser is not None
    return parser.parse(SourceFile(content=content, filename=filename, mime_type=mime_type))


def refused_with(code: str, mime_type: str, content: bytes) -> ExtractionFailed:
    with pytest.raises(ExtractionFailed) as error:
        parse(mime_type, content)
    assert error.value.code == code, (error.value.code, error.value.detail)
    return error.value


def fragments_of(result) -> list[tuple[str, dict]]:
    return [
        (fragment.text, fragment.locator)
        for payload in result.derivatives
        for fragment in payload.fragments
    ]


# ---------------------------------------------------------------------------
# XLSX — csf_a74bfe6e
# ---------------------------------------------------------------------------


def _xlsx(build) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    build(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _without_dimension_record(content: bytes) -> bytes:
    """The same workbook with every ``<dimension>`` removed, as some generators write it."""
    source = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith("xl/worksheets/"):
                data = re.sub(rb"<dimension[^>]*/>", b"", data)
            target.writestr(info, data)
    return out.getvalue()


def _whole_grid(workbook) -> None:
    """One word in A1 and one styled, empty cell at XFD1048576: a 5 KB file."""
    from openpyxl.styles import Font

    sheet = workbook.active
    sheet["A1"] = "Tere"
    sheet.cell(row=1_048_576, column=16_384).font = Font(bold=True)


@pytest.fixture
def no_walk(monkeypatch):
    """Fail loudly if openpyxl starts walking a sheet's rows."""
    from openpyxl.worksheet._read_only import ReadOnlyWorksheet

    def walked(*args, **kwargs):
        raise AssertionError("the sheet's rows were walked")

    monkeypatch.setattr(ReadOnlyWorksheet, "_cells_by_row", walked)


def test_a_sheet_declaring_the_whole_grid_is_refused_before_a_row_is_read(no_walk) -> None:
    """17 billion declared cells around one word: refused from the dimension record."""
    content = _xlsx(_whole_grid)
    assert len(content) < 20_000

    started = time.monotonic()
    error = refused_with("sheet_dimension_limit", XLSX, content)

    assert time.monotonic() - started < 5
    assert "1048576 rida × 16384 veergu" in error.detail


def test_empty_rows_are_charged_to_the_walk_budget(settings) -> None:
    """With no dimension record to check, the walk itself is what is counted.

    Two words 5 000 rows apart produce two short lines of text and used to cost
    nothing; the 4 998 empty rows between them are now charged one cell each.
    """
    settings.EXTRACTION_MAX_TABLE_CELLS = 1_000

    def far_apart(workbook) -> None:
        workbook.active["A1"] = "Algus"
        workbook.active["A5000"] = "Lõpp"

    refused_with("table_cell_limit", XLSX, _without_dimension_record(_xlsx(far_apart)))


def test_rows_past_the_row_budget_are_refused_while_walking(settings) -> None:
    settings.EXTRACTION_MAX_TABLE_ROWS = 1_000

    def far_down(workbook) -> None:
        workbook.active["A1"] = "Algus"
        workbook.active["A2000"] = "Lõpp"

    refused_with("table_row_limit", XLSX, _without_dimension_record(_xlsx(far_down)))


def test_a_row_wider_than_the_column_budget_is_refused() -> None:
    def wide(workbook) -> None:
        workbook.active.cell(row=1, column=3_000, value="kaugel paremal")

    refused_with("table_column_limit", XLSX, _without_dimension_record(_xlsx(wide)))


def test_the_walk_budget_is_shared_by_every_sheet_of_the_workbook(settings) -> None:
    """A thousand modest sheets cost what one large one does."""
    settings.EXTRACTION_MAX_TABLE_CELLS = 1_000

    def two_sheets(workbook) -> None:
        for sheet in (workbook.active, workbook.create_sheet("Teine")):
            sheet["A1"] = "Tere"
            sheet["T30"] = "Lõpp"  # 30 rows x 20 columns = 600 cells each

    refused_with("sheet_dimension_limit", XLSX, _xlsx(two_sheets))


def test_an_ordinary_workbook_reads_exactly_as_before() -> None:
    result = parse(XLSX, corpus.annex_xlsx())

    assert fragments_of(result) == [
        (
            "1: Näitaja | Väärtus\n2: Ettevõtjaid | 412",
            {"sheet": "Kokkuvõte", "row_from": 1, "row_to": 3},
        ),
        (
            "1: Kuluartikkel\n2: hoiustamiskulu | 1840",
            {"sheet": "Kulud", "row_from": 1, "row_to": 2},
        ),
    ]
    assert result.derivatives[0].metadata == {
        "sheet_count": 3,
        "sheets": ["Kokkuvõte", "Kulud", "Tühi"],
        "formula_values": "cached-only",
    }


def test_a_register_of_thousands_of_rows_is_read_whole() -> None:
    """The historical registers are a few thousand rows; none of them is near a limit."""
    rows, columns = 5_000, 12

    def register(workbook) -> None:
        sheet = workbook.active
        sheet.title = "Register"
        for row in range(1, rows + 1):
            sheet.cell(row=row, column=1, value=f"Teema {row}")
            if row % 3:
                sheet.cell(row=row, column=columns, value=f"Õiguslik märkus {row}")

    result = parse(XLSX, _xlsx(register))

    fragments = fragments_of(result)
    assert len(fragments) == rows // 50
    assert fragments[0][1] == {"sheet": "Register", "row_from": 1, "row_to": 50}
    assert fragments[-1][1] == {"sheet": "Register", "row_from": 4951, "row_to": 5000}
    assert fragments[0][0].splitlines()[:3] == [
        "1: Teema 1 | Õiguslik märkus 1",
        "2: Teema 2 | Õiguslik märkus 2",
        "3: Teema 3",
    ]


# ---------------------------------------------------------------------------
# CSV — csf_5fa01ed3
# ---------------------------------------------------------------------------


def _reference_csv(content: bytes):
    """The 4818203e algorithm, kept here as the definition of "unchanged output"."""
    text, encoding = text_module.decode_text(content)
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    fragments = []
    for start in range(0, len(rows), text_module.LINES_PER_FRAGMENT):
        chunk = rows[start : start + text_module.LINES_PER_FRAGMENT]
        body = normalise(
            "\n".join(
                " | ".join(cell.strip() for cell in row if cell.strip())
                for row in chunk
                if any(cell.strip() for cell in row)
            )
        )
        if body:
            fragments.append((body, {"row_from": start + 1, "row_to": start + len(chunk)}))
    return fragments, {"encoding": encoding, "delimiter": delimiter, "row_count": len(rows)}


ESTONIAN_CSV = (
    "Ettevõtja;Pakendiliik;Kogus\n"
    'Õun ÄÖÜ OÜ;plast;"12,5"\n'
    "\n"
    ";;\n"
    'Ülle Õis AS;klaas;"rida 1\nrida 2"\n'
)


@pytest.mark.parametrize(("encoding", "detected"), [("utf-8", "utf-8-sig"), ("cp1257", "cp1257")])
def test_a_semicolon_separated_estonian_csv_reads_exactly_as_before(encoding, detected) -> None:
    result = parse(CSV, ESTONIAN_CSV.encode(encoding))

    assert fragments_of(result) == [
        (
            "Ettevõtja | Pakendiliik | Kogus\n"
            "Õun ÄÖÜ OÜ | plast | 12,5\n"
            "Ülle Õis AS | klaas | rida 1\nrida 2",
            {"row_from": 1, "row_to": 5},
        )
    ]
    assert result.derivatives[0].fragments[0].locator_kind == LocatorKind.LINE_RANGE
    assert result.derivatives[0].fragments[0].locator_label == "read 1–5"
    assert result.derivatives[0].metadata == {
        "encoding": detected,
        "delimiter": ";",
        "row_count": 5,
    }


def test_row_locators_still_count_records_in_blocks_of_two_hundred() -> None:
    lines = ["Nimi,Summa"] + [f"Rida {n},{n}" for n in range(1, 450)]
    lines[300] = ","  # an empty record still counts as a row
    result = parse(CSV, ("\n".join(lines) + "\n").encode())

    assert [locator for _text, locator in fragments_of(result)] == [
        {"row_from": 1, "row_to": 200},
        {"row_from": 201, "row_to": 400},
        {"row_from": 401, "row_to": 450},
    ]


def test_the_streaming_reader_matches_the_old_one_on_ordinary_files() -> None:
    """Byte for byte, on a spread of shapes: quoting, blanks, commas, tabs, CRLF."""
    rng = random.Random(20261022)  # noqa: S311 - synthetic test text, not a secret
    cells = ["Näidis OÜ", "", "  12,5 ", '"a;b"', '"x\ny"', "Õ", "ÄÖÜ ä"]
    samples = [ESTONIAN_CSV, corpus.counts_csv().decode()]
    for _ in range(60):
        delimiter = rng.choice([";", ",", "\t"])
        newline = rng.choice(["\n", "\r\n"])
        rows = rng.randint(1, 650)
        samples.append(
            newline.join(
                delimiter.join(rng.choice(cells) for _ in range(rng.randint(1, 5)))
                for _ in range(rows)
            )
        )
    for sample in samples:
        content = sample.encode()
        expected_fragments, expected_metadata = _reference_csv(content)
        result = parse(CSV, content)
        assert fragments_of(result) == expected_fragments
        assert result.derivatives[0].metadata == expected_metadata


@pytest.fixture
def csv_rows_built(monkeypatch) -> list[int]:
    """The width of every row the csv module hands the parser."""
    widths: list[int] = []
    real_reader = csv.reader

    def counting_reader(*args, **kwargs):
        for row in real_reader(*args, **kwargs):
            widths.append(len(row))
            yield row

    monkeypatch.setattr(text_module.csv, "reader", counting_reader)
    return widths


def test_a_csv_over_the_size_limit_is_refused_before_it_is_decoded(settings, monkeypatch) -> None:
    settings.EXTRACTION_MAX_CSV_BYTES = 1_024

    def decoded(*args, **kwargs):
        raise AssertionError("the oversized file was decoded")

    monkeypatch.setattr(text_module, "decode_text", decoded)

    refused_with("csv_size_limit", CSV, b"a;b\n" * 600)


def test_one_enormous_record_is_refused_before_the_csv_module_builds_it(csv_rows_built) -> None:
    """Three million separators would be three million cells in one list."""
    content = b"Nimi;Summa\n" + b"Rida;1\n" * 1_200 + b";" * 3_000_000 + b"\n"

    refused_with("csv_record_limit", CSV, content)

    # The ordinary rows were read; the enormous one never became a list.
    assert csv_rows_built == [2] * 1_201


def test_a_huge_cell_is_refused_with_its_own_code() -> None:
    content = b'Nimi;Kirjeldus\n"Tere";"' + b"x" * 200_000 + b'"\n'

    refused_with("cell_character_limit", CSV, content)


def test_rows_past_the_row_budget_are_refused_while_reading(settings, csv_rows_built) -> None:
    settings.EXTRACTION_MAX_TABLE_ROWS = 1_000
    content = b"Nimi;Summa\n" + b"\n" * 50_000

    refused_with("table_row_limit", CSV, content)

    assert len(csv_rows_built) == 1_001


def test_cells_past_the_cell_budget_are_refused_while_reading(settings, csv_rows_built) -> None:
    settings.EXTRACTION_MAX_TABLE_CELLS = 500
    content = ("\n".join(";".join(["x"] * 10) for _ in range(1_000)) + "\n").encode()

    refused_with("table_cell_limit", CSV, content)

    assert len(csv_rows_built) == 51


def test_a_csv_over_the_character_budget_is_refused_while_reading(settings, csv_rows_built) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 5_000
    content = ("\n".join(f"Ettevõte {n};{n}" for n in range(5_000)) + "\n").encode()

    refused_with("character_limit", CSV, content)

    assert len(csv_rows_built) < 1_000


def test_a_malformed_csv_is_a_named_failure_rather_than_a_crash() -> None:
    refused_with("unreadable_csv", CSV, b'Nimi;Summa\n"Tere";1\r2\n' * 3)


# ---------------------------------------------------------------------------
# EML and MSG — csf_8da83d23
# ---------------------------------------------------------------------------


def _eml(*, subject: str = "Kooskõlastus", body: str = "Tere", html: str | None = None) -> bytes:
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Kadri Näidis <kadri@naidisministeerium.invalid>"
    message["To"] = "oigus@koda.invalid"
    if html is None:
        message.set_content(body)
    else:
        message.set_content(html, subtype="html")
    return message.as_bytes()


def test_an_oversized_eml_body_is_refused(settings) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 2_000

    refused_with("character_limit", EML, _eml(body="Õiguslik sisu. " * 400))


def test_oversized_eml_headers_are_refused(settings) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 2_000

    refused_with("character_limit", EML, _eml(subject="Pealkiri " * 300))


def test_an_eml_html_body_over_the_input_limit_is_refused_before_it_is_flattened(
    settings, monkeypatch
) -> None:
    settings.EXTRACTION_MAX_EMAIL_BODY_CHARACTERS = 10_000

    def flattened(*args, **kwargs):
        raise AssertionError("the oversized body was flattened")

    monkeypatch.setattr(email_eml, "html_body_to_text", flattened)

    refused_with("email_body_limit", EML, _eml(html="<p>Tere</p>" * 2_000))


def test_html_that_flattens_past_the_budget_is_refused(settings) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 2_000

    refused_with("character_limit", EML, _eml(html="<p>Õiguslik sisu</p>" * 400))


def test_an_oversized_outlook_body_is_refused(settings) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 2_000

    refused_with("character_limit", MSG, corpus.outlook_msg(body="Õiguslik sisu. " * 400))


def test_an_oversized_outlook_subject_is_refused(settings) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 2_000

    refused_with("character_limit", MSG, corpus.outlook_msg(subject="Pealkiri " * 300))


def test_an_outlook_html_body_over_the_input_limit_is_refused(settings) -> None:
    settings.EXTRACTION_MAX_EMAIL_BODY_CHARACTERS = 10_000

    refused_with("email_body_limit", MSG, corpus.outlook_msg(html_body="<p>Tere</p>" * 2_000))


def test_ordinary_messages_keep_their_text_and_attachments() -> None:
    eml = parse(EML, corpus.consultation_eml(with_html=True))
    msg = parse(MSG, corpus.outlook_msg())

    for result in (eml, msg):
        text = "\n".join(fragment for fragment, _locator in fragments_of(result))
        assert corpus.ONLY_IN_EMAIL_BODY in text
        assert corpus.DRAFT_TITLE in text
        assert result.derivatives[0].kind == DerivativeKind.EMAIL_METADATA
    assert [a.filename for a in eml.attachments if not a.inline] == ["lisa-1.pdf", "markmed.txt"]
    assert [a.filename for a in msg.attachments] == ["lisa-1.pdf"]


def test_script_and_style_are_stripped_in_linear_time() -> None:
    """The old expression rescanned the body for every unclosed opening tag.

    Forty thousand ``<script `` openings took it most of a minute; the
    replacement is a lookup per opening.
    """
    hostile = "<p>Tere</p>" + "<script " * 40_000 + "<style>" * 40_000

    started = time.monotonic()
    text = email_common.html_body_to_text(hostile)

    assert time.monotonic() - started < 5
    assert text.startswith("Tere")


def test_script_and_style_stripping_removes_exactly_what_it_used_to() -> None:
    old = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
    pieces = [
        "<script>",
        "</script>",
        "<SCRIPT a=1>",
        "</Script>",
        "<style>",
        "</style>",
        "<scripts>",
        "<style ",
        ">",
        "tekst",
        "<",
        "</style >",
        "\n",
        "</STYLE>",
        "<script/>",
    ]
    rng = random.Random(8)  # noqa: S311 - synthetic test text, not a secret
    for _ in range(5_000):
        html = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
        assert email_common._strip_script_and_style(html) == old.sub(" ", html), html


# ---------------------------------------------------------------------------
# The central postcondition
# ---------------------------------------------------------------------------


class _CarelessParser:
    """A parser that enforces nothing, standing in for one written next year."""

    name = "careless"
    version = "1"
    mime_types = frozenset({"application/x-careless"})

    def __init__(self, result: ParseResult) -> None:
        self._result = result

    def parse(self, source: SourceFile) -> ParseResult:
        return self._result


def _outcome_of(monkeypatch, result: ParseResult):
    careless = ParserRegistry()
    careless.register(_CarelessParser(result))
    monkeypatch.setattr(orchestrator, "registry", careless)
    return orchestrator.parse_source(
        filename="x.bin", mime_type="application/x-careless", load=lambda: b"x"
    )


def _text(*fragments: str, metadata: dict | None = None) -> ParseResult:
    return ParseResult(
        derivatives=(
            DerivativePayload(
                fragments=tuple(Fragment(text=text) for text in fragments),
                metadata=metadata or {},
            ),
        )
    )


def test_an_oversized_fragment_from_any_parser_is_refused_centrally(settings, monkeypatch) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 100

    outcome = _outcome_of(monkeypatch, _text("x" * 101))

    assert outcome.state == ExtractionState.FAILED
    assert outcome.error_code == "character_limit"
    assert outcome.result is None
    assert outcome.parser is not None and outcome.parser.name == "careless"


def test_the_budget_is_across_derivatives_not_per_fragment(settings, monkeypatch) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 100

    outcome = _outcome_of(monkeypatch, _text("x" * 60, "y" * 60))

    assert outcome.error_code == "character_limit"


def test_too_many_fragments_from_any_parser_are_refused(settings, monkeypatch) -> None:
    settings.EXTRACTION_MAX_FRAGMENTS = 10

    outcome = _outcome_of(monkeypatch, _text(*["x"] * 11))

    assert outcome.error_code == "fragment_limit"


def test_oversized_metadata_from_any_parser_is_refused(settings, monkeypatch) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 100

    outcome = _outcome_of(monkeypatch, _text("x", metadata={"references": ["<id>" * 10] * 5}))

    assert outcome.error_code == "metadata_limit"


def test_output_within_the_limits_passes_untouched(settings, monkeypatch) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 100
    result = _text("x" * 50, "y" * 50)

    outcome = _outcome_of(monkeypatch, result)

    assert outcome.state == ExtractionState.DONE
    assert outcome.result is result


# ---------------------------------------------------------------------------
# A refusal is an ordinary FAILED file, and the queue moves on
# ---------------------------------------------------------------------------


def test_an_oversized_staged_email_fails_and_the_reader_reads_the_next_file(
    signed_in, settings
) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 2_000
    files = [
        SimpleUploadedFile("suur.eml", _eml(body="Õiguslik sisu. " * 400), content_type=EML),
        SimpleUploadedFile(
            "kaaskiri.pdf",
            corpus.text_pdf(["Näidisministeerium\n\nKiri"]),
            content_type="application/pdf",
        ),
    ]
    response = signed_in.post(reverse("matters:intake_stage"), {"files": files})
    assert response.status_code in (200, 400), response.status_code
    staged = {
        row.original_filename: row
        for row in MatterIntakeFile.objects.filter(session=response.context["intake_session"])
    }
    assert set(staged) == {"suur.eml", "kaaskiri.pdf"}

    reports = {report.file_id: report for report in intake_extraction.drain()}

    big, letter = staged["suur.eml"], staged["kaaskiri.pdf"]
    assert reports[big.pk].state == ExtractionState.FAILED
    assert reports[big.pk].error_code == "character_limit"
    assert reports[letter.pk].state == ExtractionState.DONE
    big.refresh_from_db()
    assert big.extraction_state == ExtractionState.FAILED
    assert big.extraction_claimed_at is None
    assert not big.text
    assert intake_extraction.drain() == []


def test_an_oversized_evidence_email_is_recorded_failed_with_its_reason(
    normal_matter, capture_evidence, extract, settings
) -> None:
    from app.documents.models import DocumentDerivative, DocumentTextFragment

    settings.EXTRACTION_MAX_CHARACTERS = 2_000
    version = capture_evidence(normal_matter, _eml(body="Õiguslik sisu. " * 400), "suur.eml", EML)

    report = extract(version)

    assert report.state == ExtractionState.FAILED
    assert report.error_code == "character_limit"
    version.refresh_from_db()
    assert version.extraction_state == ExtractionState.FAILED
    failure = DocumentDerivative.objects.get(version=version)
    assert failure.error_code == "character_limit"
    assert failure.generator == "eml"
    assert not DocumentTextFragment.objects.filter(derivative__version=version).exists()


def test_a_sheet_touched_at_excels_last_row_still_reads_under_the_defaults():
    """Ctrl+Down to row 1 048 576 and a value there: legitimate, and not refused.

    Ten columns wide, so the walk is ~10 M visited cells — inside the default
    budget. Only the declared width or the total cell count refuses a sheet,
    never its height alone.
    """

    def last_row(workbook) -> None:
        sheet = workbook.active
        sheet["A1"] = "Pealkiri"
        sheet.cell(row=1_048_576, column=10, value="viimane rida")

    result = parse(XLSX, _xlsx(last_row))

    text = " ".join(text for text, _locator in fragments_of(result))
    assert "Pealkiri" in text
    assert "viimane rida" in text


# ---------------------------------------------------------------------------
# Paths that call a parser directly meet the same postcondition
# ---------------------------------------------------------------------------


def test_an_oversized_opinion_archive_parse_is_refused_by_the_postcondition(
    db, settings, monkeypatch
) -> None:
    """`opinion_text` calls `PdfParser` itself, outside `parse_source`.

    A parser that returned more than the limits allow used to be stored there
    (cut to the archive's own excerpt length and marked DONE); it is now FAILED
    with the postcondition's code, like every other path.
    """
    import datetime

    from django.core.files.base import ContentFile
    from django.utils import timezone

    from app.documents.extraction import pdf as pdf_module
    from app.documents.services import evidence_storage
    from app.legacy_import import opinion_text
    from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
    from app.legacy_import.opinion_enums import ArchiveTextState

    settings.EXTRACTION_MAX_CHARACTERS = 100
    settings.REAL_DATA_ALLOWED = False
    monkeypatch.setattr(pdf_module.PdfParser, "parse", lambda self, source: _text("x" * 101))

    sha = "d" * 64
    key = evidence_storage().save(
        f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}", ContentFile(b"%PDF-1.4\n")
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=9,
        mime_type="application/pdf",
        storage_key=key,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.make_aware(datetime.datetime(2026, 1, 1)),
    )

    report = opinion_text.extract_all()

    assert report.failed == 1
    assert report.extracted == 0
    text = OpinionArchiveText.objects.get(binary=binary)
    assert text.state == ArchiveTextState.FAILED
    assert text.note.startswith("character_limit")
    assert not text.body


def test_no_path_outside_parse_source_calls_a_parser_without_the_postcondition() -> None:
    """Every direct parser call in the application states the guard itself.

    `parse_source` applies `guard_parse_result` to whatever a parser returns. A
    module that calls a parser directly — a registry lookup, or a parser class
    instantiated and asked to parse — skips that unless it calls the guard as
    well, which is how `opinion_text` had escaped it.
    """
    from pathlib import Path

    from django.conf import settings as django_settings

    app = Path(django_settings.BASE_DIR) / "app"
    extraction = app / "documents" / "extraction"
    direct = re.compile(r"Parser\(\)\.parse\(|\.for_mime_type\(|\bparser\.parse\(")
    unguarded = []
    for path in sorted(app.rglob("*.py")):
        if extraction in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        if direct.search(source) and "guard_parse_result(" not in source:
            unguarded.append(path.relative_to(app.parent).as_posix())
    assert unguarded == []
