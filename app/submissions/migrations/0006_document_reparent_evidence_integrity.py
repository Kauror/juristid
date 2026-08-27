"""Final evidence may not be moved out of the Matter that relies on it.

`0002` made the same-Matter rule structural for the Submission: the trigger on
`submissions_submission` refuses a `final_version_id` whose document is filed
under another Matter. It watches the pointer, though, and this invariant has two
ends. Nothing watched the other one, so a single `UPDATE documents_document SET
matter_id = ...` — a shell, a data migration, a `QuerySet.update` — could carry
an already-relied-upon document into a different Matter, leaving

    submission.final_version.document.matter_id != submission.matter_id

without either the Submission or its pointer being written at all. The
submission still renders and its evidence still downloads; what is no longer
true is that the file is evidence *of this file*. That is the second parent
mutation DATA-002 names, and this is the trigger that refuses it.

Refusal, never repair. Which end is wrong is not something a trigger can know:
the document may belong where it is being sent and the submission may be the
mistake, or the reverse. `check_evidence_integrity`'s `foreign-final-evidence`
finding (DATA-001) is how an operator sees rows already in that state, and the
decision stays theirs.

Note what this is *not*. It is a structural backstop against a bypass, exactly
like the three triggers before it. It is not the fix for the concurrent case —
a `BEFORE UPDATE` trigger evaluates against the snapshot its own statement can
see, so two writers touching two different rows can still both pass. What stops
that is the lock protocol in `app/matters/locks.py`, which makes the binding
transaction hold this document's row so a concurrent reparent waits and is
re-evaluated after it wakes. See docs/adr/0040.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION documents_check_relied_upon_evidence_matter() RETURNS trigger AS $body$
DECLARE
    offending integer;
BEGIN
    IF NEW.matter_id IS NOT DISTINCT FROM OLD.matter_id THEN
        RETURN NEW;
    END IF;

    -- Every submission whose final evidence is a version of this document and
    -- whose own Matter is not where the document is being moved to.
    SELECT count(*) INTO offending
      FROM submissions_submission s
      JOIN documents_documentversion v ON v.id = s.final_version_id
     WHERE v.document_id = NEW.id
       AND s.matter_id IS DISTINCT FROM NEW.matter_id;

    IF offending > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'This document is the final evidence of a submission in its current Matter.',
            HINT = 'Move the submission, supersede it, or select different evidence first.';
    END IF;

    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS documents_check_relied_upon_evidence_matter();"

CREATE_TRIGGER = (
    "CREATE TRIGGER documents_relied_upon_evidence_stays_in_matter "
    "BEFORE UPDATE OF matter_id ON documents_document "
    "FOR EACH ROW EXECUTE FUNCTION documents_check_relied_upon_evidence_matter();"
)

DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS documents_relied_upon_evidence_stays_in_matter "
    "ON documents_document;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0005_matter_visibility_evidence_integrity"),
        ("documents", "0004_visibility_override_vocabulary"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
