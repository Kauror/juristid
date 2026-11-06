"""UI-07/UI-06: every address the register is reached by keeps its meaning.

The November 2026 UI round reorganised Täpsem otsing and removed the «Arhiiv»
status tab. Neither change is allowed to move a single row: a link from
Osakond, Ülevaade, Statistika, Minu asjad, a saved view or somebody's bookmark
must open exactly the Teemad it opened before.

This module is the proof. It builds one fixed synthetic department — the
Statistika world plus the records that tell the dimensions apart (an *open*
archive row, so archive is not closed; a file sent by A *and* addressed to B;
a commencement and a work victory; a collaboration) — and runs a fixed list of
query strings through the real register view. The list was built from the
incoming links found in the code (`register_url` callers in
`department_dashboard`, `overview`, `my_work`, `reporting.selectors`, the
saved views, the two hard-coded template links, the retired-page redirects)
and from the controls' own parameters.

The expected result sets in ``ui07_url_semantics.json`` were **recorded on the
base commit** (`38efc4ca`, before any UI-05..UI-08 change) with
``UI07_RECORD=1``. The test compares titles, which are fixed by the world,
rather than ids, which are not.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.enums import FactStatus
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.matters.models import Matter
from tests import factories
from tests.synthetic_statistics import build_world

pytestmark = pytest.mark.django_db

#: Wednesday 7 October 2026. Every relative date in the world hangs off it.
TODAY = dt.date(2026, 10, 7)
GOLDEN = Path(__file__).with_name("ui07_url_semantics.json")

#: `{name}` is replaced by that record's primary key at run time.
QUERY_STRINGS: tuple[str, ...] = (
    # -- status and the retired «Arhiiv» tab (UI-06) ----------------------
    "",
    "olek=avatud",
    "olek=suletud",
    "olek=koik",
    "olek=arhiiv",
    "olek=arhiiv&andmed=koik",
    "olek=arhiiv&liik=FULL",
    "olek=arhiiv&liik=ARCHIVE",
    "olek=arhiiv&q=arhiivirida",
    "olek=arhiiv&saatja={ministry}",
    "olek=koik&liik=ARCHIVE",
    "olek=avatud&liik=ARCHIVE",
    "olek=suletud&liik=ARCHIVE",
    "liik=ARCHIVE",
    "liik=FULL&liik=ARCHIVE&olek=koik",
    # -- Osakond / Ülevaade / Minu asjad figures --------------------------
    "olek=avatud&liik=FULL",
    "olek=avatud&liik=FULL&too=hilinenud",
    "olek=avatud&liik=FULL&too=tahtaeg-nadalal",
    "olek=avatud&liik=FULL&too=sekkumist",
    "olek=avatud&liik=FULL&too=muutusteta-30",
    "olek=avatud&liik=FULL&too=tahtaeg-vahemik",
    "olek=avatud&liik=FULL&too=tahtaeg-vahemik&too_alates=7.10.2026&too_kuni=31.10.2026",
    "olek=avatud&liik=FULL&too=hilinenud&too_vastutaja={sandra}",
    "olek=avatud&liik=FULL&vastutaja=puudub",
    "olek=avatud&liik=FULL&tegevus=puudub",
    "olek=avatud&liik=FULL&arvamus=koostamisel",
    "olek=avatud&liik=FULL&vastutaja=puudub&tegevus=puudub&saabus_alates=23.9.2026&saabus_kuni=7.10.2026",
    "olek=avatud&liik=FULL&saabus_alates=5.10.2026&saabus_kuni=7.10.2026",
    "olek=avatud&liik=FULL&vastutaja={sandra}",
    "olek=avatud&liik=FULL&vastutaja={sandra}&tegevus=puudub",
    "olek=avatud&liik=FULL&valdkond=maksud",
    "olek=avatud&liik=FULL&saatja={ministry}",
    "olek=suletud&liik=FULL&suletud=2026",
    "toovoit=2026&olek=koik",
    "toovoit=on",
    "toovoit=puudub&olek=koik",
    # -- saved views --------------------------------------------------------
    "olek=avatud&ulatus=minu",
    "olek=avatud&tahtaeg_alates=1.10.2026&tahtaeg_kuni=31.10.2026",
    "olek=avatud&vastutaja=puudub",
    # -- Statistika (always olek=koik&andmed=paris) ------------------------
    "olek=koik&andmed=paris",
    "olek=koik&andmed=paris&aasta=2026",
    "olek=koik&andmed=paris&aasta=2018-2026",
    "olek=koik&andmed=paris&aasta=teadmata",
    "olek=koik&andmed=paris&hetkeseis=puudub",
    "olek=koik&andmed=paris&hetkeseis=kooskolastusel",
    "olek=koik&andmed=paris&menetlusliik=DOMESTIC",
    "olek=koik&andmed=paris&menetlusliik=puudub",
    "olek=koik&andmed=paris&valdkond=puudub",
    "olek=koik&andmed=paris&silt=halduskoormus",
    "olek=koik&andmed=paris&paritolu=LEGACY_IMPORT,PROMOTED_LEGACY",
    "olek=koik&andmed=paris&paritolu=LEGACY_ONENOTE",
    "olek=koik&andmed=paris&allikas=on",
    "olek=koik&andmed=paris&allikas=puudub",
    "olek=koik&andmed=paris&allikas=mitu",
    "olek=koik&andmed=paris&liik=ARCHIVE",
    "olek=koik&andmed=paris&vastutaja=puudub",
    "olek=koik&andmed=paris&saabus_alates=2026-01-01&saabus_kuni=2026-03-31",
    "olek=avatud&tegevus=hilinenud",
    "olek=avatud&tegevus=ulevaatus",
    # -- commencements (the retired Jõustumised page) ---------------------
    "joustumine=on&olek=koik",
    "joustumine=puudub&olek=koik",
    "joustub_alates=1.7.2026&joustub_kuni=30.9.2026&olek=koik",
    "joustub_alates=1.7.2026&joustub_kuni=31.8.2026&olek=koik",
    # -- multi-value OR within, AND across ---------------------------------
    "olek=koik&valdkond=maksud&valdkond=keskkond",
    "olek=koik&vastutaja=puudub&vastutaja={sandra}",
    "olek=koik&vastutaja={sandra}&valdkond=maksud",
    "olek=koik&arvamus=koostamisel&arvamus=saadetud",
    "olek=koik&oigusakt=puudub",
    # -- institutions: either role, and A as sender AND B as addressee -----
    "olek=koik&asutus={ministry}",
    "olek=koik&asutus={committee}",
    "olek=koik&asutus=puudub",
    "olek=koik&saatja={ministry}",
    "olek=koik&adressaat={committee}",
    "olek=koik&saatja={ministry}&adressaat={committee}",
    "olek=koik&saatja=puudub",
    "olek=koik&adressaat=puudub",
    # -- the date windows, each its own fact --------------------------------
    "olek=koik&tahtaeg_alates=2026-10-01",
    "olek=koik&tahtaeg_kuni=31.12.2026",
    "olek=koik&saadetud_alates=1.1.2026&saadetud_kuni=31.12.2026",
    "olek=koik&samm_alates=1.10.2026&samm_kuni=31.10.2026",
    "olek=koik&viimane_alates=1.1.2026",
    "olek=koik&muudetud_alates=1.1.2000",
    "olek=koik&loodud_alates=1.1.2000",
    "olek=koik&saabus_alates=1.1.2026&tahtaeg_kuni=31.12.2026",
    # -- other secondary metadata ------------------------------------------
    "olek=koik&materjalid=on",
    "olek=koik&materjalid=puudub",
    "olek=koik&andmed=test",
    "olek=koik&ulatus=minu",
    # -- q, sort, page size, columns ride along without changing the set ----
    "q=aktsiis&olek=koik",
    "olek=koik&jarjestus=title&kaupa=30&veerud=pealkiri&veerud=vastutaja",
    "olek=koik&jarjestus=deadline",
    "olek=koik&asutus_otsing=mini",
    # -- an unreadable value empties the list, never widens it -------------
    "olek=koik&saabus_alates=31.02.2026",
    "olek=koik&aasta=2O24",
    "olek=koik&vastutaja=mitte-uuid",
    "olek=koik&saabus_alates=1.12.2026&saabus_kuni=1.1.2026",
    "olek=tundmatu",
)


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


def _extend(world: Any) -> dict[str, Any]:
    """The records that tell archive from closed and sender from addressee."""
    open_archive = Matter.objects.create(
        title="Avatud arhiivirida",
        record_mode=RecordMode.ARCHIVE,
        origin=MatterOrigin.LEGACY_IMPORT,
        is_open=True,
        addressee_organisation=world.committee,
    )
    open_archive.source_organisations.set([world.ministry])
    both = factories.MatterFactory(
        title="Saatja A ja adressaat B",
        owner=world.martin,
        reference_year=2026,
        addressee_organisation=world.committee,
        received_date=dt.date(2026, 10, 6),
    )
    both.source_organisations.set([world.ministry])
    reverse = factories.MatterFactory(
        title="Saatja B ja adressaat A",
        owner=world.sandra,
        reference_year=2026,
        addressee_organisation=world.ministry,
    )
    reverse.source_organisations.set([world.committee])
    shared = factories.MatterFactory(
        title="Kaasatud juhi teema", owner=world.sandra, reference_year=2026
    )
    shared.collaborators.add(world.head)
    factories.EffectiveDateFactory(
        matter=world.native_open,
        date_value=dt.date(2026, 7, 1),
        period_end=dt.date(2026, 9, 30),
        status=FactStatus.ACTIVE,
    )
    factories.WorkVictoryFactory(
        matter=world.native_waiting,
        status="CONFIRMED",
        period_date=dt.date(2026, 1, 1),
        period_end=dt.date(2026, 12, 31),
        confirmed_at=timezone.now(),
        confirmed_by=world.head,
    )
    factories.MatterFactory(
        title="Testandmete teema",
        owner=world.head,
        reference_year=2026,
        data_class=MatterDataClass.TEST,
    )
    factories.MatterFactory(
        title="Piiratud juhi teema",
        owner=world.head,
        reference_year=2026,
        visibility=Visibility.RESTRICTED,
    )
    return {
        "ministry": world.ministry.pk,
        "committee": world.committee.pk,
        "sandra": world.sandra.pk,
        "martin": world.martin.pk,
    }


def _titles(client: Any, query: str) -> list[str]:
    address = f"/teemad/?{query}&kaupa=koik" if query else "/teemad/?kaupa=koik"
    response = client.get(address)
    assert response.status_code == 200, (query, response.status_code)
    return sorted(matter.title for matter in response.context["page"].object_list)


def test_every_incoming_address_opens_the_rows_it_opened_on_the_base(client, frozen):
    world = build_world(today=TODAY)
    names = _extend(world)
    client.force_login(world.head)

    observed = {query: _titles(client, query.format(**names)) for query in QUERY_STRINGS}

    if os.environ.get("UI07_RECORD") == "1":
        GOLDEN.write_text(
            json.dumps(observed, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        pytest.skip("recorded")  # never in CI: UI07_RECORD is a local switch
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert set(expected) == set(QUERY_STRINGS)
    changed = {
        query: (expected[query], observed[query])
        for query in QUERY_STRINGS
        if expected[query] != observed[query]
    }
    assert not changed, changed


def test_the_world_tells_archive_from_closed(client, frozen):
    """The recorded sets are only a proof if archive ≠ closed in the world."""
    world = build_world(today=TODAY)
    _extend(world)
    client.force_login(world.head)

    archive = set(_titles(client, "olek=arhiiv"))
    closed = set(_titles(client, "olek=suletud"))

    assert "Avatud arhiivirida" in archive
    assert "Avatud arhiivirida" not in closed
    assert world.native_closed.title in closed
    assert world.native_closed.title not in archive
    # Neither contains the other, so no count could stand for the other set.
    assert archive - closed and closed - archive
