"""How imported OneNote material reads (docs/adr/0206), against invented captures only.

The Phase 2 acceptance found the data right and three screens misleading:
held files read «Kopeerimine ebaõnnestus» (ONENOTE-UI-003), the Dokumendid list
dated every OneNote file by its import day (ONENOTE-UI-001), and «Allikas» kept
saying the attachments were not imported (ONENOTE-UI-002). These tests fix what
each screen says, from the import's own records, and that nothing an ordinary
upload shows has moved.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.documents.models import Document
from app.legacy_import import onenote_capture as oc
from app.legacy_import.historical_views import (
    _file_state,
    historical_summary,
    import_statuses,
    teema_import_status,
)
from app.legacy_import.source_pages import (
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
)
from tests import factories
from tests.test_onenote_capture_import import (
    _all,
    _apply,
    _file,
    _guid,
    build_capture,
    register_teema,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def world(tmp_path, evidence_root):
    from tests.test_onenote_capture_import import T1, T2, T3

    a = register_teema("2026_1")
    b = register_teema("2026_2")
    c, d = register_teema("2026_3"), register_teema("2026_4")
    e = register_teema("2026_5")
    pages = [
        {
            "tag": "001",
            "guid": _guid(1),
            "matters": [a],
            "files": [
                _file(1, "kiri.pdf", T1),
                _file(2, "vastus.docx", T2),
                _file(3, "lisa.pdf", T3),
                _file(4, "lõuendil.pdf", None, floating=True, modified="2025-10-15T12:51:18.000Z"),
                _file(5, "tühi.txt", T1, data=b""),
            ],
        },
        {"tag": "002", "guid": _guid(2), "matters": [b], "files": [_file(1, "b.pdf", T1)]},
        {"tag": "003", "guid": _guid(3), "matters": [c, d], "files": [_file(1, "jagatud.pdf", T2)]},
        {"tag": "004", "guid": _guid(4), "matters": [e], "files": []},
    ]
    root = build_capture(tmp_path / "cap", pages)
    return {"root": root, "a": a, "b": b, "c": c, "d": d, "e": e}


def _import_all(world):
    plan = oc.build_plan(oc.load_capture(world["root"]))
    _apply(plan, _all(plan))
    _apply(plan, _all(plan, include_shared=True))


def _page_url(matter):
    link = MatterSourcePage.objects.get(matter=matter)
    return reverse("legacy_import:source_page", kwargs={"pk": link.pk})


def _documents(client, matter, **params):
    return client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}), params)


# -- ONENOTE-UI-003: what the page says about each file ---------------------------------


def _record(state, code="", *, document_id=None):
    return SimpleNamespace(state=state, error_code=code, document_id=document_id)


@pytest.mark.parametrize(
    ("record", "size", "expected"),
    [
        (None, 10, "pending"),
        (_record(ResourceImportState.IMPORTED, document_id=1), 10, "imported"),
        (_record(ResourceImportState.SKIPPED, oc.HOLD_SHARED_PAGE), 10, "held_shared"),
        (_record(ResourceImportState.SKIPPED, oc.HOLD_EMPTY_FILE), 0, "empty"),
        (_record(ResourceImportState.SKIPPED, oc.HOLD_TOO_LARGE), 10, "held_too_large"),
        (_record(ResourceImportState.SKIPPED, oc.HOLD_EARLIER_CAPTURE), 10, "superseded"),
        (_record(ResourceImportState.SKIPPED, "RECORD_HELD_FOR_REVIEW"), 10, "held_review"),
        (_record(ResourceImportState.SKIPPED, "NOT_IN_REVIEWED_IMPORT"), 10, "not_imported"),
        (_record(ResourceImportState.FAILED, "ValidationError"), 0, "empty"),
        (_record(ResourceImportState.FAILED, "OSError"), 10, "unavailable"),
    ],
)
def test_every_import_outcome_has_its_own_state(record, size, expected):
    assert _file_state(SimpleNamespace(size_bytes=size), record) == expected


def test_a_held_shared_page_file_reads_as_waiting_never_as_a_failed_copy(
    world, enabled, client, specialist
):
    _import_all(world)
    client.force_login(specialist)
    for teema in (world["c"], world["d"]):
        html = client.get(_page_url(teema)).content.decode()
        assert "Ootab teemaga seostamist" in html
        assert "Kopeerimine ebaõnnestus" not in html
        assert "jagatud.pdf" in html and "Originaal on lähtekorpuses alles" in html
        # Held means held: still no Document, no download, the row and the source kept.
        row = LegacySourceResourceImport.objects.get(matter_source_page__matter=teema)
        assert row.state == ResourceImportState.SKIPPED and row.document_id is None
        assert not Document.objects.filter(matter=teema).exists()
    assert LegacySourceResource.objects.filter(original_filename="jagatud.pdf").count() == 1


def test_only_a_copy_that_actually_failed_reads_as_failed(world, enabled, client, specialist):
    _import_all(world)
    row = LegacySourceResourceImport.objects.get(
        matter_source_page__matter=world["b"], state=ResourceImportState.IMPORTED
    )
    # An invented failure on a disposable test row: the page must say so.
    LegacySourceResourceImport.objects.filter(pk=row.pk).update(
        state=ResourceImportState.FAILED, error_code="OSError", document=None, document_version=None
    )
    client.force_login(specialist)
    html = client.get(_page_url(world["b"])).content.decode()
    assert "Kopeerimine ebaõnnestus" in html and "Ootab teemaga seostamist" not in html


def test_the_empty_file_and_the_imported_files_keep_their_states(
    world, enabled, client, specialist
):
    _import_all(world)
    client.force_login(specialist)
    html = client.get(_page_url(world["a"])).content.decode()
    assert "OneNote'is oli see fail tühi" in html
    assert "Kopeerimine ebaõnnestus" not in html and "Ootab teemaga seostamist" not in html
    assert html.count("Päritolu</a>") == 4


def test_a_file_this_reader_may_not_read_is_withheld_and_the_page_is_scoped(
    world, enabled, client, specialist
):
    _import_all(world)
    document = Document.objects.get(matter=world["a"], title="kiri.pdf")
    Document.objects.filter(pk=document.pk).update(removed_at=timezone.now())
    client.force_login(specialist)
    html = client.get(_page_url(world["a"])).content.decode()
    assert "Fail, mida selles kasutajavaates ei kuvata" in html and "kiri.pdf" not in html
    # The page and its Teema's files are read through the Matter's scope (docs/adr/0181).
    client.force_login(factories.AdministratorFactory())
    assert client.get(_page_url(world["c"])).status_code == 404
    assert _documents(client, world["c"]).status_code == 404


# -- ONENOTE-UI-002: the page's and the Teema's import status ---------------------------


def _statuses(matter, _user):
    links = list(MatterSourcePage.objects.filter(matter=matter).select_related("source_page"))
    return list(import_statuses(links).values())


def test_each_kind_of_page_gets_the_status_its_records_say(world, enabled, specialist):
    _import_all(world)
    (full,) = _statuses(world["a"], specialist)
    assert full.label == "Kõik 4 faili imporditud · 1 fail oli OneNote'is tühi" and full.tone == ""
    (single,) = _statuses(world["b"], specialist)
    assert single.label == "Fail imporditud"
    for teema in (world["c"], world["d"]):
        (shared,) = _statuses(teema, specialist)
        assert shared.label == "1 fail ootab teemaga seostamist" and shared.tone == "waiting"
    (empty_page,) = _statuses(world["e"], specialist)
    assert empty_page.label == "Lehel ei ole faile" and empty_page.files == 0


def test_a_partly_imported_or_failed_page_says_so(world, enabled, specialist):
    _import_all(world)
    link = MatterSourcePage.objects.get(matter=world["a"])
    rows = list(link.resource_imports.filter(state=ResourceImportState.IMPORTED).order_by("pk"))
    rows[0].delete()  # a decision never recorded: the file is still waiting
    LegacySourceResourceImport.objects.filter(pk=rows[1].pk).update(
        state=ResourceImportState.FAILED, error_code="OSError", document=None, document_version=None
    )
    (status,) = _statuses(world["a"], specialist)
    assert status.label == (
        "2 faili imporditud · 1 fail oli OneNote'is tühi · 1 fail on veel importimata"
        " · 1 fail kopeerimine ebaõnnestus"
    )
    assert status.tone == "problem"
    sentence = teema_import_status([status])
    assert sentence["lines"][0].startswith("Lehe sisu on Juristides. Failid: 2 faili imporditud")


def test_the_status_is_not_inferred_from_the_page_row(world, enabled, specialist):
    _import_all(world)
    LegacySourceResourceImport.objects.filter(matter_source_page__matter=world["b"]).delete()
    (status,) = _statuses(world["b"], specialist)
    assert status.label == "1 fail on veel importimata" and status.tone == "waiting"


def test_allikas_says_what_was_imported_and_keeps_the_old_sentence_without_a_page(
    world, enabled, client, specialist, monkeypatch
):
    from app.matters import views

    _import_all(world)
    monkeypatch.setattr(
        views, "_lovable_source_links", lambda matter: [{"url": "https://tenant.invalid/onenote"}]
    )
    client.force_login(specialist)
    full = _documents(client, world["a"]).content.decode()
    assert "Lehe sisu ja kõik 4 faili on Juristidesse imporditud" in full
    assert "1 fail oli OneNote&#x27;is tühi ja jäi tõendina loomata." in full
    assert "ei ole Juristidesse imporditud" not in full
    assert "Kõik 4 faili imporditud" in full  # the page row's chip
    shared = _documents(client, world["c"]).content.decode()
    assert "1 fail ootab teemaga seostamist" in shared
    assert "faili kuuluvuse otsustab inimene" in shared
    assert "ei ole Juristidesse imporditud" not in shared
    no_files = _documents(client, world["e"]).content.decode()
    assert "Lehe sisu on Juristides; lehel ei ole faile." in no_files
    never = register_teema("2026_9")
    untouched = _documents(client, never).content.decode()
    assert "Lehe sisu ja manuseid ei ole Juristidesse imporditud" in untouched


def test_the_summary_of_a_teema_without_history_is_still_empty(world, specialist):
    assert historical_summary(world["a"], specialist) == {}


# -- ONENOTE-UI-001: the historical date in the list and on the document ----------------

#: One OneNote file per year 2020–2026, given out of order. The 2021 one is
#: 31.12.2020 22:30 UTC — 1.1.2021 in Tallinn — so a UTC year would misfile it.
YEARS = [
    ("y2023.pdf", "2023-06-15T09:00:00.000Z", "15.6.2023"),
    ("y2020.pdf", "2020-03-02T08:00:00.000Z", "2.3.2020"),
    ("y2026.pdf", "2026-02-10T10:00:00.000Z", "10.2.2026"),
    ("y2021.pdf", "2020-12-31T22:30:00.000Z", "1.1.2021"),
    ("y2025.pdf", "2025-11-30T12:00:00.000Z", "30.11.2025"),
    ("y2022.pdf", "2022-07-01T21:15:00.000Z", "2.7.2022"),
    ("y2024.pdf", "2024-02-29T11:00:00.000Z", "29.2.2024"),
]


@pytest.fixture
def years(tmp_path, evidence_root, enabled):
    teema = register_teema("2026_7")
    files = [_file(n, name, when) for n, (name, when, _day) in enumerate(YEARS, start=1)]
    files.append(_file(len(files) + 1, "teadmata.pdf", None, floating=True))
    root = build_capture(
        tmp_path / "years", [{"tag": "001", "guid": _guid(7), "matters": [teema], "files": files}]
    )
    plan = oc.build_plan(oc.load_capture(root))
    _apply(plan, _all(plan))
    return teema


def _rows(html):
    """(filename, printed date cell) for each evidence row, in page order; [] without a table."""
    if 'class="table doctable"' not in html:
        return []
    body = html.split('class="table doctable"', 1)[1].split("</table>", 1)[0]
    out = []
    for row in body.split("<tr")[2:]:
        name = re.search(r">\s*([^<>\s][^<>]*?\.(?:pdf|docx|txt))\s*<", row)
        cell = row.split('class="table__date">', 1)[1].split("</td>", 1)[0]
        text = " ".join(re.sub(r"<[^>]+>|\{%.*?%\}", " ", cell).split())
        out.append((name.group(1) if name else "?", text))
    return out


def test_onenote_files_read_and_sort_on_their_onenote_day_across_years(
    years, client, specialist, capture_evidence
):
    capture_evidence(years, b"tavaline", "tavaline.pdf", "application/pdf", uploaded_by=specialist)
    client.force_login(specialist)
    html = _documents(client, years, koik="1").content.decode()
    rows = _rows(html)
    by_date = sorted(YEARS, key=lambda y: y[1], reverse=True)
    assert [name for name, _ in rows] == (
        ["tavaline.pdf"] + [name for name, _w, _d in by_date] + ["teadmata.pdf"]
    )
    cells = dict(rows)
    for name, _when, day in YEARS:
        assert cells[name] == f"{day} lisatud OneNote'i"
    assert cells["teadmata.pdf"] == "Lisamise kuupäev teadmata"
    today = timezone.localtime(timezone.now())
    assert cells["tavaline.pdf"] == f"{today.day}.{today.month}.{today.year}"
    # The two kinds of date are explained once, above the table.
    assert "OneNote'ist imporditud failide kuupäev on päev, mil fail OneNote'i lisati" in html
    # The import day appears nowhere as a OneNote file's date.
    assert "5.11.2026 lisatud" not in html


def test_the_year_filter_offers_the_onenote_years_and_the_unknown_ones(years, client, specialist):
    client.force_login(specialist)
    html = _documents(client, years).content.decode()
    select = html.split('name="aasta"', 1)[1].split("</select>", 1)[0]
    offered = re.findall(r'<option value="([^"]*)"', select)
    assert offered == ["", "2026", "2025", "2024", "2023", "2022", "2021", "2020", "teadmata"]
    assert [n for n, _ in _rows(_documents(client, years, aasta="2021").content.decode())] == [
        "y2021.pdf"
    ]
    assert [n for n, _ in _rows(_documents(client, years, aasta="2020").content.decode())] == [
        "y2020.pdf"
    ]
    assert [n for n, _ in _rows(_documents(client, years, aasta="teadmata").content.decode())] == [
        "teadmata.pdf"
    ]


def test_an_ordinary_teema_lists_exactly_as_before(client, specialist, capture_evidence):
    matter = factories.MatterFactory()
    first = capture_evidence(matter, b"esimene", "esimene.pdf", "application/pdf")
    second = capture_evidence(matter, b"teine", "teine.pdf", "application/pdf")
    Document.objects.filter(pk=first.document_id).update(
        created_at=timezone.now() - timezone.timedelta(days=400)
    )
    client.force_login(specialist)
    html = _documents(client, matter).content.decode()
    rows = _rows(html)
    assert [n for n, _ in rows] == ["teine.pdf", "esimene.pdf"]  # newest upload first
    assert all("lisatud OneNote" not in cell for _n, cell in rows)
    assert "OneNote'ist imporditud failide kuupäev" not in html
    older = Document.objects.get(pk=first.document_id).created_at.year
    assert second.document.created_at.year in {int(y) for y in re.findall(r'value="(\d{4})"', html)}
    assert str(older) in html


def test_the_document_page_states_the_onenote_day_beside_the_copy_time(years, client, specialist):
    client.force_login(specialist)
    dated = Document.objects.get(matter=years, title="y2021.pdf")
    html = client.get(
        reverse("documents:document_detail", kwargs={"pk": dated.pk})
    ).content.decode()
    card = html.split('id="originaal-heading"', 1)[1].split("Dokumendi andmed", 1)[0]
    assert "Lisatud OneNote'i</dt><dd>1.1.2021 00:30" in card
    assert "kopeeriti OneNote'ist" in card
    undated = Document.objects.get(matter=years, title="teadmata.pdf")
    html = client.get(
        reverse("documents:document_detail", kwargs={"pk": undated.pk})
    ).content.decode()
    card = html.split('id="originaal-heading"', 1)[1].split("Dokumendi andmed", 1)[0]
    assert "Lisamise kuupäev teadmata" in card


# -- verify: bounded memory, nothing checked dropped ------------------------------------


def test_verify_streams_its_rows_without_the_pages_text(world, enabled):
    _import_all(world)
    capture = oc.load_capture(world["root"])
    with CaptureQueriesContext(connection) as queries:
        result = oc.verify(capture, check_bytes=True, require_complete=True)
    assert result["problems"] == []
    assert result["counts"]["documents"] == 5 and result["counts"]["bytes_rehashed"] == 5
    assert result["counts"]["held_SHARED_PAGE_AWAITING_REVIEW"] == 2
    rows_query = next(
        q["sql"] for q in queries.captured_queries if "legacysourceresourceimport" in q["sql"]
    )
    for heavy in ('"derived_text"', '"blocks"', '"provenance_note"'):
        assert heavy not in rows_query


def test_verify_still_finds_a_document_moved_to_another_teema(world, enabled):
    _import_all(world)
    capture = oc.load_capture(world["root"])
    moved = Document.objects.filter(matter=world["a"]).first()
    Document.objects.filter(pk=moved.pk).update(matter=world["b"])
    assert any("document on another Teema" in p for p in oc.verify(capture)["problems"])


# -- the date rule at its edges: DST, pagination, ties, conflicts, Tegevused ------------

#: Europe/Tallinn summer time ends 25.10.2026 04:00 local (01:00 UTC) and starts
#: 29.03.2026 03:00 local (01:00 UTC). Each instant is given with its Tallinn day.
DST = [
    ("enne-keskööd.pdf", "2026-10-24T20:59:00.000Z", "24.10.2026"),  # 23:59 EEST
    ("pärast-keskööd.pdf", "2026-10-24T21:30:00.000Z", "25.10.2026"),  # 00:30 EEST
    ("talveaeg.pdf", "2026-10-25T22:30:00.000Z", "26.10.2026"),  # 00:30 EET
    ("kevad-kesköö.pdf", "2026-03-28T22:30:00.000Z", "29.3.2026"),  # 00:30 EET
    ("kevad-enne.pdf", "2026-03-28T21:59:00.000Z", "28.3.2026"),  # 23:59 EET
]


@pytest.fixture
def dst(tmp_path, evidence_root, enabled):
    teema = register_teema("2026_8")
    files = [_file(n, name, when) for n, (name, when, _day) in enumerate(DST, start=1)]
    root = build_capture(
        tmp_path / "dst", [{"tag": "001", "guid": _guid(8), "matters": [teema], "files": files}]
    )
    plan = oc.build_plan(oc.load_capture(root))
    _apply(plan, _all(plan))
    return teema


def test_tallinn_midnight_and_dst_decide_the_day_in_both_views(dst, client, specialist):
    from app.matters.timeline import HISTORICAL_IMPORT_ITEM, matter_timeline

    client.force_login(specialist)
    cells = dict(_rows(_documents(client, dst, koik="1").content.decode()))
    for name, _when, day in DST:
        assert cells[name] == f"{day} lisatud OneNote'i"
    # Tegevused files each document under the same Tallinn day as Dokumendid does.
    # The same day, not the same spelling: the chronology's historical rows print
    # it zero-padded since 2026-11-05 («28.03.2026»), Dokumendid as «28.3.2026».
    from app.core.dates import parse_estonian_date

    rows, _more = matter_timeline(matter=dst, user=specialist)
    days = sorted(
        parse_estonian_date(r.milestone.display_date)
        for r in rows
        if r.item_type == HISTORICAL_IMPORT_ITEM
    )
    assert days == sorted(parse_estonian_date(day) for _n, _w, day in DST)


@pytest.fixture
def many(tmp_path, evidence_root, enabled):
    teema = register_teema("2026_9")
    files = [_file(n, f"fail-{n:02}.pdf", f"2021-01-{n:02}T10:00:00.000Z") for n in range(1, 15)]
    files.append(_file(15, "sama-päev-a.pdf", "2021-01-14T10:00:00.000Z"))
    files.append(_file(16, "teadmata.pdf", None, floating=True))
    root = build_capture(
        tmp_path / "many", [{"tag": "001", "guid": _guid(9), "matters": [teema], "files": files}]
    )
    plan = oc.build_plan(oc.load_capture(root))
    _apply(plan, _all(plan))
    return teema


def test_the_first_page_holds_the_newest_onenote_days_and_ties_stay_put(many, client, specialist):
    client.force_login(specialist)
    first = [n for n, _ in _rows(_documents(client, many).content.decode())]
    assert len(first) == 12  # DOCUMENT_PAGE_SIZE
    assert "teadmata.pdf" not in first  # undated files come last, after every dated one
    assert set(first[:2]) == {"fail-14.pdf", "sama-päev-a.pdf"}  # the same day, side by side
    full = [n for n, _ in _rows(_documents(client, many, koik="1").content.decode())]
    assert full[:12] == first and full[-1] == "teadmata.pdf" and len(full) == 16
    again = [n for n, _ in _rows(_documents(client, many, koik="1").content.decode())]
    assert again == full  # a tie never moves between two loads


def test_the_list_reads_each_page_with_a_bounded_number_of_queries(many, client, specialist):
    client.force_login(specialist)
    _documents(client, many, koik="1")  # warm per-request caches
    with CaptureQueriesContext(connection) as small:
        _documents(client, many)
    with CaptureQueriesContext(connection) as full:
        _documents(client, many, koik="1")
    # Twelve rows or sixteen: the same queries, never one per document.
    assert len(full.captured_queries) <= len(small.captured_queries) + 1


def test_conflicting_source_dates_read_as_unknown_and_say_why(years, client, specialist):
    from app.legacy_import.source_pages import LegacySourceResource

    document = Document.objects.get(matter=years, title="y2022.pdf")
    row = LegacySourceResourceImport.objects.get(document=document)
    # An invented second import row naming the same file with another addition time.
    source = row.resource
    other = LegacySourceResource.objects.create(
        source_page=source.source_page,
        resource_key="invented-conflict",
        original_filename=source.original_filename,
        resource_kind=source.resource_kind,
        sha256=source.sha256,
        size_bytes=source.size_bytes,
        archive_relative_path="invented",
        source_added_at=source.source_added_at - timezone.timedelta(days=400),
        source_added_basis=source.source_added_basis,
    )
    LegacySourceResourceImport.objects.create(
        matter_source_page=row.matter_source_page,
        resource=other,
        document=document,
        document_version=row.document_version,
        import_batch=row.import_batch,
        state=ResourceImportState.IMPORTED,
    )
    client.force_login(specialist)
    cells = dict(_rows(_documents(client, years, koik="1").content.decode()))
    assert cells["y2022.pdf"] == "Lisamise kuupäev teadmata (allikad on vastuolus)"
    unknown = [n for n, _ in _rows(_documents(client, years, aasta="teadmata").content.decode())]
    assert "y2022.pdf" in unknown and "y2022.pdf" not in [
        n for n, _ in _rows(_documents(client, years, aasta="2022").content.decode())
    ]
