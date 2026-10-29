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
* **Consistent.** PostgREST reads are separate transactions, so the snapshot is
  read, then the cohort's versions and children are read again; any difference
  is a concurrent edit and the whole read is retried, a bounded number of times
  (`take_snapshot`).

The credentials never reach a log, a report or the snapshot.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
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

TIMEOUT_SECONDS = 30
PAGE_SIZE = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_INDEX_ROWS = 20_000
ID_CHUNK = 50
CONSISTENCY_ATTEMPTS = 3


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

    def __init__(self, config: SourceConfig, *, opener: Opener = _default_opener) -> None:
        refuse_privileged_key(config.publishable_key)
        self.config = config
        self.opener = opener
        self.token = ""

    def _request(self, method: str, path: str, *, body: dict | None = None, headers: dict) -> Any:
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
        except urllib.error.HTTPError as error:
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
            "/auth/v1/token?grant_type=password",
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
        columns = tuple(columns)
        if table not in TABLES or not set(columns) <= set(TABLES[table]):
            raise SourceError("NOT_ALLOWLISTED", f"Reading {table} is not allowlisted.")
        if not self.token:
            self.sign_in()
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = {"select": ",".join(columns), "order": order, **filters}
            query.update({"limit": str(PAGE_SIZE), "offset": str(offset)})
            page = self._request(
                "GET",
                f"/rest/v1/{table}?{urllib.parse.urlencode(query)}",
                headers={
                    "apikey": self.config.publishable_key,
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
            )
            if not isinstance(page, list):
                raise SourceError("RESPONSE_SHAPE", f"{table} did not answer with rows.")
            rows.extend(only_columns(page, columns, table=table))
            if len(rows) > MAX_INDEX_ROWS:
                raise SourceError("TOO_MANY_ROWS", f"{table} returned more rows than the cap.")
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE


def only_columns(
    rows: Iterable[Any], columns: Iterable[str], *, table: str
) -> list[dict[str, Any]]:
    """Each row cut down to the columns asked for: a column the source sends
    beyond the allowlist is dropped here, so it never reaches a snapshot."""
    wanted = tuple(columns)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("RESPONSE_SHAPE", f"{table} answered with something not a row.")
        out.append({column: row[column] for column in wanted if column in row})
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

#: The source's write paths. Each is called with a UUID that cannot exist, so
#: even a write path that wrongly let the identity in would find nothing to
#: change; what is checked is that it refuses the caller.
WRITE_RPCS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("save_topic", {"p_id": None, "p_expected_version": 0, "p_fields": {}}),
    ("save_topic_with_stages", {"p_id": None, "p_expected_version": 0, "p_fields": {}}),
    ("delete_topic", {"p_id": None, "p_expected_version": 0}),
    ("restore_topic", {"p_id": None, "p_expected_version": 0}),
    ("testala_save_topic", {"p_id": None, "p_expected_version": 0, "p_fields": {}}),
    ("testala_delete_topic", {"p_id": None, "p_expected_version": 0}),
    ("testala_restore_topic", {"p_id": None, "p_expected_version": 0}),
    ("testala_purge_topic", {"p_id": None}),
)

#: A probe check's verdict. Only PASS proves anything; INDETERMINATE means HTTP
#: could not tell a refusal from something else, and blocks activation exactly
#: as FAIL does until the read-only inspection (`GRANT_INSPECTION_SQL`) settles it.
PASS, FAIL, INDETERMINATE = "PASS", "FAIL", "INDETERMINATE"

#: The status codes that are the source refusing *this caller*: not signed in
#: enough (401) or not allowed (403). Anything else — a 404 for a path that does
#: not exist, a 400 from a function that raised for its own reasons, a 405 — says
#: nothing about whether the identity may write.
REFUSAL_STATUS = {"HTTP_401", "HTTP_403"}
#: PostgreSQL's own refusals, as PostgREST reports them in an error body:
#: insufficient privilege, and invalid authorization.
REFUSAL_SQLSTATE = {"42501", "28000"}


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


