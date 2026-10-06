"""The Teema UI cleanup round (docs/adr/0140): less UI, fewer decisions.

* `PRAEGUNE TEGEVUS` — `✓ Tehtud` is a toggle that stays on the row, and the
  row is `✓ Tehtud` and `Muuda`; `Lisa märge` is gone;
* the completion form — no `Järgmisena` choice; `Uus hetkeseis` through the
  canonical stage move, `Järgmine tegevus` written directly, and `Millal?`
  with `+1 päev` / `+1 nädal` / `+1 kuu`;
* `+ Lisa` and `Lisa liik` in `LISA TEEMALE`;
* one form convention: optional fields are plain, required ones carry a small
  red `*`, and «valikuline» is gone;
* `Tegevused · N kirjet` is a heading, not a fold, and the filter is gone.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django import forms
from django.conf import settings
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import add_months, format_estonian_date
from app.core.required_fields import is_required, marks_required
from app.matters.models import Entry, MatterStageEpisode
from app.matters.services import change_stage
from app.matters.workspace import TERMINAL_STAGE_MAKES_NO_STEP
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _detail(client, matter, query: str = "") -> str:
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    return client.get(url + query).content.decode()


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _action(matter, actor, text: str = "Koosta Koja arvamus komisjonile") -> NextAction:
    return set_next_action(
        matter=matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=2),
        actor=actor,
    )


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _row(zone: str) -> str:
    """The current action's own line — the task, its date and its controls."""
    start = zone.index('class="curact__task"')
    return zone[start : zone.index('id="tehtud"', start)]


def _finish(client, matter, action, **extra: str):
    return client.post(
        reverse("matters:complete_current_action", kwargs={"pk": matter.pk}),
        {"action_id": str(action.pk), "body": "<p>Saatsin kavandi üle.</p>", **extra},
        headers={"HX-Request": "true"},
    )


@pytest.fixture
def staged(specialist):
    """In `Kooskõlastusringil`, with that period recorded the canonical way."""
    matter = factories.MatterFactory(owner=specialist)
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    matter.refresh_from_db()
    return matter


# ---------------------------------------------------------------------------
# PRAEGUNE TEGEVUS
# ---------------------------------------------------------------------------


def test_the_row_is_tehtud_and_muuda_and_nothing_else(signed_in, normal_matter, specialist):
    _action(normal_matter, specialist)

    row = _row(_zone(_detail(signed_in, normal_matter)))

    assert "Koosta Koja arvamus komisjonile" in row
    assert "✓ Tehtud" in row
    assert "Muuda" in _zone(_detail(signed_in, normal_matter))
    assert "Lisa märge" not in _detail(signed_in, normal_matter)
    assert "data-open-note" not in _detail(signed_in, normal_matter)


def test_tehtud_is_a_toggle_that_stays_on_the_row(signed_in, normal_matter, specialist):
    """A checkbox and its label on the row, the form a sibling after them: the
    chip is never inside the panel it opens, so opening it cannot move it, and
    pressing it again closes it — natively."""
    _action(normal_matter, specialist)

    zone = _zone(_detail(signed_in, normal_matter))

    assert re.search(r'<input class="addpick" type="checkbox" id="tehtud-valik"', zone)
    chip = zone.index('for="tehtud-valik">✓ Tehtud</label>')
    panel = zone.index('id="tehtud"')
    assert chip < panel
    assert "✓ Tehtud" not in zone[panel:]
    assert "<details" not in zone[zone.index("tehtud-valik") - 200 : panel]


def test_tehtud_stays_visible_with_its_panel_open_after_a_refusal(
    signed_in, normal_matter, specialist
):
    action = _action(normal_matter, specialist)

    response = _finish(signed_in, normal_matter, action, body="")

    assert response.status_code == 400
    zone = _zone(response.content.decode())
    assert re.search(r'type="checkbox" id="tehtud-valik"\s+aria-controls="tehtud"\s+checked', zone)
    assert 'for="tehtud-valik">✓ Tehtud</label>' in zone


# ---------------------------------------------------------------------------
# The completion form
# ---------------------------------------------------------------------------


def test_the_form_asks_no_next_action_type(signed_in, normal_matter, specialist):
    _action(normal_matter, specialist)

    form = _zone(_detail(signed_in, normal_matter))
    form = form[form.index('id="praegune-tegevus-vorm"') : form.index("</form>")]

    assert 'name="next_choice"' not in form
    for gone in ("Järgmisena", "Praegu ei määra", "Muu tegevus", "valikuline"):
        assert gone not in form, gone
    assert "Järgmine tegevus" in form
    assert "Uus hetkeseis" in form
    assert "Millal?" in form
    # The order the brief asks for: what I did, the stage, the next action, when.
    assert (
        form.index("Mida tegid?")
        < form.index("Uus hetkeseis")
        < form.index("Järgmine tegevus")
        < form.index("Millal?")
        < form.index("Salvesta tegevus")
    )


