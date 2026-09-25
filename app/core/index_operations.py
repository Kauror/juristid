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
from django.db.migrations.operations import AddConstraint, AddIndex, RemoveConstraint, RemoveIndex
from django.db.models import UniqueConstraint


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


class AddUniqueIndexConstraintConcurrentlyWhenPossible(AddConstraint):
    """`AddConstraint` for a conditional `UniqueConstraint`, built ``CONCURRENTLY``.

    PostgreSQL implements a `UniqueConstraint` with a ``condition`` as a partial
    unique index rather than a table constraint, so it can be built the way an
    index can: ``CREATE UNIQUE INDEX CONCURRENTLY``, admitting writes while it
    builds instead of refusing them for the whole scan. Django has no operation
    for that. Refuses any other constraint rather than build it some other way.

    Still an `AddConstraint` to the release gate, which flags it for a decision
    (`app.core.deployment.CONSEQUENTIAL_OPERATIONS`): building it concurrently
    changes the lock, not what the database starts refusing.
    """

    def database_forwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            super().database_forwards(app_label, schema_editor, from_state, to_state)
            return
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            schema_editor.execute(_concurrently(self.constraint, model, schema_editor))

    def database_backwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            super().database_backwards(app_label, schema_editor, from_state, to_state)
            return
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            schema_editor.execute(_drop_concurrently(self.constraint, schema_editor))


class RemoveUniqueIndexConstraintConcurrentlyWhenPossible(RemoveConstraint):
    """`RemoveConstraint` for a conditional `UniqueConstraint`, dropped ``CONCURRENTLY``."""

    def database_forwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            super().database_forwards(app_label, schema_editor, from_state, to_state)
            return
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            constraint = from_state.models[app_label, self.model_name_lower].get_constraint_by_name(
                self.name
            )
            schema_editor.execute(_drop_concurrently(constraint, schema_editor))

    def database_backwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if _in_transaction(schema_editor):
            super().database_backwards(app_label, schema_editor, from_state, to_state)
            return
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            constraint = to_state.models[app_label, self.model_name_lower].get_constraint_by_name(
                self.name
            )
            schema_editor.execute(_concurrently(constraint, model, schema_editor))


_UNIQUE_INDEX = "CREATE UNIQUE INDEX "


def _partial_unique(constraint: Any) -> None:
    if not isinstance(constraint, UniqueConstraint) or constraint.condition is None:
        raise TypeError(
            f"{constraint.name}: only a conditional UniqueConstraint is a unique index "
            "PostgreSQL can build concurrently"
        )


def _concurrently(constraint: Any, model: Any, schema_editor: Any) -> str:
    _partial_unique(constraint)
    statement = str(constraint.create_sql(model, schema_editor))
    if not statement.startswith(_UNIQUE_INDEX):
        raise TypeError(f"{constraint.name}: unexpected DDL {statement!r}")
    return "CREATE UNIQUE INDEX CONCURRENTLY " + statement[len(_UNIQUE_INDEX) :]


def _drop_concurrently(constraint: Any, schema_editor: Any) -> str:
    _partial_unique(constraint)
    return f"DROP INDEX CONCURRENTLY IF EXISTS {schema_editor.quote_name(constraint.name)}"
