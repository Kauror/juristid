# ruff: noqa: F811 - pytest fixtures imported from the sync suite are used as arguments
"""Uuendused → Lovable: what changed in Lovable night by night (docs/adr/0192 §11).

The nights are produced by the real synchronisation (`run_sync`) over the
synthetic pilot cohort of tests/test_lovable_sync.py, so the page reads exactly
the runs, snapshots and decisions production keeps. Every title, name and link
is invented.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.urls import reverse

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_review, lovable_sync
from app.legacy_import.lovable_source import SourceError
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncTrigger,
)
from app.matters.models import Matter
from app.matters.services import set_matter_title
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, FakeLovable, _matter, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

SCHEDULED = SyncTrigger.SCHEDULED
DAY = dt.timedelta(days=1)
LATER = NIGHT + 40 * DAY  # well after every night, inside nothing in particular


def _night(n: int) -> dt.datetime:
    return NIGHT + n * DAY


def _nights(cohort: Any, *edits: dict[str, dict[str, Any]], start: int = 0) -> list[Any]:
    """One scheduled run per element of ``edits``: each a {reference: values} to
    edit in Lovable before that night's run."""
    runs = []
    for offset, night_edits in enumerate(edits):
        for reference, values in night_edits.items():
            cohort["source"].edit(reference, **values)
        runs.append(_run(cohort, SCHEDULED, at=_night(start + offset)))
    return runs


def _history(user: Any, **kw: Any) -> lovable_review.History:
    return lovable_review.nightly_history(user, now=kw.pop("now", _night(10)), **kw)


def _by_night(history: lovable_review.History) -> dict[str, lovable_review.Night]:
    return {n.run.run_key: n for n in history.nights}


def _topic(night: lovable_review.Night, reference: str) -> lovable_review.TopicChange:
    return next(t for t in night.topics if t.reference == reference)


def _fields(topic: lovable_review.TopicChange) -> dict[str, lovable_review.FieldChange]:
    return {f.name: f for f in topic.fields}


@pytest.fixture
def reader(cohort, settings):
    # The cohort was applied with the pilot's real-data switch on; a synthetic
    # persona signs in only with it off, and the page needs none of it.
    settings.REAL_DATA_ALLOWED = False
    return cohort["world"]["mari"]


# ---------------------------------------------------------------------------
# What changed in Lovable
# ---------------------------------------------------------------------------


def test_every_compared_field_has_a_place_and_an_estonian_label():
    from app.legacy_import.lovable_sync_labels import LOVABLE_FIELD_LABELS

    assert set(lovable_review.FIELD_ORDER) == set(lovable_sync.SOURCE_FIELDS)
    assert all(LOVABLE_FIELD_LABELS.get(name) for name in lovable_review.FIELD_ORDER)


def test_the_first_night_is_a_starting_point_not_a_hundred_changes(cohort, reader):
    _nights(cohort, {})
    (night,) = _history(reader).nights
    assert night.comparison == "BASELINE"
    assert night.topics == [] and night.message == lovable_review.FIRST_READING


def test_a_night_with_no_change_says_so(cohort, reader):
    _nights(cohort, {}, {})
    newest = _history(reader).nights[0]
    assert newest.comparison == "COMPARED"
    assert newest.topics == [] and newest.message == "Lovable'is ei tuvastatud muudatusi."


def test_one_field_shows_its_old_and_new_value_and_that_it_was_applied(cohort, reader):
    _nights(cohort, {}, {"2026_501": {"teema": "Näidisteema 2026_501 täpsustatud"}})
    night = _history(reader).nights[0]
    topic = _topic(night, "2026_501")
    title = _fields(topic)["title"]
    assert (title.label, title.before, title.after) == (
        "Pealkiri",
        "Näidisteema 2026_501",
        "Näidisteema 2026_501 täpsustatud",
    )
    assert (title.result, title.result_label) == ("APPLIED", "Uuendatud")
    assert topic.matter_id == _matter("2026_501").pk
    assert (night.changed_topics, night.changed_fields, night.applied) == (1, 1, 1)


