"""`Kaasamine` waits for answers, and somebody finishes it.

docs/adr/0086 turns `MatterEngagement.feedback_deadline` from a recorded fact
into a state. A round that asked members to answer by the 22nd is an **open
waiting activity**: it shows on `PRAEGUNE TEGEVUS`, it shows as work on the
responsible lawyer's Minu asjad, it turns due on the day it named, and it ends
only when somebody presses `Lõpeta kaasamine` — or when the Matter closes
underneath it.

What this module holds
----------------------
* the waiting item, who sees it, and the one thing it is never allowed to do,
  which is to replace an open `Järgmiseks`;
* the two facts the narrowing of docs/adr/0078 §3 does **not** move: no
  `NextAction`, no `MatterImportantDate`, no `Matter.response_deadline`, no
  discharge, no *Tähtajad* population, no search or archive row;
* `Lõpeta kaasamine` — its audit, its two state refusals, its closed-Matter
  refusal, its concurrency, and that an empty box is a real answer;
* what a Matter closure does to a wait, and what reopening does not undo.

Not held here: `tests/test_engagement_dates.py` owns the two dates' defaults and
clearing, `tests/test_engagement_date_precision.py` owns the preservation of a
stored period, and `tests/test_engagement_correction.py` owns the correction
contract itself.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.intelligence.models import MatterImportantDate
from app.matters import work_items as wi
from app.matters.enums import EngagementKind, RecordMode
from app.matters.models import MatterEngagement
from app.matters.services import (
    ENGAGEMENT_FEEDBACK_ALREADY_CLOSED,
    ENGAGEMENT_FEEDBACK_NOT_AWAITED,
    EngagementEditConflict,
    add_engagement,
    close_matter,
    complete_engagement_feedback,
    correct_engagement,
    engagement_revision_token,
    reopen_matter,
)
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _waiting(matter, *, days: int = 7, actor=None, **extra):
    """One consultation round still waiting, ``days`` from today.

    Relative to the clock rather than a written date: a work surface bands on
    today, so a hard-coded deadline is a test that changes meaning depending on
    when it runs.
    """
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=timezone.localdate() - dt.timedelta(days=1),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=days),
        actor=actor,
        **extra,
    )


def _finish_url(engagement) -> str:
    return reverse(
        "matters:complete_engagement_feedback",
        kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
    )


def _finish(client, engagement, **fields):
    """Press `Lõpeta kaasamine` the way the row does: with the current token."""
    engagement.refresh_from_db()
    payload = {"revision": engagement_revision_token(engagement)}
    payload.update(fields)
    return client.post(_finish_url(engagement), payload, headers={"HX-Request": "true"})


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _row(client, matter, engagement) -> str:
    """This round's own chronology row, as the page renders it."""
    body = _detail(client, matter)
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    return body[start : body.index("</article>", start)]


def _waits(user, **kwargs):
    """The feedback-wait items this reader has, out of the shared work model."""
    return [
        item
        for item in wi.work_items(user, **kwargs)
        if item.source_type == wi.SOURCE_FEEDBACK_WAIT
    ]


# ===========================================================================
# A — the wait is work
# ===========================================================================


def test_a_feedback_deadline_creates_one_work_item_for_the_matters_owner(specialist):
    """§3, §4. One row, owned by whoever carries the file.

    The reading an `Oluline tähtaeg` and an `Arvamuse tähtaeg` already get: a
    consultation belongs to the file rather than to whoever typed it in, so a
    reassignment moves it without anybody editing anything.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, days=5)

    items = _waits(specialist, responsible=specialist)

    assert len(items) == 1
    item = items[0]
    assert item.object_id == engagement.pk
    assert item.matter_id == matter.pk
    assert item.responsible == specialist
    assert item.when == engagement.feedback_deadline
    assert item.meaning == wi.MEANING_FEEDBACK_WAIT
    assert item.text == "liikmed"
    assert item.is_overdue is False


def test_a_round_with_no_deadline_is_not_work(specialist):
    """§3. What draws the item is the dated point, never the act.

    Every consultation the department has ever recorded predates the column, and
    none of them appears anywhere as work.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="liikmed", actor=specialist)

    assert _waits(specialist) == []


