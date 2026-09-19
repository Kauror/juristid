"""`Kokkuvõte` on a sent opinion — what Koda argued, in the lawyer's own words.

One column, additive, blank on every row that already exists, and **carrying no
data at all**. There is nothing truthful to backfill it from: `title` is this
record's identity and is frequently a filename, `notes` is bookkeeping beside the
record, and copying either into a column that means «what the opinion says» would
manufacture a summary nobody wrote. Every Submission recorded before today
therefore reads «kokkuvõtet ei ole», which is what is true of it
(docs/adr/0095 §2).

`title` is untouched, `NOT NULL` and still refused empty by
`submissions_submission_title_not_blank`: the panel that stopped asking for one
supplies the uploaded file's own name instead, so the identity every list and
every register cell reads is still a real string somebody chose.

Reversible: `AddField` drops the column. What a reverse would lose is the text
people wrote into it, which is the ordinary cost of removing a column and not a
rewrite of anything that was here before.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0007_opinion_marksonad_and_overview_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="summary",
            field=models.TextField(blank=True, verbose_name="kokkuvõte"),
        ),
    ]
