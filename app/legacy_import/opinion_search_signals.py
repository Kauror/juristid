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

**Three relations, not two.** `review_state` and `match_class` are projected the
same way from `OpinionMatchCandidate`, and that is the relation people actually
write: every decision in `/haldus/arvamuste-ulevaatus/`, every proposal a rerun
supersedes, every candidate a catalogue adds. Covering the first two and not the
third would have left the queue's own filter reading the state before the
decision that changed it.

**Bounded fanout, so the refresh is in the transaction.** Linking one letter,
importing one Submission and deciding one candidate each invalidate exactly one
archive row, which is class A in ADR 0041's terms: the handler refreshes inside
the business transaction, so a committed decision and a findable one are one
event, and a rolled-back decision takes its refresh with it. There is no
high-fanout mutation here and therefore no durable-debt half — the archive
projection has no equivalent of renaming an Organisation.

**A whole row, never a patch.** Each handler recomputes everything `_row_values`
knows, so a candidate's `excel_reference` — which is indexed among the row's
`identifiers` — cannot go stale while the two obvious columns stay fresh.

Bulk writers keep their own obligation. `suspend_archive_indexing` suppresses
these handlers and `refresh_archive_binaries` is what the caller owes in return
— a refresh bounded by the binaries it actually touched, never a rebuild of the
corpus (`app/legacy_import/opinion_search.py`). `apply_plan` is the only writer
in the application that needs it: it is the one place that changes any of these
three relations with a `QuerySet.update()`, which no signal can see. Everything
else — the review queue, `supersede_candidate`, `derive_links`, the catalogue
and the second matching pass — writes one row at a time through the model, so
the handlers below already cover them.

There is no handler on `OpinionArchiveSearchDocument` itself, so nothing here
can loop: these read canonical rows and write derived ones, in that direction
only.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from app.legacy_import.opinion_archive import (
    OpinionArchiveItem,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
from app.legacy_import.opinion_search import archive_indexing_is_suspended, refresh_archive_binary


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

    `OpinionSubmissionImport` and `OpinionMatchCandidate` both name an
    *occurrence*, while every column they feed is a fact about the bytes — the
    same letter found at two paths is one binary with two items. `item` is
    `PROTECT` from the binary side and the collector deletes a child before its
    parent, so the row is still there in either delete.
    """
    if item_id is None:
        return None
    return OpinionArchiveItem.objects.filter(pk=item_id).values_list("binary_id", flat=True).first()


def _refresh_behind_item(item_id: Any) -> None:
    """Refresh the binary an occurrence belongs to, if indexing is live.

    The suspension test comes before the lookup rather than after it. A bulk
    writer that has suspended should pay nothing per row, and `_binary_of` is a
    query — one per candidate over a catalogue run is a cost the suspension was
    supposed to remove.
    """
    if archive_indexing_is_suspended():
        return
    refresh_archive_binary(_binary_of(item_id))


@receiver(post_save, sender=OpinionSubmissionImport, dispatch_uid="archive_refresh_import_saved")
def refresh_on_submission_import_save(
    sender: type[OpinionSubmissionImport], instance: OpinionSubmissionImport, **kwargs: Any
) -> None:
    _refresh_behind_item(instance.item_id)


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
        _refresh_behind_item(instance.item_id)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
#
# `review_state` and `match_class` are projected from the occurrence's live
# `OpinionMatchCandidate` rows, so the same gap existed for a third column pair
# and for the *most* frequently written relation of the three. A reviewer
# answering the queue, a rerun superseding a proposal it no longer believes, a
# catalogue writing a new one: each committed while the archive workspace went
# on filtering and labelling by the state before it, until somebody happened to
# rebuild.
#
# Bounded exactly like the other two — one candidate names one occurrence, which
# names at most one binary — so it is class A and the refresh belongs in the
# transaction rather than in a queue of durable debt.


@receiver(post_save, sender=OpinionMatchCandidate, dispatch_uid="archive_refresh_candidate_saved")
def refresh_on_candidate_save(
    sender: type[OpinionMatchCandidate], instance: OpinionMatchCandidate, **kwargs: Any
) -> None:
    """Every ordinary write, whatever moved: creation, decision, supersession.

    Not narrowed by ``update_fields``. `opinion_views._mark` saves seven fields
    at once and `supersede_candidate` six, and a handler that tried to decide
    which of them matter would have to re-derive `_row_values` in miniature —
    and be wrong the day a column is added to it. Recomputing the row is a
    handful of queries, and `_reindex` writes nothing when nothing changed, so
    a save that does not move the projection costs a comparison.

    Recomputing the *whole* row also keeps `identifiers` honest: a candidate's
    `excel_reference` is indexed there, so patching only the two obvious columns
    would leave a searchable one stale.
    """
    _refresh_behind_item(instance.item_id)


@receiver(
    post_delete, sender=OpinionMatchCandidate, dispatch_uid="archive_refresh_candidate_removed"
)
def refresh_on_candidate_delete(
    sender: type[OpinionMatchCandidate],
    instance: OpinionMatchCandidate,
    origin: Any = None,
    **kwargs: Any,
) -> None:
    """A proposal that is gone must stop being projected.

    **This is deliberately not `_started_here`.** That test asks "did the delete
    begin at this model", which is right for a link — a link goes either on its
    own or with its binary — and wrong here, because a candidate is `CASCADE`
    from two *other* directions. Deleting an `OpinionArchiveItem` takes its
    candidates with it, and deleting a `Matter` takes every candidate proposing
    it; in both the binary survives, quite possibly still catalogued under a
    second occurrence, and refusing to refresh would leave exactly the stale
    `review_state` this handler exists to prevent. The Matter case is a real
    path, not a hypothetical: the TEST-data purge deletes Matters and holds
    `OpinionArchiveBinary` in `NEVER_OWNED` precisely so the bytes outlive them.

    The narrower question — is the *binary* going too — is the one worth asking,
    and is asked below.
    """
    if _binary_survives(origin):
        _refresh_behind_item(instance.item_id)


def _binary_survives(origin: Any) -> bool:
    """Whether the binary behind this candidate outlives the delete.

    Nothing can currently answer no: `OpinionArchiveItem.binary` is `PROTECT`,
    a candidate requires an item, so a binary with a candidate under it cannot
    be deleted at all. The guard is kept because the consequence of being wrong
    is not a stale row but a failed delete — the collector gathers the rows it
    will remove *before* it removes them, so a handler that recreated the
    projection row mid-cascade would leave one nothing collects, and the
    binary's own delete would then fail on a foreign key at COMMIT. That exact
    shape has already cost this codebase a bug once, on the Submission side
    (`tests/test_integration_seams.py`).
    """
    if origin is None:
        return True
    return (getattr(origin, "model", None) or type(origin)) is not OpinionArchiveBinary
