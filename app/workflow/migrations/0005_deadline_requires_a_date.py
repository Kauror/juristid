"""A DO action with a DEADLINE must carry a date.

The form said so for the user's benefit and the service now says so for every
caller, but this is the rule the whole work queue rests on: a deadline with no
date cannot be met, missed, planned against or reported on, and an importer or
integration writing one would put a permanently meaningless row into somebody's
Teen list.

WAIT and MONITOR are unaffected. They frequently have no date at all — "waiting
for the ministry, no idea when" is a real and honest state.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0004_seed_stage_vocabulary"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="nextaction",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(kind="DO", date_semantics="DEADLINE")
                    | models.Q(target_date__isnull=False)
                ),
                name="workflow_deadline_requires_a_date",
            ),
        ),
    ]
