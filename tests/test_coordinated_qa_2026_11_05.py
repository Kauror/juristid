"""Independent QA of the 2026-11-05 OneNote follow-up (workstream E), invented data only.

Written from the rules in docs/adr/0204–0208 and the 0205 amendment, not from the
rendered output. Each section names the rule it checks:

1. Dokumendid dates (0206 §3): the Tallinn OneNote day, undated last and only
   under ``aasta=teadmata``, conflicts unknown, uploads unchanged, never the
   import or copy day, the page boundary and a query bound.
2. File and page statuses (0206 §1–2): every import outcome to one state, only
   FAILED a failure, the page/Teema counts equal the import rows.
3. Continuations (0207): chains from every stage, deleted targets, cycles,
   truncation, outsiders, MCP, no redirect.
4. The held-original download (0208 §3): the gate, the seal, the blob, the
   answer, the audit, and that nothing else is written.
5. Exact page-GUID resolution (0204, D5).
6. A later capture (0205 amendment).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from itertools import pairwise
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.enums import Visibility
from app.documents.models import Document, DocumentVersion
from app.legacy_import import onenote_capture as oc
from app.legacy_import import source_originals
from app.legacy_import.historical_views import (
    HELD_STATES,
    _file_state,
    historical_summary,
    import_statuses,
)
from app.legacy_import.models import ImportBatch
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
    SourceMatchMethod,
    SourceSystem,
)
from app.matters.continuations import MAX_HOPS
from app.matters.models import Matter
from app.matters.views import DOCUMENT_PAGE_SIZE
from app.mcp_server import readers
from app.mcp_server.readers import NotFound
from tests import factories
from tests.test_onenote_capture_changes import FIRST, GUID, build, f, run
from tests.test_onenote_capture_import import (
    _all,
    _apply,
    _file,
    _guid,
    build_capture,
    register_teema,
    stored_link,
)
from tests.test_operator_commands_dry_run import changed, fingerprint

pytestmark = pytest.mark.django_db

T1 = "2021-08-30T08:48:10.000Z"
T2 = "2023-02-01T21:30:00.000Z"


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture(autouse=True)
def _no_cached_package():
    source_originals.clear_cache()
    yield
    source_originals.clear_cache()


def _import(tmp_path, name, pages):
    root = build_capture(tmp_path / name, pages)
    plan = oc.build_plan(oc.load_capture(root))
    assert plan.problems == [], plan.problems
    _apply(plan, _all(plan))
    _apply(plan, _all(plan, include_shared=True))
    return root


def _documents(client, matter, **params):
    return client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}), params)


def _rows(html: str) -> list[tuple[str, str]]:
    """(file name, the date cell's text) per evidence row, in page order."""
    if 'class="table doctable"' not in html:
        return []
    body = html.split('class="table doctable"', 1)[1].split("</table>", 1)[0]
    out = []
    for row in body.split("<tr")[2:]:
        name = re.search(r">\s*([^<>\s][^<>]*?\.(?:pdf|docx|txt))\s*<", row)
        cell = row.split('class="table__date">', 1)[1].split("</td>", 1)[0]
        out.append(
            (name.group(1) if name else "?", " ".join(re.sub(r"<[^>]+>", " ", cell).split()))
        )
    return out


def _years_offered(html: str) -> list[str]:
    select = html.split('name="aasta"', 1)[1].split("</select>", 1)[0]
    return re.findall(r'<option value="([^"]*)"', select)


# ======================================================================================
# 1. Dates (docs/adr/0206 §3)
# ======================================================================================

#: Each instant with the Europe/Tallinn day it falls on, derived by hand:
#: EET is UTC+2, EEST UTC+3; summer time began 28.3.2021 01:00 UTC and ended
#: 31.10.2021 01:00 UTC.
INSTANTS = [
    ("kevad-2359.pdf", "2021-03-27T21:59:00.000Z", "27.3.2021"),  # 23:59 EET
    ("kevad-0000.pdf", "2021-03-27T22:00:00.000Z", "28.3.2021"),  # 00:00 EET
    ("kevad-0259.pdf", "2021-03-28T00:59:00.000Z", "28.3.2021"),  # 02:59 EET, last minute
    ("sugis-2359.pdf", "2021-10-30T20:59:59.000Z", "30.10.2021"),  # 23:59:59 EEST
    ("sugis-0000.pdf", "2021-10-30T21:00:00.000Z", "31.10.2021"),  # 00:00 EEST
    ("talv-2359.pdf", "2021-10-31T21:59:00.000Z", "31.10.2021"),  # 23:59 EET
    ("talv-0000.pdf", "2021-10-31T22:00:00.000Z", "1.11.2021"),  # 00:00 EET
    ("aasta-lopp.pdf", "2019-12-31T21:59:59.000Z", "31.12.2019"),  # 23:59:59 EET
    ("aasta-algus.pdf", "2019-12-31T22:00:00.000Z", "1.1.2020"),  # 00:00 EET
]


@pytest.fixture
def dated(tmp_path, evidence_root, enabled):
    teema = register_teema("2026_31")
    files = [_file(n, name, when) for n, (name, when, _d) in enumerate(INSTANTS, start=1)]
    files.append(_file(len(files) + 1, "ujuv.pdf", None, floating=True))
    _import(
        tmp_path, "dated", [{"tag": "001", "guid": _guid(31), "matters": [teema], "files": files}]
    )
    return teema


def test_each_onenote_file_prints_its_tallinn_day_across_dst_and_midnight(
    dated, client, specialist
):
    client.force_login(specialist)
    cells = dict(_rows(_documents(client, dated, koik="1").content.decode()))
    for name, _when, day in INSTANTS:
        assert cells[name] == f"{day} lisatud OneNote'i", name
    assert cells["ujuv.pdf"] == "Lisamise kuupäev teadmata"


def test_the_list_is_newest_instant_first_and_the_undated_file_last(dated, client, specialist):
    client.force_login(specialist)
    names = [n for n, _ in _rows(_documents(client, dated, koik="1").content.decode())]
    expected = [n for n, _w, _d in sorted(INSTANTS, key=lambda i: i[1], reverse=True)]
    assert names == [*expected, "ujuv.pdf"]


def test_the_year_filter_reads_the_tallinn_year_and_the_unknown_only_under_teadmata(
    dated, client, specialist
):
    client.force_login(specialist)
    html = _documents(client, dated).content.decode()
    assert _years_offered(html) == ["", "2021", "2020", "2019", "teadmata"]

    def names(year):
        return [
            n for n, _ in _rows(_documents(client, dated, aasta=year, koik="1").content.decode())
        ]

    assert names("2019") == ["aasta-lopp.pdf"]
    assert names("2020") == ["aasta-algus.pdf"]
    assert len(names("2021")) == 7 and "ujuv.pdf" not in names("2021")
    assert names("teadmata") == ["ujuv.pdf"]
    for year in ("2019", "2020", "2021", "2026"):
        assert "ujuv.pdf" not in names(year)


def test_the_import_and_copy_days_are_never_a_onenote_files_date(dated, client, specialist):
    """Push the import's own `created_at` to a year nothing else has: it must not surface."""
    Document.objects.filter(matter=dated).update(created_at=datetime(2015, 5, 5, 10, 0, tzinfo=UTC))
    client.force_login(specialist)
    html = _documents(client, dated, koik="1").content.decode()
    assert "2015" not in _years_offered(html)
    cells = " ".join(cell for _n, cell in _rows(html))
    assert "5.5.2015" not in cells
    # The bytes were copied on 4.11.2026 (the capture's `copied_at`): not a date either.
    assert "4.11.2026" not in cells and "2026" not in _years_offered(html)
    assert _rows(_documents(client, dated, aasta="2015").content.decode()) == []


def test_an_undated_onenote_file_sorts_after_even_the_oldest_upload(
    dated, client, specialist, capture_evidence
):
    upload = capture_evidence(dated, b"vana upload", "upload.pdf", "application/pdf")
    Document.objects.filter(pk=upload.document_id).update(
        created_at=datetime(2000, 1, 3, 10, 0, tzinfo=UTC)
    )
    client.force_login(specialist)
    rows = _rows(_documents(client, dated, koik="1").content.decode())
    assert [n for n, _ in rows][-2:] == ["upload.pdf", "ujuv.pdf"]
    assert dict(rows)["upload.pdf"] == "3.1.2000"  # an upload prints its own day, unlabelled
    assert "2000" in _years_offered(_documents(client, dated).content.decode())


def test_an_upload_only_teema_keeps_upload_day_order_and_tallinn_years(
    client, specialist, capture_evidence
):
    matter = factories.MatterFactory()
    stamps = {
        "a.pdf": datetime(2020, 12, 31, 22, 30, tzinfo=UTC),  # 1.1.2021
        "b.pdf": datetime(2020, 12, 31, 21, 30, tzinfo=UTC),  # 31.12.2020
        "c.pdf": datetime(2022, 6, 30, 21, 30, tzinfo=UTC),  # 1.7.2022 EEST
    }
    for name, when in stamps.items():
        version = capture_evidence(matter, name.encode(), name, "application/pdf")
        Document.objects.filter(pk=version.document_id).update(created_at=when)
    client.force_login(specialist)
    html = _documents(client, matter).content.decode()
    assert _rows(html) == [("c.pdf", "1.7.2022"), ("a.pdf", "1.1.2021"), ("b.pdf", "31.12.2020")]
    assert _years_offered(html) == ["", "2022", "2021", "2020"]
    assert "OneNote" not in html.split('class="table doctable"', 1)[1].split("</table>", 1)[0]
    assert "kuupaeva-selgitus" not in html
    assert [n for n, _ in _rows(_documents(client, matter, aasta="2021").content.decode())] == [
        "a.pdf"
    ]


def _second_row_for(document: Document, *, added_at) -> None:
    row = LegacySourceResourceImport.objects.get(document=document)
    source = row.resource
    twin = LegacySourceResource.objects.create(
        source_page=source.source_page,
        resource_key=f"qa-twin-{document.pk}"[:100],
        original_filename=source.original_filename,
        resource_kind=source.resource_kind,
        sha256=source.sha256,
        size_bytes=source.size_bytes,
        archive_relative_path="qa/twin",
        source_added_at=added_at,
        source_added_basis=source.source_added_basis,
    )
    LegacySourceResourceImport.objects.create(
        matter_source_page=row.matter_source_page,
        resource=twin,
        document=document,
        document_version=row.document_version,
        import_batch=row.import_batch,
        state=ResourceImportState.IMPORTED,
    )


def test_two_rows_that_agree_on_the_day_are_not_a_conflict(dated, client, specialist):
    document = Document.objects.get(matter=dated, title="talv-0000.pdf")
    source = LegacySourceResourceImport.objects.get(document=document).resource
    _second_row_for(document, added_at=source.source_added_at)
    client.force_login(specialist)
    assert dict(_rows(_documents(client, dated, koik="1").content.decode()))["talv-0000.pdf"] == (
        "1.11.2021 lisatud OneNote'i"
    )


def test_rows_that_disagree_read_unknown_with_no_min_or_max(dated, client, specialist):
    document = Document.objects.get(matter=dated, title="kevad-0000.pdf")
    _second_row_for(document, added_at=datetime(2010, 1, 1, 12, 0, tzinfo=UTC))
    client.force_login(specialist)
    html = _documents(client, dated, koik="1").content.decode()
    assert dict(_rows(html))["kevad-0000.pdf"] == "Lisamise kuupäev teadmata (allikad on vastuolus)"
    assert "2010" not in _years_offered(html)
    names = [n for n, _ in _rows(html)]
    assert names.index("kevad-0000.pdf") > names.index("aasta-lopp.pdf")  # after every dated row
    assert "kevad-0000.pdf" in [
        n for n, _ in _rows(_documents(client, dated, aasta="teadmata").content.decode())
    ]
    assert "kevad-0000.pdf" not in [
        n for n, _ in _rows(_documents(client, dated, aasta="2021", koik="1").content.decode())
    ]


@pytest.fixture
def boundary(tmp_path, evidence_root, enabled):
    """Exactly one page of dated files and one undated file after it."""
    teema = register_teema("2026_32")
    files = [
        _file(n, f"d{n:02}.pdf", f"2022-02-{n:02}T10:00:00.000Z")
        for n in range(1, DOCUMENT_PAGE_SIZE + 1)
    ]
    files.append(_file(DOCUMENT_PAGE_SIZE + 1, "ujuv.pdf", None, floating=True))
    _import(
        tmp_path, "edge", [{"tag": "001", "guid": _guid(32), "matters": [teema], "files": files}]
    )
    return teema


def test_the_page_boundary_holds_the_newest_dated_files_and_counts_the_rest(
    boundary, client, specialist
):
    client.force_login(specialist)
    response = _documents(client, boundary)
    names = [n for n, _ in _rows(response.content.decode())]
    assert names == [f"d{n:02}.pdf" for n in range(DOCUMENT_PAGE_SIZE, 0, -1)]
    assert response.context["evidence_hidden"] == 1
    full = [n for n, _ in _rows(_documents(client, boundary, koik="1").content.decode())]
    assert full == [*names, "ujuv.pdf"]


@pytest.fixture
def ten_and_forty(tmp_path, evidence_root, enabled):
    small, large = register_teema("2026_33"), register_teema("2026_34")
    pages = [
        {
            "tag": "001",
            "guid": _guid(33),
            "matters": [small],
            "files": [
                _file(n, f"s{n:02}.pdf", f"2021-05-{n:02}T10:00:00.000Z") for n in range(1, 11)
            ],
        },
        {
            "tag": "002",
            "guid": _guid(34),
            "matters": [large],
            "files": [
                _file(n, f"l{n:02}.pdf", f"2021-{(n % 12) + 1:02}-{(n % 27) + 1:02}T10:00:00.000Z")
                for n in range(1, 41)
            ],
        },
    ]
    _import(tmp_path, "ql", pages)
    return small, large


def test_ten_or_forty_onenote_documents_cost_the_same_queries(ten_and_forty, client, specialist):
    small, large = ten_and_forty
    client.force_login(specialist)
    _documents(client, small, koik="1")
    _documents(client, large, koik="1")  # warm per-process caches
    with CaptureQueriesContext(connection) as ten:
        assert len(_rows(_documents(client, small, koik="1").content.decode())) == 10
    with CaptureQueriesContext(connection) as forty:
        assert len(_rows(_documents(client, large, koik="1").content.decode())) == 40
    assert len(forty.captured_queries) <= len(ten.captured_queries) + 2, (
        len(ten.captured_queries),
        len(forty.captured_queries),
    )


# ======================================================================================
# 2. Statuses (docs/adr/0206 §1–2)
# ======================================================================================


def _codes() -> dict[str, str]:
    """Every SKIPPED reason code the capture importer defines (HOLD_* and SKIP_*)."""
    return {
        name: value
        for name, value in vars(oc).items()
        if (name.startswith("HOLD_") or name.startswith("SKIP_")) and isinstance(value, str)
    }


def test_the_importer_defines_the_codes_this_review_expects():
    assert set(_codes().values()) >= {
        "SHARED_PAGE_AWAITING_REVIEW",
        "EMPTY_FILE",
        "LARGER_THAN_EVIDENCE_LIMIT",
        "EARLIER_CAPTURE_NOT_IMPORTED",
        "CONTINUES_EARLIER_BLOCK",
        "MOVED_BLOCK_BYTES_CHANGED",
        "AMBIGUOUS_EARLIER_BLOCK",
        "RESEMBLES_EARLIER_FILE_UNPROVEN",
        "SAME_FILE_ON_ANOTHER_PAGE_OF_TEEMA",
        "BYTES_CHANGED_UNDER_SAME_BLOCK",
        "BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK",
        "COMMENT_CHANGED_SINCE_IMPORT",
    }


@pytest.mark.parametrize("code", sorted(set(_codes().values())))
def test_every_skipped_code_is_one_named_non_failure_state(code):
    record = SimpleNamespace(state=ResourceImportState.SKIPPED, error_code=code, document_id=None)
    state = _file_state(SimpleNamespace(size_bytes=10), record)
    assert state not in {"unavailable", "pending", "imported", "not_imported"}, (code, state)
    assert state == ("empty" if code == oc.HOLD_EMPTY_FILE else HELD_STATES[code])


@pytest.mark.parametrize(
    ("state", "code", "size", "expected"),
    [
        (ResourceImportState.FAILED, "OSError", 10, "unavailable"),
        (ResourceImportState.FAILED, "", 10, "unavailable"),
        (ResourceImportState.FAILED, "OSError", 0, "empty"),
        (ResourceImportState.SKIPPED, "SOMETHING_NEW", 10, "not_imported"),
        (ResourceImportState.SKIPPED, "", 10, "not_imported"),
    ],
)
def test_only_a_failed_row_is_a_failure(state, code, size, expected):
    record = SimpleNamespace(state=state, error_code=code, document_id=None)
    assert _file_state(SimpleNamespace(size_bytes=size), record) == expected


@pytest.fixture
def status_world(tmp_path, evidence_root, enabled):
    a = register_teema("2026_41")
    b, c = register_teema("2026_42"), register_teema("2026_43")
    pages = [
        {
            "tag": "001",
            "guid": _guid(41),
            "matters": [a],
            "files": [
                _file(1, "kiri.pdf", T1),
                _file(2, "vastus.docx", T2),
                _file(3, "tühi.txt", T1, data=b""),
                _file(4, "ujuv.pdf", None, floating=True),
            ],
        },
        {
            "tag": "002",
            "guid": _guid(42),
            "matters": [b, c],
            "files": [_file(1, "a.pdf", T1), _file(2, "b.pdf", T2)],
        },
    ]
    _import(tmp_path, "st", pages)
    return {"a": a, "b": b, "c": c}


def _db_counts(link: MatterSourcePage) -> dict[str, int]:
    """What the import rows of one link say, counted straight from the table."""
    rows = LegacySourceResourceImport.objects.filter(matter_source_page=link)
    return {
        "imported": rows.filter(document__isnull=False).count(),
        "empty": rows.filter(resource__size_bytes=0).count(),
        "held_shared": rows.filter(
            state=ResourceImportState.SKIPPED, error_code=oc.HOLD_SHARED_PAGE
        ).count(),
    }


def test_page_counts_equal_the_import_rows(status_world, specialist):
    for key in ("a", "b", "c"):
        link = MatterSourcePage.objects.get(matter=status_world[key])
        status = import_statuses([link])[link.pk]
        expected = {k: v for k, v in _db_counts(link).items() if v}
        assert status.counts == expected, (key, status.counts)
        assert status.files == sum(expected.values())


def test_allikas_and_ajalooline_read_the_same_counts(status_world, specialist):
    summary = historical_summary(status_world["a"], specialist)
    assert summary["status"]["counts"]["imported"] == 3
    assert summary["status"]["counts"]["empty"] == 1
    assert summary["links"][0].import_status.counts == {"imported": 3, "empty": 1}
    assert "Lehe sisu ja kõik 3 faili on Juristidesse imporditud" in summary["status"]["lines"][0]
    shared = historical_summary(status_world["b"], specialist)
    assert shared["status"]["counts"]["held_shared"] == 2
    assert shared["status"]["tone"] == "waiting"
    assert not any("imporditud (" in line for line in shared["status"]["lines"])


def test_a_page_row_without_import_rows_is_never_imported(specialist):
    matter = factories.MatterFactory()
    page = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="{QA}{1}{B0}",
        page_key="p-qa-no-rows",
        source_notebook="n",
        source_section="s",
        title="Ridadeta leht",
        capture_id="qa",
        first_imported_at=timezone.now(),
        latest_imported_at=timezone.now(),
        blocks=[
            {"ordinal": 1, "kind": "FILE_ATTACHMENT", "resource_key": "r1", "text": ""},
            {"ordinal": 2, "kind": "FILE_ATTACHMENT", "resource_key": "r2", "text": ""},
        ],
    )
    for key in ("r1", "r2"):
        LegacySourceResource.objects.create(
            source_page=page,
            resource_key=key,
            original_filename=f"{key}.pdf",
            sha256="b" * 64,
            size_bytes=5,
            archive_relative_path=f"qa/{key}",
        )
    link = MatterSourcePage.objects.create(
        matter=matter, source_page=page, match_method=SourceMatchMethod.choices[0][0]
    )
    status = import_statuses([link])[link.pk]
    assert status.counts == {"pending": 2} and status.tone == "waiting"
    summary = historical_summary(matter, specialist)
    assert not any("imporditud (" in line for line in summary["status"]["lines"])
    assert "on veel importimata" in " ".join(summary["status"]["lines"])


