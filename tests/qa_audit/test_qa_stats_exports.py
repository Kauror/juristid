"""QA audit 2026-10-14 (area `stats`): CSV exports against the on-screen population.

Probes (expected to pass): the Teemad CSV behind «Laadi alla CSV» holds the
same Matters as the `MATTERS_TOTAL` card and its register list, never a TEST
twin, and the restricted Matter only for an entitled reader; the submission
CSV holds exactly the submission list's rows.

Since docs/adr/0181 every authorized reader (READER included) gets the
restricted Matter too, and an administrator without a business role gets no row.
"""

from __future__ import annotations

import csv
import io

import pytest
from django.urls import reverse

from app.reporting import metric_catalogue as keys
from app.reporting.services import compute
from tests.test_reporting_drillthrough_data_scope import add_test_twins, register_total

pytestmark = pytest.mark.django_db


def _csv(client, slug: str, **query: str) -> list[list[str]]:
    response = client.get(reverse("reporting:export", kwargs={"slug": slug}), query)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    text = b"".join(response.streaming_content).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";"))[1:]


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
@pytest.mark.parametrize("period", ["kaesolev", "koik"])
def test_teemad_csv_is_the_matters_total_population(
    viewer_name, period, client, world, reporting_context
):
    add_test_twins(world)
    viewer = getattr(world, viewer_name)
    client.force_login(viewer)
    rows = _csv(client, "teemad", periood=period)
    titles = [row[1] for row in rows]
    result = compute(keys.MATTERS_TOTAL, reporting_context(viewer, period=period))

    assert len(rows) == result.value
    assert register_total(client, result.drillthrough_url) == result.value
    assert not [t for t in titles if t.endswith("(TEST)")]
    assert world.restricted.title in titles


@pytest.mark.parametrize("slug", ["teemad", "arvamused"])
def test_an_administrator_without_a_business_role_exports_no_row(slug, client, world):
    client.force_login(world.admin)
    assert _csv(client, slug, periood="koik") == []


@pytest.mark.parametrize("viewer_name", ["head", "reader"])
def test_arvamused_csv_is_the_submission_list(viewer_name, client, world):
    add_test_twins(world)
    viewer = getattr(world, viewer_name)
    client.force_login(viewer)
    listed = client.get(reverse("reporting:submissions"), {"periood": "koik"})
    titles_on_screen = sorted(s.title for s in listed.context["paginator"].object_list)
    titles_in_csv = sorted(row[1] for row in _csv(client, "arvamused", periood="koik"))
    assert titles_in_csv == titles_on_screen
    assert world.submissions["restricted"].title in titles_in_csv
