"""The register document's own file links on a finding (owner, 2026-11-23).

One nullable column: additive, so the previous release runs on it unchanged.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("register_watch", "0006_agenda_item_decision"),
    ]

    operations = [
        migrations.AddField(
            model_name="watchfinding",
            name="files",
            field=models.JSONField(blank=True, null=True, verbose_name="failid"),
        ),
    ]
