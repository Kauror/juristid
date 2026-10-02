"""`Tagasisidet ootame kuni` is optional, and it does not define the round.

docs/adr/0132, brief §4A, §9A, §9B and §51A. A `Kaasamine` is a consultation
round: OPEN from the moment it is recorded, COMPLETED when somebody presses
`Lõpeta kaasamine` (or the Matter closes under it). The feedback deadline is an
optional due date on that work. It is never required, never defaulted, never
what opens, keeps open or ends a round, and when it is absent the round is
never overdue and reads «Tähtaeg määramata» rather than today.

The numbered tests follow the brief's §51A list one for one; the rest pin what
the change must not disturb — the round filed as history, the migration, the
Matter closure.

Dates are relative to the clock wherever a work surface is read, because a work
surface bands on today; the date-order rule is checked against fixed days,
because it reads no clock.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.documents.models import Document
from app.matters import my_work
from app.matters import work_items as wi
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.services import (
    DEADLINE_BEFORE_ENGAGEMENT,
    ENGAGEMENT_FEEDBACK_NOT_AWAITED,
    add_engagement,
    close_matter,
    complete_engagement_feedback,
    correct_engagement,
    engagement_revision_token,
    open_engagement_feedback_wait,
    record_engagement,
)
from app.matters.workspace import add_matter_external_position
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: The brief's own example day. Used only where no clock is read.
ENGAGED = dt.date(2026, 10, 2)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _round(matter, actor, **extra):
    """A round recorded by a person, the way every interactive door records one."""
    fields = {"kind": EngagementKind.OTHER, "title": "liikmed", "actor": actor}
    fields.update(extra)
    return record_engagement(matter=matter, **fields)


def _panel(client, matter, **fields):
    """`+ Kaasamine`, posted the way the browser posts it."""
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "liikmed", **fields},
        headers={"HX-Request": "true"},
    )


def _finish(client, engagement, **fields):
    """`Lõpeta kaasamine`, with the token the row was rendered from."""
    engagement.refresh_from_db()
    return client.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
        ),
        {"revision": engagement_revision_token(engagement), **fields},
        headers={"HX-Request": "true"},
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _row(client, matter, engagement) -> str:
    body = _detail(client, matter)
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    return body[start : body.index("</article>", start)]


def _dated_waits(user, **kwargs):
    return [
        item
        for item in wi.work_items(user, **kwargs)
        if item.source_type == wi.SOURCE_FEEDBACK_WAIT
    ]


def _undated_waits(user, subject):
    rows, _total = my_work.undated_items(user, subject)
    return [item for item in rows if item.is_feedback_wait]


def _d(day: dt.date) -> str:
    return day.strftime("%d.%m.%Y")


# ===========================================================================
# §51A.1 — a blank deadline saves an OPEN round with no due date
# ===========================================================================


def test_1_a_new_round_with_a_blank_deadline_is_open_work_with_no_due_date(signed_in, specialist):
    """Brief §4A example A, §9A. Saves; OPEN; on a work surface; no date; not late."""
    matter = factories.MatterFactory(owner=specialist)

    response = _panel(signed_in, matter, occurred_on=_d(timezone.localdate()))

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement = MatterEngagement.objects.get()
    # Saved, open, and with no date anybody did not choose.
    assert engagement.feedback_deadline is None
    assert engagement.lifecycle_tracked is True
    assert engagement.feedback_closed_at is None
    assert engagement.has_open_feedback_wait is True
    assert engagement.feedback_wait_is_due() is False

    # The structured open work state: one undated item, on the owner's desk.
    [item] = _undated_waits(specialist, specialist)
    assert item.object_id == engagement.pk
    assert item.responsible == specialist
    assert item.meaning == wi.MEANING_FEEDBACK_WAIT
    assert item.when is None
    assert item.display_date == ""
    assert item.date_display == "Tähtaeg määramata"
    assert item.is_overdue is False
    # Never dressed as dated work, and never as today.
    assert _dated_waits(specialist) == []
    assert matter.pk in wi.work_population_ids(specialist, wi.WORK_UNDATED)
    assert matter.pk not in wi.work_population_ids(specialist, wi.WORK_OVERDUE)


def test_1_a_round_with_no_deadline_is_not_needs_attention_for_lacking_a_date(specialist):
    """Brief §9A: never «Vajab sekkumist» merely because it has no date.

    The Matter carries a dated plan, so nothing else could put it on the list.
    """
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(
        matter=matter,
        text="Koostan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + dt.timedelta(days=10),
        actor=specialist,
    )
    _round(matter, specialist)

    assert matter.pk not in wi.work_population_ids(specialist, wi.WORK_NEEDS_ATTENTION)


def test_1_the_teema_page_says_tahtaeg_maaramata_and_offers_lopeta(signed_in, specialist):
    """Brief §9A: visible on the Matter, with a truthful compact state, and
    completable. No overdue colour, and no date of today in the line."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)

    body = _detail(signed_in, matter)
    row = _row(signed_in, matter, engagement)

    assert "Tähtaeg määramata" in body
    assert "curact__oweddate--overdue" not in body
    assert "Ootame tagasisidet · tähtaeg määramata" in row
    assert "Lõpeta kaasamine" in row
    assert "Ootan tagasisidet" not in row
    assert format_estonian_date(timezone.localdate()) not in row


