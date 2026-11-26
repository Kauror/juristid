"""«Monitooring»: new drafts, the Government agenda and the registers' list (ADR 0219).

No test contacts valitsus.ee or the model: the page is a trimmed copy of the real
19.11.26 agenda (tests/fixtures/valitsus/) and the model client is replaced.
"""

from __future__ import annotations

import datetime as dt
import json
import re
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
        raise FetchError(f"{host} vastas 404", status=404)

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
    """ADR 0229: read nightly only. An agenda published after the cycle going into
    Thursday is found by the one going into Friday (accepted), so the week is
    missing only once that Friday cycle's agenda step should have finished."""
    tallinn = agenda.TALLINN
    wednesday = dt.datetime(2026, 11, 25, 15, 0, tzinfo=tallinn)
    thursday_noon = dt.datetime(2026, 11, 26, 12, 0, tzinfo=tallinn)
    friday_before = dt.datetime(2026, 11, 27, 2, 29, tzinfo=tallinn)
    friday_after = dt.datetime(2026, 11, 27, 2, 30, tzinfo=tallinn)
    assert agenda.missing_this_week(wednesday) is False
    assert agenda.missing_this_week(thursday_noon) is False
    assert agenda.missing_this_week(friday_before) is False
    assert agenda.missing_this_week(friday_after) is True
    GovernmentAgenda.objects.create(
        url="https://valitsus.ee/a", title="x", session_date=dt.date(2026, 11, 26)
    )
    assert agenda.missing_this_week(friday_after) is False


def test_the_agenda_window_follows_tallinn_time_in_summer():
    # 2026-07-03 is a Friday in summer time (UTC+3): the window ends 02:30 Tallinn.
    monday = dt.date(2026, 6, 29)
    end = agenda.window_end(monday)
    assert end.astimezone(dt.UTC) == dt.datetime(2026, 7, 2, 23, 30, tzinfo=dt.UTC)


@override_settings(REGISTER_WATCH_FETCH=True)
def test_the_command_ends_with_a_machine_line_when_asked(monkeypatch):
    from io import StringIO

    from app.register_watch import candidates_ai

    def fake_run(**kwargs):
        return agenda.Outcome(stored=[], judged=0, usage=candidates_ai.Usage(), errors=["x"])

    monkeypatch.setattr(agenda, "run", fake_run)
    out = StringIO()
    call_command("watch_government_agenda", "--outcome-json", stdout=out)
    last = out.getvalue().strip().splitlines()[-1]
    assert last.startswith("OUTCOME-JSON ")
    assert json.loads(last.removeprefix("OUTCOME-JSON ")) == {
        "ai_calls": 0,
        "ai_failures": 0,
        "errors": 1,
        "judged": 0,
        "stored": 0,
        # r95: why a week has no agenda, and the evidence (none here).
        "inaccessible": 0,
        "missing": 0,
        "not_published": 0,
        "note": "",
        "unconfirmed": 0,
    }


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


def test_the_switch_lives_in_haldus_and_returns_there(department_head, specialist):
    """Owner, 2026-11-22: «Näita kõigile» is set in Haldus, not on Monitooring's pages."""
    haldus = reverse("haldus:osakond")
    page = _signed_in(department_head).get(haldus).content.decode()
    assert "Näita kõigile" in page
    assert "Näita kõigile" not in _signed_in(department_head).get(DRAFTS).content.decode()

    answer = _signed_in(department_head).post(
        reverse("register_watch:candidate_audience"), {"koigile": "1", "tagasi": "haldus"}
    )
    assert answer["Location"] == haldus
    assert candidate_notices.audience().everyone is True
    answer = _signed_in(department_head).post(
        reverse("register_watch:candidate_audience"), {"koigile": "0", "tagasi": "https://evil"}
    )
    assert answer["Location"] == DRAFTS


