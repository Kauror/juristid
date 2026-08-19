"""The two audit layers are append-only in the database, not just in Python."""

from __future__ import annotations

import pytest
from django.db import DatabaseError, connection, transaction

from app.audit.enums import ChangeEventType, SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.audit.services import record_change_event, record_security_event
from app.core.errors import ImmutableRecordError
from tests import factories

pytestmark = pytest.mark.django_db


def test_change_event_records_the_object_it_describes(specialist):
    matter = factories.MatterFactory(owner=specialist)
    event = record_change_event(
        event_type=ChangeEventType.MATTER_ASSIGNED,
        matter=matter,
        actor=specialist,
        obj=matter,
        payload={"to": str(specialist.pk)},
    )
    assert event.object_type == "matters.Matter"
    assert event.object_id == matter.pk


def test_change_events_cannot_be_edited_through_the_orm(specialist):
    matter = factories.MatterFactory(owner=specialist)
    event = record_change_event(
        event_type=ChangeEventType.MATTER_CREATED, matter=matter, actor=specialist
    )
    event.summary = "midagi muud"
    with pytest.raises(ImmutableRecordError):
        event.save()
    with pytest.raises(ImmutableRecordError):
        event.delete()


@pytest.mark.parametrize("table", ["audit_changeevent", "audit_securityauditevent"])
def test_audit_tables_reject_raw_updates_and_deletes(table, specialist):
    record_change_event(event_type=ChangeEventType.MATTER_CREATED, actor=specialist)
    record_security_event(event_type=SecurityEventType.EXPORT_GENERATED, actor=specialist)

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET event_type = 'X'")  # noqa: S608

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {table}")  # noqa: S608


def test_security_events_never_carry_document_bytes(specialist):
    event = record_security_event(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
        actor=specialist,
        detail={"document": "id", "size_bytes": 10},
    )
    assert set(event.detail) == {"document", "size_bytes"}
    assert SecurityAuditEvent.objects.count() == 1
