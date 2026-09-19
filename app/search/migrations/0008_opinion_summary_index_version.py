"""`INDEX_VERSION` moves to `OPSUM.1` — the default on the column, and nothing else.

Python metadata over a `CharField`'s `default`: PostgreSQL never held the old
string as a column default and will never hold the new one, so this `AlterField`
is a no-op against the table. **No row is touched, no row is rewritten, and no
row is deleted.**

What it is here for is that the constant and the migration state must not
disagree — `makemigrations --check` is a CI gate, and a bump with no migration
fails it on every later branch.

The *effect* of the bump is at query time and not here:
`app.search.services._scoped_documents` refuses to read a row that does not carry
the current version, so every row written before this release becomes ineligible
the moment the code is deployed, and search returns too little until the
established one-time rebuild runs. That is deliberately fail-closed, and it is
the same shape the `AUTH003.1` bump took.

Why the contract changed: a sent opinion's substantive description used to be
typed into `Submission.title`, which the projection indexes in the identity tier.
docs/adr/0095 §2 moves it to `Submission.summary`, which the projection now reads
into the body — so a row written before this release carries a tsvector the
current code would not produce for it (`app.search.child_indexing`).

`ARCHIVE_INDEX_VERSION` is untouched. The archive's projection did not change.

Reversible: `AlterField` reverses to the previous default, and reversing it
changes no data either.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('search', '0007_search_rebuild_debt'),
    ]

    operations = [
        migrations.AlterField(
            model_name='searchdocument',
            name='index_version',
            field=models.CharField(default='OPSUM.1', editable=False, max_length=16),
        ),
    ]
