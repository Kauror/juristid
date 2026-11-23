"""What may leave in a model prompt (audit HR-01 and HR-09, 2026-11-23).

Reading is not sending. Every check here reads the **prompt the fake client was
actually sent**, never an intermediate queryset:

* a letter title goes to the model only when its pairing was applied to a
  NORMAL, real, undeleted Teema and its sent opinion is not restricted —
  RESTRICTED, TEST, unpaired, waiting (ambiguous), background and deleted are
  left out, counted, and never logged by title;
* no TEST Teema is offered for `related`, linked, or rung about;
* the cap on offered Teemad is counted rather than silent.

No external model is called: every request goes to a fake client.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from types import SimpleNamespace

import pytest
from django.test import override_settings
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.postipoiss_models import (
    PairingState,
    PostipoissLetter,
    PostipoissPairing,
)
from app.matters.enums import MatterDataClass
from app.register_watch import agenda, ai, candidates_ai, outbound
from app.register_watch.models import (
    AgendaItem,
    CandidateItem,
    GovernmentAgenda,
    RegisterNotice,
    Strength,
    TopicCandidate,
    WatchSource,
)
from tests import factories

pytestmark = pytest.mark.django_db

SESSION = dt.date(2026, 11, 19)


class FakeClient:
    """Answers every agenda item or candidate; ``related`` on the first one."""

    def __init__(self, related: str | None = None):
        self.related = related
        self.sent: list[str] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        prompt = request["messages"][0]["content"]
        self.sent.append(prompt)
        if "New drafts:\n" in prompt:
            rows = [
                {"id": d["id"], "strength": "STRONG", "reason": "põhjus", "related": self.related}
                for d in json.loads(prompt.split("New drafts:\n", 1)[1])
            ]
        else:
            items = json.loads(prompt.split("päevakord\n", 1)[1])
            rows = [
                {
                    "number": i["number"],
                    "strength": "STRONG",
                    "reason": "põhjus",
                    "related": self.related if n == 0 else None,
                }
                for n, i in enumerate(items)
            ]
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(type="text", text=json.dumps({"items": rows}))],
        )


def _agenda() -> GovernmentAgenda:
    a = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/uudised/x",
        title="Valitsuse 19.11.26 istungi kommenteeritud päevakord",
        session_date=SESSION,
    )
    AgendaItem.objects.create(agenda=a, number=1, title="Maksukorralduse eelnõu", text="Tekst.")
    return a


_kirja = iter(range(1, 10_000))


def _letter(title: str, *, matter=None, state=None, submission=None, paired=True):
    letter = PostipoissLetter.objects.create(
        kirja_id=f"K-{next(_kirja)}",
        year=2026,
        title=title,
        letter_date=SESSION - dt.timedelta(days=10),
        package_sha256="0" * 64,
    )
    if paired:
        PostipoissPairing.objects.create(
            letter=letter,
            tier="A",
            state=state or PairingState.APPLIED,
            matter=matter,
            suggested_matter=matter if state == PairingState.PENDING else None,
            submission=submission,
        )
    return letter


def _prompt(client: FakeClient) -> str:
    assert len(client.sent) == 1
    return client.sent[0]


# -- letters (HR-01) ------------------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_only_letters_reliably_on_a_sendable_teema_reach_the_prompt(caplog):
    normal = factories.MatterFactory(title="Avalik teema")
    restricted = factories.MatterFactory(title="Kinnine teema", visibility=Visibility.RESTRICTED)
    test_teema = factories.MatterFactory(title="Testteema", data_class=MatterDataClass.TEST)
    gone = factories.MatterFactory(title="Kustutatud teema")
    gone.deleted_at = timezone.now()
    gone.save(update_fields=["deleted_at"])
    secret_opinion = factories.SubmissionFactory(
        matter=normal, visibility_override=Visibility.RESTRICTED
    )

    _letter("AVALIK KIRI", matter=normal)
    _letter(
        "AVALIKU ARVAMUSE KIRI",
        matter=normal,
        submission=factories.SubmissionFactory(matter=normal),
    )
    _letter("KINNISE TEEMA KIRI", matter=restricted)
    _letter("TESTTEEMA KIRI", matter=test_teema)
    _letter("KUSTUTATUD TEEMA KIRI", matter=gone)
    _letter("KINNINE ARVAMUS", matter=normal, submission=secret_opinion)
    _letter("SIDUMATA KIRI", paired=False)
    _letter("OOTEL KIRI", matter=normal, state=PairingState.PENDING)
    _letter("TAUSTA KIRI", state=PairingState.BACKGROUND)
    client = FakeClient()
    usage = candidates_ai.Usage()

    with caplog.at_level(logging.DEBUG, logger="app.register_watch"):
        agenda.judge(_agenda(), usage, client=client)

    prompt = _prompt(client)
    assert "AVALIK KIRI" in prompt and "AVALIKU ARVAMUSE KIRI" in prompt
    for title in (
        "KINNISE TEEMA KIRI",
        "TESTTEEMA KIRI",
        "KUSTUTATUD TEEMA KIRI",
        "KINNINE ARVAMUS",
        "SIDUMATA KIRI",
        "OOTEL KIRI",
        "TAUSTA KIRI",
    ):
        assert title not in prompt
        assert title not in caplog.text
    assert usage.letters_left_out == 7


def test_a_letter_older_than_the_window_is_neither_sent_nor_counted():
    normal = factories.MatterFactory()
    old = _letter("VANA KIRI", matter=normal)
    old.letter_date = SESSION - dt.timedelta(days=200)
    old.save(update_fields=["letter_date"])

    letters = outbound.letter_titles(since=SESSION - dt.timedelta(days=120), limit=150)

    assert letters.titles == [] and letters.left_out == 0


# -- Teemad (HR-09) -------------------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_no_test_or_restricted_teema_is_offered_to_the_agenda_model():
    normal = factories.MatterFactory(title="Avalik avatud teema")
    test_teema = factories.MatterFactory(
        title="Testi avatud teema", data_class=MatterDataClass.TEST
    )
    restricted = factories.MatterFactory(
        title="Kinnine avatud teema", visibility=Visibility.RESTRICTED
    )
    client = FakeClient(related=str(test_teema.pk))

    agenda.judge(_agenda(), candidates_ai.Usage(), client=client)

    prompt = _prompt(client)
    assert "Avalik avatud teema" in prompt and str(normal.pk) in prompt
    for matter in (test_teema, restricted):
        assert matter.title not in prompt and str(matter.pk) not in prompt
    # An id it was never offered links nothing.
    assert AgendaItem.objects.get(number=1).related_matter is None


@override_settings(REGISTER_SWEEP_AI=True)
def test_no_test_or_restricted_teema_is_offered_or_linked_for_a_new_draft():
    test_teema = factories.MatterFactory(
        title="Testi eelnõu teema", data_class=MatterDataClass.TEST
    )
    restricted = factories.MatterFactory(
        title="Kinnine eelnõu teema", visibility=Visibility.RESTRICTED
    )
    candidate = TopicCandidate.objects.create(
        title="Pakendiseaduse eelnõu", match_key="pakendiseadus", status="NEW"
    )
    CandidateItem.objects.create(
        candidate=candidate,
        source=WatchSource.ADR_MKM,
        external_id="adr:mkm:1",
        title="Pakendiseaduse eelnõu",
        occurred_on=SESSION,
    )
    client = FakeClient(related=str(test_teema.pk))

    candidates_ai.judge_unjudged(candidates_ai.Usage(), client=client)

    prompt = _prompt(client)
    assert "Testi eelnõu teema" not in prompt and str(test_teema.pk) not in prompt
    assert "Kinnine eelnõu teema" not in prompt and str(restricted.pk) not in prompt
    candidate.refresh_from_db()
    assert candidate.strength == Strength.STRONG and candidate.related_matter is None


def test_a_test_teemas_owner_is_not_rung_about_an_agenda_proposal():
    owner = factories.UserFactory()
    test_teema = factories.MatterFactory(owner=owner, data_class=MatterDataClass.TEST)
    real = factories.MatterFactory(owner=owner)
    a = _agenda()
    AgendaItem.objects.create(
        agenda=a, number=2, title="Testi punkt", strength=Strength.STRONG, related_matter=test_teema
    )
    AgendaItem.objects.create(
        agenda=a, number=3, title="Päris punkt", strength=Strength.STRONG, related_matter=real
    )

    told = agenda.tell_owners([a])

    assert told == 1
    assert list(RegisterNotice.objects.values_list("matter", flat=True)) == [real.pk]


def test_the_register_watch_model_never_reads_a_test_or_restricted_teema():
    assert outbound.may_send(factories.MatterFactory())
    assert not ai.may_send(factories.MatterFactory(data_class=MatterDataClass.TEST))
    assert not ai.may_send(factories.MatterFactory(visibility=Visibility.RESTRICTED))


@override_settings(REGISTER_WATCH_AI=True)
def test_a_test_teemas_findings_are_never_sent():
    class Refuses:
        beta = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_: pytest.fail("a TEST Teema was sent"))
        )

    test_teema = factories.MatterFactory(data_class=MatterDataClass.TEST)

    assert ai.judge(test_teema, [object()], ai.Usage(), client=Refuses()) == 0


# -- the cap (HR-09) ------------------------------------------------------------------


def test_the_topic_cap_is_counted_not_silent(monkeypatch, caplog):
    monkeypatch.setattr(candidates_ai, "TOPICS_SHOWN", 2)
    newest = []
    for n in range(3):
        newest.append(factories.MatterFactory(title=f"Avatud teema {n}"))
    usage = candidates_ai.Usage()

    with caplog.at_level(logging.WARNING, logger="app.register_watch"):
        offered = candidates_ai.open_topics(usage)

    assert len(offered) == 2
    assert usage.topics_left_out == 1
    assert "1 open Teema(s)" in caplog.text
    assert not any(f"Avatud teema {n}" in caplog.text for n in range(3))


def test_under_the_cap_nothing_is_counted():
    factories.MatterFactory()
    usage = candidates_ai.Usage()

    candidates_ai.open_topics(usage)

    assert usage.topics_left_out == 0


def test_a_proposal_stored_on_a_test_teema_before_is_no_proposal():
    owner = factories.UserFactory()
    test_teema = factories.MatterFactory(owner=owner, data_class=MatterDataClass.TEST)
    a = _agenda()
    AgendaItem.objects.create(
        agenda=a, number=2, title="Vana", strength=Strength.STRONG, related_matter=test_teema
    )

    assert not agenda.proposals().exists()
    assert agenda.teema_proposals(test_teema) == []


def test_the_sweep_records_the_topic_cap_on_its_run(monkeypatch):
    from app.register_watch import candidates
    from app.register_watch.models import SweepRun

    monkeypatch.setattr(candidates_ai, "TOPICS_SHOWN", 1)
    for n in range(2):
        factories.MatterFactory(title=f"Avatud {n}")
    record = SweepRun.objects.create(started_at=timezone.now())
    usage = candidates_ai.Usage()
    candidates_ai.open_topics(usage)

    candidates._close(record, usage)

    assert any("1 avatud teemat jäi mudelile pakkumata" in e["viga"] for e in record.errors)
