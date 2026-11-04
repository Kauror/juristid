"""Historical dates and commentary of OneNote attachments — synthetic only.

Everything is invented at test time. These tests fix *our* contract: which
time may date a file, which text may be called its comment, and that neither
is ever borrowed from somewhere else. They prove nothing about Microsoft's
behaviour; where they model it (a block keeping its time when moved), the
test says so.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tools.onenote_ingress.chronology import (
    UNKNOWN_ADDITION,
    ChronologyRow,
    DateBasis,
    OccurrenceDates,
    OccurrenceVersion,
    Qualifier,
    ReferenceDecision,
    ScanObservation,
    SourceTime,
    VersionObservation,
    Window,
    apply_reference_time,
    chronology_date,
    chronology_rows,
    detection_window,
    tallinn_day,
    version_dates,
)
from tools.onenote_ingress.context import (
    BlockKind,
    ContextBasis,
    ContextChange,
    blocks_from_desktop_xml,
    compare_context,
    context_for,
    searchable_context,
)
from tools.onenote_ingress.resources import OccurrenceKind, occurrences


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def block_time(
    moment: datetime,
    *,
    basis: DateBasis = DateBasis.ONENOTE_BLOCK_CREATED,
    capture: str = "c1",
    reader: str = "ONENOTE_DESKTOP",
) -> SourceTime:
    return SourceTime(moment, basis, reader, capture)


def dated(created: datetime | None, **kw) -> OccurrenceDates:
    return OccurrenceDates(block_created=[block_time(created)] if created else [], **kw)


# -- dates ------------------------------------------------------------------------------


def test_several_attachments_on_one_page_read_on_their_own_days():
    stored = utc(2026, 11, 20, 3, 0)
    items = [
        OccurrenceVersion("p/a/1", "d1", True, chronology_date(dated(utc(2021, 8, 30, 8, 48)))),
        OccurrenceVersion("p/b/1", "d2", True, chronology_date(dated(utc(2023, 2, 1, 9, 0)))),
        OccurrenceVersion("p/c/1", "d3", True, chronology_date(dated(utc(2021, 8, 30, 15, 0)))),
    ]
    rows = chronology_rows(items, stored_at=stored)
    assert [r.group for r in rows] == ["day:2021-08-30", "day:2023-02-01"]
    assert rows[0].members == ("p/a/1", "p/c/1")
    assert rows[0].anchor == utc(2021, 8, 30, 8, 48)  # earliest addition that day


def test_the_source_date_is_never_the_technical_import_date():
    dates = dated(
        utc(2022, 5, 4, 10, 0),
        first_detected=Window(utc(2026, 11, 19), utc(2026, 11, 20)),
        bytes_verified_at=utc(2026, 11, 20, 1),
        promoted_at=utc(2026, 11, 21),
    )
    result = chronology_date(dates)
    assert (
        result.value == utc(2022, 5, 4, 10, 0) and result.basis is DateBasis.ONENOTE_BLOCK_CREATED
    )
    rows = chronology_rows([OccurrenceVersion("k", "d", True, result)], stored_at=utc(2026, 11, 21))
    assert rows[0].group == "day:2022-05-04"


def test_an_unknown_date_stays_unknown_and_nothing_else_is_borrowed():
    dates = OccurrenceDates(
        page_created=SourceTime(utc(2020, 1, 1), DateBasis.PAGE_CREATED, "ONENOTE_DESKTOP", "c1"),
        page_modified=SourceTime(utc(2024, 1, 1), DateBasis.PAGE_MODIFIED, "ONENOTE_DESKTOP", "c1"),
        file_internal=[
            SourceTime(
                utc(2019, 6, 1), DateBasis.FILE_INTERNAL_METADATA, "FILE", "c1", detail="OOXML"
            )
        ],
        first_detected=Window(None, utc(2026, 11, 20)),
        promoted_at=utc(2026, 11, 21),
    )
    result = chronology_date(dates)
    assert result.value is None and result.basis is None
    assert result.label == UNKNOWN_ADDITION == "Lisamise kuupäev teadmata"
    assert Qualifier.NO_VERIFIED_SOURCE_TIME in result.qualifiers
    rows = chronology_rows([OccurrenceVersion("k", "d", True, result)], stored_at=utc(2026, 11, 21))
    assert rows[0].group == "undated"


def test_tallinn_days_follow_daylight_saving_both_ways():
    # 29.03.2026: EET (+2) -> EEST (+3) at 01:00 UTC. 25.10.2026: back at 01:00 UTC.
    assert tallinn_day(utc(2026, 3, 28, 21, 59)) == date(2026, 3, 28)  # 23:59 EET
    assert tallinn_day(utc(2026, 3, 28, 22, 30)) == date(2026, 3, 29)  # 00:30 EET
    assert tallinn_day(utc(2026, 7, 1, 21, 0)) == date(2026, 7, 2)  # 00:00 EEST
    assert tallinn_day(utc(2026, 7, 1, 20, 59)) == date(2026, 7, 1)
    assert tallinn_day(utc(2026, 10, 24, 21, 30)) == date(2026, 10, 25)  # 00:30 EEST
    assert tallinn_day(utc(2026, 10, 25, 21, 30)) == date(2026, 10, 25)  # 23:30 EET
    with pytest.raises(ValueError):
        SourceTime(datetime(2026, 1, 1), DateBasis.ONENOTE_BLOCK_CREATED, "ONENOTE_DESKTOP", "c")


def test_a_file_moved_from_an_older_page_keeps_its_time_and_says_so():
    """Modelled: OneNote keeps a block's creationTime when content is moved in."""
    dates = dated(
        utc(2019, 3, 1, 9),
        page_created=SourceTime(utc(2024, 6, 1), DateBasis.PAGE_CREATED, "ONENOTE_DESKTOP", "c1"),
    )
    result = chronology_date(dates)
    assert result.value == utc(2019, 3, 1, 9)
    assert Qualifier.PREDATES_PAGE in result.qualifiers


