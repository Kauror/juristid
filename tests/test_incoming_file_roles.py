"""An incoming file is classified once, whichever way it arrived (ENG-066).

`app.matters.intake.role_for` is the one rule: an `.eml` or `.msg` is the
original e-mail, anything else is the incoming authority document. Saabunud and
the staging area always asked it. `Uus teema`'s own attachment step did not —
it wrote `INCOMING_AUTHORITY` for every file — so the same e-mail was «Algne
e-kiri» when it came through Saabunud or a staged upload and «Saabunud ametlik
dokument» when it came through the direct form post, which is what the form
does with scripting off, when staging fails, and after any refused save that
held the file.

Each live path is driven here through its real route with the same three
files, and each must answer the same three roles. Documents stored before this
fix keep the role they were given: nothing here reclassifies history.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.matters.models import Matter
from app.matters.staging import MatterIntakeFile, MatterIntakeSession

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")
SAABUNUD = reverse("matters:intake")

EML = (
    b"From: Mari Naidis <mari.naidis@naidisministeerium.invalid>\r\n"
    b"To: koda@naidiskoda.invalid\r\n"
    b"Subject: Eelnou kooskolastamiseks\r\n"
    b"\r\n"
    b"Saadame eelnou kooskolastamiseks.\r\n"
)
#: The OLE compound-file header is the whole of what `read_upload` checks.
MSG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1synthetic outlook message"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

#: The three files every path is given, and the one answer each must get.
EXPECTED = {
    "kiri.eml": DocumentRole.ORIGINAL_EMAIL,
    "kiri.msg": DocumentRole.ORIGINAL_EMAIL,
    "memo.pdf": DocumentRole.INCOMING_AUTHORITY,
}


def the_three_files() -> list[SimpleUploadedFile]:
    return [
        SimpleUploadedFile("kiri.eml", EML, content_type="message/rfc822"),
        SimpleUploadedFile("kiri.msg", MSG, content_type="application/vnd.ms-outlook"),
        SimpleUploadedFile("memo.pdf", PDF, content_type="application/pdf"),
    ]


def roles_of(matter: Matter) -> dict[str, str]:
    return {document.title: document.role for document in Document.objects.filter(matter=matter)}


def test_the_direct_uus_teema_post_classifies_like_every_other_path(signed_in):
    """The reported defect: no staging, the files in the form post itself."""
    response = signed_in.post(CREATE, {"title": "Otse lisatud kiri", "files": the_three_files()})

    assert response.status_code == 302, response.status_code
    assert roles_of(Matter.objects.get(title="Otse lisatud kiri")) == EXPECTED


def test_files_held_through_a_refused_save_are_classified_when_they_land(signed_in):
    """The resumed half of the same path: refused once, filed on the retry.

    The refusal is one only the server can make — «Muu» ticked with nothing
    written beside it — so the browser sends the files and gets a page back.
    """
    refused = signed_in.post(
        CREATE,
        {
            "title": "Hoitud kiri",
            "policy_area_other_selected": "on",
            "files": the_three_files(),
        },
    )
    assert refused.status_code == 400
    assert not Matter.objects.filter(title="Hoitud kiri").exists()
    keys = [item.key for item in refused.context["held_files"]]
    assert len(keys) == 3

    signed_in.post(
        CREATE,
        {
            "title": "Hoitud kiri",
            "policy_area_other_selected": "on",
            "policy_area_other": "Ehitus",
            "pending": keys,
        },
    )

    assert roles_of(Matter.objects.get(title="Hoitud kiri")) == EXPECTED


def test_saabunud_classifies_the_same_files_the_same_way(signed_in):
    response = signed_in.post(
        SAABUNUD,
        {"title": "Saabunud kiri", "visibility": Visibility.NORMAL, "uploads": the_three_files()},
    )

    assert response.status_code == 302, response.status_code
    assert roles_of(Matter.objects.get(title="Saabunud kiri")) == EXPECTED


def test_staging_and_its_promotion_classify_the_same_files_the_same_way(signed_in):
    """Staged on choosing, promoted on «Loo teema»: both halves agree."""
    staged = signed_in.post(STAGE, {"files": the_three_files()})
    assert staged.status_code == 200, staged.status_code
    session: MatterIntakeSession = staged.context["intake_session"]

    assert {
        row.original_filename: row.role for row in MatterIntakeFile.objects.filter(session=session)
    } == EXPECTED

    signed_in.post(CREATE, {"title": "Ettevalmistatud kiri", "intake": str(session.pk)})

    assert roles_of(Matter.objects.get(title="Ettevalmistatud kiri")) == EXPECTED


def test_promotion_asks_the_classifier_rather_than_a_fallback_of_its_own(signed_in):
    """The staged row's role is a copy kept for the analyser, not a second rule.

    Promotion used to read it back and file a blank one as
    `INCOMING_AUTHORITY` — a default of its own that an `.eml` never gets from
    `role_for`. A row whose copy is blank still becomes «Algne e-kiri».
    """
    staged = signed_in.post(STAGE, {"files": the_three_files()})
    session: MatterIntakeSession = staged.context["intake_session"]
    MatterIntakeFile.objects.filter(session=session).update(role="")

    signed_in.post(CREATE, {"title": "Tühja koopiaga kiri", "intake": str(session.pk)})

    assert roles_of(Matter.objects.get(title="Tühja koopiaga kiri")) == EXPECTED


def test_a_document_already_stored_keeps_the_role_it_was_given(signed_in, specialist):
    """No reclassification of history: the fix decides new files only."""
    from tests import factories

    matter = factories.MatterFactory(owner=specialist)
    stored = factories.DocumentFactory(
        matter=matter, title="vana.eml", role=DocumentRole.INCOMING_AUTHORITY
    )

    signed_in.post(CREATE, {"title": "Uus kiri kõrval", "files": the_three_files()})

    stored.refresh_from_db()
    assert stored.role == DocumentRole.INCOMING_AUTHORITY
