"""The ledger's hooks against the real installed SDK over a synthetic HTTP transport (R98-C01).

No network: an `httpx.MockTransport` plays the provider. The real `anthropic` client does
its own retries and backoff; these tests prove the recorder *sees* them and changes none:
the same script is run with and without the recorder and the requests, retry count and
sleeps must be identical.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from app.ai_usage.models import AiAttempt, AiOperation, AttemptOutcome, Outcome, UsageStatus
from app.register_watch import ai_call

pytestmark = pytest.mark.django_db

MODEL = "claude-opus-5-5"


def _message(model: str = MODEL, **usage: int) -> dict[str, Any]:
    return {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": json.dumps({"items": [{"id": "a"}]})}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 3, **usage},
    }


class Script:
    """The provider: answers each physical request from a list of steps."""

    def __init__(self, steps: list[Callable[[httpx.Request], httpx.Response]]) -> None:
        self.steps = steps
        self.seen: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.content)
        step = self.steps[min(len(self.seen), len(self.steps)) - 1]
        return step(request)


def status(code: int, request_id: str = "req_x") -> Callable[[httpx.Request], httpx.Response]:
    return lambda r: httpx.Response(
        code,
        json={"type": "error", "error": {"type": "overloaded_error", "message": "BODY-TEXT"}},
        headers={"request-id": request_id},
    )


def ok(**kw: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda r: httpx.Response(200, json=_message(**kw), headers={"request-id": "req_ok_1"})


def raises(error: type[Exception]) -> Callable[[httpx.Request], httpx.Response]:
    def step(request: httpx.Request) -> httpx.Response:
        raise error("secret transport text", request=request)

    return step


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: slept.append(seconds))
    return slept


def _run(monkeypatch: pytest.MonkeyPatch, script: Script, *, ledger: bool) -> Any:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    original = anthropic.DefaultHttpxClient
    monkeypatch.setattr(
        anthropic,
        "DefaultHttpxClient",
        functools.partial(original, transport=httpx.MockTransport(script)),
    )
    usage = ai_call.Usage()
    if not ledger:
        # the pre-r98 path: a client built without the recorder's hooks
        client = anthropic.Anthropic(
            timeout=5.0,
            max_retries=2,
            http_client=original(timeout=5.0, transport=httpx.MockTransport(script)),
        )
    else:
        client = None
    return ai_call.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system="s",
        prompt=lambda: "p",
        schema={"type": "object"},
        max_tokens=10,
        timeout=5.0,
        key=lambda row: row["id"],
        usage=usage,
        client=client,
        service="watch_findings" if ledger else "",
        presented=1,
    )


def _attempts() -> list[AiAttempt]:
    return list(AiAttempt.objects.order_by("sequence"))


def test_a_first_try_success_is_one_measured_attempt(monkeypatch, sleeps):
    script = Script([ok(cache_read_input_tokens=5, cache_creation_input_tokens=0)])
    _run(monkeypatch, script, ledger=True)
    (attempt,) = _attempts()
    assert (attempt.outcome, attempt.http_status, attempt.provider_request_id) == (
        AttemptOutcome.OK,
        200,
        "req_ok_1",
    )
    assert (attempt.input_tokens, attempt.output_tokens) == (12, 3)
    assert (attempt.cache_read_tokens, attempt.cache_creation_tokens) == (5, 0)
    assert attempt.duration_ms is not None and not sleeps
    assert AiOperation.objects.get().outcome == Outcome.SUCCEEDED


def test_a_retryable_failure_then_success_gives_two_attempts_tokens_on_the_last(
    monkeypatch, sleeps
):
    script = Script([status(529, "req_busy"), ok()])
    answer = _run(monkeypatch, script, ledger=True)
    first, second = _attempts()
    assert not isinstance(answer, ai_call.Failure)
    assert (first.outcome, first.http_status, first.retry_class) == (
        AttemptOutcome.HTTP_ERROR,
        529,
        "retried",
    )
    assert first.provider_request_id == "req_busy"
    assert first.usage_status == UsageStatus.UNAVAILABLE and first.input_tokens is None
    assert (second.outcome, second.input_tokens, second.retry_class) == (
        AttemptOutcome.OK,
        12,
        "final",
    )
    assert AiOperation.objects.get().outcome == Outcome.SUCCEEDED_AFTER_RETRY
    assert len(sleeps) == 1  # the SDK's own backoff, once


def test_rate_limits_until_the_sdk_gives_up_are_three_unknown_attempts(monkeypatch, sleeps):
    script = Script([status(429)])
    answer = _run(monkeypatch, script, ledger=True)
    rows = _attempts()
    assert isinstance(answer, ai_call.Failure)
    assert [r.outcome for r in rows] == [AttemptOutcome.RATE_LIMITED] * 3
    assert all(r.input_tokens is None for r in rows)
    op = AiOperation.objects.get()
    assert (op.outcome, op.error_kind) == (Outcome.FAILED, "rate_limited")
    assert len(script.seen) == 3  # max_retries=2 → three requests, as before r98


def test_connection_failures_have_no_status_and_no_tokens(monkeypatch, sleeps):
    script = Script([raises(httpx.ConnectError)])
    answer = _run(monkeypatch, script, ledger=True)
    rows = _attempts()
    assert isinstance(answer, ai_call.Failure) and len(rows) == 3
    assert all(r.http_status is None and r.input_tokens is None for r in rows)
    assert rows[-1].outcome == AttemptOutcome.CONNECTION_ERROR
    assert rows[0].outcome == AttemptOutcome.UNKNOWN  # earlier ones: cause not recorded
    assert AiOperation.objects.get().error_kind == "connection"


def test_timeouts_are_recorded_as_such_and_are_not_zero_token_successes(monkeypatch, sleeps):
    script = Script([raises(httpx.ReadTimeout)])
    _run(monkeypatch, script, ledger=True)
    rows = _attempts()
    assert rows[-1].outcome == AttemptOutcome.TIMEOUT
    assert all(r.usage_status == UsageStatus.UNAVAILABLE for r in rows)
    assert AiOperation.objects.get().error_kind == "timeout"


def test_a_different_answering_model_alone_is_not_a_fallback(monkeypatch, sleeps):
    script = Script([ok(model="claude-opus-5-5-20261101")])  # an alias resolved to a dated id
    _run(monkeypatch, script, ledger=True)
    (attempt,) = _attempts()
    assert attempt.actual_model == "claude-opus-5-5-20261101"
    assert attempt.requested_model == MODEL and attempt.fallback_observed is None
    assert len(_attempts()) == 1  # provider-internal attempts are not invented


def test_a_fallback_the_provider_reports_is_recorded_and_its_cost_is_unknown(monkeypatch, sleeps):
    iteration = {
        "type": "fallback_message",
        "model": "claude-fallback-1",
        "input_tokens": 40,
        "output_tokens": 9,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    script = Script([ok(model="claude-fallback-1", iterations=[iteration])])
    _run(monkeypatch, script, ledger=True)
    (attempt,) = _attempts()
    assert attempt.fallback_observed is True
    assert (attempt.fallback_model, attempt.fallback_input_tokens) == ("claude-fallback-1", 40)
    from app.ai_usage import report

    cost = report.attempt_cost(report.PriceBook.load(), attempt)
    assert (cost.eur, cost.reason) == (None, "fallback")  # attribution not established


@pytest.mark.parametrize(
    "steps",
    [
        [ok()],
        [status(529), ok()],
        [status(429)],
        [raises(httpx.ConnectError)],
        [raises(httpx.ReadTimeout)],
    ],
)
def test_the_recorder_changes_no_request_retry_or_backoff(monkeypatch, steps):
    runs = []
    for ledger in (False, True):
        slept: list[float] = []
        monkeypatch.setattr("time.sleep", lambda seconds, bag=slept: bag.append(seconds))
        script = Script(list(steps))
        _run(monkeypatch, script, ledger=ledger)
        runs.append((script.seen, len(slept)))
    (plain_bodies, plain_sleeps), (ledger_bodies, ledger_sleeps) = runs
    assert plain_bodies == ledger_bodies  # same count, same payload bytes
    assert plain_sleeps == ledger_sleeps  # same number of backoffs
    assert json.loads(ledger_bodies[0])["max_tokens"] == 10


def test_no_provider_error_text_reaches_the_ledger(monkeypatch, sleeps):
    _run(monkeypatch, Script([status(529)]), ledger=True)
    _run(monkeypatch, Script([raises(httpx.ConnectError)]), ledger=True)
    dump = " ".join(
        str(v) for m in (AiOperation, AiAttempt) for row in m.objects.values() for v in row.values()
    )
    assert "BODY-TEXT" not in dump and "secret transport text" not in dump
