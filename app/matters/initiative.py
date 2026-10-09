"""`Koja ettepanek või pöördumine` — a Teema the Chamber started itself (owner's R5, 2026-10-09).

Most Teemad begin with something arriving: a draft sent for comment, a question
asked of Koda, with a day it arrived (`Saabus`) and a day Koda owes its opinion
by (`Arvamuse tähtaeg`). A Chamber proposal or appeal begins the other way round
— Koda writes first — and neither date exists. This module is the one place that
knows that, so the forms, the header and the services cannot disagree about it
(docs/adr/0151).

**One stored fact, and it already existed.** `Matter.track` («Menetlusliik»)
answers *what kind of process this is*, and its vocabulary has always had
`Track.KODA_INITIATIVE` «Koja algatus». A Teema the Chamber initiated is exactly
that process, whatever instrument it concerns — a proposal about a regulation is
`Õigusakt` «Määrus» on a `Koja algatus` track, the two facts independent as ADR
0070 §1 requires. A second column saying the same thing could only ever disagree
with the first. `Õigusakt` «Koja ettepanek või pöördumine» stays what it is: the
kind of instrument, not who started the work, and it implies nothing here
(docs/adr/0090 §4 — no track is derived from an instrument).

What it means, and nothing more:

* **No incoming dates are established.** A Chamber initiative is never given a
  `Saabus` or an `Arvamuse tähtaeg` it did not have: `Uus teema` records
  neither, and every writer refuses to *establish* one on such a Teema
  (:func:`refuse_new_incoming_dates`). So no response obligation can begin, and
  nothing on it reads as «arvamust koostamisel».
* **History is not erased.** A Teema marked as an initiative later keeps any
  date it already holds; changing or clearing one is the ordinary, audited
  correction it always was.
* **Marking and unmarking are audited** through `change_track`
  (`MATTER_TRACK_CHANGED`, with the previous track in the payload).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import DomainError
from app.workflow.enums import Track

#: The control's words, the owner's.
KODA_INITIATIVE_LABEL = "Koja ettepanek või pöördumine"

INITIATIVE_HAS_NO_RECEIVED_DATE = (
    "Koja ettepanekul või pöördumisel ei ole saabumise kuupäeva — Koda algatas selle ise."
)
INITIATIVE_HAS_NO_RESPONSE_DEADLINE = (
    "Koja ettepanekul või pöördumisel ei ole arvamuse tähtaega. Kui Kojalt "
    "küsitakse arvamust, eemalda teemalt märge «Koja ettepanek või pöördumine»."
)

_UNSET: Any = object()


def is_koda_initiative(matter: Any) -> bool:
    """Whether this Teema is one the Chamber started itself."""
    return getattr(matter, "track", "") == Track.KODA_INITIATIVE


def refuse_new_incoming_dates(
    matter: Any,
    *,
    initiative: bool | None = None,
    received_date: Any = _UNSET,
    response_deadline: Any = _UNSET,
) -> None:
    """Refuse to *establish* `Saabus` or `Arvamuse tähtaeg` on a Chamber initiative.

    ``initiative`` is the answer about to be stored, when the caller is changing
    it in the same save; otherwise the Matter's own. Only a date where the
    Matter holds none is refused: keeping, moving or clearing one it already
    holds is a correction of history and stays allowed.
    """
    if not (is_koda_initiative(matter) if initiative is None else initiative):
        return
    if received_date is not _UNSET and received_date and matter.received_date is None:
        raise DomainError(INITIATIVE_HAS_NO_RECEIVED_DATE)
    if response_deadline is not _UNSET and response_deadline and matter.response_deadline is None:
        raise DomainError(INITIATIVE_HAS_NO_RESPONSE_DEADLINE)


def set_koda_initiative(*, matter: Any, value: bool, actor: Any = None) -> Any:
    """Mark or unmark a Teema as a Chamber initiative, through the audited track writer.

    Marking replaces whatever `Menetlusliik` the Teema held — the previous value
    is in the event's payload. Unmarking clears only `Koja algatus`; a Teema on
    another track is left exactly as it is.
    """
    from app.matters.services import change_track

    if value and not is_koda_initiative(matter):
        return change_track(matter=matter, track=Track.KODA_INITIATIVE, actor=actor)
    if not value and is_koda_initiative(matter):
        return change_track(matter=matter, track="", actor=actor)
    return matter
