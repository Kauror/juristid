"""The OneNote extractor, against a synthetic Graph.

No real notebook is touched. The client's HTTP opener is replaced with one that
answers from a dictionary of invented responses, which is what makes it possible
to test the things that actually matter here — pagination, hierarchy, and the
token allow-list — without an Entra registration, a tenant or a person's
consent.

The allow-list is the load-bearing part of this file. Page HTML names its
resources by URL, and following one means attaching a Microsoft 365 access token
to the request. A tool that follows a URL it found in a document will eventually
send somebody's token to a host named in that document.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from tools.onenote_export.archive import Archive, safe_component
from tools.onenote_export.export import (
    ExportSummary,
    export_notebooks,
    html_to_text,
)
from tools.onenote_export.graph import (
    ALLOWED_HOSTS,
    GRAPH_ROOT,
    GraphClient,
    UnsafeUrl,
    require_safe_url,
)
from tools.onenote_export.reconcile import (
    AUTOMATIC_TIERS,
    MatchTier,
    build_candidates,
    canonical_page_url,
    page_id_from_link,
    reference_tokens,
    summarise,
)

ONENOTE_HOST = "https://www.onenote.com/api/v1.0"


# -- a synthetic Graph -----------------------------------------------------


@dataclass
class _Response:
    body: bytes
    status: int = 200

    def read(self) -> bytes:
        return self.body

    @property
    def headers(self):
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class FakeGraph:
    """Answers from a routing table and records every URL it was asked for."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    def open(self, request, timeout=None):  # urllib's opener signature
        url = request.full_url
        self.requested.append(url)
        if url not in self.routes:
            raise AssertionError(f"the export asked for an unexpected URL: {url}")
        payload = self.routes[url]
        if isinstance(payload, bytes):
            return _Response(payload)
        return _Response(json.dumps(payload).encode("utf-8"))


def page_html(*, resources: list[str] = (), evil: str = "") -> bytes:
    parts = [
        "<html><head><title>Näidisleht</title>",
        "<style>p{color:red}</style></head><body>",
        "<h1>Kooskõlastus</h1>",
        "<p>Ministeeriumi kiri saabus kolmapäeval.</p>",
        "<script>alert('ei')</script>",
    ]
    for index, url in enumerate(resources, start=1):
        parts.append(
            f'<object data="{url}" data-attachment="lisa-{index}.pdf" type="application/pdf" />'
        )
    if evil:
        parts.append(f'<img src="{evil}" data-src-type="image/png" />')
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def build_routes(*, page_count: int = 3, evil: str = "") -> dict[str, object]:
    scope = "/me/onenote"
    routes: dict[str, object] = {
        f"{GRAPH_ROOT}{scope}/notebooks?%24top=100": {
            "value": [{"id": "nb-1", "displayName": "Õigusloome"}]
        },
        f"{GRAPH_ROOT}{scope}/notebooks/nb-1/sections?%24top=100": {
            "value": [{"id": "sec-1", "displayName": "2019"}]
        },
        f"{GRAPH_ROOT}{scope}/notebooks/nb-1/sectionGroups?%24top=100": {
            "value": [{"id": "grp-1", "displayName": "Arhiiv"}]
        },
        f"{GRAPH_ROOT}{scope}/sectionGroups/grp-1/sections?%24top=100": {
            "value": [{"id": "sec-2", "displayName": "2018"}]
        },
        f"{GRAPH_ROOT}{scope}/sectionGroups/grp-1/sectionGroups?%24top=100": {"value": []},
    }

    # A section whose pages arrive in two responses, so the nextLink path is
    # exercised rather than assumed.
    first = [
        {
            "id": f"page-{index}",
            "title": f"2019_{index} Näidiseelnõu",
            "createdDateTime": "2019-03-04T09:00:00Z",
            "lastModifiedDateTime": "2019-03-05T11:00:00Z",
            "level": 0,
            "order": index,
            "contentUrl": f"{GRAPH_ROOT}{scope}/pages/page-{index}/content",
            "links": {
                "oneNoteWebUrl": {
                    "href": f"https://onedrive.live.com/view.aspx?page-id=page-{index}"
                }
            },
        }
        for index in range(1, page_count)
    ]
    last = [
        {
            "id": f"page-{page_count}",
            "title": f"2019_{page_count} Näidiseelnõu",
            "contentUrl": f"{GRAPH_ROOT}{scope}/pages/page-{page_count}/content",
            "links": {},
        }
    ]
    routes[f"{GRAPH_ROOT}{scope}/sections/sec-1/pages?pagelevel=true&%24top=100"] = {
        "value": first,
        "@odata.nextLink": f"{GRAPH_ROOT}{scope}/sections/sec-1/pages?pagelevel=true&%24skip=2",
    }
    routes[f"{GRAPH_ROOT}{scope}/sections/sec-1/pages?pagelevel=true&%24skip=2"] = {"value": last}
    routes[f"{GRAPH_ROOT}{scope}/sections/sec-2/pages?pagelevel=true&%24top=100"] = {"value": []}

    resource = f"{ONENOTE_HOST}/me/notes/resources/res-1/$value"
    for index in range(1, page_count + 1):
        routes[f"{GRAPH_ROOT}{scope}/pages/page-{index}/content"] = page_html(
            resources=[resource] if index == 1 else [], evil=evil if index == 1 else ""
        )
    routes[resource] = "%PDF-1.4 sünteetiline lisa".encode()
    return routes


