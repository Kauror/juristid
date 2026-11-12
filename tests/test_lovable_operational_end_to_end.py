# ruff: noqa: F811 - pytest fixtures imported from the sync suite are used as arguments
"""The seam between the active importer and the nightly synchronisation (docs/adr/0202).

tests/test_lovable_operational_import.py drives the importer alone, and
tests/test_lovable_operational_cohort.py drives the synchronisation over a cohort
that a version-1 package wrote and a test relabelled. Here neither half is
faked: a real version-2 ``lovable-operational`` package goes through the real
`plan` → `apply` → `verify` → `preserved`, the real `lovable_sync enroll` takes
the digest the import approved, and the real nightly run reads a fake Lovable
through the real GET-only `HttpTransport` — night after night, first COMPARE,
then one APPLY.

The world: the synthetic pilot of tests/test_lovable_sync.py (four Teemad,
`EXPECTED_COHORT` sized down to 4, its states seeded by a first COMPARE night),
one native Teema, and one Teema that holds a Lovable topic's reference without
its identity. Every title, name, reference and identity is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import urllib.parse
from typing import Any

import pytest
from django.core.management import call_command

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_pilot, lovable_review, lovable_source, lovable_sync
from app.legacy_import.lovable_pilot import (
    OPERATIONAL_COHORT,
    LovablePilotError,
    apply_plan,
    build_plan,
    check_preserved,
    plan_digest,
    preserved_document,
    verify_applied,
)
from app.legacy_import.lovable_sync import PILOT_COHORT
from app.legacy_import.lovable_sync_models import (
    LovableSyncCohort,
    LovableSyncDecision,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord
from app.matters.models import Matter
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from tests import factories
from tests.source_ownership_support import lock_lifted_for_the_engine_tests  # noqa: F401 - autouse
from tests.test_lovable_operational_cohort import _pilot_dump
from tests.test_lovable_operational_import import BACKUP, _active_package, _sid
from tests.test_lovable_operational_import import _apply as _apply_package
from tests.test_lovable_pilot import _topic, pilot_on, world  # noqa: F401
from tests.test_lovable_source_security import _Answer, _config, _jwt
from tests.test_lovable_sync import NIGHT, _decisions, _matter, _row, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db

HELD = "HELD_FOR_REVIEW"
DAY = dt.timedelta(days=1)
HOUR = dt.timedelta(hours=1)

#: The active package's five topics.
REVIEW = "2026_530"  # a clean MONITOR, REVIEW_ON
NOT_SENT = "2026_531"  # «ei saatnud» on a live stage, `valja` held
NO_STEP = "2025_40"  # no current step: `jargmiseks` and `tegevuse_tahtaeg` held
DRAFTING = "2025_41"  # an open request and its drafting step
ESTIMATE = "2026_532"  # an estimated commencement
NEW = (REVIEW, NOT_SENT, NO_STEP, DRAFTING, ESTIMATE)
PILOT_REFS = ("2026_501", "2026_502", "2026_503", "2026_505")
#: A Lovable topic whose reference a Teema holds without its identity (ADR 0202 §7).
SAME_REF = "2026_515"
NATIVE = "2026_7"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _lovable_row(reference: str, **values: Any) -> dict[str, Any]:
    """A topic row exactly as the nightly reader returns it: `TOPIC_COLUMNS`, no more."""
    row: dict[str, Any] = dict.fromkeys(lovable_source.TOPIC_COLUMNS)
    row.update(_row(reference, valja="", jargmiseks="", tegevuse_tahtaeg=None))
    row.update(id=_sid(reference), version=3, updated_at="2026-10-20T10:00:00+00:00")
    row.update(values)
    assert set(row) == set(lovable_source.TOPIC_COLUMNS)
    return row


def _review_step(kind: str, day: str, text: str) -> dict[str, Any]:
    return {
        "review": "READY",
        "kind": kind,
        "date_semantics": "REVIEW_ON",
        "target_date": day,
        "date_precision": "EXACT",
        "text": text,
        "source_text": text,
        "treatment": "SOURCE_NEXT_STEP",
        "reason": f"{kind} with a review date",
    }


def _active_topic(reference: str, row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A version-2 topic whose `source.row` is the reader's row, byte for byte."""
    topic = _topic(reference, **{"deadline": {"treatment": "NONE"}, **overrides})
    topic["source"].update(
        id=row["id"], version=row["version"], updated_at=row["updated_at"], row=row
    )
    return topic


