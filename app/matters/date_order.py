"""`Arvamuse tähtaeg` earlier than `Saabus`: a warning a person may overrule.

Approved by the owner on 2026-10-14 (QA D-UUS, docs/adr/0166 §4). A deadline
that falls before the day the material arrived makes a Teema overdue the moment
it is filed, and it is almost always a typing slip — the two boxes stand side
by side. It is also occasionally true: a ministry can send material after the
day it asked for comments by. So it is neither silently accepted nor refused:
the save stops once, says why, and goes through when the person confirms.

One rule, one sentence and one confirmation flag, read by the three places that
set one of the two dates against the other: `Uus teema` (`MatterCreateForm`),
the header's `Saabus` editor and the header's `Arvamuse tähtaeg` editor
(`app/matters/views.py`). Nothing is stored about the confirmation: the dates
themselves are the record.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.errors import DomainError

#: The POST flag a deliberate save carries. One name on every surface.
CONFIRM_FIELD = "kinnita_kuupaevad"

#: What the person is told, in one place for the form, the header and the tests.
DEADLINE_BEFORE_RECEIVED = (
    "Arvamuse tähtaeg on varasem kui saabumise kuupäev, nii et teema oleks kohe "
    "üle tähtaja. Kontrolli kuupäevi."
)

#: The confirming control's words.
CONFIRM_LABEL = "Kuupäevad on õiged, salvesta siiski"


def deadline_precedes_received(*, received: date | None, deadline: date | None) -> bool:
    """Whether the pair needs the warning: both known and the deadline earlier."""
    return received is not None and deadline is not None and deadline < received


def confirmed(data: Any) -> bool:
    """Whether a submitted form carries the deliberate confirmation."""
    value = data.get(CONFIRM_FIELD) if data is not None else None
    return str(value or "").strip().lower() in {"1", "on", "true", "jah"}


def repost_fields(post: Any) -> list[tuple[str, str]]:
    """Every value a refused header save posted, for its confirming button.

    The confirmation re-sends exactly what the person submitted — the date, its
    revision, and for the deadline editor what the change means — plus the flag.
    The CSRF token is the page's own and is rendered separately.
    """
    pairs: list[tuple[str, str]] = []
    for key in post:
        if key in {"csrfmiddlewaretoken", CONFIRM_FIELD}:
            continue
        pairs.extend((key, str(value)) for value in post.getlist(key))
    return pairs


class DateOrderUnconfirmed(DomainError):
    """A header date save that would put `Arvamuse tähtaeg` before `Saabus`."""

    def __init__(self) -> None:
        super().__init__(DEADLINE_BEFORE_RECEIVED)
