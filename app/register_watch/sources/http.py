"""The one door the register watch uses to the outside (ADR 0217 §5).

`https`, a fixed list of hosts, no redirects, a timeout, a size cap, and a
spacing between calls to one host. A call made inside a person's request may
also carry a ``deadline`` (a `time.monotonic` instant, HR-04). Then:

* the host spacing is not slept past it, and nothing is opened once it passed;
* each address is connected with no more than the time left;
* from the moment the TCP connection exists, a watchdog shuts it down at the
  deadline, so a server that drips the TLS handshake, the headers or the body
  a byte at a time is cut off there and the call raises `FetchError` (a socket
  timeout alone bounds one ``recv``, not a whole read);
* the name lookup (DNS) is *not* bounded by it: ``getaddrinfo`` cannot be
  interrupted. The hosts are the few fixed ones in `ALLOWED_HOSTS`.

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
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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

_last_call: dict[str, float] = {}


class FetchError(Exception):
    """A register could not be read; the message is safe to store and show."""


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


def _connect(
    address: tuple[str, int], timeout: Any, source_address: Any = None, **_: Any
) -> socket.socket:
    """`socket.create_connection`, each address given only the time left, then watched."""
    deadline = _DEADLINE.get()
    host, port = address
    error: OSError | None = None
    for family, kind, proto, _name, target in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
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
            dog.adopt(sock)
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
        raise FetchError(f"{host} ei vastanud ettenähtud aja jooksul")
    return min(TIMEOUT_SECONDS, left)


def _space(host: str, deadline: float | None = None) -> None:
    wait = SPACING_SECONDS.get(host, DEFAULT_SPACING) - (
        time.monotonic() - _last_call.get(host, 0.0)
    )
    if wait > 0:
        if deadline is not None and time.monotonic() + wait >= deadline:
            raise FetchError(f"{host} ei vastanud ettenähtud aja jooksul")
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


def _late(host: str) -> FetchError:
    return FetchError(f"{host} ei vastanud ettenähtud aja jooksul")


def _fetch(opener: Any, request: Any, host: str, limit: int, deadline: float | None) -> bytes:
    """Open and read one answer through ``opener``; every failure is a `FetchError`.

    With a deadline the whole call (handshake, headers, body) runs under a
    `_Watchdog`. An answer the watchdog cut short is refused even when what was
    read looks complete, so a truncated file is never taken for the file.
    """
    dog = _Watchdog(deadline) if deadline is not None else None
    dog_token = _WATCHDOG.set(dog)
    deadline_token = _DEADLINE.set(deadline)
    if dog is not None:
        dog.start()
    try:
        with opener.open(request, timeout=_left(host, deadline)) as response:
            raw = _read(response, limit, host, deadline)
    except urllib.error.HTTPError as error:
        if dog is not None and dog.fired:
            raise _late(host) from None
        raise FetchError(f"{host} vastas {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        if dog is not None and dog.fired:
            raise _late(host) from None
        raise FetchError(f"{host} ei vastanud ({type(error).__name__})") from None
    finally:
        _WATCHDOG.reset(dog_token)
        _DEADLINE.reset(deadline_token)
        if dog is not None:
            dog.stop()
    if dog is not None and dog.fired:
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
    _space(host, deadline)
    url = f"https://{host}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _text(request, host, deadline)


def post_form(host: str, path: str, fields: dict[str, str]) -> str:
    """POST a search form, UTF-8 encoded, in the page's session; the HTML answer."""
    _check(host, path)
    _space(host)
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
    return _text(request, host)


#: A register file brought onto a Teema with «Lisa märkena» (owner, 2026-11-22).
FILE_MAX_BYTES = 25 * 1024 * 1024


def get_bytes(
    host: str, path: str, *, max_bytes: int = FILE_MAX_BYTES, deadline: float | None = None
) -> bytes:
    """GET one file through the same door (hosts, no redirects, timeout), or raise `FetchError`."""
    _check(host, path)
    _space(host, deadline)
    request = urllib.request.Request(f"https://{host}{path}", headers={"User-Agent": USER_AGENT})
    raw = _fetch(_OPENER, request, host, max_bytes + 1, deadline)
    if len(raw) > max_bytes:
        raise FetchError(f"{host} fail on liiga suur")
    return raw
