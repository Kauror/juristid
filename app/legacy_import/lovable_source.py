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

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https enforced


class HttpTransport:
    """PostgREST over HTTPS as the service login. ``GET`` only."""

    def __init__(self, config: SourceConfig, *, opener: Opener = _default_opener) -> None:
        refuse_privileged_key(config.publishable_key)
        self.config = config
        self.opener = opener
        self.token = ""

    def _request(self, method: str, path: str, *, body: dict | None = None, headers: dict) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - load_config refuses anything but https://
            self.config.url + path, data=data, method=method, headers=headers
        )
        try:
            with self.opener(request, TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise SourceError(
                f"HTTP_{error.code}", f"Lovable answered {error.code} on {path.split('?')[0]}."
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
            rows.extend(page)
            if len(rows) > MAX_INDEX_ROWS:
                raise SourceError("TOO_MANY_ROWS", f"{table} returned more rows than the cap.")
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE


def _in(ids: Iterable[str]) -> str:
    return "in.(" + ",".join(str(i) for i in ids) + ")"


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(items), ID_CHUNK):
        yield items[start : start + ID_CHUNK]


def _read_cohort(transport: Transport, ids: list[str]) -> dict[str, Any]:
    topics: list[dict[str, Any]] = []
    commencements: list[dict[str, Any]] = []
    responsible: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for chunk in _chunks(ids):
        topics += transport.select("topic", TOPIC_COLUMNS, filters={"id": _in(chunk)}, order="id")
        commencements += transport.select(
            "topic_commencement",
            TABLES["topic_commencement"],
            filters={"topic_id": _in(chunk)},
            order="topic_id,position,id",
        )
        responsible += transport.select(
            "topic_responsible_change",
            TABLES["topic_responsible_change"],
            filters={"topic_id": _in(chunk)},
            order="topic_id,id",
        )
        for column in ("prev_topic_id", "next_topic_id"):
            links += transport.select(
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
    return transport.select("topic", INDEX_COLUMNS, filters={}, order="id")


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
WRITE_RPCS = (
    ("save_topic", {"p_id": None, "p_expected_version": 0, "p_fields": {}}),
    ("save_topic_with_stages", {"p_id": None, "p_expected_version": 0, "p_fields": {}}),
    ("delete_topic", {"p_id": None, "p_expected_version": 0}),
    ("restore_topic", {"p_id": None, "p_expected_version": 0}),
    ("testala_save_topic", {"p_id": None, "p_expected_version": 0, "p_fields": {}}),
    ("testala_delete_topic", {"p_id": None, "p_expected_version": 0}),
    ("testala_restore_topic", {"p_id": None, "p_expected_version": 0}),
    ("testala_purge_topic", {"p_id": None}),
)


@dataclass
class ProbeResult:
    checks: list[tuple[str, bool, str]]

    @property
    def passed(self) -> bool:
        return all(ok for _name, ok, _detail in self.checks)


def _refused(transport: HttpTransport, method: str, path: str, body: Any) -> tuple[bool, str]:
    """True when the source refused (an HTTP error, or a write that matched no row)."""
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
        return error.code.startswith("HTTP_4"), error.code
    return answer in ([], None), "no rows affected" if answer in ([], None) else "ACCEPTED"


def access_probe(config: SourceConfig, *, opener: Opener = _default_opener) -> ProbeResult:
    """Read what the job needs, and prove nothing more is possible. Counts only."""
    import uuid

    transport = HttpTransport(config, opener=opener)
    checks: list[tuple[str, bool, str]] = []
    transport.sign_in()
    claims = _jwt_claims(transport.token)
    checks.append(
        (
            "token role is authenticated",
            claims.get("role") == "authenticated",
            str(claims.get("role")),
        )
    )

    # 1. It reads the live topics and their children.
    index = _index(transport)
    checks.append(("reads live topics", len(index) > 0, f"{len(index)} rows"))
    sample = [str(r["id"]) for r in index[:ID_CHUNK]]
    cohort = _read_cohort(transport, sample) if sample else {}
    checks.append(
        ("reads topic children", len(cohort) == len(sample), f"{len(cohort)} topics read")
    )

    # 2. It cannot write: every write path refuses it.
    ghost = str(uuid.uuid4())
    for name, body in WRITE_RPCS:
        payload = {k: (ghost if v is None else v) for k, v in body.items()}
        ok, detail = _refused(transport, "POST", f"/rest/v1/rpc/{name}", payload)
        checks.append((f"refused: rpc {name}", ok, detail))
    for method in ("PATCH", "DELETE"):
        ok, detail = _refused(
            transport,
            method,
            f"/rest/v1/topic?id=eq.{ghost}",
            {"teema": "x"} if method == "PATCH" else None,
        )
        checks.append((f"refused: {method} topic", ok, detail))

    # 3. No administrative history, no deleted topics.
    for table in ("topic_change_event", "topic_change_field"):
        try:
            rows = transport._request(
                "GET",
                f"/rest/v1/{table}?select=*&limit=1",
                headers={
                    "apikey": config.publishable_key,
                    "Authorization": f"Bearer {transport.token}",
                },
            )
            hidden = rows == []
            detail = "0 rows" if hidden else f"{len(rows)} rows visible"
        except SourceError as error:
            hidden, detail = error.code.startswith("HTTP_4"), error.code
        checks.append((f"hidden: {table}", hidden, detail))
    try:
        deleted = transport._request(
            "GET",
            "/rest/v1/topic?select=id&deleted_at=not.is.null&limit=1",
            headers={
                "apikey": config.publishable_key,
                "Authorization": f"Bearer {transport.token}",
            },
        )
        checks.append(("hidden: deleted topics", deleted == [], f"{len(deleted)} rows"))
    except SourceError as error:
        checks.append(("hidden: deleted topics", error.code.startswith("HTTP_4"), error.code))

    # 4. No administrative standing: not an admin, and no staff profile at all.
    try:
        admin = transport._request(
            "POST",
            "/rest/v1/rpc/can_access_admin_view",
            body={"_user_id": claims.get("sub")},
            headers={
                "apikey": config.publishable_key,
                "Authorization": f"Bearer {transport.token}",
                "Content-Type": "application/json",
            },
        )
        checks.append(("not an administrator", admin is False, str(admin)))
    except SourceError as error:
        checks.append(("not an administrator", False, error.code))
    ok, detail = _refused(transport, "POST", "/rest/v1/rpc/topic_writer", {})
    checks.append(("no staff profile (topic_writer refuses)", ok, detail))
    return ProbeResult(checks)