def test_several_fields_and_several_teemad_in_one_night(cohort, reader):
    _nights(
        cohort,
        {},
        {
            "2026_501": {"hetkeseis": "Valitsuses", "vastutaja": "Jaan"},
            "2026_505": {"tegevuse_tahtaeg": "2026-11-27"},
        },
    )
    night = _history(reader).nights[0]
    assert [t.reference for t in night.topics] == ["2026_505", "2026_501"]
    first = _fields(_topic(night, "2026_501"))
    assert set(first) == {"stage", "owner"}
    assert (first["stage"].before, first["stage"].after) == ("Riigikogus", "Valitsuses")
    assert first["stage"].result_label == "Vajab ülevaatamist"
    assert (first["owner"].before, first["owner"].after) == ("Mari", "Jaan")
    day = _fields(_topic(night, "2026_505"))["next_action_date"]
    assert (day.label, day.before, day.after) == (
        "Järgmise tegevuse kuupäev",
        "20.11.2026",
        "27.11.2026",
    )
    assert day.result == "APPLIED"
    assert (night.changed_topics, night.changed_fields) == (2, 3)
    assert night.attention == 1


def test_a_held_change_is_new_only_on_the_night_it_changed(cohort, reader):
    _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses"}}, {}, {})
    nights = _history(reader).nights
    assert [n.comparison for n in nights] == ["COMPARED", "COMPARED", "COMPARED", "BASELINE"]
    assert nights[0].topics == [] and nights[1].topics == []
    stage = _fields(_topic(nights[2], "2026_501"))["stage"]
    assert stage.result == "REVIEW"
    # The reconciliation still holds it every night; the page does not repeat it.
    held = LovableSyncDecision.objects.filter(state__reference="2026_501", field="stage")
    assert held.count() == 3


def test_whitespace_and_case_are_not_changes(cohort, reader):
    _nights(
        cohort,
        {},
        {"2026_501": {"teema": "  Näidisteema   2026_501 ", "vastutaja": "MARI"}},
    )
    assert _history(reader).nights[0].topics == []


def test_the_nights_are_newest_first_and_seven_by_default(cohort, reader):
    runs = _nights(cohort, *[{} for _ in range(9)])
    history = _history(reader, now=_night(12))
    assert [n.run for n in history.nights] == list(reversed(runs))[:7]
    assert history.older == 2 and not history.showing_all
    everything = _history(reader, now=_night(12), show_all=True)
    assert len(everything.nights) == 9 and everything.older == 0


def test_nights_beyond_the_retention_are_not_offered(cohort, reader):
    _nights(cohort, {}, {})
    history = _history(reader, now=_night(1) + 31 * DAY, show_all=True)
    assert history.nights == []


# ---------------------------------------------------------------------------
# What Juristid did with it
# ---------------------------------------------------------------------------


def test_compare_mode_shows_an_eligible_change_as_not_applied(cohort, reader):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="t")
    _nights(cohort, {}, {"2026_505": {"tegevuse_tahtaeg": "2026-11-27"}})
    night = _history(reader).nights[0]
    day = _fields(_topic(night, "2026_505"))["next_action_date"]
    assert (day.result, day.result_label) == ("NOT_APPLIED", "Rakendamata")
    assert day.note == lovable_review.COMPARE_ONLY_NOTE
    assert night.mode_note == lovable_review.COMPARE_ONLY_NOTE and night.applied == 0


def test_a_local_edit_and_a_source_change_are_a_conflict(cohort, reader):
    _nights(cohort, {})
    set_matter_title(matter=_matter("2026_501"), value="Juristide oma pealkiri", actor=None)
    _nights(cohort, {"2026_501": {"teema": "Lovable'i uus pealkiri"}}, start=1)
    title = _fields(_topic(_history(reader).nights[0], "2026_501"))["title"]
    assert (title.result, title.result_label) == ("CONFLICT", "Vastuolu")
    assert _matter("2026_501").title == "Juristide oma pealkiri"


def test_a_local_edit_alone_is_not_a_lovable_change(cohort, reader):
    _nights(cohort, {})
    set_matter_title(matter=_matter("2026_501"), value="Juristide oma pealkiri", actor=None)
    _nights(cohort, {}, start=1)
    assert _history(reader).nights[0].topics == []


def test_an_already_matching_value_is_alignment_not_a_write(cohort, reader):
    _nights(cohort, {})
    set_matter_title(matter=_matter("2026_501"), value="Sama uus pealkiri", actor=None)
    before = ChangeEvent.objects.count()
    _nights(cohort, {"2026_501": {"teema": "Sama uus pealkiri"}}, start=1)
    assert ChangeEvent.objects.count() == before
    title = _fields(_topic(_history(reader).nights[0], "2026_501"))["title"]
    assert (title.result, title.result_label) == ("SAME", "Juristides juba sama")