def test_a_replaced_binary_is_a_new_version_dated_only_by_its_change_window():
    obs = [
        VersionObservation("c1", utc(2026, 1, 10, 12), "aaa", utc(2025, 1, 1, 9)),
        VersionObservation(
            "c2", utc(2026, 1, 11, 12), "aaa", utc(2025, 1, 1, 9)
        ),  # unchanged repeat
        VersionObservation("c3", utc(2026, 1, 12, 12), "bbb", utc(2026, 1, 12, 8)),
    ]
    versions = version_dates(obs)
    assert [(v.sha256, v.ordinal) for v in versions] == [("aaa", 1), ("bbb", 2)]
    assert versions[1].window == Window(utc(2026, 1, 11, 12), utc(2026, 1, 12, 8))
    occurrence = chronology_date(
        dated(
            utc(2025, 1, 1, 9),
            block_modified=[
                block_time(utc(2026, 1, 12, 8), basis=DateBasis.ONENOTE_BLOCK_MODIFIED)
            ],
        )
    )
    assert occurrence.value == utc(
        2025, 1, 1, 9
    )  # the occurrence's date is not the new bytes' date
    assert Qualifier.CONTENT_MAY_BE_NEWER in occurrence.qualifiers
    rows = chronology_rows(
        [
            OccurrenceVersion("o/aaa", "d1", True, occurrence),
            OccurrenceVersion(
                "o/bbb", "d2", True, occurrence, version=2, version_window=versions[1].window
            ),
        ],
        stored_at=utc(2026, 1, 13),
    )
    assert [r.group for r in rows] == ["day:2025-01-01", "updated-window"]  # window spans two days


def test_file_internal_dates_later_than_the_block_flag_the_bytes_never_the_date():
    dates = dated(
        utc(2025, 4, 1, 10),
        file_internal=[
            SourceTime(
                utc(2025, 6, 15),
                DateBasis.FILE_INTERNAL_METADATA,
                "FILE",
                "c1",
                detail="ASIC signing",
            )
        ],
    )
    result = chronology_date(dates)
    assert result.value == utc(2025, 4, 1, 10)
    assert Qualifier.CONTENT_MAY_BE_NEWER in result.qualifiers


def test_a_source_time_that_changes_between_observations_becomes_unknown_not_overwritten():
    dates = OccurrenceDates(
        block_created=[
            block_time(utc(2024, 2, 2, 10), capture="c1"),
            block_time(utc(2024, 5, 5, 10), capture="c2"),
        ]
    )
    result = chronology_date(dates)
    assert result.value is None and Qualifier.SOURCE_TIME_CHANGED in result.qualifiers
    assert len(dates.block_created) == 2  # both observations kept


