"""Jaergmiseks: the one prominent current instruction per Matter.

The partial unique index is the invariant the whole Minu too page depends on:
at most one OPEN action per Matter. Replacing an action supersedes the previous
one rather than deleting it.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0003_entry'),
        ('workflow', '0002_era_aware_legacy_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NextAction',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visibility_override', models.CharField(blank=True, choices=[('NORMAL', 'Tavaline'), ('RESTRICTED', 'Piiratud')], db_index=True, default='', help_text='Tühi tähendab, et nähtavus päritakse teemalt.', max_length=16, verbose_name='nähtavuse kitsendus')),
                ('text', models.TextField(verbose_name='järgmiseks')),
                ('kind', models.CharField(choices=[('DO', 'Teen'), ('WAIT', 'Ootan'), ('MONITOR', 'Jälgin')], db_index=True, default='DO', max_length=16, verbose_name='tegevuse liik')),
                ('date_semantics', models.CharField(choices=[('DEADLINE', 'Tähtaeg'), ('REVIEW_ON', 'Vaatan üle'), ('EXPECTED_AROUND', 'Oodatav umbes')], default='DEADLINE', max_length=32, verbose_name='kuupäeva tähendus')),
                ('target_date', models.DateField(blank=True, db_index=True, null=True, verbose_name='kuupäev')),
                ('date_precision', models.CharField(choices=[('EXACT', 'Täpne'), ('MONTH', 'Kuu täpsusega'), ('QUARTER', 'Kvartali täpsusega'), ('HALF_YEAR', 'Poolaasta täpsusega'), ('YEAR', 'Aasta täpsusega'), ('INFERRED', 'Tuletatud tekstist')], default='EXACT', max_length=16, verbose_name='kuupäeva täpsus')),
                ('source_text', models.TextField(blank=True, help_text='Kui kuupäev on tuletatud vabast tekstist, säilib siin algne sõnastus.', verbose_name='algne tekst')),
                ('status', models.CharField(choices=[('OPEN', 'Kehtiv'), ('COMPLETED', 'Tehtud'), ('CANCELLED', 'Tühistatud'), ('SUPERSEDED', 'Asendatud')], db_index=True, default='OPEN', max_length=16, verbose_name='olek')),
                ('ended_at', models.DateTimeField(blank=True, null=True, verbose_name='lõpetatud')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='created_next_actions', to=settings.AUTH_USER_MODEL)),
                ('ended_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ended_next_actions', to=settings.AUTH_USER_MODEL)),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='next_actions', to='matters.matter', verbose_name='teema')),
                ('replaced_by', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='replaces', to='workflow.nextaction', verbose_name='asendatud tegevusega')),
                ('responsible', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='next_actions', to=settings.AUTH_USER_MODEL, verbose_name='vastutaja')),
            ],
            options={
                'verbose_name': 'järgmine tegevus',
                'verbose_name_plural': 'järgmised tegevused',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['responsible', 'status', 'target_date'], name='workflow_action_queue'), models.Index(fields=['status', 'kind', 'target_date'], name='workflow_action_kind_date')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'OPEN')), fields=('matter',), name='workflow_one_open_action_per_matter'), models.CheckConstraint(condition=models.Q(('text', ''), _negated=True), name='workflow_next_action_text_required'), models.CheckConstraint(condition=models.Q(('visibility_override__in', ['', 'NORMAL', 'RESTRICTED'])), name='workflow_next_action_visibility_vocabulary')],
            },
        ),
    ]
