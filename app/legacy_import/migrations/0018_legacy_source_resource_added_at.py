"""When a historical file was added to OneNote — source provenance (docs/adr/0092 §6).

Two additive columns on `LegacySourceResource`, both empty for every existing
row: the time and the source field it was read from. No data migration — a row
gets its time when its page is catalogued again from an archive that carries
one, and a row whose source has none keeps none.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("legacy_import", "0017_evidence_pilot_record"),
    ]

    operations = [
        migrations.AddField(
            model_name="legacysourceresource",
            name="source_added_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="lisatud allikasse"),
        ),
        migrations.AddField(
            model_name="legacysourceresource",
            name="source_added_basis",
            field=models.CharField(
                blank=True, default="", max_length=64, verbose_name="lisamise aja alus"
            ),
        ),
    ]
