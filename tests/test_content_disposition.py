"""What a stored file is called on its way to the browser.

An Estonian legal archive is a corpus of Estonian filenames, so the accented
case is the ordinary one and the ASCII case is the exception. All three of the
defects below were live at once, and each fails somewhere different: one loses
the name, one loses the whole header, and one lets an uploaded filename write
header syntax.

The old assertions passed through all of it, because they checked that the
header *started with* ``attachment;`` and stopped there.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.http import FALLBACK_FILENAME, content_disposition, safe_filename
from tests import factories

pytestmark = pytest.mark.django_db

ESTONIAN = "Arvamus õigusloome eelnõu kohta.pdf"
#: š and ž are outside latin-1. Django MIME-encodes a header value it cannot
#: encode, which replaced the entire ``Content-Disposition`` — directive and all.
BEYOND_LATIN1 = "Šokk ja žanr.pdf"


# -- the builder -----------------------------------------------------------


@pytest.mark.parametrize("name", [ESTONIAN, BEYOND_LATIN1, "report.pdf", "фаил.pdf"])
def test_the_header_is_always_ascii(name):
    """Nothing is left for Django to MIME-encode.

    This is the assertion that matters most: a MIME-encoded header is not a
    mangled filename, it is a destroyed directive, and a file meant to download
    renders in the page instead.
    """
    header = content_disposition("attachment", name)

    assert header.isascii()
    assert header.startswith('attachment; filename="')


def test_a_non_ascii_name_is_percent_encoded_not_base64():
    """RFC 5987 means percent-encoded UTF-8. It has never meant base64.

    The download route used ``urlsafe_base64_encode``, so this file arrived as
    ``QXJ2YW11cyDDtWlndXNsb29tZSBlZWxuw7V1IGtvaHRhLnBkZg`` — no name, no
    extension, and nothing to suggest anything had gone wrong.
    """
    header = content_disposition("attachment", ESTONIAN)

    assert "filename*=UTF-8''Arvamus%20%C3%B5igusloome%20eeln%C3%B5u%20kohta.pdf" in header
    assert "QXJ2YW11cy" not in header


def test_an_ascii_name_needs_no_second_parameter():
    assert content_disposition("attachment", "report.pdf") == 'attachment; filename="report.pdf"'


def test_the_ascii_fallback_is_readable_rather_than_stripped():
    """`õ` degrades to `o`, so the fallback still names the file.

    Dropping unencodable characters outright turns *Õigusloome* into
    *igusloome*, which reads as a corrupted download rather than a
    transliterated one.
    """
    header = content_disposition("attachment", ESTONIAN)

    assert 'filename="Arvamus oigusloome eelnou kohta.pdf"' in header


def test_a_quote_in_the_name_cannot_add_a_parameter():
    """An upload's filename is whatever the multipart part claimed it was.

    Nothing rejects a quote on the way in, and ``filename="{name}"`` closed the
    parameter early and let the rest become header syntax.
    """
    header = content_disposition("attachment", 'evil".pdf; download="x')

    assert header.count('"') == 2
    assert "\\" not in header


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        (r"C:\Users\somebody\Desktop\fail.pdf", "fail.pdf"),
        ("a\x00b\nc.pdf", "abc.pdf"),
        ("", FALLBACK_FILENAME),
        ("...", FALLBACK_FILENAME),
    ],
)
def test_a_filename_is_reduced_to_one_safe_component(supplied, expected):
    assert safe_filename(supplied) == expected


# -- the routes that use it ------------------------------------------------


@pytest.fixture
def estonian_pdf(db, specialist):
    """One evidence version whose filename is spelled the way Estonians spell."""
    from app.documents.services import add_evidence_version, create_document

    matter = factories.MatterFactory(owner=specialist)
    document = create_document(matter=matter, title="Arvamus", created_by=specialist)
    return add_evidence_version(
        document=document,
        content=b"%PDF-1.7\nsynthetic\n",
        original_filename=ESTONIAN,
        mime_type="application/pdf",
        uploaded_by=specialist,
    )


def test_the_download_route_names_the_file_the_way_it_was_stored(client, specialist, estonian_pdf):
    client.force_login(specialist)

    response = client.get(reverse("documents:download", kwargs={"pk": estonian_pdf.pk}))
    header = response["Content-Disposition"]

    assert response.status_code == 200
    assert header.startswith("attachment;")
    assert "filename*=UTF-8''Arvamus%20%C3%B5igusloome" in header
    response.close()


def test_the_inline_route_keeps_its_directive_for_an_unencodable_name(client, specialist):
    """The inline PDF viewer is the whole point of this route.

    With the raw name in the header Django MIME-encoded the value, the browser
    saw no ``inline`` at all, and the Content-Security-Policy that makes inline
    rendering safe was attached to a response that no longer asked for it.
    """
    from app.documents.services import add_evidence_version, create_document

    matter = factories.MatterFactory(owner=specialist)
    document = create_document(matter=matter, title="Arvamus", created_by=specialist)
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.7\nsynthetic\n",
        original_filename=BEYOND_LATIN1,
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    client.force_login(specialist)

    response = client.get(reverse("documents:open", kwargs={"pk": version.pk}))
    header = response["Content-Disposition"]

    assert response.status_code == 200
    assert header.startswith("inline;")
    assert "=?utf-8?" not in header
    assert "filename*=UTF-8''%C5%A0okk" in header
    response.close()
