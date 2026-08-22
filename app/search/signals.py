"""Keeping the projection in step with the records it projects.

Stage 1's search read the Matter table directly, so a new Matter was findable
the instant it was saved. A projection is not, and CI proved the point: with
indexing left to an operator, a freshly seeded Matter searched for by title
returned nothing at all — quietly, with a plausible empty-results page. A search
that silently misses records is worse than no search, because people stop
checking.

So the ordinary path maintains itself. These handlers cover the writes the
application actually performs: saving a Matter, and changing its policy areas or
tags. They are deliberately narrow, and two things are **not** covered:

* bulk writes that bypass signals — ``QuerySet.update()``, ``bulk_create()``,
  data migrations. The importer is the one bulk writer in the system and it
  refreshes explicitly, under :func:`app.search.indexing.suspend_indexing`.
* renaming an Organisation or editing its aliases, which changes the indexed
  text of every Matter pointing at it. That is a taxonomy-administration event,
  it is rare, and fanning out from it would mean reindexing thousands of rows
  inside somebody's form submission. ``rebuild_search_index`` is the answer and
  is documented as such. The same applies to renaming a Tag or a PolicyArea.

The line between the two lists is fanout, not importance. A handler exists here
when the number of search rows a write invalidates is bounded by that write —
one Matter, one entry, one submission, one document's pages, one Matter's claim
on a OneNote page, the handful of Matters that accepted one such page. It does
not when a single edit can invalidate the whole corpus, because a synchronous
reindex of thousands of rows inside a form submission is a worse failure than
staleness an operator can fix.

Both gaps are recoverable by a rebuild, which is the property the projection was
designed around.
"""

from __future__ import annotations

from typing import Any

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from app.documents.enums import DerivativeStatus
from app.documents.models import Document, DocumentVersion
from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage
from app.matters.models import Entry, Matter, TagAssignment
from app.search.indexing import (
    indexable_matters,
    indexing_is_suspended,
    refresh_document_version,
    refresh_entry,
    refresh_matters,
    refresh_source_link,
    refresh_submission,
)
from app.search.models import SearchDocument, SearchSourceKind
from app.submissions.models import Submission, SubmissionRecipient


def _refresh(matter_id: Any) -> None:
    if indexing_is_suspended() or matter_id is None:
        return
    refresh_matters(indexable_matters().filter(pk=matter_id))


@receiver(post_save, sender=Matter, dispatch_uid="search_refresh_matter")
def refresh_on_matter_save(sender: type[Matter], instance: Matter, **kwargs: Any) -> None:
    _refresh(instance.pk)


@receiver(m2m_changed, sender=Matter.policy_areas.through, dispatch_uid="search_refresh_areas")
def refresh_on_policy_areas_changed(
    sender: type, instance: Any, action: str, **kwargs: Any
) -> None:
    # post_* only: the pre_* actions fire before the rows exist, so indexing
    # then would store the state the change is about to replace.
    if action in {"post_add", "post_remove", "post_clear"}:
        _refresh(getattr(instance, "pk", None))


@receiver(m2m_changed, sender=Matter.tags.through, dispatch_uid="search_refresh_tags")
def refresh_on_tags_changed(sender: type, instance: Any, action: str, **kwargs: Any) -> None:
    if action in {"post_add", "post_remove", "post_clear"}:
        _refresh(getattr(instance, "pk", None))


@receiver(post_save, sender=TagAssignment, dispatch_uid="search_refresh_tag_added")
@receiver(post_delete, sender=TagAssignment, dispatch_uid="search_refresh_tag_removed")
def refresh_on_tag_assignment(
    sender: type[TagAssignment], instance: TagAssignment, **kwargs: Any
) -> None:
    """Tags need both handlers, and CI proved it.

    ``Matter.tags`` has an explicit through model, because an assignment records
    who confirmed it and when. That means two different write paths reach it and
    each misses the other's signal: ``matter.tags.add(...)`` goes through
    ``bulk_create`` and never fires ``post_save``, while a ``TagAssignment``
    created directly by a service never fires ``m2m_changed``.

    Refreshing twice when both happen is cheap. Missing one makes a tagged
    matter unfindable by its tag, silently.
    """
    _refresh(instance.matter_id)


# -- Stage 2B: child content ------------------------------------------------
#
# Entries and submissions are ordinary form-driven writes, so ordinary signals
# keep them current. Document fragments are not: they appear only when a worker
# publishes a derivative, and the orchestrator refreshes them inside the same
# transaction. A signal here would fire on every fragment row of every parse and
# reindex the same version hundreds of times.


