"""UI-12: the historical OneNote copy (`/ajalugu/<pk>/`) reads as a document.

Two halves. The pure rules of `app.legacy_import.historical_reading` — the
text a reader sees (decoded exactly once, where the capture proves it
encoded), web addresses, source headings, anchors, lists, contents — are held
without a database. The page itself is then read against the synthetic shared
dossier of `tests/test_ui12_world.py`, imported through the real capture
import: the `&quot;` defect and its cause, the dossier's status said once,
order, counts, anchors and the technical details kept out of the reading flow.

Every page, file and Teema is invented.
"""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import escape

from app.legacy_import import historical_reading as reading
from app.legacy_import.historical_views import _rendered_blocks, file_summary, import_statuses
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
)
from tests.test_ui12_world import (
    DOSSIER_FILES,
    DOSSIER_TITLE,
    DOUBLE_QUOTED,
    DOUBLE_QUOTED_SHOWN,
    LITERAL_ENTITY,
    LITERAL_ENTITY_SHOWN,
    REPEATED,
    SINGLE_TITLE,
    build_world,
)

# -- the pure rules ----------------------------------------------------------------------


def _capture(text: str, **extra) -> dict:
    """A block as the capture import writes it (`rendered_blocks` adds `source_kind`)."""
    return {"ordinal": 1, "kind": "TEXT", "text": text, "source_kind": "TEXT", **extra}


def test_a_capture_block_is_decoded_exactly_once():
    assert reading.display_text(_capture(DOUBLE_QUOTED)) == DOUBLE_QUOTED_SHOWN
    # A person who typed «&quot;» keeps it: one decoding, never two.
    assert reading.display_text(_capture(LITERAL_ENTITY)) == LITERAL_ENTITY_SHOWN
    assert reading.display_text(_capture("&amp;amp;")) == "&amp;"


def test_a_block_of_an_unproven_shape_is_shown_as_stored():
    # The archive and pilot shapes carry no capture mark: their encoding is not proven.
    block = {"ordinal": 1, "kind": "TEXT", "text": "Koda &quot;toetab&quot;"}
    assert reading.display_text(block) == "Koda &quot;toetab&quot;", "an unproven shape decoded"


def test_decoded_markup_is_still_escaped_by_the_page():
    block = _capture("&lt;script&gt;alert(1)&lt;/script&gt; &lt;b&gt;paks&lt;/b&gt;")
    text = reading.display_text(block)
    assert text == "<script>alert(1)</script> <b>paks</b>"
    page = _page([block])
    html = render_to_string(
        "legacy_import/source_page.html",
        {
            "matter": SimpleNamespace(pk=uuid.uuid4(), title="Näidisteema"),
            "link": SimpleNamespace(pk=uuid.uuid4()),
            "page": page,
            "blocks": reading.reading_entries(_rendered_blocks(page, {}, {}, readable=set())),
            "dossier": SimpleNamespace(shared=False),
        },
    )
    assert "<script>" not in html and "<b>" not in html, "source text rendered as markup"
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &lt;b&gt;paks&lt;/b&gt;" in html


def test_text_parts_keep_every_character_and_link_only_web_addresses():
    text = "Vt https://eelnoud.example.invalid/a?b=1, ka www.example.invalid/x. ja onenote:///y"
    parts = reading.text_parts(text)
    assert "".join(piece for piece, _href in parts) == text
    assert [(piece, href) for piece, href in parts if href] == [
        ("https://eelnoud.example.invalid/a?b=1", "https://eelnoud.example.invalid/a?b=1"),
        ("www.example.invalid/x", "https://www.example.invalid/x"),
    ]
    assert not [href for _piece, href in parts if href.startswith("onenote:")], (
        "an onenote: address linked"
    )


def _desktop(ordinal, text, *, parent=None, kind="TEXT", depth=0):
    return {
        "ordinal": ordinal,
        "kind": kind,
        "text": text,
        "outline": 0,
        "parent": parent,
        "depth": depth,
        "source_kind": kind,
    }


