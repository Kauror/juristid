"""The full Lovable inventory (LS-A): the full reading and the per-topic ledger.

Lovable is a fake transport serving invented rows; nothing leaves the process.
Every title, name, reference, page GUID and identifier below is synthetic.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.errors import ImmutableRecordError
from app.legacy_import import lovable_inventory
from app.legacy_import.lovable_inventory import (
    Category,
    InventoryError,
    build_ledger,
    latest_inventory,
    near_identical,
    store_run,
)
from app.legacy_import.lovable_inventory_models import (
    InventoryDecisionKind,
    LovableInventoryDecision,
    LovableInventoryRun,
)
from app.legacy_import.lovable_observation import take_observation, verify_observation
from app.legacy_import.lovable_source import OBSERVE_TABLES, SourceError
from app.legacy_import.lovable_sync import TALLINN
from app.legacy_import.lovable_sync_models import LovableSyncCohort, LovableSyncState
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.legacy_import.source_pages import LegacySourcePage, MatterSourcePage, SourceMatchMethod
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db

TAKEN = dt.datetime(2026, 11, 9, 0, 20, tzinfo=TALLINN)


def sid(n: int) -> str:
    return str(uuid.UUID(int=0xB0000000_0000_4000_8000_000000000000 + n))


def page_guid(n: int) -> str:
    return f"{n:08X}-AAAA-4BBB-8CCC-DDDDDDDDDDDD"


def onenote_link(n: int) -> str:
    return (
        "https://example.invalid/sites/naidis/_layouts/Doc.aspx?sourcedoc={EEEE}"
        f"&wd=target(Näidis.one|11111111-2222-4333-8444-555555555555/Leht|{page_guid(n)}/)"
    )


def topic(
    n: int,
    *,
    nr: str | None = None,
    aasta: int | None = 2026,
    operational: bool = False,
    title: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    row = {
        "id": sid(n),
        "nr": nr if nr is not None else f"2026_{900 + n}",
        "aasta": aasta,
        "teema": title if title is not None else f"Näidisteema number {n} kohta",
        "oigusakt": "",
        "sisse": "2026-01-05",
        "arvamuse_tahtaeg": None,
        "valja": "",
        "valja_kuupaev": None,
        "vastutaja": "Mari",
        "uus_vastutaja": "",
        "hetkeseis": "jõustunud",
        "jargmiseks": "",
        "koja_ettepanek": False,
        "kellele": "",
        "tegevuse_tahtaeg": None,
        "onenote_link": "",
        "operatiivne_ulatus": operational,
        "joustumised_hallatud": False,
        "version": 1,
        "updated_at": "2026-10-01T10:00:00+00:00",
        # Never read into the reading, and dropped if a source ever sent them.
        "markmed": f"SISEMINE-{n}",
        "mitteametlik_arvamus": "mitteametlik",
        "allikas": "toorrida",
    }
    row.update(values)
    return row


class FakeLovable:
    """Lovable's PostgREST for the full reading: allowlisted tables, served
    whole; every column asked for and nothing else."""

    def __init__(self, topics: list[dict[str, Any]]) -> None:
        self.topics = {t["id"]: dict(t) for t in topics}
        self.children: dict[str, list[dict[str, Any]]] = {
            t: [] for t in OBSERVE_TABLES if t != "topic"
        }
        self.calls: list[str] = []
        self.on_select: Any = None

    def link(self, prev: int, nxt: int) -> None:
        self.children["topic_stage_link"].append(
            {"prev_topic_id": sid(prev), "next_topic_id": sid(nxt)}
        )

    def select(self, table, columns, *, filters, order):
        self.calls.append(table)
        if self.on_select is not None:
            self.on_select(self, table)
        assert filters == {}
        rows = (
            sorted(self.topics.values(), key=lambda r: r["id"])
            if table == "topic"
            else self.children[table]
        )
        return [{c: r.get(c) for c in columns if c in r} for r in rows]


def reading(fake: FakeLovable) -> dict[str, Any]:
    document = take_observation(fake, now=lambda: TAKEN, pause=lambda _s: None, detail="all")
    document.pop("children_rows", None)
    return document


# -- Juristid-side helpers (synthetic) ------------------------------------------


def register_teema(n: int, *, cohort: str = "lovable-register", **matter: Any) -> Matter:
    """A live register Teema carrying topic ``n``'s identity through its state."""
    m = factories.MatterFactory(reference_year=2026, reference_number=900 + n, **matter)
    LovableSyncState.objects.create(
        matter=m, source_id=sid(n), reference=f"2026_{900 + n}", cohort=cohort
    )
    return m