def test_the_tabs_carry_their_counts(department_head):
    from app.register_watch.models import TopicCandidate

    _agenda_with_items()  # newest week: one Tugev item
    TopicCandidate.objects.create(
        title="Pakendiseaduse eelnõu", match_key="pakendiseaduse", strength="STRONG"
    )
    first = _register_finding(department_head, "Esimene dokument", "adr:mkm:1")
    _register_finding(department_head, "Teine dokument", "adr:mkm:2", matter=first.matter)
    _register_finding(department_head, "Kolmas dokument", "adr:mkm:3")

    html = _signed_in(department_head).get(AGENDAS).content.decode()

    badges = re.findall(
        r">(Uued eelnõud|Valitsuse info|Registrite dokumendid)"
        r'<span class="tabs__badge"[^>]*>(\d+)<',
        html,
    )
    # Registrite dokumendid counts Teemad with something new (two), not documents (three).
    assert badges == [
        ("Uued eelnõud", "1"),
        ("Valitsuse info", "1"),
        ("Registrite dokumendid", "2"),
    ]
    assert '<h2 class="regwatch__section">' not in html


def test_each_teema_is_a_shut_fold_with_skip_all(department_head):
    candidate_notices.set_everyone(True)
    finding = _register_finding(department_head, "Esimene dokument", "adr:mkm:1")
    _register_finding(department_head, "Teine dokument", "adr:mkm:2", matter=finding.matter)

    html = _signed_in(department_head).get(REGISTERS, {"kelle": "koik"}).content.decode()

    assert '<details class="regwatch__teema">' in html
    assert "2 dokumenti" in html and "Märgi kõik loetuks" in html


def test_skip_all_skips_only_that_teemas_waiting_documents(department_head):
    from app.register_watch.models import FindingStatus, WatchFinding

    one = _register_finding(department_head, "Esimene dokument", "adr:mkm:1")
    _register_finding(department_head, "Teine dokument", "adr:mkm:2", matter=one.matter)
    other = _register_finding(department_head, "Teise teema dokument", "adr:mkm:3")

    answer = _signed_in(department_head).post(
        reverse("register_watch:dismiss_all", kwargs={"pk": one.matter.pk}), {"kelle": "koik"}
    )

    assert answer.status_code == 302
    statuses = dict(WatchFinding.objects.values_list("external_id", "status"))
    assert statuses == {
        "adr:mkm:1": FindingStatus.DISMISSED,
        "adr:mkm:2": FindingStatus.DISMISSED,
        "adr:mkm:3": FindingStatus.PENDING,
    }
    assert WatchFinding.objects.get(external_id="adr:mkm:1").decided_by == department_head
    other.refresh_from_db()


def test_skip_all_refuses_a_reader_and_a_teema_with_nothing_waiting(department_head, reader):
    one = _register_finding(department_head, "Esimene dokument", "adr:mkm:1")
    url = reverse("register_watch:dismiss_all", kwargs={"pk": one.matter.pk})
    assert _signed_in(reader).post(url).status_code == 404
    empty = factories.MatterFactory(owner=department_head)
    assert (
        _signed_in(department_head)
        .post(reverse("register_watch:dismiss_all", kwargs={"pk": empty.pk}))
        .status_code
        == 404
    )


def _register_finding(owner, title, external_id, matter=None):
    from app.register_watch.models import WatchFinding, WatchSource, WatchTarget

    matter = matter or factories.MatterFactory(owner=owner)
    target, _ = WatchTarget.objects.get_or_create(
        matter=matter, source=WatchSource.ADR_MKM, identifier="x", defaults={"origin": "PHRASE"}
    )
    return WatchFinding.objects.create(
        target=target, matter=matter, external_id=external_id, title=title
    )


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


# -- agenda items onto their Teema (owner, 2026-11-22: «both») ------------------------


def _tied(owner, strength=Strength.STRONG, **matter):
    teema = factories.MatterFactory(owner=owner, **matter)
    a, _ = GovernmentAgenda.objects.get_or_create(
        url="https://valitsus.ee/uudised/valitsuse-191126-istungi-kommenteeritud-paevakord",
        defaults={
            "title": "Valitsuse 19.11.26 istungi kommenteeritud päevakord",
            "session_date": SESSION,
        },
    )
    item = AgendaItem.objects.create(
        agenda=a,
        number=a.items.count() + 1,
        title="Maksukorralduse seaduse eelnõu",
        presenter="rahandusminister",
        kind="Seaduse eelnõu",
        text="Esimene lõik.\n\nTeine lõik.",
        strength=strength,
        reason="Puudutab ettevõtjaid.",
        related_matter=teema,
    )
    return teema, item


