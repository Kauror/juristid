"""An informational milestone never stands in for a missing next step (docs/adr/0120).

ADR 0120 §2 lets an upcoming `Oluline tähtaeg` be the file's next step when no
`Järgmiseks` is set — «Ootan ministeeriumi tagasisidet, 1.10.2026» says what
the file is waiting for. A commencement a draft proposes («Eelnõus kavandatud
jõustumine», `ImportantDateKind.PLANNED_COMMENCEMENT`) says nothing about what
the lawyer does next, and letting it stand in hid a file with no step from every
«järgmine tegevus puudub» population.

These tests pin the distinction on every surface that asks: the Teema's
`PRAEGUNE TEGEVUS`, `without_next_step` (register `?tegevus=puudub`, Osakond,
Statistika), `Minu asjad`'s portfolio row, the work items' lateness and the
header's nearest deadline — and that ADR 0120's own cases are unchanged.
Synthetic data only.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import (
    INFORMATIONAL_IMPORTANT_DATE_KINDS,
    FactStatus,
    ImportantDateKind,
    is_informational_milestone,
)
from app.intelligence.models import MatterImportantDate
from app.intelligence.services import add_important_date
from app.matters import dashboard
from app.matters import work_items as wi
from app.matters import workspace as ws
from app.matters.enums import EngagementKind
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.next_step import upcoming_milestone, without_next_step
from app.matters.selectors import active_deadline, filter_by_next_action
from app.reporting.context import ReportingContext, parse_period
from app.reporting.selectors.activity import active_without_next_action
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action, set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

PLANNED = ImportantDateKind.PLANNED_COMMENCEMENT
LABEL = "Eelnõus kavandatud jõustumine"
TODAY = timezone.localdate()


def _milestone(matter, actor, *, days, kind=PLANNED, title=LABEL):
    day = TODAY + timedelta(days=days)
    return add_important_date(
        matter=matter, title=title, date_value=day, period_end=day, kind=kind, actor=actor
    )


def _draft_law_teema(owner, *, response_in_days, commencement_in_days):
    """The 2026_225 shape: an open opinion deadline, a draft's commencement, no step."""
    matter = factories.MatterFactory(
        owner=owner, response_deadline=TODAY + timedelta(days=response_in_days)
    )
    _milestone(matter, owner, days=commencement_in_days)
    return matter


def _zone(client, matter) -> str:
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _missing(user, matters) -> set:
    return set(
        without_next_step(Matter.objects.filter(pk__in=[m.pk for m in matters]), user).values_list(
            "pk", flat=True
        )
    )


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_only_the_two_commencement_kinds_are_informational():
    estimated = ImportantDateKind.ESTIMATED_COMMENCEMENT
    assert INFORMATIONAL_IMPORTANT_DATE_KINDS == {PLANNED.value, estimated.value}
    assert is_informational_milestone(PLANNED)
    assert is_informational_milestone(estimated)
    # Two labels, so a source's estimate is never attributed to a bill's text.
    assert ImportantDateKind(estimated).label == "Eeldatav jõustumine (allika hinnang)"
    assert ImportantDateKind(estimated).label != ImportantDateKind(PLANNED).label
    # Every existing row is OTHER and keeps ADR 0120's behaviour, as does the
    # transposition deadline: moving a kind is a product decision.
    assert not is_informational_milestone(ImportantDateKind.OTHER)
    assert not is_informational_milestone(ImportantDateKind.TRANSPOSITION_DEADLINE)
    assert not is_informational_milestone(None)
    assert ImportantDateKind(PLANNED).label == LABEL


# ---------------------------------------------------------------------------
# 1–3. The three draft-law Teemad: deadline and milestone shown, step unset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response_in_days", "commencement_in_days"),
    [(16, 213), (16, 51), (9, 263)],
    ids=["like-2026_225", "like-2026_231", "like-2026_239"],
)
def test_a_draft_laws_commencement_leaves_the_next_step_unset(
    client, specialist, response_in_days, commencement_in_days
):
    matter = _draft_law_teema(
        specialist, response_in_days=response_in_days, commencement_in_days=commencement_in_days
    )
    client.force_login(specialist)

    zone = _zone(client, matter)
    assert "Järgmine samm on määramata" in zone
    assert LABEL not in zone
    page = _page(client, matter)
    # The milestone is still on the Teema — a date the file watches …
    assert LABEL in page
    # … and the opinion deadline is still the header's.
    assert "Arvamuse tähtaeg" in page
    # Nothing was written to make the number move.
    assert not matter.next_actions.exists()
    assert matter.pk in _missing(specialist, [matter])


