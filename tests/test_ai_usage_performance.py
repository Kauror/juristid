"""What the ledger costs (R98-C07): database writes per request, and a page whose query count
does not grow with the ledger. Timings are printed (`pytest -s`) for the readiness report; only
the query counts are asserted, because wall-clock limits are machine-dependent."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

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

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _writes(queries: list[dict[str, Any]]) -> list[str]:
    return [
        q["sql"].split()[0]
        for q in queries
        if q["sql"].split()[0] in {"INSERT", "UPDATE", "DELETE"}
    ]


def _one_request() -> None:
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
        service="watch_findings",
        presented=1,
    )


def test_database_writes_per_logical_operation_and_attempt_are_bounded():
    with CaptureQueriesContext(connection) as captured:
        _one_request()
    writes = _writes(captured.captured_queries)
    # begin(): reconcile UPDATE + operation upsert; finish(): operation upsert + one attempt insert.
    print(f"\nwrites per operation with one attempt: {len(writes)} {writes}")
    assert len(writes) <= 6
    assert len(captured.captured_queries) <= 16


def _fill(days: int, per_day: int) -> int:
    start = timezone.now() - dt.timedelta(days=days)
    operations, attempts = [], []
    for d in range(days):
        for n in range(per_day):
            at = start + dt.timedelta(days=d, minutes=n)
            op = AiOperation(
                service="watch_findings",
                requested_model="claude-opus-5-5",
                started_at=at,
                finished_at=at,
                outcome=Outcome.SUCCEEDED,
                items_presented=10,
                items_classified=10,
            )
            operations.append(op)
            attempts.append(
                AiAttempt(
                    operation=op,
                    sequence=1,
                    started_at=at,
                    outcome=AttemptOutcome.OK,
                    requested_model="claude-opus-5-5",
                    actual_model="claude-opus-5-5",
                    usage_status=UsageStatus.MEASURED,
                    input_tokens=5000,
                    output_tokens=800,
                    duration_ms=4000 + n,
                    retry_class="final",
                )
            )
    AiOperation.objects.bulk_create(operations)
    AiAttempt.objects.bulk_create(attempts)
    return len(attempts)


def test_the_page_query_count_does_not_grow_with_the_ledger(client):
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    counts = []
    for days in (5, 60):
        AiAttempt.objects.all().delete()
        AiOperation.objects.all().delete()
        rows = _fill(days, 10)  # 10 per day is ~3x the expected nightly volume
        params = {"periood": "custom", "alates": "2025-01-01", "kuni": "2027-12-31"}
        with CaptureQueriesContext(connection) as captured:
            started = time.perf_counter()
            page = client.get(reverse("haldus:ai_usage"), params)
            took = time.perf_counter() - started
        assert page.status_code == 200
        counts.append(len(captured.captured_queries))
        print(
            f"\npage: {rows} attempts -> {len(captured.captured_queries)} queries, "
            f"{took * 1000:.0f} ms"
        )
    # The 400-day ledger costs no more queries than the 10-day one (aggregation is in memory
    # per attempt row, bounded by the period; the detail table is paginated to 50 rows).
    assert counts[1] <= counts[0] + 2


def test_aggregate_summary_time_for_a_year_at_ten_times_the_expected_volume():
    rows = _fill(120, 5)
    started = time.perf_counter()
    summary = report.summarise(
        timezone.now().date() - dt.timedelta(days=366), timezone.now().date() + dt.timedelta(days=1)
    )
    took = time.perf_counter() - started
    print(f"\nsummarise: {rows} attempts in {took * 1000:.0f} ms")
    assert summary.totals.attempts == rows
