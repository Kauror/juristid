# ruff: noqa: F811 - pytest fixtures imported from the sync suite are used as arguments
"""Haldus → Andmete sünkroonimine, round 2 (LS2-D), on synthetic data.

What round 1 left implicit is proven here: the page says plainly whether a
server runner exists and what a waiting request waits for; a Lovable request is
bound to the mode it was confirmed in; an expired request never runs; runs on
the same targets never overlap (one open request per lane in the database, and
the engine's advisory lock shared with the night); a failed run keeps its
diagnostics without secrets and is retried by a new request; the host's
questions (`poll`) leave a heartbeat and refuse what the host cannot run; and
«Vaata erinevusi» puts the next-step corrections first. Lovable is the fake
transport of tests/test_lovable_sync.py; nothing reaches a network or a capture.
"""

from __future__ import annotations

import ast
import datetime as dt
import io
from pathlib import Path
from typing import Any

import psycopg
import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.errors import ImmutableRecordError
from app.legacy_import import lovable_sync, sync_admin
from app.legacy_import.lovable_source import SourceError
from app.legacy_import.lovable_sync_models import LovableSyncRun, SyncMode, SyncRunStatus
from app.legacy_import.sync_requests_models import (
    RUNNER_SILENT_AFTER_MINUTES,
    DataSyncRequest,
    DataSyncRunnerBeat,
    SyncRequestKind,
    SyncRequestLane,
    SyncRequestStatus,
)
from tests.admin_session import apply_admin_gate, choose, technical_admin, verified
from tests.refusals import refused
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, TALLINN, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

LOVABLE = SyncRequestLane.LOVABLE
ONENOTE = SyncRequestLane.ONENOTE
#: A fixed moment for everything that is not a night of the cohort.
T0 = dt.datetime(2026, 11, 9, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def gate(settings):
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    return settings


@pytest.fixture
def admin(gate):
    user, secret = technical_admin()
    return {"user": user, "secret": secret}


@pytest.fixture
def signed(client, admin):
    verified(client, admin["user"], admin["secret"])
    return client


def _compare() -> None:
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")


def _backup(moment: dt.datetime) -> str:
    return (moment - dt.timedelta(minutes=5)).astimezone(dt.UTC).strftime("sets/%Y%m%dT%H%M%SZ")


def _audits(action: str) -> list[SecurityAuditEvent]:
    return [
        e
        for e in SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN)
        if (e.detail or {}).get("action") == action
    ]


def _overview(client) -> str:
    response = client.get(reverse("haldus:data_sync"))
    assert response.status_code == 200
    return response.content.decode()


# ---------------------------------------------------------------------------
# 1. The page tells the truth about the runner and the mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [SyncMode.COMPARE, SyncMode.APPLY, SyncMode.OFF])
def test_the_page_shows_the_actual_mode(signed, mode):
    lovable_sync.set_mode(mode, reason="t")
    body = _overview(signed)
    assert f"Režiim: {SyncMode(mode).label}" in body
    if mode == SyncMode.OFF:
        assert 'aria-disabled="true">Sünkrooni nüüd' in body


def test_with_no_runner_ever_seen_the_page_says_so_plainly(signed):
    body = _overview(signed)
    assert body.count("Pole kunagi nähtud") == 2  # the Lovable lane and the OneNote lane
    assert "Serveri käivitajat pole kunagi nähtud" in body
    assert not DataSyncRunnerBeat.objects.exists(), "a GET records no heartbeat"


def test_a_request_waiting_without_a_runner_says_it_waits_for_the_server(signed, admin):
    _compare()
    response = signed.post(reverse("haldus:data_sync_now"), {"reason": "x", "mode_seen": "COMPARE"})
    assert response.status_code == 302
    shown = " ".join(m.message for m in response.wsgi_request._messages)
    assert "seda pole veel käivitatud" in shown
    assert "käivitaja võtab selle üles" not in shown, "never claims a runner that is absent"
    body = _overview(signed)
    assert sync_admin.WAITING_FOR_RUNNER in body
    assert "Serveri käivitajat pole kunagi nähtud" in body
    assert "Tehtud" not in body


