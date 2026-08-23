"""The sender migration, driven through the real executor in both directions.

The forward step has to preserve *every* sender the singular column held, and the
reverse step has to refuse rather than guess once a Matter has two. Both are
things that can only be checked by running the migration: a unit test of the
copy function would prove the Python is right about a schema nobody applied.

Each test runs inside the ordinary test transaction, so the schema changes it
makes are rolled back with everything else — PostgreSQL keeps DDL transactional,
which is what makes migrating backwards inside a test safe here.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from tests import factories

pytestmark = pytest.mark.django_db

BEFORE = ("matters", "0007_matter_data_class")
AFTER = ("matters", "0008_multiple_source_organisations")

MATTER_TABLE = "matters_matter"
LINK_TABLE = "matters_mattersourceorganisation"


def migrate_to(target: tuple[str, str]) -> None:
    """Move the `matters` app to one migration, rebuilding the graph first.

    ``SET CONSTRAINTS ALL IMMEDIATE`` first, and it is not optional. Django
    creates its foreign keys ``DEFERRABLE INITIALLY DEFERRED``, so every row a
    test wrote leaves a pending trigger event on the table — and PostgreSQL
    refuses ``ALTER TABLE`` on a table that has any, with *cannot ALTER TABLE
    because it has pending trigger events*. Setting the constraints immediate
    fires the queue and empties it, which is what lets a migration run inside
    the same transaction as the data it is migrating.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


def singular_senders() -> dict[str, str | None]:
    """`{matter id: sender id}` read straight from the restored column."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id, source_organisation_id FROM {MATTER_TABLE}")  # noqa: S608
        return {
            str(matter): str(sender) if sender else None for matter, sender in cursor.fetchall()
        }


def plural_senders() -> set[tuple[str, str]]:
    """Every `(matter, organisation)` pair in the join table."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT matter_id, organisation_id FROM {LINK_TABLE}")  # noqa: S608
        return {(str(matter), str(organisation)) for matter, organisation in cursor.fetchall()}


def column_exists(table: str, column: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        return cursor.fetchone() is not None


def table_exists(table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"public.{table}"])
        return cursor.fetchone()[0] is not None


@pytest.fixture
def restore_schema():
    """Put the schema back however the test ends.

    The transaction rollback would do it too, but a test that left the executor
    mid-way and then failed for another reason should not take the rest of the
    module down with it.
    """
    yield
    migrate_to(AFTER)


# -- shape ------------------------------------------------------------------


def test_the_migration_is_three_steps_and_no_raw_sql():
    """Add the relation, copy into it, drop the old column. Nothing else."""
    loader = MigrationExecutor(connection).loader
    migration = loader.get_migration(*AFTER)
    kinds = [type(operation).__name__ for operation in migration.operations]

    assert kinds == [
        "CreateModel",
        "AddConstraint",
        "AddField",
        "RunPython",
        "RemoveField",
    ]
    assert "RunSQL" not in kinds


def test_the_old_column_is_gone_and_the_join_table_is_there():
    assert not column_exists(MATTER_TABLE, "source_organisation_id")
    assert table_exists(LINK_TABLE)


def test_the_pair_is_unique_in_the_database():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conname = %s",
            ["matters_unique_source_organisation_per_matter"],
        )
        assert cursor.fetchone() is not None


# -- forward ----------------------------------------------------------------


def test_every_singular_sender_becomes_exactly_one_relation(specialist, restore_schema):
    """The reconciliation the production upgrade is judged on.

    One relation row per Matter whose old column was set, none for the ones
    where it was null, and no duplicate for an organisation two Matters shared
    (Agent-E brief 14, 16).
    """
    shared = factories.OrganisationFactory()
    lone = factories.OrganisationFactory()
    first = factories.MatterFactory(owner=specialist, source_organisations=[shared])
    second = factories.MatterFactory(owner=specialist, source_organisations=[shared])
    third = factories.MatterFactory(owner=specialist, source_organisations=[lone])
    empty = factories.MatterFactory(owner=specialist)

    # Down to the singular schema, which is where a production database sits
    # before the upgrade...
    migrate_to(BEFORE)
    before = singular_senders()
    assert before[str(first.pk)] == str(shared.pk)
    assert before[str(second.pk)] == str(shared.pk)
    assert before[str(empty.pk)] is None
    populated = sum(1 for sender in before.values() if sender)

    # ...and back up, which is the step this test is about.
    migrate_to(AFTER)

    pairs = plural_senders()
    assert pairs == {
        (str(first.pk), str(shared.pk)),
        (str(second.pk), str(shared.pk)),
        (str(third.pk), str(lone.pk)),
    }
    assert len(pairs) == populated
    assert not any(matter == str(empty.pk) for matter, _ in pairs)


# -- reverse ----------------------------------------------------------------


def test_reversing_restores_the_singular_column_when_nobody_has_two(specialist, restore_schema):
    ministry = factories.OrganisationFactory()
    with_sender = factories.MatterFactory(owner=specialist, source_organisations=[ministry])
    without = factories.MatterFactory(owner=specialist)

    migrate_to(BEFORE)

    restored = singular_senders()
    assert restored[str(with_sender.pk)] == str(ministry.pk)
    assert restored[str(without.pk)] is None


def test_reversing_refuses_once_a_matter_has_two_senders(specialist, restore_schema):
    """Fail closed. Every way of picking one sender to keep destroys data.

    Rollback stays available right up to the moment real multi-sender data
    exists; after that it is a decision a person has to make, not one a
    migration may make quietly (Agent-E brief 15).
    """
    matter = factories.MatterFactory(
        owner=specialist,
        source_organisations=[factories.OrganisationFactory(), factories.OrganisationFactory()],
    )

    with pytest.raises(RuntimeError) as error:
        migrate_to(BEFORE)

    message = str(error.value)
    assert "more than one sender" in message
    assert str(matter.pk) in message
    # And it refused rather than half-converting: the plural relation survives.
    assert len(plural_senders()) == 2
