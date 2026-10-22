"""`get_matter`, `get_matter_activity`, `get_matter_documents`, `get_upcoming_commencements`.

The snapshot labels every dated value for the kind of fact it is and names
what is missing instead of inventing it; WAIT, DO and MONITOR stay what they
are; a sent opinion and an official «no comments» reply stay apart. The
activity is the Teema käik chronology, filtered by event date. Commencements
follow the register's containment rule, and a date behind us is never
reported as having entered into force.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.utils import timezone

from app.intelligence.enums import EffectiveDateKind, FactStatus, ImportantDateKind
from app.matters.models import MatterProceduralDevelopment
from app.mcp_server import readers
from app.mcp_server.readers import InvalidArgument, NotFound
from app.submissions.enums import SubmissionKind
from app.submissions.services import attach_final_evidence, create_submission, mark_submission_sent
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import add_planned_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

ORIGIN = "https://juristid.example.test"


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN


@pytest.fixture
def today() -> date:
    return timezone.localdate()


@pytest.fixture
def matter(specialist, today):
    return factories.MatterFactory(
        owner=specialist,
        title="Pakendiseaduse muutmise eelnõu",
        received_date=today - timedelta(days=5),
        response_deadline=today + timedelta(days=10),
    )


def _send(matter: Any, actor: Any, *, kind: str, title: str) -> Any:
    ministry = factories.OrganisationFactory(name=f"Ministeerium {title}")
    submission = create_submission(
        matter=matter, title=title, kind=kind, actor=actor, recipients=[ministry]
    )
    attach_final_evidence(
        submission=submission,
        content=b"%PDF-1.4 arvamus",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


# ---------------------------------------------------------------------------
# get_matter
# ---------------------------------------------------------------------------


def test_a_matter_with_no_next_action_says_it_is_missing(matter, specialist, reader) -> None:
    for user in (specialist, reader):
        snapshot = readers.matter_snapshot(user, matter.display_reference)
        assert snapshot["current_next_action"] is None
        assert snapshot["waiting_for"] is None
        assert snapshot["planned_actions"] == []
        assert "current_next_action" in snapshot["missing"]
        assert "stage" in snapshot["missing"]
        assert snapshot["owner"] == specialist.display_name
        assert snapshot["url"] == f"{ORIGIN}/teemad/{matter.pk}/"
        assert snapshot["received"]["certainty"] == "confirmed"


def test_a_wait_is_what_we_are_waiting_for(matter, specialist, today) -> None:
    set_next_action(
        matter=matter,
        text="Ootame ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=7),
        actor=specialist,
    )
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))
    action = snapshot["current_next_action"]
    assert action["kind"] == "WAIT"
    assert action["date"]["semantics"] == "REVIEW_ON"
    assert action["date"]["certainty"] == "planned"
    assert action["is_overdue"] is False
    assert snapshot["waiting_for"] == "Ootame ministeeriumi vastust"
    assert "current_next_action" not in snapshot["missing"]


def test_a_do_with_a_passed_deadline_is_overdue_and_a_monitor_is_not_a_wait(
    matter, specialist, today
) -> None:
    set_next_action(
        matter=matter,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=2),
        actor=specialist,
    )
    action = readers.matter_snapshot(specialist, str(matter.pk))["current_next_action"]
    assert (action["kind"], action["is_overdue"]) == ("DO", True)

    set_next_action(
        matter=matter,
        text="Jälgime menetlust",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=date(today.year + 1, 3, 1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))
    action = snapshot["current_next_action"]
    assert action["kind"] == "MONITOR"
    assert action["date"]["certainty"] == "estimated"
    assert action["date"]["precision"] == "MONTH"
    assert snapshot["waiting_for"] is None


def test_planned_actions_are_planned(matter, specialist, today) -> None:
    set_next_action(matter=matter, text="Analüüsi eelnõu", actor=specialist)
    add_planned_action(
        matter=matter,
        text="Kohtumine ministeeriumiga",
        target_date=today + timedelta(days=20),
        actor=specialist,
    )
    planned = readers.matter_snapshot(specialist, str(matter.pk))["planned_actions"]
    assert [item["text"] for item in planned] == ["Kohtumine ministeeriumiga"]
    assert planned[0]["date"]["certainty"] == "planned"


def test_the_opinion_deadline_is_the_matters_own(matter, specialist, today) -> None:
    deadline = readers.matter_snapshot(specialist, str(matter.pk))["opinion_deadline"]
    assert deadline["date"] == (today + timedelta(days=10)).isoformat()
    assert deadline["certainty"] == "confirmed"
    assert deadline["still_owed"] is True
    assert deadline["days_remaining"] == 10

    other = factories.MatterFactory(owner=specialist)
    snapshot = readers.matter_snapshot(specialist, str(other.pk))
    assert snapshot["opinion_deadline"] is None
    assert "opinion_deadline" in snapshot["missing"]


def test_a_sent_opinion_and_a_no_comments_reply_are_counted_apart(matter, specialist) -> None:
    _send(matter, specialist, kind=SubmissionKind.FORMAL_OPINION, title="Arvamus")
    _send(matter, specialist, kind=SubmissionKind.NO_OBJECTION_RESPONSE, title="Märkusi pole")
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))
    kinds = {item["kind"]: item for item in snapshot["submissions_sent"]}
    assert kinds["FORMAL_OPINION"]["is_opinion"] is True
    assert kinds["NO_OBJECTION_RESPONSE"]["is_opinion"] is False
    assert kinds["NO_OBJECTION_RESPONSE"]["is_official_no_comments_reply"] is True
    assert kinds["FORMAL_OPINION"]["sent"]["certainty"] == "confirmed"
    assert snapshot["counts"]["opinions_sent"] == 1
    assert snapshot["counts"]["no_comments_replies_sent"] == 1
    text = readers.fetch_source(specialist, f"matter:{matter.pk}")["text"]
    assert "official no-comments reply, not an opinion" in text


def test_commencement_facts_are_labelled(matter, specialist, today) -> None:
    factories.ImportantDateFactory(
        matter=matter,
        title="Eelnõus kavandatud jõustumine",
        kind=ImportantDateKind.PLANNED_COMMENCEMENT,
        date_value=today + timedelta(days=40),
        period_end=today + timedelta(days=40),
    )
    factories.ImportantDateFactory(
        matter=matter,
        title="Allika hinnang",
        kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
        date_value=today + timedelta(days=60),
        period_end=today + timedelta(days=60),
    )
    factories.ImportantDateFactory(
        matter=matter,
        title="Tühistatud istung",
        status=FactStatus.CANCELLED,
        date_value=today + timedelta(days=5),
        period_end=today + timedelta(days=5),
    )
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=3),
        period_end=today + timedelta(days=3),
        description="Põhiosa",
    )
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today - timedelta(days=30),
        period_end=today - timedelta(days=30),
        description="Üleminekusäte",
    )
    factories.EffectiveDateFactory(
        matter=matter,
        kind=EffectiveDateKind.UNKNOWN,
        date_value=None,
        period_end=None,
        description="Teine osa",
    )
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))
    dates = {item["title"]: item["date"]["certainty"] for item in snapshot["upcoming_dates"]}
    assert dates == {
        "Eelnõus kavandatud jõustumine": "planned",
        "Allika hinnang": "estimated",
        "Tühistatud istung": "previous",
    }
    commencements = {item["what"]: item for item in snapshot["commencements"]}
    assert commencements["Põhiosa"]["date"]["certainty"] == "confirmed"
    assert commencements["Põhiosa"]["timing"] == "upcoming"
    assert commencements["Üleminekusäte"]["timing"] == "past"
    assert commencements["Teine osa"]["date"]["certainty"] == "missing"
    text = readers.fetch_source(specialist, f"matter:{matter.pk}")["text"]
    assert "entered into force" not in text


def test_the_snapshot_stays_within_its_query_budget(
    matter, specialist, today, django_assert_max_num_queries
) -> None:
    set_next_action(matter=matter, text="Analüüsi eelnõu", actor=specialist)
    _send(matter, specialist, kind=SubmissionKind.FORMAL_OPINION, title="Arvamus")
    factories.EffectiveDateFactory(matter=matter)
    factories.ImportantDateFactory(matter=matter)
    with django_assert_max_num_queries(40):
        readers.matter_snapshot(specialist, str(matter.pk))


# ---------------------------------------------------------------------------
# get_matter_activity
# ---------------------------------------------------------------------------


def _development(matter: Any, actor: Any, title: str, day: date) -> Any:
    return MatterProceduralDevelopment.objects.create(
        matter=matter, title=title, occurred_on=day, created_by=actor
    )


def test_activity_is_the_chronology_newest_first(matter, specialist, reader, today) -> None:
    _development(matter, specialist, "Eelnõu avalikustati", today - timedelta(days=30))
    _development(matter, specialist, "Valitsus kiitis heaks", today - timedelta(days=3))
    entry = factories.EntryFactory(
        matter=matter, author=specialist, body="<p>Rääkisime ministeeriumiga.</p>"
    )
    for user in (specialist, reader):
        answer = readers.matter_activity(user, matter.display_reference)
        texts = [item["text"] for item in answer["items"]]
        days = [item["date"]["date"] for item in answer["items"]]
        assert days == sorted(days, reverse=True)
        assert any("Rääkisime ministeeriumiga." in text for text in texts)
        assert any("Valitsus kiitis heaks" in text for text in texts)
        entry_item = next(item for item in answer["items"] if item["kind"] == "entry")
        assert entry_item["id"] == f"entry:{entry.pk}"
        assert entry_item["url"].endswith(f"#sissekanne-{entry.pk}")
        development = next(item for item in answer["items"] if item["kind"] == "development")
        assert development["id"].startswith("dev:")
        assert development["date"]["precision"] == "EXACT"


def test_activity_filters_by_event_date_and_kind(matter, specialist, today) -> None:
    _development(matter, specialist, "Vana areng", today - timedelta(days=60))
    _development(matter, specialist, "Uus areng", today - timedelta(days=2))
    factories.EntryFactory(matter=matter, author=specialist, body="<p>Märge.</p>")

    recent = readers.matter_activity(
        specialist, str(matter.pk), since=(today - timedelta(days=10)).isoformat()
    )
    assert all(
        item["date"]["date"] >= (today - timedelta(days=10)).isoformat() for item in recent["items"]
    )
    assert not any("Vana areng" in item["text"] for item in recent["items"])

    old = readers.matter_activity(
        specialist, str(matter.pk), until=(today - timedelta(days=30)).isoformat()
    )
    assert [item["text"] for item in old["items"] if "areng" in item["text"]] == [
        "Märge: Vana areng"
    ]

    entries = readers.matter_activity(specialist, str(matter.pk), kind="entries")
    assert {item["kind"] for item in entries["items"]} == {"entry"}


def test_activity_pages_by_twenty_five(matter, specialist, today) -> None:
    for index in range(30):
        _development(matter, specialist, f"Areng {index}", today - timedelta(days=index + 1))
    first = readers.matter_activity(specialist, str(matter.pk), kind="events")
    second = readers.matter_activity(specialist, str(matter.pk), kind="events", page=2)
    assert len(first["items"]) == 25 and first["has_more"] is True
    assert second["has_more"] is False
    seen = [
        item["text"] for item in first["items"] + second["items"] if item["kind"] == "development"
    ]
    assert len(seen) == len(set(seen)) == 30


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"since": "1.9.2026"}, "since must be an ISO date"),
        ({"until": "2026-13-01"}, "until must be an ISO date"),
        ({"since": "2026-10-10", "until": "2026-10-01"}, "since must not be after until"),
        ({"kind": "everything"}, "kind must be one of"),
        ({"page": 0}, "page must be between"),
    ],
)
def test_bad_activity_arguments_are_refused(matter, specialist, arguments, message) -> None:
    with pytest.raises(InvalidArgument, match=message):
        readers.matter_activity(specialist, str(matter.pk), **arguments)


def test_activity_stays_within_its_query_budget(
    matter, specialist, today, django_assert_max_num_queries
) -> None:
    for index in range(10):
        _development(matter, specialist, f"Areng {index}", today - timedelta(days=index + 1))
        factories.EntryFactory(matter=matter, author=specialist, body=f"<p>Märge {index}</p>")
    with django_assert_max_num_queries(40):
        readers.matter_activity(specialist, str(matter.pk))


# ---------------------------------------------------------------------------
# get_matter_documents
# ---------------------------------------------------------------------------


def test_documents_are_metadata_with_a_fetch_id_where_text_exists(
    matter, specialist, reader, capture_evidence
) -> None:
    from app.documents.derivatives import DocumentDerivative, DocumentTextFragment
    from app.documents.enums import DerivativeStatus

    with_text = capture_evidence(
        matter, b"%PDF-1.4 a", "seletuskiri.pdf", "application/pdf", title="Seletuskiri"
    )
    capture_evidence(matter, b"%PDF-1.4 b", "lisa.pdf", "application/pdf", title="Lisa")
    derivative = DocumentDerivative.objects.create(
        version=with_text,
        kind="EXTRACTED_TEXT",
        generator="test",
        generator_version="1",
        status=DerivativeStatus.ACTIVE,
    )
    DocumentTextFragment.objects.create(
        derivative=derivative, ordinal=1, text="Üks.", locator_label="lk 1"
    )
    for user in (specialist, reader):
        answer = readers.matter_documents(user, str(matter.pk))
        assert answer["total"] == 2
        rows = {row["title"]: row for row in answer["documents"]}
        assert rows["Seletuskiri"]["text_available"] is True
        assert rows["Seletuskiri"]["fragment_count"] == 1
        assert rows["Seletuskiri"]["fetch_id"] == f"doc:{with_text.document_id}"
        assert rows["Seletuskiri"]["original_filename"] == "seletuskiri.pdf"
        assert rows["Lisa"]["text_available"] is False
        assert rows["Lisa"]["fetch_id"] is None
        assert rows["Lisa"]["url"].startswith(f"{ORIGIN}/")
        assert "storage_key" not in str(rows)


def test_documents_stay_within_their_query_budget(
    matter, specialist, capture_evidence, django_assert_max_num_queries
) -> None:
    for index in range(12):
        capture_evidence(matter, f"%PDF-1.4 {index}".encode(), f"f{index}.pdf", "application/pdf")
    with django_assert_max_num_queries(40):
        readers.matter_documents(specialist, str(matter.pk))


# ---------------------------------------------------------------------------
# get_upcoming_commencements
# ---------------------------------------------------------------------------


def test_the_default_window_is_the_registers_next_thirty_days(matter, specialist, reader, today):
    soon = factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=5),
        period_end=today + timedelta(days=5),
        description="Põhiosa",
    )
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=45),
        period_end=today + timedelta(days=45),
        description="Hilisem osa",
    )
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today + timedelta(days=6),
        period_end=today + timedelta(days=6),
        status=FactStatus.CANCELLED,
        description="Tühistatud",
    )
    for user in (specialist, reader):
        answer = readers.upcoming_commencements(user)
        assert answer["from"] == today.isoformat()
        assert answer["to"] == (today + timedelta(days=29)).isoformat()
        assert [item["id"] for item in answer["commencements"]] == [f"eff:{soon.pk}"]
        row = answer["commencements"][0]
        assert row["date"]["certainty"] == "confirmed"
        assert row["matter"]["reference"] == matter.display_reference
        assert row["owner"] == matter.owner.display_name
        assert row["url"] == f"{ORIGIN}/teemad/{matter.pk}/"


def test_an_approximate_period_is_listed_only_when_wholly_inside(matter, specialist, today):
    first = date(today.year + 1, 3, 1)
    record = factories.EffectiveDateFactory(
        matter=matter,
        date_value=first,
        period_end=date(today.year + 1, 3, 31),
        date_precision=DatePrecision.MONTH,
        description="Märtsis",
    )
    inside = readers.upcoming_commencements(
        specialist, start=first.isoformat(), end=date(today.year + 1, 4, 30).isoformat()
    )
    assert [item["id"] for item in inside["commencements"]] == [f"eff:{record.pk}"]
    assert inside["commencements"][0]["date"]["certainty"] == "estimated"
    overlapping = readers.upcoming_commencements(
        specialist, start=first.isoformat(), end=date(today.year + 1, 3, 15).isoformat()
    )
    assert overlapping["commencements"] == []


def test_a_past_date_is_never_reported_as_in_force(matter, specialist, today) -> None:
    factories.EffectiveDateFactory(
        matter=matter,
        date_value=today - timedelta(days=10),
        period_end=today - timedelta(days=10),
        description="Põhiosa",
    )
    answer = readers.upcoming_commencements(
        specialist, start=(today - timedelta(days=20)).isoformat(), end=today.isoformat()
    )
    row = answer["commencements"][0]
    assert row["timing"] == "past"
    assert "entered into force" not in str(answer)


def test_planned_and_estimated_commencements_are_informational(matter, specialist, today):
    factories.ImportantDateFactory(
        matter=matter,
        title="Eelnõus kavandatud jõustumine",
        kind=ImportantDateKind.PLANNED_COMMENCEMENT,
        date_value=today + timedelta(days=3),
        period_end=today + timedelta(days=3),
    )
    factories.ImportantDateFactory(
        matter=matter,
        title="Allika hinnang",
        kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
        date_value=today + timedelta(days=4),
        period_end=today + timedelta(days=4),
    )
    factories.ImportantDateFactory(
        matter=matter, title="Komisjoni istung", date_value=today + timedelta(days=5)
    )
    answer = readers.upcoming_commencements(specialist)
    assert answer["commencements"] == []
    informational = {item["title"]: item for item in answer["informational"]}
    assert set(informational) == {"Eelnõus kavandatud jõustumine", "Allika hinnang"}
    assert informational["Eelnõus kavandatud jõustumine"]["date"]["certainty"] == "planned"
    assert informational["Allika hinnang"]["date"]["certainty"] == "estimated"
    assert "not recorded commencements" in answer["informational_note"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"start": "2026-01-01", "end": "2027-03-01"}, "at most 400 days"),
        ({"start": "2026-02-01", "end": "2026-01-01"}, "to must not be before from"),
        ({"start": "1.1.2026"}, "from must be an ISO date"),
        ({"page": -1}, "page must be between"),
    ],
)
def test_bad_commencement_windows_are_refused(specialist, arguments, message) -> None:
    with pytest.raises(InvalidArgument, match=message):
        readers.upcoming_commencements(specialist, **arguments)


def test_commencements_of_an_invisible_reader_are_not_found(matter, administrator) -> None:
    factories.EffectiveDateFactory(matter=matter)
    with pytest.raises(NotFound, match="No such record"):
        readers.upcoming_commencements(administrator)


def test_commencements_stay_within_their_query_budget(
    matter, specialist, today, django_assert_max_num_queries
) -> None:
    for index in range(10):
        other = factories.MatterFactory(owner=specialist)
        factories.EffectiveDateFactory(
            matter=other,
            date_value=today + timedelta(days=index),
            period_end=today + timedelta(days=index),
        )
    with django_assert_max_num_queries(40):
        readers.upcoming_commencements(specialist)
