"""Dokumendid round, 2026-10-26 — a file-first list and a document page that opens on the file.

The owner's review: the list showed one destination twice («Vaata sisu» beside
the title), the upload button said «Lae dokument», and the document page opened
on four correction forms while the only obvious way to the file was its own
title. These pin what replaced that — and that nothing about the evidence, its
versions or who may reach it moved with the presentation.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.documents.enums import DocumentRole, ExtractionState
from app.documents.models import DocumentVersion
from app.documents.services import add_evidence_version
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LONG = "Valitsuse_kommenteeritud_ettepanek_" + "pikk_failinimi_" * 8 + "lõpp.pdf"


def _upload(client, matter, filename, mime_type, body, title=""):
    response = client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {
            "upload": SimpleUploadedFile(filename, body, content_type=mime_type),
            "role": DocumentRole.INCOMING_AUTHORITY.value,
            "title": title,
        },
    )
    assert response.status_code == 302
    return DocumentVersion.objects.get(document__matter=matter, original_filename=filename)


def _detail(client, document_id, **params):
    return client.get(
        reverse("documents:document_detail", kwargs={"pk": document_id}), params
    ).content.decode()


def _list(client, matter):
    return client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()


def _row(html, document_id):
    start = html.index(f'id="dokument-{document_id}"')
    return html[start : html.index("</tr>", start)]


def _between(html, start, end):
    i = html.index(start)
    return html[i : html.index(end, i)]


@pytest.fixture
def pdf(signed_in, normal_matter, evidence_root):
    return _upload(signed_in, normal_matter, "kaaskiri.pdf", PDF, corpus.government_pdf(), "Kiri")


@pytest.fixture
def docx(signed_in, normal_matter, evidence_root):
    return _upload(signed_in, normal_matter, "eelnou.docx", DOCX, corpus.draft_docx())


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def test_the_list_has_no_vaata_sisu_and_the_title_opens_the_document_page(
    signed_in, normal_matter, pdf
):
    DocumentVersion.objects.filter(pk=pdf.pk).update(extraction_state=ExtractionState.DONE)

    row = _row(_list(signed_in, normal_matter), pdf.document_id)

    assert "Vaata sisu" not in row
    title = _between(row, 'class="table__title"', "</td>")
    assert title.count("<a ") == 1
    assert reverse("documents:document_detail", kwargs={"pk": pdf.document_id}) in title


def test_the_upload_button_says_lae_ules(signed_in, normal_matter, pdf):
    html = _list(signed_in, normal_matter)

    assert "↑ Lae üles" in html
    assert "Lae dokument" not in html


def test_the_empty_list_offers_lae_ules_too(signed_in, normal_matter):
    html = _list(signed_in, normal_matter)

    assert html.count("↑ Lae üles") == 2  # the toolbar and the empty card
    assert "Lae dokument" not in html


def test_ava_download_and_the_menu_sit_together_in_the_rows_last_cell(
    signed_in, normal_matter, pdf
):
    row = _row(_list(signed_in, normal_matter), pdf.document_id)
    cell = _between(row, 'class="doctable__actionscell"', "</td>")

    assert ">Ava</a>" in cell
    assert 'aria-label="Tõmba alla Kiri"' in cell and 'title="Tõmba alla"' in cell
    assert 'aria-label="Muuda Kiri"' in cell
    # Each once: nothing duplicated elsewhere in the row.
    assert row.count(">Ava</a>") == 1
    assert row.count('title="Tõmba alla"') == 1
    # The uploader keeps its own column.
    assert 'class="doctable__by"' in row


def test_a_reader_gets_the_file_actions_and_no_management_menu(client, reader, normal_matter, pdf):
    client.force_login(reader)
    row = _row(_list(client, normal_matter), pdf.document_id)
    cell = _between(row, 'class="doctable__actionscell"', "</td>")

    assert ">Ava</a>" in cell and 'title="Tõmba alla"' in cell
    assert "Muuda Kiri" not in cell
    assert "Lae üles" not in _list(client, normal_matter)


# ---------------------------------------------------------------------------
# The document page
# ---------------------------------------------------------------------------


def test_a_pdf_page_opens_on_its_identity_and_ava_fail(signed_in, pdf):
    html = _detail(signed_in, pdf.document_id)
    open_url = reverse("documents:open", kwargs={"pk": pdf.pk})
    download_url = reverse("documents:download", kwargs={"pk": pdf.pk})

    head = _between(html, 'class="dochead"', "</header>")
    assert '<h1 class="dochead__title">Kiri</h1>' in head
    meta = _between(head, 'class="dochead__meta"', "</p>")
    for fact in ("Saabunud", "PDF", "Versioon 1"):
        assert fact in meta, fact
    assert "kaaskiri.pdf" in _between(head, 'class="dochead__filename"', "</p>")
    actions = _between(head, 'aria-label="Faili toimingud"', "</div>")
    assert f'class="button button--primary" href="{open_url}"' in actions
    assert "Ava fail" in actions
    assert f'href="{download_url}">Laadi alla</a>' in actions
    assert f'data-copy-href="{open_url}"' in actions
    # The page's order: identity and the way to the file before any form.
    assert html.index("Faili toimingud") < html.index("Muuda dokumenti") < html.index("Originaal")


def test_a_word_file_is_downloaded_not_promised_an_opening(signed_in, docx):
    html = _detail(signed_in, docx.document_id)
    actions = _between(html, 'aria-label="Faili toimingud"', "</div>")
    download_url = reverse("documents:download", kwargs={"pk": docx.pk})

    assert "Ava fail" not in actions
    assert f'class="button button--primary" href="{download_url}">Laadi alla</a>' in actions
    assert actions.count("Laadi alla") == 1
    assert f'data-copy-href="{download_url}"' in actions
    assert "DOCX" in _between(html, 'class="dochead__meta"', "</p>")


def test_a_document_with_no_file_offers_no_broken_action(signed_in, normal_matter):
    document = factories.DocumentFactory(matter=normal_matter, title="Ainult kirje")

    html = _detail(signed_in, document.pk)

    assert "Faili toimingud" not in html
    assert "Ava fail" not in html and "Laadi alla" not in html
    assert "Sellel dokumendil ei ole veel faili, mida avada." in html
    assert "Originaal" not in html


def test_muuda_dokumenti_is_closed_and_keeps_its_four_forms(signed_in, pdf):
    html = _detail(signed_in, pdf.document_id)
    manage = _between(html, 'id="muuda-dokumenti"', '<section class="card card--evidence"')

    assert '<details class="card docmanage" id="muuda-dokumenti">' in html
    assert "Paranda dokumenti" not in html
    for route in ("add_version", "rename", "change_role", "remove"):
        assert reverse(f"documents:{route}", kwargs={"pk": pdf.document_id}) in manage, route
    assert manage.count("<form ") == 4


@pytest.mark.parametrize("form", ["versioon", "pealkiri", "liik"])
def test_a_link_naming_a_form_opens_the_disclosure(signed_in, pdf, form):
    html = _detail(signed_in, pdf.document_id, muuda=form)

    assert '<details class="card docmanage" id="muuda-dokumenti" open>' in html
    assert '<details class="disclosure" id="eemalda-dokument">' in html


def test_eemalda_opens_its_own_confirmation_too(signed_in, pdf):
    html = _detail(signed_in, pdf.document_id, muuda="eemalda")

    assert '<details class="card docmanage" id="muuda-dokumenti" open>' in html
    assert '<details class="disclosure" id="eemalda-dokument" open>' in html


def test_an_unknown_value_opens_nothing(signed_in, pdf):
    html = _detail(signed_in, pdf.document_id, muuda="<script>")

    assert '<details class="card docmanage" id="muuda-dokumenti">' in html
    assert "muuda=" not in html


def test_a_refused_rename_comes_back_with_its_form_open(signed_in, pdf):
    response = signed_in.post(
        reverse("documents:rename", kwargs={"pk": pdf.document_id}), {"title": "   "}
    )

    detail = reverse("documents:document_detail", kwargs={"pk": pdf.document_id})
    assert response.status_code == 302
    assert response["Location"] == f"{detail}?muuda=pealkiri#muuda-dokumenti"
    page = signed_in.get(response["Location"]).content.decode()
    assert 'id="muuda-dokumenti" open' in page
    assert 'class="messages"' in page


def test_a_refused_new_version_comes_back_with_its_form_open(signed_in, pdf):
    response = signed_in.post(reverse("documents:add_version", kwargs={"pk": pdf.document_id}), {})

    assert response["Location"].endswith("?muuda=versioon#muuda-dokumenti")


def test_a_rename_from_the_list_still_returns_to_the_list(signed_in, normal_matter, pdf):
    response = signed_in.post(
        reverse("documents:rename", kwargs={"pk": pdf.document_id}),
        {"title": "", "tagasi": "dokumendid"},
    )

    assert response["Location"] == reverse(
        "matters:matter_documents", kwargs={"pk": normal_matter.pk}
    )


def test_a_successful_correction_lands_on_the_closed_page(signed_in, pdf):
    response = signed_in.post(
        reverse("documents:rename", kwargs={"pk": pdf.document_id}), {"title": "Uus nimi"}
    )

    assert response["Location"] == reverse(
        "documents:document_detail", kwargs={"pk": pdf.document_id}
    )


def test_a_reader_sees_the_file_and_no_correction_area(client, reader, pdf):
    client.force_login(reader)
    html = _detail(client, pdf.document_id, muuda="pealkiri")

    assert "Faili toimingud" in html and "Ava fail" in html
    assert "Muuda dokumenti" not in html
    assert reverse("documents:rename", kwargs={"pk": pdf.document_id}) not in html


def test_a_closed_matter_offers_no_correction(signed_in, normal_matter, pdf):
    from django.utils import timezone

    from app.workflow.enums import Disposition

    type(normal_matter).objects.filter(pk=normal_matter.pk).update(
        is_open=False, disposition=Disposition.COMPLETED, closed_at=timezone.now()
    )

    html = _detail(signed_in, pdf.document_id)

    assert "Muuda dokumenti" not in html
    assert "Ava fail" in html


def test_every_version_keeps_its_own_open_and_download(signed_in, pdf):
    newer = add_evidence_version(
        document=pdf.document,
        content=b"uus tekst",
        original_filename="kaaskiri-v2.txt",
        mime_type="text/plain",
    )

    html = _detail(signed_in, pdf.document_id)
    data = _between(html, 'id="dokumendi-andmed"', "</details>")

    assert "<details" in html.split('id="dokumendi-andmed"')[0][-60:]
    for version in (pdf, newer):
        assert reverse("documents:open", kwargs={"pk": version.pk}) in data
        assert reverse("documents:download", kwargs={"pk": version.pk}) in data
    # The header copies the *current* version's address.
    actions = _between(html, 'aria-label="Faili toimingud"', "</div>")
    assert f'data-copy-href="{reverse("documents:open", kwargs={"pk": newer.pk})}"' in actions
    assert "Versioon 2 / 2" in _between(html, 'class="dochead__meta"', "</p>")


def test_the_technical_facts_are_still_on_the_page(signed_in, pdf):
    html = _detail(signed_in, pdf.document_id)
    data = _between(html, 'id="dokumendi-andmed"', "</details>")

    pdf.refresh_from_db()
    assert pdf.sha256 in data
    assert PDF in data
    assert f"{pdf.size_bytes} baiti" in data
    originaal = _between(html, 'id="originaal-heading"', 'id="dokumendi-andmed"')
    assert "Failinimi" in originaal and "kaaskiri.pdf" in originaal
    assert "Saadi" in originaal and "Töötlemine" in originaal


def test_a_long_filename_wraps_in_its_own_cell(signed_in, normal_matter, evidence_root):
    version = _upload(signed_in, normal_matter, LONG, PDF, corpus.government_pdf())

    html = _detail(signed_in, version.document_id)

    assert 'class="detailgrid detailgrid--wrap"' in html
    assert f"<dd>{LONG}</dd>" in html


def test_a_restricted_document_is_still_hidden_from_an_account_without_business_read(
    client, administrator, normal_matter, pdf
):
    type(pdf.document).objects.filter(pk=pdf.document_id).update(
        visibility_override=Visibility.RESTRICTED
    )
    client.force_login(administrator)

    response = client.get(reverse("documents:document_detail", kwargs={"pk": pdf.document_id}))

    assert response.status_code == 404


def test_reading_the_page_writes_nothing(signed_in, pdf):
    before = (ChangeEvent.objects.count(), DocumentVersion.objects.count())
    pdf.refresh_from_db()
    digest = pdf.sha256

    _detail(signed_in, pdf.document_id)
    _detail(signed_in, pdf.document_id, muuda="eemalda")

    pdf.refresh_from_db()
    assert (ChangeEvent.objects.count(), DocumentVersion.objects.count()) == before
    assert pdf.sha256 == digest
