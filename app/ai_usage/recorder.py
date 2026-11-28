"""Recording one AI operation and its attempts (r98, ADR 0236).

Used by `app.register_watch.ai_call`, the one transport of the model. The
accounting never decides anything for the caller: every public function here
swallows its own failure (a ledger that cannot be written leaves one
`AiLedgerGap` row where it can, and the Haldus page says its figures are
incomplete), never repeats the provider request, and writes in short
transactions of its own — nothing is held open across the request.

Attempts come from an httpx event hook on the SDK's own client, so the SDK's
retries are *seen*, not replaced: the retry count, backoff and deadline rules
stay exactly the SDK's and `ai_call._within`'s. Tokens are known only for the
attempt that returned an answer; an attempt that timed out or was refused has
`usage_status=UNAVAILABLE`, never a guessed zero.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.ai_usage.models import (
    AiAttempt,
    AiLedgerGap,
    AiOperation,
    AttemptCoverage,
    AttemptOutcome,
    Outcome,
    UsageStatus,
)

logger = logging.getLogger(__name__)

#: A started operation with no result after this long is shown as interrupted.
STALE_AFTER = timedelta(hours=3)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_run_ref: contextvars.ContextVar[str] = contextvars.ContextVar("ai_usage_run_ref", default="")


@contextlib.contextmanager
def run_context(ref: str) -> Iterator[None]:
    """Everything recorded inside carries this opaque run id (a WatchRun or command id)."""
    token = _run_ref.set(str(ref)[:64])
    try:
        yield
    finally:
        _run_ref.reset(token)


def enabled() -> bool:
    return bool(getattr(settings, "AI_USAGE_LEDGER", True))


def error_kind(error: BaseException) -> str:
    """A fixed vocabulary: the kind and the HTTP status, never the message."""
    name = type(error).__name__
    status = getattr(error, "status_code", None)
    if name == "APITimeoutError" or isinstance(error, TimeoutError):
        return "timeout"
    if status == 429:
        return "rate_limited"
    if isinstance(status, int):
        return f"http_{status}"
    if name == "APIConnectionError":
        return "connection"
    if name == "DeadlineExceeded":
        return "deadline"
    if not isinstance(error, Exception):
        return "interrupted"  # a supervisor's StepTerminated, KeyboardInterrupt
    return "other"


@dataclass
class _Event:
    started_at: Any
    t0: float
    ended_at: Any = None
    headers_ms: int | None = None
    status: int | None = None
    request_id: str = ""


@dataclass
class Recorder:
    service: str
    requested_model: str
    presented: int | None = None
    #: Seconds a live request may still run: the step budget left plus the supervisor's
    #: grace and kill delay, or the client's worst case (three phases and backoff).
    may_run_seconds: float = 900.0
    operation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    run_ref: str = field(default_factory=lambda: _run_ref.get())
    started_at: Any = field(default_factory=timezone.now)
    #: Set by `ai_call.make_client` when the httpx hooks watch the client: without it the
    #: number of physical requests is not known (see `AttemptCoverage`).
    hooked: bool = False
    _events: list[_Event] = field(default_factory=list)
    _done: bool = False
    _opened: bool = False

    @property
    def attempts_seen(self) -> int:
        return len(self._events)

    # -- the httpx event hooks (one request event = one physical attempt) ----------
    def on_request(self, request: Any) -> None:
        event = _Event(started_at=timezone.now(), t0=time.monotonic())
        self._events.append(event)
        self._record_started(len(self._events), event)

    def _record_started(self, sequence: int, event: _Event) -> None:
        """The request is about to leave: keep a durable trace of it NOW, so a hard kill
        leaves a confirmed (outcome unknown) attempt instead of nothing. It is one INSERT in
        its own savepoint; a failure leaves a gap row and the in-memory event still counts."""
        if not self._opened or not enabled():
            return
        try:
            with transaction.atomic():
                AiAttempt.objects.bulk_create(
                    [
                        AiAttempt(
                            id=uuid.uuid5(self.operation_id, str(sequence)),
                            operation_id=self.operation_id,
                            sequence=sequence,
                            started_at=event.started_at,
                            requested_model=self.requested_model[:80],
                            usage_status=UsageStatus.UNAVAILABLE,
                            outcome=AttemptOutcome.UNKNOWN,
                        )
                    ],
                    ignore_conflicts=True,
                )
        except Exception:
            logger.warning("ai usage: could not save a started attempt")
            _gap(self.service, "attempt_start_not_saved")

    def on_response(self, response: Any) -> None:
        if not self._events:
            return
        event = self._events[-1]
        event.status = int(response.status_code)
        event.ended_at = timezone.now()
        event.headers_ms = int((time.monotonic() - event.t0) * 1000)
        headers = getattr(response, "headers", None)
        raw = headers.get("request-id", "") if headers else ""
        event.request_id = raw if _REQUEST_ID.match(raw or "") else ""

    # -- the operation ---------------------------------------------------------------
    def begin(self) -> None:
        """The operation row, committed at once, so a crash leaves an IN_PROGRESS trace."""
        if not enabled():
            return
        try:
            reconcile_stale()
            self._save_operation(Outcome.IN_PROGRESS, None, None, None)
            self._opened = True
        except Exception:  # accounting never stops the work
            logger.warning("ai usage: could not open the ledger row")

    def _save_operation(
        self,
        outcome: str,
        finished: Any,
        classified: int | None,
        kind: str | None,
        coverage: str | None = None,
    ) -> None:
        defaults: dict[str, Any] = {
            "service": self.service,
            "requested_model": self.requested_model[:80],
            "run_ref": self.run_ref,
            "started_at": self.started_at,
            "items_presented": self.presented,
            "outcome": outcome,
            "expected_end_at": self.started_at + timedelta(seconds=self.may_run_seconds),
        }
        if finished is not None:
            defaults.update(
                finished_at=finished, items_classified=classified, error_kind=kind or ""
            )
        if coverage is not None:
            defaults["attempt_coverage"] = coverage
        AiOperation.objects.update_or_create(id=self.operation_id, defaults=defaults)

    def finish(
        self,
        *,
        response: Any = None,
        error: BaseException | None = None,
        outcome: str,
        classified: int | None = None,
        skipped: bool = False,
    ) -> None:
        if not enabled() or self._done:
            return
        self._done = True
        now = timezone.now()
        try:
            attempts: list[AiAttempt]
            coverage: str
            if skipped:
                attempts, coverage = [], AttemptCoverage.NONE
            else:
                attempts, coverage = self._attempts(response, error, now)
            with transaction.atomic():
                self._save_operation(
                    outcome,
                    now,
                    classified,
                    error_kind(error) if error is not None else "",
                    coverage,
                )
                AiAttempt.objects.bulk_create(
                    attempts,
                    update_conflicts=True,
                    unique_fields=["id"],
                    update_fields=_UPSERT_FIELDS,
                )
        except Exception:
            logger.warning("ai usage: could not save a result")
            _gap(self.service, "write_failed")

    def _attempts(
        self, response: Any, error: BaseException | None, now: Any
    ) -> tuple[list[AiAttempt], str]:
        """The attempt rows and what is known about them.

        Hook events are physical attempts. With none: a watched client sent no HTTP request
        (a failure before the request is a logical operation WITHOUT an attempt, not an
        attempt); an unwatched client (a test double) with an answer proves one request but
        not its timing, so one inferred row (`observed=False`) is kept and the coverage says
        the observation was unavailable."""
        events = list(self._events)
        answered = response is not None
        coverage = AttemptCoverage.OBSERVED
        inferred = False
        if not events:
            if self.hooked and not answered:
                return [], AttemptCoverage.NONE
            coverage = AttemptCoverage.UNAVAILABLE
            if not answered:
                return [], coverage
            single = _Event(started_at=self.started_at, t0=0.0)
            single.ended_at = now
            single.status = 200
            events = [single]
            inferred = True
        rows: list[AiAttempt] = []
        last = len(events) - 1
        for index, event in enumerate(events):
            is_last = index == last
            row = AiAttempt(
                id=uuid.uuid5(self.operation_id, str(index + 1)),  # idempotent on a replay
                operation_id=self.operation_id,
                sequence=index + 1,
                started_at=event.started_at,
                ended_at=event.ended_at,
                headers_ms=event.headers_ms,
                observed=not inferred,
                http_status=event.status,
                provider_request_id=event.request_id,
                requested_model=self.requested_model[:80],
                retry_class="final" if is_last else "retried",
                usage_status=UsageStatus.UNAVAILABLE,
                outcome=_attempt_outcome(event, is_last, error),
            )
            if is_last and answered:
                _read_usage(row, response)
            rows.append(row)
        return rows, coverage


_UPSERT_FIELDS = [
    "started_at",
    "ended_at",
    "headers_ms",
    "observed",
    "http_status",
    "provider_request_id",
    "requested_model",
    "actual_model",
    "retry_class",
    "usage_status",
    "outcome",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "stop_reason",
    "fallback_observed",
    "fallback_model",
    "fallback_input_tokens",
    "fallback_output_tokens",
]


def _attempt_outcome(event: _Event, is_last: bool, error: BaseException | None) -> str:
    status = event.status
    if status is not None:
        if 200 <= status < 300:
            return AttemptOutcome.OK
        return AttemptOutcome.RATE_LIMITED if status == 429 else AttemptOutcome.HTTP_ERROR
    if is_last and error is not None:
        kind = error_kind(error)
        if kind == "timeout":
            return AttemptOutcome.TIMEOUT
        if kind == "connection":
            return AttemptOutcome.CONNECTION_ERROR
        if kind == "interrupted":
            return AttemptOutcome.INTERRUPTED
    return AttemptOutcome.UNKNOWN


def _count(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _read_usage(row: AiAttempt, response: Any) -> None:
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", "")
    row.actual_model = model[:80] if isinstance(model, str) else ""
    stop = getattr(response, "stop_reason", "")
    row.stop_reason = stop[:40] if isinstance(stop, str) else ""
    iterations = getattr(usage, "iterations", None)
    if isinstance(iterations, list):
        served = [i for i in iterations if getattr(i, "type", "") == "fallback_message"]
        row.fallback_observed = bool(served)
        if served:
            fb_model = getattr(served[-1], "model", "")
            row.fallback_model = fb_model[:80] if isinstance(fb_model, str) else ""
            row.fallback_input_tokens = _count(served[-1], "input_tokens")
            row.fallback_output_tokens = _count(served[-1], "output_tokens")
    row.input_tokens = _count(usage, "input_tokens")
    row.output_tokens = _count(usage, "output_tokens")
    row.cache_read_tokens = _count(usage, "cache_read_input_tokens")
    row.cache_creation_tokens = _count(usage, "cache_creation_input_tokens")
    measured = row.input_tokens is not None and row.output_tokens is not None
    row.usage_status = UsageStatus.MEASURED if measured else UsageStatus.UNAVAILABLE
    if row.outcome == AttemptOutcome.UNKNOWN:
        row.outcome = AttemptOutcome.OK


def reconcile_stale(now: Any = None) -> int:
    """Operations still IN_PROGRESS long after they began: their result is unknown."""
    now = now or timezone.now()
    running = AiOperation.objects.filter(outcome=Outcome.IN_PROGRESS)
    due = running.filter(expected_end_at__lt=now) | running.filter(
        expected_end_at__isnull=True, started_at__lt=now - STALE_AFTER
    )
    return due.update(outcome=Outcome.INTERRUPTED)


def set_classified(operation_id: uuid.UUID | None, count: int) -> None:
    """The rows the caller accepted from the answer (its validation, not the model's say-so)."""
    if operation_id is None or not enabled():
        return
    try:
        AiOperation.objects.filter(id=operation_id).update(items_classified=count)
    except Exception:
        logger.warning("ai usage: could not save a classified count")


def _gap(service: str, kind: str) -> None:
    with contextlib.suppress(Exception):
        AiLedgerGap.objects.create(at=timezone.now(), service=service, kind=kind)
