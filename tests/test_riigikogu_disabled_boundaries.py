"""What the Riigikogu monitoring switch and the document-AI switch disable (ADR 0224
«What the switch disables», ADR 0227).

Each boundary is asserted switched off *and* switched on, so a test that passes only
because nothing works at all would fail its other half.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from app.register_watch import candidates
from app.register_watch.models import (
    CandidateItem,
    CandidateStatus,
    TargetOrigin,
    TopicCandidate,
    WatchSource,
    WatchTarget,
)
from app.riigikogu import analysis, bills
from app.riigikogu.enums import AnalysisStatus, AnalysisType, InvitationReview
from app.riigikogu.models import Analysis, Bill, Invitation
from tests import factories

pytestmark = pytest.mark.django_db

BILL_UUID = "7d0b5a8e-1b1c-4c55-9d43-0f1f8e4a2b77"


@pytest.fixture
def world(settings):
    """The fictional Riigikogu, planted with both switches on, then both switched off."""
    from app.matters.models import Matter

    settings.RIIGIKOGU_MONITORING = True
    settings.RIIGIKOGU_DOCUMENT_AI = True
    factories.UserFactory()
    head = factories.DepartmentHeadFactory()
    call_command("seed_riigikogu_demo", verbosity=0)
    settings.RIIGIKOGU_MONITORING = False
    settings.RIIGIKOGU_DOCUMENT_AI = False
    one = Matter.objects.get(title="Näidisteema: väikeettevõtluse aruandlus")
    two = Matter.objects.get(title="Näidisteema: näidisenergia")
    return {"head": head, "lawyer": one.owner, "one": one, "two": two}


def _no_network(monkeypatch):
    """Any read of api.riigikogu.ee fails the test."""
    from app.riigikogu import client

    def boom(*args, **kwargs):
        raise AssertionError("api.riigikogu.ee was read")

    monkeypatch.setattr(client, "get_json", boom)
    monkeypatch.setattr(bills, "find", boom)


# -- linking a bill by number ------------------------------------------------------------


def test_linking_by_number_is_unavailable_and_reads_nothing_while_off(client, world, monkeypatch):
    _no_network(monkeypatch)
    before = WatchTarget.objects.count()
    client.force_login(world["lawyer"])
    response = client.post(
        reverse("register_watch:riigikogu_link", kwargs={"pk": world["two"].pk}),
        {"eelnou": "77 SE"},
    )
    assert response.status_code == 404
    assert WatchTarget.objects.count() == before


def test_linking_by_number_works_once_switched_on(client, world, settings, monkeypatch):
    settings.RIIGIKOGU_MONITORING = True
    bill = Bill.objects.create(uuid=BILL_UUID, mark=77, type_code="SE", title="Seadus")
    monkeypatch.setattr(bills, "find", lambda mark, kind, **_: bill)
    client.force_login(world["lawyer"])
    response = client.post(
        reverse("register_watch:riigikogu_link", kwargs={"pk": world["two"].pk}),
        {"eelnou": "77 SE"},
    )
    assert response.status_code == 302
    assert bills.teemad_of(bill).filter(pk=world["two"].pk).exists()


# -- unlinking -----------------------------------------------------------------------------


def _linked(world):
    return WatchTarget.objects.filter(
        matter=world["one"], source=WatchSource.RIIGIKOGU, origin=TargetOrigin.LINKED, active=True
    ).first()


@pytest.mark.parametrize("on", [False, True])
def test_unlinking_only_while_switched_on(client, world, settings, on):
    settings.RIIGIKOGU_MONITORING = on
    target = _linked(world)
    assert target is not None
    client.force_login(world["lawyer"])
    response = client.post(reverse("register_watch:riigikogu_unlink", kwargs={"pk": target.pk}))
    target.refresh_from_db()
    assert response.status_code == (302 if on else 404)
    assert target.active is (not on)


# -- invitation decisions ------------------------------------------------------------------


@pytest.mark.parametrize(
    "otsus", ["kuupaev", "seo", "meeldetuletus", "pole_vaja", "kinnita", "vastus"]
)
def test_no_invitation_decision_while_off(client, world, monkeypatch, otsus):
    _no_network(monkeypatch)
    invitation = Invitation.objects.filter(review=InvitationReview.NEW).first()
    client.force_login(world["head"])
    response = client.post(
        reverse("register_watch:riigikogu_decide", kwargs={"pk": invitation.pk}),
        {"otsus": otsus, "teema_id": str(world["one"].pk), "paev": "2026-12-01"},
    )
    invitation.refresh_from_db()
    assert response.status_code == 404
    assert invitation.review == InvitationReview.NEW


def test_an_invitation_is_decided_once_switched_on(client, world, settings):
    settings.RIIGIKOGU_MONITORING = True
    invitation = Invitation.objects.filter(review=InvitationReview.NEW).first()
    client.force_login(world["head"])
    response = client.post(
        reverse("register_watch:riigikogu_decide", kwargs={"pk": invitation.pk}),
        {"otsus": "pole_vaja"},
    )
    invitation.refresh_from_db()
    assert response.status_code == 302
    assert invitation.review == InvitationReview.NO_ACTION


# -- proposing a work victory ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("monitoring", "document_ai", "status"),
    [(False, False, 404), (False, True, 404), (True, False, 404), (True, True, 302)],
)
def test_a_victory_is_proposed_only_with_both_switches_on(
    client, world, settings, monitoring, document_ai, status
):
    from app.intelligence.models import MatterWorkVictory

    settings.RIIGIKOGU_MONITORING = monitoring
    settings.RIIGIKOGU_DOCUMENT_AI = document_ai
    row = Analysis.objects.get(
        analysis_type=AnalysisType.PROPOSAL_OUTCOMES, status=AnalysisStatus.DONE
    )
    client.force_login(world["lawyer"])
    response = client.post(
        reverse("register_watch:riigikogu_victory", kwargs={"pk": row.pk, "index": 0}),
        {"teema_id": str(world["one"].pk)},
    )
    assert response.status_code == status
    assert MatterWorkVictory.objects.filter(matter=world["one"]).exists() is (status == 302)


def test_no_victory_from_a_mocked_analysis_in_production(client, world, settings):
    settings.RIIGIKOGU_MONITORING = True
    settings.RIIGIKOGU_DOCUMENT_AI = True
    settings.APPLICATION_ENVIRONMENT = "production"
    row = Analysis.objects.get(
        analysis_type=AnalysisType.PROPOSAL_OUTCOMES, status=AnalysisStatus.DONE
    )
    assert row.mocked
    client.force_login(world["lawyer"])
    response = client.post(
        reverse("register_watch:riigikogu_victory", kwargs={"pk": row.pk, "index": 0}),
        {"teema_id": str(world["one"].pk)},
    )
    assert response.status_code == 404


# -- what the pages draw ---------------------------------------------------------------------


@pytest.mark.parametrize("on", [False, True])
def test_monitooring_is_read_only_while_off(client, world, settings, on):
    settings.RIIGIKOGU_MONITORING = on
    client.force_login(world["head"])
    html = client.get(reverse("register_watch:riigikogu")).content.decode()
    assert "Kojale saadetud kutsed" in html  # the stored public record is still read
    decide = reverse(
        "register_watch:riigikogu_decide",
        kwargs={"pk": Invitation.objects.filter(review=InvitationReview.NEW).first().pk},
    )
    assert (decide in html) is on
    assert ("ei saa praegu midagi otsustada" in html) is (not on)


@pytest.mark.parametrize("on", [False, True])
def test_the_teema_tab_has_no_parliamentary_section_while_off(client, world, settings, on):
    settings.RIIGIKOGU_MONITORING = on
    client.force_login(world["lawyer"])
    html = client.get(
        reverse("matters:matter_registers", kwargs={"pk": world["one"].pk})
    ).content.decode()
    assert ('id="rkteema-heading"' in html) is on
    assert ("Seo eelnõu" in html) is on
    assert (reverse("register_watch:riigikogu_link", kwargs={"pk": world["one"].pk}) in html) is on


@pytest.mark.parametrize(
    ("document_ai", "environment", "shown"),
    [(False, "test", False), (True, "production", False), (True, "test", True)],
)
def test_a_stored_analysis_is_shown_only_when_document_ai_is_on_and_never_mocked_in_production(
    client, world, settings, document_ai, environment, shown
):
    settings.RIIGIKOGU_MONITORING = True
    settings.RIIGIKOGU_DOCUMENT_AI = document_ai
    settings.APPLICATION_ENVIRONMENT = environment
    assert Analysis.objects.filter(status=AnalysisStatus.DONE, mocked=True).exists()
    client.force_login(world["lawyer"])
    html = client.get(
        reverse("matters:matter_registers", kwargs={"pk": world["one"].pk})
    ).content.decode()
    assert ("Näidis: mudeli vastus on simuleeritud" in html) is shown
    assert ("rkanalysis__label" in html) is shown


# -- document AI writes nothing while off ---------------------------------------------------


def test_analysis_run_writes_nothing_while_off_even_with_a_mock(settings):
    settings.RIIGIKOGU_DOCUMENT_AI = False
    model = analysis.MockModel(lambda prompt: {"issues": []})
    with pytest.raises(RuntimeError, match="välja lülitatud"):
        analysis.run(AnalysisType.POSITIONS, [analysis.Source("doc", "Tekst.", "h")], model=model)
    assert not Analysis.objects.exists()
    assert model.prompts == []


def test_analysis_run_writes_once_switched_on(settings):
    settings.RIIGIKOGU_DOCUMENT_AI = True
    row = analysis.run(
        AnalysisType.POSITIONS,
        [analysis.Source("doc", "Tekst.", "h")],
        model=analysis.MockModel(lambda prompt: {"issues": []}),
    )
    assert row.status == AnalysisStatus.DONE and row.mocked


def test_the_demonstration_is_refused_in_production(settings):
    settings.APPLICATION_ENVIRONMENT = "production"
    settings.RIIGIKOGU_DOCUMENT_AI = True
    factories.UserFactory()
    with pytest.raises(CommandError, match="tootmises"):
        call_command("seed_riigikogu_demo", verbosity=0)
    assert not Bill.objects.exists() and not Analysis.objects.exists()


def test_the_demonstration_stores_no_analysis_while_document_ai_is_off(settings):
    settings.RIIGIKOGU_DOCUMENT_AI = False
    factories.UserFactory()
    factories.DepartmentHeadFactory()
    call_command("seed_riigikogu_demo", verbosity=0)
    assert Bill.objects.exists()
    assert not Analysis.objects.exists()


# -- a candidate's «Seo» / «Uus teema» ------------------------------------------------------


def _riigikogu_candidate() -> TopicCandidate:
    candidate = TopicCandidate.objects.create(
        title="Äriseadustiku muutmise seadus", match_key="äriseadustiku"
    )
    CandidateItem.objects.create(
        candidate=candidate,
        source=WatchSource.RIIGIKOGU,
        external_id=f"draft:{BILL_UUID}",
        title="Äriseadustiku muutmise seadus",
        occurred_on=dt.date(2026, 11, 20),
        url=f"https://www.riigikogu.ee/tegevus/eelnoud/eelnou/{BILL_UUID}/",
    )
    return candidate


@pytest.mark.parametrize("on", [False, True])
def test_attaching_a_candidate_ties_its_bill_only_while_switched_on(settings, department_head, on):
    settings.RIIGIKOGU_MONITORING = on
    teema = factories.MatterFactory(title="Äriühingute digitaliseerimine")
    candidate = _riigikogu_candidate()

    candidates.attach(candidate, teema, actor=department_head)

    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.ATTACHED
    linked = WatchTarget.objects.filter(
        matter=teema, source=WatchSource.RIIGIKOGU, origin=TargetOrigin.LINKED
    )
    assert linked.exists() is on
    assert Bill.objects.filter(uuid=BILL_UUID).exists() is on


@pytest.mark.parametrize("on", [False, True])
def test_adopting_a_candidate_ties_its_bill_only_while_switched_on(settings, department_head, on):
    settings.RIIGIKOGU_MONITORING = on
    teema = factories.MatterFactory(title="Äriseadustiku muutmine")
    candidate = _riigikogu_candidate()

    assert candidates.adopt_if_new(candidate, teema, actor=department_head)

    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.ADDED
    assert WatchTarget.objects.filter(matter=teema, origin=TargetOrigin.LINKED).exists() is on


# -- what keeps working ------------------------------------------------------------------------


def test_the_register_watch_still_reads_the_riigikogu_while_the_monitoring_is_off(
    settings, monkeypatch
):
    """ADR 0217/0218's authorised reads go on exactly as before: they are governed by
    ``REGISTER_WATCH_FETCH`` alone, never by ``RIIGIKOGU_MONITORING``."""
    from app.register_watch.sources import http
    from app.register_watch.sources import riigikogu as source
    from app.riigikogu import client

    settings.RIIGIKOGU_MONITORING = False
    settings.REGISTER_WATCH_FETCH = True
    monkeypatch.setattr(client, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        http,
        "get_json",
        lambda host, path, params=None, **_: {
            "_embedded": {
                "content": [
                    {"uuid": BILL_UUID, "draftTypeCode": "SE", "membership": 15, "mark": 77}
                ]
            }
        },
    )
    found = source.find_bill(77, "SE")
    assert found is not None and found["uuid"] == BILL_UUID
