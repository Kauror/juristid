"""Make imported provenance immutable in the database, not only in Python.

``MatterSourceReference.save()` has always refused to change a raw column. That
guard is real but partial: ``QuerySet.update()``, ``bulk_update()``, a data
migration, a management shell and ``psql`` all write rows without ever calling
``save()``. Provenance whose immutability depends on everyone remembering to go
through the model is a convention, and this table's whole value is that it is
*evidence* — a record of what the source said that nobody can quietly improve.

So the guarantee moves into the database. Two triggers:

* the raw columns cannot be changed by any UPDATE, from any client;
* the ledger is append-only, like the other run-history tables.

Deliberately **not** forbidden: updating the interpretive and operational
columns. ``match_method``, ``conflict_state``, ``reviewed_by``, ``review_note``
and ``onenote_content_status`` exist precisely so that a better reading, or the
later arrival of a OneNote page, can be recorded beside the raw values without
touching them. An immutability rule that also froze those would push people to
delete and re-create the row, which loses the evidence it was meant to protect.

``source_row_raw`` is JSONB, so it is compared with ``IS DISTINCT FROM`` rather
than ``<>``: JSONB has no equality-safe null semantics under ``<>`` and a NULL
on either side would make the comparison NULL, which reads as "not different"
and would let the column through.
"""

from django.db import migrations

RAW_COLUMNS = (
    "source_system",
    "source_file_name",
    "source_snapshot_sha256",
    "source_sheet",
    "source_row_number",
    "source_title",
    "source_date_raw",
    "onenote_page_id",
    "onenote_url",
)

_COMPARISONS = "\n     OR ".join(
    f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in (*RAW_COLUMNS, "source_row_raw")
)

CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION legacy_import_reject_raw_change() RETURNS trigger AS $body$
BEGIN
  IF {_COMPARISONS}
  THEN
    RAISE EXCEPTION
      'Imported source values are immutable (row %). A better reading is recorded '
      'beside the raw values, never on top of them.', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS legacy_import_reject_raw_change();"

CREATE_RAW_TRIGGER = """
CREATE TRIGGER legacy_import_mattersourcereference_raw_immutable
BEFORE UPDATE ON legacy_import_mattersourcereference
FOR EACH ROW EXECUTE FUNCTION legacy_import_reject_raw_change();
"""

DROP_RAW_TRIGGER = (
    "DROP TRIGGER IF EXISTS legacy_import_mattersourcereference_raw_immutable "
    "ON legacy_import_mattersourcereference;"
)

CREATE_LEDGER_TRIGGER = """
CREATE TRIGGER legacy_import_importrowledger_append_only
BEFORE UPDATE OR DELETE ON legacy_import_importrowledger
FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();
"""

DROP_LEDGER_TRIGGER = (
    "DROP TRIGGER IF EXISTS legacy_import_importrowledger_append_only "
    "ON legacy_import_importrowledger;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("legacy_import", "0002_import_ledger_and_provenance"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_RAW_TRIGGER, reverse_sql=DROP_RAW_TRIGGER),
        migrations.RunSQL(sql=CREATE_LEDGER_TRIGGER, reverse_sql=DROP_LEDGER_TRIGGER),
    ]
