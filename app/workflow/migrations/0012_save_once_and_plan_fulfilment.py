"""A `Tööplaan` step done by work recorded elsewhere (historical-regression round, UX-002).

Two nullable/blank columns and one check on `MatterPlanStep`: which typed record
(`Ülevaade / uudis`, `Kaasamine`, `Koja arvamus`) a person named, in its own
save, as the step's work. Both or neither, and only on a COMPLETED step. Every
existing row has neither — no step is marked done from work recorded before
this release. Instant `ADD COLUMN`s; the check holds for every existing row.
Reversible.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('matters', '0046_save_once_and_plan_fulfilment'),
        ('workflow', '0011_matter_plan_step'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='matterplanstep',
            name='fulfilled_by_operation',
            field=models.CharField(blank=True, choices=[('GENERIC', 'Tavaline tegevus'), ('WEBSITE_OVERVIEW', 'Ülevaade / uudis'), ('ENGAGEMENT', 'Kaasamine'), ('SUBMISSION', 'Koja arvamus')], default='', max_length=32),
        ),
        migrations.AddField(
            model_name='matterplanstep',
            name='fulfilled_by_record',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='matterplanstep',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('fulfilled_by_operation', ''), ('fulfilled_by_record__isnull', True)), models.Q(('fulfilled_by_record__isnull', False), ('state', 'COMPLETED'), models.Q(('fulfilled_by_operation', ''), _negated=True)), _connector='OR'), name='workflow_plan_step_fulfilled_by_record'),
        ),
    ]
