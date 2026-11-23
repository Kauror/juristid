"""The one door the register watch uses to the outside (ADR 0217 §5).

`https`, a fixed list of hosts, no redirects, a timeout, a size cap, and a
spacing between calls to one host. A call made inside a person's request may
also carry a ``deadline`` (a `time.monotonic` instant, HR-04): the spacing, the
socket timeout and the reading of the answer all stop at it, so a slow register
cannot hold a web worker past it. `settings.REGISTER_WATCH_FETCH` switches it
off (tests, CI, a recovery rehearsal): every call then raises `FetchError`, and
the run records it like any other failure.
"""

from __future__ import annotations

import http.cookiejar
import json
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


_OPENER = urllib.request.build_opener(_NoRedirect)
#: The ADR search keeps its form in a session: one cookie jar for the process.
_FORM_OPENER = urllib.request.build_opener(
    _NoRedirect, urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
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


def _set_socket_timeout(response: Any, seconds: float) -> None:
    """Shorten the open answer's socket timeout, where the response exposes its socket."""
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        settimeout(seconds)


def _read(response: Any, limit: int, host: str, deadline: float | None) -> bytes:
    """Up to ``limit`` bytes; with a deadline, in pieces, never reading past it."""
    if deadline is None:
        return response.read(limit)
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        _set_socket_timeout(response, _left(host, deadline))
        chunk = response.read(min(CHUNK_BYTES, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


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
    try:
        with _OPENER.open(request, timeout=_left(host, deadline)) as response:
            raw = _read(response, MAX_BYTES + 1, host, deadline)
    except urllib.error.HTTPError as error:
        raise FetchError(f"{host} vastas {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{host} ei vastanud ({type(error).__name__})") from None
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
    try:
        with _FORM_OPENER.open(request, timeout=_left(host, deadline)) as response:
            raw = _read(response, MAX_BYTES + 1, host, deadline)
    except urllib.error.HTTPError as error:
        raise FetchError(f"{host} vastas {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{host} ei vastanud ({type(error).__name__})") from None
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
    try:
        with _OPENER.open(request, timeout=_left(host, deadline)) as response:
            raw = _read(response, max_bytes + 1, host, deadline)
    except urllib.error.HTTPError as error:
        raise FetchError(f"{host} vastas {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{host} ei vastanud ({type(error).__name__})") from None
    if len(raw) > max_bytes:
        raise FetchError(f"{host} fail on liiga suur")
    return raw
