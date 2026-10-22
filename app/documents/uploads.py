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

**A signed container is ordinary evidence at this door** (docs/adr/0125). An
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
from typing import Any

from django.conf import settings

from app.core.errors import DomainError
from app.documents.filenames import canonical_filename
from app.documents.services import ALLOWED_EVIDENCE_MIME_TYPES
from app.documents.upload_body import BytesBody, UploadBody, UploadedFileBody


class UploadRejected(DomainError):
    """The uploaded file is not acceptable evidence.

    A `DomainError` like every other refused write, so a view that answers
    refusals with ``except DomainError`` answers a rejected file the same way:
    the sentence beside the form, not a 500 (JARCH-006, 2026-10-17 audit).
    """


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
    # Estonian digitally signed containers (docs/adr/0125).
    ".asice": SIGNED_CONTAINER_MIME_TYPE,
    ".bdoc": SIGNED_CONTAINER_MIME_TYPE,
}

#: The same list, spelled the way a file input's `accept` attribute wants it.
#: Every evidence picker in the application carries exactly this, so the
#: browser offers what this module accepts and nothing it would refuse after
#: the file has been sent (docs/adr/0125, `tests/test_signed_containers.py`).
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


class AcceptedUpload:
    """One file that passed :func:`read_upload`, and where its bytes are.

    **A body, not bytes** (csf_35546c06657fae7b9935969d, docs/adr/0182). The
    bytes stay where the upload handlers or the pending-upload volume already
    put them and are streamed from there when a store writes them
    (`app.documents.upload_body.store_body`); a batch of accepted files is a
    list of sizes and file handles, not gigabytes in a web worker.

    ``content=`` still builds one from bytes a caller already holds (an
    importer, a test), and :attr:`content` still reads the bytes back — every
    upload door writes ``body`` instead.
    """

    __slots__ = ("body", "display_title", "filename", "mime_type")

    def __init__(
        self,
        content: bytes | None = None,
        filename: str = "",
        mime_type: str = "",
        display_title: str = "",
        *,
        body: UploadBody | None = None,
    ) -> None:
        if body is None:
            body = BytesBody(content or b"")
        elif content is not None:  # pragma: no cover - a caller slip
            raise TypeError("AcceptedUpload takes content= or body=, not both")
        self.body: UploadBody = body
        self.filename = filename
        self.mime_type = mime_type
        #: The display title typed beside the file before the save, or ``""``
        #: (docs/adr/0142 §A, amended 2026-10-07). Becomes `Document.title`; the
        #: filename stays the version's `original_filename` whatever is typed here.
        self.display_title = display_title

    @property
    def size(self) -> int:
        return self.body.size

    @property
    def content(self) -> bytes:
        """Every byte, read into memory now. Upload doors use :attr:`body`."""
        return self.body.read_all()

    def sha256(self) -> str:
        return self.body.sha256()

    def __repr__(self) -> str:
        return f"AcceptedUpload({self.mime_type}, {self.size} bytes)"


#: The most a request, and the files one save files, may carry. One sentence
#: per limit, the same at the request boundary (`app.documents.upload_limits`)
#: and at a door that adds held files to new ones (:func:`check_upload_batch`).
TOO_MANY_FILES = "Korraga saab saata kuni {limit} faili. Lisa ülejäänud failid eraldi."
BATCH_TOO_LARGE = (
    "Valitud failid on kokku suuremad kui lubatud {limit_mb} MB. Lisa failid väiksemate osadena."
)


def too_many_files_refusal() -> str:
    return TOO_MANY_FILES.format(limit=settings.UPLOAD_MAX_FILES_PER_REQUEST)


