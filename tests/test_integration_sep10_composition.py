"""What only holds when this batch is composed, and neither branch could prove.

Three changes met here for the first time, and two of them are about the same
sentence on the same screen:

* **#160** scoped the Teema page's `Järgmiseks` row. It used to read
  `workflow.services.current_next_action`, the domain's reader-blind question,
  and printed a restricted step's words and date to anybody who could open the
  Matter.
* **#161** rebuilt that row's surroundings. Zone A is one action unit now, the
  row carries an accent edge, and the composer sits under it.
* **#164** applied the same rule to the photograph: an
  `OperationalMatterSnapshot` records *which* step it copied, and the three
  derived facts are blanked at read time for a reader who may not see that step.

A clean textual merge proves none of it. `git` will happily compose a scoped
selector with a template that renders a different variable, and the result is a
page that passes both branches' own tests and leaks. So the composition is
asserted here, against the real refined page rather than against selectors in
isolation.

**The oracle is the whole page, byte for byte.** `test_child_existence_visibility`
established that shape and the reason for it: a restricted child must not change
what an unauthorized reader sees *at all*, and "the secret string is absent" is
much weaker than "the page is the same". A count, a badge, an empty state or a
blank where a row used to be all answer the question the reader is not entitled
to ask — whether restricted work is happening on this named file.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.reporting.models import OperationalMatterSnapshot
from app.reporting.selectors import snapshots as snapshot_selectors
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from tests import factories

pytestmark = pytest.mark.django_db

#: Distinctive enough that a match can only have come from the restricted step,
#: and unlike anything the page renders on its own.
HIDDEN_TEXT = "SALAJANE-KOMPOSITSIOON-9312"

#: The date the step carries. Asserted separately from the text, because the row
#: renders them in two different elements and a fix that covered one and not the
#: other is exactly the shape #160 found.
HIDDEN_DATE = "31.12.2099"


@pytest.fixture
def composed(db):
    """A NORMAL Matter, a reader who may open it, and a specialist who owns it.

    The Matter is deliberately ordinary: the point of the two-world comparison
    below is that *nothing about it* changes when a restricted step appears, so
    it must be a file the reader is fully entitled to read.
    """
    owner = factories.UserFactory(display_name="Omanik Oks", role=UserRole.SPECIALIST)
    organisation = factories.OrganisationFactory(name="Näidisministeerium")
    matter = factories.MatterFactory(
        title="Kompositsiooniteema",
        owner=owner,
        visibility=Visibility.NORMAL,
        addressee_organisation=organisation,
        reference_year=2099,
        reference_number=9312,
        received_date=timezone.localdate(),
        response_deadline=timezone.localdate(),
    )
    matter.source_organisations.set([organisation])
    return {
        "owner": owner,
        "matter": matter,
        "reader": factories.UserFactory(role=UserRole.READER, display_name="Lugeja Luts"),
    }


def restricted_step(world):
    """An open step, restricted below a Matter the whole department reads."""
    return factories.NextActionFactory(
        matter=world["matter"],
        text=HIDDEN_TEXT,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.datetime(2099, 12, 31).date(),
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.OPEN,
        responsible=world["owner"],
        visibility_override=Visibility.RESTRICTED,
    )


def _body(client: Client, url: str) -> str:
    """One rendering, with only the per-request CSRF token removed."""
    response = client.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    return re.sub(r'value="[A-Za-z0-9]{32,}"', "CSRF", response.content.decode())


def _detail_url(world) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": world["matter"].pk})


def _client_for(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# #160 × #161 — the refined row renders the scoped answer
# ---------------------------------------------------------------------------


def test_the_refined_matter_page_is_the_one_under_test(composed):
    """Guards every assertion below.

    All of these would pass just as well against the *old* Järgmiseks row, and
    would then be proving nothing about the refinement this batch integrates.
    So the markers the refinement introduced are asserted to be on the page
    first: if Zone A is rebuilt again, this fails and somebody re-reads what the
    tests below are actually looking at.
    """
    page = _body(_client_for(composed["owner"]), _detail_url(composed))

    assert 'id="praegune-tegevus"' in page, "the current-action zone is not on this page"
    assert "curact" in page, "the rebuilt current-action zone is not what rendered"


def test_a_restricted_step_changes_nothing_an_unauthorized_reader_sees(composed):
    """The whole page, byte for byte, in both worlds.

    Stronger than «the text is absent», and deliberately so. What the defect
    disclosed was not only the words: a row that appears, a label that changes
    from an empty state to a filled one, or a date standing where nothing stood
    before all say that restricted work is happening on this named file.
    """
    client = _client_for(composed["reader"])
    url = _detail_url(composed)

    before = _body(client, url)
    restricted_step(composed)
    after = _body(client, url)

    assert HIDDEN_TEXT not in after, "the restricted step's words reached the page"
    assert HIDDEN_DATE not in after, "the restricted step's date reached the page"
    assert before == after, (
        "the page changed when a restricted step appeared under it, so its "
        "existence is observable even though its content is not"
    )


def test_the_empty_state_is_the_same_empty_state(composed):
    """The existence oracle stated on its own, because it is the subtle half.

    `before == after` above would also pass if both renderings were broken in
    the same way. This says what the reader must actually be looking at: the row
    that says nothing is owed, in a world where something restricted *is*.
    """
    client = _client_for(composed["reader"])
    restricted_step(composed)
    page = _body(client, _detail_url(composed))

    assert "curact__empty" in page, (
        "the reader is not being shown the empty current-action zone, so the "
        "restricted step is changing the shape of what they see"
    )
    assert "curact__text" not in page, "a step's text element rendered for a reader"
    assert "curact__date" not in page, "a step's date element rendered for a reader"


def test_the_owner_still_sees_their_own_restricted_step(composed):
    """The other half of the contract, and the more dangerous one to get wrong.

    A fix that hid the work from the people doing it would trade a disclosure
    for a product that lies to its own author.
    """
    restricted_step(composed)
    page = _body(_client_for(composed["owner"]), _detail_url(composed))

    assert HIDDEN_TEXT in page, "the step's own participant cannot see it"
    assert HIDDEN_DATE in page, "the step's own participant cannot see its date"
    assert "curact__text" in page


def test_no_other_matter_route_answers_what_the_page_refuses(composed):
    """The same fact is reachable from more than one address.

    #160 scoped `_overview_context` and `_next_action_row_context` together for
    this reason. These are the reader-reachable `GET`s on a Matter; a scoping
    that covered the page and not its neighbours would leave the answer one URL
    away.
    """
    client = _client_for(composed["reader"])
    restricted_step(composed)

    for name in ("matter_detail", "matter_documents", "timeline_page"):
        url = reverse(f"matters:{name}", kwargs={"pk": composed["matter"].pk})
        body = _body(client, url)
        assert HIDDEN_TEXT not in body, f"{name} disclosed the restricted step"
        assert HIDDEN_DATE not in body, f"{name} disclosed the restricted step's date"


# ---------------------------------------------------------------------------
# #160 × #164 — the live surface and the photograph answer alike
# ---------------------------------------------------------------------------


def _photograph(world):
    """Take the real photograph, through the command the scheduler runs.

    Not a hand-built row. `capture` is what decides which `NextAction` a
    snapshot copies and what it writes about it, so a test that constructed the
    row itself would be asserting against its own idea of the capture rather
    than against the one that runs.
    """
    snapshot_selectors.capture(on=timezone.localdate())
    return OperationalMatterSnapshot.objects.get(matter=world["matter"])


def _as_read_by(user, world):
    return snapshot_selectors.visible_snapshots(user).get(matter=world["matter"])


def test_restricting_a_step_blanks_the_photograph_that_copied_it(composed):
    """The capture is not re-run; the read simply stops answering.

    The pointer records identity, never a visibility decision, so the answer is
    derived live. That is the difference between this and a stored copy, which
    would go stale in the fail-open direction — the reason ADR 0005 removed the
    last one.
    """
    restricted_step(composed)
    _photograph(composed)

    row = _as_read_by(composed["reader"], composed)
    kind, semantics, target = row.next_action_facts()
    assert not kind
    assert not semantics
    assert target is None
    assert row.next_action_is_visible is False
    assert row.has_next_action is False


def test_the_entitled_reader_still_gets_the_photograph_intact(composed):
    action = restricted_step(composed)
    _photograph(composed)

    row = _as_read_by(composed["owner"], composed)
    kind, semantics, target = row.next_action_facts()
    assert kind == action.kind
    assert semantics == action.date_semantics
    assert target == action.target_date
    assert row.has_next_action is True


def test_relaxing_the_step_restores_the_photograph_without_recapturing(composed):
    """Derived at read time, so the answer follows the live row both ways."""
    action = restricted_step(composed)
    _photograph(composed)
    assert _as_read_by(composed["reader"], composed).next_action_facts()[2] is None

    action.visibility_override = Visibility.NORMAL
    action.save(update_fields=["visibility_override"])

    kind, _, target = _as_read_by(composed["reader"], composed).next_action_facts()
    assert target == action.target_date
    assert kind == action.kind


def test_a_row_with_no_pointer_fails_closed(composed):
    """Rows captured before `reporting/0002` recorded no step.

    Nothing says which action they copied, so nothing can say whether this
    reader may see it. Blank is the safe answer, and inventing the missing one
    would be the manufactured history the migration refuses. Reproduced by
    clearing the pointer and leaving the copied facts — which is exactly the
    shape of a row the migration added the column to.
    """
    restricted_step(composed)
    row = _photograph(composed)
    OperationalMatterSnapshot.objects.filter(pk=row.pk).update(next_action=None)

    kind, semantics, target = _as_read_by(composed["reader"], composed).next_action_facts()
    assert target is None
    assert not kind
    assert not semantics


def test_an_unauthorized_reader_cannot_count_the_difference(composed):
    """No row-count oracle.

    Excluding the snapshot would answer the question by its absence: an observer
    counting rows would learn that *something* was captured that day and then
    withheld. The row stays and its derived facts go blank, so «no action that
    day» and «a restricted action that day» read alike.

    `.order_by()` is deliberate. `OperationalMatterSnapshot.Meta.ordering` names
    columns that would join a `GROUP BY` here and split the count into one row
    per snapshot date — a pre-existing sharp edge this test declines to stand on
    rather than to fix (reported as a follow-up).
    """
    restricted_step(composed)
    _photograph(composed)
    restricted_day = snapshot_selectors.visible_snapshots(composed["reader"]).order_by().count()

    OperationalMatterSnapshot.objects.all().delete()
    composed["matter"].next_actions.all().delete()
    _photograph(composed)
    quiet_day = snapshot_selectors.visible_snapshots(composed["reader"]).order_by().count()

    assert restricted_day == quiet_day == 1, (
        "a reader can tell a restricted step from no step by counting rows"
    )