# -- the counts after a later capture or on a reused pilot page (the page view's projection)


@pytest.fixture
def later_world(tmp_path, enabled):
    teema = register_teema("2026_44")
    run(
        build(
            tmp_path / "c1",
            "cap-1",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}],
        )
    )
    return {"teema": teema, "tmp": tmp_path}


def test_a_later_captures_new_file_is_counted_in_the_page_and_teema_status(later_world, specialist):
    teema = later_world["teema"]
    run(
        build(
            later_world["tmp"] / "c2",
            "cap-2",
            [
                {
                    "tag": "001",
                    "guid": GUID,
                    "matters": [teema],
                    "files": [*FIRST, f("{B4}", "uus.pdf", "2024-05-06T07:00:00.000Z")],
                }
            ],
        )
    )
    link = MatterSourcePage.objects.get(matter=teema)
    assert Document.objects.filter(matter=teema).count() == 4
    assert _db_counts(link)["imported"] == 4
    status = historical_summary(teema, specialist)["links"][0].import_status
    assert status.counts.get("imported") == 4, status.counts


def test_the_page_view_and_the_status_count_the_same_files_after_a_move(
    later_world, client, specialist
):
    """Whatever the page view lists, by state, is what its chip counts (superseded aside)."""
    from collections import Counter

    teema = later_world["teema"]
    moved = [
        f("{M1}", "kiri.pdf", "2021-08-30T08:48:10.000Z", comment="Ministeeriumi kiri"),
        f("{M2}", "vastus.docx", "2023-02-01T21:30:00.000Z"),
        f("{M3}", "lõuend.pdf", None, floating=True),
        f("{M4}", "uus.pdf", "2024-05-06T07:00:00.000Z"),
    ]
    run(
        build(
            later_world["tmp"] / "c2",
            "cap-2",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": moved}],
        )
    )
    link = MatterSourcePage.objects.get(matter=teema)
    client.force_login(specialist)
    response = client.get(reverse("legacy_import:source_page", kwargs={"pk": link.pk}))
    shown = Counter(
        b["state"]
        for b in response.context["blocks"]
        if b["kind"] == "file" and b["state"] != "superseded"
    )
    status = historical_summary(teema, specialist)["links"][0].import_status
    assert status.counts == dict(shown), (status.counts, shown)
    assert shown["imported"] == 4 and shown["held_review"] == 1


