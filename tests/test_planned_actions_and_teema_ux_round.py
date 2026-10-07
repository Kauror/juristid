"""One current action and dated planned ones; the rail and `Tegevused` (docs/adr/0143).

PLANNED ACTIONS
* `+ Lisa tegevus` is drawn beside a current action;
* with a current action it adds a dated planned one and supersedes nothing;
* without one it sets the current action;
* a planned action needs a date; `Muuda` and `×` act on that exact row;
* completing the current action promotes the earliest planned one — unless
  the completion names its own next step;
* the recommendation shows only when nothing is current or planned;
* closure cancels the current and every planned action;
* owner hand-over moves planned steps that follow the file.

RAIL AND TEGEVUSED
* `Puudub`, `Menetluse link` / `+ Lisa`, one-heading `Seotud materjalid`,
  stacked `Saatja`;
* a completion reads «✓ <what was done>», a step-only row is one line, and the
  chronology prints dates without a clock time;
* `Liige` sits in the organisation search's row.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.services import close_matter
from app.matters.workspace import complete_current_action
from app.related_materials.services import link_related_matters
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from app.workflow.plan import seed_standard_plan
from app.workflow.services import (
    add_planned_action,
    cancel_planned_action,
    change_planned_action,
    set_next_action_for_new_work,
)
from tests import factories

pytestmark = pytest.mark.django_db


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _post(client, name: str, matter, data: dict, **kwargs):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs}),
        data,
        headers={"HX-Request": "true"},
    )


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _current(matter) -> NextAction:
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def _planned(matter) -> list[str]:
    return list(
        NextAction.objects.filter(matter=matter, status=ActionStatus.PLANNED)
        .order_by("target_date", "created_at", "pk")
        .values_list("text", flat=True)
    )


@pytest.fixture
def with_current(normal_matter, specialist):
    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu läbi", target_date=_day(2), actor=specialist
    )
    return normal_matter


# ---------------------------------------------------------------------------
# Planned actions
# ---------------------------------------------------------------------------


def test_the_set_control_stays_beside_a_current_action(client, specialist, with_current):
    client.force_login(specialist)

    zone = _zone(_detail(client, with_current))

    assert 'id="lisa-planeeritud"' in zone
    assert "+ Lisa tegevus" in zone


def test_adding_beside_a_current_action_plans_and_supersedes_nothing(
    client, specialist, with_current
):
    client.force_login(specialist)
    current = _current(with_current)

    response = _post(
        client,
        "add_planned_action",
        with_current,
        {"text": "Kaasa liikmeid", "target_date": _day(9).isoformat()},
    )

    assert response.status_code == 200
    assert _current(with_current).pk == current.pk
    assert _planned(with_current) == ["Kaasa liikmeid"]
    event = ChangeEvent.objects.filter(
        matter=with_current, event_type=ChangeEventType.NEXT_ACTION_SET
    ).latest("occurred_at")
    assert event.payload["planned"] is True
    zone = _zone(response.content.decode())
    assert "Planeeritud tegevused" in zone
    assert "Kaasa liikmeid" in zone
    assert 'aria-label="Eemalda planeeritud tegevus"' in zone


def test_adding_without_a_current_action_sets_the_current_one(client, specialist, normal_matter):
    client.force_login(specialist)

    _post(
        client,
        "add_planned_action",
        normal_matter,
        {"text": "Tutvu materjaliga", "target_date": _day(3).isoformat()},
    )

    assert _current(normal_matter).text == "Tutvu materjaliga"
    assert _planned(normal_matter) == []


def test_a_planned_action_needs_a_date(client, specialist, with_current):
    client.force_login(specialist)

    response = _post(client, "add_planned_action", with_current, {"text": "Kaasa liikmeid"})

    assert "Planeeritud tegevusel peab olema kuupäev." in response.content.decode()
    assert _planned(with_current) == []


def test_planned_rows_read_by_date_then_order_planned(specialist, with_current):
    add_planned_action(matter=with_current, text="Hiljem", target_date=_day(20), actor=specialist)
    add_planned_action(matter=with_current, text="Varem", target_date=_day(5), actor=specialist)
    add_planned_action(matter=with_current, text="Samal päeval", target_date=_day(5))

    assert _planned(with_current) == ["Varem", "Samal päeval", "Hiljem"]


def test_muuda_and_remove_act_on_that_exact_row(specialist, with_current):
    first = add_planned_action(
        matter=with_current, text="Koonda tagasiside", target_date=_day(10), actor=specialist
    )
    second = add_planned_action(
        matter=with_current, text="Saada arvamus", target_date=_day(15), actor=specialist
    )

    changed = change_planned_action(
        matter=with_current,
        action_id=first.pk,
        text="Koonda tagasiside kokku",
        target_date=_day(11),
        actor=specialist,
    )
    cancel_planned_action(matter=with_current, action_id=second.pk, actor=specialist)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED and first.replaced_by_id == changed.pk
    assert second.status == ActionStatus.CANCELLED
    assert _planned(with_current) == ["Koonda tagasiside kokku"]
    assert _current(with_current).text == "Loe eelnõu läbi"


def test_a_stale_tab_cannot_change_a_row_that_is_no_longer_planned(
    client, specialist, with_current
):
    client.force_login(specialist)
    planned = add_planned_action(
        matter=with_current, text="Kaasa liikmeid", target_date=_day(9), actor=specialist
    )
    cancel_planned_action(matter=with_current, action_id=planned.pk, actor=specialist)

    response = _post(
        client,
        "change_planned_action",
        with_current,
        {"text": "Uus tekst", "target_date": _day(9).isoformat()},
        action_id=planned.pk,
    )

    assert "vahepeal muutunud" in response.content.decode()
    assert not NextAction.objects.filter(matter=with_current, text="Uus tekst").exists()


def test_completing_promotes_the_earliest_planned_action(specialist, with_current):
    add_planned_action(matter=with_current, text="Hiljem", target_date=_day(20), actor=specialist)
    add_planned_action(matter=with_current, text="Varem", target_date=_day(5), actor=specialist)

    complete_current_action(
        matter=with_current,
        author=specialist,
        action_id=_current(with_current).pk,
        body="<p>Lugesin läbi.</p>",
    )

    promoted = _current(with_current)
    assert promoted.text == "Varem" and promoted.target_date == _day(5)
    assert _planned(with_current) == ["Hiljem"]
    assert ChangeEvent.objects.filter(
        matter=with_current,
        event_type=ChangeEventType.NEXT_ACTION_SET,
        object_id=promoted.pk,
        payload__promoted=True,
    ).exists()


def test_a_completion_that_names_its_next_step_promotes_nothing(specialist, with_current):
    add_planned_action(matter=with_current, text="Varem", target_date=_day(5), actor=specialist)

    complete_current_action(
        matter=with_current,
        author=specialist,
        action_id=_current(with_current).pk,
        body="<p>Lugesin läbi.</p>",
        next_text="Helista ministeeriumi",
        next_date=_day(1),
    )

    assert _current(with_current).text == "Helista ministeeriumi"
    assert _planned(with_current) == ["Varem"]


def test_the_recommendation_is_only_the_fallback(client, specialist):
    client.force_login(specialist)
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    assert 'id="soovitus"' in _zone(_detail(client, matter))

    set_next_action_for_new_work(matter=matter, text="Oma töö", actor=specialist)
    add_planned_action(matter=matter, text="Tulevane", target_date=_day(4), actor=specialist)
    NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).update(
        status=ActionStatus.CANCELLED
    )

    zone = _zone(_detail(client, matter))
    assert 'id="soovitus"' not in zone
    assert "Tulevane" in zone


def test_closure_cancels_current_and_every_planned_action(specialist, with_current):
    add_planned_action(matter=with_current, text="Üks", target_date=_day(5), actor=specialist)
    add_planned_action(matter=with_current, text="Kaks", target_date=_day(6), actor=specialist)

    close_matter(matter=with_current, disposition=Disposition.COMPLETED, actor=specialist)

    assert not NextAction.objects.filter(
        matter=with_current, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()
    assert (
        NextAction.objects.filter(matter=with_current, status=ActionStatus.CANCELLED).count() == 3
    )


def test_owner_hand_over_moves_planned_steps_that_follow_the_file(
    specialist, other_specialist, with_current
):
    from app.matters.services import assign_matter

    planned = add_planned_action(
        matter=with_current, text="Kaasa liikmeid", target_date=_day(9), actor=specialist
    )
    assert planned.responsible_id == specialist.pk

    assign_matter(matter=with_current, owner=other_specialist, actor=specialist)

    planned.refresh_from_db()
    assert planned.responsible_id == other_specialist.pk
    assert _current(with_current).responsible_id == other_specialist.pk


# ---------------------------------------------------------------------------
# Rail
# ---------------------------------------------------------------------------


def test_rail_labels_of_this_round(client, specialist, normal_matter):
    client.force_login(specialist)

    body = _detail(client, normal_matter)

    assert "Arvamust ei ole lisatud." not in body
    assert "Puudub" in body
    assert "Menetluse lingid" not in body and "+ Lisa menetluse link" not in body
    assert 'aria-label="Lisa menetluse link">+ Lisa</a>' in body


def test_seotud_materjalid_is_one_heading_with_a_count_and_plain_rows(
    client, specialist, normal_matter
):
    client.force_login(specialist)
    other = factories.MatterFactory(owner=specialist, title="Teine eelnõu")
    link_related_matters(matter=normal_matter, other=other, actor=specialist)

    body = _detail(client, normal_matter)
    section = body[body.index('id="seotud-materjalid"') :]
    section = section[: section.index("</section>")]

    assert "Seotud teemad" not in section
    assert re.search(r'Seotud materjalid <span class="sectionlabel__count">1</span>', section)
    assert ">Teine eelnõu</a>" in section
    assert 'aria-label="Eemalda seos: Teine eelnõu"' in section
    assert ">Ava<" not in section


def test_an_empty_seotud_materjalid_card_is_only_the_add(client, specialist, normal_matter):
    client.force_login(specialist)

    body = _detail(client, normal_matter)
    section = body[body.index('id="seotud-materjalid"') :]
    section = section[: section.index("</section>")]

    assert "sectionlabel__count" not in section
    assert "+ Lisa" in section


def test_saatja_is_stacked_one_sender_per_line(client, specialist):
    client.force_login(specialist)
    matter = factories.MatterFactory(owner=specialist)
    first = factories.OrganisationFactory(name="Justiits- ja Digiministeerium")
    second = factories.OrganisationFactory(name="Rahandusministeerium")
    matter.source_organisations.add(first, second)

    body = _detail(client, matter)

    assert "railcard__row--stacked" in body
    assert '<span class="railcard__line">Justiits- ja Digiministeerium</span>' in body
    assert '<span class="railcard__line">Rahandusministeerium</span>' in body


# ---------------------------------------------------------------------------
# Tegevused
# ---------------------------------------------------------------------------


def _kaik(body: str) -> str:
    return body[body.index('id="ajajoon"') :]


def test_a_completion_reads_as_what_was_done(client, specialist, with_current):
    client.force_login(specialist)
    complete_current_action(
        matter=with_current,
        author=specialist,
        action_id=_current(with_current).pk,
        body="<p>Lugesin VTK läbi ja tegin märkmed.</p>",
    )

    kaik = _kaik(_detail(client, with_current))

    assert 'class="uxtl__check" aria-hidden="true">✓</span>' in kaik
    assert '<span class="uxtl__donetext"' in kaik
    assert "Lugesin VTK läbi ja tegin märkmed.</span>" in kaik
    assert "märkis eelmise sammu tehtuks" not in kaik


def test_a_step_only_row_is_one_line_and_dates_carry_no_clock(client, specialist, normal_matter):
    client.force_login(specialist)
    set_next_action_for_new_work(
        matter=normal_matter,
        text="Kaasa liikmeid / küsi tagasisidet",
        target_date=_day(4),
        actor=specialist,
    )
    # The current step is not repeated in the history; once it is done, the
    # row that set it is history like any other.
    complete_current_action(
        matter=normal_matter,
        author=specialist,
        action_id=_current(normal_matter).pk,
        body="<p>Kaasatud.</p>",
    )

    kaik = _kaik(_detail(client, normal_matter))

    assert "Järgmine samm – Kaasa liikmeid / küsi tagasisidet" in kaik
    assert kaik.count("Kaasa liikmeid / küsi tagasisidet") == 1
    times = re.findall(r'<time class="uxtl__time"[^>]*>([^<]*)</time>', kaik)
    assert times and all(":" not in value for value in times)


def test_the_next_step_under_a_save_is_plain_text(client, specialist, with_current):
    client.force_login(specialist)
    complete_current_action(
        matter=with_current,
        author=specialist,
        action_id=_current(with_current).pk,
        body="<p>Tehtud.</p>",
        next_text="Saada arvamus",
        next_date=_day(3),
    )

    kaik = _kaik(_detail(client, with_current))

    assert '<span class="uxtl__nextlabel">Järgmine samm</span>' in kaik
    start = kaik.index('<span class="uxtl__nextlabel">Järgmine samm</span>')
    next_line = kaik[start : start + 400]
    assert "Saada arvamus" in next_line
    assert "→" not in next_line
    # The ✓ line does not also say it set a step: the line under it does.
    assert "määras järgmise sammu" not in kaik


# ---------------------------------------------------------------------------
# Liige
# ---------------------------------------------------------------------------


def test_liige_sits_in_the_organisation_search_row(client, specialist, normal_matter):
    client.force_login(specialist)

    body = _detail(client, normal_matter)

    rows = re.findall(r'<div class="orgpick__row">(.*?)<div class="orgpick__mark">', body, re.S)
    assert rows, "Liige is not in the organisation picker's row"
    assert all('class="orgfind"' in row for row in rows)