def test_an_archive_row_never_reaches_a_work_surface(specialist):
    """§3, and the objection docs/adr/0078 §3 raised, answered.

    A decade of imported register consultations carry reply-by dates and are
    `ARCHIVE` records. If they reached a work surface, every historical round
    somebody types in would be overdue on the day it is entered — which is
    exactly why the column was made inert in the first place.
    """
    archive = factories.MatterFactory(owner=specialist, record_mode=RecordMode.ARCHIVE)
    add_engagement(
        matter=archive,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        feedback_deadline=timezone.localdate() - dt.timedelta(days=400),
        actor=specialist,
    )

    assert _waits(specialist) == []


def test_a_wait_on_a_closed_matter_is_not_work(specialist):
    """A file nobody is working on has no current work on it."""
    matter = factories.MatterFactory(owner=specialist)
    _waiting(matter)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    assert _waits(specialist) == []


def test_the_wait_turns_due_on_the_day_it_asked_for(specialist):
    """§4. Inclusive of the day itself, and late from the day after.

    «Vastake 22. septembriks» is a thing to look at *on* the 22nd. What is late
    is this office's unread post — nothing here says the people who were asked
    replied late, and nothing counts whether they did.
    """
    matter = factories.MatterFactory(owner=specialist)

    tomorrow = _waiting(matter, days=1)
    assert _waits(specialist)[0].is_overdue is False
    assert tomorrow.feedback_wait_is_due() is False

    tomorrow.feedback_deadline = timezone.localdate()
    tomorrow.save(update_fields=["feedback_deadline"])
    assert tomorrow.feedback_wait_is_due() is True
    assert _waits(specialist)[0].is_overdue is False, "today is due, not late"

    tomorrow.feedback_deadline = timezone.localdate() - dt.timedelta(days=1)
    tomorrow.save(update_fields=["feedback_deadline"])
    assert _waits(specialist)[0].is_overdue is True