def batch_too_large_refusal() -> str:
    return BATCH_TOO_LARGE.format(limit_mb=settings.UPLOAD_MAX_REQUEST_BYTES // (1024 * 1024))


def check_upload_batch(sizes: list[int]) -> None:
    """Refuse a save whose files, together, are more than one request may carry.

    The aggregate half of the upload policy (docs/adr/0182). The request
    boundary already refuses a request over these numbers before its body is
    read; this is the same rule for a save that *also* files what an earlier
    refusal is holding (`app.documents.pending`), so held bytes and new bytes
    are counted together. Sizes only: nothing is read to answer it.
    """
    if len(sizes) > settings.UPLOAD_MAX_FILES_PER_REQUEST:
        raise UploadRejected(too_many_files_refusal())
    if sum(sizes) > settings.UPLOAD_MAX_REQUEST_BYTES:
        raise UploadRejected(batch_too_large_refusal())


#: How much of a file the content checks may read: a ZIP local header with the
#: longest possible name and extra field, and the bounded `mimetype` input
#: :func:`starts_like_signed_container` inflates. Every other signature is a
#: prefix of a few bytes.
SIGNATURE_HEAD_BYTES = _ZIP_LOCAL_HEADER.size + 2 * 0xFFFF + _MIMETYPE_INPUT_LIMIT


def _body_of(uploaded_file: Any, size: int) -> UploadBody:
    """Where this file's bytes are, without reading them."""
    if callable(getattr(uploaded_file, "seek", None)):
        body = UploadedFileBody(uploaded_file)
        body.size = size
        return body
    # A file-like object with no seek (no Django upload is one): read it, but
    # never more than one byte past the ceiling the size check just applied.
    content = uploaded_file.read(settings.MAX_EVIDENCE_UPLOAD_BYTES + 1)
    if not isinstance(content, bytes):  # pragma: no cover - defensive
        raise UploadRejected("Faili ei õnnestunud lugeda.")
    return BytesBody(content)


def _extension(filename: str) -> str:
    _, _, tail = filename.rpartition(".")
    return f".{tail.lower()}" if tail and tail != filename else ""


def read_upload(uploaded_file: Any) -> AcceptedUpload:
    """Validate one uploaded file and say where its bytes are.

    Raises :class:`UploadRejected` with a message meant for the person who
    chose the file.

    The size, extension and content checks are what they always were; what
    changed is that the file is no longer read into memory to make them. The
    size is the one the upload handler counted, the content checks read only
    :data:`SIGNATURE_HEAD_BYTES`, and the accepted file carries a body that a
    store streams from (docs/adr/0182).
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

    # The bytes stay where the upload handler wrote them; only the head the
    # content checks need is read (csf_35546c06657fae7b9935969d).
    body = _body_of(uploaded_file, size)
    head = body.head(SIGNATURE_HEAD_BYTES)
    if not isinstance(head, bytes):  # pragma: no cover - defensive
        raise UploadRejected("Faili ei õnnestunud lugeda.")

    signatures = CONTENT_SIGNATURES.get(mime_type)
    if signatures and not any(head.startswith(signature) for signature in signatures):
        raise UploadRejected(
            "Faili sisu ei vasta selle laiendile. Kontrolli, kas fail on terve ja õiget tüüpi."
        )
    if mime_type == SIGNED_CONTAINER_MIME_TYPE and not starts_like_signed_container(head):
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
        body=body,
        filename=canonical_filename(filename),
        mime_type=mime_type,
        display_title=clean_display_title(getattr(uploaded_file, "display_title", "")),
    )


#: `Document.title`'s own bound.
DISPLAY_TITLE_MAX_LENGTH = 400


def clean_display_title(value: Any) -> str:
    """A typed display title, whitespace collapsed and bounded, or ``""``."""
    return " ".join(str(value or "").split())[:DISPLAY_TITLE_MAX_LENGTH]


def document_title_for(upload: Any) -> str:
    """What a file is called on the Matter: the title typed for it, else its name.

    The original filename is never changed by this — it is the version's
    `original_filename`, beside the bytes and the checksum.
    """
    title = clean_display_title(getattr(upload, "display_title", ""))
    return title or str(getattr(upload, "filename", "") or "")[:DISPLAY_TITLE_MAX_LENGTH]
