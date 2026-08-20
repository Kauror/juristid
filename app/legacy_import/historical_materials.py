"""What each historical file is, and what may be done with it.

The archive holds sixteen file extensions across 10,916 files, and this module
answers two questions about each: what MIME type to record, and whether Stage 2B
should try to read it.

**Everything is stored. Nothing is judged.** A OneNote page's author attached
these files to that page, and the placement is the evidence of relevance —
refusing a `.xltx` because the extraction stack cannot parse it would lose an
original that somebody deliberately kept (Stage-2D brief 21, 26).

**The evidence store's format list is not the upload form's.** The interactive
upload path stays as narrow as it is: it accepts what a browser may push at us.
These formats arrive from an archive whose every byte was hashed and verified
before this code ran, so widening the *evidence* allowlist to admit them does
not widen what a visitor can submit.

**Signed containers are preserved and never opened.** ASiC-E and BDoc are 1,049
files of the corpus and they are exactly what they look like: ZIP containers
holding a signed document and its signatures. Unpacking one would mean
extracting a document *out of* the thing that attests to it, and presenting the
extract as the evidence — which is the inversion this codebase refuses
everywhere else. They are stored, downloadable, and marked NOT_APPLICABLE
(Stage-2D brief 24, 74).
"""

from __future__ import annotations

import posixpath

from app.documents.enums import DocumentRole
from app.documents.uploads import EXTENSION_MIME_TYPES

#: Formats the archive contains that the interactive upload path does not
#: accept. Each is stored as evidence and none is parsed.
HISTORICAL_EXTENSION_MIME_TYPES: dict[str, str] = {
    # Estonian digitally signed containers. The registered types.
    ".asice": "application/vnd.etsi.asic-e+zip",
    ".bdoc": "application/vnd.etsi.asic-e+zip",
    ".ddoc": "application/x-ddoc",
    # Office templates and legacy word processing.
    ".xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    ".rtf": "application/rtf",
    # Saved web pages. Stored, never rendered: the download route is always an
    # attachment with nosniff, and no parser touches it.
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".pub": "application/x-mspublisher",
    ".vsd": "application/vnd.visio",
    ".one": "application/onenote",
}

#: Extensions Stage 2B has a parser for. Everything else is stored with an
#: honest NOT_APPLICABLE rather than left PENDING for a worker that will never
#: succeed at it.
EXTRACTABLE_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".csv", ".eml", ".msg", ".png", ".jpg", ".jpeg"}
)

#: Containers whose whole purpose is to attest to what is inside them.
SIGNED_CONTAINER_EXTENSIONS = frozenset({".asice", ".bdoc", ".ddoc"})

#: The last resort. A file whose extension nothing recognises is still stored;
#: an octet stream is an honest description of "bytes we are keeping".
FALLBACK_MIME_TYPE = "application/octet-stream"


def extension_of(filename: str) -> str:
    return posixpath.splitext((filename or "").lower())[1]


def mime_type_for(filename: str) -> str:
    """What to record for a historical file. Never refuses."""
    extension = extension_of(filename)
    return (
        EXTENSION_MIME_TYPES.get(extension)
        or HISTORICAL_EXTENSION_MIME_TYPES.get(extension)
        or FALLBACK_MIME_TYPE
    )


def is_extractable(extension: str) -> bool:
    return extension.lower() in EXTRACTABLE_EXTENSIONS


def is_signed_container(filename: str) -> bool:
    return extension_of(filename) in SIGNED_CONTAINER_EXTENSIONS


def role_for(filename: str) -> str:
    """The business role, where it is actually knowable.

    `.msg` and `.eml` are deterministic — the file *is* a message, whatever it
    is about. Everything else gets `LEGACY_MATERIAL`, because a filename
    containing "arvamus" is not proof that a formal opinion was submitted, and
    a guessed role is indistinguishable from a recorded one six months later
    (Stage-2D brief 23, 65).
    """
    if extension_of(filename) in {".msg", ".eml"}:
        return DocumentRole.ORIGINAL_EMAIL
    return DocumentRole.LEGACY_MATERIAL


def extraction_note_for(filename: str) -> str:
    """What to tell an operator about a file nothing will parse."""
    if is_signed_container(filename):
        return (
            "Digiallkirjastatud ümbrik. Sisu ei pakita lahti ega kontrollita — "
            "originaal on alles ja allalaaditav."
        )
    return "Ajalooline materjal. Selle vormingu sisu ei eraldata; originaal on alles."
