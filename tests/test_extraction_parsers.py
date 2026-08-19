"""Parsers, against bytes, with no database in sight.

Parsers are pure functions of a byte string by design (see
`app/documents/extraction/base.py`), which is what makes this file possible:
every case here is "given these bytes, what did it say", and none of it needs a
Matter, a user or a transaction.

The corpus is generated in `tests/synthetic_corpus.py`. Nothing here reads a
committed binary, so nothing here can accidentally read a real one.
"""

from __future__ import annotations

import pytest

from app.documents.enums import DerivativeKind, LocatorKind, TextSource
from app.documents.extraction.base import SourceFile, registry
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.ocr import missing_languages, ocr_is_available
from app.documents.extraction.parsers import registry as loaded_registry
from tests import synthetic_corpus as corpus

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
EML = "message/rfc822"
MSG = "application/vnd.ms-outlook"

requires_ocr = pytest.mark.skipif(
    not ocr_is_available(), reason="Tesseract is not installed in this environment"
)


def parse(mime_type: str, content: bytes, filename: str = "fail"):
    parser = registry.for_mime_type(mime_type)
    assert parser is not None, f"No parser is registered for {mime_type}"
    return parser.parse(SourceFile(content=content, filename=filename, mime_type=mime_type))


def all_text(result) -> str:
    return "\n".join(
        fragment.text for payload in result.derivatives for fragment in payload.fragments
    )


def locators(result) -> list[str]:
    return [
        fragment.locator_label for payload in result.derivatives for fragment in payload.fragments
    ]


# -- the registry ----------------------------------------------------------


def test_every_required_format_has_a_parser() -> None:
    """The list of formats Stage 2B must extract, asserted rather than assumed.

    A format that quietly lost its parser would look exactly like a format that
    never had one: every file of it would land in NOT_APPLICABLE with a
    plausible message.
    """
    required = {
        PDF,
        DOCX,
        XLSX,
        PPTX,
        EML,
        MSG,
        "text/plain",
        "text/csv",
        "image/png",
        "image/jpeg",
    }
    assert required <= loaded_registry.supported_mime_types()


def test_formats_we_store_but_do_not_extract_have_no_parser() -> None:
    """ZIP and the legacy binary Office formats are stored, never opened.

    Asserted so that adding a parser for one of them is a deliberate act with a
    failing test attached, rather than something that happens because a library
    turned out to accept it (Stage-2B brief 20, 21).
    """
    for mime_type in (
        "application/zip",
        "application/msword",
        "application/vnd.ms-excel",
    ):
        assert loaded_registry.for_mime_type(mime_type) is None


# -- PDF -------------------------------------------------------------------


def test_a_pdf_is_extracted_one_fragment_per_page() -> None:
    result = parse(PDF, corpus.government_pdf(), "kaaskiri.pdf")

    assert locators(result) == [f"lk {number}" for number in range(1, 7)]
    payload = result.derivatives[0]
    assert payload.metadata["page_count"] == 6
    assert all(fragment.locator_kind == LocatorKind.PAGE for fragment in payload.fragments)


def test_a_word_on_one_page_is_findable_on_that_page() -> None:
    """The whole point of a locator: not "somewhere in this file"."""
    result = parse(PDF, corpus.government_pdf())

    matches = [
        fragment
        for payload in result.derivatives
        for fragment in payload.fragments
        if corpus.ONLY_ON_PDF_PAGE_4 in fragment.text
    ]
    assert [fragment.locator["page"] for fragment in matches] == [4]


def test_estonian_characters_survive_the_round_trip() -> None:
    assert "eelnõu" in all_text(parse(PDF, corpus.government_pdf()))


def test_a_corrupt_pdf_fails_rather_than_crashing() -> None:
    with pytest.raises(ExtractionFailed) as error:
        parse(PDF, corpus.corrupt_pdf())
    assert error.value.code == "unreadable_pdf"


def test_a_locked_pdf_is_reported_and_no_password_is_guessed() -> None:
    """A protected document is a state to report, not a puzzle to solve."""
    with pytest.raises(ExtractionFailed) as error:
        parse(PDF, corpus.encrypted_pdf())
    assert error.value.code == "encrypted_pdf"
    assert "parooliga" in error.value.detail


def test_a_pdf_with_no_text_at_all_is_a_failure_not_an_empty_success(settings) -> None:
    """ "Extraction complete, no text" and "nobody looked" must not look alike."""
    settings.EXTRACTION_OCR_ENABLED = False
    with pytest.raises(ExtractionFailed) as error:
        parse(PDF, corpus.scanned_pdf())
    assert error.value.code == "no_text_layer"


