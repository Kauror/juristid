"""`INDEX_VERSION` moves to `DOKUMENT.1`, and `DOCUMENT` joins the source kinds.

Both operations are Python metadata over `CharField`s: PostgreSQL holds neither
the version string as a column default nor the choices list, so this migration
is a no-op against the table (`sqlmigrate` prints `(no-op)` for both). **No row
is touched, rewritten or deleted, and no column, index or constraint changes.**
`SearchDocument` already had the nullable `document` foreign key a per-Document
row needs, because fragment rows use it.

It is here so the constants and the migration state agree — `makemigrations
--check` is a CI gate.

The *effect* is at query time and in the next rebuild, as with `OPSUM.1` and
`AUTH003.1`: every row written before this release carries the old version and
is not read, so search returns too little until the one-time
`rebuild_search_index` runs — and then every Document has a row of its own
(ENG-030), authored bodies are plain text rather than escaped HTML (ENG-082),
and bounded (ENG-084).

Rolling deploy: the old release writes rows under `TEEMA.1`, which the new one
does not read, and never writes a `DOCUMENT` row. Reversible: both
`AlterField`s reverse to the previous metadata and change no data. After a
rollback the old code ignores `DOCUMENT` rows only if it is also rebuilt — its
chokepoint reads `TEEMA.1` rows, so rows written under `DOKUMENT.1` are simply
ineligible to it until its own rebuild.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("search", "0009_procedural_development_and_external_position"),
    ]

    operations = [
        migrations.AlterField(
            model_name="searchdocument",
            name="index_version",
            field=models.CharField(default="DOKUMENT.1", editable=False, max_length=16),
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
                ],
                default="MATTER",
                max_length=32,
                verbose_name="allika liik",
            ),
        ),
    ]