def test_1_no_default_is_offered_on_the_panel(signed_in, specialist):
    """Brief §4A: no default deadline, no «today + N». The box is on the panel,
    optional and empty."""
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)
    panel = body[body.index('id="lisa-kaasamine"') :]
    panel = panel[: panel.index("</form>")]
    box = re.search(r'<input[^>]*name="feedback_deadline"[^>]*>', panel)

    assert box is not None
    assert 'value="' not in box.group(0) or 'value=""' in box.group(0)
    assert "required" not in box.group(0)


# ===========================================================================
# §51A.2–5 — the one chronological rule, and nothing else
# ===========================================================================


def test_2_a_deadline_on_the_engagement_day_is_accepted(normal_matter, specialist):
    """Brief §4A example C. Same-day consultation is allowed."""
    engagement = _round(normal_matter, specialist, occurred_on=ENGAGED, feedback_deadline=ENGAGED)

    assert engagement.feedback_deadline == ENGAGED
    assert engagement.has_open_feedback_wait is True


def test_3_a_deadline_the_next_day_is_accepted(normal_matter, specialist):
    """Brief §4A example B. No minimum consultation length."""
    tomorrow = ENGAGED + dt.timedelta(days=1)

    engagement = _round(normal_matter, specialist, occurred_on=ENGAGED, feedback_deadline=tomorrow)

    assert engagement.feedback_deadline == tomorrow
    assert engagement.has_open_feedback_wait is True


def test_3_a_deadline_weeks_later_is_accepted(normal_matter, specialist):
    later = ENGAGED + dt.timedelta(weeks=6)

    engagement = _round(normal_matter, specialist, occurred_on=ENGAGED, feedback_deadline=later)

    assert engagement.feedback_deadline == later


def test_4_a_deadline_the_day_before_is_refused_by_the_existing_rule(normal_matter, specialist):
    with pytest.raises(DomainError, match=DEADLINE_BEFORE_ENGAGEMENT):
        _round(
            normal_matter,
            specialist,
            occurred_on=ENGAGED,
            feedback_deadline=ENGAGED - dt.timedelta(days=1),
        )

    assert not MatterEngagement.objects.exists()


def test_4_the_panel_refuses_it_under_the_field_and_saves_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = _panel(
        signed_in,
        matter,
        occurred_on=_d(ENGAGED),
        feedback_deadline=_d(ENGAGED - dt.timedelta(days=1)),
    )

    assert response.status_code == 400
    assert DEADLINE_BEFORE_ENGAGEMENT in response.content.decode()
    assert not MatterEngagement.objects.exists()


