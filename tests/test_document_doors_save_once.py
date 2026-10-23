"""`Dokumendid` add forms save a drawn form at most once (bug-bounty C-04).

`↑ Lae dokument` (`documents:upload_evidence`) and `SharePointi viide`
(`matters:add_working_document`) created unconditionally, so a replayed,
retried or double-sent POST added a second Document. Each drawn form now
carries its own `salvestus` token, spent inside the save's transaction through
`MatterSaveOnce` — the mechanism every compact door uses (docs/adr/0135,
docs/adr/0167 amendment of 2026-10-17). A replay adds nothing and lands on the
same page with a sentence that says the first save stands, not with an error.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import DOCUMENT_FORM_ALREADY_SAVED
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _documents_page(client, matter) -> str:
    return client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()


def _drawn_token(body: str, action: str) -> str:
    form = body[body.index(f'action="{action}"') :]
    form = form[: form.index("</form>")]
    match = re.search(r'name="salvestus" value="([0-9a-f-]{36})"', form)
    assert match, f"the form posting to {action} carries its one-time token"
    return match.group(1)


def _messages(client, response) -> list[str]:
    return [str(m) for m in client.get(response["Location"]).context["messages"]]


def _upload(client, matter, token: str):
    return client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {
            "role": DocumentRole.INCOMING_AUTHORITY,
            "title": "Ministeeriumi kiri",
            "upload": SimpleUploadedFile("kiri.pdf", PDF, content_type="application/pdf"),
            "salvestus": token,
        },
    )


def _link(client, matter, token: str):
    return client.post(
        reverse("matters:add_working_document", kwargs={"pk": matter.pk}),
        {
            "title": "Arvamuse mustand",
            "web_url": "https://koda.sharepoint.com/sites/juristid/mustand.docx",
            "salvestus": token,
        },
    )


def test_the_same_upload_form_posted_twice_adds_one_document(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk})
    token = _drawn_token(_documents_page(signed_in, matter), action)

    first = _upload(signed_in, matter, token)
    second = _upload(signed_in, matter, token)

    assert Document.objects.filter(matter=matter).count() == 1
    assert DocumentVersion.objects.filter(document__matter=matter).count() == 1
    assert second.status_code == 302
    assert second["Location"] == first["Location"]
    shown = _messages(signed_in, second)
    assert DOCUMENT_FORM_ALREADY_SAVED in shown


def test_the_same_working_document_form_posted_twice_adds_one_reference(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    action = reverse("matters:add_working_document", kwargs={"pk": matter.pk})
    token = _drawn_token(_documents_page(signed_in, matter), action)

    first = _link(signed_in, matter, token)
    second = _link(signed_in, matter, token)

    assert Document.objects.filter(matter=matter, role=DocumentRole.WORKING_DOCUMENT).count() == 1
    assert second.status_code == 302
    assert second["Location"] == first["Location"]
    assert DOCUMENT_FORM_ALREADY_SAVED in _messages(signed_in, second)


def test_a_refused_upload_spends_nothing(signed_in, specialist):
    """A rejected file rolls the token back: the corrected save still saves."""
    matter = factories.MatterFactory(owner=specialist)
    action = reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk})
    token = _drawn_token(_documents_page(signed_in, matter), action)

    signed_in.post(
        action,
        {
            "role": DocumentRole.INCOMING_AUTHORITY,
            "upload": SimpleUploadedFile("virus.exe", b"MZ"),
            "salvestus": token,
        },
    )
    assert Document.objects.filter(matter=matter).count() == 0

    _upload(signed_in, matter, token)
    assert Document.objects.filter(matter=matter).count() == 1


def test_two_drawn_forms_add_two_documents(signed_in, specialist):
    """Per form, not per content: the page re-draws each form with a fresh token."""
    matter = factories.MatterFactory(owner=specialist)
    upload_action = reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk})
    link_action = reverse("matters:add_working_document", kwargs={"pk": matter.pk})
    for _ in range(2):
        body = _documents_page(signed_in, matter)
        _upload(signed_in, matter, _drawn_token(body, upload_action))
        _link(signed_in, matter, _drawn_token(body, link_action))

    assert (
        Document.objects.filter(matter=matter).exclude(role=DocumentRole.WORKING_DOCUMENT).count()
        == 2
    )
    assert Document.objects.filter(matter=matter, role=DocumentRole.WORKING_DOCUMENT).count() == 2
