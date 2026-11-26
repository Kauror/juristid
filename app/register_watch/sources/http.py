"""The one door the register watch uses to the outside (ADR 0217 §5, ADR 0231).

`https`, a fixed list of hosts, no redirects, a timeout, a size cap, and a
spacing between calls to one host. **Every call has a whole-call deadline** (a
`time.monotonic` instant, HR-04, SEC-010): the earliest of

* the caller's own ``deadline=`` (a person's request, a Riigikogu stage);
* the run's deadline (`run_deadline`, or the nightly step's
  ``JURISTID_STEP_DEADLINE`` set by `app.core.nightly_bounded`), so every adapter
  — ADR, PDR, Riigikogu, the sweep, the Government agenda, register files —
  inherits it without passing anything;
* `CALL_SECONDS` from the call's start, so no single call is unbounded.

Under it:

* the host spacing is not slept past it, and nothing is opened once it passed;
* the name lookup (DNS) waits no longer than the time left (it runs in a helper
  thread: ``getaddrinfo`` itself cannot be interrupted, so a lookup that hangs
  is abandoned, not waited for);
* each address is connected with no more than the time left;
* from the moment the TCP connection exists, a watchdog shuts it down at the
  deadline, so a server that drips the TLS handshake, the headers or the body
  a byte at a time is cut off there (a socket timeout alone bounds one
  ``recv``, not a whole read).

A call stopped by its deadline raises `DeadlineExceeded`, a `FetchError`, so
every caller that already records a register failure records this one too.

`settings.REGISTER_WATCH_FETCH` switches it
off (tests, CI, a recovery rehearsal): every call then raises `FetchError`, and
the run records it like any other failure.
"""

from __future__ import annotations

import contextlib
import contextvars
import http.client
import http.cookiejar
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from time import time as _wall_clock
from typing import Any

from django.conf import settings

#: adr.rik.ee is RIK's shared public register (the ministries' ADR): an HTML search form.
ALLOWED_HOSTS = frozenset(
    {"api.riigikogu.ee", "dok.hm.ee", "adr.envir.ee", "adr.rik.ee", "valitsus.ee"}
)
TIMEOUT_SECONDS = 25
MAX_BYTES = 8 * 1024 * 1024
#: Seconds between two calls to one host. The Riigikogu answers 429 to bursts.
SPACING_SECONDS = {"api.riigikogu.ee": 3.0, "adr.rik.ee": 2.5}
DEFAULT_SPACING = 1.0
USER_AGENT = "Juristid registrite jalgimine (Eesti Kaubandus-Toostuskoda)"
#: One call's own ceiling when nothing nearer bounds it: name lookup, connect,
#: TLS, headers and body together. Far above a normal answer (seconds) and below
#: gunicorn's 120-second worker timeout for a call made inside a request.
CALL_SECONDS = 100.0
#: The nightly step's deadline, Unix seconds, set for the step's process by
#: `app.core.nightly_bounded` (the step's execution budget less its grace).
STEP_DEADLINE_ENV = "JURISTID_STEP_DEADLINE"

_last_call: dict[str, float] = {}


class FetchError(Exception):
    """A register could not be read; the message is safe to store and show.

    ``status`` is the HTTP status when the server answered one, and
    ``retry_after`` the seconds its ``Retry-After`` header asked for (a number of
    seconds only; a date is ignored): the Riigikogu client backs off by them
    (ADR 0224 §3). Both are ``None`` for every other failure.
    """

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class DeadlineExceeded(FetchError):
    """The call's whole-call deadline, or the run's, passed (ADR 0231).

    Raised before anything is opened once the run's time is spent, and when a
    call is cut off part-way (a slow name lookup, a dripping handshake, headers
    or body). ``status`` is ``None``: it is not the register's answer.
    """


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 3600 else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Watchdog:
    """Shuts down every connection opened under it once its deadline passes (HR-04).

    It holds a duplicate of each connected socket: ``shutdown`` acts on the
    connection, not the descriptor, so it also ends a TLS socket wrapped around
    the original. The duplicates are closed and the timer stopped and joined
    when the call ends, so neither a descriptor nor a thread outlives it.
    """

    def __init__(self, deadline: float) -> None:
        self.fired = False
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self._timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._fire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def adopt(self, sock: socket.socket) -> None:
        copy = sock.dup()
        with self._lock:
            self._sockets.append(copy)
            fired = self.fired
        if fired:
            _shut(copy)

    def _fire(self) -> None:
        with self._lock:
            self.fired = True
            sockets = list(self._sockets)
        for one in sockets:
            _shut(one)

    def stop(self) -> None:
        self._timer.cancel()
        if self._timer.is_alive():
            self._timer.join()
        with self._lock:
            sockets, self._sockets = self._sockets, []
        for one in sockets:
            one.close()


def _shut(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


_WATCHDOG: contextvars.ContextVar[_Watchdog | None] = contextvars.ContextVar(
    "register_watch_watchdog", default=None
)
_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "register_watch_deadline", default=None
)
#: The run's deadline (`run_deadline`); ``None`` outside one.
_RUN_UNTIL: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "register_watch_run_until", default=None
)
_step_cache: tuple[str, float | None] = ("", None)


