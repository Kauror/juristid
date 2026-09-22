"""A lawyer may know what happens next before knowing when (docs/adr/0106).

The whole contract, asserted at each layer that owns a piece of it:

* **the service** — `DO` / `DEADLINE` / `target_date=NULL` writes, stays `OPEN`,
  and is not overdue; blank text is still refused; adding and clearing a date
  are ordinary replacements;
* **the forms** — all three that capture a next step agree: a sentence alone is
  valid, a date alone is refused on the sentence;
* **`+ Märge`** — a step with no day saves the whole operation, in one
  transaction, in every combination docs/adr/0105 §4 opened up;
* **`Minu asjad`** — an undated step is visible, counted, and not styled late;
* **`Tähtajad` and every overdue/reporting surface** — it is absent, and it
  appears the moment a date is added.

The last group is the one that matters most, because it is the claim that made
the previous round refuse this: *«a dateless step appears in nobody's Minu
asjad»*. It is asserted here rather than reasoned about.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.matters import my_work, selectors, work_items
from app.matters.forms import ComposerForm, MatterProgressForm, NextActionForm
from app.matters.models import MatterProceduralDevelopment
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NO_DATE_LABEL, NextAction
from app.workflow.services import set_next_action, set_next_action_for_new_work

pytestmark = pytest.mark.django_db


def _undated(matter, actor, text: str = "Vaatan ministeeriumi vastuse üle") -> NextAction:
    return set_next_action(matter=matter, text=text, actor=actor)


def _dated(matter, actor, when: dt.date, text: str = "Koostan arvamuse") -> NextAction:
    return set_next_action(matter=matter, text=text, target_date=when, actor=actor)


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


# ---------------------------------------------------------------------------
# 1–8 — the service and the record
# ---------------------------------------------------------------------------


def test_a_do_deadline_with_no_date_can_be_created(normal_matter, specialist):
    """1, 2. The canonical storage, and it is `DO` / `DEADLINE` and not a WAIT."""
    action = _undated(normal_matter, specialist)

    assert action.text == "Vaatan ministeeriumi vastuse üle"
    assert action.target_date is None
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.status == ActionStatus.OPEN
    # Nothing was invented to fill the column.
    assert NextAction.objects.get(pk=action.pk).target_date is None


def test_the_database_itself_accepts_it(normal_matter, specialist):
    """The constraint is gone, not merely unreached by the service.

    `workflow/0008` drops `workflow_deadline_requires_a_date`, and a test that
    only went through `set_next_action` would still pass with the constraint in
    place if the service happened to fill the date in.
    """
    NextAction.objects.create(
        matter=normal_matter,
        text="Otse mudelist",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=None,
        responsible=specialist,
    )
    assert NextAction.objects.filter(matter=normal_matter, target_date__isnull=True).count() == 1


def test_the_text_constraint_is_untouched(normal_matter, specialist):
    """What docs/adr/0106 did *not* relax. An action with no text records nothing."""
    with pytest.raises(IntegrityError), transaction.atomic():
        NextAction.objects.create(
            matter=normal_matter,
            text="",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            responsible=specialist,
        )


def test_blank_text_is_still_refused_by_the_service(normal_matter, specialist):
    """8. Both blank and text-blank-with-a-date reach the same refusal."""
    with pytest.raises(DomainError):
        set_next_action(matter=normal_matter, text="   ", actor=specialist)
    with pytest.raises(DomainError):
        set_next_action(
            matter=normal_matter,
            text="",
            target_date=timezone.localdate(),
            actor=specialist,
        )


def test_an_undated_action_is_never_overdue(normal_matter, specialist):
    """3, 4. Not late today, not late in a decade, and `days_late` is zero."""
    action = _undated(normal_matter, specialist)
    far_future = timezone.localdate() + dt.timedelta(days=4000)

    assert action.is_overdue() is False
    assert action.is_overdue(far_future) is False
    assert action.is_due_for_review(far_future) is False
    assert action.days_late == 0
    # And the queryset agrees with the property, which is the pair the register
    # and the row styling read separately.
    assert NextAction.objects.overdue(far_future).filter(pk=action.pk).count() == 0


def test_an_undated_action_renders_no_date_and_no_label(normal_matter, specialist):
    """Nothing prints a day, an approximation or a dash."""
    action = _undated(normal_matter, specialist)

    assert action.display_date == ""
    assert action.date_label == ""
    assert action.date_display == NO_DATE_LABEL
    assert action.is_approximate is False
    # The precision the field defaults to describes nothing and is not shown.
    assert action.date_precision == DatePrecision.EXACT


def test_a_dated_action_behaves_exactly_as_before(normal_matter, specialist):
    """5. The change is additive: nothing about a dated step moved."""
    yesterday = timezone.localdate() - dt.timedelta(days=1)
    action = _dated(normal_matter, specialist, yesterday)

    assert action.is_overdue() is True
    assert action.days_late == 1
    assert action.display_date == format_estonian_date(yesterday)
    assert action.date_label == "Plaanis"
    assert NextAction.objects.overdue().filter(pk=action.pk).count() == 1


def test_a_dated_action_may_be_replaced_by_an_undated_one(normal_matter, specialist):
    """6. Clearing the day is a plan change, through the ordinary supersede."""
    first = _dated(normal_matter, specialist, timezone.localdate() + dt.timedelta(days=7))
    second = set_next_action_for_new_work(matter=normal_matter, text=first.text, actor=specialist)

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert first.replaced_by_id == second.pk
    assert second.status == ActionStatus.OPEN
    assert second.target_date is None
    assert second.text == first.text


def test_an_undated_action_may_later_be_given_a_date(normal_matter, specialist):
    """7. And the deadline surfaces pick it up with nothing else happening."""
    first = _undated(normal_matter, specialist)
    when = timezone.localdate() + dt.timedelta(days=3)
    second = set_next_action_for_new_work(
        matter=normal_matter, text=first.text, target_date=when, actor=specialist
    )

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert second.target_date == when
    assert second.display_date == format_estonian_date(when)
    assert NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN).pk == second.pk


def test_the_audit_event_records_the_null_truthfully(normal_matter, specialist):
    """docs/adr/0106 §6. The event is written, and the payload says `null`."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    action = _undated(normal_matter, specialist)
    event = ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.NEXT_ACTION_SET
    ).latest("occurred_at")

    assert str(action.pk) in str(event.object_id)
    assert event.payload.get("target_date") is None


