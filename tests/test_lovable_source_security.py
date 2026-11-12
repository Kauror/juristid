"""The Lovable reader's transport and access probe (audit 2026-10-28 C-01, C-02).

DB-free. Lovable is a stub opener: no request leaves the process, and every
value is invented.

* C-02 — the service login, its bearer token and the API key go to one
  authorised origin only; a redirect is never followed; a column the source
  sends beyond the allowlist never reaches a snapshot.
* C-01 — the access probe is GET-only (owner rule 2026-11-02): it sends nothing
  write-shaped, so the write side is INDETERMINATE and blocks activation like a
  failure until the read-only catalogue inspection (`GRANT_INSPECTION_SQL`)
  settles it; the transport itself refuses any method but GET and sign-in.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pytest

from app.legacy_import import lovable_source
from app.legacy_import.lovable_source import (
    FAIL,
    INDETERMINATE,
    PASS,
    SourceConfig,
    SourceError,
)

ORIGIN = "https://naidisprojekt.supabase.co"
GHOST_TOPIC = "00000000-0000-0000-0000-000000000001"


def _jwt(role: str = "authenticated") -> str:
    body = base64.urlsafe_b64encode(json.dumps({"role": role, "sub": "u1"}).encode())
    return f"x.{body.decode().rstrip('=')}.y"


def _config(url: str = ORIGIN) -> SourceConfig:
    return SourceConfig(
        url=url,
        publishable_key="sb_publishable_naidis",
        email="e@x.invalid",
        password="pw-1",  # noqa: S106
    )


class _Answer:
    """One answer. A list of rows carries PostgREST's ``Content-Range`` — the
    rows served from the request's ``offset``, out of ``total`` (default: this
    page is the end) — which the paged reader requires (QA-01)."""

    def __init__(self, body: Any, *, url: str | None = None, total: int | None = None) -> None:
        self.raw = json.dumps(body).encode()
        self.url = url
        self.headers: dict[str, str] = {}
        if isinstance(body, list):
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url or "").query))
            start = int(query.get("offset", "0"))
            served = f"{start}-{start + len(body) - 1}" if body else "*"
            self.headers["Content-Range"] = (
                f"{served}/{start + len(body) if total is None else total}"
            )

    def read(self, _n: int) -> bytes:
        return self.raw

    def geturl(self) -> str | None:
        return self.url

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


def _http_error(url: str, status: int, body: Any = None, headers: Any = None) -> Exception:
    payload = io.BytesIO(json.dumps(body).encode() if body is not None else b"")
    return urllib.error.HTTPError(url, status, "x", headers or {}, payload)


# ---------------------------------------------------------------------------
# C-02: one authorised origin, no redirects, allowlisted columns only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com",
        "https://naidisprojekt.supabase.co.evil.example",
        "https://naidisprojekt.supabase.co:8443",
        "https://user:pw@naidisprojekt.supabase.co",
        "https://naidisprojekt.supabase.co/rest/v1",
        "https://naidisprojekt.supabase.co?x=1",
    ],
)
def test_credentials_go_only_to_the_projects_own_supabase_origin(tmp_path, url):
    path = tmp_path / "lovable-sync.env"
    path.write_text(
        "\n".join(
            [
                f"LOVABLE_SYNC_URL={url}",
                "LOVABLE_SYNC_PUBLISHABLE_KEY=sb_publishable_naidis",
                "LOVABLE_SYNC_EMAIL=e@x.invalid",
                "LOVABLE_SYNC_PASSWORD=pw-1",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceError) as refused:
        lovable_source.load_config(path)
    assert refused.value.code == "CREDENTIALS_HOST"
    assert "pw-1" not in str(refused.value)


def test_a_config_for_another_host_sends_nothing_at_all():
    sent: list[str] = []

    def opener(request, timeout):
        sent.append(request.full_url)
        return _Answer({"access_token": _jwt()})

    transport = lovable_source.HttpTransport(_config("https://evil.example.com"), opener=opener)
    with pytest.raises(SourceError) as refused:
        transport.sign_in()
    assert refused.value.code == "CREDENTIALS_HOST"
    assert sent == []


def test_the_default_opener_never_follows_a_redirect():
    """urllib's own handler would resend Authorization and apikey to the
    ``Location`` — https → http included. The default opener refuses instead."""
    handler = next(
        h
        for h in lovable_source._OPENER.handlers
        if isinstance(h, urllib.request.HTTPRedirectHandler)
    )
    request = urllib.request.Request(  # noqa: S310 - never opened
        ORIGIN + "/rest/v1/topic", headers={"Authorization": "Bearer t", "apikey": "k"}
    )
    for status in (301, 302, 303, 307, 308):
        assert (
            handler.redirect_request(request, None, status, "moved", {}, "http://evil.example/")
            is None
        )


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_answer_is_refused_before_anything_is_read(status):
    seen: list[str] = []

    def opener(request, timeout):
        seen.append(request.full_url)
        if "/auth/v1/token" in request.full_url:
            return _Answer({"access_token": _jwt()})
        raise _http_error(request.full_url, status, headers={"Location": "http://evil.example/"})

    transport = lovable_source.HttpTransport(_config(), opener=opener)
    with pytest.raises(SourceError) as refused:
        transport.select("topic", ["id"], filters={}, order="id")
    assert refused.value.code == "REDIRECT_REFUSED"
    assert all(url.startswith(ORIGIN + "/") for url in seen)


def test_an_answer_that_landed_on_another_origin_is_refused():
    """Even an opener that did follow a redirect (https → http, or another
    host) gets nothing past `_request`."""

    def opener(request, timeout):
        if "/auth/v1/token" in request.full_url:
            return _Answer({"access_token": _jwt()})
        return _Answer([{"id": GHOST_TOPIC}], url="http://naidisprojekt.supabase.co/rest/v1/x")

    transport = lovable_source.HttpTransport(_config(), opener=opener)
    with pytest.raises(SourceError) as refused:
        transport.select("topic", ["id"], filters={}, order="id")
    assert refused.value.code == "REDIRECT_REFUSED"


def test_columns_beyond_the_allowlist_never_reach_the_rows():
    def opener(request, timeout):
        if "/auth/v1/token" in request.full_url:
            return _Answer({"access_token": _jwt()})
        return _Answer(
            [{"id": GHOST_TOPIC, "nr": "2026_1", "markmed": "SISEMINE-7781", "secret": "x"}],
            url=request.full_url,
        )

    transport = lovable_source.HttpTransport(_config(), opener=opener)
    rows = transport.select("topic", ["id", "nr"], filters={}, order="id")
    assert rows == [{"id": GHOST_TOPIC, "nr": "2026_1"}]


def test_a_snapshot_keeps_only_allowlisted_columns_whatever_the_transport():
    class Chatty:
        def select(self, table, columns, *, filters, order):
            if table == "topic":
                return [{"id": GHOST_TOPIC, "nr": "2026_1", "version": 1, "markmed": "M-7781"}]
            if table == "topic_stage_link":
                return []
            return [{"topic_id": GHOST_TOPIC, "id": "c1", "extra": "E-7782"}]

    document = lovable_source.take_snapshot(
        Chatty(), [GHOST_TOPIC], mapping_version="t", pause=lambda _s: None
    )
    text = json.dumps(document)
    assert "M-7781" not in text and "E-7782" not in text
    assert document["cohort"][GHOST_TOPIC]["row"]["nr"] == "2026_1"
    lovable_source.verify_snapshot(document)


def test_a_normal_authorised_read_is_get_only_and_carries_its_credentials_home():
    seen: list[tuple[str, str, dict[str, str]]] = []

    def opener(request, timeout):
        seen.append((request.get_method(), request.full_url, dict(request.header_items())))
        if "/auth/v1/token" in request.full_url:
            return _Answer({"access_token": _jwt()}, url=request.full_url)
        return _Answer([{"id": GHOST_TOPIC, "nr": "2026_1"}], url=request.full_url)

    transport = lovable_source.HttpTransport(_config(), opener=opener)
    assert transport.select("topic", ["id", "nr"], filters={}, order="id") == [
        {"id": GHOST_TOPIC, "nr": "2026_1"}
    ]
    assert [m for m, _u, _h in seen] == ["POST", "GET"]
    assert all(url.startswith(ORIGIN + "/") for _m, url, _h in seen)
    assert seen[1][2]["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# C-01, as tightened by the owner on 2026-11-02: the probe is GET-only. Nothing
# write-shaped is ever sent — no write RPC, no PATCH/DELETE, no rpc/topic_writer
# or rpc/can_access_admin_view, not even at a UUID that cannot exist — so the
# write side is INDETERMINATE and the verdict is never PASS from HTTP alone.
# ---------------------------------------------------------------------------


def _probe(*, hidden_answer: Any = None, topics: Any = None, seen: list | None = None):
    """The probe against a stub that answers every read; ``seen`` records
    (method, path) of everything the probe sent."""
    log = seen if seen is not None else []

    def opener(request, timeout):
        url, method = request.full_url, request.get_method()
        log.append((method, url[len(ORIGIN) :]))
        if "/auth/v1/token" in url:
            return _Answer({"access_token": _jwt()}, url=url)
        if method != "GET":
            raise AssertionError(f"the probe sent {method} {url}")
        if "topic_change" in url or "deleted_at" in url:
            return _respond(url, [] if hidden_answer is None else hidden_answer)
        if "/rest/v1/topic?" in url:
            rows = [{"id": GHOST_TOPIC, "nr": "2026_1", "version": 1}] if topics is None else topics
            return _Answer(rows, url=url)
        return _Answer([], url=url)

    return lovable_source.access_probe(_config(), opener=opener)


def _respond(url: str, answer: Any) -> Any:
    if type(answer) is int:
        raise _http_error(url, answer)
    if isinstance(answer, tuple):
        status, body = answer
        raise _http_error(url, status, body)
    return _Answer(answer, url=url)


def _status(result: Any, name: str) -> str:
    return next(c.status for c in result.checks if c.name == name)


def test_the_probe_sends_only_gets_after_the_sign_in():
    seen: list[tuple[str, str]] = []
    _probe(seen=seen)
    assert seen[0] == ("POST", lovable_source.SIGN_IN_PATH)
    assert {method for method, _path in seen[1:]} == {"GET"}
    assert not any("/rpc/" in path for _m, path in seen)
    assert not any(m in ("PATCH", "DELETE", "PUT") for m, _p in seen)


def test_a_clean_read_side_is_still_never_a_pass():
    result = _probe()
    reads = [c for c in result.checks if c.name != lovable_source.WRITE_SIDE_CHECK]
    assert reads and all(c.status == PASS for c in reads), reads
    assert _status(result, lovable_source.WRITE_SIDE_CHECK) == INDETERMINATE
    assert result.verdict == INDETERMINATE and not result.passed


@pytest.mark.parametrize("refusal", [401, 403, (400, {"code": "42501", "message": "denied"})])
def test_an_explicit_refusal_of_the_history_proves_it_hidden(refusal):
    result = _probe(hidden_answer=refusal)
    assert _status(result, "hidden: topic_change_event") == PASS
    assert result.verdict == INDETERMINATE


def test_a_visible_history_row_fails_and_a_missing_history_table_is_indeterminate():
    assert _probe(hidden_answer=[{"id": "h1"}]).verdict == FAIL
    missing = _probe(hidden_answer=404)
    assert _status(missing, "hidden: topic_change_event") == INDETERMINATE
    assert missing.verdict == INDETERMINATE


def test_an_identity_that_reads_nothing_fails():
    assert _probe(topics=[]).verdict == FAIL


def test_an_error_body_is_never_carried_into_a_detail():
    result = _probe(hidden_answer=(400, {"code": "P0001", "message": "Teema puudub"}))
    assert "Teema puudub" not in " ".join(c.detail for c in result.checks)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", f"/rest/v1/topic?id=eq.{GHOST_TOPIC}"),
        ("DELETE", f"/rest/v1/topic?id=eq.{GHOST_TOPIC}"),
        ("POST", "/rest/v1/rpc/save_topic"),
        ("POST", "/rest/v1/rpc/topic_writer"),
        ("POST", "/rest/v1/rpc/can_access_admin_view"),
        ("PUT", "/rest/v1/topic"),
    ],
)
def test_the_transport_refuses_to_send_anything_write_shaped(method, path):
    sent: list[str] = []

    def opener(request, timeout):
        sent.append(request.get_method())
        return _Answer([], url=request.full_url)

    transport = lovable_source.HttpTransport(_config(), opener=opener)
    with pytest.raises(SourceError) as refused:
        transport._request(method, path, body={}, headers={})
    assert refused.value.code == "NOT_READ_ONLY"
    assert sent == []


def test_the_probe_command_blocks_activation_when_indeterminate(monkeypatch, capsys):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setattr(lovable_source, "load_config", lambda _p=None: _config())
    result = _probe()
    monkeypatch.setattr(lovable_source, "access_probe", lambda _c: result)
    with pytest.raises(CommandError, match="INDETERMINATE") as blocked:
        call_command("lovable_sync", "access-probe")
    assert "access-sql" in str(blocked.value)
    out = capsys.readouterr().out
    assert "ACCESS PROBE PASSED" not in out
    assert "write refusal: not probed over HTTP" in out


def test_the_read_only_inspection_is_select_only():
    call_sql = lovable_source.GRANT_INSPECTION_SQL
    statements = [s.strip() for s in call_sql.split(";") if s.strip()]
    body = [
        "\n".join(line for line in s.splitlines() if not line.lstrip().startswith("--")).strip()
        for s in statements
    ]
    assert len(body) == 5 and all(b.lower().startswith("select") for b in body)
    forbidden = (
        "insert ",
        "update ",
        "delete ",
        "alter ",
        "grant ",
        "drop ",
        "create ",
        "truncate",
    )
    lowered = call_sql.lower().replace("'insert', 'update', 'delete', 'all'", "")
    assert not any(word in lowered for word in forbidden)


def test_the_audit_access_proposal_grants_select_to_one_identity_only(capsys):
    from django.core.management import call_command

    sql = lovable_source.AUDIT_ACCESS_SQL
    executable = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ).lower()
    statements = [s.strip() for s in executable.split(";") if s.strip()]
    creates = [s for s in statements if s.startswith("create")]
    assert len(creates) == 2
    for statement in creates:
        assert statement.startswith("create policy")
        assert "for select" in statement and "auth.uid() = '<service uid>'::uuid" in statement
    others = [s for s in statements if not s.startswith("create")]
    assert others and all(s.startswith("select") for s in others)
    lowered = executable.replace("'insert', 'update', 'delete', 'all'", "")
    for word in ("grant ", "insert ", "update ", "delete ", "alter ", "drop ", "truncate"):
        assert word not in lowered, word
    assert "actor_id" not in executable
    call_command("lovable_sync", "audit-sql")
    assert capsys.readouterr().out.strip() == sql.strip()


# ---------------------------------------------------------------------------
# Review of PR #586 (P1): a ledger value is never even requested for a field
# off the allowlist. `old_value` / `new_value` go out only with a positive
# `field=in.(…)` filter of allowlisted names; anything else is refused before
# a single request (the stub opener records every one).
# ---------------------------------------------------------------------------

EVENT = "e0000000-0000-4000-8000-000000000001"


def _recording_transport() -> tuple[lovable_source.HttpTransport, list[str]]:
    sent: list[str] = []

    def opener(request, timeout):
        sent.append(request.full_url)
        if "/auth/v1/token" in request.full_url:
            return _Answer({"access_token": _jwt()}, url=request.full_url)
        return _Answer([], url=request.full_url)

    return lovable_source.HttpTransport(_config(), opener=opener), sent


VALUES = ("event_id", "field", "old_value", "new_value")


@pytest.mark.parametrize(
    "filters",
    [
        {"event_id": f"in.({EVENT})"},
        {"event_id": f"in.({EVENT})", "field": "in.(hetkeseis,markmed)"},
        {"event_id": f"in.({EVENT})", "field": "in.(mitteametlik_arvamus)"},
        {"event_id": f"in.({EVENT})", "field": "in.(tundmatu)"},
        {"event_id": f"in.({EVENT})", "field": "not.in.(hetkeseis)"},
        {"event_id": f"in.({EVENT})", "field": "in.(hetkeseis)", "or": "(field.eq.markmed)"},
        {"event_id": f"in.({EVENT})", "field": "in.(hetkeseis),markmed"},
        {"event_id": f"in.({EVENT})", "field": "eq.hetkeseis"},
    ],
)
def test_a_value_read_without_the_allowlist_filter_is_refused_before_any_request(filters):
    transport, sent = _recording_transport()
    for columns in (VALUES, ("event_id", "field", "new_value"), ("old_value",)):
        with pytest.raises(SourceError) as refused:
            transport.select("topic_change_field", columns, filters=filters, order="event_id")
        assert refused.value.code == "NOT_ALLOWLISTED"
    assert sent == []


def test_the_two_ledger_field_reads_have_exactly_these_shapes():
    transport, sent = _recording_transport()
    assert lovable_source.AUDIT_FIELD_NEVER.isdisjoint(lovable_source.AUDIT_VALUE_FIELDS)
    allowed = lovable_source.audit_value_filter()
    transport.select(
        "topic_change_field",
        lovable_source.AUDIT_FIELD_VALUE_SELECT,
        filters={"event_id": f"in.({EVENT})", "field": allowed},
        order="event_id,field",
    )
    transport.select(
        "topic_change_field",
        lovable_source.AUDIT_FIELD_NAME_SELECT,
        filters={"event_id": f"in.({EVENT})", "field": lovable_source.audit_name_filter()},
        order="event_id,field",
    )
    reads = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for u in sent[1:]]
    assert reads[0]["select"] == ["event_id,field,old_value,new_value"]
    assert reads[0]["field"] == [allowed] and allowed.startswith("in.(")
    names = set(allowed[4:-1].split(","))
    assert "hetkeseis" in names and not names & {"markmed", "mitteametlik_arvamus", "allikas"}
    assert reads[1]["select"] == ["event_id,field"]
    assert reads[1]["field"] == ["not." + allowed]
