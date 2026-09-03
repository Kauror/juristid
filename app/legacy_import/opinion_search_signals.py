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

**Four relations, in the end.** The three above all point at a letter *through*
an `OpinionArchiveItem`, and the occurrences themselves are the fourth. Six more
columns are read off the live set of them — `occurrence_count`,
`occurrence_paths`, `identifiers`, `title`, `recipient` and `document_date` — so
removing one filing of a letter moved all six and left the row describing a
filing that no longer existed. The candidate handler fires during that cascade
and does not help: the collector removes children before their parent, so it
recomputed a row that still contained the occurrence being deleted.

**Six, which is all of them.** The two remaining inputs are what an occurrence
*carries*: `OpinionArchiveMetadata`, KodaDash's reading of a filing, which
supplies the title, recipient and date the filename did not and unions its
`external_id` into the row's `identifiers`; and `OpinionArchiveText`, the parse
of the bytes, which is `body_text` and `has_body_text` and therefore the whole
of the archive's full-text search. A catalogue run picking up a KodaDash
workbook after materialisation, and an extraction or a re-extraction of a letter
already indexed, both committed with nothing refreshing the row.

**Bounded fanout, so the refresh is in the transaction.** Linking one letter,
importing one Submission, deciding one candidate, cataloguing one filing,
reading one row of a workbook and extracting one letter's text each invalidate
exactly one archive row, which is class A in ADR 0041's terms: the
handler refreshes inside the business transaction, so a committed decision and a
findable one are one event, and a rolled-back decision takes its refresh with it.
There is no high-fanout mutation here and therefore no durable-debt half — the
archive projection has no equivalent of renaming an Organisation.

**A whole row, never a patch.** Each handler recomputes everything `_row_values`
knows, so a candidate's `excel_reference` and a metadata row's `external_id` —
both indexed among the row's `identifiers` — cannot go stale while the obvious
columns stay fresh.

