"""`Ülevaade / uudis`, `Oluline tähtaeg` and `Töövõit` become things the corpus can hold (F-008).

Search-specific, no business data. **Old → new:** `SearchDocument` gains three
nullable foreign keys — `website_overview` (→ `matters.MatterWebsiteOverview`),
`important_date` (→ `intelligence.MatterImportantDate`) and `work_victory`
(→ `intelligence.MatterWorkVictory`), each `ON DELETE CASCADE` — and one partial
index per key, over the rows where it is set. The `source_kind` choices gain
`WEBSITE_OVERVIEW`, `IMPORTANT_DATE` and `WORK_VICTORY`, and the column's Python
default `index_version` moves from `SONAVORM.1` to `SONAVORM.2`; both of those
are metadata PostgreSQL does not hold (`sqlmigrate` prints them as no-ops).

**What it does not change.** No row is inserted, rewritten or deleted, no
existing column, index or constraint is touched, and nothing in `matters` or
`intelligence` changes. The rows themselves are written by the indexer — the
per-write refresh for records saved from now on, and the one-time
`rebuild_search_index` for every record that already exists — never by a
migration guessing at content. Every existing row keeps `SONAVORM.1` and is
therefore not read by the new release until that rebuild runs: search returns
too little in between, never too much (`app.search.models.INDEX_VERSION`).

**Compatibility with the release still serving.** All three columns are
nullable and that release never names them, so its inserts and its reads are
unaffected (`migration_plan` classifies a nullable `AddField` as additive). It
writes no row of the new kinds and reads only `SONAVORM.1` rows; after this
release's rebuild it reads nothing at all — the same fail-closed state every
earlier version bump produced for a rolled-back release, repaired by running
`rebuild_search_index` on whichever release is serving.

`migration_plan` flags the `index_version` `AlterField` for a decision, as it
flagged the same operation in `search/0010` and `search/0012`: it compares
field definitions, and Django's `default` is applied in Python and never
reaches the column, so the release still serving writes exactly what it wrote
before. The decision is the documented one — deploy, then rebuild.

**Lock impact.** Each `ADD COLUMN … NULL REFERENCES …` takes an ACCESS
EXCLUSIVE lock on `search_searchdocument` and a SHARE ROW EXCLUSIVE lock on the
referenced table for one short statement: no rewrite (the column has no
default), and the new constraint's initial check is one pass over the heap for
a column that is NULL on every row — the same statement `search/0009` ran for
`development` and `external_position`. What would have scanned the table for
longer is the index a foreign key gets by default, built under a lock that
refuses writes. So the keys are declared `db_index=False` and their indexes are
built separately, ``CONCURRENTLY`` — admitting reads and writes while they
build, which is why this migration is ``atomic = False``. Inside a transaction,
such as the test suite's, they are built plainly
(`app.core.index_operations`). Partial, so each holds only its own kind's rows
and stays small.

**Reversible.** Backwards, the three indexes are dropped and the three columns
removed (taking every row of the new kinds' links with them — the rows remain,
unreadable to the older release because they carry `SONAVORM.2`, until its own
rebuild empties the table and refills it), and the metadata reverts.
"""

import django.db.models.deletion
from django.db import migrations, models

from app.core.index_operations import AddIndexConcurrentlyWhenPossible


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("intelligence", "0003_removable_records"),
        ("matters", "0046_save_once_and_plan_fulfilment"),
        ("search", "0013_search_generations"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchdocument",
            name="website_overview",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="search_documents",
                to="matters.matterwebsiteoverview",
                verbose_name="ülevaade / uudis",
            ),
        ),
        migrations.AddField(
            model_name="searchdocument",
            name="important_date",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="search_documents",
                to="intelligence.matterimportantdate",
                verbose_name="oluline tähtaeg",
            ),
        ),
        migrations.AddField(
            model_name="searchdocument",
            name="work_victory",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="search_documents",
                to="intelligence.matterworkvictory",
                verbose_name="töövõit",
            ),
        ),
        migrations.AlterField(
            model_name="searchdocument",
            name="index_version",
            field=models.CharField(default="SONAVORM.2", editable=False, max_length=16),
        ),
        migrations.AlterField(
            model_name="searchdocument",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("MATTER", "Teema"),
                    ("ENTRY", "Sissekanne"),
                    ("SUBMISSION", "Arvamus"),
                    ("DOCUMENT_FRAGMENT", "Dokumendi sisu"),
                    ("LEGACY_SOURCE_PAGE", "Ajalooline OneNote'i leht"),
                    ("ENGAGEMENT", "Kaasamine"),
                    ("PROCEDURAL_DEVELOPMENT", "Märge"),
                    ("EXTERNAL_POSITION", "Arvamus või tagasiside"),
                    ("DOCUMENT", "Dokument"),
                    ("WEBSITE_OVERVIEW", "Ülevaade / uudis"),
                    ("IMPORTANT_DATE", "Oluline tähtaeg"),
                    ("WORK_VICTORY", "Töövõit"),
                ],
                default="MATTER",
                max_length=32,
                verbose_name="allika liik",
            ),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=models.Index(
                condition=models.Q(website_overview__isnull=False),
                fields=["website_overview"],
                name="search_by_website_overview",
            ),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=models.Index(
                condition=models.Q(important_date__isnull=False),
                fields=["important_date"],
                name="search_by_important_date",
            ),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=models.Index(
                condition=models.Q(work_victory__isnull=False),
                fields=["work_victory"],
                name="search_by_work_victory",
            ),
        ),
    ]