def test_a_graph_occurrence_takes_a_desktop_time_only_through_a_verified_mapping_and_equal_bytes():
    desktop = block_time(utc(2023, 9, 9, 9))
    empty = OccurrenceDates()
    assert chronology_date(empty).value is None  # Graph HTML carries no attachment time
    assert (
        apply_reference_time(
            empty, desktop, mapping_verified=False, graph_sha256="x", desktop_sha256="x"
        )
        is ReferenceDecision.MAPPING_NOT_VERIFIED
    )
    assert (
        apply_reference_time(
            empty, desktop, mapping_verified=True, graph_sha256="x", desktop_sha256="y"
        )
        is ReferenceDecision.BYTES_DIFFER
    )
    page_time = SourceTime(utc(2020, 1, 1), DateBasis.PAGE_CREATED, "GRAPH", "g1")
    assert (
        apply_reference_time(
            empty, page_time, mapping_verified=True, graph_sha256="x", desktop_sha256="x"
        )
        is ReferenceDecision.NOT_A_BLOCK_TIME
    )
    assert chronology_date(empty).value is None
    assert (
        apply_reference_time(
            empty, desktop, mapping_verified=True, graph_sha256="x", desktop_sha256="x"
        )
        is ReferenceDecision.APPLIED
    )
    assert chronology_date(empty).value == utc(2023, 9, 9, 9)


def test_a_first_detection_window_is_bounded_only_by_complete_scans_and_is_not_a_source_date():
    obs = [
        ScanObservation(utc(2026, 11, 1), complete=True, present=False),
        ScanObservation(utc(2026, 11, 2), complete=False, present=False),  # proves nothing
        ScanObservation(utc(2026, 11, 3), complete=True, present=True),
    ]
    assert detection_window(obs) == Window(utc(2026, 11, 1), utc(2026, 11, 3))
    assert detection_window(obs[1:]) == Window(None, utc(2026, 11, 3))  # unbounded below
    result = chronology_date(OccurrenceDates(first_detected=detection_window(obs)))
    assert result.value is None and Qualifier.DETECTION_WINDOW_ONLY in result.qualifiers


def test_repeat_ingestion_draws_the_same_rows_without_duplicates():
    one = OccurrenceVersion("p/a/sha", "d1", True, chronology_date(dated(utc(2022, 1, 5, 8))))
    first = chronology_rows([one], stored_at=utc(2026, 11, 1))
    again = chronology_rows([one, one, one], stored_at=utc(2026, 11, 2))
    assert first == again == [ChronologyRow("day:2022-01-05", utc(2022, 1, 5, 8), ("p/a/sha",), ())]


def test_restricted_files_neither_date_count_nor_expose_context():
    secret = OccurrenceVersion(
        "p/s/1", "d-secret", False, chronology_date(dated(utc(2020, 2, 2, 8)))
    )
    open_ = OccurrenceVersion("p/o/1", "d-open", True, chronology_date(dated(utc(2021, 3, 3, 8))))
    rows = chronology_rows([secret, open_], stored_at=utc(2026, 11, 1))
    assert [r.group for r in rows] == ["day:2021-03-03"] and rows[0].members == ("p/o/1",)
    blocks = blocks_from_desktop_xml(
        page([oe_file("F1", children=[oe_text("N1", "Konfidentsiaalne märkus")])])
    )
    records = context_for(blocks, 0, capture_id="c1")
    assert searchable_context(records, document_visible=False) == ()
    assert searchable_context(records, document_visible=True) == ("Konfidentsiaalne märkus",)


# -- comments and context --------------------------------------------------------------------

ONE = "http://schemas.microsoft.com/office/onenote/2013/onenote"


def oe_text(
    oid: str,
    text: str,
    *,
    created: str = "2024-01-01T10:00:00.000Z",
    children: list[str] | None = None,
) -> str:
    kids = f"<one:OEChildren>{''.join(children)}</one:OEChildren>" if children else ""
    head = f'<one:OE objectID="{oid}" creationTime="{created}" lastModifiedTime="{created}">'
    return f"{head}<one:T><![CDATA[<span>{text}</span>]]></one:T>{kids}</one:OE>"


