"""Sissekanne: the authored professional chronology.

Entry bodies are sanitised HTML written only through app.matters.services.
EntryRevision keeps the superseded text of an edited entry so an edit can never
silently rewrite what the record said at the time.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0002_visibility_vocabulary'),
        ('organisations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Entry',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visibility_override', models.CharField(blank=True, choices=[('NORMAL', 'Tavaline'), ('RESTRICTED', 'Piiratud')], db_index=True, default='', help_text='Tühi tähendab, et nähtavus päritakse teemalt.', max_length=16, verbose_name='nähtavuse kitsendus')),
                ('kind', models.CharField(choices=[('NOTE', 'Märkus'), ('MEETING', 'Kohtumine'), ('CALL', 'Telefonikõne'), ('HEARING', 'Istung või kuulamine'), ('WORKING_GROUP', 'Töörühm'), ('JOINT_COORDINATION', 'Ühistegevuse koordineerimine'), ('PUBLIC_STATEMENT', 'Avalik esinemine või kommentaar'), ('OTHER', 'Muu')], db_index=True, default='NOTE', max_length=32, verbose_name='liik')),
                ('occurred_at', models.DateTimeField(db_index=True, verbose_name='toimus')),
                ('body', models.TextField(help_text='Sanitiseeritud HTML; kirjutamine käib ainult teenusekihi kaudu.', verbose_name='sisu')),
                ('edited_at', models.DateTimeField(blank=True, null=True, verbose_name='muudetud')),
                ('edit_count', models.PositiveIntegerField(default=0, verbose_name='muudatuste arv')),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='authored_entries', to=settings.AUTH_USER_MODEL, verbose_name='autor')),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='matters.matter', verbose_name='teema')),
                ('organisation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='entries', to='organisations.organisation', verbose_name='asutus')),
            ],
            options={
                'verbose_name': 'sissekanne',
                'verbose_name_plural': 'sissekanded',
                'ordering': ['-occurred_at', '-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='EntryRevision',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('revision_number', models.PositiveIntegerField(verbose_name='versioon')),
                ('body', models.TextField(verbose_name='varasem sisu')),
                ('edited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='entry_revisions', to=settings.AUTH_USER_MODEL)),
                ('entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='revisions', to='matters.entry', verbose_name='sissekanne')),
            ],
            options={
                'verbose_name': 'sissekande varasem versioon',
                'verbose_name_plural': 'sissekande varasemad versioonid',
                'ordering': ['entry', 'revision_number'],
            },
        ),
        migrations.AddIndex(
            model_name='entry',
            index=models.Index(fields=['matter', '-occurred_at'], name='matters_entry_timeline'),
        ),
        migrations.AddConstraint(
            model_name='entry',
            constraint=models.CheckConstraint(condition=models.Q(('body', ''), _negated=True), name='matters_entry_body_required'),
        ),
        migrations.AddConstraint(
            model_name='entry',
            constraint=models.CheckConstraint(condition=models.Q(('visibility_override__in', ['', 'NORMAL', 'RESTRICTED'])), name='matters_entry_visibility_vocabulary'),
        ),
        migrations.AddConstraint(
            model_name='entryrevision',
            constraint=models.UniqueConstraint(fields=('entry', 'revision_number'), name='matters_unique_entry_revision'),
        ),
    ]
