"""Count intake-reader claims, so a staged file that kills the reader is settled FAILED.

The intake twin of documents.0015 (AUD-045). Additive: one column, default zero.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0047_website_overview_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="matterintakefile",
            name="extraction_attempts",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="töötlemiskatseid"),
        ),
    ]