def test_once_the_runner_polls_the_page_says_it_works(signed, admin):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x")
    assert sync_admin.poll(LOVABLE) == "DUE COMPARE"
    sync_admin.poll(ONENOTE)
    status = sync_admin.runner_status(LOVABLE)
    assert status.active and status.label == "Töötab"
    body = _overview(signed)
    assert "Töötab" in body and "viimati küsis" in body
    assert "Serveri käivitajat pole kunagi nähtud" not in body


def test_a_silent_runner_and_a_host_problem_are_named(admin):
    sync_admin.record_beat(LOVABLE, answer="NOT_DUE NO_REQUEST", now=T0)
    later = T0 + dt.timedelta(minutes=RUNNER_SILENT_AFTER_MINUTES + 1)
    silent = sync_admin.runner_status(LOVABLE, now=later)
    assert silent.silent and not silent.active and silent.label == "Vaikib"
    sync_admin.record_beat(LOVABLE, answer="NOT_DUE NO_REQUEST", problem="HOST_PAUSED", now=later)
    paused = sync_admin.runner_status(LOVABLE, now=later)
    assert paused.tone == "problem" and "PAUSED" in paused.note
    beat = DataSyncRunnerBeat.objects.get(lane=LOVABLE)
    assert beat.first_seen_at == T0 and beat.seen_at == later


def test_every_request_status_reads_differently(admin):
    labels = {
        sync_admin.request_view(
            DataSyncRequest(kind=SyncRequestKind.ONENOTE_VERIFY, status=status, requested_at=T0),
            now=T0,
        ).status_label
        for status in SyncRequestStatus.values
    }
    assert len(labels) == len(SyncRequestStatus.values)
    stale = sync_admin.request_view(
        DataSyncRequest(kind=SyncRequestKind.ONENOTE_VERIFY, status="PENDING", requested_at=T0),
        now=T0 + sync_admin.PENDING_EXPIRES_AFTER + dt.timedelta(seconds=1),
    )
    assert stale.stale and stale.status_label not in labels


# ---------------------------------------------------------------------------
# 2. Mode binding, expiry, one run at a time
# ---------------------------------------------------------------------------


def test_a_request_names_the_mode_it_was_confirmed_in(admin):
    _compare()
    item = sync_admin.request_lovable_sync(admin["user"], "x", mode_seen="COMPARE")
    assert item.mode_at_request == SyncMode.COMPARE
    assert _audits("DATA_SYNC_REQUESTED")[0].detail["mode"] == SyncMode.COMPARE
    # The mode is fixed with the request: neither the model nor raw SQL moves it.
    item.mode_at_request = SyncMode.APPLY
    with pytest.raises(ImmutableRecordError):
        item.save()
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE legacy_import_datasyncrequest SET mode_at_request = 'APPLY'")


def test_the_database_refuses_a_lovable_request_without_its_mode(admin):
    with pytest.raises(IntegrityError), transaction.atomic():
        DataSyncRequest.objects.create(
            kind=SyncRequestKind.LOVABLE_SYNC,
            requested_by=admin["user"],
            reason="x",
            requested_at=T0,
        )


def test_a_mode_changed_after_the_request_refuses_it_at_the_runner(admin):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x", now=T0)
    lovable_sync.set_mode(SyncMode.APPLY, reason="vahepeal")
    built: list[str] = []
    outcome = sync_admin.run_next(
        LOVABLE, transport_factory=lambda: built.append("x"), now=lambda: T0
    )
    item = outcome.request
    assert outcome.failed and item.status == SyncRequestStatus.REFUSED
    assert item.outcome_code == "MODE_CHANGED"
    assert item.result == {"mode_requested": "COMPARE", "mode_now": "APPLY"}
    assert built == [], "the source connection is never even built"
    assert not LovableSyncRun.objects.exists()
    assert "Režiim muutus" in sync_admin.failure_reason(item.outcome_code)


