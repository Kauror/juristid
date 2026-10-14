"""QA audit 2026-10-14 (area `stats`): a number and the list it opens.

* QA-STATS-06 — Andmekvaliteet «Aktiivne teema ilma valdkonnata» and «Aktiivne
  teema ilma asutuseta» count open **FULL** Matters only, but link to the
  register with `olek=avatud` and no `liik=FULL`; an open ARCHIVE row (a
  historical row the cutover held open) is in the list and not in the number.
* QA-STATS-07 — the Ülevaade table «Menetlusliigid» prints «kokku N» where N
  excludes its own «Menetlusliik määramata» row, so the rows do not add up to
  the total printed above them.
* QA-STATS-08 — under a «Kirje liik: Arhiiv» chip the «Aktiivsed täielikud
  teemad» card is 0 (FULL ∧ ARCHIVE), and its link overrides the chip with
  `liik=FULL` and opens a non-empty list.
"""

from __future__ import annotations

import html
import re

import pytest
from django.urls import reverse

from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting.selectors import quality
from app.reporting.services import compute
from tests.test_reporting_drillthrough_data_scope import register_total

pytestmark = pytest.mark.django_db


@pytest.fixture
def open_archive_row(world):
    """A historical register row still open (held by the cutover), no area, no institution."""
    return Matter.objects.create(
        title="QA avatud arhiivirida",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        reference_year=world.today.year - 3,
        reference_number=777,
        reporting_year=world.today.year - 3,
        is_open=True,
    )


def _queues(context) -> dict[str, quality.QualityQueue]:
    return {queue.key: queue for queue in quality.queues(context)}


@pytest.mark.xfail(
    strict=True,
    reason="QA-STATS-06: quality queue counts open FULL Matters, its link opens every open Matter",
)
@pytest.mark.parametrize("queue_key", ["active_without_policy_area", "unresolved_organisation"])
def test_quality_queue_opens_the_matters_it_counted(
    queue_key, client, world, reporting_context, open_archive_row
):
    client.force_login(world.head)
    queue = _queues(reporting_context(world.head))[queue_key]
    assert register_total(client, queue.url) == queue.count, queue.url


@pytest.mark.parametrize("queue_key", ["active_without_policy_area", "unresolved_organisation"])
def test_quality_queue_parity_holds_without_open_archive_rows(
    queue_key, client, world, reporting_context
):
    """Probe (passes): in the shared world, which has no open ARCHIVE row, they agree."""
    client.force_login(world.head)
    queue = _queues(reporting_context(world.head)).get(queue_key)
    if queue is None:
        pytest.skip("queue empty in the shared world")
    assert register_total(client, queue.url) == queue.count


_TABLE = re.compile(
    r'<section class="ovsection" aria-labelledby="stat-(?P<key>[a-z_]+)-heading">(?P<body>.*?)</section>',  # noqa: E501
    re.S,
)
_KOKKU = re.compile(r"kokku (\d+)")
_ROW_VALUE = re.compile(r'<td class="table__date">(\d+)</td>')


def _overview_tables(client, viewer) -> dict[str, tuple[int, list[int]]]:
    client.force_login(viewer)
    body = client.get(reverse("reporting:overview"), {"periood": "koik"}).content.decode()
    tables = {}
    for match in _TABLE.finditer(body):
        section = html.unescape(match["body"])
        tables[match["key"]] = (
            int(_KOKKU.search(section).group(1)),
            [int(v) for v in _ROW_VALUE.findall(section)],
        )
    return tables


@pytest.mark.xfail(
    strict=True,
    reason="QA-STATS-07: «Menetlusliigid · kokku N» excludes its own «Menetlusliik määramata» row",
)
def test_menetlusliigid_rows_add_up_to_the_printed_total(client, world):
    kokku, rows = _overview_tables(client, world.head)["matters_by_track"]
    assert sum(rows) == kokku, (kokku, rows)


@pytest.mark.xfail(
    strict=True,
    reason="QA-STATS-08: a fixed liik=FULL on the link overrides the ARCHIVE chip the 0 was computed under",  # noqa: E501
)
def test_a_card_link_never_overrides_the_chip_its_count_used(client, world, reporting_context):
    context = reporting_context(world.head, record_mode=RecordMode.ARCHIVE.value)
    result = compute(keys.ACTIVE_FULL_MATTERS, context)
    assert result.value == 0
    client.force_login(world.head)
    assert register_total(client, result.drillthrough_url) == result.value, result.drillthrough_url
