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


def make_client(timeout: float) -> Any:
    import anthropic  # imported here: the registers are watched without it when the model is off

    return anthropic.Anthropic(timeout=timeout, max_retries=2)


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
) -> dict[Hashable, Any] | Failure:
    """The answer's ``items`` keyed by ``key(row)``, or why there is none.

    ``prompt`` is called inside the guard, so a prompt that cannot be built is a
    failed request, like the call itself."""
    from app.register_watch.sources import http

    left = http.remaining()
    if left is not None and left <= 0:
        # The nightly step's time is spent (ADR 0231): no request is started.
        return Failure("request", OUT_OF_TIME)
    try:
        client = client or make_client(timeout)
        if left is not None:
            client = _within(client, timeout, left)
        response = client.beta.messages.create(
            model=getattr(settings, setting, DEFAULT_MODEL),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt()}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as error:  # the model is an aid; every caller goes on without it
        status = getattr(error, "status_code", None)
        return Failure("request", type(error).__name__ + (f" {status}" if status else ""))
    usage.calls += 1
    usage.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
    usage.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
    usage.model = str(getattr(response, "model", "") or usage.model)
    if response.stop_reason != "end_turn":
        stop = response.stop_reason
        return Failure("stop", f"stop_reason {stop}", stop_reason=stop)
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        return {key(row): row for row in json.loads(text)["items"]}
    except (ValueError, KeyError, TypeError):
        return Failure("unreadable", UNREADABLE)


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
