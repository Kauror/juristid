"""A replacement of the same action keeps the action's restriction (docs/adr/0139).

`Muuda` beside the open step is an edit of **the same work**: the words or the
day change, the work does not. The service writes that edit as a new
`NextAction` superseding the old one, so the replacement is created with the
old row's own restriction — exactly as it already carried the `Tööplaan` step
(docs/adr/0133 §4). Before this, a step restricted below its Matter came back
from `Muuda` as an ordinary one, and a reader who could not see it before the
edit could see it after.

Only `Muuda` carries it. New work — `+ Määra järgmine tegevus`, `Järgmisena`
after a completion, `Alusta` on a plan step, every importer — keeps the ordinary
creation rule even though it also supersedes whatever was open (the owner's
decision, docs/adr/0138 §3–§4).

Asserted through the real operation and the real route, never by building rows:
the restricted edit, the controls, the plan-linked edit, new work and the next
step not inheriting, the stale edit, and copy-at-creation semantics.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.timeline import matter_timeline
from app.matters.workspace import (
    STALE_ACTION_REFUSAL,
    change_current_action,
    complete_current_action,
)
from app.workflow import plan as work_plan
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

#: Distinctive words, so a page is searched for this test's step only.
FIRST_WORDS = "Salastatudülesanne loe eelnõu"
EDITED_WORDS = "Salastatudülesanne loe eelnõu ja kommenteeri"
MARKER = "Salastatudülesanne"


def _day(n: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=n)


def _restrict(action: NextAction, override: str = Visibility.RESTRICTED) -> NextAction:
    """As the shell, the admin or an importer writes it: no panel offers one."""
    NextAction.objects.filter(pk=action.pk).update(visibility_override=override)
    action.refresh_from_db()
    return action


def _action(matter, actor, *, restricted: bool, text: str = FIRST_WORDS) -> NextAction:
    action = set_next_action_for_new_work(matter=matter, text=text, actor=actor)
    return _restrict(action) if restricted else action


def _edit(matter, actor, action, *, text: str = EDITED_WORDS) -> NextAction:
    return change_current_action(
        matter=matter, actor=actor, action_id=action.pk, text=text, target_date=_day(7)
    )


def _page(client, user, name: str, **kwargs) -> str:
    client.force_login(user)
    response = client.get(reverse(name, kwargs=kwargs))
    return response.content.decode() if response.status_code == 200 else ""


def _readers_surfaces(client, reader, matter, specialist) -> dict[str, str]:
    """Every page a reader may open that prints a Matter's open step."""
    page, _ = matter_timeline(matter=matter, user=reader)
    return {
        "teema": _page(client, reader, "matters:matter_detail", pk=matter.pk),
        "register": _page(client, reader, "matters:matter_list"),
        "desk": _page(client, reader, "matters:person_work", pk=specialist.pk),
        "teema-kaik": " ".join(
            str(getattr(item.event, "summary", "")) for item in page if item.event
        ),
    }


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist)


# ---------------------------------------------------------------------------
# A. A restricted step, edited
# ---------------------------------------------------------------------------


def test_an_edited_restricted_step_stays_restricted(client, matter, specialist, reader):
    original = _action(matter, specialist, restricted=True)
    assert not NextAction.objects.visible_to(reader).filter(pk=original.pk).exists()

    replacement = _edit(matter, specialist, original)

    original.refresh_from_db()
    assert original.status == ActionStatus.SUPERSEDED
    assert original.visibility_override == Visibility.RESTRICTED
    assert original.replaced_by_id == replacement.pk
    assert replacement.status == ActionStatus.OPEN
    assert replacement.visibility_override == Visibility.RESTRICTED
    assert replacement.text == EDITED_WORDS

    assert not NextAction.objects.visible_to(reader).filter(pk=replacement.pk).exists()
    for surface, body in _readers_surfaces(client, reader, matter, specialist).items():
        assert MARKER not in body, surface

    assert NextAction.objects.visible_to(specialist).filter(pk=replacement.pk).exists()
    assert EDITED_WORDS in _page(client, specialist, "matters:matter_detail", pk=matter.pk)


def test_the_muuda_route_keeps_the_restriction(client, matter, specialist, reader):
    """The same rule through the page's own POST, not just the service."""
    original = _action(matter, specialist, restricted=True)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {
            "action_id": str(original.pk),
            "text": EDITED_WORDS,
            "target_date": "",
        },
    )

    assert response.status_code == 200
    replacement = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert replacement.pk != original.pk
    assert replacement.text == EDITED_WORDS
    assert replacement.visibility_override == Visibility.RESTRICTED
    assert MARKER not in _page(client, reader, "matters:matter_detail", pk=matter.pk)


# ---------------------------------------------------------------------------
# B, C. The controls
# ---------------------------------------------------------------------------


