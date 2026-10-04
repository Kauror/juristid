"""`MatterSaveOnce` — a drawn form's one-time token (historical-regression round, stage II).

A new, empty table: a save inserts its form's token inside its own transaction,
so a double press or a retried request of the same `Koja arvamus` form is
refused instead of recorded twice. Nothing existing is read, copied or changed.
Reversible: unapplying drops the table and only the spent tokens with it.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0045_response_deadline_history'),
    ]

    operations = [
        migrations.CreateModel(
            name='MatterSaveOnce',
            fields=[
                ('token', models.UUIDField(primary_key=True, serialize=False)),
                ('purpose', models.CharField(max_length=40)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='matters.matter')),
            ],
            options={
                'verbose_name': 'ühekordne salvestus',
            },
        ),
    ]