def test_a_reused_pilot_page_does_not_read_as_having_no_files(tmp_path, enabled, specialist):
    teema = register_teema("2026_45")
    pilot = LegacySourcePage.objects.create(
        source_system=SourceSystem.ONENOTE_DESKTOP,
        source_page_id="{PILOT}{1}{B0}",
        page_key=f"p-{GUID.lower()}",
        source_notebook="n",
        source_section="vana",
        title="piloodi leht",
        capture_id="pilot-2026-10",
        derived_text="Piloodi tekst",
        blocks=[
            {"ordinal": 0, "kind": "TEXT", "text": "Piloodi tekst", "resource_key": ""},
            {"ordinal": 1, "kind": "FILE_ATTACHMENT", "text": "", "resource_key": "r001"},
        ],
        first_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
        latest_imported_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    LegacySourceResource.objects.create(
        source_page=pilot,
        resource_key="r001",
        original_filename="kiri.pdf",
        sha256="a" * 64,
        size_bytes=1,
        archive_relative_path="pilot/r001",
    )
    run(
        build(
            tmp_path / "c1",
            "cap-1",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}],
        )
    )
    assert Document.objects.filter(matter=teema).count() == 3
    summary = historical_summary(teema, specialist)
    label = summary["links"][0].import_status.label
    assert label != "Lehel ei ole faile", label
    assert summary["status"]["counts"]["imported"] == 3, summary["status"]


