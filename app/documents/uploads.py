"""Server-side upload validation.

Client-side checks are a convenience for the user, never a control. Everything
here runs on the server before a single byte reaches the evidence store
(master specification 15.6).

**This is the whole of what stands between a browser and the evidence
store**, and it got more load-bearing rather than less when the malware scanner
was removed with its subsystem (docs/adr/0072). Three checks, each cheap enough
to run inside the request that uploads:

* a size ceiling, so one file cannot fill a volume;
* an extension allowlist, so the accepted set is a list somebody decided rather
  than whatever a browser offered;
* a **content signature** check, which is the one that matters — the bytes must
  actually start like the format the name claims, so `arve.pdf` that is not a
  PDF is refused here rather than handed to a parser.

None of the three was weakened when the scanner went. What went is the fourth
thing, which was a separate container asking clamd about every file and a
column recording its answer; what is left is a refusal at the door, computed
from the bytes themselves, with nothing to deploy and nothing to keep running.

**A signed container is ordinary evidence at this door** (docs/adr/0126). An
`.asice` or `.bdoc` is what the Chamber actually sends and receives, and the
exact container — not a PDF taken out of it — is the record of what went. It is
stored as the bytes that arrived and opened by nothing: no unpacking, no
signature check, no preview. Its content check reads one thing, the media type
every ASiC-E container declares in its first entry, and refuses a file that
does not open that way (:func:`starts_like_signed_container`).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from app.documents.filenames import canonical_filename
from app.documents.services import ALLOWED_EVIDENCE_MIME_TYPES


class UploadRejected(Exception):
    """The uploaded file is not acceptable evidence."""


#: The media type an ASiC-E container declares for itself, and the one the
#: evidence store records for it. `.asice` is the name the standard gives the
#: container and `.bdoc` is the Estonian BDOC 2.1 profile's name for the same
#: one, so both extensions record this — as the historical importer always has.
SIGNED_CONTAINER_MIME_TYPE = "application/vnd.etsi.asic-e+zip"

#: Extension to the MIME type the application will record. The browser's own
#: content type is advisory: it is attacker-controlled and frequently wrong even
#: when it is not.
EXTENSION_MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".msg": "application/vnd.ms-outlook",
    ".eml": "message/rfc822",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".zip": "application/zip",
    # Estonian digitally signed containers (docs/adr/0126).
    ".asice": SIGNED_CONTAINER_MIME_TYPE,
    ".bdoc": SIGNED_CONTAINER_MIME_TYPE,
}

#: The same list, spelled the way a file input's `accept` attribute wants it.
#: Every evidence picker in the application carries exactly this, so the
#: browser offers what this module accepts and nothing it would refuse after
#: the file has been sent (docs/adr/0126, `tests/test_signed_containers.py`).
UPLOAD_ACCEPT: str = ",".join(sorted(EXTENSION_MIME_TYPES))

#: Leading bytes that must match when the format has a stable signature.
#: Absence from this map means "no signature check", not "anything goes" — the
#: extension allowlist still applies. A signed container is checked by
#: :func:`starts_like_signed_container` instead, because what identifies it sits
#: one ZIP header further in than a fixed prefix can reach.
CONTENT_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    # OOXML and .zip are both ZIP containers.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04",),
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06"),
    # Legacy Office and .msg share the OLE compound-file header.
    "application/msword": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "application/vnd.ms-excel": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "application/vnd.ms-outlook": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}


#: A ZIP local file header: signature, version, flags, method, time, date,
#: CRC-32, compressed size, uncompressed size, name length, extra-field length.
_ZIP_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_ZIP_STORED = 0
_ZIP_DEFLATED = 8
_SIGNED_CONTAINER_MEDIA_TYPE = SIGNED_CONTAINER_MIME_TYPE.encode("ascii")

#: How much of a deflated `mimetype` entry is ever fed to the decompressor. The
#: entry is thirty-one characters; this is generous for any encoder and keeps
#: the work a fixed, tiny amount whatever the file claims about itself.
_MIMETYPE_INPUT_LIMIT = 4096


def starts_like_signed_container(content: bytes) -> bool:
    """Whether ``content`` opens the way every ASiC-E container must.

    ETSI EN 319 162-1, and the BDOC 2.1 profile `.bdoc` files follow, put one
    entry first in the ZIP: `mimetype`, holding the container's own media type.
    That is what makes an `.asice` recognisable from its leading bytes, and it
    is the whole of what this asks — the same question `CONTENT_SIGNATURES` asks
    of a PDF, one ZIP header further in. A PDF, a Word file or an ordinary ZIP
    renamed to `.asice` has no such entry and is refused.

    Read from the bytes in memory and nothing else: no entry is extracted or
    written anywhere, no signature is checked, and the container is never
    repackaged — the evidence stored is the file that arrived.

    **Measured against the Chamber's own 1,049 historical containers**
    (read-only, 2026-10-01), which is why two variations the standard frowns on
    are accepted rather than refused. One `.bdoc` carries a ZIP extra field in
    that first header, so the entry's data is found by the lengths the header
    declares rather than at a fixed offset; and six `.asice` written by a
    streaming encoder have the entry deflated, with its sizes in a trailing
    data descriptor — so a deflated entry is inflated, in memory, with both the
    input and the output bounded. The other 1,042 are stored exactly as the
    standard says. All 1,049 pass; a rule that refused real containers would be
    the defect this check exists to close, by a different door.
    """
    if len(content) < _ZIP_LOCAL_HEADER.size:
        return False
    (signature, _, _, method, _, _, _, _, _, name_length, extra_length) = (
        _ZIP_LOCAL_HEADER.unpack_from(content)
    )
    name_start = _ZIP_LOCAL_HEADER.size
    if signature != b"PK\x03\x04":
        return False
    if content[name_start : name_start + name_length] != b"mimetype":
        return False

    data_start = name_start + name_length + extra_length
    wanted = len(_SIGNED_CONTAINER_MEDIA_TYPE)
    if method == _ZIP_STORED:
        declared = content[data_start : data_start + wanted]
    elif method == _ZIP_DEFLATED:
        inflater = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            declared = inflater.decompress(
                content[data_start : data_start + _MIMETYPE_INPUT_LIMIT], wanted
            )
        except zlib.error:
            return False
    else:
        return False
    return declared == _SIGNED_CONTAINER_MEDIA_TYPE


@dataclass(frozen=True)
class AcceptedUpload:
    content: bytes
    filename: str
    mime_type: str


def _extension(filename: str) -> str:
    _, _, tail = filename.rpartition(".")
    return f".{tail.lower()}" if tail and tail != filename else ""


def read_upload(uploaded_file: Any) -> AcceptedUpload:
    """Validate one uploaded file and return its bytes.

    Raises :class:`UploadRejected` with a message meant for the person who
    chose the file.
    """
    if uploaded_file is None:
        raise UploadRejected("Faili ei valitud.")

    filename = getattr(uploaded_file, "name", "") or ""
    size = getattr(uploaded_file, "size", 0) or 0

    if size == 0:
        raise UploadRejected("Tühja faili ei saa tõendina salvestada.")
    if size > settings.MAX_EVIDENCE_UPLOAD_BYTES:
        limit_mb = settings.MAX_EVIDENCE_UPLOAD_BYTES // (1024 * 1024)
        raise UploadRejected(f"Fail on suurem kui lubatud {limit_mb} MB.")

    extension = _extension(filename)
    mime_type = EXTENSION_MIME_TYPES.get(extension)
    if mime_type is None:
        allowed = ", ".join(sorted(EXTENSION_MIME_TYPES))
        raise UploadRejected(f"Faililaiend {extension or '—'} ei ole lubatud. Lubatud: {allowed}")
    if mime_type not in ALLOWED_EVIDENCE_MIME_TYPES:  # pragma: no cover - guards a mapping slip
        raise UploadRejected("Failitüüp ei ole lubatud tõendivorming.")

    content = uploaded_file.read()
    if not isinstance(content, bytes):  # pragma: no cover - defensive
        raise UploadRejected("Faili ei õnnestunud lugeda.")

    signatures = CONTENT_SIGNATURES.get(mime_type)
    if signatures and not any(content.startswith(signature) for signature in signatures):
        raise UploadRejected(
            "Faili sisu ei vasta selle laiendile. Kontrolli, kas fail on terve ja õiget tüüpi."
        )
    if mime_type == SIGNED_CONTAINER_MIME_TYPE and not starts_like_signed_container(content):
        # Named, because «does not match its extension» says nothing to
        # somebody holding a file DigiDoc saved: what is wrong is that this is
        # not a signed container at all, whatever its name says.
        raise UploadRejected(
            f"Faili sisu ei vasta laiendile {extension}: see ei ole digiallkirjastatud "
            "ümbrik. Kontrolli, kas fail on terve ja õiget tüüpi."
        )

    # NFC and bounded with the extension kept (ENG-088, ENG-033): a name typed
    # on a Mac and the same name typed on Windows are one name, and a blind
    # `[:400]` used to cut the extension off an over-long one.
    return AcceptedUpload(
        content=content, filename=canonical_filename(filename), mime_type=mime_type
    )