def _step_deadline() -> float | None:
    """``JURISTID_STEP_DEADLINE`` as a `time.monotonic` instant, or ``None``."""
    global _step_cache
    raw = os.environ.get(STEP_DEADLINE_ENV, "")
    if not raw:
        return None
    if _step_cache[0] != raw:
        try:
            epoch = float(raw)
        except ValueError:
            # Unreadable is spent, never unbounded: a typo must not lift the limit.
            epoch = 0.0
        _step_cache = (raw, time.monotonic() + (epoch - _wall_clock()))
    return _step_cache[1]


def run_until() -> float | None:
    """The run's deadline (a `time.monotonic` instant): the nearer of `run_deadline`
    and the nightly step's, or ``None`` when neither applies."""
    found = [one for one in (_RUN_UNTIL.get(), _step_deadline()) if one is not None]
    return min(found) if found else None


def remaining() -> float | None:
    """Seconds left of the run's deadline (≤ 0 once passed), or ``None`` without one."""
    until = run_until()
    return None if until is None else until - time.monotonic()


def out_of_time() -> bool:
    """Whether the run's deadline has passed (never, without one)."""
    left = remaining()
    return left is not None and left <= 0


def check_deadline(what: str = "lugemine") -> None:
    """Raise `DeadlineExceeded` if the run's deadline passed: call between steps of work."""
    if out_of_time():
        raise DeadlineExceeded(f"{what}: {OUT_OF_TIME}")


#: What a run stopped by its deadline says about the rest of its work.
OUT_OF_TIME = "ajapiir täis; ülejäänu loetakse järgmisel ööl"


@contextlib.contextmanager
def run_deadline(seconds: float | None = None, *, until: float | None = None) -> Iterator[None]:
    """Bound every call inside by ``seconds`` from now (or the instant ``until``).

    Nested limits only ever narrow: the nearer one wins. ``None`` for both is no
    new limit (an outer one still applies).
    """
    instants = [one for one in (_RUN_UNTIL.get(), until) if one is not None]
    if seconds is not None:
        instants.append(time.monotonic() + seconds)
    token = _RUN_UNTIL.set(min(instants) if instants else None)
    try:
        yield
    finally:
        _RUN_UNTIL.reset(token)


def call_deadline(deadline: float | None = None) -> float:
    """One call's whole-call deadline: the nearest of the caller's, the run's and
    `CALL_SECONDS` from now."""
    instants = [time.monotonic() + CALL_SECONDS]
    instants += [one for one in (deadline, run_until()) if one is not None]
    return min(instants)


def _resolve(host: str, port: int, deadline: float | None) -> list[Any]:
    """``getaddrinfo``, waited for no longer than the time left.

    The lookup cannot be interrupted, so it runs in a daemon thread that is
    abandoned (never joined) if the deadline comes first: the resolver's own
    timeouts end it later, and nothing waits for it.
    """
    if deadline is None:
        return socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError(f"{host}: no time left for the name lookup")
    box: dict[str, Any] = {}

    def look() -> None:
        try:
            box["found"] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except BaseException as error:  # handed to the caller below
            box["error"] = error

    thread = threading.Thread(target=look, name="register-watch-dns", daemon=True)
    thread.start()
    thread.join(left)
    if thread.is_alive():
        raise TimeoutError(f"{host}: the name lookup did not answer in time")
    if "error" in box:
        raise box["error"]
    return list(box["found"])


def _connect(
    address: tuple[str, int], timeout: Any, source_address: Any = None, **_: Any
) -> socket.socket:
    """`socket.create_connection`, each address given only the time left, then watched."""
    deadline = _DEADLINE.get()
    host, port = address
    error: OSError | None = None
    for family, kind, proto, _name, target in _resolve(host, port, deadline):
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            timeout = min(TIMEOUT_SECONDS, left)
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
        except OSError as failure:
            error = failure
            sock.close()
            continue
        dog = _WATCHDOG.get()
        if dog is not None:
            try:
                dog.adopt(sock)
            except OSError:
                sock.close()
                raise
        return sock
    raise error or TimeoutError(f"{host}: no address answered in time")


class _WatchedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _connect


class _WatchedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: Any) -> Any:
        return self.do_open(_WatchedHTTPSConnection, req, context=self._context)  # type: ignore[attr-defined]


_OPENER = urllib.request.build_opener(_NoRedirect, _WatchedHTTPSHandler)
#: The ADR search keeps its form in a session: one cookie jar for the process.
_FORM_OPENER = urllib.request.build_opener(
    _NoRedirect,
    _WatchedHTTPSHandler,
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)


#: A deadline-bound answer is read in pieces this large, the clock checked between them.
CHUNK_BYTES = 256 * 1024


def _left(host: str, deadline: float | None) -> float:
    """The socket timeout for the next step: `TIMEOUT_SECONDS`, or less if the deadline is near."""
    if deadline is None:
        return TIMEOUT_SECONDS
    left = deadline - time.monotonic()
    if left <= 0:
        raise _late(host)
    return min(TIMEOUT_SECONDS, left)


