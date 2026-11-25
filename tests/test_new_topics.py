"""«Uued eelnõud»: the nightly sweep for drafts that are not a Teema yet (ADR 0218).

No test contacts a register or the model: the sources' doors and the model
client are replaced here, as in tests/test_register_watch.py.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from app.core.enums import Visibility
from app.matters.models import Matter
from app.notifications import inbox
from app.register_watch import candidate_notices, candidates, candidates_ai
from app.register_watch.models import (
    CandidateItem,
    CandidateNotice,
    CandidateStatus,
    Strength,
    SweepRun,
    TopicCandidate,
    WatchSource,
)
from app.register_watch.sources import adr, pdr, riigikogu
from app.register_watch.sources.http import FetchError
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 21)
PAGE = reverse("register_watch:candidates")
CREATE = reverse("matters:matter_create")

RIIGIKOGU_LIST = {
    "_embedded": {
        "content": [
            {
                "uuid": "bill-1100",
                "title": "Mootorsõidukimaksu seaduse kehtetuks tunnistamise seadus",
                "mark": 1100,
                "draftTypeCode": "SE",
                "initiated": "2026-11-19",
                "leadingCommittee": {"name": "Rahanduskomisjon"},
            },
            {
                # Already followed: an open Teema names «973 SE».
                "uuid": "bill-973",
                "title": "Kutse- ja oskusseadus",
                "mark": 973,
                "draftTypeCode": "SE",
                "initiated": "2026-11-20",
            },
            {
                # A question, not a draft: never listed.
                "uuid": "kysimus",
                "title": "Arupärimine",
                "mark": 5,
                "draftTypeCode": "AK",
                "initiated": "2026-11-20",
            },
        ]
    }
}


def _adr_page(code: str, rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        f'<tr><td>{number}</td><td>{day}</td><td><a href="/{code}/dokument/{key}">{title}</a>'
        "</td><td>Sissetulev kiri</td><td>Saatja</td></tr>"
        for key, number, day, title in rows
    )
    return f"<table><tr><th>Viit</th></tr>{body}</table> Kokku: {len(rows)}"


MKM_ROWS = [
    (
        "501",
        "2-3/1",
        "20.11.2026",
        "Äriseadustiku muutmise seaduse eelnõu kooskõlastamiseks esitamine",
    ),
    ("502", "2-3/2", "21.11.2026", "Arvamus äriseadustiku muutmise seaduse eelnõule"),
    # Older than the sweep's window: not a new topic.
    ("400", "2-3/0", "01.10.2026", "Vana seaduse eelnõu"),
]


@pytest.fixture
def registers(monkeypatch):
    calls: list[Any] = []
    pages: dict[str, str] = {"/mkm/otsing": _adr_page("mkm", MKM_ROWS)}
    answers: dict[tuple[str, str], Any] = {
        ("api.riigikogu.ee", "/api/volumes/drafts"): RIIGIKOGU_LIST,
        ("dok.hm.ee", "/api/v1/documents.json"): {"data": []},
        ("adr.envir.ee", "/api/v1/documents.json"): {"data": []},
    }

    def fake(host, path, params=None):
        calls.append((host, path, dict(params or {})))
        if (host, path) not in answers:
            raise FetchError(f"{host} vastas 404")
        return answers[(host, path)]

    def fake_form(host, path, fields):
        calls.append((host, path, dict(fields)))
        return pages.get(path, "<table></table> Kokku: 0")

    monkeypatch.setattr(riigikogu, "get_json", fake)
    monkeypatch.setattr(pdr, "get_json", fake)
    monkeypatch.setattr(adr, "get_text", lambda host, path: "")
    monkeypatch.setattr(adr, "post_form", fake_form)
    return SimpleNamespace(calls=calls, answers=answers, pages=pages)


class FakeClient:
    """Answers every candidate with the strength its title asks for."""

    def __init__(self, strength: str = "STRONG", related: str | None = None):
        self.strength = strength
        self.related = related
        self.sent: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.sent.append(request)
        prompt = request["messages"][0]["content"]
        drafts = json.loads(prompt.split("New drafts:\n", 1)[1])
        items = [
            {
                "id": d["id"],
                "strength": self.strength,
                "reason": "Puudutab äriühinguid.",
                "related": self.related,
            }
            for d in drafts
        ]
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="text", text=json.dumps({"items": items}))],
        )


def _signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _candidate(
    title: str = "Äriseadustiku muutmise seaduse eelnõu", **extra: Any
) -> TopicCandidate:
    candidate = TopicCandidate.objects.create(
        title=title, match_key=candidates.match_key(title), **extra
    )
    CandidateItem.objects.create(
        candidate=candidate,
        source=WatchSource.ADR_MKM,
        external_id=f"adr:mkm:{candidate.pk}",
        title=title,
        occurred_on=dt.date(2026, 11, 20),
        url=f"https://adr.rik.ee/mkm/dokument/{str(candidate.pk)[:6]}",
        detail="2-3/1 · Sissetulev kiri",
    )
    return candidate


# -- grouping ----------------------------------------------------------------------


def test_letters_about_one_draft_share_a_key():
    sent = candidates.match_key("Äriseadustiku muutmise seaduse eelnõu kooskõlastamiseks esitamine")
    opinion = candidates.match_key("Arvamus äriseadustiku muutmise seaduse eelnõule")
    other = candidates.match_key("Pakendiseaduse muutmise seaduse eelnõu")
    assert sent == opinion == "äriseadustiku"
    assert other != sent


def test_letters_with_a_sender_or_a_longer_name_join_the_same_draft():
    assert candidates._same_draft(
        candidates.match_key("Haldusmenetluse seaduse muutmise seaduse eelnõu kooskõlastamine"),
        candidates.match_key(
            "Haldusmenetluse seaduse muutmise eelnõu kooskõlastamine märkustega arvestamisel"
        ),
    )
    assert candidates.match_key(
        "Arvamus noortevaldkonna seaduse eelnõu VTK kohta_Soolise võrdõiguslikkuse volinik"
    ) == candidates.match_key(
        "Tagasiside noortevaldkonna seaduse väljatöötamiskavatsusele_Õiguskantsler"
    )
    assert not candidates._same_draft(
        candidates.match_key(
            "Riigikogu otsus «Ettepaneku tegemine Valitsusele muuta palgatoetuse tingimusi»"
        ),
        candidates.match_key(
            "Riigikogu otsus «Ettepaneku tegemine Valitsusele taaskehtestada piirikontroll»"
        ),
    )


@pytest.mark.parametrize(
    ("letter", "draft"),
    [
        (
            "Spordiseaduse täiendamise seaduse eelnõu kooskõlastamine",
            "Spordiseaduse täiendamise seaduse eelnõu",
        ),
        (
            "Arvamus riigihangete seaduse muutmise väljatöötamiskavatsusele",
            "Riigihangete seaduse muutmise väljatöötamiskavatsus",
        ),
        ("Eelnõu kooskõlastamiseks", "Eelnõu kooskõlastamiseks"),
        (
            "Riigi Infosüsteemi Ameti põhimääruse eelnõu",
            "Riigi Infosüsteemi Ameti põhimääruse eelnõu",
        ),
    ],
)
def test_uus_teema_gets_the_drafts_name_not_the_letters(letter, draft):
    assert candidates.draft_name(letter) == draft


def test_a_letter_that_names_no_draft_joins_nothing():
    assert candidates.match_key("Eelnõu kooskõlastamiseks") == ""
    assert not candidates._same_draft("", "")


@override_settings(REGISTER_SWEEP=True)
def test_an_administrative_act_is_listed_folded_and_never_sent_to_the_model(registers):
    registers.pages["/kum/otsing"] = _adr_page(
        "kum",
        [("700", "7-1/1", "20.11.2026", "Pärnu linnas Aia tn 4 kinnistu detailplaneeringu eelnõu")],
    )
    candidates.sweep(use_ai=False, today=TODAY)

    plan = TopicCandidate.objects.get(title__startswith="Pärnu linnas")
    assert plan.strength == Strength.NONE
    assert plan.reason == candidates.ADMINISTRATIVE_REASON


# -- the sweep ---------------------------------------------------------------------


@override_settings(REGISTER_SWEEP=True)
def test_the_sweep_lists_new_drafts_and_leaves_out_what_a_teema_follows(registers):
    factories.MatterFactory(title="Kutse- ja oskusseaduse eelnõu (973 SE)")

    record = candidates.sweep(use_ai=False, today=TODAY)

    titles = sorted(TopicCandidate.objects.values_list("title", flat=True))
    assert titles == [
        "Mootorsõidukimaksu seaduse kehtetuks tunnistamise seadus (1100 SE)",
        "Äriseadustiku muutmise seaduse eelnõu kooskõlastamiseks esitamine",
    ]
    # The two letters about one draft are one candidate with two documents.
    business = TopicCandidate.objects.get(match_key="äriseadustiku")
    assert business.items.count() == 2
    assert record.candidates_new == 2
    assert record.finished_at is not None
    # The ministry register was asked from the window's start only.
    asked = [fields for host, path, fields in registers.calls if path == "/mkm/otsing"]
    assert asked and all(fields["regDateBegin"] == "17.11.2026" for fields in asked)


@override_settings(REGISTER_SWEEP=True)
def test_a_second_night_adds_nothing_twice(registers):
    candidates.sweep(use_ai=False, today=TODAY)
    second = candidates.sweep(use_ai=False, today=TODAY)

    assert second.items_new == 0
    assert second.candidates_new == 0
    # Two bills and one ministry draft; no Teema names «973 SE» here.
    assert TopicCandidate.objects.count() == 3


@override_settings(REGISTER_SWEEP=True)
def test_one_failing_register_is_logged_and_the_rest_are_read(registers):
    del registers.answers[("api.riigikogu.ee", "/api/volumes/drafts")]

    record = candidates.sweep(use_ai=False, today=TODAY)

    assert any(error["allikas"] == "Riigikogu" for error in record.errors)
    assert TopicCandidate.objects.filter(match_key="äriseadustiku").exists()


@override_settings(REGISTER_SWEEP=True)
def test_a_dismissed_draft_returns_once_it_reaches_the_riigikogu(registers, department_head):
    candidate = _candidate("Mootorsõidukimaksu seaduse kehtetuks tunnistamise seadus")
    candidates.dismiss(candidate, actor=department_head)

    candidates.sweep(use_ai=False, today=TODAY)

    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.NEW
    assert candidate.reopened_at is not None
    assert candidate.items.filter(source=WatchSource.RIIGIKOGU).exists()


def test_the_command_refuses_while_the_sweep_is_off():
    with pytest.raises(CommandError, match="the sweep is off"):
        call_command("sweep_registers")


# -- the model ---------------------------------------------------------------------


@override_settings(REGISTER_SWEEP=True, REGISTER_SWEEP_AI=True)
def test_the_model_judges_and_may_name_only_an_offered_open_teema(registers):
    open_teema = factories.MatterFactory(title="Äriühingute digitaliseerimine")
    secret = factories.MatterFactory(
        title="Salajane konkurentsiasi", visibility=Visibility.RESTRICTED
    )
    client = FakeClient("STRONG", related=str(open_teema.pk))

    record = candidates.sweep(use_ai=True, ai_client=client, today=TODAY)

    prompt = client.sent[0]["messages"][0]["content"]
    assert "Salajane konkurentsiasi" not in prompt
    assert str(secret.pk) not in prompt
    judged = TopicCandidate.objects.get(match_key="äriseadustiku")
    assert judged.strength == Strength.STRONG
    assert judged.reason == "Puudutab äriühinguid."
    assert judged.related_matter == open_teema
    assert record.ai_judged == 3

    # An id it was never offered names nothing.
    unjudged = _candidate("Pakendiseaduse muutmise seaduse eelnõu")
    candidates_ai.judge_unjudged(
        candidates_ai.Usage(), client=FakeClient("WEAK", related=str(secret.pk))
    )
    unjudged.refresh_from_db()
    assert unjudged.strength == Strength.WEAK
    assert unjudged.related_matter is None


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_decision_made_while_the_model_answered_stands(department_head):
    """E1 (post-r91): the verdict is written only on a candidate still waiting.

    The head dismisses one candidate and links another while the request is
    out; neither gets the model's strength, reason or Teema afterwards. The one
    nobody touched is judged as before."""
    teema = factories.MatterFactory(title="Äriühingute digitaliseerimine")
    left = _candidate("Äriseadustiku muutmise seaduse eelnõu")
    dismissed = _candidate("Pakendiseaduse muutmise seaduse eelnõu")
    linked = _candidate("Jäätmeseaduse muutmise seaduse eelnõu")

    class Deciding(FakeClient):
        def _create(self, **request):
            candidates.dismiss(dismissed, actor=department_head)
            candidates.attach(linked, teema, actor=department_head)
            return super()._create(**request)

    usage = candidates_ai.Usage()
    judged = candidates_ai.judge_unjudged(usage, client=Deciding("STRONG", related=str(teema.pk)))

    assert judged == 1 and usage.judged == 1
    left.refresh_from_db()
    assert (left.strength, left.related_matter) == (Strength.STRONG, teema)
    for decided, status in (
        (dismissed, CandidateStatus.DISMISSED),
        (linked, CandidateStatus.ATTACHED),
    ):
        decided.refresh_from_db()
        assert decided.status == status
        assert decided.decided_by == department_head
        assert decided.strength == Strength.UNJUDGED
        assert decided.reason == ""
        assert decided.related_matter is None


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_verdict_given_while_the_model_answered_is_not_overwritten():
    """E1 review: a candidate judged by another writer while the request was out
    keeps that verdict; this answer is written only where it is still «Hindamata»."""
    teema = factories.MatterFactory(title="Äriühingute digitaliseerimine")
    candidate = _candidate()

    class JudgedMeanwhile(FakeClient):
        def _create(self, **request):
            TopicCandidate.objects.filter(pk=candidate.pk).update(
                strength=Strength.STRONG, reason="Varem hinnatud."
            )
            return super()._create(**request)

    usage = candidates_ai.Usage()
    judged = candidates_ai.judge_unjudged(
        usage, client=JudgedMeanwhile("WEAK", related=str(teema.pk))
    )

    assert judged == 0 and usage.judged == 0
    candidate.refresh_from_db()
    assert candidate.strength == Strength.STRONG
    assert candidate.reason == "Varem hinnatud."
    assert candidate.related_matter is None


# -- who sees it -------------------------------------------------------------------


def test_the_page_is_the_heads_until_opened(department_head, specialist):
    _candidate()

    assert _signed_in(department_head).get(PAGE).status_code == 200
    assert _signed_in(specialist).get(PAGE).status_code == 404

    _signed_in(department_head).post(reverse("register_watch:candidate_audience"), {"koigile": "1"})
    page = _signed_in(specialist).get(PAGE)
    assert page.status_code == 200
    assert "Äriseadustiku muutmise seaduse eelnõu" in page.content.decode()


def test_only_the_head_opens_or_closes_the_page(specialist):
    candidate_notices.set_everyone(True)
    answer = _signed_in(specialist).post(
        reverse("register_watch:candidate_audience"), {"koigile": "0"}
    )
    assert answer.status_code == 404
    assert candidate_notices.audience().everyone is True


def test_a_reader_never_decides(reader):
    candidate_notices.set_everyone(True)
    candidate = _candidate()
    answer = _signed_in(reader).post(
        reverse("register_watch:candidate_decide", kwargs={"pk": candidate.pk}), {"otsus": "jata"}
    )
    assert answer.status_code == 404
    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.NEW


def test_weak_candidates_are_folded_at_the_foot(department_head):
    _candidate("Pakendiseaduse muutmise seaduse eelnõu", strength=Strength.STRONG)
    _candidate("Riigi peremehetu vara käsunduse eelnõu", strength=Strength.WEAK)

    html = _signed_in(department_head).get(PAGE).content.decode()

    folded = html.split("regwatch__unrelated", 1)[1]
    assert "Riigi peremehetu vara" in folded
    assert "Pakendiseaduse" not in folded


# -- the bell ----------------------------------------------------------------------


@override_settings(REGISTER_SWEEP=True)
def test_the_sweep_tells_the_head_in_the_bell(registers, department_head, specialist):
    candidates.sweep(use_ai=False, today=TODAY)

    assert inbox.inbox_count(specialist) == 0
    items = [item for item in inbox.inbox_for(department_head) if item.type == "candidates"]
    assert len(items) == 1
    assert items[0].note == "3 uut eelnõu"
    assert items[0].url == PAGE

    # Read in the bell, it is history.
    row = CandidateNotice.objects.get(recipient=department_head)
    _signed_in(department_head).post(
        reverse("notifications:candidates_read", kwargs={"pk": row.pk})
    )
    assert not [i for i in inbox.inbox_for(department_head) if i.type == "candidates"]


def test_a_notice_is_resolved_once_the_list_is_empty(department_head):
    candidate = _candidate(strength=Strength.STRONG)
    candidate_notices.notify(1)
    assert candidate_notices.due_unread(department_head).count() == 1

    candidates.dismiss(candidate, actor=department_head)

    assert candidate_notices.due_unread(department_head).count() == 0


def test_somebody_elses_notice_is_not_found(department_head):
    other = factories.DepartmentHeadFactory()
    candidate_notices.notify(1)
    theirs = CandidateNotice.objects.get(recipient=other)
    answer = _signed_in(department_head).post(
        reverse("notifications:candidates_read", kwargs={"pk": theirs.pk})
    )
    assert answer.status_code == 404


# -- the decisions -----------------------------------------------------------------


def test_dismissing_writes_nothing_on_any_teema(department_head):
    candidate = _candidate()
    _signed_in(department_head).post(
        reverse("register_watch:candidate_decide", kwargs={"pk": candidate.pk}), {"otsus": "jata"}
    )
    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.DISMISSED
    assert candidate.decided_by == department_head


def test_linking_to_an_existing_teema_writes_its_register_links(department_head):
    teema = factories.MatterFactory(
        owner=department_head, reference_year=2026, reference_number=190
    )
    candidate = _candidate()

    _signed_in(department_head).post(
        reverse("register_watch:candidate_decide", kwargs={"pk": candidate.pk}),
        {"otsus": "seo", "teema": "2026_190"},
    )

    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.ATTACHED
    assert candidate.matter == teema
    urls = list(teema.procedural_links.values_list("url", flat=True))
    assert urls == [candidate.items.get().url]


def test_linking_to_an_unknown_teema_is_refused(department_head):
    candidate = _candidate()
    answer = _signed_in(department_head).post(
        reverse("register_watch:candidate_decide", kwargs={"pk": candidate.pk}),
        {"otsus": "seo", "teema": "2026_9999"},
        follow=True,
    )
    assert "Sellist avatud teemat ei leitud." in answer.content.decode()
    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.NEW


def test_adding_opens_uus_teema_filled_from_the_candidate(department_head):
    ministry = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    candidate = _candidate()
    client = _signed_in(department_head)

    answer = client.post(
        reverse("register_watch:candidate_decide", kwargs={"pk": candidate.pk}), {"otsus": "lisa"}
    )
    assert answer["Location"] == f"{CREATE}?kandidaat={candidate.pk}"

    page = client.get(answer["Location"]).content.decode()
    assert 'value="Äriseadustiku muutmise seaduse eelnõu"' in page
    assert f'name="kandidaat" value="{candidate.pk}"' in page
    assert "20.11.2026" in page
    assert re.search(rf'value="{ministry.pk}"[^>]*checked', page)

    token = re.search(r'name="intake" value="([^"]+)"', page).group(1)
    saved = client.post(
        CREATE,
        {
            "title": "Äriseadustiku muutmise seaduse eelnõu",
            "owner": str(department_head.pk),
            "intake": token,
            "kandidaat": str(candidate.pk),
        },
    )
    assert saved.status_code == 302
    teema = Matter.objects.get(title="Äriseadustiku muutmise seaduse eelnõu")
    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.ADDED
    assert candidate.matter == teema
    assert teema.procedural_links.count() == 1


def test_a_forged_candidate_on_uus_teema_binds_to_nothing(specialist):
    """A specialist may file a Teema, but not close the head's candidate with it."""
    candidate = _candidate()
    page = _signed_in(specialist).get(f"{CREATE}?kandidaat={candidate.pk}").content.decode()
    assert 'name="kandidaat"' not in page
    assert SweepRun.objects.count() == 0


