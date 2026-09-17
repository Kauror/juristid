"""An `Ülevaade / uudis` may be published without a known publication date.

**One constraint out and a weaker one in, and nothing else.**

docs/adr/0081 §6 wrote «published means published» as two implications: a
`PUBLISHED` row has an address *and* a day, and every other row has neither.
Lawyer testing in September 2026 found the first half was refusing ordinary
saves. The commonest real case is an address pasted out of a search result, a
mail or a colleague's message, where the page plainly exists and the day it went
up is not on the page, not remembered and not worth a hunt — and what people
actually filed when the save refused was **today**, a date nobody had checked,
on the one column that is the person's own statement.

So the first implication now requires the address alone
(`matters_website_overview_published_has_link`). Published *by URL* and
published *on a known day* are two different facts, and only the first one makes
a publication (docs/adr/0089 §8).

The second implication is untouched: a `PLANNED` or `CANCELLED` row still
carries neither an address nor a date, because a date on a record claiming
nothing was published is a date about nothing.

What this migration deliberately does **not** do
------------------------------------------------
**No column changes.** `published_on` has been `null=True, blank=True` since
`0022_matter_website_overview`; the requirement lived in the `CHECK` and in the
service, never in the column. The database already permitted `NULL`, so there
is nothing to alter and nothing to rewrite.

**No data migration, no backfill, no `RunPython`, no `RunSQL`.** Rows already
stored keep their dates exactly as they are. Some of them carry the day they
were recorded because the old panel proposed it, and there is **no stored
provenance that distinguishes those from a date somebody typed deliberately** —
a `published_on` equal to `created_at`'s local day is as likely to be a
publication somebody recorded the same morning as it is to be an accepted
default. A heuristic that cleared them would silently destroy true dates to
remove guessed ones, which is a worse file than the one it started from. The new
rule is prospective, and a date somebody knows is wrong is cleared by that
person, through the correction control, as a recorded correction
(docs/adr/0089 §9).

**No search rebuild and no reindex.** `MatterWebsiteOverview` has never been
projected into `SearchDocument`, and nothing about it is in the archive
projection either.

Reversibility
-------------
The direction that matters is forward, and it is strictly *weakening*: every row
that satisfied the old constraint satisfies the new one, so this applies against
a populated database without inspecting a single row.

Backwards is the direction that can fail, and it should: `migrate matters 0027`
restores a constraint requiring a date on every published row, and PostgreSQL
will refuse to add it while any row recorded under the new rule has none. That
is the honest failure. It says what a rollback would have to decide first — what
date those rows are supposed to have, which is a question only a person can
answer — rather than inventing one to make the rollback quiet.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0027_procedural_link"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="matterwebsiteoverview",
            name="matters_website_overview_published_has_link_and_date",
        ),
        migrations.AddConstraint(
            model_name="matterwebsiteoverview",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("status", "PUBLISHED"), _negated=True),
                    models.Q(("url", ""), _negated=True),
                    _connector="OR",
                ),
                name="matters_website_overview_published_has_link",
            ),
        ),
    ]
