"""A small PNG the application may safely put on a page.

The distinction this module exists for: **the original is never rendered, and a
thumbnail always can be.** An uploaded PNG is bytes somebody else chose, served
as an attachment for good reason. A thumbnail is bytes *this process* produced —
decoded, resized, re-encoded through Pillow — so whatever was interesting about
the original's structure did not survive the round trip. That is why one is safe
inline and the other is not (Stage-2B brief 33, 70).

Thumbnails are ordinary derivatives. They live in the derivative storage class,
they are deleted and rebuilt with everything else, and a deployment that loses
them loses nothing that cannot be regenerated from the evidence.
"""

from __future__ import annotations

import io
import logging

from app.documents.enums import DerivativeKind
from app.documents.extraction.base import DerivativePayload
from app.documents.extraction.limits import Limits, configure_image_safety

logger = logging.getLogger(__name__)

#: Large enough to recognise a document at a glance, small enough that a page of
#: them is not a download. Not a display size — the page scales it down further.
THUMBNAIL_BOX = (480, 480)

#: What the first page of a PDF is rendered at before being fitted to the box.
#: Lower than the OCR resolution: reading is a machine's job and looking is a
#: person's, and a person needs far fewer pixels.
THUMBNAIL_PDF_SCALE = 1.4


def image_thumbnail(content: bytes, limits: Limits) -> DerivativePayload | None:
    """A thumbnail of an uploaded image, or nothing if it cannot be made.

    Returns ``None`` rather than raising. A missing thumbnail costs a preview
    tile; a raised exception would cost the extraction of a file whose text was
    read perfectly well.
    """
    configure_image_safety(limits)
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            return _payload(image)
    except Exception:
        logger.info("Could not build a thumbnail for an image")
        return None


def pdf_first_page_thumbnail(content: bytes, limits: Limits) -> DerivativePayload | None:
    """A thumbnail of a PDF's first page.

    Useful precisely where extracted text is least useful — a scanned annex,
    where the text is an OCR guess and the picture is what the lawyer actually
    wants to check it against.
    """
    configure_image_safety(limits)
    try:
        import pypdfium2

        document = pypdfium2.PdfDocument(io.BytesIO(content))
        try:
            if len(document) == 0:
                return None
            image = document[0].render(scale=THUMBNAIL_PDF_SCALE).to_pil()
        finally:
            document.close()
        try:
            return _payload(image)
        finally:
            image.close()
    except Exception:
        logger.info("Could not build a thumbnail for a PDF")
        return None


def _payload(image: object) -> DerivativePayload:
    from PIL.Image import Resampling

    # Flattened onto white. A transparent PNG thumbnail on a dark interface is
    # an invisible thumbnail, which is worse than none.
    prepared = image.convert("RGB")  # type: ignore[attr-defined]
    prepared.thumbnail(THUMBNAIL_BOX, Resampling.LANCZOS)

    buffer = io.BytesIO()
    prepared.save(buffer, format="PNG", optimize=True)
    return DerivativePayload(
        kind=DerivativeKind.THUMBNAIL,
        binary=buffer.getvalue(),
        binary_extension="png",
        binary_mime_type="image/png",
        metadata={"width": prepared.width, "height": prepared.height, "format": "PNG"},
    )
