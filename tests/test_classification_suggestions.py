"""Proposed senders and Valdkonnad, confirmed by a person (ADR 0215, phase 3).

A proposal changes nothing on its own; a confirmation adds to the Teema's own
fields through the audited setters; Statistika's «Valdkonna järgi» and «Saatja
järgi» read only those confirmed fields.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.legacy_import.lovable_sync_models import OpinionOutcome
from app.organisations.models import OrganisationType
from app.reporting import classification
from app.reporting.models import ClassificationSuggestion, SuggestionStatus
from app.taxonomy.models import PolicyArea
from tests import factories
from tests.test_statistika_dashboard import _imported

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 16)
_REAL_LOCALDATE = timezone.localdate


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    def frozen(value=None, timezone=None):
        return TODAY if value is None else _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)


@pytest.fixture
def taxes(db):
    return PolicyArea.objects.get(key="maksud-ja-toll")


@pytest.fixture
def energy(db):
    return PolicyArea.objects.get(key="energeetika")


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(
        name="Rahandusministeerium", organisation_type=OrganisationType.MINISTRY
    )


def _proposal(matter, ministry, *keys, confidence="h"):
    return ClassificationSuggestion.objects.create(
        matter=matter,
        policy_area_keys=list(keys),
        sender=ministry,
        confidence=confidence,
        source="test",
    )


# -- loading -------------------------------------------------------------------------


def test_load_stores_one_proposal_per_teema_and_reports_what_it_could_not_resolve(taxes, ministry):
    a = factories.MatterFactory()
    b = factories.MatterFactory()
    data = {
        "source": "pealkirjad",
        "suggestions": [
            {
                "matter": str(a.pk),
                "areas": ["maksud-ja-toll"],
                "sender": "Rahandusministeerium",
                "confidence": "h",
            },
            {
                "matter": str(b.pk),
                "areas": ["tundmatu"],
                "sender": "Tundmatu amet",
                "confidence": "m",
            },
            {
                "matter": "01a1a2d6-0000-7000-8000-000000000000",
                "areas": [],
                "sender": None,
                "confidence": "l",
            },
        ],
    }

    report = classification.load(data)

    assert (report.created, report.already, report.missing_matter) == (2, 0, 1)
    assert report.unknown_sender == ("Tundmatu amet",)
    assert report.unknown_area == ("tundmatu",)
    first = ClassificationSuggestion.objects.get(matter=a)
    assert (first.policy_area_keys, first.sender, first.status) == (
        ["maksud-ja-toll"],
        ministry,
        SuggestionStatus.PENDING,
    )
    assert ClassificationSuggestion.objects.get(matter=b).sender is None
    assert not a.policy_areas.exists()
    assert not a.source_organisations.exists()


def test_a_second_load_never_reopens_a_decision(taxes, ministry, specialist):
    matter = factories.MatterFactory()
    data = {
        "source": "pealkirjad",
        "suggestions": [
            {
                "matter": str(matter.pk),
                "areas": ["maksud-ja-toll"],
                "sender": None,
                "confidence": "h",
            }
        ],
    }
    classification.load(data)
    classification.dismiss(ClassificationSuggestion.objects.get(matter=matter), actor=specialist)

    report = classification.load(data)

    assert (report.created, report.already) == (0, 1)
    assert ClassificationSuggestion.objects.get(matter=matter).status == SuggestionStatus.DISMISSED


def test_the_command_has_a_dry_run(tmp_path, taxes, capsys):
    matter = factories.MatterFactory()
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "source": "x",
                "suggestions": [
                    {"matter": str(matter.pk), "areas": [], "sender": None, "confidence": "h"}
                ],
            }
        ),
        encoding="utf-8",
    )

    call_command("load_classification_suggestions", str(path), "--dry-run")
    assert "DRY-RUN created=1" in capsys.readouterr().out
    assert not ClassificationSuggestion.objects.exists()

    call_command("load_classification_suggestions", str(path))
    assert ClassificationSuggestion.objects.count() == 1


# -- deciding ------------------------------------------------------------------------


def test_accepting_adds_to_the_teema_and_never_removes(taxes, energy, ministry, specialist):
    matter = factories.MatterFactory()
    matter.policy_areas.add(energy)
    suggestion = _proposal(matter, ministry, "maksud-ja-toll")

    classification.accept(suggestion, actor=specialist)

    assert set(matter.policy_areas.values_list("key", flat=True)) == {
        "energeetika",
        "maksud-ja-toll",
    }
    assert list(matter.source_organisations.all()) == [ministry]
    suggestion.refresh_from_db()
    assert (suggestion.status, suggestion.decided_by) == (SuggestionStatus.ACCEPTED, specialist)
    kinds = set(ChangeEvent.objects.filter(matter=matter).values_list("event_type", flat=True))
    assert {
        ChangeEventType.MATTER_POLICY_AREAS_CHANGED,
        ChangeEventType.MATTER_ORGANISATION_CHANGED,
    } <= kinds


def test_accepting_with_corrections_uses_the_persons_choice(taxes, energy, ministry, specialist):
    matter = factories.MatterFactory()
    suggestion = _proposal(matter, ministry, "maksud-ja-toll")

    classification.accept(suggestion, actor=specialist, areas=[energy], sender=None)

    assert list(matter.policy_areas.values_list("key", flat=True)) == ["energeetika"]
    assert not matter.source_organisations.exists()


def test_a_decided_proposal_cannot_be_decided_again(taxes, ministry, specialist):
    suggestion = _proposal(factories.MatterFactory(), ministry, "maksud-ja-toll")
    classification.dismiss(suggestion, actor=specialist)

    with pytest.raises(DomainError, match="juba otsustatud"):
        classification.accept(suggestion, actor=specialist)
    assert classification.accept_many([suggestion], actor=specialist) == 0


# -- the page ------------------------------------------------------------------------


def test_the_review_page_lists_only_pending_proposals(client, taxes, ministry, specialist, reader):
    shown = _proposal(factories.MatterFactory(title="Nähtav teema"), ministry, "maksud-ja-toll")
    done = _proposal(factories.MatterFactory(title="Otsustatud teema"), ministry)
    classification.dismiss(done, actor=specialist)
    client.force_login(reader)

    response = client.get(reverse("reporting:classification"))

    body = response.content.decode()
    assert response.status_code == 200
    assert "Nähtav teema" in body
    assert "Otsustatud teema" not in body
    assert "Kinnita" not in body  # a reader sees the proposal, not the buttons
    assert response.context["rows"][0]["suggestion"] == shown


def test_a_reader_cannot_confirm(client, taxes, ministry, reader):
    suggestion = _proposal(factories.MatterFactory(), ministry, "maksud-ja-toll")
    client.force_login(reader)

    response = client.post(reverse("reporting:classification_decide", kwargs={"pk": suggestion.pk}))

    assert response.status_code == 404
    suggestion.refresh_from_db()
    assert suggestion.status == SuggestionStatus.PENDING


def test_a_writer_confirms_a_row_as_corrected(client, taxes, energy, ministry, specialist):
    matter = factories.MatterFactory()
    suggestion = _proposal(matter, ministry, "maksud-ja-toll")
    client.force_login(specialist)

    response = client.post(
        reverse("reporting:classification_decide", kwargs={"pk": suggestion.pk}),
        {
            "otsus": "kinnita",
            "valdkond": ["maksud-ja-toll", "energeetika"],
            "saatja": str(ministry.pk),
            "aasta": "2026",
        },
    )

    assert response.status_code == 302
    assert response["Location"].endswith("?aasta=2026")
    assert set(matter.policy_areas.values_list("key", flat=True)) == {
        "maksud-ja-toll",
        "energeetika",
    }
    assert list(matter.source_organisations.all()) == [ministry]


def test_a_writer_confirms_the_whole_page(client, taxes, ministry, specialist):
    rows = [_proposal(factories.MatterFactory(), ministry, "maksud-ja-toll") for _ in range(3)]
    client.force_login(specialist)

    client.post(
        reverse("reporting:classification_accept_page"),
        {"ettepanek": [str(row.pk) for row in rows[:2]]},
    )

    states = [ClassificationSuggestion.objects.get(pk=row.pk).status for row in rows]
    assert states == [
        SuggestionStatus.ACCEPTED,
        SuggestionStatus.ACCEPTED,
        SuggestionStatus.PENDING,
    ]


# -- Statistika ----------------------------------------------------------------------


def test_statistika_counts_confirmed_fields_and_not_proposals(client, taxes, ministry, specialist):
    confirmed = factories.MatterFactory(owner=specialist, received_date=dt.date(2026, 6, 1))
    confirmed.policy_areas.add(taxes)
    confirmed.source_organisations.add(ministry)
    _imported(confirmed, OpinionOutcome.SENT, dt.date(2026, 6, 10))
    proposed = factories.MatterFactory(owner=specialist, received_date=dt.date(2026, 6, 2))
    _proposal(proposed, ministry, "maksud-ja-toll")
    _imported(proposed, OpinionOutcome.SENT, dt.date(2026, 6, 11))
    client.force_login(specialist)

    response = client.get(reverse("reporting:dashboard"))

    areas = {item["row"].label: item for item in response.context["areas"]}
    assert areas["Maksud ja toll"]["row"].sent == 1
    assert areas["Märkimata"]["row"].sent == 1
    assert "valdkond=maksud-ja-toll" in areas["Maksud ja toll"]["url"]
    assert areas["Märkimata"]["url"] == ""
    senders = {item["row"].label: item for item in response.context["senders"]}
    assert senders["Rahandusministeerium"]["row"].sent == 1
    assert response.context["pending_classification"] == 1
    body = response.content.decode()
    assert "Valdkonna järgi" in body and "Saatja järgi" in body


def test_the_list_narrows_to_one_area_and_one_sender(client, taxes, ministry, specialist):
    taxed = factories.MatterFactory(owner=specialist, title="Maksuteema")
    taxed.policy_areas.add(taxes)
    _imported(taxed, OpinionOutcome.SENT, dt.date(2026, 6, 10))
    sent = factories.MatterFactory(owner=specialist, title="Ministeeriumi teema")
    sent.source_organisations.add(ministry)
    _imported(sent, OpinionOutcome.SENT, dt.date(2026, 6, 11))
    client.force_login(specialist)

    by_area = client.get(reverse("reporting:opinion_rows"), {"valdkond": "maksud-ja-toll"})
    by_sender = client.get(reverse("reporting:opinion_rows"), {"saatja": str(ministry.pk)})

    assert [f.matter.title for f in by_area.context["rows"]] == ["Maksuteema"]
    assert by_area.context["liik"] == "Maksud ja toll"
    assert [f.matter.title for f in by_sender.context["rows"]] == ["Ministeeriumi teema"]