def test_an_edited_ordinary_step_stays_ordinary(client, matter, specialist, reader):
    replacement = _edit(matter, specialist, _action(matter, specialist, restricted=False))

    assert replacement.visibility_override == ""
    assert NextAction.objects.visible_to(reader).filter(pk=replacement.pk).exists()
    assert EDITED_WORDS in _page(client, reader, "matters:matter_detail", pk=matter.pk)


def test_a_normal_override_passes_nothing_on(matter, specialist):
    original = _restrict(_action(matter, specialist, restricted=False), Visibility.NORMAL)

    assert _edit(matter, specialist, original).visibility_override == ""


def test_a_restricted_matters_step_needs_no_redundant_override(specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)

    replacement = _edit(matter, specialist, _action(matter, specialist, restricted=False))

    assert replacement.visibility_override == ""
    assert replacement.effective_visibility == Visibility.RESTRICTED


# ---------------------------------------------------------------------------
# D. A plan-linked step keeps both its plan step and its restriction
# ---------------------------------------------------------------------------


def test_an_edited_plan_step_keeps_its_step_and_its_restriction(matter, specialist, reader):
    work_plan.seed_standard_plan(matter=matter, actor=specialist)
    step = work_plan.plan_steps_of(matter)[0]
    original = _restrict(work_plan.activate_plan_step(matter=matter, step=step, actor=specialist))

    replacement = _edit(matter, specialist, original)

    assert replacement.plan_step_id == step.pk
    assert replacement.visibility_override == Visibility.RESTRICTED
    assert not NextAction.objects.visible_to(reader).filter(pk=replacement.pk).exists()


# ---------------------------------------------------------------------------
# E, F. New work does not inherit, though it supersedes
# ---------------------------------------------------------------------------


def test_new_work_over_a_restricted_step_keeps_the_ordinary_rule(matter, specialist):
    """`+ Määra järgmine tegevus` / any non-edit door: supersedes, does not inherit."""
    original = _action(matter, specialist, restricted=True)

    new_work = set_next_action_for_new_work(
        matter=matter, text="Uus töö: koosta kokkuvõte", actor=specialist
    )

    original.refresh_from_db()
    assert original.status == ActionStatus.SUPERSEDED
    assert original.replaced_by_id == new_work.pk
    assert new_work.visibility_override == ""


def test_the_new_work_route_keeps_the_ordinary_rule(client, matter, specialist):
    """The same view without an `action_id` is new work, and does not inherit."""
    _action(matter, specialist, restricted=True)
    client.force_login(specialist)

    client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        {"text": "Uus töö: helista ministeeriumi", "target_date": ""},
    )

    new_work = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert new_work.text == "Uus töö: helista ministeeriumi"
    assert new_work.visibility_override == ""


def test_the_next_step_after_completing_a_restricted_step_does_not_inherit(matter, specialist):
    """docs/adr/0138 §4, unchanged by this rule."""
    original = _action(matter, specialist, restricted=True)

    result = complete_current_action(
        matter=matter,
        author=specialist,
        action_id=original.pk,
        body="<p>Lugesin läbi.</p>",
        next_text="Saada kommentaarid",
    )

    assert result.entry.visibility_override == Visibility.RESTRICTED
    assert result.action.visibility_override == ""


# ---------------------------------------------------------------------------
# G. A stale edit refuses and writes nothing
# ---------------------------------------------------------------------------


def test_a_stale_edit_of_a_restricted_step_refuses_and_writes_nothing(matter, specialist):
    stale = _action(matter, specialist, restricted=True)
    current = _edit(matter, specialist, stale)  # edited in another tab
    rows_before = list(
        NextAction.objects.filter(matter=matter).values_list(
            "pk", "status", "visibility_override", "replaced_by_id"
        )
    )

    with refused(STALE_ACTION_REFUSAL):
        _edit(matter, specialist, stale, text="Vana vahekaardi muudatus")

    assert (
        list(
            NextAction.objects.filter(matter=matter).values_list(
                "pk", "status", "visibility_override", "replaced_by_id"
            )
        )
        == rows_before
    )
    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN


# ---------------------------------------------------------------------------
# H, I. Copied at creation, never joined, never backfilled
# ---------------------------------------------------------------------------


def test_relaxing_the_original_later_leaves_the_replacement_restricted(matter, specialist):
    original = _action(matter, specialist, restricted=True)
    replacement = _edit(matter, specialist, original)

    _restrict(original, "")

    replacement.refresh_from_db()
    assert replacement.visibility_override == Visibility.RESTRICTED


def test_an_existing_replacement_is_not_rewritten(matter, specialist):
    """A chain written before its first row was restricted keeps its rows as they are."""
    original = _action(matter, specialist, restricted=False)
    replacement = _edit(matter, specialist, original)

    _restrict(original)

    replacement.refresh_from_db()
    assert replacement.visibility_override == ""
