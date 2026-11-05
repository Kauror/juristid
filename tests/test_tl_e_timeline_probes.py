"""Agent E (independent QA): probes of the historical-notes chronology.

Every probe drives the real code path — parser, projection, chronology, view,
template — with synthetic data only (invented text, `.invalid` addresses). A
test that fails here documents a defect; nothing in the application is changed.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest
from django.apps import apps
from django.urls import reverse
from django.utils import timezone

from app.matters.timeline import HISTORICAL_IMPORT_ITEM, HISTORICAL_NOTE_ITEM, matter_timeline
from tests import factories
from tests.test_tl_b_timeline_notes import blank, file_block, make_page, text
from tests.test_tl_b_timeline_notes import narrow_reader as narrow_reader  # a fixture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _public_origin(settings):
    # The MCP readers build absolute links (`app.mcp_server.links`).
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.invalid"


def _utc(*parts) -> datetime:
    return datetime(*parts, tzinfo=UTC)


NOTE_A = "12.03.2024 - Näidisamet saatis kooskõlastusringile näidiseelnõu"
NOTE_B = "20.03.2024 - Koda saatis arvamuse näidisametile"
NA_DATED = "14.04.2024 seisuga ootab näidisteema edasist menetlust"
BLOCKS = [
    text(0, NOTE_A),
    file_block(1, "otse", depth=2, parent=0),
    file_block(2, "kõrval"),
    blank(3),
    text(4, NOTE_B),
    blank(5),
    text(6, "Järgmised tegevused:"),
    text(7, "Ootame näidisvastust 30.04.", depth=2, parent=6),
    text(8, NA_DATED, depth=2, parent=6),
]
FILES = [
    ("otse", "Otsene-näidisfail.pdf", _utc(2024, 3, 12, 9, 0)),
    ("kõrval", "Kõrvalfail-näidis.pdf", _utc(2024, 3, 13, 9, 0)),
]


def _page_html(client, matter, **params):
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    return client.get(url, params).content.decode()


def _fragment_html(client, matter, offset=0):
    url = reverse("matters:timeline_page", kwargs={"pk": matter.pk})
    return client.get(url, {"nihe": offset}).content.decode()


# ---------------------------------------------------------------------------
# Authorization on every channel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["administrator", "no_role"])
def test_no_channel_shows_a_note_or_file_to_an_unauthorised_account(client, normal_matter, who):
    _page, made = make_page([normal_matter], BLOCKS, FILES)
    link, _versions = made[normal_matter.pk]
    # An administrator holds no business read (docs/adr/0181); an account with
    # no role at all is the other outsider the scope must refuse.
    user = (
        factories.AdministratorFactory()
        if who == "administrator"
        else factories.UserFactory(role="")
    )
    client.force_login(user)
    probes = {
        "teema": reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}),
        "fragment": reverse("matters:timeline_page", kwargs={"pk": normal_matter.pk}),
        "deep": reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
        + "?kirje=onenote-note:x",
        "whole": reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}) + "?kaik=koik",
        "ajalugu": reverse("legacy_import:source_page", kwargs={"pk": link.pk}),
    }
    for name, url in probes.items():
        response = client.get(url)
        body = response.content.decode(errors="replace")
        assert response.status_code in (403, 404) or (
            "Näidisamet saatis" not in body and "Otsene-näidisfail" not in body
        ), name


def test_every_html_channel_shows_the_note_to_a_reader_and_the_same_once(client, normal_matter):
    _page, made = make_page([normal_matter], BLOCKS, FILES)
    link, _versions = made[normal_matter.pk]
    client.force_login(factories.ReaderFactory())
    for html in (
        _page_html(client, normal_matter),
        _page_html(client, normal_matter, kaik="koik"),
        _fragment_html(client, normal_matter),
    ):
        # Two notes, one dated NA line and one document row.
        assert html.count('class="uxtl__histline"') == 4
        assert html.count('data-kaik-rida="onenote-note:') == 3
        # Short enough to be its own headline: once in the line, once in the panel.
        assert html.count("Näidisamet saatis kooskõlastusringile näidiseelnõu") == 2
        # The direct file once under its note, the other once on its own row.
        assert html.count(">Otsene-näidisfail.pdf</a>") == 1
        assert html.count(">Kõrvalfail-näidis.pdf</a>") == 1
        assert f"/ajalugu/{link.pk}/" in html


def test_a_restricted_document_is_absent_from_every_channel_and_every_count(
    client, normal_matter, narrow_reader
):
    make_page([normal_matter], BLOCKS, FILES, restricted=("otse", "kõrval"))
    client.force_login(narrow_reader)
    for html in (_page_html(client, normal_matter), _fragment_html(client, normal_matter)):
        assert "Näidisamet saatis" in html  # the Teema is readable
        assert "Otsene-näidisfail" not in html
        assert "Kõrvalfail-näidis" not in html
        assert "dokumenti</span>" not in html and ">Dokument</span>" not in html
        assert "Sama OneNote&#x27;i lehe failid" not in html
        assert "Sama OneNote'i lehe failid" not in html


def test_a_shared_page_reads_through_the_readers_own_link_and_never_the_others(
    client, specialist, narrow_reader
):
    from app.core.enums import Visibility

    open_matter = factories.MatterFactory(owner=specialist)
    closed_matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    _page, made = make_page([open_matter, closed_matter], BLOCKS, FILES)
    open_link, _ = made[open_matter.pk]
    closed_link, _ = made[closed_matter.pk]
    client.force_login(narrow_reader)
    html = _page_html(client, open_matter)
    assert f"/ajalugu/{open_link.pk}/" in html
    assert str(closed_link.pk) not in html
    assert str(closed_matter.pk) not in html
    assert client.get(
        reverse("matters:matter_detail", kwargs={"pk": closed_matter.pk})
    ).status_code in (403, 404)
    response = client.get(reverse("legacy_import:source_page", kwargs={"pk": closed_link.pk}))
    assert response.status_code == 404


def test_a_deleted_teema_on_a_shared_page_leaks_nothing_into_the_live_one(client, specialist):
    live = factories.MatterFactory(owner=specialist)
    gone = factories.MatterFactory(owner=specialist)
    _page, made = make_page([live, gone], BLOCKS, FILES)
    gone_link, _ = made[gone.pk]
    type(gone).objects.filter(pk=gone.pk).update(deleted_at=timezone.now())
    client.force_login(specialist)
    html = _page_html(client, live)
    assert html.count('data-kaik-rida="onenote-note:') == 3
    assert html.count("Näidisamet saatis kooskõlastusringile näidiseelnõu") == 2
    assert str(gone_link.pk) not in html
    response = client.get(reverse("legacy_import:source_page", kwargs={"pk": gone_link.pk}))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

HOSTILE = "<script>alert(1)</script>"


def test_hostile_text_titles_filenames_comments_and_links_are_inert(client, normal_matter):
    from app.documents.models import Document

    blocks = [
        text(0, f"12.03.2024 - {HOSTILE} &lt;img src=x onerror=alert(2)&gt; javascript:alert(3)"),
        file_block(1, "k1", depth=2, parent=0),
        text(
            2,
            'Vaata https://naidis.invalid/a"onmouseover="alert(4) ja onenote:///x',
            depth=2,
            parent=0,
        ),
        text(3, "javascript:alert(5)//https://naidis.invalid", depth=2, parent=0),
    ]
    make_page(
        [normal_matter],
        blocks,
        [("k1", f"{HOSTILE}.pdf", _utc(2024, 3, 12, 9))],
        title=f"Leht {HOSTILE}",
    )
    Document.objects.filter(matter=normal_matter).update(
        title=f"Pealkiri {HOSTILE}",
        provenance_note=f"Märkus OneNote'is: {HOSTILE}\nAllikas: x, plokk 1",
    )
    client.force_login(normal_matter.owner)
    for html in (_page_html(client, normal_matter), _fragment_html(client, normal_matter)):
        assert "<script>alert" not in html
        assert "<img src=x" not in html
        assert 'href="javascript:' not in html.lower()
        assert 'href="onenote:' not in html.lower()
        assert 'onmouseover="alert' not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_legacy_pages_own_link_records_never_hand_a_script_scheme_to_the_browser(
    client, normal_matter
):
    blocks = [
        {
            "kind": "TEXT",
            "text": "12.03.2024 - Vt lingid",
            "depth": 0,
            "ordinal": 0,
            "resource_key": "",
        },
        {"kind": "TEXT", "text": "siin ja seal", "depth": 1, "ordinal": 1, "resource_key": ""},
    ]
    links = [
        {"blockOrdinal": 1, "href": "javascript:alert(6)", "text": "siin"},
        {"blockOrdinal": 1, "href": "onenote:///Naidis.one#x", "text": "seal"},
        {"blockOrdinal": 1, "href": " JaVaScRiPt:alert(7)", "text": "kolm"},
        {"blockOrdinal": 1, "href": "https://naidis.invalid/ok", "text": "<b>ok</b>"},
    ]
    make_page([normal_matter], blocks, (), links=links)
    client.force_login(normal_matter.owner)
    html = _page_html(client, normal_matter)
    assert "Vt lingid" in html
    assert "javascript:" not in html.lower()
    assert 'href="onenote:' not in html.lower()
    assert "onenote:///" not in html.lower()
    assert 'href="https://naidis.invalid/ok"' in html
    assert "<b>ok</b>" not in html


# ---------------------------------------------------------------------------
# Sorting, paging, stability
# ---------------------------------------------------------------------------


def test_many_notes_on_one_day_across_two_pages_page_walk_is_exact_and_stable(specialist):
    matter = factories.MatterFactory(owner=specialist)
    for page_no in range(2):
        blocks = []
        for i in range(25):
            blocks.append(text(2 * i, f"05.05.2024 - Leht {page_no} märge {i:02d}"))
            blocks.append(blank(2 * i + 1))
        make_page([matter], blocks, title=f"Näidisleht {page_no}")
    for i in range(5):
        factories.EntryFactory(
            matter=matter, author=specialist, occurred_at=_utc(2024, 5, 5, 8 + i)
        )
    whole, _more = matter_timeline(matter=matter, user=specialist, limit=500)
    keys = [item.row_key for item in whole]
    notes = [item for item in whole if item.item_type == HISTORICAL_NOTE_ITEM]
    assert len(notes) == 50
    assert len(keys) == len(set(keys))
    again, _ = matter_timeline(matter=matter, user=specialist, limit=500)
    assert [item.row_key for item in again] == keys
    for size in (1, 3, 7, 30):
        walked, offset = [], 0
        while True:
            items, more = matter_timeline(matter=matter, user=specialist, limit=size, offset=offset)
            walked.extend(item.row_key for item in items)
            if not more:
                break
            offset += size
        assert walked == keys, size


def test_notes_sort_by_their_date_not_by_page_or_block_order(specialist):
    matter = factories.MatterFactory(owner=specialist)
    make_page(
        [matter],
        [text(0, "01.02.2024 - hilisem"), blank(1), text(2, "01.01.2024 - varasem")],
    )
    make_page([matter], [text(0, "15.01.2024 - keskmine teiselt lehelt")])
    items, _ = matter_timeline(matter=matter, user=specialist, limit=50)
    heads = [i.historical_note.headline for i in items if i.item_type == HISTORICAL_NOTE_ITEM]
    assert heads == ["hilisem", "keskmine teiselt lehelt", "varasem"]


# ---------------------------------------------------------------------------
# «Järgmised tegevused» and future dates
# ---------------------------------------------------------------------------


def test_next_actions_text_reaches_no_current_task_surface(client, normal_matter):
    from app.workflow.models import NextAction

    make_page([normal_matter], BLOCKS, FILES)
    client.force_login(normal_matter.owner)
    html = _page_html(client, normal_matter)
    assert NextAction.objects.filter(matter=normal_matter).count() == 0
    # The undated NA prose is no row and nowhere on the Teema page.
    assert "Ootame näidisvastust" not in html
    # The dated NA line appears exactly once, inside its labelled historical row.
    assert html.count("ootab näidisteema edasist menetlust") == 2  # line + panel
    row = re.search(
        r"<article[^>]*uxtl__item--historical[^>]*>(?:(?!</article>).)*ootab näidisteema",
        html,
        re.S,
    )
    assert row is not None and "Ajalooline: Järgmised tegevused" in row.group(0)


def test_a_note_dated_ahead_of_today_is_marked_eesolev_on_the_page(client, normal_matter):
    """The chronology marks every future-dated milestone «Eesolev»; a historical
    note written ahead (R7 lets 45 days through) carries `milestone.ahead` but
    the note template never prints it, so a planned reading reads as done."""
    today = timezone.localdate()
    ahead = today.replace(day=1) if today.day > 20 else today
    future = date.fromordinal(ahead.toordinal() + 10)
    block = text(0, f"{future.day:02d}.{future.month:02d}.{future.year} - kolmas lugemine")
    stamp = f"{today.isoformat()}T08:00:00.000Z"
    block["created"] = block["modified"] = stamp
    make_page([normal_matter], [block])
    rows, _ = matter_timeline(matter=normal_matter, user=normal_matter.owner, limit=50)
    (note,) = [r for r in rows if r.item_type == HISTORICAL_NOTE_ITEM]
    assert note.milestone.ahead is True
    client.force_login(normal_matter.owner)
    html = _page_html(client, normal_matter)
    row = re.search(r"<article[^>]*uxtl__item--historical[^>]*>.*?</article>", html, re.S)
    assert row is not None
    assert "Eesolev" in row.group(0)


# ---------------------------------------------------------------------------
# No writes
# ---------------------------------------------------------------------------


def _row_counts():
    return {
        model._meta.label: model._default_manager.count()
        for model in apps.get_models()
        if model._meta.managed and not model._meta.proxy
    }


def test_no_channel_writes_any_row(client, normal_matter):
    from app.mcp_server import readers

    make_page([normal_matter], BLOCKS, FILES)
    client.force_login(normal_matter.owner)
    _page_html(client, normal_matter)  # warm sessions etc.
    before = _row_counts()
    _page_html(client, normal_matter)
    _page_html(client, normal_matter, kaik="koik")
    _fragment_html(client, normal_matter)
    readers.matter_activity(normal_matter.owner, str(normal_matter.pk))
    after = _row_counts()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    # Session/access-log style tables may tick; no business table may.
    business = {
        k: v
        for k, v in changed.items()
        if k.split(".")[0]
        in {
            "matters",
            "documents",
            "workflow",
            "submissions",
            "audit",
            "legacy_import",
            "intelligence",
        }
    }
    assert business == {}


# ---------------------------------------------------------------------------
# MCP: the notes now reach get_matter_activity
# ---------------------------------------------------------------------------


def test_mcp_activity_does_not_claim_exact_precision_for_a_month_note(normal_matter):
    """A «09.2024» note is placed on 01.09.2024 for sorting only; MCP must not
    report it as an EXACT date of 2024-09-01 (manufactured certainty)."""
    from app.mcp_server import readers

    block = text(0, "09.2024 seisuga ei ole näidisteema edasi liikunud")
    block["created"] = block["modified"] = "2024-09-20T08:00:00.000Z"
    make_page([normal_matter], [block])
    answer = readers.matter_activity(normal_matter.owner, str(normal_matter.pk))
    (item,) = [i for i in answer["items"] if i.get("item_type") == HISTORICAL_NOTE_ITEM]
    assert item["date"]["precision"] != "EXACT"
    assert item["date"]["date"] != "2024-09-01" or item["date"]["precision"] in ("MONTH", "KUU")


def test_mcp_activity_labels_a_dated_next_actions_line_as_historical(normal_matter):
    from app.mcp_server import readers

    make_page([normal_matter], BLOCKS, FILES)
    answer = readers.matter_activity(normal_matter.owner, str(normal_matter.pk))
    (item,) = [
        i for i in answer["items"] if "ootab näidisteema edasist menetlust" in (i.get("text") or "")
    ]
    assert "Ajalooline" in item["text"]


def test_mcp_activity_still_names_the_document_rows_files(normal_matter):
    from app.mcp_server import readers

    make_page([normal_matter], BLOCKS, FILES)
    answer = readers.matter_activity(normal_matter.owner, str(normal_matter.pk))
    texts = " ".join(i.get("text") or "" for i in answer["items"])
    assert "Kõrvalfail-näidis.pdf" in texts
    imported = [i for i in answer["items"] if i.get("item_type") == HISTORICAL_IMPORT_ITEM]
    assert len(imported) == 1


def test_mcp_activity_carries_the_full_note_or_says_it_was_cut(normal_matter):
    """MCP sees only the ≤65-character headline of a long note, and
    `text_truncated` is False: the reader is told it has the whole row."""
    from app.mcp_server import readers

    long_note = "12.03.2024 - " + " ".join(f"sõna{i}" for i in range(60))
    make_page([normal_matter], [text(0, long_note), text(1, "teine lõik samas märkes")])
    answer = readers.matter_activity(normal_matter.owner, str(normal_matter.pk))
    (item,) = [i for i in answer["items"] if i.get("item_type") == HISTORICAL_NOTE_ITEM]
    assert "sõna59" in item["text"] or item["text_truncated"] is True
