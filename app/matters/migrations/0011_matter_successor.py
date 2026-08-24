"""`Järglane` — the Matter whose work continues this one's.

Additive and nullable. Nothing is backfilled: the register's imported
`continues_under_reference` is free text about a reference somebody typed, and
resolving it to a row would manufacture a relationship the source never
asserted. Every existing Matter keeps a null successor until a person names one
while closing (Teema redesign §16, §33).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0010_brief_summary_and_personal_notes"),
        ("organisations", "0001_initial"),
        ("taxonomy", "0003_working_policy_area_vocabulary"),
        ("workflow", "0005_deadline_requires_a_date"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="matter",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="supersedes",
                to="matters.matter",
                verbose_name="jätkub teemana",
            ),
        ),
        migrations.AddConstraint(
            model_name="matter",
            constraint=models.CheckConstraint(
                condition=models.Q(("superseded_by", models.F("id")), _negated=True),
                name="matters_not_superseded_by_itself",
            ),
        ),
    ]
