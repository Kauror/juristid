"""The database cache's table comes with the schema (L-02).

The deployed settings keep the cache in PostgreSQL, in ``core_cache``, and
nothing made that table except a hand-run ``createcachetable``: a fresh
install, a migrate-from-zero and CI's recovery rehearsal had none, and the MCP
OAuth ``/oauth/token`` and ``/oauth/revoke`` answered 500 on the first
``cache.add``. ``core/0003`` makes it, exactly as ``createcachetable`` would,
and does nothing where it already exists.

The test database here is itself a migrate-from-zero, so the first two tests
are the fresh-install case. The rest take the table away inside the test's own
transaction (PostgreSQL DDL rolls back with it), to show the failure and to
apply the migration over each state a database can be in.
"""

from __future__ import annotations

import importlib
from urllib.parse import urlencode

import pytest
from django.core.cache import caches
from django.core.management import call_command
from django.db import ProgrammingError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import Client

import config.settings as deployed
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

TABLE = "core_cache"
REFERENCE = "aud_w3_reference_cache"
MIGRATION = ("core", "0003_database_cache_table")
BEFORE = ("core", "0002_jit_off_for_this_database")

DATABASE_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": TABLE,
    }
}


@pytest.fixture
def database_cache(settings):
    """The cache the deployed settings run, not the test settings' local one."""
    settings.CACHES = DATABASE_CACHE
    return caches["default"]


def _exists(table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [table])
        return bool(cursor.fetchone()[0])


def _drop_cache_table() -> None:
    """The state of a database nobody ran `createcachetable` on. Rolled back after the test."""
    with connection.cursor() as cursor:
        cursor.execute(f'DROP TABLE "{TABLE}"')
    assert not _exists(TABLE)


def _migration():
    return MigrationLoader(connection, ignore_no_migrations=True).get_migration(*MIGRATION)


def _apply() -> None:
    state = MigrationLoader(connection).project_state(BEFORE)
    with connection.schema_editor(atomic=True) as editor:
        _migration().apply(state, editor)


def _unapply() -> None:
    loader = MigrationLoader(connection)
    state = loader.project_state(MIGRATION)
    with connection.schema_editor(atomic=True) as editor:
        _migration().unapply(state, editor)


def _shape(table: str) -> dict[str, object]:
    """Columns, constraints and indexes, with the table's own name taken out."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position
            """,
            [table],
        )
        columns = cursor.fetchall()
        cursor.execute(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname = current_schema() AND tablename = %s ORDER BY indexname
            """,
            [table],
        )
        indexes = [
            (name.replace(table, "<t>"), definition.replace(table, "<t>"))
            for name, definition in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT c.conname, c.contype, pg_get_constraintdef(c.oid)
            FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = %s AND t.relnamespace = current_schema()::regnamespace
            ORDER BY c.conname
            """,
            [table],
        )
        constraints = [
            (name.replace(table, "<t>"), kind, definition)
            for name, kind, definition in cursor.fetchall()
        ]
    return {"columns": columns, "indexes": indexes, "constraints": constraints}


