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
  inside somebody's form submission.

The line between the two lists is fanout, not importance. A handler *refreshes*
here when the number of search rows a write invalidates is bounded by that write
— one Matter, one entry, one submission, one `Kaasamine`, one document's pages,
one Matter's claim on a OneNote page, the handful of Matters that accepted one
such page. It does not when a single edit can invalidate the whole corpus,
because a synchronous reindex of thousands of rows inside a form submission is a
worse failure than staleness.

**SEARCH-001 changes what happens to the second list.** Deferring the work was
always right; deferring it into *nothing* was the defect. Until now a rename
left the corpus stale with no record anywhere that it had, and convergence
depended on a human noticing and running `rebuild_search_index`. The handlers at
the bottom of this module now write a durable obligation instead — one row, in
the same transaction as the rename — and a consumer pays it off with the same
atomic rebuild an operator would have run (`app/search/freshness.py`,
docs/adr/0039).

So there are two mechanisms and they are not interchangeable:

===============  ==============================  ===========================
fanout           when the index converges        what guarantees it
===============  ==============================  ===========================
bounded          with the business transaction   the refresh is in it
high             within one consumer pass        the debt row is in it
===============  ==============================  ===========================

Bulk writes remain the caller's responsibility, and that has not changed:
`suspend_indexing` still suppresses the *refresh* handlers. It deliberately does
**not** suppress the debt handlers below, because a debt is not work — it is the
record that work is owed, and a bulk writer that silently dropped it would
reintroduce exactly the staleness this closes.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Model
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from app.accounts.models import User
from app.documents.enums import DerivativeStatus
from app.documents.models import Document, DocumentVersion
from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterSourceOrganisation,
    TagAssignment,
)
from app.organisations.models import Organisation, OrganisationAlias
from app.search.freshness import mark_rebuild_owed
from app.search.indexing import (
    indexable_matters,
    indexing_is_suspended,
    refresh_document_version,
    refresh_engagement,
    refresh_entry,
    refresh_matters,
    refresh_source_link,
    refresh_submission,
)
from app.search.models import SearchDocument, SearchRebuildReason, SearchSourceKind
from app.submissions.models import Submission, SubmissionRecipient
from app.taxonomy.models import PolicyArea, Tag, TagAlias


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


@receiver(
    m2m_changed, sender=Matter.source_organisations.through, dispatch_uid="search_refresh_senders"
)
def refresh_on_source_organisations_changed(
    sender: type, instance: Any, action: str, **kwargs: Any
) -> None:
    """Who sent a Matter is indexed text, and nothing was maintaining it.

    `_alias_text_for` indexes every sender's name and aliases, so that a Matter
    which arrived from a ministry *and* an association is findable through
    either (ADR 0025). Nothing refreshed the row when that list changed, and the
    create path made it worse rather than merely incomplete: `create_matter`
    calls `Matter.objects.create(...)` — which fires `post_save` and indexes the
    Matter with **no senders at all** — and only afterwards calls `.set()`. A
    Matter created through the product with its sender filled in was therefore
    not findable by that sender, ever, until somebody happened to save it again.

    `update_matter_senders` was the only path that worked, and only because it
    saves the Matter afterwards for an unrelated reason (`updated_at`). That is
    the same shape the source-page handler was written to end: correct for
    exactly as long as the next call site also remembers.
    """
    if action in {"post_add", "post_remove", "post_clear"}:
        _refresh(getattr(instance, "pk", None))


@receiver(post_save, sender=MatterSourceOrganisation, dispatch_uid="search_refresh_sender_added")
@receiver(
    post_delete, sender=MatterSourceOrganisation, dispatch_uid="search_refresh_sender_removed"
)
def refresh_on_source_organisation_row(
    sender: type[MatterSourceOrganisation], instance: MatterSourceOrganisation, **kwargs: Any
) -> None:
    """The other half, for the same reason tags need two handlers.

    `source_organisations` has an explicit through model, so `.set()` reaches it
    through `bulk_create` and fires no `post_save`, while a row created directly
    by a service fires no `m2m_changed`. Each handler covers what the other
    misses, and refreshing twice costs one delete-and-insert of a single row.
    """
    _refresh(instance.matter_id)


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


