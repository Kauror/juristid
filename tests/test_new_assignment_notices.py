"""«Uus asi» — has somebody just put a new Matter on my desk?

One question, one surface, and a deliberately narrow implementation. There is no
notification centre here, no bell, no counter and no channel: there is a rail
block on Minu asjad that exists while something is unread and does not exist the
rest of the time (docs/adr/0051).

Four groups of rules, and the tests are grouped the same way.

**When a notice is raised.** A human handing a Matter to somebody — including to
themselves — and nothing else. Not a title edit, not a deadline, not a stage,
not an owner posted again unchanged, and above all not the owner backfill, an
import or a seeding command. The boundary is the one `assign_matter` already
had: an assignment carrying ``provenance`` was made by an operation, and no
colleague told anybody anything.

**When it stops being active.** A real owner transition retires the previous
owner's unread notice before writing the column, so a file handed on at 09:05
does not still sit in the 09:00 recipient's block. So does clearing the owner.
Neither deletes the row: what landed on a desk is a fact about that day.

**What acknowledgement means.** The recipient opened the Matter *from the
block*. Rendering `matter_detail` through any other route does not count, and
the regression that proves it is the load-bearing test in this file — without
it, somebody later "simplifies" the feature by clearing notices in
`matter_detail` and silently breaks the explicit self-assignment requirement.

**Whose it is.** The recipient's, absolutely. Another person cannot acknowledge
it, cannot see it, and a department head reading the same page about a colleague
does not receive it at all — the block is absent from that response rather than
hidden in it, exactly as the scratchpad is.
"""

from __future__ import annotations

import re

import pytest
from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.enums import MatterOrigin
from app.matters.models import Matter, MatterAssignmentNotice
from app.matters.person_work import unread_assignment_notices
from app.matters.services import assign_matter, close_matter, create_matter, set_matter_title
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

MY_WORK = reverse("matters:my_work")


def _open_url(notice):
    return reverse("matters:open_assignment_notice", kwargs={"notice_id": notice.pk})


def _active(recipient):
    return MatterAssignmentNotice.objects.filter(
        recipient=recipient, viewed_at__isnull=True, superseded_at__isnull=True
    )


def _rail(html: str) -> str:
    """Just the «Uus asi» section, so an unrelated hit elsewhere is not a leak.

    The manager's page prints the same Matter titles in the ordinary work
    portfolio, and it is supposed to — a department head may read a colleague's
    work. Searching the whole document for a title would call that a privacy
    breach; the question is only ever whether *this section* is there.
    """
    match = re.search(r'<section class="railblock" aria-label="Uus asi">.*?</section>', html, re.S)
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# A. — J.  When a notice is raised, and when it is not
# ---------------------------------------------------------------------------


def test_assigning_to_a_colleague_notifies_them(specialist, other_specialist):
    """A. The ordinary case: Marko hands a file to Sandra."""
    matter = factories.MatterFactory(owner=specialist)

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    notice = _active(other_specialist).get()
    assert notice.matter_id == matter.pk
    assert notice.assigned_by_id == specialist.pk
    assert not _active(specialist).exists()


def test_assigning_to_yourself_notifies_you(specialist, other_specialist):
    """B. `actor == recipient` is deliberately not excluded.

    Taking a file off the unassigned pile is an arrival on your own desk, and
    the block is the record that it has not been looked at yet. This is an
    explicit product requirement, not an accident of the implementation.
    """
    matter = factories.MatterFactory(owner=other_specialist)

    assign_matter(matter=matter, owner=specialist, actor=specialist)

    notice = _active(specialist).get()
    assert notice.recipient_id == notice.assigned_by_id == specialist.pk


def test_creating_a_matter_for_a_colleague_notifies_them(specialist, other_specialist):
    """C. Creation is an assignment too, and never passes through `assign_matter`.

    `create_matter` writes the owner column directly, so owner-change handling
    alone would leave the commonest hand-over in the product silent: filing
    something new and naming who is to deal with it.
    """
    matter = create_matter(title="Uus eelnõu kolleegile", actor=specialist, owner=other_specialist)

    assert _active(other_specialist).get().matter_id == matter.pk