def enrol_register() -> None:
    LovableSyncCohort.objects.create(
        key="lovable-register",
        pilot="lovable-register",
        package_sha256="c" * 64,
        expected=1,
        enrolled_at=timezone.now(),
        reason="näidis",
    )


def lovable_reference(m: Matter, n: int, *, stage: str = "jõustunud") -> MatterSourceReference:
    batch = factories_import_batch()
    return MatterSourceReference.objects.create(
        matter=m,
        import_batch=batch,
        source_system="LOVABLE",
        source_file_name="public.topic",
        source_snapshot_sha256="d" * 64,
        source_sheet="public.topic:2026",
        source_row_number=900 + n,
        source_row_raw={"lovable": {"id": sid(n)}, "row": {"hetkeseis": stage}},
        source_contract_version="juristid.lovable-pilot/2",
    )


def factories_import_batch():
    from app.legacy_import.models import ImportBatch

    return ImportBatch.objects.create(
        source_system="LOVABLE", importer_version="test/1", started_at=timezone.now()
    )


def tombstone(*, lovable_id: str | None = None, reference: str | None = None) -> Matter:
    m = factories.MatterFactory()
    if lovable_id:
        EvidencePilotRecord.objects.create(
            pilot="lovable-operational",
            pilot_version="2.0",
            package_sha256="e" * 64,
            record_key=f"k-{lovable_id}",
            record_kind="MATTER",
            matter=m,
            target_model="matters.Matter",
            target_id=m.pk,
            evidence={"lovable_id": lovable_id},
        )
    Matter.all_objects.filter(pk=m.pk).update(
        deleted_at=timezone.now(), reference_year=None, reference_number=None
    )
    if reference:
        record_security_event(
            event_type=SecurityEventType.MATTER_DELETED,
            subject=m,
            detail={"reference": reference, "counts": {}},
        )
    return m


def archive_page(n: int) -> LegacySourcePage:
    now = timezone.now()
    return LegacySourcePage.objects.create(
        source_page_id="{" + page_guid(n) + "}",
        page_key=f"p-{page_guid(n).lower()}",
        source_notebook="Näidis",
        source_section="Arhiiv",
        title="Näidisleht",
        capture_id="naidis-capture",
        first_imported_at=now,
        latest_imported_at=now,
    )


def row_of(ledger: lovable_inventory.Ledger, n: int) -> lovable_inventory.LedgerRow:
    (row,) = [r for r in ledger.rows if r.source_id == sid(n)]
    return row


# ---------------------------------------------------------------------------
# The full reading
# ---------------------------------------------------------------------------


def test_the_default_observation_document_is_unchanged_by_the_full_option():
    fake = FakeLovable([topic(1, operational=True), topic(2)])
    default = take_observation(fake, now=lambda: TAKEN, pause=lambda _s: None)
    assert set(default) == {
        "format",
        "schema",
        "project",
        "taken_at",
        "attempts",
        "index",
        "detail",
        "sha256",
        "children_rows",
    }
    assert set(default["detail"]) == {sid(1)}


