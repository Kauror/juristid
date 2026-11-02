"""Lovable's own audit ledger in the observation (docs/adr/0201): disabled by
default; when switched on, read forward from a cursor, page by page.

The ledger is a fake (tests/test_lovable_observation_collector.FakeRegister);
every event, field and value is synthetic.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from app.legacy_import import lovable_observation
from app.legacy_import.lovable_observation_models import (
    AuditAccess,
    LovableAuditCursor,
    LovableSourceEvent,
    LovableSourceEventField,
    ObservationRunStatus,
)
from app.legacy_import.lovable_source import SourceError
from app.legacy_import.lovable_sync_models import SyncTrigger
from tests.test_lovable_observation_collector import (  # noqa: F401
    NIGHT,
    FakeRegister,
    _id,
    _observe,
    _register,
    observing,
)

pytestmark = pytest.mark.django_db

MOMENT = "2026-11-01T09:00:00+00:00"


def _event_id(n: int) -> str:
    return str(uuid.UUID(int=0xE0000000_0000_4000_8000_000000000000 + n))


def _event(n: int, *, at: str = MOMENT, topic: int = 1, action: str = "muutmine") -> dict:
    return {
        "id": _event_id(n),
        "topic_id": _id(topic),
        "action": action,
        "actor_id": "never-read",
        "actor_kind": "kasutaja",
        "version": n + 1,
        "occurred_at": at,
    }


def _field(n: int, name: str, old: Any, new: Any) -> dict:
    return {"event_id": _event_id(n), "field": name, "old_value": old, "new_value": new}


@pytest.fixture
def audit_on(settings, observing):  # noqa: F811
    settings.LOVABLE_AUDIT_ENABLED = True


def _manual(source: FakeRegister, minutes: int) -> Any:
    return _observe(source, NIGHT + dt.timedelta(minutes=minutes), trigger=SyncTrigger.MANUAL)


def test_disabled_audit_requests_nothing_from_the_ledger(observing):  # noqa: F811
    source = _register()
    source.events = [_event(1)]
    run = _observe(source)
    assert run.status == ObservationRunStatus.SUCCEEDED
    assert run.audit_access == AuditAccess.DISABLED and run.audit_error == ""
    assert not source.audit_tables_called()
    assert not LovableSourceEvent.objects.exists()


def test_events_are_stored_with_allowlisted_values_and_withheld_others(audit_on):
    source = _register()
    source.events = [_event(1)]
    source.fields = [
        _field(1, "hetkeseis", "Riigikogus", "Valitsuses"),
        _field(1, "joustumised", [], [{"kuupaev": "2027-01-01"}]),
        _field(1, "markmed", "SISEMINE-A", "SISEMINE-B"),
        _field(1, "mitteametlik_arvamus", "M1", "M2"),
        _field(1, "allikas", "toor", "toor2"),
        _field(1, "teema", "x" * 5000, "y"),
    ]
    run = _observe(source)
    assert run.audit_access == AuditAccess.AVAILABLE, run.audit_error
    event = LovableSourceEvent.objects.get()
    assert (event.source_event_id, event.action, event.actor_kind) == (
        _event_id(1),
        "muutmine",
        "kasutaja",
    )
    assert event.run == run and len(event.sha256) == 64
    fields = {f.field: f for f in event.fields.all()}
    assert (fields["hetkeseis"].old_value, fields["hetkeseis"].new_value) == (
        '"Riigikogus"',
        '"Valitsuses"',
    )
    assert fields["joustumised"].new_value == '[{"kuupaev": "2027-01-01"}]'
    for withheld in ("markmed", "mitteametlik_arvamus", "allikas"):
        assert fields[withheld].withheld
        assert (fields[withheld].old_value, fields[withheld].new_value) == ("", "")
    assert len(fields["teema"].old_value) == lovable_observation.AUDIT_VALUE_MAX
    stored = " ".join(f"{f.old_value} {f.new_value}" for f in LovableSourceEventField.objects.all())
    assert "SISEMINE" not in stored and "never-read" not in str(LovableSourceEvent.objects.values())
    assert run.counts["audit"]["withheld"] == 3


def test_same_moment_events_page_by_id_and_a_reread_is_idempotent(audit_on, monkeypatch):
    monkeypatch.setattr(lovable_observation, "AUDIT_PAGE_SIZE", 2)
    source = _register()
    # Five events at one moment: the page boundary falls between them.
    source.events = [_event(n) for n in (5, 1, 4, 2, 3)]
    run = _observe(source)
    assert run.audit_access == AuditAccess.AVAILABLE
    assert set(LovableSourceEvent.objects.values_list("source_event_id", flat=True)) == {
        _event_id(n) for n in range(1, 6)
    }
    cursor = LovableAuditCursor.objects.get()
    assert cursor.last_event_id == _event_id(5) and cursor.backfill_complete
    assert run.counts["audit"]["pages"] == 3

    # Nothing new: a re-read asks after the cursor and stores nothing.
    again = _manual(source, 10)
    assert again.counts["audit"]["events_new"] == 0
    # Reading from the start again (a lost cursor) stores no duplicate.
    LovableAuditCursor.objects.all().delete()
    LovableAuditCursor.objects.create(pk=1, backfill_complete=True)
    third = _manual(source, 20)
    assert third.counts["audit"]["events_seen"] == 5
    assert LovableSourceEvent.objects.count() == 5


def test_an_interrupted_backfill_resumes_after_the_last_committed_page(audit_on, monkeypatch):
    monkeypatch.setattr(lovable_observation, "AUDIT_PAGE_SIZE", 2)
    source = _register()
    source.events = [_event(n, at=f"2026-11-01T09:00:0{n}+00:00") for n in range(1, 8)]
    reads = {"n": 0}
    original = source.select

    def flaky(table, columns, *, filters, order):
        if table == "topic_change_field":
            reads["n"] += 1
            if reads["n"] == 3:
                raise SourceError("UNREACHABLE", "Lovable could not be reached (TimeoutError).")
        return original(table, columns, filters=filters, order=order)

    source.select = flaky  # type: ignore[method-assign]
    first = _observe(source)
    assert first.status == ObservationRunStatus.SUCCEEDED  # the reading itself stands
    assert first.audit_access == AuditAccess.UNAVAILABLE
    assert first.audit_error.startswith("UNREACHABLE")
    assert LovableSourceEvent.objects.count() == 4  # two committed pages
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(4)

    second = _manual(source, 30)
    assert second.audit_access == AuditAccess.AVAILABLE
    assert sorted(LovableSourceEvent.objects.values_list("source_event_id", flat=True)) == [
        _event_id(n) for n in range(1, 8)
    ]


def test_a_value_changed_and_changed_back_is_two_events(audit_on):
    source = _register()
    source.events = [
        _event(1, at="2026-11-01T09:00:00+00:00"),
        _event(2, at="2026-11-01T10:00:00+00:00"),
    ]
    source.fields = [
        _field(1, "hetkeseis", "Riigikogus", "Valitsuses"),
        _field(2, "hetkeseis", "Valitsuses", "Riigikogus"),
    ]
    _observe(source)
    events = list(LovableSourceEvent.objects.order_by("occurred_at"))
    assert len(events) == 2
    assert [e.fields.get().new_value for e in events] == ['"Valitsuses"', '"Riigikogus"']


@pytest.mark.parametrize(
    "refusal",
    [
        SourceError("HTTP_401", "Lovable answered 401 on /rest/v1/topic_change_event."),
        SourceError("HTTP_403", "Lovable answered 403 on /rest/v1/topic_change_event."),
        SourceError(
            "HTTP_400",
            "Lovable answered 400 on /rest/v1/topic_change_event.",
            postgrest_code="42501",
        ),
    ],
)
def test_a_refused_ledger_is_unavailable_and_nothing_is_inferred(audit_on, refusal):
    source = _register()
    source.audit_fail = refusal
    run = _observe(source)
    assert run.status == ObservationRunStatus.SUCCEEDED
    assert run.audit_access == AuditAccess.UNAVAILABLE
    assert run.audit_error.startswith("REFUSED")
    assert not LovableSourceEvent.objects.exists()
    assert LovableAuditCursor.objects.get().last_event_id == ""


def test_no_rows_on_the_first_ever_read_is_unavailable_not_empty(audit_on):
    source = _register()
    run = _observe(source)
    assert run.audit_access == AuditAccess.UNAVAILABLE
    assert run.audit_error == "NO_ROWS_VISIBLE"
    assert not LovableAuditCursor.objects.get().backfill_complete


def test_events_past_the_retention_are_not_kept(audit_on):
    source = _register()
    source.events = [
        _event(1, at="2026-09-01T09:00:00+00:00"),
        _event(2, at="2026-11-01T09:00:00+00:00"),
    ]
    run = _observe(source)
    assert list(LovableSourceEvent.objects.values_list("source_event_id", flat=True)) == [
        _event_id(2)
    ]
    assert run.counts["audit"]["events_expired"] == 1
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(2)


def test_the_cursor_filter_is_built_only_from_stored_values():
    cursor = LovableAuditCursor(
        last_occurred_at=dt.datetime(2026, 11, 1, 9, tzinfo=dt.UTC), last_event_id=_event_id(3)
    )
    assert lovable_observation._cursor_filter(cursor) == {
        "or": (
            '(occurred_at.gt."2026-11-01T09:00:00+00:00",'
            f'and(occurred_at.eq."2026-11-01T09:00:00+00:00",id.gt.{_event_id(3)}))'
        )
    }
    cursor.last_event_id = "x),id.gt.(0"
    with pytest.raises(SourceError):
        lovable_observation._cursor_filter(cursor)