def test_5_a_deadline_with_no_engagement_date_is_accepted(signed_in, specialist):
    """Brief §4A: an unknown start date is no reason to refuse a deadline."""
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + dt.timedelta(days=3)

    response = _panel(signed_in, matter, occurred_on="", feedback_deadline=_d(deadline))

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on is None
    assert engagement.feedback_deadline == deadline
    assert engagement.has_open_feedback_wait is True


# ===========================================================================
# §51A.6–8 — a blank deadline takes everything a round takes
# ===========================================================================


def test_6_provider_links_and_counts_work_with_no_deadline(signed_in, specialist):
    """Smaily and Alchemer pointers and the manual `Vastuseid` count."""
    matter = factories.MatterFactory(owner=specialist)

    response = _panel(
        signed_in,
        matter,
        smaily_url="https://sendsmaily.net/kampaania/1",
        alchemer_url="https://survey.alchemer.eu/s3/1",
        response_count="12",
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement = MatterEngagement.objects.get()
    assert engagement.feedback_deadline is None
    assert engagement.smaily_url == "https://sendsmaily.net/kampaania/1"
    assert engagement.alchemer_url == "https://survey.alchemer.eu/s3/1"
    assert engagement.response_count == 12
    assert engagement.has_open_feedback_wait is True

    row = _row(signed_in, matter, engagement)
    assert "Smaily" in row
    assert "Alchemer" in row
    assert "Vastuseid 12" in row

    # And the count stays correctable on the open round.
    correct_engagement(
        engagement=engagement,
        title=engagement.title,
        response_count=15,
        actor=specialist,
        expected_revision=engagement_revision_token(engagement),
    )
    engagement.refresh_from_db()
    assert engagement.response_count == 15
    assert engagement.has_open_feedback_wait is True


def test_7_a_direct_email_round_with_files_only_works_with_no_deadline(signed_in, specialist):
    """No link, no deadline: the answers arrive as files and are attached to the
    round as it is finished."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)

    response = _finish(
        signed_in,
        engagement,
        attachments=SimpleUploadedFile(
            "vastus.pdf", b"%PDF-1.4 vastus", content_type="application/pdf"
        ),
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_wait_is_closed is True
    assert list(
        Document.objects.filter(links__engagement=engagement).values_list("title", flat=True)
    ) == ["vastus.pdf"]


def test_8_received_feedback_links_to_a_round_with_no_deadline(specialist):
    """`Meile saadetud tagasiside` names the round it answers, deadline or not,
    and the round's own summary is kept beside it."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist, feedback_received="Liikmed toetasid.")

    position = add_matter_external_position(
        matter=matter,
        author=specialist,
        organisation=factories.OrganisationFactory(name="Näidisliit"),
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Toetame eelnõu.",
        engagement=engagement,
    ).record

    assert MatterExternalPosition.objects.get(pk=position.pk).engagement_id == engagement.pk
    engagement.refresh_from_db()
    assert engagement.feedback_received == "Liikmed toetasid."
    # Linking what came back is not finishing the round.
    assert engagement.has_open_feedback_wait is True


# ===========================================================================
# §51A.9 — Lõpeta kaasamine completes a round with no deadline
# ===========================================================================


def test_9_lopeta_kaasamine_completes_a_round_with_no_deadline(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)

    response = _finish(signed_in, engagement, feedback_received="Keegi ei vastanud.")

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert engagement.feedback_closed_by == specialist
    assert engagement.feedback_received == "Keegi ei vastanud."
    assert engagement.feedback_wait_is_closed is True
    event = ChangeEvent.objects.get(
        object_id=engagement.pk, event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
    )
    assert event.payload["reason"] == "completed"
    assert event.payload["feedback_deadline"] is None
    # Off every work surface, and in the chronology as completed history.
    assert _undated_waits(specialist, specialist) == []
    assert "Tagasiside ootamine lõpetatud" in _row(signed_in, matter, engagement)


# ===========================================================================
# §51A.10 — adding, moving or clearing the deadline changes no lifecycle
# ===========================================================================


def _correct(engagement, actor, **fields):
    engagement.refresh_from_db()
    return correct_engagement(
        engagement=engagement,
        title=engagement.title,
        actor=actor,
        expected_revision=engagement_revision_token(engagement),
        **fields,
    )


def test_10_adding_moving_and_clearing_a_deadline_keeps_one_open_round(specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist, occurred_on=timezone.localdate())
    soon = timezone.localdate() + dt.timedelta(days=5)
    later = timezone.localdate() + dt.timedelta(days=20)

    # Add: the same round gains a due date and moves to the dated surfaces.
    _correct(engagement, specialist, feedback_deadline=soon)
    engagement.refresh_from_db()
    assert engagement.has_open_feedback_wait is True
    assert [item.when for item in _dated_waits(specialist)] == [soon]
    assert _undated_waits(specialist, specialist) == []

    # Move: the same item, a different day.
    _correct(engagement, specialist, feedback_deadline=later)
    assert [item.when for item in _dated_waits(specialist)] == [later]

    # Clear: still open, back to «Tähtaeg määramata» — not completed.
    _correct(engagement, specialist, feedback_deadline=None)
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert engagement.has_open_feedback_wait is True
    assert _dated_waits(specialist) == []
    assert [item.object_id for item in _undated_waits(specialist, specialist)] == [engagement.pk]

    # One consultation throughout, and no completion was ever written.
    assert MatterEngagement.objects.filter(matter=matter).count() == 1
    assert not ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
    ).exists()