# -- the allow-list --------------------------------------------------------


def test_only_two_hosts_may_receive_a_token() -> None:
    assert ALLOWED_HOSTS == {"graph.microsoft.com", "www.onenote.com"}


@pytest.mark.parametrize(
    "url",
    [
        "http://graph.microsoft.com/v1.0/me/onenote/pages",  # not HTTPS
        "https://evil.invalid/v1.0/me/onenote/pages",
        # Contains the allowed host as a substring and is a different host.
        "https://graph.microsoft.com.evil.invalid/v1.0/me",
        "https://graph.microsoft.com.evil.invalid/resources/x/$value",
        "https://sharepoint.invalid/sites/koda/document.pdf",
        "file:///etc/passwd",
    ],
)
def test_a_url_outside_the_allow_list_is_refused(url: str) -> None:
    with pytest.raises(UnsafeUrl):
        require_safe_url(url)


def test_a_graph_url_that_is_not_a_resource_path_is_refused_as_a_resource() -> None:
    """The second half of the check: right host, wrong shape."""
    with pytest.raises(UnsafeUrl):
        require_safe_url(f"{GRAPH_ROOT}/me/drive/root/children", expect_resource=True)


def test_a_real_resource_url_is_accepted() -> None:
    url = f"{ONENOTE_HOST}/me/notes/resources/res-1/$value"
    assert require_safe_url(url, expect_resource=True) == url


def test_a_hostile_url_in_page_html_is_never_fetched(tmp_path) -> None:
    """The whole reason the allow-list exists.

    The page names an image on a host the tool does not trust. It must be
    counted as skipped and must not appear in the request log — a token sent
    there is a token in somebody else's server log.
    """
    evil = "https://tracker.invalid/steal/resources/x/$value"
    graph = FakeGraph(build_routes(evil=evil))
    summary = export_notebooks(
        GraphClient("synthetic-token", opener=graph), Archive(tmp_path), max_pages=10
    )

    assert summary.skipped_resources == 1
    assert not any("tracker.invalid" in url for url in graph.requested)


# -- the export ------------------------------------------------------------


@pytest.fixture
def exported(tmp_path):
    graph = FakeGraph(build_routes())
    archive = Archive(tmp_path)
    summary = export_notebooks(GraphClient("synthetic-token", opener=graph), archive, max_pages=10)
    return summary, archive, graph


def test_the_export_follows_the_next_link(exported) -> None:
    """Graph returns 20 entries by default. Assuming one response is complete
    would export a fraction of a notebook and look finished."""
    summary, _, graph = exported

    assert summary.pages == 3
    assert any("%24skip=2" in url for url in graph.requested)


def test_the_hierarchy_is_preserved_rather_than_flattened(exported) -> None:
    _, archive, _ = exported
    manifest = archive.read_manifest()

    assert {record["notebook_name"] for record in manifest} == {"Õigusloome"}
    assert {record["section_name"] for record in manifest} == {"2019"}
    assert all(record["page_id"] for record in manifest)


