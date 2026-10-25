"""Clicking a filename does the useful thing — decided once (QoL round 2026-10-25).

`app.documents.inline.file_link` is the one place that chooses between opening
a stored version in a new tab (`documents:open`) and saving it
(`documents:download`). These tests hold the decision itself, every surface
that now links through it — `Teema käik`, the document's own page, Dokumendid,
search — and that none of it widened what renders inline or who may have it.
"""

from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.inline import file_link, file_link_for
from app.documents.services import add_evidence_version, create_document
from app.matters.timeline import ChronologyFile
from tests import factories
from tests import synthetic_corpus as corpus

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ASICE = "application/vnd.etsi.asic-e+zip"

NEW_TAB = 'target="_blank" rel="noopener noreferrer"'


def _version(filename: str, mime_type: str) -> SimpleNamespace:
    return SimpleNamespace(pk=uuid.uuid4(), original_filename=filename, mime_type=mime_type)


# -- the decision -----------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [
        ("kaaskiri.pdf", PDF),
        ("pilt.png", "image/png"),
        ("foto.jpg", "image/jpeg"),
        ("foto.JPEG", "image/jpeg"),
        ("markmed.txt", "text/plain; charset=utf-8"),
    ],
)
def test_a_previewable_version_opens_in_the_browser(filename, mime_type):
    version = _version(filename, mime_type)

    link = file_link(version)

    assert link.opens_inline is True
    assert link.href == link.open_url == reverse("documents:open", kwargs={"pk": version.pk})
    assert link.download_url == reverse("documents:download", kwargs={"pk": version.pk})


@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [
        ("eelnou.docx", DOCX),
        ("tabel.xlsx", XLSX),
        ("seisukoht.asice", ASICE),
        ("leht.html", "text/html"),
        ("joonis.svg", "image/svg+xml"),
        # The extension and the stored MIME type disagree: a single claim
        # never opens the door (app/documents/inline.py).
        ("evil.html", PDF),
        ("report.pdf", "text/html"),
        ("pilt.png", "image/jpeg"),
        ("ilma-laiendita", PDF),
    ],
)
def test_everything_else_downloads(filename, mime_type):
    version = _version(filename, mime_type)

    link = file_link(version)

    assert link.opens_inline is False
    assert link.open_url == ""
    assert (
        link.href == link.download_url == reverse("documents:download", kwargs={"pk": version.pk})
    )


def test_the_values_alone_decide_the_same_way():
    version_id = uuid.uuid4()
    assert file_link_for(version_id=version_id, filename="a.pdf", mime_type=PDF).opens_inline
    assert not file_link_for(version_id=version_id, filename="a.docx", mime_type=DOCX).opens_inline


def test_a_timeline_file_is_built_through_the_same_decision():
    """Every row that attaches files — a closure's included — inherits it."""
    pdf = ChronologyFile.of_version(_version("kiri.pdf", PDF), detail="12 KB")
    docx = ChronologyFile.of_version(_version("eelnou.docx", DOCX), kind="toodokument")

    assert pdf.opens_inline and "/ava/" in pdf.url and pdf.detail == "12 KB"
    assert pdf.download_url != pdf.url
    assert not docx.opens_inline and docx.url == docx.download_url and docx.kind == "toodokument"


# -- rendered surfaces ------------------------------------------------------