# ---------------------------------------------------------------------------
# 4–7. Real next steps are unchanged
# ---------------------------------------------------------------------------


def test_a_do_step_beside_an_informational_milestone_is_the_next_step(client, specialist):
    matter = _draft_law_teema(specialist, response_in_days=16, commencement_in_days=200)
    set_next_action_for_new_work(
        matter=matter,
        text="Koostan arvamuse",
        target_date=TODAY + timedelta(days=14),
        actor=specialist,
    )
    client.force_login(specialist)

    zone = _zone(client, matter)
    assert "Koostan arvamuse" in zone
    assert "Järgmine samm on määramata" not in zone
    assert matter.pk not in _missing(specialist, [matter])


def test_a_wait_step_is_the_next_step(client, specialist):
    matter = _draft_law_teema(specialist, response_in_days=16, commencement_in_days=200)
    set_next_action(
        matter=matter,
        text="Ootan II lugemist Riigikogus",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=TODAY + timedelta(days=30),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )
    client.force_login(specialist)

    assert "Ootan II lugemist Riigikogus" in _zone(client, matter)
    assert matter.pk not in _missing(specialist, [matter])


def test_a_waiting_milestone_still_stands_in_for_the_step(client, specialist):
    """ADR 0120 §2's own case, unchanged: an `OTHER` milestone is what the file waits for."""
    matter = factories.MatterFactory(owner=specialist)
    _milestone(
        matter,
        specialist,
        days=10,
        kind=ImportantDateKind.OTHER,
        title="Ootan ministeeriumi tagasisidet",
    )
    client.force_login(specialist)

    zone = _zone(client, matter)
    assert "Ootan ministeeriumi tagasisidet" in zone
    assert "Järgmine samm on määramata" not in zone
    assert matter.pk not in _missing(specialist, [matter])


def test_an_open_round_waiting_for_feedback_is_the_next_step(client, specialist):
    matter = _draft_law_teema(specialist, response_in_days=16, commencement_in_days=200)
    ws.add_matter_engagement(
        matter=matter,
        author=specialist,
        audience="Liikmed",
        kind=EngagementKind.SURVEY.value,
        occurred_on=TODAY,
        feedback_deadline=TODAY + timedelta(days=10),
    )
    client.force_login(specialist)

    assert "Järgmine samm on määramata" not in _zone(client, matter)
    assert matter.pk not in _missing(specialist, [matter])


# ---------------------------------------------------------------------------
# 8. Cancelled, past and restricted milestones
# ---------------------------------------------------------------------------


def test_cancelled_past_and_restricted_milestones_are_never_the_step(
    specialist, reader, administrator
):
    cancelled = factories.MatterFactory(owner=specialist)
    record = _milestone(cancelled, specialist, days=10, kind=ImportantDateKind.OTHER, title="X")
    MatterImportantDate.objects.filter(pk=record.pk).update(status=FactStatus.CANCELLED)

    past = factories.MatterFactory(owner=specialist)
    _milestone(past, specialist, days=-3, kind=ImportantDateKind.OTHER, title="Möödunud")

    restricted = factories.MatterFactory(owner=specialist)
    hidden = _milestone(
        restricted, specialist, days=10, kind=ImportantDateKind.OTHER, title="Piiratud"
    )
    MatterImportantDate.objects.filter(pk=hidden.pk).update(visibility_override="RESTRICTED")

    assert _missing(specialist, [cancelled, past]) == {cancelled.pk, past.pk}
    # A restriction below the Matter decides nothing for a reader who may not
    # see it: the step it would stand for is not theirs to see (AUTH-003).
    # Since docs/adr/0181 a READER sees it as the owner does; the one who may
    # not is the administrator without a grant.
    assert restricted.pk not in _missing(specialist, [restricted])
    assert restricted.pk not in _missing(reader, [restricted])
    assert restricted.pk in _missing(administrator, [restricted])