def test_creating_a_matter_for_yourself_notifies_you(specialist):
    """D. And the same when the creator names themselves."""
    matter = create_matter(title="Uus eelnõu endale", actor=specialist, owner=specialist)

    assert _active(specialist).get().matter_id == matter.pk


def test_creating_without_an_owner_notifies_nobody(specialist):
    """E. Nothing landed on anybody's desk."""
    create_matter(title="Vastutajata teema", actor=specialist, owner=None)

    assert not MatterAssignmentNotice.objects.exists()


def test_reassignment_supersedes_the_unread_notice(specialist, other_specialist):
    """F. 09:00 Sandra, 09:05 Ireen. Sandra must not still be offered it.

    A notice nobody read is still about a state that has ended, and offering it
    would open a Matter its recipient no longer owns.
    """
    ireen = factories.UserFactory()
    matter = factories.MatterFactory(owner=None)

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    stale = _active(other_specialist).get()

    assign_matter(matter=matter, owner=ireen, actor=specialist)

    stale.refresh_from_db()
    assert stale.superseded_at is not None
    assert stale.viewed_at is None
    assert not _active(other_specialist).exists()
    assert _active(ireen).get().matter_id == matter.pk


def test_unassignment_retires_the_notice_and_raises_none(specialist, other_specialist):
    """G. owner → None. The file is on nobody's desk, so nobody is told."""
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    assign_matter(matter=matter, owner=None, actor=specialist)

    assert not _active(other_specialist).exists()
    assert MatterAssignmentNotice.objects.count() == 1
    assert MatterAssignmentNotice.objects.get().superseded_at is not None


def test_a_no_op_assignment_creates_no_second_notice(specialist, other_specialist):
    """H. The same owner submitted again is not a new arrival.

    Different from creation naming that person: there, the file arrived; here,
    nothing was assigned, and `assign_matter` returns before it writes anything.
    """
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    first = _active(other_specialist).get()

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    assert list(_active(other_specialist).values_list("pk", flat=True)) == [first.pk]


def test_an_automated_assignment_notifies_nobody(specialist, other_specialist):
    """I. The owner backfill derives ownership from a spreadsheet cell.

    It runs under an operator's account, so «is there an actor» is not the
    question. `provenance` is: it is present exactly when no colleague decided
    this, and it is the boundary the audit trail already used.
    """
    matter = factories.MatterFactory(owner=None)

    assign_matter(
        matter=matter,
        owner=other_specialist,
        actor=specialist,
        provenance={"era": "2019", "rule": "initials"},
    )

    matter.refresh_from_db()
    assert matter.owner_id == other_specialist.pk
    assert not MatterAssignmentNotice.objects.exists()


def test_an_import_notifies_nobody(specialist, other_specialist):
    """J. An imported row's owner is reconstructed history, not news.

    The day of the import is not the day the file landed on that person's desk,
    and a register refresh that produced a queue of «uus asi» rows would be the
    historical backfill this feature explicitly refuses (§26).
    """
    create_matter(
        title="Imporditud registririda",
        actor=specialist,
        owner=other_specialist,
        origin=MatterOrigin.LEGACY_IMPORT,
        assign_reference=False,
    )

    assert not MatterAssignmentNotice.objects.exists()


def test_a_seeding_command_notifies_nobody(specialist, other_specialist):
    """The same boundary, from the other caller that has no colleague behind it.

    `seed_e2e_data` builds the world every browser and visual scenario runs
    against. A synthetic lawyer who signs in to a rail full of hand-overs
    nobody performed would put the feature into every baseline of every page.
    """
    create_matter(
        title="Seemendatud teema",
        actor=specialist,
        owner=other_specialist,
        provenance={"materialised_by": "seed"},
    )

    assert not MatterAssignmentNotice.objects.exists()


