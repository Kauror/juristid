"""Reading what a browser sent, when no form stands between it and a query.

A form field refuses a bad value on its own: Django's ``CharField`` refuses a
NUL byte, a ``UUIDField`` refuses ``abc``, a ``TypedChoiceField`` refuses a
value it does not offer. A ``request.GET.get(...)`` refuses nothing, and a hand-
edited address reaches the database as typed. Four shapes of that were 500s
(ENG-046):

* a NUL byte in any text PostgreSQL is asked to compare with
  (``DataError: PostgreSQL text fields cannot contain NUL``);
* a number bigger than the column it is compared with (``bigint out of range``)
  or than Python's slicing will take (``OverflowError``);
* an ISO date that is not a day (``2026-02-30``), which Django's ``parse_date``
  raises on rather than answering ``None``;
* a redirect target that is not a path on this site.

Each reader here answers **"not a usable value"** in the one way its caller
already handles — ``None``, the default, or ``""`` — and never raises. What a
caller does with "not usable" stays the caller's decision, because that is where
the page knows whether an unreadable filter should empty the list or be ignored.

NUL bytes are also refused for the whole request, before any view runs, by
`app.core.middleware.RefuseNulMiddleware`. `has_nul` is the one definition both
use.
"""

from __future__ import annotations

import uuid
from collections.abc import Container

from django.utils.http import url_has_allowed_host_and_scheme

#: The NUL byte. PostgreSQL text cannot hold it, no keyboard types it, and no
#: link this application writes carries it.
NUL = "\x00"

#: Far above any page, offset or year a person asks for, and far below
#: PostgreSQL's ``bigint`` and Python's ``ssize_t``. A caller with a smaller real
#: bound passes its own.
INT_CEILING = 1_000_000


def has_nul(value: str | None) -> bool:
    return bool(value) and NUL in (value or "")


def text_or_empty(value: str | None) -> str:
    """Stripped text, or ``""`` when it carries a NUL byte.

    Refused rather than cleaned: ``a\\x00b`` is not a way anybody writes ``ab``,
    and a filter that silently searched for something other than what was sent
    would be a quieter version of the same wrong answer.
    """
    if value is None or has_nul(value):
        return ""
    return value.strip()


def bounded_int(
    value: str | None, *, default: int, minimum: int = 0, maximum: int = INT_CEILING
) -> int:
    """An integer in ``[minimum, maximum]``, or ``default``.

    Out of range is ``default`` rather than clamped. A clamped offset is a page
    nobody asked for; the default is the page the address would have shown
    without the broken parameter.
    """
    text = text_or_empty(value)
    if not text or len(text) > len(str(maximum)) + 1:
        return default
    try:
        number = int(text)
    except ValueError:
        return default
    if number < minimum or number > maximum:
        return default
    return number


def uuid_or_none(value: str | None) -> uuid.UUID | None:
    text = text_or_empty(value)
    if not text:
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def choice_or_default[T](value: str | None, choices: Container[str], default: T) -> str | T:
    """``value`` when it is one of ``choices``, ``default`` otherwise."""
    text = text_or_empty(value)
    return text if text in choices else default


def safe_local_path(candidate: str | None, *, host: str, require_https: bool = False) -> str:
    """A redirect target, only if it is an absolute path on this site.

    ``/teemad/`` passes. Everything else is ``""``: a scheme or a host (even
    this one), a protocol-relative ``//elsewhere``, the backslash form browsers
    read as one (``/\\elsewhere``), a relative ``teemad/`` and a bare URL name.
    The last two matter because ``django.shortcuts.redirect`` treats a string
    without a slash as a *view name* and resolves it; a caller hands the answer
    to ``HttpResponseRedirect``, which does not.
    """
    text = text_or_empty(candidate)
    if not text.startswith("/") or text.startswith("//") or text.startswith("/\\"):
        return ""
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        return ""
    if not url_has_allowed_host_and_scheme(text, allowed_hosts={host}, require_https=require_https):
        return ""
    return text