def test_a_full_reading_keeps_every_topic_and_never_an_excluded_column():
    fake = FakeLovable([topic(1, operational=True), topic(2), topic(3)])
    fake.link(2, 1)
    document = reading(fake)
    verify_observation(document)
    assert document["detail_scope"] == "all"
    assert set(document["detail"]) == set(document["bundles"]) == {sid(1), sid(2), sid(3)}
    text = json.dumps(document, ensure_ascii=False)
    for never in ("markmed", "mitteametlik_arvamus", "allikas", "SISEMINE", "toorrida"):
        assert never not in text
    assert document["bundles"][sid(2)]["stage_links"] == [{"prev": sid(2), "next": sid(1)}]
    # The same GET-only reads, twice: topic and the four child tables per pass.
    assert fake.calls.count("topic") == 2


def test_a_full_reading_is_repeated_when_the_source_moves_mid_read():
    fake = FakeLovable([topic(1), topic(2)])
    seen = {"n": 0}

    def edit(source: FakeLovable, table: str) -> None:
        if table == "topic":
            seen["n"] += 1
            if seen["n"] == 2:  # the second pass of the first attempt sees an edit
                source.topics[sid(1)]["version"] = 2

    fake.on_select = edit
    document = reading(fake)
    assert document["attempts"] == 2


def test_an_unknown_detail_scope_is_refused():
    with pytest.raises(SourceError, match="detail is one of"):
        take_observation(FakeLovable([topic(1)]), detail="some")


def test_the_ledger_refuses_a_reading_without_full_detail():
    fake = FakeLovable([topic(1, operational=True), topic(2)])
    document = take_observation(fake, now=lambda: TAKEN, pause=lambda _s: None)
    document.pop("children_rows")
    with pytest.raises(InventoryError, match="have no detail"):
        build_ledger(document)


def test_the_ledger_refuses_a_tampered_reading():
    document = reading(FakeLovable([topic(1)]))
    document["index"][0]["nr"] = "2026_1"
    with pytest.raises(InventoryError, match="digest does not match"):
        build_ledger(document)


# ---------------------------------------------------------------------------
# Every category, on synthetic data
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db):
    """One topic for every category (and the flags), and the Juristid side."""
    enrol_register()
    topics = [
        # 1 EXACT (enrolled register Teema), operational and ongoing.
        topic(1, operational=True, hetkeseis="Riigikogus", title="Esimese näidise teema"),
        # 2 EXACT through a canonical Lovable reference only (not enrolled).
        topic(2, title="Teise näidise teema"),
        # 3 LINK_ENRICHMENT: a Teema with the reference and no Lovable identity.
        topic(3, title="Kolmanda näidise teema"),
        # 4 ARCHIVE_UNLINKED: its OneNote page is in the archive.
        topic(4, onenote_link=onenote_link(4), title="Neljanda näidise teema"),
        # 5 ABSENT, proven.
        topic(5, title="Viienda näidise teema"),
        # 6 ABSENT, its UUID only on a tombstone of the replacement.
        topic(6, title="Kuuenda näidise teema"),
        # 7 + 8 POSSIBLE_DUPLICATE: two source topics share one reference.
        topic(7, nr="2026_977", title="Seitsmenda näidise teema"),
        topic(8, nr="2026_977", title="Kaheksanda näidise teema"),
        # 9 + 10 POSSIBLE_DUPLICATE: same-year near-identical titles.
        topic(9, title="Riigilõivuseaduse muutmise seaduse eelnõu"),
        topic(10, title="Riigilõivuseaduse muutmise seaduse eelnõu."),
        # 11 EXCLUDED: no valid reference.
        topic(11, nr="ilma numbrita", title="Üheteistkümnenda näidise teema"),
        # 12 EXCLUDED: aasta disagrees with nr.
        topic(12, aasta=2025, title="Kaheteistkümnenda näidise teema"),
        # 13 ABSENT with a reference a tombstone once held.
        topic(13, title="Kolmeteistkümnenda näidise teema"),
        # 14 → 15 a stage link with near-identical titles: never a duplicate.
        topic(14, title="Toote nõuetele vastavuse seaduse muutmine"),
        topic(
            15,
            title="Toote nõuetele vastavuse seaduse muutmine II",
            operational=True,
            hetkeseis="Riigikogus",
        ),
        # 16 near-identical to the EXACT topic 1: a candidate, never EXACT.
        topic(16, title="Esimese näidise teema", aasta=2026),
    ]
    fake = FakeLovable(topics)
    fake.link(14, 15)
    exact = register_teema(1)
    by_reference = factories.MatterFactory(reference_year=2026, reference_number=902)
    lovable_reference(by_reference, 2)
    enrichment = factories.MatterFactory(reference_year=2026, reference_number=903)
    page = archive_page(4)
    other = factories.MatterFactory(reference_year=2024, reference_number=4)
    MatterSourcePage.objects.create(
        matter=other, source_page=page, match_method=SourceMatchMethod.MANUAL
    )
    tombstone(lovable_id=sid(6), reference="2026_906")
    tombstone(reference="2026_913")
    # 99: a live register Teema whose topic is gone from the source.
    gone = register_teema(99)
    return {
        "fake": fake,
        "exact": exact,
        "by_reference": by_reference,
        "enrichment": enrichment,
        "other": other,
        "gone": gone,
    }