@receiver(post_save, sender=MatterEngagement, dispatch_uid="search_refresh_engagement")
def refresh_on_engagement_save(
    sender: type[MatterEngagement], instance: MatterEngagement, **kwargs: Any
) -> None:
    """A recorded `Kaasamine` has to be a findable one.

    AUTH-003 moved engagement text out of the MATTER row and into a row of its
    own, so that a consultation restricted below its Matter could be hidden
    without hiding the Matter. It added no way for that row to appear: only a
    full rebuild ever wrote one. So between AUTH-003 and here, every engagement
    recorded through `add_engagement` was invisible to search — and the
    integrity check did not count the kind, so no report said so.

    That is the shape of defect this module exists to prevent, and it is
    unbounded in the direction that matters: not "the index is a little behind"
    but "this content is not in the corpus at all, and nothing will tell you".

    There is deliberately no `post_delete` companion. `SearchDocument.engagement`
    is a real foreign key with `on_delete=CASCADE`, so removing an engagement
    removes its projection row in the same statement the database already runs —
    a handler would be a second mechanism for something the schema guarantees.
    The regression test asserts the cascade rather than the handler, because the
    cascade is what is load-bearing.
    """
    if indexing_is_suspended():
        return
    refresh_engagement(instance)


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

    **Not when the submission is going too.** Deleting a Submission cascades to
    its recipients, and Django deletes in two phases: rows with no receivers are
    raw-deleted first — `SearchDocument` among them — and only then does the
    per-model loop delete the recipients and fire this handler. At that moment
    the Submission row still exists, because it is deleted later in the same
    loop, so re-projecting here inserts a search row the cascade has already
    swept up. Nothing collects it afterwards. The FK is deferred, so the failure
    lands at COMMIT as an integrity error naming a submission that is no longer
    there, and the delete the operator asked for does not happen at all.

    `origin` is what tells the two apart, and here it is exact rather than
    approximate, because only two things can reach a recipient row at all.
    `SubmissionRecipient.organisation` is PROTECT, so deleting an Organisation
    cannot cascade here; and `Document.matter`, `ChangeEvent.matter` and
    `MatterSourceReference.matter` are PROTECT too, so a Matter carrying an
    opinion cannot be deleted either. That leaves deleting the recipients —
    which is `set_recipients` replacing them, so the submission survives and
    must be reindexed — and deleting the Submission, after which there is
    nothing left to keep findable.
    """
    if indexing_is_suspended():
        return
    if kwargs.get("signal") is post_delete and not _deletion_started_at_the_recipients(
        kwargs.get("origin")
    ):
        return
    submission = Submission.objects.filter(pk=instance.submission_id).first()
    if submission is not None:
        refresh_submission(submission)


def _deletion_started_at_the_recipients(origin: Any) -> bool:
    """Was `delete()` called on recipients, or on something above them?

    ``origin`` is an instance for ``obj.delete()`` and a queryset for
    ``qs.delete()``, so the model is read from whichever it is. A missing
    ``origin`` is treated as "started here": that is the direction that keeps
    the index correct, and the wrong guess costs one redundant refresh rather
    than a row nothing will ever collect.
    """
    if origin is None:
        return True
    model = getattr(origin, "model", None) or type(origin)
    return model is SubmissionRecipient


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


# -- SEARCH-001: high fanout becomes durable debt ---------------------------
#
# Everything above refreshes. Everything below records that a refresh is owed,
# because the write it follows can invalidate the indexed text of the whole
# corpus and no request may pay for that.
#
# The two halves share nothing except the projection builders, and that is the
# point: the debt is paid off by `rebuild_all`, which is the same code path an
# operator's `rebuild_search_index` runs and the same one that composes every
# searchable column. There is no second, faster way to compose indexed text, so
# there is nothing that can drift away from what a rebuild would produce.


def _mark_on_rename(fields: tuple[str, ...], reason: str) -> Any:
    """Mark a rebuild owed when a save actually changes reference text.

    Three properties, each of which is why this is not simply "mark on save".

    *It runs on `pre_save`.* The comparison needs the row as it currently
    stands, and by `post_save` the database already says the new name, so
    nothing would ever look renamed. The mark it writes lands in the same
    transaction as the save, so the two commit or roll back together and a save
    that raises after this point takes its debt with it.

    *It believes `update_fields`.* A save that does not name a watched field
    cannot have changed one. That is what keeps this subsystem off the sign-in
    path: `user.save(update_fields=["last_login"])` issues no query here at all.

    *It compares rather than assuming.* `Organisation` and `User` rows are saved
    for reasons that have nothing to do with their names. Marking on every save
    would make the debt table a log of ordinary activity and owe a rebuild
    every few seconds forever, which is a worse failure than the one being
    fixed. One indexed primary-key lookup on a rarely-saved model is the price
    of not doing that.

    An instance with no stored row is being created, and nothing can be carrying
    its old text.
    """

    def handler(sender: type[Model], instance: Any, **kwargs: Any) -> None:
        if instance.pk is None:
            return
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not set(update_fields) & set(fields):
            return
        stored = sender._default_manager.filter(pk=instance.pk).values(*fields).first()
        if stored is None:
            return
        if any(stored[field] != getattr(instance, field) for field in fields):
            mark_rebuild_owed(reason)

    return handler


def _mark_alias_change(reason: str) -> Any:
    """Aliases are marked unconditionally, and the asymmetry is deliberate.

    A rename is compared because renames are rare and the models are saved
    often. An alias row is the opposite: it exists only to be indexed, so every
    write to one is a write to the corpus's searchable text — creating it,
    editing it and deleting it alike. There is nothing to compare against and
    nothing cheaper to test.

    A new alias on a brand-new Organisation owes nothing in truth, and is marked
    anyway. That costs one coalesced rebuild; guessing the other way costs an
    abbreviation that silently finds nothing.
    """

    def handler(sender: type[Model], instance: Any, **kwargs: Any) -> None:
        mark_rebuild_owed(reason)

    return handler


# `weak=False` on every one of these, and it is load-bearing rather than
# defensive. `Signal.connect` holds its receiver *weakly* by default, so a
# closure passed in inline — which is what each of these is — has no other
# reference, is collected at the next garbage collection, and silently stops
# being a receiver. The handler is still in the file, the `dispatch_uid` is
# still registered, and nothing anywhere raises; the rename simply stops owing
# a rebuild. CI caught it here, which is the only place it could have been
# caught, because the failure is indistinguishable from the defect this module
# was written to fix.
pre_save.connect(
    _mark_on_rename(("name",), SearchRebuildReason.ORGANISATION_RENAMED),
    sender=Organisation,
    dispatch_uid="search_debt_org_rename",
    weak=False,
)
pre_save.connect(
    _mark_on_rename(("name_et",), SearchRebuildReason.TAG_RENAMED),
    sender=Tag,
    dispatch_uid="search_debt_tag_rename",
    weak=False,
)
pre_save.connect(
    _mark_on_rename(("name_et",), SearchRebuildReason.POLICY_AREA_RENAMED),
    sender=PolicyArea,
    dispatch_uid="search_debt_area_rename",
    weak=False,
)
# A person's display name is `alias_text` on every ENTRY row they authored. The
# fanout is smaller than a ministry's and still unbounded by the write, and one
# mechanism for "reference text changed" is better than a second one calibrated
# to a smaller number.
pre_save.connect(
    _mark_on_rename(("display_name",), SearchRebuildReason.PERSON_RENAMED),
    sender=User,
    dispatch_uid="search_debt_person_rename",
    weak=False,
)

post_save.connect(
    _mark_alias_change(SearchRebuildReason.ORGANISATION_ALIAS_CHANGED),
    sender=OrganisationAlias,
    dispatch_uid="search_debt_org_alias_saved",
    weak=False,
)
post_delete.connect(
    _mark_alias_change(SearchRebuildReason.ORGANISATION_ALIAS_CHANGED),
    sender=OrganisationAlias,
    dispatch_uid="search_debt_org_alias_deleted",
    weak=False,
)
post_save.connect(
    _mark_alias_change(SearchRebuildReason.TAG_ALIAS_CHANGED),
    sender=TagAlias,
    dispatch_uid="search_debt_tag_alias_saved",
    weak=False,
)
post_delete.connect(
    _mark_alias_change(SearchRebuildReason.TAG_ALIAS_CHANGED),
    sender=TagAlias,
    dispatch_uid="search_debt_tag_alias_deleted",
    weak=False,
)
