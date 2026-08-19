"""EntryRevision is historical evidence, so the database treats it as such.

An entry may be corrected, and the wording it had before the correction is what
proves the record was not silently rewritten. A revision that can itself be
edited or deleted proves nothing, so this aligns the guarantee with the
description, using the same append-only function as the audit tables.
"""

from django.db import migrations

TABLE = "matters_entryrevision"

CREATE = (
    f"CREATE TRIGGER {TABLE}_append_only "
    f"BEFORE UPDATE OR DELETE ON {TABLE} "
    f"FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();"
)

DROP = f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE};"


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0003_entry"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE, reverse_sql=DROP),
    ]
