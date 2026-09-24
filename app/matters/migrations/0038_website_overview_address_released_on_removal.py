"""A published `Ülevaade / uudis` taken off the file no longer holds its address.

`matters_website_overview_one_row_per_published_link` keeps one row per
address per Matter, which is a deliberate rule (docs/adr/0081): the same page
recorded twice is not a second overview. It was written before removal existed,
and removal (docs/adr/0102) sets `removed_at` without touching `status` — so a
removed published row kept the address for ever, and recording that page again
on the same Matter, the documented repair for a removal made in error, was a
permanent `IntegrityError` (ENG-025).

The rule is now about **live** rows: `status = 'PUBLISHED' AND removed_at IS
NULL`. Drop and re-create under the same name, with nothing else changed.

**A relaxation, and nothing is rewritten.** Every row that satisfied the old
condition satisfies the new one, so building the index cannot fail on existing
data, and no row is read, backfilled or changed. While the old constraint still
exists (between deploy and migrate) the only effect is the old refusal, which
the services now answer with a sentence rather than a 500.

**Reversible, and reversing it can fail on data.** Once a removed page has been
recorded again, re-creating the old constraint meets two PUBLISHED rows with one
address on one Matter and refuses — the honest outcome, since only somebody who
knows which row was the mistake can resolve it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matters", "0037_development_title_optional"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="matterwebsiteoverview",
            name="matters_website_overview_one_row_per_published_link",
        ),
        migrations.AddConstraint(
            model_name="matterwebsiteoverview",
            constraint=models.UniqueConstraint(
                condition=models.Q(("removed_at__isnull", True), ("status", "PUBLISHED")),
                fields=("matter", "url"),
                name="matters_website_overview_one_row_per_published_link",
            ),
        ),
    ]
