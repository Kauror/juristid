"""A new next step opens on today (owner's UX package, 2026-10-28).

ADR 0052, addendum of 2026-10-28. On the Teema page's `+ Lisa tegevus` the
box holds today's date in Europe/Tallinn, `Täpne päev` is chosen and `Täna` is
drawn pressed; `✓ Tehtud`'s `Järgmine tegevus` → `Millal?` opens on today too.
What must not change with it:

* `Muuda` opens on the step's own date and precision, an undated one on none;
* a refused save redraws what was posted, an emptied box included;
* an emptied box still saves a step with no date (docs/adr/0106);
* `Kuu`, `Kvartal` and `Aasta` read only their own controls, so the day left in
  the hidden box is never what is stored;
* the form without a precision control, the planned-step form and `Alusta`
  keep an empty box.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.forms import (
    CompleteCurrentActionForm,
    NextActionForm,
    PlannedActionForm,
    StartPlanStepForm,
)
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _panel(body: str) -> str:
    """`#lisa-jargmine`, from its id to the end of its form."""
    section = body[body.index('id="lisa-jargmine"') :]
    return section[: section.index("</form>")]


def _box_tag(panel: str) -> str:
    tag = re.search(r'<input[^>]*name="target_date"[^>]*>', panel)
    assert tag is not None, "no date box in the panel"
    return tag.group(0)


def _box(panel: str) -> str | None:
    """The value the `target_date` box is drawn with, or ``None`` for an empty box."""
    tag = re.search(r'<input[^>]*name="target_date"[^>]*>', panel)
    assert tag is not None, "no date box in the panel"
    value = re.search(r'value="([^"]*)"', tag.group(0))
    return value.group(1) if value else None


def _chip(panel: str, label: str) -> str:
    tag = re.search(r'<button[^>]*data-label-default="' + re.escape(label) + r'"[^>]*>', panel)
    assert tag is not None, f"no «{label}» chip"
    return tag.group(0)


def _post(client, matter, payload):
    return client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _today_text() -> str:
    today = timezone.localdate()
    return f"{today.day}.{today.month}.{today.year}"


# ---------------------------------------------------------------------------
# The new step
# ---------------------------------------------------------------------------


def test_a_new_step_opens_on_today_with_tana_pressed(signed_in, normal_matter):
    panel = _panel(_detail(signed_in, normal_matter))

    assert _box(panel) == _today_text()
    assert re.search(r'value="EXACT"[^>]*checked', panel)
    assert 'aria-pressed="true"' in _chip(panel, "Täna")
    assert "is-selected" in _chip(panel, "Täna")
    for other in ("Homme", "+1 nädal", "+2 nädalat"):
        assert 'aria-pressed="false"' in _chip(panel, other)
    # Renewed to Tallinn's today by static/js/app.js if the page sits open overnight.
    assert "data-today-default" in _box_tag(panel)


def test_saving_the_default_files_the_step_for_today(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        {"text": "Helista", "next_precision": "EXACT", "target_date": _today_text()},
    )

    assert response.status_code == 200
    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.target_date == timezone.localdate()
    assert action.date_precision == DatePrecision.EXACT


def test_an_emptied_box_still_saves_a_step_with_no_date(signed_in, normal_matter):
    response = _post(
        signed_in, normal_matter, {"text": "Helista", "next_precision": "EXACT", "target_date": ""}
    )

    assert response.status_code == 200
    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.target_date is None


@pytest.mark.parametrize(
    ("precision", "answers", "anchor"),
    [
        (
            DatePrecision.MONTH,
            {"next_month": "11", "next_year": "2027"},
            datetime.date(2027, 11, 1),
        ),
        (
            DatePrecision.QUARTER,
            {"next_quarter": "3", "next_year": "2027"},
            datetime.date(2027, 7, 1),
        ),
        (DatePrecision.YEAR, {"next_year": "2028"}, datetime.date(2028, 1, 1)),
    ],
)
def test_a_period_ignores_the_day_left_in_the_hidden_box(
    signed_in, normal_matter, precision, answers, anchor
):
    response = _post(
        signed_in,
        normal_matter,
        {
            "text": "Koosta arvamus",
            "next_precision": precision,
            "target_date": _today_text(),
            **answers,
        },
    )

    assert response.status_code == 200
    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert (action.target_date, action.date_precision) == (anchor, precision)


def test_a_period_left_unanswered_is_refused_not_filed_for_today(signed_in, normal_matter):
    response = _post(
        signed_in,
        normal_matter,
        {"text": "Koosta arvamus", "next_precision": "MONTH", "target_date": _today_text()},
    )

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_a_refused_save_redraws_the_posted_day_not_today(signed_in, normal_matter):
    response = _post(
        signed_in, normal_matter, {"text": "", "next_precision": "EXACT", "target_date": "5.1.2031"}
    )

    assert response.status_code == 400
    panel = _panel(response.content.decode())
    assert _box(panel) == "5.1.2031"
    assert 'aria-pressed="true"' not in _chip(panel, "Täna")
    assert "data-today-default" not in _box_tag(panel)


def test_a_refused_save_with_an_emptied_box_keeps_it_empty(signed_in, normal_matter):
    response = _post(
        signed_in, normal_matter, {"text": "", "next_precision": "EXACT", "target_date": ""}
    )

    assert response.status_code == 400
    assert _box(_panel(response.content.decode())) in (None, "")


# ---------------------------------------------------------------------------
# An existing step keeps its own
# ---------------------------------------------------------------------------


def test_muuda_opens_on_the_steps_own_date(signed_in, normal_matter, specialist):
    set_next_action(
        matter=normal_matter,
        text="Olemasolev",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=datetime.date(2031, 3, 3),
        actor=specialist,
    )

    panel = _panel(_detail(signed_in, normal_matter))

    assert _box(panel) == "3.3.2031"
    assert 'aria-pressed="true"' not in _chip(panel, "Täna")
    assert "data-today-default" not in _box_tag(panel)


def test_muuda_on_an_undated_step_stays_undated(signed_in, normal_matter, specialist):
    set_next_action(matter=normal_matter, text="Kuupäevata", actor=specialist)

    assert _box(_panel(_detail(signed_in, normal_matter))) in (None, "")


def test_muuda_on_a_month_keeps_the_month(signed_in, normal_matter, specialist):
    set_next_action(
        matter=normal_matter,
        text="Oktoobris",
        target_date=datetime.date(2031, 10, 1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    panel = _panel(_detail(signed_in, normal_matter))

    assert re.search(r'value="MONTH"[^>]*checked', panel)
    assert _box(panel) in (None, "")


# ---------------------------------------------------------------------------
# Europe/Tallinn, not UTC
# ---------------------------------------------------------------------------


def test_today_is_tallinns_today_at_half_past_ten_utc(monkeypatch):
    """22:30 UTC on 27 October is 00:30 on the 28th in Tallinn (EET, UTC+2)."""
    moment = datetime.datetime(2026, 10, 27, 22, 30, tzinfo=datetime.UTC)
    monkeypatch.setattr(timezone, "now", lambda: moment)

    form = NextActionForm(periods=True)

    assert form.initial["target_date"] == datetime.date(2026, 10, 28)
    assert form.quick_date_text == "28.10.2026"
    assert CompleteCurrentActionForm().initial["next_date"] == datetime.date(2026, 10, 28)


# ---------------------------------------------------------------------------
# Where the default does not reach
# ---------------------------------------------------------------------------


def test_the_other_next_step_hosts_keep_an_empty_box():
    # The form without a precision control: no host renders it today (Uus
    # teema no longer asks for a first step), and it must not default if one
    # does.
    assert "target_date" not in NextActionForm().initial
    assert "target_date" not in NextActionForm(prefix="next").initial
    # A planned step, and `Alusta`.
    assert "target_date" not in PlannedActionForm().initial
    assert "target_date" not in StartPlanStepForm().initial


def test_tehtud_opens_the_next_step_on_today_and_drops_it_without_a_sentence():
    form = CompleteCurrentActionForm()
    assert form.initial["next_date"] == timezone.localdate()

    bound = CompleteCurrentActionForm(
        {
            "action_id": "00000000-0000-0000-0000-000000000001",
            "body": "Tegin",
            "next_text": "",
            "next_date": _today_text(),
        }
    )
    bound.is_valid()
    assert bound.cleaned_data["next_date"] is None