def test_uus_hetkeseis_arrives_as_jatan_muutmata(signed_in, staged, specialist):
    _action(staged, specialist)

    zone = _zone(_detail(signed_in, staged))
    select = zone[zone.index('id="id_praegune_hetkeseis"') :]
    select = select[: select.index("</select>")]

    first_option = re.search(r"<option[^>]*>([^<]*)</option>", select)
    assert first_option is not None
    assert first_option.group(1).strip() == "Jätan muutmata"
    assert 'value="" selected' in select


def test_a_blank_next_action_opens_no_step(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)

    response = _finish(signed_in, normal_matter, action, next_text="", next_date="")

    assert response.status_code == 200
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert not NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).exists()


def test_a_written_next_action_is_opened_with_its_day(signed_in, normal_matter, specialist):
    action = _action(normal_matter, specialist)
    week = timezone.localdate() + timedelta(days=7)

    _finish(
        signed_in,
        normal_matter,
        action,
        next_text="Kohtun ministeeriumiga",
        next_date=format_estonian_date(week),
    )

    following = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert following.text == "Kohtun ministeeriumiga"
    assert following.target_date == week
    assert following.plan_step_id is None


def test_a_chosen_stage_moves_the_file_canonically_in_the_same_save(signed_in, staged, specialist):
    """The same `stage_transition` `+ Lisa · Tavaline` uses: one
    `MATTER_STAGE_CHANGED`, in the same operation as the note, and the note is
    the period it was written in — the old one."""
    action = _action(staged, specialist)
    government = _stage("government")
    old_period = MatterStageEpisode.objects.get(matter=staged, is_current=True)
    moves_before = ChangeEvent.objects.filter(
        matter=staged, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ).count()

    response = _finish(
        signed_in, staged, action, stage=str(government.pk), next_text="Jälgin valitsust"
    )

    assert response.status_code == 200
    staged.refresh_from_db()
    assert staged.stage_id == government.pk
    moves = ChangeEvent.objects.filter(
        matter=staged, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ).order_by("created_at")
    assert moves.count() == moves_before + 1
    entry = Entry.objects.get(matter=staged)
    added = ChangeEvent.objects.get(event_type=ChangeEventType.ENTRY_ADDED, object_id=entry.pk)
    assert moves.last().operation_id == added.operation_id
    assert added.stage_episode_id == old_period.pk
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert (
        NextAction.objects.get(matter=staged, status=ActionStatus.OPEN).text == "Jälgin valitsust"
    )


def test_jatan_muutmata_moves_nothing(signed_in, staged, specialist):
    action = _action(staged, specialist)

    moves_before = ChangeEvent.objects.filter(
        matter=staged, event_type=ChangeEventType.MATTER_STAGE_CHANGED
    ).count()

    _finish(signed_in, staged, action, stage="")

    staged.refresh_from_db()
    assert staged.stage.key == "consultation"
    assert (
        ChangeEvent.objects.filter(
            matter=staged, event_type=ChangeEventType.MATTER_STAGE_CHANGED
        ).count()
        == moves_before
    )


def test_a_closing_stage_closes_the_file_and_refuses_a_next_action(signed_in, staged, specialist):
    action = _action(staged, specialist)
    in_force = _stage("in_force")

    refused = _finish(signed_in, staged, action, stage=str(in_force.pk), next_text="Jälgin")

    assert refused.status_code == 400
    assert TERMINAL_STAGE_MAKES_NO_STEP in refused.content.decode()
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert not Entry.objects.filter(matter=staged).exists()

    closed = _finish(signed_in, staged, action, stage=str(in_force.pk))

    assert closed.status_code == 200
    staged.refresh_from_db()
    assert not staged.is_open
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED


def test_the_quick_days_fill_the_ordinary_date_box(signed_in, normal_matter, specialist):
    """`+1 päev`, `+1 nädal`, `+1 kuu` — server-resolved days in the box's own
    format, beside the ordinary calendar box they write into."""
    _action(normal_matter, specialist)
    today = timezone.localdate()

    zone = _zone(_detail(signed_in, normal_matter))
    when = zone[zone.index('data-quickdate-group="id_praegune_jargmine_kuupaev"') :]
    when = when[: when.index("</div>")]

    for label, day in (
        ("+1 päev", today + timedelta(days=1)),
        ("+1 nädal", today + timedelta(days=7)),
        ("+1 kuu", add_months(today, 1)),
    ):
        assert f'data-quickdate="{format_estonian_date(day)}"' in when, label
        assert f">{label}</button>" in when
    # The calendar box is still the field that is submitted.
    assert 'id="id_praegune_jargmine_kuupaev"' in when
    assert 'name="next_date"' in when


# ---------------------------------------------------------------------------
# + Lisa
# ---------------------------------------------------------------------------


