"""One request to the model and its JSON answer, for the register-watch callers (audit HR-08).

`ai` (a Teema's findings), `candidates_ai` (new drafts) and `agenda` (the
Government's agenda) share the transport only: the client, the request's fixed
fields, token accounting, the stop check and reading the answer's ``items``.
Each caller keeps its own prompt, schema, ``max_tokens``, timeout, allowed
values, writes, logging and retries; this module logs nothing and never calls
`Usage.failed` itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any, Literal

from django.conf import settings

from app.register_watch import house_style

DEFAULT_MODEL = "claude-opus-5-5"
UNREADABLE = "vastus ei olnud loetav"
#: The `Failure` reason when the run's time was spent before the request (ADR 0231).
OUT_OF_TIME = "ajapiir täis"
#: Client timeouts, kept as they were: the watch (ADR 0217) 120 s; the new-drafts
#: sweep and the Government agenda 180 s since ADR 0218.
WATCH_TIMEOUT = 120.0
SWEEP_TIMEOUT = 180.0
#: Set on `Usage` (not a field, so its counts stay what they were) to the ledger id of
#: the last logical request; callers read it with `last_operation` (app.ai_usage).
LEDGER_ATTRIBUTE = "ledger_operation"


def last_operation(usage: Usage) -> Any:
    return getattr(usage, LEDGER_ATTRIBUTE, None)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    judged: int = 0
    failures: int = 0
    #: The last failure's kind and HTTP status, never its message: shown in
    #: Haldus → Registrite jälgimine.
    last_failure: str = ""

    def failed(self, reason: str) -> None:
        self.failures += 1
        self.last_failure = reason[:120]


@dataclass(frozen=True)
class Failure:
    """No answer to use: the request failed, the model stopped early, or the answer was unreadable.

    ``reason`` is what goes to `Usage.failed`; ``stop_reason`` is set for ``"stop"``."""

    kind: Literal["request", "stop", "unreadable"]
    reason: str
    stop_reason: str | None = None


def make_client(timeout: float, recorder: Any = None) -> Any:
    import anthropic  # imported here: the registers are watched without it when the model is off

    hooked = getattr(anthropic, "DefaultHttpxClient", None)
    if recorder is None or hooked is None:
        if recorder is not None:
            recorder.hooked = False  # nothing watches this client: its requests are not counted
        return anthropic.Anthropic(timeout=timeout, max_retries=2)
    # The same client and retries; the hooks only watch each physical request (r98).
    http_client = hooked(
        timeout=timeout,
        event_hooks={"request": [recorder.on_request], "response": [recorder.on_response]},
    )
    recorder.hooked = True  # from here on «no hook event» means «no request was sent»
    return anthropic.Anthropic(timeout=timeout, max_retries=2, http_client=http_client)


def call_items(
    *,
    setting: str,
    system: str,
    prompt: Callable[[], str],
    schema: dict[str, Any],
    max_tokens: int,
    timeout: float,
    key: Callable[[Any], Hashable],
    usage: Usage,
    client: Any = None,
    service: str = "",
    presented: int | None = None,
) -> dict[Hashable, Any] | Failure:
    """The answer's ``items`` keyed by ``key(row)``, or why there is none.

    ``prompt`` is called inside the guard, so a prompt that cannot be built is a
    failed request, like the call itself."""
    from app.ai_usage import recorder as ledger
    from app.ai_usage.models import Outcome
    from app.register_watch.sources import http

    model = getattr(settings, setting, DEFAULT_MODEL)
    rec = ledger.Recorder(service, str(model), presented) if service else None
    left = http.remaining()
    if rec is not None and left is not None:
        # the step's budget left, the supervisor's grace (300 s) and kill delay (60 s)
        rec.may_run_seconds = max(left, 0.0) + 360.0
    elif rec is not None:
        rec.may_run_seconds = 3 * timeout * 3 + 300.0
    if rec is not None:
        setattr(usage, LEDGER_ATTRIBUTE, rec.operation_id)
    if left is not None and left <= 0:
        # The nightly step's time is spent (ADR 0231): no request is started.
        if rec is not None:
            rec.finish(outcome=Outcome.SKIPPED_NO_TIME, skipped=True)
        return Failure("request", OUT_OF_TIME)
    if rec is not None:
        rec.begin()
    response: Any = None
    try:
        if client is None and rec is not None:
            rec.hooked = (
                True  # our own client carries the hooks; a failure before it sends is no attempt
            )
        client = client or make_client(timeout, rec)
        if left is not None:
            client = _within(client, timeout, left)
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt()}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except BaseException as error:
        if not isinstance(error, Exception):
            # The supervisor's SIGTERM (`StepTerminated`) or an interrupt: the outcome of the
            # request is unknown. Recorded, then let go on as it was.
            if rec is not None:
                rec.finish(error=error, outcome=Outcome.INTERRUPTED)
            raise
        # the model is an aid; every caller goes on without it
        if rec is not None:
            rec.finish(error=error, outcome=Outcome.FAILED)
        status = getattr(error, "status_code", None)
        return Failure("request", type(error).__name__ + (f" {status}" if status else ""))
    usage.calls += 1
    usage.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
    usage.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
    usage.model = str(getattr(response, "model", "") or usage.model)
    many = rec is not None and rec.attempts_seen > 1
    if response.stop_reason != "end_turn":
        stop = response.stop_reason
        if rec is not None:
            rec.finish(response=response, outcome=Outcome.INCOMPLETE)
        return Failure("stop", f"stop_reason {stop}", stop_reason=stop)
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        answers = {key(row): row for row in json.loads(text)["items"]}
    except (ValueError, KeyError, TypeError):
        if rec is not None:
            rec.finish(response=response, outcome=Outcome.UNREADABLE)
        return Failure("unreadable", UNREADABLE)
    if rec is not None:
        rec.finish(
            response=response,
            outcome=Outcome.SUCCEEDED_AFTER_RETRY if many else Outcome.SUCCEEDED,
            classified=len(answers),
        )
    return answers


def _within(client: Any, timeout: float, left: float) -> Any:
    """The client bounded by the run's time left: its timeout no longer than what is
    left, and no retries a second try could not finish in (ADR 0231). The SDK's
    timeout bounds each phase of a request, not the whole of it; the nightly
    step's hard limit (`app.core.nightly_bounded`) is what ends a response that
    trickles past it."""
    with_options = getattr(client, "with_options", None)
    if with_options is None:
        return client
    bounded = min(timeout, left)
    return with_options(timeout=bounded, max_retries=2 if left >= 3 * timeout else 0)


def clean_line(value: Any) -> str:
    """The model's line as stored: trimmed, in the house style, at most 500 characters."""
    return house_style.fix(str(value or "").strip())[:500]