def test_pages_without_text_do_not_become_empty_fragments() -> None:
    result = parse(PDF, corpus.mixed_pdf())
    assert locators(result) == ["lk 1"]
    assert result.derivatives[0].metadata["page_count"] == 3


def test_a_pdf_over_the_page_limit_is_refused_rather_than_truncated(settings) -> None:
    settings.EXTRACTION_MAX_PDF_PAGES = 3
    with pytest.raises(ExtractionFailed) as error:
        parse(PDF, corpus.government_pdf())
    assert error.value.code == "page_limit"


# -- OCR -------------------------------------------------------------------


@requires_ocr
def test_the_configured_ocr_languages_are_actually_installed() -> None:
    """Tesseract asked for a language it lacks falls back to English silently.

    That failure reaches the search index looking exactly like a successful
    extraction of Estonian text, which is why this is asserted rather than
    trusted (`app/documents/extraction/ocr.py`).
    """
    assert missing_languages() == ()


@requires_ocr
def test_a_scanned_pdf_page_is_read_by_ocr_and_says_so() -> None:
    result = parse(PDF, corpus.scanned_pdf())

    payload = result.derivatives[0]
    assert payload.kind == DerivativeKind.OCR_TEXT
    assert payload.metadata["pages_from_ocr"] == 1
    assert all(fragment.text_source == TextSource.OCR for fragment in payload.fragments)
    assert corpus.ONLY_IN_OCR_IMAGE in all_text(result).upper()


@requires_ocr
def test_a_healthy_pdf_is_never_sent_to_ocr() -> None:
    """The author's own characters are exact; a recognition engine's are not."""
    result = parse(PDF, corpus.government_pdf())

    payload = result.derivatives[0]
    assert payload.metadata["pages_from_ocr"] == 0
    assert all(fragment.text_source == TextSource.NATIVE for fragment in payload.fragments)


@requires_ocr
def test_an_image_is_read_by_ocr() -> None:
    result = parse("image/png", corpus.scanned_png(), "skann.png")

    payload = result.derivatives[0]
    assert payload.kind == DerivativeKind.OCR_TEXT
    assert corpus.ONLY_IN_OCR_IMAGE in all_text(result).upper()


def test_an_image_with_no_text_is_reported_honestly() -> None:
    if not ocr_is_available():
        with pytest.raises(ExtractionFailed) as error:
            parse("image/jpeg", corpus.photo_jpeg(), "pilt.jpg")
        assert error.value.code == "ocr_unavailable"
        return
    with pytest.raises(ExtractionFailed) as error:
        parse("image/jpeg", corpus.photo_jpeg(), "pilt.jpg")
    assert error.value.code == "no_recognisable_text"
    assert "originaal on alles" in error.value.detail


# -- Office ----------------------------------------------------------------


def test_a_docx_reports_sections_and_never_invents_pages() -> None:
    """Word paginates at render time; the file contains no page boundaries."""
    result = parse(DOCX, corpus.draft_docx(), "markused.docx")

    assert all(
        fragment.locator_kind == LocatorKind.SECTION
        for payload in result.derivatives
        for fragment in payload.fragments
    )
    assert not any("lk " in label for label in locators(result))
    assert result.derivatives[0].metadata["pagination"] == "not-available-in-format"


def test_a_docx_table_is_extracted_with_its_own_locator() -> None:
    result = parse(DOCX, corpus.draft_docx())

    table = [
        fragment
        for payload in result.derivatives
        for fragment in payload.fragments
        if corpus.ONLY_IN_DOCX_TABLE in fragment.text
    ]
    assert len(table) == 1
    assert table[0].locator == {"table": 1}


def test_an_xlsx_reports_the_sheet_and_row_range() -> None:
    result = parse(XLSX, corpus.annex_xlsx(), "lisa.xlsx")

    match = [
        fragment
        for payload in result.derivatives
        for fragment in payload.fragments
        if corpus.ONLY_ON_XLSX_SHEET_2 in fragment.text
    ]
    assert len(match) == 1
    assert match[0].locator["sheet"] == "Kulud"
    assert 'leht "Kulud"' in match[0].locator_label


