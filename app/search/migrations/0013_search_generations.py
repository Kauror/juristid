"""Search generations: a full rebuild no longer holds business writes (ENG-011).

Search-specific, no business data.

* **`SearchGeneration`**, a new table the release still serving never reads.
* **`SearchDocument.generation`**, an integer with a *database* default of 1:
  every existing row is in generation 1, and the release still serving inserts
  rows there without naming the column. A constant default and no CHECK, so
  adding it is a catalogue change and holds its ACCESS EXCLUSIVE lock for
  milliseconds.
* **The one-row-per-source uniqueness gains the generation.** The new partial
  unique index is built before the old one is dropped, so there is no moment
  without one, and both are built and dropped ``CONCURRENTLY`` — writes to the
  table carry on meanwhile. Measured on the 146,850-row IMPORT corpus, a plain
  build would have refused writes for 246 ms. `migration_plan` flags both as
  constraint changes, which they are; the relaxation is safe for the release
  still serving, because its refresh deletes a source's rows in every
  generation before inserting one.
* **No plain index on `generation`**: readers filter on it in every query,
  and such an index would let the planner read the whole active generation
  instead of asking the text indexes. The rebuild's lookups by generation use
  the unique index, which leads with it.

``atomic = False`` for the concurrent builds; inside a transaction, such as the
test suite's, they are built plainly (`app.core.index_operations`).

Reversible. Backwards, the old uniqueness returns — which requires that at most
one generation's rows exist, so reverse only after a rebuild has finished and
cleaned up (`check_search_integrity` reports leftovers), or after
`rebuild_search_index` on the older release, which empties the table first.
"""

from django.db import migrations, models

import app.core.ids
from app.core.index_operations import (
    AddUniqueIndexConstraintConcurrentlyWhenPossible,
    RemoveUniqueIndexConstraintConcurrentlyWhenPossible,
)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("search", "0012_recall_and_match_context"),
    ]

    operations = [
        migrations.CreateModel(
            name="SearchGeneration",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=app.core.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("number", models.PositiveIntegerField(unique=True, verbose_name="number")),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("BUILDING", "Ehitamisel"),
                            ("ACTIVE", "Kasutusel"),
                            ("RETIRED", "Asendatud"),
                            ("FAILED", "Katkenud"),
                        ],
                        max_length=16,
                        verbose_name="olek",
                    ),
                ),
                (
                    "index_version",
                    models.CharField(blank=True, max_length=16, verbose_name="indeksi versioon"),
                ),
                ("started_at", models.DateTimeField(verbose_name="alustatud")),
                (
                    "activated_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="kasutusele võetud"),
                ),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="lõpetatud"),
                ),
                (
                    "rows",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="ridu"),
                ),
                ("last_error", models.TextField(blank=True, verbose_name="viimane viga")),
            ],
            options={
                "verbose_name": "otsinguindeksi põlvkond",
                "verbose_name_plural": "otsinguindeksi põlvkonnad",
                "ordering": ["number"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("state", "ACTIVE")),
                        fields=("state",),
                        name="search_one_active_generation",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("state", "BUILDING")),
                        fields=("state",),
                        name="search_one_building_generation",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="searchdocument",
            name="generation",
            field=models.IntegerField(
                db_default=1, default=1, editable=False, verbose_name="põlvkond"
            ),
        ),
        AddUniqueIndexConstraintConcurrentlyWhenPossible(
            model_name="searchdocument",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_object_id__isnull", False)),
                fields=("generation", "source_kind", "source_object_id"),
                name="search_one_row_per_source_and_generation",
            ),
        ),
        RemoveUniqueIndexConstraintConcurrentlyWhenPossible(
            model_name="searchdocument",
            name="search_one_document_per_source_object",
        ),
    ]
