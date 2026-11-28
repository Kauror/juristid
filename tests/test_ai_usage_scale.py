"""Representative-scale measurements of the AI ledger (r98 final, item 11). NOT part of routine CI.

The routine tests (`test_ai_usage_performance.py`, `test_ai_usage_concurrency.py`) use small
fixtures so a runner stays inside its time limit. This module runs the same questions at the
sizes the ledger can actually reach and prints the numbers (`pytest -s`); it is skipped unless
`R98_SCALE=1`, because it takes minutes and its timings depend on the machine. Every number the
readiness report and ADR 0236 quote as «measured at scale» comes from this file, with the date
and the dataset size printed beside it.

    R98_SCALE=1 uv run pytest tests/test_ai_usage_scale.py -s -q
"""

from __future__ import annotations

import datetime as dt
import os
import time
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.ai_usage import report
from app.ai_usage.models import AiAttempt, AiOperation, AttemptOutcome, Outcome, UsageStatus
from app.register_watch import ai_call
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified
from tests.test_register_ai_call_characterization import FakeClient, _text, response

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not os.environ.get("R98_SCALE"), reason="set R98_SCALE=1 to measure"),
]


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _fill(days: int, per_day: int, attempts_each: int = 1) -> int:
    start = timezone.now() - dt.timedelta(days=days)
    operations, attempts = [], []
    for d in range(days):
        for n in range(per_day):
            at = start + dt.timedelta(days=d, minutes=n)
            op = AiOperation(
                id=uuid.uuid4(),
                service=("watch_findings", "candidates", "government_agenda")[n % 3],
                requested_model="claude-opus-5-5",
                started_at=at,
                finished_at=at,
                outcome=Outcome.SUCCEEDED,
                items_presented=10,
                items_classified=10,
            )
            operations.append(op)
            for seq in range(1, attempts_each + 1):
                attempts.append(
                    AiAttempt(
                        operation=op,
                        sequence=seq,
                        started_at=at,
                        outcome=AttemptOutcome.OK,
                        requested_model="claude-opus-5-5",
                        actual_model="claude-opus-5-5",
                        usage_status=UsageStatus.MEASURED,
                        input_tokens=5000,
                        output_tokens=800,
                        headers_ms=4000 + n,
                        retry_class="final",
                    )
                )
    AiOperation.objects.bulk_create(operations, batch_size=2000)
    AiAttempt.objects.bulk_create(attempts, batch_size=2000)
    return len(attempts)


def _one_request(service: str = "watch_findings") -> None:
    ai_call.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system="s",
        prompt=lambda: "p",
        schema={},
        max_tokens=1,
        timeout=5.0,
        key=lambda row: row["id"],
        usage=ai_call.Usage(),
        client=FakeClient(lambda r: response(_text([{"id": "a"}]))),
        service=service,
        presented=1,
    )


def test_scale_page_summary_and_storage(client):
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    print(f"\nR98 SCALE {dt.date.today()} on this machine")
    rows = 0
    for days, per_day in ((365, 3), (365, 30), (1825, 30)):  # expected; 10x; 5 years at 10x
        AiAttempt.objects.all().delete()
        AiOperation.objects.all().delete()
        rows = _fill(days, per_day)
        params = {"periood": "custom", "alates": "2000-01-01", "kuni": "2099-12-31"}
        counter = []
        with connection.execute_wrapper(lambda ex, sql, *a: (counter.append(sql), ex(sql, *a))[1]):
            started = time.perf_counter()
            page = client.get(reverse("haldus:ai_usage"), params)
            page_ms = (time.perf_counter() - started) * 1000
        assert page.status_code == 200
        started = time.perf_counter()
        summary = report.summarise(dt.date(2000, 1, 1), dt.date(2099, 12, 31))
        summarise_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        csv_page = client.get(reverse("haldus:ai_usage_csv"), params)
        csv_ms = (time.perf_counter() - started) * 1000
        with connection.cursor() as cursor:
            cursor.execute(
                "select pg_total_relation_size('ai_usage_aiattempt'), "
                "pg_total_relation_size('ai_usage_aioperation')"
            )
            attempt_bytes, operation_bytes = cursor.fetchone()
        print(
            f"  {days} days x {per_day}/day = {rows} attempts: page {page_ms:.0f} ms "
            f"({len(counter)} queries), summarise {summarise_ms:.0f} ms, "
            f"csv (<=20000 rows) {csv_ms:.0f} ms [{len(csv_page.content) // 1024} KiB], "
            f"storage {attempt_bytes / rows:.0f} B/attempt + {operation_bytes / rows:.0f} B/operation"
        )
        assert summary.totals.attempts == rows
    # annual growth at the expected volume: ~3 operations a night
    expected_per_year = 3 * 365
    print(
        f"  expected volume {expected_per_year} operations/year -> "
        f"{expected_per_year * (attempt_bytes + operation_bytes) / rows / 1024:.0f} KiB/year"
    )


def test_scale_overhead_of_the_accounting_per_request():
    n = 200
    from django.conf import settings

    timings = {}
    for label, on in (("ledger off", False), ("ledger on", True)):
        settings.AI_USAGE_LEDGER = on
        started = time.perf_counter()
        for _ in range(n):
            _one_request()
        timings[label] = (time.perf_counter() - started) / n * 1000
    settings.AI_USAGE_LEDGER = True
    with CaptureQueriesContext(connection) as captured:
        _one_request()
    writes = [
        q["sql"].split()[0]
        for q in captured.captured_queries
        if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}
    ]
    print(
        f"\n  per request, test double (no network): off {timings['ledger off']:.2f} ms, "
        f"on {timings['ledger on']:.2f} ms -> overhead {timings['ledger on'] - timings['ledger off']:.2f} ms; "
        f"{len(writes)} writes, {len(captured.captured_queries)} queries"
    )
