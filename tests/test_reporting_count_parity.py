"""Statistics count distinct keys, once per page, and every number is unchanged (ENG-078).

`count()` was ``queryset.distinct().count()``: ``COUNT(*)`` over a
``SELECT DISTINCT`` of every Matter column, rebuilt for each metric that
shares a population — one identical statement ran eleven times on one page. It
now counts distinct primary keys and remembers each answer on the request's
`ReportingContext` while metrics are computed.

The contract is parity, so the first test asks every metric in the catalogue,
for every persona and across periods and filters, and compares the whole
result — value, population, eligibility, coverage, segments, distribution,
matrix and comparison — against the old spelling.
"""

from __future__ import annotations

import dataclasses
import importlib
import pkgutil
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.reporting import selectors as selector_package
from app.reporting import services
from app.reporting.metric_catalogue import CATALOGUE
from app.reporting.selectors import base

pytestmark = pytest.mark.django_db


def _old_count(queryset):
    """The spelling before ENG-078, kept as the parity oracle."""
    return queryset.distinct().count()


def _modules_importing_count() -> list[Any]:
    found = [base]
    for info in pkgutil.iter_modules(selector_package.__path__):
        module = importlib.import_module(f"{selector_package.__name__}.{info.name}")
        if getattr(module, "count", None) is base.count and module is not base:
            found.append(module)
    return found


def _normalised(result) -> Any:
    """Everything a reader can see of one answer, minus the moment it was asked."""
    data = dataclasses.asdict(result)
    data.pop("as_of", None)
    return data


def _everything(context) -> dict[str, Any]:
    return {key: _normalised(services.compute(key, context)) for key in sorted(CATALOGUE)}


@pytest.fixture
def old_counting(monkeypatch):
    def use_old():
        for module in _modules_importing_count():
            monkeypatch.setattr(module, "count", _old_count)

    return use_old


def _contexts(world, reporting_context):
    this_year = str(world.today.year)
    return [
        ("martin koik", reporting_context(world.martin)),
        ("martin year", reporting_context(world.martin, period=this_year)),
        ("martin last year", reporting_context(world.martin, period=str(world.today.year - 1))),
        ("head koik", reporting_context(world.head)),
        ("reader koik", reporting_context(world.reader)),
        ("admin koik", reporting_context(world.admin)),
        ("sandra owner", reporting_context(world.sandra, owner_id=world.sandra.pk)),
        ("head area", reporting_context(world.head, policy_area_key=world.area_tax.key)),
        ("head stage", reporting_context(world.head, stage_key=world.stage.key)),
        ("head tag", reporting_context(world.head, tag_key=world.tag.key)),
    ]


def test_every_metric_returns_exactly_what_it_did(
    world, responsibility_world, archive_world, reporting_context, old_counting
):
    # `responsibility_world` and `archive_world` add their records to `world`.
    contexts = _contexts(world, reporting_context)
    after = {label: _everything(context) for label, context in contexts}

    old_counting()
    # Fresh contexts: the new ones remembered their answers.
    before = {label: _everything(context) for label, context in _contexts(world, reporting_context)}

    for label in after:
        for key in sorted(CATALOGUE):
            assert after[label][key] == before[label][key], (label, key)


def test_a_join_still_cannot_inflate_a_count(world):
    """Distinct keys over a fan-out join count each Matter once."""
    from app.matters.models import Matter

    fanned = Matter.objects.filter(documents__isnull=False)
    assert fanned.count() > fanned.distinct().count(), "no fan-out; this test tests nothing"
    assert base.count(fanned) == fanned.distinct().count()


def test_a_page_asks_each_population_once(world, reporting_context):
    """No identical COUNT statement twice on one page, and fewer statements."""
    context = reporting_context(world.head)
    with CaptureQueriesContext(connection) as queries:
        services.matters_page(context)

    counts = [q["sql"] for q in queries.captured_queries if q["sql"].startswith("SELECT COUNT(")]
    assert len(counts) == len(set(counts)), "an identical count ran twice"
    assert not any("SELECT DISTINCT" in sql and '"matters_matter"."title"' in sql for sql in counts)


def test_the_memo_does_not_outlive_its_context(world, reporting_context):
    first = reporting_context(world.head)
    services.compute(sorted(CATALOGUE)[0], first)
    second = reporting_context(world.head)
    assert not any(key.startswith("count:") for key in second._shared)
    assert base._ANSWERING.get() is None