def test_10_clearing_the_deadline_of_a_completed_round_leaves_it_completed(specialist):
    """Supersedes docs/adr/0086 §6's «clearing the deadline clears the closure»:
    removing a date neither completes nor reopens anything."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(
        matter,
        specialist,
        occurred_on=timezone.localdate(),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=3),
    )
    complete_engagement_feedback(engagement=engagement, actor=specialist)
    closed_at = MatterEngagement.objects.get(pk=engagement.pk).feedback_closed_at

    _correct(engagement, specialist, feedback_deadline=None)

    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None
    assert engagement.feedback_closed_at == closed_at
    assert engagement.feedback_wait_is_closed is True
    assert _undated_waits(specialist, specialist) == []


def test_10_a_deadline_set_on_a_completed_round_does_not_reopen_it(specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)
    complete_engagement_feedback(engagement=engagement, actor=specialist)

    _correct(engagement, specialist, feedback_deadline=timezone.localdate() + dt.timedelta(days=3))

    engagement.refresh_from_db()
    assert engagement.feedback_wait_is_closed is True
    assert _dated_waits(specialist) == []


# ===========================================================================
# §51A.11–12 — time never completes a round
# ===========================================================================


def test_11_passing_the_deadline_does_not_auto_complete(specialist):
    """Lateness is presentation: the item is overdue, the round is still open."""
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    engagement = _round(
        matter,
        specialist,
        occurred_on=today - dt.timedelta(days=30),
        feedback_deadline=today - dt.timedelta(days=10),
    )

    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert engagement.has_open_feedback_wait is True
    assert engagement.feedback_wait_is_due() is True
    [item] = _dated_waits(specialist)
    assert item.is_overdue is True
    assert matter.pk in wi.work_population_ids(specialist, wi.WORK_OVERDUE)


def test_12_a_round_with_no_deadline_stays_open_indefinitely(specialist):
    """A round recorded two years ago with no date is still open — and still
    never due, however far the clock moves — until somebody finishes it."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(
        matter, specialist, occurred_on=timezone.localdate() - dt.timedelta(days=730)
    )
    far_future = timezone.localdate() + dt.timedelta(days=3650)

    assert engagement.has_open_feedback_wait is True
    assert engagement.feedback_wait_is_due(far_future) is False
    [item] = _undated_waits(specialist, specialist)
    assert item.is_overdue is False
    assert wi.feedback_wait_item(engagement, far_future).is_overdue is False

    complete_engagement_feedback(engagement=engagement, actor=specialist)
    engagement.refresh_from_db()
    assert engagement.has_open_feedback_wait is False


# ===========================================================================
# What the change must not disturb
# ===========================================================================