def test_a_failed_night_says_it_read_nothing_and_the_next_compares_with_the_last_reading(
    cohort, reader
):
    _nights(cohort, {})
    cohort["source"].fail = SourceError("UNREACHABLE", "x")
    _nights(cohort, {}, start=1)
    cohort["source"].fail = None
    _nights(cohort, {"2026_501": {"hetkeseis": "Valitsuses"}}, start=2)
    newest, failed, first = _history(reader).nights
    assert failed.failed and failed.comparison == "NO_SNAPSHOT"
    assert failed.message == lovable_review.NOT_READ and failed.topics == []
    assert newest.comparison == "COMPARED"
    assert newest.baseline_local == lovable_review._local(first.run.started_at)
    assert _fields(_topic(newest, "2026_501"))["stage"].after == "Valitsuses"


def test_manual_and_check_runs_are_not_nights_but_a_manual_write_is_named(cohort, reader):
    _nights(cohort, {})
    cohort["source"].edit("2026_501", teema="Käsitsi rakendatud pealkiri")
    manual = _run(cohort, SyncTrigger.MANUAL, at=_night(0) + dt.timedelta(hours=8))
    lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: _night(0) + dt.timedelta(hours=9))
    _nights(cohort, {}, start=1)
    nights = _history(reader).nights
    assert [n.run.trigger for n in nights] == [SCHEDULED, SCHEDULED]
    title = _fields(_topic(nights[0], "2026_501"))["title"]
    assert title.result == "APPLIED"
    assert lovable_review._local(manual.started_at) in title.note


def test_a_topic_gone_from_lovable_is_an_event_not_an_edit(cohort, reader):
    _nights(cohort, {})
    gone = next(i for i, r in cohort["source"].rows.items() if r["nr"] == "2026_503")
    del cohort["source"].rows[gone]
    _nights(cohort, {}, start=1)
    topic = _topic(_history(reader).nights[0], "2026_503")
    assert topic.kind == "MISSING" and topic.fields == []
    assert topic.matter_id == _matter("2026_503").pk


def test_a_renumbered_topic_links_to_its_own_teema(cohort, reader):
    _nights(cohort, {}, {"2026_501": {"nr": "2026_519"}})
    topic = _topic(_history(reader).nights[0], "2026_501")
    assert topic.renumbered == ("2026_501", "2026_519")
    assert topic.matter_id == _matter("2026_501").pk


def test_a_changed_commencement_is_compared_by_content(cohort, reader):
    source = cohort["source"]
    topic_id = next(i for i, r in source.rows.items() if r["nr"] == "2026_501")
    row = {"topic_id": topic_id, "liik": "Eeldatav", "kuupaev": "2027-01-01", "tapsustus": ""}
    source.commencements.append({**row, "id": "c1", "position": 1})
    _nights(cohort, {})
    source.commencements[0]["kuupaev"] = "2027-07-01"
    _nights(cohort, {}, start=1)
    change = _fields(_topic(_history(reader).nights[0], "2026_501"))["commencements"]
    assert (change.label, change.before, change.after) == (
        "Jõustumised",
        "Eeldatav 01.01.2027",
        "Eeldatav 01.07.2027",
    )
    assert change.result == "REVIEW"


def test_an_expired_reading_is_said_not_silently_skipped(cohort, reader):
    runs = _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses"}})
    LovableSyncSnapshot.objects.filter(run=runs[0]).delete()
    LovableSyncRun.objects.filter(pk=runs[0].pk).update(started_at=_night(-40))
    newest = _history(reader).nights[0]
    assert newest.comparison == "UNAVAILABLE" and newest.message == lovable_review.EXPIRED
    assert newest.topics == []


def test_a_snapshot_that_does_not_verify_is_not_compared(cohort, reader):
    runs = _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses"}})
    snapshot = LovableSyncSnapshot.objects.get(run=runs[1])
    snapshot.document = {**snapshot.document, "taken_at": "2000-01-01T00:00:00+00:00"}
    snapshot.save()
    newest = _history(reader).nights[0]
    assert newest.comparison == "NO_SNAPSHOT" and newest.message == lovable_review.NOT_VERIFIABLE


# ---------------------------------------------------------------------------
# Who may read it
# ---------------------------------------------------------------------------


def test_nobody_without_a_persona_learns_a_teema(cohort):
    _nights(cohort, {}, {"2026_501": {"teema": "SALAJANE-PEALKIRI-4411"}})
    night = lovable_review.nightly_history(None, now=_night(10)).nights[0]
    assert night.topics == [] and night.hidden == 1


def test_a_deleted_teema_is_left_out_entirely(cohort, reader):
    _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses"}})
    Matter.all_objects.filter(pk=_matter("2026_501").pk).update(deleted_at=_night(5))
    night = _history(reader).nights[0]
    assert night.topics == [] and night.hidden == 0


