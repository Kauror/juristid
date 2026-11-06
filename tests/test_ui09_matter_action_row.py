"""UI-09: one action row under `PRAEGUNE TEGEVUS`, with `Lõpeta teema` at its right.

The owner's brief (6 November 2026, §12): the `LISA TEEMALE` heading and its
standalone block are gone; one row directly under the zone holds `+ Lisa
tegevus`, `+ Ülevaade / uudis`, `+ Kaasamine`, `+ Koja arvamus / kohtumine`,
`+ Lisa`, and `Lõpeta teema` at the right end of the same row. Every form,
permission and confirmation stays as it was; the row works on a Matter with no
current action; a closed Matter and a reader get no write control.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.urls import reverse

from app.core.dates import format_estonian_date
from app.matters.forms import period_initial
from app.matters.services import close_matter
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

ROW_ORDER = [
    "+ Lisa tegevus",
    "+ Ülevaade / uudis",
    "+ Kaasamine",
    "+ Koja arvamus / kohtumine",
    "+ Lisa",
    "Lõpeta teema",
]


def _step(matter, owner):
    return set_next_action(
        matter=matter,
        text="Koosta vastus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=datetime.date(2026, 11, 20),
        date_precision=DatePrecision.EXACT,
        responsible=owner,
        actor=owner,
    )


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _row(body: str) -> str:
    start = body.index('id="lisa-teemale"')
    return body[start : body.index("</section>", start)]


def _top_level_chips(row: str) -> list[str]:
    """The chips of the row itself, not of a family's sub-choices.

    A chip is a `<label for="…-valik">`; the row's own are the ones whose radio
    is in the `lisa-valik` group.
    """
    radios = re.findall(r'name="lisa-valik" id="([^"]+)"', row)
    chips = []
    for radio in radios:
        match = re.search(rf'<label class="[^"]*" for="{radio}">([^<]+)</label>', row)
        assert match, radio
        chips.append(match.group(1))
    return chips


def test_the_row_holds_the_six_controls_in_order_with_no_heading(
    signed_in, specialist, normal_matter
):
    _step(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)

    assert _top_level_chips(_row(body)) == ROW_ORDER
    assert 'id="lisa-pealkiri"' not in body
    assert ">Lisa teemale</h2>" not in body
    # One flex row: every chip and its panel are children of the same `.cx-panels`.
    row = _row(body)
    assert row.count('<div class="cx-panels">') == 1
    # `Lõpeta teema` is pushed to the right of that same row, and is last.
    assert re.search(r'class="disclosure-chip disclosure-chip--last" for="teema-lopeta-valik"', row)


def test_the_row_sits_directly_under_praegune_tegevus(signed_in, specialist, normal_matter):
    _step(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)
    zone = body.index('id="praegune-tegevus"')
    opening = body[body.rindex("<section", 0, zone) : zone]
    assert "curact--withrow" in opening
    zone_end = body.index("</section>", zone) + len("</section>")
    between = body[zone_end : body.index('<section class="addzone" id="lisa-teemale"')]
    assert between.strip() == "", "nothing stands between the zone and its row"


def test_lisa_tegevus_beside_a_current_step_is_a_planned_action(
    signed_in, specialist, normal_matter
):
    _step(normal_matter, specialist)
    row = _row(_detail(signed_in, normal_matter))
    assert 'id="lisa-planeeritud-valik"' in row
    assert reverse("matters:add_planned_action", kwargs={"pk": normal_matter.pk}) in row
    # And not the next-step editor, which is `Muuda` on the step's line.
    assert 'id="lisa-jargmine"' not in row


def test_the_row_works_on_a_matter_with_no_current_action(signed_in, normal_matter):
    assert not NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).exists()
    body = _detail(signed_in, normal_matter)
    row = _row(body)

    assert _top_level_chips(row) == ROW_ORDER
    # `+ Lisa tegevus` is the next-step editor there: the same id, form and endpoint.
    assert 'id="lisa-jargmine-valik"' in row
    assert 'id="lisa-jargmine"' in row
    assert reverse("matters:set_action", kwargs={"pk": normal_matter.pk}) in row
    assert (
        'name="action_id"'
        not in row[row.index('id="lisa-jargmine"') : row.index("+ Ülevaade / uudis")]
    )
    assert 'id="lisa-planeeritud"' not in body


def test_lisa_tegevus_posts_as_before_with_no_current_action(signed_in, normal_matter):
    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {
            "text": "Helista ministeeriumile",
            **{
                key: format_estonian_date(value) if isinstance(value, datetime.date) else value
                for key, value in period_initial(
                    "next",
                    datetime.date(2026, 11, 20),
                    DatePrecision.EXACT,
                    date_field="target_date",
                ).items()
            },
        },
    )
    assert response.status_code == 200
    assert NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN).text == (
        "Helista ministeeriumile"
    )


def test_a_refused_lisa_tegevus_reopens_in_the_row(signed_in, specialist, normal_matter):
    _step(normal_matter, specialist)
    response = signed_in.post(
        reverse("matters:add_planned_action", kwargs={"pk": normal_matter.pk}),
        {"text": "", "target_date": "20.11.2026"},
    )
    assert response.status_code == 400
    row = _row(response.content.decode())
    assert re.search(
        r'id="lisa-planeeritud-valik"\s+data-addpick aria-controls="lisa-planeeritud"\s+checked',
        row,
    )


def test_lopeta_teema_keeps_its_own_form_and_endpoint(signed_in, specialist, normal_matter):
    _step(normal_matter, specialist)
    row = _row(_detail(signed_in, normal_matter))
    panel = row[row.index('id="teema-lopeta"') :]
    assert reverse("matters:close_from_workspace", kwargs={"pk": normal_matter.pk}) in panel
    assert 'name="disposition"' in panel
    # Not the completion of the current step: that is `✓ Tehtud`, on the step's line.
    assert reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk}) not in panel


# ---------------------------------------------------------------------------
# Read-only access gets no new write control
# ---------------------------------------------------------------------------


def test_a_reader_gets_no_row(client, reader, specialist, normal_matter):
    _step(normal_matter, specialist)
    client.force_login(reader)
    body = _detail(client, normal_matter)
    assert 'id="lisa-teemale"' not in body
    assert "curact--withrow" not in body
    for chip in ROW_ORDER:
        assert f">{chip}</label>" not in body


def test_a_closed_matter_gets_no_row(signed_in, specialist, normal_matter):
    _step(normal_matter, specialist)
    close_matter(
        matter=normal_matter, disposition="COMPLETED", actor=specialist, follow_ups_confirmed=True
    )
    body = _detail(signed_in, normal_matter)
    assert 'id="lisa-teemale"' not in body
    assert "curact--withrow" not in body
    for chip in ROW_ORDER:
        assert f">{chip}</label>" not in body
