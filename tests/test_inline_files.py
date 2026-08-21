"""Which stored files open in the browser, and which are only ever saved.

"Open it inline" means "render it in this application's origin", so the decision
is a security one wearing a usability hat. These tests pin the allow-list, the
agreement rule that stops a single wrong MIME value opening the door, and the
fact that a second route onto the same bytes is not a second answer about who
may have them.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.enums import DocumentRole, MalwareScanState
from app.documents.inline import may_open_inline
from app.documents.services import add_evidence_version, create_document
from tests import factories
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MSG = "application/vnd.ms-outlook"
ASICE = "application/vnd.etsi.asic-e+zip"


@pytest.fixture
def stored(evidence_root, normal_matter):
    """One stored evidence version, of whatever shape the case needs."""

    def store(content: bytes, filename: str, mime_type: str, matter=None):
        document = create_document(
            matter=matter or normal_matter,
            title=filename,
            role=DocumentRole.INCOMING_AUTHORITY,
        )
        return add_evidence_version(
            document=document,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            malware_scan_state=MalwareScanState.PENDING,
        )

    return store


# -- the rule ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "mime_type", "expected"),
    [
        ("kaaskiri.pdf", PDF, True),
        ("pilt.png", PNG, True),
        ("pilt.jpg", "image/jpeg", True),
        ("markmed.txt", "text/plain", True),
        ("eelnou.docx", DOCX, False),
        ("kiri.msg", MSG, False),
        ("seisukoht.asice", ASICE, False),
        ("leht.html", "text/html", False),
        ("joonis.svg", "image/svg+xml", False),
        ("pakk.zip", "application/zip", False),
    ],
)
def test_the_allow_list_decides(filename, mime_type, expected):
    assert may_open_inline(filename=filename, mime_type=mime_type) is expected


def test_the_extension_and_the_mime_type_must_agree():
    """A MIME type is a claim by whoever uploaded the file.

    `evil.html` announced as a PDF must not open inline, and neither must a real
    PDF announced as HTML. Requiring both to land on the *same* entry means one
    wrong value cannot open the door (Stage-2E.1 brief 12).
    """
    assert not may_open_inline(filename="evil.html", mime_type=PDF)
    assert not may_open_inline(filename="kaaskiri.pdf", mime_type="text/html")
    assert not may_open_inline(filename="kaaskiri.pdf", mime_type=DOCX)


def test_an_unrecognised_format_downloads():
    assert not may_open_inline(filename="midagi.xyz", mime_type="application/x-unknown")
    assert not may_open_inline(filename="ilma-laiendita", mime_type=PDF)


# -- the routes -------------------------------------------------------------


def test_a_pdf_opens_inline_with_the_headers_that_make_it_safe(signed_in, stored):
    version = stored(corpus.government_pdf(), "kaaskiri.pdf", PDF)

    response = signed_in.get(reverse("documents:open", kwargs={"pk": version.pk}))

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("inline;")
    assert response["Content-Type"] == PDF
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'none'" in response["Content-Security-Policy"]


def test_an_office_file_asked_to_open_is_redirected_to_the_download(signed_in, stored):
    """The reader asked to see it. "Here it is, saved instead" beats an error."""
    version = stored(corpus.draft_docx(), "eelnou.docx", DOCX)

    response = signed_in.get(reverse("documents:open", kwargs={"pk": version.pk}))

    assert response.status_code == 302
    assert response["Location"] == reverse("documents:download", kwargs={"pk": version.pk})


def test_an_inline_response_claims_our_mime_type_not_the_uploaded_one(signed_in, stored):
    version = stored(corpus.government_pdf(), "kaaskiri.pdf", PDF)
    response = signed_in.get(reverse("documents:open", kwargs={"pk": version.pk}))
    assert response["Content-Type"] == "application/pdf"


def test_the_storage_key_is_never_exposed(signed_in, stored):
    version = stored(corpus.government_pdf(), "kaaskiri.pdf", PDF)
    response = signed_in.get(reverse("documents:open", kwargs={"pk": version.pk}))
    assert version.storage_key not in response["Content-Disposition"]
    assert "X-Accel-Redirect" not in response
    assert "Location" not in response


# -- authorization ----------------------------------------------------------


def test_a_restricted_document_is_a_404_on_both_routes(client, stored, specialist):
    """The same answer from both, or the second route is a way round the first."""
    hidden = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    version = stored(corpus.government_pdf(), "kaaskiri.pdf", PDF, matter=hidden)

    client.force_login(factories.UserFactory())
    assert client.get(reverse("documents:open", kwargs={"pk": version.pk})).status_code == 404
    assert client.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404

    client.force_login(specialist)
    assert client.get(reverse("documents:open", kwargs={"pk": version.pk})).status_code == 200


def test_the_department_scope_reads_a_normal_file_but_not_a_restricted_one(
    client, stored, specialist, settings
):
    """Past the shared gate, before anybody picks a persona (brief 33)."""
    from app.accounts.enums import AuthMode

    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105
    settings.DEV_LOGIN_ENABLED = False

    open_version = stored(corpus.government_pdf(), "avalik.pdf", PDF)
    hidden = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    hidden_version = stored(corpus.government_pdf(), "piiratud.pdf", PDF, matter=hidden)

    gate = reverse("accounts:shared_gate")
    page = client.get(gate).content.decode()
    import re

    token = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', page).group(1)
    client.post(gate, {"password": settings.SHARED_GATE_PASSWORD, "csrfmiddlewaretoken": token})

    assert client.get(reverse("documents:open", kwargs={"pk": open_version.pk})).status_code == 200
    assert (
        client.get(reverse("documents:open", kwargs={"pk": hidden_version.pk})).status_code == 404
    )


def test_opening_a_file_is_audited_without_claiming_an_identity(signed_in, stored, specialist):
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    version = stored(corpus.government_pdf(), "kaaskiri.pdf", PDF)
    signed_in.get(reverse("documents:open", kwargs={"pk": version.pk}))

    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).latest("occurred_at")
    assert event.detail["disposition"] == "inline"
    assert event.detail["authenticated_via"] == "NONE"
    assert event.detail["sha256"] == version.sha256