def test_a_heading_is_a_short_line_the_outline_nests_material_under():
    blocks = [
        _desktop(0, "Taust"),
        _desktop(1, "Lõik taustast.", parent=0, depth=1),
        _desktop(2, "Koda saatis arvamuse."),  # a sentence heads nothing
        _desktop(3, "alamlõik", parent=2, depth=1),
        _desktop(4, "Lõik ilma alamplokkideta"),  # nests nothing
        _desktop(5, "x" * 91),
        _desktop(6, "pikk all", parent=5, depth=1),
        _desktop(7, "12.03.2024 - Ministeerium saatis eelnõu"),
        _desktop(8, "fail", parent=7, kind="FILE_ATTACHMENT", depth=1),
    ]
    dated = reading.dated_starts(blocks)
    assert set(dated) == {7}
    assert reading.headings(blocks, dated=dated) == {0}, "a heading the page did not have"


def test_a_heading_in_the_depth_shape_of_an_earlier_capture():
    blocks = [
        {"ordinal": 1, "kind": "TEXT", "text": "Arvamused:", "depth": 0},
        {"ordinal": 2, "kind": "LIST_ITEM", "text": "koja arvamus", "depth": 1},
        {"ordinal": 3, "kind": "TEXT", "text": "Lõpp", "depth": 0},
    ]
    assert reading.headings(blocks) == {1}


def test_only_an_ordinal_that_names_one_block_is_an_anchor():
    blocks = [{"ordinal": 1}, {"ordinal": 2}, {"ordinal": 2}, {"ordinal": None}, {"ordinal": "3"}]
    assert reading.anchored_ordinals(blocks) == {1}, "an ambiguous ordinal anchored"


def test_list_items_are_one_list_until_something_else_comes():
    item = {"kind": "text", "list_item": True, "text": "a"}
    other = {"kind": "text", "list_item": False, "text": "b"}
    file = {"kind": "file"}
    entries = reading.reading_entries([item, item, file, item, other])
    assert [entry["kind"] for entry in entries] == ["list", "file", "list", "text"]
    assert len(entries[0]["items"]) == 2 and len(entries[2]["items"]) == 1


def test_contents_list_only_the_pages_own_headings_and_dated_notes():
    blocks = [
        {"kind": "text", "anchor": "plokk-0", "heading": True, "dated": None, "text": "Taust"},
        {"kind": "text", "anchor": "plokk-1", "heading": False, "dated": None, "text": "lõik"},
        {
            "kind": "text",
            "anchor": "plokk-2",
            "heading": False,
            "dated": {"date": "12.03.2024", "iso": "2024-03-12", "label": "Ministeerium"},
            "text": "12.03.2024 - Ministeerium",
        },
        {"kind": "text", "anchor": "", "heading": True, "dated": None, "text": "ankruta"},
    ]
    assert reading.contents(blocks) == [], "a contents list for a short page"
    blocks.append({"kind": "text", "anchor": "plokk-9", "heading": True, "text": "Lõpp"})
    assert [(entry["anchor"], entry["label"]) for entry in reading.contents(blocks)] == [
        ("plokk-0", "Taust"),
        ("plokk-2", "Ministeerium"),
        ("plokk-9", "Lõpp"),
    ]


@pytest.mark.parametrize(
    ("name", "kind"),
    [("a.pdf", "PDF"), ("B.Docx", "DOCX"), ("ilma", ""), (".peidetud", ""), ("x.toolong1", "")],
)
def test_a_file_type_is_its_extension(name, kind):
    assert reading.file_type(name) == kind


def _page(blocks, **extra):
    values = {
        "blocks": blocks,
        "links": [],
        "source_modified_at": None,
        "capture_id": "onenote-desktop-20261104-näidis",
        "first_imported_at": None,
        "title": "Näidisleht",
        "display_location": "",
        "reading_order_ambiguous": False,
    }
    values.update(extra)
    return SimpleNamespace(**values)