def test_every_category_from_synthetic_data(world):
    ledger = build_ledger(reading(world["fake"]))
    expected = {
        1: (Category.EXACT, "EXACT_ENROLLED"),
        2: (Category.EXACT, "EXACT_NOT_ENROLLED"),
        3: (Category.LINK_ENRICHMENT, "SAME_REFERENCE_NO_IDENTITY"),
        4: (Category.ARCHIVE_UNLINKED, "ARCHIVE_PAGE_GUID"),
        5: (Category.ABSENT, "PROVEN_ABSENT"),
        6: (Category.ABSENT, "PROVEN_ABSENT"),
        7: (Category.POSSIBLE_DUPLICATE, "SOURCE_REFERENCE_SHARED"),
        8: (Category.POSSIBLE_DUPLICATE, "SOURCE_REFERENCE_SHARED"),
        9: (Category.POSSIBLE_DUPLICATE, "NEAR_IDENTICAL_TITLE"),
        10: (Category.POSSIBLE_DUPLICATE, "NEAR_IDENTICAL_TITLE"),
        11: (Category.EXCLUDED, "NO_VALID_REFERENCE"),
        12: (Category.EXCLUDED, "REFERENCE_YEAR_MISMATCH"),
        13: (Category.ABSENT, "PROVEN_ABSENT"),
        14: (Category.ABSENT, "PROVEN_ABSENT"),
        15: (Category.ABSENT, "PROVEN_ABSENT"),
        16: (Category.POSSIBLE_DUPLICATE, "NEAR_IDENTICAL_TITLE"),
        99: (Category.DELETED_OR_SUPERSEDED, "SOURCE_MISSING"),
    }
    found = {n: (row_of(ledger, n).category, row_of(ledger, n).reason) for n in expected}
    assert found == expected
    assert {c for c, _ in expected.values()} == set(Category.values)

    assert row_of(ledger, 1).matter_id == str(world["exact"].pk)
    assert row_of(ledger, 1).cohort == "lovable-register"
    assert row_of(ledger, 2).matter_id == str(world["by_reference"].pk)
    assert row_of(ledger, 3).matter_id == str(world["enrichment"].pk)
    assert row_of(ledger, 4).archive_pages and row_of(ledger, 4).matter_id is None
    # The archive page is linked to another Teema: a suggestion, not a link.
    assert [c["basis"] for c in row_of(ledger, 4).candidates] == ["SHARED_ARCHIVE_PAGE"]
    gone = row_of(ledger, 99)
    assert gone.in_source is False and gone.matter_id == str(world["gone"].pk)


