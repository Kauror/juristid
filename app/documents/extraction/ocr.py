"""Local OCR, and the rule about when to use it.

No document content leaves this machine. OCR is a Tesseract process on the same
host reading a bitmap this process rendered, and the engine's language data
ships in the image. A cloud OCR service would be more accurate and is not
available to us: the corpus contains ministry drafts and member correspondence,
and "the API provider does not train on submitted data" is a promise, not a
control (Stage-2B brief 14).

**OCR is not run on healthy documents.** A text-native PDF already carries the
author's own characters, which are exact; replacing them with a recognition
engine's guess would make the search corpus worse and the provenance a lie. The
trigger is an absent or implausibly thin text layer on a specific page, not a
property of the file, because real government PDFs are routinely a scanned annex
stapled to a typed cover letter.
"""

from __future__ import annotations

import functools
import logging
import shutil

from django.conf import settings

from app.documents.extraction.errors import ExtractionFailed
from app.documents.extraction.limits import Limits, configure_image_safety

logger = logging.getLogger(__name__)

#: What Tesseract is asked for. Estonian first because the corpus is Estonian;
#: English alongside it because EU documents and ministry annexes are routinely
#: bilingual, and a page recognised with the wrong language model is not
#: slightly worse, it is unusable.
DEFAULT_LANGUAGES = "est+eng"


class OcrUnavailable(RuntimeError):
    """The engine is not installed, or its language data is missing."""


@functools.cache
def ocr_engine_version() -> str:
    """The engine's own version string, recorded on every OCR derivative.

    Cached: the answer cannot change while the process lives, and asking costs
    a subprocess. An OCR corpus is only comparable within one engine version,
    so this is what a future "rebuild everything Tesseract 5.3 produced"
    selects on.
    """
    if shutil.which("tesseract") is None:
        raise OcrUnavailable("tesseract is not on PATH")
    import pytesseract

    try:
        return f"tesseract {pytesseract.get_tesseract_version()}"
    except Exception as error:  # pragma: no cover - engine present but broken
        raise OcrUnavailable(str(error)) from error


def ocr_is_available() -> bool:
    if not settings.EXTRACTION_OCR_ENABLED:
        return False
    try:
        ocr_engine_version()
    except OcrUnavailable:
        return False
    return True


def missing_languages() -> tuple[str, ...]:
    """Which configured languages Tesseract cannot actually load.

    Checked rather than assumed, because the failure it prevents is silent:
    Tesseract asked for a language it does not have falls back to English and
    returns confident nonsense for Estonian text, which reaches the search index
    looking exactly like a successful extraction.
    """
    try:
        ocr_engine_version()
    except OcrUnavailable:
        return tuple(configured_languages())
    import pytesseract

    try:
        installed = set(pytesseract.get_languages(config=""))
    except Exception:  # pragma: no cover - older engines lack the query
        return ()
    return tuple(code for code in configured_languages() if code not in installed)


def configured_languages() -> tuple[str, ...]:
    raw = settings.EXTRACTION_OCR_LANGUAGES or DEFAULT_LANGUAGES
    return tuple(part for part in raw.split("+") if part)


def recognise_image(content: bytes, limits: Limits) -> str:
    """Read one image's text. Returns an empty string when there is none."""
    configure_image_safety(limits)
    import io

    import pytesseract
    from PIL import Image, UnidentifiedImageError

    ocr_engine_version()  # raises OcrUnavailable if the engine is not usable

    del pytesseract  # imported above only so an absent binding fails early
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            return recognise_pil_image(image)
    except UnidentifiedImageError as error:
        raise ExtractionFailed("unreadable_image", "Pilti ei õnnestunud avada.") from error
    except Image.DecompressionBombError as error:
        raise ExtractionFailed(
            "image_too_large", "Pildi mõõtmed ületavad lubatud piiri."
        ) from error


def recognise_pil_image(image: object) -> str:
    """Read text off an already-decoded image.

    Used directly by the PDF parser, which rasterises pages itself rather than
    round-tripping them through PNG bytes.
    """
    import pytesseract

    ocr_engine_version()
    try:
        text = pytesseract.image_to_string(image, lang=settings.EXTRACTION_OCR_LANGUAGES)
    except pytesseract.TesseractError as error:
        # The engine ran and refused. Distinct from it being absent, and worth a
        # different message: one is a deployment problem, the other is this file.
        raise ExtractionFailed("ocr_failed", "OCR ei suutnud seda pilti lugeda.") from error
    return text or ""
