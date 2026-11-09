"""Haldus → Andmete sünkroonimine (LS-C), on synthetic data.

Lovable is the fake transport the sync tests use (`tests.test_lovable_sync`);
OneNote's check and import are stubs standing in for Agent D's interface
(`onenote_capture.verify_capture` / `apply_capture`). Every title, name and
capture is invented. Nothing here reaches a network or a real capture.
"""

from __future__ import annotations

import datetime as dt
import io
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from app.accounts import admin_session
from app.accounts.enums import UserRole
from app.audit.enums import SecurityEventType
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.core.errors import ImmutableRecordError
from app.haldus import sections
from app.legacy_import import lovable_sync, onenote_capture, sync_admin
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.sync_requests_models import (
    DataSyncRequest,
    SyncRequestKind,
    SyncRequestLane,
    SyncRequestStatus,
)
from tests import factories
from tests.admin_session import apply_admin_gate, choose, full_admin, technical_admin, verified
from tests.refusals import refused
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

PLAN = "a" * 64
OTHER_PLAN = "b" * 64
CAPTURE = "onenote-naidis-20261109"

PAGES = (
    "haldus:data_sync",
    "haldus:data_sync_now",
    "haldus:data_sync_differences",
    "haldus:data_sync_history",
)
POSTS = (
    "haldus:data_sync_check",
    "haldus:data_sync_now",
    "haldus:data_sync_resolve",
    "haldus:data_sync_onenote_verify",
    "haldus:data_sync_onenote_import",
)


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


def _rows() -> dict[str, int]:
    return {
        "requests": DataSyncRequest.objects.count(),
        "security": SecurityAuditEvent.objects.exclude(
            event_type__in=[
                SecurityEventType.ADMINISTRATOR_SESSION_STARTED,
                SecurityEventType.SHARED_GATE_PASSED,
                SecurityEventType.PERSONA_SELECTED,
            ]
        ).count(),
        "changes": ChangeEvent.objects.count(),
        "runs": LovableSyncRun.objects.count(),
        "control": LovableSyncControl.objects.count(),
        "decisions": LovableSyncDecision.objects.count(),
    }


def _audits(action: str) -> list[SecurityAuditEvent]:
    return [
        e
        for e in SecurityAuditEvent.objects.filter(event_type=SecurityEventType.IMPORT_RUN)
        if (e.detail or {}).get("action") == action
    ]


# ---------------------------------------------------------------------------
# Who may open it
# ---------------------------------------------------------------------------


def test_the_section_replaces_the_lovable_tab_and_keeps_its_address(signed):
    page = signed.get(reverse("haldus:index"))
    keys = [s.key for s in page.context["haldus_sections"]]
    assert "andmed" in keys and "lovable" not in keys
    assert sections.section("andmed").url_name == "haldus:data_sync"
    assert reverse("haldus:data_sync") == "/haldus/andmete-sunkroonimine/"
    # The older Lovable page still answers at its address, as a sub-page.
    old = signed.get(reverse("haldus:lovable"))
    assert old.status_code == 200
    assert reverse("haldus:data_sync") in old.content.decode()


def test_a_technical_administrator_opens_every_page(signed):
    for name in PAGES:
        assert signed.get(reverse(name)).status_code == 200, name
    body = signed.get(reverse("haldus:data_sync")).content.decode()
    assert "Lovable" in body and "OneNote" in body
    assert "Inventuuri pole veel tehtud." in body


def test_anonymous_is_refused_everywhere(client, gate):
    for name in PAGES:
        response = client.get(reverse(name))
        assert response.status_code in (302, 404), name
        assert response.status_code != 200
    for name in POSTS:
        assert client.post(reverse(name)).status_code in (302, 404), name
    assert DataSyncRequest.objects.count() == 0