def test_a_uuid_or_reference_only_on_a_tombstone_is_absent_with_a_note(world):
    ledger = build_ledger(reading(world["fake"]))
    assert row_of(ledger, 6).category == Category.ABSENT
    assert "TOMBSTONE_UUID" in row_of(ledger, 6).notes
    assert "TOMBSTONE_REFERENCE" in row_of(ledger, 6).notes
    assert row_of(ledger, 13).notes == ["TOMBSTONE_REFERENCE"]
    assert row_of(ledger, 5).notes == []


def test_fuzzy_title_candidates_never_become_exact(world):
    ledger = build_ledger(reading(world["fake"]))
    lookalike = row_of(ledger, 16)
    assert lookalike.category == Category.POSSIBLE_DUPLICATE
    assert lookalike.matter_id is None
    assert lookalike.candidates == [
        {
            "source_id": sid(1),
            "reference": "2026_901",
            "matter_id": str(world["exact"].pk),
            "basis": "NEAR_IDENTICAL_TITLE",
        }
    ]
    # The exact topic stays exact and does not take the lookalike as a candidate.
    assert row_of(ledger, 1).category == Category.EXACT and row_of(ledger, 1).candidates == []
    assert sum(1 for r in ledger.rows if r.matter_id == str(world["exact"].pk)) == 1


def test_a_stage_linked_pair_is_flagged_never_a_duplicate(world):
    ledger = build_ledger(reading(world["fake"]))
    predecessor = row_of(ledger, 14)
    assert predecessor.superseded_in_source is True
    assert predecessor.candidates == []
    assert row_of(ledger, 15).superseded_in_source is False


def test_exactly_one_category_per_row_and_totals_equal_rows(world):
    ledger = build_ledger(reading(world["fake"]))
    counts = ledger.counts()
    assert counts["rows"] == len(ledger.rows) == 17
    assert sum(counts["by_category"].values()) == counts["rows"]
    assert counts["source_topics"] == 16 and counts["juristid_only"] == 1
    assert counts["operational"] + counts["historical"] == counts["source_topics"]
    assert sum(counts["by_category_operational"].values()) == counts["operational"]
    assert sum(counts["by_category_historical"].values()) == counts["historical"]
    assert counts["excluded_by_reason"] == {
        "NO_VALID_REFERENCE": 1,
        "REFERENCE_YEAR_MISMATCH": 1,
    }
    assert len({r.source_id for r in ledger.rows}) == len(ledger.rows)
    assert all(r.category in Category.values for r in ledger.rows)


def test_building_the_ledger_writes_nothing(world):
    document = reading(world["fake"])
    before = (
        Matter.all_objects.count(),
        LovableSyncState.objects.count(),
        LovableInventoryRun.objects.count(),
    )
    with CaptureQueriesContext(connection) as queries:
        build_ledger(document)
    statements = [q["sql"].lstrip().split(None, 1)[0].upper() for q in queries.captured_queries]
    assert set(statements) <= {"SELECT"}, statements
    assert len(statements) <= 12  # a handful of SELECTs, never one per topic
    after = (
        Matter.all_objects.count(),
        LovableSyncState.objects.count(),
        LovableInventoryRun.objects.count(),
    )
    assert before == after


# ---------------------------------------------------------------------------
# Flags and the harder duplicates
# ---------------------------------------------------------------------------