# ======================================================================================
# 3. Continuations (docs/adr/0207)
# ======================================================================================


def _chain(n: int, **fields) -> list[Matter]:
    matters = [factories.MatterFactory(title=f"QA etapp {i}", **fields) for i in range(1, n + 1)]
    for earlier, later in pairwise(matters):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _teema_page(client, matter):
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200 and not response.has_header("Location")
    return response.content.decode()


def _nav(body: str) -> str:
    match = re.search(r'<nav class="matterhead__continuation".*?</nav>', body, re.DOTALL)
    return match.group(0) if match else ""


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_one_successor_and_one_predecessor_are_named_both_ways(signed_in, specialist):
    old, new = _chain(2, owner=specialist)
    nav_old, nav_new = (
        _text(_nav(_teema_page(signed_in, old))),
        _text(_nav(_teema_page(signed_in, new))),
    )
    assert f"Jätkub teemas {new.display_reference}" in nav_old
    assert f"Eelnev teema: {old.display_reference}" in nav_new


def test_several_predecessors_are_all_listed(signed_in, specialist):
    target = factories.MatterFactory(owner=specialist, title="QA siht")
    earlier = [factories.MatterFactory(owner=specialist, title=f"QA eelnev {i}") for i in range(3)]
    Matter.all_objects.filter(pk__in=[m.pk for m in earlier]).update(superseded_by=target)
    nav = _text(_nav(_teema_page(signed_in, target)))
    assert "Eelnevad teemad" in nav
    for matter in earlier:
        assert matter.display_reference in nav


