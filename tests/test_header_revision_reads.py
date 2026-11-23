"""The header's inline-editor revisions read the Matter's row once (HR-12).

The Teema header draws six inline editors that post a revision back (ENG-028).
Four of them — `Lühikokkuvõte`, `Hetkeseis`, `Vastutaja`, `Saabus` — carry a
column of the Matter's own row, and each used to be its own single-column read
of that row: four queries for one row on every Teema page and every header
fragment (health review 2026-11-23, HR-12). They are one fresh read now.

What must not move is the digest itself: a page rendered by the previous
release posts its token to the next one, so each field's revision has to stay
byte-for-byte what `matter_field_revision` gave for that field alone.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.matters.services import _field_digest, matter_field_revision, owner_revision_for
from tests import factories

pytestmark = pytest.mark.django_db

#: The four row columns the header's revisions read.
ROW_COLUMNS = ("brief_summary", "stage_id", "owner_id", "received_date")


def _column_read(column: str) -> re.Pattern[str]:
    return re.compile(rf'"matters_matter"\."{column}" AS "{column}"')


def _revision_row_reads(sql: list[str]) -> list[str]:
    """The statements that read one of the revision columns by name."""
    return [
        statement
        for statement in sql
        if any(_column_read(column).search(statement) for column in ROW_COLUMNS)
    ]


@pytest.mark.parametrize("viewer", ["owner", "reader"])
def test_the_teema_page_reads_the_revision_columns_in_one_statement(
    client, specialist, reader, organisation, viewer
):
    """Before HR-12: four statements, one per column. Now: exactly one, carrying all four."""
    matter = factories.MatterFactory(
        owner=specialist,
        source_organisations=[organisation],
        brief_summary="Lühike kokkuvõte",
        received_date=dt.date(2026, 11, 2),
    )
    client.force_login(specialist if viewer == "owner" else reader)

    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 200
    reads = _revision_row_reads([query["sql"] for query in captured.captured_queries])
    assert len(reads) == 1, f"{len(reads)} reads of the revision columns: {reads}"
    for column in ROW_COLUMNS:
        assert _column_read(column).search(reads[0]), column


def test_the_header_reads_six_revisions_in_three_queries(
    django_assert_num_queries, specialist, organisation
):
    """One read of the row, one of `Valdkonnad`, one of `Saatja` — exact, not a ceiling."""
    from app.matters.services import matter_field_revisions
    from app.matters.views import HEADER_REVISION_FIELDS

    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    with django_assert_num_queries(3):
        revisions = matter_field_revisions(matter, HEADER_REVISION_FIELDS)
    assert set(revisions) == set(HEADER_REVISION_FIELDS)


@pytest.mark.parametrize("filled", [True, False], ids=["filled", "blank"])
def test_each_revision_is_the_digest_it_always_was(specialist, organisation, filled):
    """A token rendered before HR-12 is still accepted after it, and the other way round."""
    from app.matters.services import matter_field_revisions
    from app.matters.views import HEADER_REVISION_FIELDS

    area = factories.PolicyAreaFactory()
    stage = factories.StageFactory()
    matter = factories.MatterFactory(
        owner=specialist,
        source_organisations=[organisation] if filled else [],
        brief_summary="Kokkuvõte" if filled else "",
        received_date=dt.date(2026, 11, 2) if filled else None,
        stage=stage if filled else None,
    )
    if filled:
        matter.policy_areas.add(area)

    revisions = matter_field_revisions(matter, HEADER_REVISION_FIELDS)

    assert revisions["owner"] == owner_revision_for(specialist.pk)
    assert revisions["brief_summary"] == _field_digest(
        "brief_summary", ["Kokkuvõte" if filled else ""]
    )
    assert revisions["received_date"] == _field_digest(
        "received_date", ["2026-11-02" if filled else ""]
    )
    assert revisions["stage"] == _field_digest("stage", [str(stage.pk) if filled else ""])
    assert revisions["policy_areas"] == _field_digest(
        "policy_areas", [str(area.pk)] if filled else []
    )
    assert revisions["source_organisations"] == _field_digest(
        "source_organisations", [str(organisation.pk)] if filled else []
    )
    for field in HEADER_REVISION_FIELDS:
        assert revisions[field] == matter_field_revision(matter, field), field


def test_the_revision_reads_the_database_not_the_instance(specialist):
    """Still a fresh read: a row changed behind the instance changes the token (ENG-028)."""
    from app.matters.models import Matter
    from app.matters.services import matter_field_revisions

    matter = factories.MatterFactory(owner=specialist, brief_summary="Enne")
    before = matter_field_revisions(matter, ("brief_summary",))["brief_summary"]
    Matter.objects.filter(pk=matter.pk).update(brief_summary="Pärast")

    after = matter_field_revisions(matter, ("brief_summary",))["brief_summary"]

    assert matter.brief_summary == "Enne"
    assert after != before
    assert after == _field_digest("brief_summary", ["Pärast"])