@pytest.mark.parametrize(
    "factory",
    [factories.UserFactory, factories.DepartmentHeadFactory],
    ids=["specialist", "department-head"],
)
def test_a_business_persona_without_the_step_up_gets_404(client, gate, factory):
    from tests.admin_session import pass_gate

    pass_gate(client)
    choose(client, factory())
    _compare()
    for name in PAGES:
        assert client.get(reverse(name)).status_code == 404, name
    for name in POSTS:
        assert client.post(reverse(name), {"reason": "x"}).status_code == 404, name
    assert DataSyncRequest.objects.count() == 0


def test_a_verified_administrator_who_is_not_technical_gets_404(client, gate):
    head, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    verified(client, head, secret)
    _compare()
    for name in PAGES:
        assert client.get(reverse(name)).status_code == 404, name
    assert client.post(reverse("haldus:data_sync_now"), {"reason": "x"}).status_code == 404
    assert DataSyncRequest.objects.count() == 0


def test_a_stale_proof_asks_again_and_records_nothing(signed, monkeypatch):
    _compare()
    monkeypatch.setattr(admin_session, "needs_fresh_proof", lambda request: True)
    response = signed.post(
        reverse("haldus:data_sync_now"), {"reason": "kontroll", "mode_seen": SyncMode.COMPARE}
    )
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("haldus:reauthenticate"))
    assert DataSyncRequest.objects.count() == 0


def test_every_post_needs_the_csrf_token(signed):
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = signed.cookies  # the same verified session, without a token
    assert strict.get(reverse("haldus:data_sync")).status_code == 200
    _compare()
    for name in POSTS:
        assert strict.post(reverse(name), {"reason": "x"}).status_code == 403, name
    assert DataSyncRequest.objects.count() == 0


def test_get_and_head_write_nothing(signed):
    before = _rows()
    for name in PAGES:
        assert signed.get(reverse(name)).status_code == 200, name
        assert signed.head(reverse(name)).status_code == 200, name
    signed.get(reverse("haldus:data_sync_onenote_import"), {"capture": CAPTURE})
    assert _rows() == before
    # Reading never creates the control row (lovable_sync.control() would).
    assert not LovableSyncControl.objects.exists()


# ---------------------------------------------------------------------------
# «Sünkrooni nüüd»
# ---------------------------------------------------------------------------


def test_the_confirmation_states_the_mode_and_what_happens(signed):
    _compare()
    body = signed.get(reverse("haldus:data_sync_now")).content.decode()
    assert "Ainult võrdlus" in body
    assert "Juristide teemasid ei muudeta" in body
    assert f'name="mode_seen" value="{SyncMode.COMPARE}"' in body


def test_sync_now_records_one_request_and_an_audit_row(signed, admin):
    _compare()
    response = signed.post(
        reverse("haldus:data_sync_now"), {"reason": "uus teema", "mode_seen": SyncMode.COMPARE}
    )
    assert response.status_code == 302
    item = DataSyncRequest.objects.get()
    assert item.kind == SyncRequestKind.LOVABLE_SYNC
    assert item.status == SyncRequestStatus.PENDING
    assert item.requested_by == admin["user"]
    # The web wrote no run: the host runner does that.
    assert not LovableSyncRun.objects.exists()
    rows = _audits("DATA_SYNC_REQUESTED")
    assert len(rows) == 1 and rows[0].actor == admin["user"]
    assert rows[0].detail["mode"] == SyncMode.COMPARE
    page = signed.get(reverse("haldus:data_sync")).content.decode()
    assert "Ootab käivitamist" in page and "Värskenda seisu" in page


def test_a_second_sync_request_is_refused_while_one_is_open(signed, admin):
    _compare()
    data = {"reason": "esimene", "mode_seen": SyncMode.COMPARE}
    assert signed.post(reverse("haldus:data_sync_now"), data).status_code == 302
    second = signed.post(reverse("haldus:data_sync_now"), {**data, "reason": "teine"})
    assert second.status_code == 409
    assert escape(sync_admin.LANE_BUSY) in second.content.decode()
    assert DataSyncRequest.objects.count() == 1
    refused_rows = _audits("DATA_SYNC_REQUEST_REFUSED")
    assert [r.detail["code"] for r in refused_rows] == ["LANE_BUSY"]
    assert all(r.succeeded is False for r in refused_rows)


