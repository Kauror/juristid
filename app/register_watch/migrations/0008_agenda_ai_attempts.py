"""How many times the model was asked about an agenda (audit HR-02, 2026-11-23).

A new counter only; no row changes. An agenda stored before this has 0 and so
may be asked at most `agenda.MAX_ATTEMPTS` more times.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("register_watch", "0007_finding_files"),
    ]

    operations = [
        migrations.AddField(
            model_name="governmentagenda",
            name="ai_attempts",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="hindamiskatseid"),
        ),
    ]
