"""The Government agenda's model verdicts: bounded, and never over a person (audit HR-02).

Before 2026-11-23 an agenda was marked judged only when *every* item came back,
so one item the model left out sent the whole agenda again every half hour for
ever, and each pass rewrote the strength, reason and Teema of items a person
had already added or marked read. Now:

* only waiting, unjudged items are sent, and only they are written — under a
  lock, so a decision made while the model answered stands;
* one readable answer is the attempt: what it left out stays «Hindamata», shown
  as such, and is not asked again;
* a failed, cut-off or unreadable answer is retried at most `MAX_ATTEMPTS`
  times;
* a Teema id the model was never offered links nothing.

No external model is called.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.register_watch import agenda
from app.register_watch.models import (
    AgendaItem,
    AgendaItemStatus,
    GovernmentAgenda,
    Strength,
)
from app.register_watch.sources import valitsus
from app.register_watch.sources.http import FetchError
from tests import factories

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_site")]

SESSION = dt.date(2026, 11, 19)


@pytest.fixture
def no_site(monkeypatch):
    """valitsus.ee is never read: every test's week is already stored."""

    def refuse(host, path):
        raise FetchError("not in tests")

    monkeypatch.setattr(valitsus, "get_text", refuse)


class Client_:
    """Answers with ``rows`` (number → strength, or a whole row dict), or fails."""

    def __init__(self, rows=None, *, fail=None, stop="end_turn", text=None, during=None):
        self.rows = rows or {}
        self.fail = fail
        self.stop = stop
        self.text = text
        self.during = during
        self.sent: list[list[int]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        prompt = request["messages"][0]["content"]
        self.sent.append([i["number"] for i in json.loads(prompt.split("päevakord\n", 1)[1])])
        if self.during:
            self.during()
        if self.fail:
            raise self.fail
        rows = [
            row if isinstance(row, dict) else {"number": n, "strength": row, "reason": "põhjus"}
            for n, row in self.rows.items()
        ]
        rows = [{"reason": "põhjus", "related": None, **row} for row in rows]
        return SimpleNamespace(
            stop_reason=self.stop,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(type="text", text=self.text or json.dumps({"items": rows}))],
        )


def _agenda(*items: dict) -> GovernmentAgenda:
    a = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/uudised/x",
        title="Valitsuse 19.11.26 istungi kommenteeritud päevakord",
        session_date=SESSION,
    )
    for n, extra in enumerate(items or ({}, {}, {}), start=1):
        AgendaItem.objects.create(agenda=a, number=n, title=f"Punkt {n}", **extra)
    return a


def _run(client):
    return agenda.run(today=SESSION, ai_client=client)


def _item(number: int) -> AgendaItem:
    return AgendaItem.objects.get(number=number)


# -- an incomplete answer -------------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_an_incomplete_answer_is_the_attempt_and_the_rest_stay_unjudged():
    a = _agenda()
    client = Client_({1: "STRONG"})

    _run(client)
    _run(client)

    assert client.sent == [[1, 2, 3]]  # asked once, not again on the next run
    a.refresh_from_db()
    assert a.judged_at is not None and a.ai_attempts == 1
    assert _item(1).strength == Strength.STRONG
    assert _item(2).strength == Strength.UNJUDGED and _item(3).strength == Strength.UNJUDGED


@override_settings(REGISTER_SWEEP_AI=True)
def test_an_unusable_verdict_is_left_unjudged_not_invented():
    _agenda()
    client = Client_({1: "MAYBE", 2: {"number": 2, "strength": "WEAK"}, 3: "NONE"})

    _run(client)

    assert _item(1).strength == Strength.UNJUDGED and _item(1).reason == ""
    assert _item(2).strength == Strength.WEAK
    assert _item(3).strength == Strength.NONE


# -- what is already settled ----------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_judged_item_and_a_persons_decision_are_neither_sent_nor_overwritten():
    person = factories.UserFactory()
    teema = factories.MatterFactory()
    a = _agenda(
        {"strength": Strength.POSSIBLE, "reason": "varasem", "related_matter": teema},
        {
            "strength": Strength.STRONG,
            "reason": "lisatud",
            "related_matter": teema,
            "status": AgendaItemStatus.ADDED,
            "decided_by": person,
            "decided_at": timezone.now(),
        },
        {"status": AgendaItemStatus.DISMISSED, "decided_by": person, "decided_at": timezone.now()},
        {},
    )
    client = Client_({1: "NONE", 2: "NONE", 3: "NONE", 4: "WEAK"})

    agenda.judge(a, agenda.candidates_ai.Usage(), client=client)

    assert client.sent == [[4]]
    first, added, dismissed = _item(1), _item(2), _item(3)
    assert (first.strength, first.reason, first.related_matter) == (
        Strength.POSSIBLE,
        "varasem",
        teema,
    )
    assert (added.strength, added.reason, added.related_matter, added.status) == (
        Strength.STRONG,
        "lisatud",
        teema,
        AgendaItemStatus.ADDED,
    )
    assert (dismissed.strength, dismissed.status) == (
        Strength.UNJUDGED,
        AgendaItemStatus.DISMISSED,
    )
    assert _item(4).strength == Strength.WEAK


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_decision_made_while_the_model_answered_stands():
    person = factories.UserFactory()
    a = _agenda()

    def decide():
        agenda.dismiss(_item(2), actor=person)

    agenda.judge(
        a, agenda.candidates_ai.Usage(), client=Client_({1: "STRONG", 2: "STRONG"}, during=decide)
    )

    assert _item(1).strength == Strength.STRONG
    second = _item(2)
    assert second.status == AgendaItemStatus.DISMISSED and second.strength == Strength.UNJUDGED


