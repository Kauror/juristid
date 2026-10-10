"""A lawyer's save on a Matter an import holds does not wait for the import (AUD-043).

Real threads and two connections. An owner backfill is paused inside its
transaction, holding the Matters it names; meanwhile a second connection
inserts a row that references one of them — the `ChangeEvent` every save, upload
and search refresh writes. That insert takes `FOR KEY SHARE` on the Matter,
which the apply's old `FOR UPDATE` blocked for the whole batch and the
interactive strength, `FOR NO KEY UPDATE`, does not.

Two connections only (this machine's emulated PostgreSQL will not start many
backends at once).
"""

from __future__ import annotations

import threading

import pytest
from django.db import connections, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.services import record_change_event
from app.legacy_import import owner_backfill
from app.legacy_import.owner_backfill import apply_backfill_plan, build_backfill_plan
from app.matters.models import Matter
from tests.synthetic_portfolio import build_portfolio

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_a_child_insert_completes_while_an_apply_holds_the_matter(monkeypatch):
    build_portfolio()
    plan = build_backfill_plan()
    held_id = plan.assignable[0].matter.pk

    holding = threading.Event()
    release = threading.Event()
    real_assign = owner_backfill.assign_matter

    def assign_and_hold(**kwargs):
        result = real_assign(**kwargs)
        if not holding.is_set():
            holding.set()
            release.wait(timeout=30)
        return result

    monkeypatch.setattr(owner_backfill, "assign_matter", assign_and_hold)
    errors: list[BaseException] = []

    def apply() -> None:
        try:
            apply_backfill_plan(plan)
        except BaseException as error:
            errors.append(error)
        finally:
            connections.close_all()

    applier = threading.Thread(target=apply)
    applier.start()
    try:
        assert holding.wait(timeout=30), "the apply never reached its first write"

        inserted = threading.Event()

        def insert_child() -> None:
            try:
                with transaction.atomic():
                    record_change_event(
                        event_type=ChangeEventType.MATTER_TITLE_CHANGED,
                        matter=Matter.objects.get(pk=held_id),
                        summary="Samaaegne salvestus",
                    )
                inserted.set()
            finally:
                connections.close_all()

        writer = threading.Thread(target=insert_child)
        writer.start()
        # The apply still holds every lock it took; the insert must not wait.
        assert inserted.wait(timeout=15), "a child insert waited for the import"
        writer.join(timeout=15)
    finally:
        release.set()
        applier.join(timeout=60)

    assert errors == []
    assert ChangeEvent.objects.filter(matter_id=held_id, summary="Samaaegne salvestus").exists()
