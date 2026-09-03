"""Keeping the archive projection in step with the records it projects.

`app/search/signals.py` does this for the global `SearchDocument`, and ADR 0041
settled the contract it follows: **a mutation may not leave the projection stale
with nothing recording that it did.** That ADR's invalidation map is exhaustive
for one projection and silent about the other. `OpinionArchiveSearchDocument`
arrived later, with its own rebuild and no handlers at all, so the contract
simply never reached it.

The cost was not theoretical. `is_linked` and `has_submission` are computed at
index time from `OpinionArchiveMatterLink` and `OpinionSubmissionImport` — two
relations that nothing about a binary touches — so the archive workspace went on
reporting `767 kirja · 0 teemaga seotud` while 320 links and 313 canonical
Submissions stood in the database behind it. Every letter's own page named its
Teema correctly; the list, the `Teemaga seotud` tab and the header count all
read the projection and said the opposite. To a lawyer that reads as a failed
migration (UX-006).

**Bounded fanout, so the refresh is in the transaction.** Linking one letter
invalidates exactly one archive row, which is class A in ADR 0041's terms: the
handler refreshes inside the business transaction, so a committed link and a
findable link are one event, and a rolled-back link takes its refresh with it.
There is no high-fanout mutation here and therefore no durable-debt half — the
archive projection has no equivalent of renaming an Organisation.

Bulk writers keep their own obligation. `suspend_archive_indexing` suppresses
these handlers and `refresh_archive_binaries` is what the caller owes in return
— a refresh bounded by the binaries it actually touched, never a rebuild of the
corpus (`app/legacy_import/opinion_search.py`).

There is no handler on `OpinionArchiveSearchDocument` itself, so nothing here
can loop: these read canonical rows and write derived ones, in that direction
only.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from app.legacy_import.opinion_archive import OpinionArchiveItem, OpinionSubmissionImport
from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
from app.legacy_import.opinion_search import refresh_archive_binary


def _started_here(origin: Any, model: type[Model]) -> bool:
    """Whether this delete began at the row itself rather than cascading into it.

    The same test `app/search/signals.py` makes, for the same reason: when a
    binary goes, its links and its projection row go with it in the same
    statement, and reindexing a binary that is being deleted is work for a row
    that will not exist. ``origin`` is an instance for ``obj.delete()`` and a
    queryset for ``qs.delete()``. A missing ``origin`` is treated as "started
    here" — that wrong guess costs one redundant refresh rather than a stale row.
    """
    if origin is None:
        return True
    return (getattr(origin, "model", None) or type(origin)) is model


@receiver(post_save, sender=OpinionArchiveMatterLink, dispatch_uid="archive_refresh_link_saved")
def refresh_on_matter_link_save(
    sender: type[OpinionArchiveMatterLink], instance: OpinionArchiveMatterLink, **kwargs: Any
) -> None:
    refresh_archive_binary(instance.binary_id)


@receiver(post_delete, sender=OpinionArchiveMatterLink, dispatch_uid="archive_refresh_link_removed")
def refresh_on_matter_link_delete(
    sender: type[OpinionArchiveMatterLink],
    instance: OpinionArchiveMatterLink,
    origin: Any = None,
    **kwargs: Any,
) -> None:
    if _started_here(origin, OpinionArchiveMatterLink):
        refresh_archive_binary(instance.binary_id)


def _binary_of(item_id: Any) -> Any:
    """The binary behind an archive occurrence, without dragging the row in.

    `OpinionSubmissionImport` names an *occurrence*, and `has_submission` is a
    fact about the bytes — the same letter found at two paths is one binary with
    two items. `item` is `PROTECT`, so it is still there on a delete.
    """
    if item_id is None:
        return None
    return OpinionArchiveItem.objects.filter(pk=item_id).values_list("binary_id", flat=True).first()


@receiver(post_save, sender=OpinionSubmissionImport, dispatch_uid="archive_refresh_import_saved")
def refresh_on_submission_import_save(
    sender: type[OpinionSubmissionImport], instance: OpinionSubmissionImport, **kwargs: Any
) -> None:
    refresh_archive_binary(_binary_of(instance.item_id))


@receiver(
    post_delete, sender=OpinionSubmissionImport, dispatch_uid="archive_refresh_import_removed"
)
def refresh_on_submission_import_delete(
    sender: type[OpinionSubmissionImport],
    instance: OpinionSubmissionImport,
    origin: Any = None,
    **kwargs: Any,
) -> None:
    if _started_here(origin, OpinionSubmissionImport):
        refresh_archive_binary(_binary_of(instance.item_id))
