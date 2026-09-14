"""`Tagasisidet ootame kuni` — one nullable date beside the engagement's own.

Additive, and only additive. One `date NULL` column arrives on
`matters_matterengagement`; nothing existing is read, rewritten or dropped, no
constraint is recreated and no index is built.

**There is no backfill, and there is nothing to backfill from.** Every row that
exists today was written before the question was asked, so the truthful value
for all of them is `NULL` — which is what a nullable `AddField` gives them
through schema semantics alone, with no `RunPython` and no table rewrite.
Deriving a reply-by date from `occurred_on`, `created_at`, the note or a
provider link would put a deadline on the file that nobody set.

No database default either. A column default would mean every future row
written by any path that does not name the field carries a date somebody's
software chose, which is the failure this whole release is about: the panel used
to stamp today on `occurred_on` behind the person's back.

Not indexed. Nothing filters, orders or counts on this column in this release,
and `feedback_deadline` is deliberately not a work item, a deadline row or a
statistic (`app/matters/models.py`).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0019_engagement_provider_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="matterengagement",
            name="feedback_deadline",
            field=models.DateField(
                blank=True, null=True, verbose_name="tagasisidet ootame kuni"
            ),
        ),
    ]