def test_an_expired_request_is_never_run_later(admin):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x", now=T0)
    late = T0 + sync_admin.PENDING_EXPIRES_AFTER + dt.timedelta(minutes=1)
    # Even before anything marks it EXPIRED, the claim refuses to pick it up.
    assert sync_admin.claim_next(LOVABLE, now=late) is None
    outcome = sync_admin.run_next(LOVABLE, transport_factory=lambda: object(), now=lambda: late)
    assert outcome.request is None and outcome.code == sync_admin.NOTHING
    item = DataSyncRequest.objects.get()
    assert item.status == SyncRequestStatus.EXPIRED and item.claimed_at is None
    assert not LovableSyncRun.objects.exists()
    assert sync_admin.poll(LOVABLE, now=late) == "NOT_DUE NO_REQUEST"


def _hold_the_run_lock() -> psycopg.Connection:
    db = connection.settings_dict
    other = psycopg.connect(
        host=db["HOST"] or None,
        port=db["PORT"] or None,
        user=db["USER"],
        password=db["PASSWORD"],
        dbname=db["NAME"],
        autocommit=True,
    )
    held = other.execute(
        "SELECT pg_try_advisory_lock(%s, %s)", [lovable_sync.LOCK_NAMESPACE, lovable_sync.LOCK_KEY]
    ).fetchone()
    assert held is not None and held[0]
    return other


def test_a_request_never_runs_beside_the_night_and_is_retried_by_a_new_one(cohort, gate):
    """The engine's advisory lock (24607, 1) is shared by the night and every
    request: while it is held the request is REFUSED (LOCKED) and writes no run;
    a new request afterwards runs the same engine."""
    assert (lovable_sync.LOCK_NAMESPACE, lovable_sync.LOCK_KEY) == (24607, 1)
    _compare()
    user, _ = technical_admin()
    sync_admin.request_lovable_sync(user, "esimene", now=NIGHT)
    other = _hold_the_run_lock()
    try:
        outcome = sync_admin.run_next(
            LOVABLE,
            transport_factory=lambda: cohort["source"],
            backup_set=_backup(NIGHT),
            now=lambda: NIGHT,
        )
    finally:
        other.close()
    assert outcome.request.status == SyncRequestStatus.REFUSED
    assert outcome.request.outcome_code == "LOCKED"
    assert outcome.request.result["error_class"] == "SyncRefused"
    assert not LovableSyncRun.objects.exists()
    retry_at = NIGHT + dt.timedelta(minutes=10)
    sync_admin.request_lovable_sync(user, "uuesti", now=retry_at)
    again = sync_admin.run_next(
        LOVABLE,
        transport_factory=lambda: cohort["source"],
        backup_set=_backup(retry_at),
        now=lambda: retry_at,
    )
    assert again.request.status == SyncRequestStatus.DONE
    assert again.request.sync_run.status in (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY)


def test_a_failed_run_keeps_its_diagnostics_and_a_new_request_retries_it(cohort, gate):
    _compare()
    user, _ = technical_admin()
    sync_admin.request_lovable_sync(user, "esimene", now=NIGHT)
    cohort["source"].fail = SourceError("HTTP_503", "unavailable")
    first = sync_admin.run_next(
        LOVABLE,
        transport_factory=lambda: cohort["source"],
        backup_set=_backup(NIGHT),
        now=lambda: NIGHT,
    ).request
    assert first.status == SyncRequestStatus.FAILED and first.outcome_code == "HTTP_503"
    assert first.result["error_code"] == "HTTP_503"
    assert first.sync_run.status == SyncRunStatus.FAILED and first.sync_run.error
    cohort["source"].fail = None
    retry_at = NIGHT + dt.timedelta(minutes=10)
    sync_admin.request_lovable_sync(user, "uuesti", now=retry_at)
    second = sync_admin.run_next(
        LOVABLE,
        transport_factory=lambda: cohort["source"],
        backup_set=_backup(retry_at),
        now=lambda: retry_at,
    ).request
    assert second.status == SyncRequestStatus.DONE and second.pk != first.pk
    first.refresh_from_db()
    assert first.status == SyncRequestStatus.FAILED, "the failure stays on record"


def test_diagnostics_keep_the_class_and_never_a_free_form_message():
    leaky = "https://user:hunter2@example.invalid/x?apikey=abc"
    assert sync_admin.diagnostics(OSError(leaky)) == {"error_class": "OSError"}
    fixed = sync_admin.diagnostics(SourceError("SIGN_IN_FAILED", "The login was refused."))
    assert fixed == {
        "error_class": "SourceError",
        "error_code": "SIGN_IN_FAILED",
        "error_message": "The login was refused.",
    }