def test_the_day_passing_does_not_close_the_wait(specialist):
    """§4. A wait ends when somebody says so, and for no other reason.

    A round that disappeared on its own deadline would leave the page at exactly
    the moment the work — read what came back, write it down — became due.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, days=-30)

    engagement.refresh_from_db()
    assert engagement.has_open_feedback_wait is True
    assert engagement.feedback_closed_at is None
    assert len(_waits(specialist)) == 1


def test_an_ownerless_matter_puts_the_wait_on_nobodys_desk(specialist):
    """§4. It reaches the department's *vastutajata* surfaces instead.

    Never a duplicate on every lawyer's list, and never an invented owner. The
    reading `important_deadlines` already gives an unowned milestone.
    """
    unowned = factories.MatterFactory(owner=None)
    _waiting(unowned)

    mine = _waits(specialist, responsible=specialist)
    everybody = _waits(specialist)

    assert mine == []
    assert len(everybody) == 1
    assert everybody[0].responsible is None
    assert everybody[0].responsible_name == "vastutajata"
    assert unowned.pk in wi.work_population_ids(
        specialist, wi.WORK_NEEDS_ATTENTION, responsible=None
    )


def test_a_restricted_round_contributes_nothing_to_a_reader_who_may_not_see_it(specialist, reader):
    """AUTH-003. Scoped through the engagement's own `visible_to`.

    A `Kaasamine` may carry a stricter override than its Matter, so a
    consultation restricted below a NORMAL file has to disappear from the work
    model entirely for somebody outside it — not appear stripped of its title,
    and not move a band boundary. A reader who may see the *Matter* is the sharp
    case: the file is open to them and the round is not (docs/adr/0038).

    The before/after is the assertion. Without it, a source that returned
    nothing to this reader for some unrelated reason would pass in silence.
    """
    from app.core.enums import Visibility

    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter)

    assert len(_waits(reader)) == 1, "the reader could not see the Matter at all"

    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override"])

    assert _waits(reader) == []
    assert len(_waits(specialist)) == 1, "the participant lost their own wait"


# ===========================================================================
# B — it never replaces the plan, and writes no other record
# ===========================================================================


def test_an_open_next_action_and_a_wait_are_both_shown(signed_in, specialist):
    """§3. Two true facts about one file, and neither is withheld.

    The regression this guards is a page or a work model that treats the wait as
    a next action — overwriting it, suppressing it, or being suppressed by it.
    """
    matter = factories.MatterFactory(owner=specialist)
    action = factories.NextActionFactory(
        matter=matter,
        responsible=specialist,
        text="Kirjuta arvamuse mustand",
        target_date=timezone.localdate() + dt.timedelta(days=3),
    )
    engagement = _waiting(matter, days=5)

    items = wi.work_items(specialist, responsible=specialist)
    body = _detail(signed_in, matter)

    assert {item.source_type for item in items} == {wi.SOURCE_NEXT_ACTION, wi.SOURCE_FEEDBACK_WAIT}
    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert "Kirjuta arvamuse mustand" in body
    assert "Ootame tagasisidet" in body
    assert f"#kaasamine-{engagement.pk}-sisu" in body


def test_the_matter_page_states_the_wait_with_no_open_step_at_all(signed_in, specialist):
    """§3. The case the line was really added for.

    A Matter whose only live work is «ootame vastuseid» used to say «Järgmine
    samm on määramata» and nothing else.
    """
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + dt.timedelta(days=14)
    engagement = _waiting(matter, days=14)

    body = _detail(signed_in, matter)
    zone = body[body.index('id="praegune-tegevus"') :]
    zone = zone[: zone.index("</section>")]

    assert "Ootame tagasisidet" in zone
    assert f"kuni {deadline.day}.{deadline.month}.{deadline.year}" in zone
    assert f"#kaasamine-{engagement.pk}-sisu" in zone


def test_a_closed_wait_leaves_the_matter_page(signed_in, specialist):
    """§6. Completing removes it from the active surface, and only from there."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter)

    complete_engagement_feedback(engagement=engagement, actor=specialist)

    zone = _detail(signed_in, matter)
    zone = zone[zone.index('id="praegune-tegevus"') :]
    zone = zone[: zone.index("</section>")]
    assert "Ootame tagasisidet" not in zone


def test_a_wait_writes_no_action_no_milestone_and_no_response_deadline(specialist):
    """§3. Every non-effect docs/adr/0078 §3 decided, kept.

    A deadline column is exactly the kind of thing that grows a task queue by
    accident, and this is the assertion that says it did not.
    """
    matter = factories.MatterFactory(owner=specialist)

    _waiting(matter)

    matter.refresh_from_db()
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not MatterImportantDate.objects.filter(matter=matter).exists()
    assert matter.response_deadline is None


def test_a_wait_discharges_no_response_obligation_and_joins_no_deadline_group(specialist):
    """§3. It is not an `Arvamuse tähtaeg`, and not a *Tähtaeg* at all.

    `real_deadlines` is what the *Tähtajad* panels and the register's deadline
    groups read, and those name what the Chamber promised outside the building.
    A collection date counted there would make the department's own question
    look like a promise to a ministry.
    """
    matter = factories.MatterFactory(
        owner=specialist, response_deadline=timezone.localdate() + dt.timedelta(days=20)
    )
    _waiting(matter, days=5)

    items = wi.work_items(specialist, responsible=specialist)
    obligation = wi.response_obligation_of(matter, specialist)

    assert obligation.is_outstanding is True, "the wait discharged the official obligation"
    assert wi.SOURCE_FEEDBACK_WAIT not in {item.source_type for item in wi.real_deadlines(items)}
    assert matter.pk not in wi.work_population_ids(
        specialist, wi.WORK_DEADLINE_THIS_WEEK, items=items
    )


