"""Projecting child content: entries, submissions and document fragments.

Stage 2A indexed Matter-level content only, and said why: indexing a child
safely needs the child's *current* restriction to participate in the query
rather than a copy of it in the index (docs/adr/0013). Stage 2B supplies that
through real foreign keys on `SearchDocument`, so this module can write the rows
without also having to write an authorization value — which remains the one
column this table must never have.

Every function here follows the Stage 2A shape: delete the rows for a source,
then insert what it currently is. Never update in place, never read the existing
index to decide what to write. A projection that depends on its own previous
state cannot be trusted to converge, and converging from a half-built index is
the whole reason a rebuild is a usable recovery tool.

**Fragment text is copied into `body_text`.** It already exists in
`DocumentTextFragment`, so this duplicates it, and that is a deliberate trade.
The alternative — computing the vector from a joined column and reading the
snippet through the join — needs a subquery per vector and a second query per
result page, in exchange for text that PostgreSQL compresses anyway. The layering
still holds: evidence rebuilds fragments, fragments rebuild this
(Stage-2B brief 47).
"""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone

from app.core.richtext import plain_text
from app.documents.enums import DerivativeStatus
from app.documents.models import DocumentTextFragment, DocumentVersion
from app.matters.models import Entry
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.submissions.models import Submission

#: A single fragment is already bounded by the parser's own limits, but a
#: pathological one would still be copied here in full. Cutting it for the index
#: does not lose evidence — the fragment keeps the whole text and the original
#: keeps the bytes — so this is a bound on the projection, not on the record.
MAX_INDEXED_FRAGMENT_CHARACTERS = 200_000


def indexable_entries() -> QuerySet[Entry]:
    return Entry.objects.select_related("matter", "author", "organisation")


def indexable_submissions() -> QuerySet[Submission]:
    return Submission.objects.select_related("matter").prefetch_related(
        "recipient_rows__organisation__aliases"
    )


def indexable_fragments() -> QuerySet[DocumentTextFragment]:
    """Fragments of derivatives that are currently live.

    SUPERSEDED and FAILED derivatives are deliberately excluded. A superseded
    derivative's fragments are the previous parser's opinion, kept until the
    rebuild that removes them; indexing both would return the same page twice
    with slightly different text and no way for a reader to tell which is
    current.
    """
    return DocumentTextFragment.objects.filter(
        derivative__status=DerivativeStatus.ACTIVE
    ).select_related(
        "derivative",
        "derivative__version",
        "derivative__version__document",
        "derivative__version__document__matter",
    )


