"""The Õigusakt vocabulary table.

Schema only. The reviewed rows arrive in ``taxonomy/0006``, after the Matter
relation exists, because that migration's reverse asks whether a Matter points
at a row before it removes one.

``LegalInstrumentType`` is the third governed vocabulary beside ``PolicyArea``
and ``Tag``, and the one the taxonomy module's opening rule was holding a place
for: *legal instrument* was named there as something neither of the other two
may encode. It has a home now (docs/adr/0070).
"""

import app.core.ids
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taxonomy', '0004_retire_catchall_and_deadline_areas'),
    ]

    operations = [
        migrations.CreateModel(
            name='LegalInstrumentType',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.SlugField(max_length=64, unique=True, verbose_name='võti')),
                ('label_et', models.CharField(max_length=200, verbose_name='nimi')),
                ('description', models.TextField(blank=True, verbose_name='kirjeldus')),
                ('is_active', models.BooleanField(default=True, verbose_name='aktiivne')),
                ('sort_order', models.PositiveSmallIntegerField(default=100, verbose_name='järjekord')),
            ],
            options={
                'verbose_name': 'õigusakti liik',
                'verbose_name_plural': 'õigusakti liigid',
                'ordering': ['sort_order', 'label_et'],
            },
        ),
    ]
