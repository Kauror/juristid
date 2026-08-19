"""Finding words that exist only inside a file, and knowing where they were.

Two questions decide whether document search is worth having. Can a lawyer find
a phrase they remember reading? And when they do, does the result tell them
enough to open the right page rather than the right file?

The corpus places each marker word in exactly one location, so a passing test
proves *which* source answered rather than merely that something did
(Stage-2B brief 65).
"""

from __future__ import annotations

import pytest

from app.search.indexing import rebuild_all
from app.search.models import SearchDocument, SearchSourceKind
from app.search.services import (
    MATCH_REFERENCE,
    TIER_DOCUMENT_TITLE,
    TIER_FULLTEXT,
    TIER_REFERENCE,
    result_count,
    search,
    search_documents,
)
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
EML = "message/rfc822"


@pytest.fixture
def indexed_matter(specialist, capture_evidence, extract):
    """One Matter carrying every format, extracted and indexed."""
    matter = factories.MatterFactory(
        owner=specialist,
        reference_year=2026,
        reference_number=42,
        title="Pakendiseaduse muutmise eelnõu",
    )
    files = [
        (corpus.government_pdf(), "kaaskiri.pdf", PDF),
        (corpus.draft_docx(), "markused.docx", DOCX),
        (corpus.annex_xlsx(), "lisa.xlsx", XLSX),
        (corpus.briefing_pptx(), "ulevaade.pptx", PPTX),
        (corpus.consultation_eml(), "kiri.eml", EML),
    ]
    for content, filename, mime_type in files:
        version = capture_evidence(matter, content, filename, mime_type, title=filename)
        extract(version)
    factories.EntryFactory(
        matter=matter, author=specialist, body="<p>Helistasin ministeeriumi nõunikule.</p>"
    )
    factories.SubmissionFactory(
        matter=matter,
        title="Koja arvamus pakendiseaduse eelnõule",
        reference="KODA-2026-42",
        notes="Toetame põhimõtet, kuid palume pikemat üleminekuaega.",
    )
    return matter


# -- finding a word that exists in exactly one place ------------------------


@pytest.mark.parametrize(
    ("term", "expected_locator"),
    [
        (corpus.ONLY_ON_PDF_PAGE_4, "lk 4"),
        (corpus.ONLY_IN_DOCX_TABLE, "tabel 1"),
        (corpus.ONLY_ON_XLSX_SHEET_2, 'leht "Kulud", read 1–2'),
        (corpus.ONLY_ON_PPTX_SLIDE_3, "slaid 3"),
        (corpus.ONLY_IN_EMAIL_BODY, "kirja sisu"),
    ],
)
def test_a_word_inside_a_file_is_found_with_its_locator(
    indexed_matter, specialist, term, expected_locator
) -> None:
    results = search(query=term, user=specialist)

    assert results, f"nothing matched {term!r}"
    assert results[0].source_kind == SearchSourceKind.DOCUMENT_FRAGMENT
    assert results[0].source_locator == expected_locator


def test_a_word_only_in_an_email_attachment_is_found_in_the_attachment(
    indexed_matter, specialist, extract
) -> None:
    """The annex is searchable in its own right, not only through the message."""
    from app.documents.models import DocumentVersion

    for version in DocumentVersion.objects.filter(extraction_state="PENDING"):
        extract(version)

    results = search(query=corpus.ONLY_IN_ATTACHMENT, user=specialist)
    assert results
    assert results[0].document_title.endswith(".pdf")


def test_a_result_names_the_document_it_came_from(indexed_matter, specialist) -> None:
    results = search(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)
    assert results[0].document_title == "kaaskiri.pdf"
    assert results[0].document_id is not None
    assert results[0].document_version_id is not None


def test_an_entry_is_findable_by_words_only_it_contains(indexed_matter, specialist) -> None:
    results = search(query="nõunikule", user=specialist)
    assert [result.source_kind for result in results] == [SearchSourceKind.ENTRY]
    assert results[0].entry_id is not None


def test_a_submission_is_findable_by_its_own_reference(indexed_matter, specialist) -> None:
    results = search(query="KODA-2026-42", user=specialist)
    assert any(result.source_kind == SearchSourceKind.SUBMISSION for result in results)


