"""The snapshot records which step it photographed.

One nullable foreign key, no data migration, and deliberately no backfill.

The three ``next_action_*`` columns are copied from a ``NextAction``, which may
carry its own ``visibility_override`` and so be restricted below a Matter the
whole department reads. Until now the read scoped those columns by the Matter
alone, which made the photograph a projection broader than its source
(docs/adr/0038, 0068 — F-4 of the 2026-09-09 restricted-data leakage audit).

The column stores the step's *identity*, never its visibility: the read joins
the live row and derives the answer there, so restricting a step takes effect on
the next query rather than on the next capture. A stored copy would go stale in
the fail-open direction, which is the reason ADR 0005 removed the last one.

Rows written before this migration have no pointer and nothing recorded which
step they copied, so their next-action facts blank for any reader who does not
already see every restricted child. That is the safe direction, and inventing
the missing answer is exactly the manufactured history this module refuses
(Stage-2E brief 52).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reporting', '0001_initial'),
        ('workflow', '0006_stage_help_from_the_department'),
    ]

    operations = [
        migrations.AddField(
            model_name='operationalmattersnapshot',
            name='next_action',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='operational_snapshots', to='workflow.nextaction', verbose_name='järgmine tegevus'),
        ),
    ]
