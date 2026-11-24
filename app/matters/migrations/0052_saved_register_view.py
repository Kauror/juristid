"""«Minu vaated»: one row per person and named Teemad view (docs/adr/0222).

Additive: a new table nothing existing reads, so the release still serving is
unaffected, and reversing the migration drops only people's saved view names and
their query strings — never business data. No backfill.
"""

import app.core.ids
import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0051_procedural_link_removable'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedRegisterView',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=60, verbose_name='nimi')),
                ('query', models.CharField(blank=True, max_length=2000, verbose_name='filtrid')),
                ('position', models.PositiveSmallIntegerField(default=0, verbose_name='järjekord')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_register_views', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'salvestatud vaade',
                'verbose_name_plural': 'salvestatud vaated',
                'ordering': ['position', 'created_at'],
                'constraints': [models.UniqueConstraint(models.F('user'), django.db.models.functions.text.Lower('name'), name='matters_savedview_user_name')],
            },
        ),
    ]