def test_an_xlsx_formula_is_never_evaluated() -> None:
    """`=B2*2` with no cached value contributes nothing. It is not computed."""
    result = parse(XLSX, corpus.annex_xlsx())

    text = all_text(result)
    assert "824" not in text  # what evaluating B2*2 would have produced
    assert result.derivatives[0].metadata["formula_values"] == "cached-only"


def test_an_empty_worksheet_produces_no_fragments() -> None:
    result = parse(XLSX, corpus.annex_xlsx())
    assert not any('leht "Tühi"' in label for label in locators(result))


def test_a_pptx_reports_slide_numbers_and_labels_speaker_notes() -> None:
    result = parse(PPTX, corpus.briefing_pptx(), "ulevaade.pptx")

    assert "slaid 3" in locators(result)
    notes = [label for label in locators(result) if "esineja märkmed" in label]
    assert notes == ["slaid 2, esineja märkmed"]


def test_a_word_on_one_slide_is_located_on_that_slide() -> None:
    result = parse(PPTX, corpus.briefing_pptx())
    match = [
        fragment
        for payload in result.derivatives
        for fragment in payload.fragments
        if corpus.ONLY_ON_PPTX_SLIDE_3 in fragment.text
    ]
    assert [fragment.locator["slide"] for fragment in match] == [3]


def test_a_zip_that_is_not_an_office_document_fails_cleanly() -> None:
    with pytest.raises(ExtractionFailed) as error:
        parse(DOCX, corpus.broken_docx(), "vale.docx")
    assert error.value.code == "unreadable_docx"


def test_a_container_with_too_many_members_is_refused_before_it_is_opened(settings) -> None:
    """The check reads the central directory, not the members.

    Catching this after inflating would mean the guard runs once the damage is
    already allocated.
    """
    settings.EXTRACTION_MAX_ARCHIVE_MEMBERS = 10
    with pytest.raises(ExtractionFailed) as error:
        parse(DOCX, corpus.zip_bomb(200), "pomm.docx")
    assert error.value.code == "too_many_members"


def test_a_container_promising_too_much_uncompressed_data_is_refused(settings) -> None:
    settings.EXTRACTION_MAX_UNCOMPRESSED_BYTES = 1000
    with pytest.raises(ExtractionFailed) as error:
        parse(DOCX, corpus.zip_bomb(50), "pomm.docx")
    assert error.value.code in {"decompression_limit", "compression_ratio"}


# -- text ------------------------------------------------------------------


def test_plain_text_records_the_encoding_it_decoded_with() -> None:
    result = parse("text/plain", corpus.memo_txt(), "markmed.txt")
    assert result.derivatives[0].metadata["encoding"] in {"utf-8", "utf-8-sig"}


def test_legacy_estonian_text_is_still_readable() -> None:
    content = corpus.memo_txt().decode("utf-8").encode("cp1257")
    result = parse("text/plain", content, "vana.txt")

    assert result.derivatives[0].metadata["encoding"] == "cp1257"
    assert "Märkmed" in all_text(result)


def test_undecodable_bytes_are_refused_rather_than_mangled() -> None:
    """`õ` quietly becoming `?` indexes and searches like correct text."""
    with pytest.raises(ExtractionFailed) as error:
        parse("text/plain", corpus.undecodable_txt(), "prügi.txt")
    assert error.value.code in {"undecodable_text", "not_text"}


def test_a_csv_delimiter_is_detected_not_assumed() -> None:
    result = parse("text/csv", corpus.counts_csv(), "kogused.csv")
    assert result.derivatives[0].metadata["delimiter"] == ";"
    assert "plast" in all_text(result)


def test_a_character_budget_refuses_rather_than_truncating(settings) -> None:
    settings.EXTRACTION_MAX_CHARACTERS = 50
    with pytest.raises(ExtractionFailed) as error:
        parse(PDF, corpus.government_pdf())
    assert error.value.code == "character_limit"


# -- email -----------------------------------------------------------------


def test_an_eml_yields_metadata_and_body_as_separate_derivatives() -> None:
    result = parse(EML, corpus.consultation_eml(), "kiri.eml")

    kinds = [payload.kind for payload in result.derivatives]
    assert DerivativeKind.EMAIL_METADATA in kinds
    assert DerivativeKind.EXTRACTED_TEXT in kinds


