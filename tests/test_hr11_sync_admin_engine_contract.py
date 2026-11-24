"""HR-11: «Vaata erinevusi» reads the Lovable engine through a fixed contract.

`sync_admin` used to find `pending_differences` by name and guess attribute
names on each row, so an engine rename made the differences page quietly
empty. It now imports the engine names directly and reads `PendingDifference`
by its declared fields. These tests hold that contract:

* every engine name `sync_admin` uses exists, with the signature it calls, so a
  rename in `lovable_sync` fails here (and at import) instead of in production;
* `engine_rows`, `differences` and `difference_for` read the real
  `pending_differences` after a real COMPARE night — nothing monkeypatched.

Synthetic data only: the fake Lovable transport of `tests.test_lovable_sync`.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.lovable_sync_labels import LOVABLE_REASONS
from app.legacy_import.lovable_sync_models import SyncMode
from tests.admin_session import technical_admin
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _run, cohort  # noqa: F401

SYNC_ADMIN = Path(sync_admin.__file__)
ENGINE = "app.legacy_import.lovable_sync"


def _engine_names_used() -> set[str]:
    """Every `lovable_sync` name `sync_admin` reaches: ``from … import X`` (module
    level or inside a function) and ``lovable_sync.X``."""
    tree = ast.parse(SYNC_ADMIN.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == ENGINE:
            names.update(alias.name for alias in node.names)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "lovable_sync"
        ):
            names.add(node.attr)
    return names


def _keyword_only(function: object) -> set[str]:
    return {
        name
        for name, p in inspect.signature(function).parameters.items()  # type: ignore[arg-type]
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


# ---------------------------------------------------------------------------
# The contract (no database)
# ---------------------------------------------------------------------------


def test_sync_admin_reads_the_engine_by_name_nowhere():
    """No `getattr(lovable_sync, …)` and no signature probing: a missing engine
    name must be an ImportError/AttributeError, never an empty page."""
    tree = ast.parse(SYNC_ADMIN.read_text(encoding="utf-8"))
    engine_aliases = {"lovable_sync"} | {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == ENGINE
    }
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id in {"getattr", "hasattr"}
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in engine_aliases
        ):
            found.append(f"{func.id}(…) at line {node.lineno}")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "signature"
            and isinstance(func.value, ast.Name)
            and func.value.id == "inspect"
        ):
            found.append(f"inspect.signature at line {node.lineno}")
    assert found == []


def test_every_engine_name_sync_admin_uses_exists():
    used = _engine_names_used()
    # The scan itself must see the names this finding is about.
    assert {
        "pending_differences",
        "PendingDifference",
        "FIELD_POLICY",
        "STEP_FIELDS",
        "historical_hold",
        "resolve_field",
        "SyncRefused",
        "RESOLVABLE_FIELDS",
    } <= used
    missing = sorted(name for name in used if not hasattr(lovable_sync, name))
    assert missing == [], f"sync_admin uses engine names that no longer exist: {missing}"


def test_the_engine_signatures_sync_admin_calls():
    # pending_differences(*, visible_to=None) -> list[PendingDifference]
    sig = inspect.signature(lovable_sync.pending_differences)
    assert _keyword_only(lovable_sync.pending_differences) == {"visible_to"}
    assert sig.parameters["visible_to"].default is None
    # resolve_field(*, reference, field_name, note, actor=None)
    assert {"reference", "field_name", "note", "actor"} <= _keyword_only(lovable_sync.resolve_field)
    # historical_hold(cohort, reason, baseline, source): called positionally.
    assert list(inspect.signature(lovable_sync.historical_hold).parameters) == [
        "cohort",
        "reason",
        "baseline",
        "source",
    ]
    # SyncRefused carries the refusal code the page audits.
    assert lovable_sync.SyncRefused("X", "y").code == "X"


def test_the_pending_difference_row_has_the_fields_the_page_reads():
    fields = {f.name for f in dataclasses.fields(lovable_sync.PendingDifference)}
    assert {
        "kind",
        "field",
        "reason",
        "reference",
        "since",
        "matter",
        "baseline",
        "current",
        "source",
        "cohort",
    } <= fields
    policy_fields = {f.name for f in dataclasses.fields(lovable_sync.FieldPolicy)}
    assert "explanation" in policy_fields
    assert all(isinstance(p, lovable_sync.FieldPolicy) for p in lovable_sync.FIELD_POLICY.values())


def test_the_step_fields_are_the_engines_own():
    assert sync_admin.STEP_FIELDS is lovable_sync.STEP_FIELDS


# ---------------------------------------------------------------------------
# Through the real engine (one COMPARE night)
# ---------------------------------------------------------------------------


@pytest.fixture
def held_night(cohort):  # noqa: F811
    """A COMPARE night in which Lovable moved 2026_501's stage: one pending FIELD."""
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    return cohort


@pytest.mark.django_db
def test_engine_rows_reads_the_real_pending_differences(held_night):
    reader = held_night["world"]["mari"]
    pending = [
        p
        for p in lovable_sync.pending_differences(visible_to=reader)
        if p.reference == "2026_501" and p.field == "stage"
    ]
    assert len(pending) == 1
    item = pending[0]

    rows, hidden, held = sync_admin.engine_rows(reader)
    row = next(r for r in rows if r.reference == "2026_501" and r.field == "stage")
    assert row.matter.pk == item.matter.pk
    assert row.source == "Valitsuses" == sync_admin.value_text(item.source)
    assert row.accepted == sync_admin.value_text(item.baseline)
    assert row.juristid == sync_admin.value_text(item.current)
    assert row.since == item.since
    expected = (
        LOVABLE_REASONS.get(item.reason, "") or lovable_sync.FIELD_POLICY["stage"].explanation
    )
    assert row.explanation == expected and row.explanation
    assert row.resolvable is True
    assert row.historical is False
    # The page shows the same row.
    diff = sync_admin.differences(reader)
    assert any(r.reference == "2026_501" and r.field == "stage" for r in diff.rows)
    assert diff.named >= 1
    assert hidden >= 0 and held == 0


@pytest.mark.django_db
def test_difference_for_finds_the_real_row_and_nothing_else(held_night):
    reader = held_night["world"]["mari"]
    row = sync_admin.difference_for(reader, "2026_501", "stage")
    assert row is not None and row.source == "Valitsuses"
    assert sync_admin.difference_for(reader, "2026_501", "title") is None
    assert sync_admin.difference_for(reader, "2026_999", "stage") is None


@pytest.mark.django_db
def test_a_reader_without_a_persona_gets_counts_never_names(held_night, gate):
    """The technical administrator with no persona opens no Teema: the engine's
    difference is counted in ``hidden``, never named."""
    admin_user, _secret = technical_admin()
    server = lovable_sync.pending_differences(visible_to=None)
    assert server
    rows, hidden, held = sync_admin.engine_rows(admin_user)
    assert rows == []
    assert hidden + held == len(server)
    assert sync_admin.difference_for(admin_user, "2026_501", "stage") is None


@pytest.fixture
def gate(settings):
    from tests.admin_session import apply_admin_gate

    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    return settings
