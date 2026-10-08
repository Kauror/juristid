"""A sent `Koja arvamus` may stand on several files (docs/adr/0144 §5).

Additive. `Submission.final_version` stays the first sent file with every
guarantee it had; `SubmissionSentFile` holds the second and later ones. No
existing row changes, so every opinion recorded before this migration reads
exactly as it did — one file.

The three backstops that protect `final_version` are extended to these rows,
because what they protect is the same answer — "what exactly did Koda send":

* a trigger on the new table refuses a file from another Matter, and a file
  less restricted than its submission (the `0002` rule);
* `documents_check_relied_upon_evidence` (`0002`) refuses relaxing a document
  that is a sent file of a more restricted submission;
* `matters_check_relied_upon_evidence` (`0005`) refuses relaxing a Matter that
  would leave a sent file below its submission;
* `documents_check_relied_upon_evidence_matter` (`0006`) refuses moving a sent
  file's document to another Matter.

Each replaced function reads one derived relation instead of
`final_version_id` alone: the submission's first file, plus its further files.
Reversing restores the earlier function bodies byte for byte, read from the
migrations that installed them.
"""

import importlib

import django.db.models.deletion
from django.db import migrations, models

import app.core.ids

_0002 = importlib.import_module("app.submissions.migrations.0002_final_evidence_integrity")
_0005 = importlib.import_module("app.submissions.migrations.0005_matter_visibility_evidence_integrity")
_0006 = importlib.import_module("app.submissions.migrations.0006_document_reparent_evidence_integrity")

#: Every (submission, version) a send stands on: the first file and the rest.
_RELIED_UPON = """
    (SELECT s0.id AS submission_id, s0.final_version_id AS version_id
       FROM submissions_submission s0
      WHERE s0.final_version_id IS NOT NULL
     UNION ALL
     SELECT f.submission_id, f.version_id
       FROM submissions_submissionsentfile f)
"""

CREATE_SENT_FILE_FUNCTION = """
CREATE OR REPLACE FUNCTION submissions_check_sent_file() RETURNS trigger AS $body$
DECLARE
    evidence_matter uuid;
    evidence_override text;
    submission_matter uuid;
    submission_override text;
    submission_matter_visibility text;
    evidence_effective text;
    submission_effective text;
BEGIN
    SELECT d.matter_id, d.visibility_override
      INTO evidence_matter, evidence_override
      FROM documents_documentversion v
      JOIN documents_document d ON d.id = v.document_id
     WHERE v.id = NEW.version_id;

    SELECT s.matter_id, s.visibility_override
      INTO submission_matter, submission_override
      FROM submissions_submission s
     WHERE s.id = NEW.submission_id;

    IF evidence_matter IS DISTINCT FROM submission_matter THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'A sent file must belong to the submission''s Matter.',
            HINT = 'Capture the file under this Matter.';
    END IF;

    SELECT m.visibility INTO submission_matter_visibility
      FROM matters_matter m WHERE m.id = submission_matter;

    evidence_effective := CASE
        WHEN submission_matter_visibility = 'RESTRICTED' THEN 'RESTRICTED'
        WHEN evidence_override = 'RESTRICTED' THEN 'RESTRICTED'
        ELSE 'NORMAL' END;
    submission_effective := CASE
        WHEN submission_matter_visibility = 'RESTRICTED' THEN 'RESTRICTED'
        WHEN submission_override = 'RESTRICTED' THEN 'RESTRICTED'
        ELSE 'NORMAL' END;

    IF submission_effective = 'RESTRICTED' AND evidence_effective = 'NORMAL' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'A sent file may not be less restricted than its submission.',
            HINT = 'Restrict the document, or capture it under the submission.';
    END IF;

    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

DROP_SENT_FILE_FUNCTION = "DROP FUNCTION IF EXISTS submissions_check_sent_file();"

CREATE_SENT_FILE_TRIGGER = (
    "CREATE TRIGGER submissions_sent_file_integrity "
    "BEFORE INSERT OR UPDATE ON submissions_submissionsentfile "
    "FOR EACH ROW EXECUTE FUNCTION submissions_check_sent_file();"
)

DROP_SENT_FILE_TRIGGER = (
    "DROP TRIGGER IF EXISTS submissions_sent_file_integrity ON submissions_submissionsentfile;"
)

REPLACE_SUBMISSION_FUNCTION = """
CREATE OR REPLACE FUNCTION submissions_check_final_evidence() RETURNS trigger AS $body$
DECLARE
    evidence_matter uuid;
    evidence_override text;
    submission_matter_visibility text;
    evidence_effective text;
    submission_effective text;
    offending integer;
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

    -- The further sent files hold to the same two rules when the submission
    -- itself moves or tightens (docs/adr/0144 §5).
    SELECT count(*) INTO offending
      FROM submissions_submissionsentfile f
      JOIN documents_documentversion v ON v.id = f.version_id
      JOIN documents_document d ON d.id = v.document_id
     WHERE f.submission_id = NEW.id
       AND (
            d.matter_id IS DISTINCT FROM NEW.matter_id
         OR (submission_effective = 'RESTRICTED'
             AND submission_matter_visibility <> 'RESTRICTED'
             AND d.visibility_override IS DISTINCT FROM 'RESTRICTED')
       );

    IF offending > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = 'A sent file must stay in the submission''s Matter and be no less restricted than it.',
            HINT = 'Restrict or move the sent files together with the submission.';
    END IF;

    RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

