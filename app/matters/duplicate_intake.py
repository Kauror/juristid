"""`Uus teema` does not file the same Teema twice by accident (QA-COORD-01, D3).

The form's `intake` token already makes one rendered form save once (ENG-074,
`app/matters/intake_staging.py`): a double press or a replayed POST lands on
the Teema the first one created. What it could not see was browser Back. The
page is `no-store` for privacy (`app/core/middleware.py`), so Back fetches it
again with a **fresh** token while Chrome restores what was typed — and the
second Salvesta was, to the server, a new form.

Two layers, both asking only about the *person's own recent work* and never
about anybody else's titles (no global uniqueness, docs/adr/0166 §2):

1. **The restored form names its earlier tokens.** The page remembers, in the
   tab's `sessionStorage`, every token it was rendered with; on a page reached
   by Back or Forward it adds them to the submission as `intake_earlier`
   (`static/js/app.js`, `bindCreateFormHistory`). A Teema one of those tokens
   created, with the same title, is this form's Teema however long ago it was
   filed.
2. **A safety net with no script.** Without those tokens — scripting off, a
   second tab, a pasted form — a Teema this person filed through `Uus teema`
   in the last `RECENT_WINDOW` under the same title is reported too.

Either way nothing is created and nothing is guessed: the form comes back with
«Sama pealkirjaga teema loodi just — ava see / loo siiski uus». «Ava see» is a
link; «loo siiski uus» posts the same form with `CONFIRM_FIELD`, and a person
who means a second Teema with the same words gets one.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from app.core.errors import DomainError
from app.matters.models import Matter
from app.matters.staging import MatterIntakeSession

#: How far back an identical title from the same person counts as "just".
RECENT_WINDOW = timedelta(minutes=10)

#: The POST flag «Loo siiski uus» carries.
CONFIRM_FIELD = "loo_siiski_uus"

#: The earlier tokens a restored form posts (at most this many are read).
EARLIER_FIELD = "intake_earlier"
MAX_EARLIER = 20

#: What the person is told.
RECENT_DUPLICATE = "Sama pealkirjaga teema loodi just."


class RecentDuplicateUnconfirmed(DomainError):
    """The Teema this form would file already exists from this person, just now."""

    def __init__(self, existing: Matter) -> None:
        super().__init__(RECENT_DUPLICATE)
        self.existing = existing


def normalised_title(title: str) -> str:
    """Case and spacing do not make two titles different."""
    return " ".join((title or "").split()).casefold()


def recent_duplicate(*, actor: Any, title: str, earlier_tokens: list[str]) -> Matter | None:
    """The Teema this person just filed under the same title, if any.

    Read through `MatterIntakeSession`, the record a successful `Uus teema`
    save leaves (`record_created_matter`): only Teemad filed through this form
    count, so an import, a seed or a Teema created by any other door is never
    mistaken for an accidental resubmission. Only Matters the person may still
    see, and not deleted ones.
    """
    wanted = normalised_title(title)
    if not wanted or getattr(actor, "pk", None) is None:
        return None
    mine = MatterIntakeSession.objects.owned_by(actor).filter(
        consumed_at__isnull=False, matter__isnull=False
    )
    tokens = [token for token in (earlier_tokens or [])[:MAX_EARLIER] if _is_uuid(token)]
    recent = mine.filter(consumed_at__gte=timezone.now() - RECENT_WINDOW)
    candidate_ids = set(recent.values_list("matter_id", flat=True))
    if tokens:
        candidate_ids |= set(mine.filter(pk__in=tokens).values_list("matter_id", flat=True))
    if not candidate_ids:
        return None
    candidates = (
        Matter.objects.visible_to(actor)
        .filter(pk__in=candidate_ids, deleted_at__isnull=True)
        .order_by("-created_at")
    )
    for matter in candidates:
        if normalised_title(matter.title) == wanted:
            return matter
    return None


def refuse_unconfirmed_duplicate(
    *, actor: Any, title: str, earlier_tokens: list[str], confirmed: bool
) -> None:
    """Refuse a save that would file this person's just-filed Teema again."""
    if confirmed:
        return
    existing = recent_duplicate(actor=actor, title=title, earlier_tokens=earlier_tokens)
    if existing is not None:
        raise RecentDuplicateUnconfirmed(existing)


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True
