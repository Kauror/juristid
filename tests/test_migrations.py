"""The migration graph must stay complete and applicable from zero."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def test_no_model_change_is_missing_a_migration():
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


@pytest.mark.django_db
def test_every_migration_is_applied_in_a_fresh_database():
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    assert executor.migration_plan(targets) == []


@pytest.mark.django_db
def test_the_shared_append_only_function_is_installed():
    with connection.cursor() as cursor:
        cursor.execute("SELECT proname FROM pg_proc WHERE proname = 'core_reject_mutation'")
        assert cursor.fetchone() is not None
