"""The 2026 Excel operational pilot (docs/adr/0148), on a synthetic workbook.

Every title, name and organisation below is invented; the shapes are the
register's. The tests walk the pilot's three steps — manifest, plan, apply — and
pin the rules the brief set: scope, selection, lifecycle, `JÄRGMISEKS`,
placeholder evidence, follow-up checks, reservation, rerun and refusal.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.accounts.models import User
from app.documents.models import DocumentVersion
from app.legacy_import import excel_pilot
from app.legacy_import.excel_pilot import (
    DRAFTING_TEXT,
    GROUP_A,
    GROUP_B,
    GROUP_C,
    REASON_DRAFTING,
    STEP_ACTION,
    STEP_ENTRY,
    STEP_NONE,
    STEP_UNDATED_ACTION,
    PilotError,
    Selection,
    SourceRow,
    build_manifest,
    build_plan,
    drafting_action_of,
    lifecycle_of,
    manifest_digest,
    next_step_of,
    read_pilot_sheet,
)
from app.legacy_import.models import (
    CurrentRegisterState,
    ExcelPilotImport,
    ImportRowLedger,
    placeholder_versions,
)
from app.legacy_import.placeholder_pdf import (
    HEADLINE,
    SUBHEADLINE,
    PlaceholderFacts,
    render_placeholder_pdf,
)
from app.legacy_import.register_semantics import OpinionSentState, is_terminal_status
from app.matters.enums import RecordMode, ResponseDeadlineOutcome
from app.matters.models import Entry, Matter, MatterReferenceSequence, MatterResponseDeadline
from app.matters.services import create_matter
from app.matters.work_status import ACTIVE, CONCLUDED, CONTINUES, INACTIVE, work_status_of
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import (
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
    Disposition,
    FollowUpOutcome,
)
from app.workflow.models import NextAction, OpinionFollowUp, resolve_legacy_status
from tests import factories
from tests.refusals import refused
from tests.synthetic_register import Row, Sheet, write_workbook

SNAPSHOT = dt.date(2026, 10, 8)
WAIT_REVIEW = "Ootan II lugemist, vaatan üle 15.10"
AMBIGUOUS = (
    "Ootan kava avalikule kooskõlastusringile. Uuri hiljemalt 06.11, millal tuleb. "
    "Valitsus peab kava heaks kiitma hiljemalt 31.12.2026."
)
INFORMATION = "Seadus jõustub eeldatavasti 1. jaanuaril 2027."


def _rows() -> list[Row]:
    d = dt.datetime
    return [
        Row(
            "2026_1",
            "Näidisseaduse eelnõu",
            "seadus",
            d(2026, 9, 1),
            d(2026, 9, 20),
            None,
            "Rahandusministeerium",
            "Mari",
            None,
            0,
            "kooskõlastusringil",
            None,
        ),
        Row(
            "2026_2",
            "Näidismääruse muutmine",
            "muu siseriiklik",
            d(2026, 9, 2),
            d(2026, 9, 30),
            None,
            "Riigikogu majanduskomisjon",
            "Jaan",
            2,
            10,
            "jõustumise ootel",
            INFORMATION,
        ),
        Row(
            "2026_3",
            "Näidiskonsultatsioon",
            "ELi konsultatsioon",
            d(2026, 9, 3),
            d(2026, 10, 1),
            None,
            "Kliimaministeerium",
            "Mari",
            None,
            None,
            "Eesti seisukoht koostamisel",
            WAIT_REVIEW,
        ),
        Row(
            "2026_4",
            "Saadetud ja jälgitav seadus",
            "seadus",
            d(2026, 5, 1),
            d(2026, 5, 30),
            d(2026, 6, 1),
            "Rahandusministeerium, Kliimaministeerium",
            "Jaan",
            1,
            40,
            "Riigikogus",
            "Vaata 18.11 üle, kas eelnõu liigub.",
        ),
        Row(
            "2026_5",
            "Saadetud kava",
            "muu siseriiklik",
            d(2026, 9, 1),
            d(2026, 9, 20),
            d(2026, 9, 25),
            "Kliimaministeerium",
            "Mari",
            None,
            None,
            "muu",
            AMBIGUOUS,
        ),
        Row(
            "2026_6",
            "Saatmata jäetud konsultatsioon",
            "muu ELi dokument",
            d(2026, 4, 1),
            d(2026, 5, 1),
            "ei saatnud",
            "Kliimaministeerium",
            "Jaan",
            0,
            0,
            "ELi menetluses",
            "vaata üle detsembris",
        ),
        Row(
            "2026_7",
            "Jõustunud seadus",
            "seadus",
            d(2026, 2, 1),
            d(2026, 2, 20),
            d(2026, 3, 1),
            "Riigikogu põhiseaduskomisjon",
            "Mari",
            0,
            5,
            "jõustunud",
            None,
        ),
        Row(
            "2026_8",
            "Lõpetatud teema",
            "määrus",
            d(2026, 1, 10),
            d(2026, 1, 30),
            d(2026, 2, 1),
            "Rahandusministeerium",
            "Jaan",
            None,
            None,
            "rohkem ei tegele",
            "Mari on edasi tegelenud selle teemaga",
        ),
        Row(
            "2026_9",
            "Jätkuv VTK",
            "VTK",
            d(2026, 3, 1),
            d(2026, 3, 30),
            d(2026, 4, 1),
            "Rahandusministeerium",
            "Jaan",
            None,
            None,
            "rohkem ei tegele",
            "Jätkub teema 2026_1 all.",
        ),
        # Not selected: a sent, active row and a blank-VÄLJA row that is finished.
        Row(
            "2026_10",
            "Valimata saadetud teema",
            "seadus",
            d(2026, 6, 1),
            d(2026, 6, 20),
            d(2026, 6, 21),
            "Rahandusministeerium",
            "Mari",
            None,
            None,
            "Riigikogus",
            None,
        ),
        Row(
            "2026_11",
            "Tühi VÄLJA, kuid lõpetatud",
            "seadus",
            d(2026, 6, 1),
            d(2026, 6, 20),
            None,
            "Rahandusministeerium",
            "Mari",
            None,
            None,
            "rohkem ei tegele",
            None,
        ),
        Row("2026_12"),
        Row("2026_15"),
        Row(owner="KOKKU", responded=3, requested=55),
    ]


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    other_year = Sheet(
        2025,
        [
            Row(
                "2025_1",
                "Eelmise aasta teema",
                "seadus",
                None,
                None,
                None,
                "Rahandusministeerium",
                "Mari",
                None,
                None,
                "idee",
                None,
            )
        ],
    )
    return write_workbook(tmp_path / "pilot.xlsx", [Sheet(2026, _rows()), other_year])


@pytest.fixture
def selection() -> Selection:
    return Selection(
        snapshot_date=SNAPSHOT,
        group_b=[("2026_4", "saadetud, jälgitav"), ("2026_5", "saadetud, kuupäev kahtlane")],
        group_c=[
            ("2026_6", "ei saatnud"),
            ("2026_7", "jõustunud"),
            ("2026_8", "rohkem ei tegele"),
            ("2026_9", "jätkub mujal"),
        ],
    )


@pytest.fixture
def world(db):
    mari = factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    jaan = factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    for name in (
        "Rahandusministeerium",
        "Kliimaministeerium",
        "Riigikogu majanduskomisjon",
        "Riigikogu põhiseaduskomisjon",
    ):
        factories.OrganisationFactory(name=name)
    return {"mari": mari, "jaan": jaan}


@pytest.fixture
def pilot_on(settings):
    settings.EXCEL_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


def _manifest(workbook: Path, selection: Selection) -> dict:
    return build_manifest(read_pilot_sheet(workbook), selection)


def _apply(workbook: Path, manifest: dict, **overrides):
    plan = build_plan(workbook, manifest)
    arguments = {
        "operator_intent": excel_pilot.OPERATOR_INTENT,
        "expect_manifest_sha256": plan.digest,
        "backup_set": "sets/20261009T000000Z",
        **overrides,
    }
    return excel_pilot.apply_plan(plan, **arguments)


def _matter(reference: str) -> Matter:
    year, number = reference.split("_")
    return Matter.all_objects.get(reference_year=int(year), reference_number=int(number))


# ---------------------------------------------------------------------------
# The manifest: scope, selection, reproducibility
# ---------------------------------------------------------------------------


def test_the_manifest_is_scope_limited_and_reproducible(workbook, selection):
    reading = read_pilot_sheet(workbook)
    assert set(reading.rows) == {f"2026_{n}" for n in range(1, 12)}
    assert reading.highest_number == 15, "reserved numbers count towards the reservation"

    first = build_manifest(reading, selection)
    second = build_manifest(read_pilot_sheet(workbook), selection)
    assert manifest_digest(first) == manifest_digest(second)

    groups = {row["reference"]: row["group"] for row in first["rows"]}
    assert groups == {
        "2026_1": GROUP_A,
        "2026_2": GROUP_A,
        "2026_3": GROUP_A,
        "2026_4": GROUP_B,
        "2026_5": GROUP_B,
        "2026_6": GROUP_C,
        "2026_7": GROUP_C,
        "2026_8": GROUP_C,
        "2026_9": GROUP_C,
    }
    assert first["counts"] == {"A": 3, "B": 2, "C": 4}
    assert "2026_10" not in groups and "2026_11" not in groups
    assert not any(row["reference"].startswith("2025") for row in first["rows"])
    assert first["workbook"]["reserve_through"] == 15


#: The real 2026 sheet's uncontracted columns, and the one Lovable adds beside them.
OUT_OF_SCOPE_COLUMNS = ("UUS VASTUTAJA", "KOJA ETTEPANEK VÕI PÖÖRDUMINE", "MITTEAMETLIK ARVAMUS")
OUT_OF_SCOPE = (
    "OUT-OF-SCOPE-UUS-VASTUTAJA",
    "OUT-OF-SCOPE-KOJA-ETTEPANEK",
    "OUT-OF-SCOPE-MITTEAMETLIK",
)


def _with_out_of_scope_columns(path: Path) -> Path:
    """Every uncontracted column, filled on every row."""
    from openpyxl import load_workbook

    book = load_workbook(path)
    sheet = book["2026"]
    width = sheet.max_column
    for offset, header in enumerate(OUT_OF_SCOPE_COLUMNS, start=1):
        sheet.cell(row=1, column=width + offset, value=header)
    for row in range(2, sheet.max_row + 1):
        for offset, marker in enumerate(OUT_OF_SCOPE, start=1):
            sheet.cell(row=row, column=width + offset, value=marker)
    target = path.with_name("pilot-with-out-of-scope-columns.xlsx")
    book.save(target)
    book.close()
    return target


def test_the_out_of_scope_columns_never_reach_the_database(world, workbook, selection, pilot_on):
    from django.core import serializers

    from app.legacy_import.models import MatterSourceReference

    widened = _with_out_of_scope_columns(workbook)
    plain, wide = read_pilot_sheet(workbook), read_pilot_sheet(widened)
    # Not read: the two columns move no row's digest.
    assert {ref: row.row_sha256 for ref, row in wide.rows.items()} == {
        ref: row.row_sha256 for ref, row in plain.rows.items()
    }
    assert any(
        OUT_OF_SCOPE[0] in extracted.raw_row.values() for extracted in wide.extracted.values()
    )

    _apply(widened, _manifest(widened, selection))

    contracted = {column.letter for column in wide.contract.columns}
    references = list(MatterSourceReference.objects.all())
    assert len(references) == 9
    for reference in references:
        assert set(reference.source_row_raw) <= contracted, "provenance keeps contracted cells only"
    dump = serializers.serialize(
        "json",
        [
            *references,
            *ExcelPilotImport.objects.all(),
            *Matter.all_objects.all(),
            *Entry.objects.all(),
            *NextAction.objects.all(),
            *Submission.objects.all(),
        ],
    )
    for marker in OUT_OF_SCOPE:
        assert marker not in dump, f"{marker} reached the database"


def test_a_blank_valja_is_not_automatically_active(workbook, selection):
    """2026_11 has a blank VÄLJA and «rohkem ei tegele»: finished, so not group A."""
    manifest = _manifest(workbook, selection)
    assert "2026_11" not in {row["reference"] for row in manifest["rows"]}


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda s: s.group_b.append(("2026_1", "x")), "selected twice"),
        (lambda s: s.group_b.append(("2026_11", "x")), "group B needs"),
        (lambda s: s.group_c.append(("2026_10", "x")), "group C needs"),
        (lambda s: s.group_c.append(("2026_99", "x")), "not a titled row"),
    ],
)
def test_a_selection_outside_its_group_rule_is_refused(workbook, selection, change, message):
    change(selection)
    with pytest.raises(PilotError, match=message):
        _manifest(workbook, selection)


def test_a_continuation_whose_successor_is_not_selected_is_refused(workbook, selection, tmp_path):
    rows = _rows()
    rows[8] = Row(
        "2026_9",
        "Jätkuv VTK",
        "VTK",
        None,
        None,
        dt.datetime(2026, 4, 1),
        "Rahandusministeerium",
        "Jaan",
        None,
        None,
        "rohkem ei tegele",
        "Jätkub teema 2026_10 all.",
    )
    path = write_workbook(tmp_path / "other.xlsx", [Sheet(2026, rows)])
    with pytest.raises(PilotError, match="not in the sample"):
        _manifest(path, selection)


# ---------------------------------------------------------------------------
# The rules, as pure functions
# ---------------------------------------------------------------------------


def _row(**changes) -> SourceRow:
    base = {
        "reference": "2026_1",
        "number": 1,
        "row_number": 2,
        "title": "T",
        "instrument_raw": "seadus",
        "received": None,
        "deadline": dt.date(2026, 9, 1),
        "valja_raw": "",
        "valja_state": OpinionSentState.BLANK,
        "sent_on": None,
        "addressee_raw": "Rahandusministeerium",
        "owner_raw": "Mari",
        "responded": None,
        "requested": None,
        "status_raw": "kooskõlastusringil",
        "next_text": "",
        "has_link": False,
        "row_sha256": "x",
    }
    base.update(changes)
    return SourceRow(**base)


@pytest.mark.parametrize(
    "changes, status, disposition",
    [
        ({}, ACTIVE.key, ""),
        (
            {"valja_raw": "ei saatnud", "valja_state": OpinionSentState.NOT_SENT},
            CONCLUDED.key,
            Disposition.NO_POSITION_FORMED,
        ),
        ({"status_raw": "jõustunud"}, INACTIVE.key, Disposition.COMPLETED),
        ({"status_raw": "rohkem ei tegele"}, CONCLUDED.key, Disposition.MONITORING_STOPPED),
        (
            {
                "status_raw": "rohkem ei tegele",
                "valja_state": OpinionSentState.NOT_SENT,
                "valja_raw": "ei saatnud",
            },
            CONCLUDED.key,
            Disposition.MONITORING_STOPPED,
        ),
        (
            {"status_raw": "rohkem ei tegele", "next_text": "Jätkub teema 2026_9 all."},
            CONTINUES.key,
            Disposition.SUPERSEDED,
        ),
        (
            {
                "valja_raw": "05.06.2026",
                "valja_state": OpinionSentState.DATE,
                "sent_on": dt.date(2026, 6, 5),
            },
            ACTIVE.key,
            "",
        ),
    ],
)
def test_the_lifecycle_rule(changes, status, disposition):
    lifecycle = lifecycle_of(_row(**changes))
    assert (lifecycle.status, lifecycle.disposition) == (status, disposition)


def _sent_row(**changes) -> SourceRow:
    """Live work whose opinion went out: `JÄRGMISEKS` is the only source of its step."""
    sent = {
        "valja_raw": "05.06.2026",
        "valja_state": OpinionSentState.DATE,
        "sent_on": dt.date(2026, 6, 5),
    }
    return _row(**{**sent, **changes})


def test_a_sent_opinion_closes_nothing():
    assert lifecycle_of(_sent_row()).status == ACTIVE.key


@pytest.mark.parametrize(
    "text, treatment, kind, has_date",
    [
        ("", STEP_NONE, "", False),
        (WAIT_REVIEW, STEP_ACTION, ActionKind.WAIT, True),
        ("Vaata 18.11 üle, kas eelnõu liigub.", STEP_ACTION, ActionKind.MONITOR, True),
        (AMBIGUOUS, STEP_UNDATED_ACTION, ActionKind.WAIT, False),
        (INFORMATION, STEP_ENTRY, "", False),
    ],
)
def test_the_next_step_rule_for_live_work(text, treatment, kind, has_date):
    row = _sent_row(next_text=text)
    step = next_step_of(row, lifecycle_of(row), SNAPSHOT)
    assert step.treatment == treatment
    assert step.kind == kind
    assert (step.target_date is not None) == has_date
    assert drafting_action_of(row, lifecycle_of(row)) is None, "a sent opinion is not drafted"


def test_a_finished_matters_instruction_is_a_note_never_a_task():
    row = _row(status_raw="rohkem ei tegele", next_text=WAIT_REVIEW)
    step = next_step_of(row, lifecycle_of(row), SNAPSHOT)
    assert step.treatment == STEP_ENTRY
    assert drafting_action_of(row, lifecycle_of(row)) is None


# The opinion still being written: one «Koostan arvamuse», dated by its deadline.


def test_an_opinion_being_written_starts_with_the_drafting_step():
    row = _row(next_text="")
    lifecycle = lifecycle_of(row)
    assert drafting_action_of(row, lifecycle) == {
        "text": "Koostan arvamuse",
        "kind": ActionKind.DO,
        "date_semantics": DateSemantics.DEADLINE,
        "target_date": "2026-09-01",
        "date_precision": DatePrecision.EXACT,
        "source_field": "ARVAMUSE TÄHTAEG",
    }
    assert next_step_of(row, lifecycle, SNAPSHOT).treatment == STEP_NONE, "no note to keep"


@pytest.mark.parametrize("text", [INFORMATION, WAIT_REVIEW, AMBIGUOUS])
def test_a_drafting_rows_jargmiseks_is_a_note_beside_the_step(text):
    """Information or an instruction alike: kept word for word, never a second task."""
    row = _row(next_text=text)
    lifecycle = lifecycle_of(row)
    step = next_step_of(row, lifecycle, SNAPSHOT)
    assert (step.treatment, step.text, step.kind, step.target_date) == (STEP_ENTRY, text, "", None)
    assert step.reasons[0] == REASON_DRAFTING
    assert drafting_action_of(row, lifecycle)["target_date"] == "2026-09-01"


def test_an_instruction_kept_as_a_note_is_named_and_information_is_not():
    information = next_step_of(_row(next_text=INFORMATION), lifecycle_of(_row()), SNAPSHOT)
    assert information.reasons == (REASON_DRAFTING,)
    instruction = next_step_of(_row(next_text=WAIT_REVIEW), lifecycle_of(_row()), SNAPSHOT)
    assert instruction.reasons == (REASON_DRAFTING, "instruction-kept-as-note:WAIT")


@pytest.mark.parametrize(
    "changes",
    [
        {"deadline": None},  # no day for the work: nothing is made up
        {"valja_raw": "ei saatnud", "valja_state": OpinionSentState.NOT_SENT},
        {"status_raw": "jõustunud"},
        {"status_raw": "rohkem ei tegele"},
        {"status_raw": "rohkem ei tegele", "next_text": "Jätkub teema 2026_9 all."},
    ],
)
def test_no_drafting_step_without_a_live_unsent_opinion_and_its_deadline(changes):
    row = _row(**changes)
    assert drafting_action_of(row, lifecycle_of(row)) is None


def test_a_missing_stage_does_not_stop_the_drafting_step():
    row = _row(status_raw="")
    lifecycle = lifecycle_of(row)
    assert lifecycle.status == ACTIVE.key
    assert drafting_action_of(row, lifecycle) is not None


def test_an_ambiguous_date_is_never_guessed():
    row = _sent_row(next_text=AMBIGUOUS)
    step = next_step_of(row, lifecycle_of(row), SNAPSHOT)
    assert step.target_date is None
    assert step.date_precision == "EXACT"
    assert "AMBIGUOUS_DATE" in step.reasons


@pytest.mark.django_db
def test_the_departments_current_status_labels_resolve():
    expected = {
        "jõustumise ootel": "awaiting_entry",
        "Eesti seisukoht koostamisel": "estonian_eu_position",
        "ELi õiguse ülevõtmise ootel": "awaiting_transposition",
        "rohkem ei tegele": "monitoring_stopped",
    }
    for label, key in expected.items():
        mapping = resolve_legacy_status(label)
        assert mapping is not None and mapping.stage is not None, label
        assert mapping.stage.key == key
    assert is_terminal_status("rohkem ei tegele")
    assert is_terminal_status("Rohkem ei tegele ")
    historical = resolve_legacy_status("rohkem pole tegevusi plaanis")
    assert historical.stage is None and historical.disposition == Disposition.MONITORING_STOPPED


# ---------------------------------------------------------------------------
# The placeholder
# ---------------------------------------------------------------------------


def _facts(**changes) -> PlaceholderFacts:
    base = {
        "reference": "2026_4",
        "title": "Saadetud ja jälgitav seadus",
        "sent_on": dt.date(2026, 6, 1),
        "addressee_raw": "Rahandusministeerium",
        "workbook_name": "pilot.xlsx",
        "workbook_sha256": "a" * 64,
        "sheet": "2026",
        "row_number": 5,
    }
    base.update(changes)
    return PlaceholderFacts(**base)


def test_the_placeholder_says_what_it_is_and_nothing_more():
    from pypdf import PdfReader

    content = render_placeholder_pdf(_facts())
    assert content == render_placeholder_pdf(_facts()), "deterministic"
    text = PdfReader(io.BytesIO(content)).pages[0].extract_text()
    for required in (
        HEADLINE,
        SUBHEADLINE,
        "2026_4",
        "01.06.2026",
        "ainult Juristidi proovikasutuse",
    ):
        assert required in text
    assert render_placeholder_pdf(_facts(reference="2026_5")) != content


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------


def test_the_plan_refuses_a_workbook_that_is_not_the_manifests(
    world, workbook, selection, tmp_path
):
    manifest = _manifest(workbook, selection)
    rows = _rows()
    rows[0] = Row(
        "2026_1",
        "Muudetud pealkiri",
        "seadus",
        None,
        None,
        None,
        "Rahandusministeerium",
        "Mari",
        None,
        None,
        "kooskõlastusringil",
        None,
    )
    other = write_workbook(tmp_path / "changed.xlsx", [Sheet(2026, rows)])
    with pytest.raises(PilotError, match="hashes to"):
        build_plan(other, manifest)


def test_the_plan_resolves_every_name(world, workbook, selection):
    plan = build_plan(workbook, _manifest(workbook, selection))
    assert plan.problems == []
    assert plan.database_state == "fresh"
    by_reference = {row.reference: row for row in plan.rows}
    assert by_reference["2026_1"].owner == world["mari"]
    assert len(by_reference["2026_4"].recipients) == 2
    assert by_reference["2026_2"].addressee.name == "Riigikogu majanduskomisjon"


def test_the_apply_writes_the_sample_as_native_records(world, workbook, selection, pilot_on):
    users_before = _accounts_digest()
    report = _apply(workbook, _manifest(workbook, selection))

    assert report.matters == 9
    assert report.by_status == {"ACTIVE": 5, "CONCLUDED": 2, "INACTIVE": 1, "CONTINUES": 1}
    assert _accounts_digest() == users_before, "the pilot touches no account"

    # Active work: open, FULL, staged, classified.
    first = _matter("2026_1")
    assert (first.is_open, first.record_mode, first.stage.key) == (
        True,
        RecordMode.FULL,
        "consultation",
    )
    assert [i.key for i in first.legal_instruments.all()] == ["seadus"]
    assert first.owner == world["mari"]
    assert work_status_of(first) == ACTIVE
    second = _matter("2026_2")
    assert second.stage.key == "awaiting_entry"
    assert [i.key for i in second.legal_instruments.all()] == ["muu-siseriiklik"]

    # Finished work: closed in the register's undated shape, never dated.
    for reference, status in (
        ("2026_6", CONCLUDED),
        ("2026_7", INACTIVE),
        ("2026_8", CONCLUDED),
        ("2026_9", CONTINUES),
    ):
        matter = _matter(reference)
        assert (matter.is_open, matter.record_mode, matter.closed_at) == (
            False,
            RecordMode.ARCHIVE,
            None,
        )
        assert work_status_of(matter) == status, reference
        assert not matter.shows_register_archive_notice
    assert _matter("2026_9").superseded_by == first
    assert _matter("2026_8").stage.key == "monitoring_stopped"

    # Nothing from the register-era machinery.
    assert CurrentRegisterState.objects.count() == 0
    assert ImportRowLedger.objects.count() == 0
    assert ExcelPilotImport.objects.count() == 9
    assert MatterReferenceSequence.objects.get(year=2026).last_number == 15


def test_sent_opinions_are_canonical_with_placeholder_evidence(
    world, workbook, selection, pilot_on
):
    _apply(workbook, _manifest(workbook, selection))
    sends = Submission.objects.filter(status=SubmissionStatus.SENT)
    assert {s.matter.display_reference for s in sends} == {
        "2026_4",
        "2026_5",
        "2026_7",
        "2026_8",
        "2026_9",
    }
    for submission in sends:
        assert submission.sent_at_precision == SentAtPrecision.DATE
        assert timezone.localtime(submission.sent_at).time() == dt.time(0, 0)
        assert submission.created_by is None and submission.sent_by is None
        assert submission.final_version is not None
        assert submission.final_version.original_filename.startswith("PROOVIMPORT-ASENDUSDOKUMENT")
    four = sends.get(matter__reference_number=4)
    assert timezone.localdate(four.sent_at) == dt.date(2026, 6, 1)
    assert four.recipients.count() == 2

    markers = placeholder_versions()
    assert set(markers) == {s.final_version for s in sends}
    assert all(
        v.source_identifier.startswith(excel_pilot.PLACEHOLDER_SOURCE_PREFIX) for v in markers
    )
    assert DocumentVersion.objects.count() == markers.count(), "no other evidence was written"


def test_deadlines_are_answered_declined_or_left_open(world, workbook, selection, pilot_on):
    _apply(workbook, _manifest(workbook, selection))
    assert _matter("2026_1").response_deadline == dt.date(2026, 9, 20), "still being written"
    four = _matter("2026_4")
    assert four.response_deadline is None
    answered = MatterResponseDeadline.objects.get(matter=four)
    assert answered.outcome == ResponseDeadlineOutcome.ANSWERED
    assert answered.submission is not None
    declined = MatterResponseDeadline.objects.get(matter=_matter("2026_6"))
    assert declined.outcome == ResponseDeadlineOutcome.NOT_ANSWERING
    assert not Submission.objects.filter(matter=_matter("2026_6")).exists()


def test_follow_ups_only_for_active_sent_opinions(world, workbook, selection, pilot_on):
    _apply(workbook, _manifest(workbook, selection))
    follow_ups = OpinionFollowUp.objects.select_related("submission__matter")
    assert {f.submission.matter.display_reference for f in follow_ups} == {"2026_4", "2026_5"}
    for follow_up in follow_ups:
        assert follow_up.first_due_on == follow_up.sent_on + dt.timedelta(days=30)
        check = NextAction.objects.get(follow_up=follow_up)
        assert check.status == ActionStatus.PLANNED
        assert check.responsible == follow_up.submission.matter.owner
    assert NextAction.objects.filter(follow_up__isnull=False).count() == 2


def test_the_imported_check_works_through_the_established_outcomes(
    world, workbook, selection, pilot_on
):
    from app.workflow.follow_ups import complete_check, locked_check, reschedule_check

    _apply(workbook, _manifest(workbook, selection))
    four = _matter("2026_4")
    check = NextAction.objects.get(matter=four, follow_up__isnull=False)
    later = timezone.localdate() + dt.timedelta(days=10)
    moved = reschedule_check(matter=four, action_id=check.pk, target_date=later)
    assert moved.pk == check.pk and moved.target_date == later
    with transaction.atomic():
        locked = locked_check(Matter.objects.select_for_update().get(pk=four.pk), check.pk)
        complete_check(action=locked, outcome=FollowUpOutcome.RESPONSE_RECEIVED)
    assert NextAction.objects.get(pk=check.pk).status == ActionStatus.COMPLETED


def test_next_steps_use_the_ordinary_workflow(world, workbook, selection, pilot_on):
    _apply(workbook, _manifest(workbook, selection))
    current = NextAction.objects.filter(status=ActionStatus.OPEN, follow_up__isnull=True)
    by_reference = {action.matter.display_reference: action for action in current}
    assert set(by_reference) == {"2026_1", "2026_2", "2026_3", "2026_4", "2026_5"}
    assert {by_reference[ref].text for ref in ("2026_1", "2026_2", "2026_3")} == {DRAFTING_TEXT}
    assert by_reference["2026_4"].kind == ActionKind.MONITOR
    undated = by_reference["2026_5"]
    assert undated.target_date is None and undated.kind == ActionKind.WAIT
    assert undated.text == AMBIGUOUS, "the instruction is kept word for word"
    assert all(a.responsible == a.matter.owner for a in current)
    assert not NextAction.objects.filter(
        matter__is_open=False, status__in=["OPEN", "PLANNED"]
    ).exists()

    notes = {e.matter.display_reference: e for e in Entry.objects.all()}
    assert set(notes) == {"2026_2", "2026_3", "2026_6", "2026_8", "2026_9"}
    assert all(note.author is None for note in notes.values())
    assert "Jätkub teema 2026_1 all" in notes["2026_9"].body
    assert notes["2026_3"].body == WAIT_REVIEW, "a drafting row's instruction is a note"


def test_every_opinion_being_written_has_one_drafting_step(world, workbook, selection, pilot_on):
    report = _apply(workbook, _manifest(workbook, selection))
    assert report.drafting_actions == 3

    drafting = NextAction.objects.filter(text=DRAFTING_TEXT)
    assert {a.matter.display_reference for a in drafting} == {"2026_1", "2026_2", "2026_3"}
    for action in drafting:
        matter = action.matter
        assert (action.status, action.kind, action.date_semantics, action.date_precision) == (
            ActionStatus.OPEN,
            ActionKind.DO,
            DateSemantics.DEADLINE,
            DatePrecision.EXACT,
        )
        # One obligation seen twice: the step's day is the deadline the Matter keeps open.
        assert action.target_date == matter.response_deadline
        assert action.responsible == matter.owner
        assert action.created_by is None
        assert NextAction.objects.filter(matter=matter).count() == 1, "no second task"
        assert not MatterResponseDeadline.objects.filter(matter=matter).exists()
    # Already past on the day of the import, and kept exactly so.
    assert drafting.get(matter=_matter("2026_1")).target_date == dt.date(2026, 9, 20)
    assert drafting.get(matter=_matter("2026_1")).is_overdue(today=SNAPSHOT)
    assert drafting.get(matter=_matter("2026_3")).target_date == dt.date(2026, 10, 1)

    # The note beside the step on 2026_2 is the cell, word for word, and nothing else.
    assert list(Entry.objects.filter(matter=_matter("2026_2")).values_list("body", flat=True)) == [
        INFORMATION
    ]
    assert not Entry.objects.filter(matter=_matter("2026_1")).exists()


def test_the_drafting_step_is_one_ordinary_history_event(world, workbook, selection, pilot_on):
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    _apply(workbook, _manifest(workbook, selection))
    action = NextAction.objects.get(matter=_matter("2026_1"))
    events = ChangeEvent.objects.filter(
        event_type=ChangeEventType.NEXT_ACTION_SET, object_id=action.pk
    )
    assert events.count() == 1
    event = events.get()
    assert event.actor is None, "no person is invented"
    assert event.payload["provenance"]["treatment"] == "OPINION_DRAFTING"
    assert event.payload["provenance"]["source_field"] == "ARVAMUSE TÄHTAEG"
    assert event.payload["target_date"] == "2026-09-20"
    record = ExcelPilotImport.objects.get(matter=_matter("2026_1"))
    assert record.pilot_version == excel_pilot.PILOT_VERSION
    assert record.interpretation["current_action"]["text"] == DRAFTING_TEXT


def test_the_plan_names_an_instruction_kept_as_a_note(world, workbook, selection):
    plan = build_plan(workbook, _manifest(workbook, selection))
    warnings = {row.reference: row.warnings for row in plan.rows}
    assert any("WAIT instruction" in warning for warning in warnings["2026_3"])
    assert not any("instruction" in warning for warning in warnings["2026_2"])
    lines = "\n".join(excel_pilot.summarise_plan(plan))
    assert "draft=2026-09-20" in lines


def test_a_drafting_row_without_a_stage_stays_active_with_its_step(
    world, selection, pilot_on, tmp_path
):
    rows = _rows()
    rows.insert(
        11,
        Row(
            "2026_13",
            "Teema hetkeseisuta",
            "määrus",
            dt.datetime(2026, 9, 5),
            dt.datetime(2026, 10, 20),
            None,
            "Rahandusministeerium",
            "Jaan",
            None,
            None,
            None,
            None,
        ),
    )
    path = write_workbook(tmp_path / "no-stage.xlsx", [Sheet(2026, rows)])
    _apply(path, _manifest(path, selection))
    matter = _matter("2026_13")
    assert (matter.is_open, matter.stage) == (True, None), "no stage is invented"
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert (step.text, step.target_date, step.responsible) == (
        DRAFTING_TEXT,
        dt.date(2026, 10, 20),
        world["jaan"],
    )


def test_a_manifest_from_the_earlier_rules_is_refused(world, workbook, selection):
    """A changed interpretation invalidates the manifest it no longer produces."""
    stale = _manifest(workbook, selection)
    stale["pilot_version"] = "1.0"
    for row in stale["rows"]:
        row["expected"].pop("current_action")
    with pytest.raises(PilotError, match="different manifest"):
        build_plan(workbook, stale)


def test_native_work_never_gets_a_drafting_step_from_its_deadline(
    world, workbook, selection, pilot_on
):
    """The rule is the pilot's, at import time; a new Teema is untouched (docs/adr/0133 §8)."""
    _apply(workbook, _manifest(workbook, selection))
    native = create_matter(
        title="Uus konsultatsioon",
        actor=world["mari"],
        reference_year=2026,
        response_deadline=dt.date(2026, 11, 30),
    )
    assert not NextAction.objects.filter(matter=native).exists()


