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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings

from app.documents.services import ALLOWED_EVIDENCE_MIME_TYPES


class UploadRejected(Exception):
    """The uploaded file is not acceptable evidence."""


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
}

#: Leading bytes that must match when the format has a stable signature.
#: Absence from this map means "no signature check", not "anything goes" — the
#: extension allowlist still applies.
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

    return AcceptedUpload(content=content, filename=filename[:400], mime_type=mime_type)