def test_a_wait_is_outside_the_search_projection(specialist):
    """§5, §9. The feedback text is not indexed, and neither is the state."""
    from app.search.models import SearchDocument, SearchSourceKind

    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    complete_engagement_feedback(
        engagement=engagement,
        feedback_received="Liikmed toetasid, kaubandus soovis pikemat aega.",
        actor=specialist,
    )

    row = SearchDocument.objects.get(
        source_kind=SearchSourceKind.ENGAGEMENT, source_object_id=engagement.pk
    )
    assert "kaubandus" not in row.body_text.lower()
    assert "kaubandus" not in row.title.lower()


def test_a_matter_page_costs_no_query_per_waiting_round(signed_in, specialist):
    """The waits are one scoped read, and so are the forms built from them.

    `visible_to` resolves the reader's scope by asking the database whether they
    hold a break-glass grant, so a page that read the waits per row would pay for
    that lookup per row — the cost `annotate_last_activity` was caught making
    twelve times on one page (docs/adr/0027 round). The completion forms are
    built per record in Python and buy no queries at all.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    matter = factories.MatterFactory(owner=specialist)
    url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    def cost() -> int:
        with CaptureQueriesContext(connection) as captured:
            signed_in.get(url)
        return len(captured)

    _waiting(matter, days=3, actor=specialist)
    one = cost()
    for day in range(4, 12):
        _waiting(matter, days=day, actor=specialist)

    assert cost() <= one, "the page pays per waiting round"


def test_closing_a_matter_reads_the_matter_row_once_however_many_rounds_wait(specialist):
    """The closure audits against the row it already holds.

    Ending five waits costs five `UPDATE`s and five `ChangeEvent` inserts, which
    is the work itself and is not what this measures. What must not grow is the
    number of times the **Matter** is read: `engagement.matter` is a lazy
    descriptor on a row fetched without `select_related`, so a leaf that reached
    through it would re-fetch the locked Matter once per round
    (`_close_one_feedback_wait`).
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def matter_reads(rounds: int) -> int:
        matter = factories.MatterFactory(owner=specialist)
        for day in range(rounds):
            _waiting(matter, days=day + 1, actor=specialist)
        with CaptureQueriesContext(connection) as captured:
            close_matter(
                matter=matter,
                disposition=Disposition.COMPLETED,
                actor=specialist,
                reason="valmis",
            )
        return sum(
            1
            for query in captured.captured_queries
            if 'FROM "matters_matter"' in query["sql"] and str(matter.pk) in query["sql"]
        )

    assert matter_reads(5) == matter_reads(1)


# ===========================================================================
# C — `Lõpeta kaasamine`
# ===========================================================================


