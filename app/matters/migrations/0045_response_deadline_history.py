"""`Arvamuse tähtaeg` keeps its history (historical-regression round, F-005/F-006).

Additive and rolling-safe:

* ``Matter.response_requested_at`` — nullable, no default: an instant
  ``ADD COLUMN`` with no table rewrite. Every existing row reads ``NULL``,
  which is exactly "a deadline from before requests were tracked" and keeps the
  discharge reading it always had (ADR 0059). Nothing is backfilled: no request
  time is invented for a deadline nobody recorded one for.
* ``MatterResponseDeadline`` — a new, empty table. No existing deadline is
  copied into it, closed, answered or linked to any opinion.

The running revision before the swap neither reads nor writes either, and a
deadline it writes in the window is read by the new code as a legacy one.
Reversible: unapplying drops the column and the table, losing only history
recorded after this release.
"""

import app.core.ids
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0044_engagement_lifecycle_tracked'),
        ('submissions', '0008_opinion_summary'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='matter',
            name='response_requested_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='arvamust küsiti'),
        ),
        migrations.CreateModel(
            name='MatterResponseDeadline',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deadline', models.DateField(verbose_name='arvamuse tähtaeg')),
                ('requested_at', models.DateTimeField(blank=True, null=True)),
                ('outcome', models.CharField(choices=[('ANSWERED', 'Vastatud'), ('NOT_ANSWERING', 'Otsustati mitte vastata'), ('SUPERSEDED', 'Asendatud uue küsimisega'), ('MOVED', 'Tähtaeg muudetud'), ('CANCELLED', 'Tühistatud'), ('CLOSED', 'Lõppes teema sulgemisega')], max_length=24, verbose_name='tulemus')),
                ('note', models.TextField(blank=True, default='', verbose_name='selgitus')),
                ('next_deadline', models.DateField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('ended_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ended_response_deadlines', to='matters.matter')),
                ('submission', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='answered_response_deadlines', to='submissions.submission', verbose_name='vastuseks saadetud arvamus')),
            ],
            options={
                'ordering': ['-ended_at', '-created_at'],
                'indexes': [models.Index(fields=['matter', '-ended_at'], name='matters_respdl_matter_idx')],
                'constraints': [models.CheckConstraint(condition=models.Q(('outcome__in', ['ANSWERED', 'NOT_ANSWERING', 'SUPERSEDED', 'MOVED', 'CANCELLED', 'CLOSED'])), name='matters_respdl_outcome_known'), models.CheckConstraint(condition=models.Q(('submission__isnull', True), ('outcome', 'ANSWERED'), _connector='OR'), name='matters_respdl_submission_only_when_answered'), models.CheckConstraint(condition=models.Q(('next_deadline__isnull', True), ('outcome__in', ['MOVED', 'SUPERSEDED', 'ANSWERED', 'NOT_ANSWERING']), _connector='OR'), name='matters_respdl_next_only_when_followed')],
            },
        ),
    ]
