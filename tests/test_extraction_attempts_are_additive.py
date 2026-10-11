"""The two `extraction_attempts` columns are additive for a release still serving (ENG-014).

The production procedure migrates while the old release still serves, then
swaps the containers. A NOT NULL column with only Django's `default` would make
every upload on the old release fail in that window — and `migration_plan`
refuses such a plan. Both columns carry a database default (`db_default=0`).
"""

from __future__ import annotations

import pytest
from django.db.migrations.loader import MigrationLoader

from app.core.deployment import consequential_operations


@pytest.mark.parametrize(
    "app_label,name",
    [
        ("documents", "0015_extraction_attempt_budget"),
        ("matters", "0048_intake_extraction_attempt_budget"),
    ],
)
def test_the_extraction_attempt_columns_are_additive(app_label, name):
    loader = MigrationLoader(None, ignore_no_migrations=True)
    migration = loader.disk_migrations[(app_label, name)]
    before = loader.project_state((app_label, migration.dependencies[0][1]))

    assert consequential_operations(migration, state=before) == {}
