"""Where the MCP server's database work runs: a bounded pool of worker threads.

The server is asynchronous (Starlette and the MCP SDK on anyio) and Django's ORM
is not, so nothing in this process touches the ORM on the event loop. Every
token verification and every reader call goes through `ReaderRuntime.run`,
which:

- admits at most `MCP_MAX_CONCURRENT_CALLS` at a time (an anyio
  CapacityLimiter); the rest wait their turn rather than open more connections;
- gives up after `MCP_CALL_TIMEOUT_SECONDS` with `CallTimedOut`, whose message
  is safe to show. A thread cannot be killed, so the abandoned one finishes on
  its own; the database ends its statement at the same timeout, because the
  `mcp` role's connections carry `statement_timeout` (config/settings.py);
- calls `close_old_connections()` before the work and closes the connection
  after it **in the same thread**, so a pooled worker thread never carries a
  connection from one call into the next and connections never pile up.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import anyio
import anyio.to_thread
from django.conf import settings

T = TypeVar("T")


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


class ReaderRuntime:
    """The bounded worker pool. One per ASGI application."""

    def __init__(self, *, max_concurrent_calls: int, timeout_seconds: float) -> None:
        if max_concurrent_calls < 1:
            raise ValueError("max_concurrent_calls must be at least 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_concurrent_calls = max_concurrent_calls
        self.timeout_seconds = timeout_seconds
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
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(self.max_concurrent_calls)
        return self._limiter

    async def run(self, work: Callable[[], T]) -> T:
        """Run synchronous `work` in a worker thread, bounded and timed out."""
        try:
            with anyio.fail_after(self.timeout_seconds):
                return await anyio.to_thread.run_sync(
                    _in_this_thread_with_fresh_connection,
                    work,
                    limiter=self.limiter,
                    abandon_on_cancel=True,
                )
        except TimeoutError as exc:
            raise CallTimedOut() from exc
