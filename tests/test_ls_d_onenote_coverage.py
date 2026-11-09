"""OneNote coverage, the worklist and the request API (live-sync round 2026-11, agent D).

Every count ``coverage_summary`` gives is checked against the records it names;
the worklist is checked to hold exactly the pages a person should capture next,
never the notebook. Invented Teemad, pages and captures only.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.utils import timezone

from app.core.errors import ImmutableRecordError
from app.documents.models import Document
from app.legacy_import import onenote_capture as oc
from app.legacy_import import onenote_coverage as cov
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncRun,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.onenote_coverage_models import OneNoteCoverageMark, PageState
from app.legacy_import.source_pages import (
    LegacySourceResourceImport,
    MatterSourcePage,
    ResourceImportState,
)
from tests.test_onenote_capture_changes import FIRST, T3, build, f, run
from tests.test_onenote_capture_import import register_teema, stored_link

pytestmark = pytest.mark.django_db

G_A = "0000BB01-0000-4000-8000-0000000000B1"
G_B = "0000BB02-0000-4000-8000-0000000000B2"
G_S = "0000BB03-0000-4000-8000-0000000000B3"
G_X = "0000BB04-0000-4000-8000-0000000000B4"


@pytest.fixture
def enabled(settings):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


def linked(reference: str, guid: str | None, *, is_open: bool = True):
    teema = register_teema(reference, is_open=is_open)
    if guid is not None:
        state = teema.lovable_sync_state
        state.applied = {"onenote_link": stored_link(guid)}
        state.save(update_fields=["applied"])
    return teema


@pytest.fixture
def world(tmp_path, enabled):
    """A captured (A), a not captured (B), a shared captured page (S1, S2), no link (C),
    and a link naming two pages (D)."""
    a = linked("2026_61", G_A)
    b = linked("2026_62", G_B, is_open=False)
    s1, s2 = linked("2026_63", G_S), linked("2026_64", G_S)
    c = linked("2026_65", None)
    d = linked("2026_66", None)
    state = d.lovable_sync_state
    state.applied = {"onenote_link": f"{stored_link(G_A)} {stored_link(G_B)}"}
    state.save(update_fields=["applied"])
    root = build(
        tmp_path / "c1",
        "cap-1",
        [
            {"tag": "001", "guid": G_A, "matters": [a], "files": FIRST},
            {"tag": "002", "guid": G_S, "matters": [s1, s2], "files": FIRST[:1]},
        ],
        link_pages=False,
    )
    run(root)
    return {"a": a, "b": b, "s1": s1, "s2": s2, "c": c, "d": d, "root": root, "tmp": tmp_path}


# -- the summary -----------------------------------------------------------------------


def test_coverage_counts_equal_the_records(world):
    summary = cov.coverage_summary()
    op = summary["operational"]
    assert summary["archive_complete"] is False
    assert op["teemad"] == 6 and op["open_teemad"] == 5
    assert op["teemad_with_exact_page"] == 4  # A, B, S1, S2
    assert op["teemad_without_page_link"] == 1 and op["teemad_with_ambiguous_link"] == 1
    assert op["pages"] == 3 and op["shared_pages"] == 1
    assert op["pages_captured"] == 2 and op["pages_not_captured"] == 1
    assert op["teemad_with_captured_page"] == 3 and op["teemad_without_capture"] == 1
    assert op["open_teemad_without_capture"] == 0  # B is closed
    assert op["captured_by"] == {"cap-1": 2}
    rows = LegacySourceResourceImport.objects.all()
    assert op["attachments_imported"] == rows.filter(state=ResourceImportState.IMPORTED).count()
    assert op["attachments_imported"] == Document.objects.count() == 3
    assert op["attachments_held"] == {oc.HOLD_SHARED_PAGE: 2}
    assert sum(op["states"].values()) == op["pages"]
    assert op["states"][PageState.IMPORTED] == 1
    assert op["states"][PageState.AWAITING_ASSOCIATION] == 1
    assert op["states"][PageState.NOT_INSPECTED] == 1
    assert summary["last_capture"]["capture_id"] == "cap-1"


def test_apply_records_a_mark_per_page_with_its_capture(world):
    marks = {m.page_guid: m for m in OneNoteCoverageMark.objects.all()}
    assert marks[G_A].state == PageState.IMPORTED and marks[G_A].capture_id == "cap-1"
    assert marks[G_S].state == PageState.AWAITING_ASSOCIATION
    assert marks[G_A].source_page is not None
    assert cov.page_states([G_A])[G_A]["basis"] == "MARK"


def test_a_page_without_any_mark_is_read_from_its_records(world):
    catalogued = cov._catalogued()
    assert cov._derived_state(catalogued[G_A]) == PageState.IMPORTED
    assert cov._derived_state(catalogued[G_S]) == PageState.AWAITING_ASSOCIATION
    assert cov._derived_state(catalogued.get(G_B)) == PageState.NOT_INSPECTED
    states = cov.page_states([G_B])
    assert states[G_B]["state"] == PageState.NOT_INSPECTED and states[G_B]["basis"] == "RECORDS"


def test_a_coverage_mark_is_append_only(world):
    mark = OneNoteCoverageMark.objects.first()
    mark.reason = "muudetud"
    with pytest.raises(ImmutableRecordError):
        mark.save()
    with pytest.raises(Exception, match="Append-only table"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE legacy_import_onenotecoveragemark SET reason = 'x'")


def test_marks_refuse_an_unknown_state():
    with pytest.raises(ValueError, match="not a page state"):
        cov.mark_pages([G_A], "DONE", reason="x")


# -- the worklist ----------------------------------------------------------------------


def _decision(teema, field, source, *, at, outcome=SyncOutcome.REVIEW_REQUIRED):
    run_row = LovableSyncRun.objects.create(
        run_key=uuid.uuid4().hex[:40],
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.COMPARE,
        status=SyncRunStatus.SUCCEEDED,
        started_at=at,
        mapping_version="test",
    )
    decision = LovableSyncDecision.objects.create(
        run=run_row,
        state=teema.lovable_sync_state,
        field=field,
        outcome=outcome,
        source=source,
    )
    LovableSyncDecision.objects.filter(pk=decision.pk).update(created_at=at)
    return decision


def _work(since=None):
    return {item.page_guid: set(item.reasons) for item in cov.worklist(since=since)}


def test_the_worklist_holds_the_uncaptured_operational_page_and_nothing_else(world):
    assert _work() == {G_B: {cov.REASON_NO_CAPTURE}}


def test_a_lovable_change_since_the_last_capture_puts_its_page_back(world):
    since = timezone.now()
    _decision(world["a"], "next_action_text", "vana", at=since - timedelta(days=1))
    _decision(world["a"], "next_action_text", "uus", at=since + timedelta(hours=1))
    assert _work(since)[G_A] == {cov.REASON_LOVABLE_CHANGED}


def test_a_pending_difference_reported_again_is_not_a_change(world):
    since = timezone.now()
    _decision(world["a"], "stage", "OPINION", at=since - timedelta(days=1))
    _decision(world["a"], "stage", "OPINION", at=since + timedelta(hours=1))
    assert G_A not in _work(since)


def test_a_page_read_after_the_change_leaves_the_worklist(world):
    since = OneNoteCoverageMark.objects.get(page_guid=G_A).recorded_at - timedelta(seconds=1)
    # The change arrives after the page was imported, so the page is read again ...
    _decision(world["a"], "sent", True, at=timezone.now(), outcome=SyncOutcome.CONFLICT)
    assert G_A in _work(since)
    # ... until a person reads it and finds nothing new.
    cov.mark_pages([G_A], PageState.NO_CHANGES, reason="loetud, muudatusi pole")
    assert G_A not in _work(since)


def test_a_changed_onenote_link_names_the_new_page(world):
    since = timezone.now()
    _decision(world["a"], "onenote_link", stored_link(G_A), at=since - timedelta(days=1))
    _decision(world["a"], "onenote_link", stored_link(G_X), at=since + timedelta(hours=1))
    work = _work(since)
    assert work[G_X] == {cov.REASON_LINK_CHANGED}
    assert cov.REASON_LOVABLE_CHANGED in work[G_A]


def test_an_observed_but_unapplied_link_is_listed_until_the_page_is_read(world):
    state = world["a"].lovable_sync_state
    state.observed = {"onenote_link": stored_link(G_X)}
    state.save(update_fields=["observed"])
    assert _work()[G_X] == {cov.REASON_OBSERVED_LINK}
    cov.mark_pages([G_X], PageState.NO_CHANGES, reason="leht loetud")
    assert G_X not in _work()


def test_a_failed_page_is_listed_for_reinspection(world):
    cov.mark_pages([G_A], PageState.FAILED, reason="leht ei avanenud")
    assert _work()[G_A] == {cov.REASON_MARKED}


def test_the_worklist_defaults_to_changes_after_the_last_capture(world):
    _decision(world["a"], "title", "uus", at=datetime(2020, 1, 1, tzinfo=UTC))
    assert G_A not in _work()  # before the capture: already read


def test_the_capture_request_carries_identities_only(world, tmp_path):
    out = tmp_path / "request.json"
    call_command("onenote_capture_import", "worklist", "--out", str(out), stdout=_Sink())
    request = json.loads(out.read_text(encoding="utf-8"))
    assert [p["page_guid"] for p in request["pages"]] == [G_B]
    assert set(request["register_map"]) == {G_A, G_B, G_S}
    assert {t["reference"] for t in request["register_map"][G_S]} == {"2026_63", "2026_64"}
    assert "title" not in json.dumps(request)


class _Sink:
    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text

    def flush(self):
        pass


# -- the request API -------------------------------------------------------------------


def test_verify_capture_reports_a_broken_seal_without_raising(world):
    (world["root"] / "manifest.json").write_text("{}", encoding="utf-8")
    check = oc.verify_capture(world["root"], record=True)
    assert not check.ok and "does not match the seal" in check.problems[0]
    assert not OneNoteCoverageMark.objects.filter(reason="verify_capture").exists()


def _second(world):
    return build(
        world["tmp"] / "c2",
        "cap-2",
        [
            {
                "tag": "001",
                "guid": G_A,
                "matters": [world["a"]],
                "files": [*FIRST, f("{B4}", "uus.pdf", T3)],
            },
            {"tag": "002", "guid": G_S, "matters": [world["s1"], world["s2"]], "files": FIRST[:1]},
        ],
        link_pages=False,
    )


def test_verify_capture_gives_the_plan_to_approve_and_records_the_page_states(world):
    root = _second(world)
    check = oc.verify_capture(root, record=True)
    assert check.ok and check.plan_sha256 == oc.build_plan(oc.load_capture(root)).digest
    assert check.page_states == {f"p-{G_A.lower()}": "VERIFIED", f"p-{G_S.lower()}": "NO_CHANGES"}
    assert cov.page_states([G_A, G_S]) == {
        G_A: {
            "state": "VERIFIED",
            "basis": "MARK",
            "at": cov.page_states([G_A])[G_A]["at"],
            "capture_id": "cap-2",
            "reason": "verify_capture",
        },
        G_S: {
            "state": "NO_CHANGES",
            "basis": "MARK",
            "at": cov.page_states([G_S])[G_S]["at"],
            "capture_id": "cap-2",
            "reason": "verify_capture",
        },
    }
    assert Document.objects.count() == 3  # nothing imported by a check


def test_apply_capture_refuses_a_plan_other_than_the_approved_one(world):
    root = _second(world)
    before = Document.objects.count()
    with pytest.raises(oc.CaptureError, match="not the approved one"):
        oc.apply_capture(root, approved_plan_sha256="0" * 64, actor=None, backup_set="sets/x")
    assert Document.objects.count() == before


def test_apply_capture_keeps_every_gate(world, settings):
    root = _second(world)
    sha = oc.verify_capture(root).plan_sha256
    with pytest.raises(oc.CaptureError, match="Name the verified backup set"):
        oc.apply_capture(root, approved_plan_sha256=sha, actor=None, backup_set="")
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = False
    with pytest.raises(oc.CaptureError, match="The OneNote capture import is off here"):
        oc.apply_capture(root, approved_plan_sha256=sha, actor=None, backup_set="sets/x")


def test_apply_capture_imports_the_approved_plan_once(world, monkeypatch):
    root = _second(world)
    sha = oc.verify_capture(root).plan_sha256
    monkeypatch.setenv("JURISTID_ONENOTE_BACKUP_SET", "sets/runner")
    report = oc.apply_capture(root, approved_plan_sha256=sha, actor=None)
    assert report.documents_created == 1
    states = cov.page_states([G_A, G_S])
    assert states[G_A]["state"] == "IMPORTED" and states[G_S]["state"] == "NO_CHANGES"
    again = oc.apply_capture(root, approved_plan_sha256=sha, actor=None)
    assert again.batch_id == "" and again.documents_created == 0


# -- the command -----------------------------------------------------------------------


def test_the_coverage_command_prints_counts_and_mark_needs_a_reason(world):
    sink = _Sink()
    call_command("onenote_capture_import", "coverage", stdout=sink)
    printed = json.loads(sink.text)
    assert printed["archive_complete"] is False and "sections" not in printed
    with pytest.raises(CommandError, match="give --page"):
        call_command("onenote_capture_import", "mark", "--page", G_A, "--state", "FAILED")
    call_command(
        "onenote_capture_import",
        "mark",
        "--page",
        G_A,
        "--state",
        "FAILED",
        "--reason",
        "x",
        stdout=_Sink(),
    )
    assert cov.page_states([G_A])[G_A]["state"] == PageState.FAILED
    assert MatterSourcePage.objects.count() == 3


def test_the_read_only_baseline_script_agrees_with_the_summary(world, capsys):
    """The script the lead runs on production before 0025 (counts from records only)."""
    import runpy
    from pathlib import Path

    from django.conf import settings

    runpy.run_path(str(Path(settings.BASE_DIR) / "tools/onenote_capture/coverage_baseline.py"))
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    op = cov.coverage_summary()["operational"]
    for key in (
        "teemad",
        "open_teemad",
        "teemad_with_exact_page",
        "teemad_without_page_link",
        "teemad_with_ambiguous_link",
        "teemad_with_captured_page",
        "teemad_without_capture",
        "open_teemad_without_capture",
        "pages",
        "shared_pages",
        "pages_captured",
        "pages_not_captured",
        "captured_by",
        "attachments_imported",
        "attachments_held",
        "attachments_failed",
    ):
        assert printed[key] == op[key], key
    assert printed["states"] == {k: v for k, v in op["states"].items() if v}
    assert printed["archive_complete"] is False
