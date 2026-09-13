"""A working-document address is stored whole or refused, never shortened.

The defect these pin down was silent. `link_working_document` wrote
``sharepoint_web_url=url[:1000]``: a SharePoint address longer than the column
was cut to fit, saved, and reported back as «Töödokumendi viide on lisatud.»
The lawyer got a success message and a link that opens nothing, and the part
that was cut is not recoverable — the only copy of it was in the clipboard they
have since overwritten.

1000 was not a considered bound either. It was three literals that happened to
agree — the model's ``max_length``, the form's, and the slice — and nothing kept
them agreeing. Long site and drive paths with a sharing token in the query
string run past it legitimately.

So: one number (`app.documents.limits.WORKING_DOCUMENT_URL_MAX_LENGTH`), read by
the model, the form and the service; exact preservation below it; a `DomainError`
above it. The tests below are written against the *boundary* rather than around
it — 4096 accepted and 4097 refused — because an off-by-one here is again a
stored value that silently disagrees with what somebody typed.

Nothing here repairs an already-truncated row. The missing suffix of a URL cut
to 1000 characters is unknowable, and a backfill that guessed would manufacture
exactly the false certainty the product forbids (AGENTS.md).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.limits import WORKING_DOCUMENT_URL_MAX_LENGTH
from app.documents.models import Document
from app.documents.services import link_working_document
from app.matters.forms import WorkingDocumentForm
from tests import factories

pytestmark = pytest.mark.django_db


#: The head and tail of an address SharePoint really issues: a nested site path,
#: an encoded document library, the file, and the sharing parameters.
_HEAD = (
    "https://koda.sharepoint.com/sites/Oigusosakond/"
    "Jagatud%20dokumendid/Kaibemaksuseaduse%20muutmine%202026/"
)
_TAIL = "/Koja%20arvamus.docx?d=w9f3b1c4e5a&csf=1&web=1&e=7QmTZa"
#: One more nested folder, repeated to reach a length. Padding with realistic
#: path segments rather than one absurd token keeps the fixture honest about
#: *why* these addresses get long.
_SEGMENT = "Tooversioonid/"


def _sharepoint_url(length: int) -> str:
    """A SharePoint-shaped address of exactly ``length`` characters."""
    padding = length - len(_HEAD) - len(_TAIL)
    assert padding > 0, "asked for a URL shorter than its own scaffolding"
    filler = (_SEGMENT * (padding // len(_SEGMENT) + 1))[:padding]
    url = _HEAD + filler + _TAIL
    assert len(url) == length
    return url


def _link(matter, specialist, url: str) -> Document:
    return link_working_document(
        matter=matter,
        title="Koja arvamus.docx",
        web_url=url,
        site_path="Õigusosakond / KMS 2026",
        created_by=specialist,
    )


# ---------------------------------------------------------------------------
# The bound is one number
# ---------------------------------------------------------------------------


def test_the_model_the_form_and_the_service_share_one_bound():
    """Three places enforce it; none of them carries its own literal."""
    assert WORKING_DOCUMENT_URL_MAX_LENGTH == 4096
    field = Document._meta.get_field("sharepoint_web_url")
    assert field.max_length == WORKING_DOCUMENT_URL_MAX_LENGTH
    assert WorkingDocumentForm().fields["web_url"].max_length == WORKING_DOCUMENT_URL_MAX_LENGTH


# ---------------------------------------------------------------------------
# What is stored is what was supplied
# ---------------------------------------------------------------------------


def test_an_ordinary_short_reference_still_works(normal_matter, specialist):
    url = "https://koda.sharepoint.com/sites/oigus/arvamus.docx"

    document = _link(normal_matter, specialist, url)
    document.refresh_from_db()

    assert document.sharepoint_web_url == url
    assert document.role == DocumentRole.WORKING_DOCUMENT
    assert document.has_working_document


def test_a_long_sharepoint_address_survives_the_form_and_the_service(normal_matter, specialist):
    """The case that used to be corrupted: past 1000, inside 4096."""
    url = _sharepoint_url(2500)
    assert 1000 < len(url) <= WORKING_DOCUMENT_URL_MAX_LENGTH

    form = WorkingDocumentForm({"title": "Koja arvamus.docx", "web_url": url, "site_path": ""})
    assert form.is_valid(), form.errors

    document = link_working_document(
        matter=normal_matter,
        title=form.cleaned_data["title"],
        web_url=form.cleaned_data["web_url"],
        created_by=specialist,
    )
    document.refresh_from_db()

    # Read back from PostgreSQL, so the column proves it too and not only the
    # instance the service happened to return.
    assert document.sharepoint_web_url == url
    assert len(document.sharepoint_web_url) == len(url)


def test_the_stored_value_is_exactly_the_cleaned_value(normal_matter, specialist):
    """Surrounding whitespace goes; nothing else is touched."""
    url = _sharepoint_url(1400)

    document = _link(normal_matter, specialist, f"  \n {url} \t ")
    document.refresh_from_db()

    assert document.sharepoint_web_url == url


def test_exactly_the_maximum_is_accepted(normal_matter, specialist):
    url = _sharepoint_url(WORKING_DOCUMENT_URL_MAX_LENGTH)

    document = _link(normal_matter, specialist, url)
    document.refresh_from_db()

    assert len(document.sharepoint_web_url) == WORKING_DOCUMENT_URL_MAX_LENGTH
    assert document.sharepoint_web_url == url


def test_one_character_past_the_maximum_is_refused(normal_matter, specialist):
    url = _sharepoint_url(WORKING_DOCUMENT_URL_MAX_LENGTH + 1)

    with pytest.raises(DomainError) as refusal:
        _link(normal_matter, specialist, url)

    # Refused, not shortened — and nothing was written on the way out.
    assert str(WORKING_DOCUMENT_URL_MAX_LENGTH) in str(refusal.value)
    assert not Document.objects.filter(role=DocumentRole.WORKING_DOCUMENT).exists()


def test_whitespace_is_trimmed_before_the_length_is_judged(normal_matter, specialist):
    """A padded 4096 is 4096, not 4100."""
    url = _sharepoint_url(WORKING_DOCUMENT_URL_MAX_LENGTH)

    document = _link(normal_matter, specialist, f"  {url}  ")
    document.refresh_from_db()

    assert document.sharepoint_web_url == url


# ---------------------------------------------------------------------------
# The form says so before the POST
# ---------------------------------------------------------------------------


def test_the_form_reports_an_over_long_address_as_a_field_error():
    url = _sharepoint_url(WORKING_DOCUMENT_URL_MAX_LENGTH + 1)

    form = WorkingDocumentForm({"title": "Koja arvamus.docx", "web_url": url, "site_path": ""})

    assert not form.is_valid()
    assert list(form.errors) == ["web_url"]
    assert str(WORKING_DOCUMENT_URL_MAX_LENGTH) in " ".join(form.errors["web_url"])


def test_the_rendered_input_lets_the_browser_paste_the_whole_address():
    """The other half of the old bug, and the half a server test cannot see.

    ``maxlength`` was 1000, so the browser cut a pasted address before a request
    was ever made. The field had already lost the suffix by the time anything on
    the server could have refused it.
    """
    assert 'maxlength="4096"' in str(WorkingDocumentForm()["web_url"])


def test_the_form_accepts_an_address_the_old_bound_refused():
    form = WorkingDocumentForm(
        {"title": "Koja arvamus.docx", "web_url": _sharepoint_url(2500), "site_path": ""}
    )

    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# The refusals that were already there stay there
# ---------------------------------------------------------------------------


def test_a_non_web_scheme_is_still_refused(normal_matter, specialist):
    with pytest.raises(DomainError):
        _link(normal_matter, specialist, "file:///C:/kohalik.docx")


def test_an_address_without_a_host_is_still_refused(normal_matter, specialist):
    with pytest.raises(DomainError):
        _link(normal_matter, specialist, "https:///sites/oigus/arvamus.docx")


def test_a_long_address_with_a_bad_scheme_is_still_refused(normal_matter, specialist):
    """Length does not become the only thing checked."""
    with pytest.raises(DomainError):
        _link(normal_matter, specialist, "file://" + "a/" * 800)


# ---------------------------------------------------------------------------
# End to end, through the view a person actually uses
# ---------------------------------------------------------------------------


def test_the_documents_tab_stores_a_long_address_whole(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)
    url = _sharepoint_url(3000)

    response = client.post(
        reverse("matters:add_working_document", kwargs={"pk": matter.pk}),
        {"title": "Koja arvamus.docx", "web_url": url, "site_path": ""},
        follow=True,
    )

    assert response.status_code == 200
    document = Document.objects.get(matter=matter, role=DocumentRole.WORKING_DOCUMENT)
    assert document.sharepoint_web_url == url
