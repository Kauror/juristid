"""What may leave the application in a model prompt (ADR 0217 §4, ADR 0218 §3, ADR 0219).

Reading and sending are different permissions. A lawyer may *read* a RESTRICTED
Teema and the Chamber's every letter in Juristid; none of that is therefore
allowed into a request to an external model. This module is the one statement
of what is, for the three register-watch prompts (`ai`, `candidates_ai`,
`agenda`):

* **A Teema** may be sent — its title offered for a `related` link, or its title
  as the tracked topic — only when it is ``NORMAL``, real data (never a TEST
  Teema: those must not be matched, linked or rung about), and not deleted.
  `sendable_topics` adds «open» for the link list.
* **A letter's title** may be sent only when the letter is *reliably* tied to
  such a Teema: its pairing was applied (by the calibrated rule or a person) to
  a ``NORMAL``, real, undeleted Teema, and the sent opinion it became, if any,
  is not itself restricted. An unpaired letter, one still waiting for a
  decision (whatever it was suggested for), one kept only as background and one
  whose Teema is gone are all left out: until a person or the rule has said
  which Teema a letter belongs to, nobody knows whether its title is a
  restricted subject (audit HR-01, 2026-11-23).

The cost of that strictness is accepted, not hidden: the letter list is a
signal of the Chamber's current work, and every letter still waiting in Haldus
«Ajaloolised kirjad» is left out of it until it is paired. `letter_titles`
reports how many it left out so the run can say so; it never logs a title.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from django.db.models import Q, QuerySet

from app.core.enums import Visibility
from app.matters.enums import MatterDataClass
from app.matters.models import Matter

logger = logging.getLogger("app.register_watch")


def may_send(matter: Any) -> bool:
    """Whether this Teema's public facts may go into a model prompt."""
    return (
        getattr(matter, "visibility", None) == Visibility.NORMAL
        and getattr(matter, "data_class", None) == MatterDataClass.REAL
        and getattr(matter, "deleted_at", None) is None
    )


def sendable_matters() -> QuerySet[Matter]:
    """Every Teema `may_send` allows, as a query (open or closed)."""
    return Matter.objects.filter(deleted_at__isnull=True, visibility=Visibility.NORMAL).real_data()


def sendable_topics() -> QuerySet[Matter]:
    """The open Teemad a model may name in `related`."""
    return sendable_matters().filter(is_open=True)


@dataclass(frozen=True)
class Letters:
    titles: list[str]
    #: Letters of the period left out because their Teema is not known to be
    #: sendable (unpaired, waiting, background, restricted, TEST or gone).
    left_out: int


def letter_titles(*, since: dt.date, limit: int) -> Letters:
    """The newest letter titles since ``since`` that may be sent, at most ``limit``."""
    from app.legacy_import.postipoiss_models import PairingState, PostipoissLetter

    period = PostipoissLetter.objects.filter(letter_date__gte=since)
    sendable = period.filter(
        pairing__state=PairingState.APPLIED,
        pairing__matter__in=sendable_matters(),
    ).filter(
        Q(pairing__submission__isnull=True)
        | Q(pairing__submission__visibility_override__in=("", Visibility.NORMAL))
    )
    titles = list(sendable.order_by("-letter_date").values_list("title", flat=True)[:limit])
    left_out = period.count() - sendable.count()
    if left_out:
        logger.info("register watch: %d letter title(s) not sent (Teema not sendable)", left_out)
    return Letters(titles=titles, left_out=left_out)
