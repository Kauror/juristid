"""`DocumentLink` learns an eighth kind of record: a sent `Koja arvamus`.

The relation behind an opinion's **working documents** — the editable DOCX the
letter was drafted in, which the lawyer reuses, searches and versions later. It
is never the opinion's evidence: what was sent stays `Submission.final_version`,
the exact pinned version the `SENT` check stands on (docs/adr/0129 §1, §2, §5).

Additive, and in the shape the table was built for. `TARGET_FIELDS` is one tuple
and the `CHECK`, the per-kind uniqueness and the visibility clause are all
generated from it, so a new kind is one nullable foreign key plus the recreation
of the one constraint that enumerates them (app/documents/links.py) — the same
three operations `0010` and `0011` performed for the sixth and seventh kinds.

**Nothing is read and nothing is rewritten here.** The column is nullable with no
default, so PostgreSQL adds it as a catalogue change rather than a table rewrite;
every existing link row reads back `NULL` in it, which is the truthful answer.
The `CHECK` is validated against a table whose new column is `NULL` everywhere,
so every existing row satisfies it for exactly the reason it did before. What the
dormant `Submission.working_document` column may hold is carried over by `0014`,
a migration of its own so this one stays a pure schema change.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0012_removable_documents'),
        ('submissions', '0008_opinion_summary'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='documentlink',
            name='documents_link_has_exactly_one_record',
        ),
        migrations.AddField(
            model_name='documentlink',
            name='submission',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='document_links', to='submissions.submission', verbose_name='koja arvamus'),
        ),
        migrations.AddConstraint(
            model_name='documentlink',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('entry__isnull', False), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True), ('submission__isnull', True)), models.Q(('engagement__isnull', False), ('entry__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True), ('submission__isnull', True)), models.Q(('important_date__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True), ('submission__isnull', True)), models.Q(('effective_date__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True), ('submission__isnull', True)), models.Q(('work_victory__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True), ('submission__isnull', True)), models.Q(('external_position__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('procedural_development__isnull', True), ('submission__isnull', True)), models.Q(('procedural_development__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('submission__isnull', True)), models.Q(('submission__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True)), _connector='OR'), name='documents_link_has_exactly_one_record'),
        ),
        migrations.AddConstraint(
            model_name='documentlink',
            constraint=models.UniqueConstraint(condition=models.Q(('submission__isnull', False)), fields=('document', 'submission'), name='documents_one_link_per_submission'),
        ),
    ]
