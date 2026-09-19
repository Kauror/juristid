"""`Liige` on received feedback — and one `CHECK` keeping it there.

Additive and carrying no data, for the reason docs/adr/0091's provenance column
gives at greater length: the two things a backfill could read are the membership
registry and the organisation's name, and both are exactly the inferences this
field exists to refuse. Whether a member wrote in is something the lawyer who
filed the answer knows and the database does not, so every existing row gets
`False` — which here means «nobody said» as much as it means «not a member», and
neither is a claim about the past.

Nothing recomputes it later either. An organisation that joins or leaves the
Chamber next year does not change what was true of feedback filed this year, so
there is no signal, no trigger and no periodic job attached to this column.

The `CHECK` is validated against the existing table as it is added and every
stored row satisfies it: they all have `source_is_member = False`, so the left
branch holds whatever their provenance is. What it refuses from now on is a
`True` on a discovered position or a `LEGACY` row — a value that could not have
come off either panel (docs/adr/0095 §4).

Reversible in full: `AddConstraint` and `AddField` both reverse, and dropping
them refuses nothing and rewrites nothing.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0030_procedural_development"),
        ("organisations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="matterexternalposition",
            name="source_is_member",
            field=models.BooleanField(default=False, verbose_name="liikmelt"),
        ),
        migrations.AddConstraint(
            model_name="matterexternalposition",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("source_is_member", False), ("provenance", "RECEIVED"), _connector="OR"
                ),
                name="matters_external_position_member_is_received",
            ),
        ),
    ]