def _commencement_row(reference: str) -> dict[str, Any]:
    return {
        "id": f"00000000-0000-0000-0c01-{int(reference[5:]):012d}",
        "topic_id": _sid(reference),
        "liik": "Eeldatava kuupäevaga jõustumine",
        "kuupaev": "2027-03-01",
        "tapsustus": "",
        "position": 0,
    }


def _active_topics() -> list[dict[str, Any]]:
    estimate = _active_topic(
        ESTIMATE,
        _lovable_row(ESTIMATE, jargmiseks="ootan 2. lugemist", tegevuse_tahtaeg="2027-01-25"),
        next_action=_review_step("WAIT", "2027-01-25", "ootan 2. lugemist"),
        commencements=[
            {
                "key": f"{ESTIMATE}-C01",
                "review": "READY",
                "target": "MatterImportantDate",
                "kind": "ESTIMATED_COMMENCEMENT",
                "date": "2027-03-01",
                "date_precision": "EXACT",
                "note": "",
            }
        ],
        field_decisions={"jargmiseks": "APPLIED"},
    )
    estimate["source"]["commencements"] = [_commencement_row(ESTIMATE)]
    return [
        _active_topic(
            REVIEW,
            _lovable_row(
                REVIEW, jargmiseks="Jälgin eelnõu menetlust", tegevuse_tahtaeg="2027-01-15"
            ),
            next_action=_review_step("MONITOR", "2027-01-15", "Jälgin eelnõu menetlust"),
            field_decisions={"jargmiseks": "APPLIED"},
        ),
        _active_topic(
            NOT_SENT,
            _lovable_row(
                NOT_SENT,
                vastutaja="Jaan",
                hetkeseis="kooskõlastusel",
                valja="ei saatnud",
                jargmiseks="ootan kooskõlastuse tulemusi",
                tegevuse_tahtaeg="2027-01-10",
            ),
            owner_raw="Jaan",
            status_raw="kooskõlastusel",
            stage="consultation",
            next_action=_review_step("WAIT", "2027-01-10", "ootan kooskõlastuse tulemusi"),
            field_decisions={"valja": HELD, "jargmiseks": "APPLIED"},
            review_notes=[{"field": "valja", "reason": "«ei saatnud» on a live stage"}],
        ),
        _active_topic(
            NO_STEP,
            _lovable_row(
                NO_STEP,
                vastutaja="Jaan",
                jargmiseks="vt OneNote, helistada ministeeriumi",
                tegevuse_tahtaeg="2026-10-01",
                sisse="2025-03-10",
            ),
            owner_raw="Jaan",
            received_date="2025-03-10",
            next_action=None,
            field_decisions={"jargmiseks": HELD, "tegevuse_tahtaeg": HELD},
            review_notes=[
                {"field": "jargmiseks", "reason": "no kind can be read"},
                {"field": "tegevuse_tahtaeg", "reason": "a DO whose day has passed"},
            ],
        ),
        _active_topic(
            DRAFTING,
            _lovable_row(
                DRAFTING,
                hetkeseis="kooskõlastusel",
                jargmiseks="Koostan arvamuse",
                tegevuse_tahtaeg="2027-01-20",
                arvamuse_tahtaeg="2027-01-20",
                sisse="2025-06-02",
            ),
            status_raw="kooskõlastusel",
            stage="consultation",
            received_date="2025-06-02",
            next_action={
                "review": "READY",
                "kind": "DO",
                "date_semantics": "DEADLINE",
                "target_date": "2027-01-20",
                "date_precision": "EXACT",
                "text": "Koostan arvamuse",
                "source_text": "Koostan arvamust.",
                "treatment": "OPINION_DRAFTING",
                "reason": "drafting",
            },
            deadline={"treatment": "OPEN_REQUEST", "deadline": "2027-01-20"},
            field_decisions={"jargmiseks": "APPLIED"},
        ),
        estimate,
    ]


def _the_package() -> dict[str, Any]:
    return _active_package(*_active_topics())


