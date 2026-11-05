"""Agent E (independent verifier): query counts and timings, base vs candidate.

Runs unchanged on 44c65121 and on the candidate (URLs only, no new API). For
each history shape it measures the Teema page, the «Seotud materjalid»
fragment and the suggestions panel (`?avatud=1`), each on a fresh request:
query count with `CaptureQueriesContext`, and the median wall time of five
runs. Results are printed and, when ``PA_E_PERF_OUT`` is set, appended there
as JSON lines. The only assertion is a loose bound so a run never hides a
runaway; the comparison is in the round report. Synthetic data only.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from itertools import pairwise
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.matters.models import Matter
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
_N = [0]
SHAPES = ("none", "direct", "four_stages", "converging3", "deep30")


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


def _teema(owner: Any) -> Matter:
    _N[0] += 1
    return factories.MatterFactory(
        owner=owner,
        reference_year=2035,
        reference_number=_N[0],
        title=f"Pakendiseaduse muutmine osa {_N[0]}",
    )


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _shape(name: str, owner: Any) -> Matter:
    if name == "none":
        return _teema(owner)
    if name == "converging3":
        viewed = _teema(owner)
        for _ in range(3):
            _continues(_teema(owner), viewed)
        return viewed
    n = {"direct": 2, "four_stages": 4, "deep30": 30}[name]
    stages = [_teema(owner) for _ in range(n)]
    for earlier, later in pairwise(stages):
        _continues(earlier, later)
    return stages[-1]


def _measure(client: Any, url: str, **extra: Any) -> tuple[int, float]:
    client.get(url, **extra)  # warm templates and caches
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url, **extra)
    # Read now: every later request clears the log (`reset_queries`).
    count = len(queries.captured_queries)
    assert response.status_code == 200, url
    times = []
    for _ in range(5):
        start = time.perf_counter()
        client.get(url, **extra)
        times.append((time.perf_counter() - start) * 1000)
    return count, round(statistics.median(times), 1)


@pytest.mark.parametrize("shape", SHAPES)
def test_e_measure(client, specialist, shape):
    viewed = _shape(shape, specialist)
    for _ in range(5):  # a few unrelated Teemad for the suggestion pool
        _teema(specialist)
    client.force_login(specialist)
    section = reverse("related_materials:section", kwargs={"pk": viewed.pk})
    row = {
        "shape": shape,
        "page": _measure(client, reverse("matters:matter_detail", kwargs={"pk": viewed.pk})),
        "fragment": _measure(client, section, **HX),
        "suggestions": _measure(client, section + "?avatud=1", **HX),
    }
    print(json.dumps(row))
    out = os.environ.get("PA_E_PERF_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    assert row["page"][0] < 400
