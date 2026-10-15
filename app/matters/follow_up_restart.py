"""«Planeeri järelkontroll» on a reopened Teema — what the page offers (owner decision D9).

Closing a Teema cancels its planned or current `Arvamuse järelkontroll` with
«Teema suleti», and reopening revives nothing (docs/adr/0146 §8): a check that
silently came back would be work nobody chose. What the owner decided on
2026-10-14 is an **explicit** continuation — on the reopened Teema, each check
the closure ended is named with its opinion and offers one small form: the day
to look again, and «Planeeri järelkontroll». Saving it plans a new check through
`app.workflow.follow_ups.restart_follow_up`; the cancelled one stays in the
history as it was.

**Read through the opinion's visibility.** Which follow-ups qualify is a domain
question (`restartable_follow_ups`, reader-blind); which of them this page names
is the reader's — an opinion the reader may not see is not offered, and its line
is the same one every check shows (`app.matters.follow_up_display`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone


@dataclass
class RestartOffer:
    """One follow-up a closure ended, as this reader may see it, with its form."""

    follow_up_id: Any
    subject: Any
    #: A `FollowUpRestartForm`, prefilled with the cancelled check's day when
    #: that day is not already behind us — a suggestion the person saves or changes.
    form: Any


def restart_offers(matter: Any, viewer: Any, *, can_write: bool) -> list[RestartOffer]:
    """The checks this open Matter's closure ended, that ``viewer`` may plan again."""
    if not can_write or viewer is None or not getattr(matter, "is_open", False):
        return []
    from app.matters.follow_up_display import follow_up_subjects
    from app.matters.forms import FollowUpRestartForm
    from app.workflow.enums import ActionStatus
    from app.workflow.follow_ups import restartable_follow_ups
    from app.workflow.models import NextAction

    follow_ups = list(restartable_follow_ups(matter).order_by("sent_on", "created_at"))
    if not follow_ups:
        return []
    # The cancelled checks this reader may see — the same rows the follow-ups'
    # lines are read from on every other surface.
    cancelled = list(
        NextAction.objects.visible_to(viewer)
        .filter(follow_up__in=follow_ups, status=ActionStatus.CANCELLED)
        .order_by("follow_up_id", "-ended_at", "-created_at")
    )
    subjects = follow_up_subjects(cancelled, viewer)
    last_day: dict[Any, Any] = {}
    for action in cancelled:
        last_day.setdefault(action.follow_up_id, action.target_date)
    today = timezone.localdate()
    offers: list[RestartOffer] = []
    for follow_up in follow_ups:
        subject = subjects.get(follow_up.pk)
        if subject is None:
            continue
        day = last_day.get(follow_up.pk)
        offers.append(
            RestartOffer(
                follow_up_id=follow_up.pk,
                subject=subject,
                form=FollowUpRestartForm(
                    follow_up_id=follow_up.pk,
                    initial={"target_date": day if day is not None and day >= today else None},
                ),
            )
        )
    return offers
