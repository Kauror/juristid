"""Evidence binaries are immutable.

The byte-identity columns of a DocumentVersion may never change: a correction
creates a new version. Operational state columns (malware scan, text
extraction) stay editable, because they describe what we have learned about the
binary rather than the binary itself (master specification 3.6, 11.2).
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION documents_reject_evidence_change() RETURNS trigger AS $body$
BEGIN
    IF NEW.document_id IS DISTINCT FROM OLD.document_id
        OR NEW.version_number IS DISTINCT FROM OLD.version_number
        OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
        OR NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
        OR NEW.original_filename IS DISTINCT FROM OLD.original_filename
        OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
        OR NEW.acquired_at IS DISTINCT FROM OLD.acquired_at
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'Evidence binaries are immutable; add a new DocumentVersion instead.';
    END IF;
    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS documents_reject_evidence_change();"

CREATE_TRIGGER = (
    "CREATE TRIGGER documents_documentversion_immutable "
    "BEFORE UPDATE ON documents_documentversion "
    "FOR EACH ROW EXECUTE FUNCTION documents_reject_evidence_change();"
)

DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS documents_documentversion_immutable "
    "ON documents_documentversion;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