def test_an_unexpected_error_fails_the_request_with_its_class_only(admin, monkeypatch):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x", now=T0)

    def boom(*a: Any, **kw: Any) -> Any:
        raise ValueError("password=hunter2")

    monkeypatch.setattr(lovable_sync, "run_sync", boom)
    with pytest.raises(ValueError):
        sync_admin.run_next(LOVABLE, transport_factory=lambda: object(), now=lambda: T0)
    item = DataSyncRequest.objects.get()
    assert item.status == SyncRequestStatus.FAILED and item.outcome_code == "INTERNAL"
    assert item.result == {"error_class": "ValueError"}
    assert "hunter2" not in str(SecurityAuditEvent.objects.values_list("detail", flat=True))


# ---------------------------------------------------------------------------
# 3. The host's questions: poll, heartbeat, host-side refusals
# ---------------------------------------------------------------------------


def test_poll_answers_like_the_engine_and_leaves_a_heartbeat(admin):
    assert sync_admin.poll(LOVABLE, now=T0) == "NOT_DUE NO_REQUEST"
    assert sync_admin.poll(ONENOTE, now=T0) == "NOT_DUE NO_REQUEST"
    assert set(DataSyncRunnerBeat.objects.values_list("lane", flat=True)) == {LOVABLE, ONENOTE}
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x", now=T0)
    assert sync_admin.poll(LOVABLE, now=T0) == "DUE COMPARE"
    assert DataSyncRequest.objects.get().status == SyncRequestStatus.PENDING, "poll claims nothing"
    sync_admin.request_onenote_verify(admin["user"], "kogum-1", "x", now=T0)
    assert sync_admin.poll(ONENOTE, now=T0) == "DUE ONENOTE"


@pytest.mark.parametrize("problem", ["HOST_PAUSED", "NO_CREDENTIALS", "CREDENTIALS_MODE"])
def test_a_host_problem_refuses_the_waiting_request_by_name(admin, problem):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x", now=T0)
    assert sync_admin.poll(LOVABLE, problem=problem, now=T0) == f"REFUSED {problem}"
    item = DataSyncRequest.objects.get()
    assert item.status == SyncRequestStatus.REFUSED and item.outcome_code == problem
    assert sync_admin.failure_reason(problem) == sync_admin.RUNNER_PROBLEMS[problem]
    assert sync_admin.runner_status(LOVABLE, now=T0).problem == problem
    assert [a.detail["status"] for a in _audits("DATA_SYNC_FINISHED")] == ["REFUSED"]
    # With nothing waiting the same problem is only remembered.
    assert sync_admin.poll(LOVABLE, problem=problem, now=T0) == "NOT_DUE NO_REQUEST"


def test_a_missing_capture_corpus_refuses_the_onenote_request(admin):
    sync_admin.request_onenote_verify(admin["user"], "kogum-1", "x", now=T0)
    assert sync_admin.poll(ONENOTE, problem="NO_CAPTURE_SOURCE", now=T0) == (
        "REFUSED NO_CAPTURE_SOURCE"
    )
    assert DataSyncRequest.objects.get().outcome_code == "NO_CAPTURE_SOURCE"


def test_a_request_recorded_before_the_cutover_is_refused_not_left_waiting(admin):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x", now=T0)
    lovable_sync.set_mode(SyncMode.OFF, reason="t")
    lovable_sync.cut_over(reason="t")
    assert sync_admin.poll(LOVABLE, now=T0) == "NOT_DUE CUT_OVER"
    assert DataSyncRequest.objects.get().outcome_code == "CUT_OVER"


def test_the_poll_command_prints_one_line_and_checks_its_problem(admin):
    out = io.StringIO()
    call_command("data_sync_requests", "poll", "--kind", "onenote", stdout=out)
    assert out.getvalue().strip() == "NOT_DUE NO_REQUEST"
    with pytest.raises(CommandError):
        call_command("data_sync_requests", "poll", "--kind", "lovable", "--problem", "rm -rf")
    with pytest.raises(CommandError, match="needs --kind"):
        call_command("data_sync_requests", "poll")