def test_a_three_stage_chain_reads_the_same_path_from_each_stage(signed_in, specialist):
    stages = _chain(3, owner=specialist)
    refs = [m.display_reference for m in stages]
    for stage in stages:
        nav = _nav(_teema_page(signed_in, stage))
        text = _text(nav)
        assert "Teema etapid" in text
        positions = [text.index(ref) for ref in refs]
        assert positions == sorted(positions), (stage.display_reference, text)
        # The current stage is marked and is not a link to itself.
        assert "aria-current" in nav
        assert f'href="/teemad/{stage.pk}/"' not in nav
        for other in stages:
            if other.pk != stage.pk:
                assert f'href="/teemad/{other.pk}/"' in nav


def test_mcp_get_matter_gives_the_same_chain_from_each_stage(signed_in, specialist, settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"
    stages = _chain(3, owner=specialist)
    refs = [m.display_reference for m in stages]
    for stage in stages:
        c = readers.matter_snapshot(specialist, stage.display_reference)["continuations"]
        assert [s["reference"] for s in c["chain"]] == refs
        assert c["cyclic"] is False and c["truncated"] is False


def test_a_deleted_target_is_said_to_exist_and_never_named(signed_in, specialist, settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"
    old, gone = _chain(2, owner=specialist)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=timezone.now(), title="KUSTUTATUD-QA")
    body = _teema_page(signed_in, old)
    assert "Jätkub teemas, mida sul ei ole lubatud näha" in _text(body)
    for leak in (gone.display_reference, "KUSTUTATUD-QA", str(gone.pk)):
        assert leak not in body
    c = readers.matter_snapshot(specialist, old.display_reference)["continuations"]
    assert c["successor"] is None and c["successor_hidden"] is True
    assert gone.display_reference not in json.dumps(c) and str(gone.pk) not in json.dumps(c)
    # A deleted predecessor is not history at all (0102): nothing, not even a count.
    first, second = _chain(2, owner=specialist)
    Matter.all_objects.filter(pk=first.pk).update(deleted_at=timezone.now())
    back = readers.matter_snapshot(specialist, second.display_reference)["continuations"]
    assert back["predecessors"] == [] and back["hidden_predecessors"] == 0
    assert str(first.pk) not in _teema_page(signed_in, second)


def test_a_three_matter_cycle_is_finite_and_noted_from_every_stage(signed_in, specialist):
    a, b, c = _chain(3, owner=specialist)
    Matter.all_objects.filter(pk=c.pk).update(superseded_by=a)
    for stage in (a, b, c):
        stage = Matter.all_objects.get(pk=stage.pk)
        text = _text(_nav(_teema_page(signed_in, stage)))
        assert "Ahel jõuab tagasi juba näidatud teemani" in text
        assert "viimane etapp" not in text  # a cycle has no endpoint


def test_a_long_chain_is_cut_at_the_bound_and_names_nothing_beyond_it(signed_in, specialist):
    stages = _chain(MAX_HOPS + 3, owner=specialist)
    body = _teema_page(signed_in, stages[0])
    nav = _nav(body)
    assert f"Ahel on pikem kui {MAX_HOPS} etappi" in _text(nav)
    for beyond in stages[MAX_HOPS + 1 :]:
        assert beyond.display_reference not in nav and str(beyond.pk) not in nav
    assert stages[MAX_HOPS].display_reference in nav  # the twelfth hop is shown


def test_an_outsider_reads_no_stage_anywhere(client, specialist, administrator, settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"
    stages = _chain(3, owner=specialist)
    client.force_login(administrator)
    for stage in stages:
        response = client.get(reverse("matters:matter_detail", kwargs={"pk": stage.pk}))
        assert response.status_code == 404
        for other in stages:
            assert other.display_reference not in response.content.decode()
        with pytest.raises(NotFound):
            readers.matter_snapshot(administrator, stage.display_reference)


# ======================================================================================
# 4. The held-original download (docs/adr/0208 §3)
# ======================================================================================

KIRI = b"%PDF-1.4 QA kiri"


@pytest.fixture
def held(tmp_path, evidence_root, enabled, settings):
    a, b = register_teema("2025_140"), register_teema("2025_196")
    c, d = register_teema("2025_200"), register_teema("2025_201")
    z = register_teema("2025_100")
    pages = [
        {
            "tag": "001",
            "guid": _guid(51),
            "matters": [a, b],
            "files": [
                _file(1, "kiri.pdf", T1, data=KIRI),
                _file(2, "tühi.txt", T1, data=b""),
                _file(3, "suur.pdf", T2, data=b"%PDF-1.4 suur"),
            ],
        },
        {"tag": "002", "guid": _guid(52), "matters": [c, d], "files": [_file(1, "teine.pdf", T2)]},
        {"tag": "004", "guid": _guid(54), "matters": [z], "files": [_file(1, "oma.pdf", T1)]},
    ]
    corpus = tmp_path / "corpus"
    root = _import(corpus / "onenote-desktop-capture-qa", "pkg-v2", pages)
    settings.HISTORICAL_SOURCE_ROOT = str(corpus)
    return {"root": root, "corpus": corpus, "a": a, "b": b, "c": c, "d": d, "z": z}


def _page(n: int) -> LegacySourcePage:
    return LegacySourcePage.objects.get(page_key=f"p-{_guid(n).lower()}")


def _link(matter, n: int) -> MatterSourcePage:
    return MatterSourcePage.objects.get(matter=matter, source_page=_page(n))


def _res(n: int, name: str) -> LegacySourceResource:
    return LegacySourceResource.objects.get(source_page=_page(n), original_filename=name)


def _url(link, resource) -> str:
    return reverse(
        "legacy_import:source_original", kwargs={"pk": link.pk, "resource_pk": resource.pk}
    )


def _audits():
    return list(
        SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
            detail__source=source_originals.AUDIT_SOURCE,
        )
    )


def _body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


#: Tables a served or refused download may write: its audit row and the session.
WRITES_ALLOWED = {"audit_securityauditevent", "django_session"}


def _only_audit_written(before) -> None:
    assert set(changed(before)) <= WRITES_ALLOWED, changed(before)


def test_a_served_original_is_exact_attachment_only_and_writes_one_audit_row(
    held, client, specialist
):
    client.force_login(specialist)
    before = fingerprint()
    response = client.get(_url(_link(held["a"], 51), _res(51, "kiri.pdf")))
    assert response.status_code == 200
    assert _body(response) == KIRI
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith("attachment;")
    assert "inline" not in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"] and "private" in response["Cache-Control"]
    (row,) = _audits()
    assert row.succeeded and row.detail["outcome"] == "served"
    assert row.detail["matter"] == str(held["a"].pk)
    assert row.detail["sha256"] == hashlib.sha256(KIRI).hexdigest()
    _only_audit_written(before)


def test_head_is_answered_and_audited_once(held, client, specialist):
    client.force_login(specialist)
    response = client.head(_url(_link(held["a"], 51), _res(51, "kiri.pdf")))
    assert response.status_code == 200
    assert len(_audits()) == 1


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_a_method_other_than_get_or_head_serves_nothing(held, specialist, method):
    client = Client(enforce_csrf_checks=True)
    client.force_login(specialist)
    response = client.generic(method, _url(_link(held["a"], 51), _res(51, "kiri.pdf")))
    assert response.status_code in {403, 405}, (method, response.status_code)
    assert not _audits()


def test_another_links_or_another_pages_resource_is_a_silent_404(held, client, specialist):
    client.force_login(specialist)
    before = fingerprint()
    # A shared page's file through another page's link, and the other way round.
    assert client.get(_url(_link(held["z"], 54), _res(51, "kiri.pdf"))).status_code == 404
    assert client.get(_url(_link(held["a"], 51), _res(52, "teine.pdf"))).status_code == 404
    # A resource id that exists nowhere.
    assert (
        client.get(
            reverse(
                "legacy_import:source_original",
                kwargs={"pk": _link(held["a"], 51).pk, "resource_pk": _link(held["a"], 51).pk},
            )
        ).status_code
        == 404
    )
    assert not _audits()
    _only_audit_written(before)


@pytest.mark.parametrize(
    "case",
    [
        "imported_elsewhere_only",
        "empty",
        "too_large",
        "earlier_capture",
        "continues",
        "no_row_here",
    ],
)
def test_a_resource_not_held_on_this_link_is_a_silent_404(held, client, specialist, case):
    link = _link(held["a"], 51)
    kiri = _res(51, "kiri.pdf")
    row = LegacySourceResourceImport.objects.get(matter_source_page=link, resource=kiri)
    target = kiri
    if case == "imported_elsewhere_only":
        target = _res(54, "oma.pdf")
        link = _link(held["z"], 54)
    elif case == "empty":
        target = _res(51, "tühi.txt")
    elif case == "too_large":
        LegacySourceResourceImport.objects.filter(pk=row.pk).update(error_code=oc.HOLD_TOO_LARGE)
    elif case == "earlier_capture":
        LegacySourceResourceImport.objects.filter(pk=row.pk).update(
            error_code=oc.HOLD_EARLIER_CAPTURE
        )
    elif case == "continues":
        LegacySourceResourceImport.objects.filter(pk=row.pk).update(error_code=oc.SKIP_CONTINUES)
    elif case == "no_row_here":
        row.delete()
    client.force_login(specialist)
    assert client.get(_url(link, target)).status_code == 404
    assert not _audits()


@pytest.mark.parametrize(
    ("digest", "outcome"),
    [
        ("../../manifest.json", "resource_digest_invalid"),
        (hashlib.sha256(KIRI).hexdigest().upper(), "resource_digest_invalid"),
        (hashlib.sha256(KIRI).hexdigest()[:40], "resource_digest_invalid"),
        ("/etc/passwd", "resource_digest_invalid"),
        ("..\\..\\SEAL.json", "resource_digest_invalid"),
        ("0" * 64, "blob_not_sealed"),
    ],
)
def test_a_crafted_resource_digest_is_a_404_with_one_failed_audit_row(
    held, client, specialist, digest, outcome
):
    kiri = _res(51, "kiri.pdf")
    LegacySourceResource.objects.filter(pk=kiri.pk).update(sha256=digest)
    client.force_login(specialist)
    response = client.get(_url(_link(held["a"], 51), kiri))
    assert response.status_code == 404
    body = _body(response).decode("utf-8", "replace")
    assert digest not in body and "blobs" not in body
    (row,) = _audits()
    assert not row.succeeded and row.detail["outcome"] == outcome


@pytest.mark.parametrize("damage", ["seal_digest", "manifest_byte", "sums_line", "seal_sums"])
def test_a_seal_that_does_not_prove_its_package_serves_nothing(held, client, specialist, damage):
    root = held["root"]
    seal_path = root / "SEAL.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if damage == "seal_digest":
        seal_path.write_text(json.dumps({**seal, "manifest_sha256": "f" * 64}))
    elif damage == "manifest_byte":
        manifest = root / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
    elif damage == "sums_line":
        sums = root / "SHA256SUMS"
        sums.write_text(sums.read_text(encoding="utf-8") + f"{'1' * 64}  blobs/{'1' * 64}\n")
    elif damage == "seal_sums":
        seal_path.write_text(json.dumps({**seal, "sha256sums_sha256": "e" * 64}))
    client.force_login(specialist)
    assert client.get(_url(_link(held["a"], 51), _res(51, "kiri.pdf"))).status_code == 404
    (row,) = _audits()
    assert row.detail["outcome"] == "package_missing"


def test_a_package_outside_the_corpus_root_is_never_found(
    held, client, specialist, settings, tmp_path
):
    empty = tmp_path / "empty-corpus"
    empty.mkdir()
    settings.HISTORICAL_SOURCE_ROOT = str(empty)  # the real package is a sibling, outside
    client.force_login(specialist)
    assert client.get(_url(_link(held["a"], 51), _res(51, "kiri.pdf"))).status_code == 404
    assert _audits()[0].detail["outcome"] == "package_missing"


def test_a_decoy_package_never_serves_other_bytes(held, client, specialist):
    """A second package sealing the same manifest, sorted first, with a tampered blob."""
    import shutil

    decoy = held["corpus"] / "aaa-decoy"
    shutil.copytree(held["root"], decoy)
    kiri = _res(51, "kiri.pdf")
    (decoy / "blobs" / kiri.sha256).write_bytes(b"%PDF-1.4 VOLTS")
    client.force_login(specialist)
    response = client.get(_url(_link(held["a"], 51), kiri))
    if response.status_code == 200:
        assert _body(response) == KIRI
    else:
        assert response.status_code == 404
        assert _audits()[0].detail["outcome"] in {"size_mismatch", "hash_mismatch"}


def test_a_filename_cannot_inject_a_header(held, client, specialist):
    kiri = _res(51, "kiri.pdf")
    LegacySourceResource.objects.filter(pk=kiri.pk).update(
        original_filename='kiri"\r\nSet-Cookie: x=1;\r\n.pdf'
    )
    client.force_login(specialist)
    response = client.get(_url(_link(held["a"], 51), kiri))
    assert response.status_code == 200
    assert (
        "\r" not in response["Content-Disposition"] and "\n" not in response["Content-Disposition"]
    )
    assert not response.has_header("Set-Cookie") or "x=1" not in response["Set-Cookie"]


def test_an_outsider_administrator_is_served_nothing(held, client, administrator):
    url = _url(_link(held["a"], 51), _res(51, "kiri.pdf"))
    client.force_login(administrator)
    assert client.get(url).status_code == 404
    assert not _audits()


@pytest.mark.xfail(
    strict=True,
    reason="QA-E MINOR-2: visible_links does not exclude a soft-deleted Teema's link, so its "
    "OneNote page and held originals stay reachable through it (docs/adr/0102)",
)
def test_a_deleted_teemas_link_serves_no_original(held, client, specialist):
    Matter.all_objects.filter(pk=held["b"].pk).update(deleted_at=timezone.now())
    client.force_login(specialist)
    assert client.get(_url(_link(held["b"], 51), _res(51, "kiri.pdf"))).status_code == 404


@pytest.fixture
def narrow_reader(monkeypatch, specialist):
    """A reader of NORMAL work only (the hand-built scope docs/adr/0181 still honours)."""
    from app.core.authorization import Scope
    from app.core.authorization import scope_for_user as real

    def scope(user):
        if getattr(user, "pk", None) == specialist.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    for module in (
        "app.matters.models",
        "app.documents.models",
        "app.legacy_import.historical_views",
    ):
        monkeypatch.setattr(f"{module}.scope_for_user", scope)
    return specialist


def test_a_continuation_edge_grants_no_page_of_another_teema(held, client, narrow_reader):
    """2025_100 (restricted, unreadable) continues as 2025_140: its page stays closed."""
    z, a = held["z"], held["a"]
    Matter.all_objects.filter(pk=z.pk).update(
        visibility=Visibility.RESTRICTED, owner=factories.UserFactory(), superseded_by=a
    )
    hidden_link = _link(z, 54)
    LegacySourceResourceImport.objects.filter(matter_source_page=hidden_link).update(
        state=ResourceImportState.SKIPPED,
        error_code=oc.HOLD_SHARED_PAGE,
        document=None,
        document_version=None,
    )
    client.force_login(narrow_reader)
    page_url = reverse("legacy_import:source_page", kwargs={"pk": hidden_link.pk})
    assert client.get(page_url).status_code == 404
    assert client.get(_url(hidden_link, _res(54, "oma.pdf"))).status_code == 404
    html = _documents(client, a).content.decode()
    assert "Eelnevate etappide ajalooline materjal" not in html
    assert str(hidden_link.pk) not in html
    own = client.get(
        reverse("legacy_import:source_page", kwargs={"pk": _link(a, 51).pk})
    ).content.decode()
    assert str(hidden_link.pk) not in own and str(z.pk) not in own
    assert not _audits()


# ======================================================================================
# 5. Exact page-GUID resolution (docs/adr/0204, D5)
# ======================================================================================

PAGE = "1A2B3C4D-0000-4000-8000-00000000AA01"
OTHER = "1A2B3C4D-0000-4000-8000-00000000AA02"
NOTEBOOK = "9F9F9F9F-0000-4000-8000-00000000FF01"
SECTION = "5E5E5E5E-0000-4000-8000-00000000EE01"
WEB = "https://tenant.invalid/sites/x/_layouts/15/Doc.aspx"


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (
            f"{WEB}?sourcedoc={{{NOTEBOOK}}}&wd=target(Osa.one%7C{SECTION}%2FLeht%7C{PAGE}%2F)",
            {PAGE},
        ),
        (
            f"{WEB}?sourcedoc={{{NOTEBOOK}}}&wd=target(Osa.one%7C{SECTION}%2FLeht%7C{PAGE.lower()}%2F)",
            {PAGE},
        ),
        # Encoded once more, and a title that holds a pipe, a slash and a GUID-looking run.
        (
            f"{WEB}?sourcedoc={{{NOTEBOOK}}}&wd=target%2528Osa.one%257C{SECTION}%252FA%257CB%252F{OTHER}x%257C{PAGE}%252F%2529",
            {PAGE},
        ),
        # Only the notebook, only a section, a notebook path, a section file: no page.
        (f"{WEB}?sourcedoc={{{PAGE}}}", set()),
        (f"{WEB}?sourcedoc={{{NOTEBOOK}}}&wd=target(Osa.one%7C{PAGE}%2F)", set()),
        (f"https://tenant.invalid/_layouts/OneNote.aspx?id=%2Fsites%2Fx%2F{PAGE}", set()),
        (f"{WEB}?sourcedoc={{{NOTEBOOK}}}&wdsectionfileid={{{PAGE}}}", set()),
        (f"onenote:https://tenant.invalid/x/Osa.one#Leht&section-id={{{PAGE}}}&end", set()),
        (
            f"onenote:https://tenant.invalid/x/Osa.one#Leht&section-id={{{SECTION}}}&page-id={{{PAGE}}}&end",
            {PAGE},
        ),
        ("", set()),
        ("not a link at all", set()),
    ],
)
def test_register_page_guids_reads_only_where_a_link_names_a_page(link, expected):
    got = oc.register_page_guids(link)
    assert got == frozenset(expected)
    assert NOTEBOOK not in got and SECTION not in got


