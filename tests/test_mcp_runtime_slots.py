"""A timed-out call keeps its slot until its thread really ends (MCPSEC-10).

`ReaderRuntime.run` gives up on a call after `MCP_CALL_TIMEOUT_SECONDS`, and a
thread cannot be killed: the abandoned one goes on with its own database
connection. It used to hand its slot back the moment the caller gave up, so
slow calls stacked up past `MCP_MAX_CONCURRENT_CALLS` open connections. Now the
slot is a semaphore taken *inside* the worker thread and released only when
the work ends; the caller still gets its timeout answer at once, and a call
that could not get a slot before its deadline never runs at all.
"""

from __future__ import annotations

import threading
import time

import anyio
import pytest

from app.mcp_server.runtime import CallTimedOut, ReaderRuntime


def test_an_abandoned_call_holds_its_slot_and_the_next_one_waits():
    runtime = ReaderRuntime(max_concurrent_calls=1, timeout_seconds=0.3)
    release = threading.Event()
    finished = threading.Event()
    second_ran = threading.Event()

    def slow() -> str:
        release.wait(5)
        finished.set()
        return "slow"

    def quick() -> str:
        second_ran.set()
        return "quick"

    async def scenario() -> None:
        started = time.monotonic()
        with pytest.raises(CallTimedOut):
            await runtime.run(slow)
        # The timeout answer is prompt although the thread runs on.
        assert time.monotonic() - started < 1.5
        assert not finished.is_set()
        # The slow call still holds the only slot: the next call times out
        # without its work ever starting.
        with pytest.raises(CallTimedOut):
            await runtime.run(quick)
        assert not second_ran.is_set()
        release.set()
        for _ in range(100):
            if finished.is_set():
                break
            await anyio.sleep(0.02)
        assert await runtime.run(quick) == "quick"

    anyio.run(scenario)


def test_no_more_than_the_limit_ever_run_at_once():
    runtime = ReaderRuntime(max_concurrent_calls=2, timeout_seconds=0.2)
    lock = threading.Lock()
    active = 0
    peak = 0

    def work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.6)
        with lock:
            active -= 1

    async def call() -> None:
        try:
            await runtime.run(work)
        except CallTimedOut:
            pass

    async def scenario() -> None:
        for _ in range(3):
            async with anyio.create_task_group() as group:
                for _ in range(4):
                    group.start_soon(call)

    anyio.run(scenario)
    time.sleep(1.5)
    assert peak <= 2