@receiver(post_save, sender=Entry, dispatch_uid="search_refresh_entry")
def refresh_on_entry_save(sender: type[Entry], instance: Entry, **kwargs: Any) -> None:
    if indexing_is_suspended():
        return
    refresh_entry(instance)


@receiver(post_delete, sender=Entry, dispatch_uid="search_remove_entry")
def remove_on_entry_delete(sender: type[Entry], instance: Entry, **kwargs: Any) -> None:
    SearchDocument.objects.filter(
        source_kind=SearchSourceKind.ENTRY, source_object_id=instance.pk
    ).delete()


@receiver(post_save, sender=Submission, dispatch_uid="search_refresh_submission")
def refresh_on_submission_save(
    sender: type[Submission], instance: Submission, **kwargs: Any
) -> None:
    if indexing_is_suspended():
        return
    refresh_submission(instance)


@receiver(post_save, sender=SubmissionRecipient, dispatch_uid="search_refresh_recipient_added")
@receiver(post_delete, sender=SubmissionRecipient, dispatch_uid="search_refresh_recipient_removed")
def refresh_on_recipient_change(
    sender: type[SubmissionRecipient], instance: SubmissionRecipient, **kwargs: Any
) -> None:
    """Recipients are indexed text, so changing them changes the index.

    Both directions, for the same reason tags needed both: adding a recipient
    and removing one are different write paths, and a submission that stays
    findable under a ministry it is no longer addressed to is wrong in the
    direction people notice least.
    """
    if indexing_is_suspended():
        return
    submission = Submission.objects.filter(pk=instance.submission_id).first()
    if submission is not None:
        refresh_submission(submission)


@receiver(post_save, sender=MatterSourcePage, dispatch_uid="search_refresh_source_link")
def refresh_on_source_link_change(
    sender: type[MatterSourcePage], instance: MatterSourcePage, **kwargs: Any
) -> None:
    """Attaching a page to a Matter makes it findable under that Matter.

    Five call sites create these rows — two in the historical importer, two in
    the review queue, one in the seed command — and every one of them
    remembered to call `index_source_link` afterwards. That is the defect: the
    projection was correct only for as long as the next person to write a sixth
    call site also remembered, and a page that is attached and unfindable
    reports itself as a page that was never attached.

    So it is a signal, and the explicit calls become redundant rather than
    load-bearing. Refreshing twice is one extra delete-and-insert of a single
    row; missing it once is a historical file that silently is not in the
    corpus (compare `refresh_on_tag_assignment`, for the same reason).
    """
    if indexing_is_suspended():
        return
    refresh_source_link(instance)


@receiver(post_save, sender=LegacySourcePage, dispatch_uid="search_refresh_source_page")
def refresh_on_source_page_change(
    sender: type[LegacySourcePage], instance: LegacySourcePage, **kwargs: Any
) -> None:
    """A re-captured OneNote page changes what its search rows say.

    The historical importer upserts pages: a second capture of the same page
    overwrites ``title``, ``derived_text`` and ``reference_tokens`` in place, and
    that is a normal operation — the export archive is known to have produced
    stale and duplicated page HTML at least once, so re-capturing is the fix
    rather than the exception. The Matter↔page rows are indexed when the *link*
    is created and never again, so without this the corpus keeps answering with
    the text of a capture the archive has already replaced.

    Bounded fanout: a page belongs to the handful of Matters that accepted it,
    normally one. On a first import there are no links yet and this is a single
    lookup that finds nothing.
    """
    if indexing_is_suspended():
        return
    for link in MatterSourcePage.objects.filter(source_page=instance):
        refresh_source_link(link)


@receiver(post_save, sender=Document, dispatch_uid="search_refresh_document_title")
def refresh_on_document_change(sender: type[Document], instance: Document, **kwargs: Any) -> None:
    """A renamed or reclassified document changes what its fragments say.

    The fragment rows carry the document's title as their own, so that a result
    can name the file it came from. Renaming the document without this leaves
    every page of it indexed under the old name.
    """
    if indexing_is_suspended():
        return
    # Only versions that actually have extracted content. Every evidence upload
    # saves its Document once to move the current-version pointer, and without
    # this guard that would issue a delete-and-reinsert for a version whose
    # parse has not happened yet.
    versions = DocumentVersion.objects.filter(
        document=instance, derivatives__status=DerivativeStatus.ACTIVE
    ).distinct()
    for version in versions:
        refresh_document_version(version)
