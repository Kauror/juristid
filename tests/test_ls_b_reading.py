"""The machine reader (`app.legacy_import.lovable_reading`), on synthetic topics.

Every title, name and link is invented. Each test pins one reading rule of the
module docstring: no step from free text, «ei saatnud» never sent, a past
deadline never sent, estimates stay estimates, stage only through the explicit
mapping, terminal / continuing / historical topics created closed — and every
reading is a topic `lovable_pilot.validate_package` accepts.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.legacy_import import lovable_reading
from app.legacy_import.lovable_pilot import (
    HISTORY_COHORT,
    OPERATIONAL_COHORT,
    build_plan,
    validate_package,
)
from app.legacy_import.lovable_reading import (
    READER_VERSION,
    STAGE_MAPPING,
    TERMINAL_STAGES,
    ReadingRefused,
    package_for,
    read_topic,
)
from app.workflow.models import StageVocabulary
from app.workflow.services import OPINION_PREPARATION_TEXT
from tests import factories

AS_OF = dt.date(2026, 11, 9)
PEOPLE = {"Mari": "Mari Näidis"}
URL = "https://example.invalid/sites/naidis/_layouts/Doc.aspx?sourcedoc={CCCC}"


def bundle(reference: str = "2026_701", **values: Any) -> dict[str, Any]:
    number = int(reference.split("_")[1])
    row = {
        "id": f"00000000-0000-0000-0000-{number:012d}",
        "nr": reference,
        "aasta": int(reference[:4]),
        "teema": f"Näidisteema {reference}",
        "vastutaja": "Mari",
        "uus_vastutaja": "",
        "hetkeseis": "Riigikogus",
        "jargmiseks": "",
        "tegevuse_tahtaeg": None,
        "arvamuse_tahtaeg": None,
        "valja": "",
        "valja_kuupaev": None,
        "kellele": "",
        "koja_ettepanek": False,
        "oigusakt": "",
        "sisse": "2026-11-02",
        "onenote_link": URL,
        "operatiivne_ulatus": True,
        "version": 1,
        "updated_at": "2026-11-05T10:00:00+00:00",
    }
    commencements = values.pop("commencements", [])
    stage_links = values.pop("stage_links", [])
    row.update(values)
    return {
        "row": row,
        "commencements": commencements,
        "responsible_changes": [],
        "stage_links": stage_links,
    }


def _valid(topic: dict[str, Any]) -> None:
    cohort = HISTORY_COHORT if topic["reading"]["historical"] else OPERATIONAL_COHORT
    package = package_for(
        [topic], as_of=AS_OF, snapshot_sha256="b" * 64, people=PEOPLE, cohort=cohort
    )
    report = validate_package(package)
    assert report.problems == []


def test_a_plain_open_topic_is_open_with_no_step_and_validates():
    topic = read_topic(bundle(), as_of=AS_OF, people=PEOPLE, historical=False)
    assert (topic["action"], topic["lifecycle"], topic["disposition"]) == ("CREATE", "ACTIVE", "")
    assert topic["stage"] == "parliament" and topic["next_action"] is None
    assert topic["reading"] == {
        "reader_version": READER_VERSION,
        "as_of": "2026-11-09",
        "historical": False,
    }
    assert topic["onenote_url"] == URL
    _valid(topic)


@pytest.mark.parametrize(
    "wording",
    [
        "Koostan arvamuse",
        "ootan Komisjoni ettepanekut",
        "jälgin menetlust",
        "Valitsuse heakskiidu ootel",
    ],
)
def test_free_text_never_becomes_a_step_and_is_held_with_its_day(wording):
    topic = read_topic(
        bundle(jargmiseks=wording, tegevuse_tahtaeg="2026-12-01"),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert topic["next_action"] is None
    assert topic["field_decisions"]["jargmiseks"] == "HELD_FOR_REVIEW"
    assert topic["field_decisions"]["tegevuse_tahtaeg"] == "HELD_FOR_REVIEW"
    assert any(note.startswith(lovable_reading.HELD_STEP) for note in topic["review_notes"])
    _valid(topic)


def test_an_open_request_with_nothing_sent_is_drafted_from_structured_fields_only():
    topic = read_topic(
        bundle(arvamuse_tahtaeg="2026-11-30", jargmiseks="vaatan üle"),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert topic["deadline"] == {"treatment": "OPEN_REQUEST", "deadline": "2026-11-30"}
    step = topic["next_action"]
    assert (step["kind"], step["date_semantics"], step["target_date"]) == (
        "DO",
        "DEADLINE",
        "2026-11-30",
    )
    assert step["text"] == OPINION_PREPARATION_TEXT and step["treatment"] == "OPINION_DRAFTING"
    # The free text is kept as source, never read as the step.
    assert topic["field_decisions"]["jargmiseks"] == "PRESERVED_AS_SOURCE_ONLY"
    _valid(topic)


def test_ei_saatnud_on_a_live_stage_is_held_never_sent_never_concluded():
    topic = read_topic(
        bundle(valja="ei saatnud.", arvamuse_tahtaeg="2026-11-30"),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert topic["lifecycle"] == "ACTIVE"
    assert topic["field_decisions"]["valja"] == "HELD_FOR_REVIEW"
    assert topic["deadline"]["treatment"] == "SOURCE_ONLY"
    assert topic["deadline"]["reason"] == "NOT_SENT"
    assert topic["next_action"] is None
    _valid(topic)


def test_a_passed_deadline_with_nothing_sent_is_held_never_sent_never_overdue():
    topic = read_topic(
        bundle(arvamuse_tahtaeg="2026-10-01"), as_of=AS_OF, people=PEOPLE, historical=False
    )
    assert topic["deadline"] == {
        "treatment": "SOURCE_ONLY",
        "deadline": "2026-10-01",
        "reason": "PASSED_NOT_SENT",
    }
    assert topic["field_decisions"]["arvamuse_tahtaeg"] == "HELD_FOR_REVIEW"
    assert topic["next_action"] is None
    _valid(topic)


def test_a_sent_date_is_never_an_open_request():
    topic = read_topic(
        bundle(arvamuse_tahtaeg="2026-11-30", valja_kuupaev="2026-11-05"),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert topic["deadline"]["treatment"] == "SOURCE_ONLY"
    assert topic["deadline"]["reason"] == "SENT"
    _valid(topic)


def test_a_chamber_initiative_takes_no_new_request():
    topic = read_topic(
        bundle(arvamuse_tahtaeg="2026-11-30", koja_ettepanek=True),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert topic["deadline"]["reason"] == "KODA_INITIATIVE" and topic["next_action"] is None
    _valid(topic)


def test_estimates_stay_estimates_and_a_fixed_day_is_known_only_once_adopted():
    commencements = [
        {"id": "c1", "liik": "Üldises korras", "kuupaev": None, "tapsustus": "", "position": 1},
        {"id": "c2", "liik": "Eeldatava kuupäevaga", "kuupaev": "2027-01-01", "position": 2},
        {"id": "c3", "liik": "Kindla kuupäevaga", "kuupaev": "2027-07-01", "position": 3},
        {"id": "c4", "liik": "Midagi muud", "kuupaev": None, "position": 4},
    ]
    live = read_topic(
        bundle(commencements=commencements), as_of=AS_OF, people=PEOPLE, historical=False
    )
    kinds = [(c["review"], c.get("kind")) for c in live["commencements"]]
    assert kinds == [
        ("READY", "GENERAL_ORDER"),
        ("READY", "ESTIMATED_COMMENCEMENT"),
        ("READY", "PLANNED_COMMENCEMENT"),
        ("SOURCE_ONLY", None),
    ]
    assert live["field_decisions"]["joustumised"] == "HELD_FOR_REVIEW"
    _valid(live)
    adopted = read_topic(
        bundle(commencements=commencements[2:3], hetkeseis="jõustumise ootel"),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert adopted["commencements"][0]["kind"] == "KNOWN_DATE"
    _valid(adopted)


def test_stage_comes_only_from_the_explicit_mapping():
    assert "rohkem pole tegevusi plaanis" not in STAGE_MAPPING
    assert TERMINAL_STAGES == {"in_force", "monitoring_stopped"}
    for label in ("riigikogus", "Riigikogus menetluses", "lõpetatud"):
        topic = read_topic(bundle(hetkeseis=label), as_of=AS_OF, people=PEOPLE, historical=False)
        assert topic["stage"] == ""
        assert topic["field_decisions"]["hetkeseis"] == "HELD_FOR_REVIEW"
        _valid(topic)


@pytest.mark.django_db
def test_every_mapped_stage_exists_in_the_vocabulary():
    keys = set(StageVocabulary.objects.values_list("key", flat=True))
    assert set(STAGE_MAPPING.values()) <= keys


@pytest.mark.parametrize(
    ("values", "lifecycle", "disposition"),
    [
        ({"hetkeseis": "jõustunud"}, "INACTIVE", "COMPLETED"),
        ({"hetkeseis": "rohkem ei tegele"}, "CONCLUDED", "MONITORING_STOPPED"),
        # «ei saatnud» is never the reason: the act in force is (LS2-A).
        ({"hetkeseis": "jõustunud", "valja": "ei saatnud"}, "INACTIVE", "COMPLETED"),
    ],
)
def test_a_terminal_topic_is_created_closed_with_nothing_current(values, lifecycle, disposition):
    topic = read_topic(
        bundle(
            arvamuse_tahtaeg="2026-12-01",
            jargmiseks="ootan",
            tegevuse_tahtaeg="2026-12-01",
            commencements=[{"id": "c1", "liik": "Üldises korras", "position": 1}],
            **values,
        ),
        as_of=AS_OF,
        people={},
        historical=False,
    )
    assert (topic["lifecycle"], topic["disposition"]) == (lifecycle, disposition)
    assert topic["next_action"] is None
    assert topic["deadline"]["treatment"] == "SOURCE_ONLY"
    assert all(c["review"] == "SOURCE_ONLY" for c in topic["commencements"])
    _valid(topic)


def test_a_predecessor_continues_and_links_only_a_named_partner():
    me = "00000000-0000-0000-0000-000000000701"
    nxt = "00000000-0000-0000-0000-000000000702"
    linked = bundle(stage_links=[{"prev": me, "next": nxt}])
    unlinked = read_topic(linked, as_of=AS_OF, people={}, historical=False)
    assert (unlinked["lifecycle"], unlinked["disposition"]) == ("CONTINUES", "SUPERSEDED")
    assert unlinked["successor"] == ""
    _valid(unlinked)
    named = read_topic(
        {**linked, "references": {nxt: "2026_702"}}, as_of=AS_OF, people={}, historical=False
    )
    assert named["successor"] == "2026_702" and named["source"]["successor_id"] == nxt
    _valid(named)


def test_historical_is_always_closed_with_no_open_step_or_request():
    topic = read_topic(
        bundle(operatiivne_ulatus=False, arvamuse_tahtaeg="2026-12-01", jargmiseks="Koostan"),
        as_of=AS_OF,
        people={},
        historical=True,
    )
    # Outside Lovable's operational scope is not a reason: none is invented.
    assert (topic["lifecycle"], topic["disposition"]) == ("INACTIVE", "")
    assert topic["closure_basis"] == "UNKNOWN"
    assert topic["next_action"] is None and topic["deadline"]["treatment"] != "OPEN_REQUEST"
    assert topic["reading"]["historical"] is True
    _valid(topic)
    ongoing = read_topic(bundle(), as_of=AS_OF, people=PEOPLE, historical=True)
    assert ongoing["lifecycle"] == "INACTIVE" and ongoing["next_action"] is None


def test_the_source_copy_carries_only_allowlisted_columns():
    raw = bundle(markmed="salajane märkus", mitteametlik_arvamus="x", allikas="y")
    topic = read_topic(raw, as_of=AS_OF, people=PEOPLE, historical=False)
    assert not {"markmed", "mitteametlik_arvamus", "allikas"} & set(topic["source"]["row"])
    _valid(topic)


def test_a_received_date_after_the_reading_is_held():
    topic = read_topic(bundle(sisse="2026-12-24"), as_of=AS_OF, people=PEOPLE, historical=False)
    assert topic["received_date"] is None
    assert topic["field_decisions"]["sisse"] == "HELD_FOR_REVIEW"
    _valid(topic)


def test_an_http_link_is_held_not_linked():
    topic = read_topic(
        bundle(onenote_link="http://example.invalid/x"),
        as_of=AS_OF,
        people=PEOPLE,
        historical=False,
    )
    assert (
        topic["onenote_url"] == "" and topic["field_decisions"]["onenote_link"] == "HELD_FOR_REVIEW"
    )
    _valid(topic)


@pytest.mark.parametrize(
    ("values", "people", "code"),
    [
        ({}, {}, "OWNER_UNRESOLVED"),
        ({"teema": "  "}, PEOPLE, "NO_TITLE"),
        ({"nr": "2026_07"}, PEOPLE, "BAD_REFERENCE"),
        ({"operatiivne_ulatus": False}, PEOPLE, "NOT_OPERATIONAL"),
    ],
)
def test_what_cannot_be_written_honestly_is_refused(values, people, code):
    with pytest.raises(ReadingRefused) as error:
        read_topic(bundle(**values), as_of=AS_OF, people=people, historical=False)
    assert error.value.code == code


@pytest.mark.django_db
def test_the_read_package_plans_ready_against_the_database():
    factories.UserFactory(display_name="Mari Näidis")
    topics = [
        read_topic(bundle("2026_711"), as_of=AS_OF, people=PEOPLE, historical=False),
        read_topic(
            bundle("2026_712", arvamuse_tahtaeg="2099-01-01"),
            as_of=AS_OF,
            people=PEOPLE,
            historical=False,
        ),
        read_topic(
            bundle("2026_713", hetkeseis="jõustunud"), as_of=AS_OF, people={}, historical=False
        ),
    ]
    plan = build_plan(
        package_for(
            topics,
            as_of=AS_OF,
            snapshot_sha256="c" * 64,
            people=PEOPLE,
            cohort=OPERATIONAL_COHORT,
        )
    )
    assert plan.problems == [] and plan.database_state == "ready"
