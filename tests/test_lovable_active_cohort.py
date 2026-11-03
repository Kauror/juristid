# ruff: noqa: F811 - pytest fixtures imported from the sync suite are used as arguments
"""The active Lovable register as a second reviewed cohort (docs/adr/0202 §6, §7).

The pilot cohort is the synthetic one of tests/test_lovable_sync.py (four
Teemad, `EXPECTED_COHORT` sized down to 4). The active cohort is three more
Teemad written by the same importer and then relabelled ``lovable-active`` —
exactly what the active importer writes (its own `EvidencePilotRecord.pilot`, its
own package digest, `field_decisions` with `HELD_FOR_REVIEW`). Every title,
name and link is invented; nothing reaches Lovable.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import urllib.parse
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.urls import reverse

from app.core.enums import Visibility
from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_review, lovable_source, lovable_sync
from app.legacy_import.lovable_observation import _Mapper
from app.legacy_import.lovable_observation_models import MappingStatus
from app.legacy_import.lovable_pilot import build_plan
from app.legacy_import.lovable_sync import ACTIVE_COHORT, PILOT_COHORT, SyncRefused
from app.legacy_import.lovable_sync_models import (
    LovableSyncCohort,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.matters.models import Matter
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories
from tests.lovable_observability_support import narrowed_reader
from tests.test_lovable_pilot import _apply, _package, pilot_on, world  # noqa: F401
from tests.test_lovable_source_security import ORIGIN, _Answer, _config, _jwt
from tests.test_lovable_sync import (
    NIGHT,
    FakeLovable,
    _decisions,
    _do_step,
    _matter,
    _row,
    _run,
    _source_topic,
    cohort,  # noqa: F401
)

pytestmark = pytest.mark.django_db

HELD = "HELD_FOR_REVIEW"
HOUR = dt.timedelta(hours=1)
DAY = dt.timedelta(days=1)
SUCCESSOR = "00000000-0000-0000-0000-000000000999"


def _active_topics() -> list[dict[str, Any]]:
    """510: stage and «Välja» held; 511: its next step held; 512: its title held."""
    return [
        _source_topic(
            "2026_510",
            _row("2026_510"),
            field_decisions={"hetkeseis": HELD, "valja": HELD, "jargmiseks": "APPLIED"},
        ),
        _source_topic(
            "2026_511",
            _row(
                "2026_511",
                vastutaja="Jaan",
                jargmiseks="Saadan märkused",
                tegevuse_tahtaeg="2026-11-20",
            ),
            owner_raw="Jaan",
            next_action=_do_step(),
            field_decisions={"jargmiseks": HELD},
        ),
        _source_topic("2026_512", _row("2026_512"), field_decisions={"teema": HELD}),
    ]


@pytest.fixture
def active(cohort):
    """The pilot of four, plus three Teemad imported as ``lovable-active``."""
    topics = _active_topics()
    package = _package(*topics)
    package["snapshot"]["snapshot_sha256"] = "d" * 64
    package["snapshot"]["highest_reference"] = {"2026": 520}
    digest = build_plan(package).digest
    _apply(package)
    relabelled = EvidencePilotRecord.objects.filter(package_sha256=digest).update(
        pilot=ACTIVE_COHORT
    )
    assert relabelled > 3
    assert (
        EvidencePilotRecord.objects.filter(
            pilot=ACTIVE_COHORT, package_sha256=digest, record_kind="MATTER"
        ).count()
        == 3
    )
    for topic in topics:
        cohort["source"].put(
            topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3
        )
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    return {**cohort, "digest": digest, "active_topics": topics}


def _enroll(active: dict[str, Any], **kw: Any) -> lovable_sync.Enrolment:
    arguments = {
        "cohort": ACTIVE_COHORT,
        "package_sha256": active["digest"],
        "expect": 3,
        "reason": "Aktiivne register, impordi kontroll läbitud",
        **kw,
    }
    return lovable_sync.enroll(**arguments)


def _state(reference: str) -> LovableSyncState:
    return LovableSyncState.objects.get(reference=reference)


def _pilot_dump() -> list[dict[str, Any]]:
    return list(
        LovableSyncState.objects.filter(cohort=PILOT_COHORT)
        .order_by("reference")
        .values(
            "pk",
            "reference",
            "cohort",
            "applied",
            "applied_version",
            "applied_snapshot_sha256",
            "observed",
            "observed_version",
            "observed_run",
            "updated_at",
        )
    )


# ---------------------------------------------------------------------------
# The pilot alone is unchanged
# ---------------------------------------------------------------------------


def test_an_imported_but_unenrolled_cohort_is_not_synchronised(active):
    run = _run(active)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["checked"] == 4
    assert set(run.counts["by_cohort"]) == {PILOT_COHORT}
    assert run.counts["by_cohort"][PILOT_COHORT]["expected"] == 4
    assert set(LovableSyncState.objects.values_list("cohort", flat=True)) == {PILOT_COHORT}
    # The pilot alone reads as it always has: no per-cohort lines.
    assert not any("kontrollitud" in line for line in lovable_sync.summary_lines(run))
    assert lovable_sync.expected_cohorts() == {PILOT_COHORT: 4}


def test_expected_cohorts_reads_only(active):
    from app.legacy_import.lovable_sync_models import LovableSyncControl

    LovableSyncControl.objects.all().delete()
    assert lovable_sync.expected_cohorts() == {PILOT_COHORT: 4}
    assert lovable_sync.expected_total() == 4
    assert not LovableSyncControl.objects.exists()


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


def test_enrolment_seeds_baselines_from_the_import_snapshot(active):
    # A lawyer's edit made before enrolment is a local change, never the baseline.
    Matter.all_objects.filter(pk=_matter("2026_510").pk).update(title="Juristi pealkiri")
    done = _enroll(active)
    assert (done.cohort, done.expected, done.seeded, done.already) == (ACTIVE_COHORT, 3, 3, False)
    # 510: stage + «Välja»; 511: the step's wording, so its day too; 512: the title.
    assert done.import_held_fields == 5
    row = LovableSyncCohort.objects.get()
    assert (row.key, row.pilot, row.package_sha256, row.expected) == (
        ACTIVE_COHORT,
        ACTIVE_COHORT,
        active["digest"],
        3,
    )
    state = _state("2026_510")
    assert state.cohort == ACTIVE_COHORT
    assert state.applied["title"] == {
        "source": "Näidisteema 2026_510",
        "target": "Näidisteema 2026_510",
    }
    assert state.applied["stage"]["import_held"] is True
    assert state.applied["sent"]["import_held"] is True
    assert "import_held" not in state.applied["next_action_text"]
    # The step the active import wrote, looked up under the cohort's own key.
    step = _state("2026_511").applied["next_action"]
    assert step["treatment"] == "SOURCE_NEXT_STEP"
    assert step["target"]["text"] == "Saadan märkused"

    run = _run(active)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["checked"] == 7
    assert _decisions(run, "2026_510")["title"].outcome == SyncOutcome.LOCAL_TEST_CHANGE


def test_enrolment_leaves_every_pilot_state_as_it_was(active):
    first = _run(active)
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    before = _pilot_dump()
    assert len(before) == 4 and {s["cohort"] for s in before} == {PILOT_COHORT}
    _enroll(active)
    assert _pilot_dump() == before
    assert LovableSyncState.objects.count() == 7


def test_the_same_enrolment_again_changes_nothing(active):
    _enroll(active)
    states = list(LovableSyncState.objects.order_by("pk").values("pk", "applied", "updated_at"))
    again = _enroll(active)
    assert again.already and again.seeded == 3
    assert list(LovableSyncState.objects.order_by("pk").values("pk", "applied", "updated_at")) == (
        states
    )
    assert LovableSyncCohort.objects.count() == 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"cohort": "lovable-somewhere"}, "UNKNOWN_COHORT"),
        ({"cohort": PILOT_COHORT}, "PILOT_BUILT_IN"),
        ({"expect": 4}, "COUNT_MISMATCH"),
        ({"package_sha256": "e" * 64}, "COUNT_MISMATCH"),
        ({"package_sha256": "not-a-digest"}, "BAD_DIGEST"),
        ({"reason": "  "}, "NO_REASON"),
    ],
)
def test_enrolment_refuses_a_wrong_request(active, change, code):
    with pytest.raises(SyncRefused) as refused:
        _enroll(active, **change)
    assert refused.value.code == code
    assert not LovableSyncCohort.objects.exists()
    assert not LovableSyncState.objects.exists()


def test_enrolment_refuses_after_cutover(active):
    lovable_sync.cut_over(reason="test")
    with pytest.raises(SyncRefused, match="Final cutover") as refused:
        _enroll(active)
    assert refused.value.code == "CUT_OVER"
    assert not LovableSyncCohort.objects.exists()


def test_enrolment_refuses_a_teema_without_exactly_one_canonical_source(active):
    reference = MatterSourceReference.objects.get(matter=_matter("2026_512"))
    reference.pk = None
    reference.id = None
    reference.source_snapshot_sha256 = "f" * 64
    reference.save()  # a second canonical Lovable reference for one Teema
    with pytest.raises(SyncRefused, match="not exactly one") as refused:
        _enroll(active)
    assert refused.value.code == "NO_CANONICAL_SOURCE"
    assert not LovableSyncState.objects.exists()


def test_enrolment_refuses_a_provenance_teema_with_no_lovable_source(active):
    stray = factories.MatterFactory(
        reference_year=2026, reference_number=519, title="Ilma allikata"
    )
    template = EvidencePilotRecord.objects.filter(
        pilot=ACTIVE_COHORT, package_sha256=active["digest"], record_kind="MATTER"
    ).first()
    assert template is not None
    EvidencePilotRecord.objects.create(
        pilot=ACTIVE_COHORT,
        pilot_version=template.pilot_version,
        package_sha256=active["digest"],
        record_key="2026_519",
        record_kind="MATTER",
        matter=stray,
        target_model="matters.Matter",
        target_id=stray.pk,
    )
    with pytest.raises(SyncRefused, match="not exactly one") as refused:
        _enroll(active, expect=4)
    assert refused.value.code == "NO_CANONICAL_SOURCE"
    assert not LovableSyncState.objects.exists()


def test_enrolment_never_enrols_a_source_twice(active):
    matter = _matter("2026_511")
    raw = MatterSourceReference.objects.get(matter=matter).source_row_raw
    LovableSyncState.objects.create(
        matter=matter,
        source_id=raw["lovable"]["id"],
        reference="2026_511",
        cohort=PILOT_COHORT,
        applied={},
    )
    with pytest.raises(SyncRefused, match="already synchronised") as refused:
        _enroll(active)
    assert refused.value.code == "ALREADY_SYNCHRONISED"
    assert not LovableSyncCohort.objects.exists()
    assert LovableSyncState.objects.count() == 1


def test_a_cohort_is_enrolled_once(active):
    _enroll(active)
    with pytest.raises(SyncRefused, match="already enrolled") as refused:
        _enroll(active, expect=2)
    assert refused.value.code == "COHORT_ENROLLED"
    assert LovableSyncCohort.objects.get().expected == 3


def test_a_native_teema_with_a_source_reference_number_is_never_enrolled(active):
    native = factories.MatterFactory(reference_year=2026, reference_number=513, title="Oma teema")
    native_id = "00000000-0000-0000-0000-000000000513"
    MatterSourceReference.objects.create(
        matter=native,
        import_batch=factories.ImportBatchFactory(source_system="LOVABLE"),
        source_system="LOVABLE",
        source_file_name="public.topic",
        source_snapshot_sha256="b" * 64,
        source_sheet="public.topic:2026",
        source_row_number=513,
        source_row_raw={"lovable": {"id": native_id}, "row": _row("2026_513")},
        match_method="REFERENCE_TOKEN",
    )
    active["source"].put(native_id, _row("2026_513"), version=1)
    _enroll(active)
    run = _run(active)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert not LovableSyncState.objects.filter(matter=native).exists()
    assert run.counts["checked"] == 7
    # Observation: the enrolled Teema is linked, the same-number one only a candidate.
    mapper = _Mapper()
    assert mapper(str(_state("2026_510").source_id), "2026_510") == (
        MappingStatus.PILOT_LINKED,
        _matter("2026_510").pk,
    )
    assert mapper(native_id, "2026_513") == (MappingStatus.REFERENCE_CANDIDATE, native.pk)


def test_the_enroll_command_prints_counts_only(active):
    out = io.StringIO()
    arguments = [
        "lovable_sync",
        "enroll",
        "--cohort",
        ACTIVE_COHORT,
        "--package-sha256",
        active["digest"],
        "--expect",
        "3",
        "--reason",
        "impordi kontroll läbitud",
    ]
    call_command(*arguments, stdout=out)
    assert out.getvalue().strip() == (
        "enrolled: cohort=lovable-active expected=3 seeded=3 import_held_fields=5"
    )
    again = io.StringIO()
    call_command(*arguments, stdout=again)
    assert again.getvalue().startswith("already enrolled: cohort=lovable-active")
    assert "2026_51" not in out.getvalue() + again.getvalue()
    err = io.StringIO()
    with pytest.raises(SystemExit) as stopped:
        call_command(*arguments[:-4], "--expect", "9", "--reason", "x", stderr=err)
    assert stopped.value.code == 2
    assert "REFUSED COHORT_ENROLLED" in err.getvalue()


# ---------------------------------------------------------------------------
# The run over two cohorts
# ---------------------------------------------------------------------------


def test_the_size_check_is_per_cohort(active):
    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    # The active cohort loses one Teema's provenance (and so its state).
    matter = _matter("2026_512")
    EvidencePilotRecord.objects.filter(matter=matter, record_kind="MATTER").delete()
    LovableSyncState.objects.filter(matter=matter).delete()
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith("COHORT_SIZE")
    assert "lovable-active holds 2 Teemad, not 3" in run.error


def test_import_held_fields_wait_every_night_and_only_a_resolution_clears_them(active):
    _enroll(active)
    first = _run(active)
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    held = {
        (d.state.reference, d.field)
        for d in first.decisions.filter(reason=lovable_sync.IMPORT_HELD)
    }
    assert held == {
        ("2026_510", "stage"),
        ("2026_510", "sent"),
        ("2026_511", "next_action_text"),
        ("2026_511", "next_action_date"),
        ("2026_512", "title"),
    }
    assert all(
        d.outcome == SyncOutcome.REVIEW_REQUIRED
        for d in first.decisions.filter(reason=lovable_sync.IMPORT_HELD)
    )
    part = first.counts["by_cohort"][ACTIVE_COHORT]
    assert (part["expected"], part["checked"], part["review"]) == (3, 3, 3)
    assert (part["import_held"], part["import_held_fields"]) == (3, 5)
    assert first.counts["by_cohort"][PILOT_COHORT]["import_held"] == 0
    assert first.counts["import_held"] == 3

    # Lovable and Juristid agree on 512's new title: still a person's call.
    active["source"].edit("2026_512", teema="Uus pealkiri 512")
    Matter.all_objects.filter(pk=_matter("2026_512").pk).update(title="Uus pealkiri 512")
    second = _run(active, at=NIGHT + HOUR)
    title = _decisions(second, "2026_512")["title"]
    assert (title.outcome, title.reason) == (SyncOutcome.REVIEW_REQUIRED, lovable_sync.IMPORT_HELD)
    assert _state("2026_512").applied["title"]["import_held"] is True

    lovable_sync.resolve_field(reference="2026_512", field_name="title", note="vaadatud")
    assert "import_held" not in _state("2026_512").applied["title"]
    third = _run(active, at=NIGHT + 2 * HOUR)
    assert "title" not in _decisions(third, "2026_512")
    # The step is one operation: held together, and resolved together.
    assert _state("2026_511").applied["next_action_date"]["import_held"] is True
    lovable_sync.resolve_field(reference="2026_511", field_name="next_action_text", note="ok")
    assert not lovable_sync.held_by_import(_state("2026_511").applied)
    fourth = _run(active, at=NIGHT + 3 * HOUR)
    assert not {"next_action_text", "next_action_date"} & set(_decisions(fourth, "2026_511"))


def test_an_import_held_step_is_never_applied(active):
    _enroll(active)
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    step = NextAction.objects.get(matter=_matter("2026_511"), status=ActionStatus.OPEN)
    active["source"].edit("2026_511", tegevuse_tahtaeg="2026-11-27")
    run = _run(active)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    date = _decisions(run, "2026_511")["next_action_date"]
    assert (date.outcome, date.reason) == (SyncOutcome.REVIEW_REQUIRED, lovable_sync.IMPORT_HELD)
    assert NextAction.objects.get(pk=step.pk).target_date == dt.date(2026, 11, 20)
    assert run.counts["updated"] == 0


class LinkedLovable(FakeLovable):
    """`FakeLovable` that also answers the stage-link table."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows
        self.links: list[tuple[str, str]] = []

    def select(
        self, table: str, columns: Any, *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        if table != "topic_stage_link":
            return super().select(table, columns, filters=filters, order=order)
        [(column, value)] = filters.items()
        wanted = set(value[4:-1].split(","))
        side = 0 if column == "prev_topic_id" else 1
        return [
            {"prev_topic_id": pair[0], "next_topic_id": pair[1]}
            for pair in self.links
            if pair[side] in wanted
        ]


def test_source_activity_is_lovables_own_ongoing_rule():
    me = "00000000-0000-0000-0000-000000000001"

    def bundle(links: list[dict[str, str]] | None = None, **row: Any) -> dict[str, Any]:
        base = {"id": me, "operatiivne_ulatus": True, "hetkeseis": "Riigikogus"}
        return {"row": {**base, **row}, "stage_links": links or []}

    activity = lovable_sync.source_activity
    assert activity(None) == lovable_sync.ACTIVITY_MISSING
    assert activity(bundle()) == lovable_sync.ACTIVITY_ONGOING
    assert activity(bundle(operatiivne_ulatus=False)) == lovable_sync.ACTIVITY_NOT_OPERATIONAL
    assert activity(bundle(hetkeseis="Jõustunud")) == lovable_sync.ACTIVITY_FINISHED
    assert activity(bundle(hetkeseis="rohkem ei tegele")) == lovable_sync.ACTIVITY_FINISHED
    assert activity(bundle([{"prev": me, "next": SUCCESSOR}])) == lovable_sync.ACTIVITY_CONTINUES
    # Continuing *from* another topic is current work.
    assert activity(bundle([{"prev": SUCCESSOR, "next": me}])) == lovable_sync.ACTIVITY_ONGOING


def test_open_teemad_whose_source_stopped_are_counted_not_acted_on(active):
    _enroll(active)
    source = LinkedLovable(active["source"].rows)
    active["source"] = source
    ids = {t["reference"]: t["source"]["id"] for t in active["active_topics"]}
    source.edit("2026_510", hetkeseis="jõustunud")
    source.links.append((ids["2026_511"], SUCCESSOR))
    source.edit("2026_512", operatiivne_ulatus=False)
    run = _run(active)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    part = run.counts["by_cohort"][ACTIVE_COHORT]
    assert part["activity"] == {
        "ONGOING": 0,
        "FINISHED": 1,
        "CONTINUES": 1,
        "NOT_OPERATIONAL": 1,
        "MISSING": 0,
    }
    assert part["stopped_active"] == 3
    pilot = run.counts["by_cohort"][PILOT_COHORT]
    # 2026_503 is «jõustunud» in Lovable and closed in Juristid: not stopped work.
    assert pilot["activity"]["FINISHED"] == 1 and pilot["stopped_active"] == 0
    assert run.counts["stopped_active"] == 3
    # Counts only: the held stage is still a person's call, every Teema stays open.
    assert all(_matter(r).is_open for r in ("2026_510", "2026_511", "2026_512"))
    stage = _decisions(run, "2026_510")["stage"]
    assert stage.outcome == SyncOutcome.REVIEW_REQUIRED


def _local_fields(reference: str) -> dict[str, Any]:
    matter = _matter(reference)
    return {
        "policy_areas": sorted(str(pk) for pk in matter.policy_areas.values_list("pk", flat=True)),
        "policy_area_other": matter.policy_area_other,
        "visibility": matter.visibility,
        "steps": sorted(
            str(pk) for pk in NextAction.objects.filter(matter=matter).values_list("pk", flat=True)
        ),
    }


def test_valdkond_and_local_fields_are_never_written(active):
    _enroll(active)
    matter = _matter("2026_510")
    matter.policy_areas.add(factories.PolicyAreaFactory())
    Matter.all_objects.filter(pk=matter.pk).update(policy_area_other="Kohalik valdkond")
    before = _local_fields("2026_510")
    active["source"].edit("2026_510", teema="Lovable'i uus pealkiri", hetkeseis="Valitsuses")

    compare = _run(active)
    assert compare.status == SyncRunStatus.SUCCEEDED, compare.error
    assert _local_fields("2026_510") == before
    assert _matter("2026_510").title == "Näidisteema 2026_510"

    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    applied = _run(active, at=NIGHT + HOUR)
    assert applied.status == SyncRunStatus.SUCCEEDED, applied.error
    assert _matter("2026_510").title == "Lovable'i uus pealkiri"  # on the allowlist
    stage = _decisions(applied, "2026_510")["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.REVIEW_REQUIRED, lovable_sync.IMPORT_HELD)
    assert _local_fields("2026_510") == before


def test_apply_on_the_new_cohort_obeys_the_allowlist_and_the_breaker(active, monkeypatch):
    _enroll(active)
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    active["source"].edit("2026_512", kellele="Ministeerium")
    held = _run(active)
    addressee = _decisions(held, "2026_512")["addressee"]
    assert (addressee.outcome, addressee.reason) == (SyncOutcome.REVIEW_REQUIRED, lovable_sync.HELD)

    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 1)
    active["source"].edit("2026_510", teema="Uus 510")
    active["source"].edit("2026_501", teema="Uus 501")
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.REVIEW_ONLY and run.error == lovable_sync.BREAKER
    assert _matter("2026_510").title == "Näidisteema 2026_510"
    assert _matter("2026_501").title == "Näidisteema 2026_501"


def test_reads_are_get_only_across_both_cohorts(active):
    _enroll(active)
    fake = active["source"]
    seen: list[str] = []

    def opener(request: Any, timeout: Any) -> Any:
        seen.append(request.get_method())
        url = request.full_url
        if "/auth/v1/token" in url:
            return _Answer({"access_token": _jwt()}, url=url)
        parts = urllib.parse.urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        query = dict(urllib.parse.parse_qsl(parts.query))
        columns = query.pop("select").split(",")
        order, offset = query.pop("order"), int(query.pop("offset"))
        limit = int(query.pop("limit"))
        rows = fake.select(table, columns, filters=query, order=order)
        return _Answer(rows[offset : offset + limit], url=url)

    transport = lovable_source.HttpTransport(_config(), opener=opener)
    run = lovable_sync.run_sync(
        trigger=SyncTrigger.MANUAL,
        transport=transport,
        backup_set=(NIGHT - dt.timedelta(minutes=5))
        .astimezone(dt.UTC)
        .strftime("sets/%Y%m%dT%H%M%SZ"),
        now=lambda: NIGHT,
        pause=lambda _s: None,
    )
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["checked"] == 7
    assert seen[0] == "POST" and set(seen[1:]) == {"GET"}
    assert ORIGIN.startswith("https://")


# ---------------------------------------------------------------------------
# Uuendused → Lovable, Haldus and the read service
# ---------------------------------------------------------------------------


def test_the_first_night_after_enrolment_shows_teemad_entering_not_changes(active):
    reader = factories.UserFactory(display_name="Lugeja Näidis")
    _run(active, SyncTrigger.SCHEDULED, at=NIGHT)
    _enroll(active)
    # A Lovable edit made after the import snapshot: the comparison starts here,
    # so it is the import baseline's business (a source change), not the night's.
    active["source"].edit("2026_510", teema="Muudetud enne esimest ööd")
    _run(active, SyncTrigger.SCHEDULED, at=NIGHT + DAY)
    history = lovable_review.nightly_history(reader, now=NIGHT + 3 * DAY)
    night = history.nights[0]
    entering = {t.reference: t for t in night.topics}
    assert set(entering) == {"2026_510", "2026_511", "2026_512"}
    assert {t.kind for t in entering.values()} == {lovable_review.ENROLLED}
    assert all(not t.fields for t in entering.values())
    assert (night.enrolled, night.changed_topics, night.attention, night.hidden) == (3, 0, 0, 0)
    assert night.message == lovable_review.NO_CHANGES

    # The read service's reading of that night (unwindowed: snapshots carry the
    # real clock's moment, the runs the test's).
    reading = obs._pilot_readings(reader, NIGHT + 3 * DAY, full=False)[0]
    assert reading.changed_topics == 0
    topic = next(t for t in reading.topics if t.reference == "2026_510")
    assert topic.kind == lovable_review.ENROLLED and topic.fields == []
    assert "lisandus võrdlusesse" in topic.summary
    # «Ainult tähelepanu vajavad» agrees with the night's count: entering is not attention.
    from app.core import views as core_views

    assert not any(core_views._topic_needs_attention(t) for t in reading.topics)
    [row] = core_views._night_rows([reading], attention=True)
    assert row["topics"] == []

    # The next night compares the new cohort like any other.
    active["source"].edit("2026_511", kellele="Ministeerium")
    _run(active, SyncTrigger.SCHEDULED, at=NIGHT + 2 * DAY)
    latest = lovable_review.nightly_history(reader, now=NIGHT + 3 * DAY).nights[0]
    assert [(t.reference, t.kind) for t in latest.topics] == [("2026_511", "CHANGED")]


def test_the_new_cohort_is_counted_not_named_for_a_reader_who_may_not_open_it(
    active, monkeypatch, client, settings
):
    _enroll(active)
    Matter.all_objects.filter(pk=_matter("2026_510").pk).update(
        visibility=Visibility.RESTRICTED, title="VARJATUD-PEALKIRI-510"
    )
    _run(active, SyncTrigger.SCHEDULED, at=NIGHT)
    active["source"].edit("2026_510", hetkeseis="SALAJANE-ETAPP-510")
    _run(active, SyncTrigger.SCHEDULED, at=NIGHT + DAY)
    later = NIGHT + 3 * DAY

    narrowed = narrowed_reader(monkeypatch)
    answers = [
        obs.status(narrowed, now=later),
        obs.review_queue(narrowed, now=later),
        obs.readiness(narrowed, now=later),
    ]
    readings = obs._pilot_readings(narrowed, later, full=True)
    dumped = "".join(json.dumps(a.as_dict(), default=str) for a in [*answers, *readings])
    assert "SALAJANE" not in dumped and "VARJATUD" not in dumped and "2026_510" not in dumped
    assert readings[0].hidden == 1
    assert answers[1].hidden >= 1
    assert obs.matter_changes(narrowed, "2026_510", now=later) is None
    assert lovable_review.nightly_history(narrowed, now=later).nights[0].hidden == 1
    status = answers[0].as_dict()
    assert status["cohorts"][ACTIVE_COHORT]["expected"] == 3
    assert status["cohorts"][ACTIVE_COHORT]["states"] == 3
    indicator = {i.key: i for i in answers[0].indicators}["cohort_lovable_active"]
    assert indicator.value == 3 and "ootele jäetud 3" in indicator.denominator
    assert {i.key: i for i in answers[0].indicators}["pilot_coverage"].denominator == (
        "7 sünkroonitavast teemast"
    )

    nobody = AnonymousUser()
    assert obs._pilot_readings(nobody, later, full=True) == []
    assert obs.review_queue(nobody, now=later).total == 0
    assert obs.matter_changes(nobody, "2026_511", now=later) is None

    # `lovable_sync report`: counts and codes per cohort, no reference or value.
    out = io.StringIO()
    call_command("lovable_sync", "report", "--json", stdout=out)
    report = json.loads(out.getvalue())
    assert report["status"]["cohorts"][ACTIVE_COHORT]["expected"] == 3
    assert "2026_51" not in out.getvalue() and "SALAJANE" not in out.getvalue()

    # Haldus: a technical administrator sees the cohorts' counts, never the content.
    from tests.admin_session import apply_admin_gate, technical_admin, verified

    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    admin, secret = technical_admin()
    verified(client, admin, secret)
    body = client.get(reverse("haldus:lovable")).content.decode()
    assert "Sünkroonitavad kohordid" in body and "Aktiivne register" in body
    assert "SALAJANE" not in body and "VARJATUD" not in body and "2026_510" not in body


# ---------------------------------------------------------------------------
# «Kontrolli muudatusi» between enrolment and the next night (review P3-8)
# ---------------------------------------------------------------------------


def test_a_check_before_the_first_night_skips_the_unread_cohort(active, monkeypatch):
    assert _run(active, SyncTrigger.SCHEDULED, at=NIGHT).status == SyncRunStatus.SUCCEEDED
    _enroll(active)
    # Three missing Teemad would stop the run; «not yet read» must not count.
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 1)
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + HOUR)
    assert check.status == SyncRunStatus.SUCCEEDED, check.error
    assert (check.counts["checked"], check.counts["missing"]) == (4, 0)
    assert check.counts["not_yet_read"] == 3
    assert check.counts["by_cohort"][ACTIVE_COHORT]["not_yet_read"] == 3
    assert check.counts["by_cohort"][ACTIVE_COHORT]["checked"] == 0
    assert not check.decisions.filter(state__cohort=ACTIVE_COHORT).exists()
    assert "Veel lugemata (lisati pärast viimast lugemist): 3" in lovable_sync.summary_lines(check)

    # The next run reads them fresh: nothing missing, the breaker stays quiet.
    run = _run(active, at=NIGHT + 2 * HOUR)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert (run.counts["checked"], run.counts["missing"], run.counts["not_yet_read"]) == (7, 0, 0)
    again = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + 3 * HOUR)
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert (again.counts["checked"], again.counts["not_yet_read"]) == (7, 0)


# ---------------------------------------------------------------------------
# The owner resolver never learns from an import-held owner (review P3-10)
# ---------------------------------------------------------------------------


def test_an_import_held_owner_is_not_learnt_as_a_name():
    def state(**owner: Any) -> LovableSyncState:
        return LovableSyncState(applied={"owner": owner})

    learnt = lovable_sync.OwnerResolver([state(source="Peeter", target="u-1")])
    assert learnt.pairs == {"peeter": {"u-1"}}
    held = lovable_sync.OwnerResolver([state(source="Peeter", target="u-1", import_held=True)])
    assert held.pairs == {}
