"""What the reviewed institutions would resolve in the imported register.

A diagnostic, and the tests below exist mostly to keep it one. The measurement
is cheap to get wrong in two expensive ways — merging the two eras' counterparty
columns, and quietly writing the relationships it claims to be counting — and
each of those has a test aimed straight at it.

The era point is the load-bearing one. Column G is ``KELLELT`` up to 2019 and
``KELLELE`` from 2020: who sent it, then who it was sent to. Summing them would
describe a correspondence pattern that never existed, so a sender is only ever
counted as a sender and an addressee only ever as an addressee.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command

from app.audit.models import ChangeEvent
from app.legacy_import.counterparty_coverage import (
    ADDRESSEE,
    AMBIGUOUS,
    BLANK,
    EXACT_ALIAS,
    EXACT_CANONICAL,
    SOURCE,
    UNMATCHED,
    build_coverage_report,
    classify,
)
from app.organisations.models import Organisation
from tests import factories

pytestmark = pytest.mark.django_db

SNAPSHOT = "f" * 64

#: The eras those two sheets belong to, per the reviewed contracts.
ERA_2019 = "2018-2019"
ERA_2021 = "2020-2022"


def register_row(sheet: str, counterparty: str, *, row: int, **matter_kwargs):
    """One imported register row whose column G carries `counterparty`.

    Keyed by column letter, exactly as the importer stores it — the raw row is
    provenance and its shape must not be reinterpreted here.
    """
    matter = factories.MatterFactory(**matter_kwargs)
    return factories.MatterSourceReferenceFactory(
        matter=matter,
        source_sheet=sheet,
        source_row_number=row,
        source_snapshot_sha256=SNAPSHOT,
        source_row_raw={"A": str(row), "B": "Mingi eelnõu", "G": counterparty},
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_a_canonical_ministry_name_matches_exactly():
    register_row("2019", "Rahandusministeerium", row=3)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.counts[(ERA_2019, SOURCE, EXACT_CANONICAL)] == 1
    assert report.total(EXACT_CANONICAL) == 1


def test_spelling_differences_are_not_identity_differences():
    """Casefolded, diacritics stripped, whitespace collapsed — and no further."""
    register_row("2019", "  rahandusMINISTEERIUM ", row=3)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.counts[(ERA_2019, SOURCE, EXACT_CANONICAL)] == 1


def test_a_reviewed_abbreviation_matches_as_an_alias():
    register_row("2019", "MKM", row=4)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.counts[(ERA_2019, SOURCE, EXACT_ALIAS)] == 1


def test_an_empty_cell_is_blank_rather_than_unmatched():
    register_row("2019", "", row=5)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.counts[(ERA_2019, SOURCE, BLANK)] == 1
    assert report.total(UNMATCHED) == 0


def test_an_unknown_institution_is_unmatched_and_never_guessed():
    register_row("2019", "Tehnilise Järelevalve Amet", row=6)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.counts[(ERA_2019, SOURCE, UNMATCHED)] == 1
    assert report.distinct_unresolved == 1


def test_a_compound_value_is_not_split():
    """A Matter may hold several senders. That is not licence to invent them.

    Whether this cell means two ministries, or one ministry and a copy
    recipient, is a reading of the source a person has to make — and splitting
    on ``ja`` would manufacture relationships out of punctuation.
    """
    register_row("2019", "Rahandusministeerium ja Justiits- ja Digiministeerium", row=7)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.counts[(ERA_2019, SOURCE, UNMATCHED)] == 1
    assert report.total(EXACT_CANONICAL) == 0
    assert report.distinct_unresolved == 1


def test_two_reviewed_candidates_are_ambiguous_rather_than_a_coin_toss():
    """Never resolved by iteration order. Ambiguity is reported, not spent."""
    canonical = {"riigikogu": {"Riigikogu", "Riigikogu Kantselei"}}
    assert classify("Riigikogu", canonical, {}) == AMBIGUOUS
    assert classify("Riigikogu", {}, {"riigikogu": {"A", "B"}}) == AMBIGUOUS
    assert classify("Riigikogu", {"riigikogu": {"Riigikogu"}}, {}) == EXACT_CANONICAL


# ---------------------------------------------------------------------------
# Era semantics
# ---------------------------------------------------------------------------


def test_the_pre_2020_column_counts_as_a_sender():
    register_row("2019", "Rahandusministeerium", row=3)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.by_direction(SOURCE) == 1
    assert report.by_direction(ADDRESSEE) == 0


def test_the_post_2020_column_counts_as_an_addressee():
    register_row("2021", "Rahandusministeerium", row=3)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.by_direction(ADDRESSEE) == 1
    assert report.by_direction(SOURCE) == 0
    assert report.counts[(ERA_2021, ADDRESSEE, EXACT_CANONICAL)] == 1


def test_the_two_eras_are_reported_apart_and_never_summed():
    register_row("2019", "Rahandusministeerium", row=3)
    register_row("2021", "Rahandusministeerium", row=4)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)
    figures = report.summary()

    assert figures["by_era"][ERA_2019] == {SOURCE: {EXACT_CANONICAL: 1}}
    assert figures["by_era"][ERA_2021] == {ADDRESSEE: {EXACT_CANONICAL: 1}}
    # The total exists, but only underneath a direction that was never merged.
    assert report.total(EXACT_CANONICAL) == 2


def test_a_sheet_without_a_reviewed_contract_is_reported_not_dropped():
    """A year with no such column and a year with an empty cell differ."""
    register_row("Muu leht", "Rahandusministeerium", row=3)

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.references_read == 1
    assert report.rows_without_a_counterparty_column == 1
    assert report.classified == 0


# ---------------------------------------------------------------------------
# It is a diagnostic
# ---------------------------------------------------------------------------


def test_coverage_writes_nothing_at_all():
    reference = register_row("2019", "Rahandusministeerium", row=3)
    matter = reference.matter
    events = ChangeEvent.objects.count()
    organisations = Organisation.objects.count()

    build_coverage_report(snapshot_sha256=SNAPSHOT)

    matter.refresh_from_db()
    assert matter.source_organisations.count() == 0
    assert matter.addressee_organisation_id is None
    assert ChangeEvent.objects.count() == events
    assert Organisation.objects.count() == organisations


def test_the_summary_reports_no_relationships_written():
    register_row("2019", "Tundmatu Asutus", row=3)
    figures = build_coverage_report(snapshot_sha256=SNAPSHOT).summary()
    assert figures["matter_relationships_written"] == 0


def test_unresolved_values_are_counted_but_never_printed(capsys):
    register_row("2019", "Väga Eriline Osaühing", row=3)
    register_row("2019", "Väga Eriline Osaühing", row=4)

    call_command("reference_data", "coverage", "--expect-register-snapshot-sha256", SNAPSHOT)
    out = capsys.readouterr().out

    assert "Väga Eriline Osaühing" not in out
    assert "Distinct unresolved values  1" in out
    assert "Matters affected            2" in out
    assert "no Matter sender, addressee or ChangeEvent was written" in out


def test_the_json_summary_carries_no_raw_values(capsys):
    register_row("2019", "Väga Eriline Osaühing", row=3)

    call_command(
        "reference_data",
        "coverage",
        "--expect-register-snapshot-sha256",
        SNAPSHOT,
        "--json",
    )
    payload = json.loads(capsys.readouterr().out.split("\n\n")[0])

    assert payload["distinct_unresolved_values"] == 1
    assert "Väga Eriline" not in json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# The snapshot has to be named
# ---------------------------------------------------------------------------


def test_coverage_refuses_without_a_named_snapshot():
    register_row("2019", "Rahandusministeerium", row=3)

    with pytest.raises(CommandError) as failure:
        call_command("reference_data", "coverage")
    assert "expect-register-snapshot-sha256" in str(failure.value)


def test_coverage_refuses_a_snapshot_the_database_does_not_hold():
    register_row("2019", "Rahandusministeerium", row=3)

    with pytest.raises(CommandError) as failure:
        call_command("reference_data", "coverage", "--expect-register-snapshot-sha256", "a" * 64)
    assert "snapshot" in str(failure.value).lower()


def test_only_the_named_register_is_measured():
    """A database imported twice holds two registers, not one twice as long."""
    register_row("2019", "Rahandusministeerium", row=3)
    other = factories.MatterSourceReferenceFactory(
        source_sheet="2019",
        source_row_number=3,
        source_snapshot_sha256="b" * 64,
        source_row_raw={"G": "Rahandusministeerium"},
    )
    assert other.pk

    report = build_coverage_report(snapshot_sha256=SNAPSHOT)

    assert report.references_read == 1


# ---------------------------------------------------------------------------
# The protected operator artifact
# ---------------------------------------------------------------------------


def test_the_operator_artifact_holds_the_values_and_their_counts(tmp_path):
    register_row("2019", "Väga Eriline Osaühing", row=3)
    register_row("2019", "Väga Eriline Osaühing", row=4)
    destination = tmp_path / "unresolved.json"

    call_command(
        "reference_data",
        "coverage",
        "--expect-register-snapshot-sha256",
        SNAPSHOT,
        "--output",
        str(destination),
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["unresolved"] == [
        {"value": "Väga Eriline Osaühing", "occurrences": 2, "matters": 2}
    ]


def test_the_operator_artifact_refuses_a_path_inside_the_checkout():
    """One `git add` away from a pull request is not somewhere this may write."""
    register_row("2019", "Väga Eriline Osaühing", row=3)
    from pathlib import Path

    from app.core.management.commands import reference_data as command_module

    inside = Path(command_module.__file__).resolve().parents[4] / "unresolved.json"

    with pytest.raises(CommandError) as failure:
        call_command(
            "reference_data",
            "coverage",
            "--expect-register-snapshot-sha256",
            SNAPSHOT,
            "--output",
            str(inside),
        )
    assert "inside the checkout" in str(failure.value)
    assert not inside.exists()