def test_registering_incoming_material_for_a_colleague_notifies_them(specialist, other_specialist):
    """Saabunud is the fifth human assignment path, and it centralises too.

    `register_incoming` files what arrived and names who is to deal with it. It
    reaches ownership through `create_matter` rather than through a trigger of
    its own, which is the whole reason there is one rule here and not five.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.intake import register_incoming, validate_uploads

    uploads = validate_uploads(
        [SimpleUploadedFile("eelnou.pdf", b"%PDF-1.4 synthetic incoming draft")]
    )

    result = register_incoming(uploads=uploads, actor=specialist, owner=other_specialist)

    assert _active(other_specialist).get().matter_id == result.matter.pk


def test_no_human_path_writes_the_owner_column_behind_the_services(specialist):
    """§20. Two writers of `Matter.owner`, and they are the two that notify.

    The rule this feature rests on is not "every view remembered to call the
    service" — it is that there is nowhere else to call. Asserted against the
    source rather than against behaviour, because the failure it guards is a
    *future* module writing the column directly and being silent: no test of
    today's paths can fail for that, and this one can.
    """
    from pathlib import Path

    from django.conf import settings

    attribute = re.compile(r"^\s*[\w.]*matter\.owner\s*=(?!=)", re.M)
    bulk = re.compile(r"Matter\.objects[^\n]*\.update\([^)]*\bowner\b")

    application = Path(settings.BASE_DIR) / "app"
    offenders = sorted(
        str(path.relative_to(settings.BASE_DIR)).replace("\\", "/")
        for path in application.rglob("*.py")
        if attribute.search(text := path.read_text(encoding="utf-8")) or bulk.search(text)
    )

    assert offenders == ["app/matters/services.py"], (
        "Matter.owner is written outside the assignment services; route it "
        "through create_matter/assign_matter rather than adding a third trigger"
    )


def test_editing_other_fields_notifies_nobody(specialist, other_specialist):
    """Only ownership. A title change is not somebody handing over work."""
    matter = factories.MatterFactory(owner=other_specialist)

    set_matter_title(matter=matter, value="Parandatud pealkiri", actor=specialist)

    assert not MatterAssignmentNotice.objects.exists()


# ---------------------------------------------------------------------------
# The selector
# ---------------------------------------------------------------------------


def test_the_selector_returns_only_this_persons_active_notices(specialist, other_specialist):
    mine = factories.MatterFactory(owner=None)
    theirs = factories.MatterFactory(owner=None)
    assign_matter(matter=mine, owner=specialist, actor=other_specialist)
    assign_matter(matter=theirs, owner=other_specialist, actor=specialist)

    assert [n.matter_id for n in unread_assignment_notices(specialist)] == [mine.pk]


def test_the_selector_is_newest_first(specialist, other_specialist):
    """An arrival notification, ordered by arrival. Not by deadline or severity."""
    first = factories.MatterFactory(owner=None, title="Esimesena saabunud")
    second = factories.MatterFactory(owner=None, title="Teisena saabunud")
    assign_matter(matter=first, owner=specialist, actor=other_specialist)
    assign_matter(matter=second, owner=specialist, actor=other_specialist)

    assert [n.matter.title for n in unread_assignment_notices(specialist)] == [
        "Teisena saabunud",
        "Esimesena saabunud",
    ]


def test_the_selector_drops_a_matter_this_person_no_longer_owns(specialist, other_specialist):
    """Belt and braces beside superseding: the read asks the current state too."""
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    notice = _active(specialist).get()

    # Written around the service, so only the ownership fact moves and the
    # notice is deliberately left active. The selector must still refuse it.
    Matter.objects.filter(pk=matter.pk).update(owner=other_specialist)

    assert unread_assignment_notices(specialist) == []
    notice.refresh_from_db()
    assert notice.viewed_at is None


def test_the_selector_drops_a_closed_matter(specialist, other_specialist):
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)

    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    assert unread_assignment_notices(specialist) == []


def test_the_selector_reads_through_the_matters_own_gate(specialist, other_specialist):
    """M. Ownership is not authorization, and the selector does not assume it is.

    Worth being exact about what this can and cannot demonstrate today. In the
    current model an *owner* participates in their own Matter, so `visible_to`
    admits a RESTRICTED file its owner holds — which is correct, and means
    restricting a Matter is not by itself a way to make the rail hide it.

    What can collapse is the scope. An account that has been deactivated is not
    an authenticated reader at all, and every Matter queryset it touches is
    empty. That is the observable proof that the gate is in the query rather
    than assumed away by `owner=user`, and it is the shape a future rule —
    a narrower participation model, a Matter moved out of the department — would
    arrive in (app/core/authorization.py, docs/adr/0042).
    """
    matter = factories.MatterFactory(owner=None, title="Piiratud pealkiri")
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    Matter.objects.filter(pk=matter.pk).update(visibility=Visibility.RESTRICTED)
    assert unread_assignment_notices(specialist), "an owner may still read their own file"

    specialist.is_active = False
    specialist.save(update_fields=["is_active"])

    assert unread_assignment_notices(specialist) == []


# ---------------------------------------------------------------------------
# K. — L.  Acknowledgement
# ---------------------------------------------------------------------------


def test_opening_from_the_block_marks_it_viewed(client, specialist, other_specialist):
    """K. And lands the reader on the Matter."""
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    notice = _active(specialist).get()
    client.force_login(specialist)

    response = client.post(_open_url(notice))

    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "matters:matter_detail", kwargs={"pk": matter.pk}
    )
    notice.refresh_from_db()
    assert notice.viewed_at is not None
    assert unread_assignment_notices(specialist) == []


def test_acknowledgement_is_idempotent(client, specialist, other_specialist):
    """A resent form must not move the stamp somebody already earned."""
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    notice = _active(specialist).get()
    client.force_login(specialist)

    client.post(_open_url(notice))
    notice.refresh_from_db()
    first = notice.viewed_at

    client.post(_open_url(notice))
    notice.refresh_from_db()

    assert notice.viewed_at == first


def test_another_person_cannot_acknowledge_it(client, specialist, other_specialist):
    """L. 404 — the row does not resolve for anybody but its recipient.

    404 rather than 403 for the reason every restricted surface here answers
    404: a 403 would confirm the notice exists.
    """
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    notice = _active(specialist).get()
    client.force_login(other_specialist)

    response = client.post(_open_url(notice))

    assert response.status_code == 404
    notice.refresh_from_db()
    assert notice.viewed_at is None


def test_acknowledgement_refuses_a_matter_the_recipient_may_not_read(client, specialist, reader):
    """The Matter goes through `get_visible_matter` like every other route.

    A READER whose account holds a notice about a RESTRICTED file they neither
    own nor collaborate on: the notice is theirs, and the Matter is still not
    theirs to read. The route must refuse before it redirects, and must not
    stamp a receipt on the way (03-BACKEND §5).
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    notice = MatterAssignmentNotice.objects.create(
        matter=matter, recipient=reader, assigned_by=specialist
    )
    client.force_login(reader)

    response = client.post(_open_url(notice))

    assert response.status_code == 404
    notice.refresh_from_db()
    assert notice.viewed_at is None


