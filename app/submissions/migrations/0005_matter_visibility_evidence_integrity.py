"""Relaxing a Matter may not leave final evidence below its submission.

`submissions/migrations/0002` made the rule structural for the two records the
comparison names: a trigger on `submissions_submission` for the moment evidence
is attached or sent, and one on `documents_document` for the moment an
already-referenced document is relaxed.

Both sides are *derived* values, though, and the third input to both is the
Matter's visibility. Relaxing the Matter drops the evidence to whatever its own
override says while a submission carrying its own RESTRICTED override stays
where it is — so the state the other two triggers refuse to create could still
be reached, through an ordinary audited call to `set_matter_visibility`, without
either record being written. This is the third trigger, and it closes that.

Only relaxation can break it: tightening the Matter raises both sides together.

`app.matters.services.set_matter_visibility` performs the same check first, so a
person editing a Matter gets a sentence rather than a database error. This is
the backstop, and it is worth having for the same reason the other two are —
what it protects is the system's answer to "what exactly did Koda send", and one
`UPDATE` should not be able to make that answer readable by the wrong people.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION matters_check_relied_upon_evidence() RETURNS trigger AS $body$
DECLARE
    offending integer;
BEGIN
    IF NEW.visibility IS NOT DISTINCT FROM OLD.visibility THEN
        RETURN NEW;
    END IF;
    -- Tightening raises the submission and its evidence together.
    IF NEW.visibility = 'RESTRICTED' THEN
        RETURN NEW;
    END IF;

    SELECT count(*) INTO offending
      FROM submissions_submission s
      JOIN documents_documentversion v ON v.id = s.final_version_id
      JOIN documents_document d ON d.id = v.document_id
     WHERE s.matter_id = NEW.id
       AND s.visibility_override = 'RESTRICTED'
       AND d.visibility_override IS DISTINCT FROM 'RESTRICTED';

    IF offending > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'Relaxing this Matter would leave final evidence less restricted than its submission.',
            HINT = 'Restrict the evidence documents first, or relax the submissions.';
    END IF;

    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS matters_check_relied_upon_evidence();"

CREATE_TRIGGER = (
    "CREATE TRIGGER matters_relied_upon_evidence_stays_restricted "
    "BEFORE UPDATE OF visibility ON matters_matter "
    "FOR EACH ROW EXECUTE FUNCTION matters_check_relied_upon_evidence();"
)

DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS matters_relied_upon_evidence_stays_restricted ON matters_matter;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0004_submission_sent_at_precision"),
        ("matters", "0011_matter_successor"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