def test_section_groups_are_walked(tmp_path) -> None:
    routes = build_routes()
    routes[f"{GRAPH_ROOT}/me/onenote/sections/sec-2/pages?pagelevel=true&%24top=100"] = {
        "value": [
            {
                "id": "page-9",
                "title": "2018_7 Arhiiviteema",
                "contentUrl": f"{GRAPH_ROOT}/me/onenote/pages/page-9/content",
                "links": {},
            }
        ]
    }
    routes[f"{GRAPH_ROOT}/me/onenote/pages/page-9/content"] = page_html()

    archive = Archive(tmp_path)
    summary = export_notebooks(
        GraphClient("synthetic-token", opener=FakeGraph(routes)), archive, max_pages=10
    )

    assert summary.section_groups == 1
    grouped = [
        record for record in archive.read_manifest() if record["section_group_path"] == ["Arhiiv"]
    ]
    assert [record["section_name"] for record in grouped] == ["2018"]


def test_both_the_raw_html_and_the_derived_text_are_written(exported, tmp_path) -> None:
    """Keeping only the tidy version is the same mistake as storing extracted
    text instead of a PDF."""
    _, archive, _ = exported
    directory = archive.pages_root / safe_component("page-1")

    assert (directory / "page.html").read_bytes().startswith(b"<html>")
    text = (directory / "page.txt").read_text(encoding="utf-8")
    assert "Ministeeriumi kiri" in text
    assert "<p>" not in text
    assert "alert(" not in text
    assert "color:red" not in text


def test_every_page_records_its_provenance(exported) -> None:
    _, archive, _ = exported
    record = next(r for r in archive.read_manifest() if r["page_id"] == "page-1")

    assert record["exporter_version"]
    assert record["extracted_at"]
    assert record["html_sha256"]
    assert record["graph_scope"] == "/me/onenote"
    assert record["created_at"] == "2019-03-04T09:00:00Z"


def test_attachments_are_downloaded_with_a_checksum(exported) -> None:
    summary, archive, _ = exported
    record = next(r for r in archive.read_manifest() if r["page_id"] == "page-1")

    assert summary.attachments == 1
    assert record["attachments"][0]["filename"] == "lisa-1.pdf"
    assert len(record["attachments"][0]["sha256"]) == 64
    stored = archive.pages_root / "page-1" / "attachments" / record["attachments"][0]["stored_as"]
    assert stored.read_bytes().startswith(b"%PDF")


def test_the_page_ceiling_is_enforced(tmp_path) -> None:
    """A bounded proof, not a notebook download."""
    graph = FakeGraph(build_routes(page_count=3))
    summary = export_notebooks(
        GraphClient("synthetic-token", opener=graph), Archive(tmp_path), max_pages=2
    )
    assert summary.pages == 2


def test_the_summary_carries_counts_and_nothing_identifying() -> None:
    """This object is what may be pasted into a pull request."""
    summary = ExportSummary(notebooks=1, pages=12, attachments=3)
    text = summary.as_text()

    assert "12" in text
    assert all(word not in text.lower() for word in ("title", "url", "http", "@"))


def test_one_bad_page_does_not_end_the_run(tmp_path) -> None:
    routes = build_routes()
    del routes[f"{GRAPH_ROOT}/me/onenote/pages/page-2/content"]
    graph = FakeGraph(routes)
    graph.routes = routes

    class Tolerant(FakeGraph):
        def open(self, request, timeout=None):
            if request.full_url.endswith("page-2/content"):
                raise OSError("the service hung up")
            return super().open(request, timeout=timeout)

    summary = export_notebooks(
        GraphClient("synthetic-token", opener=Tolerant(routes)), Archive(tmp_path), max_pages=10
    )

    assert summary.pages == 2
    assert summary.errors


def test_html_to_text_keeps_paragraph_structure() -> None:
    """Paragraphs stay apart. OneNote pages are prose, and a note run into one
    line is harder to read than the HTML it came from."""
    text = html_to_text(b"<p>Esimene</p><p>Teine</p>")

    assert "Esimene" in text
    assert "Teine" in text
    assert text.index("Esimene") < text.index("Teine")
    assert len(text.splitlines()) > 1


# -- reconciliation --------------------------------------------------------