def test_the_row_offers_the_finish_control_only_while_the_wait_is_open(signed_in, specialist):
    """§6. Before and after, on the row the record lives on."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)

    open_row = _row(signed_in, matter, engagement)
    assert "Lõpeta kaasamine" in open_row
    assert _finish_url(engagement) in open_row

    complete_engagement_feedback(engagement=engagement, actor=specialist)

    closed_row = _row(signed_in, matter, engagement)
    assert _finish_url(engagement) not in closed_row
    assert "Tagasiside ootamine lõpetatud" in closed_row


def test_two_waiting_rounds_get_two_forms_with_no_shared_ids(signed_in, specialist):
    """§6. One file may be running several consultations at once.

    Every control in the completion form is built per record, and the file
    picker is the one that had to be forced: `workspace_attachments` puts a
    fixed `id` in the widget's attrs, and an explicit attrs id beats `auto_id`,
    so two rounds would otherwise render two inputs with one id and the second
    `<label for>` would open the first round's picker
    (`_engagement_feedback_form`).

    The revision tokens matter as much: two forms sharing one would let
    `Lõpeta` on the second round post the first one's version.
    """
    matter = factories.MatterFactory(owner=specialist)
    first = _waiting(matter, days=3, actor=specialist)
    second = _waiting(matter, days=9, actor=specialist)

    body = _detail(signed_in, matter)
    ids = re.findall(r'id="([^"]+)"', body)

    assert [name for name in ids if ids.count(name) > 1] == []
    for engagement in (first, second):
        assert f"id_kaasamine_{engagement.pk}_tagasiside" in body
        assert f"id_kaasamine_{engagement.pk}_tagasiside_feedback_received" in body


def test_completing_records_the_feedback_and_closes_the_wait(signed_in, specialist):
    """§6. One save: the words, the decision, and the row leaving active work."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)

    response = _finish(
        signed_in, engagement, feedback_received="Liikmed toetasid, kaubandus soovis pikemat aega."
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_received.startswith("Liikmed toetasid")
    assert engagement.feedback_closed_at is not None
    assert engagement.feedback_closed_by == specialist
    assert engagement.has_open_feedback_wait is False
    assert _waits(specialist) == []


def test_completing_with_an_empty_box_records_that_nothing_came_back(signed_in, specialist):
    """§6. «Keegi ei vastanud» is a result, not a missing field.

    A completion that demanded prose would make the commonest disappointing
    outcome the one thing a lawyer could not file.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)

    response = _finish(signed_in, engagement, feedback_received="")

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_received == ""
    assert engagement.feedback_closed_at is not None
    assert _waits(specialist) == []


def test_the_completed_round_stays_in_the_chronology(signed_in, specialist):
    """§6. It leaves active work and stays history, with its answers under it."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)

    _finish(signed_in, engagement, feedback_received="Kaks vastust, mõlemad toetavad.")

    row = _row(signed_in, matter, engagement)
    assert "Kaasamine: liikmed" in row
    assert "Tagasiside ootamine lõpetatud" in row
    assert "Kaks vastust, mõlemad toetavad." in row


def test_the_completion_is_audited_as_a_decision(specialist):
    """§6. Actor, timestamp, the deadline it was closed against, and why.

    Never *what* was written: feedback runs to paragraphs and lives on the
    record where it can be corrected, and an audit table holding a second copy
    is a worse copy nobody maintains.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, days=3, actor=specialist)

    complete_engagement_feedback(
        engagement=engagement, feedback_received="Liikmed toetasid.", actor=specialist
    )

    event = ChangeEvent.objects.filter(
        object_id=engagement.pk, event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
    ).latest("created_at")
    engagement.refresh_from_db()
    assert event.actor == specialist
    assert event.matter_id == matter.pk
    assert event.payload["reason"] == "completed"
    assert event.payload["feedback_deadline"] == engagement.feedback_deadline.isoformat()
    assert event.payload["closed_at"] == engagement.feedback_closed_at.isoformat()
    assert event.payload["has_feedback_received"] is True
    assert "Liikmed toetasid." not in str(event.payload)


def test_a_round_nobody_is_waiting_on_cannot_be_finished(specialist):
    """§6. There is no wait to end, and inventing one would date the file."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="liikmed", actor=specialist
    )

    with pytest.raises(DomainError) as refusal:
        complete_engagement_feedback(engagement=engagement, actor=specialist)

    assert str(refusal.value) == ENGAGEMENT_FEEDBACK_NOT_AWAITED
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None