def _entry_values(entry: Entry, now: object) -> dict[str, object]:
    # The body is sanitised HTML; the index stores its text. Putting markup in
    # a tsvector indexes `<strong>` as a word, and puts a tag in every snippet.
    body = plain_text(entry.body)
    return {
        "matter": entry.matter,
        "source_kind": SearchSourceKind.ENTRY,
        "source_object_id": entry.pk,
        "entry": entry,
        "title": entry.get_kind_display(),
        "identifiers": "",
        "alias_text": " ".join(
            part
            for part in (
                entry.organisation.name if entry.organisation else "",
                entry.author.display_name if entry.author else "",
            )
            if part
        ),
        "body_text": body,
        "source_locator": f"sissekanne-{entry.pk}",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def _submission_values(submission: Submission, now: object) -> dict[str, object]:
    recipients = [
        row.organisation.name for row in submission.recipient_rows.all() if row.organisation_id
    ]
    aliases = [
        alias.alias
        for row in submission.recipient_rows.all()
        if row.organisation_id
        for alias in row.organisation.aliases.all()
    ]
    return {
        "matter": submission.matter,
        "source_kind": SearchSourceKind.SUBMISSION,
        "source_object_id": submission.pk,
        "submission": submission,
        "title": submission.title,
        # The Koda reference on a sent opinion is what a ministry quotes back at
        # us, so it belongs in the exact-identifier tier rather than the body.
        "identifiers": submission.reference or "",
        "alias_text": " ".join(dict.fromkeys([*recipients, *aliases])),
        # `notes` is canonical authored text. The final PDF's contents are *not*
        # copied here — they are indexed through their own DocumentVersion, so
        # a match can say which file and which page it came from instead of
        # attributing a whole document to a Submission row (Stage-2B brief 38).
        "body_text": submission.notes or "",
        "source_locator": f"arvamus-{submission.pk}",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def fragment_values(fragment: DocumentTextFragment, now: object) -> dict[str, object]:
    version = fragment.derivative.version
    document = version.document
    return {
        "matter": document.matter,
        "source_kind": SearchSourceKind.DOCUMENT_FRAGMENT,
        "source_object_id": fragment.pk,
        "document": document,
        "document_version": version,
        "fragment": fragment,
        # The filename, not the fragment's text. A result has to say which file
        # matched, and the title tier is where that belongs.
        "title": document.title,
        "identifiers": version.original_filename,
        "alias_text": "",
        "body_text": fragment.text[:MAX_INDEXED_FRAGMENT_CHARACTERS],
        "source_locator": fragment.locator_label,
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def indexable_source_links() -> QuerySet:
    """Matter↔page relationships, with the page they project."""
    from app.legacy_import.source_pages import MatterSourcePage

    return MatterSourcePage.objects.select_related("matter", "source_page")


def source_link_values(link, now: object) -> dict[str, object]:
    """One search row for one Matter's claim on one historical page.

    The narrative is the body; the section and page title are the identity a
    lawyer actually searches by. Raw page XML is deliberately absent — it is
    source evidence, it is full of OneNote markup, and indexing it would fill
    the corpus with attribute names (Stage-2D brief 36).
    """
    page = link.source_page
    location = " · ".join(part for part in (page.source_section, page.source_parent_page) if part)
    return {
        "matter": link.matter,
        "source_kind": SearchSourceKind.LEGACY_SOURCE_PAGE,
        "source_object_id": link.pk,
        "matter_source_page": link,
        "title": page.title,
        "identifiers": page.reference_tokens,
        "alias_text": " ".join(
            part
            for part in (page.source_section, page.source_section_group, page.source_parent_page)
            if part
        ),
        "body_text": page.derived_text[:MAX_INDEXED_FRAGMENT_CHARACTERS],
        "source_locator": location[:200],
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def refresh_source_links(links: QuerySet) -> int:
    rows = list(links)
    if not rows:
        return 0
    now = timezone.now()
    SearchDocument.objects.filter(
        source_kind=SearchSourceKind.LEGACY_SOURCE_PAGE,
        source_object_id__in=[link.pk for link in rows],
    ).delete()
    SearchDocument.objects.bulk_create(
        [SearchDocument(**source_link_values(link, now)) for link in rows]
    )
    return len(rows)


def refresh_entries(entries: QuerySet[Entry]) -> int:
    rows = list(entries)
    if not rows:
        return 0
    now = timezone.now()
    identifiers = [entry.pk for entry in rows]
    SearchDocument.objects.filter(
        source_kind=SearchSourceKind.ENTRY, source_object_id__in=identifiers
    ).delete()
    SearchDocument.objects.bulk_create(
        [SearchDocument(**_entry_values(entry, now)) for entry in rows]
    )
    return len(rows)


def refresh_submissions(submissions: QuerySet[Submission]) -> int:
    rows = list(submissions)
    if not rows:
        return 0
    now = timezone.now()
    identifiers = [submission.pk for submission in rows]
    SearchDocument.objects.filter(
        source_kind=SearchSourceKind.SUBMISSION, source_object_id__in=identifiers
    ).delete()
    SearchDocument.objects.bulk_create(
        [SearchDocument(**_submission_values(submission, now)) for submission in rows]
    )
    return len(rows)


def refresh_fragments(fragments: QuerySet[DocumentTextFragment]) -> int:
    """Rewrite the projection for a set of fragments. Idempotent.

    Deletes before it inserts, like every other function in this module. An
    earlier version did not: it was called only by the full rebuild, which had
    just emptied the table, so there was nothing to delete. That reasoning held
    for exactly one of the rebuild's two modes. ``rebuild_all(clear=False)`` —
    the operator's ``--keep-existing`` — refills a table that still has rows in
    it, and every fragment that was already indexed then hit
    ``search_one_document_per_source_object``: one ``IntegrityError``, the whole
    rebuild transaction gone, and a recovery flag that could not be used on any
    corpus that had ever been indexed.

    The delete costs one statement per batch against a partial unique index, on
    a table the caller is rewriting anyway. That is a smaller price than a mode
    whose only working case is the one where it had nothing to do.
    """
    rows = list(fragments)
    if not rows:
        return 0
    now = timezone.now()
    SearchDocument.objects.filter(
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT,
        source_object_id__in=[fragment.pk for fragment in rows],
    ).delete()
    SearchDocument.objects.bulk_create(
        [SearchDocument(**fragment_values(fragment, now)) for fragment in rows]
    )
    return len(rows)


def refresh_version_fragments(version: DocumentVersion) -> int:
    """Rewrite the projection for one document version's live fragments.

    Deleting by ``document_version`` rather than by fragment id matters: a
    reprocess that produced *fewer* fragments than last time would otherwise
    leave the surplus rows behind, pointing at pages the current derivative no
    longer has.
    """
    now = timezone.now()
    SearchDocument.objects.filter(
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT, document_version=version
    ).delete()
    fragments = list(indexable_fragments().filter(derivative__version=version).order_by("ordinal"))
    if not fragments:
        return 0
    SearchDocument.objects.bulk_create(
        [SearchDocument(**fragment_values(fragment, now)) for fragment in fragments]
    )
    return len(fragments)