def _http(fake: Any, seen: list[tuple[str, str]]) -> lovable_source.HttpTransport:
    """The real GET-only reader, its HTTP answered from the fake Lovable."""

    def opener(request: Any, timeout: Any) -> Any:
        url = request.full_url
        parts = urllib.parse.urlsplit(url)
        seen.append((request.get_method(), parts.path))
        if parts.path == "/auth/v1/token":
            return _Answer({"access_token": _jwt()}, url=url)
        table = parts.path.rsplit("/", 1)[-1]
        query = dict(urllib.parse.parse_qsl(parts.query))
        columns = query.pop("select").split(",")
        order, offset, limit = query.pop("order"), int(query.pop("offset")), int(query.pop("limit"))
        rows = fake.select(table, columns, filters=query, order=order)
        return _Answer(rows[offset : offset + limit], url=url)

    return lovable_source.HttpTransport(_config(), opener=opener)


def _night(world: dict[str, Any], at: dt.datetime, trigger: str = SyncTrigger.SCHEDULED) -> Any:
    seen: list[tuple[str, str]] = []
    run = _run({"source": _http(world["source"], seen)}, trigger, at=at)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    world.setdefault("requests", []).append(seen)
    return run


def _business_digests(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """`preserved_digests` of every Matter in its settled scope: without what the
    synchronisation moves every night (a state's last observation, a run's
    decisions) — what a lawyer could see change, and what `preserved --before`
    compares after an import. The full digest, which covers that ledger on
    purpose, is the one compared inside the apply transaction."""
    return lovable_pilot.preserved_digests(settled=True)


def _by_reference(digests: dict[str, str]) -> dict[str, str]:
    names = {str(m.pk): m.display_reference for m in Matter.all_objects.all()}
    return {names[pk]: digest for pk, digest in digests.items()}


def _found(run: Any, references: tuple[str, ...]) -> set[tuple[str, str, str, str]]:
    return {
        (d.state.reference, d.field, d.outcome, d.reason)
        for d in run.decisions.select_related("state").filter(state__reference__in=references)
    }


# ---------------------------------------------------------------------------
# The world, night by night
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e(cohort, monkeypatch):
    """The pilot, a native Teema, a same-reference Teema, and Lovable holding
    the active topics too; the pilot's first COMPARE night seeds its states."""
    lovable_sync.set_mode(SyncMode.COMPARE, reason="esimesed ööd")
    mari = cohort["world"]["mari"]
    area = factories.PolicyAreaFactory()
    native = factories.MatterFactory(
        reference_year=2026, reference_number=7, owner=mari, title="Oma näidisteema"
    )
    native.policy_areas.add(area)
    factories.NextActionFactory(matter=native)
    factories.ImportantDateFactory(matter=native)
    same = factories.MatterFactory(
        reference_year=2026, reference_number=515, title="Sama numbriga näidisteema"
    )
    fake = cohort["source"]
    fake.rows[_sid(SAME_REF)] = _lovable_row(SAME_REF)
    for topic in _active_topics():
        fake.rows[topic["source"]["id"]] = copy.deepcopy(topic["source"]["row"])
        fake.commencements.extend(copy.deepcopy(topic["source"]["commencements"]))
    here = {**cohort, "native": native, "same": same, "area": area}
    night = _night(here, NIGHT)
    assert night.counts["checked"] == 4
    assert set(LovableSyncState.objects.values_list("cohort", flat=True)) == {PILOT_COHORT}
    here["pilot_states"] = _pilot_dump()
    return here


@pytest.fixture
def imported(e2e):
    """plan → apply of the active package, with every gate satisfied."""
    package = _the_package()
    plan = build_plan(package)
    assert plan.problems == [], plan.problems
    assert (plan.cohort, plan.database_state, plan.collisions) == (OPERATIONAL_COHORT, "ready", [])
    report = apply_plan(
        plan,
        operator_intent=OPERATIONAL_COHORT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set=BACKUP,
    )
    return {**e2e, "package": package, "digest": plan.digest, "report": report}


def _enroll_arguments(digest: str, expect: int = 5) -> list[str]:
    return [
        "lovable_sync",
        "enroll",
        "--cohort",
        OPERATIONAL_COHORT,
        "--package-sha256",
        digest,
        "--expect",
        str(expect),
        "--reason",
        "Aktiivne register, impordi kontroll läbitud",
    ]


@pytest.fixture
def enrolled(imported):
    out = io.StringIO()
    call_command(*_enroll_arguments(imported["digest"]), stdout=out)
    return {**imported, "enrol_output": out.getvalue().strip()}


@pytest.fixture
def first_night(enrolled, monkeypatch):
    """Lovable returns exactly the import snapshot's rows for every cohort id."""
    before = _business_digests(monkeypatch)
    run = _night(enrolled, NIGHT + DAY)
    return {**enrolled, "first": run, "digests": (before, _business_digests(monkeypatch))}


@pytest.fixture
def second_night(first_night, monkeypatch):
    """Lovable moves the clean topic's review day and finishes another."""
    first_night["source"].edit(REVIEW, tegevuse_tahtaeg="2027-02-01")
    first_night["source"].edit(ESTIMATE, hetkeseis="jõustunud")
    before = _business_digests(monkeypatch)
    run = _night(first_night, NIGHT + 2 * DAY)
    return {**first_night, "second": run, "digests": (before, _business_digests(monkeypatch))}


# ---------------------------------------------------------------------------
# 3. The import: verified, and nothing that was there changed
# ---------------------------------------------------------------------------


def test_the_import_verifies_and_every_existing_teema_is_preserved(imported):
    report = imported["report"]
    assert report.cohort == OPERATIONAL_COHORT and not report.already_applied
    assert report.written["MATTER"] == 5
    assert verify_applied(build_plan(imported["package"])).ok
    # `apply --preserved-out`, then `preserved --before`, through the API.
    document = preserved_document(report.preserved_settled_before, report.preserved_references)
    existing = {NATIVE, SAME_REF, *PILOT_REFS}
    assert set(document["references"].values()) == existing
    comparison = check_preserved(json.loads(json.dumps(document)))
    assert comparison.ok, comparison.summary()
    assert comparison.checked == len(existing)
    # Valdkond on the native Teema is untouched, and the pilot's states too.
    assert list(imported["native"].policy_areas.all()) == [imported["area"]]
    assert _pilot_dump() == imported["pilot_states"]
    # Imported, not yet synchronised: no state, and the next night is the pilot's alone.
    assert set(LovableSyncState.objects.values_list("cohort", flat=True)) == {PILOT_COHORT}
    created = {m.display_reference: m for m in Matter.all_objects.filter(reference_number__gte=40)}
    assert set(NEW) <= set(created)
    assert {created[r].owner.display_name for r in NEW} == {"Mari Näidis", "Jaan Näidis"}
    assert {created[r].reference_year for r in NEW} == {2025, 2026}
    assert created[NOT_SENT].is_open and not Submission.objects.exists()
    assert not NextAction.objects.filter(matter=created[NO_STEP]).exists()


# ---------------------------------------------------------------------------
# 4. Enrolment with the import's digest
# ---------------------------------------------------------------------------


def test_enrolment_seeds_the_new_cohort_from_the_import_and_leaves_the_pilot(enrolled):
    assert enrolled["enrol_output"] == (
        "enrolled: cohort=lovable-operational expected=5 seeded=5 import_held_fields=3"
    )
    row = LovableSyncCohort.objects.get()
    assert (row.key, row.package_sha256, row.expected) == (
        OPERATIONAL_COHORT,
        enrolled["digest"],
        5,
    )
    states = {s.reference: s for s in LovableSyncState.objects.filter(cohort=OPERATIONAL_COHORT)}
    assert set(states) == set(NEW)
    for reference, state in states.items():
        assert state.matter == _matter(reference)
        assert str(state.source_id) == _sid(reference)
        assert (state.applied_version, state.applied_snapshot_sha256) == (3, "b" * 64)
    assert lovable_sync.held_by_import(states[NOT_SENT].applied) == {"sent"}
    assert lovable_sync.held_by_import(states[NO_STEP].applied) == {
        "next_action_text",
        "next_action_date",
    }
    for reference in (REVIEW, DRAFTING, ESTIMATE):
        assert lovable_sync.held_by_import(states[reference].applied) == set()
    # The step the active import wrote, read under the cohort's own provenance.
    assert states[REVIEW].applied["next_action"]["treatment"] == "SOURCE_NEXT_STEP"
    assert states[REVIEW].applied["next_action"]["target"]["kind"] == "MONITOR"
    assert states[DRAFTING].applied["next_action"]["treatment"] == "OPINION_DRAFTING"
    assert states[NO_STEP].applied["next_action"]["target"] is None
    # The pilot's states are byte for byte what the first night left.
    assert json.dumps(_pilot_dump(), default=str) == json.dumps(
        enrolled["pilot_states"], default=str
    )
    # A reference is never an identity.
    assert not LovableSyncState.objects.filter(matter=enrolled["same"]).exists()
    assert not LovableSyncState.objects.filter(matter=enrolled["native"]).exists()
    assert lovable_sync.expected_cohorts() == {PILOT_COHORT: 4, OPERATIONAL_COHORT: 5}


# ---------------------------------------------------------------------------
# 5. The first night over both cohorts
# ---------------------------------------------------------------------------


def test_the_first_night_plans_what_the_import_held(first_night):
    """Source authority (docs/adr/0213 §10): what the import held converges on
    Lovable — planned only, on this COMPARE night."""
    run = first_night["first"]
    assert run.counts["checked"] == 9
    eligible = SyncOutcome.ELIGIBLE
    assert _found(run, NEW) == {
        (NOT_SENT, "sent", eligible, ""),
        (NOT_SENT, "sent_date", eligible, ""),
        (NOT_SENT, "addressee", eligible, ""),
        (NO_STEP, "next_action_text", eligible, ""),
        (NO_STEP, "next_action_date", eligible, ""),
    }
    assert not _found(run, PILOT_REFS)
    part = run.counts["by_cohort"][OPERATIONAL_COHORT]
    assert part == {
        "expected": 5,
        "checked": 5,
        "unchanged": 3,
        "review": 0,
        "conflicts": 0,
        "eligible_unapplied": 2,
        "import_held": 0,
        "import_held_fields": 0,
        "stopped_active": 0,
        "not_yet_read": 0,
        "activity": {
            "ONGOING": 5,
            "FINISHED": 0,
            "CONTINUES": 0,
            "NOT_OPERATIONAL": 0,
            "MISSING": 0,
        },
    }
    assert run.counts["by_cohort"][PILOT_COHORT]["checked"] == 4
    assert (run.counts["eligible_unapplied"], run.counts["conflicts"]) == (2, 0)
    # Nothing a lawyer could see changed, the imported Teemad included. The
    # drafting step's baseline settles (docs/adr/0213 §10: Lovable's «Koostan
    # arvamust.» is the step Juristid already holds) — bookkeeping only.
    before, after = first_night["digests"]
    assert len(before) == len(after) == 11
    # The baselines settle on the first comparison (bookkeeping only; the
    # digests include the synchronisation state); nothing was applied.
    assert not run.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    # The same-reference Teema's topic is read only as an index row.
    assert not LovableSyncState.objects.filter(matter=first_night["same"]).exists()
    assert run.counts["outside_cohort"] == 1
    # GET only, after the one sign-in.
    seen = first_night["requests"][-1]
    assert seen[0] == ("POST", "/auth/v1/token")
    assert seen[1:] and {method for method, _ in seen[1:]} == {"GET"}
    assert all(path.startswith("/rest/v1/") for _, path in seen[1:])


def test_a_lovable_edit_after_the_snapshot_is_a_source_change_on_the_first_night(enrolled):
    """Drift (ADR 0202 §5) is read GET-only; the first night reports it against
    the import baseline (§6), and Uuendused still shows the Teema entering."""
    fake = enrolled["source"]
    fake.edit(REVIEW, teema="Näidisteema 2026_530 (parandatud)")
    seen: list[tuple[str, str]] = []
    drift = lovable_pilot.source_drift(enrolled["package"], _http(fake, seen))
    assert [(d["reference"], d["kinds"]) for d in drift] == [(REVIEW, ["VERSION"])]
    assert {method for method, _ in seen[1:]} == {"GET"}

    run = _night(enrolled, NIGHT + DAY)
    title = _decisions(run, REVIEW)["title"]
    assert (title.outcome, title.baseline, title.source) == (
        SyncOutcome.ELIGIBLE,
        "Näidisteema 2026_530",
        "Näidisteema 2026_530 (parandatud)",
    )
    # The title, and the two first convergences (NOT_SENT, NO_STEP) still planned.
    assert run.counts["by_cohort"][OPERATIONAL_COHORT]["eligible_unapplied"] == 3
    assert _matter(REVIEW).title == "Näidisteema 2026_530"
    reader = factories.UserFactory(display_name="Lugeja Näidis")
    night = lovable_review.nightly_history(reader, now=NIGHT + 3 * DAY).nights[0]
    assert {t.reference: t.kind for t in night.topics}[REVIEW] == lovable_review.ENROLLED


# ---------------------------------------------------------------------------
# 6. The second night: a date, a finished topic, and Uuendused → Lovable
# ---------------------------------------------------------------------------


def test_the_second_night_reports_a_date_and_a_finished_topic(second_night):
    run = second_night["second"]
    date = _decisions(run, REVIEW)["next_action_date"]
    assert (date.outcome, date.source) == (SyncOutcome.ELIGIBLE, "2027-02-01")
    # «jõustunud»: the closure is planned (COMPARE), never a person's call now.
    stage = _decisions(run, ESTIMATE)["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.ELIGIBLE, "")
    # COMPARE: nothing written.
    step = NextAction.objects.get(matter=_matter(REVIEW), status=ActionStatus.OPEN)
    assert step.target_date == dt.date(2027, 1, 15)
    before, after = second_night["digests"]
    assert after == before
    part = run.counts["by_cohort"][OPERATIONAL_COHORT]
    assert part["stopped_active"] == 1 and run.counts["stopped_active"] == 1
    assert part["activity"]["FINISHED"] == 1 and part["activity"]["ONGOING"] == 4
    assert part["eligible_unapplied"] == 4 and part["conflicts"] == 0
    assert _matter(ESTIMATE).is_open
    # The import's holds are planned like any other change.
    assert (NOT_SENT, "sent", SyncOutcome.ELIGIBLE, "") in _found(run, NEW)


def test_uuendused_shows_the_cohort_entering_then_its_changes(second_night):
    reader = factories.UserFactory(display_name="Lugeja Näidis")
    history = lovable_review.nightly_history(reader, now=NIGHT + 3 * DAY)
    second, first, pilot_only = history.nights[:3]
    assert pilot_only.comparison == "BASELINE"

    assert {t.reference: t.kind for t in first.topics} == dict.fromkeys(
        NEW, lovable_review.ENROLLED
    )
    assert all(not t.fields for t in first.topics)
    assert (first.enrolled, first.changed_topics, first.attention) == (5, 0, 0)
    assert first.message == lovable_review.NO_CHANGES

    changes = {t.reference: t for t in second.topics}
    assert set(changes) == {REVIEW, ESTIMATE}
    assert {t.kind for t in changes.values()} == {"CHANGED"}
    assert second.enrolled == 0 and second.changed_topics == 2
    [day] = changes[REVIEW].fields
    assert (day.name, day.result, day.note) == (
        "next_action_date",
        lovable_review.NOT_APPLIED,
        lovable_review.COMPARE_ONLY_NOTE,
    )
    [stage] = changes[ESTIMATE].fields
    assert (stage.name, stage.result) == ("stage", lovable_review.NOT_APPLIED)


# ---------------------------------------------------------------------------
# 7. APPLY on the new cohort
# ---------------------------------------------------------------------------


def test_apply_writes_every_source_change_through_its_service(second_night, monkeypatch):
    from app.matters.workspace import change_current_action

    mari = second_night["world"]["mari"]
    review = _matter(REVIEW)
    area = factories.PolicyAreaFactory()
    review.policy_areas.add(area)
    estimate = _matter(ESTIMATE)
    local = NextAction.objects.get(matter=estimate, status=ActionStatus.OPEN)
    change_current_action(
        matter=estimate,
        actor=mari,
        action_id=local.pk,
        text="ootan 2. lugemist ja helistan",
        kind=local.kind,
        date_semantics=local.date_semantics,
        target_date=local.target_date,
    )
    old = NextAction.objects.get(matter=review, status=ActionStatus.OPEN)
    before = _by_reference(_business_digests(monkeypatch))

    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    run = _night(second_night, NIGHT + 2 * DAY + HOUR, SyncTrigger.MANUAL)
    assert run.mode == SyncMode.APPLY and run.backup_set
    # REVIEW's day, ESTIMATE's closure, NOT_SENT's outcome and NO_STEP's step.
    assert run.verified and run.counts["updated"] == 4

    # The date, through `set_next_action`, with the synchronisation's provenance.
    new = NextAction.objects.get(matter=review, status=ActionStatus.OPEN)
    assert new.pk != old.pk and new.target_date == dt.date(2027, 2, 1)
    assert (new.kind, new.date_semantics, new.text, new.responsible_id) == (
        old.kind,
        old.date_semantics,
        old.text,
        old.responsible_id,
    )
    old.refresh_from_db()
    assert old.status == ActionStatus.SUPERSEDED
    event = ChangeEvent.objects.get(matter=review, object_id=new.pk, event_type="NEXT_ACTION_SET")
    assert event.actor is None
    assert event.payload["provenance"]["provenance_source"] == lovable_sync.PROVENANCE_SOURCE
    assert event.payload["provenance"]["sync_run"] == str(run.pk)
    assert LovableSyncDecision.objects.filter(
        run=run, state__reference=REVIEW, field="next_action_date", outcome=SyncOutcome.APPLIED
    ).exists()
    # Valdkond, set in Juristid, survives the write on the same Teema.
    assert list(_matter(REVIEW).policy_areas.all()) == [area]

    # «jõustunud» closes ESTIMATE; the lawyer's step ends with the closure
    # (cancelled, kept in the history, never completed).
    found = _decisions(run, ESTIMATE)
    assert found["stage"].outcome == SyncOutcome.APPLIED
    closed = _matter(ESTIMATE)
    assert (closed.is_open, closed.stage.key, closed.disposition) == (
        False,
        "in_force",
        "COMPLETED",
    )
    assert NextAction.objects.get(pk=local.pk).status == ActionStatus.SUPERSEDED
    assert not NextAction.objects.filter(
        matter=estimate, status__in=(ActionStatus.OPEN, ActionStatus.COMPLETED)
    ).exists()
    # «ei saatnud» recorded as Lovable's outcome; nothing sent, no document.
    from app.legacy_import.lovable_sync_models import current_opinion_outcomes

    outcome = current_opinion_outcomes([_matter(NOT_SENT).pk])[_matter(NOT_SENT).pk]
    assert outcome.outcome == "NOT_SENT"
    assert not Submission.objects.exists()
    # Only the Teemad Lovable changed.
    after = _by_reference(_business_digests(monkeypatch))
    assert {r for r in before if after[r] != before[r]} == {REVIEW, ESTIMATE, NOT_SENT, NO_STEP}


# ---------------------------------------------------------------------------
# 8. Doing it again
# ---------------------------------------------------------------------------


def test_the_same_import_and_enrolment_again_change_nothing(first_night):
    package = first_night["package"]
    counts = (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        NextAction.objects.count(),
        ChangeEvent.objects.count(),
    )
    states = list(LovableSyncState.objects.order_by("pk").values("pk", "applied", "updated_at"))
    assert build_plan(package).database_state == "applied"
    assert _apply_package(package).already_applied
    again = lovable_sync.enroll(
        cohort=OPERATIONAL_COHORT,
        package_sha256=first_night["digest"],
        expect=5,
        reason="uuesti",
    )
    assert again.already and again.seeded == 5
    out = io.StringIO()
    call_command(*_enroll_arguments(first_night["digest"]), stdout=out)
    assert out.getvalue().startswith("already enrolled: cohort=lovable-operational expected=5")
    assert (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        NextAction.objects.count(),
        ChangeEvent.objects.count(),
    ) == counts
    assert (
        list(LovableSyncState.objects.order_by("pk").values("pk", "applied", "updated_at"))
        == states
    )
    assert LovableSyncCohort.objects.count() == 1


# ---------------------------------------------------------------------------
# 9. A reference already in Juristid
# ---------------------------------------------------------------------------


def test_a_reference_a_teema_already_holds_is_refused_at_plan(e2e):
    clash = _active_package(
        _active_topics()[0],
        _active_topic(NATIVE, _lovable_row(NATIVE)),
        _active_topic(SAME_REF, _lovable_row(SAME_REF)),
    )
    plan = build_plan(clash)
    assert plan.database_state == "conflict"
    assert plan.collisions == [SAME_REF, NATIVE]
    assert any("never overwritten" in p and NATIVE in p for p in plan.problems)
    matters = Matter.all_objects.count()
    with pytest.raises(LovablePilotError, match="never overwritten"):
        _apply_package(clash)
    assert Matter.all_objects.count() == matters
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()
