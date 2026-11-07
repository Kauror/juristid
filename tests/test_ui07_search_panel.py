"""UI-07/UI-08: Täpsem otsing, grouped — every control still writes its parameter.

The URL meanings are proved in `test_ui07_url_semantics.py`. This module holds
the panel to its side of the bargain: every dimension that had a control still
has one, in the group the owner named; the dimensions with no control ride
along on submit instead of vanishing; a shut group lists its active
constraints as removable chips; and «Tühjenda kõik» keeps the columns and the
page size.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from app.matters import register_filters
from app.matters.views import PANEL_DATE_RANGES, PANEL_GROUPS, PANEL_LINK_ONLY
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")

#: Every parameter the panel had a control for on the base (38efc4ca).
CONTROLLED_ON_THE_BASE = (
    "ulatus",
    "aasta",
    "vastutaja",
    "hetkeseis",
    "menetlusliik",
    "valdkond",
    "oigusakt",
    "liik",
    "paritolu",
    "tegevus",
    "arvamus",
    "saadetud_alates",
    "saadetud_kuni",
    "too",
    "toovoit",
    "joustumine",
    "joustub_alates",
    "joustub_kuni",
    "materjalid",
    "andmed",
    "saabus_alates",
    "saabus_kuni",
    "tahtaeg_alates",
    "tahtaeg_kuni",
    "samm_alates",
    "samm_kuni",
    "viimane_alates",
    "viimane_kuni",
    "muudetud_alates",
    "muudetud_kuni",
    "asutus",
    "saatja",
    "adressaat",
    "jarjestus",
)


@pytest.fixture
def signed_in(client):
    client.force_login(factories.DepartmentHeadFactory())
    return client


def panel_of(response) -> str:
    body = response.content.decode()
    return body.split('id="tapsem-otsing"', 1)[1].split('aria-label="Kirje seis"', 1)[0]


def names_in(markup: str) -> set[str]:
    return set(re.findall(r'\sname="([^"{}]+)"', markup))


def test_no_control_was_lost(signed_in):
    names = names_in(panel_of(signed_in.get(REGISTER)))

    missing = [name for name in CONTROLLED_ON_THE_BASE if name not in names]
    assert not missing, missing


def test_the_groups_are_in_the_owner_s_order(signed_in):
    panel = panel_of(signed_in.get(REGISTER))

    order = [
        panel.index("Põhifiltrid"),
        panel.index("Kuupäevad"),
        panel.index('id="otsing-asutused"'),
        panel.index('id="otsing-muud"'),
        panel.index('name="jarjestus"'),
    ]
    assert order == sorted(order)
    # Kirje liik — the archive — is in the compact secondary group.
    muud = panel[panel.index('id="otsing-muud"') :]
    assert 'name="liik" value="ARCHIVE"' in muud
    # Both institution directions and the either-role one, side by side.
    asutused = panel[panel.index('id="otsing-asutused"') : panel.index('id="otsing-muud"')]
    for field in ("asutus", "saatja", "adressaat"):
        assert f'<select class="field__input" name="{field}">' in asutused


def test_every_date_constraint_has_its_own_pair_of_boxes(signed_in):
    panel = panel_of(signed_in.get(REGISTER))
    dates = panel[panel.index("Kuupäevad") : panel.index('id="otsing-asutused"')]

    assert len(PANEL_DATE_RANGES) == 7
    for _label, start, end in PANEL_DATE_RANGES:
        assert f'name="{start}"' in dates and f'name="{end}"' in dates
    assert 'name="aasta"' in dates


def test_several_date_constraints_apply_at_once_and_are_redisplayed(signed_in):
    old = factories.MatterFactory(title="Vana", received_date=dt.date(2024, 1, 10))
    factories.MatterFactory(title="Uus", received_date=dt.date(2026, 1, 10))
    old.response_deadline = dt.date(2024, 2, 1)
    old.save()

    response = signed_in.get(
        REGISTER,
        {"saabus_alates": "1.1.2024", "saabus_kuni": "2024-12-31", "tahtaeg_kuni": "1.3.2024"},
    )
    assert [m.title for m in response.context["page"].object_list] == ["Vana"]
    panel = panel_of(response)
    assert 'name="saabus_kuni" value="31.12.2024"' in panel
    assert 'name="tahtaeg_kuni" value="1.3.2024"' in panel


def test_an_unreadable_date_stays_in_its_box_with_the_others(signed_in):
    """The pipeline stops at the first unreadable date; the boxes after it keep theirs."""
    panel = panel_of(
        signed_in.get(REGISTER, {"saabus_alates": "31.02.2026", "viimane_alates": "1.1.2026"})
    )

    assert 'name="saabus_alates" value="31.02.2026"' in panel
    assert 'name="viimane_alates" value="1.1.2026"' in panel


@pytest.mark.parametrize("name", PANEL_LINK_ONLY)
def test_a_dimension_with_no_control_rides_along_on_submit(signed_in, name):
    value = "2026" if name == register_filters.CLOSED_YEAR_PARAM else "on"
    panel = panel_of(signed_in.get(REGISTER, {name: value, "too": "hilinenud"}))

    assert f'<input type="hidden" name="{name}" value="{value}">' in panel


def test_carried_dimensions_are_said_as_removable_chips(signed_in):
    factories.TagFactory(key="halduskoormus", name_et="Halduskoormus")
    response = signed_in.get(REGISTER, {"silt": "halduskoormus", "allikas": "on", "aasta": "2026"})
    panel = panel_of(response)

    carried = panel.split("filtergroup__chips--carried", 1)[1].split("</div>", 1)[0]
    assert "Silt:" in carried and "Ajalooline allikas:" in carried
    # Each × removes its own dimension and keeps the rest.
    hrefs = re.findall(r'href="\?([^"]*)"', carried)
    assert any(
        "silt" not in href and "allikas=on" in href and "aasta=2026" in href for href in hrefs
    )


def test_a_shut_group_lists_its_constraints_as_chips(signed_in):
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    committee = factories.OrganisationFactory(name="Näidiskomisjon")
    response = signed_in.get(
        REGISTER,
        {"saatja": str(ministry.pk), "adressaat": str(committee.pk), "liik": "ARCHIVE"},
    )
    panel = panel_of(response)

    groups = response.context["panel_groups"]
    assert [chip["name"] for chip in groups["asutused"]["chips"]] == ["saatja", "adressaat"]
    assert [chip["name"] for chip in groups["muud"]["chips"]] == ["liik"]
    # Shut on arrival; the count is in the heading and the chips under it.
    assert '<details class="filtergroup__disclosure" id="otsing-asutused" >' in panel
    assert "· 2 valitud" in panel and "· 1 valitud" in panel
    after = panel.split('id="otsing-asutused"', 1)[1].split("</details>", 1)[1]
    chips = after.split('id="otsing-muud"', 1)[0]
    assert "Näidisministeerium" in chips and "Näidiskomisjon" in chips
    # Removing the sender keeps the addressee: A AND B is two constraints.
    sender = next(c for c in groups["asutused"]["chips"] if c["name"] == "saatja")
    assert "saatja" not in sender["remove_query"]
    assert f"adressaat={committee.pk}" in sender["remove_query"]


def test_a_typed_institution_search_without_javascript_opens_its_group(signed_in):
    response = signed_in.get(REGISTER, {"asutus_otsing": "mini"})

    assert response.context["panel_groups"]["asutused"]["open"] is True
    assert '<details class="filtergroup__disclosure" id="otsing-asutused" open>' in panel_of(
        response
    )


def test_minu_omad_is_one_box_writing_the_same_parameter(signed_in):
    panel = panel_of(signed_in.get(REGISTER, {"ulatus": "minu"}))

    assert re.search(r'<input type="checkbox" name="ulatus" value="minu"\s+checked>', panel)
    assert '<select class="field__input" name="ulatus">' not in panel


def test_a_two_origin_statistika_value_stays_selected(signed_in):
    panel = panel_of(signed_in.get(REGISTER, {"paritolu": "LEGACY_IMPORT,PROMOTED_LEGACY"}))

    assert (
        '<option value="LEGACY_IMPORT,PROMOTED_LEGACY" selected>'
        "Imporditud registrist, Arhiivist aktiveeritud</option>"
    ) in panel


def test_reset_keeps_columns_and_page_size(signed_in):
    panel = panel_of(
        signed_in.get(REGISTER, {"hetkeseis": "puudub", "kaupa": "30", "veerud": "vastutaja"})
    )

    reset = re.search(r'<a class="button button--quiet" href="\?([^"]*)">Tühjenda kõik</a>', panel)
    assert reset is not None
    assert "kaupa=30" in reset.group(1) and "veerud=vastutaja" in reset.group(1)
    assert "hetkeseis" not in reset.group(1)


def test_the_groups_name_only_real_parameters():
    every = set(register_filters.MULTI_VALUE_PARAMS) | {
        "asutus",
        "saatja",
        "adressaat",
        "paritolu",
        "toovoit",
        "joustumine",
        "materjalid",
        "andmed",
    }
    for names in PANEL_GROUPS.values():
        assert set(names) <= every
