"""The Lovable reader's transport and access probe (audit 2026-10-28 C-01, C-02).

DB-free. Lovable is a stub opener: no request leaves the process, and every
value is invented.

* C-02 — the service login, its bearer token and the API key go to one
  authorised origin only; a redirect is never followed; a column the source
  sends beyond the allowlist never reaches a snapshot.
* C-01 — the access probe passes only on an explicit refusal. ``200 []`` from a
  PATCH/DELETE under row-level security looks exactly like a permitted write
  that matched nothing, and a 404 means a path is missing, not that it refuses:
  both are INDETERMINATE, which blocks activation like a failure until the
  read-only catalogue inspection (`GRANT_INSPECTION_SQL`) settles it.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
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
    def __init__(self, body: Any, *, url: str | None = None) -> None:
        self.raw = json.dumps(body).encode()
        self.url = url

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
# C-01: only an explicit refusal proves the identity cannot write
# ---------------------------------------------------------------------------


def _probe(write_answer: Any, *, hidden_answer: Any = None, overrides: Any = None):
    """The probe against a stub whose write paths all answer ``write_answer``:
    a status code (raised as an HTTP error), or a JSON body (a 2xx)."""
    overrides = overrides or {}

    def opener(request, timeout):
        url, method = request.full_url, request.get_method()
        if "/auth/v1/token" in url:
            return _Answer({"access_token": _jwt()}, url=url)
        for needle, answer in overrides.items():
            if needle in url and (method != "GET" or needle.startswith("GET ")):
                return _respond(url, answer)
        if "/rpc/can_access_admin_view" in url:
            return _Answer(False, url=url)
        if "/rpc/" in url or method in ("PATCH", "DELETE"):
            return _respond(url, write_answer)
        if "topic_change" in url or "deleted_at" in url:
            return _respond(url, [] if hidden_answer is None else hidden_answer)
        if "/rest/v1/topic?" in url:
            return _Answer([{"id": GHOST_TOPIC, "nr": "2026_1", "version": 1}], url=url)
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


def test_explicit_refusals_everywhere_prove_least_privilege():
    for refusal in (401, 403, (400, {"code": "42501", "message": "permission denied"})):
        result = _probe(refusal)
        assert result.verdict == PASS and result.passed, [c for c in result.checks if not c.ok]


def test_patch_answering_200_empty_proves_nothing():
    result = _probe(403)
    patched = _probe_with_method("PATCH", [])
    assert _status(patched, "refused: PATCH topic") == INDETERMINATE
    assert patched.verdict == INDETERMINATE and not patched.passed
    assert result.passed  # the same probe with an explicit refusal passes


def test_delete_answering_200_empty_proves_nothing():
    deleted = _probe_with_method("DELETE", [])
    assert _status(deleted, "refused: DELETE topic") == INDETERMINATE
    assert deleted.verdict == INDETERMINATE and not deleted.passed


def test_a_table_write_that_returns_rows_fails():
    patched = _probe_with_method("PATCH", [{"id": GHOST_TOPIC}])
    assert _status(patched, "refused: PATCH topic") == FAIL
    assert patched.verdict == FAIL


def test_a_missing_rpc_is_not_evidence_of_refusal():
    result = _probe(403, overrides={"/rpc/save_topic": 404})
    assert _status(result, "refused: rpc save_topic") == INDETERMINATE
    assert result.verdict == INDETERMINATE and not result.passed


def test_a_missing_route_everywhere_is_indeterminate_not_a_pass():
    result = _probe((404, {"code": "PGRST202", "message": "Could not find the function"}))
    write_checks = [c for c in result.checks if c.name.startswith("refused:")]
    assert write_checks and {c.status for c in write_checks} == {INDETERMINATE}
    assert result.verdict == INDETERMINATE


def test_a_write_function_that_runs_for_the_caller_fails():
    result = _probe(403, overrides={"/rpc/delete_topic": None})
    assert _status(result, "refused: rpc delete_topic") == FAIL
    assert result.verdict == FAIL


def test_an_unexpected_answer_is_indeterminate():
    for answer in (500, 405, (400, {"code": "P0001", "message": "Teema puudub"})):
        result = _probe(answer)
        assert result.verdict == INDETERMINATE, answer
        details = " ".join(c.detail for c in result.checks)
        assert "Teema puudub" not in details  # an error's words are never carried


def test_a_visible_history_row_fails_and_a_missing_history_table_is_indeterminate():
    assert _probe(403, hidden_answer=[{"id": "h1"}]).verdict == FAIL
    assert _probe(403, hidden_answer=404).verdict == INDETERMINATE


def test_an_administrator_identity_fails():
    result = _probe(403, overrides={"/rpc/can_access_admin_view": True})
    assert _status(result, "not an administrator") == FAIL


def test_the_probe_command_blocks_activation_when_indeterminate(monkeypatch, capsys):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    indeterminate = lovable_source.ProbeResult(
        [lovable_source.ProbeCheck("refused: PATCH topic", INDETERMINATE, "2xx with no rows")]
    )
    monkeypatch.setattr(lovable_source, "load_config", lambda _p=None: _config())
    monkeypatch.setattr(lovable_source, "access_probe", lambda _c: indeterminate)
    with pytest.raises(CommandError, match="INDETERMINATE"):
        call_command("lovable_sync", "access-probe")
    assert "ACCESS PROBE PASSED" not in capsys.readouterr().out


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


def _probe_with_method(method: str, answer: Any) -> Any:
    def opener(request, timeout):
        url = request.full_url
        if "/auth/v1/token" in url:
            return _Answer({"access_token": _jwt()}, url=url)
        if request.get_method() == method:
            return _respond(url, answer)
        if "/rpc/can_access_admin_view" in url:
            return _Answer(False, url=url)
        if "/rpc/" in url or request.get_method() in ("PATCH", "DELETE"):
            return _respond(url, 403)
        if "topic_change" in url or "deleted_at" in url:
            return _Answer([], url=url)
        if "/rest/v1/topic?" in url:
            return _Answer([{"id": GHOST_TOPIC, "nr": "2026_1", "version": 1}], url=url)
        return _Answer([], url=url)

    return lovable_source.access_probe(_config(), opener=opener)
