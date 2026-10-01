"""An `Ülevaade / uudis` may carry the published page's own `Pealkiri`.

One file is often written up more than once — the VTK, then the bill — and a
closed `Teema käik` row shows only its headline and its day, so the two read as
two identical `Ülevaade / uudis` lines until somebody follows the links
(JUR-CASE-07, docs/adr/0127 §1, narrowing the «no title» of docs/adr/0081 §2
and docs/adr/0085 §1).

**One additive column, optional, and nothing is backfilled.** Empty is the
ordinary value and is what every existing row keeps: a stored write-up has no
name somebody typed, so none is derived — not from the address, the page or the
Matter. The row reads exactly as it did.

A database default as well as a Python one, as `0040_timeline_added_steps` did
for its two columns: the release still serving while `migrate` runs inserts
plans through `plan_website_overview`, which does not name the column.

**Reversible without loss of structure.** Removing the column drops the names
typed since; nothing else reads it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0040_timeline_added_steps"),
    ]

    operations = [
        migrations.AddField(
            model_name="matterwebsiteoverview",
            name="title",
            field=models.CharField(
                blank=True, db_default="", default="", max_length=300, verbose_name="pealkiri"
            ),
        ),
    ]
