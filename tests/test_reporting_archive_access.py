"""Statistika asks `may_read_archive` before it counts the opinion archive.

csf_a21a2d1c9c6d746327ed27e9 (docs/adr/0175): the archive pages refused a reader
and a viewer with no persona, and Statistika told both of them how large the
archive is, how many distinct letters it holds, how they fall by year and how
much of it reached a Matter. The selectors queried `OpinionArchiveItem`
directly; nothing between the page and the corpus asked the archive's rule.

What is asserted here:

* every archive metric is registered, and registered means guarded;
* a viewer the rule refuses gets no archive metric — not on a page, not in a
  page builder's lists, not in a CSV, and not from a direct `compute` call;
* the refusal comes before any archive query;
* a viewer the rule admits gets exactly the numbers the selector computes;
* browse and Statistika follow one decision: moving a role in or out of
  `ARCHIVE_READERS` moves both.
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.accounts.enums import UserRole
from app.core.authorization import DEPARTMENT_VIEWER
from app.legacy_import import opinion_access
from app.legacy_import.opinion_archive import OpinionArchiveItem
from app.reporting import metric_catalogue as keys
from app.reporting import services
from app.reporting.archive_access import ARCHIVE_WITHHELD, ArchiveMetricWithheld
from app.reporting.metric_catalogue import definition
from app.reporting.selectors.base import answering

pytestmark = pytest.mark.django_db

#: Written out rather than read from `services.ARCHIVE_METRICS`, so a decorator
#: dropped from one selector fails here instead of quietly shrinking the set.
EXPECTED_ARCHIVE_METRICS = frozenset(
    {
        keys.OPINION_ARCHIVE_OCCURRENCES,
        keys.OPINION_ARCHIVE_DISTINCT_BINARIES,
        keys.OPINION_ARCHIVE_BY_YEAR,
        keys.OPINION_ARCHIVE_BY_MONTH,
        keys.OPINION_ARCHIVE_YOY_CHANGE,
        keys.OPINION_ARCHIVE_LINK_COVERAGE,
        keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY,
        keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY,
        keys.OPINION_ARCHIVE_MATTER_COVERAGE,
        keys.OPINION_ARCHIVE_UNRESOLVED,
        keys.HISTORICAL_SUBMISSION_COVERAGE,
    }
)

STATISTIKA_PAGES = (
    "/statistika/",
    "/statistika/teemad/",
    "/statistika/tegevus/",
    "/statistika/ajalooline/",
    "/statistika/andmekvaliteet/",
)

CSV_EXPORTS = ("teemad", "arvamused", "materjalid", "andmekvaliteet")

#: Words only an archive figure prints. `Arvamuste arhiiv ajas` is the heading
#: of the archive section on Tegevus; the labels are each metric's own.
ARCHIVE_WORDS = (
    "Arvamuste arhiiv ajas",
    "tegevus-arhiiv-heading",
    *(definition(key).label_et for key in sorted(EXPECTED_ARCHIVE_METRICS)),
)


def _normalised(result) -> dict:
    data = dataclasses.asdict(result)
    data.pop("as_of", None)
    return data


def _csv(client, slug: str) -> str:
    response = client.get(f"/statistika/eksport/{slug}.csv", {"periood": "koik"})
    assert response.status_code == 200, slug
    return b"".join(response.streaming_content).decode("utf-8-sig")


def _filenames() -> list[str]:
    names = list(OpinionArchiveItem.objects.values_list("original_filename", flat=True))
    assert names, "the archive fixture holds no files to look for"
    return names


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_every_archive_metric_is_registered_as_reading_the_archive():
    assert services.ARCHIVE_METRICS == EXPECTED_ARCHIVE_METRICS


def test_the_submission_recipient_share_is_not_an_archive_metric():
    """It sits beside the archive figures on Andmekvaliteet and counts Submissions."""
    assert keys.SUBMISSION_RECIPIENT_COVERAGE not in services.ARCHIVE_METRICS


def test_every_selector_in_the_archive_module_is_registered():
    """Everything `selectors/archive.py` answers reads the archive."""
    from app.reporting.selectors import archive

    in_module = {
        key
        for key, computer in services.COMPUTERS.items()
        if getattr(computer, "__module__", "") == archive.__name__
    }
    assert in_module
    assert in_module <= services.ARCHIVE_METRICS


# ---------------------------------------------------------------------------
# Refused viewers
# ---------------------------------------------------------------------------


def _refused_viewers(world):
    return {"reader": world.reader, "department": DEPARTMENT_VIEWER}


@pytest.mark.parametrize("who", ["reader", "department"])
@pytest.mark.parametrize("key", sorted(EXPECTED_ARCHIVE_METRICS))
def test_a_viewer_who_may_not_read_the_archive_gets_no_archive_metric(
    who, key, world, archive_world, reporting_context
):
    viewer = _refused_viewers(world)[who]
    assert not opinion_access.may_read_archive(viewer)
    context = reporting_context(viewer)

    assert services.withheld(key, context)
    assert services.compute_many([key], context) == []
    with pytest.raises(ArchiveMetricWithheld, match=re.escape(ARCHIVE_WITHHELD)):
        services.compute(key, context)


@pytest.mark.parametrize("key", sorted(EXPECTED_ARCHIVE_METRICS))
def test_the_refusal_comes_before_any_archive_query(key, world, archive_world, reporting_context):
    context = reporting_context(world.reader)
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(ArchiveMetricWithheld, match=re.escape(ARCHIVE_WITHHELD)):
            services.compute(key, context)
    archive_queries = [q["sql"] for q in queries if "legacy_import_opinion" in q["sql"]]
    assert archive_queries == [], key


def test_no_page_builder_hands_a_refused_viewer_an_archive_result(
    world, archive_world, reporting_context
):
    context = reporting_context(world.reader)
    pages = {
        "matters": services.matters_page(context),
        "activity": services.activity_page(context),
        "historical": services.historical_page(context),
        "quality": services.quality_page(context),
    }
    for name, page in pages.items():
        shown = {result.key for result in page.all_results}
        assert not shown & services.ARCHIVE_METRICS, name
    activity = pages["activity"]
    assert activity.groups["archive_trends"] == []
    assert activity.groups["archive_charts"] == []
    assert activity.groups["archive_matrices"] == []


@pytest.mark.parametrize("path", STATISTIKA_PAGES)
def test_no_statistika_page_shows_a_reader_the_archive(path, client, world, archive_world):
    client.force_login(world.reader)
    response = client.get(path, {"periood": "koik"})
    assert response.status_code == 200, path
    body = response.content.decode()
    for word in ARCHIVE_WORDS:
        assert word not in body, (path, word)
    for name in _filenames():
        assert name not in body, (path, name)


@pytest.mark.parametrize("slug", CSV_EXPORTS)
def test_no_csv_export_carries_the_archive_to_a_reader(slug, client, world, archive_world):
    client.force_login(world.reader)
    body = _csv(client, slug)
    for word in ARCHIVE_WORDS:
        assert word not in body, (slug, word)
    for name in _filenames():
        assert name not in body, (slug, name)


# ---------------------------------------------------------------------------
# Admitted viewers keep their numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["martin", "head", "admin"])
@pytest.mark.parametrize("key", sorted(EXPECTED_ARCHIVE_METRICS))
def test_an_archive_reader_gets_exactly_what_the_selector_computes(
    who, key, world, archive_world, reporting_context
):
    viewer = getattr(world, who)
    assert opinion_access.may_read_archive(viewer)
    context = reporting_context(viewer)
    assert not services.withheld(key, context)

    shown = services.compute(key, context)
    selector = getattr(services.COMPUTERS[key], "__wrapped__")  # noqa: B009
    fresh = reporting_context(viewer)
    with answering(fresh):
        unguarded = selector(fresh)
    assert _normalised(shown) == _normalised(unguarded), key


def test_an_archive_reader_still_sees_the_archive_section(client, world, archive_world):
    """The positive control for the page tests above: the words are there to find."""
    client.force_login(world.martin)
    body = client.get("/statistika/tegevus/", {"periood": "koik"}).content.decode()
    assert "tegevus-arhiiv-heading" in body
    assert definition(keys.OPINION_ARCHIVE_BY_YEAR).label_et in body
    quality = client.get("/statistika/andmekvaliteet/", {"periood": "koik"}).content.decode()
    assert definition(keys.OPINION_ARCHIVE_MATTER_COVERAGE).label_et in quality


# ---------------------------------------------------------------------------
# One decision
# ---------------------------------------------------------------------------


def test_browse_and_statistika_follow_one_decision(
    monkeypatch, client, world, archive_world, reporting_context
):
    """Widen the reader set to READER and narrow it past SPECIALIST: both move."""
    browse = reverse("legacy_import:opinion_archive_browse")
    key = keys.OPINION_ARCHIVE_DISTINCT_BINARIES

    monkeypatch.setattr(opinion_access, "ARCHIVE_READERS", frozenset({UserRole.READER.value}))

    client.force_login(world.reader)
    assert client.get(browse).status_code == 200
    assert not services.withheld(key, reporting_context(world.reader))
    assert services.compute(key, reporting_context(world.reader)).value > 0

    client.force_login(world.martin)
    assert client.get(browse).status_code == 403
    assert services.withheld(key, reporting_context(world.martin))
    body = client.get("/statistika/tegevus/", {"periood": "koik"}).content.decode()
    assert "tegevus-arhiiv-heading" not in body
