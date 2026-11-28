"""The one way this package reads api.riigikogu.ee (ADR 0224 §3).

Through the register watch's fenced door (`app.register_watch.sources.http`):
https, the fixed host list, no redirects, a timeout, a size cap and the
``REGISTER_WATCH_FETCH`` switch. On top of it, three things the Riigikogu needs:

* **The published limits, shared across processes.** «Ühelt IP-aadressilt on
  lubatud kuni 1 päring sekundis, ühe URLi ehk teekonna kohta kuni 12 päringut
  minutis» (riigikogu.ee/riigikogu/avaandmed, read 24.11.2026). Every call first
  takes its turn from the `ApiCall` ledger under one advisory lock, so the
  register watch, the new-drafts sweep and this cycle — separate processes —
  cannot add up to a burst. The per-path limit is kept at 11 a minute and the
  per-address spacing at 1.2 s: a margin, not the edge.
* **Bounded retries.** 429 waits for ``Retry-After`` when it says a number of
  seconds, else 5, 15, 45 s; a 5xx or a dropped connection is tried twice more; a
  4xx other than 429 is the answer and is not repeated.
* **A request budget** per stage (`Budget`): a stage that would exceed it stops
  as partial instead of reading for ever.
* **A time limit** (`time_limit`, set by the nightly cycle per stage): no
  request starts, no back-off or ledger wait sleeps, and no answer is waited for
  past it — the fenced door's watchdog cuts a call at the earlier of the limit and
  `CALL_SECONDS` (connect, TLS, headers and body together; the 25-second socket
  timeout alone bounds one ``recv``, not a call). Reaching it is `OutOfTime`, a
  `BudgetExceeded`: the stage stops as partial and its checkpoint stays. Outside
  a limit (the register watch's own reads) nothing changes.

Nothing here decides what an empty answer means. A caller that gets an empty
page must not read it as «everything is gone» (ADR 0224 §4).
"""

from __future__ import annotations

import contextvars
import datetime as dt
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.db import OperationalError, connection, transaction
from django.utils import timezone

from app.register_watch.sources import http
from app.register_watch.sources.http import FetchError

HOST = "api.riigikogu.ee"
#: Below the published 1 a second and 12 a minute.
MIN_SPACING = dt.timedelta(seconds=1.2)
PER_PATH_PER_MINUTE = 11
#: The ledger's own lock; the number is this app's.
LEDGER_LOCK = 222_0001
#: Its holder keeps it for a few short queries and never sleeps under it; a wait
#: longer than this means something is wrong, and the call fails instead of hanging.
LEDGER_LOCK_TIMEOUT_MS = 30_000
#: Waits between tries of a 429 without ``Retry-After``.
BACKOFF_SECONDS = (5.0, 15.0, 45.0)
#: A ``Retry-After`` longer than this ends the stage's reading instead of sleeping.
MAX_WAIT_SECONDS = 120.0
PAGE_SIZE = 100

#: Under a time limit, one call's own ceiling (connect, TLS, headers and body).
CALL_SECONDS = 60.0

#: Replaced in tests: nothing here ever sleeps in CI. A test with a fake clock
#: replaces both, `sleep` advancing `clock`.
sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic

#: The `clock` instant no request, wait or answer may pass (`time_limit`).
_UNTIL: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "riigikogu_until", default=None
)


class BudgetExceeded(FetchError):
    """The stage used its requests; what is left is read next night."""


class OutOfTime(BudgetExceeded, http.DeadlineExceeded):
    """The stage used its time (or the nightly step's, ADR 0231); what is left is
    read next night."""


@contextmanager
def time_limit(until: float | None) -> Iterator[None]:
    """Every call inside stops by ``until`` (a `clock` instant); ``None`` is no limit."""
    token = _UNTIL.set(until)
    try:
        yield
    finally:
        _UNTIL.reset(token)


def time_left() -> float | None:
    """Seconds to the nearer of the current limit and the run's deadline
    (`http.remaining`: the nightly step's budget, ADR 0231); ≤ 0 once it passed,
    ``None`` without either."""
    until = _UNTIL.get()
    own = None if until is None else until - clock()
    run = http.remaining()
    if own is None or run is None:
        return run if own is None else own
    return min(own, run)


def _out_of_time() -> OutOfTime:
    return OutOfTime("ajapiir täis; ülejäänu loetakse järgmisel ööl")


def _check_time() -> None:
    left = time_left()
    if left is not None and left <= 0:
        raise _out_of_time()


def _nap(seconds: float) -> None:
    """Sleep ``seconds``, unless that would pass the limit: then the reading ends now."""
    left = time_left()
    if left is not None and seconds >= left:
        raise _out_of_time()
    sleep(seconds)


def _door() -> dict[str, float]:
    """The fenced door's ``deadline`` (a `time.monotonic` instant) under a limit; else nothing."""
    left = time_left()
    if left is None:
        return {}
    return {"deadline": time.monotonic() + min(max(left, 0.0), CALL_SECONDS)}


@dataclass
class Budget:
    """Requests a stage may make, and what it has spent (counted into its `StageRun`)."""

    limit: int
    spent: int = 0
    rate_limited: int = 0
    errors: list[str] = field(default_factory=list)

    def take(self) -> None:
        if self.spent >= self.limit:
            raise BudgetExceeded(f"päringute piir ({self.limit}) täis")
        self.spent += 1


