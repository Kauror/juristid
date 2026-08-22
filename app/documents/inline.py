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

from pathlib import Path
from typing import Any

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


def inline_mime_for(filename: str) -> str:
    """The MIME type an inline response may claim: ours, never the upload's."""
    return INLINE_SAFE.get(extension_of(filename), "application/octet-stream")


def apply_inline_headers(response: Any, *, filename: str) -> Any:
    """Mark a response as safe-to-display, and nothing more than displayed."""
    response["Content-Disposition"] = content_disposition("inline", filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = INLINE_CONTENT_SECURITY_POLICY
    response["Referrer-Policy"] = "no-referrer"
    return response
