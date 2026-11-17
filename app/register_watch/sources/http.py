"""The one door the register watch uses to the outside (ADR 0217 §5).

`https`, a fixed list of hosts, no redirects, a timeout, a size cap, and a
spacing between calls to one host. `settings.REGISTER_WATCH_FETCH` switches it
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
ALLOWED_HOSTS = frozenset({"api.riigikogu.ee", "dok.hm.ee", "adr.envir.ee", "adr.rik.ee"})
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


def _space(host: str) -> None:
    wait = SPACING_SECONDS.get(host, DEFAULT_SPACING) - (
        time.monotonic() - _last_call.get(host, 0.0)
    )
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def get_json(host: str, path: str, params: dict[str, Any] | None = None) -> Any:
    """GET ``https://host/path?params`` and parse it as JSON, or raise `FetchError`."""
    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        raise FetchError("registrite lugemine on välja lülitatud")
    if host not in ALLOWED_HOSTS or not path.startswith("/"):
        raise FetchError(f"keelatud aadress: {host}")
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"https://{host}{path}" + (f"?{query}" if query else "")
    _space(host)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BYTES + 1)
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


def _text(request: urllib.request.Request, host: str) -> str:
    try:
        with _FORM_OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise FetchError(f"{host} vastas {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{host} ei vastanud ({type(error).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise FetchError(f"{host} vastus on liiga suur")
    return raw.decode("utf-8", errors="replace")


def get_text(host: str, path: str) -> str:
    """GET an HTML page (and keep its session cookie), or raise `FetchError`."""
    _check(host, path)
    _space(host)
    url = f"https://{host}{path}"
    return _text(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), host)


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
