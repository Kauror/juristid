"""The search projection says what the record says, and never stops a save.

Three findings, each proven on the code before it was fixed:

* **ENG-030** — a Document reached the projection only through the rows of an
  ACTIVE derivative. Since docs/adr/0072 nothing extracts an ordinary upload, so
  a newly filed document could not be found by its title or its filename at
  all. ADR 0072 gave up searching *inside* documents, not finding them by name.
* **ENG-082** — `plain_text` returned nh3's output, which is escaped HTML with
  the tags removed: `&amp;` reached the index and then the snippet, where the
  template escaped it again, and `<p>Tere</p><p>kolleeg</p>` became one word.
* **ENG-084** — authored text was copied into the projection uncapped, so a
  body of about a megabyte of unique words exceeded PostgreSQL's 1 MiB tsvector
  limit inside the lawyer's own save, and the save failed with a 500.
"""

from __future__ import annotations

import random

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.core.richtext import plain_text
from app.documents.enums import ExtractionState
from app.documents.extraction.email_common import html_body_to_text
from app.search.child_indexing import MAX_INDEXED_FRAGMENT_CHARACTERS
from app.search.indexing import rebuild_all
from app.search.management.commands.check_search_integrity import build_report
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import search
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

TXT = "text/plain"
PDF = "application/pdf"


def _kinds(results) -> set[str]:
    return {result.source_kind for result in results}


def _document_results(results, document) -> list:
    return [r for r in results if r.document_id == document.pk]


# ---------------------------------------------------------------------------
# ENG-030 — every document can be found by name
# ---------------------------------------------------------------------------


@pytest.fixture
def unextracted(specialist, capture_evidence):
    """A document nobody ever read — the ordinary state of an upload."""
    matter = factories.MatterFactory(owner=specialist, title="Kliimaseaduse eelnõu")
    version = capture_evidence(
        matter,
        b"Kliimaseaduse kooskolastuskiri, lihttekst.",
        "zqxfilename-2026.txt",
        TXT,
        title="Kliimaseaduse ZQXDOCTITLE kooskõlastuskiri",
    )
    assert version.extraction_state == ExtractionState.PENDING
    version.document.refresh_from_db()
    return version.document


@pytest.mark.parametrize(
    "term",
    ["ZQXDOCTITLE", "zqxfilename-2026.txt", "zqxfilename", "kooskõlastuskiri"],
)
def test_an_unextracted_document_is_found_by_title_and_filename(unextracted, specialist, term):
    results = search(query=term, user=specialist)

    hits = _document_results(results, unextracted)
    assert hits, f"{term!r} found nothing"
    assert hits[0].source_kind == SearchSourceKind.DOCUMENT
    assert hits[0].document_title == unextracted.title


