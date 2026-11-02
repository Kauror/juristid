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
from app.legacy_import.lovable_source import (
    AUDIT_VALUE_FIELDS,
    SourceError,
    audit_name_filter,
    audit_value_filter,
)
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
    assert len(fields["teema"].old_value) == lovable_observation.AUDIT_VALUE_LIMIT
    assert fields["teema"].truncated and fields["teema"].new_value == '"y"'
    assert not fields["hetkeseis"].truncated and not fields["markmed"].truncated
    stored = " ".join(f"{f.old_value} {f.new_value}" for f in LovableSourceEventField.objects.all())
    assert "SISEMINE" not in stored and "never-read" not in str(LovableSourceEvent.objects.values())
    assert run.counts["audit"]["withheld"] == 3
    assert run.counts["audit"]["truncated"] == 1


def test_same_moment_events_page_by_id_and_a_reread_is_idempotent(audit_on, monkeypatch):
    monkeypatch.setattr(lovable_observation, "AUDIT_PAGE_SIZE", 2)
    source = _register()
    # Five events at one moment: the page boundary falls between them.
    source.events = [_event(n) for n in (5, 1, 4, 2, 3)]
    source.fields = [_field(n, "hetkeseis", "a", "b") for n in range(1, 6)]
    run = _observe(source)
    assert run.audit_access == AuditAccess.AVAILABLE
    assert set(LovableSourceEvent.objects.values_list("source_event_id", flat=True)) == {
        _event_id(n) for n in range(1, 6)
    }
    cursor = LovableAuditCursor.objects.get()
    assert cursor.last_event_id == _event_id(5) and cursor.backfill_complete
    assert run.counts["audit"]["pages"] == 3

    # Nothing new: a re-read (from the overlap before the cursor) stores nothing.
    again = _manual(source, 10)
    assert again.counts["audit"]["events_new"] == 0
    assert again.counts["audit"]["events_seen"] == 5 and again.counts["audit"]["late"] == 0
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
    source.fields = [_field(n, "hetkeseis", "a", "b") for n in range(1, 8)]
    reads = {"n": 0}
    original = source.select

    def flaky(table, columns, *, filters, order):
        if table == "topic_change_field":
            reads["n"] += 1
            # Two field reads (values, then names only) per page: the fifth
            # is the third page's first.
            if reads["n"] == 5:
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
    source.fields = [_field(n, "hetkeseis", "a", "b") for n in (1, 2)]
    run = _observe(source)
    assert list(LovableSourceEvent.objects.values_list("source_event_id", flat=True)) == [
        _event_id(2)
    ]
    assert run.counts["audit"]["events_expired"] == 1
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(2)


def test_the_cursor_filter_is_built_only_from_stored_values():
    """A run starts `AUDIT_OVERLAP` (48 h) before the stored cursor."""
    assert lovable_observation.AUDIT_OVERLAP == dt.timedelta(hours=48)
    cursor = LovableAuditCursor(
        last_occurred_at=dt.datetime(2026, 11, 3, 9, tzinfo=dt.UTC), last_event_id=_event_id(3)
    )
    assert lovable_observation._cursor_filter(cursor) == {
        "or": (
            '(occurred_at.gt."2026-11-01T09:00:00+00:00",'
            'and(occurred_at.eq."2026-11-01T09:00:00+00:00",'
            "id.gt.00000000-0000-0000-0000-000000000000))"
        )
    }
    cursor.last_event_id = "x),id.gt.(0"
    with pytest.raises(SourceError):
        lovable_observation._cursor_filter(cursor)
    with pytest.raises(SourceError):
        lovable_observation._after(dt.datetime(2026, 11, 1, tzinfo=dt.UTC), "x),id.gt.(0")


# -- excluded values are never requested (review of PR #586, P1) -------------


EXCLUDED = ("markmed", "mitteametlik_arvamus", "allikas", "tundmatu_uus_veerg")


def _mixed_fields(n: int) -> list[dict]:
    return [
        _field(n, "hetkeseis", "Riigikogus", "Valitsuses"),
        _field(n, "markmed", f"SISEMINE-{n}", "SISEMINE-B"),
        _field(n, "mitteametlik_arvamus", f"ARVAMUS-{n}", "ARVAMUS-B"),
        _field(n, "allikas", f"TOOR-{n}", "TOOR-B"),
        _field(n, "tundmatu_uus_veerg", f"UUS-{n}", "UUS-B"),
    ]


def test_values_are_requested_only_for_allowlisted_field_names(audit_on):
    source = _register()
    source.events = [_event(n, at=f"2026-11-01T09:00:0{n}+00:00") for n in (1, 2)]
    source.fields = _mixed_fields(1) + _mixed_fields(2)
    run = _observe(source)
    assert run.audit_access == AuditAccess.AVAILABLE, run.audit_error

    assert source.field_requests, "the ledger's fields were read"
    for columns, filters in source.field_requests:
        assert set(filters) == {"event_id", "field"}
        if {"old_value", "new_value"} & set(columns):
            # A positive filter, of allowlisted names only, at the source.
            assert filters["field"] == audit_value_filter()
            names = set(filters["field"][4:-1].split(","))
            assert names <= AUDIT_VALUE_FIELDS and not names & set(EXCLUDED)
        else:
            assert columns == ("event_id", "field")
            assert filters["field"] == audit_name_filter()
    # What Juristid was handed never carried a value of an excluded field.
    for answer in source.field_answers:
        for row in answer:
            if row["field"] in EXCLUDED:
                assert "old_value" not in row and "new_value" not in row, row
    handed = str(source.field_answers)
    assert not any(word in handed for word in ("SISEMINE", "ARVAMUS", "TOOR", "UUS-"))

    fields = {
        f.field: f
        for f in LovableSourceEventField.objects.filter(event__source_event_id=_event_id(1))
    }
    assert set(fields) == {"hetkeseis", *EXCLUDED}
    assert fields["hetkeseis"].new_value == '"Valitsuses"' and not fields["hetkeseis"].withheld
    for name in EXCLUDED:
        assert fields[name].withheld
        assert (fields[name].old_value, fields[name].new_value) == ("", "")
    assert run.counts["audit"]["withheld"] == 8