def _physical(table: str) -> tuple[int, int]:
    """The relation's identity and its file: a rebuild or a re-create changes one of them."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT oid::int, relfilenode::int FROM pg_class WHERE oid = to_regclass(%s)", [table]
        )
        return tuple(cursor.fetchone())


def _oauth_statuses(settings) -> tuple[int, int]:
    """What `/oauth/token` and `/oauth/revoke` answer a client that is not enrolled."""
    support.enable(settings)
    client = Client(raise_request_exception=False)
    body = urlencode({"grant_type": "authorization_code", "client_id": "nobody"})
    kind = "application/x-www-form-urlencoded"
    token = client.post("/oauth/token", body, content_type=kind).status_code
    revoke = client.post("/oauth/revoke", body, content_type=kind).status_code
    return token, revoke


# -- a database migrated from zero ---------------------------------------------------


def test_the_deployed_settings_name_the_table_the_migration_makes() -> None:
    """The migration names `core_cache`; it is only right while the settings do too."""
    assert deployed.CACHES["default"]["BACKEND"] == "django.core.cache.backends.db.DatabaseCache"
    assert deployed.CACHES["default"]["LOCATION"] == TABLE


def test_a_database_migrated_from_zero_has_the_cache_table(database_cache) -> None:
    applied = MigrationRecorder(connection).applied_migrations()
    assert MIGRATION in applied, "the test database was migrated without core/0003"
    assert _exists(TABLE), "a migrate-from-zero left no core_cache"

    assert database_cache.add("aud-w3:fresh", 1, timeout=60) is True
    assert database_cache.add("aud-w3:fresh", 2, timeout=60) is False
    assert database_cache.incr("aud-w3:fresh") == 2


def test_oauth_token_and_revoke_do_not_answer_500_on_a_fresh_database(
    settings, database_cache
) -> None:
    token, revoke = _oauth_statuses(settings)
    assert token != 500 and revoke != 500, (token, revoke)
    assert 400 <= token < 500 and 400 <= revoke < 500, (token, revoke)


# -- the failure, and the migration over every state a database is in -----------------


def test_without_the_table_the_oauth_endpoints_fail_and_the_migration_mends_them(
    settings, database_cache
) -> None:
    """The reproduction of L-02, then the fix applied to that very state."""
    _drop_cache_table()
    with pytest.raises(ProgrammingError, match="core_cache"), transaction.atomic():
        database_cache.add("aud-w3:missing", 1, timeout=60)
    # The request swallows the error into a 500 and leaves the transaction
    # aborted, so it runs inside a savepoint that is rolled back by hand.
    savepoint = transaction.savepoint()
    try:
        assert _oauth_statuses(settings) == (500, 500)
    finally:
        transaction.savepoint_rollback(savepoint)

    _apply()

    assert _exists(TABLE)
    assert database_cache.add("aud-w3:mended", 1, timeout=60) is True
    token, revoke = _oauth_statuses(settings)
    assert token != 500 and revoke != 500, (token, revoke)


def test_the_table_is_the_one_createcachetable_makes(database_cache) -> None:
    call_command("createcachetable", REFERENCE, verbosity=0)
    try:
        _drop_cache_table()
        _apply()
        made, reference = _shape(TABLE), _shape(REFERENCE)
        assert made == reference
        # Production's two, by name.
        assert [name for name, _ in made["indexes"]] == ["<t>_expires", "<t>_pkey"]
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{REFERENCE}"')


def test_applying_it_where_the_table_exists_changes_nothing(database_cache) -> None:
    """Production's state: a table made by hand, with rows in it."""
    _drop_cache_table()
    call_command("createcachetable", verbosity=0)  # as it was made on the host
    database_cache.set("aud-w3:kept", "live counter", timeout=600)
    shape, physical = _shape(TABLE), _physical(TABLE)

    _apply()
    _apply()

    assert _shape(TABLE) == shape
    assert _physical(TABLE) == physical, "the table was re-created or rewritten"
    assert database_cache.get("aud-w3:kept") == "live counter"


def test_the_reverse_leaves_the_table_and_its_rows(database_cache) -> None:
    database_cache.set("aud-w3:rollback", "still here", timeout=600)
    shape, physical = _shape(TABLE), _physical(TABLE)

    _unapply()

    assert _exists(TABLE), "stepping back dropped a table the previous code needs too"
    assert _shape(TABLE) == shape
    assert _physical(TABLE) == physical
    assert database_cache.get("aud-w3:rollback") == "still here"

    _apply()
    assert database_cache.get("aud-w3:rollback") == "still here"


def test_the_migration_is_sql_a_reader_can_see_and_a_noop_backwards() -> None:
    module = importlib.import_module("app.core.migrations.0003_database_cache_table")
    (operation,) = module.Migration.operations
    assert type(operation).__name__ == "RunSQL"
    assert operation.reversible
    assert "to_regclass('core_cache') IS NULL" in module.CREATE_CACHE_TABLE
    for forbidden in ("DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT", "ALTER"):
        assert forbidden not in module.CREATE_CACHE_TABLE.upper(), forbidden
