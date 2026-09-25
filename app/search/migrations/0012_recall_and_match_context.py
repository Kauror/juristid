"""Round 6's recall and match-context columns (ENG-031, ENG-083).

Two columns, one version, two indexes — search-specific, and no business data.

* **`people_text`** holds the author's name a row names, in a column of its own
  so a hit on it can say «Autor». It has a *database* default as well as a
  Python one: the release still serving while this migrates inserts projection
  rows without naming the column, and without a database default those inserts
  would fail — inside a lawyer's save.
* **`search_folded`** is every searchable column with diacritics removed and
  nothing stemmed, for the diacritic-free and word-beginning tiers. Nullable,
  like every vector here: a row gets it when the indexer writes it.
* **`index_version`'s default** moves to `SONAVORM.1`, with the constant in
  `app/search/models.py`. Rows built before this are ineligible until the
  one-time rebuild (runbook step 11), which is what fills the two columns.

Adding the columns is metadata only in PostgreSQL 18 (a constant default is
not written into existing rows). The two indexes are built ``CONCURRENTLY``,
for `0011`'s reason: the release migrates while the previous web process still
serves, and a plain build would hold every search-refreshing write for its
duration. Hence ``atomic = False`` (and, inside a transaction such as the test
suite's, a plain build — `app.core.index_operations`). Reversible.
"""

import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models

from app.core.index_operations import AddIndexConcurrentlyWhenPossible


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("documents", "0011_procedural_development"),
        ("legacy_import", "0015_alter_opinionmatchcandidate_match_class_and_more"),
        ("matters", "0039_intake_session_records_the_matter_it_created"),
        ("search", "0011_trigram_indexes_every_row"),
        ("submissions", "0008_opinion_summary"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchdocument",
            name="people_text",
            field=models.TextField(
                blank=True,
                db_default="",
                default="",
                help_text="Kirje autori nimi.",
                verbose_name="inimesed",
            ),
        ),
        migrations.AddField(
            model_name="searchdocument",
            name="search_folded",
            field=django.contrib.postgres.search.SearchVectorField(
                editable=False, null=True
            ),
        ),
        migrations.AlterField(
            model_name="searchdocument",
            name="index_version",
            field=models.CharField(default="SONAVORM.1", editable=False, max_length=16),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_folded"], name="search_folded_gin"
            ),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["people_text"],
                name="search_people_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
