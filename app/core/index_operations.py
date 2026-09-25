"""Index migrations that build concurrently when they can, and plainly when they cannot.

Django's `AddIndexConcurrently` refuses to run inside a transaction, which is
correct: ``CREATE INDEX CONCURRENTLY`` is not allowed in one. But a migration
runs inside a transaction in two places the project depends on, and neither is
production:

* the test suite, where every test is a transaction and a handful of tests
  walk the migration graph backwards across unrelated apps to prove a data
  migration reverses (`tests/test_multiple_senders_migration.py`); and
* any ``migrate`` a caller wraps in `transaction.atomic`.

In a real ``migrate`` a migration marked ``atomic = False`` runs outside any
transaction, and these build the index ``CONCURRENTLY`` — admitting reads and
writes to the table while they do. Inside a transaction they build it plainly,
because that is the only thing PostgreSQL permits there, and a transaction is by
definition not the release's migrate step. The index is identical either way;
only the lock taken while building it differs.

`migration_plan` classifies them as additive, like the operations they extend
(`app.core.deployment.ADDITIVE_OPERATIONS`).
"""

from __future__ import annotations

from typing import Any

from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db.migrations.operations import AddIndex, RemoveIndex


def _in_transaction(schema_editor: Any) -> bool:
    return bool(schema_editor.connection.in_atomic_block)


class AddIndexConcurrentlyWhenPossible(AddIndexConcurrently):
    """`AddIndexConcurrently`, or a plain `AddIndex` inside a transaction."""

    def database_forwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            AddIndex.database_forwards(self, app_label, schema_editor, from_state, to_state)
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            AddIndex.database_backwards(self, app_label, schema_editor, from_state, to_state)
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class RemoveIndexConcurrentlyWhenPossible(RemoveIndexConcurrently):
    """`RemoveIndexConcurrently`, or a plain `RemoveIndex` inside a transaction."""

    def database_forwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            RemoveIndex.database_forwards(self, app_label, schema_editor, from_state, to_state)
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            RemoveIndex.database_backwards(self, app_label, schema_editor, from_state, to_state)
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)
