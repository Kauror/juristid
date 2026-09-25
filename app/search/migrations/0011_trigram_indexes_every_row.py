"""Trigram indexes over every projection row, built without blocking writes.

ENG-010. The two trigram indexes were partial — MATTER rows only — so they
served the fuzzy tier and nothing else; the substring tiers for child titles,
document names and aliases had no index at all. These three cover every row.

**Concurrently, and so not atomic.** A plain ``CREATE INDEX`` holds a SHARE lock
on `search_searchdocument` for the whole build, and every search-refreshing
business write — a Märge, a document, a title change — would wait behind it.
The release sequence migrates while the previous web process is still serving
(deploy/unraid-main/README.md, step 9), so that wait would land on a lawyer's
save. ``CREATE INDEX CONCURRENTLY`` takes a SHARE UPDATE EXCLUSIVE lock, which
admits reads and writes; it cannot run inside a transaction, hence
``atomic = False``. Inside a transaction — the test suite walking the graph
backwards — the same indexes are built plainly
(`app.core.index_operations`).

**New indexes first, old ones after.** In between, both exist, so there is no
moment at which the fuzzy tier has no index. If a concurrent build fails it
leaves an INVALID index behind; running `migrate` again does not repair that
by itself, and the recovery is `DROP INDEX CONCURRENTLY <name>` followed by
`migrate` (documented in docs/adr/0116).

Reversible: the reverse drops the three and rebuilds the two partial ones,
concurrently as well. Nothing here touches a row; the projection is unchanged.
"""

import django.contrib.postgres.indexes
from django.db import migrations

from app.core.index_operations import (
    AddIndexConcurrentlyWhenPossible,
    RemoveIndexConcurrentlyWhenPossible,
)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("documents", "0011_procedural_development"),
        ("legacy_import", "0015_alter_opinionmatchcandidate_match_class_and_more"),
        ("matters", "0039_intake_session_records_the_matter_it_created"),
        ("search", "0010_document_search_source"),
        ("submissions", "0008_opinion_summary"),
    ]

    operations = [
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["title"], name="search_title_trgm", opclasses=["gin_trgm_ops"]
            ),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["identifiers"],
                name="search_identifiers_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        AddIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["alias_text"],
                name="search_alias_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        RemoveIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            name="search_title_trigram",
        ),
        RemoveIndexConcurrentlyWhenPossible(
            model_name="searchdocument",
            name="search_identifiers_trigram",
        ),
    ]
