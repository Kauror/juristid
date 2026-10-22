"""`get_matter_activity` labels commencements and milestones for what they are (MCPSEC-04).

ADR 0185 §2: a date in the past is never reported as «entered into force», and
a cancelled, superseded, planned or estimated date is never a ``confirmed``
fact. The chronology (`app.matters.timeline`) writes «Jõustus» once a recorded
commencement date has passed — the browser's word, decided in docs/adr/0123 —
and its milestones carry no certainty of their own, so the MCP item derives
both from the record itself, exactly as `get_matter` does.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.utils import timezone

from app.intelligence.enums import FactStatus, ImportantDateKind
from app.mcp_server import readers
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

ORIGIN = "https://juristid.example.test"


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN


@pytest.fixture
def today() -> date:
    return timezone.localdate()


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, title="Pakendiseaduse muutmine")


def _items(user: Any, matter: Any, kind: str) -> list[dict[str, Any]]:
    answer = readers.matter_activity(user, str(matter.pk), kind="events")
    return [item for item in answer["items"] if item["kind"] == kind]


def test_a_past_exact_commencement_is_a_recorded_date_never_entered_into_force(
    matter, specialist, today
):
    record = factories.EffectiveDateFactory(
        matter=matter,
        date_value=today - timedelta(days=30),
        period_end=today - timedelta(days=30),
        description="Üleminekusäte",
    )
    [item] = _items(specialist, matter, "commencement")
    assert item["id"] == f"eff:{record.pk}"
    assert "Jõustus" not in item["text"]
    assert "entered into force" not in item["text"].replace("whether it actually entered", "")
    assert "Recorded commencement date" in item["text"]
    assert "does not record whether it actually entered into force" in item["text"]
    assert item["date"]["certainty"] == "confirmed"
    assert item["timing"] == "past"


def test_an_exact_commencement_today_or_ahead_is_scheduled(matter, specialist, today):
    # Drawn from its first day: a commencement ahead of today is on the strip,
    # not in Teema käik; today's is in the chronology.
    factories.EffectiveDateFactory(matter=matter, description="Põhiosa")
    [item] = _items(specialist, matter, "commencement")
    assert "Jõustub" not in item["text"] and "Jõustus" not in item["text"]
    assert item["date"]["certainty"] == "confirmed"
    assert item["timing"] in ("in_period", "upcoming")


def test_a_cancelled_or_superseded_commencement_is_previous(matter, specialist, today):
    for status in (FactStatus.CANCELLED, FactStatus.SUPERSEDED):
        factories.EffectiveDateFactory(
            matter=matter,
            status=status,
            date_value=today - timedelta(days=10),
            period_end=today - timedelta(days=10),
            description=f"Kehtetu {status}",
        )
    items = _items(specialist, matter, "commencement")
    assert len(items) == 2
    assert {item["date"]["certainty"] for item in items} == {"previous"}
    assert all("Jõustus" not in item["text"] for item in items)


def test_an_approximate_commencement_whose_period_ended_is_estimated(matter, specialist, today):
    first = today.replace(day=1)
    last_month = (first - timedelta(days=1)).replace(day=1)
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=last_month,
        period_end=first - timedelta(days=1),
        date_precision=DatePrecision.MONTH,
        description="Kuu täpsusega",
    )
    [item] = _items(specialist, matter, "commencement")
    assert item["date"]["certainty"] == "estimated"
    assert "Jõustus" not in item["text"]


def test_a_cancelled_important_date_is_previous_not_confirmed(matter, specialist, today):
    factories.ImportantDateFactory(
        matter=matter,
        title="Tühistatud istung",
        status=FactStatus.CANCELLED,
        date_value=today - timedelta(days=3),
        period_end=today - timedelta(days=3),
    )
    [item] = _items(specialist, matter, "important_date")
    assert item["date"]["certainty"] == "previous"


def test_a_past_estimated_or_planned_commencement_milestone_is_not_confirmed(
    matter, specialist, today
):
    factories.ImportantDateFactory(
        matter=matter,
        title="Allika hinnang",
        kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
        date_value=today - timedelta(days=5),
        period_end=today - timedelta(days=5),
    )
    factories.ImportantDateFactory(
        matter=matter,
        title="Eelnõus kavandatud",
        kind=ImportantDateKind.PLANNED_COMMENCEMENT,
        date_value=today - timedelta(days=6),
        period_end=today - timedelta(days=6),
    )
    labels = {
        item["text"].split("\n")[0]: item["date"]["certainty"]
        for item in _items(specialist, matter, "important_date")
    }
    assert labels == {"Allika hinnang": "estimated", "Eelnõus kavandatud": "planned"}


def test_activity_and_get_matter_agree_on_every_dated_record(matter, specialist, today):
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today - timedelta(days=40),
        period_end=today - timedelta(days=40),
        description="Vana",
    )
    factories.ImportantDateFactory(
        matter=matter,
        title="Möödunud tähtaeg",
        date_value=today - timedelta(days=2),
        period_end=today - timedelta(days=2),
    )
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))
    expected = {item["id"]: item["date"]["certainty"] for item in snapshot["commencements"]}
    expected |= {item["id"]: item["date"]["certainty"] for item in snapshot["past_dates"]}
    answer = readers.matter_activity(specialist, str(matter.pk), kind="events")
    found = {item["id"]: item["date"]["certainty"] for item in answer["items"] if item["id"]}
    assert {key: found[key] for key in expected} == expected