def test_closing_the_matter_ends_an_open_round_with_no_deadline(specialist):
    """docs/adr/0086 §7, for every open round — a terminal `Hetkeseis` closes
    through the same `close_matter` (docs/adr/0131 §10)."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)

    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    engagement.refresh_from_db()
    assert engagement.feedback_wait_is_closed is True
    assert (
        ChangeEvent.objects.get(
            object_id=engagement.pk, event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED
        ).payload["reason"]
        == "matter_closed"
    )


def test_a_round_filed_as_history_is_neither_open_nor_completed(specialist):
    """The importer's door. No historical consultation becomes somebody's work,
    and none is completed by a decision nobody made."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="liikmed", lifecycle_tracked=False
    )

    assert engagement.has_feedback_wait is False
    assert engagement.has_open_feedback_wait is False
    assert engagement.feedback_wait_is_closed is False
    assert _undated_waits(specialist, specialist) == []
    with pytest.raises(DomainError, match=ENGAGEMENT_FEEDBACK_NOT_AWAITED):
        complete_engagement_feedback(engagement=engagement, actor=specialist)


def test_ootan_tagasisidet_opens_a_historical_round_with_no_date(signed_in, specialist):
    """`Ootan tagasisidet` no longer requires a day (brief §4A: nothing requires
    the deadline before a round can become OPEN)."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="liikmed", lifecycle_tracked=False
    )
    assert "Ootan tagasisidet" in _row(signed_in, matter, engagement)

    response = signed_in.post(
        reverse(
            "matters:open_engagement_wait",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {"revision": engagement_revision_token(engagement), "feedback_deadline": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.lifecycle_tracked is True
    assert engagement.feedback_deadline is None
    assert engagement.has_open_feedback_wait is True
    assert [item.object_id for item in _undated_waits(specialist, specialist)] == [engagement.pk]


def test_ootan_tagasisidet_does_not_restart_an_open_round(specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)

    with pytest.raises(DomainError):
        open_engagement_feedback_wait(engagement=engagement, deadline=None, actor=specialist)


def test_a_deadline_on_a_historical_round_starts_its_lifecycle(specialist):
    """A reply-by date is a wait somebody decided on; the database refuses a
    deadline on a row with no lifecycle, so the service starts it."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.WEB_CALL, title="liikmed", lifecycle_tracked=False
    )

    _correct(engagement, specialist, feedback_deadline=timezone.localdate() + dt.timedelta(days=4))

    engagement.refresh_from_db()
    assert engagement.lifecycle_tracked is True
    assert engagement.has_open_feedback_wait is True


# ===========================================================================
# matters/0044 — every existing row keeps the reading it had
# ===========================================================================

BEFORE = ("matters", "0043_seed_current_stage_episodes")
AFTER = ("matters", "0044_engagement_lifecycle_tracked")


def _migrate_to(target: tuple[str, str]) -> None:
    """Move the `matters` app to one migration — `test_multiple_senders_migration`'s
    harness, for its reason: pending deferred FK triggers block `ALTER TABLE`."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


@pytest.fixture
def restore_schema():
    yield
    _migrate_to(AFTER)


def test_the_migration_keeps_every_existing_rows_reading(specialist, restore_schema):
    """A deadline row was an open or completed wait and stays one; a row with
    neither deadline nor closure was in no state and stays history; an insert
    from the release still serving, which names no column, opens a round."""
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()
    waiting = _round(matter, specialist, occurred_on=today, feedback_deadline=today)
    finished = _round(matter, specialist, occurred_on=today, feedback_deadline=today)
    complete_engagement_feedback(engagement=finished, actor=specialist)
    history = _round(matter, specialist, occurred_on=today)

    _migrate_to(BEFORE)
    _migrate_to(AFTER)

    readings = dict(MatterEngagement.objects.values_list("pk", "lifecycle_tracked"))
    assert readings == {waiting.pk: True, finished.pk: True, history.pk: False}

    # The database default is what an old-release insert gets.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'matters_matterengagement' AND column_name = 'lifecycle_tracked'"
        )
        assert cursor.fetchone()[0] == "true"