def test_web_and_client_halves_that_disagree_name_two_pages():
    web = f"{WEB}?sourcedoc={{{NOTEBOOK}}}&wd=target(Osa.one%7C{SECTION}%2FLeht%7C{PAGE}%2F)"
    client = f"onenote:https://tenant.invalid/x/Osa.one#Leht&section-id={{{SECTION}}}&page-id={{{OTHER}}}&end"
    assert oc.register_page_guids(f"{web} {client}") == {PAGE, OTHER}
    same = client.replace(OTHER, PAGE)
    assert oc.register_page_guids(f"{web}\n{same}") == {PAGE}


def _plan_with_applied(tmp_path, applied):
    teema = register_teema("2026_61")
    root = build_capture(
        tmp_path / "g", [{"tag": "001", "guid": _guid(61), "matters": [teema], "files": []}]
    )
    state = teema.lovable_sync_state
    state.applied = {"onenote_link": applied}
    state.save(update_fields=["applied"])
    return oc.build_plan(oc.load_capture(root))


def test_a_link_naming_the_page_only_as_its_notebook_guid_is_refused(tmp_path):
    plan = _plan_with_applied(tmp_path, f"{WEB}?sourcedoc={{{_guid(61)}}}")
    assert any("does not name exactly this page" in p for p in plan.problems)