def test_the_database_holds_one_open_request_per_lane(admin):
    now = dt.datetime(2026, 11, 9, 10, 0, tzinfo=dt.UTC)
    DataSyncRequest.objects.create(
        kind=SyncRequestKind.ONENOTE_VERIFY,
        requested_by=admin["user"],
        reason="a",
        requested_at=now,
        capture_name=CAPTURE,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DataSyncRequest.objects.bulk_create(
            [
                DataSyncRequest(
                    kind=SyncRequestKind.ONENOTE_IMPORT,
                    lane=SyncRequestLane.ONENOTE,
                    requested_by=admin["user"],
                    reason="b",
                    requested_at=now,
                    capture_name=CAPTURE,
                    approved_plan_sha256=PLAN,
                )
            ]
        )


def test_a_changed_mode_refuses_the_confirmed_request(admin):
    _compare()
    with refused(sync_admin.MODE_CHANGED):
        sync_admin.request_lovable_sync(admin["user"], "x", mode_seen=SyncMode.APPLY)
    assert DataSyncRequest.objects.count() == 0


def test_a_paused_or_cut_over_sync_cannot_be_requested(admin):
    with refused(sync_admin.SYNC_PAUSED):
        sync_admin.request_lovable_sync(admin["user"], "x")
    lovable_sync.set_mode(SyncMode.OFF, reason="t")
    lovable_sync.cut_over(reason="t")
    with refused(sync_admin.SYNC_CUT_OVER):
        sync_admin.request_lovable_sync(admin["user"], "x")
    assert DataSyncRequest.objects.count() == 0


def test_a_request_needs_a_reason(admin):
    _compare()
    with refused(sync_admin.NEEDS_REASON):
        sync_admin.request_lovable_sync(admin["user"], "   ")


def test_a_stale_pending_request_expires_and_is_never_deleted(admin):
    _compare()
    early = dt.datetime(2026, 11, 9, 6, 0, tzinfo=dt.UTC)
    first = sync_admin.request_lovable_sync(admin["user"], "hommik", now=early)
    later = early + sync_admin.PENDING_EXPIRES_AFTER + dt.timedelta(minutes=1)
    second = sync_admin.request_lovable_sync(admin["user"], "lõuna", now=later)
    first.refresh_from_db()
    assert first.status == SyncRequestStatus.EXPIRED
    assert first.outcome_code == "EXPIRED_UNCLAIMED"
    assert second.status == SyncRequestStatus.PENDING
    assert DataSyncRequest.objects.count() == 2
    with pytest.raises(ImmutableRecordError):
        first.delete()


def test_status_only_moves_forward_even_under_raw_sql(admin):
    _compare()
    item = sync_admin.request_lovable_sync(admin["user"], "x")
    item.status = SyncRequestStatus.DONE  # PENDING → DONE skips RUNNING
    with pytest.raises(ImmutableRecordError):
        item.save()
    item.status = SyncRequestStatus.PENDING
    item.reason = "muudetud"
    with pytest.raises(ImmutableRecordError):
        item.save()
    for statement in (
        "UPDATE legacy_import_datasyncrequest SET status = 'DONE'",
        "UPDATE legacy_import_datasyncrequest SET reason = 'muudetud'",
        "DELETE FROM legacy_import_datasyncrequest",
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement)
    assert DataSyncRequest.objects.get().status == SyncRequestStatus.PENDING


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def _backup(moment: dt.datetime) -> str:
    return (moment - dt.timedelta(minutes=5)).astimezone(dt.UTC).strftime("sets/%Y%m%dT%H%M%SZ")


def test_run_next_with_nothing_pending_does_nothing(db):
    outcome = sync_admin.run_next(SyncRequestLane.LOVABLE)
    assert outcome.request is None and outcome.code == sync_admin.NOTHING
    assert not outcome.failed
    out = io.StringIO()
    call_command("data_sync_requests", "run-next", "--kind", "onenote", stdout=out)
    assert sync_admin.NOTHING in out.getvalue()


def test_run_next_runs_the_same_engine_with_a_fresh_read(cohort, gate):  # noqa: F811
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    user, _ = technical_admin()
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    requested = sync_admin.request_lovable_sync(user, "kontroll", now=NIGHT)
    reads: list[str] = []
    source = cohort["source"]
    source.on_select = reads.append
    outcome = sync_admin.run_next(
        SyncRequestLane.LOVABLE,
        transport_factory=lambda: source,
        backup_set=_backup(NIGHT),
        now=lambda: NIGHT,
    )
    item = outcome.request
    assert item is not None and item.pk == requested.pk
    assert item.status == SyncRequestStatus.DONE and not outcome.failed
    run = item.sync_run
    assert run is not None and run.trigger == SyncTrigger.MANUAL
    assert run.mode == SyncMode.COMPARE
    assert "topic" in reads  # the source was read, not a stored preview
    assert item.result["counts"]["checked"] == run.counts["checked"]
    assert [a.detail["status"] for a in _audits("DATA_SYNC_FINISHED")] == ["DONE"]
    assert len(_audits("DATA_SYNC_STARTED")) == 1


def test_a_failed_read_fails_the_request_and_keeps_the_run(cohort, gate):  # noqa: F811
    from app.legacy_import.lovable_source import SourceError

    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    user, _ = technical_admin()
    sync_admin.request_lovable_sync(user, "kontroll", now=NIGHT)
    cohort["source"].fail = SourceError("HTTP_503", "unavailable")
    outcome = sync_admin.run_next(
        SyncRequestLane.LOVABLE,
        transport_factory=lambda: cohort["source"],
        backup_set=_backup(NIGHT),
        now=lambda: NIGHT,
    )
    assert outcome.failed
    item = outcome.request
    assert item.status == SyncRequestStatus.FAILED and item.outcome_code == "HTTP_503"
    assert item.sync_run.status == SyncRunStatus.FAILED
    assert "503" in sync_admin.failure_reason(item.outcome_code)


def test_a_request_the_engine_refuses_is_refused_not_retried(admin):
    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x")
    lovable_sync.set_mode(SyncMode.OFF, reason="peatatud vahepeal")
    outcome = sync_admin.run_next(SyncRequestLane.LOVABLE, transport_factory=lambda: object())
    assert outcome.failed
    assert outcome.request.status == SyncRequestStatus.REFUSED
    assert outcome.request.outcome_code == "PAUSED"
    assert not LovableSyncRun.objects.exists()


def test_the_command_exits_1_on_failure_and_needs_the_switches(admin, settings, monkeypatch):
    from django.core.management.base import CommandError

    from app.legacy_import.management.commands import data_sync_requests as command

    _compare()
    sync_admin.request_lovable_sync(admin["user"], "x")
    settings.LOVABLE_SYNC_ENABLED = False
    with pytest.raises(CommandError, match="nothing was claimed"):
        call_command("data_sync_requests", "run-next", "--kind", "lovable")
    assert DataSyncRequest.objects.get().status == SyncRequestStatus.PENDING
    settings.LOVABLE_SYNC_ENABLED = True
    settings.REAL_DATA_ALLOWED = True

    def broken(options: dict[str, Any]) -> Any:
        def build() -> Any:
            raise RuntimeError("no credentials here")

        return build

    monkeypatch.setattr(command, "lovable_transport", broken)
    with pytest.raises(SystemExit) as exit_info:
        call_command("data_sync_requests", "run-next", "--kind", "lovable", stdout=io.StringIO())
    assert exit_info.value.code == 1
    assert DataSyncRequest.objects.get().status == SyncRequestStatus.FAILED
    out = io.StringIO()
    call_command("data_sync_requests", "list", stdout=out)
    assert "FAILED" in out.getvalue()


def test_a_lost_runner_is_recorded_as_failed(admin):
    _compare()
    start = dt.datetime(2026, 11, 9, 1, 0, tzinfo=dt.UTC)
    sync_admin.request_lovable_sync(admin["user"], "x", now=start)
    item = sync_admin.claim_next(SyncRequestLane.LOVABLE, now=start)
    assert item is not None
    later = start + sync_admin.RUNNING_LOST_AFTER + dt.timedelta(minutes=1)
    assert sync_admin.recover_lost(SyncRequestLane.LOVABLE, now=later) == 1
    item.refresh_from_db()
    assert item.status == SyncRequestStatus.FAILED and item.outcome_code == "RUNNER_LOST"


# ---------------------------------------------------------------------------
# Counters equal records; history
# ---------------------------------------------------------------------------


def test_the_panel_counts_equal_the_ledger(cohort, gate):  # noqa: F811
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses", teema="Uus pealkiri 501")
    cohort["source"].edit("2026_505", jargmiseks="Uus sõnastus")
    run = _run(cohort)
    panel = sync_admin.lovable_panel(None)
    assert panel.run == run
    for outcome in SyncOutcome.values:
        assert panel.decisions[outcome] == run.decisions.filter(outcome=outcome).count(), outcome
    assert panel.updated_teemad == run.counts["updated"]
    assert panel.source_live == run.counts["source_live"]
    assert panel.cohort_size == lovable_sync.LovableSyncState.objects.count()
    assert panel.decisions[SyncOutcome.REVIEW_REQUIRED] > 0
    history = sync_admin.history(page=1)
    row = next(r for r in history.object_list if r.run == run)
    assert row.review == run.decisions.filter(outcome=SyncOutcome.REVIEW_REQUIRED).count()
    assert row.conflicts == run.decisions.filter(outcome=SyncOutcome.CONFLICT).count()


def test_the_history_is_paginated(cohort, gate):  # noqa: F811
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    for n in range(sync_admin.HISTORY_PAGE_SIZE + 1):
        _run(cohort, at=NIGHT + dt.timedelta(minutes=n))
    first = sync_admin.history(page=1)
    assert len(first.object_list) == sync_admin.HISTORY_PAGE_SIZE
    assert first.paginator.num_pages == 2
    assert len(sync_admin.history(page=2).object_list) == 1


def test_a_failed_night_shows_its_reason(cohort, gate, client):  # noqa: F811
    from app.legacy_import.lovable_source import SourceError

    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].fail = SourceError("SIGN_IN_FAILED", "x")
    _run(cohort, SyncTrigger.SCHEDULED)
    admin_user, secret = technical_admin()
    verified(client, admin_user, secret)
    body = client.get(reverse("haldus:data_sync")).content.decode()
    assert "Vajab tähelepanu" in body
    assert escape(sync_admin.FAILURE_REASONS["SIGN_IN_FAILED"]) in body


