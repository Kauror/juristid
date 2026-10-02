"""`MatterStageEpisode` — one period in which a Matter held one `Hetkeseis` (docs/adr/0131).

**One additive table, and nothing else changes.** `Matter.stage` keeps its
column, its meaning and every reader; the table beside it records the periods.
The rows a Matter already deserves are written by ``0043``, separately, so the
schema step and the data step can be judged apart.

The constraints are the invariants the services keep, stated again where a bulk
`update()`, a shell session or a future data migration cannot step around them:
at most one current period per Matter, a sequence per Matter, a current period
with no end, a recorded period with a start and a found one without, and no
period ending before it began. Nothing is unique on ``(matter, stage)`` — a
second consultation round is a second row.

**Reversible.** Dropping the table drops the periods recorded since; nothing
else reads it, and ``audit.0031``'s column is removed first by the graph.
"""

import app.core.ids
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0041_website_overview_title'),
        ('workflow', '0010_monitoring_stopped_stage'),
    ]

    operations = [
        migrations.CreateModel(
            name='MatterStageEpisode',
            fields=[
                ('id', models.UUIDField(default=app.core.ids.uuid7, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sequence', models.PositiveIntegerField(verbose_name='järjekorranumber')),
                ('origin', models.CharField(choices=[('RECORDED', 'Salvestatud üleminek'), ('CARRIED_OVER', 'Varasem hetkeseis, algus teadmata'), ('IMPORTED', 'Imporditud hetkeseis, algus teadmata')], default='RECORDED', max_length=16, verbose_name='päritolu')),
                ('started_at', models.DateTimeField(blank=True, null=True, verbose_name='algus')),
                ('ended_at', models.DateTimeField(blank=True, null=True, verbose_name='lõpp')),
                ('is_current', models.BooleanField(default=True, verbose_name='kehtiv')),
                ('matter', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stage_episodes', to='matters.matter', verbose_name='teema')),
                ('stage', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='matter_episodes', to='workflow.stagevocabulary', verbose_name='hetkeseis')),
            ],
            options={
                'verbose_name': 'hetkeseisu etapp',
                'verbose_name_plural': 'hetkeseisu etapid',
                'ordering': ['matter', 'sequence'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('is_current', True)), fields=('matter',), name='matters_one_current_stage_episode'), models.UniqueConstraint(fields=('matter', 'sequence'), name='matters_stage_episode_sequence_unique'), models.CheckConstraint(condition=models.Q(('is_current', False), ('ended_at__isnull', True), _connector='OR'), name='matters_current_stage_episode_has_no_end'), models.CheckConstraint(condition=models.Q(models.Q(('origin', 'RECORDED'), ('started_at__isnull', False)), models.Q(('origin__in', ['CARRIED_OVER', 'IMPORTED']), ('started_at__isnull', True)), _connector='OR'), name='matters_stage_episode_start_matches_origin'), models.CheckConstraint(condition=models.Q(('started_at__isnull', True), ('ended_at__isnull', True), ('ended_at__gte', models.F('started_at')), _connector='OR'), name='matters_stage_episode_ends_after_it_starts'), models.CheckConstraint(condition=models.Q(('sequence__gte', 1)), name='matters_stage_episode_sequence_positive')],
            },
        ),
    ]
