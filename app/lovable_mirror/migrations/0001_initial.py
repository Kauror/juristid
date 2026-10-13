"""The Lovable mirror's own tables (docs/adr/0157). Additive; touches no other app.

The three tables refuse UPDATE in the database, through the shared
`core_reject_mutation()` function: a stored snapshot is what the export said
and is never edited. DELETE stays possible — removing a whole snapshot is the
retention path for real data (docs/lovable-mirror.md).
"""

import app.core.ids
import django.db.models.deletion
from django.db import migrations, models


TABLES = (
    "lovable_mirror_sourcesnapshot",
    "lovable_mirror_sourcerecord",
    "lovable_mirror_sourcefieldvalue",
)


def create_trigger(table: str) -> str:
    return (
        f"CREATE TRIGGER {table}_immutable "
        f"BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION core_reject_mutation();"
    )


def drop_trigger(table: str) -> str:
    return f"DROP TRIGGER IF EXISTS {table}_immutable ON {table};"


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name='SourceSnapshot',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='LOVABLE_ALPHA', max_length=32)),
                ('manifest_sha256', models.CharField(help_text='Manifest kinnitab iga tabelifaili räsi, seega kinnitab see kogu paki.', max_length=64, unique=True, verbose_name='manifesti SHA-256')),
                ('format_version', models.PositiveSmallIntegerField(verbose_name='vormingu versioon')),
                ('data_class', models.CharField(choices=[('REAL', 'Päris andmed'), ('SYNTHETIC', 'Väljamõeldud andmed')], max_length=16)),
                ('source_schema_version', models.CharField(max_length=200, verbose_name='allika skeem')),
                ('catalogue_version', models.CharField(max_length=20, verbose_name='kataloogi versioon')),
                ('reader', models.CharField(help_text='Mis tõi andmed: operaatori eksport või (tulevikus) otselugeja.', max_length=32, verbose_name='lugeja')),
                ('source_exported_at', models.DateTimeField(verbose_name='allikas eksporditud')),
                ('imported_at', models.DateTimeField(auto_now_add=True, verbose_name='imporditud')),
                ('exported_by', models.CharField(blank=True, max_length=200)),
                ('export_method', models.CharField(blank=True, max_length=400)),
                ('bundle_label', models.CharField(blank=True, help_text='Paki kausta nimi. Täisrada ei salvestata.', max_length=200)),
                ('row_counts', models.JSONField(default=dict, verbose_name='ridu tabeliti')),
                ('uncatalogued_fields', models.JSONField(default=dict, verbose_name='kataloogimata väljad tabeliti')),
                ('notes', models.TextField(blank=True)),
            ],
            options={
                'verbose_name': "Lovable'i hetktõmmis",
                'verbose_name_plural': "Lovable'i hetktõmmised",
                'ordering': ['-source_exported_at', '-imported_at', '-id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('manifest_sha256__regex', '^[0-9a-f]{64}$')), name='lovable_mirror_snapshot_sha256_hex')],
            },
        ),
        migrations.CreateModel(
            name='SourceRecord',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('table', models.CharField(max_length=64)),
                ('source_id', models.CharField(max_length=200, verbose_name='allika ID')),
                ('stable_key', models.CharField(blank=True, help_text='Kataloogi järgi püsiv võti (topics: nr). Tühi, kui allikas seda ei andnud.', max_length=200, verbose_name='püsiv võti')),
                ('row_sha256', models.CharField(max_length=64)),
                ('snapshot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='lovable_mirror.sourcesnapshot')),
            ],
            options={
                'ordering': ['table', 'stable_key', 'source_id'],
            },
        ),
        migrations.CreateModel(
            name='SourceFieldValue',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('field', models.CharField(max_length=128)),
                ('value', models.JSONField(blank=True, null=True)),
                ('mapping_class', models.CharField(choices=[('IDENTITY', 'Identiteet'), ('COMPARABLE', 'Võrreldav'), ('REVIEW_ONLY', 'Ainult ülevaatuseks'), ('NEVER_AUTO', 'Mitte kunagi automaatselt'), ('PROVENANCE', 'Päritolu'), ('LOVABLE_DERIVED', "Lovable'i arvutus"), ('UNCATALOGUED', 'Kataloogimata')], max_length=32)),
                ('record', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='values', to='lovable_mirror.sourcerecord')),
            ],
            options={
                'ordering': ['field'],
                'constraints': [models.UniqueConstraint(fields=('record', 'field'), name='lovable_mirror_value_unique_per_record')],
            },
        ),
        migrations.AddIndex(
            model_name='sourcerecord',
            index=models.Index(fields=['snapshot', 'table', 'stable_key'], name='lovmir_record_key'),
        ),
        migrations.AddConstraint(
            model_name='sourcerecord',
            constraint=models.UniqueConstraint(fields=('snapshot', 'table', 'source_id'), name='lovable_mirror_record_unique_per_snapshot'),
        ),
        *[
            migrations.RunSQL(sql=create_trigger(table), reverse_sql=drop_trigger(table))
            for table in TABLES
        ],
    ]
