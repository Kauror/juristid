"""Remembered blocks: one row per person and explicitly chosen block (docs/adr/0194).

Additive: a new table nothing existing reads, so the release still serving is
unaffected and reversing the migration drops only presentation preferences.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_credential_link_stamp_and_one_live'),
    ]

    operations = [
        migrations.CreateModel(
            name='DisclosurePreference',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(max_length=80, verbose_name='ploki tunnus')),
                ('is_open', models.BooleanField(verbose_name='avatud')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='disclosure_preferences', to=settings.AUTH_USER_MODEL, verbose_name='kasutaja')),
            ],
            options={
                'verbose_name': 'ploki eelistus',
                'verbose_name_plural': 'plokkide eelistused',
                'constraints': [models.UniqueConstraint(fields=('user', 'key'), name='accounts_disclosurepref_user_key')],
            },
        ),
    ]