# -- is it working -----------------------------------------------------------------


@override_settings(REGISTER_SWEEP=True, REGISTER_WATCH_FETCH=True)
def test_a_stopped_or_missing_sweep_is_red(registers):
    from django.utils import timezone

    now = timezone.now()
    # Long after the release, no run: the schedule was never installed.
    assert candidates.sweep_health(now + dt.timedelta(days=3)).detail == "never run"

    candidates.sweep(use_ai=False, today=TODAY)
    assert candidates.sweep_health().ok

    assert candidates.sweep_health(now + candidates.STALE_AFTER + dt.timedelta(hours=1)).ok is False
    SweepRun.objects.create(started_at=timezone.now(), errors=[{"viga": "x", "katkes": True}])
    assert candidates.sweep_health().detail == "last run stopped before its end"


def test_a_switched_off_sweep_is_green():
    assert candidates.sweep_health().detail == "off"


def test_a_letter_naming_no_draft_is_folded_apart_and_not_counted(department_head):
    nameless = _candidate("Eelnõu kooskõlastamiseks")
    assert nameless.match_key == ""
    candidate_notices.notify(1)

    html = _signed_in(department_head).get(PAGE).content.decode()

    assert "Pealkiri ei ütle, millise eelnõuga on tegu (1)" in html
    assert candidate_notices.due_unread(department_head).count() == 0
    assert candidates.worth_telling().count() == 0