# ---------------------------------------------------------------------------
# «Vaata erinevusi» and decisions
# ---------------------------------------------------------------------------


def test_an_invisible_teema_is_counted_never_named(cohort, gate, client):  # noqa: F811
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    admin_user, secret = technical_admin()
    verified(client, admin_user, secret)
    body = client.get(reverse("haldus:data_sync_differences")).content.decode()
    assert "Valitsuses" not in body and "2026_501" not in body
    assert "teemadel, mida sinu valitud isik ei näe" in body
    # Choosing a business persona names it.
    choose(client, cohort["world"]["mari"])
    body = client.get(reverse("haldus:data_sync_differences")).content.decode()
    assert "Valitsuses" in body and "Otsusta" in body


def test_the_engines_differences_are_used_and_filtered_when_present(admin, monkeypatch):
    visible = factories.MatterFactory()
    hidden = SimpleNamespace(pk="00000000-0000-0000-0000-000000000001")
    items = [
        SimpleNamespace(
            matter=visible,
            field="stage",
            reason="HELD_FIELD",
            since=None,
            accepted="Riigikogus",
            juristid="Riigikogus",
            source="Valitsuses",
        ),
        SimpleNamespace(
            matter=hidden,
            field="title",
            reason="",
            since=None,
            accepted="SALAJANE",
            juristid="SALAJANE",
            source="SALAJANE",
        ),
    ]
    seen: list[Any] = []

    def pending_differences(*, visible_to: Any = None) -> list[Any]:
        seen.append(visible_to)
        return items

    monkeypatch.setattr(lovable_sync, "pending_differences", pending_differences, raising=False)
    reader = factories.UserFactory()
    result = sync_admin.differences(reader)
    assert seen == [reader]
    assert result.source == "engine"
    assert [r.source for r in result.rows] == ["Valitsuses"]
    assert result.hidden == 1
    assert all("SALAJANE" not in (r.accepted + r.source) for r in result.rows)


