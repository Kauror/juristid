"""The one read service over Lovable observation and synchronisation (docs/adr/0201).

Synthetic throughout: the pilot cohort, its nights and the wider readings are
built directly (tests/lovable_observability_support.py); nothing reaches
Lovable. The real-world cases are re-told with invented references, titles and
values.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.core.enums import Visibility
from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_observation_models import (
    AuditAccess,
    MappingStatus,
    ObservationRunStatus,
    ObservedChangeKind,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncRun,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
)
from tests import factories
from tests.lovable_observability_support import (
    NIGHT,
    Pilot,
    control,
    narrowed_reader,
    night_at,
    observation_run,
    observed,
    retention_run,
    row_counts,
    source_event,
    write_statements,
)

pytestmark = pytest.mark.django_db

LATER = night_at(3) + dt.timedelta(hours=2)


@pytest.fixture
def reader():
    return factories.UserFactory(display_name="Lugeja Näidis")


@pytest.fixture
def pilot():
    control(SyncMode.COMPARE)
    return Pilot()


@pytest.fixture
def field_names_policy(settings):
    """The owner-approvable alternative to the default (docs/adr/0201 §6)."""
    settings.LOVABLE_UNLINKED_DETAIL = "FIELD_NAMES"
    assert obs.unlinked_detail() == obs.FIELD_NAMES


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _topic(reading: obs.Reading, reference: str) -> obs.TopicDiff:
    return next(t for t in reading.topics if t.reference == reference)


def _fields(topic: obs.TopicDiff) -> dict[str, obs.FieldDiff]:
    return {f.field: f for f in topic.fields}


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


def test_status_on_an_empty_database_writes_nothing_and_says_off(reader):
    before = row_counts()
    with CaptureQueriesContext(connection) as queries:
        status = obs.status(reader, now=NIGHT)
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before
    assert not LovableSyncControl.objects.exists()  # no get_or_create
    assert status.mode == SyncMode.OFF and not status.apply_authorised
    assert status.snapshot_freshness == obs.NONE
    assert status.audit.access == AuditAccess.DISABLED and not status.audit.available
    assert status.audit.message.startswith("Lovable'i muutmisajalugu pole kättesaadav:")


def test_status_after_nights_is_compare_and_fresh(pilot, reader):
    pilot.add(243)
    pilot.night(night_at(0))
    pilot.night(night_at(1))
    before = row_counts()
    with CaptureQueriesContext(connection) as queries:
        status = obs.status(reader, now=night_at(1) + dt.timedelta(hours=1))
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before
    assert status.mode == SyncMode.COMPARE and status.mode_label == "Ainult võrdlus"
    assert status.apply_authorised is False and status.cut_over is False
    assert status.last_scheduled_at == night_at(1) and status.last_success_at == night_at(1)
    assert status.snapshot_freshness == obs.FRESH
    assert status.cohort_size == 1
    assert status.live_topics is None and status.operational_topics is None
    keys = {i.key for i in status.indicators}
    assert {
        "latest_scheduled",
        "latest_success",
        "mode",
        "snapshot_freshness",
        "audit_freshness",
        "pilot_coverage",
        "operational_observed",
        "live_indexed",
        "pilot_topics_changed",
        "observed_topics_changed",
        "source_events",
        "changed_fields",
        "eligible",
        "review_required",
        "conflicts",
        "already_converged",
        "applied",
        "unmatched_operational",
        "new_source_topics",
        "missing",
        "restored",
        "renumbered",
    } <= keys
    for indicator in status.indicators:
        assert indicator.denominator and indicator.window
        if indicator.value is None:
            assert indicator.unavailable_reason
    by_key = {i.key: i for i in status.indicators}
    assert by_key["pilot_coverage"].denominator == "piloodi 100 teemast"
    assert by_key["source_events"].value is None
    assert "muutmisajalugu" in by_key["source_events"].unavailable_reason
    assert by_key["mode"].value == 0
    assert by_key["mode"].display == "ei"  # words on the page, 0/1 in the answer
    # Without a wider reading the pilot night's own topic index still counts the
    # source (bug hunt 2026-11-03: «—» beside «416 operational not in Juristid»).
    night_counts = LovableSyncRun.objects.order_by("-started_at").first().counts
    assert by_key["live_indexed"].value == night_counts["source_live"] > 0
    assert by_key["operational_observed"].value == night_counts["source_operational"]
    assert "piloodi lugemise teemaindeksis" in by_key["operational_observed"].denominator
    # A night re-decides the whole open backlog, and the unit says so.
    assert by_key["review_required"].denominator == obs.PENDING_TALLY_UNIT
    # Pilot nights and wider readings are never one number.
    assert (
        by_key["pilot_topics_changed"].denominator != by_key["observed_topics_changed"].denominator
    )
    json.dumps(status.as_dict())


def test_a_failed_run_and_a_stale_snapshot_are_warned(pilot, reader):
    pilot.add(243)
    pilot.night(night_at(0))
    pilot.night(night_at(1), fail="HTTP_401: the token was refused for user x")
    status = obs.status(reader, now=night_at(2) + dt.timedelta(hours=6))
    assert status.last_failure_code == "HTTP_401"
    assert "the token" not in _dump(status.as_dict())
    assert status.snapshot_freshness == obs.STALE
    text = " ".join(status.warnings)
    assert "ebaõnnestus: HTTP_401" in text
    assert "26 tunni" in text
    assert "Laiem vaatlus" in text  # observe off
    assert "muutmisajalugu pole kättesaadav" in text


def test_status_reads_observation_counts_and_audit_access(pilot, reader):
    control(SyncMode.COMPARE, observe=True)
    first = observation_run(night_at(0))
    observation_run(
        night_at(1),
        baseline=first,
        audit=AuditAccess.UNAVAILABLE,
        audit_error="EMPTY_UNDER_RLS",
        counts={"live": 420, "operational": 151, "detailed": 170},
    )
    status = obs.status(reader, now=night_at(1) + dt.timedelta(hours=1))
    assert (status.live_topics, status.operational_topics) == (420, 151)
    assert status.observation_freshness == obs.FRESH
    assert status.audit.access == AuditAccess.UNAVAILABLE
    assert "EMPTY_UNDER_RLS" in status.audit.message


def test_observe_on_but_never_read_is_warned(pilot, reader):
    control(SyncMode.COMPARE, observe=True)
    status = obs.status(reader, now=NIGHT)
    assert obs.NO_OBSERVATION in status.warnings


# ---------------------------------------------------------------------------
# nights(): pilot nights
# ---------------------------------------------------------------------------


def test_2026_220_wording_and_date_change_together(pilot, reader):
    pilot.add(220)
    pilot.night(night_at(0))
    pilot.edit(
        "2026_220", jargmiseks="Oodata eelnõu teist versiooni", tegevuse_tahtaeg="2026-12-04"
    )
    pilot.night(night_at(1))
    page = obs.nights(reader, now=LATER)
    newest = page.items[0]
    assert newest.kind == obs.PILOT_NIGHT and newest.comparison == "COMPARED"
    topic = _topic(newest, "2026_220")
    assert "Järgmise tegevuse sõnastus ja kuupäev muutusid koos" in topic.summary
    fields = _fields(topic)
    assert fields["next_action_text"].result == "REVIEW"
    # The day is never carried on its own: it is held with the wording.
    assert fields["next_action_date"].result == "REVIEW"
    assert fields["next_action_date"].reason == "Kuupäev muutus koos sõnastusega."
    assert (fields["next_action_date"].before, fields["next_action_date"].after) == (
        "20.11.2026",
        "04.12.2026",
    )
    assert newest.outcomes == {"REVIEW": 2}
    # The first night is a starting point, never a hundred changes.
    oldest = page.items[-1]
    assert oldest.comparison == "BASELINE" and oldest.topics == []


def test_only_the_date_says_the_date(pilot, reader):
    pilot.add(505)
    pilot.night(night_at(0))
    pilot.edit("2026_505", tegevuse_tahtaeg="2026-11-27")
    pilot.night(night_at(1))
    topic = _topic(obs.nights(reader, now=LATER).items[0], "2026_505")
    assert topic.summary == "Järgmise tegevuse kuupäev muutus."
    assert _fields(topic)["next_action_date"].result == "NOT_APPLIED"  # COMPARE only


def test_night_filters_narrow_topics_but_keep_the_reading(pilot, reader):
    pilot.add(501)
    pilot.add(502)
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.edit("2026_502", teema="Uus sõnastus 2026_502")
    pilot.night(night_at(1))
    every = obs.nights(reader, now=LATER).items[0]
    assert {t.reference for t in every.topics} == {"2026_501", "2026_502"}
    by_field = obs.nights(reader, field_name="stage", now=LATER).items[0]
    assert [t.reference for t in by_field.topics] == ["2026_501"]
    assert by_field.changed_topics == 2  # the counts say what the filter left out
    by_outcome = obs.nights(reader, outcome=SyncOutcome.REVIEW_REQUIRED, now=LATER).items[0]
    assert [t.reference for t in by_outcome.topics] == ["2026_501"]
    by_ref = obs.nights(reader, reference="2026/502", now=LATER).items[0]
    assert [t.reference for t in by_ref.topics] == ["2026_502"]
    by_text = obs.nights(reader, text="valitsuses", now=LATER).items[0]
    assert [t.reference for t in by_text.topics] == ["2026_501"]
    window = obs.nights(reader, since=night_at(0).date(), until=night_at(0).date(), now=LATER)
    # 22:15 UTC is the next calendar day in Tallinn.
    assert window.total == 0
    tallinn_day = night_at(1).astimezone(lovable_sync.TALLINN).date()
    one = obs.nights(reader, since=tallinn_day, until=tallinn_day, now=LATER)
    assert one.total == 1


def test_readings_are_paginated(pilot, reader):
    pilot.add(501)
    for n in range(obs.NIGHTS_PAGE + 2):
        pilot.night(night_at(n))
    now = night_at(obs.NIGHTS_PAGE + 2)
    first = obs.nights(reader, now=now)
    assert (first.total, first.pages, len(first.items)) == (obs.NIGHTS_PAGE + 2, 2, obs.NIGHTS_PAGE)
    second = obs.nights(reader, page=2, now=now)
    assert len(second.items) == 2
    assert second.items[-1].taken_at == night_at(0)


def test_a_long_value_is_cut_in_the_list_and_whole_in_the_detail(pilot, reader):
    pilot.add(501)
    pilot.night(night_at(0))
    long_title = "Pikk pealkiri " + "sõna " * 60
    pilot.edit("2026_501", teema=long_title)
    pilot.night(night_at(1))
    title = _fields(_topic(obs.nights(reader, now=LATER).items[0], "2026_501"))["title"]
    assert title.truncated and len(title.after) <= obs.SHORT_VALUE
    detail = obs.matter_changes(reader, "2026_501", now=LATER)
    assert detail is not None
    full = _fields(detail.nights[0])["title"]
    assert not full.truncated and full.after == lovable_sync.text_value(long_title)


def test_a_failed_night_and_a_missing_earlier_baseline(pilot, reader):
    pilot.add(501)
    pilot.night(night_at(0), fail="UNREACHABLE: x")
    pilot.night(night_at(1))
    readings = obs.nights(reader, now=LATER).items
    assert [r.comparison for r in readings] == ["BASELINE", "NO_SNAPSHOT"]
    assert readings[0].baseline_taken_at is None
    assert "lähtepunkt" in readings[0].message


# ---------------------------------------------------------------------------
# nights() / observed_changes(): the wider readings
# ---------------------------------------------------------------------------


def _three_mappings(pilot):
    """One unmatched, one same-number candidate and one pilot topic, changed."""
    pilot.add(501)
    candidate = factories.MatterFactory(
        reference_year=2026, reference_number=777, title="Kandidaat X"
    )
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(
        run,
        "2026_900",
        fields=[("title", "Salajane vana", "Salajane uus")],
    )
    observed(
        run,
        "2026_777",
        mapping=MappingStatus.REFERENCE_CANDIDATE,
        matter=candidate,
        fields=[("next_action_text", "Väärtus A", "Väärtus B")],
    )
    observed(
        run,
        "2026_501",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_501"),
        source_id=pilot.states["2026_501"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", "Valitsuses")],
    )
    return candidate


def test_unlinked_topics_are_counted_only_by_default(pilot, reader):
    """AGGREGATE (the default): no unlinked item anywhere — no number, field
    name or candidate link — only counts and the sentence."""
    assert obs.unlinked_detail() == obs.AGGREGATE
    candidate = _three_mappings(pilot)
    page = obs.observed_changes(reader, now=LATER)
    assert [t.reference for t in page.items] == ["2026_501"]
    assert page.total == 1
    assert page.totals["mapping"] == {"PILOT_LINKED": 1}
    assert page.unlinked.policy == obs.AGGREGATE
    assert page.unlinked.total == 2
    assert page.unlinked.kind == {"CHANGED": 2}
    assert page.unlinked.mapping == {"REFERENCE_CANDIDATE": 1, "UNMATCHED": 1}
    assert page.unlinked.field == {"next_action_text": 1, "title": 1}
    assert page.unlinked.note == (
        "2 muudatust teemades, millel pole Juristides kinnitatud vastet; "
        "üksikasju ei näidata, kuni omanik on nähtavuse otsustanud."
    )
    dumped = _dump(page.as_dict())
    for secret in ("2026_900", "2026_777", "Salajane", "Väärtus A", "Kandidaat X"):
        assert secret not in dumped
    assert str(candidate.pk) not in dumped
    # Text and reference never match an unlinked topic, nor move its count.
    for probe in (
        obs.observed_changes(reader, text="2026_900", now=LATER),
        obs.observed_changes(reader, reference="2026_777", now=LATER),
    ):
        assert probe.total == 0 and probe.unlinked.total == 2
    # A mapping filter for unlinked topics lists nothing; the count stays.
    only = obs.observed_changes(reader, mapping=MappingStatus.UNMATCHED, now=LATER)
    assert only.items == [] and only.unlinked.total == 1
    # The wider readings: the same, per reading, with the reading's own totals.
    reading = obs.nights(reader, population="ALL_LIVE", now=LATER).items[0]
    assert [t.reference for t in reading.topics] == ["2026_501"]
    assert reading.changed_topics == 3 and reading.unlinked.total == 2
    assert reading.changed_fields == 3
    assert "2026_900" not in _dump(reading.as_dict())
    assert "2026_777" not in _dump(reading.as_dict())
    by_field = obs.nights(reader, population="ALL_LIVE", field_name="title", now=LATER).items[0]
    assert by_field.topics == [] and by_field.unlinked.total == 1


def test_unmatched_values_are_withheld_and_candidates_unconfirmed(
    pilot, reader, field_names_policy
):
    """FIELD_NAMES (owner-approvable): number, kind and field names, never values."""
    pilot.add(501)
    candidate = factories.MatterFactory(
        reference_year=2026, reference_number=777, title="Kandidaat X"
    )
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(
        run,
        "2026_900",
        fields=[("title", "Salajane vana", "Salajane uus")],
    )
    observed(
        run,
        "2026_777",
        mapping=MappingStatus.REFERENCE_CANDIDATE,
        matter=candidate,
        fields=[("next_action_text", "Väärtus A", "Väärtus B")],
    )
    observed(
        run,
        "2026_501",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_501"),
        source_id=pilot.states["2026_501"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", "Valitsuses")],
    )
    page = obs.observed_changes(reader, now=LATER)
    assert page.total == 3
    by_ref = {t.reference: t for t in page.items}
    unmatched = by_ref["2026_900"]
    assert unmatched.title == "" and unmatched.matter_id is None
    assert [(f.field, f.withheld, f.before, f.after) for f in unmatched.fields] == [
        ("title", True, "", "")
    ]
    cand = by_ref["2026_777"]
    assert cand.mapping == MappingStatus.REFERENCE_CANDIDATE
    assert cand.title == "" and cand.matter_id == str(candidate.pk)
    assert all(f.withheld for f in cand.fields)
    linked = by_ref["2026_501"]
    assert linked.title == "Näidisteema 2026_501"
    assert (_fields(linked)["stage"].before, _fields(linked)["stage"].after) == (
        "Ministeeriumis",
        "Valitsuses",
    )
    dumped = _dump(page.as_dict())
    assert "Salajane" not in dumped and "Väärtus A" not in dumped and "Kandidaat X" not in dumped
    assert page.totals["mapping"] == {
        "PILOT_LINKED": 1,
        "REFERENCE_CANDIDATE": 1,
        "UNMATCHED": 1,
    }
    # A title of an unconfirmed candidate is never searchable.
    assert obs.observed_changes(reader, text="Kandidaat", now=LATER).total == 0
    assert obs.observed_changes(reader, text="Näidisteema", now=LATER).total == 1


def test_populations_are_kept_apart_in_the_counts(pilot, reader):
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(run, "2026_901", operational=True, field_names=["stage"])
    observed(run, "2026_902", operational=False, field_names=["stage"])
    everything = obs.observed_changes(reader, population="ALL_LIVE", now=LATER)
    operational = obs.observed_changes(reader, population="OPERATIONAL", now=LATER)
    assert (everything.unlinked.total, operational.unlinked.total) == (2, 1)
    assert everything.items == [] and operational.items == []


def test_populations_are_kept_apart(pilot, reader, field_names_policy):
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(run, "2026_901", operational=True, field_names=["stage"])
    observed(run, "2026_902", operational=False, field_names=["stage"])
    observed(
        run, "2026_903", operational=False, scope_changed=True, field_names=["operational_scope"]
    )
    everything = obs.observed_changes(reader, population="ALL_LIVE", now=LATER)
    operational = obs.observed_changes(reader, population="OPERATIONAL", now=LATER)
    assert everything.total == 3
    assert {t.reference for t in operational.items} == {"2026_901", "2026_903"}
    readings = obs.nights(reader, population="OPERATIONAL", now=LATER).items
    assert readings[0].kind == obs.OBSERVATION and readings[0].comparison == "COMPARED"
    assert {t.reference for t in readings[0].topics} == {"2026_901", "2026_903"}
    assert readings[1].comparison == "BASELINE"
    assert obs.observed_changes(reader, field_name="operational_scope", now=LATER).total == 1


def test_2026_2_stage_given_up_while_work_continues_under_a_new_number(
    pilot, reader, field_names_policy
):
    from app.submissions.models import Submission

    pilot.add(2, hetkeseis="Ministeeriumis")
    pilot.night(night_at(0))
    pilot.edit("2026_2", hetkeseis="Rohkem ei tegele")
    pilot.night(night_at(1))
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(run, "2026_250", kind=ObservedChangeKind.NEW, field_names=[])
    before_state = LovableSyncState.objects.get(reference="2026_2")
    submissions = Submission.objects.count()

    stage = _fields(_topic(obs.nights(reader, now=LATER).items[0], "2026_2"))["stage"]
    assert stage.result == "REVIEW" and stage.after == "Rohkem ei tegele"
    new = obs.observed_changes(reader, kind=ObservedChangeKind.NEW, now=LATER).items
    assert [(t.reference, t.mapping, t.matter_id) for t in new] == [
        ("2026_250", MappingStatus.UNMATCHED, None)
    ]
    assert new[0].summary == "Lovable'is on uus teema."
    queue = obs.review_queue(reader, now=LATER)
    assert [(i.reference, i.field) for i in queue.items] == [("2026_2", "stage")]
    # Nothing closed, relinked or sent.
    matter = pilot.matter("2026_2")
    assert matter.is_open
    after_state = LovableSyncState.objects.get(reference="2026_2")
    assert after_state.source_id == before_state.source_id
    assert after_state.applied["stage"] == before_state.applied["stage"]
    assert Submission.objects.count() == submissions


def test_a_new_unlinked_topic_is_only_counted_by_default(pilot, reader):
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(run, "2026_250", kind=ObservedChangeKind.NEW, field_names=[])
    new = obs.observed_changes(reader, kind=ObservedChangeKind.NEW, now=LATER)
    assert new.items == [] and new.unlinked.kind == {"NEW": 1}
    assert "2026_250" not in _dump(new.as_dict())
    reading = obs.nights(reader, population="ALL_LIVE", now=LATER).items[0]
    assert reading.new_topics == 1 and reading.topics == []
    assert reading.message == ""  # not «no changes»: there was one, counted


def test_a_renumbered_pilot_topic_is_linked_through_its_state(pilot, reader):
    state = pilot.add(310)
    pilot.night(night_at(0))
    pilot.edit("2026_310", nr="2026_311")
    pilot.night(night_at(1))
    topic = obs.nights(reader, now=LATER).items[0].topics[0]
    assert topic.kind == ObservedChangeKind.RENUMBERED
    assert (topic.previous_reference, topic.reference) == ("2026_310", "2026_311")
    assert topic.matter_id == str(state.matter_id)
    assert "2026_310 → 2026_311" in topic.summary


# ---------------------------------------------------------------------------
# source_events()
# ---------------------------------------------------------------------------


def test_audit_unavailable_shows_no_event(pilot, reader):
    state = pilot.add(501)
    run = observation_run(night_at(0), audit=AuditAccess.DISABLED)
    source_event(state.source_id, night_at(0), fields=[("teema", "a", "b", False)], run=run)
    audit, page = obs.source_events(reader, now=LATER)
    assert not audit.available and page.items == [] and page.total == 0
    assert audit.message.startswith("Lovable'i muutmisajalugu pole kättesaadav:")


def _audit_world(pilot):
    open_state = pilot.add(501)
    closed_state = pilot.add(502, visibility=Visibility.RESTRICTED, teema="Varjatud teema")
    run = observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    other = uuid.uuid4()
    first = observation_run(night_at(-1))
    observed(first, "2026_950", source_id=other, field_names=["title"])
    source_event(
        open_state.source_id,
        night_at(0),
        fields=[("jargmiseks", "Vana tegevus", "Uus tegevus", False), ("markmed", "", "", True)],
        run=run,
    )
    source_event(
        closed_state.source_id, night_at(0), fields=[("teema", "Varjatud", "Varjatud 2", False)]
    )
    source_event(
        other,
        night_at(0) + dt.timedelta(minutes=5),
        fields=[("teema", "Võõras vana", "Võõras uus", False)],
        action="lisamine",
    )
    return other


def test_audit_events_of_unlinked_topics_are_counted_only_by_default(pilot, monkeypatch):
    restricted_reader = narrowed_reader(monkeypatch)
    other = _audit_world(pilot)
    audit, page = obs.source_events(restricted_reader, now=LATER)
    assert audit.available and audit.events_stored == 3
    assert page.total == 1 and page.hidden == 1
    assert [e.reference for e in page.items] == ["2026_501"]
    assert page.unlinked.total == 1
    assert page.unlinked.kind == {"lisamine": 1}
    assert page.unlinked.field == {"teema": 1}
    assert page.unlinked.note.startswith("1 sündmus teemades, millel pole Juristides")
    dumped = _dump(page.as_dict())
    for secret in ("2026_950", "Võõras", str(other), "Varjatud"):
        assert secret not in dumped
    # The reference filter never reaches an unlinked topic, nor narrows its count.
    _, by_ref = obs.source_events(restricted_reader, reference="2026_950", now=LATER)
    assert by_ref.items == [] and by_ref.unlinked.total == 1
    _, by_field = obs.source_events(restricted_reader, field_name="next_action_text", now=LATER)
    assert [e.reference for e in by_field.items] == ["2026_501"]
    assert by_field.unlinked.total == 0


def test_a_value_cut_at_ingestion_says_so_everywhere(pilot, reader):
    state = pilot.add(501)
    observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    source_event(
        state.source_id,
        night_at(0),
        fields=[("jargmiseks", "Lühike", "Pikk " * 10, False), ("teema", "a", "b", False)],
        truncated=("jargmiseks",),
    )
    _, page = obs.source_events(reader, now=LATER)
    listed = {f.field: f for f in page.items[0].fields}
    assert listed["jargmiseks"].source_truncated and not listed["jargmiseks"].truncated
    assert not listed["teema"].source_truncated
    detail = obs.matter_changes(reader, "2026_501", now=LATER)
    assert detail is not None
    whole = {f.field: f for f in detail.source_events[0].fields}
    assert whole["jargmiseks"].source_truncated is True
    assert whole["jargmiseks"].truncated is False
    assert whole["teema"].source_truncated is False


def test_audit_events_with_values_only_for_an_openable_pilot_teema(
    pilot, monkeypatch, field_names_policy
):
    restricted_reader = narrowed_reader(monkeypatch)
    open_state = pilot.add(501)
    closed_state = pilot.add(502, visibility=Visibility.RESTRICTED, teema="Varjatud teema")
    run = observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    other = uuid.uuid4()
    first = observation_run(night_at(-1))
    observed(first, "2026_950", source_id=other, field_names=["title"])
    source_event(
        open_state.source_id,
        night_at(0),
        fields=[("jargmiseks", "Vana tegevus", "Uus tegevus", False), ("markmed", "", "", True)],
        run=run,
    )
    source_event(
        closed_state.source_id, night_at(0), fields=[("teema", "Varjatud", "Varjatud 2", False)]
    )
    source_event(other, night_at(0), fields=[("teema", "Võõras vana", "Võõras uus", False)])
    audit, page = obs.source_events(restricted_reader, now=LATER)
    assert audit.available and audit.events_stored == 3
    assert page.total == 2 and page.hidden == 1
    by_ref = {e.reference: e for e in page.items}
    mine = {f.field: f for f in by_ref["2026_501"].fields}
    assert (mine["jargmiseks"].before, mine["jargmiseks"].after) == ("Vana tegevus", "Uus tegevus")
    assert mine["jargmiseks"].label == "Järgmine tegevus (sõnastus)"
    assert mine["markmed"].withheld and mine["markmed"].before == ""
    foreign = by_ref["2026_950"]
    assert foreign.mapping == MappingStatus.UNMATCHED and foreign.title == ""
    assert all(f.withheld for f in foreign.fields)
    dumped = _dump(page.as_dict())
    assert "Varjatud" not in dumped and "Võõras" not in dumped
    _, by_field = obs.source_events(restricted_reader, field_name="next_action_text", now=LATER)
    assert [e.reference for e in by_field.items] == ["2026_501"]


# ---------------------------------------------------------------------------
# review_queue()
# ---------------------------------------------------------------------------


def test_2026_243_preparing_an_opinion_becomes_monitoring(pilot, reader):
    pilot.add(243, jargmiseks="Valmistada ette arvamus")
    pilot.night(night_at(0))
    pilot.edit("2026_243", jargmiseks="Jälgida menetlust")
    pilot.night(night_at(1))
    pilot.night(night_at(2))
    pilot.night(night_at(3))
    page = obs.review_queue(reader, now=LATER)
    texts = [i for i in page.items if i.field == "next_action_text"]
    assert len(texts) == 1  # one pending field, however many nights
    item = texts[0]
    assert item.outcome == SyncOutcome.REVIEW_REQUIRED
    assert item.reason == lovable_sync.TEXT_CHANGED
    assert item.nights_pending == 3
    assert item.first_observed_at == night_at(1) and item.last_observed_at == night_at(3)
    assert item.last_applied == "Valmistada ette arvamus"
    assert item.lovable_now == "Jälgida menetlust"
    assert item.juristid_now == "Valmistada ette arvamus"
    assert "tee/oota/jälgi" in item.suggested_action
    assert item.suggested_action.endswith("Rea märgib lahendatuks haldur.")
    assert "lovable_sync" not in item.suggested_action  # no server command for a lawyer
    assert item.explanation.startswith("Järgmise tegevuse sõnastus muutus")
    assert item.age_days == 2
    # Still held: nothing in Juristid moved.
    step = lovable_sync.current_values(pilot.matter("2026_243"))["next_action"]
    assert step["text"] == "Valmistada ette arvamus"


def test_2026_186_local_change_and_link_change_are_told_apart(pilot, reader):
    from app.workflow.models import NextAction

    pilot.add(186)
    pilot.night(night_at(0))
    # The lawyer changed the step in Juristid; Lovable moved the day and the link.
    NextAction.objects.filter(matter=pilot.matter("2026_186")).update(
        text="Kohtuda ministeeriumiga"
    )
    pilot.edit(
        "2026_186", tegevuse_tahtaeg="2026-11-28", onenote_link="https://onenote.example/uus"
    )
    pilot.night(night_at(1))
    fields = _fields(_topic(obs.nights(reader, now=LATER).items[0], "2026_186"))
    assert fields["next_action_date"].result == "CONFLICT"
    assert fields["onenote_link"].result == "NOT_APPLIED"  # eligible, COMPARE only
    queue = {i.field: i for i in obs.review_queue(reader, now=LATER).items}
    assert queue["next_action_date"].outcome == SyncOutcome.CONFLICT
    assert queue["onenote_link"].outcome == SyncOutcome.ELIGIBLE
    assert "APPLY" in queue["onenote_link"].suggested_action
    assert queue["next_action_date"].juristid_now == "20.11.2026"


def test_2026_213_one_estimate_becomes_several(pilot, reader):
    pilot.add(213)
    pilot.edit(
        "2026_213",
        commencements=[
            {"liik": "Hinnanguline", "kuupaev": "2027-01-01", "tapsustus": "", "position": 1}
        ],
    )
    pilot.states["2026_213"].applied["commencements"] = {
        "source": lovable_sync._commencements(pilot.bundles["2026_213"])
    }
    pilot.states["2026_213"].save()
    pilot.night(night_at(0))
    pilot.edit(
        "2026_213",
        commencements=[
            {"liik": "Hinnanguline", "kuupaev": "2027-01-01", "tapsustus": "osa 1", "position": 1},
            {"liik": "Hinnanguline", "kuupaev": "2027-07-01", "tapsustus": "osa 2", "position": 2},
        ],
    )
    pilot.night(night_at(1))
    topic = _topic(obs.nights(reader, now=LATER).items[0], "2026_213")
    change = _fields(topic)["commencements"]
    assert change.result == "REVIEW"
    assert change.after == "Hinnanguline 01.01.2027 (osa 1); Hinnanguline 01.07.2027 (osa 2)"
    assert topic.summary == "Lovable'i jõustumise hinnangud muutusid."
    item = next(i for i in obs.review_queue(reader, now=LATER).items if i.field == "commencements")
    assert item.reason == lovable_sync.HELD
    assert item.juristid_now == obs.NOT_COMPARED_IN_JURISTID
    assert "jõustunud" not in _dump(obs.matter_changes(reader, "2026_213", now=LATER).as_dict())


def test_2026_244_a_sent_value_never_becomes_a_submission(pilot, reader):
    from app.submissions.models import Submission

    pilot.add(244)
    pilot.night(night_at(0))
    pilot.edit("2026_244", valja="Jah", valja_kuupaev="2026-10-30")
    submissions = Submission.objects.count()
    pilot.night(night_at(1))
    topic = _topic(obs.nights(reader, now=LATER).items[0], "2026_244")
    assert "Lovable'i «Välja» märge ja kuupäev muutusid" in topic.summary
    assert {f.result for f in topic.fields} == {"REVIEW"}
    queue = {i.field for i in obs.review_queue(reader, now=LATER).items}
    assert queue == {"sent", "sent_date"}
    obs.readiness(reader, now=LATER)
    assert Submission.objects.count() == submissions


def test_a_resolved_field_leaves_the_queue(pilot, reader):
    pilot.add(501)
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.night(night_at(1))
    assert [i.field for i in obs.review_queue(reader, now=LATER).items] == ["stage"]
    lovable_sync.resolve_field(reference="2026_501", field_name="stage", note="t")
    assert obs.review_queue(reader, now=LATER).items == []


def test_the_queue_is_oldest_first_and_filtered(pilot, reader):
    pilot.add(501)
    pilot.add(502)
    pilot.night(night_at(0))
    pilot.edit("2026_502", hetkeseis="Valitsuses")
    pilot.night(night_at(1))
    pilot.edit("2026_501", hetkeseis="Riigikogus")
    pilot.night(night_at(2))
    page = obs.review_queue(reader, now=LATER)
    assert [i.reference for i in page.items] == ["2026_502", "2026_501"]
    assert [
        i.reference for i in obs.review_queue(reader, reference="2026_501", now=LATER).items
    ] == ["2026_501"]
    assert obs.review_queue(reader, field_name="title", now=LATER).total == 0
    assert obs.review_queue(reader, text="riigikogus", now=LATER).total == 1
    assert page.totals["field"] == {"stage": 2}


# ---------------------------------------------------------------------------
# matter_changes()
# ---------------------------------------------------------------------------


def test_matter_changes_by_reference_and_by_id(pilot, reader):
    state = pilot.add(243, jargmiseks="Valmistada ette arvamus")
    pilot.night(night_at(0))
    pilot.edit("2026_243", jargmiseks="Jälgida menetlust")
    pilot.night(night_at(1))
    by_ref = obs.matter_changes(reader, "2026/243", now=LATER)
    by_id = obs.matter_changes(reader, str(state.matter_id), now=LATER)
    assert by_ref is not None and by_id is not None
    assert by_ref.as_dict() == by_id.as_dict()
    fields = {f["field"]: f for f in by_ref.fields}
    assert fields["next_action_text"] == {
        "field": "next_action_text",
        "label": "Järgmine tegevus (sõnastus)",
        "automatic": False,
        "last_applied": "Valmistada ette arvamus",
        "juristid_now": "Valmistada ette arvamus",
        "lovable_observed": "Jälgida menetlust",
        "pending": True,
    }
    assert [t.reference for t in by_ref.nights] == ["2026_243"]
    assert {d["field"] for d in by_ref.decisions} == {"next_action_text"}
    assert [p.field for p in by_ref.pending] == ["next_action_text"]
    assert by_ref.source_events == [] and not by_ref.audit.available


def test_matter_changes_is_none_for_unknown_hidden_or_non_pilot(pilot, reader, monkeypatch):
    pilot.add(501, visibility=Visibility.RESTRICTED)
    outsider = factories.MatterFactory(reference_year=2026, reference_number=600)
    restricted = narrowed_reader(monkeypatch)
    assert obs.matter_changes(reader, "2026_999") is None
    assert obs.matter_changes(reader, str(outsider.pk)) is None
    assert obs.matter_changes(reader, "2026_600") is None
    assert obs.matter_changes(restricted, "2026_501") is None
    assert obs.matter_changes(reader, "2026_501") is not None


def test_a_deleted_teema_is_left_out(pilot, reader):
    pilot.add(501)
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.night(night_at(1))
    from app.matters.models import Matter

    Matter.all_objects.filter(pk=pilot.states["2026_501"].matter_id).update(deleted_at=night_at(1))
    assert obs.matter_changes(reader, "2026_501", now=LATER) is None
    page = obs.review_queue(reader, now=LATER)
    assert page.items == [] and page.hidden == 0
    newest = obs.nights(reader, now=LATER).items[0]
    assert newest.topics == [] and newest.hidden == 0


# ---------------------------------------------------------------------------
# Who sees what
# ---------------------------------------------------------------------------


def test_an_administrator_without_a_business_role_gets_nothing(pilot):
    admin = factories.AdministratorFactory()
    pilot.add(501, teema="Admin ei näe seda")
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.night(night_at(1))
    first = observation_run(night_at(0))
    observed(observation_run(night_at(1), baseline=first), "2026_900", field_names=["title"])
    assert obs.nights(admin, now=LATER).items == []
    assert obs.nights(admin, population="ALL_LIVE", now=LATER).items == []
    assert obs.observed_changes(admin, now=LATER).items == []
    assert obs.review_queue(admin, now=LATER).items == []
    assert obs.source_events(admin, now=LATER)[1].items == []
    assert obs.matter_changes(admin, "2026_501", now=LATER) is None
    status = obs.status(admin, now=LATER)
    assert status.pending_review == 1  # a count, never an identity
    readiness = obs.readiness(admin, now=LATER)
    assert readiness.simulation_rows == []
    dumped = _dump(status.as_dict()) + _dump(readiness.as_dict())
    assert "Admin ei näe seda" not in dumped and "Valitsuses" not in dumped
    assert "2026_501" not in dumped


def test_a_restricted_teema_is_hidden_everywhere(pilot, monkeypatch):
    restricted_reader = narrowed_reader(monkeypatch)
    pilot.add(501)
    pilot.add(502, visibility=Visibility.RESTRICTED, teema="Varjatud teema 502")
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.edit("2026_502", hetkeseis="Salajane etapp")
    pilot.night(night_at(1))
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(
        run,
        "2026_502",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_502"),
        source_id=pilot.states["2026_502"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", "Salajane etapp")],
    )
    answers = [
        obs.status(restricted_reader, now=LATER),
        obs.nights(restricted_reader, now=LATER),
        obs.nights(restricted_reader, population="ALL_LIVE", now=LATER),
        obs.observed_changes(restricted_reader, now=LATER),
        obs.review_queue(restricted_reader, now=LATER),
        obs.readiness(restricted_reader, now=LATER),
    ]
    dumped = "".join(_dump(a.as_dict()) for a in answers)
    assert "Varjatud" not in dumped and "Salajane" not in dumped
    assert "2026_502" not in dumped
    assert "Valitsuses" in dumped  # the open one is there
    night = answers[1].items[0]
    assert night.hidden == 1
    assert answers[2].items[0].hidden == 1
    assert answers[3].hidden == 1
    assert answers[4].hidden == 1
    # A text filter never matches what the reader may not see.
    assert obs.nights(restricted_reader, text="Varjatud", now=LATER).items[0].topics == []
    assert obs.review_queue(restricted_reader, text="Salajane", now=LATER).total == 0
    assert obs.observed_changes(restricted_reader, text="Varjatud", now=LATER).total == 0


# ---------------------------------------------------------------------------
# Reads only, and in a constant number of queries
# ---------------------------------------------------------------------------


def test_no_question_writes_anything(pilot, reader):
    control(SyncMode.COMPARE, observe=True)
    pilot.add(243)
    pilot.add(186)
    pilot.night(night_at(0))
    pilot.edit("2026_243", jargmiseks="Jälgida")
    pilot.night(night_at(1))
    first = observation_run(night_at(0))
    observation_run(night_at(1), baseline=first, audit=AuditAccess.AVAILABLE)
    before = row_counts()
    with CaptureQueriesContext(connection) as queries:
        obs.status(reader, now=LATER)
        obs.nights(reader, now=LATER)
        obs.nights(reader, population="OPERATIONAL", now=LATER)
        obs.observed_changes(reader, now=LATER)
        obs.source_events(reader, now=LATER)
        obs.review_queue(reader, now=LATER)
        obs.matter_changes(reader, "2026_243", now=LATER)
        obs.readiness(reader, now=LATER)
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before


def test_current_values_bulk_is_current_values(pilot):
    pilot.add(501)
    pilot.add(502, onenote_link="https://onenote.example/a")
    states = list(LovableSyncState.objects.select_related("matter"))
    bulk = obs.current_values_bulk([s.matter for s in states])
    for state in states:
        assert bulk[state.matter_id] == lovable_sync.current_values(state.matter)


def _world(size: int, base: int) -> Pilot:
    pilot = Pilot()
    for number in range(base + 1, base + size + 1):
        pilot.add(number)
    pilot.night(night_at(0))
    for number in range(base + 1, base + size + 1):
        pilot.edit(f"2026_{number}", hetkeseis="Valitsuses", jargmiseks=f"Uus {number}")
    pilot.night(night_at(1))
    pilot.night(night_at(2))
    return pilot


def _queries(call) -> int:
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries.captured_queries)


@pytest.mark.parametrize(
    "question",
    [
        lambda u, ref: obs.review_queue(u, now=LATER),
        lambda u, ref: obs.nights(u, now=LATER),
        lambda u, ref: obs.status(u, now=LATER),
        lambda u, ref: obs.matter_changes(u, ref, now=LATER),
    ],
)
def test_no_query_per_teema(reader, question):
    from app.legacy_import.lovable_sync_models import LovableSyncRun

    control(SyncMode.COMPARE)
    _world(3, 800)
    small = _queries(lambda: question(reader, "2026_801"))
    LovableSyncState.objects.all().delete()
    LovableSyncRun.objects.all().delete()
    _world(20, 900)
    large = _queries(lambda: question(reader, "2026_901"))
    assert large - small <= 3, (small, large)


def test_observation_lists_are_constant_in_queries(pilot, reader):
    def build(n: int) -> None:
        first = observation_run(night_at(0))
        run = observation_run(night_at(1), baseline=first)
        for i in range(n):
            matter = factories.MatterFactory(reference_year=2025, reference_number=i + 1 + n * 100)
            observed(
                run,
                f"2025_{i + 1 + n * 100}",
                mapping=MappingStatus.REFERENCE_CANDIDATE,
                matter=matter,
                fields=[("title", "a", "b")],
            )

    build(3)
    small = _queries(lambda: obs.observed_changes(reader, now=LATER))
    small_n = _queries(lambda: obs.nights(reader, population="ALL_LIVE", now=LATER))
    build(20)
    large = _queries(lambda: obs.observed_changes(reader, now=LATER))
    large_n = _queries(lambda: obs.nights(reader, population="ALL_LIVE", now=LATER))
    assert large - small <= 2 and large_n - small_n <= 2, (small, large, small_n, large_n)


def test_every_answer_is_json(pilot, reader):
    pilot.add(501)
    pilot.night(night_at(0))
    for answer in (
        obs.status(reader, now=LATER),
        obs.nights(reader, now=LATER),
        obs.review_queue(reader, now=LATER),
        obs.readiness(reader, now=LATER),
    ):
        json.dumps(answer.as_dict())


def test_observation_failed_reading_says_so(reader):
    observation_run(night_at(0), status=ObservationRunStatus.FAILED, error="INCONSISTENT: x")
    reading = obs.nights(reader, population="ALL_LIVE", now=LATER).items[0]
    assert reading.comparison == "NO_SNAPSHOT" and "INCONSISTENT" in reading.message
    assert ": x" not in reading.message


def test_estonian_ledger_actions_have_labels(pilot, reader):
    state = pilot.add(501)
    observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    for action in ("muutmine", "lisamine", "kustutamine", "taastamine"):
        source_event(
            state.source_id, night_at(0), action=action, fields=[("teema", "a", "b", False)]
        )
    _, page = obs.source_events(reader, now=LATER)
    assert {e.action_label for e in page.items} == {"Muudetud", "Loodud", "Kustutatud", "Taastatud"}
    assert {e.actor_kind for e in page.items} == {"kasutaja"}


# ---------------------------------------------------------------------------
# observation_health(): the monitoring verdict (production_status, overview)
# ---------------------------------------------------------------------------

SOON = night_at(1) + dt.timedelta(hours=1)


def _health(**kwargs):
    before = row_counts()
    with CaptureQueriesContext(connection) as queries:
        health = obs.observation_health(**kwargs)
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before
    return health


def test_health_off_and_empty_is_green_and_creates_nothing():
    health = _health(now=SOON)
    assert health.ok and health.detail == "off" and not health.observe
    assert not LovableSyncControl.objects.exists()


def test_health_off_with_raw_data_needs_a_fresh_retention_run(reader):
    control(SyncMode.OFF, observe=False)
    first = observation_run(night_at(0))
    observed(observation_run(night_at(1), baseline=first), "2026_901", field_names=["stage"])
    stale = _health(now=SOON)
    assert not stale.ok
    assert stale.detail == "no successful retention run in the last 26 hours"
    retention_run(night_at(1))
    assert _health(now=SOON).ok and _health(now=SOON).detail == "off"
    retention_run(night_at(1) + dt.timedelta(minutes=30), status="FAILED", error="DB_ERROR: x")
    failed = _health(now=SOON)
    assert not failed.ok and failed.detail == "retention failed (DB_ERROR)"
    assert failed.retention_failure_code == "DB_ERROR"
    status = obs.status(reader, now=SOON)
    assert not status.observation_health_ok
    assert any("kustutamine" in w and "DB_ERROR" in w for w in status.warnings)
    by_key = {i.key: i for i in status.indicators}
    assert by_key["retention_freshness"].value == 1  # hours since the good run


def test_health_on_and_failed_is_red():
    control(SyncMode.COMPARE, observe=True)
    observation_run(night_at(0))
    retention_run(night_at(1))
    observation_run(night_at(1), status=ObservationRunStatus.FAILED, error="HTTP_401: secret")
    health = _health(now=SOON)
    assert not health.ok
    assert health.problems == ["last observation failed (HTTP_401)"]
    assert "secret" not in health.detail


def test_health_on_and_stale_is_red():
    control(SyncMode.COMPARE, observe=True)
    observation_run(night_at(0))
    retention_run(night_at(2))
    health = _health(now=night_at(2) + dt.timedelta(hours=1))
    assert health.problems == ["no successful observation in the last 26 hours"]


def test_health_on_and_fresh_is_green():
    control(SyncMode.COMPARE, observe=True)
    run = observation_run(night_at(1))
    retention_run(night_at(1))
    health = _health(now=SOON)
    assert health.ok and health.detail == f"on, last success {run.run_key}"


def test_health_on_and_stuck_running_is_red():
    control(SyncMode.COMPARE, observe=True)
    observation_run(night_at(1))
    observation_run(night_at(1) + dt.timedelta(minutes=5), status=ObservationRunStatus.RUNNING)
    retention_run(night_at(1))
    assert _health(now=night_at(1) + dt.timedelta(hours=1)).ok  # running, not stuck
    stuck = _health(now=night_at(1) + dt.timedelta(hours=3))
    assert stuck.problems == ["an observation has been running over 2 hours"]


def test_health_audit_enabled_but_unreadable_is_red(settings):
    control(SyncMode.COMPARE, observe=True)
    retention_run(night_at(1))
    observation_run(night_at(1), audit=AuditAccess.DISABLED)
    assert _health(now=SOON).ok  # audit not enabled: DISABLED is expected
    settings.LOVABLE_AUDIT_ENABLED = True
    assert _health(now=SOON).problems == ["audit history DISABLED"]
    observation_run(
        night_at(1) + dt.timedelta(minutes=1),
        audit=AuditAccess.UNAVAILABLE,
        audit_error="EMPTY_UNDER_RLS",
    )
    assert _health(now=SOON).problems == ["audit history UNAVAILABLE, EMPTY_UNDER_RLS"]
    # The run itself says the ledger was tried, even where this process's flag is off.
    settings.LOVABLE_AUDIT_ENABLED = False
    assert not _health(now=SOON).ok
    observation_run(night_at(1) + dt.timedelta(minutes=2), audit=AuditAccess.AVAILABLE)
    settings.LOVABLE_AUDIT_ENABLED = True
    assert _health(now=SOON).ok
