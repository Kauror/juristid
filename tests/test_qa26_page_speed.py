"""Quality audit 2026-11, package G: the Lovable and OneNote pages' cost.

* QA26-PERF-01 — «Valmisolek» (Uuendused → Lovable, Haldus → Lovable) planned
  every synchronised Teema with ~10 queries each: 24 s and 24,400 statements on
  production's 2,370 Teemad. It now reads Juristid's values in bulk.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import LovableSyncRun, LovableSyncState, SyncMode
from tests import factories
from tests.lovable_observability_support import Pilot, control, night_at

pytestmark = pytest.mark.django_db

LATER = night_at(3) + dt.timedelta(hours=2)


@pytest.fixture
def reader():
    return factories.UserFactory(display_name="Lugeja Näidis")


def _world(size: int, base: int) -> Pilot:
    pilot = Pilot()
    for number in range(base + 1, base + size + 1):
        pilot.add(number)
    pilot.night(night_at(0))
    for number in range(base + 1, base + size + 1):
        pilot.edit(f"2026_{number}", hetkeseis="Valitsuses", jargmiseks=f"Uus {number}")
    pilot.night(night_at(1))
    return pilot


def _queries(call) -> int:
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries.captured_queries)


def test_readiness_asks_nothing_per_teema(reader):
    """PERF-01: the plan rows of 20 Teemad cost what those of 3 cost."""
    control(SyncMode.COMPARE)
    _world(3, 800)
    small_answer = obs.readiness(reader, now=LATER)
    small = _queries(lambda: obs.readiness(reader, now=LATER))
    LovableSyncState.objects.all().delete()
    LovableSyncRun.objects.all().delete()
    _world(20, 900)
    large_answer = obs.readiness(reader, now=LATER)
    large = _queries(lambda: obs.readiness(reader, now=LATER))
    # The simulation really planned every Teema (not a vacuous constant).
    assert sum(small_answer.simulation.values()) > 0
    assert sum(large_answer.simulation.values()) > sum(small_answer.simulation.values())
    assert large - small <= 3, (small, large)


def test_conditional_values_bulk_is_each_teemas_own(reader):
    """The bulk reader gives every Teema its own values, never a neighbour's."""
    from app.intelligence.enums import EffectiveDateKind, FactStatus
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.matters.models import MatterResponseDeadline

    control(SyncMode.COMPARE)
    pilot = _world(4, 700)
    states = list(LovableSyncState.objects.select_related("matter").order_by("reference"))
    first, second, third, _fourth = (s.matter for s in states)
    day = night_at(3).date()
    MatterEffectiveDate.objects.create(
        matter=first,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=day,
        period_end=day,
        status=FactStatus.ACTIVE,
    )
    MatterImportantDate.objects.create(
        matter=second,
        kind="PLANNED_COMMENCEMENT",
        date_value=night_at(4).date(),
        period_end=night_at(4).date(),
        title="Kavandatud jõustumine",
        status=FactStatus.ACTIVE,
        source_text=lovable_sync.LOVABLE_COMMENCEMENT_MARK,
    )
    MatterResponseDeadline.objects.create(
        matter=third, deadline=night_at(5).date(), outcome="ANSWERED"
    )
    third.superseded_by = second
    third.save(update_fields=["superseded_by"])
    del pilot
    matters = [s.matter for s in LovableSyncState.objects.select_related("matter")]
    bulk = lovable_sync.conditional_values_bulk(matters)
    for matter in matters:
        assert bulk[matter.pk] == lovable_sync.conditional_values(matter), matter.pk
    assert bulk[first.pk]["commencements"]
    assert bulk[second.pk]["lovable_commencements"]
    assert not bulk[first.pk]["lovable_commencements"]
    assert bulk[third.pk]["request_history"] == 1 and bulk[first.pk]["request_history"] == 0
    assert bulk[third.pk][lovable_sync.CONTINUATION] == [
        str(LovableSyncState.objects.get(matter=second).source_id)
    ]
    assert bulk[first.pk][lovable_sync.CONTINUATION] == []
