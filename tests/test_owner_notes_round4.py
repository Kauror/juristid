"""The owner's notes of 2026-11-23 (round 4).

* «Märgi kõik loetuks» on a Teema's «Registritest» tab takes its agenda items too.
* The Koda house style corrects what the model writes.
* The header search finds the application's own pages and settings.
* «Menetluse kulg» carries each phase's acts and a year where it changes.
* Haldus → Ülevaade draws no card headings; the Menetluse link's controls are icons.
* «Majanduspoliitika ülevaade» is reached from Osakond, not Statistika.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from django.urls import reverse

from app.matters.legal_process import KIND_MILESTONE, KIND_PHASE, RailStep
from app.matters.rail_events import PhaseActs, with_acts_and_years
from app.register_watch import house_style
from app.register_watch.models import (
    AgendaItem,
    AgendaItemStatus,
    FindingStatus,
    GovernmentAgenda,
    Strength,
    WatchFinding,
    WatchSource,
    WatchTarget,
)
from app.search import destinations
from tests import factories

# -- Koda house style -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "meant"),
    [
        (
            "näitab, kas ja kuidas kaja ettepanekuid arvestatakse.",
            "näitab, kas ja kuidas koja ettepanekuid arvestatakse.",
        ),
        (
            "Diislikütuse aktsiis – Koja enda ettepanek.",
            "Diislikütuse aktsiis – koja enda ettepanek.",
        ),
        ("vastab Koja ettepanekule", "vastab koja ettepanekule"),
        ("määruse kohta; Koda on arvamust andnud.", "määruse kohta; koda on arvamust andnud."),
        ("Kaubandus-Tööstuskoja enda arvamus.", "Kaubanduskoja enda arvamus."),
        ("saadeti Kaubandus- ja Tööstuskojale", "saadeti kaubanduskojale"),
        ("küsis kaubandus koja arvamust", "küsis kaubanduskoja arvamust"),
        ("Eesti Kaubandus- ja Tööstuskoja peadirektor", "Eesti Kaubandus-Tööstuskoja peadirektor"),
    ],
)
def test_the_house_style_corrects_the_models_slips(written, meant):
    assert house_style.fix(written) == meant


@pytest.mark.parametrize(
    "right",
    [
        "Koja hinnangul on muudatus vajalik.",
        "Eesti Kaubandus-Tööstuskoja peadirektor.",
        "Ministeeriumi vastus Looduskoostöö Koja ettepanekutele.",
        "Põllumajandus-Kaubanduskoja seisukoht.",
        "Kaja Kallas kohtus ministriga.",
        "Kodakondsuse andmine üksikisikutele.",
    ],
)
def test_the_house_style_leaves_right_text_and_other_names_alone(right):
    assert house_style.fix(right) == right


def test_every_model_is_told_the_house_style():
    from app.register_watch import agenda, ai, candidates_ai

    for system in (ai.SYSTEM, candidates_ai.SYSTEM, agenda.SYSTEM):
        assert house_style.KODA_STYLE in system


# -- «Märgi kõik loetuks» on the Teema's tab ------------------------------------------


def _finding(matter):
    target = WatchTarget.objects.create(
        matter=matter, source=WatchSource.HTM_REGISTER, identifier="973 SE", origin="TITLE"
    )
    return WatchFinding.objects.create(
        target=target, matter=matter, external_id="doc:h1", title="Muudatusettepanekud"
    )


@pytest.mark.django_db
def test_mark_all_read_on_the_tab_takes_the_documents_and_the_agenda(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Maksukorralduse eelnõu")
    finding = _finding(matter)
    session = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/a", title="Päevakord", session_date=dt.date(2026, 11, 19)
    )
    item = AgendaItem.objects.create(
        agenda=session,
        number=1,
        title="Maksukorraldus",
        strength=Strength.STRONG,
        related_matter=matter,
    )
    client.force_login(specialist)

    tab = client.get(reverse("matters:matter_registers", kwargs={"pk": matter.pk})).content.decode()
    assert "Märgi kõik loetuks" in tab

    response = client.post(
        reverse("register_watch:dismiss_all", kwargs={"pk": matter.pk}), {"tagasi": "teema"}
    )

    assert response["Location"] == reverse("matters:matter_registers", kwargs={"pk": matter.pk})
    finding.refresh_from_db()
    item.refresh_from_db()
    assert finding.status == FindingStatus.DISMISSED
    assert item.status == AgendaItemStatus.DISMISSED


@pytest.mark.django_db
def test_mark_all_read_from_monitooring_leaves_the_agenda_alone(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Maksukorralduse eelnõu")
    _finding(matter)
    session = GovernmentAgenda.objects.create(
        url="https://valitsus.ee/b", title="Päevakord", session_date=dt.date(2026, 11, 19)
    )
    item = AgendaItem.objects.create(
        agenda=session,
        number=1,
        title="Maksukorraldus",
        strength=Strength.STRONG,
        related_matter=matter,
    )
    client.force_login(specialist)

    client.post(reverse("register_watch:dismiss_all", kwargs={"pk": matter.pk}))

    item.refresh_from_db()
    assert item.status == AgendaItemStatus.PENDING


# -- the header search finds the app ---------------------------------------------------


def _request(user):
    return SimpleNamespace(user=user)


@pytest.mark.django_db
def test_the_search_finds_the_colour_switch_and_the_board_report(specialist, monkeypatch):
    monkeypatch.setattr(destinations, "_haldus_sections", lambda request: [])

    dark = destinations.find(_request(specialist), "tume")
    assert dark[0]["title"] == "Hele või tume vaade" and dark[0]["action"] == destinations.THEME

    board = destinations.find(_request(specialist), "juhatuse ülevaade")
    assert [row["title"] for row in board] == ["Majanduspoliitika ülevaade"]
    assert board[0]["url"] == reverse("reporting:report_builder")

    # Estonian letters fold: «ulevaade» finds «ülevaade».
    assert destinations.find(_request(specialist), "juhatuse ulevaade") == board


@pytest.mark.django_db
def test_the_search_offers_only_what_the_reader_may_open(reader, monkeypatch):
    monkeypatch.setattr(destinations, "_haldus_sections", lambda request: [])

    assert destinations.find(_request(reader), "uus teema") == []


@pytest.mark.django_db
def test_the_suggestions_carry_the_app_rows(client, specialist):
    client.force_login(specialist)

    payload = client.get(reverse("search:suggestions"), {"q": "kiirklahv"}).json()

    assert payload["app"][0]["url"] == reverse("core:keyboard_shortcuts")


# -- «Menetluse kulg»: acts and years ----------------------------------------------------


def test_a_phase_carries_its_acts_and_the_year_is_written_where_it_changes():
    steps = [
        RailStep("algatamine", "Algatamine", KIND_PHASE, "reached", sort_on=dt.date(2025, 4, 3)),
        RailStep("riigikogus", "Riigikogu", KIND_PHASE, "current", sort_on=dt.date(2026, 9, 14)),
        RailStep("x", "Tähtaeg", KIND_MILESTONE, "future", sort_on=dt.date(2026, 12, 1)),
    ]
    acts = {"riigikogus": PhaseActs("etapp-7", 3, (("20.10.2026", "Komisjon"),))}

    drawn = with_acts_and_years(steps, acts)

    assert [step.year_label for step in drawn] == ["2025", "2026", ""]
    assert drawn[1].acts_count == 3 and drawn[1].acts_anchor == "etapp-7"
    assert drawn[0].acts == () and drawn[2].acts == ()


# -- Haldus, Menetluse link, the report's door ----------------------------------------


@pytest.mark.django_db
def test_haldus_overview_draws_no_card_headings(client, specialist):
    client.force_login(specialist)

    body = client.get(reverse("haldus:index")).content.decode()

    assert '<h2 class="visually-hidden" id="seaded">Seaded</h2>' in body
    assert '<h2 class="visually-hidden" id="rakendus">Rakendus</h2>' in body
    assert "acctsection__title--split" in body


@pytest.mark.django_db
def test_the_board_report_opens_from_osakond_not_statistika(client, specialist):
    client.force_login(specialist)
    report = reverse("reporting:report_builder")

    assert report in client.get(reverse("matters:department")).content.decode()
    assert report not in client.get(reverse("reporting:dashboard")).content.decode()