def test_a_submission_is_findable_by_its_notes(indexed_matter, specialist) -> None:
    results = search(query="üleminekuaega", user=specialist)
    assert [result.source_kind for result in results] == [SearchSourceKind.SUBMISSION]


# -- ranking ---------------------------------------------------------------


def test_an_exact_reference_outranks_everything_in_the_documents(
    indexed_matter, specialist
) -> None:
    """A phrase on page 14 of an annex must never outrank `2026_42`."""
    results = search(query="2026_42", user=specialist)

    assert results[0].match_kind == MATCH_REFERENCE
    assert results[0].source_kind == SearchSourceKind.MATTER


def test_a_reference_lookup_does_not_return_the_matters_pages(indexed_matter, specialist) -> None:
    """Typing a reference means "open that file", not "list its contents"."""
    assert result_count(query="2026_42", user=specialist) == 1


def test_a_matter_title_match_outranks_a_document_body_match(indexed_matter, specialist) -> None:
    documents = list(search_documents(query="Pakendiseaduse muutmise eelnõu", user=specialist)[:5])
    tiers = [int(document.match_tier) for document in documents]

    assert tiers == sorted(tiers, reverse=True)
    assert documents[0].source_kind == SearchSourceKind.MATTER
    assert tiers[0] > TIER_FULLTEXT


def test_a_document_name_match_outranks_a_body_match(indexed_matter, specialist) -> None:
    documents = list(search_documents(query="kaaskiri.pdf", user=specialist))
    assert documents
    assert int(documents[0].match_tier) == TIER_DOCUMENT_TITLE


def test_the_tier_constants_keep_reference_at_the_top() -> None:
    assert TIER_REFERENCE > TIER_DOCUMENT_TITLE > TIER_FULLTEXT


# -- snippets --------------------------------------------------------------


def test_a_body_match_carries_a_highlighted_snippet(indexed_matter, specialist) -> None:
    results = search(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)

    runs = results[0].snippet
    assert runs, "a body match should come with an excerpt"
    highlighted = [run.text for run in runs if run.highlight]
    assert any(corpus.ONLY_ON_PDF_PAGE_4 in text.lower() for text in highlighted)


def test_a_snippet_is_text_and_carries_no_markup(indexed_matter, specialist) -> None:
    """The highlight is a tag the template writes, never one the database sent.

    PostgreSQL marks the matched words with two private-use characters; the
    service splits on them. Nothing that came out of a document arrives at the
    page as HTML (Stage-2B brief 42, 70).
    """
    for result in search(query="eelnõu", user=specialist):
        for run in result.snippet:
            assert "<" not in run.text
            assert "⦑" not in run.text
            assert "⦒" not in run.text


def test_a_snippet_is_bounded(indexed_matter, specialist) -> None:
    for result in search(query="eelnõu", user=specialist):
        assert len("".join(run.text for run in result.snippet)) < 2000


# -- index lifecycle -------------------------------------------------------


def test_extraction_indexes_its_fragments_in_the_same_breath(
    normal_matter, specialist, capture_evidence, extract
) -> None:
    """A committed derivative with no search row is content that cannot be
    found — the silent half of every search complaint."""
    version = capture_evidence(normal_matter, corpus.government_pdf(), "uus.pdf", PDF)
    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist) == 0

    extract(version)

    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist) == 1


def test_a_new_entry_is_findable_immediately(normal_matter, specialist) -> None:
    factories.EntryFactory(
        matter=normal_matter, author=specialist, body="<p>Erakordne märksõna: tellingud.</p>"
    )
    assert result_count(query="tellingud", user=specialist) == 1


def test_an_edited_entry_is_findable_by_its_new_text(normal_matter, specialist) -> None:
    entry = factories.EntryFactory(
        matter=normal_matter, author=specialist, body="<p>Esialgne tekst.</p>"
    )
    entry.body = "<p>Parandatud tekst: kraanaraam.</p>"
    entry.save()

    assert result_count(query="kraanaraam", user=specialist) == 1
    assert result_count(query="Esialgne", user=specialist) == 0


def test_a_deleted_entry_leaves_no_search_row(normal_matter, specialist) -> None:
    entry = factories.EntryFactory(
        matter=normal_matter, author=specialist, body="<p>Kaduv märksõna: sillutis.</p>"
    )
    entry.delete()

    assert result_count(query="sillutis", user=specialist) == 0


