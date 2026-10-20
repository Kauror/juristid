"""A historical page's own links render whatever shape its capture wrote them in.

The OneNote desktop archive has written a page's hyperlinks (``links.json``) in
two shapes: ``{"url", "displayText"}`` in the 2026-08 corpus and ``{"href",
"text", "blockOrdinal"}`` in the evidence-pilot capture (v2.3). Both are stored
as captured in ``LegacySourcePage.links``. The page read only the first, and a
template filter argument that names a missing key raises, so every pilot page
with a link answered 500 (production, 20.10.2026, `/ajalugu/<id>/`).
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.source_pages import (
    LegacySourcePage,
    MatterSourcePage,
    SourceMatchClass,
    SourceMatchMethod,
    SourcePageRole,
    SourceRelationshipKind,
    SourceSystem,
)

pytestmark = pytest.mark.django_db

PILOT_LINK = {
    "href": "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=OJ:L_000000000",
    "text": "Näidisakt ELi Teatajas",
    "blockOrdinal": 3,
}
ARCHIVE_LINK = {"url": "https://eelnoud.example.invalid/19-0001", "displayText": "EIS"}


def _page(matter, links) -> MatterSourcePage:
    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="1-links-test",
        page_key="p-links-test",
        source_notebook="Näidismärkmik",
        source_section="Näidissektsioon",
        title="Näidisleht",
        page_level=1,
        page_order=1,
        page_role=SourcePageRole.MATTER_LIKE,
        role_reason="sünteetiline",
        source_created_at=now,
        source_modified_at=now,
        capture_id="c1",
        source_xml_sha256="0" * 64,
        derived_text="Näidisleht",
        blocks=[{"ordinal": 1, "kind": "TEXT", "text": "Lehe tekst."}],
        links=links,
        reading_order_strategy="VISUAL_THEN_XML",
        text_characters=10,
        block_count=1,
        file_count=0,
        file_bytes=0,
        first_imported_at=now,
        latest_imported_at=now,
    )
    return MatterSourcePage.objects.create(
        matter=matter,
        source_page=page,
        relationship_kind=SourceRelationshipKind.PRIMARY,
        match_method=SourceMatchMethod.EXCEL_EXACT_PAGE_ID,
        match_class=SourceMatchClass.EXACT,
        source_audit_reference="test",
    )


def _get(client, link):
    return client.get(reverse("legacy_import:source_page", kwargs={"pk": link.pk}))


def test_a_page_with_pilot_shaped_links_renders_them(signed_in, normal_matter):
    response = _get(signed_in, _page(normal_matter, [PILOT_LINK]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Lehel olnud lingid" in html
    assert "Näidisakt ELi Teatajas" in html
    assert "eur-lex.europa.eu/legal-content" in html


def test_a_page_with_archive_shaped_links_still_renders_them(signed_in, normal_matter):
    response = _get(signed_in, _page(normal_matter, [ARCHIVE_LINK]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "EIS" in html and "eelnoud.example.invalid/19-0001" in html


def test_both_shapes_on_one_page_and_a_link_without_text(signed_in, normal_matter):
    bare = {"href": "https://example.invalid/ilma-tekstita"}
    response = _get(signed_in, _page(normal_matter, [PILOT_LINK, ARCHIVE_LINK, bare]))
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count('class="filelist__item"') >= 3
    assert "example.invalid/ilma-tekstita" in html


def test_a_page_without_links_has_no_links_section(signed_in, normal_matter):
    response = _get(signed_in, _page(normal_matter, []))
    assert response.status_code == 200
    assert "Lehel olnud lingid" not in response.content.decode()


def test_the_stored_links_are_read_not_rewritten(signed_in, normal_matter):
    link = _page(normal_matter, [PILOT_LINK, ARCHIVE_LINK])
    _get(signed_in, link)
    link.source_page.refresh_from_db()
    assert link.source_page.links == [PILOT_LINK, ARCHIVE_LINK]


def test_page_links_names_both_shapes_alike():
    from app.legacy_import.historical_views import page_links

    assert page_links([PILOT_LINK, ARCHIVE_LINK, {"blockOrdinal": 1}, "not a link"]) == [
        {"url": PILOT_LINK["href"], "text": PILOT_LINK["text"]},
        {"url": ARCHIVE_LINK["url"], "text": ARCHIVE_LINK["displayText"]},
    ]


def test_a_reader_who_may_not_see_the_matter_is_still_refused(client, normal_matter):
    response = _get(client, _page(normal_matter, [PILOT_LINK]))
    assert response.status_code in (302, 403, 404)


# ---------------------------------------------------------------------------
# The Teema page carries no historical row; Dokumendid is the way in
# (owner, 20.10.2026)
# ---------------------------------------------------------------------------


def _teema(client, matter):
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _dokumendid(client, matter):
    return client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))


def test_a_teema_with_onenote_history_has_no_historical_row(signed_in, normal_matter):
    _page(normal_matter, [PILOT_LINK])
    html = _teema(signed_in, normal_matter)
    assert "historicalrow" not in html
    assert "Vaata ajaloolist materjali" not in html
    assert "ajalooline-materjal-heading" not in html


def test_its_dokumendid_tab_leads_to_the_page_and_the_page_opens(signed_in, normal_matter):
    link = _page(normal_matter, [PILOT_LINK])
    response = _dokumendid(signed_in, normal_matter)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Ajalooline OneNote" in html
    page_url = reverse("legacy_import:source_page", kwargs={"pk": link.pk})
    assert f'href="{page_url}"' in html
    assert signed_in.get(page_url).status_code == 200


def test_a_teema_without_onenote_history_is_unchanged(signed_in, normal_matter):
    html = _teema(signed_in, normal_matter)
    assert "historicalrow" not in html and "PRAEGUNE TEGEVUS" in html.upper()
    response = _dokumendid(signed_in, normal_matter)
    assert response.status_code == 200
    assert "Ajalooline OneNote" not in response.content.decode()
