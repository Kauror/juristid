"""The stored register state can be checked against today's derivation (ENG-144).

`CurrentRegisterState` is derived from immutable source rows, and nothing
compared it with a fresh projection: a release that changed how the register is
read would leave the table saying the old thing until somebody happened to rerun
the cutover. `check_current_register_state` asks the writer's own projection and
reports, per Matter, a row missing, a row extra, or the names of the columns
that differ — read-only, and never a cell's value.

Every title, name and sentence below is invented (master specification 5.3).
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import.current_state import CurrentRegisterState
from app.legacy_import.current_state_verify import (
    CHANGED,
    COMPARED_FIELDS,
    EXTRA,
    MISSING,
    SnapshotNotSettled,
    verify_current_state,
)
from app.legacy_import.final_cutover import (
    apply_cutover_plan,
    build_cutover_plan,
    rebuild_current_state,
)
from app.matters.enums import RecordMode
from app.matters.services import create_imported_matter
from tests.synthetic_cutover import approve_snapshot
from tests.synthetic_portfolio import (
    CURRENT_YEAR,
    add_source_reference,
    build_people,
    build_register,
    snapshot_for,
)

pytestmark = pytest.mark.django_db

SNAPSHOT = snapshot_for("verify-28-08")
LIVE_STATUS = "kooskõlastusringil"
PRIVATE_INSTRUCTION = "Helistan sünteetilisele ministeeriumile ja küsin eelnõu seisu"


@pytest.fixture
def world(monkeypatch):
    approve_snapshot(monkeypatch, sha256=SNAPSHOT)
    register = build_register(build_people())
    register.snapshot = SNAPSHOT
    return register


def add_matter(register, *, reference: int, **cells):
    matter = create_imported_matter(
        title=f"Sünteetiline eelnõu {reference}",
        reference_year=CURRENT_YEAR,
        reference_number=reference,
        record_mode=RecordMode.FULL,
    )
    add_source_reference(
        register,
        matter,
        snapshot=SNAPSHOT,
        status_cell=cells.pop("status_cell", LIVE_STATUS),
        **cells,
    )
    return matter


@pytest.fixture
def derived(world):
    """Three Matters, their state rebuilt the way the cutover writes it."""
    matters = [
        add_matter(world, reference=1, feedback_requested_cell="220"),
        add_matter(world, reference=2, next_action_cell=PRIVATE_INSTRUCTION),
        add_matter(world, reference=3),
    ]
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))
    return matters


def _run(*args: str) -> tuple[int, str]:
    output = io.StringIO()
    try:
        call_command("check_current_register_state", *args, stdout=output)
    except SystemExit as exit_:
        return int(exit_.code or 0), output.getvalue()
    return 0, output.getvalue()


def test_the_comparison_covers_every_derived_column():
    """Read from the model, so a column added later is compared too."""
    assert "observed_at" not in COMPARED_FIELDS
    assert {"source_snapshot_sha256", "currency", "owner_resolved", "review_reason"} <= set(
        COMPARED_FIELDS
    )


def test_an_exact_match_is_green(derived):
    verification = verify_current_state()

    assert verification.ok
    assert verification.snapshot_sha256 == SNAPSHOT
    assert (verification.stored, verification.projected) == (3, 3)
    code, text = _run()
    assert code == 0
    assert "Stored register state matches the current projection." in text


def test_the_state_the_real_apply_writes_verifies(world):
    """Not only the rebuild: the whole cutover, then a fresh plan, agree."""
    add_matter(world, reference=4)
    add_matter(world, reference=5, status_cell="Arvamus saadetud")
    apply_cutover_plan(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    assert verify_current_state().ok


def test_a_missing_row_is_named(derived):
    CurrentRegisterState.objects.filter(matter=derived[0]).delete()

    verification = verify_current_state()

    assert [(d.kind, d.matter_id) for d in verification.differences] == [(MISSING, derived[0].pk)]
    assert verification.differences[0].reference == derived[0].display_reference


def test_an_extra_row_is_named(derived):
    stray = create_imported_matter(
        title="Sünteetiline registrist puuduv teema",
        reference_year=CURRENT_YEAR,
        reference_number=99,
        record_mode=RecordMode.FULL,
    )
    row = CurrentRegisterState.objects.get(matter=derived[2])
    row.pk = None
    row.matter = stray
    row.save()

    verification = verify_current_state()

    assert [(d.kind, d.matter_id) for d in verification.differences] == [(EXTRA, stray.pk)]


def test_a_changed_projected_field_is_named_by_column(derived):
    CurrentRegisterState.objects.filter(matter=derived[0]).update(member_feedback_requested=221)

    verification = verify_current_state()

    assert [(d.kind, d.matter_id, d.fields) for d in verification.differences] == [
        (CHANGED, derived[0].pk, ("member_feedback_requested",))
    ]


def test_a_change_in_what_the_source_says_is_found(world, derived):
    """What a new reading rule does: the projection moves, the table does not."""
    add_source_reference(
        world,
        derived[2],
        snapshot=SNAPSHOT,
        status_cell=LIVE_STATUS,
        feedback_responded_cell="0",
    )

    verification = verify_current_state()

    assert [d.kind for d in verification.differences] == [CHANGED]
    assert "member_feedback_responded" in verification.differences[0].fields

    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))
    assert verify_current_state().ok


def test_the_report_names_columns_and_never_their_values(derived):
    CurrentRegisterState.objects.filter(matter=derived[1]).update(
        next_action_text="Muudetud tekst", owner_raw="Sünteetiline Isik"
    )

    code, text = _run()

    assert code == 1
    assert "changed: 1" in text
    assert derived[1].display_reference in text
    assert "next_action_text" in text and "owner_raw" in text
    for value in (PRIVATE_INSTRUCTION, "Muudetud tekst", "Sünteetiline Isik", derived[1].title):
        assert value not in text


def test_verifying_writes_nothing(derived):
    CurrentRegisterState.objects.filter(matter=derived[0]).delete()
    before = list(CurrentRegisterState.objects.order_by("matter_id").values())

    with CaptureQueriesContext(connection) as queries:
        assert not verify_current_state().ok

    writes = [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []
    assert list(CurrentRegisterState.objects.order_by("matter_id").values()) == before


def test_two_stored_snapshots_are_refused_rather_than_guessed(derived):
    CurrentRegisterState.objects.filter(matter=derived[0]).update(
        source_snapshot_sha256=snapshot_for("verify-21-08")
    )

    with pytest.raises(SnapshotNotSettled):
        verify_current_state()
    code, text = _run()
    assert code == 1
    assert "--snapshot" in text

    # Naming the approved one verifies against it, and the odd row is changed.
    verification = verify_current_state(snapshot_sha256=SNAPSHOT)
    assert [(d.kind, d.fields) for d in verification.differences] == [
        (CHANGED, ("source_snapshot_sha256",))
    ]


def test_an_empty_table_with_no_snapshot_is_green(db):
    code, text = _run()
    assert code == 0
    assert "(none stored)" in text