def test_the_launcher_says_lisa_and_lisa_liik(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    zone = body[body.index('id="lisa-teemale"') :]

    assert 'for="lisa-marge-valik">+ Lisa</label>' in zone
    assert ">Lisa liik</span>" in zone
    assert "+ Märge" not in body
    assert "Märke liik" not in body
    for kind in ("Tavaline", "Oluline tähtaeg", "Jõustumine", "Töövõit"):
        assert f">{kind}</label>" in zone, kind


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


MARK = '<span class="req-mark" aria-hidden="true">*</span>'


def test_no_rendered_teema_form_says_valikuline(signed_in, normal_matter, specialist):
    _action(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)

    assert "valikuline" not in body.lower()


@pytest.mark.parametrize("route", ["matters:matter_create", "matters:matter_edit"])
def test_the_matter_forms_mark_the_title_and_nothing_optional(signed_in, normal_matter, route):
    kwargs = {} if route == "matters:matter_create" else {"pk": normal_matter.pk}
    body = signed_in.get(reverse(route, kwargs=kwargs)).content.decode()

    assert "valikuline" not in body.lower()
    assert re.search(r"Pealkiri\s*" + re.escape(MARK), body)
    assert body.count(MARK) >= 1


def test_required_labels_carry_the_mark_and_optional_ones_do_not(
    signed_in, normal_matter, specialist
):
    _action(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)

    for required in ("Mida tegid?", "Keda kaasati", "Mis tähtaeg", "Mis jõustub", "Mis muutus"):
        assert re.search(re.escape(required) + r"\s*" + re.escape(MARK), body), required
    # A planned action's day is required (docs/adr/0143), so its panel is read
    # on its own and the rest of the page keeps the optional «Millal?».
    start = body.index('id="lisa-planeeritud"')
    planned = body[start : body.index("</details>", start)]
    rest = body[:start] + body[start + len(planned) :]
    assert re.search(re.escape("Millal?") + r"\s*" + re.escape(MARK), planned)
    for optional in ("Vastuseid", "Järgmine tegevus", "Uus hetkeseis", "Millal?"):
        assert not re.search(re.escape(optional) + r"\s*" + re.escape(MARK), rest), optional


def test_no_template_carries_the_old_optional_marker():
    """The convention is the tag, not a word typed into templates."""
    roots = [Path(settings.BASE_DIR) / "templates"]
    offenders = [
        str(path)
        for root in roots
        for path in root.rglob("*.html")
        if 'class="cx-f__opt"' in path.read_text(encoding="utf-8")
        or "(valikuline)" in path.read_text(encoding="utf-8")
        or ">valikuline<" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_field_label_reads_the_field_not_the_template():
    class Example(forms.Form):
        plain = forms.CharField(label="Märkus", required=False)
        needed = forms.CharField(label="Pealkiri")
        cleaned = marks_required(forms.CharField(label="Keda kaasati", required=False))

    form = Example()
    rendered = Template(
        "{% load form_labels %}"
        "[{% field_label form.plain %}][{% field_label form.needed %}]"
        "[{% field_label form.cleaned %}][{% field_label mapping %}]"
    ).render(Context({"form": form, "mapping": {"label": "Vali <kaks>"}}))

    assert "[Märkus]" in rendered
    assert "[Pealkiri" + MARK in rendered
    assert "[Keda kaasati" + MARK in rendered
    assert "[Vali &lt;kaks&gt;]" in rendered
    assert not is_required(form["plain"])
    assert is_required(form["cleaned"])


# ---------------------------------------------------------------------------
# Tegevused
# ---------------------------------------------------------------------------


def test_tegevused_is_a_heading_with_its_count_and_not_a_fold(signed_in, normal_matter, specialist):
    _action(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)
    section = body[body.index('id="ajajoon"') - 60 :]
    head = section[: section.index('class="accordion__body"')]

    assert '<section class="accordion accordion--timeline" id="ajajoon"' in section
    assert ">Tegevused</h2>" in head
    assert re.search(r"\d+ kirjet?", head)
    assert "<summary" not in head
    assert "<details" not in head


def test_the_period_accordions_still_fold_one_by_one(signed_in, staged, specialist):
    _action(staged, specialist)

    body = _detail(signed_in, staged)

    assert re.search(r'<details class="kaikstage[^"]*"', body)
    assert 'class="kaikstage__head"' in body


def test_the_filter_is_gone_and_its_parameters_change_nothing(signed_in, normal_matter, specialist):
    _action(normal_matter, specialist)

    plain = _detail(signed_in, normal_matter)
    with_old_query = _detail(
        signed_in, normal_matter, "?alates=1.1.2030&kuni=2.1.2030&liik=tahtajad"
    )

    for gone in (
        "tlfilter",
        'name="alates"',
        'name="kuni"',
        'name="liik"',
        "Filtreeri",
        "Tühjenda filter",
    ):
        assert gone not in plain, gone
    count = re.search(r"(\d+) kirjet?", plain[plain.index('id="ajajoon"') :])
    count_again = re.search(
        r"(\d+) kirjet?", with_old_query[with_old_query.index('id="ajajoon"') :]
    )
    assert count is not None and count_again is not None
    assert count.group(1) == count_again.group(1)