# ---------------------------------------------------------------------------
# 4. Nothing here can write to Lovable or OneNote, or leak a credential
# ---------------------------------------------------------------------------

_HERE = Path(django_settings.BASE_DIR) / "app"
_FILES = (
    _HERE / "legacy_import" / "sync_admin.py",
    _HERE / "legacy_import" / "sync_requests_models.py",
    _HERE / "legacy_import" / "management" / "commands" / "data_sync_requests.py",
    _HERE / "haldus" / "data_sync_views.py",
)


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_the_admin_code_has_no_network_or_onenote_writer(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    for forbidden in ("requests", "httpx", "urllib.request", "http.client", "win32com", "socket"):
        assert forbidden not in imported, forbidden
    text = path.read_text(encoding="utf-8")
    for forbidden in ("UpdatePageContent", "DeleteHierarchy", "transport.post", 'method="POST"'):
        assert forbidden not in text, forbidden


def test_the_runner_reads_lovable_only_through_the_get_only_transport(monkeypatch):
    from app.legacy_import import lovable_source
    from app.legacy_import.management.commands import data_sync_requests as command

    built: list[Any] = []
    monkeypatch.setattr(lovable_source, "load_config", lambda path=None: "config")
    monkeypatch.setattr(lovable_source, "HttpTransport", lambda config: built.append(config))
    command.lovable_transport({"credentials": ""})()
    assert built == ["config"]
    monkeypatch.undo()
    # ...and that transport refuses every method but GET (and the sign-in POST).
    transport = object.__new__(lovable_source.HttpTransport)
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        with pytest.raises(SourceError) as refusal:
            transport._request(method, "/rest/v1/topic", headers={})
        assert refusal.value.code == "NOT_READ_ONLY"


def test_the_command_output_carries_codes_not_credentials(admin, settings, monkeypatch):
    from app.legacy_import.management.commands import data_sync_requests as command

    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x")
    settings.LOVABLE_SYNC_ENABLED = True
    settings.REAL_DATA_ALLOWED = True

    def leaking(options: dict[str, Any]) -> Any:
        def build() -> Any:
            raise SourceError("CREDENTIALS_MISSING", "LOVABLE_SYNC_PASSWORD=hunter2")

        return build

    monkeypatch.setattr(command, "lovable_transport", leaking)
    out = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("data_sync_requests", "run-next", "--kind", "lovable", stdout=out)
    assert "hunter2" not in out.getvalue()
    listing = io.StringIO()
    call_command("data_sync_requests", "list", stdout=listing)
    assert "hunter2" not in listing.getvalue() and "CREDENTIALS_MISSING" in listing.getvalue()


# ---------------------------------------------------------------------------
# 5. Unauthorized readers and writers (CSRF: tests/test_ls_c_data_sync.py
#    test_every_post_needs_the_csrf_token)
# ---------------------------------------------------------------------------


def test_a_business_persona_cannot_open_or_post_and_nothing_is_recorded(client, gate):
    from tests import factories
    from tests.admin_session import pass_gate

    persona = factories.UserFactory()
    pass_gate(client)
    choose(client, persona)
    _compare()
    for name in ("haldus:data_sync", "haldus:data_sync_differences", "haldus:data_sync_now"):
        assert client.get(reverse(name)).status_code == 404, name
    for name, data in (
        ("haldus:data_sync_now", {"reason": "x", "mode_seen": "COMPARE"}),
        ("haldus:data_sync_check", {}),
        ("haldus:data_sync_onenote_verify", {"capture": "kogum-1", "reason": "x"}),
    ):
        assert client.post(reverse(name), data).status_code == 404, name
    assert not DataSyncRequest.objects.exists()
    assert not LovableSyncRun.objects.exists()


# ---------------------------------------------------------------------------
# 6. «Vaata erinevusi»: the next-step corrections first
# ---------------------------------------------------------------------------


@pytest.fixture
def pending(cohort, gate):
    """One night with a changed next step (505, a DO due 20.11.2026), a changed
    addressee (501) and a sent opinion (502): three groups."""
    _compare()
    cohort["source"].edit("2026_505", jargmiseks="Ootan ministeeriumi vastust")
    cohort["source"].edit("2026_501", kellele="Riigikogu")
    cohort["source"].edit("2026_502", valja="2026-10-27")
    _run(cohort, lovable_sync.SyncTrigger.SCHEDULED)
    return cohort


def _local(day: dt.date, hour: int = 12) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=TALLINN)


