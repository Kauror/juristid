"""Submission: outbound written advocacy.

The check constraint is the point: a SENT submission must carry both a sent
timestamp and the exact immutable evidence version that was sent. There is
deliberately no Matter-level opinion_sent_date.
"""

import app.core.ids
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('documents', '0004_visibility_override_vocabulary'),
        ('matters', '0003_entry'),
        ('organisations', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Submission',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visibility_override', models.CharField(blank=True, choices=[('NORMAL', 'Tavaline'), ('RESTRICTED', 'Piiratud')], db_index=True, default='', help_text='Tühi tähendab, et nähtavus päritakse teemalt.', max_length=16, verbose_name='nähtavuse kitsendus')),
                ('kind', models.CharField(choices=[('FORMAL_OPINION', 'Ametlik arvamus'), ('SUPPLEMENTARY_OPINION', 'Täiendav arvamus'), ('PARLIAMENTARY_SUBMISSION', 'Pöördumine Riigikogule'), ('JOINT_LETTER', 'Ühispöördumine'), ('INFORMAL_WRITTEN_RESPONSE', 'Mitteametlik kirjalik vastus'), ('OTHER', 'Muu')], db_index=True, default='FORMAL_OPINION', max_length=40, verbose_name='liik')),
                ('title', models.CharField(max_length=400, verbose_name='pealkiri')),
                ('status', models.CharField(choices=[('DRAFT', 'Koostamisel'), ('SENT', 'Saadetud'), ('WITHDRAWN', 'Tagasi võetud'), ('SUPERSEDED', 'Asendatud')], db_index=True, default='DRAFT', max_length=16, verbose_name='olek')),
                ('sent_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='saadetud')),
                ('channel', models.CharField(blank=True, help_text='Näiteks EIS, e-post või dokumendiregistri viide.', max_length=200, verbose_name='kanal')),
                ('reference', models.CharField(blank=True, max_length=200, verbose_name='viide')),
                ('notes', models.TextField(blank=True, verbose_name='märkused')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='created_submissions', to=settings.AUTH_USER_MODEL)),
                ('final_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='finalised_submissions', to='documents.documentversion', verbose_name='lõplik tõend')),
                ('joint_submitters', models.ManyToManyField(blank=True, help_text='Teised organisatsioonid, kelle nimel pöördumine ühiselt esitati.', related_name='joint_submissions', to='organisations.organisation', verbose_name='kaasesitajad')),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='submissions', to='matters.matter', verbose_name='teema')),
                ('recipients', models.ManyToManyField(blank=True, related_name='received_submissions', to='organisations.organisation', verbose_name='adressaadid')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='sent_submissions', to=settings.AUTH_USER_MODEL)),
                ('working_document', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='draft_submissions', to='documents.document', verbose_name='töödokument')),
            ],
            options={
                'verbose_name': 'väljasaadetud arvamus',
                'verbose_name_plural': 'väljasaadetud arvamused',
                'ordering': ['-sent_at', '-created_at'],
                'indexes': [models.Index(fields=['matter', '-sent_at'], name='submissions_matter_sent'), models.Index(fields=['status', '-sent_at'], name='submissions_status_sent')],
                'constraints': [models.CheckConstraint(condition=models.Q(('title', ''), _negated=True), name='submissions_title_required'), models.CheckConstraint(condition=models.Q(models.Q(('status', 'SENT'), _negated=True), models.Q(('final_version__isnull', False), ('sent_at__isnull', False)), _connector='OR'), name='submissions_sent_requires_timestamp_and_evidence'), models.CheckConstraint(condition=models.Q(('visibility_override__in', ['', 'NORMAL', 'RESTRICTED'])), name='submissions_visibility_vocabulary')],
            },
        ),
    ]