def test_a_decision_is_confirmed_audited_and_made_by_the_engine(cohort, gate, client):  # noqa: F811
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    admin_user, secret = technical_admin()
    verified(client, admin_user, secret)
    choose(client, cohort["world"]["mari"])
    url = reverse("haldus:data_sync_resolve")
    confirm = client.get(url, {"viide": "2026_501", "vali": "stage"})
    assert confirm.status_code == 200
    assert "Kinnitan otsuse" in confirm.content.decode()
    before = LovableSyncDecision.objects.count()
    response = client.post(url, {"viide": "2026_501", "vali": "stage", "note": "vaadatud"})
    assert response.status_code == 302
    decision = LovableSyncDecision.objects.order_by("-created_at").first()
    assert LovableSyncDecision.objects.count() == before + 1
    assert decision.field == "stage" and decision.reason.startswith(lovable_sync.RESOLVED_PREFIX)
    rows = _audits("LOVABLE_FIELD_RESOLVED")
    assert len(rows) == 1 and rows[0].actor == admin_user and rows[0].detail["field"] == "stage"


def test_a_decision_on_an_invisible_teema_is_404(cohort, gate, client):  # noqa: F811
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    admin_user, secret = technical_admin()
    verified(client, admin_user, secret)
    url = reverse("haldus:data_sync_resolve")
    assert client.get(url, {"viide": "2026_501", "vali": "stage"}).status_code == 404
    data = {"viide": "2026_501", "vali": "stage", "note": "x"}
    assert client.post(url, data).status_code == 404
    with refused(sync_admin.NOT_RESOLVABLE):
        sync_admin.resolve_difference(
            admin_user, None, reference="2026_501", field_name="stage", note="x"
        )


