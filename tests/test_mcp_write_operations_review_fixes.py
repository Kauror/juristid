"""The domain and security review's fixes, each proved on the real services.

P2-1: a `no_response` check's promotion is previewed exactly as the services
promote. P2-2: `change_current` keeps what it is not told to change, as the web
«Muuda» does (ENG-021). The P3 list: plain-text reasons, move wording, an
old-style deadline, precision-aware lateness, no kind labels (ADR 0054), the
important date's per-kind consequences, truthful reassignment text, the `new`
refusal, and staleness on a discharge. And: refusals never echo the caller's
input, and a NUL is refused in every argument.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.utils import timezone
from pydantic import ValidationError

from app.matters.response_deadlines import request_response_deadline
from app.matters.services import close_matter
from app.mcp_writes.operations import REGISTRY
from app.workflow.enums import ActionStatus
from app.workflow.follow_ups import schedule_first_check
from app.workflow.models import NextAction, OpinionFollowUp
from app.workflow.services import add_planned_action, promote_next_planned_action, set_next_action
from tests import factories
from tests.test_mcp_write_operations_support import (
    change,
    commit,
    is_stale,
    prepare,
    preview_text,
    public_origin,  # noqa: F401
    refusal,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_origin")]

KIND_LABELS = re.compile(r"\b(Teen|Ootan|Jälgin)\b")


def _in(days: int) -> date:
    return timezone.localdate() + timedelta(days=days)


def _check(matter, actor):
    submission = factories.send_opinion_through_services(matter, actor)
    schedule_first_check(submission=submission, actor=actor)
    check = promote_next_planned_action(matter=matter, actor=actor)
    assert check is not None and check.follow_up_id is not None
    return check


def _no_response(user, matter, day):
    return prepare(
        "complete_action",
        user,
        matter=str(matter.pk),
        follow_up_outcome="no_response",
        next_check_on=day.isoformat(),
    )


# ---------------------------------------------------------------------------
# P2-1 — the promotion after «no response yet»
# ---------------------------------------------------------------------------


def test_with_no_other_plan_the_next_check_becomes_current_and_the_preview_says_so(
    specialist, normal_matter
):
    check = _check(normal_matter, specialist)
    prepared = _no_response(specialist, normal_matter, _in(10))
    text = preview_text(prepared)
    assert "Järgmisest kontrollist" in text and "saab kohe teema praegune tegevus" in text
    assert "varaseim" not in text
    commit("complete_action", specialist, prepared)
    current = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert current.follow_up_id == check.follow_up_id
    assert current.target_date == _in(10)


def test_an_earlier_planned_step_is_promoted_and_named(specialist, normal_matter):
    _check(normal_matter, specialist)
    earlier = add_planned_action(
        matter=normal_matter, text="Kohtumine ministeeriumis", target_date=_in(5), actor=specialist
    )
    prepared = _no_response(specialist, normal_matter, _in(10))
    text = preview_text(prepared)
    assert "«Kohtumine ministeeriumis" in text and "järgmine kontroll jääb planeerituks" in text
    commit("complete_action", specialist, prepared)
    assert NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN).pk == earlier.pk


def test_a_later_planned_step_waits_behind_the_new_check(specialist, normal_matter):
    _check(normal_matter, specialist)
    later = add_planned_action(
        matter=normal_matter, text="Hiline samm", target_date=_in(30), actor=specialist
    )
    prepared = _no_response(specialist, normal_matter, _in(10))
    assert "saab kohe teema praegune tegevus" in preview_text(prepared)
    commit("complete_action", specialist, prepared)
    current = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert current.follow_up_id is not None
    later.refresh_from_db()
    assert later.status == ActionStatus.PLANNED


def test_on_the_same_day_the_existing_step_goes_first(specialist, normal_matter):
    _check(normal_matter, specialist)
    same_day = add_planned_action(
        matter=normal_matter, text="Sama päev", target_date=_in(10), actor=specialist
    )
    prepared = _no_response(specialist, normal_matter, _in(10))
    assert "«Sama päev" in preview_text(prepared)
    commit("complete_action", specialist, prepared)
    assert NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN).pk == same_day.pk


def test_the_plan_is_in_the_state(specialist, normal_matter):
    _check(normal_matter, specialist)
    prepared = _no_response(specialist, normal_matter, _in(10))
    assert prepared.state["planned_actions"] == []
    add_planned_action(matter=normal_matter, text="Uus", target_date=_in(3), actor=specialist)
    assert is_stale("complete_action", specialist, prepared)


# ---------------------------------------------------------------------------
# P2-2 — change_current keeps what it is not told to change
# ---------------------------------------------------------------------------


def _imported(matter, **step):
    return set_next_action(matter=matter, provenance={"source": "test"}, **step)


def test_a_text_edit_keeps_kind_meaning_date_and_an_unoffered_precision(specialist, normal_matter):
    old = _imported(
        normal_matter,
        text="Ministeeriumi eelnõu teine versioon",
        kind="WAIT",
        date_semantics="EXPECTED_AROUND",
        target_date=date(2027, 7, 1),
        date_precision="HALF_YEAR",
    )
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        text="Ministeeriumi eelnõu kolmas versioon",
    )
    meaning = change(prepared, "date_semantics")
    assert meaning.current == meaning.proposed
    assert change(prepared, "date").proposed == "II poolaasta 2027"
    executed = commit("set_next_action", specialist, prepared)
    new = NextAction.objects.get(pk=executed.record_id)
    assert (new.kind, new.date_semantics, new.target_date, new.date_precision) == (
        old.kind,
        old.date_semantics,
        old.target_date,
        old.date_precision,
    )


def test_an_omitted_date_is_kept_and_clear_date_clears(specialist, normal_matter):
    _imported(normal_matter, text="Samm", target_date=_in(4))
    kept = prepare(
        "set_next_action", specialist, matter=str(normal_matter.pk), mode="change_current", text="X"
    )
    assert kept.canonical["target_date"] == _in(4).isoformat()
    cleared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        clear_date=True,
    )
    assert cleared.canonical["target_date"] is None
    executed = commit("set_next_action", specialist, cleared)
    assert NextAction.objects.get(pk=executed.record_id).target_date is None
    with pytest.raises(ValidationError):
        REGISTRY["set_next_action"].arguments.model_validate(
            {"matter": "2026_1", "mode": "new", "text": "x", "kind": "do", "clear_date": True}
        )


def test_an_imported_do_review_is_not_turned_into_a_deadline(specialist, normal_matter):
    _imported(
        normal_matter, text="Vaatan üle", kind="DO", date_semantics="REVIEW_ON", target_date=_in(9)
    )
    kept = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        kind="do",
        text="Vaatan uuesti üle",
    )
    assert kept.canonical["date_semantics"] == "REVIEW_ON"
    asked = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        date_meaning="planned_day",
    )
    meaning = change(asked, "date_semantics")
    assert meaning.current != meaning.proposed
    assert meaning.proposed.startswith("Plaanis")
    executed = commit("set_next_action", specialist, asked)
    assert NextAction.objects.get(pk=executed.record_id).date_semantics == "DEADLINE"


def test_a_kind_change_asks_what_it_needs(specialist, normal_matter):
    _imported(normal_matter, text="Saata arvamus", target_date=_in(3))
    assert (
        refusal(
            "set_next_action",
            specialist,
            matter=str(normal_matter.pk),
            mode="change_current",
            kind="wait",
        ).code
        == "missing_information"
    )
    asked = refusal(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        kind="wait",
        waiting_for="MKM",
    )
    assert asked.code == "missing_information"  # what the kept date now means
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        kind="wait",
        waiting_for="MKM",
        date_meaning="review_on",
    )
    assert "ei ole enam Koja enda töö" in preview_text(prepared)
    assert prepared.canonical["text"] == "Saata arvamus (ootame: MKM)"


def test_wait_to_monitor_keeps_its_meaning(specialist, normal_matter):
    _imported(
        normal_matter,
        text="Ootame MKM vastust",
        kind="WAIT",
        date_semantics="EXPECTED_AROUND",
        target_date=_in(20),
    )
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        kind="monitor",
    )
    assert prepared.canonical["date_semantics"] == "EXPECTED_AROUND"


# ---------------------------------------------------------------------------
# P3
# ---------------------------------------------------------------------------


def test_a_reason_is_stored_as_plain_text(specialist, normal_matter):
    check = _check(normal_matter, specialist)
    prepared = prepare(
        "complete_action",
        specialist,
        matter=str(normal_matter.pk),
        follow_up_outcome="monitoring_ended",
        what_was_done="Eelnõu «võeti» tagasi & menetlus lõppes",
    )
    commit("complete_action", specialist, prepared)
    follow_up = OpinionFollowUp.objects.get(pk=check.follow_up_id)
    assert follow_up.end_reason == "Eelnõu «võeti» tagasi & menetlus lõppes"


def test_a_move_says_which_way_and_uses_the_stored_label(specialist, normal_matter):
    request_response_deadline(matter=normal_matter, deadline=_in(10), actor=specialist)
    earlier = prepare(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="move",
        deadline=_in(5).isoformat(),
    )
    assert "tuuakse ettepoole" in preview_text(earlier)
    assert "«Tähtaeg muudetud»" in preview_text(earlier)
    later = prepare(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="move",
        deadline=_in(20).isoformat(),
    )
    assert "lükatakse edasi" in preview_text(later)


def test_a_move_of_an_old_style_answered_deadline_warns(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_in(-5))
    factories.send_opinion_through_services(matter, specialist)
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(matter.pk),
        mode="move",
        deadline=_in(15).isoformat(),
    )
    assert any("new_request" in warning for warning in prepared.preview.warnings)


def test_an_old_style_deadline_discharged_meanwhile_is_stale(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_in(5))
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(matter.pk),
        mode="move",
        deadline=_in(15).isoformat(),
    )
    assert prepared.state["outstanding"] is True
    factories.send_opinion_through_services(matter, specialist)
    assert is_stale("response_deadline", specialist, prepared)


def test_lateness_wording_respects_precision_and_meaning(specialist, normal_matter):
    month = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Kirjutada arvamus",
        kind="do",
        date=_in(40).isoformat()[:7],
        date_precision="month",
    )
    assert "alles pärast selle perioodi lõppu" in preview_text(month)
    expected = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Riigikogu menetlus",
        kind="monitor",
        date=_in(40).isoformat(),
        date_precision="exact",
        date_meaning="expected_around",
    )
    text = preview_text(expected)
    assert "eeldatavasti tegutseb" in text
    assert "ülevaatuse päev" not in text


def test_no_kind_label_on_the_approval_page(specialist, normal_matter):
    _imported(normal_matter, text="Kirjutada arvamus", kind="WAIT", date_semantics="REVIEW_ON")
    for prepared in (
        prepare(
            "set_next_action",
            specialist,
            matter=str(normal_matter.pk),
            mode="change_current",
            kind="monitor",
        ),
        prepare(
            "complete_action", specialist, matter=str(normal_matter.pk), what_was_done="Tehtud."
        ),
    ):
        assert not KIND_LABELS.search(preview_text(prepared))


def test_an_important_date_says_what_its_kind_does(specialist, normal_matter):
    args = {
        "matter": str(normal_matter.pk),
        "title": "T",
        "date": _in(9).isoformat(),
        "date_precision": "exact",
    }
    other = preview_text(prepare("add_important_date", specialist, **args))
    assert "järgmine samm" in other and "Minu asjad" in other
    transposition = preview_text(
        prepare("add_important_date", specialist, **args, kind="transposition_deadline")
    )
    assert "Menetluse kulg" in transposition and "järgmine samm" in transposition
    estimate = preview_text(
        prepare("add_important_date", specialist, **args, kind="estimated_commencement")
    )
    assert "võib see tähtaeg" not in estimate


def test_reassignment_text_is_true_to_what_moves(specialist, other_specialist):
    unowned = factories.MatterFactory(owner=None)
    text = preview_text(
        prepare(
            "assign_matter",
            specialist,
            matter=str(unowned.pk),
            action="assign",
            responsible_person=other_specialist.display_name,
        )
    )
    assert "Ühegi tegevuse vastutaja ei muutu" in text
    assert "järgisid" not in text
    owned = factories.MatterFactory(owner=specialist)
    factories.NextActionFactory(matter=owned, responsible=other_specialist)
    held = preview_text(prepare("assign_matter", specialist, matter=str(owned.pk), action="clear"))
    assert "Ühegi tegevuse vastutaja ei muutu" in held
    factories.NextActionFactory(
        matter=owned, responsible=specialist, status=ActionStatus.PLANNED, target_date=_in(3)
    )
    moving = preview_text(
        prepare("assign_matter", specialist, matter=str(owned.pk), action="clear")
    )
    assert "(1 tegevus)" in moving and "jääb vastutajata" in moving


def test_a_closed_matter_assignment_points_to_the_web(specialist, normal_matter):
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    refused = refusal("assign_matter", specialist, matter=str(normal_matter.pk), action="clear")
    assert refused.code == "domain_rule"
    assert "does allow giving a closed Matter another Vastutaja" in refused.message


def test_new_with_a_current_step_does_not_advise_rewriting_it(specialist, normal_matter):
    set_next_action(matter=normal_matter, text="Olemas", actor=specialist)
    refused = refusal(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Uus",
        kind="do",
    )
    assert "change_current" not in refused.message
    assert "stays as it is" in refused.message and "planned" in refused.message


# ---------------------------------------------------------------------------
# Security review P3-3
# ---------------------------------------------------------------------------


def test_person_refusals_do_not_echo_the_name(specialist):
    unknown = refusal("create_matter", specialist, title="T", responsible_person="Zxqv Plorb")
    assert "Zxqv" not in unknown.message
    factories.UserFactory(display_name="Mari Kask")
    factories.UserFactory(display_name="Mari Tamm")
    ambiguous = refusal("create_matter", specialist, title="T", responsible_person="mari")
    assert "«mari»" not in ambiguous.message and "Mari Kask" in ambiguous.message


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_a_nul_is_refused_in_every_operation(name):
    with pytest.raises(ValidationError):
        REGISTRY[name].arguments.model_validate({"matter": "2026_1\x00", "title": "x"})
    with pytest.raises(ValidationError):
        REGISTRY[name].arguments.model_validate(
            {"matter": "2026_1", "title": "a\x00b", "text": "a\x00"}
        )


# ---------------------------------------------------------------------------
# Re-review P3-A..C
# ---------------------------------------------------------------------------


def test_a_change_that_changes_nothing_is_refused(specialist, normal_matter):
    _imported(
        normal_matter,
        text="Ootame MKM vastust",
        kind="WAIT",
        date_semantics="EXPECTED_AROUND",
        target_date=date(2027, 7, 1),
        date_precision="HALF_YEAR",
    )
    base = {"matter": str(normal_matter.pk), "mode": "change_current"}
    assert refusal("set_next_action", specialist, **base).code == "invalid_argument"
    same = refusal(
        "set_next_action",
        specialist,
        **base,
        text="Ootame  MKM vastust",
        kind="wait",
        date_meaning="expected_around",
    )
    assert same.code == "invalid_argument"
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_a_kept_meaning_does_not_survive_a_kind_change(specialist, normal_matter):
    _imported(
        normal_matter,
        text="Ootame MKM vastust",
        kind="WAIT",
        date_semantics="EXPECTED_AROUND",
        target_date=_in(20),
    )
    assert (
        refusal(
            "set_next_action",
            specialist,
            matter=str(normal_matter.pk),
            mode="change_current",
            kind="do",
            date_meaning="expected_around",
        ).code
        == "invalid_argument"
    )
    to_do = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        kind="do",
    )
    assert to_do.canonical["date_semantics"] == "DEADLINE"


def test_markup_is_refused_in_a_follow_up_reason(specialist, normal_matter):
    _check(normal_matter, specialist)
    refused = refusal(
        "complete_action",
        specialist,
        matter=str(normal_matter.pk),
        follow_up_outcome="response_received",
        what_was_done='Vastus <a href="https://example.org">siin</a>',
    )
    assert refused.code == "invalid_argument"
    assert "example.org" not in refused.message and "what_was_done" in refused.message