@pytest.mark.parametrize(
    ("source", "target", "ok"),
    [
        ("page", "page", True),
        ("page", "", True),
        ("", "page", True),
        ("client", "page", True),
        ("page", "other", False),
        ("client-other", "page", False),
        ("notebook", "notebook", False),
    ],
)
def test_the_applied_source_target_dict_is_read_by_both_halves(tmp_path, source, target, ok):
    guid = _guid(61)
    values = {
        "": "",
        "page": stored_link(guid),
        "other": stored_link(_guid(62)),
        "client": f"onenote:https://t.invalid/x/Osa.one#L&section-id={{{SECTION}}}&page-id={{{guid}}}&end",
        "client-other": f"onenote:https://t.invalid/x/Osa.one#L&page-id={{{_guid(62)}}}&end",
        "notebook": f"{WEB}?sourcedoc={{{guid}}}",
    }
    plan = _plan_with_applied(tmp_path, {"source": values[source], "target": values[target]})
    named_problem = any("does not name exactly this page" in p for p in plan.problems)
    assert named_problem is (not ok), plan.problems


# ======================================================================================
# 6. A later capture (docs/adr/0205, amendment of 2026-11-05)
# ======================================================================================


def test_the_same_capture_applied_twice_writes_nothing(tmp_path, enabled):
    teema = register_teema("2026_71")
    root = build(
        tmp_path / "c1", "cap-1", [{"tag": "001", "guid": GUID, "matters": [teema], "files": FIRST}]
    )
    run(root)
    before = fingerprint()
    plan = oc.build_plan(oc.load_capture(root))
    assert oc.select_pages(plan) == [] and oc.select_pages(plan, include_shared=True) == []
    report = oc.apply(
        plan,
        oc.select_pages(plan),
        operator_intent=oc.OPERATOR_INTENT,
        expect_manifest_sha256=plan.capture.manifest_sha256,
        expect_plan_sha256=plan.digest,
        backup_set="sets/qa",
        label="qa",
    )
    assert report.batch_id == "" and report.documents_created == 0
    assert changed(before) == []