def test_the_page_answers_a_business_reader(client, cohort, reader):
    _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses"}})
    client.force_login(reader)
    response = client.get(reverse("core:release_notes_lovable"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Valitsuses" in body and "Riigikogus" in body and "Hetkeseis" in body
    assert reverse("matters:matter_detail", args=[_matter("2026_501").pk]) in body
    assert 'aria-current="page">Lovable<' in body


def test_anonymous_is_sent_to_sign_in(client, settings):
    settings.AUTH_MODE = "none"
    response = client.get(reverse("core:release_notes_lovable"))
    assert response.status_code == 302


def test_the_shared_password_alone_shows_no_change(client, settings, cohort):
    _nights(cohort, {}, {"2026_501": {"teema": "SALAJANE-PEALKIRI-4411"}})
    settings.REAL_DATA_ALLOWED = False
    apply_shared_gate(settings)
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    response = client.get(reverse("core:release_notes_lovable"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:choose_persona"))
    assert "SALAJANE" not in response.content.decode()
    # The Juristid section stays open to the same session.
    assert client.get(reverse("core:release_notes")).status_code == 200


def test_a_technical_administrator_without_a_business_role_is_refused(client, cohort, settings):
    _nights(cohort, {}, {"2026_501": {"teema": "SALAJANE-PEALKIRI-4411"}})
    settings.REAL_DATA_ALLOWED = False
    client.force_login(factories.AdministratorFactory())
    response = client.get(reverse("core:release_notes_lovable"))
    assert response.status_code == 403
    assert "SALAJANE" not in response.content.decode()


def test_the_page_takes_no_post(client, cohort, reader):
    client.force_login(reader)
    assert client.post(reverse("core:release_notes_lovable")).status_code == 405


def test_source_text_is_escaped(client, cohort, reader):
    _nights(cohort, {}, {"2026_501": {"hetkeseis": "<script>alert(1)</script>"}})
    client.force_login(reader)
    body = client.get(reverse("core:release_notes_lovable")).content.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_an_excluded_source_field_never_reaches_the_page(client, cohort, reader):
    _nights(cohort, {}, {"2026_501": {"markmed": "SISEMINE-MARGE-7781", "hetkeseis": "Valitsuses"}})
    client.force_login(reader)
    body = client.get(reverse("core:release_notes_lovable")).content.decode()
    assert "SISEMINE-MARGE-7781" not in body and "Valitsuses" in body


def test_the_page_shows_no_identifiers_or_raw_structures(client, cohort, reader):
    _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses"}})
    client.force_login(reader)
    body = client.get(reverse("core:release_notes_lovable")).content.decode()
    state = LovableSyncState.objects.get(reference="2026_501")
    assert str(state.source_id) not in body
    assert '"source"' not in body and "REVIEW_REQUIRED" not in body


def test_viewing_writes_nothing(client, cohort, reader, monkeypatch):
    _nights(cohort, {}, {"2026_501": {"hetkeseis": "Valitsuses", "teema": "Uus pealkiri"}})
    client.force_login(reader)

    def fingerprint() -> tuple:
        return (
            ChangeEvent.objects.count(),
            LovableSyncRun.objects.count(),
            LovableSyncDecision.objects.count(),
            LovableSyncSnapshot.objects.count(),
            list(LovableSyncState.objects.order_by("pk").values_list("applied", "observed")),
            list(Matter.all_objects.order_by("pk").values_list("pk", "title", "updated_at")),
        )

    def unreachable(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the page must not reach Lovable")

    before = fingerprint()
    monkeypatch.setattr(FakeLovable, "select", unreachable)
    assert client.get(reverse("core:release_notes_lovable")).status_code == 200
    assert client.get(reverse("core:release_notes_lovable") + "?koik=1").status_code == 200
    assert fingerprint() == before


def test_no_nights_yet_says_so(client, cohort, reader):
    client.force_login(reader)
    body = client.get(reverse("core:release_notes_lovable")).content.decode()
    assert "Öiseid sünkroonimisi ei ole veel olnud." in body


# ---------------------------------------------------------------------------
# The Juristid section beside it
# ---------------------------------------------------------------------------


def test_the_juristid_section_is_unchanged_and_the_default(client, settings):
    settings.AUTH_MODE = "none"
    body = client.get(reverse("core:release_notes")).content.decode()
    assert 'aria-current="page">Juristid<' in body
    assert reverse("core:release_notes_lovable") in body
    assert "relnotes__day" in body