def test_an_informational_milestone_that_has_passed_is_not_late(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, specialist, days=-5)

    items = [
        item
        for item in wi.work_items(specialist, today=TODAY)
        if item.matter_id == matter.pk and item.source_type == wi.SOURCE_IMPORTANT_DEADLINE
    ]
    assert [item.is_informational for item in items] == [True]
    assert not items[0].is_overdue
    deadline = active_deadline(matter, specialist, today=TODAY)
    assert deadline is not None and deadline.is_past and not deadline.is_overdue
    # An `OTHER` milestone that passed is still late, as before.
    other = factories.MatterFactory(owner=specialist)
    _milestone(other, specialist, days=-5, kind=ImportantDateKind.OTHER, title="Lubatud vastus")
    late = active_deadline(other, specialist, today=TODAY)
    assert late is not None and late.is_overdue


# ---------------------------------------------------------------------------
# 9. Several milestones, only some operational
# ---------------------------------------------------------------------------


def test_the_operational_milestone_wins_even_when_an_informational_one_is_due_first(
    client, specialist
):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, specialist, days=5)  # informational, due first
    _milestone(matter, specialist, days=20, kind=ImportantDateKind.OTHER, title="Komisjoni istung")
    client.force_login(specialist)

    chosen = upcoming_milestone(MatterImportantDate.objects.filter(matter=matter), TODAY)
    assert chosen is not None and chosen.title == "Komisjoni istung"
    assert "Komisjoni istung" in _zone(client, matter)
    assert matter.pk not in _missing(specialist, [matter])


def test_only_informational_milestones_leave_the_step_unset(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, specialist, days=5)
    _milestone(matter, specialist, days=90, title="Teine kavandatud jõustumine")

    assert upcoming_milestone(MatterImportantDate.objects.filter(matter=matter), TODAY) is None
    assert matter.pk in _missing(specialist, [matter])


# ---------------------------------------------------------------------------
# 10. One population on every screen
# ---------------------------------------------------------------------------


def test_every_screen_counts_the_same_files_as_having_no_next_step(specialist, department_head):
    draft = _draft_law_teema(specialist, response_in_days=16, commencement_in_days=200)
    waiting = factories.MatterFactory(owner=specialist)
    _milestone(waiting, specialist, days=10, kind=ImportantDateKind.OTHER, title="Ootan vastust")
    working = factories.MatterFactory(owner=specialist)
    set_next_action_for_new_work(
        matter=working,
        text="Koostan arvamuse",
        target_date=TODAY + timedelta(days=7),
        actor=specialist,
    )
    idle = factories.MatterFactory(owner=specialist)
    ours = [draft, waiting, working, idle]
    expected = {draft.pk, idle.pk}

    for viewer in (specialist, department_head):
        scoped = Matter.objects.filter(pk__in=[m.pk for m in ours])
        # The register's `?tegevus=puudub`.
        register = set(filter_by_next_action(scoped, viewer, "puudub").values_list("pk", flat=True))
        # Osakond's «järgmise tegevuseta».
        osakond = set(
            dashboard.without_next_action(viewer).filter(pk__in=scoped).values_list("pk", flat=True)
        )
        assert register == osakond == expected

    # Minu asjad's own rows say the same about the owner's files.
    portfolio = build_my_work(specialist, TODAY).portfolio
    assert portfolio is not None
    rows = {
        row.matter.pk: row for row in portfolio.all_rows if row.matter.pk in {m.pk for m in ours}
    }
    assert {pk for pk, row in rows.items() if not row.has_action} == expected
    # The draft's milestone is not dressed as the row's step.
    assert rows[draft.pk].next_action is None
    assert rows[waiting.pk].next_action is not None

    # Statistika's figure counts the same files (it links to `?tegevus=puudub`).
    context = ReportingContext(
        viewer=department_head,
        period=parse_period("koik", TODAY),
        today=TODAY,
        now=timezone.now(),
    )
    figure = active_without_next_action(context).value
    assert (
        figure
        == without_next_step(
            Matter.objects.filter(is_open=True, record_mode="FULL"), department_head
        ).count()
    )


def test_a_source_estimated_commencement_is_informational_too(client, specialist):
    """The 2026_225 shape of v2.3: the owner's selected estimate, not the bill's date."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=TODAY + timedelta(days=15))
    _milestone(
        matter,
        specialist,
        days=219,
        kind=ImportantDateKind.ESTIMATED_COMMENCEMENT,
        title="Eeldatav jõustumine",
    )
    client.force_login(specialist)

    assert "Järgmine samm on määramata" in _zone(client, matter)
    assert matter.pk in _missing(specialist, [matter])
    record = MatterImportantDate.objects.get(matter=matter)
    passed = wi._deadline_item(record, record.period_end + timedelta(days=1))
    assert passed.is_informational and not passed.is_overdue
