"""The unattended, read-only Lovable reader (docs/adr/0192 §2–§4).

Lovable's own HTTPS API (Supabase PostgREST) as a **signed-in user with no staff
profile**: the source's row-level rules let any signed-in user read live topics
and their commencements, responsible changes and stage links, and every write
path in the source (`save_topic`, `delete_topic`, …) refuses a caller with no
`user_profiles` row. So the identity can read and nothing else, and this module
only ever issues ``GET`` against a fixed allowlist of tables and columns.

* **No generic query.** `TABLES` names every table and column read; a filter is
  a list of UUIDs, never caller text.
* **No privileged key.** The credentials file holds the project's *publishable*
  key and the service login. A token whose ``role`` claim is not
  ``authenticated`` — a service-role key pasted by mistake — is refused before
  anything is read (`refuse_privileged_token`).
* **Bounded.** HTTPS only, a timeout on every request, a page size, a response
  size cap and a row cap.
* **Whole or nothing.** A paged read asks for the exact row count and serves
  every counted row exactly once, whatever page the server cuts, or refuses
  (`HttpTransport.select`); a passing gateway error (502/503/504) is retried a
  bounded number of times, anything else refuses at once.
* **Consistent.** PostgREST reads are separate transactions, so the snapshot is
  read, then the cohort's versions and children are read again; any difference
  is a concurrent edit and the whole read is retried, a bounded number of times
  (`take_snapshot`).
* **Read-only on the wire.** `HttpTransport` sends ``GET`` and the one sign-in
  ``POST`` and refuses any other method; the access probe sends nothing
  write-shaped (owner rule 2026-11-02).

The credentials never reach a log, a report or the snapshot.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.legacy_import.integrity import canonical_sha256

SNAPSHOT_FORMAT = "juristid.lovable-sync-snapshot"
SNAPSHOT_FORMAT_VERSION = 1
LOVABLE_PROJECT = "fba2f6eb-5c33-4c63-875c-560a9bc05830"

#: Every column the synchronisation may read, by table. `markmed` (the source's
#: own notes) and `mitteametlik_arvamus` are not read: neither is synchronised,
#: and what is not needed is not copied (docs/adr/0192 §4).
TOPIC_COLUMNS = (
    "id",
    "nr",
    "aasta",
    "teema",
    "oigusakt",
    "sisse",
    "arvamuse_tahtaeg",
    "valja",
    "valja_kuupaev",
    "vastutaja",
    "uus_vastutaja",
    "hetkeseis",
    "jargmiseks",
    "koja_ettepanek",
    "kellele",
    "tegevuse_tahtaeg",
    "onenote_link",
    "operatiivne_ulatus",
    "version",
    "updated_at",
)
INDEX_COLUMNS = ("id", "nr", "aasta", "version", "operatiivne_ulatus")
TABLES: dict[str, tuple[str, ...]] = {
    "topic": TOPIC_COLUMNS,
    "topic_commencement": ("id", "topic_id", "liik", "kuupaev", "tapsustus", "position"),
    "topic_responsible_change": ("id", "topic_id", "vana_vastutaja", "uus_vastutaja", "alates"),
    "topic_stage_link": ("prev_topic_id", "next_topic_id"),
}

#: The wider observation (docs/adr/0201) reads every live topic. Its topic
#: allowlist is the pilot's plus ``joustumised_hallatud`` (whether the topic's
#: commencements are managed); the pilot snapshot (`TOPIC_COLUMNS`) is
#: unchanged. Never read: ``allikas``, ``raw_hyperlink``, ``vastutaja_user_id``,
#: ``*_by`` (identity or raw import data), ``markmed``, ``mitteametlik_arvamus``,
#: and the raw import tables ``topic_source_row`` / ``topic_raw_rows``.
OBSERVE_TOPIC_COLUMNS = (*TOPIC_COLUMNS, "joustumised_hallatud")
OBSERVE_TABLES: dict[str, tuple[str, ...]] = {
    "topic": OBSERVE_TOPIC_COLUMNS,
    "topic_commencement": TABLES["topic_commencement"],
    "topic_responsible_change": TABLES["topic_responsible_change"],
    "topic_stage_link": TABLES["topic_stage_link"],
    #: Related topics («seosed»), readable when both topics are visible.
    "topic_link": ("topic_id", "linked_topic_id"),
}
#: Lovable's own audit ledger, read only by the audit reader
#: (`lovable_observation`) and only when ``LOVABLE_AUDIT_ENABLED`` is on and an
#: owner-approved read path exists (`AUDIT_ACCESS_SQL`). ``actor_id`` is never
#: read: an event keeps only the actor's kind.
AUDIT_TABLES: dict[str, tuple[str, ...]] = {
    "topic_change_event": ("id", "topic_id", "action", "actor_kind", "version", "occurred_at"),
    "topic_change_field": ("event_id", "field", "old_value", "new_value"),
}


#: Ledger field names whose ``old_value`` / ``new_value`` may be **requested**
#: at all (review of PR #586: the prohibition is on collection, not only on
#: storage): the topic's allowlisted columns minus identity and bookkeeping,
#: and the ledger's own composite names. A constant — never caller text.
AUDIT_VALUE_FIELDS = frozenset(
    {c for c in TOPIC_COLUMNS if c not in ("id", "version", "updated_at")}
    | {"joustumised", "joustumised_hallatud", "menetlusetapid", "seosed"}
)
#: Never requested with a value, whatever an allowlist says.
AUDIT_FIELD_NEVER = frozenset({"markmed", "mitteametlik_arvamus"})
if AUDIT_VALUE_FIELDS & AUDIT_FIELD_NEVER:  # pragma: no cover - a constant
    raise RuntimeError("An excluded ledger field is on the value allowlist.")
#: The two value columns of ``topic_change_field``.
AUDIT_VALUE_COLUMNS = ("old_value", "new_value")
#: What a value read selects, and what a names-only read selects.
AUDIT_FIELD_VALUE_SELECT = ("event_id", "field", "old_value", "new_value")
AUDIT_FIELD_NAME_SELECT = ("event_id", "field")
_FIELD_NAME_LIST = re.compile(r"in\.\(([a-z_]+(?:,[a-z_]+)*)\)")


def audit_value_filter() -> str:
    """The source-side filter a value read must carry: ``in.(<allowlisted>)``."""
    return "in.(" + ",".join(sorted(AUDIT_VALUE_FIELDS)) + ")"


def audit_name_filter() -> str:
    """The names-only read's filter: every field *not* on the value allowlist."""
    return "not.in.(" + ",".join(sorted(AUDIT_VALUE_FIELDS)) + ")"


