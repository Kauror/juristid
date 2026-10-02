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

**Scope is exact.** The value is set by :func:`composer_operation` (or
:func:`separate_operation`) and unset when that block exits, including on an
exception. Nothing outside such a block has one, so an importer, a shell
session, a management command and an ordinary inline edit all keep writing
standalone rows exactly as they do today.

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

#: Which `Hetkeseis` period each Matter's writes in this save belong to.
#:
#: ``None`` outside a save; inside one, Matter id → episode id, filled the first
#: time a Matter's period is asked for and never changed afterwards. That is the
#: whole of docs/adr/0131 §5: a save that records an act *and* moves the stage
#: did the act in the period it started in, so every row it writes — before the
#: move or after it — names that period. A save with no period to start in (a
#: Matter with no `Hetkeseis` choosing its first one) is bound to the period it
#: opens, which `app.matters.services.change_stage` pins (`pin_stage_episode`).
_stage_episode_pins: ContextVar[dict[uuid.UUID, uuid.UUID] | None] = ContextVar(
    "audit_stage_episode_pins", default=None
)


def current_operation_id() -> uuid.UUID | None:
    """The operation this code is running inside, or ``None``."""
    return _current_operation.get()


def pinned_stage_episode(matter_id: uuid.UUID) -> uuid.UUID | None:
    """The period this save's writes on a Matter belong to, if one is pinned yet."""
    pins = _stage_episode_pins.get()
    if pins is None:
        return None
    return pins.get(matter_id)


def pin_stage_episode(matter_id: uuid.UUID, episode_id: uuid.UUID) -> uuid.UUID:
    """Pin the period for this save — unless one is pinned already, which wins.

    Returns the pinned period. Outside a save nothing is pinned and the answer
    is simply the period offered, because a lone write is its own save.
    """
    pins = _stage_episode_pins.get()
    if pins is None:
        return episode_id
    return pins.setdefault(matter_id, episode_id)


@contextmanager
def stage_episode_scope() -> Iterator[None]:
    """Make everything written inside one save for the purpose of `Hetkeseis` periods.

    :func:`composer_operation` opens one itself. This is for a save that writes
    several separate rows on purpose — `Muuda teemat` corrects a title, an
    owner and a stage as three facts — and must still place them in one period
    rather than splitting them around its own stage change. Nests like the
    operation does: an inner scope keeps the outer one's pins.
    """
    if _stage_episode_pins.get() is not None:
        yield
        return
    token = _stage_episode_pins.set({})
    try:
        yield
    finally:
        _stage_episode_pins.reset(token)


@contextmanager
def composer_operation(operation_id: uuid.UUID | None = None) -> Iterator[uuid.UUID]:
    """Mark everything written inside as one professional action.

    Nests without surprise: an inner block keeps the outer operation rather than
    starting a second one, because a service calling another service is still
    the same thing the person did.

    One operation is also one `Hetkeseis` period per Matter
    (:func:`stage_episode_scope`).
    """
    existing = _current_operation.get()
    if existing is not None:
        yield existing
        return

    value = operation_id or uuid.uuid4()
    token = _current_operation.set(value)
    try:
        with stage_episode_scope():
            yield value
    finally:
        _current_operation.reset(token)


@contextmanager
def separate_operation(operation_id: uuid.UUID) -> Iterator[uuid.UUID]:
    """Mark everything written inside as the operation named — never the one around it.

    :func:`composer_operation` joins an outer operation, because a service a
    person's save calls is still that save. This is for the one act that must
    not be joined: the attachments of **one e-mail** are one addition however
    many there are, and two e-mails are two however close together they are
    read (docs/adr/0122 §1). If the attachments of an e-mail were ever read
    inside another operation — two messages uploaded in one press and opened in
    the same request — joining it would fold both messages' attachments into one
    row. The outer operation is restored when this block exits.
    """
    token = _current_operation.set(operation_id)
    try:
        yield operation_id
    finally:
        _current_operation.reset(token)
