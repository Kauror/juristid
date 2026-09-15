"""`Kaasamise kuupäev` learns how exactly it is known.

One `varchar(16) NOT NULL DEFAULT 'EXACT'` column on
`matters_matterengagement`, and one `CHECK` holding it to the `DatePrecision`
vocabulary. Nothing existing is read, rewritten or dropped; no index is built
and no other constraint is recreated.

**Schema only. There is no backfill and no inferred historical precision.**
Every row that exists was written through a box that asked for a day, so
`EXACT` is not a default standing in for an unknown answer — it is what those
rows actually mean, and the field default states it once rather than a
`RunPython` writing it two thousand times. A row whose date is `NULL` is
unaffected in the way that matters: an unknown date has no precision, and
`EXACT` beside a `NULL` renders nothing at all
(`MatterEngagement.display_date`).

**Why `EXACT` may be a column default here when `feedback_deadline` refused
one.** That column's default would have been a *date* — a value somebody's
software chose for a fact only a person knows. This one is the statement that a
recorded day is a day, which is what every existing row already asserts and
what the panel still writes unless somebody says otherwise.

Adding a `NOT NULL` column with a constant default is a catalogue-only
operation on PostgreSQL 11 and later: no table rewrite, no long lock. The
`CHECK` is validated against the existing rows, which is a scan of a small
table and is the same shape as the three constraints this model already
carries.

**No `period_end` column.** `MatterImportantDate` and `MatterEffectiveDate`
store one because their question is *has this passed*, asked in SQL about a
period's last day. An engagement is something that already happened, it is
never late, and the two readers that treat `occurred_on` as a number — the
`Viimane tegevus` maximum and the register sort — order on the anchor, which is
precisely what an anchor is for (docs/adr/0079 §2, *Alternatives*;
docs/adr/0082 §4).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0020_engagement_feedback_deadline"),
    ]

    operations = [
        migrations.AddField(
            model_name="matterengagement",
            name="occurred_on_precision",
            field=models.CharField(
                choices=[
                    ("EXACT", "Täpne"),
                    ("MONTH", "Kuu täpsusega"),
                    ("QUARTER", "Kvartali täpsusega"),
                    ("HALF_YEAR", "Poolaasta täpsusega"),
                    ("YEAR", "Aasta täpsusega"),
                    ("INFERRED", "Tuletatud tekstist"),
                ],
                default="EXACT",
                max_length=16,
                verbose_name="kuupäeva täpsus",
            ),
        ),
        migrations.AddConstraint(
            model_name="matterengagement",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    occurred_on_precision__in=[
                        "EXACT",
                        "MONTH",
                        "QUARTER",
                        "HALF_YEAR",
                        "YEAR",
                        "INFERRED",
                    ]
                ),
                name="matters_engagement_occurred_precision_vocabulary",
            ),
        ),
    ]
