"""What Ülevaade shows after the final cutover, and where the numbers come from.

Two metrics arrive with ADR 0021 and both are easy to get subtly wrong.

``Arvamusi koostamisel`` is not a count of submissions and not a count of open
Matters: it is the intersection of a canonical fact (open FULL) and a source
fact (no recorded send date), and it has to keep working when a lawyer changes
the canonical half without anybody re-running the cutover.

The responsibility breakdown counts the register's own first names rather than
resolved accounts, because two current Matters name somebody with no account and
filing them under *Määramata* would throw away the one thing the register is
certain about.

Neither is a ranking, and one test says so.
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
    OWNER_KNOWN,
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


def card(user, key):
    return next(c for c in dashboard.summary_cards(user) if c.key == key)


def rows(entries) -> dict[str, int]:
    return {row.label: row.count for row in entries}


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


def test_the_card_counts_what_the_selector_returns(applied) -> None:
    head = applied.people.head
    assert card(head, "drafting").count == dashboard.drafting_matters(head).count()


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
    assert dashboard.drafting_matters(head).count() <= card(head, "active").count


# =========================================================================
# Source responsibility
# =========================================================================


def test_responsibility_is_counted_under_the_name_the_register_gives(applied) -> None:
    breakdown = rows(dashboard.source_responsibility(applied.people.head))
    assert breakdown.get(OWNER_KNOWN, 0) >= 1


def test_the_responsibility_rows_sum_to_the_matters_they_describe(applied) -> None:
    """A breakdown that does not add up is worse than no breakdown."""
    head = applied.people.head
    breakdown = dashboard.source_responsibility(head)
    covered = dashboard.active_matters(head).filter(current_register_state__isnull=False)
    assert sum(row.count for row in breakdown) == covered.distinct().count()


def test_the_drafting_breakdown_sums_to_the_drafting_card(applied) -> None:
    head = applied.people.head
    breakdown = dashboard.drafting_by_responsibility(head)
    assert sum(row.count for row in breakdown) == dashboard.drafting_matters(head).count()


def test_a_specialist_and_the_head_agree_on_the_breakdown(applied) -> None:
    """The visibility predicate joins the collaborators many-to-many for
    everybody except the head, so a count that fanned out would differ between
    the two (app/core/authorization.py)."""
    assert rows(dashboard.source_responsibility(applied.people.head)) == rows(
        dashboard.source_responsibility(applied.people.sandra)
    )


def test_the_breakdown_publishes_no_rate_or_ranking(applied) -> None:
    """Source responsibility, never productivity (specification 18.8).

    Rows carry a name and a count and nothing that reads as a score, and they
    are not links: the register filters on the *resolved* owner while this
    counts the source name, so a link would open a list disagreeing with the
    number above it.
    """
    for row in dashboard.source_responsibility(applied.people.head):
        assert row.url == ""
        assert isinstance(row.count, int)


def test_a_source_name_is_never_shortened_to_its_first_word(applied) -> None:
    """Short names are for resolved accounts. This is somebody's provenance.

    The rail counts the register's own VASTUTAJA text, and the register names a
    colleague who has no account here. Running that string through the
    short-name rule would file a decade of somebody's work under a first name
    the source never wrote, which is the opposite of what keeping the raw text
    is for (docs/adr/0021).
    """
    from app.legacy_import.current_state import CurrentRegisterState

    matter = applied.refresh(CURRENT_DRAFTING)
    state = CurrentRegisterState.objects.get(matter=matter)
    state.owner_raw = "Former Lawyer Fullname"
    state.save(update_fields=["owner_raw", "updated_at"])

    breakdown = rows(dashboard.source_responsibility(applied.people.head))
    assert "Former Lawyer Fullname" in breakdown
    assert "Former" not in breakdown


def test_a_restricted_matter_contributes_to_no_breakdown_it_should_not(applied) -> None:
    from app.core.enums import Visibility

    matter = applied.refresh(CURRENT_DRAFTING)
    matter.visibility = Visibility.RESTRICTED
    matter.owner = None
    matter.save(update_fields=["visibility", "owner", "updated_at"])

    outsider = applied.people.anneli
    assert CURRENT_DRAFTING not in titles(dashboard.drafting_matters(outsider))


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
    assert "Järgmiseks (Excelist)" in body
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
    assert "Järgmiseks (Excelist)" not in body


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
    assert "Järgmiseks (Excelist)" in body

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

    assert "Järgmiseks (Excelist)" in body
    assert "Tähtaeg möödas" not in body
    assert not NextAction.objects.filter(matter=matter).exists()