# ---------------------------------------------------------------------------
# OneNote
# ---------------------------------------------------------------------------


@pytest.fixture
def capture_root(tmp_path, settings):
    root = tmp_path / "captures"
    (root / CAPTURE).mkdir(parents=True)
    settings.ONENOTE_CAPTURE_ROOT = str(root)
    return root


def _stub_onenote(monkeypatch, *, plan: str = PLAN, ok: bool = True) -> dict[str, Any]:
    calls: dict[str, Any] = {"verify": [], "apply": []}

    def verify_capture(path: Any) -> Any:
        calls["verify"].append(path)
        return SimpleNamespace(
            ok=ok,
            plan_sha256=calls.get("plan", plan),
            manifest_sha256="c" * 64,
            problems=[] if ok else ["Fail puudu: salajane.pdf"],
            summary={"pages": 3, "promote": 5, "hold": 1, "capture_id": "x"},
        )

    def apply_capture(path: Any, *, approved_plan_sha256: str, actor: Any) -> Any:
        calls["apply"].append((path, approved_plan_sha256, actor))
        return {"pages_written": 3, "promoted": 5}

    monkeypatch.setattr(onenote_capture, "verify_capture", verify_capture, raising=False)
    monkeypatch.setattr(onenote_capture, "apply_capture", apply_capture, raising=False)
    return calls


