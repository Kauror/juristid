"""Two nullable columns, so that a deleted Teema can be a tombstone.

`Kustuta teema` removes every owned business row the database permits, and the
`Matter` row itself stays behind: `audit.ChangeEvent.matter` is `PROTECT` onto
an append-only table, so the audit history of a Matter can be neither deleted
nor detached and the row it points at cannot go (docs/adr/0096 §4).

**Additive and nothing else.** No `RunPython`, no `RunSQL`, no backfill and no
default: every existing Matter keeps `deleted_at IS NULL`, which is what "live"
means, so the new default manager returns exactly the population it returned
before this migration ran.

The manager and Meta changes carry no SQL at all — Django records them so that
the migration state matches the model, and `base_manager_name` is stated
explicitly because `objects` now filters and the related descriptors that must
*not* filter have to be pointed at the manager that does not.
"""

import django.db.models.deletion
import django.db.models.manager
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0031_external_position_member"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="matter",
            options={
                "base_manager_name": "all_objects",
                "default_manager_name": "objects",
                "ordering": ["-reference_year", "-reference_number", "-created_at"],
                "verbose_name": "teema",
                "verbose_name_plural": "teemad",
            },
        ),
        migrations.AlterModelManagers(
            name="matter",
            managers=[
                ("objects", django.db.models.manager.Manager()),
                ("all_objects", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AddField(
            model_name="matter",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True, db_index=True, null=True, verbose_name="kustutatud"
            ),
        ),
        migrations.AddField(
            model_name="matter",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="deleted_matters",
                to=settings.AUTH_USER_MODEL,
                verbose_name="kustutaja",
            ),
        ),
    ]
