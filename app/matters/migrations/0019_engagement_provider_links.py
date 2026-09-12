"""`Smaily link` and `Alchemer link` — two optional pointers beside the generic one.

Additive, and only additive. Two nullable-in-practice `varchar(1000)` columns
with a `''` default arrive on `matters_matterengagement`; nothing existing is
touched. No constraint is dropped or recreated, no index is built, no table is
rewritten, and `url` keeps exactly the meaning it has had since 0009 — the one
durable address for the engagement as a whole.

**There is no backfill, and nothing to backfill from.** A historical row's
`url` is whichever single address somebody had at the time; guessing from its
host which of the two new columns it "really" was would rewrite a record on the
strength of a string match. Every row that exists today reads back with both
new fields blank, which is the truthful answer to a question nobody was asked
until now, and the read surface renders nothing at all for an engagement that
has neither (docs/adr/0027, amended 2026-09-12).

`blank=True` with no `null=True` is the repository's convention for an optional
text column — the absent value is `''`, so no read has to distinguish two kinds
of emptiness, and the existing `url` column is declared the same way.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0018_engagement_kind_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="matterengagement",
            name="smaily_url",
            field=models.URLField(blank=True, max_length=1000, verbose_name="Smaily link"),
        ),
        migrations.AddField(
            model_name="matterengagement",
            name="alchemer_url",
            field=models.URLField(blank=True, max_length=1000, verbose_name="Alchemer link"),
        ),
    ]
