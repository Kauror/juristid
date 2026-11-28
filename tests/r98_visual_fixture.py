"""Synthetic data for the r98 pages, and a renderer for visual review (R98 visual evidence).

`seed()` builds an invented AI ledger, price, nightly records, sources and a parity report.
The test below renders each new Haldus page to an HTML file **only when** `R98_SHOT_DIR` is set;
otherwise it is skipped. A person (or the screenshot script) then loads those files in a browser
against a running dev server for the stylesheets. Nothing here is real data.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from app.ai_usage import report
from app.ai_usage.models import (
    AiAttempt,
    AiLedgerGap,
    AiOperation,
    AiPrice,
    AttemptOutcome,
    Outcome,
    UsageStatus,
)
from app.core import nightly_outcome
from app.documents.enums import ExtractionState
from app.documents.models import DocumentVersion
from app.legacy_import.lovable_sync_models import LovableParityReport
from app.register_watch.models import AgendaCheckState, GovernmentAgenda, GovernmentAgendaCheck
from tests import factories
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified

pytestmark = pytest.mark.django_db

MODEL = "claude-opus-5-5"


def _attempts(op: AiOperation, plan: list[tuple[str, int | None, int | None, int]]) -> None:
    for number, (outcome, tin, tout, ms) in enumerate(plan, start=1):
        measured = tin is not None
        AiAttempt.objects.create(
            operation=op,
            sequence=number,
            started_at=op.started_at + dt.timedelta(seconds=number),
            outcome=outcome,
            http_status=200 if outcome == AttemptOutcome.OK else 529,
            retry_class="final" if number == len(plan) else "retried",
            requested_model=MODEL,
            actual_model=MODEL if outcome == AttemptOutcome.OK else "",
            usage_status=UsageStatus.MEASURED if measured else UsageStatus.UNAVAILABLE,
            input_tokens=tin,
            output_tokens=tout,
            cache_read_tokens=0 if measured else None,
            cache_creation_tokens=0 if measured else None,
            headers_ms=ms,
        )


def seed(days: int = 8) -> None:
    now = timezone.now()
    today = now.astimezone(report.TALLINN).date()
    AiPrice.objects.create(
        model=MODEL,
        currency="EUR",
        input_per_mtok=Decimal("3.6"),
        output_per_mtok=Decimal("18"),
        cache_read_per_mtok=Decimal("0.18"),
        effective_from=today - dt.timedelta(days=days + 2),
        source_url="https://example.test/synthetic-pricing",
        verified_on=today,
    )
    for back in range(days, -1, -1):
        day = today - dt.timedelta(days=back)
        base = dt.datetime.combine(day, dt.time(2, 5), tzinfo=report.TALLINN)
        specs = [
            (
                "watch_findings",
                Outcome.SUCCEEDED,
                [(AttemptOutcome.OK, 5200 + back * 90, 700, 4100)],
            ),
            (
                "candidates",
                Outcome.SUCCEEDED_AFTER_RETRY,
                [
                    (AttemptOutcome.HTTP_ERROR, None, None, 900),
                    (AttemptOutcome.OK, 8100, 1200, 6300),
                ],
            ),
        ]
        if back % 3 == 0:
            specs.append(
                (
                    "government_agenda",
                    Outcome.FAILED,
                    [(AttemptOutcome.TIMEOUT, None, None, 120000)] * 3,
                )
            )
        for number, (service, outcome, plan) in enumerate(specs):
            op = AiOperation.objects.create(
                id=uuid.uuid4(),
                service=service,
                requested_model=MODEL,
                run_ref=f"run-{day:%m%d}",
                started_at=base + dt.timedelta(minutes=10 * number),
                finished_at=base + dt.timedelta(minutes=10 * number + 1),
                outcome=outcome,
                items_presented=12,
                items_classified=12 if outcome != Outcome.FAILED else 0,
            )
            _attempts(op, plan)
    AiLedgerGap.objects.create(
        at=now - dt.timedelta(days=1), service="candidates", kind="write_failed"
    )
    for back in range(5, 0, -1):
        start = (now - dt.timedelta(days=back)).replace(hour=21, minute=55, second=0, microsecond=0)
        for step, outcome in (
            ("lovable", nightly_outcome.SUCCEEDED),
            ("register-watch", nightly_outcome.SUCCEEDED_NO_NEW),
            ("riigikogu", nightly_outcome.SUCCEEDED),
            ("register-sweep", nightly_outcome.PARTIAL if back == 3 else nightly_outcome.SUCCEEDED),
            ("government-agenda", nightly_outcome.SUCCEEDED_NO_NEW),
        ):
            if back == 2 and step == "government-agenda":
                continue  # a step with no record
            nightly_outcome.remember(
                nightly_outcome.StepOutcome(
                    step, outcome, "TIMEOUT" if back == 3 else "", {"n": 4}
                ),
                rc=0,
                seconds=900 + back * 60,
                cycle_start=start,
            )
    GovernmentAgenda.objects.create(title="a", session_date=today - dt.timedelta(days=7))
    GovernmentAgendaCheck.objects.create(
        week=today - dt.timedelta(days=today.weekday()),
        state=AgendaCheckState.NOT_PUBLISHED,
        discovery_ok=True,
    )
    LovableParityReport.objects.create(
        at=now - dt.timedelta(hours=9),
        mode="full",
        teemad=30,
        counts={
            "MATCH": 168,
            "EXPLAINED_DIFFERENCE": 40,
            "HELD": 2,
            "MISMATCH": 0,
            "UNVERIFIED": 0,
        },
        mismatches=[],
        held=[["2099_5", "stage"], ["2099_5", "step"]],
        unverified=[],
    )
    for state, note in (
        (ExtractionState.PENDING, ""),
        (ExtractionState.DONE, ""),
        (ExtractionState.FAILED, "Faili lugemine katkestati, sest see kestis üle 240 sekundi."),
        (ExtractionState.INTAKE_READ, ""),
    ):
        DocumentVersion.objects.create(
            document=factories.DocumentFactory(),
            version_number=1,
            storage_key=f"k/{uuid.uuid4()}",
            original_filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            acquired_at=now,
            extraction_state=state,
            extraction_note=note,
        )


PAGES = {
    "overview": "haldus:index",
    "ai-usage": "haldus:ai_usage",
    "nightly": "haldus:nightly_history",
    "freshness": "haldus:freshness",
    "processing": "haldus:processing",
}


def test_render_the_r98_pages_for_review(client, settings):
    out = os.environ.get("R98_SHOT_DIR")
    if not out:
        pytest.skip("set R98_SHOT_DIR to render the pages")
    apply_admin_gate(settings)
    settings.AI_BUDGET_WARN_EUR = "0.50"
    seed()
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    for name, url_name in PAGES.items():
        response = client.get(reverse(url_name), {"periood": "7d"} if name == "ai-usage" else {})
        assert response.status_code == 200, name
        Path(out, f"{name}.html").write_text(response.content.decode(), encoding="utf-8")
    narrow = client.get(
        reverse("haldus:ai_usage"),
        {"periood": "custom", "alates": "2026-01-01", "kuni": "2099-01-01"},
    )
    assert narrow.status_code == 200


def seed_browser() -> None:
    """The browser suite's world (`e2e/test_r98_pages.py`): `seed()` plus what the pages' new
    states need — an entirely missing night, a source run that finished with errors, an
    interrupted request with no attempt, a stored and a fresh parity report, and a sync run.
    Run in a subprocess against the browser suite's own database; nothing here is real."""
    from app.legacy_import.lovable_sync_models import (
        LovableSyncRun,
        SyncMode,
        SyncRunStatus,
        SyncTrigger,
    )
    from app.register_watch.models import SweepRun, WatchRun

    seed(days=8)
    now = timezone.now()
    today = now.astimezone(report.TALLINN).date()
    from app.core.models import NightlyStepRecord

    # One night with no record at all, between recorded ones (seed() records nights 1..5 back).
    missing = now - dt.timedelta(days=4)
    NightlyStepRecord.objects.filter(
        cycle_start__gte=missing.replace(hour=0, minute=0),
        cycle_start__lt=missing.replace(hour=23, minute=59),
    ).delete()
    WatchRun.objects.create(
        started_at=now - dt.timedelta(hours=3),
        finished_at=now - dt.timedelta(hours=2),
        targets_checked=9,
        errors=[{"allikas": "riigikogu", "viga": "ajalõpp"}],
    )
    SweepRun.objects.create(
        started_at=now - dt.timedelta(hours=3), finished_at=now - dt.timedelta(hours=2)
    )
    AiOperation.objects.create(
        service="candidates",
        requested_model=MODEL,
        started_at=now - dt.timedelta(days=1),
        expected_end_at=now - dt.timedelta(days=1) + dt.timedelta(minutes=30),
        outcome="IN_PROGRESS",
    )
    for n in range(30):  # enough attempts for the detail table to need a second page
        when = now - dt.timedelta(hours=2, minutes=n)
        op = AiOperation.objects.create(
            id=uuid.uuid4(),
            service="watch_findings",
            requested_model=MODEL,
            run_ref=f"extra-{n}",
            started_at=when,
            finished_at=when + dt.timedelta(seconds=30),
            outcome=Outcome.SUCCEEDED,
            items_presented=5,
            items_classified=5,
        )
        _attempts(op, [(AttemptOutcome.OK, 4000, 500, 3000 + n)])
    started = now - dt.timedelta(hours=8)
    LovableSyncRun.objects.create(
        run_key=f"{today.isoformat()}-browser",
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.APPLY,
        status=SyncRunStatus.SUCCEEDED,
        started_at=started,
        finished_at=started + dt.timedelta(minutes=6),
        snapshot_taken_at=started,
        mapping_version="x",
        backup_set="sets/20261128T000000Z",
        verified=True,
        counts={
            "source_live": 2500,
            "source_operational": 2373,
            "checked": 2373,
            "unchanged": 2364,
            "changed_source": 9,
            "breaker_limit": 20,
            "changed_handovers": 0,
            "handover_limit": 100,
            "restore_limit": 20,
            "held_local_work": 2,
        },
    )
    LovableParityReport.objects.update(
        mismatches=[["2099_3", "title"]],
        counts={
            "MATCH": 168,
            "EXPLAINED_DIFFERENCE": 40,
            "HELD": 2,
            "MISMATCH": 1,
            "UNVERIFIED": 0,
        },
        run_key=f"{today.isoformat()}-browser",
        eligible=30,
    )
    LovableParityReport.objects.create(
        at=now - dt.timedelta(minutes=30),
        mode="sample",
        source="fresh",
        snapshot_taken_at=now - dt.timedelta(minutes=31),
        snapshot_sha256="b" * 64,
        teemad=10,
        sample_size=10,
        eligible=30,
        seed="e2e",
        counts={"MATCH": 60, "EXPLAINED_DIFFERENCE": 3, "HELD": 0, "MISMATCH": 0, "UNVERIFIED": 0},
    )
