"""Count intake-reader claims, so a staged file that kills the reader is settled FAILED.

The intake twin of documents.0015 (AUD-045). Additive: one column whose default
of zero is the database's (`db_default`), so the release still serving can
insert a row without knowing it (ENG-014).
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
            field=models.PositiveSmallIntegerField(
                default=0, db_default=0, verbose_name="töötlemiskatseid"
            ),
        ),
    ]
