"""Where the MCP server's database work runs: a bounded pool of worker threads.

The server is asynchronous (Starlette and the MCP SDK on anyio) and Django's ORM
is not, so nothing in this process touches the ORM on the event loop. Every
token verification and every reader call goes through `ReaderRuntime.run`,
which:

- admits at most `MCP_MAX_CONCURRENT_CALLS` pieces of database work at a time.
  The slot is a `threading.BoundedSemaphore` taken *inside* the worker thread
  and released only when the work there has ended (MCPSEC-10). An anyio limiter
  alone is released the moment its caller is cancelled, while the abandoned
  thread runs on with its own connection — so slow calls used to stack up past
  the limit and compete with `web` for PostgreSQL's connections;
- gives up after `MCP_CALL_TIMEOUT_SECONDS` with `CallTimedOut`, whose message
  is safe to show, and answers at once. The deadline covers waiting for a slot
  too, and a call that did not get one before its deadline — or whose caller
  has already given up — never starts its work. A started thread cannot be
  killed, so it finishes on its own and keeps its slot until it does; the
  database ends its statement at the same timeout, because the `mcp` role's
  connections carry `statement_timeout` (config/settings.py);
- calls `close_old_connections()` before the work and closes the connection
  after it **in the same thread**, so a pooled worker thread never carries a
  connection from one call into the next and connections never pile up.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

import anyio
import anyio.to_thread
from django.conf import settings

T = TypeVar("T")

#: Worker threads may outnumber the slots — a thread waiting for a slot holds
#: no connection — but not without bound.
THREADS_PER_SLOT = 4


class CallTimedOut(Exception):
    """A call took longer than MCP_CALL_TIMEOUT_SECONDS. The message is safe to show."""

    def __init__(self) -> None:
        super().__init__("Juristid took too long to answer. Try a narrower question.")


def _in_this_thread_with_fresh_connection[R](work: Callable[[], R]) -> R:
    """Run `work` in the current worker thread with a connection of its own.

    Django keeps one connection per thread. Opening and closing it here, in the
    thread that uses it, is what keeps a reused pool thread from handing a
    stale or broken connection to the next call.
    """
    from django.db import close_old_connections, connection

    close_old_connections()
    try:
        return work()
    finally:
        connection.close()


class _NoSlot(Exception):
    """The deadline passed, or the caller gave up, before a slot was free."""


class ReaderRuntime:
    """The bounded worker pool. One per ASGI application."""

    def __init__(self, *, max_concurrent_calls: int, timeout_seconds: float) -> None:
        if max_concurrent_calls < 1:
            raise ValueError("max_concurrent_calls must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_concurrent_calls = max_concurrent_calls
        self.timeout_seconds = timeout_seconds
        # Held by the thread doing the work, from before it opens a connection
        # until after it has closed it.
        self._slots = threading.BoundedSemaphore(max_concurrent_calls)
        # Created on first use, inside the event loop that will use it.
        self._limiter: anyio.CapacityLimiter | None = None

    @classmethod
    def from_settings(cls) -> ReaderRuntime:
        return cls(
            max_concurrent_calls=int(settings.MCP_MAX_CONCURRENT_CALLS),
            timeout_seconds=float(settings.MCP_CALL_TIMEOUT_SECONDS),
        )

    @property
    def limiter(self) -> anyio.CapacityLimiter:
        """How many worker threads may exist at once (waiting or working)."""
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(self.max_concurrent_calls * THREADS_PER_SLOT)
        return self._limiter

    def _guarded(self, work: Callable[[], T], deadline: float, abandoned: threading.Event) -> T:
        """In the worker thread: take a slot before the deadline, do the work, release it."""
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._slots.acquire(timeout=remaining):
            raise _NoSlot()
        try:
            if abandoned.is_set():
                # Nobody is waiting for this answer any more: do not start.
                raise _NoSlot()
            return _in_this_thread_with_fresh_connection(work)
        finally:
            self._slots.release()

    async def run(self, work: Callable[[], T]) -> T:
        """Run synchronous `work` in a worker thread, bounded and timed out."""
        deadline = time.monotonic() + self.timeout_seconds
        abandoned = threading.Event()
        try:
            with anyio.fail_after(self.timeout_seconds):
                return await anyio.to_thread.run_sync(
                    self._guarded,
                    work,
                    deadline,
                    abandoned,
                    limiter=self.limiter,
                    abandon_on_cancel=True,
                )
        except TimeoutError as exc:
            abandoned.set()
            raise CallTimedOut() from exc
        except _NoSlot as exc:
            abandoned.set()
            raise CallTimedOut() from exc
        except BaseException:
            abandoned.set()
            raise
