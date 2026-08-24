"""One professional action, however many canonical records it writes.

A composer save is one thing a lawyer did. Underneath it may create an `Entry`,
capture a `DocumentVersion`, supersede a `NextAction`, record a
`MatterImportantDate`, add a `MatterEngagement` and close the Matter — six
correct, separate, append-only facts, and six lines in a chronology that is
supposed to read like a case file rather than a database log.

The fix is not to write fewer facts. It is to say, at the moment they are
written, which ones came from the same action.

**Why a context variable and not a parameter.** The alternative is an
``operation_id=`` argument on every service in `app.matters`, `app.workflow`,
`app.documents`, `app.submissions` and `app.intelligence`, threaded through
every caller including importers that have no operation at all. That is a
change to twenty signatures to carry one value that is constant for the
duration of a request handler — and the day one of them is forgotten, the
timeline splits an action in half with nothing failing. Binding it to the
execution context instead means a service cannot forget to pass it on.

**Scope is exact.** The value is set by :func:`composer_operation` and unset
when that block exits, including on an exception. Nothing outside such a block
has one, so an importer, a shell session, a management command and an ordinary
inline edit all keep writing standalone rows exactly as they do today.

**It changes no history.** ``operation_id`` is additive and nullable; existing
rows keep the null they were written with, and the timeline treats a null as
"this stands alone", which is what those rows have always meant.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: The operation the current execution context belongs to, if any.
_current_operation: ContextVar[uuid.UUID | None] = ContextVar(
    "audit_current_operation", default=None
)


def current_operation_id() -> uuid.UUID | None:
    """The operation this code is running inside, or ``None``."""
    return _current_operation.get()


@contextmanager
def composer_operation(operation_id: uuid.UUID | None = None) -> Iterator[uuid.UUID]:
    """Mark everything written inside as one professional action.

    Nests without surprise: an inner block keeps the outer operation rather than
    starting a second one, because a service calling another service is still
    the same thing the person did.
    """
    existing = _current_operation.get()
    if existing is not None:
        yield existing
        return

    value = operation_id or uuid.uuid4()
    token = _current_operation.set(value)
    try:
        yield value
    finally:
        _current_operation.reset(token)
