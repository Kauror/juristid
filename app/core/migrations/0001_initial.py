"""Shared database guards.

``app.core`` owns no tables. It installs the one PL/pgSQL function that
append-only tables attach to, so the audit and evidence guarantees are enforced
by the database rather than by convention.
"""

from django.db import migrations

CREATE_REJECT_MUTATION = """
CREATE OR REPLACE FUNCTION core_reject_mutation() RETURNS trigger AS $body$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'restrict_violation',
        MESSAGE = 'Append-only table ' || TG_TABLE_NAME || ' does not permit ' || TG_OP || '.',
        HINT = 'Record a new row instead of changing or removing an existing one.';
END;
$body$ LANGUAGE plpgsql;
"""

DROP_REJECT_MUTATION = "DROP FUNCTION IF EXISTS core_reject_mutation();"


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunSQL(sql=CREATE_REJECT_MUTATION, reverse_sql=DROP_REJECT_MUTATION),
    ]