# ---------------------------------------------------------------------------
# 9–13 — the forms
# ---------------------------------------------------------------------------


def test_next_action_form_accepts_text_alone(normal_matter):
    """9. `PRAEGUNE TEGEVUS` → `Muuda`, and `Uus teema`'s first step."""
    form = NextActionForm({"text": "Vaatan uue versiooni üle"})

    assert form.is_valid(), form.errors
    assert form.as_service_kwargs()["target_date"] is None
    assert form.as_service_kwargs()["kind"] == ActionKind.DO
    assert form.as_service_kwargs()["date_semantics"] == DateSemantics.DEADLINE


def test_next_action_form_accepts_text_and_date(normal_matter):
    """10. Unchanged."""
    when = timezone.localdate() + dt.timedelta(days=5)
    form = NextActionForm({"text": "Koostan arvamuse", "target_date": format_estonian_date(when)})

    assert form.is_valid(), form.errors
    assert form.as_service_kwargs()["target_date"] == when


def test_next_action_form_refuses_a_date_alone_on_the_text(normal_matter):
    """11. The refusal that stays, on the control that is empty."""
    form = NextActionForm({"text": "", "target_date": "30.09.2026"})

    assert not form.is_valid()
    assert form.errors["text"] == ["Kirjuta järgmine tegevus."]
    assert "target_date" not in form.errors


def test_next_action_form_still_refuses_a_malformed_period(normal_matter):
    """A `Kuu` nobody answered is a mistake; an untouched date box is an answer.

    The distinction docs/adr/0106 turns on. Choosing a precision and then not
    answering its control is a half-finished thought and is still refused, on
    that control — what stopped being refused is leaving the whole date group
    alone.
    """
    form = NextActionForm(
        {"text": "Vaatan üle", "next_precision": DatePrecision.MONTH.value},
        periods=True,
    )

    assert not form.is_valid()
    assert "next_month" in form.errors


def test_the_progress_form_accepts_a_next_step_with_no_date(normal_matter):
    """12, for `+ Märge`'s own block."""
    form = MatterProgressForm({"next_text": "Helistan ministeeriumisse"})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["next_text"] == "Helistan ministeeriumisse"
    assert form.cleaned_data.get("next_date") is None


