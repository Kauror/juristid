"""Count extraction claims, so a file that kills its worker is settled FAILED (AUD-045).

Additive: one column whose default of zero is the database's (`db_default`),
so the release still serving can insert a row without knowing the column
(ENG-014; the deployment plan would otherwise refuse it). Existing rows read as "never
claimed since last asked for", which is the honest reading for every one that
is not mid-parse right now; a row that is mid-parse simply gets the full budget.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0014_carry_dormant_working_documents"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="extraction_attempts",
            field=models.PositiveSmallIntegerField(
                default=0, db_default=0, verbose_name="töötlemiskatseid"
            ),
        ),
    ]