def test_no_historical_stage_is_invented(world, workbook, selection, pilot_on):
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
    from app.matters.models import MatterProceduralDevelopment, MatterStageEpisode

    _apply(workbook, _manifest(workbook, selection))
    for matter in Matter.objects.all():
        assert MatterStageEpisode.objects.filter(matter=matter).count() <= 1
    assert not MatterProceduralDevelopment.objects.exists()
    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_CLOSED).exists()


def test_a_second_apply_writes_nothing(world, workbook, selection, pilot_on):
    manifest = _manifest(workbook, selection)
    _apply(workbook, manifest)
    counts = (
        Matter.objects.count(),
        Submission.objects.count(),
        NextAction.objects.count(),
        OpinionFollowUp.objects.count(),
        Entry.objects.count(),
    )
    report = _apply(workbook, manifest)
    assert report.already_applied
    assert counts == (
        Matter.objects.count(),
        Submission.objects.count(),
        NextAction.objects.count(),
        OpinionFollowUp.objects.count(),
        Entry.objects.count(),
    )


def test_the_reservation_protects_the_rest_of_the_register(world, workbook, selection, pilot_on):
    _apply(workbook, _manifest(workbook, selection))
    native = create_matter(
        title="Uus teema pärast piloodi importi", actor=world["mari"], reference_year=2026
    )
    assert native.display_reference == "2026_16"


