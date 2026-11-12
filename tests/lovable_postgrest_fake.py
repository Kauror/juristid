"""A PostgREST stand-in behind `lovable_source.HttpTransport`'s ``opener=`` seam.

The in-process ``FakeLovable`` classes stub ``Transport.select`` and so never
exercise paging; this answers the transport's real HTTP requests instead, the
way Supabase's PostgREST does (audit 2026-11-11 QA-01, §D.2 «Transport
realism»), so a truncated or inconsistent reading can be provoked on purpose:

* the password sign-in (the one POST) and ``GET /rest/v1/<table>`` only; any
  other request is recorded and fails the test;
* ``select=`` (only allowlisted columns, as `lovable_source.allowlisted` says),
  the ``in.(…)``, ``eq.``, ``is.null`` and ``not.is.null`` filters;
* ``order`` over one or more columns; rows that tie on it come back in an order
  that may change from request to request (``shuffle_ties``), as a database
  with no unique sort key may;
* ``limit`` / ``offset``, under a server ``max_rows`` cap (Supabase's
  ``db-max-rows``) that silently shortens a page;
* ``Content-Range`` (``first-last/total`` with ``Prefer: count=exact``,
  ``first-last/*`` without), which ``content_range`` can override per request;
* 401 / 5xx injection (``fail``: a list of status codes the next requests
  answer with) and a hook between requests (``between``) that edits the rows
  to make the source change while it is paged.

Every value is invented. Nothing leaves the process.
"""

from __future__ import annotations

import base64
import io
import json
import random
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any

from app.legacy_import import lovable_source

ORIGIN = "https://naidisprojekt.supabase.co"


def jwt(role: str = "authenticated") -> str:
    body = base64.urlsafe_b64encode(json.dumps({"role": role, "sub": "u1"}).encode())
    return f"x.{body.decode().rstrip('=')}.y"


def config(url: str = ORIGIN) -> lovable_source.SourceConfig:
    return lovable_source.SourceConfig(
        url=url,
        publishable_key="sb_publishable_naidis",
        email="e@x.invalid",
        password="pw-1",  # noqa: S106 - invented
    )


class _Answer:
    def __init__(self, body: Any, url: str, headers: dict[str, str]) -> None:
        self.raw = json.dumps(body).encode()
        self.url = url
        self.headers = headers

    def read(self, _n: int) -> bytes:
        return self.raw

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


def _matches(row: dict[str, Any], column: str, condition: str) -> bool:
    value = row.get(column)
    if condition == "is.null":
        return value is None
    if condition == "not.is.null":
        return value is not None
    if condition.startswith("eq."):
        return str(value) == condition[3:]
    if condition.startswith("in.(") and condition.endswith(")"):
        return str(value) in set(condition[4:-1].split(","))
    if condition.startswith("not.in.(") and condition.endswith(")"):
        return str(value) not in set(condition[8:-1].split(","))
    raise AssertionError(f"the fake does not model the filter {column}={condition}")


class PostgrestFake:
    """``opener(request, timeout)`` over ``tables`` ({table: [row, …]}), or over
    ``source(table, columns, filters, order)`` when given (an in-process fake's
    ``select``, for the full rows a run reads)."""

    def __init__(
        self,
        tables: dict[str, list[dict[str, Any]]] | None = None,
        *,
        source: Callable[..., list[dict[str, Any]]] | None = None,
        max_rows: int | None = None,
        shuffle_ties: bool = False,
        seed: int = 1999,
    ) -> None:
        self.tables = tables if tables is not None else {}
        self.source = source
        self.max_rows = max_rows
        self.shuffle_ties = shuffle_ties
        self.random = random.Random(seed)  # noqa: S311 - a test's tie order
        #: Status codes the next requests answer with, first first (401, 503, …).
        self.fail: list[int] = []
        #: ``(table, offset) -> header``: a ``Content-Range`` the server lies with.
        self.content_range: dict[tuple[str, int], str] = {}
        #: Called before every table read with ``(table, offset)``.
        self.between: Callable[[str, int], None] | None = None
        #: Drop the last row of every page (a short read the range does not admit).
        self.short_pages = False
        #: (method, path, query) of everything received.
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    # -- what was asked --------------------------------------------------------

    def reads(self, table: str | None = None) -> list[dict[str, str]]:
        return [
            q
            for m, p, q in self.requests
            if m == "GET" and (table is None or p == f"/rest/v1/{table}")
        ]

    @property
    def methods(self) -> set[str]:
        return {m for m, _p, _q in self.requests}

    # -- the opener ------------------------------------------------------------

    def __call__(self, request: Any, timeout: float) -> Any:
        url = request.full_url
        parts = urllib.parse.urlsplit(url)
        method = request.get_method()
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.requests.append((method, parts.path, dict(query)))
        if method == "POST" and parts.path == "/auth/v1/token":
            return _Answer({"access_token": jwt()}, url, {})
        if method != "GET" or not parts.path.startswith("/rest/v1/"):
            raise AssertionError(f"Lovable received {method} {parts.path}")
        if self.fail:
            status = self.fail.pop(0)
            raise urllib.error.HTTPError(
                url, status, "x", {}, io.BytesIO(json.dumps({"code": "PGRST"}).encode())
            )
        table = parts.path.rsplit("/", 1)[-1]
        columns = query.pop("select").split(",")
        assert lovable_source.allowlisted(table, columns), (table, columns)
        order = query.pop("order")
        offset = int(query.pop("offset", "0"))
        limit = int(query.pop("limit", "1000000"))
        if self.between is not None:
            self.between(table, offset)
        rows = self._rows(table, columns, query, order)
        size = min(limit, self.max_rows) if self.max_rows else limit
        page = rows[offset : offset + size]
        exact = "count=exact" in (request.get_header("Prefer") or "")
        total = str(len(rows)) if exact else "*"
        served = f"{offset}-{offset + len(page) - 1}" if page else "*"
        header = self.content_range.get((table, offset), f"{served}/{total}")
        if self.short_pages and page:
            page = page[:-1]
        return _Answer(page, url, {"Content-Range": header})

    def _rows(
        self, table: str, columns: list[str], filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        if self.source is not None:
            every = [dict(r) for r in self.source(table, columns, filters=filters, order=order)]
        else:
            every = [
                {c: r.get(c) for c in columns if c in r}
                for r in self.tables.get(table, [])
                if all(_matches(r, c, cond) for c, cond in filters.items())
            ]
        keys = [k.split(".")[0] for k in order.split(",") if k]
        if self.shuffle_ties:
            # Ties come back in an order of their own on every request.
            self.random.shuffle(every)
        return sorted(every, key=lambda r: tuple(str(r.get(k)) for k in keys))