def test_activity_and_the_open_closed_stage_and_continuation_mismatches():
    enrol_register()
    fake = FakeLovable(
        [
            # Ongoing in the source, closed in Juristid; its successor is 2.
            topic(1, operational=True, hetkeseis="Riigikogus"),
            topic(2, operational=True, hetkeseis="Riigikogus"),
            # Finished in the source, open in Juristid, Juristid names a successor.
            topic(3, operational=True, hetkeseis="jõustunud"),
        ]
    )
    fake.link(1, 2)
    one = register_teema(1, is_open=False, disposition="COMPLETED", record_mode="ARCHIVE")
    two = register_teema(2)
    three = register_teema(3)
    Matter.all_objects.filter(pk=three.pk).update(superseded_by=two)
    lovable_reference(two, 2, stage="Valitsuses")
    ledger = build_ledger(reading(fake))

    first, second, third = row_of(ledger, 1), row_of(ledger, 2), row_of(ledger, 3)
    assert (first.activity, second.activity, third.activity) == (
        "CONTINUES",
        "ONGOING",
        "FINISHED",
    )
    assert first.open_closed_mismatch is False  # continues elsewhere and closed: agreed
    assert first.continuation_mismatch == "SOURCE_ONLY_CONTINUATION"
    assert one.superseded_by_id is None
    assert second.open_closed_mismatch is False and second.continuation_mismatch == ""
    assert second.stage_mismatch is True and second.stage_basis == "IMPORTED_SOURCE_STAGE"
    assert third.open_closed_mismatch is True
    assert third.continuation_mismatch == "JURISTID_ONLY_CONTINUATION"
    counts = ledger.counts()
    assert counts["open_closed_mismatch"] == 1 and counts["continuation_mismatch"] == 2


def test_a_successor_the_source_names_differently_is_a_continuation_mismatch():
    enrol_register()
    fake = FakeLovable([topic(n, operational=True, hetkeseis="Riigikogus") for n in (1, 2, 3)])
    fake.link(1, 2)
    first = register_teema(1, is_open=False, disposition="SUPERSEDED", record_mode="ARCHIVE")
    register_teema(2)
    third = register_teema(3)
    Matter.all_objects.filter(pk=first.pk).update(superseded_by=third)
    assert row_of(build_ledger(reading(fake)), 1).continuation_mismatch == "SUCCESSOR_DIFFERS"


def test_a_reference_held_by_another_lovable_identity_needs_a_person():
    enrol_register()
    # Juristid's 2026_901 follows topic 1, which the source renumbered to 2026_950;
    # the source gave 2026_901 to topic 2.
    register_teema(1)
    fake = FakeLovable([topic(1, nr="2026_950"), topic(2, nr="2026_901")])
    ledger = build_ledger(reading(fake))
    assert row_of(ledger, 1).category == Category.EXACT
    assert "RENUMBERED_IN_SOURCE" in row_of(ledger, 1).notes
    assert (row_of(ledger, 2).category, row_of(ledger, 2).reason) == (
        Category.POSSIBLE_DUPLICATE,
        "REFERENCE_HELD_BY_OTHER_IDENTITY",
    )


def test_one_uuid_on_two_teemad_and_one_teema_with_two_identities_are_never_exact():
    enrol_register()
    a = register_teema(1)
    twin = factories.MatterFactory(reference_year=2024, reference_number=1)
    lovable_reference(twin, 1)
    b = register_teema(2)
    lovable_reference(b, 3)  # Teema b also records topic 3's identity
    fake = FakeLovable([topic(1), topic(2), topic(3)])
    ledger = build_ledger(reading(fake))
    assert row_of(ledger, 1).reason == "UUID_ON_SEVERAL_TEEMAD"
    assert {c["matter_id"] for c in row_of(ledger, 1).candidates} == {str(a.pk), str(twin.pk)}
    assert row_of(ledger, 2).reason == row_of(ledger, 3).reason == "TEEMA_HOLDS_SEVERAL_IDENTITIES"
    assert not ledger.by_category(Category.EXACT)


def test_a_retired_cohort_state_is_exact_but_says_so():
    register_teema(1, cohort="lovable-operational")
    ledger = build_ledger(reading(FakeLovable([topic(1)])))
    assert (row_of(ledger, 1).category, row_of(ledger, 1).reason) == (
        Category.EXACT,
        "EXACT_COHORT_RETIRED",
    )


