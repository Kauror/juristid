"""PDF: one fragment per page, and OCR only for the pages that need it.

Pages are kept separate all the way through. Flattening a 200-page draft into
one string before deciding locators would make every match say "somewhere in
this file", and `lk 17` is most of what makes a document search useful to
somebody who has to quote it.

The mixed case is the normal case. A ministry sends a typed covering letter with
a photographed annex stapled behind it, in one PDF. Deciding per file — "this
one is scanned" — gets that wrong in both directions, so the decision is made per
page against the text actually present on it.
"""

from __future__ import annotations

import io
import logging

from django.conf import settings

from app.documents.enums import DerivativeKind, LocatorKind, TextSource
from app.documents.extraction.base import (
    DerivativePayload,
    Fragment,
    ParseResult,
    SourceFile,
    normalise,
    registry,
)
from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import current_limits, guard_character_budget
from app.documents.extraction.ocr import (
    OcrUnavailable,
    ocr_engine_version,
    ocr_is_available,
    recognise_pil_image,
)
from app.documents.extraction.thumbnails import pdf_first_page_thumbnail

logger = logging.getLogger(__name__)


class PdfParser:
    name = "pdf"
    version = "1"
    mime_types = frozenset({"application/pdf"})

    def parse(self, source: SourceFile) -> ParseResult:
        import pypdf

        limits = current_limits()
        try:
            reader = pypdf.PdfReader(io.BytesIO(source.content))
        except Exception as error:
            raise ExtractionFailed(
                "unreadable_pdf", "PDF-i ei õnnestunud avada; fail võib olla rikutud."
            ) from error

        if reader.is_encrypted:
            # An empty user password is how "restricted printing" PDFs are
            # produced and is not a lock anybody intended. Anything else is a
            # document somebody chose to protect: it is recorded as needing
            # attention, and no password is guessed at (Stage-2B brief 13).
            if not _try_empty_password(reader):
                raise ExtractionFailed(
                    "encrypted_pdf",
                    "PDF on parooliga kaitstud. Sisu ei eraldata; originaal on alles.",
                )

        try:
            page_count = len(reader.pages)
        except Exception as error:
            raise ExtractionFailed("unreadable_pdf", "PDF-i lehekülgi ei õnnestunud lugeda.") from (
                error
            )

        if page_count == 0:
            raise ExtractionFailed("empty_pdf", "PDF ei sisalda ühtegi lehekülge.")
        if page_count > limits.max_pdf_pages:
            raise ExtractionFailed(
                "page_limit",
                f"PDF-is on {page_count} lehekülge, lubatud on {limits.max_pdf_pages}.",
            )

        native = _native_text_per_page(reader, page_count)
        thin = [
            number
            for number, text in enumerate(native, start=1)
            if len(text) < settings.EXTRACTION_OCR_MIN_NATIVE_CHARACTERS
        ]

        ocr_text: dict[int, str] = {}
        ocr_version = ""
        ocr_note = ""
        if thin and ocr_is_available():
            try:
                ocr_version = ocr_engine_version()
                ocr_text = _ocr_pages(source.content, thin)
            except OcrUnavailable as error:  # pragma: no cover - probed above
                ocr_note = f"OCR ei olnud kättesaadav: {error}"
        elif thin:
            ocr_note = "OCR ei ole selles keskkonnas sisse lülitatud."

        fragments: list[Fragment] = []
        total = 0
        ocr_pages = 0
        for number in range(1, page_count + 1):
            text = normalise(native[number - 1])
            origin = TextSource.NATIVE
            recognised = normalise(ocr_text.get(number, ""))
            if number in thin and recognised:
                # OCR wins only where the native layer was too thin to be the
                # document's real text. Where both exist, the author's own
                # characters are exact and a recognition engine's are not.
                text = recognised
                origin = TextSource.OCR
                ocr_pages += 1
            if not text:
                continue
            total += len(text)
            guard_character_budget(total, limits)
            fragments.append(
                Fragment(
                    text=text,
                    locator_kind=LocatorKind.PAGE,
                    locator={"page": number},
                    locator_label=f"lk {number}",
                    text_source=origin,
                )
            )

        notes = [note for note in (ocr_note, _coverage_note(fragments, page_count)) if note]
        # A PDF that yields nothing at all is reported, not quietly stored as an
        # empty success. "Extraction complete, no text" and "extraction never
        # looked" are indistinguishable to everybody downstream.
        if not fragments:
            raise ExtractionFailed(
                "no_text_layer",
                "PDF-ist ei leitud teksti ja OCR ei andnud tulemust. "
                + (ocr_note or "Tegemist võib olla pildifailiga."),
            )

        kind = (
            DerivativeKind.OCR_TEXT
            if ocr_pages == len(fragments)
            else DerivativeKind.EXTRACTED_TEXT
        )
        payloads = [
            DerivativePayload(
                kind=kind,
                fragments=tuple(fragments),
                metadata={
                    "page_count": page_count,
                    "pages_with_text": len(fragments),
                    "pages_from_ocr": ocr_pages,
                    "ocr_engine": ocr_version,
                    "ocr_languages": settings.EXTRACTION_OCR_LANGUAGES if ocr_pages else "",
                },
            )
        ]
        # The picture matters most exactly where the text is least trustworthy:
        # on a scanned annex, the extracted text is a recognition guess and the
        # first page is what a lawyer checks it against.
        thumbnail = pdf_first_page_thumbnail(source.content, limits)
        if thumbnail is not None:
            payloads.append(thumbnail)

        return ParseResult(derivatives=tuple(payloads), note=" ".join(notes))


def _try_empty_password(reader: object) -> bool:
    try:
        return bool(reader.decrypt(""))  # type: ignore[attr-defined]
    except Exception:
        return False


def _native_text_per_page(reader: object, page_count: int) -> list[str]:
    """Each page's own text layer, with a page that raises treated as empty.

    One malformed page must not cost the other 199. pypdf raises a wide range of
    exceptions on damaged content streams, and a page that cannot be read
    natively is exactly the page OCR exists for.
    """
    texts: list[str] = []
    for index in range(page_count):
        try:
            texts.append(reader.pages[index].extract_text() or "")  # type: ignore[attr-defined]
        except Exception:
            logger.warning("PDF page %d could not be read natively", index + 1)
            texts.append("")
    return texts


def _ocr_pages(content: bytes, pages: list[int]) -> dict[int, str]:
    """Rasterise and recognise only the pages that need it.

    PDFium renders here rather than poppler: it arrives as a self-contained
    wheel, so there is no system package to install on one machine and forget on
    another (docs/adr/0014).
    """
    import pypdfium2

    scale = settings.EXTRACTION_OCR_DPI / 72
    results: dict[int, str] = {}
    document = pypdfium2.PdfDocument(io.BytesIO(content))
    try:
        for number in pages:
            try:
                bitmap = document[number - 1].render(scale=scale)
                image = bitmap.to_pil()
            except Exception:
                logger.warning("PDF page %d could not be rasterised for OCR", number)
                continue
            try:
                results[number] = recognise_pil_image(image)
            finally:
                image.close()
    finally:
        document.close()
    return results


def _coverage_note(fragments: list[Fragment], page_count: int) -> str:
    from_ocr = sum(1 for fragment in fragments if fragment.text_source == TextSource.OCR)
    if not from_ocr:
        return ""
    return f"{from_ocr} lehekülge {page_count}-st loeti OCR-iga."


registry.register(PdfParser())
