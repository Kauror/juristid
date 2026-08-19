"""Recipients and joint submitters become records, not bare links.

Addressee and "teadmiseks" are different facts: only the addressees answer the
question a reporting count asks, which is who Koda formally wrote to. A joint
letter is likewise only joint once the co-signatory confirms, so an intended
signatory is not recorded as an agreed one.

This replaces the two implicit many-to-many tables. No environment holds real
data yet, so no data migration is carried: the Secure Pilot Gate has not been
passed and every row anywhere is synthetic.
"""

import app.core.ids
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organisations', '0001_initial'),
        ('submissions', '0002_final_evidence_integrity'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubmissionJointSubmitter',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('confirmed', models.BooleanField(default=False, verbose_name='kinnitatud')),
                ('confirmed_at', models.DateTimeField(blank=True, null=True, verbose_name='kinnitatud')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='märkus')),
                ('organisation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='joint_submission_rows', to='organisations.organisation', verbose_name='organisatsioon')),
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='joint_submitter_rows', to='submissions.submission', verbose_name='arvamus')),
            ],
            options={
                'verbose_name': 'kaasesitaja',
                'verbose_name_plural': 'kaasesitajad',
                'ordering': ['organisation__name'],
            },
        ),
        # Adding `through=` is not an alteration: Django refuses to convert an
        # implicit many-to-many into an explicit one in place, so the old table
        # is dropped and the relationship re-declared against the new model.
        migrations.RemoveField(
            model_name='submission',
            name='joint_submitters',
        ),
        migrations.AddField(
            model_name='submission',
            name='joint_submitters',
            field=models.ManyToManyField(blank=True, help_text='Teised organisatsioonid, kelle nimel pöördumine ühiselt esitati.', related_name='joint_submissions', through='submissions.SubmissionJointSubmitter', to='organisations.organisation', verbose_name='kaasesitajad'),
        ),
        migrations.CreateModel(
            name='SubmissionRecipient',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('ADDRESSEE', 'Adressaat'), ('FOR_INFORMATION', 'Teadmiseks')], db_index=True, default='ADDRESSEE', max_length=32, verbose_name='roll')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='märkus')),
                ('organisation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='submission_recipient_rows', to='organisations.organisation', verbose_name='organisatsioon')),
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recipient_rows', to='submissions.submission', verbose_name='arvamus')),
            ],
            options={
                'verbose_name': 'arvamuse saaja',
                'verbose_name_plural': 'arvamuse saajad',
                'ordering': ['role', 'organisation__name'],
            },
        ),
        migrations.RemoveField(
            model_name='submission',
            name='recipients',
        ),
        migrations.AddField(
            model_name='submission',
            name='recipients',
            field=models.ManyToManyField(blank=True, related_name='received_submissions', through='submissions.SubmissionRecipient', to='organisations.organisation', verbose_name='saajad'),
        ),
        migrations.AddConstraint(
            model_name='submissionjointsubmitter',
            constraint=models.UniqueConstraint(fields=('submission', 'organisation'), name='submissions_unique_joint_submitter'),
        ),
        migrations.AddConstraint(
            model_name='submissionjointsubmitter',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('confirmed', False), ('confirmed_at__isnull', True)), models.Q(('confirmed', True), ('confirmed_at__isnull', False)), _connector='OR'), name='submissions_joint_confirmation_consistent'),
        ),
        migrations.AddConstraint(
            model_name='submissionrecipient',
            constraint=models.UniqueConstraint(fields=('submission', 'organisation'), name='submissions_unique_recipient_per_submission'),
        ),
    ]
