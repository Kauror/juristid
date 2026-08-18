"""An assigned Entra object id is immutable in the database.

The model's ``save()`` already refuses to change one, but that guard is absent
from ``QuerySet.update()``, ``bulk_update``, a data migration or a shell
session. Identity is the one thing that must not drift, so the rule belongs
where nothing can route around it.

The initial NULL to UUID assignment stays allowed: that is what the first Entra
sign-in does to an account that already exists.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION accounts_reject_entra_identity_change() RETURNS trigger AS $body$
BEGIN
    IF OLD.entra_object_id IS NOT NULL
        AND NEW.entra_object_id IS DISTINCT FROM OLD.entra_object_id
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'entra_object_id is immutable once assigned.',
            HINT = 'Deactivate the account and create a new one instead.';
    END IF;
    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS accounts_reject_entra_identity_change();"

CREATE_TRIGGER = (
    "CREATE TRIGGER accounts_user_entra_object_id_immutable "
    "BEFORE UPDATE OF entra_object_id ON accounts_user "
    "FOR EACH ROW EXECUTE FUNCTION accounts_reject_entra_identity_change();"
)

DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS accounts_user_entra_object_id_immutable ON accounts_user;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
