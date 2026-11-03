"""The pending-review queue does not read every night's decision (review P2-5).

`lovable_observability._pending_fields` answers «what waits, since when, for
how many nights» on every `status()` call. Import-held fields (docs/adr/0202 §6)
are pending for as long as nobody resolves them, so a reading of every night's
row per field would grow without bound. These tests hold the bounded reading to
the answers of the original one (kept here as `_reference`), and its number of
queries to a constant as the nights grow. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import os
import time
import uuid
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_review
from app.legacy_import.lovable_sync import IMPORT_HELD, MAPPING_VERSION
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncTrigger,
)
from tests import factories

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 21, 15, tzinfo=dt.UTC)
SETTLED = (SyncOutcome.ALREADY_CONVERGED, SyncOutcome.APPLIED)


def _reference(states: list[LovableSyncState]) -> list[tuple[Any, ...]]:
    """The original reading: every night's row, oldest first, walked in Python."""
    candidates = []
    for state in states:
        observed = state.observed or {}
        held = obs.lovable_sync.held_by_import(state.applied)
        for name in obs.FIELDS:
            if name not in observed:
                continue
            applied = (state.applied.get(name) or {}).get("source")
            if name not in held and lovable_review._key(name, applied) == lovable_review._key(
                name, observed[name]
            ):
                continue
            candidates.append((state, name, observed[name]))
    rows: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for row in (
        LovableSyncDecision.objects.filter(state__in=states, field__in=obs.FIELDS)
        .exclude(run__trigger=SyncTrigger.CHECK)
        .order_by("run__started_at", "created_at")
        .values("state_id", "field", "outcome", "reason", "source", "run_id", "run__started_at")
    ):
        rows.setdefault((row["state_id"], row["field"]), []).append(row)
    out = []
    for state, name, observed in candidates:
        history = rows.get((state.pk, name), [])
        newest = history[-1] if history else None
        if newest is not None and newest["outcome"] in SETTLED:
            continue
        settled = [i for i, r in enumerate(history) if r["outcome"] in SETTLED]
        since = history[settled[-1] + 1 :] if settled else history
        want = lovable_review._key(name, observed)
        first = next((r for r in since if lovable_review._key(name, r["source"]) == want), None)
        if first is None and since:
            first = since[0]
        tail = since[since.index(first) :] if first is not None else []
        last_at = newest["run__started_at"] if newest is not None else None
        out.append(
            (
                str(state.pk),
                name,
                first["run__started_at"] if first is not None else last_at,
                last_at,
                len({r["run_id"] for r in tail}),
                newest["outcome"] if newest else None,
                newest["reason"] if newest else None,
            )
        )
    return sorted(out, key=str)


def _bounded(states: list[LovableSyncState]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            (
                str(p.state.pk),
                p.field,
                p.first_at,
                p.last_at,
                p.nights,
                (p.newest or {}).get("outcome"),
                (p.newest or {}).get("reason"),
            )
            for p in obs._pending_fields(states)
        ),
        key=str,
    )