def test_a_page_id_in_a_link_is_extracted_exactly() -> None:
    assert page_id_from_link("https://onedrive.live.com/view.aspx?page-id=abc-123") == "abc-123"
    desktop_link = "onenote:///C:/Notes#Section&page-id={0A1B2C3D-4E5F-6789-ABCD-EF0123456789}"
    assert page_id_from_link(desktop_link) == "0A1B2C3D-4E5F-6789-ABCD-EF0123456789"


def test_a_link_with_no_identifier_yields_nothing_rather_than_a_guess() -> None:
    assert page_id_from_link("https://onedrive.live.com/view.aspx?section=2019") == ""
    assert page_id_from_link("") == ""


def test_urls_differing_only_in_noise_canonicalise_together() -> None:
    left = "https://ONEDRIVE.live.com/view.aspx?page-id=abc&wd=target#heading"
    right = "https://onedrive.live.com/view.aspx?wd=target&page-id=abc"
    assert canonical_page_url(left) == canonical_page_url(right)


def test_reference_tokens_normalise_every_shape() -> None:
    assert reference_tokens("Vaata 2019_184 ja 2019-184 ja 2019 184") == {"2019_184"}


def test_an_exact_page_id_match_is_automatic() -> None:
    manifest = [{"page_id": "abc-123", "title": "Midagi", "web_url": ""}]
    references = [{"id": "r1", "onenote_url": "https://x.invalid?page-id=abc-123"}]

    candidates = build_candidates(source_references=references, manifest=manifest)

    assert [candidate.tier for candidate in candidates] == [MatchTier.PAGE_ID]
    assert candidates[0].automatic is True


def test_a_shared_reference_token_is_automatic() -> None:
    manifest = [{"page_id": "p9", "title": "2019_184 Näidiseelnõu", "web_url": ""}]
    references = [
        {"id": "r2", "onenote_url": "https://x.invalid/no-id", "source_title": "2019-184 midagi"}
    ]

    candidates = build_candidates(source_references=references, manifest=manifest)

    assert candidates[0].tier == MatchTier.REFERENCE_TOKEN
    assert candidates[0].automatic is True


def test_a_similar_title_is_never_automatic() -> None:
    """The finding this whole module is shaped around: a OneNote hyperlink in
    this register has already been observed pointing at the wrong page, and a
    similar title is weaker evidence than a hyperlink."""
    manifest = [{"page_id": "p1", "title": "Pakendiseaduse muutmine 2019", "web_url": ""}]
    references = [{"id": "r3", "onenote_url": "", "source_title": "Pakendiseaduse muutmine  2019"}]

    candidates = build_candidates(source_references=references, manifest=manifest)

    assert candidates[0].tier == MatchTier.TITLE_SIMILARITY
    assert candidates[0].automatic is False
    assert MatchTier.TITLE_SIMILARITY not in AUTOMATIC_TIERS


def test_a_weak_title_match_produces_no_candidate_at_all() -> None:
    manifest = [{"page_id": "p1", "title": "Midagi hoopis muud", "web_url": ""}]
    references = [{"id": "r4", "onenote_url": "", "source_title": "Pakendiseaduse muutmine"}]

    assert build_candidates(source_references=references, manifest=manifest) == []


def test_a_reviewed_mapping_wins_where_nothing_else_matches() -> None:
    manifest = [{"page_id": "p7", "title": "Ei seostu", "web_url": ""}]
    references = [{"id": "r5", "onenote_url": "", "source_title": "Ka ei seostu"}]

    candidates = build_candidates(
        source_references=references, manifest=manifest, reviewed_mapping={"r5": "p7"}
    )

    assert candidates[0].tier == MatchTier.REVIEWED_MAPPING


def test_the_summary_reports_counts_by_tier() -> None:
    manifest = [{"page_id": "abc", "title": "2019_1 Teema", "web_url": ""}]
    references = [
        {"id": "r1", "onenote_url": "https://x.invalid?page-id=abc"},
        {"id": "r2", "onenote_url": "", "source_title": "midagi täiesti muud"},
    ]

    counts = summarise(
        build_candidates(source_references=references, manifest=manifest), total_references=2
    )

    assert counts["PAGE_ID"] == 1
    assert counts["unmatched"] == 1
    assert counts["automatic"] == 1