def _path_key(path: str) -> str:
    return path[:200]


def _wait_turn(path: str) -> None:
    """Take this call's turn in the shared ledger, sleeping outside the lock until it comes."""
    key = _path_key(path)
    while True:
        _check_time()
        try:
            wait = _take_turn(key)
        except OperationalError as error:
            if not _lock_timed_out(error):
                raise
            # The ledger's holder did not let go within LEDGER_LOCK_TIMEOUT_MS.
            raise FetchError("Riigikogu päringute järjekord on hõivatud") from None
        if wait <= 0:
            return
        _nap(min(wait, MAX_WAIT_SECONDS))


def _lock_timed_out(error: OperationalError) -> bool:
    cause = error.__cause__
    return (getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)) == "55P03"


def _take_turn(key: str) -> float:
    """Record this call and return 0 when it is its turn, else the seconds to wait."""
    from app.riigikogu.models import ApiCall

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL lock_timeout = {int(LEDGER_LOCK_TIMEOUT_MS)}")
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LEDGER_LOCK])
        now = timezone.now()
        last = ApiCall.objects.order_by("-at").values_list("at", flat=True).first()
        wait = 0.0
        if last is not None:
            wait = max(wait, (last + MIN_SPACING - now).total_seconds())
        recent = list(
            ApiCall.objects.filter(path=key, at__gte=now - dt.timedelta(minutes=1))
            .order_by("at")
            .values_list("at", flat=True)
        )
        if len(recent) >= PER_PATH_PER_MINUTE:
            oldest = recent[len(recent) - PER_PATH_PER_MINUTE]
            wait = max(wait, (oldest + dt.timedelta(minutes=1) - now).total_seconds())
        if wait <= 0:
            ApiCall.objects.create(path=key, at=now)
            ApiCall.objects.filter(at__lt=now - dt.timedelta(hours=1)).delete()
            return 0.0
        return wait


def _attempt(call: Callable[[], Any], budget: Budget | None, path: str) -> Any:
    tries = 0
    while True:
        _check_time()
        if budget is not None:
            budget.take()
        _wait_turn(path)
        try:
            return call()
        except BudgetExceeded:
            raise
        except FetchError as error:
            tries += 1
            if error.status == 429:
                if budget is not None:
                    budget.rate_limited += 1
                if tries > len(BACKOFF_SECONDS):
                    raise
                wait = (
                    error.retry_after
                    if error.retry_after is not None
                    else BACKOFF_SECONDS[tries - 1]
                )
                if wait > MAX_WAIT_SECONDS:
                    raise
                _nap(wait)
                continue
            transient = error.status is None or error.status >= 500
            if not transient or tries > 2:
                raise
            _nap(BACKOFF_SECONDS[0])


def get_json(
    path: str, params: dict[str, Any] | None = None, *, budget: Budget | None = None
) -> Any:
    """One JSON answer from the API, through the ledger and the retries."""
    return _attempt(lambda: http.get_json(HOST, path, params, **_door()), budget, path)


def get_bytes(
    path: str, *, budget: Budget | None = None, max_bytes: int = 5 * 1024 * 1024
) -> bytes:
    """One file from the API (a letter's HTML or PDF)."""
    return _attempt(
        lambda: http.get_bytes(HOST, path, max_bytes=max_bytes, **_door()), budget, path
    )


def rows(answer: Any) -> list[dict[str, Any]]:
    """The rows of a paged answer, or of a plain list; anything else is an error, not «none»."""
    if isinstance(answer, list):
        return [row for row in answer if isinstance(row, dict)]
    if isinstance(answer, dict):
        embedded = answer.get("_embedded")
        if isinstance(embedded, dict):
            content = embedded.get("content")
            if isinstance(content, list):
                return [row for row in content if isinstance(row, dict)]
        if "page" in answer and not embedded:
            return []
    raise FetchError("Riigikogu vastus on võõra kujuga")


def pages(
    path: str, params: dict[str, Any], *, budget: Budget | None = None, max_pages: int = 20
) -> Iterator[dict[str, Any]]:
    """Every row of a paged search, page by page; a malformed page raises, never ends quietly."""
    for number in range(max_pages):
        answer = get_json(path, {**params, "page": number, "size": PAGE_SIZE}, budget=budget)
        found = rows(answer)
        yield from found
        page = answer.get("page") if isinstance(answer, dict) else None
        total_pages = page.get("totalPages") if isinstance(page, dict) else None
        if not found or not isinstance(total_pages, int) or number + 1 >= total_pages:
            return
    raise FetchError(f"Riigikogu otsingus on üle {max_pages} lehekülje")


def page_url_for_bill(uuid: Any) -> str:
    """The bill's public page (the address the register watch already writes)."""
    return f"https://www.riigikogu.ee/tegevus/eelnoud/{uuid}/"


def document_api_url(uuid: Any) -> str:
    """The document's own record. The public site has no stable per-document page
    address we could verify (24.11.2026), so a document links to its file, and its
    bill links to the bill's page."""
    return f"https://api.riigikogu.ee/api/documents/{uuid}"


def file_url(uuid: Any) -> str:
    return f"https://api.riigikogu.ee/api/files/{uuid}/download"