def test_a_verify_request_runs_on_the_host_and_shows_its_check(
    signed, admin, capture_root, monkeypatch
):
    calls = _stub_onenote(monkeypatch)
    response = signed.post(
        reverse("haldus:data_sync_onenote_verify"), {"capture": CAPTURE, "reason": "uus kogum"}
    )
    assert response.status_code == 302
    assert calls["verify"] == []  # the web does not read the capture
    outcome = sync_admin.run_next(SyncRequestLane.ONENOTE)
    item = outcome.request
    assert item.status == SyncRequestStatus.DONE
    assert item.result["verified"] is True and item.result["plan_sha256"] == PLAN
    assert item.result["counts"] == {"pages": 3, "promote": 5, "hold": 1}
    assert calls["verify"] == [capture_root / CAPTURE]
    body = signed.get(reverse("haldus:data_sync")).content.decode()
    assert "Kontrollitud" in body and "Impordi…" in body
    assert "salajane" not in body


def test_a_capture_name_that_is_a_path_is_refused(admin):
    for name in ("../etc", "a/b", "", ".hidden", "a..b"):
        with refused(sync_admin.BAD_CAPTURE_NAME):
            sync_admin.request_onenote_verify(admin["user"], name, "x")
    assert DataSyncRequest.objects.count() == 0


def test_an_import_is_refused_without_a_verified_plan(admin, capture_root, monkeypatch):
    with refused(sync_admin.NOT_VERIFIED):
        sync_admin.request_onenote_import(admin["user"], CAPTURE, PLAN, "x")
    _stub_onenote(monkeypatch, ok=False)
    sync_admin.request_onenote_verify(admin["user"], CAPTURE, "x")
    outcome = sync_admin.run_next(SyncRequestLane.ONENOTE)
    assert outcome.request.status == SyncRequestStatus.FAILED
    assert outcome.request.outcome_code == "NOT_VERIFIED"
    assert outcome.request.result["problems"] == 1
    with refused(sync_admin.NOT_VERIFIED):
        sync_admin.request_onenote_import(admin["user"], CAPTURE, PLAN, "x")
    assert not DataSyncRequest.objects.filter(kind=SyncRequestKind.ONENOTE_IMPORT).exists()
    assert [r.detail["code"] for r in _audits("DATA_SYNC_REQUEST_REFUSED")] == [
        "NOT_VERIFIED",
        "NOT_VERIFIED",
    ]


def test_an_import_with_another_sha_is_refused(admin, capture_root, monkeypatch):
    _stub_onenote(monkeypatch)
    sync_admin.request_onenote_verify(admin["user"], CAPTURE, "x")
    sync_admin.run_next(SyncRequestLane.ONENOTE)
    with refused(sync_admin.PLAN_CHANGED):
        sync_admin.request_onenote_import(admin["user"], CAPTURE, OTHER_PLAN, "x")
    assert not DataSyncRequest.objects.filter(kind=SyncRequestKind.ONENOTE_IMPORT).exists()


def test_an_import_whose_plan_changed_on_the_host_writes_nothing(admin, capture_root, monkeypatch):
    calls = _stub_onenote(monkeypatch)
    sync_admin.request_onenote_verify(admin["user"], CAPTURE, "x")
    sync_admin.run_next(SyncRequestLane.ONENOTE)
    sync_admin.request_onenote_import(admin["user"], CAPTURE, PLAN, "import")
    calls["plan"] = OTHER_PLAN  # the capture or Juristid moved since the check
    outcome = sync_admin.run_next(SyncRequestLane.ONENOTE)
    assert outcome.request.status == SyncRequestStatus.REFUSED
    assert outcome.request.outcome_code == "PLAN_CHANGED"
    assert calls["apply"] == []


