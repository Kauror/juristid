"""Planning and applying an import, against a real PostgreSQL database.

The acceptance scenarios from the Stage-2A brief live here, one test class per
scenario, plus the invariants that hold across all of them: every row accounted
for, provenance immutable, and a rerun that changes nothing.

Every workbook is synthetic and generated in a temp directory.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from app.core.errors import ImmutableRecordError
from app.legacy_import.apply import apply_plan
from app.legacy_import.enums import Anomaly, OneNoteContentStatus, ProposedRecordMode, RowOutcome
from app.legacy_import.models import (
    ImportBatch,
    ImportRowLedger,
    MatterSourceReference,
    ReconciliationStatus,
)
from app.legacy_import.planner import build_plan
from app.legacy_import.resolution import MappingFileError, MappingTables
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter, MatterReferenceSequence
from app.matters.services import allocate_matter_reference, create_matter
from app.organisations.models import Organisation, OrganisationType
from app.workflow.models import NextAction, StageVocabulary
from tests import factories
from tests.synthetic_register import (
    ONENOTE_LINK,
    Row,
    Sheet,
    era_corpus,
    pre_numbered_corpus,
    write_workbook,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return write_workbook(tmp_path / "synthetic.xlsx", era_corpus())


@pytest.fixture
def kadri(db):
    """The one owner name the synthetic corpus uses."""
    return factories.UserFactory(display_name="Kadri", upn="kadri@example.invalid")


@pytest.fixture
def ministry(db):
    return Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )


@pytest.fixture
def authority(db):
    return Organisation.objects.create(
        name="Näidisamet", organisation_type=OrganisationType.AUTHORITY
    )


def _apply(path: Path, **kwargs) -> tuple[object, object]:
    plan = build_plan(path, **kwargs)
    return plan, apply_plan(plan)


def _matter(reference: str) -> Matter:
    year, number = Matter.parse_reference(reference)
    return Matter.objects.get(reference_year=year, reference_number=number)


def _reference_of(matter: Matter) -> MatterSourceReference:
    return matter.source_references.get()


# =========================================================================
# Scenario A — a 2017 legacy row
# =========================================================================


def test_scenario_a_a_2017_row_imports_as_archive_with_its_sender(
    corpus: Path, kadri, ministry
) -> None:
    _apply(corpus)
    matter = _matter("2017_1")

    assert matter.record_mode == RecordMode.ARCHIVE
    assert matter.origin == MatterOrigin.LEGACY_IMPORT
    assert matter.source_era == "2011-2017"

    # KELLELT is the sender. The addressee column did not exist that year and
    # is not invented from the same value.
    assert matter.source_organisation == ministry
    assert matter.addressee_organisation is None

    # The era had no status model, so no stage is manufactured for it.
    assert matter.stage is None
    assert matter.disposition == ""

    reference = _reference_of(matter)
    assert reference.source_sheet == "2017"
    assert reference.source_row_raw["B"] == matter.title
    assert reference.source_era == "2011-2017"


def test_scenario_a_the_2011_serial_date_string_is_read_and_kept(corpus: Path, kadri) -> None:
    _apply(corpus)
    matter = _matter("2011_1")
    assert matter.received_date == dt.date(2010, 12, 31)
    # The raw value is what the register actually contained.
    assert _reference_of(matter).source_date_raw == "40543"


# =========================================================================
# Scenario B — a 2021 row with member counts
# =========================================================================


def test_scenario_b_a_2021_row_takes_an_addressee_and_never_a_sender(
    corpus: Path, kadri, authority
) -> None:
    _apply(corpus)
    matter = _matter("2021_1")
    assert matter.addressee_organisation == authority
    assert matter.source_organisation is None, "2020+ has no sender column to read"


def test_scenario_b_member_counts_stay_raw_and_produce_no_rate(
    corpus: Path, kadri, authority
) -> None:
    _apply(corpus)
    raw = _reference_of(_matter("2021_1")).source_row_raw
    assert raw["I"] == "9", "answered"
    assert raw["J"] == "3", "asked directly — legitimately fewer"

    # Nothing computes a response rate, and no Consultation exists to hold one.
    assert not hasattr(_matter("2021_1"), "consultations")


def test_scenario_b_blank_and_zero_counts_survive_as_different_values(
    corpus: Path, kadri, ministry
) -> None:
    raw = None
    _apply(corpus)
    raw = _reference_of(_matter("2018_1")).source_row_raw
    assert raw["I"] == "0", "a measured zero"
    assert raw["J"] == "", "never asked — not the same as asking nobody"


# =========================================================================
# Scenario C — a 2024 free-text status
# =========================================================================


def test_scenario_c_an_unknown_status_is_preserved_and_flagged(
    corpus: Path, kadri, ministry
) -> None:
    stages_before = set(StageVocabulary.objects.values_list("key", flat=True))
    plan, _ = _apply(corpus)

    matter = _matter("2024_1")
    assert matter.stage is None, "an unreviewed label never picks a stage"
    assert _reference_of(matter).source_row_raw["K"] == "Riigikogus 2. lugemisel"

    row = next(p for p in plan.rows if p.row.display_reference == "2024_1")
    assert Anomaly.UNMAPPED_STATUS.value in row.anomalies

    # No vocabulary grew itself from a spreadsheet.
    assert set(StageVocabulary.objects.values_list("key", flat=True)) == stages_before


def test_scenario_c_the_import_continues_past_an_unknown_status(
    corpus: Path, kadri, ministry
) -> None:
    _, result = _apply(corpus)
    assert result.created > 1
    assert Matter.objects.filter(reference_year=2024).exists()


def test_a_closure_label_closes_an_archive_matter_without_inventing_a_date(
    corpus: Path, kadri, ministry
) -> None:
    """`rohkem pole tegevusi plaanis` says Koda stopped, never when.

    A FULL Matter must carry a closure timestamp; an ARCHIVE one need not. That
    is exactly why the planner proposes ARCHIVE here rather than fabricating a
    date to satisfy the constraint.
    """
    _apply(corpus)
    matter = _matter("2025_2")
    assert matter.is_open is False
    assert matter.disposition == "MONITORING_STOPPED"
    assert matter.closed_at is None
    assert matter.record_mode == RecordMode.ARCHIVE


def test_in_force_is_a_stage_and_does_not_close_the_matter(corpus: Path, kadri, ministry) -> None:
    _apply(corpus)
    matter = _matter("2025_1")
    assert matter.stage is not None and matter.stage.key == "in_force"
    assert matter.is_open is True, "an act entering force does not finish Koda's file"


# =========================================================================
# Scenario D — a 2026 row with a OneNote hyperlink
# =========================================================================


def test_scenario_d_the_hyperlink_is_preserved_exactly_and_not_followed(
    corpus: Path, kadri, ministry
) -> None:
    _apply(corpus)
    reference = _reference_of(_matter("2026_1"))

    assert reference.onenote_url == ONENOTE_LINK
    assert reference.onenote_content_status == OneNoteContentStatus.NOT_IMPORTED
    # Never guessed from the URL's shape: this column is used to match pages.
    assert reference.onenote_page_id == ""
    assert reference.source_sheet == "2026"
    assert reference.source_row_number > 0
    assert len(reference.source_snapshot_sha256) == 64


def test_a_row_without_a_link_is_not_marked_as_awaiting_one(corpus: Path, kadri, ministry) -> None:
    _apply(corpus)
    assert (
        _reference_of(_matter("2026_2")).onenote_content_status
        == OneNoteContentStatus.NOT_APPLICABLE
    )


# =========================================================================
# Scenario E — ambiguous JÄRGMISEKS
# =========================================================================


def test_scenario_e_no_next_action_is_ever_created_from_free_text(
    corpus: Path, kadri, ministry
) -> None:
    _apply(corpus)
    assert NextAction.objects.count() == 0, "the importer proposes; it does not decide"


def test_scenario_e_the_next_action_text_survives_verbatim(corpus: Path, kadri, ministry) -> None:
    _apply(corpus)
    raw = _reference_of(_matter("2026_2")).source_row_raw
    assert raw["L"] == "Räägime läbi ja vaatame, mis edasi saab."


def test_scenario_e_a_deterministic_candidate_is_emitted_but_not_applied(
    corpus: Path, kadri, ministry
) -> None:
    plan = build_plan(corpus)
    row = next(p for p in plan.rows if p.row.display_reference == "2026_1")
    assert row.candidate is not None and row.candidate.is_deterministic
    apply_plan(plan)
    assert NextAction.objects.count() == 0


# =========================================================================
# Scenario F — rerunning the same snapshot
# =========================================================================


def test_scenario_f_a_rerun_creates_nothing_new(corpus: Path, kadri, ministry) -> None:
    _, first = _apply(corpus)
    matters_after_first = Matter.objects.count()
    references_after_first = MatterSourceReference.objects.count()

    _, second = _apply(corpus)

    assert Matter.objects.count() == matters_after_first
    assert MatterSourceReference.objects.count() == references_after_first
    assert second.created == 0
    assert second.already_imported == first.created


def test_scenario_f_the_rerun_says_plainly_that_it_did_nothing(
    corpus: Path, kadri, ministry
) -> None:
    _apply(corpus)
    plan, result = _apply(corpus)
    assert plan.outcome_counts.get(RowOutcome.WOULD_CREATE.value, 0) == 0
    assert result.already_imported > 0


def test_the_same_source_row_cannot_be_recorded_twice_for_one_snapshot(
    corpus: Path, kadri, ministry
) -> None:
    """The idempotency guarantee is a constraint, not a code path."""
    _apply(corpus)
    original = MatterSourceReference.objects.first()
    assert original is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterSourceReference.objects.create(
            matter=original.matter,
            source_system=original.source_system,
            source_snapshot_sha256=original.source_snapshot_sha256,
            source_sheet=original.source_sheet,
            source_row_number=original.source_row_number,
        )


# =========================================================================
# Scenario G — a native Matter created after an import
# =========================================================================


def test_scenario_g_a_native_matter_cannot_collide_with_an_imported_number(
    tmp_path: Path, kadri, ministry
) -> None:
    path = write_workbook(tmp_path / "pre.xlsx", pre_numbered_corpus(filled=3, reserved_to=300))
    _apply(path)

    year, number = allocate_matter_reference(2026)
    assert year == 2026
    # Not 4. The register reserved every number to 300, and those numbers are
    # already written on files even though no row carries a matter yet.
    assert number > 300


def test_reserved_numbers_do_not_become_matters(tmp_path: Path, kadri, ministry) -> None:
    path = write_workbook(tmp_path / "pre.xlsx", pre_numbered_corpus(filled=3, reserved_to=20))
    _, result = _apply(path)
    assert result.created == 3
    assert result.reserved == 17
    assert Matter.objects.filter(reference_year=2026).count() == 3
    assert MatterReferenceSequence.objects.get(pk=2026).last_number == 20


def test_an_existing_native_matter_is_never_overwritten(tmp_path: Path, kadri) -> None:
    native = create_matter(title="Süsteemis kirjutatud teema", reference_year=2026)
    reference = f"{native.reference_year}_{native.reference_number}"

    path = write_workbook(
        tmp_path / "clash.xlsx",
        [Sheet(2026, [Row(reference=reference, title="Allika hoopis teine pealkiri")])],
    )
    plan, result = _apply(path)

    native.refresh_from_db()
    assert native.title == "Süsteemis kirjutatud teema"
    assert native.origin == MatterOrigin.NATIVE
    assert result.created == 0
    assert result.review_required == 1

    row = plan.rows[0]
    assert Anomaly.REFERENCE_CONFLICTS_WITH_NATIVE.value in row.anomalies


def test_a_disagreeing_title_on_the_same_reference_needs_a_person(tmp_path: Path, kadri) -> None:
    first = write_workbook(
        tmp_path / "a.xlsx", [Sheet(2026, [Row(reference="2026_1", title="Algne pealkiri")])]
    )
    _apply(first)

    second = write_workbook(
        tmp_path / "b.xlsx",
        [Sheet(2026, [Row(reference="2026_1", title="Hoopis teine pealkiri")])],
    )
    plan, result = _apply(second)

    assert result.review_required == 1
    assert result.created == 0
    assert Anomaly.SOURCE_DISAGREES_WITH_MATTER.value in plan.rows[0].anomalies
    assert _matter("2026_1").title == "Algne pealkiri"


def test_a_newer_snapshot_of_an_unchanged_row_adds_provenance_rather_than_replacing_it(
    tmp_path: Path, kadri
) -> None:
    rows = [Sheet(2026, [Row(reference="2026_1", title="Sama pealkiri")])]
    first = write_workbook(tmp_path / "one.xlsx", rows)
    _apply(first)

    # Same content, different bytes, therefore a different snapshot.
    second = write_workbook(tmp_path / "two.xlsx", [*rows, Sheet(2025, [])])
    _, result = _apply(second)

    matter = _matter("2026_1")
    assert result.matched == 1
    assert matter.source_references.count() == 2, "new evidence, not a correction of the old"


# =========================================================================
# Row accounting and the ledger
# =========================================================================


def test_every_row_leaves_the_planner_with_exactly_one_outcome(corpus: Path, kadri) -> None:
    plan = build_plan(corpus)
    assert plan.is_complete
    assert sum(plan.outcome_counts.values()) == len(plan.rows)


def test_the_ledger_totals_equal_the_plan_totals(corpus: Path, kadri, ministry) -> None:
    plan, result = _apply(corpus)
    batch = result.batch

    blank = plan.outcome_counts.get(RowOutcome.BLANK_PADDING.value, 0)
    assert ImportRowLedger.objects.filter(import_batch=batch).count() == len(plan.rows) - blank
    assert result.accounted_rows == len(plan.rows)

    for outcome, expected in plan.outcome_counts.items():
        if outcome == RowOutcome.BLANK_PADDING.value:
            continue
        assert (
            ImportRowLedger.objects.filter(import_batch=batch, outcome=outcome).count() == expected
        )


def test_the_batch_records_its_snapshot_and_versions(corpus: Path, kadri, ministry) -> None:
    _, result = _apply(corpus)
    batch = ImportBatch.objects.get(pk=result.batch.pk)
    assert len(batch.source_snapshot_sha256) == 64
    assert batch.importer_version
    assert batch.contract_version
    assert batch.finished_at is not None
    assert batch.reconciliation_status in {
        ReconciliationStatus.COMPLETED,
        ReconciliationStatus.COMPLETED_WITH_GAPS,
    }


def test_the_ledger_is_append_only_in_the_database(corpus: Path, kadri, ministry) -> None:
    _apply(corpus)
    entry = ImportRowLedger.objects.first()
    assert entry is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportRowLedger.objects.filter(pk=entry.pk).update(note="rewritten")


# =========================================================================
# Provenance immutability
# =========================================================================


def test_raw_source_values_cannot_be_changed_through_the_model(
    corpus: Path, kadri, ministry
) -> None:
    _apply(corpus)
    reference = MatterSourceReference.objects.first()
    assert reference is not None
    reference.source_title = "parandatud"
    with pytest.raises(ImmutableRecordError):
        reference.save()


def test_raw_source_values_cannot_be_changed_by_a_queryset_update(
    corpus: Path, kadri, ministry
) -> None:
    """The gap the model-layer guard could never cover.

    ``QuerySet.update()`` never calls ``save()``. Without the trigger, the whole
    immutability claim would rest on everyone remembering to use the model.
    """
    _apply(corpus)
    reference = MatterSourceReference.objects.first()
    assert reference is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterSourceReference.objects.filter(pk=reference.pk).update(source_title="parandatud")


def test_the_onenote_url_is_immutable_in_the_database(corpus: Path, kadri, ministry) -> None:
    _apply(corpus)
    reference = MatterSourceReference.objects.exclude(onenote_url="").first()
    assert reference is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterSourceReference.objects.filter(pk=reference.pk).update(
            onenote_url="https://example.invalid/repaired"
        )


def test_the_raw_row_json_is_immutable_in_the_database(corpus: Path, kadri, ministry) -> None:
    _apply(corpus)
    reference = MatterSourceReference.objects.first()
    assert reference is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterSourceReference.objects.filter(pk=reference.pk).update(source_row_raw={})


def test_interpretation_and_operational_columns_remain_editable(
    corpus: Path, kadri, ministry
) -> None:
    """Immutability protects the evidence, not the reading of it.

    A better interpretation, or a OneNote page that finally arrives, must be
    recordable — otherwise people delete and recreate the row, which loses the
    evidence the rule was written to protect.
    """
    _apply(corpus)
    reference = MatterSourceReference.objects.exclude(onenote_url="").first()
    assert reference is not None
    MatterSourceReference.objects.filter(pk=reference.pk).update(
        onenote_content_status=OneNoteContentStatus.UNAVAILABLE,
        review_note="vaadatud üle",
        conflict_state="RESOLVED_BY_REVIEW",
    )
    reference.refresh_from_db()
    assert reference.onenote_content_status == OneNoteContentStatus.UNAVAILABLE


# =========================================================================
# Resolution: exact or nothing
# =========================================================================


def test_an_exact_owner_name_resolves_and_an_unknown_one_does_not(
    corpus: Path, kadri, ministry
) -> None:
    plan, _ = _apply(corpus)
    assert _matter("2026_1").owner == kadri

    unknown = _matter("2026_2")
    assert unknown.owner is None
    assert _reference_of(unknown).source_row_raw["H"] == "Keegi Tundmatu"
    row = next(p for p in plan.rows if p.row.display_reference == "2026_2")
    assert Anomaly.UNMAPPED_OWNER.value in row.anomalies


def test_a_shared_owner_cell_is_never_split_into_a_guess(tmp_path: Path, kadri) -> None:
    path = write_workbook(
        tmp_path / "shared.xlsx",
        [Sheet(2026, [Row(reference="2026_1", title="Jagatud teema", owner="Kadri, Mart")])],
    )
    _apply(path)
    assert _matter("2026_1").owner is None, "two names is a shared file, not a person"


def test_no_user_is_ever_created_from_a_register_name(tmp_path: Path) -> None:
    from app.accounts.models import User

    before = User.objects.count()
    path = write_workbook(
        tmp_path / "names.xlsx",
        [Sheet(2026, [Row(reference="2026_1", title="Teema", owner="Uus Inimene")])],
    )
    _apply(path)
    assert User.objects.count() == before


def test_no_organisation_is_ever_created_from_a_spelling_variant(tmp_path: Path) -> None:
    before = Organisation.objects.count()
    path = write_workbook(
        tmp_path / "orgs.xlsx",
        [
            Sheet(
                2026,
                [Row(reference="2026_1", title="Teema", counterparty="Täiesti Tundmatu Asutus")],
            )
        ],
    )
    _apply(path)
    assert Organisation.objects.count() == before
    assert _matter("2026_1").addressee_organisation is None


def test_a_reviewed_alias_resolves_where_similarity_must_not(tmp_path: Path) -> None:
    """An alias is a person's recorded decision, which is the evidence
    similarity matching lacks."""
    ministry = Organisation.objects.create(
        name="Majandus- ja Kommunikatsiooniministeerium",
        organisation_type=OrganisationType.MINISTRY,
    )
    ministry.aliases.create(alias="MKM", alias_type="ABBREVIATION")

    path = write_workbook(
        tmp_path / "alias.xlsx",
        [Sheet(2026, [Row(reference="2026_1", title="Teema", counterparty="MKM")])],
    )
    _apply(path)
    assert _matter("2026_1").addressee_organisation == ministry


def test_a_mapping_file_supplies_answers_the_importer_refuses_to_guess(
    tmp_path: Path, kadri
) -> None:
    ministry = Organisation.objects.create(
        name="Näidisministeerium", organisation_type=OrganisationType.MINISTRY
    )
    mapping = tmp_path / "mapping.toml"
    mapping.write_text(
        "[owners]\n"
        '"Keegi Tundmatu" = "kadri@example.invalid"\n'
        "\n[organisations]\n"
        '"Tundmatu Näidisasutus" = "Näidisministeerium"\n',
        encoding="utf-8",
    )

    path = write_workbook(
        tmp_path / "mapped.xlsx",
        [
            Sheet(
                2026,
                [
                    Row(
                        reference="2026_1",
                        title="Teema",
                        owner="Keegi Tundmatu",
                        counterparty="Tundmatu Näidisasutus",
                    )
                ],
            )
        ],
    )
    _apply(path, mappings=MappingTables.load(mapping))

    matter = _matter("2026_1")
    assert matter.owner == kadri
    assert matter.addressee_organisation == ministry


def test_a_mapping_file_pointing_at_nothing_fails_loudly(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.toml"
    mapping.write_text('[owners]\n"Kadri" = "nobody@example.invalid"\n', encoding="utf-8")
    path = write_workbook(
        tmp_path / "bad.xlsx",
        [Sheet(2026, [Row(reference="2026_1", title="Teema", owner="Kadri")])],
    )
    with pytest.raises(MappingFileError):
        build_plan(path, mappings=MappingTables.load(mapping))


# =========================================================================
# Record mode
# =========================================================================


def test_older_years_are_proposed_as_archive_with_a_reason(corpus: Path, kadri) -> None:
    plan = build_plan(corpus, today=dt.date(2026, 8, 19))
    row = next(p for p in plan.rows if p.row.display_reference == "2017_1")
    assert row.proposed_record_mode == ProposedRecordMode.ARCHIVE
    assert row.proposed_record_mode_reason


def test_a_recent_row_with_real_signals_is_a_full_candidate_but_still_lands_as_archive(
    corpus: Path, kadri, ministry
) -> None:
    """A candidate is a question for a person, not a promotion."""
    plan, _ = _apply(corpus)
    row = next(p for p in plan.rows if p.row.display_reference == "2026_1")
    assert row.proposed_record_mode == ProposedRecordMode.FULL_CANDIDATE
    assert _matter("2026_1").record_mode == RecordMode.ARCHIVE


def test_a_recent_row_with_no_signals_is_not_promoted_by_its_year_alone(
    tmp_path: Path, kadri
) -> None:
    path = write_workbook(
        tmp_path / "bare.xlsx",
        [Sheet(2026, [Row(reference="2026_1", title="Ilma seisundita teema")])],
    )
    plan = build_plan(path, today=dt.date(2026, 8, 19))
    assert plan.rows[0].proposed_record_mode == ProposedRecordMode.ARCHIVE


def test_a_reviewed_override_is_the_only_route_to_a_full_record(
    tmp_path: Path, kadri, ministry
) -> None:
    mapping = tmp_path / "modes.toml"
    mapping.write_text('[record_modes]\n"2026_1" = "FULL"\n', encoding="utf-8")
    path = write_workbook(
        tmp_path / "full.xlsx",
        [
            Sheet(
                2026,
                [
                    Row(
                        reference="2026_1",
                        title="Kinnitatud aktiivne teema",
                        counterparty="Näidisministeerium",
                        status="kooskõlastusringil",
                    )
                ],
            )
        ],
    )
    _apply(path, mappings=MappingTables.load(mapping))
    assert _matter("2026_1").record_mode == RecordMode.FULL


def test_an_override_cannot_force_full_onto_a_row_that_would_need_an_invented_date(
    tmp_path: Path, kadri, ministry
) -> None:
    """Refused out loud rather than silently downgraded or silently faked."""
    mapping = tmp_path / "modes.toml"
    mapping.write_text('[record_modes]\n"2026_1" = "FULL"\n', encoding="utf-8")
    path = write_workbook(
        tmp_path / "closed.xlsx",
        [
            Sheet(
                2026,
                [
                    Row(
                        reference="2026_1",
                        title="Lõpetatud teema",
                        counterparty="Näidisministeerium",
                        status="rohkem pole tegevusi plaanis",
                    )
                ],
            )
        ],
    )
    plan, result = _apply(path, mappings=MappingTables.load(mapping))
    assert plan.rows[0].outcome == RowOutcome.REVIEW_REQUIRED
    assert result.created == 0


# =========================================================================
# The commands
# =========================================================================


def test_the_dry_run_writes_nothing(corpus: Path, kadri, ministry, tmp_path: Path) -> None:
    matters = Matter.objects.count()
    call_command(
        "import_legacy_register",
        str(corpus),
        "--dry-run",
        "--report-dir",
        str(tmp_path / "reports"),
    )
    assert Matter.objects.count() == matters
    assert ImportBatch.objects.count() == 0
    assert MatterSourceReference.objects.count() == 0


def test_the_dry_run_reports_separate_aggregate_and_row_level_files(
    corpus: Path, kadri, tmp_path: Path
) -> None:
    directory = tmp_path / "reports"
    call_command("import_legacy_register", str(corpus), "--dry-run", "--report-dir", str(directory))
    for name in ("summary.json", "summary.md", "rows.csv", "anomalies.csv"):
        assert (directory / name).exists()


def test_apply_refuses_when_the_environment_is_not_cleared_for_real_data(
    corpus: Path, kadri, tmp_path: Path, settings
) -> None:
    from django.core.management.base import CommandError

    settings.REAL_DATA_ALLOWED = False
    with pytest.raises(CommandError, match="REAL_DATA_ALLOWED"):
        call_command(
            "import_legacy_register",
            str(corpus),
            "--apply",
            "--report-dir",
            str(tmp_path / "reports"),
        )


def test_neither_mode_is_the_default(corpus: Path, tmp_path: Path) -> None:
    """A default of "do nothing" is the one people stop reading."""
    import contextlib
    import io

    from django.core.management.base import CommandError

    with pytest.raises((CommandError, SystemExit)), contextlib.redirect_stderr(io.StringIO()):
        call_command("import_legacy_register", str(corpus))


def test_the_offline_inspector_writes_no_reports_when_asked_not_to(
    corpus: Path, tmp_path: Path
) -> None:
    directory = tmp_path / "none"
    call_command(
        "inspect_legacy_register", str(corpus), "--report-dir", str(directory), "--no-reports"
    )
    assert not directory.exists()