def test_acknowledgement_refuses_get(client, specialist, other_specialist):
    """A GET may never mutate a read receipt, whatever the URL says."""
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    notice = _active(specialist).get()
    client.force_login(specialist)

    assert client.get(_open_url(notice)).status_code == 405
    notice.refresh_from_db()
    assert notice.viewed_at is None


def test_acknowledgement_requires_a_session(client, specialist, other_specialist):
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    notice = _active(specialist).get()

    response = client.post(_open_url(notice))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(str(resolve_url(settings.LOGIN_URL)))
    notice.refresh_from_db()
    assert notice.viewed_at is None


# ---------------------------------------------------------------------------
# §22.  The regression that protects the self-assignment requirement
# ---------------------------------------------------------------------------


def test_ordinary_matter_viewing_does_not_acknowledge(client, specialist):
    """Self-assignment must survive being redirected into your own Matter.

    Saving a new Teema with your own name on it lands you on the Matter page.
    If that page cleared the notice, the block would be gone before its owner
    ever reached Minu asjad — and the explicit requirement that self-assignment
    produces a visible notice would be quietly untrue.

    Written as a sequence rather than as two assertions because the ordering is
    the whole claim: unviewed *after* the detail page, still on the rail, and
    gone only after the acknowledgement.
    """
    matter = create_matter(title="Endale määratud teema", actor=specialist, owner=specialist)
    notice = _active(specialist).get()
    client.force_login(specialist)

    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert detail.status_code == 200
    notice.refresh_from_db()
    assert notice.viewed_at is None, "rendering the Matter must not be a read receipt"

    # And it is still on the rail.
    assert "Uus asi" in _rail(client.get(MY_WORK).content.decode())

    client.post(_open_url(notice))

    notice.refresh_from_db()
    assert notice.viewed_at is not None
    assert _rail(client.get(MY_WORK).content.decode()) == ""


