"""Physical-attempt accounting boundaries (r98 final corrections, item 4), against the real SDK.

A confirmed physical attempt is a request the client actually sent (an httpx request event).
A failure before any request is a logical operation WITHOUT an attempt. An unwatched client
is «attempt observability unavailable». A hard kill keeps the attempts already started."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_usage import recorder
from app.ai_usage.models import (
    AiAttempt,
    AiLedgerGap,
    AiOperation,
    AttemptCoverage,
    AttemptOutcome,
    Outcome,
)
from app.register_watch import ai_call
from tests.test_ai_usage_real_sdk import Script, _run, ok, sleeps, status  # noqa: F401
from tests.test_register_ai_call_characterization import FakeClient, _text, response

pytestmark = pytest.mark.django_db


def test_a_failure_before_any_request_is_an_operation_without_an_attempt(monkeypatch, sleeps):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")

    def broken_prompt() -> str:
        raise RuntimeError("cannot build the prompt")

    result = ai_call.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system="s",
        prompt=broken_prompt,
        schema={"type": "object"},
        max_tokens=10,
        timeout=5.0,
        key=lambda row: row["id"],
        usage=ai_call.Usage(),
        service="watch_findings",
        presented=1,
    )
    assert isinstance(result, ai_call.Failure)
    operation = AiOperation.objects.get()
    assert operation.outcome == Outcome.FAILED
    assert operation.attempt_coverage == AttemptCoverage.NONE
    assert AiAttempt.objects.count() == 0  # a prompt that never became a request is no attempt


def test_a_hard_kill_after_the_request_started_keeps_the_attempt(monkeypatch, sleeps):
    """SIGKILL gives no chance to finish: the request-start row is already there."""
    monkeypatch.setattr(recorder.Recorder, "finish", lambda self, **kw: None)
    _run(monkeypatch, Script([ok()]), ledger=True)
    operation = AiOperation.objects.get()
    assert operation.outcome == Outcome.IN_PROGRESS  # never finished, as after a kill
    attempt = AiAttempt.objects.get()
    assert attempt.outcome == AttemptOutcome.UNKNOWN  # sent; what came back is not known
    assert attempt.input_tokens is None and attempt.sequence == 1


def test_every_retry_of_a_killed_operation_is_already_stored(monkeypatch, sleeps):
    monkeypatch.setattr(recorder.Recorder, "finish", lambda self, **kw: None)
    _run(monkeypatch, Script([status(529), status(529), ok()]), ledger=True)
    assert AiAttempt.objects.count() == 3


def test_an_unwatched_client_with_an_answer_is_inferred_and_marked(monkeypatch):
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
    operation = AiOperation.objects.get()
    attempt = AiAttempt.objects.get()
    assert operation.attempt_coverage == AttemptCoverage.UNAVAILABLE
    assert attempt.observed is False and attempt.headers_ms is None


def test_an_unwatched_client_that_failed_records_no_attempt_and_says_unavailable():
    def boom(request: Any) -> Any:
        raise RuntimeError("down")

    ai_call.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system="s",
        prompt=lambda: "p",
        schema={},
        max_tokens=1,
        timeout=5.0,
        key=lambda row: row["id"],
        usage=ai_call.Usage(),
        client=FakeClient(boom),
        service="watch_findings",
        presented=1,
    )
    assert AiOperation.objects.get().attempt_coverage == AttemptCoverage.UNAVAILABLE
    assert AiAttempt.objects.count() == 0


def test_a_watched_request_is_observed_with_a_headers_time_only(monkeypatch, sleeps):
    _run(monkeypatch, Script([ok()]), ledger=True)
    assert AiOperation.objects.get().attempt_coverage == AttemptCoverage.OBSERVED
    attempt = AiAttempt.objects.get()
    assert attempt.observed is True and attempt.headers_ms is not None
    assert not hasattr(attempt, "duration_ms")  # the field says what it measures


def test_a_failing_start_record_never_changes_the_provider_call(monkeypatch, sleeps):
    real = AiAttempt.objects.bulk_create
    calls = {"n": 0}

    def flaky(rows: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1 and not kw.get("update_conflicts"):
            raise RuntimeError("database hiccup")
        return real(rows, **kw)

    monkeypatch.setattr(
        type(AiAttempt.objects), "bulk_create", lambda self, rows, **kw: flaky(rows, **kw)
    )
    answer = _run(monkeypatch, Script([ok()]), ledger=True)
    assert not isinstance(answer, ai_call.Failure)  # the answer is the same
    assert AiLedgerGap.objects.filter(kind="attempt_start_not_saved").count() == 1
    attempt = AiAttempt.objects.get()  # the final upsert still stored the attempt
    assert attempt.outcome == AttemptOutcome.OK and attempt.input_tokens == 12
