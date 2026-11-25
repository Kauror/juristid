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

Nothing here decides what an empty answer means. A caller that gets an empty
page must not read it as «everything is gone» (ADR 0224 §4).
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from app.register_watch.sources import http
from app.register_watch.sources.http import FetchError

HOST = "api.riigikogu.ee"
#: Below the published 1 a second and 12 a minute.
MIN_SPACING = dt.timedelta(seconds=1.2)
PER_PATH_PER_MINUTE = 11
#: The ledger's own lock; the number is this app's.
LEDGER_LOCK = 222_0001
#: Waits between tries of a 429 without ``Retry-After``.
BACKOFF_SECONDS = (5.0, 15.0, 45.0)
#: A ``Retry-After`` longer than this ends the stage's reading instead of sleeping.
MAX_WAIT_SECONDS = 120.0
PAGE_SIZE = 100

#: Replaced in tests: nothing here ever sleeps in CI.
sleep: Callable[[float], None] = time.sleep


class BudgetExceeded(FetchError):
    """The stage used its requests; what is left is read next night."""


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
    from app.riigikogu.models import ApiCall

    key = _path_key(path)
    while True:
        with transaction.atomic():
            with connection.cursor() as cursor:
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
                return
        sleep(min(wait, MAX_WAIT_SECONDS))


def _attempt(call: Callable[[], Any], budget: Budget | None, path: str) -> Any:
    tries = 0
    while True:
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
                sleep(wait)
                continue
            transient = error.status is None or error.status >= 500
            if not transient or tries > 2:
                raise
            sleep(BACKOFF_SECONDS[0])


def get_json(
    path: str, params: dict[str, Any] | None = None, *, budget: Budget | None = None
) -> Any:
    """One JSON answer from the API, through the ledger and the retries."""
    return _attempt(lambda: http.get_json(HOST, path, params), budget, path)


def get_bytes(
    path: str, *, budget: Budget | None = None, max_bytes: int = 5 * 1024 * 1024
) -> bytes:
    """One file from the API (a letter's HTML or PDF)."""
    return _attempt(lambda: http.get_bytes(HOST, path, max_bytes=max_bytes), budget, path)


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