# ---------------------------------------------------------------------------
# §23.  The rail
# ---------------------------------------------------------------------------


def test_no_unread_renders_no_block_at_all(client, specialist):
    """Not an empty section, not a zero, not a heading. Nothing."""
    client.force_login(specialist)

    html = client.get(MY_WORK).content.decode()

    assert "Märkmed" in html
    assert "Uus asi" not in html
    assert 'aria-label="Uus asi"' not in html


def test_one_unread_renders_the_block_above_markmed(client, specialist, other_specialist):
    matter = factories.MatterFactory(owner=None, title="Saabunud üksik teema")
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    client.force_login(specialist)

    html = client.get(MY_WORK).content.decode()
    block = _rail(html)

    assert block
    assert block.count("Saabunud üksik teema") == 1
    assert html.index('aria-label="Uus asi"') < html.index('aria-label="Märkmed"')


def test_several_unread_render_one_heading_and_every_row(client, specialist, other_specialist):
    """§16. One section, several rows — never one section per arrival."""
    titles = ["Teema A saabunud", "Teema B saabunud", "Teema C saabunud"]
    for title in titles:
        assign_matter(
            matter=factories.MatterFactory(owner=None, title=title),
            owner=specialist,
            actor=other_specialist,
        )
    client.force_login(specialist)

    html = client.get(MY_WORK).content.decode()
    block = _rail(html)

    assert html.count('aria-label="Uus asi"') == 1
    assert html.count('<h2 class="railblock__label">Uus asi</h2>') == 1
    for title in titles:
        assert title in block


def test_the_block_shrinks_and_then_disappears(client, specialist, other_specialist):
    """§16, end to end: B is opened, A and C remain, then nothing remains."""
    matters = {
        title: factories.MatterFactory(owner=None, title=title)
        for title in ("Teema A saabunud", "Teema B saabunud", "Teema C saabunud")
    }
    for matter in matters.values():
        assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    client.force_login(specialist)

    middle = _active(specialist).get(matter=matters["Teema B saabunud"])
    client.post(_open_url(middle))

    block = _rail(client.get(MY_WORK).content.decode())
    assert "Teema A saabunud" in block
    assert "Teema C saabunud" in block
    assert "Teema B saabunud" not in block

    for remaining in list(_active(specialist)):
        client.post(_open_url(remaining))

    html = client.get(MY_WORK).content.decode()
    assert _rail(html) == ""
    assert "Uus asi" not in html
    assert "Märkmed" in html


# ---------------------------------------------------------------------------
# §24.  The manager's page
# ---------------------------------------------------------------------------


def test_a_department_head_does_not_receive_a_colleagues_queue(client, specialist, department_head):
    """Absent from the response, not hidden in it — and not marked read either.

    The Matter title itself may legitimately appear on that page: a department
    head may read a colleague's work portfolio, and this is a personal-state
    test, not a claim that the manager cannot see the Matter. So the assertion
    targets the section.
    """
    matter = factories.MatterFactory(owner=None, title="Sandra uus teema")
    assign_matter(matter=matter, owner=specialist, actor=department_head)
    notice = _active(specialist).get()
    client.force_login(department_head)

    response = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "assignment_notices" not in response.context
    assert _rail(html) == ""
    assert 'aria-label="Uus asi"' not in html
    assert reverse("matters:open_assignment_notice", kwargs={"notice_id": notice.pk}) not in html

    notice.refresh_from_db()
    assert notice.viewed_at is None