def check_audit_query(table: str, columns: Iterable[str], filters: dict[str, str]) -> None:
    """Refuse, before any request, a ledger read that could collect a value of
    a field off the allowlist: ``old_value`` / ``new_value`` are selectable
    only with a positive ``field=in.(…)`` filter naming allowlisted fields
    only, and no other filter beside it and ``event_id``."""
    if table != "topic_change_field" or not set(columns) & set(AUDIT_VALUE_COLUMNS):
        return
    names = _FIELD_NAME_LIST.fullmatch(filters.get("field", ""))
    if (
        set(filters) - {"event_id", "field"}
        or names is None
        or not set(names.group(1).split(",")) <= AUDIT_VALUE_FIELDS
    ):
        raise SourceError(
            "NOT_ALLOWLISTED",
            "Ledger values are read only for allowlisted field names, filtered at the source.",
        )


def allowlisted(table: str, columns: Iterable[str]) -> bool:
    """Whether ``columns`` of ``table`` are on one of the read allowlists."""
    wanted = set(columns)
    return any(
        table in allow and wanted <= set(allow[table])
        for allow in (TABLES, OBSERVE_TABLES, AUDIT_TABLES)
    )


TIMEOUT_SECONDS = 30
PAGE_SIZE = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_INDEX_ROWS = 20_000
ID_CHUNK = 50
CONSISTENCY_ATTEMPTS = 3
#: A paged read asks PostgREST for the exact row count (``Prefer: count=exact``)
#: and serves every row of it, or refuses (`HttpTransport.select`, audit
#: 2026-11-11 QA-01 / C-L-06): a server ``max-rows`` cap below `PAGE_SIZE`, a
#: count that moves between pages, a page that overlaps or skips another, or a
#: short read is never accepted as the whole table.
_CONTENT_RANGE = re.compile(r"^(?:(\d+)-(\d+)|\*)/(\d+|\*)$")
#: Answers a GET retries (a gateway or the service briefly unavailable), at most
#: `READ_ATTEMPTS` times in all; a GET repeated changes nothing at the source.
#: Anything else — 401, 403, 500, a refusal — refuses at once.
RETRY_STATUS = frozenset({"HTTP_502", "HTTP_503", "HTTP_504"})
READ_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 5.0
#: The only non-GET request ever sent: the service login's password sign-in.
SIGN_IN_PATH = "/auth/v1/token?grant_type=password"


logger = logging.getLogger("app.legacy_import.lovable")


class SourceError(Exception):
    """The source could not be read safely. ``code`` is for the run ledger; the
    message never carries a credential or a row."""

    def __init__(self, code: str, message: str, *, postgrest_code: str = "") -> None:
        super().__init__(message)
        self.code = code
        #: PostgREST's own error code (a SQLSTATE such as ``42501``, or ``PGRST…``)
        #: from an error answer's JSON body, when it had one. Never its message.
        self.postgrest_code = postgrest_code


