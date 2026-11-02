"""The wider Lovable observation (docs/adr/0201): reading, comparing, the run.

Lovable is a fake transport serving invented rows; nothing leaves the process.
Every title, name and identifier below is synthetic.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from typing import Any

import pytest

from app.legacy_import import lovable_observation
from app.legacy_import.lovable_observation import run_observation, set_observe
from app.legacy_import.lovable_observation_models import (
    AuditAccess,
    LovableObservationRun,
    LovableObservationSnapshot,
    LovableObservedChange,
    LovableRetentionRun,
    LovableSourceEvent,
    MappingStatus,
    ObservationRunStatus,
    ObservedChangeKind,
    RetentionRunStatus,
)
from app.legacy_import.lovable_source import (
    AUDIT_TABLES,
    OBSERVE_TABLES,
    PAGE_SIZE,
    SourceError,
)
from app.legacy_import.lovable_sync import TALLINN, SyncRefused
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncTrigger,
)
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db

NIGHT = dt.datetime(2026, 11, 2, 0, 15, tzinfo=TALLINN)


def _id(n: int) -> str:
    return str(uuid.UUID(int=0xA0000000_0000_4000_8000_000000000000 + n))


def _topic(n: int, *, operational: bool = False, **values: Any) -> dict[str, Any]:
    return {
        "id": _id(n),
        "nr": f"2026_{900 + n}",
        "aasta": 2026,
        "teema": f"Näidisteema number {n}",
        "oigusakt": "",
        "sisse": "2026-01-05",
        "arvamuse_tahtaeg": None,
        "valja": "",
        "valja_kuupaev": None,
        "vastutaja": "Mari",
        "uus_vastutaja": "",
        "hetkeseis": "Riigikogus",
        "jargmiseks": "ootan",
        "koja_ettepanek": False,
        "kellele": "",
        "tegevuse_tahtaeg": "2026-11-20",
        "onenote_link": "",
        "operatiivne_ulatus": operational,
        "joustumised_hallatud": False,
        "version": 1,
        "updated_at": "2026-10-01T10:00:00+00:00",
        # Never read, and dropped if a source ever sent it.
        "markmed": f"SISEMINE-{n}",
        "allikas": "toorrida",
    }


class FakeRegister:
    """Lovable's PostgREST for the observation: allowlisted tables only, plus
    the audit ledger. Records every call; serves only the columns asked for."""

    def __init__(self, topics: list[dict[str, Any]]) -> None:
        self.topics = {t["id"]: dict(t) for t in topics}
        self.children: dict[str, list[dict[str, Any]]] = {t: [] for t in OBSERVE_TABLES}
        self.children.pop("topic")
        self.events: list[dict[str, Any]] = []
        self.fields: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.fail: Exception | None = None
        self.audit_fail: Exception | None = None
        self.on_select: Any = None
        #: Every ledger field read as it reached the source: (columns, filters),
        #: and every answer Juristid was handed for it.
        self.field_requests: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.field_answers: list[list[dict[str, Any]]] = []
        #: A misbehaving source that ignores the ``field`` filter.
        self.ignore_field_filter = False
        self.requests = 0
        self.bytes_read = 0

    # -- the reads -------------------------------------------------------------

    def select(self, table, columns, *, filters, order):
        self.calls.append(table)
        if self.on_select is not None:
            self.on_select(self, table)
        if self.fail is not None:
            raise self.fail
        columns = tuple(columns)
        if table == "topic":
            assert filters == {}
            rows = sorted(self.topics.values(), key=lambda r: r["id"])
        elif table in self.children:
            assert filters == {}
            rows = self.children[table]
        elif table == "topic_change_field":
            if self.audit_fail is not None:
                raise self.audit_fail
            self.field_requests.append((columns, dict(filters)))
            wanted = set(filters["event_id"][4:-1].split(","))
            rows = [r for r in self.fields if r["event_id"] in wanted]
            spec = filters.get("field")
            if spec and not self.ignore_field_filter:
                negate = spec.startswith("not.")
                names = set(spec.removeprefix("not.")[4:-1].split(","))
                rows = [r for r in rows if (r["field"] in names) != negate]
        else:
            raise AssertionError(f"unexpected table {table}")
        out = [{**{c: r.get(c) for c in columns}, "extra": "x"} for r in rows]
        if table == "topic_change_field":
            self.field_answers.append(out)
        self.requests += max(1, -(-len(out) // PAGE_SIZE))
        self.bytes_read += len(json.dumps(out, default=str))
        return out

    def select_page(self, table, columns, *, filters, order, limit=PAGE_SIZE):
        self.calls.append(table)
        assert table == "topic_change_event" and order == "occurred_at,id"
        if self.audit_fail is not None:
            raise self.audit_fail
        rows = sorted(self.events, key=lambda e: (e["occurred_at"], e["id"]))
        if filters:
            (expr,) = filters.values()
            moment = dt.datetime.fromisoformat(expr.split('"')[1])
            last = expr.rsplit("id.gt.", 1)[1].removesuffix("))")
            rows = [
                e
                for e in rows
                if dt.datetime.fromisoformat(e["occurred_at"]) > moment
                or (dt.datetime.fromisoformat(e["occurred_at"]) == moment and e["id"] > last)
            ]
        self.requests += 1
        return [{c: e.get(c) for c in columns} for e in rows[:limit]]

    # -- editing the source ----------------------------------------------------

    def edit(self, n: int, *, bump: bool = True, **values: Any) -> None:
        self.topics[_id(n)].update(values)
        if bump:
            self.topics[_id(n)]["version"] += 1

    def audit_tables_called(self) -> bool:
        return any(c in AUDIT_TABLES for c in self.calls)


def _register() -> FakeRegister:
    """Ten topics: 0–3 operational, 4–9 not; 5 and 6 are in the pilot."""
    return FakeRegister([_topic(n, operational=n < 4) for n in range(10)])


@pytest.fixture
def observing():
    set_observe(True, reason="test")


@pytest.fixture
def pilot():
    """Topics 5 and 6 are pilot Teemad (approved provenance, `LovableSyncState`)."""
    linked = {}
    for n in (5, 6):
        matter = factories.MatterFactory(reference_year=2026, reference_number=900 + n)
        LovableSyncState.objects.create(
            matter=matter, source_id=_id(n), reference=f"2026_{900 + n}"
        )
        linked[n] = matter
    return linked


def _observe(source: FakeRegister, at: dt.datetime = NIGHT, trigger: str = SyncTrigger.SCHEDULED):
    return run_observation(trigger=trigger, transport=source, now=lambda: at, pause=lambda _s: None)


def _changes(run: LovableObservationRun) -> dict[str, LovableObservedChange]:
    return {str(c.source_id): c for c in run.changes.all()}


def _ledger_counts() -> tuple[int, ...]:
    return (
        LovableSyncState.objects.count(),
        LovableSyncDecision.objects.count(),
        LovableSyncSnapshot.objects.count(),
        LovableSyncRun.objects.count(),
        Matter.all_objects.count(),
    )


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------


def test_a_first_reading_indexes_every_live_topic_and_details_only_some(observing, pilot):
    source = _register()
    source.children["topic_commencement"] = [
        {
            "id": "c1",
            "topic_id": _id(1),
            "liik": "jõustumine",
            "kuupaev": "2027-01-01",
            "tapsustus": "",
            "position": 1,
        },
    ]
    source.children["topic_link"] = [{"topic_id": _id(1), "linked_topic_id": _id(7)}]
    run = _observe(source)

    assert run.status == ObservationRunStatus.SUCCEEDED, run.error
    assert run.baseline is None and run.changes.count() == 0
    counts = run.counts
    assert counts["comparison"] == "BASELINE"
    assert (counts["live"], counts["operational"], counts["detailed"]) == (10, 4, 6)
    assert (counts["pilot_expected"], counts["pilot_found"]) == (2, 2)
    assert counts["children_rows"]["topic_commencement"] == 1
    for key in ("rows_read", "requests", "bytes", "seconds", "attempts", "by_kind", "by_mapping"):
        assert key in counts
    assert counts["attempts"] == 1

    document = run.snapshot.document
    lovable_observation.verify_observation(document)
    assert document["format"] == "juristid.lovable-observation"
    assert [e["id"] for e in document["index"]] == sorted(_id(n) for n in range(10))
    assert set(document["detail"]) == {_id(n) for n in (0, 1, 2, 3, 5, 6)}
    assert set(document["index"][0]) == {
        "id",
        "nr",
        "aasta",
        "version",
        "operational",
        "row_sha256",
        "children_sha256",
        "sha256",
    }
    assert document["detail"][_id(1)]["commencements"] == [["jõustumine", "2027-01-01", ""]]
    assert document["detail"][_id(1)]["topic_links"] == [_id(7)]
    text = json.dumps(document, ensure_ascii=False)
    # A topic neither operational nor in the pilot keeps its index entry only.
    assert "Näidisteema number 8" not in text and "Näidisteema number 7" not in text
    assert "Näidisteema number 5" in text
    assert "SISEMINE" not in text and "toorrida" not in text and '"extra"' not in text
    assert not source.audit_tables_called()
    assert run.audit_access == AuditAccess.DISABLED


def test_a_reading_that_moves_between_passes_is_repeated(observing):
    source = _register()
    topic_reads = []

    def once(fake, table):
        if table == "topic":
            topic_reads.append(1)
            if len(topic_reads) == 2:  # first attempt, second pass
                fake.edit(3, hetkeseis="Valitsuses")

    source.on_select = once
    run = _observe(source)
    assert run.status == ObservationRunStatus.SUCCEEDED
    assert run.counts["attempts"] == 2
    assert run.snapshot.document["detail"][_id(3)]["stage"] == "Valitsuses"


def test_a_reading_that_never_settles_fails_inconsistent(observing):
    source = _register()
    source.on_select = lambda fake, table: table == "topic" and fake.edit(2, teema=str(time.time()))
    run = _observe(source)
    assert run.status == ObservationRunStatus.FAILED
    assert run.error.startswith("INCONSISTENT")
    assert not LovableObservationSnapshot.objects.exists()


# ---------------------------------------------------------------------------
# Comparing two readings
# ---------------------------------------------------------------------------


def test_new_changed_missing_renumbered_and_scope_changes(observing, pilot):
    source = _register()
    _observe(source)
    source.topics[_id(10)] = _topic(10)  # NEW
    source.edit(1, jargmiseks="saadan arvamuse", tegevuse_tahtaeg="2026-12-01")  # CHANGED, detailed
    source.edit(8, hetkeseis="Valitsuses")  # CHANGED, not detailed
    del source.topics[_id(9)]  # MISSING
    source.edit(7, nr="2026_999")  # RENUMBERED
    source.edit(4, operatiivne_ulatus=True)  # scope in
    source.edit(5, teema="Uus pealkiri")  # pilot, CHANGED
    run = _observe(source, NIGHT + dt.timedelta(days=1))

    assert run.status == ObservationRunStatus.SUCCEEDED, run.error
    assert run.counts["comparison"] == "COMPARED" and run.baseline is not None
    found = _changes(run)
    assert set(found) == {_id(n) for n in (10, 1, 8, 9, 7, 4, 5)}
    assert found[_id(10)].kind == ObservedChangeKind.NEW
    assert found[_id(9)].kind == ObservedChangeKind.MISSING
    assert found[_id(9)].reference == "2026_909"

    one = found[_id(1)]
    assert (one.kind, one.detailed, one.children_only) == (ObservedChangeKind.CHANGED, True, False)
    assert set(one.field_names) == {"next_action_text", "next_action_date"}
    assert {
        "field": "next_action_date",
        "before": "2026-11-20",
        "after": "2026-12-01",
    } in one.fields
    assert (one.version_before, one.version_after) == (1, 2)

    eight = found[_id(8)]
    assert (eight.detailed, eight.field_names, eight.fields) == (False, [], [])

    seven = found[_id(7)]
    assert seven.kind == ObservedChangeKind.RENUMBERED
    assert (seven.reference, seven.previous_reference) == ("2026_999", "2026_907")

    four = found[_id(4)]
    assert four.scope_changed and four.operational and not four.detailed

    five = found[_id(5)]
    assert five.in_pilot and five.mapping == MappingStatus.PILOT_LINKED
    assert five.matter_id == pilot[5].pk
    assert five.field_names == ["title"]

    assert run.counts["by_kind"] == {"NEW": 1, "CHANGED": 4, "MISSING": 1, "RENUMBERED": 1}
    assert run.counts["scope_changed"] == 1


def test_a_topic_back_after_a_missing_night_is_back(observing):
    source = _register()
    _observe(source)
    gone = source.topics.pop(_id(8))
    second = _observe(source, NIGHT + dt.timedelta(days=1))
    assert _changes(second)[_id(8)].kind == ObservedChangeKind.MISSING
    source.topics[_id(8)] = gone
    third = _observe(source, NIGHT + dt.timedelta(days=2))
    assert _changes(third)[_id(8)].kind == ObservedChangeKind.BACK
    assert third.baseline == second


def test_a_child_record_change_without_the_row_moving_is_children_only(observing):
    source = _register()
    _observe(source)
    source.children["topic_commencement"].append(
        {
            "id": "c9",
            "topic_id": _id(2),
            "liik": "jõustumine",
            "kuupaev": "2027-07-01",
            "tapsustus": "osaliselt",
            "position": 1,
        }
    )
    source.children["topic_responsible_change"].append(
        {
            "id": "r1",
            "topic_id": _id(8),
            "vana_vastutaja": "Mari",
            "uus_vastutaja": "Jaan",
            "alates": "2026-11-01",
        }
    )
    run = _observe(source, NIGHT + dt.timedelta(days=1))
    found = _changes(run)
    two, eight = found[_id(2)], found[_id(8)]
    assert two.children_only and two.detailed and two.field_names == ["commencements"]
    assert (two.version_before, two.version_after) == (1, 1)
    assert eight.children_only and not eight.detailed and eight.fields == []
    assert run.counts["children_only"] == 2


def test_mapping_is_by_provenance_or_reference_never_by_title(observing, pilot):
    source = _register()
    _observe(source)
    # A Juristid Teema with topic 3's number: a candidate, never a link.
    candidate = factories.MatterFactory(reference_year=2026, reference_number=903)
    # A Teema with topic 2's exact title but another number: nothing.
    factories.MatterFactory(
        title="Näidisteema number 2", reference_year=2026, reference_number=1999
    )
    # A tombstoned Teema with topic 1's number is no candidate.
    gone = factories.MatterFactory(reference_year=2026, reference_number=901)
    Matter.all_objects.filter(pk=gone.pk).update(deleted_at=NIGHT)
    for n in (1, 2, 3, 6):
        source.edit(n, hetkeseis="Valitsuses")
    run = _observe(source, NIGHT + dt.timedelta(days=1))
    found = _changes(run)
    assert (found[_id(3)].mapping, found[_id(3)].matter_id) == (
        MappingStatus.REFERENCE_CANDIDATE,
        candidate.pk,
    )
    assert (found[_id(2)].mapping, found[_id(2)].matter_id) == (MappingStatus.UNMATCHED, None)
    assert found[_id(1)].mapping == MappingStatus.UNMATCHED
    assert (found[_id(6)].mapping, found[_id(6)].matter_id) == (
        MappingStatus.PILOT_LINKED,
        pilot[6].pk,
    )
    assert run.counts["by_mapping"] == {"REFERENCE_CANDIDATE": 1, "UNMATCHED": 2, "PILOT_LINKED": 1}


# ---------------------------------------------------------------------------
# The run's guards
# ---------------------------------------------------------------------------


def test_a_failed_read_is_a_failed_run_with_no_snapshot(observing):
    source = _register()
    source.fail = SourceError("HTTP_503", "Lovable answered 503 on /rest/v1/topic.")
    run = _observe(source)
    assert run.status == ObservationRunStatus.FAILED
    assert run.error.startswith("HTTP_503")
    assert run.finished_at is not None
    assert not LovableObservationSnapshot.objects.exists()
    # A failed reading is never a baseline: the next one is a first reading.
    source.fail = None
    nxt = _observe(source, NIGHT + dt.timedelta(days=1))
    assert nxt.counts["comparison"] == "BASELINE" and nxt.baseline is None


def test_a_second_scheduled_reading_the_same_night_is_refused(observing):
    source = _register()
    first = _observe(source)
    assert first.run_key == "observe:2026-11-02"
    with pytest.raises(SyncRefused) as refused:
        _observe(source, NIGHT + dt.timedelta(minutes=50))
    assert refused.value.code == "DUPLICATE"
    manual = _observe(source, NIGHT + dt.timedelta(hours=1), trigger=SyncTrigger.MANUAL)
    assert manual.run_key == "observe-manual:2026-11-02T01:15:00"


def test_observation_off_or_cut_over_is_refused_before_anything_is_read():
    source = _register()
    with pytest.raises(SyncRefused) as off:
        _observe(source)
    assert off.value.code == "OBSERVE_OFF"
    set_observe(True, reason="test")
    LovableSyncControl.objects.filter(pk=1).update(cut_over=True, mode=SyncMode.OFF)
    with pytest.raises(SyncRefused) as cut:
        _observe(source)
    assert cut.value.code == "CUT_OVER"
    assert source.calls == [] and not LovableObservationRun.objects.exists()
    with pytest.raises(SyncRefused):
        set_observe(True, reason="again")


def test_the_reading_links_the_nights_pilot_run_and_writes_nothing_of_the_pilots(observing, pilot):
    night = LovableSyncRun.objects.create(
        run_key="2026-11-02",
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.COMPARE,
        started_at=NIGHT,
        mapping_version="lovable-sync/1",
    )
    source = _register()
    before = _ledger_counts()
    states = list(LovableSyncState.objects.values())
    run = _observe(source)
    source.edit(5, teema="Muudetud")
    _observe(source, NIGHT + dt.timedelta(days=1))
    assert run.sync_run == night
    assert _ledger_counts() == before
    assert list(LovableSyncState.objects.values()) == states


def test_a_reading_prunes_nothing_retention_is_its_own_job(observing):
    """The observation run deletes nothing (one owner of retention:
    `prune_observations`); the independent job then prunes both old snapshots
    and the old change, and keeps the newest verified snapshot."""
    source = _register()
    old = _observe(source, NIGHT - dt.timedelta(days=45))
    source.edit(1, hetkeseis="Valitsuses")
    older_change_run = _observe(source, NIGHT - dt.timedelta(days=40))
    LovableObservedChange.objects.filter(run=older_change_run).update(
        created_at=NIGHT - dt.timedelta(days=40)
    )
    assert older_change_run.changes.count() == 1
    latest = _observe(source, NIGHT)
    assert "pruned" not in latest.counts
    assert LovableObservationSnapshot.objects.filter(run=old).exists()

    pruned = lovable_observation.prune_observations(now=NIGHT)
    assert pruned.status == RetentionRunStatus.SUCCEEDED and pruned.run_key == "prune:2026-11-02"
    assert set(LovableObservationSnapshot.objects.values_list("run", flat=True)) == {latest.pk}
    assert not LovableObservedChange.objects.filter(run=older_change_run).exists()
    assert LovableObservationRun.objects.count() == 3  # run rows and counts are kept
    assert pruned.counts == {"snapshots": 2, "changes": 1, "events": 0, "kept_baseline": False}


def _old_raw_data(source: FakeRegister) -> LovableObservationRun:
    """A verified reading 60 days ago, an older one with a change, and an
    expired source event: everything past the 30 days."""
    _observe(source, NIGHT - dt.timedelta(days=61))
    source.edit(1, hetkeseis="Valitsuses")
    newest = _observe(source, NIGHT - dt.timedelta(days=60))
    LovableObservedChange.objects.filter(run=newest).update(
        created_at=NIGHT - dt.timedelta(days=60)
    )
    LovableSourceEvent.objects.create(
        source_event_id=str(uuid.UUID(int=7)),
        topic_id=_id(1),
        action="muutmine",
        occurred_at=NIGHT - dt.timedelta(days=50),
        sha256="0" * 64,
    )
    return newest


def test_retention_runs_with_observation_off_and_every_recent_reading_failed(observing):
    source = _register()
    newest = _old_raw_data(source)
    source.fail = SourceError("UNREACHABLE", "Lovable could not be reached (TimeoutError).")
    for days in (3, 2, 1):
        failed = _observe(source, NIGHT - dt.timedelta(days=days))
        assert failed.status == ObservationRunStatus.FAILED
    set_observe(False, reason="paused")
    source.calls.clear()

    pruned = lovable_observation.prune_observations(now=NIGHT)
    assert pruned.status == RetentionRunStatus.SUCCEEDED, pruned.error
    assert source.calls == []  # retention never reads Lovable
    # The newest verified snapshot stays although it is 60 days old; nothing
    # else past the 30 days does.
    assert list(LovableObservationSnapshot.objects.values_list("run", flat=True)) == [newest.pk]
    assert not LovableObservedChange.objects.exists()
    assert not LovableSourceEvent.objects.exists()
    assert pruned.counts == {"snapshots": 1, "changes": 1, "events": 1, "kept_baseline": True}
    assert LovableObservationRun.objects.count() == 5


def test_pruning_never_removes_the_only_verified_snapshot(observing):
    source = _register()
    only = _observe(source, NIGHT - dt.timedelta(days=60))
    pruned = lovable_observation.prune_observations(now=NIGHT)
    assert pruned.counts["snapshots"] == 0 and pruned.counts["kept_baseline"] is True
    assert LovableObservationSnapshot.objects.filter(run=only).exists()


def test_a_second_pruning_of_the_night_is_refused_and_a_manual_one_is_not():
    first = lovable_observation.prune_observations(now=NIGHT)
    assert first.status == RetentionRunStatus.SUCCEEDED
    with pytest.raises(SyncRefused) as twice:
        lovable_observation.prune_observations(now=NIGHT + dt.timedelta(hours=1))
    assert twice.value.code == "DUPLICATE"
    manual = lovable_observation.prune_observations(trigger=SyncTrigger.MANUAL, now=NIGHT)
    assert manual.run_key.startswith("prune-manual:")
    assert LovableRetentionRun.objects.count() == 2


def test_a_failed_pruning_is_recorded_with_its_code(monkeypatch, observing):
    source = _register()
    _old_raw_data(source)

    def broken(_now):
        raise SourceError("DB_DOWN", "nope")

    monkeypatch.setattr(lovable_observation, "_prune", broken)
    run = lovable_observation.prune_observations(now=NIGHT)
    assert run.status == RetentionRunStatus.FAILED
    assert run.error == "DB_DOWN: SourceError" and run.finished_at is not None

    monkeypatch.setattr(lovable_observation, "_prune", lambda _now: 1 / 0)
    other = lovable_observation.prune_observations(
        trigger=SyncTrigger.MANUAL, now=NIGHT + dt.timedelta(minutes=1)
    )
    assert (
        other.status == RetentionRunStatus.FAILED and other.error == "INTERNAL: ZeroDivisionError"
    )
    assert LovableObservationSnapshot.objects.count() == 2  # nothing half-deleted


def test_the_prune_command_needs_no_credentials_and_refuses_a_second_night(capsys):
    from django.core.management import call_command

    call_command("lovable_sync", "prune-observations")
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "SUCCEEDED" and out["run_key"].startswith("prune:")
    with pytest.raises(SystemExit) as again:
        call_command("lovable_sync", "prune-observations")
    assert again.value.code == 2
    call_command("lovable_sync", "prune-observations", "--manual")
    assert json.loads(capsys.readouterr().out)["run_key"].startswith("prune-manual:")
    status = lovable_observation.observation_status()
    assert status["last_retention"]["status"] == "SUCCEEDED"


def test_observe_status_reads_without_creating_the_control_row():
    status = lovable_observation.observation_status()
    assert status["observe"] is False and status["last_run"] is None
    assert not LovableSyncControl.objects.exists()


@pytest.mark.skipif(
    not os.environ.get("LOVABLE_OBSERVE_PERF"), reason="measurement; set LOVABLE_OBSERVE_PERF=1"
)
def test_measure_a_register_of_2600_topics(observing, capsys):
    topics = [_topic(n, operational=n % 3 == 0) for n in range(2600)]
    source = FakeRegister(topics)
    source.children["topic_commencement"] = [
        {
            "id": f"c{n}",
            "topic_id": _id(n),
            "liik": "jõustumine",
            "kuupaev": "2027-01-01",
            "tapsustus": "",
            "position": 1,
        }
        for n in range(0, 2600, 2)
    ]
    source.children["topic_responsible_change"] = [
        {
            "id": f"r{n}",
            "topic_id": _id(n),
            "vana_vastutaja": "Mari",
            "uus_vastutaja": "Jaan",
            "alates": "2026-01-01",
        }
        for n in range(0, 2600, 5)
    ]
    tick = time.perf_counter()
    _observe(source)
    first_seconds = time.perf_counter() - tick
    for n in range(0, 2600, 50):
        source.edit(n, hetkeseis="Valitsuses")
    tick = time.perf_counter()
    second = _observe(source, NIGHT + dt.timedelta(days=1))
    second_seconds = time.perf_counter() - tick
    size = len(json.dumps(second.snapshot.document))
    with capsys.disabled():
        print(
            f"\n2600 topics: first {first_seconds:.2f}s, compared {second_seconds:.2f}s, "
            f"rows {second.counts['rows_read']}, requests {second.counts['requests']}, "
            f"bytes {second.counts['bytes']}, document {size} bytes, "
            f"detailed {second.counts['detailed']}, changes {second.changes.count()}"
        )
    assert second.counts["live"] == 2600 and second.changes.count() == 52


def test_the_command_switches_observation_and_runs_it(monkeypatch, settings, capsys):
    from django.core.management import call_command

    from app.legacy_import import lovable_source

    source = _register()
    settings.LOVABLE_SYNC_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    monkeypatch.setattr(lovable_source, "load_config", lambda _p=None: object())
    monkeypatch.setattr(lovable_source, "HttpTransport", lambda _config: source)

    with pytest.raises(SystemExit) as refused:
        call_command("lovable_sync", "observe", "--manual")
    assert refused.value.code == 2  # OBSERVE_OFF

    call_command("lovable_sync", "control", "--observe", "on", "--reason", "owner said so")
    control = LovableSyncControl.objects.get()
    assert control.observe and control.changed_reason == "owner said so"
    capsys.readouterr()
    call_command("lovable_sync", "observe", "--manual")
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "SUCCEEDED" and out["counts"]["live"] == 10
    assert "Näidisteema" not in json.dumps(out, ensure_ascii=False)

    call_command("lovable_sync", "observe-status", "--json")
    status = json.loads(capsys.readouterr().out)
    assert status["observe"] is True and status["last_success"]["status"] == "SUCCEEDED"
    call_command("lovable_sync", "control", "--observe", "off", "--reason", "pause")
    assert not LovableSyncControl.objects.get().observe
