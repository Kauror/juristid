"""`DocumentLink` learns a seventh kind of record: `Menetluse areng`.

Additive, and deliberately in the shape the table was built for. `TARGET_FIELDS`
is one tuple and the `CHECK`, the per-kind uniqueness and the visibility clause
are all generated from it, so a new kind is one nullable foreign key plus the
recreation of the one constraint that enumerates them (app/documents/links.py).

**Nothing is read and nothing is rewritten.** The column is nullable with no
default, so PostgreSQL adds it as a catalogue change rather than a table rewrite;
every existing link row reads back `NULL` in it, which is the truthful answer —
none of them is about a procedural development, because the record did not exist.
There is no `RunPython`, no `RunSQL` and no backfill, and there is nothing to
backfill from.

The `CHECK` is dropped and recreated because it names every target column by hand
and a seventh one has to appear in it. That is the documented cost of typed
columns over a generic target, and it is paid here for the second time: the
constraint is validated against a table whose new column is `NULL` everywhere, so
every existing row satisfies it for exactly the reason it did before
(docs/adr/0084 §9, docs/adr/0090 §5).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0010_document_link_external_position'),
        ('intelligence', '0001_initial'),
        ('matters', '0028_procedural_development'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='documentlink',
            name='documents_link_has_exactly_one_record',
        ),
        migrations.AddField(
            model_name='documentlink',
            name='procedural_development',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='document_links', to='matters.matterproceduraldevelopment', verbose_name='menetluse areng'),
        ),
        migrations.AddConstraint(
            model_name='documentlink',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('entry__isnull', False), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True)), models.Q(('engagement__isnull', False), ('entry__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True)), models.Q(('important_date__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True)), models.Q(('effective_date__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True)), models.Q(('work_victory__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('external_position__isnull', True), ('procedural_development__isnull', True)), models.Q(('external_position__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('procedural_development__isnull', True)), models.Q(('procedural_development__isnull', False), ('entry__isnull', True), ('engagement__isnull', True), ('important_date__isnull', True), ('effective_date__isnull', True), ('work_victory__isnull', True), ('external_position__isnull', True)), _connector='OR'), name='documents_link_has_exactly_one_record'),
        ),
        migrations.AddConstraint(
            model_name='documentlink',
            constraint=models.UniqueConstraint(condition=models.Q(('procedural_development__isnull', False)), fields=('document', 'procedural_development'), name='documents_one_link_per_procedural_development'),
        ),
    ]