REPLACE_DOCUMENT_VISIBILITY_FUNCTION = f"""
CREATE OR REPLACE FUNCTION documents_check_relied_upon_evidence() RETURNS trigger AS $body$
DECLARE
    offending integer;
BEGIN
    IF NEW.visibility_override IS NOT DISTINCT FROM OLD.visibility_override THEN
        RETURN NEW;
    END IF;
    IF NEW.visibility_override = 'RESTRICTED' THEN
        RETURN NEW;
    END IF;

    SELECT count(*) INTO offending
      FROM {_RELIED_UPON} ev
      JOIN submissions_submission s ON s.id = ev.submission_id
      JOIN documents_documentversion v ON v.id = ev.version_id
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

REPLACE_MATTER_VISIBILITY_FUNCTION = f"""
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
      FROM {_RELIED_UPON} ev
      JOIN submissions_submission s ON s.id = ev.submission_id
      JOIN documents_documentversion v ON v.id = ev.version_id
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

REPLACE_DOCUMENT_MATTER_FUNCTION = f"""
CREATE OR REPLACE FUNCTION documents_check_relied_upon_evidence_matter() RETURNS trigger AS $body$
DECLARE
    offending integer;
BEGIN
    IF NEW.matter_id IS NOT DISTINCT FROM OLD.matter_id THEN
        RETURN NEW;
    END IF;

    SELECT count(*) INTO offending
      FROM {_RELIED_UPON} ev
      JOIN submissions_submission s ON s.id = ev.submission_id
      JOIN documents_documentversion v ON v.id = ev.version_id
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


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0014_carry_dormant_working_documents"),
        ("submissions", "0008_opinion_summary"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubmissionSentFile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=app.core.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveSmallIntegerField(verbose_name="järjekord")),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sent_file_rows",
                        to="submissions.submission",
                        verbose_name="arvamus",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sent_in_submission_rows",
                        to="documents.documentversion",
                        verbose_name="saadetud fail",
                    ),
                ),
            ],
            options={
                "verbose_name": "arvamuse saadetud fail",
                "verbose_name_plural": "arvamuse saadetud failid",
                "ordering": ["submission", "position"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("submission", "version"),
                        name="submissions_sent_file_once_per_submission",
                    ),
                    models.UniqueConstraint(
                        fields=("submission", "position"),
                        name="submissions_sent_file_position_unique",
                    ),
                ],
            },
        ),
        migrations.RunSQL(sql=CREATE_SENT_FILE_FUNCTION, reverse_sql=DROP_SENT_FILE_FUNCTION),
        migrations.RunSQL(sql=CREATE_SENT_FILE_TRIGGER, reverse_sql=DROP_SENT_FILE_TRIGGER),
        migrations.RunSQL(
            sql=REPLACE_SUBMISSION_FUNCTION,
            reverse_sql=_0002.CREATE_CHECK_FUNCTION,
        ),
        migrations.RunSQL(
            sql=REPLACE_DOCUMENT_VISIBILITY_FUNCTION,
            reverse_sql=_0002.CREATE_DOCUMENT_FUNCTION,
        ),
        migrations.RunSQL(
            sql=REPLACE_MATTER_VISIBILITY_FUNCTION,
            reverse_sql=_0005.CREATE_FUNCTION,
        ),
        migrations.RunSQL(
            sql=REPLACE_DOCUMENT_MATTER_FUNCTION,
            reverse_sql=_0006.CREATE_FUNCTION,
        ),
    ]
