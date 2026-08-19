"""Make the audit tables append-only in the database, not just in Python."""

from django.db import migrations

TABLES = ("audit_changeevent", "audit_securityauditevent")


def create(table: str) -> str:
    return (
        f"CREATE TRIGGER {table}_append_only "
        f"BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();"
    )


def drop(table: str) -> str:
    return f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=create(table), reverse_sql=drop(table)) for table in TABLES
    ]
