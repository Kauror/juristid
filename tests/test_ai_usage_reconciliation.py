"""One independently calculated ledger, read every way the pages read it (r98 final, items 3 and 5).

The fixture below is small enough to total by hand; every expected number in this file was worked
out on paper from the rows, not by calling `summarise` twice. Prices: USD 4 in / 20 out / 0.2
cache-read per million tokens, 2.0 USD per EUR (so EUR = USD / 2).

  A  watch_findings  D1 10:00      1 attempt  1,000,000 in / 100,000 out        USD 6   = 3.0 EUR
  B  candidates      D1 12:00      529 (no usage), then 500,000 / 50,000        USD 3   = 1.5 EUR
  C  candidates      D2 09:00      FAILED, 3 timeouts, no usage                 unknown ×3
  D  watch_findings  D1 23:59:30   429 at 23:59:31, then at D2 00:00:05 answered
                                   200,000 / 10,000                             USD 1   = 0.5 EUR
                                   (starts on D1, finishes on D2: counted on D1)
  E  watch_findings  D2 11:00      FAILED, answered by an UNPRICED model        unknown (no price)
  F  candidates      D2 12:00      served by a provider fallback                unknown (fallback)

  known cost 5.0 EUR; 10 attempts, 7 of them with unknown cost.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.ai_usage import report
from app.ai_usage.models import (
    AiAttempt,
    AiFxRate,
    AiLedgerGap,
    AiOperation,
    AiPrice,
    AttemptCoverage,
    Outcome,
    UsageStatus,
)
from app.haldus import ai_usage_views
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified

pytestmark = pytest.mark.django_db

M = "claude-opus-5-5"
D1 = dt.date(2026, 11, 10)
D2 = dt.date(2026, 11, 11)
TZ = report.TALLINN


def at(day: dt.date, hh: int, mm: int, ss: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm, ss), tzinfo=TZ)


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _op(service: str, when: dt.datetime, outcome: str, classified: int | None = None, **kw: Any):
    return AiOperation.objects.create(
        service=service,
        requested_model=M,
        started_at=when,
        finished_at=when + dt.timedelta(seconds=30),
        outcome=outcome,
        items_presented=classified,
        items_classified=classified,
        **kw,
    )


def _att(op: AiOperation, seq: int, when: dt.datetime, outcome: str = "OK", **kw: Any):
    fields: dict[str, Any] = {
        "operation": op,
        "sequence": seq,
        "started_at": when,
        "outcome": outcome,
        "requested_model": M,
        "retry_class": "final",
        "usage_status": UsageStatus.UNAVAILABLE,
    }
    if "input_tokens" in kw:
        fields.update(
            usage_status=UsageStatus.MEASURED,
            actual_model=M,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
    fields.update(kw)
    return AiAttempt.objects.create(**fields)


@pytest.fixture
def ledger() -> dict[str, AiOperation]:
    AiPrice.objects.create(
        model=M,
        currency="USD",
        input_per_mtok=Decimal("4"),
        output_per_mtok=Decimal("20"),
        cache_read_per_mtok=Decimal("0.2"),
        effective_from=dt.date(2026, 11, 1),
        source_url="https://example.test/p",
        verified_on=dt.date(2026, 11, 9),
    )
    AiFxRate.objects.create(
        currency="USD", on_date=dt.date(2026, 11, 9), per_eur=Decimal("2"), source="synthetic"
    )
    a = _op("watch_findings", at(D1, 10, 0), Outcome.SUCCEEDED, 10)
    _att(a, 1, at(D1, 10, 0), input_tokens=1_000_000, output_tokens=100_000)
    b = _op("candidates", at(D1, 12, 0), Outcome.SUCCEEDED_AFTER_RETRY, 5)
    _att(b, 1, at(D1, 12, 0), "HTTP_ERROR", retry_class="retried", http_status=529)
    _att(b, 2, at(D1, 12, 1), input_tokens=500_000, output_tokens=50_000)
    c = _op("candidates", at(D2, 9, 0), Outcome.FAILED)
    for n in (1, 2, 3):
        _att(c, n, at(D2, 9, n), "TIMEOUT", retry_class="retried" if n < 3 else "final")
    d = _op("watch_findings", at(D1, 23, 59, 30), Outcome.SUCCEEDED_AFTER_RETRY, 4)
    _att(d, 1, at(D1, 23, 59, 31), "RATE_LIMITED", retry_class="retried", http_status=429)
    _att(d, 2, at(D2, 0, 0, 5), input_tokens=200_000, output_tokens=10_000)
    e = _op("watch_findings", at(D2, 11, 0), Outcome.FAILED)
    _att(
        e,
        1,
        at(D2, 11, 0),
        input_tokens=1000,
        output_tokens=10,
        actual_model="claude-unpriced",
        requested_model="claude-unpriced",
    )
    f = _op("candidates", at(D2, 12, 0), Outcome.SUCCEEDED, 3)
    _att(f, 1, at(D2, 12, 0), input_tokens=1000, output_tokens=10, fallback_observed=True)
    return {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f}


def _sum(**filters: Any) -> report.Summary:
    return report.summarise(D1, D2, **filters)


def test_the_whole_period_matches_the_hand_calculation(ledger):
    s = _sum()
    t = s.totals
    assert (t.operations, t.attempts) == (6, 10)
    assert t.cost_eur == Decimal("5.0")
    assert t.unknown_cost_attempts == 7
    assert t.unknown_reasons == {"no_usage": 5, "no_price": 1, "fallback": 1}
    assert (t.input_tokens, t.output_tokens) == (
        1_000_000 + 500_000 + 200_000 + 1000 + 1000,
        100_000 + 50_000 + 10_000 + 10 + 10,
    )
    assert t.complete is False  # unknown cost: a known subtotal, not a total
    assert t.items_classified == 10 + 5 + 4 + 3


def test_a_request_crossing_midnight_is_counted_once_on_the_day_it_started(ledger):
    s = _sum()
    d1, d2 = s.by_day[D1], s.by_day[D2]
    assert (d1.operations, d1.attempts, d1.cost_eur) == (
        3,
        5,
        Decimal("5.0"),
    )  # A, B, D (+ both of D's attempts)
    assert (d2.operations, d2.attempts, d2.cost_eur) == (3, 5, Decimal("0"))  # C, E, F
    assert d1.operations + d2.operations == s.totals.operations
    assert d1.attempts + d2.attempts == s.totals.attempts


def test_service_filter(ledger):
    s = _sum(service="candidates")
    assert (s.totals.operations, s.totals.attempts, s.totals.cost_eur) == (3, 6, Decimal("1.5"))
    assert set(s.by_service) == {"candidates"}


def test_requested_versus_actual_model(ledger):
    # counted under the model that answered, else the one requested: A, B(both), C(3), D(2), F
    on_m = _sum(model=M)
    assert (on_m.totals.operations, on_m.totals.attempts) == (5, 9)
    assert on_m.totals.cost_eur == Decimal("5.0")
    assert set(on_m.by_model) == {M}
    other = _sum(model="claude-unpriced")
    assert (other.totals.operations, other.totals.attempts) == (1, 1)
    assert other.unpriced_models == ["claude-unpriced"]
    unknown = _sum(model="never-heard-of-it")
    assert (unknown.totals.operations, unknown.totals.attempts) == (0, 0)


def test_outcome_filter_is_the_logical_outcome_everywhere(ledger):
    failed = _sum(outcome="FAILED")
    assert (failed.totals.operations, failed.totals.attempts) == (2, 4)  # C and E
    retried = _sum(outcome="SUCCEEDED_AFTER_RETRY")
    assert (retried.totals.operations, retried.totals.attempts) == (2, 4)  # B and D
    assert retried.totals.cost_eur == Decimal("2.0")
    assert retried.totals.retried_attempts == 2 and retried.by_outcome == {
        "SUCCEEDED_AFTER_RETRY": 2
    }


def test_combined_filters_agree(ledger):
    s = _sum(service="candidates", outcome="FAILED", model=M)
    assert (s.totals.operations, s.totals.attempts) == (1, 3)
    assert set(s.by_service) == {"candidates"} and set(s.by_model) == {M}
    assert s.totals.cost_eur == Decimal("0")


def test_provider_fallback_cost_is_unknown_with_its_reason(ledger):
    s = _sum(service="candidates", outcome="SUCCEEDED")  # only F
    assert s.totals.unknown_reasons == {"fallback": 1}
    assert s.totals.complete is False and s.totals.cost_eur == Decimal("0")


def test_a_clean_filtered_slice_is_complete_and_its_cost_per_item_is_exact(ledger):
    s = _sum(service="watch_findings", outcome="SUCCEEDED")  # only A
    assert s.totals.complete
    assert s.totals.cost_eur == Decimal("3.0") and s.totals.cost_per_item == Decimal("0.3")


def test_detail_rows_are_exactly_the_summary_population(ledger):
    for filters in ({}, {"service": "candidates"}, {"model": M}, {"outcome": "FAILED"}):
        s = _sum(**filters)
        rows = report.attempts_of(s, filters.get("model", ""))
        assert rows.count() == s.totals.attempts, filters


# -- one completeness rule ---------------------------------------------------------------------


def test_an_interrupted_request_without_an_attempt_makes_it_incomplete(ledger):
    AiOperation.objects.create(
        service="watch_findings",
        requested_model=M,
        started_at=at(D2, 15, 0),
        expected_end_at=at(D2, 15, 10),
        outcome=Outcome.IN_PROGRESS,
    )
    t = _sum(service="watch_findings", outcome="INTERRUPTED").totals
    assert (t.ops_interrupted, t.ops_unobserved) == (1, 1)
    assert not t.complete and "ops_unobserved" in t.incomplete


def test_a_running_request_unwatched_attempts_and_gaps_each_make_it_incomplete(ledger):
    now = timezone.now()
    running = AiOperation.objects.create(
        service="candidates",
        requested_model=M,
        started_at=now,
        expected_end_at=now + dt.timedelta(minutes=30),
        outcome=Outcome.IN_PROGRESS,
    )
    day = report.local_date(now)
    t = report.summarise(day, day).totals
    assert t.ops_running == 1 and not t.complete
    running.delete()
    AiOperation.objects.filter(pk=ledger["a"].pk).update(
        attempt_coverage=AttemptCoverage.UNAVAILABLE
    )
    only_a = _sum(service="watch_findings", outcome="SUCCEEDED").totals
    assert only_a.ops_unwatched == 1 and not only_a.complete
    AiOperation.objects.filter(pk=ledger["a"].pk).update(attempt_coverage=AttemptCoverage.OBSERVED)
    assert _sum(service="watch_findings", outcome="SUCCEEDED").totals.complete
    AiLedgerGap.objects.create(at=at(D1, 10, 5), service="watch_findings", kind="write_failed")
    gap = _sum(service="watch_findings", outcome="SUCCEEDED")
    assert gap.totals.gaps == 1 and not gap.totals.complete
    assert not gap.by_day[D1].complete and not gap.by_service["watch_findings"].complete


def test_an_old_rate_and_an_old_price_are_flagged_not_silently_used(ledger):
    AiFxRate.objects.all().delete()
    AiFxRate.objects.create(
        currency="USD", on_date=dt.date(2026, 10, 1), per_eur=Decimal("2"), source="old"
    )
    t = _sum(service="watch_findings", outcome="SUCCEEDED").totals
    assert t.cost_eur == Decimal("3.0") and t.stale_rate == 1 and not t.complete
    AiFxRate.objects.all().delete()
    AiFxRate.objects.create(
        currency="USD", on_date=dt.date(2026, 11, 9), per_eur=Decimal("2"), source="ok"
    )
    AiPrice.objects.update(verified_on=dt.date(2026, 3, 1))
    t = _sum(service="watch_findings", outcome="SUCCEEDED").totals
    assert t.stale_price == 1 and not t.complete


def test_a_period_before_the_ledger_began_is_not_a_total(ledger):
    early = report.summarise(D1 - dt.timedelta(days=5), D2).totals
    assert early.ledger_starts_late == 1 and not early.complete
    assert report.summarise(D1, D2, service="watch_findings", outcome="SUCCEEDED").totals.complete


def test_a_missing_exchange_rate_makes_the_cost_unknown_not_zero(ledger):
    AiFxRate.objects.all().delete()
    t = _sum(service="watch_findings", outcome="SUCCEEDED").totals
    assert t.cost_eur == Decimal("0") and t.unknown_reasons == {"no_rate": 1}


# -- budget: accounting health apart from policy ---------------------------------------------


@pytest.fixture
def month_of(ledger):
    return D2  # the ledger sits in November 2026


def test_without_thresholds_the_policy_is_none_and_health_is_not_assumed_ok(ledger, month_of):
    b = report.budget(month_of)
    assert b.level == "none" and b.complete is False and b.known_eur == Decimal("5.0")


def test_a_known_subtotal_below_the_limit_proves_nothing(ledger, month_of, settings):
    settings.AI_BUDGET_WARN_EUR = "100"
    b = report.budget(month_of)
    assert b.level == "unproven" and not b.complete


def test_a_known_subtotal_that_already_reaches_the_limit_is_proof(ledger, month_of, settings):
    settings.AI_BUDGET_WARN_EUR = "4"
    settings.AI_BUDGET_ESCALATE_EUR = "9"
    assert report.budget(month_of).level == "warn"
    settings.AI_BUDGET_ESCALATE_EUR = "5"
    assert report.budget(month_of).level == "escalate"


def test_a_complete_total_below_the_limit_is_ok(settings):
    AiPrice.objects.create(
        model=M,
        currency="EUR",
        input_per_mtok=4,
        output_per_mtok=20,
        cache_read_per_mtok=Decimal("0.2"),
        effective_from=dt.date(2026, 11, 1),
        source_url="https://example.test/p",
        verified_on=dt.date(2026, 11, 9),
    )
    first = _op("watch_findings", at(dt.date(2026, 11, 1), 8, 0), Outcome.SUCCEEDED, 1)
    _att(first, 1, at(dt.date(2026, 11, 1), 8, 0), input_tokens=100_000, output_tokens=1000)
    op = _op("watch_findings", at(D2, 10, 0), Outcome.SUCCEEDED, 1)
    _att(op, 1, at(D2, 10, 0), input_tokens=100_000, output_tokens=1000)
    settings.AI_BUDGET_WARN_EUR = "10"
    b = report.budget(D2)
    assert b.complete and b.level == "ok"


# -- the pages --------------------------------------------------------------------------------


@pytest.fixture
def admin(client):
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    return client


RANGE = {"periood": "custom", "alates": D1.isoformat(), "kuni": D2.isoformat()}


def test_the_page_says_incomplete_and_never_just_ok(ledger, admin):
    page = admin.get(reverse("haldus:ai_usage"), RANGE).content.decode()
    assert "Arvestus puudulik" in page and "Arvestus täielik" not in page
    assert "Hoiatuspiire pole seatud" in page
    assert "vähemalt" in page  # the cost is shown as a lower bound


def test_the_page_for_a_clean_slice_says_complete(ledger, admin):
    page = admin.get(
        reverse("haldus:ai_usage"), {**RANGE, "teenus": "watch_findings", "tulemus": "SUCCEEDED"}
    )
    assert "Arvestus täielik" in page.content.decode()


def test_the_filters_agree_between_totals_detail_rows_and_the_export(ledger, admin):
    params = {**RANGE, "teenus": "candidates", "tulemus": "SUCCEEDED_AFTER_RETRY", "mudel": M}
    page = admin.get(reverse("haldus:ai_usage"), params)
    summary = report.summarise(
        D1, D2, service="candidates", outcome="SUCCEEDED_AFTER_RETRY", model=M
    )
    assert summary.totals.attempts == 2  # B's two attempts
    assert page.context["page"].paginator.count == summary.totals.attempts
    export = admin.get(reverse("haldus:ai_usage_csv"), params).content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(export), delimiter=";"))
    assert len(rows) - 1 == summary.totals.attempts  # header + one row per attempt


def test_charts_are_made_from_the_same_daily_totals(ledger, admin):
    page = admin.get(reverse("haldus:ai_usage"), RANGE)
    charts = {c["key"]: c for c in page.context["charts"]}
    s = _sum()
    assert [p.value for p in charts["volume"]["trend"].points] == [
        t.attempts for t in s.by_day.values()
    ]
    assert [p.value for p in charts["failures"]["trend"].points] == [
        t.failed_attempts for t in s.by_day.values()
    ]
    assert [p.value for p in charts["cost"]["trend"].points] == [
        int(t.cost_eur * 100) for t in s.by_day.values()
    ]


def test_daily_drill_down_links_keep_the_applicable_filters(ledger, admin):
    page = admin.get(
        reverse("haldus:ai_usage"),
        {**RANGE, "teenus": "candidates", "mudel": M, "tulemus": "FAILED"},
    )
    links = [row[3] for row in page.context["day_rows"]]
    assert links and all(
        "teenus=candidates" in link
        and f"mudel={M}" in link
        and "tulemus=FAILED" in link
        and "periood=custom" in link
        for link in links
    )
    one_day = admin.get(reverse("haldus:ai_usage"), {**RANGE, "alates": D2.isoformat()})
    assert set(one_day.context["summary"].by_day) == {D2}


def test_pagination_keeps_the_filters_and_covers_every_row(ledger, admin, monkeypatch):
    monkeypatch.setattr(ai_usage_views, "PAGE_SIZE", 4)
    params = {**RANGE, "teenus": "candidates"}
    first = admin.get(reverse("haldus:ai_usage"), params)
    assert first.context["page"].paginator.num_pages == 2
    second = admin.get(reverse("haldus:ai_usage"), {**params, "leht": 2})
    seen = [r["attempt"].pk for r in first.context["rows"]] + [
        r["attempt"].pk for r in second.context["rows"]
    ]
    assert len(seen) == len(set(seen)) == 6
    assert "teenus=candidates" in second.content.decode()


def test_the_export_discloses_its_row_limit(ledger, admin, monkeypatch):
    monkeypatch.setattr(ai_usage_views, "ROW_LIMIT", 3)
    text = admin.get(reverse("haldus:ai_usage_csv"), RANGE).content.decode("utf-8")
    assert text.strip().splitlines()[-1].strip('"').startswith("KATKESTATUD: 3 rida 10-st")
    full = admin.get(reverse("haldus:ai_usage_csv"), RANGE)
    assert "attachment" in full["Content-Disposition"]
