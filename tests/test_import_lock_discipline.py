"""Import applies lock Matters the way interactive writers do (AUD-043).

The canonical discipline (`app.matters.locks`, docs/adr/0109–0111): Matter
first, `FOR NO KEY UPDATE`, several Matters in ascending id, and only the
Matters the operation names. The import applies took `FOR UPDATE`, in sheet
order, one at a time as the loop reached them — and `register_outreach` locked
every Matter carrying a reference in one unordered statement. Asserted here on
the statements themselves:

* every Matter lock an apply takes is `FOR NO KEY UPDATE`;
* the batch's Matters are locked before anything is written, ascending;
* outreach locks the Matters its mapping names and no other.

The behaviour that matters to a lawyer — a child row inserted on a Matter the
apply holds does not wait for the apply — is in
`tests/test_import_lock_concurrency.py`, which needs real transactions.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import.owner_backfill import apply_backfill_plan, build_backfill_plan
from app.legacy_import.register_outreach import apply_mapping, mapping_digest
from tests.synthetic_portfolio import build_portfolio
from tests.test_register_outreach import add_matter, approved, world  # noqa: F401

pytestmark = pytest.mark.django_db

_UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}")


def _matter_locks(queries) -> list[tuple[str, uuid.UUID]]:
    """(lock clause, Matter id) for every locking read of `matters_matter`."""
    locks = []
    for query in queries:
        sql = query["sql"]
        if "FOR " not in sql or 'FROM "matters_matter"' not in sql:
            continue
        clause = "FOR NO KEY UPDATE" if "FOR NO KEY UPDATE" in sql else "FOR UPDATE"
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        found = _UUID.search(where.lower())
        locks.append((clause, uuid.UUID(found.group(0)) if found else None))
    return locks


def _first_write(queries) -> int:
    for index, query in enumerate(queries):
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            return index
    return len(queries)


def test_the_backfill_locks_its_matters_ascending_and_no_key_before_writing() -> None:
    build_portfolio()
    plan = build_backfill_plan()
    named = sorted({uuid.UUID(str(c.matter.pk)) for c in plan.assignable})
    assert len(named) >= 1

    with CaptureQueriesContext(connection) as captured:
        apply_backfill_plan(plan)

    queries = list(captured.captured_queries)
    locks = _matter_locks(queries)
    assert locks and {clause for clause, _ in locks} == {"FOR NO KEY UPDATE"}
    before_writing = _matter_locks(queries[: _first_write(queries)])
    assert [pk for _, pk in before_writing][: len(named)] == named


def test_outreach_locks_only_the_matters_its_mapping_names(world) -> None:  # noqa: F811
    mapped = add_matter(world)
    unrelated = add_matter(world, reference=2, title="Teine sünteetiline teema")
    links = approved(mapped)

    with CaptureQueriesContext(connection) as captured:
        apply_mapping(links=links, expect_mapping_sha256=mapping_digest(links))

    locks = _matter_locks(captured.captured_queries)
    assert locks and {clause for clause, _ in locks} == {"FOR NO KEY UPDATE"}
    locked = {pk for _, pk in locks}
    assert uuid.UUID(str(mapped.pk)) in locked
    assert uuid.UUID(str(unrelated.pk)) not in locked
