"""The AI usage ledger, its pricing and its Haldus page (r98, docs/adr/0236).

No provider is called: the transport runs against doubles, and the physical
attempts the SDK would make are simulated by firing the recorder's own hooks.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.ai_usage import recorder, report
from app.ai_usage.models import (
    AiAttempt,
    AiFxRate,
    AiLedgerGap,
    AiOperation,
    AiPrice,
    AttemptOutcome,
    Outcome,
    UsageStatus,
)
from app.register_watch import ai_call
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified
from tests.test_register_ai_call_characterization import (
    APITimeoutError,
    FakeClient,
    Overloaded,
    _text,
    response,
)

pytestmark = pytest.mark.django_db
MODEL = "claude-opus-5-5"


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _call(client: Any) -> tuple[Any, ai_call.Usage]:
    usage = ai_call.Usage()
    answer = ai_call.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system="s",
        prompt=lambda: "PROMPT-SECRET-TEXT",
        schema={},
        max_tokens=10,
        timeout=5.0,
        key=lambda row: row["id"],
        usage=usage,
        client=client,
        service="watch_findings",
        presented=2,
    )
    return answer, usage


def _ok(rows: list[dict] | None = None, **kw: Any) -> Any:
    return response(_text(rows if rows is not None else [{"id": "a"}, {"id": "b"}]), **kw)


def test_a_successful_request_is_one_operation_one_attempt():
    answer, usage = _call(FakeClient(lambda r: _ok(model="served-1")))
    op = AiOperation.objects.get()
    attempt = op.attempts.get()
    assert not isinstance(answer, ai_call.Failure)
    assert ai_call.last_operation(usage) == op.pk
    assert (op.outcome, op.items_presented, op.items_classified) == (Outcome.SUCCEEDED, 2, 2)
    assert attempt.outcome == AttemptOutcome.OK
    assert (attempt.input_tokens, attempt.output_tokens) == (10, 5)
    assert attempt.usage_status == UsageStatus.MEASURED
    assert attempt.actual_model == "served-1" and attempt.fallback_observed is None
    # cache fields the response did not carry are unknown, not zero
    assert attempt.cache_read_tokens is None and attempt.cache_creation_tokens is None


def test_cache_tokens_are_kept_when_reported():
    usage = SimpleNamespace(
        input_tokens=100, output_tokens=7, cache_read_input_tokens=40, cache_creation_input_tokens=0
    )
    _call(FakeClient(lambda r: _ok(usage=usage, model=MODEL)))
    attempt = AiAttempt.objects.get()
    assert (attempt.cache_read_tokens, attempt.cache_creation_tokens) == (40, 0)
    assert attempt.fallback_observed is None  # no iterations reported: nothing said


def _client_with_attempts(statuses: list[int | None], monkeypatch: Any, final: Any) -> None:
    """A transport whose one `create` makes several physical requests, as the SDK does."""

    def make(timeout: float, rec: Any = None) -> Any:
        def create(**request: Any) -> Any:
            for status in statuses:
                rec.on_request(None)
                if status is not None:
                    headers = {"request-id": "req_ABC123"}
                    rec.on_response(SimpleNamespace(status_code=status, headers=headers))
            if isinstance(final, Exception):
                raise final
            return final

        return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))

    monkeypatch.setattr(ai_call, "make_client", make)


def test_a_retry_that_succeeds_shows_both_attempts(monkeypatch):
    _client_with_attempts([529, 200], monkeypatch, _ok(model=MODEL))
    _call(None)
    op = AiOperation.objects.get()
    first, second = op.attempts.order_by("sequence")
    assert op.outcome == Outcome.SUCCEEDED_AFTER_RETRY
    assert (first.outcome, first.retry_class, first.http_status) == (
        AttemptOutcome.HTTP_ERROR,
        "retried",
        529,
    )
    assert first.usage_status == UsageStatus.UNAVAILABLE and first.input_tokens is None
    assert (second.outcome, second.retry_class, second.provider_request_id) == (
        AttemptOutcome.OK,
        "final",
        "req_ABC123",
    )


def test_a_rate_limit_then_failure_has_unknown_usage(monkeypatch):
    _client_with_attempts([429, 429, 429], monkeypatch, Overloaded())
    answer, usage = _call(None)
    op = AiOperation.objects.get()
    assert isinstance(answer, ai_call.Failure) and usage.calls == 0
    assert op.outcome == Outcome.FAILED and op.error_kind == "http_529"
    outcomes = [a.outcome for a in op.attempts.order_by("sequence")]
    assert outcomes == [AttemptOutcome.RATE_LIMITED] * 3
    assert all(a.input_tokens is None for a in op.attempts.all())


def test_a_timeout_is_recorded_without_inventing_tokens(monkeypatch):
    _client_with_attempts([None], monkeypatch, APITimeoutError("secret detail"))
    _call(None)
    op = AiOperation.objects.get()
    attempt = op.attempts.get()
    assert (op.outcome, op.error_kind) == (Outcome.FAILED, "timeout")
    assert attempt.outcome == AttemptOutcome.TIMEOUT
    assert attempt.usage_status == UsageStatus.UNAVAILABLE and attempt.output_tokens is None


def test_a_cut_off_answer_is_not_a_success():
    _call(FakeClient(lambda r: _ok(stop="max_tokens")))
    assert AiOperation.objects.get().outcome == Outcome.INCOMPLETE
    attempt = AiAttempt.objects.get()
    assert attempt.stop_reason == "max_tokens" and attempt.input_tokens == 10  # billed, measured


def test_an_unreadable_answer_is_http_ok_but_not_a_success():
    _call(FakeClient(lambda r: response("not json")))
    op = AiOperation.objects.get()
    assert op.outcome == Outcome.UNREADABLE and op.attempts.get().outcome == AttemptOutcome.OK


def test_no_time_left_is_a_skip_and_not_a_billable_attempt(monkeypatch):
    from app.register_watch.sources import http

    monkeypatch.setattr(http, "remaining", lambda: 0.0)
    answer, _ = _call(FakeClient(lambda r: _ok()))
    assert isinstance(answer, ai_call.Failure)
    op = AiOperation.objects.get()
    assert op.outcome == Outcome.SKIPPED_NO_TIME and not op.attempts.exists()


def test_a_duplicate_accounting_event_changes_nothing():
    rec = recorder.Recorder("watch_findings", MODEL, 1)
    rec.begin()
    for _ in range(3):
        rec.finish(response=_ok(model=MODEL), outcome=Outcome.SUCCEEDED, classified=1)
    assert AiOperation.objects.count() == 1 and AiAttempt.objects.count() == 1
    # a replay under a new recorder object with the same id is idempotent too
    again = recorder.Recorder("watch_findings", MODEL, 1, operation_id=rec.operation_id)
    again.finish(response=_ok(model=MODEL), outcome=Outcome.SUCCEEDED, classified=1)
    assert AiAttempt.objects.count() == 1


def test_an_interrupted_operation_is_reconciled_as_unknown():
    old = timezone.now() - dt.timedelta(hours=5)
    stale = AiOperation.objects.create(
        service="candidates", requested_model=MODEL, started_at=old, outcome=Outcome.IN_PROGRESS
    )
    recorder.Recorder("candidates", MODEL).begin()  # the next request tidies the old one
    stale.refresh_from_db()
    assert stale.outcome == Outcome.INTERRUPTED


def test_a_ledger_that_cannot_write_leaves_the_work_and_a_gap(monkeypatch):
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(AiAttempt.objects, "bulk_create", boom)
    answer, _ = _call(FakeClient(lambda r: _ok()))
    assert not isinstance(answer, ai_call.Failure)  # the business result is unaffected
    assert AiLedgerGap.objects.count() == 1


def test_the_ledger_never_stores_prompts_answers_or_error_text(monkeypatch):
    _client_with_attempts([None], monkeypatch, APITimeoutError("Bearer sk-ant-SECRET body text"))
    _call(None)
    monkeypatch.undo()
    _call(FakeClient(lambda r: _ok(model=MODEL)))
    dump = " ".join(
        str(v)
        for model in (AiOperation, AiAttempt)
        for row in model.objects.values()
        for v in row.values()
    )
    for secret in ("PROMPT-SECRET-TEXT", "sk-ant", "Bearer", "body text", "secret detail"):
        assert secret not in dump


# -- prices ---------------------------------------------------------------------------------


def _price(**kw: Any) -> AiPrice:
    base: dict[str, Any] = {
        "model": MODEL,
        "currency": "EUR",
        "input_per_mtok": Decimal("10"),
        "output_per_mtok": Decimal("50"),
        "cache_read_per_mtok": Decimal("1"),
        "cache_creation_per_mtok": Decimal("12"),
        "effective_from": dt.date(2026, 1, 1),
        "source_url": "https://example.test/pricing",
        "verified_on": dt.date(2026, 11, 1),
    }
    base.update(kw)
    return AiPrice.objects.create(**base)


def _attempt(day: dt.date, **kw: Any) -> AiAttempt:
    op = AiOperation.objects.create(
        service="watch_findings",
        requested_model=MODEL,
        started_at=dt.datetime.combine(day, dt.time(12), tzinfo=report.TALLINN),
        outcome=Outcome.SUCCEEDED,
        items_presented=1,
        items_classified=1,
    )
    fields: dict[str, Any] = {
        "operation": op,
        "sequence": 1,
        "started_at": op.started_at,
        "outcome": AttemptOutcome.OK,
        "requested_model": MODEL,
        "actual_model": MODEL,
        "usage_status": UsageStatus.MEASURED,
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "duration_ms": 1000,
        "retry_class": "final",
    }
    fields.update(kw)
    return AiAttempt.objects.create(**fields)


def test_cost_is_tokens_times_the_price_of_that_day():
    _price()
    attempt = _attempt(dt.date(2026, 11, 10))
    assert report.attempt_cost(report.PriceBook.load(), attempt).eur == Decimal("15")


def test_a_later_price_does_not_rewrite_an_earlier_estimate():
    _price(effective_to=dt.date(2026, 11, 14))
    _price(effective_from=dt.date(2026, 11, 15), input_per_mtok=Decimal("20"))
    book = report.PriceBook.load()
    early = report.attempt_cost(book, _attempt(dt.date(2026, 11, 10)))
    late = report.attempt_cost(book, _attempt(dt.date(2026, 11, 20)))
    assert (early.eur, late.eur) == (Decimal("15"), Decimal("25"))


def test_an_unknown_price_or_unmeasured_usage_is_unknown_not_zero():
    assert report.attempt_cost(report.PriceBook.load(), _attempt(dt.date(2026, 11, 10))).eur is None
    _price()
    unmeasured = _attempt(
        dt.date(2026, 11, 11),
        usage_status=UsageStatus.UNAVAILABLE,
        input_tokens=None,
        output_tokens=None,
    )
    assert report.attempt_cost(report.PriceBook.load(), unmeasured).eur is None
    assert report.money(None) == "Maksumus teadmata"


def test_a_dollar_price_needs_a_recorded_rate_and_keeps_the_original():
    _price(currency="USD")
    attempt = _attempt(dt.date(2026, 11, 10))
    assert report.attempt_cost(report.PriceBook.load(), attempt).eur is None  # no rate: unknown
    AiFxRate.objects.create(
        currency="USD", on_date=dt.date(2026, 11, 9), per_eur=Decimal("1.25"), source="ECB"
    )
    cost = report.attempt_cost(report.PriceBook.load(), attempt)
    assert (cost.original, cost.currency, cost.eur) == (Decimal("15"), "USD", Decimal("12"))


def test_missing_cache_fields_make_the_estimate_a_lower_bound():
    _price()
    attempt = _attempt(dt.date(2026, 11, 10), cache_read_tokens=None, cache_creation_tokens=None)
    assert report.attempt_cost(report.PriceBook.load(), attempt).lower_bound is True


def test_a_billed_category_without_a_price_is_unknown():
    _price(cache_read_per_mtok=None)
    attempt = _attempt(dt.date(2026, 11, 10), cache_read_tokens=500)
    assert report.attempt_cost(report.PriceBook.load(), attempt).eur is None


# -- the summary reconciles with an independent enumeration -------------------------------------


def test_the_summary_reconciles_with_the_rows():
    _price()
    days = [dt.date(2026, 11, d) for d in (3, 3, 4)]
    made = [
        _attempt(d, input_tokens=n * 100_000, output_tokens=n * 10_000)
        for d, n in zip(days, (1, 2, 3), strict=True)
    ]
    _attempt(
        dt.date(2026, 11, 4),
        outcome=AttemptOutcome.TIMEOUT,
        usage_status=UsageStatus.UNAVAILABLE,
        input_tokens=None,
        output_tokens=None,
        retry_class="retried",
    )
    summary = report.summarise(dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    expected = sum(
        (Decimal(a.input_tokens or 0) * 10 + Decimal(a.output_tokens or 0) * 50) / 1_000_000
        for a in made
    )
    assert summary.totals.cost_eur == expected == Decimal("9")
    assert summary.totals.attempts == 4 and summary.totals.operations == 4
    assert summary.totals.unknown_cost_attempts == 1 and summary.totals.failed_attempts == 1
    assert summary.totals.retried_attempts == 1
    assert sum(t.cost_eur for t in summary.by_day.values()) == summary.totals.cost_eur
    assert sum(t.attempts for t in summary.by_service.values()) == summary.totals.attempts


def test_budget_levels_and_incomplete_coverage(settings):
    _price()
    _attempt(dt.date(2026, 11, 2), input_tokens=1_000_000, output_tokens=0)  # 10 €
    today = dt.date(2026, 11, 20)
    assert report.budget(today).level == "none"  # no limit is invented
    settings.AI_BUDGET_WARN_EUR = "5"
    settings.AI_BUDGET_ESCALATE_EUR = "50"
    assert report.budget(today).level == "warn"
    settings.AI_BUDGET_ESCALATE_EUR = "10"
    assert report.budget(today).level == "escalate"
    _attempt(
        dt.date(2026, 11, 3),
        usage_status=UsageStatus.UNAVAILABLE,
        input_tokens=None,
        output_tokens=None,
    )
    assert report.budget(today).complete is False  # a low total is not proof of safety


def test_the_coverage_note_says_earlier_days_are_unknown_not_zero():
    _attempt(dt.date(2026, 11, 10))
    summary = report.summarise(dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert "teadmata" in report.coverage_note(summary)


# -- the page --


def test_the_page_is_for_the_technical_administrator_only(client):
    assert client.get(reverse("haldus:ai_usage")).status_code in (302, 404)
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    _price()
    _attempt(timezone.now().astimezone(report.TALLINN).date())
    page = client.get(reverse("haldus:ai_usage"), {"periood": "month"})
    assert page.status_code == 200
    body = page.content.decode()
    assert "AI kasutus" in body and "PROMPT" not in body
    csv = client.get(reverse("haldus:ai_usage_csv"))
    assert csv.status_code == 200 and "text/csv" in csv["Content-Type"]


# -- interrupted and unfinished operations (R98-C02) ----------------------------------------------


class StepTerminated(BaseException):
    """Named like `app.core.nightly_bounded.StepTerminated`: not an `Exception`."""


def test_a_supervisor_termination_is_recorded_as_interrupted_and_still_propagates():
    def terminate(request: Any) -> Any:
        raise StepTerminated

    with pytest.raises(StepTerminated):
        _call(FakeClient(terminate))
    op = AiOperation.objects.get()
    attempt = op.attempts.get()
    assert (op.outcome, op.error_kind) == (Outcome.INTERRUPTED, "interrupted")
    assert attempt.outcome == AttemptOutcome.INTERRUPTED and attempt.input_tokens is None


def test_a_request_still_inside_its_window_is_not_called_interrupted():
    now = timezone.now()
    live = AiOperation.objects.create(
        service="candidates",
        requested_model=MODEL,
        started_at=now - dt.timedelta(minutes=20),
        expected_end_at=now + dt.timedelta(minutes=10),
        outcome=Outcome.IN_PROGRESS,
    )
    recorder.reconcile_stale()
    live.refresh_from_db()
    assert live.outcome == Outcome.IN_PROGRESS
    assert report.effective_outcome(live, now) == Outcome.IN_PROGRESS


def test_a_killed_request_is_not_active_forever():
    now = timezone.now()
    killed = AiOperation.objects.create(
        service="candidates",
        requested_model=MODEL,
        started_at=now - dt.timedelta(hours=2),
        expected_end_at=now - dt.timedelta(hours=1, minutes=30),  # SIGKILL: no result row
        outcome=Outcome.IN_PROGRESS,
    )
    assert report.effective_outcome(killed, now) == Outcome.INTERRUPTED  # shown so at once
    assert recorder.reconcile_stale() == 1  # and stored so by the next request
    killed.refresh_from_db()
    assert killed.outcome == Outcome.INTERRUPTED and not killed.attempts.exists()
