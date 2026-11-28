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
    return "other"


@dataclass
class _Event:
    started_at: Any
    t0: float
    ended_at: Any = None
    duration_ms: int | None = None
    status: int | None = None
    request_id: str = ""


@dataclass
class Recorder:
    service: str
    requested_model: str
    presented: int | None = None
    operation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    run_ref: str = field(default_factory=lambda: _run_ref.get())
    started_at: Any = field(default_factory=timezone.now)
    _events: list[_Event] = field(default_factory=list)
    _done: bool = False

    @property
    def attempts_seen(self) -> int:
        return len(self._events)

    # -- the httpx event hooks (one request event = one physical attempt) ----------
    def on_request(self, request: Any) -> None:
        self._events.append(_Event(started_at=timezone.now(), t0=time.monotonic()))

    def on_response(self, response: Any) -> None:
        if not self._events:
            return
        event = self._events[-1]
        event.status = int(response.status_code)
        event.ended_at = timezone.now()
        event.duration_ms = int((time.monotonic() - event.t0) * 1000)
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
        except Exception:  # accounting never stops the work
            logger.warning("ai usage: could not open the ledger row")

    def _save_operation(
        self, outcome: str, finished: Any, classified: int | None, kind: str | None
    ) -> None:
        defaults: dict[str, Any] = {
            "service": self.service,
            "requested_model": self.requested_model[:80],
            "run_ref": self.run_ref,
            "started_at": self.started_at,
            "items_presented": self.presented,
            "outcome": outcome,
        }
        if finished is not None:
            defaults.update(
                finished_at=finished, items_classified=classified, error_kind=kind or ""
            )
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
            attempts = [] if skipped else self._attempts(response, error, now)
            with transaction.atomic():
                self._save_operation(
                    outcome, now, classified, error_kind(error) if error is not None else ""
                )
                AiAttempt.objects.bulk_create(attempts, ignore_conflicts=True)
        except Exception:
            logger.warning("ai usage: could not save a result")
            _gap(self.service, "write_failed")

    def _attempts(self, response: Any, error: BaseException | None, now: Any) -> list[AiAttempt]:
        events = list(self._events)
        answered = response is not None
        if not events:  # a client with no hook (a test double): one attempt, timed as a whole
            elapsed = int((now - self.started_at).total_seconds() * 1000)
            single = _Event(started_at=self.started_at, t0=0.0)
            single.ended_at = now
            single.duration_ms = max(elapsed, 0)
            single.status = 200 if answered else None
            events = [single]
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
                duration_ms=event.duration_ms,
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
        return rows


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
    row.fallback_observed = row.actual_model != row.requested_model if row.actual_model else None
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
    cutoff = (now or timezone.now()) - STALE_AFTER
    return AiOperation.objects.filter(outcome=Outcome.IN_PROGRESS, started_at__lt=cutoff).update(
        outcome=Outcome.INTERRUPTED
    )


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
