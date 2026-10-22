"""Every link the MCP server returns is absolute and built from a verified id (docs/adr/0185 §2).

The origin comes from settings, never from a request; it must be a bare https
origin outside DEBUG; and only this application's own paths are appended to it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from app.mcp_server import links
from app.search.models import SearchSourceKind

ORIGIN = "https://juristid.example.test"


@pytest.fixture(autouse=True)
def origin(settings):
    settings.DEBUG = False
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN
    return settings


def test_an_https_origin_is_used_as_it_stands(origin) -> None:
    assert links.public_origin() == ORIGIN
    origin.JURISTID_PUBLIC_ORIGIN = ORIGIN + "/"
    assert links.public_origin() == ORIGIN


@pytest.mark.parametrize(
    "value",
    [
        "",
        "juristid.example.test",
        "ftp://juristid.example.test",
        "https://juristid.example.test/otsing",
        "https://juristid.example.test?x=1",
        "https://user:secret@juristid.example.test",
        "https://",
    ],
)
def test_an_origin_that_is_not_a_bare_origin_is_refused(origin, value) -> None:
    origin.JURISTID_PUBLIC_ORIGIN = value
    with pytest.raises(ImproperlyConfigured, match="JURISTID_PUBLIC_ORIGIN"):
        links.public_origin()


def test_plain_http_is_refused_outside_debug(origin) -> None:
    origin.JURISTID_PUBLIC_ORIGIN = "http://juristid.example.test"
    with pytest.raises(ImproperlyConfigured, match="must use https"):
        links.public_origin()


def test_plain_http_is_accepted_in_debug(origin) -> None:
    origin.DEBUG = True
    origin.JURISTID_PUBLIC_ORIGIN = "http://localhost:8000"
    assert links.matter_url(uuid.UUID(int=1)).startswith("http://localhost:8000/")


@pytest.mark.parametrize("path", ["//elsewhere.example/x", "https://elsewhere.example/", "teemad/"])
def test_only_own_paths_are_made_absolute(path) -> None:
    with pytest.raises(ValueError, match="own paths"):
        links.absolute(path)


def test_each_link_is_the_origin_plus_the_pages_path() -> None:
    matter, entry, document, link = (uuid.uuid4() for _ in range(4))
    detail = reverse("matters:matter_detail", kwargs={"pk": matter})
    assert links.matter_url(matter) == ORIGIN + detail
    assert links.entry_url(matter, entry) == f"{ORIGIN}{detail}#sissekanne-{entry}"
    assert links.document_url(document) == ORIGIN + reverse(
        "documents:document_detail", kwargs={"pk": document}
    )
    assert links.source_page_url(link) == ORIGIN + reverse(
        "legacy_import:source_page", kwargs={"pk": link}
    )
    assert links.matter_documents_url(matter) == ORIGIN + reverse(
        "matters:matter_documents", kwargs={"pk": matter}
    )
    opinions = links.submissions_url(SimpleNamespace(pk=matter))
    assert opinions.startswith(ORIGIN + reverse("matters:matter_documents", kwargs={"pk": matter}))
    assert "?roll=" in opinions


def test_a_result_links_where_the_search_page_does() -> None:
    entry = uuid.uuid4()
    result = SimpleNamespace(
        source_kind=SearchSourceKind.ENTRY.value,
        matter=SimpleNamespace(pk=uuid.uuid4()),
        document_id=None,
        entry_id=entry,
        source_page_id=None,
        submission_id=None,
    )
    assert links.result_url(result).startswith(ORIGIN + "/")
    assert links.result_url(result).endswith(f"#sissekanne-{entry}")
