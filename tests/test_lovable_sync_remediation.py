# ruff: noqa: F811 - pytest fixtures imported from the sync suite are used as arguments
"""Lovable sync and Uuendused → Lovable after the 2026-10-28 audit.

B-01 / C-08: a field is «Juristides juba sama» only where the reconciliation
recorded convergence; a re-numbered or missing topic, a run that stopped before
recording, an operator's resolution and a failed write each say what they are.
A failed run's reading is never the next baseline. B-05: a failed read-back
stops the run's writes. C-06: `Kontrolli muudatusi` does not pile up identical
checks. Scenario A (partial failure) and scenario B (a RESTRICTED cohort Teema
and an unauthorised viewer) end to end. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.urls import reverse

from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.legacy_import import lovable_review, lovable_sync
from app.legacy_import.lovable_sync import SyncRefused
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.matters.models import Matter
from app.matters.services import set_matter_title
from tests import factories
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_review import (  # noqa: F401
    _fields,
    _history,
    _night,
    _nights,
    _topic,
    reader,
)
from tests.test_lovable_sync import NIGHT, _matter, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

SCHEDULED = SyncTrigger.SCHEDULED
SAME = lovable_review.SAME


def _results(history: lovable_review.History) -> list[str]:
    return [f.result for n in history.nights for t in n.topics for f in t.fields]


def _break_after_the_snapshot(monkeypatch: Any) -> None:
    """The run stops after it stored its reading and before it recorded anything."""

    def boom() -> Any:
        raise RuntimeError("synthetic crash after the snapshot")

    monkeypatch.setattr(lovable_sync, "RunCounts", boom)


# ---------------------------------------------------------------------------
# B-01: never «sama» without evidence
# ---------------------------------------------------------------------------


def test_a_renumbered_topic_with_changed_fields_is_not_compared(cohort, reader):
    _nights(
        cohort,
        {},
        {"2026_501": {"nr": "2026_519", "teema": "Pakendiseaduse muutmine", "hetkeseis": "Jõus"}},
    )
    night = _history(reader).nights[0]
    topic = _topic(night, "2026_501")
    assert topic.renumbered == ("2026_501", "2026_519")
    fields = _fields(topic)
    assert set(fields) == {"title", "stage"}
    for change in fields.values():
        assert (change.result, change.result_label) == ("NOT_COMPARED", "Ei võrreldud")
        assert change.note == lovable_review.NOT_COMPARED_NOTES["IDENTIFIER_CHANGED"]
    assert _matter("2026_501").title != "Pakendiseaduse muutmine"


def test_a_topic_missing_from_the_reading_is_never_same(cohort, reader):
    _nights(cohort, {})
    gone = next(i for i, r in cohort["source"].rows.items() if r["nr"] == "2026_503")
    row = cohort["source"].rows.pop(gone)
    _nights(cohort, {}, start=1)
    row["hetkeseis"] = "Jõus"
    cohort["source"].rows[gone] = row
    _nights(cohort, {}, start=2)
    nights = _history(reader).nights
    assert _topic(nights[1], "2026_503").kind == "MISSING"
    assert _topic(nights[0], "2026_503").kind == "BACK"
    assert SAME not in _results(_history(reader))
    star = LovableSyncDecision.objects.get(
        run=nights[1].run, state__reference="2026_503", field="*"
    )
    assert star.outcome == SyncOutcome.SOURCE_MISSING


def test_the_read_model_never_says_same_for_a_missing_topic_decision(cohort):
    run = LovableSyncRun(status=SyncRunStatus.SUCCEEDED, mode=SyncMode.APPLY, counts={"x": 1})
    star = LovableSyncDecision(field="*", outcome=SyncOutcome.SOURCE_MISSING)
    result, note = lovable_review._result(
        "title",
        "x",
        decision=None,
        topic=star,
        applied_in=None,
        converged_in=None,
        resolved_at=None,
        night=run,
        reconciled=True,
    )
    assert result == "NOT_COMPARED" and note == lovable_review.NOT_COMPARED_NOTES["SOURCE_MISSING"]


def test_a_night_that_stopped_after_its_snapshot_compares_nothing(cohort, reader, monkeypatch):
    _nights(cohort, {})
    with monkeypatch.context() as patch:
        _break_after_the_snapshot(patch)
        (failed,) = _nights(
            cohort, {"2026_501": {"teema": "Uus 501", "hetkeseis": "Valitsuses"}}, start=1
        )
    assert failed.status == SyncRunStatus.FAILED and failed.counts == {}
    assert LovableSyncSnapshot.objects.filter(run=failed).exists()
    # The title was written before the crash: that write and its record stand.
    assert set(failed.decisions.values_list("outcome", flat=True)) == {SyncOutcome.APPLIED}

    night = _history(reader).nights[0]
    assert night.run == failed and night.warning == lovable_review.NOT_RECONCILED
    fields = _fields(_topic(night, "2026_501"))
    assert fields["title"].result == "APPLIED"
    assert fields["stage"].result == "NOT_COMPARED"
    assert fields["stage"].note == "Öö katkes enne Juristidega võrdlemist (INTERNAL)."


def test_an_already_recorded_failed_night_is_read_safely(cohort, reader):
    """A run recorded before this fix: it stored its snapshot and then failed.
    The read model does not rewrite it; it just never calls its fields «sama»."""
    _nights(cohort, {})
    (later,) = _nights(cohort, {"2026_501": {"hetkeseis": "Valitsuses"}}, start=1)
    LovableSyncDecision.objects.filter(run=later).delete()
    LovableSyncRun.objects.filter(pk=later.pk).update(
        status=SyncRunStatus.FAILED, counts={}, error="COHORT_MISSING: 9 missing"
    )
    stage = _fields(_topic(_history(reader).nights[0], "2026_501"))["stage"]
    assert stage.result == "NOT_COMPARED" and "(COHORT_MISSING)" in stage.note


def test_a_failed_reading_is_never_the_next_baseline(cohort, reader, monkeypatch):
    first = _nights(cohort, {})[0]
    with monkeypatch.context() as patch:
        _break_after_the_snapshot(patch)
        _nights(cohort, {"2026_501": {"hetkeseis": "Valitsuses"}}, start=1)
    (third,) = _nights(cohort, {}, start=2)
    newest = _history(reader).nights[0]
    assert newest.run == third
    # Measured against the last good night, so the change is still seen and held.
    assert newest.baseline_local == lovable_review._local(first.started_at)
    stage = _fields(_topic(newest, "2026_501"))["stage"]
    assert (stage.result, stage.after) == ("REVIEW", "Valitsuses")
    # Kontrolli muudatusi compares with the good night's reading, never the failed one.
    check = lovable_sync.run_sync(
        trigger=SyncTrigger.CHECK, now=lambda: _night(2) + dt.timedelta(hours=9)
    )
    assert check.snapshot_sha256 == LovableSyncSnapshot.objects.get(run=third).sha256
    assert lovable_sync.comparable_snapshots().first().run == third


def test_too_many_missing_rows_store_no_reading(cohort, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 0)
    del cohort["source"].rows[cohort["topics"][0]["source"]["id"]]
    run = _run(cohort, SCHEDULED)
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("COHORT_MISSING")
    assert not LovableSyncSnapshot.objects.filter(run=run).exists()


def test_a_genuinely_converged_field_is_same(cohort, reader):
    _nights(cohort, {})
    set_matter_title(matter=_matter("2026_501"), value="Sama uus pealkiri", actor=None)
    _nights(cohort, {"2026_501": {"teema": "Sama uus pealkiri"}}, start=1)
    title = _fields(_topic(_history(reader).nights[0], "2026_501"))["title"]
    assert (title.result, title.note) == (SAME, "")


def test_a_manual_runs_convergence_between_nights_is_named(cohort, reader):
    _nights(cohort, {})
    set_matter_title(matter=_matter("2026_501"), value="Sama uus pealkiri", actor=None)
    cohort["source"].edit("2026_501", teema="Sama uus pealkiri")
    manual = _run(cohort, SyncTrigger.MANUAL, at=_night(0) + dt.timedelta(hours=8))
    _nights(cohort, {}, start=1)
    title = _fields(_topic(_history(reader).nights[0], "2026_501"))["title"]
    assert title.result == SAME
    assert title.note == f"Käsitsi käivitusel {lovable_review._local(manual.started_at)}."


def test_no_decision_and_no_evidence_is_unknown_never_same(cohort):
    run = LovableSyncRun(status=SyncRunStatus.SUCCEEDED, mode=SyncMode.APPLY, counts={"x": 1})
    result, note = lovable_review._result(
        "stage",
        "x",
        decision=None,
        topic=None,
        applied_in=None,
        converged_in=None,
        resolved_at=None,
        night=run,
        reconciled=True,
    )
    assert result == "UNKNOWN" and note == lovable_review.UNEXPLAINED_NOTE


# ---------------------------------------------------------------------------
# C-08: every outcome stays itself, in the read model and on the page
# ---------------------------------------------------------------------------


def test_every_outcome_is_distinguishable_on_the_page(client, cohort, reader, monkeypatch):
    _nights(cohort, {})
    set_matter_title(matter=_matter("2026_502"), value="Sama 502", actor=None)

    real_apply = lovable_sync.apply_matter

    def apply_or_crash(plan: Any, **kw: Any) -> Any:
        if plan.state.reference == "2026_505":
            raise RuntimeError("synthetic write failure")
        return real_apply(plan, **kw)

    monkeypatch.setattr(lovable_sync, "apply_matter", apply_or_crash)
    _nights(
        cohort,
        {
            "2026_501": {"teema": "Uus 501", "hetkeseis": "Valitsuses", "kellele": "Riigikogu"},
            "2026_502": {"teema": "Sama 502"},
            "2026_503": {"nr": "2026_518", "teema": "Ümber"},
            "2026_505": {"tegevuse_tahtaeg": "2026-11-27"},
        },
        start=1,
    )
    lovable_sync.resolve_field(reference="2026_501", field_name="addressee", note="kontrollitud")

    night = _history(reader).nights[0]
    seen = {
        (t.reference, f.name): (f.result, f.result_label) for t in night.topics for f in t.fields
    }
    assert seen[("2026_501", "title")] == ("APPLIED", "Uuendatud")
    assert seen[("2026_502", "title")] == ("SAME", "Juristides juba sama")
    assert seen[("2026_501", "stage")] == ("REVIEW", "Vajab ülevaatamist")
    assert seen[("2026_501", "addressee")] == ("RESOLVED", "Lahendatud käsitsi")
    assert seen[("2026_503", "title")] == ("NOT_COMPARED", "Ei võrreldud")
    assert seen[("2026_505", "next_action_date")] == ("FAILED", "Viga")
    assert len({label for _r, label in seen.values()}) == len(set(seen.values()))

    resolved = _fields(_topic(night, "2026_501"))["addressee"]
    assert resolved.note.startswith("Lahendati ") and "kontrollitud" not in resolved.note
    assert _fields(_topic(night, "2026_505"))["next_action_date"].note == (
        lovable_review.WRITE_FAILED_NOTE
    )

    client.force_login(reader)
    body = client.get(reverse("core:release_notes_lovable")).content.decode()
    for label in (
        "Uuendatud",
        "Juristides juba sama",
        "Vajab ülevaatamist",
        "Lahendatud käsitsi",
        "Ei võrreldud",
        "Viga",
    ):
        assert label in body, label
    assert body.count("Juristides juba sama") == 1


# ---------------------------------------------------------------------------
# B-05 + scenario A: a failed read-back stops the run
# ---------------------------------------------------------------------------


def test_a_failed_read_back_stops_every_later_write(cohort, reader, monkeypatch):
    first = _nights(cohort, {})[0]
    calls: list[str] = []

    def verify_fails_first(plan: Any) -> bool:
        calls.append(plan.state.reference)
        return False

    monkeypatch.setattr(lovable_sync, "verify_matter", verify_fails_first)
    before_505 = (_matter("2026_505").title, _writes_for("2026_505"))
    (night,) = _nights(
        cohort,
        {
            "2026_501": {"teema": "Näidisteema 2026_501 uus"},
            "2026_505": {"teema": "Näidisteema 2026_505 uus", "tegevuse_tahtaeg": "2026-11-27"},
        },
        start=1,
    )
    night.refresh_from_db()
    # The first eligible Teema was written and is kept, with its provenance.
    assert calls == ["2026_501"]
    assert _matter("2026_501").title == "Näidisteema 2026_501 uus"
    event = ChangeEvent.objects.get(matter=_matter("2026_501"), event_type="MATTER_TITLE_CHANGED")
    assert event.payload["sync_run"] == str(night.pk)
    # Nothing after it was written.
    assert (_matter("2026_505").title, _writes_for("2026_505")) == before_505
    stopped = {d.field: d for d in night.decisions.filter(state__reference="2026_505")}
    assert {stopped["title"].reason, stopped["next_action_date"].reason} == {
        lovable_sync.VERIFY_STOPPED
    }
    assert {stopped["title"].outcome, stopped["next_action_date"].outcome} == {SyncOutcome.ELIGIBLE}
    star = night.decisions.get(state__reference="2026_501", field="*")
    assert (star.outcome, star.reason) == (SyncOutcome.FAILED, lovable_sync.VERIFY_MISMATCH)
    # An honest failed run.
    assert night.status == SyncRunStatus.FAILED and not night.verified
    assert night.error.startswith("VERIFY:") and "1 Teema(d) not written" in night.error
    assert (night.counts["verify_failed"], night.counts["not_executed"]) == (1, 1)

    # The page: nothing is «sama»; the written Teema is an error, the rest not applied.
    shown = _history(reader).nights[0]
    assert shown.warning == lovable_review.VERIFY_STOPPED_NIGHT
    assert SAME not in _results(_history(reader))
    title_501 = _fields(_topic(shown, "2026_501"))["title"]
    assert (title_501.result, title_501.note) == ("FAILED", lovable_review.VERIFY_FAILED_NOTE)
    for change in _fields(_topic(shown, "2026_505")).values():
        assert (change.result, change.note) == ("NOT_APPLIED", lovable_review.VERIFY_STOPPED_NOTE)

    # The next night is measured against the last good reading, and writes what was held.
    monkeypatch.setattr(lovable_sync, "verify_matter", lambda plan: True)
    (next_night,) = _nights(cohort, {}, start=2)
    assert next_night.status == SyncRunStatus.SUCCEEDED, next_night.error
    newest = _history(reader).nights[0]
    assert newest.baseline_local == lovable_review._local(first.started_at)
    assert _matter("2026_505").title == "Näidisteema 2026_505 uus"
    title_505 = _fields(_topic(newest, "2026_505"))["title"]
    assert title_505.result == "APPLIED"
    assert lovable_sync.comparable_snapshots().first().run == next_night


def _writes_for(reference: str) -> int:
    return ChangeEvent.objects.filter(matter=_matter(reference)).count()


# ---------------------------------------------------------------------------
# C-06: Kontrolli muudatusi
# ---------------------------------------------------------------------------


def test_a_second_check_within_a_minute_is_refused(cohort):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    _run(cohort)
    at = NIGHT + dt.timedelta(hours=8)
    lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: at)
    with pytest.raises(SyncRefused) as refused:
        lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: at + dt.timedelta(seconds=30))
    assert refused.value.code == "CHECK_TOO_SOON"
    assert LovableSyncRun.objects.filter(trigger=SyncTrigger.CHECK).count() == 1


def test_an_identical_check_reuses_the_last_and_records_nothing_new(cohort):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    cohort["source"].edit("2026_501", hetkeseis="Valitsuses")
    _run(cohort)
    at = NIGHT + dt.timedelta(hours=8)
    first = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: at)
    rows = LovableSyncDecision.objects.count()
    again = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: at + dt.timedelta(hours=1))
    assert again == first
    assert LovableSyncDecision.objects.count() == rows
    assert LovableSyncRun.objects.filter(trigger=SyncTrigger.CHECK).count() == 1
    # Something changed in Juristid: the next check is a new record, and the old stays.
    set_matter_title(matter=_matter("2026_502"), value="Juristide oma 502", actor=None)
    changed = lovable_sync.run_sync(
        trigger=SyncTrigger.CHECK, now=lambda: at + dt.timedelta(hours=2)
    )
    assert changed != first
    assert LovableSyncRun.objects.filter(trigger=SyncTrigger.CHECK).count() == 2
    assert LovableSyncDecision.objects.filter(run=first).count() > 0


def test_the_haldus_button_twice_records_one_check(client, cohort, settings):
    from tests.admin_session import apply_admin_gate, technical_admin, verified

    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    _run(cohort)
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    assert client.post(reverse("haldus:lovable_check")).status_code == 302
    assert client.post(reverse("haldus:lovable_check")).status_code == 302
    assert LovableSyncRun.objects.filter(trigger=SyncTrigger.CHECK).count() == 1


# ---------------------------------------------------------------------------
# Scenario B: a RESTRICTED cohort Teema and a viewer with no business read
# ---------------------------------------------------------------------------


SECRET_TITLE = "PIIRATUD-PEALKIRI-5521"  # noqa: S105 - a marker, not a password
SECRET_STAGE = "PIIRATUD-ETAPP-5522"  # noqa: S105 - a marker, not a password


@pytest.fixture
def restricted_night(cohort):
    Matter.all_objects.filter(pk=_matter("2026_501").pk).update(visibility=Visibility.RESTRICTED)
    _nights(cohort, {}, {"2026_501": {"teema": SECRET_TITLE, "hetkeseis": SECRET_STAGE}})
    return _matter("2026_501")


def test_a_business_reader_reads_the_restricted_teemas_changes(client, restricted_night, settings):
    """ADR 0181: READER and the other business roles read RESTRICTED content."""
    settings.REAL_DATA_ALLOWED = False
    reader_role = factories.ReaderFactory()
    night = lovable_review.nightly_history(reader_role, now=_night(10)).nights[0]
    assert _fields(_topic(night, "2026_501"))["stage"].after == SECRET_STAGE
    client.force_login(reader_role)
    assert SECRET_STAGE in client.get(reverse("core:release_notes_lovable")).content.decode()


@pytest.mark.parametrize("who", ["nobody", "no_role", "technical_admin"])
def test_an_unauthorised_viewer_learns_nothing_of_the_restricted_teema(restricted_night, who):
    viewer = {
        "nobody": lambda: None,
        "no_role": lambda: factories.UserFactory(role=""),
        "technical_admin": factories.AdministratorFactory,
    }[who]()
    history = lovable_review.nightly_history(viewer, now=_night(10))
    for night in history.nights:
        assert night.topics == []
        assert (night.changed_topics, night.changed_fields, night.applied, night.attention) == (
            0,
            0,
            0,
            0,
        )
    text = repr(history)
    assert SECRET_TITLE not in text and SECRET_STAGE not in text
    assert restricted_night.title not in text


def test_the_pages_refuse_the_unauthorised_viewer(client, restricted_night, settings):
    settings.REAL_DATA_ALLOWED = False
    client.force_login(factories.AdministratorFactory())
    response = client.get(reverse("core:release_notes_lovable"))
    body = response.content.decode()
    assert response.status_code == 403
    assert SECRET_TITLE not in body and SECRET_STAGE not in body
    assert "muudetud" not in body  # no per-night counts either


def test_haldus_counts_but_never_names_the_restricted_change(client, restricted_night, settings):
    from tests.admin_session import apply_admin_gate, technical_admin, verified

    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    body = client.get(reverse("haldus:lovable")).content.decode()
    assert SECRET_TITLE not in body and SECRET_STAGE not in body
    assert reverse("matters:matter_detail", args=[restricted_night.pk]) not in body


def test_haldus_names_a_stopped_change_in_words_not_codes(client, cohort, settings, monkeypatch):
    from tests.admin_session import apply_admin_gate, choose, technical_admin, verified

    monkeypatch.setattr(lovable_sync, "verify_matter", lambda plan: False)
    cohort["source"].edit("2026_501", teema="Näidisteema 2026_501 uus")
    cohort["source"].edit("2026_505", teema="Näidisteema 2026_505 uus")
    _run(cohort, SCHEDULED)
    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    choose(client, cohort["world"]["mari"])
    body = client.get(reverse("haldus:lovable")).content.decode()
    assert "Jäi kirjutamata: varasema teema tagasilugemine ebaõnnestus" in body
    assert "Kirjutati, kuid tagasilugemine ei vastanud kirjutatule." in body
    assert "VERIFY_STOPPED" not in body and "VERIFY_MISMATCH" not in body