def test_a_person_reading_their_own_page_by_the_person_route_still_gets_it(
    client, specialist, other_specialist
):
    """`/inimesed/<self>/asjad/` is the self view, and behaves like one."""
    matter = factories.MatterFactory(owner=None, title="Iseenda lehel nähtav")
    assign_matter(matter=matter, owner=specialist, actor=other_specialist)
    client.force_login(specialist)

    html = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk})).content.decode()

    assert "Iseenda lehel nähtav" in _rail(html)


# ---------------------------------------------------------------------------
# §27, §28, §29.  Query shape, atomicity, and the audit trail beside it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 20])
def test_the_rail_query_does_not_grow_with_the_number_of_notices(
    django_assert_num_queries, specialist, other_specialist, count
):
    """The same two queries at 0, 1 and 20 notices. No growth per row.

    Two and not one, and both are constant: `visible_to` resolves the reader's
    scope, which asks whether they hold a break-glass grant, and then the rail
    is read in a single statement with the Matter joined. Twenty notices cost
    what one costs; that is the property worth locking.

    Asserted against the selector rather than the whole page, because the page's
    own count moves whenever an unrelated band changes and a test that failed
    for that reason would be deleted rather than read.
    """
    for index in range(count):
        assign_matter(
            matter=factories.MatterFactory(owner=None, title=f"Teema {index}"),
            owner=specialist,
            actor=other_specialist,
        )

    with django_assert_num_queries(2):
        rows = unread_assignment_notices(specialist)
        # The template reads the title through the join, so touching it here is
        # what makes the assertion mean "no N+1" rather than "no evaluation".
        assert [row.matter.title for row in rows] == [
            f"Teema {index}" for index in reversed(range(count))
        ]


def test_a_failed_notice_takes_the_ownership_change_with_it(
    monkeypatch, specialist, other_specialist
):
    """§28. The notice and the ownership are one transaction, or neither happens.

    The failure is provoked at the notice write, which is the direction that
    matters: an ownership change committed without the notice its recipient is
    owed would be a silent hand-over.
    """
    from app.matters import services

    matter = factories.MatterFactory(owner=None)

    def explode(**kwargs):
        raise RuntimeError("teavituse kirjutamine ebaõnnestus")

    monkeypatch.setattr(services, "_raise_assignment_notice", explode)

    with pytest.raises(RuntimeError):
        assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    matter.refresh_from_db()
    assert matter.owner_id is None
    assert not MatterAssignmentNotice.objects.exists()


def test_the_assignment_audit_event_is_not_replaced(specialist, other_specialist):
    """§29. The notice sits beside the canonical trail, not instead of it."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    matter = factories.MatterFactory(owner=None)

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    events = ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED)
    assert events.count() == 1
    assert _active(other_specialist).count() == 1


def test_the_notice_stores_no_copy_of_the_matter(specialist, other_specialist):
    """§7. Foreign keys, not snapshots. A renamed Teema renames on the rail."""
    matter = factories.MatterFactory(owner=None, title="Esialgne pealkiri")
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    set_matter_title(matter=matter, value="Parandatud pealkiri", actor=specialist)

    assert unread_assignment_notices(other_specialist)[0].matter.title == "Parandatud pealkiri"
    stored = {field.name for field in MatterAssignmentNotice._meta.get_fields()}
    assert not stored & {"title", "matter_title", "owner_name", "url"}


def test_the_stamps_are_wall_clock_and_ordered(specialist, other_specialist):
    """A cheap guard on the lifecycle: created, then superseded, never before."""
    before = timezone.now()
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    assign_matter(matter=matter, owner=specialist, actor=specialist)

    retired = MatterAssignmentNotice.objects.get(recipient=other_specialist)
    assert before <= retired.created_at <= retired.superseded_at <= timezone.now()