Bulk writers keep their own obligation. `suspend_archive_indexing` suppresses
these handlers and `refresh_archive_binaries` is what the caller owes in return
— a refresh bounded by the binaries it actually touched, never a rebuild of the
corpus (`app/legacy_import/opinion_search.py`). Two writers in the application
need it, and both are places a `QuerySet.update()` changes a projected column
where no signal can see it: `apply_plan`, which marks candidates applied, and
`materialize`, which points a catalogued occurrence at its bytes. Everything
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
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from app.legacy_import.opinion_archive import (
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_binary import (
    OpinionArchiveBinary,
    OpinionArchiveMatterLink,
    OpinionArchiveText,
)
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


# ---------------------------------------------------------------------------
# Occurrences
# ---------------------------------------------------------------------------
#
# The relation the other two hang off, and the last of the four to be covered.
# `OpinionArchiveItem` is one filing of one letter at one path, and the row
# builder reads six columns off the live set of them: `occurrence_count`,
# `occurrence_paths`, `identifiers`, `title`, `recipient` and `document_date`
# (with `source_year` following the date). Removing a filing therefore moves all
# six, and takes that occurrence's metadata and candidates with it by CASCADE.
#
# Deleting an occurrence used to leave the row claiming the path and the count of
# a filing that no longer existed. Nothing noticed: the candidate handler above
# does fire during that cascade, but it fires *before* the item row goes — the
# collector removes children first — so it recomputed a row that still contained
# the occurrence being deleted, and `archive_index_findings` had no check that
# could see the difference.
#
# The binary always outlives its occurrences, and the schema is what says so
# rather than a convention: `OpinionArchiveItem.binary` and `.batch` are both
# `PROTECT`, and they are the model's only foreign keys, so no cascade can reach
# an occurrence at all. Every deletion begins at the row or at a queryset of
# them, and the letter's bytes are still held afterwards — quite possibly still
# catalogued under a second path.


@receiver(pre_save, sender=OpinionArchiveItem, dispatch_uid="archive_note_occurrence_move")
def note_occurrence_move(sender: type[OpinionArchiveItem], instance: Any, **kwargs: Any) -> None:
    """Remember the binary an occurrence is moving away from.

    An occurrence carries its filename, its path, its metadata and its candidates
    with it, so moving one between binaries — or clearing its binary — invalidates
    *both* rows and `post_save` can only see the new one. The comparison has to
    happen here, because by `post_save` the stored row already says the new value.

    The same shape `app/search/signals.py` uses for a rename, and for the same
    three reasons: it runs on `pre_save`, it believes `update_fields`, and it
    compares rather than assuming. Both free tests come before the lookup, so a
    bulk writer that has suspended pays nothing per row and neither does a
    create.

    **`_state.adding`, never `pk is None`.** `BaseModel` fills the primary key in
    from a `uuid7` default, so an unsaved instance already has one and the usual
    test for a creation is false for every row here. Cataloguing an archive is
    767 creations in one pass, and getting this wrong would have made each of
    them pay a SELECT for a move that cannot have happened. There is a query
    count in the suite rather than a comment.

    **No production path reassigns an occurrence today** and the guard is kept
    anyway. `materialize` is the only writer that sets `binary`, it sets it only
    where it was null, and it does so with a queryset `update()` that no signal
    sees at all — which is why that path owes the bounded refresh it now pays.
    What this covers is the shell session and the next writer, and the cost of
    covering it is one indexed primary-key lookup on a model that is otherwise
    written once per filing.
    """
    instance._archive_binary_before = None
    if instance._state.adding or archive_indexing_is_suspended():
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "binary" not in set(update_fields):
        return
    stored = (
        sender._default_manager.filter(pk=instance.pk).values_list("binary_id", flat=True).first()
    )
    if stored != instance.binary_id:
        instance._archive_binary_before = stored


@receiver(post_save, sender=OpinionArchiveItem, dispatch_uid="archive_refresh_occurrence_saved")
def refresh_on_occurrence_save(
    sender: type[OpinionArchiveItem], instance: Any, **kwargs: Any
) -> None:
    """A filing that was created, renamed or moved must be projected as it is.

    Both binaries, in the order they became wrong: the one the occurrence left,
    then the one it joined. `refresh_archive_binary` ignores a null, so the
    ordinary case — a create, or a save that moved nothing — is one refresh.

    Not narrowed by ``update_fields``: five of the model's columns reach the row
    and a handler that tried to decide which of them matter would be re-deriving
    `_occurrence_values` in miniature, and wrong the day a column joins it.
    `_reindex` writes nothing when nothing changed, so a save that does not move
    the projection costs a comparison.
    """
    refresh_archive_binary(getattr(instance, "_archive_binary_before", None))
    refresh_archive_binary(instance.binary_id)


@receiver(post_delete, sender=OpinionArchiveItem, dispatch_uid="archive_refresh_occurrence_removed")
def refresh_on_occurrence_delete(
    sender: type[OpinionArchiveItem],
    instance: OpinionArchiveItem,
    origin: Any = None,
    **kwargs: Any,
) -> None:
    """A filing that is gone must stop being projected.

    `post_delete` and not `pre_delete`: the recompute has to see the corpus as it
    is afterwards, and by here the occurrence's metadata and candidates have gone
    with it. What it produces is exactly what a clean rebuild would — including
    for the last occurrence of a letter, where the row stays and reports nothing
    found: the binary is canonical evidence and the catalogue is not what makes
    it real, so `rebuild_archive_index` keeps writing a row for it.

    Guarded by `_binary_survives` for the reason it was written: re-projecting a
    binary that is itself being deleted would insert a row the cascade has
    already swept past, and the binary's delete would then fail on a foreign key
    at COMMIT. Nothing can currently answer no here either — `PROTECT` means a
    binary with an occurrence cannot be deleted at all — and the consequence of
    being wrong is a failed delete rather than a stale row, which is the
    asymmetry that decides it.
    """
    if _binary_survives(origin):
        refresh_archive_binary(instance.binary_id)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
#
# KodaDash's reading of an occurrence, and the fifth relation the row builder
# reads. It is a *fallback* rather than a source of its own, which is exactly
# what made it easy to miss: `title`, `recipient` and `document_date` are the
# first filing's filename fields, and metadata supplies them only where the
# filename did not. Where the archive's names are good the column never moves,
# and the relation looks inert.
#
# It is not inert. `external_id` is unioned into `identifiers` unconditionally,
# so a KodaDash row is the letter's register-side handle and the only thing that
# makes it findable by that handle — and a great many archive filenames carry no
# recipient and no date at all, which is where the fallback is the value being
# projected rather than a spare one.
#
# The write that mattered is the ordinary one. `_write_metadata` is shared by
# `catalogue_plan` and `apply_plan`, and only the second suspends: a KodaDash
# workbook that arrives *after* the archive has been catalogued and materialised
# is one `catalogue` run writing metadata rows against occurrences whose bytes
# are already held and already indexed. Every one of those committed with
# nothing refreshing the row.
#
# Bounded like the other four — one metadata row names one occurrence, which
# names at most one binary — so it is class A and the refresh belongs in the
# business transaction.


@receiver(pre_save, sender=OpinionArchiveMetadata, dispatch_uid="archive_note_metadata_move")
def note_metadata_move(sender: type[OpinionArchiveMetadata], instance: Any, **kwargs: Any) -> None:
    """Remember the binary a metadata row is moving away from.

    The same shape as `note_occurrence_move` and for the same reason: a row that
    changes which occurrence it describes invalidates *two* archive rows, and
    `post_save` can only see the one it joined. Both free tests come first, so a
    create pays nothing and a suspended bulk write pays nothing per row.

    It compares `item` and resolves the binary only once the item has actually
    changed. Moving a reading between two filings of the *same* letter moves no
    projection row at all, and the stored `item__binary_id` comes back in the
    same query that answers the question, so an ordinary save costs one lookup.

    **No production writer reassigns `item`.** `_write_metadata` creates rows
    with `get_or_create` and never edits one; a changed KodaDash snapshot writes
    a new row rather than repointing an old one, because the artefact's own hash
    is part of the key. Nothing about the model forbids it though — `item` is an
    ordinary editable foreign key with no unique constraint over it — so this is
    the shell session and the next writer, at the price of one indexed lookup.
    """
    instance._archive_binary_before = None
    if instance._state.adding or archive_indexing_is_suspended():
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "item" not in set(update_fields):
        return
    stored = (
        sender._default_manager.filter(pk=instance.pk)
        .values_list("item_id", "item__binary_id")
        .first()
    )
    if stored is not None and stored[0] != instance.item_id:
        instance._archive_binary_before = stored[1]


@receiver(post_save, sender=OpinionArchiveMetadata, dispatch_uid="archive_refresh_metadata_saved")
def refresh_on_metadata_save(
    sender: type[OpinionArchiveMetadata], instance: Any, **kwargs: Any
) -> None:
    """A reading that was written or edited must be projected as it is.

    Both binaries, in the order they became wrong: the one the row left, then
    the one it joined. `refresh_archive_binary` ignores a null, so the ordinary
    case — a create, or a save that moved nothing — is one refresh, and a
    reading of an occurrence still waiting for its bytes is a safe no-op through
    `_binary_of`.

    Not narrowed by ``update_fields``, like every other handler here: four of
    the model's columns reach the row, `_write_metadata` writes eighteen at a
    time, and a handler that tried to decide which of them matter would be
    re-deriving `_occurrence_values` in miniature and wrong the day a column
    joins it. `_reindex` writes nothing when nothing changed.
    """
    refresh_archive_binary(getattr(instance, "_archive_binary_before", None))
    _refresh_behind_item(instance.item_id)


@receiver(
    post_delete, sender=OpinionArchiveMetadata, dispatch_uid="archive_refresh_metadata_removed"
)
def refresh_on_metadata_delete(
    sender: type[OpinionArchiveMetadata],
    instance: OpinionArchiveMetadata,
    origin: Any = None,
    **kwargs: Any,
) -> None:
    """A reading that is gone must stop being projected.

    `_started_here`, deliberately — the link's guard rather than the candidate's,
    and the two disagree here. A metadata row has exactly one foreign key,
    `item`, so the only cascade that can reach it is an occurrence's deletion,
    and that occurrence's own `post_delete` already owns the final refresh. It
    is also the only handler that can produce the *right* answer: the collector
    removes children before their parent, so a refresh fired from here during an
    Item cascade recomputes a row that still contains the occurrence being
    deleted — one statement early, which is the mistake the candidate handler
    was written to avoid rather than a version of it worth repeating.

    So refreshing from here during a cascade would be both redundant and briefly
    wrong, and the Item handler is what makes it neither. A direct
    `metadata.delete()` has no such owner and is refreshed here.
    """
    if _started_here(origin, OpinionArchiveMetadata):
        _refresh_behind_item(instance.item_id)


# ---------------------------------------------------------------------------
# Extracted text
# ---------------------------------------------------------------------------
#
# The sixth relation, and the one the archive's full-text search is made of.
# `body_text` is what the Estonian and simple vectors are built from and
# `has_body_text` is what the `Sisuga` filter and the coverage figure read, and
# both come from an `OpinionArchiveText` row that nothing about a binary
# touches.
#
# `opinion_text._record` is the one production writer: an `update_or_create` per
# binary, inside its own atomic block, called by `extract_all` for every letter
# whose text is not already current. Before this handler, a completed extraction
# left the corpus holding bodies the search could not see, and a *re*-extraction
# was worse than that — the row went on serving the previous parse, so a letter
# stayed findable by words that had been replaced. The one-directional lag check
# in `archive_index_findings` could see the first case and not the second; it is
# now a comparison of both columns in both directions (`_text_drift_findings`).
#
# One text row names exactly one binary, so this is class A like the rest and
# the refresh goes in the transaction `_record` already opens: an extraction
# that rolls back takes its projection write with it. Deliberately *not* batched
# behind a suspension around `extract_all` — that would commit hundreds of
# canonical bodies and converge their rows only at the end, so an extraction
# killed halfway would leave exactly the stale search this closes.


@receiver(pre_save, sender=OpinionArchiveText, dispatch_uid="archive_note_text_move")
def note_text_move(sender: type[OpinionArchiveText], instance: Any, **kwargs: Any) -> None:
    """Remember the binary a text row is moving away from.

    `binary` is a `OneToOneField`, so a move is only legal onto bytes that hold
    no text of their own — and it is legal, which is the whole reason for this:
    an ordinary save that repointed a body would leave the letter it came from
    still searchable by it.

    Both free tests before the lookup, as in `note_occurrence_move`, and
    `_state.adding` rather than `pk is None` because `BaseModel` fills the
    primary key in from a `uuid7` default. It believes `update_fields`, which is
    what keeps the real writer off this path entirely: `_record` hands
    `update_or_create` a defaults dict of eight concrete columns and none of
    them is `binary`, so Django saves with `update_fields` and an extraction pays
    no SELECT for a move it cannot have made.
    """
    instance._archive_binary_before = None
    if instance._state.adding or archive_indexing_is_suspended():
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "binary" not in set(update_fields):
        return
    stored = (
        sender._default_manager.filter(pk=instance.pk).values_list("binary_id", flat=True).first()
    )
    if stored != instance.binary_id:
        instance._archive_binary_before = stored


@receiver(post_save, sender=OpinionArchiveText, dispatch_uid="archive_refresh_text_saved")
def refresh_on_text_save(sender: type[OpinionArchiveText], instance: Any, **kwargs: Any) -> None:
    """Every extraction outcome, not only the one that found something.

    `DONE` with a body is the case that adds to the corpus; `NO_TEXT_LAYER`,
    `BLOCKED`, `FAILED` and a `DONE` that came back empty all *remove* from it,
    and a row left claiming a body after the policy stopped permitting the parse
    is the same defect wearing the other sign. Recomputing through `_row_values`
    covers all of them without this handler knowing what `ArchiveTextState`
    means — `_text_values` and the model's `has_body` decide that, in one place.

    Both binaries, in the order they became wrong. `refresh_archive_binary`
    ignores a null, so the ordinary re-extraction is one refresh.
    """
    refresh_archive_binary(getattr(instance, "_archive_binary_before", None))
    refresh_archive_binary(instance.binary_id)


@receiver(post_delete, sender=OpinionArchiveText, dispatch_uid="archive_refresh_text_removed")
def refresh_on_text_delete(
    sender: type[OpinionArchiveText],
    instance: OpinionArchiveText,
    origin: Any = None,
    **kwargs: Any,
) -> None:
    """A body that is gone must stop being searchable.

    `_binary_survives`, and here it is load-bearing rather than defensive.
    `OpinionArchiveText.binary` is `CASCADE` and is the model's only foreign
    key, so deleting a binary really does delete its text — and re-projecting
    that binary mid-cascade would insert a row the collector has already swept
    past, leaving the binary's own delete to fail on a foreign key at COMMIT.
    This is the first archive relation where that path is reachable at all: the
    occurrences and the candidates are kept off it by `OpinionArchiveItem.binary`
    being `PROTECT`, and a binary holding text but no filings is an ordinary
    state — a letter whose catalogue rows were removed while the evidence stayed.

    A direct `text.delete()` — dropping a parse to force a re-extraction — has
    no such owner, and clears the row's body here.
    """
    if _binary_survives(origin):
        refresh_archive_binary(instance.binary_id)