def oe_file(
    oid: str,
    name: str = "otsus.pdf",
    *,
    created: str = "2024-01-01T10:01:00.000Z",
    children: list[str] | None = None,
) -> str:
    kids = f"<one:OEChildren>{''.join(children)}</one:OEChildren>" if children else ""
    head = f'<one:OE objectID="{oid}" creationTime="{created}" lastModifiedTime="{created}">'
    return f'{head}<one:InsertedFile pathCache="x" preferredName="{name}"/>{kids}</one:OE>'


def oe_unfetched(oid: str) -> str:
    moment = "2024-01-01T10:00:00.000Z"
    return f'<one:OE objectID="{oid}" creationTime="{moment}" lastModifiedTime="{moment}"/>'


def page(*outlines: list[str]) -> str:
    body = "".join(
        f"<one:Outline><one:OEChildren>{''.join(items)}</one:OEChildren></one:Outline>"
        for items in outlines
    )
    return f'<one:Page xmlns:one="{ONE}" ID="P" name="Leht">{body}</one:Page>'


def ordinal_of(blocks, oid: str) -> int:
    return next(b.ordinal for b in blocks if b.object_id == oid)


def test_a_note_nested_under_a_file_is_that_files_comment():
    blocks = blocks_from_desktop_xml(
        page(
            [
                oe_text("T0", "Sissejuhatus"),
                oe_file("F1", children=[oe_text("N1", "Ministeeriumi vastus, saabus e-postiga")]),
            ]
        )
    )
    records = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")
    assert records[0].basis is ContextBasis.ATTACHED_NOTE and records[0].attributed
    assert records[0].text == ("Ministeeriumi vastus, saabus e-postiga",)


def test_a_paragraph_clearly_captioning_one_attachment_is_a_candidate_not_a_claim():
    blocks = blocks_from_desktop_xml(
        page(
            [
                oe_text("T1", "Lisan Koja arvamuse eelnõule:"),
                oe_file("F1"),
                oe_text("T2", "Järgmine samm: ootame vastust"),
            ]
        )
    )
    records = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")
    adjacent = next(r for r in records if r.basis is ContextBasis.ADJACENT_SINGLE_FILE)
    assert adjacent.text == ("Lisan Koja arvamuse eelnõule:", "Järgmine samm: ootame vastust")
    assert not adjacent.attributed  # shown as context beside the file, never as its comment


def test_nearby_text_for_several_files_is_attributed_to_none():
    blocks = blocks_from_desktop_xml(
        page(
            [
                oe_text("T1", "Kolm lisa:"),
                oe_file("F1", "a.pdf"),
                oe_file("F2", "b.pdf"),
                oe_unfetched("U1"),
                oe_text("T2", "Lõpp"),
            ]
        )
    )
    for oid in ("F1", "F2"):
        records = context_for(blocks, ordinal_of(blocks, oid), capture_id="c1")
        kinds = {r.basis for r in records}
        assert (
            ContextBasis.ADJACENT_SHARED in kinds and ContextBasis.ADJACENT_SINGLE_FILE not in kinds
        )
        assert searchable_context(records, document_visible=True) == ()
    assert blocks[ordinal_of(blocks, "U1")].kind is BlockKind.UNFETCHED_OBJECT


def test_a_single_file_next_to_an_unfetched_object_is_not_single():
    blocks = blocks_from_desktop_xml(
        page([oe_text("T1", "Lisa:"), oe_file("F1"), oe_unfetched("U1"), oe_text("T2", "x")])
    )
    records = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")
    assert ContextBasis.ADJACENT_SHARED in {r.basis for r in records}


def test_two_same_named_attachments_keep_their_own_comments():
    blocks = blocks_from_desktop_xml(
        page(
            [
                oe_file("F1", "lisa.pdf", children=[oe_text("N1", "Esimene versioon, 2023")]),
                oe_text("T", "vahepeal"),
                oe_file("F2", "lisa.pdf", children=[oe_text("N2", "Parandatud, 2024")]),
            ]
        )
    )
    first = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")[0]
    second = context_for(blocks, ordinal_of(blocks, "F2"), capture_id="c1")[0]
    assert (first.occurrence, first.text) == ("F1", ("Esimene versioon, 2023",))
    assert (second.occurrence, second.text) == ("F2", ("Parandatud, 2024",))


