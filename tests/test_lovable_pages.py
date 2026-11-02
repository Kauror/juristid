"""Uuendused → Lovable's six views and its Excel export (docs/adr/0201 §5–§7).

The pages ask the one read service (`app.legacy_import.lovable_observability`)
and nothing else, so these tests check the door and the rendering: who may
open which view, that a Teema the reader may not open and a Lovable topic with
no confirmed Juristid link never leak a title or a value into the HTML or the
workbook, that filters and pages travel in the address, and that every view
renders on an empty database. Synthetic throughout
(tests/lovable_observability_support.py); nothing reaches Lovable.
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
from typing import Any

import pytest
from django.urls import reverse
from openpyxl import load_workbook

from app.core import views as core_views
from app.core.enums import Visibility
from app.legacy_import import lovable_observability as obs
from app.legacy_import.lovable_observation_models import (
    AuditAccess,
    MappingStatus,
    ObservedChangeKind,
)
from app.legacy_import.lovable_sync_models import SyncMode
from tests import factories
from tests.lovable_observability_support import (
    Pilot,
    control,
    narrowed_reader,
    night_at,
    observation_run,
    observed,
    row_counts,
    source_event,
)

pytestmark = pytest.mark.django_db

LATER = night_at(3) + dt.timedelta(hours=2)
PAGE = "core:release_notes_lovable"
EXPORT = "core:release_notes_lovable_export"
VIEWS = list(core_views.LOVABLE_VIEWS)


@pytest.fixture(autouse=True)
def clock(monkeypatch, settings):
    """Every question is asked at `LATER`, whatever day the suite runs on."""
    settings.REAL_DATA_ALLOWED = False
    monkeypatch.setattr(core_views, "_lovable_now", lambda: LATER)


@pytest.fixture
def reader():
    return factories.UserFactory(display_name="Lugeja Näidis")


@pytest.fixture
def pilot():
    control(SyncMode.COMPARE)
    return Pilot()


def _url(view: str = "", name: str = PAGE, **params: str) -> str:
    query = {"vaade": view, **params} if view else params
    url = reverse(name)
    if query:
        url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    return url


def _get(client: Any, view: str = "", **params: str) -> str:
    response = client.get(_url(view, **params))
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def _xlsx_text(response: Any) -> str:
    assert response.status_code == 200
    assert response["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response["Cache-Control"] == "private, no-store"
    workbook = load_workbook(io.BytesIO(response.content), read_only=True)
    cells = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells.extend(str(c) for c in row if c is not None)
    return "\n".join(cells)


def _two_nights(pilot: Pilot) -> None:
    pilot.add(501)
    pilot.add(502)
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.edit("2026_502", teema="Uus sõnastus 2026_502")
    pilot.night(night_at(1))


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", VIEWS)
def test_anonymous_is_sent_to_sign_in(client, settings, view):
    settings.AUTH_MODE = "none"
    assert client.get(_url(view)).status_code == 302
    assert client.get(_url(view, name=EXPORT)).status_code == 302


def test_a_technical_administrator_is_refused_every_view_and_the_export(client, pilot):
    pilot.add(501, teema="ADMIN-EI-NAE-4411")
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="ADMIN-EI-NAE-VAARTUS")
    pilot.night(night_at(1))
    client.force_login(factories.AdministratorFactory())
    for view in VIEWS:
        response = client.get(_url(view))
        assert response.status_code == 403, view
        assert "ADMIN-EI-NAE" not in response.content.decode()
    for view in core_views.LOVABLE_EXPORTS:
        response = client.get(_url(view, name=EXPORT))
        assert response.status_code == 403, view
        assert "ADMIN-EI-NAE" not in response.content.decode()


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_renders_on_an_empty_database_and_writes_nothing(client, reader, view):
    client.force_login(reader)
    before = row_counts()
    body = _get(client, view)
    assert row_counts() == before
    assert 'aria-current="page">Lovable<' in body
    assert f'href="?vaade={view}"\n         aria-current="page"' in body
    assert "None" not in body.replace("is None", "")


def test_the_default_view_is_the_overview(client, reader):
    client.force_login(reader)
    body = _get(client)
    assert "Neli eri asja" in body and "Lugemiste vahelised erinevused" in body
    assert 'data-indicator="pilot_coverage"' in body


def test_post_is_refused(client, reader):
    client.force_login(reader)
    assert client.post(_url("oised")).status_code == 405
    assert client.post(_url("oised", name=EXPORT)).status_code == 405


def test_a_view_without_an_export_has_none(client, reader):
    client.force_login(reader)
    assert client.get(_url("valmisolek", name=EXPORT)).status_code == 404
    assert "Laadi Excel" not in _get(client, "valmisolek")
    assert "Laadi Excel" in _get(client, "oised")


def test_an_unknown_view_is_the_overview(client, reader):
    client.force_login(reader)
    assert "Neli eri asja" in _get(client, "midagi")


# ---------------------------------------------------------------------------
# What a reader sees
# ---------------------------------------------------------------------------


def test_the_nights_show_the_summary_and_the_fields_in_details(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    body = _get(client, "oised")
    assert "2 teemat muudetud" in body
    assert "Hetkeseis muutus." in body
    assert '<details class="lovtopic">' in body
    assert "Valitsuses" in body and "Ministeeriumis" in body
    assert "Vajab ülevaatamist" in body
    assert reverse("matters:matter_detail", args=[pilot.states["2026_501"].matter_id]) in body
    assert "lähtepunkt" in body
    # No raw identifiers or service keys in the page.
    assert str(pilot.states["2026_501"].source_id) not in body
    assert "REVIEW_REQUIRED" not in body and "PILOT_LINKED" not in body.replace(
        'topic.mapping == "PILOT_LINKED"', ""
    )


def test_night_filters_narrow_topics_and_say_so(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    body = _get(client, "oised", vali="stage")
    assert "Valitsuses" in body and "Uus sõnastus 2026_502" not in body
    assert "Filtriga sobib 1 teema." in body
    assert 'value="stage" selected' in body
    body = _get(client, "oised", otsi="sõnastus")
    assert "Uus sõnastus 2026_502" in body and "Valitsuses" not in body
    body = _get(client, "oised", tahelepanu="1")
    assert "Valitsuses" in body  # the stage change waits for a person


def test_an_unreadable_date_is_said_and_ignored(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    body = _get(client, "oised", algus="31.02.2026")
    assert "ei ole kuupäev" in body
    assert "Valitsuses" in body


def test_a_date_window_outside_the_nights_shows_none(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    body = _get(client, "oised", algus="1.9.2026", lopp="2.9.2026")
    assert "Valitsuses" not in body
    assert "Valitud filtriga lugemisi pole." in body


def test_pages_keep_the_filters(client, pilot, reader, monkeypatch):
    pilot.add(501)
    for n in range(obs.NIGHTS_PAGE + 2):
        pilot.night(night_at(n))
    monkeypatch.setattr(core_views, "_lovable_now", lambda: night_at(obs.NIGHTS_PAGE + 2))
    client.force_login(reader)
    body = _get(client, "oised", ulatus="piloot")
    assert "Leht 1 / 2" in body
    assert "?vaade=oised&amp;ulatus=piloot&amp;leht=2" in body
    second = _get(client, "oised", ulatus="piloot", leht="2")
    assert "Leht 2 / 2" in second
    # A broken page number is the first page, never an error.
    assert "Leht 1 / 2" in _get(client, "oised", leht="²")


def test_the_queue_lists_pending_fields_with_a_suggestion_and_no_button(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    body = _get(client, "ootel")
    assert "Hetkeseis" in body and "Valitsuses" in body
    assert "lovable_sync resolve" in body
    assert "<button" not in body.split("lovqueue", 1)[1]
    assert "Valitsuses" not in _get(client, "ootel", viide="2026_502")
    assert "Valitsuses" in _get(client, "ootel", viide="2026/501")


def _observed_world(pilot: Pilot) -> Any:
    pilot.add(501)
    candidate = factories.MatterFactory(
        reference_year=2026, reference_number=777, title="KANDIDAADI-PEALKIRI-6601"
    )
    first = observation_run(night_at(0))
    run = observation_run(night_at(1), baseline=first)
    observed(run, "2026_900", fields=[("title", "SALAJANE-VANA-6602", "SALAJANE-UUS-6603")])
    observed(
        run,
        "2026_777",
        mapping=MappingStatus.REFERENCE_CANDIDATE,
        matter=candidate,
        fields=[("next_action_text", "VAARTUS-A-6604", "VAARTUS-B-6605")],
    )
    observed(
        run,
        "2026_501",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_501"),
        source_id=pilot.states["2026_501"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", "Valitsuses")],
    )
    observed(run, "2026_950", kind=ObservedChangeKind.NEW, field_names=[])
    return candidate


UNLINKED_SENTENCE = (
    "teemades, millel pole Juristides kinnitatud vastet; "
    "üksikasju ei näidata, kuni omanik on nähtavuse otsustanud."
)


def test_unlinked_topics_are_only_counted_in_html_and_xlsx(client, pilot, reader):
    """The default (docs/adr/0201 §6): no unlinked topic's number, field name
    or candidate link in the page, the nights or the workbook — a count only."""
    candidate = _observed_world(pilot)
    client.force_login(reader)
    unlinked = ("2026_900", "2026_777", "2026_950")
    secrets = ("SALAJANE", "VAARTUS-A", "VAARTUS-B", "KANDIDAADI-PEALKIRI", *unlinked)
    candidate_url = reverse("matters:matter_detail", args=[candidate.pk])
    pages = {
        "vaadeldud": _get(client, "vaadeldud"),
        "oised-koik": _get(client, "oised", ulatus="koik"),
        "oised-operatiivsed": _get(client, "oised", ulatus="operatiivsed"),
    }
    # A search for an unlinked topic finds no topic (the query is only echoed
    # in the form) and leaves the count where it was.
    for probe in ({"otsi": "2026_900"}, {"viide": "2026_777"}):
        found = _get(client, "vaadeldud", **probe)
        assert 'class="lovnight__topic"' not in found, probe
        assert f"3 muudatust {UNLINKED_SENTENCE}" in found, probe
    for name, body in pages.items():
        for secret in secrets:
            assert secret not in body, (name, secret)
        assert candidate_url not in body, name
        assert f"3 muudatust {UNLINKED_SENTENCE}" in body, name
    body = pages["vaadeldud"]
    assert "Valitsuses" in body and "Näidisteema 2026_501" in body
    assert "Muutunud väljad kokku:" in body
    for view, params in (("vaadeldud", {}), ("oised", {"ulatus": "koik"})):
        text = _xlsx_text(client.get(_url(view, name=EXPORT, **params)))
        for secret in secrets:
            assert secret not in text, (view, secret)
        assert f"3 muudatust {UNLINKED_SENTENCE}" in text, view
        assert "ainult nende koguarv" in text
    assert "Valitsuses" in _xlsx_text(client.get(_url("vaadeldud", name=EXPORT)))


def test_observed_changes_show_mapping_and_never_unmatched_values(client, pilot, reader, settings):
    """The owner-approvable FIELD_NAMES policy: number, kind and field names."""
    settings.LOVABLE_UNLINKED_DETAIL = "FIELD_NAMES"
    candidate = _observed_world(pilot)
    client.force_login(reader)
    body = _get(client, "vaadeldud")
    for secret in ("SALAJANE", "VAARTUS-A", "VAARTUS-B", "KANDIDAADI-PEALKIRI"):
        assert secret not in body
    assert "Juristides vastet pole" in body
    assert "sama numbriga teema (seos kinnitamata)" in body
    assert reverse("matters:matter_detail", args=[candidate.pk]) in body
    assert "Valitsuses" in body and "Näidisteema 2026_501" in body
    assert "Lovable&#x27;is on uus teema." in body
    # The unmatched topic is never linked to anything.
    section = body.split('aria-label="2026_900"', 1)[1].split("</section>", 1)[0]
    assert "<a " not in section
    workbook = _xlsx_text(client.get(_url("vaadeldud", name=EXPORT)))
    for secret in ("SALAJANE", "VAARTUS-A", "VAARTUS-B", "KANDIDAADI-PEALKIRI"):
        assert secret not in workbook
    assert "Valitsuses" in workbook and "2026_900" in workbook
    # Filters: kind and mapping.
    assert "2026_900" not in _get(client, "vaadeldud", liik="uus")
    assert "2026_950" in _get(client, "vaadeldud", liik="uus")
    only_candidates = _get(client, "vaadeldud", seos="kandidaat")
    assert "2026_777" in only_candidates and "2026_900" not in only_candidates


def test_audit_unavailable_says_so_and_shows_no_event(client, pilot, reader):
    state = pilot.add(501)
    run = observation_run(night_at(0), audit=AuditAccess.DISABLED)
    source_event(
        state.source_id, night_at(0), fields=[("teema", "SYNDMUS-6611", "b", False)], run=run
    )
    client.force_login(reader)
    body = _get(client, "ajalugu")
    assert "Lovable&#x27;i muutmisajalugu pole kättesaadav" in body
    assert "SYNDMUS-6611" not in body
    assert "lovevents" not in body


def test_available_audit_events_are_listed(client, pilot, reader):
    state = pilot.add(501)
    run = observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    source_event(
        state.source_id,
        night_at(0),
        fields=[("jargmiseks", "Vana tegevus", "Uus tegevus", False), ("markmed", "", "", True)],
        run=run,
    )
    source_event(uuid.uuid4(), night_at(0), fields=[("teema", "VOORAS-6612", "x", False)])
    client.force_login(reader)
    body = _get(client, "ajalugu")
    assert "Vana tegevus → Uus tegevus" in body
    assert "väärtust ei näidata" in body
    assert "VOORAS-6612" not in body
    assert f"1 sündmus {UNLINKED_SENTENCE}" in body


def test_a_value_cut_at_ingestion_is_marked_in_the_history(client, pilot, reader):
    state = pilot.add(501)
    run = observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    source_event(
        state.source_id,
        night_at(0),
        fields=[("jargmiseks", "Vana", "PIKK-ALGUS-6613", False)],
        run=run,
        truncated=("jargmiseks",),
    )
    client.force_login(reader)
    body = _get(client, "ajalugu")
    assert "PIKK-ALGUS-6613" in body
    assert ">lühendatud</span>" in body


def test_readiness_is_an_assessment(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    body = _get(client, "valmisolek")
    assert "See on hinnang, mitte sisselülitamine." in body
    assert "Takistused" in body and "Omaniku otsused" in body
    assert "Vastendus" in body and "Katvus" in body
    assert "<form" not in body.split("lovtabs", 1)[1]


def test_the_export_writes_the_filtered_nights(client, pilot, reader):
    _two_nights(pilot)
    client.force_login(reader)
    response = client.get(_url("oised", name=EXPORT, vali="stage"))
    assert "lovable-oised-" in response["Content-Disposition"]
    text = _xlsx_text(response)
    assert "Valitsuses" in text and "Uus sõnastus 2026_502" not in text
    assert "Väli: Hetkeseis" in text
    queue = _xlsx_text(client.get(_url("ootel", name=EXPORT)))
    assert "Valitsuses" in queue and "lovable_sync resolve" in queue


def test_a_formula_looking_value_is_text_in_the_workbook(client, pilot, reader):
    pilot.add(501)
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="=HYPERLINK(1)")
    pilot.night(night_at(1))
    client.force_login(reader)
    response = client.get(_url("oised", name=EXPORT))
    workbook = load_workbook(io.BytesIO(response.content))
    values = [c for s in workbook.worksheets for row in s.iter_rows() for c in row]
    found = [c for c in values if c.value == "=HYPERLINK(1)"]
    assert found and all(c.data_type == "s" for c in found)


# ---------------------------------------------------------------------------
# A Teema the reader may not open
# ---------------------------------------------------------------------------


def test_a_restricted_teema_is_absent_from_every_view_and_export(client, pilot, monkeypatch):
    restricted_reader = narrowed_reader(monkeypatch)
    pilot.add(501)
    pilot.add(502, visibility=Visibility.RESTRICTED, teema="VARJATUD-PEALKIRI-6621")
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="Valitsuses")
    pilot.edit("2026_502", hetkeseis="SALAJANE-ETAPP-6622")
    pilot.night(night_at(1))
    first = observation_run(night_at(0), audit=AuditAccess.AVAILABLE)
    run = observation_run(night_at(1), baseline=first, audit=AuditAccess.AVAILABLE)
    observed(
        run,
        "2026_502",
        mapping=MappingStatus.PILOT_LINKED,
        matter=pilot.matter("2026_502"),
        source_id=pilot.states["2026_502"].source_id,
        in_pilot=True,
        fields=[("stage", "Ministeeriumis", "SALAJANE-ETAPP-6622")],
    )
    source_event(
        pilot.states["2026_502"].source_id,
        night_at(1),
        fields=[("hetkeseis", "x", "SALAJANE-ETAPP-6622", False)],
        run=run,
    )
    client.force_login(restricted_reader)
    secrets = ("VARJATUD-PEALKIRI", "SALAJANE-ETAPP", "2026_502")
    seen = ""
    for view in VIEWS:
        body = _get(client, view)
        seen += body
        for secret in secrets:
            assert secret not in body, (view, secret)
    for view in ("vaadeldud",):
        body = _get(client, view, ulatus="koik")
        assert "SALAJANE-ETAPP" not in body
    assert "Valitsuses" in _get(client, "oised")
    assert "ei ole sulle nähtav" in _get(client, "oised")
    for view in core_views.LOVABLE_EXPORTS:
        text = _xlsx_text(client.get(_url(view, name=EXPORT)))
        for secret in secrets:
            assert secret not in text, (view, secret)
    # A text search never finds what the reader may not see.
    assert "Valitsuses" not in _get(client, "oised", otsi="VARJATUD")


# ---------------------------------------------------------------------------
# Haldus → Lovable sünkroonimine
# ---------------------------------------------------------------------------


def test_haldus_shows_observation_counts_but_no_business_text(client, settings, pilot):
    from tests.admin_session import apply_admin_gate, technical_admin, verified

    apply_admin_gate(settings)
    settings.REAL_DATA_ALLOWED = False
    pilot.add(501, teema="HALDUS-PEALKIRI-6631")
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis="HALDUS-VAARTUS-6632")
    pilot.night(night_at(1))
    first = observation_run(night_at(0))
    observed(
        observation_run(night_at(1), baseline=first),
        "2026_900",
        fields=[("title", "HALDUS-VOORAS-6633", "HALDUS-VOORAS-6634")],
    )
    admin, secret = technical_admin()
    verified(client, admin, secret)
    response = client.get(reverse("haldus:lovable"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "Vaatlus ja valmisolek" in body
    assert "Ülevaatust ootavaid välju</dt><dd>1</dd>" in body
    assert "Valmisoleku hinnang (mitte sisselülitamine)" in body
    assert "Elavaid teemasid Lovable'is</dt><dd>400</dd>" in body
    assert "HALDUS-" not in body
    assert "2026_501" not in body
    # Raw observation data with no retention run: the monitoring row is red.
    assert "<dt>Vaatluse seire</dt>" in body
    assert "no successful retention run in the last 26 hours" in body
    assert "2026_900" not in body
