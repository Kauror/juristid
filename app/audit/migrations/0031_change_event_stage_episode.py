"""`ChangeEvent.stage_episode` — which `Hetkeseis` period an act was done in (docs/adr/0131 §4).

**One nullable column on the audit seam, and no backfill.** Every row written
before this keeps the null it is given here, and `Teema käik` reads a null as
«done before periods existed» rather than guessing a period from today's stage.
The append-only triggers guard `UPDATE` and `DELETE`, not `ALTER TABLE`, and no
existing row is updated.

The release still serving while `migrate` runs never names the column, so its
inserts write null — the same honest «not recorded» every older row carries.

**Reversible.** Removing the column drops the bindings recorded since; the
events themselves are untouched.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0030_document_correction_event_types'),
        ('matters', '0042_matter_stage_episode'),
    ]

    operations = [
        migrations.AddField(
            model_name='changeevent',
            name='stage_episode',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='change_events', to='matters.matterstageepisode', verbose_name='hetkeseisu etapp'),
        ),
    ]