def test_a_next_step_correction_comes_first_with_juristid_step_as_it_is_now(pending):
    reader = pending["world"]["mari"]
    after_the_deadline = _local(dt.date(2026, 11, 25))
    diff = sync_admin.differences(reader, now=after_the_deadline)
    (card,) = diff.step_cards
    assert card.reference == "2026_505" and card.group == "step_past"
    assert card.step is not None and card.step.past and card.step.missed
    assert card.step.target_date == dt.date(2026, 11, 20)
    assert [r.field for r in card.rows] == ["next_action_text"]
    text = card.rows[0]
    assert text.source == "Ootan ministeeriumi vastust"
    assert text.accepted == "Saadan märkused"
    assert "tee/oota/jälgi" in text.explanation  # the lawyer classifies, never the page
    assert card.age_days == (dt.date(2026, 11, 25) - NIGHT.date()).days
    # The rest follow by group: the request before the other fields.
    assert [heading for heading, _rows in diff.groups][:1] == ["Arvamuse küsimine ja saatmine"]
    assert diff.rows[0].field in sync_admin.REQUEST_FIELDS
    assert diff.rows[-1].field == "addressee"


def test_before_the_deadline_the_correction_is_still_first_but_not_past(pending):
    reader = pending["world"]["mari"]
    diff = sync_admin.differences(reader, now=_local(dt.date(2026, 10, 30)))
    (card,) = diff.step_cards
    assert card.group == "step" and not card.step.past and not card.step.missed
    assert card.age_days == 2


@pytest.mark.parametrize(
    ("today", "past"),
    [
        (dt.date(2026, 11, 20), False),  # the deadline day itself is not past
        (dt.date(2026, 11, 21), True),
        (dt.date(2026, 12, 31), True),
        (dt.date(2027, 1, 1), True),  # across the year
    ],
)
def test_the_past_step_boundary_is_a_tallinn_day(pending, today, past):
    reader = pending["world"]["mari"]
    # 00:30 Tallinn is still the previous day in UTC: the Tallinn day decides.
    moment = _local(today, hour=0) + dt.timedelta(minutes=30)
    (card,) = sync_admin.differences(reader, now=moment).step_cards
    assert card.step.past is past


def test_the_differences_page_shows_cards_a_b_c_age_and_the_decision(pending, client):
    admin_user, secret = technical_admin()
    verified(client, admin_user, secret)
    choose(client, pending["world"]["mari"])
    body = client.get(reverse("haldus:data_sync_differences")).content.decode()
    assert "Järgmise sammu parandused" in body
    first_card = body.index("dsync__card")
    assert body.index("2026_505") > first_card and body.index("2026_505") < body.index("2026_501")
    assert "Juristides praegu" in body and "Saadan märkused" in body
    assert "Ootan ministeeriumi vastust" in body and "Ava teema" in body
    assert "ootel" in body and "Otsusta: järgmine tegevus (sõnastus)…" in body
    # No auto-classification of the free text anywhere on the page.
    assert "Liik: Ootan" not in body


def test_value_text_shows_days_and_steps_in_estonian_order():
    assert sync_admin.value_text("2026-12-01") == "1.12.2026"
    assert sync_admin.value_text({"text": "Saadan", "target_date": "2027-01-02"}) == (
        "«Saadan» · 2.1.2027"
    )
    assert sync_admin.value_text(None) == "—"


def test_a_refused_decision_names_its_refusal(pending):
    with refused(sync_admin.NOT_RESOLVABLE):
        sync_admin.resolve_difference(
            technical_admin()[0],
            pending["world"]["mari"],
            reference="2026_505",
            field_name="no_such_field",
            note="x",
        )