class _Anchors(HTMLParser):
    """Every `<a>` with its attributes and text, and whether any nests in another."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict] = []
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            if self.depth:
                self.nested = True
            self.depth += 1
            self.anchors.append({**dict(attrs), "text": ""})

    def handle_endtag(self, tag):
        if tag == "a" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth and self.anchors:
            self.anchors[-1]["text"] += data


def _anchors(html: str) -> _Anchors:
    parser = _Anchors()
    parser.feed(html)
    return parser


def _upload(client, matter, filename: str, mime_type: str, body: bytes, title: str = ""):
    response = client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {
            "upload": SimpleUploadedFile(filename, body, content_type=mime_type),
            "role": DocumentRole.INCOMING_AUTHORITY.value,
            "title": title,
        },
    )
    assert response.status_code == 302
    from app.documents.models import DocumentVersion

    return DocumentVersion.objects.get(document__matter=matter, original_filename=filename)


@pytest.fixture
def two_files(signed_in, normal_matter, evidence_root):
    pdf = _upload(signed_in, normal_matter, "kaaskiri.pdf", PDF, corpus.government_pdf())
    docx = _upload(signed_in, normal_matter, "eelnou.docx", DOCX, corpus.draft_docx())
    return pdf, docx


@pytest.mark.django_db
def test_the_chronology_opens_a_pdf_in_a_new_tab_and_downloads_a_word_file(
    signed_in, normal_matter, two_files
):
    pdf, docx = two_files
    body = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    files = [a for a in _anchors(body.content.decode()).anchors if a.get("class") == "uxtl__file"]
    by_name = {a["text"].strip(): a for a in files}

    assert by_name["kaaskiri.pdf"]["href"] == reverse("documents:open", kwargs={"pk": pdf.pk})
    assert by_name["kaaskiri.pdf"]["target"] == "_blank"
    assert by_name["kaaskiri.pdf"]["rel"] == "noopener noreferrer"
    assert by_name["eelnou.docx"]["href"] == reverse("documents:download", kwargs={"pk": docx.pk})
    assert "target" not in by_name["eelnou.docx"]


@pytest.mark.django_db
def test_the_document_page_names_open_download_and_copy_link(signed_in, two_files):
    pdf, docx = two_files

    page = signed_in.get(reverse("documents:document_detail", kwargs={"pk": pdf.document_id}))
    html = page.content.decode()
    open_url = reverse("documents:open", kwargs={"pk": pdf.pk})
    download_url = reverse("documents:download", kwargs={"pk": pdf.pk})
    actions = html[html.index('aria-label="Faili toimingud"') :]
    actions = actions[: actions.index("</div>")]
    assert f'href="{open_url}"' in actions and NEW_TAB in actions and ">Ava</a>" in actions
    assert f'href="{download_url}">Laadi alla</a>' in actions
    assert f'data-copy-href="{open_url}"' in actions and ">Kopeeri link</button>" in actions
    heading = html[html.index('class="pagehead__title"') :]
    assert heading.index(open_url) < heading.index("</h1>")

    page = signed_in.get(reverse("documents:document_detail", kwargs={"pk": docx.document_id}))
    html = page.content.decode()
    download_url = reverse("documents:download", kwargs={"pk": docx.pk})
    actions = html[html.index('aria-label="Faili toimingud"') :]
    actions = actions[: actions.index("</div>")]
    assert ">Ava</a>" not in actions and "target=" not in actions
    assert f'href="{download_url}">Laadi alla</a>' in actions
    assert f'data-copy-href="{download_url}"' in actions
    assert reverse("documents:open", kwargs={"pk": docx.pk}) not in html


@pytest.mark.django_db
def test_the_version_list_links_each_version_by_the_same_rule(signed_in, two_files):
    pdf, _ = two_files
    older = pdf
    newer = add_evidence_version(
        document=pdf.document,
        content=b"uus tekst",
        original_filename="kaaskiri-v2.txt",
        mime_type="text/plain",
    )

    html = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": pdf.document_id})
    ).content.decode()
    table = html[html.index('id="versioonid-heading"') :]
    rows = {a["text"].strip(): a for a in _anchors(table).anchors}

    for version in (older, newer):
        assert rows[version.original_filename]["href"] == reverse(
            "documents:open", kwargs={"pk": version.pk}
        )
        assert rows[version.original_filename]["target"] == "_blank"
    assert html.count("Laadi alla kaaskiri") == 2


@pytest.mark.django_db
def test_dokumendid_opens_previewable_rows_in_a_new_tab_and_offers_copy_link(
    signed_in, normal_matter, two_files
):
    pdf, docx = two_files
    html = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    anchors = _anchors(html).anchors
    ava = {a["aria-label"]: a for a in anchors if a.get("text", "").strip() == "Ava"}

    assert ava["Ava kaaskiri.pdf"]["href"] == reverse("documents:open", kwargs={"pk": pdf.pk})
    assert ava["Ava kaaskiri.pdf"]["target"] == "_blank"
    assert ava["Ava eelnou.docx"]["href"] == reverse("documents:download", kwargs={"pk": docx.pk})
    assert "target" not in ava["Ava eelnou.docx"]
    for version in (pdf, docx):
        assert re.search(r'data-copy-href="' + re.escape(file_link(version).href) + r'"', html), (
            version.original_filename
        )


@pytest.mark.django_db
def test_the_dokumendid_upload_takes_one_file_dropped_or_picked(signed_in, normal_matter):
    html = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    panel = html[html.index('id="lae-dokument"') :]
    panel = panel[: panel.index("</form>")]

    boxes = re.findall(r'<input type="file"[^>]*>', panel)
    assert len(boxes) == 1
    assert 'name="upload"' in boxes[0] and "multiple" not in boxes[0]
    assert "data-filedrop" in panel and "data-filedrop-text" in panel
    # Its own title and role stay: one file, one document.
    assert 'name="title"' in panel and 'name="role"' in panel
    # The shared queue is held hidden — no second title box beside `Pealkiri`.
    assert "data-upload-queue-hidden" in panel


@pytest.mark.django_db
def test_two_files_posted_at_once_still_make_one_document(signed_in, normal_matter, evidence_root):
    from app.documents.models import Document

    before = Document.objects.filter(matter=normal_matter).count()
    signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": normal_matter.pk}),
        {
            "upload": [
                SimpleUploadedFile("yks.pdf", corpus.government_pdf(), content_type=PDF),
                SimpleUploadedFile("kaks.pdf", corpus.government_pdf(), content_type=PDF),
            ],
            "role": DocumentRole.INCOMING_AUTHORITY.value,
        },
    )
    assert Document.objects.filter(matter=normal_matter).count() == before + 1


# -- authorization is unchanged ---------------------------------------------


@pytest.mark.django_db
def test_the_links_answer_exactly_as_the_routes_always_did(client, specialist, evidence_root):
    """A link built here is the authorized route, not a way round it.

    An outsider gets the 404 both routes always gave; a READER reads, as
    docs/adr/0181 decided — nothing about who may have the bytes moved."""
    hidden = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    document = create_document(matter=hidden, title="kiri", role=DocumentRole.INCOMING_AUTHORITY)
    pdf = add_evidence_version(
        document=document,
        content=corpus.government_pdf(),
        original_filename="kiri.pdf",
        mime_type=PDF,
    )
    link = file_link(pdf)

    client.force_login(factories.AdministratorFactory())
    for url in (link.href, link.download_url):
        assert client.get(url).status_code == 404
    client.logout()
    for url in (link.href, link.download_url):
        assert client.get(url).status_code == 302  # to sign-in

    client.force_login(factories.ReaderFactory())
    assert client.get(link.href).status_code == 200


# -- search -----------------------------------------------------------------


def _search(client, q):
    return client.get(reverse("search:search"), {"q": q})


@pytest.mark.django_db
def test_a_document_result_offers_its_file_beside_the_row_not_inside_it(
    signed_in, normal_matter, evidence_root
):
    pdf = _upload(
        signed_in, normal_matter, "otsing.pdf", PDF, corpus.government_pdf(), "Sarapuuotsing kiri"
    )
    docx = _upload(
        signed_in, normal_matter, "otsing.docx", DOCX, corpus.draft_docx(), "Sarapuuotsing eelnou"
    )

    html = _search(signed_in, "Sarapuuotsing").content.decode()
    parsed = _anchors(html)

    assert not parsed.nested, "a link inside a result link"
    files = {a["aria-label"]: a for a in parsed.anchors if a.get("class") == "resultrow__file"}
    assert files["Ava fail otsing.pdf"]["href"] == reverse("documents:open", kwargs={"pk": pdf.pk})
    assert files["Ava fail otsing.pdf"]["target"] == "_blank"
    assert files["Laadi alla otsing.docx"]["href"] == reverse(
        "documents:download", kwargs={"pk": docx.pk}
    )
    # The row itself still opens the document's own page.
    results = [a["href"] for a in parsed.anchors if a.get("class") == "result"]
    assert reverse("documents:document_detail", kwargs={"pk": pdf.document_id}) in results


@pytest.mark.django_db
def test_a_result_that_is_not_a_file_offers_no_file(signed_in, specialist):
    factories.MatterFactory(owner=specialist, title="Kadakaotsing teema")
    html = _search(signed_in, "Kadakaotsing").content.decode()
    assert 'class="result"' in html
    assert "resultrow__file" not in html


@pytest.mark.django_db
def test_the_file_actions_cost_the_results_page_no_query(signed_in, normal_matter, evidence_root):
    def count() -> int:
        with CaptureQueriesContext(connection) as captured:
            assert _search(signed_in, "Pihlakaotsing").status_code == 200
        return len(captured)

    _upload(signed_in, normal_matter, "p1.pdf", PDF, corpus.government_pdf(), "Pihlakaotsing 1")
    one = count()
    for number in range(2, 5):
        _upload(
            signed_in,
            normal_matter,
            f"p{number}.pdf",
            PDF,
            corpus.government_pdf(),
            f"Pihlakaotsing {number}",
        )
    four = count()

    assert four == one
    assert _search(signed_in, "Pihlakaotsing").content.decode().count("resultrow__file") == 4
