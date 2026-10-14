"""Answers to a `Kaasamine`: the first finishes the wait, later ones still belong to it.

docs/adr/0167 §2 (owner decision D8, 2026-10-14). A consultation round is
answered by people, and people do not all answer on the same day. Until this
record the first `Lisa tagasiside` closed the round *and* took it out of the
form's list, so the second member's answer could only be filed as a loose
`Meile saadetud tagasiside` with no tie to the consultation that produced it.

What this module states, and nothing else:

* :func:`answerable_rounds` — the rounds `Lisa tagasiside` may name: this
  Matter's tracked rounds as this reader may see them, **open or already
  finished**. The open ones are still the waits (`open_feedback_waits`);
  the finished ones are offered so a later answer can be tied to them.
* :func:`finish_wait_on_reply` — the save contract. A reply to a round that is
  still open finishes its wait through the canonical
  `complete_engagement_feedback`, exactly as since 2026-10-07; a reply to a
  round already finished records the answer and leaves the round's closure —
  who, when, its audit row — as it was. Never reopened, never re-closed.
* :func:`refuse_repeated_reply` — a reply identical to one already on the
  round (same words, address, day and files) is a repeated submit, not a
  second answer, and is refused before anything is written.

Every reply is its own `MatterExternalPosition` row. Nothing here updates or
merges an earlier one, so every answer, its date, its files and its audit
events stay as they were recorded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from django.db.models import QuerySet

from app.core.errors import DomainError
from app.matters.enums import RecordMode
from app.matters.locks import lock_open_matter_for_business_write
from app.matters.models import (
    EXTERNAL_POSITION_SUMMARY_MAX_LENGTH,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
)

#: Refused when the same reply is submitted to the same round twice.
REPLY_ALREADY_RECORDED = (
    "See tagasiside on sellele kaasamisele juba lisatud (sama tekst, kuupäev ja failid)."
)


def answerable_rounds(viewer: Any, matter: Matter) -> QuerySet[MatterEngagement]:
    """The rounds a reply on this Matter may name, as this reader may see them.

    Tracked rounds (`lifecycle_tracked`) on an open `FULL` Matter — the same
    population `work_items.open_feedback_waits` draws its waits from, with the
    finished ones kept. A round the register importer filed as history has no
    lifecycle and is not offered: it is started through `Ootan tagasisidet`
    first. Scoped through the round's **own** `visible_to`, so a restricted
    round is neither offered nor accepted for a reader who may not open it
    (AUTH-003); removed rounds are dropped by the same chokepoint.
    """
    return MatterEngagement.objects.visible_to(viewer).filter(
        matter=matter,
        lifecycle_tracked=True,
        matter__is_open=True,
        matter__record_mode=RecordMode.FULL,
    )


def finish_wait_on_reply(*, engagement: MatterEngagement, actor: Any = None) -> bool:
    """Finish the round's wait if this reply is its first answer; say whether it did.

    **The contract `Lisa tagasiside` — and `Ootame tagasisidet → Tehtud`,
    which opens the same form — relies on** (docs/adr/0167 §2):

    * the round is re-read under the Matter's lock, inside the caller's
      transaction, so the state decided on is the committed one;
    * an **open** round (`lifecycle_tracked`, no `feedback_closed_at`) is
      finished through `complete_engagement_feedback` — one
      `ENGAGEMENT_FEEDBACK_CLOSED` event, the actor and the time — and
      ``True`` is returned. The waiting line under `PRAEGUNE TEGEVUS` and
      every work list drop it in the same transaction;
    * a round **already finished** — by an earlier reply, by
      `Lõpeta kaasamine`, or by a Matter closure before a reopening — is left
      exactly as it is and ``False`` is returned. A later answer neither
      moves the closure nor writes a second closing event.

    The feedback text the round itself holds (`feedback_received`) is never
    touched here: the reply's words are on the reply.
    """
    from app.matters.services import complete_engagement_feedback

    locked_matter = lock_open_matter_for_business_write(engagement.matter_id)
    current = MatterEngagement.objects.select_for_update(no_key=True).get(
        pk=engagement.pk, matter=locked_matter
    )
    if not current.lifecycle_tracked or current.feedback_closed_at is not None:
        return False
    complete_engagement_feedback(engagement=current, actor=actor)
    engagement.feedback_closed_at = current.feedback_closed_at
    engagement.feedback_closed_by = current.feedback_closed_by
    return True


def _upload_digests(uploads: Iterable[Any]) -> list[str]:
    """SHA-256 of each file about to be captured, leaving every file rewound."""
    digests = []
    for upload in uploads:
        if not upload:
            continue
        digest = hashlib.sha256()
        if hasattr(upload, "chunks"):
            for chunk in upload.chunks():
                digest.update(chunk)
            upload.seek(0)
        else:
            digest.update(getattr(upload, "content", b"") or b"")
        digests.append(digest.hexdigest())
    return sorted(digests)


def refuse_repeated_reply(
    *,
    matter: Matter,
    engagement: MatterEngagement | None,
    summary: str,
    url: str,
    stated_on: Any,
    uploads: Iterable[Any] = (),
) -> None:
    """Refuse a reply identical to one already recorded against the same round.

    Since a finished round accepts later answers, a second press of `Salvesta`
    — a double click, a browser resubmitting the form — would otherwise file
    the same answer twice (owner decision D3: an accidental duplicate is
    prevented, a deliberate one is not). «Identical» is every source the reply
    carries: the same words, the same address, the same day **and** the same
    set of files by checksum. A second member's answer with the same text on
    another day, or the same text with another file, is a different answer and
    is recorded.

    Asked under the Matter's lock, so two concurrent presses are serialised
    and the second one sees the first.
    """
    if engagement is None:
        return
    from app.matters.services import normalize_external_position_url

    lock_open_matter_for_business_write(matter.pk)
    clean_summary = (summary or "").strip()[:EXTERNAL_POSITION_SUMMARY_MAX_LENGTH]
    clean_url = normalize_external_position_url(url)
    candidates = MatterExternalPosition.objects.filter(
        matter=matter,
        engagement=engagement,
        removed_at__isnull=True,
        summary=clean_summary,
        url=clean_url,
        stated_on=stated_on,
    )
    if not candidates.exists():
        return
    digests = _upload_digests(uploads)
    for position in candidates:
        recorded = sorted(
            position.document_links.filter(
                document__removed_at__isnull=True,
                document__current_version__isnull=False,
            ).values_list("document__current_version__sha256", flat=True)
        )
        if recorded == digests:
            raise DomainError(REPLY_ALREADY_RECORDED)
