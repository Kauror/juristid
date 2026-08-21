"""Response headers that have to carry a filename.

One function, because a filename is the one thing in an HTTP header that is not
ours: it comes from whoever uploaded the file, and this corpus is Estonian, so
``Arvamus õigusloome eelnõu kohta.pdf`` is the ordinary case rather than the
edge one.

Three ways of getting this wrong were live in this codebase at once, and each
one fails somewhere different.

**Base64 is not percent-encoding.** ``filename*=UTF-8''`` is RFC 5987, which
means percent-encoded UTF-8. The download route encoded the name with
``urlsafe_base64_encode`` instead, so an Estonian filename arrived in the
browser as ``QXJ2YW11cyDDtWlndXNsb29tZSBlZWxuw7V1IGtvaHRhLnBkZg`` — saved, with
no extension and no name anybody could read.

**A raw non-latin-1 name destroys the whole header.** Django encodes header
values as latin-1 and MIME-encodes the value when it cannot. ``õ`` and ``ä``
survive that; ``š`` and ``ž`` do not, and the result is not a mangled filename
but ``=?utf-8?b?...?=`` *in place of the entire header* — the ``attachment``
directive included. A file that was meant to download renders in the page.

**A quote in the name adds a parameter.** ``filename="{name}"`` with a name
containing ``"`` closes the parameter early and lets the rest of the filename
become header syntax. Nothing rejects such a name on the way in: an upload's
filename is whatever the multipart part says it is.

So the name is sanitised to a single path component with no quotes, backslashes
or control characters, and then written twice — a latin-1-safe ``filename=`` for
old clients and an RFC 5987 ``filename*=`` for everybody else, exactly as
RFC 6266 §4.3 prescribes. Every byte of this header is now ASCII by
construction, so there is nothing left for Django to MIME-encode.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from urllib.parse import quote

#: Anything a client could use as a directory separator. Applied before the
#: basename is taken, because ``PurePosixPath`` does not treat ``\`` as one and
#: a Windows browser will happily send ``C:\Users\...\fail.pdf``.
_SEPARATORS = re.compile(r"[\\/]+")

#: C0 and C1 control characters, plus the quote and backslash that are header
#: syntax inside a quoted-string.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f-\x9f"\\]')

#: What a file is called when sanitising leaves nothing usable.
FALLBACK_FILENAME = "fail"


def safe_filename(filename: str) -> str:
    """One path component, safe to place inside a header parameter.

    Never returns an empty string: a header saying ``filename=""`` is worse
    than one naming the file ``fail``, because a browser shown the first will
    invent a name from the URL — which here is a UUID.
    """
    name = _SEPARATORS.sub("/", filename or "")
    name = posixpath.basename(name)
    name = _UNSAFE.sub("", name).strip().strip(".")
    return name or FALLBACK_FILENAME


def _ascii_fallback(filename: str) -> str:
    """The best ASCII rendering of a name, for the plain ``filename=`` half.

    Decomposed and stripped of combining marks, so ``õ`` degrades to ``o``
    rather than vanishing and leaving ``igusloome``. This half exists only for
    clients that ignore ``filename*``; every current browser prefers the
    RFC 5987 parameter and gets the real name.
    """
    folded = unicodedata.normalize("NFKD", filename)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _UNSAFE.sub("", ascii_only).strip() or FALLBACK_FILENAME


def content_disposition(disposition: str, filename: str) -> str:
    """An RFC 6266 ``Content-Disposition`` value. ASCII, always.

    ``disposition`` is ``"attachment"`` or ``"inline"``; it is never taken from
    input, so it is not escaped, only asserted.
    """
    if disposition not in {"attachment", "inline"}:  # pragma: no cover - programming error
        raise ValueError(f"Unsupported disposition {disposition!r}.")

    name = safe_filename(filename)
    fallback = _ascii_fallback(name)
    header = f'{disposition}; filename="{fallback}"'
    if name != fallback:
        # `quote` with an empty safe list, so every reserved character is
        # escaped rather than only the ones that happen to matter in a path.
        header += f"; filename*=UTF-8''{quote(name, safe='')}"
    return header
