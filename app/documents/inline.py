"""Which stored files a browser may be allowed to render, and which it may not.

Clicking a filename should do the useful thing: open a PDF, download a Word
document. Making that decision safely is the whole content of this module,
because "open it in the browser" means "run it in this application's origin" for
anything the browser treats as active content.

Two rules, and the second is the one that matters:

**A short allow-list, never a deny-list.** Only PDF, PNG, JPEG and plain text.
Everything else — Office formats, email, signed containers, HTML, SVG, anything
unrecognised — downloads. A format nobody has thought about is a format that
downloads.

**The extension and the stored MIME type must agree.** A MIME type arriving with
an upload is a claim by whoever uploaded it. `evil.html` announced as
`application/pdf` must not open inline, and neither must `report.pdf` announced
as `text/html`. Requiring both to independently land in the allow-list, and to
land on the *same* entry, means a single wrong value cannot open the door
(Stage-2E.1 brief 12).

When the two disagree, or anything is unrecognised: download.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.urls import reverse

from app.core.http import content_disposition

#: Extension → the one MIME type it may be served as. A mapping rather than two
#: sets, so agreement is checkable rather than assumed.
INLINE_SAFE: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
}

#: Sent with every inline response. No scripts, no plugins, no framing, no form
#: posts — a stored file rendered in this origin can do nothing but be looked at.
#:
#: Deliberately *not* `sandbox`: that directive applies to a top-level PDF
#: navigation and breaks the browser's own viewer, which would push people back
#: to downloading the thing this exists to let them read.
INLINE_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
    "object-src 'none'; script-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'"
)


def extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def may_open_inline(*, filename: str, mime_type: str) -> bool:
    """Whether this file may be rendered by the browser rather than saved.

    True only when the extension is on the list *and* the stored MIME type is
    the one that extension is allowed to carry. Either alone is a claim; both
    agreeing is a corroboration.
    """
    expected = INLINE_SAFE.get(extension_of(filename))
    if expected is None:
        return False
    declared = (mime_type or "").split(";", 1)[0].strip().lower()
    return declared == expected


@dataclass(frozen=True)
class FileLink:
    """Where a link to one stored version goes, decided once for every surface.

    Clicking a filename should do the useful thing (module docstring): a PDF
    opens in a new tab through `documents:open`, a Word file downloads through
    `documents:download`. Every template that prints a filename used to pick a
    route on its own, and all but two picked the download. This is the one
    place that decides, from the allow-list above and nothing else — the open
    route checks the same rule again on its own, so a link built here can never
    widen what renders inline.

    Built from the version row the caller already holds: the filename and the
    stored MIME type are columns on it, so deciding costs no query.
    """

    version_id: Any
    filename: str
    opens_inline: bool

    @property
    def download_url(self) -> str:
        """Always the attachment route: the explicit «Laadi alla»."""
        return reverse("documents:download", kwargs={"pk": self.version_id})

    @property
    def open_url(self) -> str:
        """The inline route, or empty when this file must download."""
        if not self.opens_inline:
            return ""
        return reverse("documents:open", kwargs={"pk": self.version_id})

    @property
    def href(self) -> str:
        """What the filename itself links to: open when it may, else download."""
        return self.open_url or self.download_url


def file_link(version: Any) -> FileLink:
    """The link for one `DocumentVersion` (or anything with its three fields)."""
    return file_link_for(
        version_id=version.pk,
        filename=version.original_filename,
        mime_type=version.mime_type,
    )


def file_link_for(*, version_id: Any, filename: str, mime_type: str) -> FileLink:
    """The same decision, from the values alone — for a row that is not a version."""
    return FileLink(
        version_id=version_id,
        filename=filename or "",
        opens_inline=may_open_inline(filename=filename, mime_type=mime_type),
    )


#: The `Content-Type` an inline response sends, where it is more than the bare
#: type. Plain text names its charset, so a browser never guesses one — and an
#: Estonian note does not open as `MÃ¤rkmed`. Nothing else is widened.
INLINE_CONTENT_TYPES: dict[str, str] = {"text/plain": "text/plain; charset=utf-8"}


def inline_mime_for(filename: str) -> str:
    """The MIME type an inline response may claim: ours, never the upload's."""
    mime = INLINE_SAFE.get(extension_of(filename), "application/octet-stream")
    return INLINE_CONTENT_TYPES.get(mime, mime)


def apply_inline_headers(response: Any, *, filename: str) -> Any:
    """Mark a response as safe-to-display, and nothing more than displayed."""
    response["Content-Disposition"] = content_disposition("inline", filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = INLINE_CONTENT_SECURITY_POLICY
    response["Referrer-Policy"] = "no-referrer"
    return response
