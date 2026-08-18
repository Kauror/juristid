"""A historical status label is unique per era, not globally.

The register's vocabulary changed materially between 2011 and 2026, so the same
`Hetkeseis` text can mean different things in different years. An empty
`source_era` is the generic fallback; an exact era match takes precedence
(see `app.workflow.models.resolve_legacy_status`).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflow', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='legacystatusmapping',
            options={'ordering': ['raw_label', 'source_era'], 'verbose_name': 'ajaloolise seisundi vaste', 'verbose_name_plural': 'ajalooliste seisundite vasted'},
        ),
        migrations.AlterField(
            model_name='legacystatusmapping',
            name='raw_label',
            field=models.CharField(help_text='Täpselt nii, nagu see töövihikus esineb.', max_length=200, verbose_name='algne väärtus'),
        ),
        migrations.AlterField(
            model_name='legacystatusmapping',
            name='source_era',
            field=models.CharField(blank=True, db_index=True, default='', help_text='Näiteks 2023-2024 või 2025, kui tähendus on aastati erinev. Tühi väärtus on üldine vaste, mida kasutatakse siis, kui täpsemat ei leidu.', max_length=32, verbose_name='allika periood'),
        ),
        migrations.AddConstraint(
            model_name='legacystatusmapping',
            constraint=models.UniqueConstraint(fields=('raw_label', 'source_era'), name='workflow_legacy_status_unique_per_era'),
        ),
    ]