def _postgrest_code(error: urllib.error.HTTPError) -> str:
    """The ``code`` of a PostgREST error body, and nothing else from it."""
    try:
        body = json.loads(error.read(4096) or b"null")
    except (ValueError, OSError, AttributeError, TypeError):
        return ""
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{1,12}", code) else ""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceConfig:
    url: str
    publishable_key: str
    email: str
    password: str

    def __repr__(self) -> str:  # never print the secret
        return f"SourceConfig(url={self.url!r}, email=<set>, password=<set>)"


REQUIRED_KEYS = (
    "LOVABLE_SYNC_URL",
    "LOVABLE_SYNC_PUBLISHABLE_KEY",
    "LOVABLE_SYNC_EMAIL",
    "LOVABLE_SYNC_PASSWORD",
)


def load_config(path: str | Path | None = None) -> SourceConfig:
    """The service login: a ``KEY=value`` file, or — what the host scheduler does —
    the same keys in the environment, which Docker filled from the root-only file
    on the host (``--env-file``) so the file never enters the container."""
    import os

    values: dict[str, str] = {}
    if path is None:
        values = {key: os.environ.get(key, "") for key in REQUIRED_KEYS}
    else:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as error:
            raise SourceError(
                "CREDENTIALS_UNREADABLE", "Lovable credentials file cannot be read."
            ) from error
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise SourceError(
            "CREDENTIALS_INCOMPLETE", f"Lovable credentials lack {', '.join(missing)}."
        )
    url = values["LOVABLE_SYNC_URL"].rstrip("/")
    if not url.startswith("https://"):
        raise SourceError("CREDENTIALS_NOT_HTTPS", "LOVABLE_SYNC_URL must be https://.")
    url = authorised_origin(url)
    if values["LOVABLE_SYNC_PUBLISHABLE_KEY"].startswith("sb_secret_"):
        raise SourceError(
            "PRIVILEGED_KEY", "A secret API key is not accepted; use the publishable key."
        )
    return SourceConfig(
        url=url,
        publishable_key=values["LOVABLE_SYNC_PUBLISHABLE_KEY"],
        email=values["LOVABLE_SYNC_EMAIL"],
        password=values["LOVABLE_SYNC_PASSWORD"],
    )


#: The only origin the service login, its token and the publishable key are ever
#: sent to: Lovable's own Supabase project API, ``https://<project ref>.supabase.co``
#: on the default port, with no path, user part, query or fragment.
AUTHORISED_HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.supabase\.co$")