def test_the_progress_form_refuses_a_date_with_no_next_step(normal_matter):
    """And it lands on `Järgmine tegevus`, never as a panel-level sentence."""
    form = MatterProgressForm({"title": "Midagi juhtus", "next_date": "30.09.2026"})

    assert not form.is_valid()
    assert form.errors["next_text"] == ["Kirjuta järgmine tegevus."]
    assert "next_date" not in form.errors


def test_the_composer_accepts_a_next_step_with_no_date(normal_matter):
    """The third form that carries the rule."""
    form = ComposerForm({"body": "Kohtusime ministeeriumiga", "next_text": "Ootan uut versiooni"})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["next_action_kwargs"]["target_date"] is None


def test_editing_an_existing_action_can_clear_its_date(signed_in, normal_matter, specialist):
    """13, 28, 29. Emptying the box is how a date comes off, and it persists."""
    _dated(normal_matter, specialist, timezone.localdate() + dt.timedelta(days=9))

    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Koostan arvamuse", "target_date": "", "next_precision": DatePrecision.EXACT},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    open_now = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert open_now.target_date is None
    # A hard read of the page, not the swap's own answer.
    body = signed_in.get(_teema(normal_matter)).content.decode()
    assert NO_DATE_LABEL in body


def test_a_cleared_date_can_be_set_again(signed_in, normal_matter, specialist):
    """30. And the deadline surfaces come back with it."""
    _undated(normal_matter, specialist, text="Koostan arvamuse")
    when = timezone.localdate() + dt.timedelta(days=2)

    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {
            "text": "Koostan arvamuse",
            "target_date": format_estonian_date(when),
            "next_precision": DatePrecision.EXACT,
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    open_now = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert open_now.target_date == when
    groups = {group.key: group.actions for group in selectors.my_work_timeline(specialist)}
    assert open_now in groups["soon"]
    assert open_now not in groups["undated"]


# ---------------------------------------------------------------------------
# 14–18 — `+ Märge`, through its real route
# ---------------------------------------------------------------------------


def _add_note(matter) -> str:
    return reverse("matters:add_note", kwargs={"pk": matter.pk})


def _pdf(name: str):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic evidence", content_type="application/pdf")


def test_a_marge_of_only_an_undated_next_step_saves(signed_in, normal_matter, stage):
    """14. The exact case the brief names: no comment, no file, no state change."""
    response = signed_in.post(_add_note(normal_matter), {"next_text": "Helistan ministeeriumisse"})

    assert response.status_code == 200
    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.text == "Helistan ministeeriumisse"
    assert action.target_date is None
    assert MatterProceduralDevelopment.objects.filter(matter=normal_matter).exists()


def test_a_marge_of_a_comment_and_an_undated_next_step_saves(signed_in, normal_matter, stage):
    """15."""
    response = signed_in.post(
        _add_note(normal_matter),
        {"title": "Kohtusime ministeeriumiga", "next_text": "Ootan uut versiooni"},
    )

    assert response.status_code == 200
    record = MatterProceduralDevelopment.objects.get(matter=normal_matter)
    assert record.title == "Kohtusime ministeeriumiga"
    assert NextAction.objects.get(matter=normal_matter).target_date is None


def test_a_marge_of_a_state_change_and_an_undated_next_step_saves(signed_in, normal_matter, stage):
    """16. One transaction over three canonical services."""
    response = signed_in.post(
        _add_note(normal_matter),
        {"stage": str(stage.pk), "next_text": "Ootan uut versiooni"},
    )

    assert response.status_code == 200
    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == stage.pk
    assert NextAction.objects.get(matter=normal_matter).target_date is None


def test_a_marge_of_a_file_and_an_undated_next_step_saves(
    signed_in, normal_matter, stage, evidence_root
):
    """17."""
    response = signed_in.post(
        _add_note(normal_matter),
        {"next_text": "Vaatan kirja üle", "attachments": [_pdf("kiri.pdf")]},
    )

    assert response.status_code == 200
    from app.documents.links import DocumentLink

    record = MatterProceduralDevelopment.objects.get(matter=normal_matter)
    assert DocumentLink.objects.filter(procedural_development=record).count() == 1
    assert NextAction.objects.get(matter=normal_matter).target_date is None


def test_a_marge_with_a_next_date_and_no_sentence_writes_nothing(signed_in, normal_matter, stage):
    """18. Refused on the sentence, and the whole operation is unwound."""
    response = signed_in.post(
        _add_note(normal_matter),
        {"title": "Midagi juhtus", "next_date": "30.09.2026"},
    )

    assert response.status_code == 400
    assert "Kirjuta järgmine tegevus." in response.content.decode()
    assert not MatterProceduralDevelopment.objects.filter(matter=normal_matter).exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# 19–22 — Minu asjad
# ---------------------------------------------------------------------------


def test_an_undated_action_appears_in_minu_asjad(signed_in, normal_matter, specialist):
    """19, 20, 22. Visible, counted, named — and it survives a plain GET.

    This is the claim docs/adr/0105 §4 got wrong. `my_work.undated_items` has
    rendered a `Kuupäevata` block since the page was built; it simply had only
    WAIT and MONITOR rows to put in it.
    """
    action = _undated(normal_matter, specialist)

    rows, total = my_work.undated_items(specialist, specialist)
    assert total == 1
    assert [row.text for row in rows] == [action.text]

    body = signed_in.get(reverse("matters:my_work")).content.decode()
    assert "Kuupäevata" in body
    assert action.text in body


def test_an_undated_action_is_not_marked_overdue_in_minu_asjad(normal_matter, specialist):
    """21. Not late, not ripe, and the portfolio row does not call for attention."""
    _undated(normal_matter, specialist)
    today = timezone.localdate()

    rows, _total = my_work.undated_items(specialist, specialist)
    assert [row.is_overdue for row in rows] == [False]
    assert [row.is_review_ripe for row in rows] == [False]
    assert [row.date_display for row in rows] == [NO_DATE_LABEL]

    portfolio = my_work.build_portfolio(specialist, specialist, today=today, items=[])
    row = next(r for r in portfolio.rows if r.matter.pk == normal_matter.pk)
    assert row.needs_attention is False


def test_a_matter_with_an_undated_action_has_an_action(normal_matter, specialist):
    """25, at the portfolio layer. `järgmine tegevus puudub` must exclude it."""
    _undated(normal_matter, specialist)
    portfolio = my_work.build_portfolio(
        specialist, specialist, today=timezone.localdate(), items=[]
    )
    row = next(r for r in portfolio.rows if r.matter.pk == normal_matter.pk)

    assert row.has_action is True
    assert row.next_action is not None
    assert row.action is None
    assert row.next_action.date_display == NO_DATE_LABEL


# ---------------------------------------------------------------------------
# 23–24 — Tähtajad
# ---------------------------------------------------------------------------


def test_an_undated_action_is_absent_from_every_dated_band(normal_matter, specialist):
    """23. It is in `Kuupäevata` and in none of the four date bands."""
    action = _undated(normal_matter, specialist)
    groups = {group.key: group.actions for group in selectors.my_work_timeline(specialist)}

    assert action in groups["undated"]
    for band in ("passed", "today", "soon", "later"):
        assert action not in groups[band], band
    assert selectors.overdue_count(groups["undated"]) == 0


def test_an_undated_action_is_absent_from_the_upcoming_window(normal_matter, specialist):
    """23, on the department's own deadline surface."""
    from app.matters import dashboard

    _undated(normal_matter, specialist)
    result = dashboard.upcoming_rows(specialist)

    assert all(row.matter.pk != normal_matter.pk for row in result.rows)


def test_adding_a_date_puts_it_on_the_deadline_surfaces(normal_matter, specialist):
    """24. Nothing else has to happen — the surfaces read the column."""
    _undated(normal_matter, specialist, text="Koostan arvamuse")
    when = timezone.localdate() + dt.timedelta(days=2)
    set_next_action_for_new_work(
        matter=normal_matter, text="Koostan arvamuse", target_date=when, actor=specialist
    )

    groups = {group.key: group.actions for group in selectors.my_work_timeline(specialist)}
    assert [a.text for a in groups["soon"]] == ["Koostan arvamuse"]
    assert groups["undated"] == []


# ---------------------------------------------------------------------------
# 25–27 — register and reporting
# ---------------------------------------------------------------------------


def test_the_register_does_not_call_an_undated_action_missing(normal_matter, specialist):
    """25. A Matter with an undated step is not `?tegevus=puudub`."""
    _undated(normal_matter, specialist)
    from app.matters.models import Matter

    everything = Matter.objects.visible_to(specialist)
    missing = selectors.filter_by_next_action(everything, specialist, selectors.MISSING)

    assert normal_matter.pk not in {m.pk for m in missing}


def test_the_register_overdue_filter_excludes_it(normal_matter, specialist):
    """26. `?tegevus=hilinenud` is a date question and it has no date."""
    _undated(normal_matter, specialist)
    from app.matters.models import Matter

    everything = Matter.objects.visible_to(specialist)
    late = selectors.filter_by_next_action(everything, specialist, "hilinenud")
    review = selectors.filter_by_next_action(everything, specialist, selectors.REVIEW_DUE)

    assert normal_matter.pk not in {m.pk for m in late}
    assert normal_matter.pk not in {m.pk for m in review}


def test_reporting_does_not_count_it_as_late(normal_matter, specialist):
    """27. And it is still in the population, which is the honest denominator."""
    from app.reporting.context import ReportingContext, parse_period
    from app.reporting.selectors import activity

    _undated(normal_matter, specialist)
    today = timezone.localdate()
    context = ReportingContext(
        viewer=specialist,
        period=parse_period("koik", today),
        today=today,
        now=timezone.now(),
    )
    result = activity.overdue_do_deadline(context)

    assert result.value == 0
    assert result.population_count >= 1


def test_without_next_action_excludes_a_matter_holding_an_undated_step(normal_matter, specialist):
    """25, on Osakond's own column. Action existence is not date presence."""
    from app.matters import dashboard

    _undated(normal_matter, specialist)
    assert normal_matter.pk not in {m.pk for m in dashboard.without_next_action(specialist)}


# ---------------------------------------------------------------------------
# The Teema page itself
# ---------------------------------------------------------------------------


def test_praegune_tegevus_says_the_date_is_unset(signed_in, normal_matter, specialist):
    """It reads as a task without a date, never as a broken row."""
    action = _undated(normal_matter, specialist)
    body = signed_in.get(_teema(normal_matter)).content.decode()

    assert action.text in body
    assert NO_DATE_LABEL in body
    # Muted, and never the overdue grammar: a step with no deadline recorded
    # cannot be late.
    assert "curact__date--unset" in body
    assert "curact__date--overdue" not in body


def test_an_undated_action_reads_once_and_not_in_the_chronology(
    signed_in, normal_matter, specialist
):
    """The open step is current work, not history — unchanged by this round."""
    action = _undated(normal_matter, specialist)
    body = signed_in.get(_teema(normal_matter)).content.decode()
    history = body[body.index('id="ajalugu-loend"') :]

    assert action.text not in history


def test_a_work_item_for_an_undated_action_carries_no_day(normal_matter, specialist):
    """The read model, which every work surface shares."""
    action = _undated(normal_matter, specialist)
    item = work_items.action_item(action, timezone.localdate())

    assert item.when is None
    assert item.period_end is None
    assert item.display_date == ""
    assert item.date_display == NO_DATE_LABEL
    assert item.is_overdue is False
    assert item.is_review_ripe is False


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------


def test_the_prepare_by_flow_still_invents_no_action(normal_matter, specialist):
    """docs/adr/0106 §6. `Uus teema`'s convenience step is a different act.

    A supplied preparation date creates `Koostan arvamuse`; a blank one creates
    nothing. Relaxing the constraint must not turn the second case into an
    undated step nobody asked for — the lawyer may make one by hand afterwards,
    and that is their decision rather than the form's.

    The guard is the service's own and does not depend on the constraint that
    went, which is what this asserts.
    """
    from app.workflow.services import establish_opinion_preparation_action

    with pytest.raises(DomainError):
        establish_opinion_preparation_action(
            matter=normal_matter, prepare_by=None, actor=specialist
        )
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_the_defer_base_counts_from_today_for_an_undated_step(normal_matter, specialist):
    """The one place an undated step meets date arithmetic.

    `Lükka edasi` no longer renders on the Teema page and its route survives, so
    the base has to stay defined: for a step with no day there is no other day to
    count from, and today is the honest answer rather than an error.
    """
    from app.matters.views import defer_base

    action = _undated(normal_matter, specialist)
    today = timezone.localdate()

    assert defer_base(action, today) == today
    assert defer_base(None, today) == today


def test_historical_wait_and_monitor_semantics_are_untouched(normal_matter, specialist):
    """docs/adr/0106 §6. The richer vocabulary is not collapsed into DO."""
    action = set_next_action(
        matter=normal_matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=None,
        actor=specialist,
    )

    assert action.kind == ActionKind.WAIT
    assert action.date_semantics == DateSemantics.REVIEW_ON
    assert action.is_overdue() is False
    assert action.is_review_kind is True