def _versions():
    return {
        v.pk: (v.sha256, v.size_bytes, v.storage_key, v.document_id)
        for v in DocumentVersion.objects.all()
    }


def _stored_bytes():
    from app.documents.services import evidence_storage

    storage = evidence_storage()
    out = {}
    for version in DocumentVersion.objects.all():
        with storage.open(version.storage_key, "rb") as handle:
            out[version.pk] = hashlib.sha256(handle.read()).hexdigest()
    return out


def test_a_moved_and_retitled_page_with_new_block_ids_makes_no_second_document(
    later_world, specialist
):
    teema = later_world["teema"]
    business = oc.business_digest([str(teema.pk)])
    held_versions, held_bytes = _versions(), _stored_bytes()
    moved = [
        f("{M1}", "kiri.pdf", "2021-08-30T08:48:10.000Z", comment="Ministeeriumi kiri"),
        f("{M2}", "vastus.docx", "2023-02-01T21:30:00.000Z"),
    ]
    _plan, report = run(
        build(
            later_world["tmp"] / "c2",
            "cap-2",
            [
                {
                    "tag": "001",
                    "guid": GUID,
                    "matters": [teema],
                    "files": moved,
                    "section": "Arhiiv 2",
                    "title": "Uus pealkiri",
                }
            ],
        )
    )
    assert report.documents_created == 0
    assert Document.objects.filter(matter=teema).count() == 3
    assert _versions() == held_versions and _stored_bytes() == held_bytes
    assert oc.business_digest([str(teema.pk)]) == business
    codes = set(
        LegacySourceResourceImport.objects.filter(
            resource__resource_key__in=["{M1}", "{M2}"]
        ).values_list("error_code", flat=True)
    )
    assert codes == {oc.SKIP_CONTINUES}


def test_a_changed_byte_never_overwrites_a_version_and_leaves_the_register_alone(
    later_world, specialist
):
    teema = later_world["teema"]
    business = oc.business_digest([str(teema.pk)])
    held_versions, held_bytes = _versions(), _stored_bytes()
    resources_before = {
        r.pk: (r.sha256, r.size_bytes, r.source_added_at)
        for r in LegacySourceResource.objects.all()
    }
    files = [
        FIRST[0],
        f("{B2}", "vastus.docx", "2023-02-01T21:30:00.000Z", data=b"QA muudetud baidid"),
        FIRST[2],
        f("{B5}", "lisa.pdf", "2025-01-02T03:04:05.000Z"),
    ]
    plan, report = run(
        build(
            later_world["tmp"] / "c2",
            "cap-2",
            [{"tag": "001", "guid": GUID, "matters": [teema], "files": files}],
        )
    )
    assert ("{B2}", "HOLD", oc.HOLD_BYTES_CHANGED) in {
        (i.resource_key, i.disposition, i.reason) for i in plan.items
    }
    assert report.documents_created == 1  # only the new file
    after = _versions()
    assert {pk: after[pk] for pk in held_versions} == held_versions
    assert {pk: v for pk, v in _stored_bytes().items() if pk in held_bytes} == held_bytes
    for pk, values in resources_before.items():
        r = LegacySourceResource.objects.get(pk=pk)
        assert (r.sha256, r.size_bytes, r.source_added_at) == values
    assert oc.business_digest([str(teema.pk)]) == business
    # No batch of this capture is trusted as the earlier one's.
    assert ImportBatch.objects.filter(source_system=oc.BATCH_SOURCE_SYSTEM).count() >= 2