def _world(n_states: int, n_nights: int, *, first_number: int = 700) -> list[LovableSyncState]:
    """``n_states`` Teemad in four shapes, each decided on every night:

    0. an import-held stage, unchanged in Lovable, REVIEW_REQUIRED/IMPORT_HELD;
    1. a title that converged mid-way and then changed again (pending since);
    2. an owner whose Lovable spelling changed case (the same name), after an
       earlier different name;
    3. a field whose newest decision settled it (not pending)."""
    runs = LovableSyncRun.objects.bulk_create(
        [
            LovableSyncRun(
                run_key=f"n{i}",
                trigger=SyncTrigger.SCHEDULED,
                mode=SyncMode.COMPARE,
                started_at=START + dt.timedelta(days=i),
                mapping_version=MAPPING_VERSION,
            )
            for i in range(n_nights)
        ]
    )
    check = LovableSyncRun.objects.create(
        run_key="check",
        trigger=SyncTrigger.CHECK,
        mode=SyncMode.COMPARE,
        started_at=START + dt.timedelta(days=n_nights),
        mapping_version=MAPPING_VERSION,
    )
    states = []
    for i in range(n_states):
        matter = factories.MatterFactory(reference_year=2026, reference_number=first_number + i)
        shape = i % 4
        applied: dict[str, Any] = {
            "stage": {"source": "Riigikogus"},
            "title": {"source": "Vana", "target": "Vana"},
            "owner": {"source": "Mari", "target": None},
        }
        observed: dict[str, Any] = {"stage": "Riigikogus", "title": "Vana", "owner": "Mari"}
        if shape == 0:
            applied["stage"]["import_held"] = True
        elif shape == 1:
            observed["title"] = "Uusim"
        elif shape == 2:
            observed["owner"] = "jaan näidis"
        else:
            observed["title"] = "Juba sama"
        states.append(
            LovableSyncState.objects.create(
                matter=matter,
                source_id=uuid.uuid4(),
                reference=f"2026_{first_number + i}",
                cohort="lovable-active",
                applied=applied,
                observed=observed,
            )
        )
    decisions = []
    half = n_nights // 2
    for state_index, state in enumerate(states):
        shape = state_index % 4
        for night, run in enumerate(runs):
            if shape == 0:
                row = ("stage", SyncOutcome.REVIEW_REQUIRED, IMPORT_HELD, "Riigikogus")
            elif shape == 1:
                if night < half:
                    row = ("title", SyncOutcome.REVIEW_REQUIRED, "TITLE_WAS_REVIEWED", "Vahepealne")
                elif night == half:
                    row = ("title", SyncOutcome.ALREADY_CONVERGED, "", "Vahepealne")
                else:
                    row = ("title", SyncOutcome.REVIEW_REQUIRED, "TITLE_WAS_REVIEWED", "Uusim")
            elif shape == 2:
                source = "Peeter" if night < half else "Jaan Näidis"
                row = ("owner", SyncOutcome.REVIEW_REQUIRED, "UNRESOLVED_OWNER", source)
            else:
                outcome = (
                    SyncOutcome.ALREADY_CONVERGED
                    if night == n_nights - 1
                    else SyncOutcome.REVIEW_REQUIRED
                )
                row = ("title", outcome, "", "Juba sama")
            name, outcome, reason, source = row
            decisions.append(
                LovableSyncDecision(
                    run=run,
                    state=state,
                    field=name,
                    outcome=outcome,
                    reason=reason,
                    source=source,
                )
            )
        # A check is never a night: its rows change nothing here.
        decisions.append(
            LovableSyncDecision(
                run=check,
                state=state,
                field="stage",
                outcome=SyncOutcome.ALREADY_CONVERGED,
                source="Riigikogus",
            )
        )
    LovableSyncDecision.objects.bulk_create(decisions)
    return list(LovableSyncState.objects.select_related("matter", "observed_run"))


def test_the_bounded_reading_gives_the_original_answers():
    states = _world(8, 12)
    expected = _reference(states)
    assert _bounded(states) == expected
    by_shape = {row[1]: row for row in expected}
    assert by_shape["stage"][4] == 12  # held every night, never settled
    assert by_shape["title"][4] == 5  # since the convergence on night 6
    assert by_shape["owner"][4] == 6  # since the first «Jaan Näidis», any case
    assert len(expected) == 6  # shape 3 settled: not pending


def test_the_number_of_queries_does_not_grow_with_the_nights():
    def queries(nights: int) -> int:
        LovableSyncDecision.objects.all().delete()
        LovableSyncState.objects.all().delete()
        LovableSyncRun.objects.all().delete()
        states = _world(8, nights, first_number=700 + nights * 10)
        with CaptureQueriesContext(connection) as captured:
            obs._pending_fields(states)
        return len(captured.captured_queries)

    assert queries(3) == queries(30) <= 4


@pytest.mark.skipif(
    not os.environ.get("JURISTID_LOVABLE_SCALE"), reason="scale measurement, run on demand"
)
def test_scale_250_teemad_by_30_nights():  # pragma: no cover - measurement only
    # JURISTID_LOVABLE_SCALE=<nights> (any non-number: 30).
    raw = os.environ.get("JURISTID_LOVABLE_SCALE", "")
    states = _world(250, int(raw) if raw.isdigit() else 30)
    started = time.perf_counter()
    bounded = _bounded(states)
    bounded_s = time.perf_counter() - started
    started = time.perf_counter()
    reference = _reference(states)
    reference_s = time.perf_counter() - started
    assert bounded == reference
    print(f"\nbounded {bounded_s:.3f}s, every-night reading {reference_s:.3f}s")