def authorised_origin(url: str) -> str:
    """``url`` as the one origin credentials may go to, or `SourceError`.

    Not any ``https://`` host: a mistyped or substituted ``LOVABLE_SYNC_URL``
    would otherwise receive the service e-mail and password on sign-in."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme != "https"
        or not AUTHORISED_HOST.match(host)
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise SourceError(
            "CREDENTIALS_HOST",
            "LOVABLE_SYNC_URL must be the Lovable project's https://<ref>.supabase.co origin.",
        )
    return f"https://{host}"


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as error:
        raise SourceError("TOKEN_UNREADABLE", "The sign-in token is not a JWT.") from error
    return claims if isinstance(claims, dict) else {}


def refuse_privileged_token(token: str) -> None:
    """Only an ordinary signed-in user reads. Any other role stops the run."""
    if _jwt_claims(token).get("role") != "authenticated":
        raise SourceError(
            "PRIVILEGED_TOKEN", "The source identity is not an ordinary signed-in user."
        )


def refuse_privileged_key(key: str) -> None:
    """A legacy JWT-style publishable key must be the anonymous one."""
    if key.count(".") == 2 and _jwt_claims(key).get("role") not in {"anon", None}:
        raise SourceError("PRIVILEGED_KEY", "The API key is not the publishable (anon) key.")


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class Transport(Protocol):
    def select(
        self, table: str, columns: Iterable[str], *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]: ...


Opener = Callable[[urllib.request.Request, float], Any]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A 3xx is never followed: urllib's own handler would resend the bearer
    token and the API key to whatever ``Location`` says, ``http://`` included.
    Returning nothing leaves the 3xx as an `HTTPError`, which `_request` turns
    into ``REDIRECT_REFUSED`` before anything is read."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    # https and the authorised origin are enforced by `load_config` and `_request`.
    return _OPENER.open(request, timeout=timeout)


class HttpTransport:
    """PostgREST over HTTPS as the service login. ``GET`` only."""

    def __init__(
        self,
        config: SourceConfig,
        *,
        opener: Opener = _default_opener,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        refuse_privileged_key(config.publishable_key)
        self.config = config
        self.opener = opener
        self.pause = pause
        self.token = ""
        #: The last answer's ``Content-Range`` (PostgREST's served range and, when
        #: asked for, the exact count), or ``None``.
        self.last_content_range: str | None = None
        #: What this transport has asked for, for the run's counts: requests made
        #: and response bytes received. Never a row.
        self.requests = 0
        self.bytes_read = 0

    def _request(self, method: str, path: str, *, body: dict | None = None, headers: dict) -> Any:
        # Read-only by construction (owner rule 2026-11-02): GET, and the one
        # password sign-in POST. No write-shaped request is ever sent, not even
        # as a probe.
        if not (method == "GET" or (method == "POST" and path.startswith(SIGN_IN_PATH))):
            raise SourceError("NOT_READ_ONLY", f"{method} is never sent to Lovable.")
        origin = authorised_origin(self.config.url)
        target = origin + path
        if not path.startswith("/") or urllib.parse.urlsplit(target).netloc != origin[8:]:
            raise SourceError("CREDENTIALS_HOST", "A Lovable request left the authorised origin.")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - the authorised https origin only
            target, data=data, method=method, headers=headers
        )
        try:
            with self.opener(request, TIMEOUT_SECONDS) as response:
                landed = str(getattr(response, "geturl", lambda: target)() or target)
                if not landed.startswith("https://") or (
                    urllib.parse.urlsplit(landed).netloc != origin[8:]
                ):
                    raise SourceError(
                        "REDIRECT_REFUSED", "Lovable answered from another origin; nothing read."
                    )
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                answer_headers = getattr(response, "headers", None)
                self.last_content_range = (
                    answer_headers.get("Content-Range") if answer_headers else None
                )
                self.requests += 1
                self.bytes_read += len(raw or b"")
        except urllib.error.HTTPError as error:
            self.requests += 1
            if 300 <= error.code < 400:
                raise SourceError(
                    "REDIRECT_REFUSED",
                    f"Lovable redirected {path.split('?')[0]}; redirects are never followed.",
                ) from None
            raise SourceError(
                f"HTTP_{error.code}",
                f"Lovable answered {error.code} on {path.split('?')[0]}.",
                postgrest_code=_postgrest_code(error),
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourceError(
                "UNREACHABLE", f"Lovable could not be reached ({type(error).__name__})."
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SourceError("RESPONSE_TOO_LARGE", "A Lovable response exceeded the size cap.")
        try:
            return json.loads(raw or b"null")
        except ValueError:
            raise SourceError("RESPONSE_NOT_JSON", "A Lovable response was not JSON.") from None

    def sign_in(self) -> None:
        answer = self._request(
            "POST",
            SIGN_IN_PATH,
            body={"email": self.config.email, "password": self.config.password},
            headers={"apikey": self.config.publishable_key, "Content-Type": "application/json"},
        )
        token = (answer or {}).get("access_token") if isinstance(answer, dict) else None
        if not token:
            raise SourceError("SIGN_IN_FAILED", "The Lovable service login was refused.")
        refuse_privileged_token(token)
        self.token = token

    def select(
        self, table: str, columns: Iterable[str], *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        """Every row the source holds for the query, or `SourceError`.

        Paged by what the server actually served, not by what was asked for, and
        held to PostgREST's exact count: a page smaller than asked (a server
        ``max-rows`` below `PAGE_SIZE`) is followed by the next, the count may not
        move between pages, a row may not appear twice, and the rows add up to
        the count exactly. A source population that is truncated or inconsistent
        refuses the read; it is never taken for a smaller source (audit
        2026-11-11 QA-01, C-L-06)."""
        columns = tuple(columns)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        total: int | None = None
        offset = 0
        while True:
            page = self._page(
                table, columns, filters=filters, order=order, offset=offset, count=True
            )
            start, end, reported = _served_range(self.last_content_range, table)
            if reported > MAX_INDEX_ROWS:
                raise SourceError("TOO_MANY_ROWS", f"{table} returned more rows than the cap.")
            if total is None:
                total = reported
            elif reported != total:
                raise SourceError(
                    "PAGES_INCONSISTENT", f"{table} changed its row count while it was paged."
                )
            if page and (start != offset or end - start + 1 != len(page)):
                raise SourceError(
                    "PAGES_INCONSISTENT", f"{table} served a page that is not the range it names."
                )
            for row in page:
                key = json.dumps(row, sort_keys=True, default=str)
                if key in seen:
                    raise SourceError(
                        "PAGES_INCONSISTENT", f"{table} served a row twice across pages."
                    )
                seen.add(key)
            rows.extend(page)
            if len(rows) >= total:
                break
            if not page:
                raise SourceError(
                    "TRUNCATED", f"{table} served {len(rows)} of the {total} rows it counted."
                )
            offset += len(page)
        if len(rows) != total:
            raise SourceError(
                "PAGES_INCONSISTENT", f"{table} served {len(rows)} rows; it counted {total}."
            )
        return rows

    def select_page(
        self,
        table: str,
        columns: Iterable[str],
        *,
        filters: dict[str, str],
        order: str,
        limit: int = PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """One page only — what the audit reader commits before asking for more."""
        return self._page(table, tuple(columns), filters=filters, order=order, limit=limit)

    def _page(
        self,
        table: str,
        columns: tuple[str, ...],
        *,
        filters: dict[str, str],
        order: str,
        offset: int = 0,
        limit: int = PAGE_SIZE,
        count: bool = False,
    ) -> list[dict[str, Any]]:
        if not allowlisted(table, columns):
            raise SourceError("NOT_ALLOWLISTED", f"Reading {table} is not allowlisted.")
        check_audit_query(table, columns, filters)
        if not self.token:
            self.sign_in()
        query = {"select": ",".join(columns), "order": order, **filters}
        query.update({"limit": str(min(limit, PAGE_SIZE)), "offset": str(offset)})
        headers = {
            "apikey": self.config.publishable_key,
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if count:
            headers["Prefer"] = "count=exact"
        path = f"/rest/v1/{table}?{urllib.parse.urlencode(query)}"
        attempt = 1
        while True:
            try:
                page = self._request("GET", path, headers=headers)
                break
            except SourceError as error:
                if error.code not in RETRY_STATUS or attempt >= READ_ATTEMPTS:
                    raise
                logger.warning(
                    "lovable: %s answered %s; read attempt %s of %s",
                    table,
                    error.code,
                    attempt,
                    READ_ATTEMPTS,
                )
                self.pause(RETRY_PAUSE_SECONDS * attempt)
                attempt += 1
        if not isinstance(page, list):
            raise SourceError("RESPONSE_SHAPE", f"{table} did not answer with rows.")
        return only_columns(page, columns, table=table)


def _served_range(content_range: str | None, table: str) -> tuple[int, int, int]:
    """``(first, last, total)`` from PostgREST's ``Content-Range`` (``0-999/2364``;
    ``*/2364`` for an empty page). No header, or no exact count, refuses: the
    read cannot then prove it saw every row."""
    match = _CONTENT_RANGE.match((content_range or "").strip())
    if match is None or match.group(3) == "*":
        raise SourceError("NO_ROW_COUNT", f"{table} answered without an exact row count.")
    first, last = match.group(1), match.group(2)
    return (
        int(first) if first is not None else -1,
        int(last) if last is not None else -2,
        int(match.group(3)),
    )


def only_columns(
    rows: Iterable[Any], columns: Iterable[str], *, table: str
) -> list[dict[str, Any]]:
    """Each row cut down to the columns asked for: a column the source sends
    beyond the allowlist is dropped here, so it never reaches a snapshot.

    A NUL (U+0000) inside a string is dropped here too. PostgreSQL holds it in
    neither ``text`` nor ``jsonb``, so before this the first consumer that could
    not hold it was the snapshot's INSERT, whose ``DataError`` failed the whole
    night as ``INTERNAL`` without naming a table or a row — and every night after
    it, until somebody edited Lovable (security review 2026-10-29, C-02). Nothing
    Juristid could ever store is lost by the drop; the log names the table, the
    row's id and the column, never the value. Other control characters pass
    unchanged, as they do through Juristid's own title path.
    """
    wanted = tuple(columns)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("RESPONSE_SHAPE", f"{table} answered with something not a row.")
        kept: dict[str, Any] = {}
        for column in wanted:
            if column not in row:
                continue
            value = row[column]
            if isinstance(value, str) and "\x00" in value:
                logger.warning(
                    "lovable: %s row %s column %s held U+0000; dropped before the snapshot",
                    table,
                    row.get("id", "?"),
                    column,
                )
                value = value.replace("\x00", "")
            kept[column] = value
        out.append(kept)
    return out


def _in(ids: Iterable[str]) -> str:
    return "in.(" + ",".join(str(i) for i in ids) + ")"


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


def _select(
    transport: Transport, table: str, columns: Iterable[str], *, filters: dict[str, str], order: str
) -> list[dict[str, Any]]:
    """Every snapshot read, whatever the transport: only the allowlisted columns
    of an allowlisted table are kept for the durable snapshot (docs/adr/0192 §4)."""
    columns = tuple(columns)
    if table not in TABLES or not set(columns) <= set(TABLES[table]):
        raise SourceError("NOT_ALLOWLISTED", f"Reading {table} is not allowlisted.")
    rows = transport.select(table, columns, filters=filters, order=order)
    return only_columns(rows, columns, table=table)


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(items), ID_CHUNK):
        yield items[start : start + ID_CHUNK]


def _read_cohort(transport: Transport, ids: list[str]) -> dict[str, Any]:
    topics: list[dict[str, Any]] = []
    commencements: list[dict[str, Any]] = []
    responsible: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for chunk in _chunks(ids):
        topics += _select(transport, "topic", TOPIC_COLUMNS, filters={"id": _in(chunk)}, order="id")
        commencements += _select(
            transport,
            "topic_commencement",
            TABLES["topic_commencement"],
            filters={"topic_id": _in(chunk)},
            order="topic_id,position,id",
        )
        responsible += _select(
            transport,
            "topic_responsible_change",
            TABLES["topic_responsible_change"],
            filters={"topic_id": _in(chunk)},
            order="topic_id,id",
        )
        for column in ("prev_topic_id", "next_topic_id"):
            links += _select(
                transport,
                "topic_stage_link",
                TABLES["topic_stage_link"],
                filters={column: _in(chunk)},
                order="prev_topic_id,next_topic_id",
            )
    by_id: dict[str, Any] = {}
    for row in topics:
        by_id[str(row["id"])] = {
            "row": row,
            "commencements": [],
            "responsible_changes": [],
            "stage_links": [],
        }
    for row in commencements:
        if str(row["topic_id"]) in by_id:
            by_id[str(row["topic_id"])]["commencements"].append(row)
    for row in responsible:
        if str(row["topic_id"]) in by_id:
            by_id[str(row["topic_id"])]["responsible_changes"].append(row)
    seen_links: set[tuple[str, str]] = set()
    for row in links:
        pair = (str(row["prev_topic_id"]), str(row["next_topic_id"]))
        if pair in seen_links:
            continue
        seen_links.add(pair)
        for side in pair:
            if side in by_id:
                by_id[side]["stage_links"].append({"prev": pair[0], "next": pair[1]})
    return by_id


def _index(transport: Transport) -> list[dict[str, Any]]:
    return _select(transport, "topic", INDEX_COLUMNS, filters={}, order="id")


def take_snapshot(
    transport: Transport,
    cohort_ids: list[str],
    *,
    mapping_version: str,
    now: Callable[[], dt.datetime] | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """One consistent reading of the cohort and an index of every live topic.

    Index, cohort, index again, cohort again: a version that moved or a child
    record whose content hash moved between the two passes is an edit made while
    reading, and the whole reading is repeated (at most `CONSISTENCY_ATTEMPTS`
    times) rather than stitched together.
    """
    clock = now or (lambda: dt.datetime.now(dt.UTC))
    ids = sorted(str(i) for i in cohort_ids)
    for attempt in range(1, CONSISTENCY_ATTEMPTS + 1):
        taken_at = clock()
        first_index = _index(transport)
        first = _read_cohort(transport, ids)
        second_index = _index(transport)
        second = _read_cohort(transport, ids)
        if canonical_sha256(first) == canonical_sha256(second) and canonical_sha256(
            first_index
        ) == canonical_sha256(second_index):
            document = {
                "format": SNAPSHOT_FORMAT,
                "format_version": SNAPSHOT_FORMAT_VERSION,
                "project": LOVABLE_PROJECT,
                "taken_at": taken_at.isoformat(),
                "mapping_version": mapping_version,
                "attempts": attempt,
                "index": first_index,
                "cohort": first,
            }
            document["sha256"] = canonical_sha256(document)
            return document
        if attempt < CONSISTENCY_ATTEMPTS:
            pause(5 * attempt)
    raise SourceError("INCONSISTENT", "Lovable changed while it was read, on every attempt.")


def verify_snapshot(document: dict[str, Any]) -> None:
    """A stored or received snapshot is what it claims to be."""
    if (
        document.get("format") != SNAPSHOT_FORMAT
        or document.get("format_version") != SNAPSHOT_FORMAT_VERSION
    ):
        raise SourceError("SNAPSHOT_FORMAT", "The snapshot is not a Lovable sync snapshot.")
    body = {key: value for key, value in document.items() if key != "sha256"}
    if canonical_sha256(body) != document.get("sha256"):
        raise SourceError("SNAPSHOT_DIGEST", "The snapshot's digest does not match its content.")


# ---------------------------------------------------------------------------
# The access probe (docs/adr/0192 §2): what the owner asked to see proven with
# the actual account before the job is switched on
# ---------------------------------------------------------------------------

#: A probe check's verdict. Only PASS proves anything; INDETERMINATE means the
#: probe could not prove the point, and blocks activation exactly as FAIL does
#: until the read-only inspection (`GRANT_INSPECTION_SQL`) settles it.
PASS, FAIL, INDETERMINATE = "PASS", "FAIL", "INDETERMINATE"

#: The status codes that are the source refusing *this caller*: not signed in
#: enough (401) or not allowed (403). Anything else — a 404 for a path that does
#: not exist, a 400, a 405 — says nothing about what the identity may see.
REFUSAL_STATUS = {"HTTP_401", "HTTP_403"}
#: PostgreSQL's own refusals, as PostgREST reports them in an error body:
#: insufficient privilege, and invalid authorization.
REFUSAL_SQLSTATE = {"42501", "28000"}

#: The write side is never probed over HTTP (owner rule 2026-11-02): no write
#: RPC, no PATCH/DELETE, no ``rpc/topic_writer`` or ``rpc/can_access_admin_view``
#: — not even against a UUID that cannot exist. It is settled only by the
#: read-only catalogue inspection, so the probe's verdict is never PASS.
WRITE_SIDE_CHECK = (
    "write refusal: not probed over HTTP (owner rule 2026-11-02) — run `lovable_sync access-sql`"
)


@dataclass(frozen=True)
class ProbeCheck:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def __iter__(self) -> Any:  # (name, ok, detail), as the checks always read
        return iter((self.name, self.ok, self.detail))


@dataclass
class ProbeResult:
    checks: list[ProbeCheck]

    @property
    def passed(self) -> bool:
        return all(check.status == PASS for check in self.checks)

    @property
    def verdict(self) -> str:
        """PASS only when every check proved its point; any FAIL is FAIL;
        otherwise INDETERMINATE — never enough to switch the job on."""
        statuses = {check.status for check in self.checks}
        if FAIL in statuses:
            return FAIL
        return PASS if statuses == {PASS} else INDETERMINATE


def _hidden_verdict(error: SourceError | None, rows: Any) -> tuple[str, str]:
    """A read the identity must not see: no rows, or an explicit refusal."""
    if error is not None:
        if error.code in REFUSAL_STATUS or error.postgrest_code in REFUSAL_SQLSTATE:
            return PASS, error.code
        return INDETERMINATE, f"{error.code} proves nothing is hidden"
    if rows == []:
        return PASS, "0 rows"
    if isinstance(rows, list):
        return FAIL, f"{len(rows)} rows visible"
    return INDETERMINATE, f"unexpected answer ({type(rows).__name__})"


def access_probe(config: SourceConfig, *, opener: Opener = _default_opener) -> ProbeResult:
    """Read what the job needs, and show the hidden reads stay hidden. GET only.

    Sign-in (the one POST), the token's role, the live topics and their
    children, and ``GET … limit=1`` on the admin-only history and on deleted
    topics. The write side is reported as one INDETERMINATE check and nothing
    write-shaped is sent (owner rule 2026-11-02): whether the identity can
    write is shown only by `GRANT_INSPECTION_SQL`, run read-only in Lovable's SQL
    editor. So the verdict is at best INDETERMINATE, never PASS. Counts only."""
    transport = HttpTransport(config, opener=opener)
    checks: list[ProbeCheck] = []
    transport.sign_in()
    role = _jwt_claims(transport.token).get("role")
    checks.append(
        ProbeCheck(
            "token role is authenticated", PASS if role == "authenticated" else FAIL, str(role)
        )
    )

    # 1. It reads the live topics and their children.
    index = _index(transport)
    checks.append(ProbeCheck("reads live topics", PASS if index else FAIL, f"{len(index)} rows"))
    sample = [str(r["id"]) for r in index[:ID_CHUNK]]
    cohort = _read_cohort(transport, sample) if sample else {}
    checks.append(
        ProbeCheck(
            "reads topic children",
            PASS if sample and len(cohort) == len(sample) else FAIL,
            f"{len(cohort)} topics read",
        )
    )

    # 2. No administrative history, no deleted topics (GET, one row at most,
    #    identity columns only: a visible row is counted, never kept).
    reads = [
        ("hidden: topic_change_event", "/rest/v1/topic_change_event?select=id&limit=1"),
        ("hidden: topic_change_field", "/rest/v1/topic_change_field?select=event_id&limit=1"),
        ("hidden: deleted topics", "/rest/v1/topic?select=id&deleted_at=not.is.null&limit=1"),
    ]
    for name, path in reads:
        try:
            rows, failure = (
                transport._request(
                    "GET",
                    path,
                    headers={
                        "apikey": config.publishable_key,
                        "Authorization": f"Bearer {transport.token}",
                    },
                ),
                None,
            )
        except SourceError as refusal:
            if refusal.code in ("CREDENTIALS_HOST", "REDIRECT_REFUSED", "NOT_READ_ONLY"):
                raise
            rows, failure = None, refusal
        status, detail = _hidden_verdict(failure, rows)
        checks.append(ProbeCheck(name, status, detail))

    # 3. The write side: not probed over HTTP at all.
    checks.append(
        ProbeCheck(WRITE_SIDE_CHECK, INDETERMINATE, "nothing write-shaped was sent to Lovable")
    )
    return ProbeResult(checks)


HIDDEN_TABLES = ("topic_change_event", "topic_change_field")

#: The independent check, for when HTTP cannot prove a refusal (docs/adr/0192
#: status update 2026-10-29). The owner runs it in Lovable's own SQL editor
#: (Cloud → Database → SQL), read-only: every statement is a SELECT on the
#: catalogue, nothing is written, and no Lovable row is touched. Replace the
#: e-mail placeholder with the service login's address before running.
#:
#: Expected for a read-only identity: (1) exactly one user and **no** profile
#: row; (2) row-level security enabled on every listed table; (3) **no** INSERT,
#: UPDATE, DELETE or ALL policy that applies to ``authenticated`` or ``public``
#: on them; (4) every write function either not executable by
#: ``authenticated`` or, if SECURITY DEFINER, its definition calls
#: ``topic_writer()`` or ``caller_is_admin()`` — and (5) ``topic_writer``'s own
#: definition raises when the caller has no ``user_profiles`` row. Anything
#: else: the job is not switched on and the identity is not widened.
GRANT_INSPECTION_SQL = """\
-- 1. The service identity exists once and has no staff profile.
select u.id, (select count(*) from public.user_profiles p where p.user_id = u.id) as profiles
from auth.users u where lower(u.email) = lower('<service login e-mail>');