@pytest.mark.parametrize(
    "state",
    [ExtractionState.NOT_APPLICABLE, ExtractionState.FAILED, ExtractionState.INTAKE_READ],
)
def test_any_extraction_state_is_findable_by_name(state, specialist, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(matter, b"PK\x03\x04", "allkiri.asice", TXT, title="Allkirjastatud")
    type(version).objects.filter(pk=version.pk).update(extraction_state=state)

    rebuild_all()

    assert _document_results(search(query="allkiri.asice", user=specialist), version.document)


def test_a_historical_version_filename_still_finds_the_document(
    unextracted, specialist, capture_evidence
):
    from app.documents.services import add_evidence_version

    add_evidence_version(
        document=unextracted,
        content=b"Uuem versioon.",
        original_filename="zqxuuem-versioon.txt",
        mime_type=TXT,
        uploaded_by=specialist,
    )

    for term in ("zqxfilename-2026.txt", "zqxuuem-versioon.txt"):
        assert _document_results(search(query=term, user=specialist), unextracted), term


def test_a_rename_is_findable_at_once_and_the_old_name_is_not(unextracted, specialist):
    unextracted.title = "Ümbernimetatud ZQXNEWNAME kiri"
    unextracted.save(update_fields=["title", "updated_at"])

    assert _document_results(search(query="ZQXNEWNAME", user=specialist), unextracted)
    assert not _document_results(search(query="ZQXDOCTITLE", user=specialist), unextracted)


def test_a_version_stored_without_becoming_current_adds_its_filename(unextracted, specialist):
    from app.documents.services import add_evidence_version

    add_evidence_version(
        document=unextracted,
        content=b"Kolmas.",
        original_filename="zqxkorvalversioon.txt",
        mime_type=TXT,
        uploaded_by=specialist,
        make_current=False,
    )

    assert _document_results(search(query="zqxkorvalversioon", user=specialist), unextracted)


def test_the_row_carries_no_storage_key_or_identifier(unextracted):
    row = SearchDocument.objects.get(
        source_kind=SearchSourceKind.DOCUMENT, source_object_id=unextracted.pk
    )
    version = unextracted.current_version
    for column in (row.title, row.identifiers, row.alias_text, row.body_text):
        assert version.storage_key not in column
        assert str(unextracted.pk) not in column
        assert str(version.pk) not in column


def test_rebuild_reconstructs_the_row_and_integrity_expects_it(unextracted):
    before = SearchDocument.objects.get(
        source_kind=SearchSourceKind.DOCUMENT, source_object_id=unextracted.pk
    )

    result = rebuild_all()

    after = SearchDocument.objects.get(
        source_kind=SearchSourceKind.DOCUMENT, source_object_id=unextracted.pk
    )
    assert (after.title, after.identifiers) == (before.title, before.identifiers)
    assert after.index_version == INDEX_VERSION
    assert result.document_rows >= 1
    report = build_report()
    assert report.ok, report.findings
    counted = {label: (expected, actual) for label, expected, actual in report.counts}
    assert counted["Dokumendid"][0] == counted["Dokumendid"][1] >= 1


def test_a_missing_document_row_is_reported(unextracted):
    SearchDocument.objects.filter(source_kind=SearchSourceKind.DOCUMENT).delete()

    report = build_report()

    assert any(f.label == "Dokumendid" for f in report.findings)


def test_the_result_opens_the_document_page(unextracted, client, specialist):
    client.force_login(specialist)

    page = client.get(reverse("search:search"), {"q": "ZQXDOCTITLE"})

    assert page.status_code == 200
    assert (
        reverse("documents:document_detail", kwargs={"pk": unextracted.pk}) in page.content.decode()
    )


# -- authorization ------------------------------------------------------------


def _restricted_document(matter, capture_evidence, *, override=""):
    version = capture_evidence(
        matter, b"Salajane.", "zqxsalajane-fail.txt", TXT, title="ZQXSECRET dokument"
    )
    document = version.document
    if override:
        document.visibility_override = override
        document.save(update_fields=["visibility_override", "updated_at"])
    return document


def test_a_normal_document_on_a_normal_matter_is_found_by_any_reader(
    specialist, other_specialist, reader, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    document = _restricted_document(matter, capture_evidence)

    for person in (specialist, other_specialist, reader):
        assert _document_results(search(query="ZQXSECRET", user=person), document), person


def test_a_restricted_document_on_a_normal_matter_is_hidden_with_its_filename(
    specialist, reader, administrator, capture_evidence
):
    """Outsiders are the reader and the administrator: since docs/adr/0042 every
    specialist is authorized to restricted content."""
    matter = factories.MatterFactory(owner=specialist)
    document = _restricted_document(matter, capture_evidence, override=Visibility.RESTRICTED)

    for outsider in (reader, administrator):
        for term in ("ZQXSECRET", "zqxsalajane-fail.txt", "zqxsalajane"):
            assert not _document_results(search(query=term, user=outsider), document), term
    assert _document_results(search(query="ZQXSECRET", user=specialist), document)


def test_a_normal_document_on_a_restricted_matter_is_hidden(
    specialist, reader, administrator, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    document = _restricted_document(matter, capture_evidence)

    for outsider in (reader, administrator):
        for term in ("ZQXSECRET", "zqxsalajane-fail.txt"):
            assert not search(query=term, user=outsider), term
    assert _document_results(search(query="ZQXSECRET", user=specialist), document)


def test_restricting_a_document_hides_it_on_the_next_query(specialist, reader, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    document = _restricted_document(matter, capture_evidence)
    assert _document_results(search(query="ZQXSECRET", user=reader), document)

    type(document).objects.filter(pk=document.pk).update(visibility_override=Visibility.RESTRICTED)

    assert not _document_results(search(query="ZQXSECRET", user=reader), document)


def test_the_search_page_does_not_print_a_hidden_filename(
    specialist, reader, capture_evidence, client
):
    matter = factories.MatterFactory(owner=specialist)
    _restricted_document(matter, capture_evidence, override=Visibility.RESTRICTED)
    client.force_login(reader)

    page = client.get(reverse("search:search"), {"q": "zqxsalajane"}).content.decode()

    # The query itself is echoed back; the document's name and file are not.
    assert "zqxsalajane-fail.txt" not in page
    assert "ZQXSECRET" not in page


# ---------------------------------------------------------------------------
# ENG-082 — plain text is plain text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>AS Näide &amp; Partnerid</p>", "AS Näide & Partnerid"),
        ("<p>Tere</p><p>kolleeg</p>", "Tere kolleeg"),
        ("<ul><li>Esimene</li><li>Teine</li></ul>", "Esimene Teine"),
        ("Rida<br>teine<br/>kolmas", "Rida teine kolmas"),
        ("Tere,&nbsp;kolleeg", "Tere, kolleeg"),
        ("AS A &amp; B &lt;info&gt;", "AS A & B <info>"),
        ("<strong>Tere</strong>kolleeg", "Terekolleeg"),
        ("<script>alert(1)</script>Tekst", "Tekst"),
        ("&lt;script&gt;alert(1)&lt;/script&gt;", "<script>alert(1)</script>"),
        ("&amp;amp;", "&amp;"),
        ("AS Näide & Partnerid", "AS Näide & Partnerid"),
    ],
)
def test_plain_text_decodes_once_and_keeps_words_apart(html, text):
    assert plain_text(html) == text


def test_html_mail_text_is_plain_too():
    body = (
        "<html><style>p{color:red}</style><p>Tere,&nbsp;kolleeg</p>"
        "<p>AS A &amp; B &lt;info&gt;</p><script>x()</script></html>"
    )
    assert html_body_to_text(body) == "Tere, kolleeg AS A & B <info>"


def _development(matter, author, note: str):
    from app.matters.models import MatterProceduralDevelopment

    return MatterProceduralDevelopment.objects.create(
        matter=matter, created_by=author, title="Märge", note=note
    )


def test_a_snippet_shows_an_ampersand_once_and_a_tag_as_text(specialist, client):
    matter = factories.MatterFactory(owner=specialist)
    _development(matter, specialist, "Kohtusime: AS Zqxnäide & Partnerid <b>ütles</b> &lt;ei&gt;")
    client.force_login(specialist)

    page = client.get(reverse("search:search"), {"q": "Zqxnäide"}).content.decode()

    assert "&amp;amp;" not in page
    assert "Partnerid" in page
    assert "<b>ütles</b>" not in page  # never markup, whatever the note held


def test_the_indexed_body_holds_no_entities_and_no_fused_words(specialist):
    from app.matters.models import Entry

    matter = factories.MatterFactory(owner=specialist)
    entry = factories.EntryFactory(
        matter=matter, author=specialist, body="<p>Zqxesimene</p><p>Zqxteine &amp; kolmas</p>"
    )
    row = SearchDocument.objects.get(source_kind=SearchSourceKind.ENTRY, source_object_id=entry.pk)
    assert row.body_text == "Zqxesimene Zqxteine & kolmas"
    assert Entry.objects.filter(pk=entry.pk).exists()
    assert search(query="Zqxteine", user=specialist)


# ---------------------------------------------------------------------------
# ENG-084 — the projection is bounded; the record is not
# ---------------------------------------------------------------------------


def _unique_words(characters: int) -> str:
    rng = random.Random(84)  # noqa: S311 - test text, not a secret
    words: list[str] = []
    length = 0
    while length < characters:
        word = "".join(rng.choice("abcdefghijklmnoprstuvõäöü") for _ in range(9))
        words.append(word)
        length += len(word) + 1
    return " ".join(words)[:characters]


@pytest.mark.parametrize("characters", [2_000, 200_000, 1_000_000])
def test_a_long_note_saves_whole_and_is_indexed_bounded(characters, specialist):
    matter = factories.MatterFactory(owner=specialist)
    note = "Zqxalgus " + _unique_words(characters)

    development = _development(matter, specialist, note)

    development.refresh_from_db()
    assert development.note == note  # the record keeps every character
    row = SearchDocument.objects.get(
        source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT, source_object_id=development.pk
    )
    assert len(row.body_text) == min(len(note), MAX_INDEXED_FRAGMENT_CHARACTERS)
    assert row.search_estonian is not None
    assert search(query="Zqxalgus", user=specialist)  # the leading words stay findable


def test_a_huge_summary_through_the_real_route_saves(specialist, client):
    from app.matters.services import matter_field_revision

    matter = factories.MatterFactory(owner=specialist)
    summary = "Zqxkokkuvote " + _unique_words(1_000_000)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:update_summary", kwargs={"pk": matter.pk}),
        {"brief_summary": summary, "revision": matter_field_revision(matter, "brief_summary")},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.brief_summary == summary
    row = SearchDocument.objects.get(
        source_kind=SearchSourceKind.MATTER, source_object_id=matter.pk
    )
    assert len(row.body_text) == MAX_INDEXED_FRAGMENT_CHARACTERS
    assert search(query="Zqxkokkuvote", user=specialist)


def test_every_authored_child_body_is_bounded(specialist):
    """Entry, Kaasamine and a sent opinion's summary and notes (Märge above)."""
    from app.matters.models import MatterEngagement

    matter = factories.MatterFactory(owner=specialist)
    huge = _unique_words(1_000_000)

    entry = factories.EntryFactory(matter=matter, author=specialist, body=f"<p>{huge}</p>")
    engagement = MatterEngagement.objects.create(
        matter=matter, kind="SURVEY", title="Küsitlus", note=huge
    )
    submission = factories.SubmissionFactory(matter=matter, summary=huge, notes=huge)

    for kind, source in (
        (SearchSourceKind.ENTRY, entry),
        (SearchSourceKind.ENGAGEMENT, engagement),
        (SearchSourceKind.SUBMISSION, submission),
    ):
        row = SearchDocument.objects.get(source_kind=kind, source_object_id=source.pk)
        assert len(row.body_text) <= MAX_INDEXED_FRAGMENT_CHARACTERS, kind
        assert row.search_estonian is not None, kind


def test_short_text_is_indexed_whole(specialist):
    matter = factories.MatterFactory(owner=specialist)
    development = _development(matter, specialist, "Lühike märge ministeeriumi kohta.")
    row = SearchDocument.objects.get(
        source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT, source_object_id=development.pk
    )
    assert row.body_text == "Lühike märge ministeeriumi kohta."
    assert corpus  # the corpus module stays importable for the fixtures above