def test_a_verified_import_runs_the_gated_importer_bound_to_the_plan(
    signed, admin, capture_root, monkeypatch
):
    calls = _stub_onenote(monkeypatch)
    sync_admin.request_onenote_verify(admin["user"], CAPTURE, "x")
    sync_admin.run_next(SyncRequestLane.ONENOTE)
    confirm = signed.get(reverse("haldus:data_sync_onenote_import"), {"capture": CAPTURE})
    assert confirm.status_code == 200 and PLAN in confirm.content.decode()
    response = signed.post(
        reverse("haldus:data_sync_onenote_import"),
        {"capture": CAPTURE, "plan_sha256": PLAN, "reason": "impordi"},
    )
    assert response.status_code == 302
    item = DataSyncRequest.objects.get(kind=SyncRequestKind.ONENOTE_IMPORT)
    assert item.approved_plan_sha256 == PLAN and item.status == SyncRequestStatus.PENDING
    outcome = sync_admin.run_next(SyncRequestLane.ONENOTE)
    assert outcome.request.status == SyncRequestStatus.DONE
    assert calls["apply"] == [(capture_root / CAPTURE, PLAN, admin["user"])]
    assert outcome.request.result["counts"] == {"pages_written": 3, "promoted": 5}


def test_without_ds_interface_the_request_is_refused_not_guessed(admin, capture_root, monkeypatch):
    monkeypatch.delattr(onenote_capture, "verify_capture", raising=False)
    sync_admin.request_onenote_verify(admin["user"], CAPTURE, "x")
    outcome = sync_admin.run_next(SyncRequestLane.ONENOTE)
    assert outcome.request.status == SyncRequestStatus.REFUSED
    assert outcome.request.outcome_code == "NOT_AVAILABLE"


def test_the_onenote_panel_reads_ds_summary_and_degrades_without_it(db, monkeypatch):
    panel = sync_admin.onenote_panel()
    assert not panel.available and panel.note == sync_admin.ONENOTE_MISSING
    fake = SimpleNamespace(
        coverage_summary=lambda: {
            "last_capture": {"capture_id": "kogum-1", "captured_at": None, "section": "Näidis"},
            "pages_checked": 12,
            "imported": 7,
            "failed": 0,
            "sections_not_covered": ["Vanad asjad"],
            "per_state": {"IMPORTED": 7, "NOT_INSPECTED": 5},
        }
    )
    monkeypatch.setattr(sync_admin, "_coverage_summary", fake.coverage_summary)
    panel = sync_admin.onenote_panel()
    assert panel.available
    counts = dict(panel.counts)
    assert counts["Kontrollitud lehti"] == 12 and counts["Imporditud"] == 7
    assert counts["Uusi lehti"] is None
    assert panel.sections_not_covered == ["Vanad asjad"]
    assert panel.sections_not_covered_count == 1


def test_the_page_renders_ds_summary(signed, monkeypatch):
    summary = {
        "last_capture": {"capture_id": "kogum-1", "captured_at": None, "section": "Näidis"},
        "pages_checked": 12,
        "awaiting_association": 3,
        "sections_not_covered": ["Vanad asjad"],
        "per_state": {"IMPORTED": 7},
    }
    monkeypatch.setattr(sync_admin, "_coverage_summary", lambda: summary)
    body = signed.get(reverse("haldus:data_sync")).content.decode()
    assert "kogum-1" in body and "Kontrollitud lehti" in body and "Vanad asjad" in body
    assert "Ootab seose otsust" in body
    assert sync_admin.ONENOTE_MISSING not in body