@override_settings(REGISTER_SWEEP_AI=True)
def test_an_agenda_with_nothing_left_to_ask_is_closed_without_a_request():
    a = _agenda({"strength": Strength.NONE})
    client = Client_()

    _run(client)

    assert client.sent == []
    a.refresh_from_db()
    assert a.judged_at is not None and a.ai_attempts == 0


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_teema_id_never_offered_links_nothing():
    secret = factories.MatterFactory(visibility="RESTRICTED")
    _agenda({})

    _run(Client_({1: {"number": 1, "strength": "STRONG", "related": str(secret.pk)}}))

    assert _item(1).strength == Strength.STRONG and _item(1).related_matter is None


# -- failures and their bound ---------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [
        lambda: Client_(fail=RuntimeError("down")),
        lambda: Client_({1: "STRONG"}, stop="max_tokens"),
        lambda: Client_(text="{not json"),
        lambda: Client_(text='{"items": []}'),
        lambda: Client_({7: "STRONG", 1: "MAYBE"}),
    ],
    ids=["request-failed", "cut-off", "unreadable", "empty", "no-usable-verdict"],
)
@override_settings(REGISTER_SWEEP_AI=True)
def test_a_failed_attempt_is_retried_only_up_to_the_limit(make):
    client = make()
    a = _agenda()

    for _ in range(agenda.MAX_ATTEMPTS + 3):
        _run(client)

    assert len(client.sent) == agenda.MAX_ATTEMPTS
    a.refresh_from_db()
    assert a.judged_at is None and a.ai_attempts == agenda.MAX_ATTEMPTS
    assert not AgendaItem.objects.exclude(strength=Strength.UNJUDGED).exists()


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_failure_then_an_answer_ends_the_retries():
    a = _agenda()
    _run(Client_(fail=RuntimeError("down")))

    answered = Client_({1: "STRONG", 2: "NONE", 3: "NONE"})
    _run(answered)
    _run(answered)

    assert len(answered.sent) == 1
    a.refresh_from_db()
    assert a.judged_at is not None and a.ai_attempts == 2


def test_with_the_model_off_nothing_is_counted():
    a = _agenda()

    _run(Client_({1: "STRONG"}))

    a.refresh_from_db()
    assert a.judged_at is None and a.ai_attempts == 0


# -- what the page says ---------------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_the_page_says_which_items_are_unjudged():
    head = factories.DepartmentHeadFactory()
    _agenda()
    _run(Client_({1: "STRONG"}))
    client = Client()
    client.force_login(head)

    html = client.get(reverse("register_watch:agendas")).content.decode()

    assert "2 punkti hindamata" in html
    assert html.count('<span class="regwatch__strength">Hindamata</span>') == 2


# -- who is told ----------------------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_new_agenda_left_partly_unjudged_is_still_told_to_the_head(monkeypatch):
    from app.register_watch.models import CandidateNotice

    head = factories.DepartmentHeadFactory()
    found = valitsus.Agenda(
        url="https://valitsus.ee/uudised/uus",
        title="Valitsuse 19.11.26 istungi kommenteeritud päevakord",
        session_date=SESSION,
        published=None,
        items=[
            valitsus.AgendaItem(number=n, title=f"Punkt {n}", presenter="", kind="", text="")
            for n in (1, 2)
        ],
    )
    monkeypatch.setattr(
        valitsus,
        "look",
        lambda day, **_: (
            valitsus.Lookup(day, valitsus.FOUND, agenda=found)
            if day == SESSION
            else valitsus.Lookup(day, valitsus.NOT_FOUND)
        ),
    )

    _run(Client_({1: "NONE"}))

    assert _item(2).strength == Strength.UNJUDGED
    assert CandidateNotice.objects.get(recipient=head).agendas == 1


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_retry_tells_the_owner_of_the_teema_it_proposes():
    from app.register_watch.models import RegisterNotice

    owner = factories.UserFactory()
    teema = factories.MatterFactory(owner=owner, title="Maksukorraldus")
    _agenda({})
    _run(Client_(fail=RuntimeError("down")))

    _run(Client_({1: {"number": 1, "strength": "STRONG", "related": str(teema.pk)}}))

    assert _item(1).related_matter == teema
    assert RegisterNotice.objects.filter(matter=teema).exists()


def test_with_the_model_off_no_item_is_marked_but_the_agenda_is():
    head = factories.DepartmentHeadFactory()
    _agenda()
    client = Client()
    client.force_login(head)

    html = client.get(reverse("register_watch:agendas")).content.decode()

    assert " · hindamata" in html
    assert '<span class="regwatch__strength">Hindamata</span>' not in html