def test_renaming_a_document_renames_it_in_every_fragment(
    normal_matter, specialist, capture_evidence, extract
) -> None:
    version = capture_evidence(
        normal_matter, corpus.government_pdf(), "kaaskiri.pdf", PDF, title="Vana nimi"
    )
    extract(version)

    document = version.document
    document.title = "Uus nimi"
    document.save()

    results = search(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)
    assert results[0].document_title == "Uus nimi"


def test_html_in_an_entry_body_is_not_indexed_as_words(normal_matter, specialist) -> None:
    """Indexing markup makes `strong` a searchable word and puts a tag in the
    snippet."""
    factories.EntryFactory(
        matter=normal_matter,
        author=specialist,
        body="<p><strong>Rõhutatud</strong> märkus.</p>",
    )
    row = SearchDocument.objects.get(source_kind=SearchSourceKind.ENTRY)
    assert "<" not in row.body_text
    assert result_count(query="strong", user=specialist) == 0
    assert result_count(query="Rõhutatud", user=specialist) == 1


# -- rebuilding ------------------------------------------------------------


def test_a_full_rebuild_reproduces_the_same_results(indexed_matter, specialist) -> None:
    before = {
        term: result_count(query=term, user=specialist)
        for term in (
            corpus.ONLY_ON_PDF_PAGE_4,
            corpus.ONLY_IN_DOCX_TABLE,
            corpus.ONLY_ON_XLSX_SHEET_2,
            "nõunikule",
            "üleminekuaega",
            "2026_42",
        )
    }
    assert all(count > 0 for count in before.values())

    rebuild_all()

    after = {term: result_count(query=term, user=specialist) for term in before}
    assert after == before


def test_a_rebuild_counts_every_source_kind(indexed_matter) -> None:
    result = rebuild_all()

    assert result.matters >= 1
    assert result.entries >= 1
    assert result.submissions >= 1
    assert result.fragments >= 10
    assert result.documents == SearchDocument.objects.count()


def test_a_rebuild_from_empty_and_from_stale_agree(indexed_matter, specialist) -> None:
    rebuild_all()
    from_stale = result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)

    SearchDocument.objects.all().delete()
    rebuild_all()

    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist) == from_stale


def test_an_interrupted_rebuild_leaves_the_previous_index_complete(
    indexed_matter, specialist, monkeypatch
) -> None:
    """Extends the Stage-2A regression to the sources Stage 2B added.

    A partially rebuilt index answers confidently and silently with a fraction
    of the corpus, and "vasteid ei leitud" looks the same whether the document
    does not exist or the rebuild died before reaching it (docs/adr/0013).
    """
    before = result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)
    assert before == 1

    from app.search import indexing

    def explode(*args, **kwargs):
        raise RuntimeError("rebuild interrupted after the matters were written")

    monkeypatch.setattr(indexing, "_rebuild_children", explode)
    with pytest.raises(RuntimeError):
        rebuild_all()

    assert result_count(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist) == before


def test_deleting_all_derived_state_and_rebuilding_restores_search(
    indexed_matter, specialist
) -> None:
    """Evidence → derivatives → SearchDocument, proven end to end.

    Everything derived is deleted; only the Matter, its children and the
    evidence bytes remain. Search comes back from those.
    """
    from app.documents.extraction.orchestrator import (
        claim_version,
        discard_derivatives,
        extract_document_version,
    )
    from app.documents.models import DocumentVersion

    term = corpus.ONLY_ON_PDF_PAGE_4
    before = result_count(query=term, user=specialist)
    checksums = dict(DocumentVersion.objects.values_list("pk", "sha256"))

    for version in DocumentVersion.objects.all():
        discard_derivatives(version)
    SearchDocument.objects.all().delete()
    assert result_count(query=term, user=specialist) == 0

    for version in DocumentVersion.objects.all():
        claimed = claim_version(version.pk, force=True)
        extract_document_version(claimed)
    rebuild_all()

    assert result_count(query=term, user=specialist) == before
    assert dict(DocumentVersion.objects.values_list("pk", "sha256")) == checksums