-- 2. Row-level security is on for every table the job touches or must not see.
select c.relname as table_name, c.relrowsecurity as rls_enabled, c.relforcerowsecurity as rls_forced
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('topic', 'topic_commencement',
  'topic_responsible_change', 'topic_stage_link', 'topic_source_row',
  'topic_change_event', 'topic_change_field', 'user_profiles')
order by 1;

-- 3. Write policies that could apply to a signed-in user (expected: no rows).
select tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public' and tablename in ('topic', 'topic_commencement',
  'topic_responsible_change', 'topic_stage_link', 'topic_source_row',
  'topic_change_event', 'topic_change_field', 'user_profiles')
  and cmd in ('INSERT', 'UPDATE', 'DELETE', 'ALL')
  and (roles && array['authenticated', 'public']::name[])
order by 1, 2;

-- 4. The write functions: who may call them and whether they check the caller.
select p.proname, p.prosecdef as security_definer,
  has_function_privilege('authenticated', p.oid, 'EXECUTE') as authenticated_may_execute,
  (pg_get_functiondef(p.oid) ~* '(topic_writer|caller_is_admin)\\s*\\(') as checks_caller
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in ('save_topic', 'save_topic_with_stages',
  'delete_topic', 'restore_topic', 'testala_save_topic', 'testala_delete_topic',
  'testala_restore_topic', 'testala_purge_topic')