def test_an_eml_preserves_the_headers_that_reconstruct_a_conversation() -> None:
    """No thread model is built. The evidence for one is kept anyway."""
    result = parse(EML, corpus.consultation_eml())

    metadata = next(
        payload.metadata
        for payload in result.derivatives
        if payload.kind == DerivativeKind.EMAIL_METADATA
    )
    assert metadata["message_id"] == "<katse-1@naidisministeerium.invalid>"
    assert metadata["in_reply_to"] == "<katse-0@naidisministeerium.invalid>"
    assert metadata["references"] == ["<katse-0@naidisministeerium.invalid>"]
    assert metadata["from_email"] == "kadri@naidisministeerium.invalid"
    assert any("oigus@koda.invalid" in address for address in metadata["to"])


def test_an_absent_header_is_absent_rather_than_invented() -> None:
    result = parse(EML, corpus.consultation_eml())
    metadata = next(
        payload.metadata
        for payload in result.derivatives
        if payload.kind == DerivativeKind.EMAIL_METADATA
    )
    assert "bcc" not in metadata


def test_an_html_only_body_is_reduced_to_text_and_scripts_are_removed() -> None:
    """Removing the element, not merely its tags.

    A sanitiser that stripped tags and kept text would fold the stylesheet and
    the script body into the indexed content.
    """
    result = parse(
        EML,
        corpus.consultation_eml(with_html=True, attachments=False, inline_logo=False),
        "kiri.eml",
    )

    body = next(
        fragment.text
        for payload in result.derivatives
        for fragment in payload.fragments
        if fragment.locator_label == "kirja sisu"
    )
    assert "alert(" not in body
    assert "color:red" not in body
    # No markup survives into the body. Checked on the body alone: the header
    # fragment legitimately contains angle brackets, because that is how an
    # email address is written.
    assert "<" not in body
    assert corpus.ONLY_IN_EMAIL_BODY in body


def test_an_email_does_not_fetch_anything_it_references() -> None:
    """The tracking pixel's host must never be contacted.

    Asserted by the absence of the URL in the output rather than by mocking the
    network: nothing in the parser has a client to mock, which is the property
    worth keeping.
    """
    result = parse(EML, corpus.consultation_eml(with_html=True, attachments=False))
    assert "tracker.invalid" not in all_text(result)


def test_attachments_are_returned_and_inline_resources_are_marked_apart() -> None:
    result = parse(EML, corpus.consultation_eml())

    real = [attachment for attachment in result.attachments if not attachment.inline]
    inline = [attachment for attachment in result.attachments if attachment.inline]
    assert sorted(attachment.filename for attachment in real) == ["lisa-1.pdf", "markmed.txt"]
    assert [attachment.filename for attachment in inline] == ["allkiri-logo.png"]


def test_too_many_attachments_is_refused_rather_than_trimmed(settings) -> None:
    settings.EXTRACTION_MAX_EMAIL_ATTACHMENTS = 1
    with pytest.raises(ExtractionFailed) as error:
        parse(EML, corpus.consultation_eml())
    assert error.value.code == "attachment_limit"


def test_a_malformed_eml_does_not_crash_and_invents_nothing() -> None:
    """Python's parser is tolerant. Tolerance must not become invention."""
    result = parse(EML, corpus.malformed_eml(), "katkine.eml")

    metadata = next(
        payload.metadata
        for payload in result.derivatives
        if payload.kind == DerivativeKind.EMAIL_METADATA
    )
    assert metadata["subject"] == "katkine"
    assert "sent_at" not in metadata
    assert "message_id" not in metadata


def test_an_empty_message_is_a_failure() -> None:
    with pytest.raises(ExtractionFailed) as error:
        parse(EML, b"", "tühi.eml")
    assert error.value.code == "empty_message"


def test_an_outlook_message_produces_the_same_shape_as_an_eml() -> None:
    """Two libraries, one output shape, so the rules are tested once."""
    result = parse(MSG, corpus.outlook_msg(), "kiri.msg")

    kinds = [payload.kind for payload in result.derivatives]
    assert DerivativeKind.EMAIL_METADATA in kinds
    assert DerivativeKind.EXTRACTED_TEXT in kinds
    assert corpus.ONLY_IN_EMAIL_BODY in all_text(result)


def test_an_outlook_attachment_is_returned_with_its_filename() -> None:
    result = parse(MSG, corpus.outlook_msg(), "kiri.msg")

    assert [attachment.filename for attachment in result.attachments] == ["lisa-1.pdf"]
    assert result.attachments[0].content.startswith(b"%PDF")


def test_a_corrupt_msg_fails_cleanly() -> None:
    with pytest.raises(ExtractionFailed) as error:
        parse(MSG, b"not an OLE file at all", "katkine.msg")
    assert error.value.code == "unreadable_msg"
