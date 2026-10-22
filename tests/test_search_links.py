"""Where a search result opens is decided once, in `app.search.links` (docs/adr/0185).

The rule moved out of `app.search.views` when the MCP server became its second
reader. These tests hold that the move changed nothing: the page still links
every kind exactly where it did, and the view uses the shared function rather
than a copy of it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.urls import reverse

from app.search import views
from app.search.links import (
    document_path,
    entry_path,
    matter_path,
    source_page_path,
    target_path,
)
from app.search.models import SearchSourceKind
from app.submissions.opinions import opinions_url


def _result(kind: str, **ids: object) -> SimpleNamespace:
    matter = SimpleNamespace(pk=uuid.uuid4())
    values = {
        "document_id": None,
        "entry_id": None,
        "source_page_id": None,
        "submission_id": None,
        **ids,
    }
    return SimpleNamespace(source_kind=kind, matter=matter, **values)


def test_the_search_page_links_through_the_shared_rule() -> None:
    assert views._target_url is target_path


def test_a_matter_opens_on_its_page() -> None:
    result = _result(SearchSourceKind.MATTER.value)
    assert target_path(result) == reverse("matters:matter_detail", kwargs={"pk": result.matter.pk})


def test_an_entry_opens_at_its_anchor() -> None:
    entry = uuid.uuid4()
    result = _result(SearchSourceKind.ENTRY.value, entry_id=entry)
    expected = reverse("matters:matter_detail", kwargs={"pk": result.matter.pk})
    assert target_path(result) == f"{expected}#sissekanne-{entry}"
    assert entry_path(result.matter.pk, entry) == f"{expected}#sissekanne-{entry}"


def test_a_document_and_its_fragment_open_on_the_document_page() -> None:
    document = uuid.uuid4()
    for kind in (SearchSourceKind.DOCUMENT.value, SearchSourceKind.DOCUMENT_FRAGMENT.value):
        result = _result(kind, document_id=document)
        assert target_path(result) == reverse("documents:document_detail", kwargs={"pk": document})
    assert document_path(document) == reverse("documents:document_detail", kwargs={"pk": document})


def test_a_historical_page_opens_on_its_own_page() -> None:
    link = uuid.uuid4()
    result = _result(SearchSourceKind.LEGACY_SOURCE_PAGE.value, source_page_id=link)
    assert target_path(result) == reverse("legacy_import:source_page", kwargs={"pk": link})
    assert source_page_path(link) == target_path(result)


def test_an_opinion_opens_on_the_matters_opinions() -> None:
    result = _result(SearchSourceKind.SUBMISSION.value, submission_id=uuid.uuid4())
    assert target_path(result) == opinions_url(result.matter)


def test_every_other_kind_opens_on_the_matter() -> None:
    for kind in (
        SearchSourceKind.ENGAGEMENT.value,
        SearchSourceKind.PROCEDURAL_DEVELOPMENT.value,
        SearchSourceKind.EXTERNAL_POSITION.value,
        SearchSourceKind.WEBSITE_OVERVIEW.value,
        SearchSourceKind.IMPORTANT_DATE.value,
        SearchSourceKind.WORK_VICTORY.value,
    ):
        result = _result(kind)
        assert target_path(result) == matter_path(result.matter.pk)


def test_a_kind_missing_its_typed_id_falls_back_to_the_matter() -> None:
    for kind in (
        SearchSourceKind.ENTRY.value,
        SearchSourceKind.DOCUMENT_FRAGMENT.value,
        SearchSourceKind.LEGACY_SOURCE_PAGE.value,
        SearchSourceKind.SUBMISSION.value,
    ):
        result = _result(kind)
        assert target_path(result) == matter_path(result.matter.pk)