def test_files_the_text_does_not_reach_follow_a_notice_with_no_capture_id_and_no_anchor():
    resource = SimpleNamespace(
        pk=1, original_filename="hilisem.pdf", size_bytes=10, source_added_at=None
    )
    page = _page([_capture("Lehe tekst", ordinal=0)])
    blocks = _rendered_blocks(page, {"r-late": resource}, {}, readable=set())
    assert [block["kind"] for block in blocks] == ["text", "notice", "file"]
    assert "onenote-desktop" not in blocks[1]["text"], "a capture id in the reading flow"
    assert blocks[2]["anchor"] == "", "an anchor on a place no stored block has"
    assert blocks[0]["anchor"] == "plokk-0"


# -- the page, against the synthetic shared dossier --------------------------------------


@pytest.fixture
def world(tmp_path, settings, evidence_root):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    corpus = tmp_path / "corpus"
    settings.HISTORICAL_SOURCE_ROOT = str(corpus)
    return build_world(corpus)


def _link(matter, title):
    return MatterSourcePage.objects.get(matter=matter, source_page__title=title)


def _get(client, link) -> str:
    response = client.get(reverse("legacy_import:source_page", kwargs={"pk": link.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _reading_flow(html: str) -> str:
    """The page up to its technical details: what a reader reads."""
    return html.split("<summary>Tehnilised andmed</summary>", 1)[0]


@pytest.mark.django_db
def test_prose_quotes_read_as_quotes_and_the_stored_text_is_untouched(world, client, specialist):
    link = _link(world["teemad"][1], DOSSIER_TITLE)
    stored = list(link.source_page.blocks)
    assert DOUBLE_QUOTED in [block.get("text") for block in stored]  # as the reader stored it
    client.force_login(specialist)
    html = _get(client, link)
    # The defect: the stored reference escaped a second time, read as «&quot;».
    assert "&amp;quot;kliimaseaduseks" not in html, "a reference escaped twice"
    assert "&quot;kliimaseaduseks&quot;" in html  # one escape of a real `"`
    # A person's own «&quot;» survives: decoded once, escaped once.
    assert "sõna &amp;quot; otse tekstis" in html
    link.source_page.refresh_from_db()
    assert link.source_page.blocks == stored, "the stored text rewritten"


@pytest.mark.django_db
def test_the_shared_status_is_said_once_and_the_association_stays_unresolved(
    world, client, specialist
):
    link = _link(world["teemad"][1], DOSSIER_TITLE)
    before = list(
        LegacySourceResourceImport.objects.filter(resource__source_page=link.source_page)
        .order_by("pk")
        .values_list("pk", "state", "error_code", "document_id")
    )
    client.force_login(specialist)
    html = _get(client, link)
    flat = " ".join(html.split())
    assert flat.count("failid ootavad teemaga seostamist") == 1
    assert "Ootab teemaga seostamist" not in html, "the held state repeated per file"
    assert "faili kuuluvuse otsustab inimene" not in html
    # Every file keeps its read-only original download, through the existing route.
    held = [n for n, _name, _created, _parent in DOSSIER_FILES]
    assert html.count("Laadi alla (allikas)") == len(held)
    # Its Teemad, each linked to its own page.
    for teema in world["teemad"]:
        if teema.pk != link.matter_id:
            assert reverse("matters:matter_detail", kwargs={"pk": teema.pk}) in html
    # Presentation only: nothing about the import rows changed.
    after = list(
        LegacySourceResourceImport.objects.filter(resource__source_page=link.source_page)
        .order_by("pk")
        .values_list("pk", "state", "error_code", "document_id")
    )
    assert after == before
    assert {state for _pk, state, _code, _doc in after} == {ResourceImportState.SKIPPED}
    assert all(document is None for *_rest, document in after)


@pytest.mark.django_db
def test_a_file_whose_state_differs_from_the_dossier_still_says_it(world, client, specialist):
    link = _link(world["teemad"][1], DOSSIER_TITLE)
    row = LegacySourceResourceImport.objects.get(
        matter_source_page=link, resource__original_filename="Pressiteade.docx"
    )
    # A disposable test row: one file held for review instead.
    LegacySourceResourceImport.objects.filter(pk=row.pk).update(error_code="RECORD_HELD_FOR_REVIEW")
    client.force_login(specialist)
    html = _get(client, link)
    assert html.count("Ootab ülevaatust") == 1
    chip = html.index("Ootab ülevaatust")
    assert html.rfind("Pressiteade.docx", 0, chip) > html.rfind("Muudatusettepanekute", 0, chip)


@pytest.mark.django_db
def test_files_and_documents_are_counted_apart(world, client, specialist):
    second = world["teemad"][1]
    dossier = _link(second, DOSSIER_TITLE)
    single = _link(second, SINGLE_TITLE)
    statuses = import_statuses([dossier, single])
    assert file_summary(statuses[dossier.pk]) == (
        f"Lehel on {len(DOSSIER_FILES)} faili; ükski neist ei ole selle teema dokument."
    )
    assert file_summary(statuses[single.pk]) == "Lehel on 3 faili; kõik on selle teema dokumendid."
    client.force_login(specialist)
    assert "Lehel on 3 faili; kõik on selle teema dokumendid." in _get(client, single)


@pytest.mark.django_db
def test_order_headings_lists_links_and_anchors(world, client, specialist):
    link = _link(world["teemad"][1], DOSSIER_TITLE)
    client.force_login(specialist)
    html = _get(client, link)
    content = html.split('id="sisu-heading"', 1)[1].split("</section>", 1)[0]
    # The original interleaving, in stored order: text, files, text.
    markers = [
        "Taust",
        "kliimaseaduseks",
        "Kliimaseaduse_VTK.pdf",
        "12.03.2024 - Ministeerium",
        "Eelnou_kooskolastus.pdf",
        "Arvamused:",
        "Koja_arvamus_2024-04-02.pdf",
        "05.2024 - Teine lugemine",
        "Majanduskomisjoni_protokoll.pdf",
        "Järgmised tegevused:",
        "18.09.2024 - Eelnõu läks",
        "Lõuendil_kokkuvõte.pdf",
    ]
    places = [content.index(marker) for marker in markers]
    assert places == sorted(places)
    # A deliberately repeated sentence is the source's, and is there twice.
    assert content.count(REPEATED) == 2
    # Source headings are headings; dated notes start sections and are anchored.
    assert re.findall(r"<h3[^>]*>([^<]+)</h3>", content) == [
        "Taust",
        "Arvamused:",
        "Järgmised tegevused:",
    ]
    assert (
        'id="plokk-4" class="casefile__text casefile__block--d0 casefile__text--dated"' in content
    )
    # The address written in the prose is a working link that sends no referrer.
    assert (
        '<a href="https://eelnoud.example.invalid/kliima/2024-0142" target="_blank" '
        'rel="noopener noreferrer">' in content
    )
    # Every contents entry leads to an element that is on the page.
    toc = html.split('aria-label="Lehe sisukord"', 1)[1].split("</nav>", 1)[0]
    targets = re.findall(r'href="#(plokk-\d+)"', toc)
    assert len(targets) == 6
    assert all(f'id="{target}"' in content for target in targets)


@pytest.mark.django_db
def test_technical_metadata_is_only_in_the_technical_details(world, client, specialist):
    link = _link(world["teemad"][1], DOSSIER_TITLE)
    page = LegacySourcePage.objects.get(pk=link.source_page_id)
    client.force_login(specialist)
    html = _get(client, link)
    flow = _reading_flow(html)
    details = html.split("<summary>Tehnilised andmed</summary>", 1)[1]
    for technical in (
        page.capture_id,
        page.page_key,
        page.source_page_id,
        page.source_xml_sha256,
        str(link.get_match_method_display()),
    ):
        technical = escape(technical)
        assert technical not in flow, f"{technical!r} in the reading flow"
        assert technical in details, f"{technical!r} missing from the technical details"
    # No «<capture> · plokk N» reference anywhere a reader reads.
    assert not re.search(r"plokk \d|plokid \d", flow)


@pytest.mark.django_db
def test_the_page_is_still_refused_to_a_reader_without_the_teema(world, client):
    link = _link(world["teemad"][1], DOSSIER_TITLE)
    response = client.get(reverse("legacy_import:source_page", kwargs={"pk": link.pk}))
    assert response.status_code in (302, 403, 404), "an anonymous reader let in"
