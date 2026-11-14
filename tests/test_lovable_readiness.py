"""`lovable_observability.readiness`: what stands between today and APPLY (docs/adr/0201 §7).

A read-only assessment. The simulation runs the existing, pure
`lovable_sync.plan_matter` over the newest stored pilot snapshot; every write
path of the synchronisation is booby-trapped here, so a readiness answer that
reached one fails the test. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import import lovable_observability as obs
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_observation_models import AuditAccess, ObservedChangeKind
from app.legacy_import.lovable_sync_models import SyncMode
from tests import factories
from tests.lovable_observability_support import (
    Pilot,
    control,
    night_at,
    observation_run,
    observed,
    row_counts,
    write_statements,
)

pytestmark = pytest.mark.django_db

LATER = night_at(2) + dt.timedelta(hours=2)
WRITE_PATHS = (
    "apply_matter",
    "_apply_field",
    "_record",
    "_observe",
    "_advance",
    "run_sync",
    "resolve_field",
    "set_mode",
    "cut_over",
    "control",
    "ensure_states",
    "seed_state",
    "prune_snapshots",
    "sync_health",
    "due",
)


@pytest.fixture
def reader():
    return factories.UserFactory(display_name="Lugeja Näidis")


@pytest.fixture
def cases():
    """The real-world cases, re-told with invented values, after one compared night."""
    from app.matters.services import set_matter_title
    from app.workflow.models import NextAction
    from tests.source_ownership_support import divergence_from_before_the_lock

    control(SyncMode.COMPARE)
    pilot = Pilot()
    for number in (243, 220, 186, 213, 2, 244, 300, 301, 302):
        pilot.add(number, owner_target="?" if number == 301 else None)
    pilot.night(night_at(0))
    pilot.edit("2026_243", jargmiseks="Jälgida menetlust")
    pilot.edit("2026_220", jargmiseks="Oodata teist versiooni", tegevuse_tahtaeg="2026-12-04")
    NextAction.objects.filter(matter=pilot.matter("2026_186")).update(
        text="Kohtuda ministeeriumiga"
    )
    pilot.edit(
        "2026_186", tegevuse_tahtaeg="2026-11-28", onenote_link="https://onenote.example/uus"
    )
    pilot.edit(
        "2026_213",
        commencements=[
            {"liik": "Hinnanguline", "kuupaev": "2027-01-01", "tapsustus": "", "position": 1},
            {"liik": "Hinnanguline", "kuupaev": "2027-07-01", "tapsustus": "", "position": 2},
        ],
    )
    pilot.edit("2026_2", hetkeseis="Rohkem ei tegele")
    pilot.edit("2026_244", valja="Jah", valja_kuupaev="2026-10-30")
    pilot.edit("2026_300", teema="Uus pealkiri 2026_300")
    pilot.edit("2026_301", vastutaja="Jaan Näidis")
    del pilot.bundles["2026_302"]  # gone from the source
    pilot.night(night_at(1))
    # After the night the lawyer writes Lovable's new title: Juristid has converged.
    # A save from before the TL-01 lock: a person is refused it in COMPARE now (E9).
    with divergence_from_before_the_lock():
        set_matter_title(
            matter=pilot.matter("2026_300"), value="Uus pealkiri 2026_300", actor=pilot.owner
        )
    return pilot


@pytest.fixture
def no_write_paths(monkeypatch):
    def refuse(name):
        def trap(*args, **kwargs):
            raise AssertionError(f"readiness reached the write path lovable_sync.{name}")

        return trap

    for name in WRITE_PATHS:
        monkeypatch.setattr(lovable_sync, name, refuse(name))
    planned = []
    real = lovable_sync.plan_matter

    def spy(*args, **kwargs):
        planned.append(args[0].reference)
        assert kwargs.get("current") is not None  # B is read in bulk, never per Teema
        return real(*args, **kwargs)

    monkeypatch.setattr(lovable_sync, "plan_matter", spy)
    return planned


def _rows(readiness: obs.Readiness) -> dict[tuple[str, str], str]:
    return {(r["reference"], r["field"]): r["category"] for r in readiness.simulation_rows}


def test_readiness_plans_with_the_existing_rules_and_writes_nothing(cases, reader, no_write_paths):
    before = row_counts()
    with CaptureQueriesContext(connection) as queries:
        readiness = obs.readiness(reader, now=LATER)
    assert write_statements(queries.captured_queries) == []
    assert row_counts() == before
    assert sorted(no_write_paths) == sorted(cases.states)
    rows = _rows(readiness)
    # Source authority (docs/adr/0213 §10): every source change is a safe update
    # through its service; only what cannot be represented needs a person.
    safe = "SAFE_UPDATE_CANDIDATE"
    for key in (
        ("2026_243", "next_action_text"),
        ("2026_220", "next_action_text"),
        ("2026_220", "next_action_date"),
        ("2026_186", "next_action_text"),
        ("2026_186", "next_action_date"),
        ("2026_186", "onenote_link"),
        ("2026_2", "stage"),
        # The closure planned for 2026_2 ends its open step with it.
        ("2026_2", "next_action_text"),
        ("2026_2", "next_action_date"),
        ("2026_244", "sent"),
        ("2026_244", "sent_date"),
    ):
        assert rows[key] == safe, key
    assert rows[("2026_213", "commencements")] == "MANUAL_REVIEW"
    assert rows[("2026_300", "title")] == "CONVERGED"
    assert rows[("2026_301", "owner")] == "MANUAL_REVIEW"
    # Its unresolved owner's name is kept as the former owner (owner decision 14.11.2026).
    assert rows[("2026_301", "former_owner")] == safe
    assert rows[("2026_302", "*")] == "UNKNOWN_SOURCE"
    sim = readiness.simulation
    assert sim["SAFE_UPDATE_CANDIDATE"] == 14 and "CONFLICT" not in sim
    assert sim["UNKNOWN_SOURCE"] == 1 and sim["CONVERGED"] == 1
    assert sim["MANUAL_REVIEW"] == 2
    assert sum(sim.values()) == sum(
        1 for r in readiness.simulation_rows if r["field"] == "*"
    ) + sum(sum(f.simulated.values()) for f in readiness.fields)
    json.dumps(readiness.as_dict())


def test_simulation_never_changes_juristid(cases, reader):
    from app.submissions.models import Submission

    matter = cases.matter("2026_186")
    before = lovable_sync.current_values(matter)
    submissions = Submission.objects.count()
    obs.readiness(reader, now=LATER)
    assert lovable_sync.current_values(cases.matter("2026_186")) == before
    assert Submission.objects.count() == submissions
    assert cases.matter("2026_2").is_open


def test_the_field_table_says_why_a_field_is_held(cases, reader):
    fields = {f.field: f for f in obs.readiness(reader, now=LATER).fields}
    # Source authority (docs/adr/0213 §10): every source-owned field is written
    # through its service; only the operational scope has no place in Juristid.
    for name in lovable_sync.AUTHORITATIVE_FIELDS:
        assert fields[name].mapping == "EXACT" and fields[name].service, name
        assert fields[name].blocking_reason == "", name
    assert "Operatiivse ulatuse" in fields["operational_scope"].blocking_reason
    assert "dokumendita" in " ".join(fields["sent"].risks)
    assert "hinnanguks" in " ".join(fields["commencements"].risks)
    assert fields["commencements"].simulated == {"UNCHANGED": 7, "MANUAL_REVIEW": 1}
    dumped = json.dumps(obs.readiness(reader, now=LATER).as_dict(), ensure_ascii=False)
    assert "jõustunud" not in dumped


def test_blockers_and_owner_decisions(cases, reader):
    readiness = obs.readiness(reader, now=LATER)
    text = " ".join(readiness.blockers)
    assert "APPLY-režiimi kunagi lubanud" in text
    assert "access-sql" in text and "ainult GET" in text
    assert "muutmisajalugu pole kättesaadav" in text
    assert "etapisõnastiku vastendust" in text
    assert "Laiem vaatlus on välja lülitatud" in text
    # Under source authority a local edit and a source change is no conflict.
    assert "Lahendamata vastuolusid" not in text
    assert readiness.conflicts == 0
    decisions = " ".join(readiness.owner_decisions)
    for needle in ("sidumata", "30 päeva", "access-sql", "etapisõnastiku", "kogu registri"):
        assert needle in decisions


def test_coverage_has_numerators_and_denominators(cases, reader):
    factories.MatterFactory(reference_year=2026, reference_number=700)
    cases.outside("2026_700")  # operational, same number as a Juristid Teema
    cases.outside("2026_701")  # operational, nothing in Juristid
    cases.outside("2026_702", operational=False)
    cases.night(night_at(2))
    readiness = obs.readiness(reader, now=LATER + dt.timedelta(days=1))
    coverage = {i.key: i for i in readiness.coverage}
    assert (coverage["pilot_coverage"].value, coverage["pilot_coverage"].denominator) == (
        9,
        "piloodi 100 teemast",
    )
    # 302 is gone from the source, so 8 of the 10 operational topics are the pilot's.
    assert coverage["operational_source_coverage"].value == 8
    assert coverage["operational_source_coverage"].denominator == "10 operatiivsest teemast"
    assert coverage["owner_mapping"].value == 8  # «Jaan Näidis» is nobody
    assert coverage["owner_mapping"].denominator == "9 vastutajaga piloodi teemast"
    assert coverage["title_mapping"].value == 9
    assert coverage["stage_mapping"].value == 0
    assert coverage["commencement_mapping"].value == 0
    assert coverage["new_topic_mapping"].value == 0
    assert coverage["next_action_text_mapping"].value == 0
    assert coverage["outcome_conflict"].value == 0
    assert coverage["unresolved_conflicts"].value == 0
    for indicator in readiness.coverage:
        assert indicator.denominator and indicator.window
    identity = readiness.identity
    assert identity["index_from"] == "PILOT"
    assert identity["operational_reference_candidates"] == 1
    assert identity["operational_unmatched"] == 1
    assert identity["manual_confirmation"] == 1
    assert identity["pilot_missing_last_night"] == 1


def test_source_and_identity_from_the_wider_reading(cases, reader):
    control(SyncMode.COMPARE, observe=True)
    first = observation_run(night_at(0))
    run = observation_run(
        night_at(1),
        baseline=first,
        audit=AuditAccess.UNAVAILABLE,
        counts={"live": 400, "operational": 150, "detailed": 160},
    )
    observed(run, "2026_251", kind=ObservedChangeKind.RENUMBERED, previous_reference="2026_250")
    observed(run, "2026_252", kind=ObservedChangeKind.NEW, field_names=[])
    observed(run, "2026_253", fields=[("stage", "a", "b")])
    readiness = obs.readiness(reader, now=LATER)
    source = readiness.source
    assert source["pilot"]["snapshot_verified"] is True
    assert source["pilot"]["topics_in_snapshot"] == 8
    assert source["observation"]["index_only"] == 240
    assert source["observation"]["has_baseline"] is True
    assert source["audit"]["access"] == AuditAccess.UNAVAILABLE
    assert readiness.identity["renumbered"] == 1 and readiness.identity["new"] == 1
    stage = next(f for f in readiness.fields if f.field == "stage")
    assert stage.uncovered_changes == 1
    assert "Laiem vaatlus on välja lülitatud" not in " ".join(readiness.blockers)


def test_without_a_comparable_snapshot_nothing_is_simulated(reader):
    control(SyncMode.COMPARE)
    pilot = Pilot()
    pilot.add(501)
    pilot.night(night_at(0), fail="UNREACHABLE: x")
    readiness = obs.readiness(reader, now=LATER)
    assert readiness.simulation == {} and readiness.simulation_rows == []
    assert any("simulatsiooni ei tehtud" in b for b in readiness.blockers)
    assert any("ebaõnnestus (UNREACHABLE)" in b for b in readiness.blockers)


def test_a_reader_without_a_business_role_gets_counts_only(cases):
    admin = factories.AdministratorFactory()
    readiness = obs.readiness(admin, now=LATER)
    assert readiness.simulation_rows == []
    assert readiness.simulation["SAFE_UPDATE_CANDIDATE"] == 14  # 13 + 2026_301's former owner
    dumped = json.dumps(readiness.as_dict(), ensure_ascii=False)
    assert "Näidisteema" not in dumped and "Jälgida" not in dumped
