"""`Selgitus` becomes `Seisukoht`, and that is the whole of this migration.

One `AlterField` over a ``verbose_name``. The column is the same
``matters_matterexternalposition.summary`` it always was — same type, same
nullability, same blank-ness, same name in the database — and what changed is
what the application calls it: a caption under a link became the written
position itself, and one of the three answers to the source rule
(docs/adr/0084 §2, §3, amended 2026-09-16).

**No SQL, no data and nothing to backfill.** ``verbose_name`` is Python
metadata that Django keeps in the migration state so that `makemigrations
--check` stays quiet; PostgreSQL is never told about it, so `sqlmigrate` over
this migration emits the transaction and nothing between its ends. Rows written
before today keep their text exactly as somebody typed it, which is the
truthful answer: a short explanation of what an organisation said *is* a
written position, read under a new label, and rewriting any of it would be this
application editing somebody else's words.

The source rule that now accepts that text alone is **not** here and cannot be.
Two of the three sources are columns on this row and the third is a row in
`documents_documentlink`, and a `CHECK` sees one row and cannot count another
table — so the rule lives in `app.matters.services._external_position_source`,
at the two doors a person's save comes through, exactly where the two-source
version of it already lived (docs/adr/0084 §3).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0023_matter_external_position"),
    ]

    operations = [
        migrations.AlterField(
            model_name="matterexternalposition",
            name="summary",
            field=models.TextField(blank=True, verbose_name="seisukoht"),
        ),
    ]
