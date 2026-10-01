"""What the dormant `Submission.working_document` column holds, read as a link.

`Submission.working_document` has existed since the foundational schema — one
nullable `SET_NULL` pointer at a single `Document`, meant for an opinion's
working file — and nothing in the product has ever written it: no form, no
service, no importer and no admin. `DocumentLink.submission` (`0013`) is the
canonical relation now (docs/adr/0129 §3), and the column is left exactly where it
is: dropping it is a schema-cleanup decision for its own change, not something
done on the way past.

**So this is a carry-over, written for the case nobody expects.** If a row does
hold a pointer — set by hand, in a shell — the association is preserved where the
product reads working documents, so it is not silently lost behind a column the
page never shows. On a database where the column is empty, which is every one
this repository has ever made, it reads one empty result and writes nothing.

Every row it would write keeps the relation's own rules, because a migration is
not a way around them:

* **same Matter only** — the dormant column carries no cross-Matter guard, so a
  pointer across files is left on the column and not turned into a link that
  `check_evidence_integrity` would report as damage;
* **never an opinion's own letter** — a document that is the `final_version` of
  any Submission, or carries the opinion role, is evidence and is not linked as
  a working file (docs/adr/0129 §5);
* **nobody is named as having linked it** — `created_by` stays empty, because
  nobody recorded who set the pointer and inventing an author would be a fact
  this migration made up.

Deterministic (submissions in primary-key order) and idempotent (`get_or_create`
on the pair the uniqueness constraint already guards), so a re-run writes
nothing. The column itself is never read back or cleared. The reverse is a no-op:
a link written here is indistinguishable from one a person made afterwards, and
`0013`'s own reverse removes the column the links live in.
"""

from django.db import migrations

#: `DocumentRole.KODA_SUBMISSION_FINAL`, spelled out: a migration does not import
#: the application's enums, which may move on after it has run.
OPINION_ROLE = "KODA_SUBMISSION_FINAL"


def carry_dormant_working_documents(apps, schema_editor):
    Submission = apps.get_model("submissions", "Submission")
    DocumentLink = apps.get_model("documents", "DocumentLink")

    pointing = list(
        Submission.objects.filter(working_document__isnull=False)
        .select_related("working_document")
        .order_by("pk")
    )
    if not pointing:
        return
    evidence_documents = set(
        Submission.objects.filter(final_version__isnull=False).values_list(
            "final_version__document_id", flat=True
        )
    )
    for submission in pointing:
        document = submission.working_document
        if document.matter_id != submission.matter_id:
            continue
        if document.pk in evidence_documents or document.role == OPINION_ROLE:
            continue
        DocumentLink.objects.get_or_create(document=document, submission=submission)


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0013_document_link_submission'),
    ]

    operations = [
        migrations.RunPython(carry_dormant_working_documents, migrations.RunPython.noop),
    ]
