"""Final evidence must belong to the submission's Matter, and must not be
readable by anyone who cannot read the submission.

Both rules are checked in `app.submissions.services`, which is where a caller
gets a clear error. These triggers are the backstop, and they are worth having
because of what the rules protect: a submission's final version is the system's
answer to "what exactly did Koda send". Evidence from another file makes that
answer wrong, and evidence that is less restricted than the submission makes the
restriction cosmetic — the exact text would be listed and downloadable by people
who cannot see the submission at all.

Two triggers, because the invariant has two sides:

* on `submissions_submission`, for the moment evidence is attached or sent;
* on `documents_document`, for the moment an already-referenced document is
  relaxed. Without the second, the rule could be defeated after the fact by a
  single `UPDATE` on the document.

Effective visibility is derived, never stored (docs/adr/0005), so both triggers
compute it the same way the application does: the more restrictive of the
Matter's visibility and the record's own override.
"""

from django.db import migrations

CREATE_CHECK_FUNCTION = """
CREATE OR REPLACE FUNCTION submissions_check_final_evidence() RETURNS trigger AS $body$
DECLARE
    evidence_matter uuid;
    evidence_override text;
    submission_matter_visibility text;
    evidence_effective text;
    submission_effective text;
BEGIN
    IF NEW.final_version_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT d.matter_id, d.visibility_override
      INTO evidence_matter, evidence_override
      FROM documents_documentversion v
      JOIN documents_document d ON d.id = v.document_id
     WHERE v.id = NEW.final_version_id;

    IF evidence_matter IS DISTINCT FROM NEW.matter_id THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'Final evidence must belong to the submission''s Matter.',
            HINT = 'Capture or select evidence stored under this Matter.';
    END IF;

    SELECT m.visibility INTO submission_matter_visibility
      FROM matters_matter m WHERE m.id = NEW.matter_id;

    -- Effective visibility: RESTRICTED if either the Matter or the record's
    -- own override says so.
    evidence_effective := CASE
        WHEN submission_matter_visibility = 'RESTRICTED' THEN 'RESTRICTED'
        WHEN evidence_override = 'RESTRICTED' THEN 'RESTRICTED'
        ELSE 'NORMAL' END;
    submission_effective := CASE
        WHEN submission_matter_visibility = 'RESTRICTED' THEN 'RESTRICTED'
        WHEN NEW.visibility_override = 'RESTRICTED' THEN 'RESTRICTED'
        ELSE 'NORMAL' END;

    IF submission_effective = 'RESTRICTED' AND evidence_effective = 'NORMAL' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'Final evidence may not be less restricted than its submission.',
            HINT = 'Restrict the document, or capture the evidence under the submission.';
    END IF;

    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_CHECK_FUNCTION = "DROP FUNCTION IF EXISTS submissions_check_final_evidence();"

CREATE_SUBMISSION_TRIGGER = (
    "CREATE TRIGGER submissions_final_evidence_integrity "
    "BEFORE INSERT OR UPDATE OF final_version_id, matter_id, visibility_override "
    "ON submissions_submission "
    "FOR EACH ROW EXECUTE FUNCTION submissions_check_final_evidence();"
)

DROP_SUBMISSION_TRIGGER = (
    "DROP TRIGGER IF EXISTS submissions_final_evidence_integrity ON submissions_submission;"
)

CREATE_DOCUMENT_FUNCTION = """
CREATE OR REPLACE FUNCTION documents_check_relied_upon_evidence() RETURNS trigger AS $body$
DECLARE
    offending integer;
BEGIN
    -- Only a relaxation can break the rule, so tightening needs no check.
    IF NEW.visibility_override IS NOT DISTINCT FROM OLD.visibility_override THEN
        RETURN NEW;
    END IF;
    IF NEW.visibility_override = 'RESTRICTED' THEN
        RETURN NEW;
    END IF;

    SELECT count(*) INTO offending
      FROM submissions_submission s
      JOIN documents_documentversion v ON v.id = s.final_version_id
      JOIN matters_matter m ON m.id = s.matter_id
     WHERE v.document_id = NEW.id
       AND m.visibility <> 'RESTRICTED'
       AND s.visibility_override = 'RESTRICTED';

    IF offending > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'This document is the final evidence of a more restricted submission.',
            HINT = 'Relax the submission first, or supersede it.';
    END IF;

    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_DOCUMENT_FUNCTION = "DROP FUNCTION IF EXISTS documents_check_relied_upon_evidence();"

CREATE_DOCUMENT_TRIGGER = (
    "CREATE TRIGGER documents_relied_upon_evidence_stays_restricted "
    "BEFORE UPDATE OF visibility_override ON documents_document "
    "FOR EACH ROW EXECUTE FUNCTION documents_check_relied_upon_evidence();"
)

DROP_DOCUMENT_TRIGGER = (
    "DROP TRIGGER IF EXISTS documents_relied_upon_evidence_stays_restricted "
    "ON documents_document;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0001_initial"),
        ("documents", "0004_visibility_override_vocabulary"),
        ("matters", "0004_entry_revision_append_only"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_CHECK_FUNCTION, reverse_sql=DROP_CHECK_FUNCTION),
        migrations.RunSQL(sql=CREATE_SUBMISSION_TRIGGER, reverse_sql=DROP_SUBMISSION_TRIGGER),
        migrations.RunSQL(sql=CREATE_DOCUMENT_FUNCTION, reverse_sql=DROP_DOCUMENT_FUNCTION),
        migrations.RunSQL(sql=CREATE_DOCUMENT_TRIGGER, reverse_sql=DROP_DOCUMENT_TRIGGER),
    ]