def test_text_in_another_outline_is_never_adjacent():
    blocks = blocks_from_desktop_xml(page([oe_file("F1")], [oe_text("T9", "Teine kast lehel")]))
    records = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")
    assert {r.basis for r in records} == {ContextBasis.PAGE_NARRATIVE}


def test_a_file_under_a_heading_gets_the_heading_as_container_text():
    blocks = blocks_from_desktop_xml(
        page([oe_text("H", "2024 kooskõlastus", children=[oe_file("F1")])])
    )
    records = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")
    assert records[0].basis is ContextBasis.CONTAINER_HEADING and records[0].text == (
        "2024 kooskõlastus",
    )


def test_an_updated_comment_without_new_bytes_is_a_new_context_record_not_a_new_version():
    before = blocks_from_desktop_xml(page([oe_file("F1", children=[oe_text("N1", "Mustand")])]))
    after = blocks_from_desktop_xml(
        page([oe_file("F1", children=[oe_text("N1", "Lõplik, saadetud 12.3")])])
    )
    old = context_for(before, 0, capture_id="c1")[0]
    new = context_for(after, 0, capture_id="c2")[0]
    assert compare_context(old, new) is ContextChange.TEXT_CHANGED
    assert old.text == ("Mustand",)  # the earlier capture's record is untouched
    obs = [
        VersionObservation("c1", utc(2026, 1, 1), "same", None),
        VersionObservation("c2", utc(2026, 2, 1), "same", None),
    ]
    assert len(version_dates(obs)) == 1


def test_printout_images_are_never_mistaken_for_the_attachment():
    scope = "/v1.0/sites/s"
    url = f"https://graph.microsoft.com{scope}/onenote/resources/r1/$value"
    html = f'<img src="{url}" data-fullres-src="{url}" data-options="printout" data-index="0"/>'
    (occ,) = occurrences("p", html, site_scope_path=scope)
    assert occ.kind is OccurrenceKind.PRINTOUT_IMAGE


def test_window_rows_fit_a_single_day_when_they_can():
    occ = chronology_date(dated(utc(2025, 1, 1, 9)))
    w = Window(utc(2026, 1, 12, 6), utc(2026, 1, 12, 9))
    rows = chronology_rows(
        [OccurrenceVersion("o/2", "d", True, occ, version=2, version_window=w)],
        stored_at=utc(2026, 2, 1),
    )
    assert rows[0].group == "updated:2026-01-12"
    assert timedelta(0) < rows[0].anchor - w.not_before  # type: ignore[operator]


# -- D1 pilot findings ---------------------------------------------------------------------


def test_a_list_item_holding_only_its_bullet_may_hide_an_unfetched_file():
    hidden = (
        '<one:OE objectID="L1" creationTime="2024-01-01T10:00:00.000Z">'
        '<one:List><one:Bullet bullet="2"/></one:List></one:OE>'
    )
    blocks = blocks_from_desktop_xml(
        page([oe_text("T1", "Lisa:"), oe_file("F1"), hidden, oe_text("T2", "x")])
    )
    assert blocks[ordinal_of(blocks, "L1")].kind is BlockKind.UNFETCHED_OBJECT
    records = context_for(blocks, ordinal_of(blocks, "F1"), capture_id="c1")
    assert ContextBasis.ADJACENT_SHARED in {r.basis for r in records}  # not "single" any more


def test_a_floating_file_has_no_date_and_no_position():
    floating = (
        '<one:InsertedFile objectID="FL" lastModifiedTime="2026-11-04T12:03:39.000Z" '
        'pathCache="x" preferredName="a.pdf"/>'
    )
    xml = page([oe_text("T1", "Tekst")]).replace("</one:Page>", floating + "</one:Page>")
    blocks = blocks_from_desktop_xml(xml)
    block = blocks[ordinal_of(blocks, "FL")]
    assert block.kind is BlockKind.FILE and block.created == "" and block.outline == -1
    records = context_for(blocks, block.ordinal, capture_id="c1")
    assert [r.basis for r in records] == [ContextBasis.PAGE_NARRATIVE]
    result = chronology_date(OccurrenceDates())  # no creationTime -> no addition date
    assert result.value is None and result.label == UNKNOWN_ADDITION
