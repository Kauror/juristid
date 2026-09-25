"""Projecting child content: entries, submissions, documents and their fragments.

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

from collections.abc import Sequence

from django.db.models import QuerySet
from django.utils import timezone

from app.core.richtext import plain_text
from app.core.text import normalize_for_matching
from app.documents.enums import DerivativeStatus
from app.documents.models import Document, DocumentTextFragment, DocumentVersion
from app.matters.models import (
    Entry,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.submissions.models import Submission

#: A single fragment is already bounded by the parser's own limits, but a
#: pathological one would still be copied here in full. Cutting it for the index
#: does not lose evidence — the fragment keeps the whole text and the original
#: keeps the bytes — so this is a bound on the projection, not on the record.
MAX_INDEXED_FRAGMENT_CHARACTERS = 200_000


def _generations(generations: Sequence[int] | None) -> Sequence[int]:
    """The generations a refresh writes: the ones given, or every live one.

    A full rebuild passes its own building generation. Everything else passes
    nothing, and so writes the active generation and, while a rebuild is
    filling one, the building generation too — which is what keeps a change
    committed during a rebuild in the generation that becomes active
    (`app.search.generations`, docs/adr/0118).
    """
    if generations is not None:
        return generations
    from app.search.generations import live_generations

    return live_generations()


def bounded_body(text: str) -> str:
    """Authored or extracted text, bounded for the projection only.

    PostgreSQL refuses a tsvector over 1 MiB, and the vectors are computed
    inside the business write that changed the text — so an unbounded body
    made a save of about a megabyte of unique words fail with a 500 and roll
    back, the text with it (ENG-084). The record keeps every character; the
    index keeps the first :data:`MAX_INDEXED_FRAGMENT_CHARACTERS`, which is the
    bound fragments and historical pages already had. Cut by character, so
    never inside a UTF-8 sequence.
    """
    return (text or "")[:MAX_INDEXED_FRAGMENT_CHARACTERS]


def names_with_folded_forms(names: list[str]) -> str:
    """Names, and each one again without its diacritics, once each.

    The alias tier compares a query's diacritic-free form with this column, so
    «Pollumajandustootjate Liit» reaches a row that names
    «Põllumajandustootjate Liit». The Matter row has always written both forms
    (`app.search.indexing._alias_text_for`); a child row that names an
    organisation now does too (ENG-031).
    """
    present = [name for name in names if name]
    return " ".join(dict.fromkeys([*present, *(normalize_for_matching(n) for n in present)]))


def indexable_entries() -> QuerySet[Entry]:
    return Entry.objects.select_related("matter", "author", "organisation")


def indexable_submissions() -> QuerySet[Submission]:
    return Submission.objects.select_related("matter").prefetch_related(
        "recipient_rows__organisation__aliases"
    )


def indexable_engagements() -> QuerySet:
    """Every `Kaasamine`, with the Matter its row will hang off.

    Unfiltered by visibility on purpose. This builds the projection; *reading*
    it is authorized at query time against the engagement's own current
    override, which is what lets somebody restrict a consultation and have it
    disappear from search on the next query with no reindex
    (`app.search.services._scoped_documents`).
    """
    return MatterEngagement.objects.select_related("matter")


def _engagement_values(engagement, now: object) -> dict[str, object]:
    """One `Kaasamine` as a search row.

    The same three things the Matter row used to swallow: the title, the note,
    and the link's *host* rather than the link. A campaign URL is mostly
    tracking parameters, and indexing those adds thousands of meaningless
    tokens without making anything findable; the host's own labels go in beside
    it because PostgreSQL reads `survey.alchemer.example` as one token and
    somebody typing the vendor's name would otherwise get nothing.

    The link terms are `alias_text` rather than `body_text` because that is what
    they are — alternate names for the same thing — and the title tier should
    not rank a Matter highly because a hostname happened to match.
    """
    return {
        "matter": engagement.matter,
        "source_kind": SearchSourceKind.ENGAGEMENT,
        "source_object_id": engagement.pk,
        "engagement": engagement,
        "title": engagement.title,
        "identifiers": "",
        "alias_text": " ".join(dict.fromkeys(engagement.link_search_terms)),
        "people_text": "",
        "body_text": bounded_body(engagement.note or ""),
        # No locator. `source_locator` says *where a result opens from when
        # the source is not the Teema itself* — a page number, an archive
        # section. A Kaasamine has no such place, and what used to be written
        # here was `kaasamine-<primary key>`: a database identifier printed at
        # a lawyer, saying nothing the `Kaasamine` badge beside it does not
        # already say, and changing on every reseed (docs/adr/0057).
        "source_locator": "",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def refresh_engagements(engagements: QuerySet, *, generations: Sequence[int] | None = None) -> int:
    rows = list(engagements)
    if not rows:
        return 0
    generations = _generations(generations)
    # **Delete for every row, insert only for the ones still on the file.**
    #
    # A record a lawyer removed is read here like any other — the builders use
    # the plain manager, so it arrives — and then projects nothing. That keeps
    # one shape of statement for both cases: the per-write refresh withdraws a
    # row the moment it is removed, and a full rebuild reaches the same index
    # without a second code path deciding what to skip (docs/adr/0102).
    now = timezone.now()
    identifiers = [engagement.pk for engagement in rows]
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.ENGAGEMENT,
        source_object_id__in=identifiers,
    ).delete()
    live = [row for row in rows if not row.is_removed]
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**_engagement_values(engagement, now), generation=generation)
            for engagement in live
            for generation in generations
        ]
    )
    return len(live)


def indexable_developments() -> QuerySet:
    """Every `Märge`, with the Matter its row will hang off.

    Unfiltered by visibility, like every other builder here: the projection
    covers everything and *reading* it is authorized at query time against the
    development's own current override.
    """
    return MatterProceduralDevelopment.objects.select_related("matter")


def _development_values(development, now: object) -> dict[str, object]:
    """One `Märge` as a search row.

    What a lawyer wrote and would search for: the sentence naming what
    happened, and the note underneath it. Nothing else on the record is text a
    person would type into a search box — a phase key is a vocabulary token and
    a precision is a flag.

    `note` is rich text, so it goes through `plain_text` for the reason
    `_entry_values` does: indexing markup makes `<p>` a searchable token and
    puts tag names in the way of the words.
    """
    return {
        "matter": development.matter,
        "source_kind": SearchSourceKind.PROCEDURAL_DEVELOPMENT,
        "source_object_id": development.pk,
        "development": development,
        "title": development.title,
        "identifiers": "",
        "alias_text": "",
        "people_text": "",
        "body_text": bounded_body(plain_text(development.note or "")),
        # No locator, for `_engagement_values`' reason: a `Märge` opens on its
        # Teema and has no place inside anything.
        "source_locator": "",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def refresh_developments(
    developments: QuerySet, *, generations: Sequence[int] | None = None
) -> int:
    rows = list(developments)
    if not rows:
        return 0
    generations = _generations(generations)
    # **Delete for every row, insert only for the ones still on the file.**
    #
    # A record a lawyer removed is read here like any other — the builders use
    # the plain manager, so it arrives — and then projects nothing. That keeps
    # one shape of statement for both cases: the per-write refresh withdraws a
    # row the moment it is removed, and a full rebuild reaches the same index
    # without a second code path deciding what to skip (docs/adr/0102).
    now = timezone.now()
    identifiers = [development.pk for development in rows]
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT,
        source_object_id__in=identifiers,
    ).delete()
    live = [row for row in rows if not row.is_removed]
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**_development_values(development, now), generation=generation)
            for development in live
            for generation in generations
        ]
    )
    return len(live)


def indexable_positions() -> QuerySet:
    """Every `Meile saadetud tagasiside` and `Teiste arvamus`."""
    return MatterExternalPosition.objects.select_related("matter", "organisation")


def _position_values(position, now: object) -> dict[str, object]:
    """One recorded opinion as a search row.

    The organisation goes in `alias_text` rather than the title tier, and that
    is what answers the second half of the report: an Organisation known to a
    Matter *only* through feedback was unfindable, because the Matter row
    carries senders and this body is not one. It is an alternate name for the
    thing rather than its name, so it must not rank a Matter as highly as a
    title match — the reasoning `_engagement_values` gives for link hosts.

    `source_label` rides beside it for the same reason: free-entry source
    naming is how an opinion from a body nobody has filed against yet is
    recorded, and it is exactly what somebody would search for.

    **`lawyer_note` is deliberately not indexed, and `url` is not either.**
    The note is this office's own assessment of a third party, and §9 of the
    lawyer-workflow package decided that disclosing it is a decision somebody
    makes rather than a convenience search performs — putting it in the corpus
    would make it findable by everyone who can read the Matter. The URL is left
    out for `_engagement_values`' reason in reverse: what would be worth
    indexing there is the host, and a position's link is a citation rather than
    a name anybody searches by.

    Visibility is the row's own: this kind maps to
    `external_position__visibility_override`, so a restricted opinion
    disappears from search on the next query with no reindex.
    """
    organisation = getattr(position.organisation, "name", "") or ""
    return {
        "matter": position.matter,
        "source_kind": SearchSourceKind.EXTERNAL_POSITION,
        "source_object_id": position.pk,
        "external_position": position,
        # Who took the position, as the row's own title: what a result shows
        # under the Teema to say which opinion matched (ENG-083).
        "title": organisation or position.source_label or "Seisukoht",
        "identifiers": "",
        "alias_text": names_with_folded_forms([organisation, position.source_label]),
        "people_text": "",
        # The summary is the body, so a match inside it can be quoted. It used
        # to be the row's title, and a title is never excerpted: an opinion
        # found by a phrase in its summary showed the Teema and nothing that
        # said why (ENG-083).
        "body_text": bounded_body(position.summary or ""),
        "source_locator": "",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def refresh_positions(positions: QuerySet, *, generations: Sequence[int] | None = None) -> int:
    rows = list(positions)
    if not rows:
        return 0
    generations = _generations(generations)
    # **Delete for every row, insert only for the ones still on the file.**
    #
    # A record a lawyer removed is read here like any other — the builders use
    # the plain manager, so it arrives — and then projects nothing. That keeps
    # one shape of statement for both cases: the per-write refresh withdraws a
    # row the moment it is removed, and a full rebuild reaches the same index
    # without a second code path deciding what to skip (docs/adr/0102).
    now = timezone.now()
    identifiers = [position.pk for position in rows]
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.EXTERNAL_POSITION,
        source_object_id__in=identifiers,
    ).delete()
    live = [row for row in rows if not row.is_removed]
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**_position_values(position, now), generation=generation)
            for position in live
            for generation in generations
        ]
    )
    return len(live)


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
        "alias_text": names_with_folded_forms(
            [entry.organisation.name if entry.organisation else ""]
        ),
        # The author, in a column of their own: a search for a colleague's name
        # is not a search for an organisation, and the result says «Autor»
        # instead of «Asutus, valdkond või silt» (ENG-083).
        "people_text": entry.author.display_name if entry.author else "",
        "body_text": bounded_body(body),
        # As above: an entry opens at its own anchor on the Teema page, which
        # `_target_url` builds from `entry_id`. The locator was a primary key.
        "source_locator": "",
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
        "alias_text": " ".join(
            dict.fromkeys([names_with_folded_forms(recipients), *aliases])
        ).strip(),
        "people_text": "",
        # `summary` and `notes`, both canonical authored text.
        #
        # **`summary` is here because the substance moved into it.** Until
        # docs/adr/0095 §2 the descriptive sentence a lawyer wrote about a sent
        # opinion went into `title`, which this projection indexes in the
        # identity tier — so «pakendiseaduse üleminekuaeg» found the opinion
        # that argued it. That box now asks for a summary instead and `title`
        # carries the file's name, so indexing only the two old columns would
        # quietly retire a search that works today. This is the whole reason
        # `INDEX_VERSION` moves in this release (app/search/models.py).
        #
        # The final PDF's contents are still *not* copied here — they are
        # indexed through their own DocumentVersion, so a match can say which
        # file and which page it came from instead of attributing a whole
        # document to a Submission row (Stage-2B brief 38).
        "body_text": bounded_body(
            "\n".join(part for part in (submission.summary, submission.notes) if part)
        ),
        # As above. `_target_url` builds the anchor from `submission_id`.
        "source_locator": "",
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
        "people_text": "",
        "body_text": bounded_body(fragment.text),
        "source_locator": fragment.locator_label,
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def indexable_documents() -> QuerySet[Document]:
    """Every Document on a Matter that exists, with what its row names.

    Whatever its extraction state — never run, not applicable, failed, read
    only at intake — because a file is findable by its name whether or not
    anybody has read its contents (ENG-030). Unfiltered by visibility, like
    every builder here: reading is authorized against the Document's own
    current override at query time.
    """
    return (
        Document.objects.filter(matter__deleted_at__isnull=True)
        .select_related("matter", "current_version")
        .prefetch_related("versions")
    )


def _document_filenames(document: Document) -> str:
    """Every name the document's bytes were stored under, current first.

    Historical versions' names are identifiers too: somebody who remembers
    `eelnou_v1.docx` is looking for this document even after `eelnou_v2.docx`
    replaced it. Never a storage key and never an id — those are not names
    anybody types, and a key is an internal path.
    """
    current = document.current_version
    names = [current.original_filename] if current is not None else []
    for version in sorted(document.versions.all(), key=lambda row: -row.version_number):
        names.append(version.original_filename)
    return " ".join(dict.fromkeys(name for name in names if name))


def document_values(document: Document, now: object) -> dict[str, object]:
    """One Document as a search row: its title and its filenames.

    Metadata only. Content stays with `DOCUMENT_FRAGMENT` rows, which exist
    only where a derivative does (docs/adr/0072). The row opens on the
    document's own page, which is what a fragment row opens on too.
    """
    return {
        "matter": document.matter,
        "source_kind": SearchSourceKind.DOCUMENT,
        "source_object_id": document.pk,
        "document": document,
        "document_version": document.current_version,
        "title": document.title,
        "identifiers": bounded_body(_document_filenames(document)),
        "alias_text": "",
        "people_text": "",
        "body_text": "",
        "source_locator": "",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def refresh_documents(
    documents: QuerySet[Document], *, generations: Sequence[int] | None = None
) -> int:
    rows = list(documents)
    if not rows:
        return 0
    generations = _generations(generations)
    now = timezone.now()
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.DOCUMENT,
        source_object_id__in=[document.pk for document in rows],
    ).delete()
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**document_values(document, now), generation=generation)
            for document in rows
            for generation in generations
        ]
    )
    return len(rows)


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
        "people_text": "",
        "body_text": bounded_body(page.derived_text),
        "source_locator": location[:200],
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


def refresh_source_links(links: QuerySet, *, generations: Sequence[int] | None = None) -> int:
    rows = list(links)
    if not rows:
        return 0
    generations = _generations(generations)
    now = timezone.now()
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.LEGACY_SOURCE_PAGE,
        source_object_id__in=[link.pk for link in rows],
    ).delete()
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**source_link_values(link, now), generation=generation)
            for link in rows
            for generation in generations
        ]
    )
    return len(rows)


def refresh_entries(entries: QuerySet[Entry], *, generations: Sequence[int] | None = None) -> int:
    rows = list(entries)
    if not rows:
        return 0
    generations = _generations(generations)
    # **Delete for every row, insert only for the ones still on the file.**
    #
    # A record a lawyer removed is read here like any other — the builders use
    # the plain manager, so it arrives — and then projects nothing. That keeps
    # one shape of statement for both cases: the per-write refresh withdraws a
    # row the moment it is removed, and a full rebuild reaches the same index
    # without a second code path deciding what to skip (docs/adr/0102).
    now = timezone.now()
    identifiers = [entry.pk for entry in rows]
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.ENTRY,
        source_object_id__in=identifiers,
    ).delete()
    live = [row for row in rows if not row.is_removed]
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**_entry_values(entry, now), generation=generation)
            for entry in live
            for generation in generations
        ]
    )
    return len(live)


def refresh_submissions(
    submissions: QuerySet[Submission], *, generations: Sequence[int] | None = None
) -> int:
    rows = list(submissions)
    if not rows:
        return 0
    generations = _generations(generations)
    now = timezone.now()
    identifiers = [submission.pk for submission in rows]
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.SUBMISSION,
        source_object_id__in=identifiers,
    ).delete()
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**_submission_values(submission, now), generation=generation)
            for submission in rows
            for generation in generations
        ]
    )
    return len(rows)


def refresh_fragments(
    fragments: QuerySet[DocumentTextFragment], *, generations: Sequence[int] | None = None
) -> int:
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
    generations = _generations(generations)
    now = timezone.now()
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT,
        source_object_id__in=[fragment.pk for fragment in rows],
    ).delete()
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**fragment_values(fragment, now), generation=generation)
            for fragment in rows
            for generation in generations
        ]
    )
    return len(rows)


def refresh_version_fragments(
    version: DocumentVersion, *, generations: Sequence[int] | None = None
) -> int:
    """Rewrite the projection for one document version's live fragments.

    Deleting by ``document_version`` rather than by fragment id matters: a
    reprocess that produced *fewer* fragments than last time would otherwise
    leave the surplus rows behind, pointing at pages the current derivative no
    longer has.
    """
    now = timezone.now()
    generations = _generations(generations)
    SearchDocument.objects.filter(
        generation__in=generations,
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT,
        document_version=version,
    ).delete()
    fragments = list(indexable_fragments().filter(derivative__version=version).order_by("ordinal"))
    if not fragments:
        return 0
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**fragment_values(fragment, now), generation=generation)
            for fragment in fragments
            for generation in generations
        ]
    )
    return len(fragments)
