"""Index migrations build concurrently in a real migrate, and plainly in a transaction.

`app.core.index_operations` exists because Django's concurrent index operations
refuse to run inside a transaction, and the test suite is one: tests that walk
the migration graph backwards across the search app failed the moment its first
concurrent migration landed (Round 6, `search/0011`). These hold both halves of
the promise — concurrently where the release migrates, plainly where it cannot
— and that the release gate reads them as additive.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.core.deployment import ADDITIVE_OPERATIONS
from app.core.index_operations import (
    AddIndexConcurrentlyWhenPossible,
    RemoveIndexConcurrentlyWhenPossible,
)

TRIGRAM_INDEXES = {"search_title_trgm", "search_identifiers_trgm", "search_alias_trgm"}


def _indexes() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'search_searchdocument'")
        return {name for (name,) in cursor.fetchall()}


@pytest.mark.django_db
def test_inside_a_transaction_the_indexes_are_built_plainly():
    assert connection.in_atomic_block
    call_command("migrate", "search", "0010", verbosity=0)
    try:
        assert not TRIGRAM_INDEXES & _indexes()
        assert "search_title_trigram" in _indexes()
    finally:
        call_command("migrate", "search", verbosity=0)
    assert TRIGRAM_INDEXES <= _indexes()
    assert "search_title_trigram" not in _indexes()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_outside_a_transaction_they_are_built_concurrently():
    assert not connection.in_atomic_block
    try:
        with CaptureQueriesContext(connection) as backwards:
            call_command("migrate", "search", "0010", verbosity=0)
        with CaptureQueriesContext(connection) as forwards:
            call_command("migrate", "search", verbosity=0)
    finally:
        call_command("migrate", verbosity=0)
    down = " ".join(query["sql"] for query in backwards.captured_queries)
    up = " ".join(query["sql"] for query in forwards.captured_queries)
    assert 'DROP INDEX CONCURRENTLY IF EXISTS "search_title_trgm"' in down
    assert 'CREATE INDEX CONCURRENTLY "search_title_trgm"' in up
    assert 'DROP INDEX CONCURRENTLY IF EXISTS "search_title_trigram"' in up
    assert TRIGRAM_INDEXES <= _indexes()


def test_the_release_gate_reads_them_as_additive():
    for operation in (AddIndexConcurrentlyWhenPossible, RemoveIndexConcurrentlyWhenPossible):
        assert operation.__name__ in ADDITIVE_OPERATIONS


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_generation_uniqueness_is_swapped_concurrently_and_never_absent():
    """`search/0013`: the new partial unique index first, then the old one dropped.

    Both ``CONCURRENTLY`` outside a transaction, so writes to the projection
    carry on while they build; and in that order, so there is no moment
    without a one-row-per-source guarantee (docs/adr/0118).
    """
    try:
        with CaptureQueriesContext(connection) as backwards:
            call_command("migrate", "search", "0012", verbosity=0)
        with CaptureQueriesContext(connection) as forwards:
            call_command("migrate", "search", "0013", verbosity=0)
    finally:
        call_command("migrate", verbosity=0)
    up = [query["sql"] for query in forwards.captured_queries]
    created = next(
        i for i, sql in enumerate(up) if "search_one_row_per_source_and_generation" in sql
    )
    dropped = next(i for i, sql in enumerate(up) if "search_one_document_per_source_object" in sql)
    assert up[created].startswith("CREATE UNIQUE INDEX CONCURRENTLY")
    assert up[dropped].startswith("DROP INDEX CONCURRENTLY IF EXISTS")
    assert created < dropped
    down = " ".join(query["sql"] for query in backwards.captured_queries)
    assert 'CREATE UNIQUE INDEX CONCURRENTLY "search_one_document_per_source_object"' in down
    assert "search_one_row_per_source_and_generation" in down


def test_only_a_partial_unique_constraint_is_built_concurrently():
    from django.db import models

    from app.core.index_operations import _concurrently

    plain = models.UniqueConstraint(fields=["title"], name="x")
    with pytest.raises(TypeError):
        _concurrently(plain, None, None)
