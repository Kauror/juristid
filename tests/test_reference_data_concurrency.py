"""Two removals of one organisation spelling: one removes it, the other is refused.

`remove_organisation_alias` used to read the spelling *before* locking its
institution. A second removal queued on the lock then carried a row the first
had already deleted, deleted nothing and audited a removal anyway. It now
locks the institution first and reads the spelling again under the lock.

Real PostgreSQL, two transactions, three connections in all (the test's own
and one per thread). The interleaving is forced without a sleep: the first
thread holds the institution's row lock until PostgreSQL reports the second
queued behind it.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.db import connection, connections, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.organisations import administration as catalogue
from app.organisations.models import Organisation, OrganisationAlias
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

WAIT_SECONDS = 15


def _someone_waits_on_me() -> bool:
    with connection.cursor() as cursor:
        # Statistics views are snapshotted per transaction; this one polls
        # inside a transaction, so the snapshot is dropped before each read.
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND pg_backend_pid() = ANY(pg_blocking_pids(pid)))"
        )
        return bool(cursor.fetchone()[0])


def test_a_second_concurrent_removal_finds_the_spelling_gone_and_audits_nothing():
    actor = factories.DepartmentHeadFactory()
    body = factories.OrganisationFactory(name="Samaaegne amet")
    alias = OrganisationAlias.objects.create(organisation=body, alias="SA")

    holding = threading.Event()
    second_started = threading.Event()
    outcome: dict[str, object] = {}

    def first() -> None:
        try:
            with transaction.atomic():
                Organisation.objects.select_for_update(no_key=True).get(pk=body.pk)
                holding.set()
                second_started.wait(WAIT_SECONDS)
                deadline = time.monotonic() + WAIT_SECONDS
                while not _someone_waits_on_me() and time.monotonic() < deadline:
                    time.sleep(0.02)
                outcome["first_saw_waiter"] = _someone_waits_on_me()
                # The first removal, as the service makes it.
                OrganisationAlias.objects.filter(pk=alias.pk).delete()
        finally:
            connections.close_all()

    def second() -> None:
        try:
            holding.wait(WAIT_SECONDS)
            second_started.set()
            catalogue.remove_organisation_alias(actor=actor, alias=alias, expected_revision=None)
            outcome["second"] = "removed"
        except DomainError as error:
            outcome["second"] = str(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_SECONDS * 2)

    assert outcome.get("first_saw_waiter") is True, outcome
    assert outcome.get("second") == catalogue.ALIAS_NOT_FOUND
    assert not ChangeEvent.objects.filter(
        event_type=ChangeEventType.ORGANISATION_ALIAS_REMOVED
    ).exists()