# ---------------------------------------------------------------------------
# Refusals — nothing written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "setting, override, message",
    [
        ("EXCEL_PILOT_ENABLED", {}, "JURISTID_EXCEL_PILOT"),
        ("REAL_DATA_ALLOWED", {}, "REAL_DATA_ALLOWED"),
        (None, {"operator_intent": "jah"}, "operator-intent"),
        (None, {"expect_manifest_sha256": "0" * 64}, "manifest digest"),
        (None, {"backup_set": " "}, "backup set"),
    ],
)
def test_the_apply_refuses_without_every_authorisation(
    world, workbook, selection, pilot_on, setting, override, message
):
    if setting:
        setattr(pilot_on, setting, False)
    with pytest.raises(PilotError, match=message):
        _apply(workbook, _manifest(workbook, selection), **override)
    assert not Matter.all_objects.exists()


def test_the_pilot_never_mixes_with_other_business_data(world, workbook, selection, pilot_on):
    create_matter(title="Vana arendusteema", actor=world["mari"], reference_year=2026)
    with pytest.raises(PilotError, match="did not create"):
        _apply(workbook, _manifest(workbook, selection))
    assert not ExcelPilotImport.objects.exists()


def test_the_send_evidence_rule_is_unchanged(world):
    matter = factories.MatterFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.create(
            matter=matter, title="Tõendita", status=SubmissionStatus.SENT, sent_at=timezone.now()
        )
    from app.documents.services import add_evidence_version, create_document
    from app.submissions.services import register_sent_opinion

    document = create_document(matter=matter, title="Fail")
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4 x",
        original_filename="x.pdf",
        mime_type="application/pdf",
    )
    with refused("Saatmise registreerimiseks on vaja vähemalt üht adressaati."):
        register_sent_opinion(
            document=document,
            version=version,
            title="Fail",
            recipients=[],
            sent_at=timezone.now(),
            sent_at_precision=SentAtPrecision.DATE,
        )