def write_probe_verdict(
    error: SourceError | None, answer: Any = None, *, kind: str
) -> tuple[str, str]:
    """What one write attempt's answer proves, with no business content.

    ``kind`` is ``"rpc"`` (a write function) or ``"table"`` (PATCH/DELETE on a
    table, aimed at a UUID that cannot exist).

    * 401/403, or PostgreSQL's own permission refusal in the error body → PASS.
    * Any 2xx from a write *function* → FAIL: it ran for this caller.
    * A 2xx from a table write that returned rows → FAIL: something changed.
    * ``200 []`` from a table write → INDETERMINATE: under row-level security a
      forbidden UPDATE and a permitted one that matched no row answer alike.
    * 404 (a missing route or function), any other 4xx, a 5xx, an unreachable
      source or an unexpected body → INDETERMINATE: absence is not refusal.
    """
    if error is not None:
        if error.code in REFUSAL_STATUS or error.postgrest_code in REFUSAL_SQLSTATE:
            return PASS, f"{error.code} {error.postgrest_code}".strip()
        return INDETERMINATE, f"{error.code} {error.postgrest_code}".strip() + " proves no refusal"
    if kind == "rpc":
        return FAIL, "ACCEPTED: the function ran for this caller"
    if isinstance(answer, list) and answer:
        return FAIL, f"ACCEPTED: {len(answer)} row(s) returned"
    if answer in ([], None):
        return INDETERMINATE, "2xx with no rows: a hidden refusal and a no-match look the same"
    return INDETERMINATE, f"unexpected answer ({type(answer).__name__})"


def _attempt(transport: HttpTransport, method: str, path: str, body: Any) -> tuple[Any, Any]:
    try:
        answer = transport._request(
            method,
            path,
            body=body,
            headers={
                "apikey": transport.config.publishable_key,
                "Authorization": f"Bearer {transport.token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
    except SourceError as error:
        if error.code in ("CREDENTIALS_HOST", "REDIRECT_REFUSED"):
            raise
        return error, None
    return None, answer


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
    """Read what the job needs, and try to show nothing more is possible. Counts only.

    Over HTTP alone a write the identity may not make cannot always be told
    from one that found nothing, so this can end INDETERMINATE: the job then
    stays off until `GRANT_INSPECTION_SQL` has been run read-only in Lovable's
    SQL editor and shows no write path for the identity."""
    import uuid

    transport = HttpTransport(config, opener=opener)
    checks: list[ProbeCheck] = []
    transport.sign_in()
    claims = _jwt_claims(transport.token)
    role = claims.get("role")
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

    # 2. It cannot write: every write path must refuse it, explicitly.
    ghost = str(uuid.uuid4())
    for name, body in WRITE_RPCS:
        payload = {k: (ghost if v is None else v) for k, v in body.items()}
        error, answer = _attempt(transport, "POST", f"/rest/v1/rpc/{name}", payload)
        status, detail = write_probe_verdict(error, answer, kind="rpc")
        checks.append(ProbeCheck(f"refused: rpc {name}", status, detail))
    for method in ("PATCH", "DELETE"):
        error, answer = _attempt(
            transport,
            method,
            f"/rest/v1/topic?id=eq.{ghost}",
            {"teema": "x"} if method == "PATCH" else None,
        )
        status, detail = write_probe_verdict(error, answer, kind="table")
        checks.append(ProbeCheck(f"refused: {method} topic", status, detail))

    # 3. No administrative history, no deleted topics.
    reads = [(f"hidden: {t}", f"/rest/v1/{t}?select=*&limit=1") for t in HIDDEN_TABLES]
    reads.append(
        ("hidden: deleted topics", "/rest/v1/topic?select=id&deleted_at=not.is.null&limit=1")
    )
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
            if refusal.code in ("CREDENTIALS_HOST", "REDIRECT_REFUSED"):
                raise
            rows, failure = None, refusal
        status, detail = _hidden_verdict(failure, rows)
        checks.append(ProbeCheck(name, status, detail))

    # 4. No administrative standing: not an admin, and no staff profile at all.
    admin_error, admin = _attempt(
        transport, "POST", "/rest/v1/rpc/can_access_admin_view", {"_user_id": claims.get("sub")}
    )
    if admin_error is not None:
        checks.append(ProbeCheck("not an administrator", INDETERMINATE, admin_error.code))
    else:
        checks.append(
            ProbeCheck(
                "not an administrator",
                PASS if admin is False else FAIL if admin is True else INDETERMINATE,
                str(admin) if isinstance(admin, bool) or admin is None else type(admin).__name__,
            )
        )
    writer_error, answer = _attempt(transport, "POST", "/rest/v1/rpc/topic_writer", {})
    status, detail = write_probe_verdict(writer_error, answer, kind="rpc")
    checks.append(ProbeCheck("no staff profile (topic_writer refuses)", status, detail))
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
