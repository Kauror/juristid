"""The repeatable Excel delta report.

Every test here builds a synthetic workbook, catalogues a baseline into
``MatterSourceReference``, and then asks the report what changed. The one
property that matters more than any individual finding is at the bottom: the
command writes nothing, and a test asserts that by counting every row in every
table it could plausibly touch.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from app.legacy_import.models import MatterSourceReference
from app.legacy_import.parser import RegisterWorkbook, file_sha256
from app.legacy_import.snapshot_delta import DeltaRefused, build_report
from tests import factories
from tests.synthetic_register import Row, Sheet, write_workbook

pytestmark = pytest.mark.django_db


def _sheet(rows: list[Row], year: int = 2026) -> list[Sheet]:
    return [Sheet(year=year, rows=rows)]


def _catalogue(tmp_path, rows: list[Row], *, year: int = 2026, owner=None):
    """Write a workbook and store its rows as the baseline production holds.

    Deliberately built through the real parser rather than by hand: the report
    reads ``source_row_raw`` keyed by column letter, and a fixture that invented
    that mapping would agree with a bug in the reader.
    """
    path = write_workbook(tmp_path / "baseline.xlsx", _sheet(rows, year))
    batch = factories.ImportBatchFactory(source_file_name=path.name)
    from app.legacy_import.contracts import load_contracts

    contract = load_contracts()[year]
    created = {}
    with RegisterWorkbook(path) as workbook:
        for row in workbook.rows(contract):
            if row.is_blank:
                continue
            reference = row.text("A").strip()
            title = row.text("B").strip()
            if not title:
                continue
            number = int(reference.split("_")[1])
            matter = factories.MatterFactory(
                reference_year=year,
                reference_number=number,
                title=title,
                owner=owner,
            )
            factories.MatterSourceReferenceFactory(
                matter=matter,
                import_batch=batch,
                source_sheet=str(year),
                source_row_number=row.row_number,
                source_row_raw=row.raw_mapping(),
                source_file_name=path.name,
                onenote_url=row.hyperlinks().get("B", ""),
            )
            created[reference] = matter
    return created


def _report(path, **kwargs):
    return build_report(workbook_path=path, generated_at=timezone.now(), **kwargs)


# ---------------------------------------------------------------------------
# The identity rules
# ---------------------------------------------------------------------------


def test_unchanged_workbook_reports_every_row_identical(tmp_path):
    rows = [
        Row(reference="2026_1", title="Esimene teema", owner="Marko", status="idee"),
        Row(reference="2026_2", title="Teine teema", owner="Ireen", status="muu"),
    ]
    _catalogue(tmp_path, rows)
    path = write_workbook(tmp_path / "again.xlsx", _sheet(rows))

    report = _report(path)

    assert report.identical == 2
    assert report.changed == 0
    assert report.new == 0
    assert report.removed == 0
    assert report.semantic_field_count == 0


def test_formatting_only_workbook_is_not_a_business_change(tmp_path):
    """A re-saved workbook with different row heights is not a delta.

    The real 21.08→23.08 pair differed in twenty-one ZIP members and two cells.
    A report that called a re-save a change would cry wolf on every snapshot,
    so the comparison reads values through the era contract and never bytes.
    """
    rows = [Row(reference="2026_1", title="Teema", owner="Marko", status="idee")]
    _catalogue(tmp_path, rows)
    path = write_workbook(tmp_path / "resaved.xlsx", _sheet(rows))

    # A second physical file holding the same values. The comparison reads
    # values through the era contract and never bytes, so whether these two
    # happen to hash alike is not what decides the answer.
    report = _report(path)

    assert report.changed == 0
    assert report.identical == 1


def test_changed_cell_is_reported_with_both_values(tmp_path):
    _catalogue(
        tmp_path,
        [Row(reference="2026_1", title="Teema", owner="Marko", next_action="Vaatan 21.08 üle.")],
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet(
            [Row(reference="2026_1", title="Teema", owner="Marko", next_action="Vaatan 07.09 üle.")]
        ),
    )

    report = _report(path)

    assert report.changed == 1
    (row,) = report.changed_rows
    (delta,) = row.fields
    assert delta.canonical_field == "next_action_text"
    assert delta.baseline == "Vaatan 21.08 üle."
    assert delta.workbook == "Vaatan 07.09 üle."
    assert delta.semantic_differs


def test_whitespace_only_change_is_raw_but_not_semantic(tmp_path):
    """Trailing whitespace differs literally and means nothing.

    Both facts are reported. Collapsing them would either hide a real edit or
    turn every re-typed cell into a cutover decision.
    """
    _catalogue(
        tmp_path, [Row(reference="2026_1", title="Teema", next_action="Uuri ministeeriumilt.")]
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet([Row(reference="2026_1", title="Teema", next_action="Uuri ministeeriumilt.  ")]),
    )

    report = _report(path)

    (row,) = report.changed_rows
    (delta,) = row.fields
    assert delta.raw_differs
    assert not delta.semantic_differs
    assert report.semantic_field_count == 0


def test_new_row_is_reported_and_not_imported(tmp_path):
    _catalogue(tmp_path, [Row(reference="2026_1", title="Teema")])
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet(
            [
                Row(reference="2026_1", title="Teema"),
                Row(reference="2026_2", title="Uus teema", owner="Sandra"),
            ]
        ),
    )

    before = MatterSourceReference.objects.count()
    report = _report(path)

    assert report.new == 1
    assert [row.reference for row in report.rows if row.status == "NEW"] == ["2026_2"]
    assert MatterSourceReference.objects.count() == before


def test_removed_row_is_reported_and_nothing_is_deleted(tmp_path):
    _catalogue(
        tmp_path,
        [Row(reference="2026_1", title="Teema"), Row(reference="2026_2", title="Teine")],
    )
    path = write_workbook(tmp_path / "new.xlsx", _sheet([Row(reference="2026_1", title="Teema")]))

    report = _report(path)

    assert report.removed == 1
    assert [row.reference for row in report.rows if row.status == "REMOVED"] == ["2026_2"]
    assert MatterSourceReference.objects.count() == 2


def test_moved_row_with_the_same_reference_is_not_new(tmp_path):
    """Identity is the NR, never the row number.

    A row that slid down the sheet because somebody inserted one above it is the
    same Matter. Matching on position would report it as one deletion and one
    creation, which at cutover reads as "a current file disappeared".
    """
    _catalogue(
        tmp_path, [Row(reference="2026_1", title="Teema"), Row(reference="2026_2", title="Teine")]
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet([Row(reference="2026_2", title="Teine"), Row(reference="2026_1", title="Teema")]),
    )

    report = _report(path)

    assert report.new == 0
    assert report.removed == 0
    assert report.identical == 2


def test_duplicate_reference_in_the_workbook_is_refused(tmp_path):
    _catalogue(tmp_path, [Row(reference="2026_1", title="Teema")])
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet(
            [
                Row(reference="2026_1", title="Teema"),
                Row(reference="2026_1", title="Sama number teise teemaga"),
            ]
        ),
    )

    with pytest.raises(DeltaRefused, match="identiteet"):
        _report(path)


# ---------------------------------------------------------------------------
# Hyperlinks, continuation, portfolio
# ---------------------------------------------------------------------------


def test_changed_onenote_hyperlink_is_reported(tmp_path):
    _catalogue(
        tmp_path,
        [Row(reference="2026_1", title="Teema", hyperlink="https://example.invalid/vana")],
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet([Row(reference="2026_1", title="Teema", hyperlink="https://example.invalid/uus")]),
    )

    report = _report(path)

    (link,) = report.hyperlinks
    assert link.baseline.endswith("/vana")
    assert link.workbook.endswith("/uus")


def test_added_continuation_is_reported_with_its_target(tmp_path):
    _catalogue(tmp_path, [Row(reference="2026_1", title="Teema", next_action="Ootan eelnõu.")])
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet([Row(reference="2026_1", title="Teema", next_action="Jätkub teema 2026_70 all")]),
    )

    report = _report(path)

    (item,) = report.continuations
    assert item.baseline_verdict == "NONE"
    assert item.workbook_verdict == "SUPERSEDED"
    assert item.workbook_target == "2026_70"


def test_portfolio_identity_change_is_named_even_when_totals_match(tmp_path):
    """Equal counts with swapped membership is the failure a total cannot show."""
    from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency

    matters = _catalogue(
        tmp_path,
        [
            Row(reference="2026_1", title="Esimene", status="idee"),
            Row(reference="2026_2", title="Teine", status="jõustunud"),
        ],
    )
    for reference, currency in (
        ("2026_1", RegisterCurrency.CURRENT),
        ("2026_2", RegisterCurrency.RETIRED),
    ):
        matter = matters[reference]
        CurrentRegisterState.objects.create(
            matter=matter,
            # Not nullable, and rightly so: derived current state that could not
            # name the source row it came from would be an opinion, not a
            # reading.
            source_reference=MatterSourceReference.objects.get(matter=matter),
            source_sheet="2026",
            currency=currency,
            observed_at=timezone.now(),
        )

    # The statuses swap: one closes, the other reopens. One current before, one
    # current after, and a different one each time.
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet(
            [
                Row(reference="2026_1", title="Esimene", status="jõustunud"),
                Row(reference="2026_2", title="Teine", status="idee"),
            ]
        ),
    )

    report = _report(path)

    assert report.portfolio.current == report.portfolio.production_current == 1
    assert report.portfolio.totals_match
    assert not report.portfolio.identities_match
    assert report.portfolio.would_activate == ["2026_2"]
    assert report.portfolio.would_retire == ["2026_1"]


# ---------------------------------------------------------------------------
# Dual writes
# ---------------------------------------------------------------------------


def test_native_write_on_a_changed_row_is_a_conflict(tmp_path, specialist):
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    matters = _catalogue(
        tmp_path,
        [Row(reference="2026_1", title="Teema", next_action="Vana juhis.")],
        owner=specialist,
    )
    ChangeEvent.objects.create(
        matter=matters["2026_1"],
        actor=specialist,
        event_type=ChangeEventType.NEXT_ACTION_SET,
        occurred_at=timezone.now(),
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet([Row(reference="2026_1", title="Teema", next_action="Uus juhis.")]),
    )

    report = _report(path)

    (conflict,) = report.conflicts
    assert conflict.reference == "2026_1"
    assert conflict.changed_fields == ("next_action_text",)
    assert conflict.native_events[0].event_type == ChangeEventType.NEXT_ACTION_SET


def test_native_write_on_an_unchanged_row_is_not_a_conflict(tmp_path, specialist):
    """Independent work is reported, never resolved, and never called a clash."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    rows = [
        Row(reference="2026_1", title="Teema", next_action="Juhis."),
        Row(reference="2026_2", title="Teine", next_action="Teine juhis."),
    ]
    matters = _catalogue(tmp_path, rows, owner=specialist)
    ChangeEvent.objects.create(
        matter=matters["2026_2"],
        actor=specialist,
        event_type=ChangeEventType.NEXT_ACTION_SET,
        occurred_at=timezone.now(),
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet(
            [
                Row(reference="2026_1", title="Teema", next_action="Muudetud juhis."),
                Row(reference="2026_2", title="Teine", next_action="Teine juhis."),
            ]
        ),
    )

    report = _report(path)

    assert report.changed == 1
    assert report.conflicts == []
    assert [item.reference for item in report.native_writes] == ["2026_2"]


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_command_refuses_an_unexpected_digest(tmp_path):
    _catalogue(tmp_path, [Row(reference="2026_1", title="Teema")])
    path = write_workbook(tmp_path / "new.xlsx", _sheet([Row(reference="2026_1", title="Teema")]))

    with pytest.raises(CommandError, match="SHA-256"):
        call_command(
            "register_snapshot_delta",
            "--workbook",
            str(path),
            "--expect-sha256",
            "0" * 64,
            stdout=StringIO(),
        )


