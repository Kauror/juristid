"""What Ülevaade shows after the final cutover, and where the numbers come from.

Two metrics arrive with ADR 0021 and both are easy to get subtly wrong.

``Arvamusi koostamisel`` is not a count of submissions and not a count of open
Matters: it is the intersection of a canonical fact (open FULL) and a source
fact (no recorded send date), and it has to keep working when a lawyer changes
the canonical half without anybody re-running the cutover.
"""

from __future__ import annotations

import pytest

from app.legacy_import.final_cutover import apply_cutover_plan, build_cutover_plan
from app.matters import dashboard
from app.matters.services import close_matter
from app.workflow.enums import Disposition
from tests.synthetic_cutover import (
    CURRENT_DRAFTING,
    CURRENT_OTHER_STATUS,
    CURRENT_SENT,
    FINAL_SNAPSHOT,
    RETIRING_IN_FORCE,
    approve_snapshot,
    build_world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def applied(monkeypatch: pytest.MonkeyPatch):
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)
    world = build_world()
    apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))
    return world


def titles(queryset) -> set[str]:
    return set(queryset.values_list("title", flat=True))


# =========================================================================
# Arvamusi koostamisel
# =========================================================================


def test_drafting_is_current_work_with_no_recorded_send_date(applied) -> None:
    head = applied.people.head
    drafting = titles(dashboard.drafting_matters(head))

    assert CURRENT_DRAFTING in drafting
    assert CURRENT_OTHER_STATUS in drafting


def test_a_sent_opinion_is_not_being_drafted(applied) -> None:
    """It went out. The proceeding continues, and the drafting step does not."""
    assert CURRENT_SENT not in titles(dashboard.drafting_matters(applied.people.head))


def test_a_retired_matter_is_not_being_drafted(applied) -> None:
    """Both halves are required: no send date on finished work is not a task."""
    assert RETIRING_IN_FORCE not in titles(dashboard.drafting_matters(applied.people.head))


def test_closing_a_matter_here_removes_it_from_drafting_immediately(applied) -> None:
    """The canonical half leads, which is what makes the number self-correcting.

    No re-run of the cutover, no touch of the derived table: a lawyer closes a
    file and the count is right on the next page load.
    """
    head = applied.people.head
    matter = applied.refresh(CURRENT_DRAFTING)
    before = dashboard.drafting_matters(head).count()

    close_matter(
        matter=matter,
        disposition=Disposition.RESPONSE_COMPLETE,
        reason="Sünteetiline lõpetamine.",
        actor=head,
    )

    assert dashboard.drafting_matters(head).count() == before - 1


def test_drafting_never_exceeds_the_active_set(applied) -> None:
    head = applied.people.head
    assert dashboard.drafting_matters(head).count() <= dashboard.active_matters(head).count()


# =========================================================================
# Scoping
# =========================================================================


def test_a_restricted_matter_contributes_to_no_breakdown_it_should_not(applied) -> None:
    from app.core.enums import Visibility
    from tests import factories

    matter = applied.refresh(CURRENT_DRAFTING)
    matter.visibility = Visibility.RESTRICTED
    matter.owner = None
    matter.save(update_fields=["visibility", "owner", "updated_at"])

    # A lawyer reads it since docs/adr/0042, and a READER since docs/adr/0181;
    # an administrator without a business role reads nothing, and that is the
    # scoping this breakdown has to respect.
    assert CURRENT_DRAFTING in titles(dashboard.drafting_matters(applied.people.reader))
    outsider = factories.AdministratorFactory()
    assert not dashboard.drafting_matters(outsider).exists()


# =========================================================================
# The source instruction is shown, and creates nothing
# =========================================================================


def test_the_matter_page_shows_the_register_instruction(applied, client) -> None:
    from django.urls import reverse

    from app.legacy_import.current_state import CurrentRegisterState

    matter = applied.refresh(CURRENT_DRAFTING)
    state = CurrentRegisterState.objects.get(matter=matter)
    state.next_action_text = "Sünteetiline registri juhis."
    state.save(update_fields=["next_action_text", "updated_at"])

    client.force_login(applied.people.head)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Excelist" in body
    assert "Sünteetiline registri juhis." in body


def test_a_structured_action_replaces_the_source_instruction(applied, client) -> None:
    """Once somebody writes one here, the native workflow is the authority.

    Showing the older Excel wording beside it would invite acting on whichever
    was read first.
    """
    from django.urls import reverse

    from app.legacy_import.current_state import CurrentRegisterState
    from app.workflow.services import set_next_action

    matter = applied.refresh(CURRENT_DRAFTING)
    state = CurrentRegisterState.objects.get(matter=matter)
    state.next_action_text = "Sünteetiline registri juhis."
    state.save(update_fields=["next_action_text", "updated_at"])
    set_next_action(
        matter=matter,
        text="Sünteetiline struktuurne samm.",
        actor=applied.people.head,
        target_date=None,
        kind="MONITOR",
        date_semantics="REVIEW_ON",
    )

    client.force_login(applied.people.head)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Sünteetiline struktuurne samm." in body
    assert "Excelist" not in body


def test_the_page_no_longer_explains_how_the_importer_works(applied, client) -> None:
    """The provenance label is the whole explanation a reader needs.

    A sentence under every Matter restating that the importer derives neither a
    deadline nor a kind from this text answered a question nobody had asked. It
    described an implementation constraint; what stays is the register's own
    words and the label saying whose words they are.
    """
    from django.urls import reverse

    from app.legacy_import.current_state import CurrentRegisterState

    matter = applied.refresh(CURRENT_DRAFTING)
    state = CurrentRegisterState.objects.get(matter=matter)
    state.next_action_text = "Sünteetiline registri juhis."
    state.save(update_fields=["next_action_text", "updated_at"])

    client.force_login(applied.people.head)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    # The half that is useful, kept.
    assert "Sünteetiline registri juhis." in body
    assert "Excelist" in body

    # The half that was a lecture, gone.
    assert "Registri tekst" not in body
    assert "struktuurne tegevus" not in body
    assert "tähtaega ega liiki" not in body


def test_the_shown_instruction_carries_no_date_or_overdue_state(applied, client) -> None:
    """It is text. No kind, no deadline, no *Tähtaeg möödas* (ADR 0021)."""
    from django.urls import reverse

    from app.legacy_import.current_state import CurrentRegisterState
    from app.workflow.models import NextAction

    matter = applied.refresh(CURRENT_DRAFTING)
    state = CurrentRegisterState.objects.get(matter=matter)
    state.next_action_text = "Sünteetiline juhis tähtajaga 01.01.2020."
    state.save(update_fields=["next_action_text", "updated_at"])

    client.force_login(applied.people.head)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Excelist" in body
    assert "Tähtaeg möödas" not in body
    assert not NextAction.objects.filter(matter=matter).exists()