@pytest.mark.parametrize(
    ("a", "b", "near"),
    [
        ("riigilõivuseaduse muutmine", "riigilõivuseaduse muutmine", True),
        ("riigilõivuseaduse muutmise eelnõu", "riigilõivuseaduse muutmise eelnõud", True),
        ("lühike", "lühikene", False),  # too short to pair on similarity
        ("tulumaksuseaduse muutmine", "käibemaksuseaduse muutmine", False),
    ],
)
def test_near_identical_titles(a, b, near):
    assert near_identical(a, b) is near


# ---------------------------------------------------------------------------
# Storing, the report and the command
# ---------------------------------------------------------------------------


def test_store_run_and_latest_inventory(world):
    ledger = build_ledger(reading(world["fake"]))
    assert latest_inventory() is None
    run = store_run(ledger)
    assert latest_inventory() == run
    assert run.counts == ledger.counts()
    assert run.ledger_sha256 == ledger.sha256()
    assert len(run.rows) == len(ledger.rows)
    assert "Näidis" not in json.dumps(run.rows, ensure_ascii=False)  # no title is stored


def test_the_ledger_command_writes_the_report_and_stores_only_when_asked(world, tmp_path):
    document = reading(world["fake"])
    saved = tmp_path / "reading.json"
    saved.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    call_command("lovable_inventory", "ledger", "--document", str(saved), "--out", str(out))
    assert LovableInventoryRun.objects.count() == 0
    for name in ("ledger.json", "ledger.csv", "coverage.md"):
        assert (out / name).is_file()
    coverage = (out / "coverage.md").read_text(encoding="utf-8")
    assert "| EXACT | 2 |" in coverage and "2026_977" in coverage
    lines = (out / "ledger.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 17
    call_command(
        "lovable_inventory", "ledger", "--document", str(saved), "--out", str(out), "--store"
    )
    assert LovableInventoryRun.objects.count() == 1
    call_command("lovable_inventory", "report")


def test_the_read_command_uses_the_get_only_transport_and_stores_nothing(
    world, tmp_path, monkeypatch, settings
):
    from app.legacy_import.management.commands import lovable_inventory as command

    settings.LOVABLE_SYNC_ENABLED, settings.REAL_DATA_ALLOWED = True, True

    monkeypatch.setattr(command.lovable_source, "load_config", lambda _path=None: object())
    monkeypatch.setattr(command.lovable_source, "HttpTransport", lambda _config: world["fake"])
    call_command("lovable_inventory", "read", "--out", str(tmp_path))
    saved = json.loads(Path(tmp_path, "reading.json").read_text(encoding="utf-8"))
    verify_observation(saved)
    assert saved["detail_scope"] == "all"
    assert LovableInventoryRun.objects.count() == 0
    assert set(world["fake"].calls) <= set(OBSERVE_TABLES)


def test_the_read_command_refuses_without_the_flags(tmp_path, settings):
    settings.LOVABLE_SYNC_ENABLED = False
    with pytest.raises(CommandError, match="read needs JURISTID_LOVABLE_SYNC"):
        call_command("lovable_inventory", "read", "--out", str(tmp_path))
    assert not any(tmp_path.iterdir())


def test_the_full_read_refuses_after_cut_over(world):
    from app.legacy_import.lovable_sync import SyncRefused
    from app.legacy_import.lovable_sync_models import LovableSyncControl

    LovableSyncControl.objects.create(pk=1, cut_over=True)
    with pytest.raises(SyncRefused, match="Final cutover is recorded"):
        lovable_inventory.read_full(world["fake"], pause=lambda _s: None)
    assert world["fake"].calls == []


def test_a_decision_is_append_only():
    person = factories.UserFactory()
    decision = LovableInventoryDecision.objects.create(
        source_id=sid(1),
        reference="2026_901",
        decision=InventoryDecisionKind.NOT_SAME,
        reason="eri teemad",
        actor=person,
        decided_at=timezone.now(),
    )
    decision.reason = "muudetud"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        decision.save()
    with pytest.raises(DatabaseError, match="does not permit UPDATE"):
        with transaction.atomic():
            LovableInventoryDecision.objects.filter(pk=decision.pk).update(reason="x")