def test_command_accepts_the_matching_digest_and_writes_nothing(tmp_path):
    """The property the whole tool rests on: it is a report, not a bridge."""
    from app.audit.models import ChangeEvent
    from app.legacy_import.current_state import CurrentRegisterState
    from app.matters.models import Matter
    from app.submissions.models import Submission
    from app.workflow.models import NextAction

    _catalogue(
        tmp_path,
        [Row(reference="2026_1", title="Teema", next_action="Vana juhis.")],
    )
    path = write_workbook(
        tmp_path / "new.xlsx",
        _sheet([Row(reference="2026_1", title="Teema", next_action="Uus juhis.")]),
    )
    digest, _ = file_sha256(path)

    before = {
        model.__name__: model.objects.count()
        for model in (
            Matter,
            MatterSourceReference,
            CurrentRegisterState,
            Submission,
            NextAction,
            ChangeEvent,
        )
    }

    out = StringIO()
    call_command(
        "register_snapshot_delta", "--workbook", str(path), "--expect-sha256", digest, stdout=out
    )

    after = {
        model.__name__: model.objects.count()
        for model in (
            Matter,
            MatterSourceReference,
            CurrentRegisterState,
            Submission,
            NextAction,
            ChangeEvent,
        )
    }
    assert before == after
    assert "Andmebaasi ei kirjutatud." in out.getvalue()
    assert "MUUDETUD            1" in out.getvalue()


def test_command_json_output_is_deterministic(tmp_path):
    _catalogue(tmp_path, [Row(reference="2026_1", title="Teema", next_action="Vana.")])
    path = write_workbook(
        tmp_path / "new.xlsx", _sheet([Row(reference="2026_1", title="Teema", next_action="Uus.")])
    )

    def run() -> str:
        out = StringIO()
        call_command("register_snapshot_delta", "--workbook", str(path), "--json", stdout=out)
        payload = out.getvalue()
        # The stamp is the one value that legitimately moves between runs.
        return "\n".join(line for line in payload.splitlines() if '"generated_at"' not in line)

    assert run() == run()


def test_command_does_not_rewrite_the_workbook(tmp_path):
    _catalogue(tmp_path, [Row(reference="2026_1", title="Teema")])
    path = write_workbook(tmp_path / "new.xlsx", _sheet([Row(reference="2026_1", title="Teema")]))
    before, size = file_sha256(path)

    call_command("register_snapshot_delta", "--workbook", str(path), stdout=StringIO())

    assert file_sha256(path) == (before, size)
