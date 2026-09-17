"""`Menetluse areng` becomes a canonical Matter fact.

One `CreateModel` and one `choices` edit, neither of which reads or writes a row.

`MatterProceduralDevelopment` is a new table, so every Matter has zero of them
and there is nothing to backfill **from**. That is the point rather than a gap:
the `Entry` this record replaces carried its account as prose, and deriving a
title, a date and a lawyer's note out of one `body` is exactly the guessing this
repository refuses everywhere else. The one round in which developments were
`Entry` rows never reached a deployed environment, so no stored row means
anything different today than it did yesterday.

`Entry.kind` loses `PROCEDURAL_DEVELOPMENT`, which is an `AlterField` over a
`choices` list — Python metadata, not a database object. PostgreSQL never saw the
value and will never see it go. **No row carries it**: it was added and removed
inside this branch, and `EntryKind` keeps every value it had before.

Reversible in full: `CreateModel` reverses by dropping a table this migration
created, and the `choices` edit reverses to a list nothing validates against.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0029_external_position_provenance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='entry',
            name='kind',
            field=models.CharField(choices=[('NOTE', 'Märkus'), ('MEETING', 'Kohtumine'), ('CALL', 'Telefonikõne'), ('HEARING', 'Istung või kuulamine'), ('WORKING_GROUP', 'Töörühm'), ('JOINT_COORDINATION', 'Ühistegevuse koordineerimine'), ('PUBLIC_STATEMENT', 'Avalik esinemine või kommentaar'), ('OTHER', 'Muu')], db_index=True, default='NOTE', max_length=32, verbose_name='liik'),
        ),
        migrations.CreateModel(
            name='MatterProceduralDevelopment',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visibility_override', models.CharField(blank=True, choices=[('NORMAL', 'Tavaline'), ('RESTRICTED', 'Piiratud')], db_index=True, default='', help_text='Tühi tähendab, et nähtavus päritakse teemalt.', max_length=16, verbose_name='nähtavuse kitsendus')),
                ('title', models.CharField(max_length=500, verbose_name='sündmus')),
                ('occurred_on', models.DateField(blank=True, db_index=True, null=True, verbose_name='kuupäev')),
                ('occurred_on_precision', models.CharField(choices=[('EXACT', 'Täpne'), ('MONTH', 'Kuu täpsusega'), ('QUARTER', 'Kvartali täpsusega'), ('HALF_YEAR', 'Poolaasta täpsusega'), ('YEAR', 'Aasta täpsusega'), ('INFERRED', 'Tuletatud tekstist')], default='EXACT', max_length=16, verbose_name='kuupäeva täpsus')),
                ('note', models.TextField(blank=True, verbose_name='juristi märkus')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='recorded_procedural_developments', to=settings.AUTH_USER_MODEL, verbose_name='lisas')),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='procedural_developments', to='matters.matter', verbose_name='teema')),
            ],
            options={
                'verbose_name': 'menetluse areng',
                'verbose_name_plural': 'menetluse arengud',
                'ordering': [models.OrderBy(models.F('occurred_on'), descending=True, nulls_last=True), '-created_at', '-id'],
                'indexes': [models.Index(fields=['matter', '-occurred_on'], name='matters_devel_matter_date')],
                'constraints': [models.CheckConstraint(condition=models.Q(('title', ''), _negated=True), name='matters_development_title_required'), models.CheckConstraint(condition=models.Q(('occurred_on_precision__in', ['EXACT', 'MONTH', 'QUARTER', 'HALF_YEAR', 'YEAR', 'INFERRED'])), name='matters_development_precision_vocabulary'), models.CheckConstraint(condition=models.Q(('occurred_on__isnull', False), ('occurred_on_precision', 'EXACT'), _connector='OR'), name='matters_development_undated_is_exact'), models.CheckConstraint(condition=models.Q(('visibility_override__in', ['', 'NORMAL', 'RESTRICTED'])), name='matters_development_visibility_vocabulary')],
            },
        ),
    ]
