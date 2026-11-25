"""Personal preferences: one row per person, today the landing page (docs/adr/0221).

Additive: a new table nothing existing reads, so the release still serving is
unaffected and reversing the migration drops only presentation preferences.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_disclosure_preferences'),
    ]

    operations = [
        migrations.CreateModel(
            name='PersonalPreference',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('landing_page', models.CharField(blank=True, default='', max_length=40, verbose_name='avaleht')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='personal_preference', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'isiklik eelistus',
                'verbose_name_plural': 'isiklikud eelistused',
            },
        ),
    ]