def test_a_source_that_ignores_the_field_filter_is_flagged_and_nothing_is_kept(audit_on):
    source = _register()
    source.ignore_field_filter = True
    source.events = [_event(1)]
    source.fields = _mixed_fields(1)
    run = _observe(source)
    assert run.status == ObservationRunStatus.SUCCEEDED  # the reading itself stands
    assert run.audit_access == AuditAccess.UNAVAILABLE
    assert run.audit_error == "SOURCE_FILTER_IGNORED"
    assert not LovableSourceEvent.objects.exists()
    assert not LovableSourceEventField.objects.exists()
    assert LovableAuditCursor.objects.get().last_event_id == ""
    assert "SISEMINE" not in str(run.counts)


# -- field visibility and completeness (review of PR #586, P1) ---------------


def test_events_visible_but_fields_hidden_store_nothing_until_fields_are_visible(audit_on):
    """Partial row-level security: events answer, field rows answer `200 []`."""
    source = _register()
    source.events = [_event(n, at=f"2026-11-01T09:00:0{n}+00:00") for n in (1, 2, 3)]
    hidden = [_field(n, "hetkeseis", "a", f"b{n}") for n in (1, 2, 3)]
    source.fields = []
    run = _observe(source)
    assert run.audit_access == AuditAccess.UNAVAILABLE
    assert run.audit_error == "FIELDS_NOT_VISIBLE"
    assert not LovableSourceEvent.objects.exists()
    cursor = LovableAuditCursor.objects.get()
    assert cursor.last_event_id == "" and cursor.last_occurred_at is None

    source.fields = hidden  # access fixed
    later = _manual(source, 30)
    assert later.audit_access == AuditAccess.AVAILABLE, later.audit_error
    assert later.counts["audit"]["events_new"] == 3
    values = sorted(LovableSourceEventField.objects.values_list("new_value", flat=True))
    assert values == ['"b1"', '"b2"', '"b3"']
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(3)


def test_an_edit_without_field_rows_stops_the_reading_at_that_event(audit_on):
    source = _register()
    source.events = [
        _event(1, at="2026-11-01T09:00:01+00:00", action="lisamine"),  # may have none
        _event(2, at="2026-11-01T09:00:02+00:00"),
        _event(3, at="2026-11-01T09:00:03+00:00"),  # an edit with no visible field
        _event(4, at="2026-11-01T09:00:04+00:00"),
    ]
    source.fields = [_field(2, "hetkeseis", "a", "b"), _field(4, "hetkeseis", "b", "c")]
    run = _observe(source)
    assert run.audit_access == AuditAccess.UNAVAILABLE
    assert run.audit_error == "INCOMPLETE_EVENT"
    assert sorted(LovableSourceEvent.objects.values_list("source_event_id", flat=True)) == [
        _event_id(1),
        _event_id(2),
    ]
    cursor = LovableAuditCursor.objects.get()
    assert cursor.last_event_id == _event_id(2) and not cursor.backfill_complete

    source.fields.append(_field(3, "teema", "x", "y"))
    later = _manual(source, 30)
    assert later.audit_access == AuditAccess.AVAILABLE, later.audit_error
    assert LovableSourceEvent.objects.count() == 4
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(4)


# -- late-arriving events (review of PR #586, P2) ---------------------------


def test_an_event_that_appears_behind_the_cursor_within_the_overlap_is_picked_up(audit_on):
    source = _register()
    source.events = [_event(2, at="2026-11-01T10:00:00+00:00")]
    source.fields = [_field(2, "hetkeseis", "a", "b")]
    _observe(source)
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(2)

    # Committed late in Lovable: an hour *before* the stored cursor.
    source.events.append(_event(1, at="2026-11-01T09:00:00+00:00"))
    source.fields.append(_field(1, "teema", "x", "y"))
    later = _manual(source, 30)
    audit = later.counts["audit"]
    assert (audit["events_new"], audit["events_seen"], audit["late"]) == (1, 1, 1)
    assert LovableSourceEvent.objects.count() == 2
    # The cursor never moves backwards.
    assert LovableAuditCursor.objects.get().last_event_id == _event_id(2)

    # Beyond the overlap it is missed: the documented limitation (ADR 0201 §4).
    source.events.append(_event(3, at="2026-10-29T09:00:00+00:00"))
    source.fields.append(_field(3, "teema", "x", "z"))
    third = _manual(source, 60)
    assert third.counts["audit"]["events_new"] == 0
    assert not LovableSourceEvent.objects.filter(source_event_id=_event_id(3)).exists()
