"""«Monitooring»: new drafts, the Government agenda and the registers' list (ADR 0219).

No test contacts valitsus.ee or the model: the page is a trimmed copy of the real
19.11.26 agenda (tests/fixtures/valitsus/) and the model client is replaced.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from app.core.enums import Visibility
from app.notifications import inbox
from app.register_watch import agenda, candidate_notices
from app.register_watch.models import AgendaItem, CandidateNotice, GovernmentAgenda, Strength
from app.register_watch.sources import valitsus
from app.register_watch.sources.http import FetchError
from tests import factories

pytestmark = pytest.mark.django_db

PAGE = (Path(__file__).parent / "fixtures" / "valitsus" / "agenda-191126.html").read_text(
    encoding="utf-8"
)
SESSION = dt.date(2026, 11, 19)
HOME = reverse("register_watch:home")
DRAFTS = reverse("register_watch:candidates")
AGENDAS = reverse("register_watch:agendas")
REGISTERS = reverse("register_watch:review")


def _signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def site(monkeypatch):
    """valitsus.ee answers the 19.11.26 agenda and 404 for every other address."""
    asked: list[str] = []

    def fake(host, path):
        asked.append(path)
        if path == valitsus.candidate_paths(SESSION)[0]:
            return PAGE
        raise FetchError(f"{host} vastas 404")

    monkeypatch.setattr(valitsus, "get_text", fake)
    return SimpleNamespace(asked=asked)


class FakeClient:
    """Answers items 1 and 6 STRONG, 7 POSSIBLE, 15 WEAK, the rest NONE."""

    def __init__(self, related: str | None = None):
        self.related = related
        self.sent: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.sent.append(request)
        prompt = request["messages"][0]["content"]
        items = json.loads(prompt.split("istungi kommenteeritud päevakord\n", 1)[1])
        verdict = {1: "STRONG", 6: "STRONG", 7: "POSSIBLE", 15: "WEAK"}
        rows = [
            {
                "number": i["number"],
                "strength": verdict.get(i["number"], "NONE"),
                "reason": f"põhjus {i['number']}",
                "related": self.related if i["number"] == 1 else None,
            }
            for i in items
        ]
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            content=[SimpleNamespace(type="text", text=json.dumps({"items": rows}))],
        )


# -- reading the page ----------------------------------------------------------------


def test_the_real_agenda_page_reads_every_item():
    found = valitsus.parse("https://valitsus.ee/x", PAGE)

    assert found.session_date == SESSION
    assert found.published == dt.datetime(2026, 11, 18, 19, 10)
    assert [item.number for item in found.items] == list(range(1, 26))
    first = found.items[0]
    assert first.title == "Maksukorralduse seaduse ja teiste seaduste muutmise seaduse eelnõu"
    assert first.presenter == "rahandusminister Jürgen Ligi"
    assert first.kind == "Seaduse eelnõu"
    assert "juhatuse liikme vastutust" in first.text
    # Item 10 is written without bold, its labels in paragraphs of their own.
    tenth = found.items[9]
    assert tenth.title == "Eesti kodakondsuse andmine (2 isikut)"
    assert tenth.presenter == "siseminister Igor Taro"
    assert tenth.kind == "Korralduse eelnõu"
    assert tenth.text.startswith("Siseminister teeb ettepaneku")


def test_a_page_that_is_not_an_agenda_is_refused():
    with pytest.raises(FetchError, match="ei ole kommenteeritud päevakord"):
        valitsus.parse("https://valitsus.ee/x", "<h1>Pressiteade</h1><p>tekst</p>")


def test_both_address_shapes_are_tried():
    assert valitsus.candidate_paths(dt.date(2026, 10, 1)) == [
        "/uudised/valitsuse-11026-istungi-kommenteeritud-paevakord",
        "/uudised/valitsuse-1-oktoober-2026-istungi-kommenteeritud-paevakord",
    ]


# -- a run ----------------------------------------------------------------------------


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_run_stores_the_week_once_and_the_model_judges_it(site, department_head):
    open_teema = factories.MatterFactory(title="Maksukorralduse muudatused")
    secret = factories.MatterFactory(title="Salajane", visibility=Visibility.RESTRICTED)
    client = FakeClient(related=str(open_teema.pk))

    outcome = agenda.run(today=SESSION, ai_client=client)

    stored = GovernmentAgenda.objects.get()
    assert outcome.stored == [stored]
    assert stored.session_date == SESSION and stored.judged_at is not None
    assert stored.items.count() == 25
    prompt = client.sent[0]["messages"][0]["content"]
    assert "Salajane" not in prompt and str(secret.pk) not in prompt
    first = stored.items.get(number=1)
    assert first.strength == Strength.STRONG
    assert first.related_matter == open_teema
    assert stored.items.filter(strength=Strength.NONE).count() == 21

    # The bell: one notice for the head, carrying the agenda, opening «Valitsuse info».
    row = CandidateNotice.objects.get(recipient=department_head)
    assert row.agendas == 1 and row.candidates == 0
    item = next(i for i in inbox.inbox_for(department_head) if i.type == "candidates")
    assert item.title == "Valitsuse info"
    assert item.url == AGENDAS
    assert item.note == "valitsuse päevakord"

    # The same week again reads nothing and tells nobody again.
    asked = len(site.asked)
    again = agenda.run(today=SESSION, ai_client=client)
    assert again.stored == [] and len(site.asked) == asked
    assert CandidateNotice.objects.get(recipient=department_head).agendas == 1


def test_without_the_model_the_agenda_is_stored_unjudged_and_told(site, department_head):
    agenda.run(today=SESSION, use_ai=True)

    stored = GovernmentAgenda.objects.get()
    assert stored.judged_at is None
    assert not stored.items.exclude(strength="").exists()
    assert CandidateNotice.objects.get(recipient=department_head).agendas == 1


@override_settings(REGISTER_SWEEP_AI=True)
def test_an_id_the_model_was_never_offered_names_nothing(site):
    secret = factories.MatterFactory(title="Salajane", visibility=Visibility.RESTRICTED)

    agenda.run(today=SESSION, ai_client=FakeClient(related=str(secret.pk)))

    assert AgendaItem.objects.get(number=1).related_matter is None


def test_a_missing_week_is_said_once_the_window_closed():
    tallinn = agenda.TALLINN
    wednesday = dt.datetime(2026, 11, 25, 15, 0, tzinfo=tallinn)
    thursday_noon = dt.datetime(2026, 11, 26, 12, 0, tzinfo=tallinn)
    assert agenda.missing_this_week(wednesday) is False
    assert agenda.missing_this_week(thursday_noon) is True
    GovernmentAgenda.objects.create(
        url="https://valitsus.ee/a", title="x", session_date=dt.date(2026, 11, 26)
    )
    assert agenda.missing_this_week(thursday_noon) is False


def test_the_command_refuses_while_reading_is_off():
    with pytest.raises(CommandError, match="reading is off"):
        call_command("watch_government_agenda")


# -- who sees Monitooring ------------------------------------------------------------


def _agenda_with_items() -> GovernmentAgenda:
    a = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/uudised/x",
        title="Valitsuse 19.11.26 istungi kommenteeritud päevakord",
        session_date=SESSION,
    )
    AgendaItem.objects.create(
        agenda=a,
        number=1,
        title="Maksukorralduse eelnõu",
        strength=Strength.STRONG,
        reason="Puudutab ettevõtjaid.",
        text="Esimene lõik.\n\nTeine lõik.",
    )
    AgendaItem.objects.create(
        agenda=a, number=2, title="Kodakondsuse andmine", strength=Strength.NONE
    )
    AgendaItem.objects.create(
        agenda=a, number=3, title="Sanktsioonide ülevaatus", strength=Strength.WEAK
    )
    older = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/uudised/y",
        title="Valitsuse 12.11.26 istungi kommenteeritud päevakord",
        session_date=dt.date(2026, 11, 12),
    )
    AgendaItem.objects.create(
        agenda=older, number=1, title="Vanem punkt", strength=Strength.POSSIBLE
    )
    return a


def test_monitooring_is_the_heads_until_opened(department_head, specialist, reader):
    _agenda_with_items()
    for path in (DRAFTS, AGENDAS, REGISTERS):
        assert _signed_in(department_head).get(path).status_code == 200
        assert _signed_in(specialist).get(path).status_code == 404
        assert _signed_in(reader).get(path).status_code == 404

    candidate_notices.set_everyone(True)
    for path in (DRAFTS, AGENDAS, REGISTERS):
        assert _signed_in(specialist).get(path).status_code == 200
        assert _signed_in(reader).get(path).status_code == 200


def test_the_bar_shows_monitooring_to_who_may_see_it(department_head, specialist):
    link = f'href="{HOME}">Monitooring</a>'
    my_work = reverse("matters:my_work")
    assert link in _signed_in(department_head).get(my_work).content.decode()
    assert link not in _signed_in(specialist).get(my_work).content.decode()
    candidate_notices.set_everyone(True)
    assert link in _signed_in(specialist).get(my_work).content.decode()


def test_osakond_no_longer_carries_the_link(department_head):
    page = _signed_in(department_head).get(reverse("matters:department")).content.decode()
    assert "Uued eelnõud</a>" not in page


def test_the_agenda_page_opens_the_newest_week_and_folds_the_rest(department_head):
    _agenda_with_items()

    html = _signed_in(department_head).get(AGENDAS).content.decode()

    newest = html.index("Valitsuse 19.11.26")
    older = html.index("Valitsuse 12.11.26")
    assert newest < older
    # The one open fold is the newest week's.
    assert html.count('<details class="agenda" open>') == 1
    assert html.index('<details class="agenda" open>') < newest
    assert "Kontrollimist vajavad võimalikud seosed (1)" in html
    assert "Ülejäänud punktid (1)" in html
    assert "<p>Esimene lõik.</p><p>Teine lõik.</p>" in html


def test_the_switch_returns_to_the_section_it_was_pressed_on(department_head):
    answer = _signed_in(department_head).post(
        reverse("register_watch:candidate_audience"), {"koigile": "1", "tagasi": "valitsus"}
    )
    assert answer["Location"] == AGENDAS
    answer = _signed_in(department_head).post(
        reverse("register_watch:candidate_audience"), {"koigile": "0", "tagasi": "https://evil"}
    )
    assert answer["Location"] == DRAFTS


def test_the_old_addresses_lead_into_monitooring(department_head):
    client = _signed_in(department_head)
    assert client.get("/arengud/?kelle=koik")["Location"] == f"{REGISTERS}?kelle=koik"
    assert client.get("/arengud/uued-eelnoud/")["Location"] == DRAFTS


def test_a_lawyer_still_decides_on_the_teemas_own_tab(specialist):
    """Monitooring's list is the head's; a Teema's «Registritest» tab is not."""
    from app.register_watch.models import FindingStatus, WatchFinding, WatchSource, WatchTarget

    matter = factories.MatterFactory(owner=specialist)
    target = WatchTarget.objects.create(
        matter=matter, source=WatchSource.ADR_MKM, identifier="x", origin="PHRASE"
    )
    finding = WatchFinding.objects.create(
        target=target, matter=matter, external_id="adr:mkm:1", title="Dokument"
    )

    answer = _signed_in(specialist).post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}),
        {"otsus": "jata", "tagasi": "teema"},
    )

    assert answer["Location"] == reverse("matters:matter_registers", kwargs={"pk": matter.pk})
    finding.refresh_from_db()
    assert finding.status == FindingStatus.DISMISSED
