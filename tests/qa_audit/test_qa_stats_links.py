"""QA audit 2026-10-14 (area `stats`): a number and the list it opens.

* QA-STATS-06 — Andmekvaliteet «Aktiivne teema ilma valdkonnata» and «Aktiivne
  teema ilma asutuseta» count open **FULL** Matters only, but link to the
  register with `olek=avatud` and no `liik=FULL`; an open ARCHIVE row (a
  historical row the cutover held open) is in the list and not in the number.
* QA-STATS-07 — the Ülevaade table «Menetlusliigid» prints «kokku N» where N
  excludes its own «Menetlusliik määramata» row, so the rows do not add up to
  the total printed above them.
* QA-STATS-08 — under a «Kirje liik: Arhiiv» chip the «Aktiivsed täielikud
  teemad» card is 0 (FULL ∧ ARCHIVE), and its link overrode the chip with
  `liik=FULL` and opened a non-empty list. Fixed: such a link is not drawn.
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


def test_menetlusliigid_rows_add_up_to_the_printed_total(client, world):
    kokku, rows = _overview_tables(client, world.head)["matters_by_track"]
    assert sum(rows) == kokku, (kokku, rows)


def test_a_card_link_never_overrides_the_chip_its_count_used(client, world, reporting_context):
    """QA-STATS-08, fixed: the link never replaces the chip the count was taken under.

    Changed from the audit's first draft (QA remediation G, 2026-10-14). That
    draft opened the link and expected 0 rows. FULL ∧ ARCHIVE is an empty
    intersection that the register has no parameter for: `liik` names one record
    mode. So the only link that cannot contradict the 0 is no link at all. That
    was the first of the two fixes QA-STATS-08 itself proposed, and it is what
    every Statistika template already renders for an empty `drillthrough_url`.
    The assertion stays as strict as before. With a contradicting chip there
    must be no link. With an agreeing chip the link must keep the chip and open
    exactly the counted rows.
    """
    archive = compute(
        keys.ACTIVE_FULL_MATTERS,
        reporting_context(world.head, record_mode=RecordMode.ARCHIVE.value),
    )
    assert archive.value == 0
    assert archive.drillthrough_url == "", archive.drillthrough_url

    full = compute(
        keys.ACTIVE_FULL_MATTERS, reporting_context(world.head, record_mode=RecordMode.FULL.value)
    )
    assert full.value > 0
    assert "liik=FULL" in full.drillthrough_url
    client.force_login(world.head)
    assert register_total(client, full.drillthrough_url) == full.value, full.drillthrough_url