def test_a_tied_item_waits_on_the_teemas_registritest_tab(specialist):
    teema, item = _tied(specialist)
    client = _signed_in(specialist)

    tab = client.get(reverse("matters:matter_registers", kwargs={"pk": teema.pk})).content.decode()
    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": teema.pk})).content.decode()

    assert "Valitsuse istungi päevakorrast" in tab
    assert "1. Maksukorralduse seaduse eelnõu" in tab
    assert reverse("register_watch:agenda_decide", kwargs={"pk": item.pk}) in tab
    # The tab's badge counts it, as a register document would be.
    assert re.search(r'Registritest\s*<span class="tabs__badge"[^>]*>1<', detail)


def test_an_item_the_model_found_irrelevant_is_no_proposal(specialist):
    teema, _ = _tied(specialist, strength=Strength.NONE)
    tab = _signed_in(specialist).get(reverse("matters:matter_registers", kwargs={"pk": teema.pk}))
    assert "Valitsuse istungi päevakorrast" not in tab.content.decode()


def test_adding_writes_a_menetluse_areng_and_returns_to_the_tab(specialist):
    from app.matters.models import MatterProceduralDevelopment
    from app.register_watch.models import AgendaItemStatus

    teema, item = _tied(specialist)

    answer = _signed_in(specialist).post(
        reverse("register_watch:agenda_decide", kwargs={"pk": item.pk}),
        {"otsus": "lisa", "tagasi": "teema"},
    )

    assert answer["Location"] == reverse("matters:matter_registers", kwargs={"pk": teema.pk})
    development = MatterProceduralDevelopment.objects.get(matter=teema)
    assert development.title == "Valitsuse istung: Maksukorralduse seaduse eelnõu"
    assert development.occurred_on == SESSION
    assert "punkt 1 · Esitaja: rahandusminister · Tüüp: Seaduse eelnõu" in development.note
    assert "Teine lõik." in development.note and item.agenda.url in development.note
    item.refresh_from_db()
    assert item.status == AgendaItemStatus.ADDED and item.development == development
    assert item.decided_by == specialist


def test_skipping_writes_nothing_and_a_second_decision_is_refused(specialist):
    from app.matters.models import MatterProceduralDevelopment
    from app.register_watch.models import AgendaItemStatus

    teema, item = _tied(specialist)
    url = reverse("register_watch:agenda_decide", kwargs={"pk": item.pk})

    _signed_in(specialist).post(url, {"otsus": "jata"})

    item.refresh_from_db()
    assert item.status == AgendaItemStatus.DISMISSED
    assert not MatterProceduralDevelopment.objects.filter(matter=teema).exists()
    assert _signed_in(specialist).post(url, {"otsus": "lisa"}).status_code == 404


def test_a_reader_cannot_add(specialist, reader):
    _, item = _tied(specialist)
    url = reverse("register_watch:agenda_decide", kwargs={"pk": item.pk})
    assert _signed_in(reader).post(url, {"otsus": "lisa"}).status_code == 404


def test_valitsuse_info_offers_lisa_teemale_and_then_says_it_was_added(department_head):
    _, item = _tied(department_head)
    client = _signed_in(department_head)

    assert "Lisa teemale" in client.get(AGENDAS).content.decode()
    client.post(reverse("register_watch:agenda_decide", kwargs={"pk": item.pk}), {"otsus": "lisa"})
    page = client.get(AGENDAS).content.decode()

    assert "lisatud teemale" in page and "Lisa teemale" not in page


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_run_tells_the_owner_of_a_tied_teema(site, specialist):
    from app.register_watch import notices

    teema = factories.MatterFactory(owner=specialist, title="Maksukorralduse muudatused")

    agenda.run(today=SESSION, ai_client=FakeClient(related=str(teema.pk)))

    assert notices.due_unread(specialist).filter(matter=teema).count() == 1
    item = AgendaItem.objects.get(number=1)
    agenda.dismiss(item, actor=specialist)
    # Nothing waits on the Teema any more: the notice is history.
    assert notices.due_unread(specialist).filter(matter=teema).count() == 0
