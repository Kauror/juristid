"""The compact Teema round (owner's brief, 2026-10-07): display only.

A. `+ Lisa tegevus` names the add-action control; `+ Määra järgmine tegevus`
   is gone from it.
B. Planned actions render as a plain dated list — no `Planeeritud tegevused`
   heading — and behave exactly as before.
C. `Arvamuse tähtaeg` is not repeated inside `PRAEGUNE TEGEVUS`; the header
   still states it.
D. `Menetluse link` shows the address and `Muuda`, not the kind label.
E. `Seotud materjalid`: the count and `+ Lisa` in the heading row, one add
   control, title and `×` in one row, unlink still works.
F. `Teema andmed` carries no `Teemaviide`.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import ProceduralLinkKind
from app.matters.models import MatterProceduralLink
from app.related_materials.models import MatterRelation
from app.related_materials.services import link_related_matters
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import add_planned_action, set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _block(body: str, element_id: str, end: str) -> str:
    start = body.index(f'id="{element_id}"')
    return body[start : body.index(end, start)]


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


@pytest.fixture
def with_current(normal_matter, specialist):
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu läbi", target_date=_day(10), actor=specialist
    )
    return normal_matter


# ---------------------------------------------------------------------------
# A. `+ Lisa tegevus`
# ---------------------------------------------------------------------------


def test_the_add_action_control_is_lisa_tegevus_beside_a_current_action(
    client, specialist, with_current
):
    client.force_login(specialist)

    zone = _zone(_detail(client, with_current))

    assert '<summary class="disclosure-chip">+ Lisa tegevus</summary>' in zone
    assert "Määra järgmine tegevus" not in zone


def test_the_add_action_control_is_lisa_tegevus_with_nothing_current(
    client, specialist, normal_matter
):
    client.force_login(specialist)

    zone = _zone(_detail(client, normal_matter))

    assert "+ Lisa tegevus" in zone
    assert "Määra järgmine tegevus" not in zone


# ---------------------------------------------------------------------------
# B. Planned actions
# ---------------------------------------------------------------------------


def test_planned_rows_render_under_the_control_without_a_heading(client, specialist, with_current):
    client.force_login(specialist)
    add_planned_action(
        matter=with_current, text="Kontrolli uut versiooni", target_date=_day(16), actor=specialist
    )
    add_planned_action(
        matter=with_current, text="Helista ministeeriumile", target_date=_day(9), actor=specialist
    )

    zone = _zone(_detail(client, with_current))

    assert "Planeeritud tegevused</h3>" not in zone
    assert "curact__plannedhead" not in zone
    # The list keeps its name for assistive technology.
    assert 'aria-label="Planeeritud tegevused"' in zone
    rows = re.findall(r'<span class="curact__plannedtext">([^<]+)</span>', zone)
    assert rows == ["Helista ministeeriumile", "Kontrolli uut versiooni"]
    assert zone.index("+ Lisa tegevus") < zone.index("Helista ministeeriumile")
    assert zone.count('aria-label="Eemalda planeeritud tegevus"') == 2


def test_planned_behaviour_is_unchanged(client, specialist, with_current):
    client.force_login(specialist)

    client.post(
        reverse("matters:add_planned_action", kwargs={"pk": with_current.pk}),
        {"text": "Saada tagasiside", "target_date": _day(16).isoformat()},
        headers={"HX-Request": "true"},
    )

    assert NextAction.objects.get(matter=with_current, status=ActionStatus.OPEN).text == (
        "Loe eelnõu läbi"
    )
    assert list(
        NextAction.objects.filter(matter=with_current, status=ActionStatus.PLANNED).values_list(
            "text", flat=True
        )
    ) == ["Saada tagasiside"]


# ---------------------------------------------------------------------------
# C. Response deadline
# ---------------------------------------------------------------------------


def test_the_response_deadline_is_stated_in_the_header_and_not_under_the_task(client, specialist):
    client.force_login(specialist)
    deadline = _day(-3)
    matter = factories.MatterFactory(owner=specialist, response_deadline=deadline)
    set_next_action_for_new_work(
        matter=matter, text="Jälgin menetlust", target_date=_day(20), actor=specialist
    )

    body = _detail(client, matter)
    zone = _zone(body)

    assert "curact__owed" not in zone
    assert "Arvamuse tähtaeg" not in zone
    header = body[: body.index('id="praegune-tegevus"')]
    assert '<span class="metaline__label">Arvamuse tähtaeg</span>' in header
    assert f"{deadline.day}.{deadline.month}.{deadline.year}" in header


# ---------------------------------------------------------------------------
# D. Menetluse link
# ---------------------------------------------------------------------------


def test_the_procedural_link_shows_the_address_and_muuda_only(client, specialist, normal_matter):
    client.force_login(specialist)
    link = MatterProceduralLink.objects.create(
        matter=normal_matter,
        kind=ProceduralLinkKind.OTHER,
        url="https://eelnoud.valitsus.ee/main/mount/docList/abc",
        created_by=specialist,
    )

    card = _block(_detail(client, normal_matter), "menetluse-lingid", 'id="seotud-materjalid"')

    # The visible row: the address and `Muuda`. The kind is still asked inside
    # `Muuda`'s own form, which is not the row.
    row = card[: card.index('<details class="proclink__fix"')]
    assert link.get_kind_display() not in row
    assert "Muu menetluslink" not in row
    assert "railcard__key" not in row
    assert 'href="https://eelnoud.valitsus.ee/main/mount/docList/abc"' in card
    assert ">Muuda</summary>" in card
    assert link.kind  # the stored kind is untouched


# ---------------------------------------------------------------------------
# E. Seotud materjalid
# ---------------------------------------------------------------------------


def _section(body: str) -> str:
    return _block(body, "seotud-materjalid", "</section>")


def test_the_heading_row_carries_the_count_and_one_add(client, specialist, normal_matter):
    client.force_login(specialist)
    other = factories.MatterFactory(
        owner=specialist, title="NÄIDIS – Kutse- ja oskusseaduse eelnõu"
    )
    link_related_matters(matter=normal_matter, other=other, actor=specialist)

    section = _section(_detail(client, normal_matter))

    assert re.search(r'Seotud materjalid <span class="sectionlabel__count">1</span></h2>', section)
    assert section.count(">+ Lisa</summary>") == 1
    # One add control, placed in the heading row by the rail's CSS.
    assert section.count("relatedmaterials__picker") == 1


def test_a_related_item_is_its_title_and_its_x_in_one_row(client, specialist, normal_matter):
    client.force_login(specialist)
    other = factories.MatterFactory(
        owner=specialist, title="NÄIDIS – Kutse- ja oskusseaduse eelnõu"
    )
    link_related_matters(matter=normal_matter, other=other, actor=specialist)

    section = _section(_detail(client, normal_matter))
    row = re.search(r'<li class="factrow relatedmaterials__row"[^>]*>(.*?)</li>', section, re.S)

    assert row, "no related row"
    inside = row.group(1)
    assert f'href="{reverse("matters:matter_detail", kwargs={"pk": other.pk})}"' in inside
    assert ">NÄIDIS – Kutse- ja oskusseaduse eelnõu</a>" in inside
    assert 'title="Eemalda seos"' in inside
    assert ">×</button>" in inside
    assert ">Ava<" not in inside
    assert other.display_reference not in inside


def test_unlinking_still_removes_only_the_relation(client, specialist, normal_matter):
    client.force_login(specialist)
    other = factories.MatterFactory(owner=specialist, title="Teine teema")
    link_related_matters(matter=normal_matter, other=other, actor=specialist)

    response = client.post(
        reverse("related_materials:unlink", kwargs={"pk": normal_matter.pk}),
        {"teema": str(other.pk)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert not MatterRelation.objects.filter(
        matter_a__in=(normal_matter, other), matter_b__in=(normal_matter, other)
    ).exists()
    other.refresh_from_db()
    assert other.is_open
    assert not re.search(
        r'Seotud materjalid <span class="sectionlabel__count">', response.content.decode()
    )


# ---------------------------------------------------------------------------
# F. Teema andmed
# ---------------------------------------------------------------------------


def test_teema_andmed_carries_no_reference(client, specialist, normal_matter):
    client.force_login(specialist)

    body = _detail(client, normal_matter)
    card = body[body.index('id="teema-andmed"') :]
    card = card[: card.index('id="koja-arvamus"')]

    assert "Teemaviide" not in card
    assert "railcard__ref" not in card
    assert normal_matter.display_reference not in card
    assert "Saatja" in card
