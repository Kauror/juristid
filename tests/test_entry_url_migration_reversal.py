"""Reversing `matters.0049_entry_url` over populated links: what is lost (JTEST-012).

`0049` adds `Entry.url` and `EntryRevision.url` for `+ Lisa · Muu`
(docs/adr/0097, amendment of 2026-10-15). The CI step-back targets migrations
far older than a deployment's leaf, and the seed it reverses over has no entry
with a link, so a backward migrate across `0049` *with links in the columns*
had never run anywhere but in the audit's own probe.

This runs it through the real executor, inside the test transaction
(PostgreSQL DDL is transactional, the same arrangement as
`tests/test_multiple_senders_migration.py`), and pins the answer:

* backwards to `0048` succeeds and keeps every entry and every revision — the
  rows, their bodies and their kinds;
* **the links are dropped.** `RemoveField` takes the two columns with their
  values, and nothing copies them anywhere first;
* forwards again, the columns come back empty (`db_default=""`): the links do
  not return. A rollback across `0049` is a data loss of every `Muu` link and
  every earlier link a correction kept, and the runbook says so
  (`deploy/unraid-main/RECOVERY.md`, `docs/production-readiness.md`).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from app.matters.services import edit_entry
from app.matters.workspace import add_matter_other
from tests import factories

pytestmark = pytest.mark.django_db

BEFORE = ("matters", "0048_intake_extraction_attempt_budget")
ADDED = ("matters", "0049_entry_url")

FIRST_LINK = "https://example.org/algne-materjal"
CORRECTED_LINK = "https://example.org/parandatud-materjal"


def _leaf() -> tuple[str, str]:
    loader = MigrationExecutor(connection).loader
    (leaf,) = loader.graph.leaf_nodes("matters")
    return leaf


def migrate_to(target: tuple[str, str]) -> None:
    """Move `matters` to one migration; constraints immediate first (see the sender test)."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


def _rows(table: str, *columns: str) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id")  # noqa: S608
        return list(cursor.fetchall())


def _column_exists(table: str, column: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        return cursor.fetchone() is not None


def _constraint_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", [name])
        return cursor.fetchone() is not None


@pytest.fixture
def restore_schema():
    """Back to the leaf however the test ends, so the module's other tests see today's schema."""
    yield
    migrate_to(_leaf())


@pytest.fixture
def linked(specialist):
    """A `Muu` with a link, corrected once, so both new columns hold a value."""
    matter = factories.MatterFactory(owner=specialist)
    entry = add_matter_other(
        matter=matter,
        author=specialist,
        description="Taustamaterjal lingiga.",
        occurred_on=dt.date(2026, 10, 1),
        url=FIRST_LINK,
    ).entry
    edit_entry(entry=entry, body=entry.body, actor=specialist, url=CORRECTED_LINK)
    factories.EntryFactory(matter=matter, author=specialist, body="<p>Märge ilma lingita.</p>")
    return matter, entry


def test_0049_is_still_the_step_that_adds_both_columns():
    """The test is about this migration; if its shape changes, so must the test."""
    migration = MigrationExecutor(connection).loader.get_migration(*ADDED)
    assert [(type(op).__name__, getattr(op, "name", "")) for op in migration.operations] == [
        ("AddField", "url"),
        ("AddField", "url"),
        ("AddConstraint", ""),
    ]


def test_reversing_0049_keeps_every_entry_and_drops_every_link(linked, restore_schema):
    _matter, entry = linked
    entries_before = _rows("matters_entry", "id", "kind", "body")
    revisions_before = _rows("matters_entryrevision", "id", "entry_id", "body")
    # The world really holds links in both columns — otherwise there is nothing to lose.
    assert (str(entry.pk), CORRECTED_LINK) in {
        (str(pk), url) for pk, url in _rows("matters_entry", "id", "url")
    }
    assert FIRST_LINK in {url for (url,) in _rows("matters_entryrevision", "url")}

    migrate_to(BEFORE)

    assert not _column_exists("matters_entry", "url")
    assert not _column_exists("matters_entryrevision", "url")
    assert not _constraint_exists("matters_entry_url_only_other")
    assert _rows("matters_entry", "id", "kind", "body") == entries_before
    assert _rows("matters_entryrevision", "id", "entry_id", "body") == revisions_before


def test_migrating_forward_again_does_not_bring_the_links_back(linked, restore_schema):
    entries_before = _rows("matters_entry", "id", "kind", "body")

    migrate_to(BEFORE)
    migrate_to(_leaf())

    assert _rows("matters_entry", "id", "kind", "body") == entries_before
    assert {url for (url,) in _rows("matters_entry", "url")} == {""}
    assert {url for (url,) in _rows("matters_entryrevision", "url")} == {""}
    assert _constraint_exists("matters_entry_url_only_other")