def test_the_generic_importers_still_schedule_no_follow_up():
    """Only the gated pilot reaches the scheduler from `app.legacy_import`."""
    root = Path(__file__).resolve().parents[1] / "app" / "legacy_import"
    callers = sorted(
        path.name
        for path in root.rglob("*.py")
        if "schedule_first_check" in path.read_text(encoding="utf-8")
        or "schedule_follow_up_of" in path.read_text(encoding="utf-8")
    )
    assert callers == ["excel_pilot.py"]


def _accounts_digest() -> str:
    rows = User.objects.order_by("upn").values_list(
        "upn", "display_name", "role", "is_active", "is_staff", "is_superuser", "password"
    )
    return hashlib.sha256(repr(list(rows)).encode()).hexdigest()


def test_the_opinions_being_written_count_as_drafting_with_no_draft_created(
    world, workbook, selection, pilot_on
):
    """docs/adr/0149: the drafting population reads the native facts the pilot wrote.

    The «Koostan arvamuse» step and the open `Arvamuse tähtaeg` are what make an
    opinion being written read as «koostamisel» — no DRAFT Submission is made to
    reach the number, and a sent, still-active file is not drafting.
    """
    from app.matters.dashboard import drafting_matters

    _apply(workbook, _manifest(workbook, selection))
    reader = world["mari"]
    drafting = {m.display_reference for m in drafting_matters(reader)}
    assert drafting == {"2026_1", "2026_2", "2026_3"}
    assert not Submission.objects.filter(status=SubmissionStatus.DRAFT).exists()