order by 1;

-- 5. topic_writer refuses a caller with no profile (read the definition).
select pg_get_functiondef(p.oid)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'topic_writer';
"""

#: A **proposal**, text only: nothing in Juristid executes it, and it is not to
#: be run in Lovable unless the owner has reviewed and approved it (docs/adr/0201).
#: It gives ONE service identity a SELECT-only path to the audit ledger —
#: ``topic_change_event`` (Juristid's reader never asks for ``actor_id``) and
#: ``topic_change_field`` — through an RLS SELECT policy keyed to that identity's
#: auth uid. No write grant, no admin role, no ``user_profiles`` row; every other
#: caller's access is unchanged. Then the verification queries, read-only.
#: Replace ``<service uid>`` with the uid from `GRANT_INSPECTION_SQL` query 1.
#: With access in place, Juristid still reads it only when the container has
#: ``JURISTID_LOVABLE_AUDIT=1``.
AUDIT_ACCESS_SQL = """\
-- PROPOSAL — review before running. Juristid never runs this. Run in Lovable's
-- SQL editor only with the owner's approval. Replace <service uid>.

-- A. One SELECT policy per audit table, for the one service identity only.
--    Permissive policies are OR-ed, so the existing admin policy is unchanged;
--    no INSERT/UPDATE/DELETE policy and no grant beyond SELECT is added.
create policy juristid_audit_read_event on public.topic_change_event
  as permissive for select to authenticated
  using (auth.uid() = '<service uid>'::uuid);

