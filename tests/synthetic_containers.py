"""Tiny Estonian signed containers, written at test time.

Nothing here is a real signature and nothing is a checked-in binary: a real
container carries a person's certificate, and a test has no business holding
one. What these do carry is the *shape* the upload check reads — a ZIP whose
first entry is `mimetype`, holding the container's own media type — and the
inner layout DigiDoc writes beside it (a document, `META-INF/manifest.xml`, a
`signatures0.xml`), so a file built here is what an `.asice` looks like to
everything in Juristid that does not verify signatures, which is everything
(docs/adr/0125).

Three variants, because the Chamber's own 1,049 historical containers hold all
three (measured read-only, 2026-10-01): the standard layout, a `mimetype` entry
with an extra field in its header, and a `mimetype` entry deflated by a
streaming encoder with its sizes in a trailing data descriptor.

Standard library only, so the browser suite can build the same files.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

ASIC_E_MEDIA_TYPE = b"application/vnd.etsi.asic-e+zip"

#: An info-zip «extended timestamp» extra field: the kind a real encoder adds.
EXTENDED_TIMESTAMP = b"\x55\x54\x05\x00\x01\x00\x00\x00\x00"


class _Unseekable(io.BytesIO):
    """A sink `zipfile` cannot seek in, so it streams: data descriptors."""

    def seekable(self) -> bool:
        return False

    def seek(self, *args: Any) -> int:
        raise io.UnsupportedOperation("seek")

    def tell(self) -> int:
        raise io.UnsupportedOperation("tell")


def signed_container(
    *,
    document: bytes = b"%PDF-1.4 synthetic signed document",
    document_name: str = "dokument.pdf",
    deflated_mimetype: bool = False,
    mimetype_extra: bytes = b"",
    first_entry: str = "mimetype",
    media_type: bytes = ASIC_E_MEDIA_TYPE,
) -> bytes:
    """An ASiC-E container's bytes: `.asice` and `.bdoc` are both this.

    ``deflated_mimetype`` writes the file the way a streaming encoder does —
    first entry deflated, sizes in a data descriptor — which is the variation
    six of the Chamber's real containers have. ``first_entry`` and
    ``media_type`` exist to build the files that must be refused.
    """
    sink = _Unseekable() if deflated_mimetype else io.BytesIO()
    with zipfile.ZipFile(sink, "w") as archive:
        info = zipfile.ZipInfo(first_entry, date_time=(2026, 10, 1, 9, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED if deflated_mimetype else zipfile.ZIP_STORED
        info.extra = mimetype_extra
        archive.writestr(info, media_type)
        archive.writestr(document_name, document, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(
            "META-INF/manifest.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:'
                'manifest:1.0"><manifest:file-entry manifest:full-path="/" '
                'manifest:media-type="application/vnd.etsi.asic-e+zip"/>'
                f'<manifest:file-entry manifest:full-path="{document_name}" '
                'manifest:media-type="application/pdf"/></manifest:manifest>'
            ).encode(),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "META-INF/signatures0.xml",
            b'<asic:XAdESSignatures xmlns:asic="http://uri.etsi.org/02918/v1.2.1#"/>',
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return sink.getvalue()


def plain_zip() -> bytes:
    """An ordinary ZIP — a document and nothing that makes it a container."""
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, "w") as archive:
        archive.writestr("dokument.pdf", b"%PDF-1.4 not signed", compress_type=zipfile.ZIP_DEFLATED)
    return sink.getvalue()