def test_a_finished_wait_is_not_finished_twice(signed_in, specialist):
    """§6. A second press writes no second audit row and moves no timestamp.

    «Who ended this round and when» has to keep one answer, and a browser
    holding a page from before somebody else's save is the ordinary way to
    arrive here.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    complete_engagement_feedback(engagement=engagement, actor=specialist)
    engagement.refresh_from_db()
    first = engagement.feedback_closed_at

    response = signed_in.post(
        _finish_url(engagement),
        {"revision": engagement_revision_token(engagement), "feedback_received": "hilinenud"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert ENGAGEMENT_FEEDBACK_ALREADY_CLOSED in response.content.decode()
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at == first
    assert engagement.feedback_received == ""
    assert (
        ChangeEvent.objects.filter(
            object_id=engagement.pk, event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
        ).count()
        == 1
    )


def test_a_stale_completion_writes_nothing_at_all(signed_in, specialist):
    """§6, and the one failure mode a partial write would be.

    The token is compared after the row is locked and before anything is
    decided, so a conflict leaves the feedback text *and* the timestamp
    untouched — not one of the two.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    stale = engagement_revision_token(engagement)
    correct_engagement(engagement=engagement, title="kaubandusvaldkonna töögrupp")

    response = signed_in.post(
        _finish_url(engagement),
        {"revision": stale, "feedback_received": "vananenud vastus"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 409
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert engagement.feedback_received == ""
    assert engagement.title == "kaubandusvaldkonna töögrupp"
    assert len(_waits(specialist)) == 1


def test_the_service_refuses_a_stale_completion_too(specialist):
    """Below the view, because the view is not the only caller."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    stale = engagement_revision_token(engagement)
    correct_engagement(engagement=engagement, title="teine nimi")

    with pytest.raises(EngagementEditConflict):
        complete_engagement_feedback(
            engagement=engagement, actor=specialist, expected_revision=stale
        )

    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None


def test_a_closed_matter_refuses_a_completion(signed_in, specialist):
    """§7. Enforced under the Matter's row lock, not by a rendered button.

    The browser that posts may be holding a page from before the closure, which
    is the ordinary way a crafted-looking POST actually arrives.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    token = engagement_revision_token(engagement)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    response = signed_in.post(
        _finish_url(engagement),
        {"revision": token, "feedback_received": "hiline"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.feedback_received == ""


def test_a_reader_cannot_finish_a_round(client, specialist, reader):
    """docs/adr/0042. The write boundary, on the new route as on every other."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    client.force_login(reader)

    response = client.post(
        _finish_url(engagement),
        {"revision": engagement_revision_token(engagement)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None


def test_a_completion_aimed_at_another_matters_round_is_a_404(signed_in, specialist):
    """The child is scoped through its own `visible_to` *and* through the Matter
    in the URL, so a crafted pair naming somebody else's round is refused."""
    mine = factories.MatterFactory(owner=specialist)
    theirs = factories.MatterFactory(owner=specialist)
    engagement = _waiting(theirs, actor=specialist)

    response = signed_in.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": mine.pk, "engagement_id": engagement.pk},
        ),
        {"revision": engagement_revision_token(engagement)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None


def test_feedback_stays_correctable_after_the_round_is_closed(signed_in, specialist):
    """§6. A lawyer who typed the answers in a hurry can fix them.

    Through `Muuda` and `correct_engagement`, with the Matter's lock, the
    revision token and its own audit row — the existing correction conventions,
    not a special case.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    complete_engagement_feedback(
        engagement=engagement, feedback_received="Liikmedt oetasid", actor=specialist
    )
    engagement.refresh_from_db()
    closed_at = engagement.feedback_closed_at

    response = signed_in.post(
        reverse(
            "matters:update_engagement",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "title": engagement.title,
            "feedback_received": "Liikmed toetasid",
            "feedback_deadline": engagement.feedback_deadline.strftime("%d.%m.%Y"),
            "revision": engagement_revision_token(engagement),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_received == "Liikmed toetasid"
    assert engagement.feedback_closed_at == closed_at, "correcting the words reopened the wait"


def test_clearing_the_deadline_takes_the_closure_with_it(specialist):
    """§6. A wait that no longer exists cannot stay completed.

    The `CHECK` says so and the service normalises to it, so the row cannot end
    up as a closure of nothing — a state `has_open_feedback_wait` would read as
    neither open nor closed.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    complete_engagement_feedback(engagement=engagement, actor=specialist)

    correct_engagement(engagement=engagement, feedback_deadline=None, actor=specialist)

    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None
    assert engagement.feedback_closed_at is None
    assert engagement.feedback_closed_by is None
    assert engagement.has_feedback_wait is False
    assert _waits(specialist) == []


def test_setting_a_deadline_on_an_undated_round_opens_a_new_wait(specialist):
    """§6. The inverse, and it is deliberately not symmetrical.

    A correction cannot *reopen* a wait somebody closed — the deadline it was
    closed against is still there, so the clearing branch is never reached.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="liikmed", actor=specialist
    )

    correct_engagement(
        engagement=engagement,
        feedback_deadline=timezone.localdate() + dt.timedelta(days=5),
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.has_open_feedback_wait is True
    assert len(_waits(specialist)) == 1


# ===========================================================================
# D — the Matter closing underneath a wait
# ===========================================================================


def test_closing_the_matter_ends_every_open_wait_audibly(specialist):
    """§7. In the transaction that shuts the file, each with its own event.

    The rule `end_open_action_for_closure` and
    `cancel_planned_website_overviews_for_closure` already keep, arriving at the
    third thing a closed file could otherwise keep owing: an open wait draws a
    work item while every route that could finish one refuses a closed Matter.
    """
    matter = factories.MatterFactory(owner=specialist)
    first = _waiting(matter, days=3, actor=specialist)
    second = _waiting(matter, days=9, actor=specialist)

    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    for engagement in (first, second):
        engagement.refresh_from_db()
        assert engagement.feedback_closed_at is not None
        assert engagement.feedback_closed_by == specialist
        event = ChangeEvent.objects.filter(
            object_id=engagement.pk, event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
        ).latest("created_at")
        assert event.payload["reason"] == "matter_closed"
    assert _waits(specialist) == []


def test_closing_writes_no_feedback_for_anybody(specialist):
    """§7. A file being shut says nothing about what members answered."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)

    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    engagement.refresh_from_db()
    assert engagement.feedback_received == ""


def test_closing_is_never_blocked_by_a_wait(specialist):
    """§7. There is no precondition here and no refusal."""
    matter = factories.MatterFactory(owner=specialist)
    _waiting(matter, actor=specialist)
    _waiting(matter, days=30, actor=specialist)

    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    matter.refresh_from_db()
    assert matter.is_open is False


def test_reopening_does_not_revive_a_wait(specialist):
    """§7. Exactly as it does not revive a cancelled `NextAction`.

    A reopened file that is genuinely still waiting gets a new deadline from
    somebody who has decided that it is, which is a statement with a name on it.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    reopen_matter(matter=matter, actor=specialist, reason="tuli tagasi")

    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert engagement.has_open_feedback_wait is False
    assert _waits(specialist) == []


def test_an_already_finished_round_is_not_closed_twice_by_the_matter(specialist):
    """§7. The closure only touches what is still open.

    A second `ENGAGEMENT_FEEDBACK_CLOSED` on a round somebody finished in
    August would move the timestamp onto the day the file was shut, and the
    history would then name the wrong person and the wrong reason.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _waiting(matter, actor=specialist)
    complete_engagement_feedback(engagement=engagement, actor=specialist)
    engagement.refresh_from_db()
    first = engagement.feedback_closed_at

    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    engagement.refresh_from_db()
    assert engagement.feedback_closed_at == first
    assert (
        ChangeEvent.objects.filter(
            object_id=engagement.pk, event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
        ).count()
        == 1
    )


# ===========================================================================
# E — the panel writes the feedback it was given
# ===========================================================================


def test_the_panel_stores_feedback_without_closing_anything(signed_in, specialist):
    """§5, §6. Writing down what came back and deciding the round is over are
    two acts, and only the second is a decision somebody's name goes on."""
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + dt.timedelta(days=4)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {
            "audience": "liikmed",
            "occurred_on": "",
            "feedback_deadline": deadline.strftime("%d.%m.%Y"),
            "feedback_received": "Kaks vastust juba käes.",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement = MatterEngagement.objects.get()
    assert engagement.feedback_received == "Kaks vastust juba käes."
    assert engagement.has_open_feedback_wait is True
    assert len(_waits(specialist)) == 1


def test_an_emptied_reply_by_date_opens_no_wait(signed_in, specialist):
    """§2, §3. Clearing the defaulted box is a real answer, not an omission."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "liikmed", "feedback_deadline": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement = MatterEngagement.objects.get()
    assert engagement.feedback_deadline is None
    assert engagement.has_feedback_wait is False
    assert _waits(specialist) == []