def _space(host: str, deadline: float | None = None) -> None:
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise _late(host)
    wait = SPACING_SECONDS.get(host, DEFAULT_SPACING) - (
        time.monotonic() - _last_call.get(host, 0.0)
    )
    if wait > 0:
        if deadline is not None and time.monotonic() + wait >= deadline:
            raise _late(host)
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def _read(response: Any, limit: int, host: str, deadline: float | None) -> bytes:
    """Up to ``limit`` bytes; with a deadline, in pieces, the clock checked between them."""
    if deadline is None:
        return response.read(limit)
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        _left(host, deadline)
        chunk = response.read(min(CHUNK_BYTES, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _late(host: str) -> DeadlineExceeded:
    return DeadlineExceeded(f"{host} ei vastanud ettenähtud aja jooksul")


def _fetch(opener: Any, request: Any, host: str, limit: int, deadline: float | None) -> bytes:
    """Open and read one answer through ``opener``; every failure is a `FetchError`.

    The whole call (name lookup, handshake, headers, body) runs under a
    `_Watchdog` at `call_deadline`. An answer the watchdog cut short is refused
    even when what was read looks complete, so a truncated file is never taken
    for the file; a call stopped by its deadline is `DeadlineExceeded`.
    """
    deadline = call_deadline(deadline)
    dog = _Watchdog(deadline)
    dog_token = _WATCHDOG.set(dog)
    deadline_token = _DEADLINE.set(deadline)
    dog.start()
    try:
        with opener.open(request, timeout=_left(host, deadline)) as response:
            raw = _read(response, limit, host, deadline)
    except urllib.error.HTTPError as error:
        if dog.fired:
            raise _late(host) from None
        raise FetchError(
            f"{host} vastas {error.code}", status=error.code, retry_after=_retry_after(error)
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        if dog.fired or time.monotonic() >= deadline:
            raise _late(host) from None
        raise FetchError(f"{host} ei vastanud ({type(error).__name__})") from None
    finally:
        _WATCHDOG.reset(dog_token)
        _DEADLINE.reset(deadline_token)
        dog.stop()
    if dog.fired:
        raise _late(host)
    return raw


def get_json(
    host: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    deadline: float | None = None,
) -> Any:
    """GET ``https://host/path?params`` and parse it as JSON, or raise `FetchError`."""
    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        raise FetchError("registrite lugemine on välja lülitatud")
    if host not in ALLOWED_HOSTS or not path.startswith("/"):
        raise FetchError(f"keelatud aadress: {host}")
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"https://{host}{path}" + (f"?{query}" if query else "")
    deadline = call_deadline(deadline)
    _space(host, deadline)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    raw = _fetch(_OPENER, request, host, MAX_BYTES + 1, deadline)
    if len(raw) > MAX_BYTES:
        raise FetchError(f"{host} vastus on liiga suur")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FetchError(f"{host} vastus ei ole JSON") from None


def _check(host: str, path: str) -> None:
    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        raise FetchError("registrite lugemine on välja lülitatud")
    if host not in ALLOWED_HOSTS or not path.startswith("/"):
        raise FetchError(f"keelatud aadress: {host}")


def _text(request: urllib.request.Request, host: str, deadline: float | None = None) -> str:
    raw = _fetch(_FORM_OPENER, request, host, MAX_BYTES + 1, deadline)
    if len(raw) > MAX_BYTES:
        raise FetchError(f"{host} vastus on liiga suur")
    return raw.decode("utf-8", errors="replace")


def get_text(host: str, path: str, *, deadline: float | None = None) -> str:
    """GET an HTML page (and keep its session cookie), or raise `FetchError`."""
    _check(host, path)
    deadline = call_deadline(deadline)
    _space(host, deadline)
    url = f"https://{host}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _text(request, host, deadline)


def post_form(
    host: str, path: str, fields: dict[str, str], *, deadline: float | None = None
) -> str:
    """POST a search form, UTF-8 encoded, in the page's session; the HTML answer."""
    _check(host, path)
    deadline = call_deadline(deadline)
    _space(host, deadline)
    url = f"https://{host}{path}"
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields, encoding="utf-8").encode("ascii"),
        headers={
            "User-Agent": USER_AGENT,
            "Referer": url,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    return _text(request, host, deadline)


#: A register file brought onto a Teema with «Lisa märkena» (owner, 2026-11-22).
FILE_MAX_BYTES = 25 * 1024 * 1024


def get_bytes(
    host: str, path: str, *, max_bytes: int = FILE_MAX_BYTES, deadline: float | None = None
) -> bytes:
    """GET one file through the same door (hosts, no redirects, timeout), or raise `FetchError`."""
    _check(host, path)
    deadline = call_deadline(deadline)
    _space(host, deadline)
    request = urllib.request.Request(f"https://{host}{path}", headers={"User-Agent": USER_AGENT})
    raw = _fetch(_OPENER, request, host, max_bytes + 1, deadline)
    if len(raw) > max_bytes:
        raise FetchError(f"{host} fail on liiga suur")
    return raw
