"""Work victories reach an AI client exactly as the product shows them (MCPSEC-14).

The Teema page, the register, Statistika and search read `VISIBLE_VICTORY_STATUS`
(a confirmed `Töövõit`) alone; a candidate is a proposal read only in the review
block, and NOT_REALIZED is a claim that did not hold. `fetch win:<id>` and
`get_matter_activity` follow the same rule, and what they return is confirmed.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from app.intelligence.enums import WorkVictoryStatus
from app.mcp_server import readers
from app.mcp_server.readers import NotFound
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist)


def _victory(matter, status, title):
    confirmed = status != WorkVictoryStatus.CANDIDATE
    return factories.WorkVictoryFactory(
        matter=matter,
        status=status,
        title=title,
        period_date=timezone.localdate().replace(month=1, day=1),
        period_end=timezone.localdate().replace(month=12, day=31),
        confirmed_at=timezone.now() if confirmed else None,
    )


@pytest.mark.parametrize("status", [WorkVictoryStatus.CANDIDATE, WorkVictoryStatus.NOT_REALIZED])
def test_an_unshown_victory_is_not_found(matter, specialist, status):
    record = _victory(matter, status, "Kandidaat")
    with pytest.raises(NotFound):
        readers.fetch_source(specialist, f"win:{record.pk}")


def test_a_confirmed_victory_is_fetched_as_confirmed(matter, specialist):
    record = _victory(matter, WorkVictoryStatus.CONFIRMED, "Päris võit")
    answer = readers.fetch_source(specialist, f"win:{record.pk}")
    assert answer["metadata"]["certainty"] == "confirmed"
    assert answer["metadata"]["confirmed"] is True


def test_activity_shows_confirmed_victories_only(matter, specialist):
    shown = _victory(matter, WorkVictoryStatus.CONFIRMED, "Päris võit")
    _victory(matter, WorkVictoryStatus.NOT_REALIZED, "Ei realiseerunud")
    _victory(matter, WorkVictoryStatus.CANDIDATE, "Kandidaat")
    answer = readers.matter_activity(specialist, str(matter.pk), kind="events")
    victories = [item for item in answer["items"] if item["kind"] == "work_victory"]
    assert [item["id"] for item in victories] == [f"win:{shown.pk}"]
    assert victories[0]["date"]["certainty"] == "confirmed"