create policy juristid_audit_read_field on public.topic_change_field
  as permissive for select to authenticated
  using (auth.uid() = '<service uid>'::uuid);

-- (The identity could technically select actor_id too; Juristid's allowlist
--  never asks for it. If actor_id must be unreadable even in principle, the
--  alternative is a view without actor_id owned by a non-superuser, granted
--  SELECT to authenticated and filtered on the same auth.uid(); the reader's
--  table name would then change — an owner decision, not made here.)

-- B. Verification (read-only).
-- B1. The two policies exist, are SELECT-only and name only the service uid.
select tablename, policyname, cmd, roles, qual
from pg_policies
where schemaname = 'public' and tablename in ('topic_change_event', 'topic_change_field')
order by 1, 2;

-- B2. Still no write policy for a signed-in user on any topic table (no rows).
select tablename, policyname, cmd
from pg_policies
where schemaname = 'public'
  and tablename in ('topic', 'topic_commencement', 'topic_responsible_change',
    'topic_stage_link', 'topic_link', 'topic_change_event', 'topic_change_field')
  and cmd in ('INSERT', 'UPDATE', 'DELETE', 'ALL')
  and (roles && array['authenticated', 'public']::name[]);

-- B3. Still no staff profile and no admin standing for the identity
--     (expected: profiles = 0, is_admin = false).
select (select count(*) from public.user_profiles p
        where p.user_id = '<service uid>'::uuid) as profiles,
       public.can_access_admin_view('<service uid>'::uuid) as is_admin;

-- C. Withdrawal, if ever needed.
-- drop policy juristid_audit_read_event on public.topic_change_event;
-- drop policy juristid_audit_read_field on public.topic_change_field;
"""
