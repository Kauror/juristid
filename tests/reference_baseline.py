"""Putting the migrated database back after a transactional test flushed it.

`django_db(transaction=True)` gives a test real transactions, which it can only
have by not being wrapped in one — so Django cleans up afterwards by flushing
every table (`TransactionTestCase._fixture_teardown`). The flush is indifferent
to where a row came from: rows written by *data migrations* go with everything
else, and migrations are not re-run, so the canonical vocabularies simply
disappear.

Measured on this repository, immediately after `migrate` and again after one
transactional test's teardown:

| table                        | migrated | after a flush |
| ---------------------------- | -------- | ------------- |
| `taxonomy.PolicyArea`        | 28       | 0             |
| `workflow.StageVocabulary`   | 10       | 0             |
| `workflow.LegacyStatusMapping` | 11     | 0             |
| `contenttypes.ContentType`   | 61       | 61            |
| `auth.Permission`            | 244      | 244           |

The last two survive because `flush` emits `post_migrate`, and their receivers
recreate them. Nothing emits the data migrations again, which is the whole
defect.

**What is restored, and where it comes from.** Django snapshots the test
database as a JSON string once, inside `setup_databases()`, immediately after
`create_test_db()` and before any test has run — `connection._test_serialized_contents`.
That snapshot *is* the reviewed migrated state; this module never restates a
vocabulary of its own, and adding a seeded row to a migration needs no change
here. The snapshot only exists when at least one collected test asks for it, by
declaring `serialized_rollback=True`; `tests/test_reference_data_isolation.py`
is what keeps that true, for every transactional test in the suite.

**Why the flush first.** `deserialize_db_from_string` saves each row with its
original primary key, so restoring onto rows that `post_migrate` has already
recreated with *fresh* keys would collide on `django_content_type`'s unique
constraint. A transactional test that declares `serialized_rollback=True`
inhibits that signal and leaves the tables genuinely empty, so the flush here is
a no-op TRUNCATE over empty tables; it is done anyway so the restore is correct
whatever left the database in whatever state.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper

#: The attribute Django writes the migrated snapshot to. Named here rather than
#: spelled out at each use, because it is the one piece of Django's internals
#: this module depends on and it should be findable by grep.
SNAPSHOT_ATTRIBUTE = "_test_serialized_contents"


def snapshot_for(connection: BaseDatabaseWrapper) -> str | None:
    """The migrated-state snapshot Django took for this connection, if any."""
    return getattr(connection, SNAPSHOT_ATTRIBUTE, None)


def connections_with_a_snapshot() -> list[BaseDatabaseWrapper]:
    """Every configured connection Django serialized the migrated state for.

    Iterating aliases rather than `connections.all()`, which would open a
    connection to each one to find out.
    """
    return [
        connections[alias] for alias in connections if snapshot_for(connections[alias]) is not None
    ]


def restore_migrated_baseline() -> list[str]:
    """Return every database with a snapshot to the state `migrate` left it in.

    Returns the aliases it restored, so a caller can assert it did something.
    """
    restored = []
    for connection in connections_with_a_snapshot():
        snapshot = snapshot_for(connection)
        assert snapshot is not None
        call_command(
            "flush",
            verbosity=0,
            interactive=False,
            database=connection.alias,
            reset_sequences=False,
            allow_cascade=False,
            # The signal whose receivers would otherwise recreate content types
            # and permissions under new primary keys, moments before the
            # snapshot reinstates them under their original ones.
            inhibit_post_migrate=True,
        )
        connection.creation.deserialize_db_from_string(snapshot)
        restored.append(connection.alias)
    return restored


# ---------------------------------------------------------------------------
# Which tests this applies to
# ---------------------------------------------------------------------------
#
# The rules below are pytest-django's own, restated: `_get_databases_for_test`
# and the ordering key in `pytest_django.plugin` both decide transactionality
# exactly this way. Restated rather than imported, because both are private and
# a rename in a dependency bump should not be able to take CI down over a rule
# that had not changed.
#
# What keeps the restatement honest is behaviour rather than agreement. The
# marker is the only route into a transactional test this repository uses, and
# `test_the_only_route_into_a_transactional_test_is_the_marker` fails if another
# one appears; `test_the_migrated_baseline_is_back_after_that_flush` fails if
# the restore stops running for the marker. A pytest-django upgrade that moved
# the rule out from under this file therefore shows up as a red test, not as a
# quiet return to the old defect.


#: Fixtures that make a test transactional without the marker saying so.
TRANSACTIONAL_FIXTURES = frozenset({"transactional_db", "live_server", "django_db_reset_sequences"})


def _marker_arguments(marker: pytest.Mark) -> tuple[bool, bool, bool]:
    """Read the `django_db` marker the way pytest-django reads it.

    Binding against a signature rather than looking in `marker.kwargs`, because
    every one of these may be given positionally — `django_db(True)` is a
    transactional test — and a check that only read the keywords would call it
    an ordinary one.
    """

    def signature(
        transaction: bool = False,
        reset_sequences: bool = False,
        databases: object = None,
        serialized_rollback: bool = False,
        available_apps: object = None,
    ) -> tuple[bool, bool, bool]:
        return transaction, reset_sequences, serialized_rollback

    return signature(*marker.args, **marker.kwargs)


def uses_real_transactions(item: pytest.Item) -> bool:
    """Whether this test's database teardown is a flush rather than a rollback."""
    # Imported here rather than at the top: this module is reached from a
    # conftest, and `django.test` wants settings in force before it loads.
    from django.test import TestCase, TransactionTestCase

    test_class = getattr(item, "cls", None)
    if test_class is not None and issubclass(test_class, TransactionTestCase):
        # `TestCase` is itself a subclass of `TransactionTestCase`; only the
        # ones that are *not* also a `TestCase` flush.
        return not issubclass(test_class, TestCase)

    marker = item.get_closest_marker("django_db")
    if marker is not None:
        transaction, reset_sequences, _serialized_rollback = _marker_arguments(marker)
        if transaction or reset_sequences:
            return True
    return bool(TRANSACTIONAL_FIXTURES & set(getattr(item, "fixturenames", ())))


def declares_serialized_rollback(item: pytest.Item) -> bool:
    """Whether this test asks Django for the migrated-state snapshot."""
    marker = item.get_closest_marker("django_db")
    if marker is not None:
        _transaction, _reset_sequences, serialized_rollback = _marker_arguments(marker)
        if serialized_rollback:
            return True
    if "django_db_serialized_rollback" in set(getattr(item, "fixturenames", ())):
        return True
    test_class = getattr(item, "cls", None)
    return bool(getattr(test_class, "serialized_rollback", False))
