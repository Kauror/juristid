"""Correcting a `Kaasamine` that is already on the chronology.

The product rule this module asserts:

    A consultation already filed may be corrected — every field it stores,
    both of its dates included — by anybody who may author business content,
    **on an open Matter only**.

That last clause is the one difference from `tests/test_entry_correction.py`,
and it is deliberate rather than an oversight. Rewriting the wording of a
Sissekanne touches no canonical fact and is offered on a closed file; correcting
a `Kaasamine` moves the dates, the channel, the audience and the links of a
structured record that the chronology, the register's activity date and the
search projection all read. That is normal interactive business work, so a
finished file refuses it and reopening is the way out — which leaves somebody's
name on both decisions (docs/adr/0075 §12, docs/adr/0076 §2).

Three things this exists to make possible, each with tests below.

**A date the old panel invented can be told the truth.** Until 2026-09-14
`+ Kaasamine` stamped `timezone.localdate()` on every row it wrote. Those rows
are indistinguishable from honest same-day records, so nothing backfills them
— but from here on a person who knows the consultation was in March can say so,
and a person who does not know can empty the box and have the row read
«Kuupäev teadmata» (docs/adr/0078 §2).

**`Tagasisidet ootame kuni` has a way back out.** The column arrived with the
panel that writes it and no form could read it back, so a reply-by date was a
stored fact nobody could change or remove (docs/adr/0078 §3).

**A stale correction cannot overwrite a fresh one.** The row lock serialises two
editors; it does not tell the second that their browser was holding an old copy.
The `revision` token does, and a save carrying a stale one writes nothing.

Both dates are **exact days** and there is no precision control, since
docs/adr/0085 §1 took the four-way `Täpsus` chips off both `Kaasamine`
surfaces — an editor may not offer a precision the creating panel cannot write,
which is the rule that put them on both forms in docs/adr/0082 and the rule that
now takes them off both. A record already dated to a period is preserved rather
than rewritten, and that contract lives in
`tests/test_engagement_date_precision.py`.
"""

from __future__ import annotations

import datetime as dt
import uuid as _uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement
from app.matters.services import (
    ENGAGEMENT_EDIT_CONFLICT,
    EngagementEditConflict,
    add_engagement,
    close_matter,
    correct_engagement,
    engagement_revision_token,
)
from app.matters.timeline import ENGAGEMENT_DATE_UNKNOWN, matter_timeline
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


# -- harness ----------------------------------------------------------------


def _days_ago(days: int) -> dt.date:
    """A day in the past, relative to the clock rather than written down.

    A chronology row only renders once its day has arrived, so a hard-coded
    date is a test that starts passing or failing depending on when it is run.
    Relative dates keep the fixture in the past for good.
    """
    return timezone.localdate() - dt.timedelta(days=days)


#: When the consultation was actually held, and when the old panel filed it.
LONG_AGO = _days_ago(190)
RECORDED = _days_ago(30)


def _url(matter, engagement) -> str:
    return reverse(
        "matters:update_engagement",
        kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
    )


def _open_form(client, matter, engagement):
    """Click `Muuda`: the row's text region comes back as the correction form."""
    return client.get(_url(matter, engagement))


def _revision_in(html: str) -> str:
    """The token the rendered form is holding.

    Read out of the markup rather than computed, because the whole point of the
    field is that the browser carries the server's value back unchanged — a test
    that recomputed it would prove the service works and say nothing about
    whether the form actually ships it.
    """
    marker = 'name="revision"'
    assert marker in html, "the correction form carries no revision token"
    field = html[html.index(marker) : html.index(marker) + 400]
    start = field.index('value="') + len('value="')
    return field[start : field.index('"', start)]


def _fields(engagement, **changes) -> dict[str, str]:
    """Everything the correction form posts, filled from the record.

    A real browser sends every box, so a test that sent three of them would be
    exercising a request the product never makes — and would prove nothing about
    the fields it left out, which is exactly where `feedback_deadline` was lost.
    """
    payload = {
        "kind": engagement.kind,
        "title": engagement.title,
        "url": engagement.url,
        "smaily_url": engagement.smaily_url,
        "alchemer_url": engagement.alchemer_url,
        "note": engagement.note,
        "occurred_on": (
            format_estonian_date(engagement.occurred_on) if engagement.occurred_on else ""
        ),
        "feedback_deadline": (
            format_estonian_date(engagement.feedback_deadline)
            if engagement.feedback_deadline
            else ""
        ),
        "feedback_received": engagement.feedback_received,
        "revision": engagement_revision_token(engagement),
    }
    payload.update(changes)
    return payload


def _save(client, matter, engagement, **changes):
    """Save a correction the way the page does: with the token the row is at."""
    engagement.refresh_from_db()
    return client.post(_url(matter, engagement), _fields(engagement, **changes))


def _close(matter, actor):
    """Close it the way the other tab does — through the domain service."""
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=actor, reason="QA")
    matter.refresh_from_db()
    return matter


def _chronology_row(client, matter, engagement) -> str:
    """The markup of this engagement's own chronology row, as the page renders it.

    Read from the page rather than from the milestone, because the reply-by
    date's three wordings are a *state* rendered as their own line by
    `matters/partials/engagement_row.html` — they are deliberately not part of
    `milestone.sub`, which would state one fact twice on one row
    (docs/adr/0085 §3, §4).
    """
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    return body[start : body.index("</article>", start)]


def _milestone(matter, user, engagement):
    """The chronology row this engagement draws, or ``None``."""
    items, _ = matter_timeline(matter=matter, user=user, limit=50)
    for item in items:
        if item.is_engagement and item.record.pk == engagement.pk:
            return item.milestone
    return None


@pytest.fixture
def engagement(normal_matter, specialist):
    return add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=RECORDED,
        actor=specialist,
    )


# -- A. the affordance and the form ------------------------------------------


def test_the_chronology_offers_muuda_on_a_kaasamine(signed_in, normal_matter, engagement):
    """§C. A real row, with a usable control on it and the right swap target."""
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = page.content.decode()

    assert page.status_code == 200
    assert f"kaasamine-{engagement.pk}-sisu" in html
    assert _url(normal_matter, engagement) in html
    assert ">Muuda<" in html
    # It swaps the record's own region and nothing around it, so the dot, the
    # spine and the attached files cannot move.
    assert f'hx-target="#kaasamine-{engagement.pk}-sisu"' in html


def test_the_form_opens_filled_from_the_record(signed_in, normal_matter, engagement):
    response = _open_form(signed_in, normal_matter, engagement)
    html = response.content.decode()

    assert response.status_code == 200
    assert "Liikmete küsitlus" in html
    assert format_estonian_date(RECORDED) in html
    assert 'name="occurred_on"' in html
    assert 'name="feedback_deadline"' in html
    assert _revision_in(html) == engagement_revision_token(engagement)
    # The ids are keyed on the record, because a reader may open two rows at
    # once and two elements sharing an id make a label reach the wrong box.
    assert f"id_kaasamine_{engagement.pk}_occurred_on" in html


def test_the_form_asks_both_dates_as_days_and_offers_no_precision_control(
    signed_in, normal_matter, engagement
):
    """docs/adr/0085 §1, narrowing docs/adr/0082 §1.

    The four-way `Täpsus` control came off both `Kaasamine` surfaces: it was two
    decisions deep on a panel whose overwhelming case is «this happened today»,
    and the editor may not offer a precision the creating panel cannot write.
    Both dates are exact days here, and neither has a group of chips.

    What a stored period does instead is
    `tests/test_engagement_date_precision.py`: it is preserved, named on this
    form in words, and removable only on purpose.
    """
    html = _open_form(signed_in, normal_matter, engagement).content.decode()

    assert "precision__chips" not in html
    assert 'name="engagement_precision"' not in html
    assert 'name="feedback_deadline_precision"' not in html
    assert 'name="occurred_on"' in html
    assert 'name="feedback_deadline"' in html
    # `Poolaasta` is a stored precision and not an offered chip, here as
    # everywhere else (docs/adr/0079 §7).
    assert "Poolaasta" not in html


def test_tuhista_gives_back_the_stored_row_and_writes_nothing(signed_in, normal_matter, engagement):
    response = signed_in.get(_url(normal_matter, engagement), {"vaade": "lugemine"})
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="occurred_on"' not in html
    assert "Kaasamine: Liikmete küsitlus" in html
    engagement.refresh_from_db()
    assert engagement.occurred_on == RECORDED


# -- B. the dates a person can now fix ---------------------------------------


def test_a_stamped_date_can_be_corrected_to_the_day_it_happened(
    signed_in, normal_matter, engagement
):
    """The whole point. A consultation held in March, filed as September."""
    response = _save(
        signed_in, normal_matter, engagement, occurred_on=format_estonian_date(LONG_AGO)
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.occurred_on == LONG_AGO
    # And the row says so, rather than still reading September.
    assert format_estonian_date(LONG_AGO) in response.content.decode()


def test_clearing_the_date_stores_null_and_the_row_says_so(
    signed_in, normal_matter, engagement, specialist
):
    """«Kuupäev teadmata» is a real answer, and emptying the box is how it is given."""
    response = _save(signed_in, normal_matter, engagement, occurred_on="")

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.occurred_on is None
    assert ENGAGEMENT_DATE_UNKNOWN in response.content.decode()
    assert _milestone(normal_matter, specialist, engagement).display_date == (
        ENGAGEMENT_DATE_UNKNOWN
    )


def test_a_feedback_deadline_round_trips_through_the_form(
    signed_in, normal_matter, engagement, specialist
):
    """The column that could be written and never read back (docs/adr/0078 §3)."""
    deadline = RECORDED + dt.timedelta(days=10)

    response = _save(
        signed_in, normal_matter, engagement, feedback_deadline=format_estonian_date(deadline)
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_deadline == deadline
    # Visibly stated on the row it belongs to — as its own line now rather than
    # inside the metadata sentence, because it is a state with three wordings
    # and a colour of its own (docs/adr/0085 §3, §4).
    #
    # **This one reads as due**, because `RECORDED` is thirty days ago and the
    # deadline ten days after it: the round asked for answers three weeks ago
    # and nobody has finished it. The sentence is about this office's unread
    # post, which is why it is «tähtaeg möödus» and not «te jäite hiljaks».
    row = _chronology_row(signed_in, normal_matter, engagement)
    assert f"Tagasiside tähtaeg möödus {format_estonian_date(deadline)}" in row

    # And the form it is read back into is holding it.
    html = _open_form(signed_in, normal_matter, engagement).content.decode()
    assert format_estonian_date(deadline) in html


def test_clearing_a_feedback_deadline_stores_null(signed_in, normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Küsitlus",
        occurred_on=RECORDED,
        feedback_deadline=RECORDED + dt.timedelta(days=4),
        actor=specialist,
    )

    response = _save(signed_in, normal_matter, engagement, feedback_deadline="")

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None
    assert "Tagasisidet ootame kuni" not in response.content.decode()


def test_a_partial_correction_does_not_clear_the_feedback_deadline(normal_matter, specialist):
    """`_UNSET` at the service boundary. A caller that does not name a field
    leaves it exactly as it was — the importer and the register refresh depend
    on it, and so does every future caller nobody has written yet."""
    deadline = RECORDED + dt.timedelta(days=7)
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Enne",
        occurred_on=RECORDED,
        feedback_deadline=deadline,
        actor=specialist,
    )

    correct_engagement(engagement=engagement, title="Pärast", actor=specialist)

    engagement.refresh_from_db()
    assert engagement.title == "Pärast"
    assert engagement.feedback_deadline == deadline
    assert engagement.occurred_on == RECORDED


def test_a_deadline_before_the_engagement_is_refused_on_its_own_field(
    signed_in, normal_matter, engagement
):
    """The one rule relating the two dates, shared with the `+ Kaasamine` panel.

    A record must not be correctable into a state it could never have been
    created in (`refuse_deadline_before_engagement`).
    """
    before = RECORDED - dt.timedelta(days=1)

    response = _save(
        signed_in, normal_matter, engagement, feedback_deadline=format_estonian_date(before)
    )
    html = response.content.decode()

    assert response.status_code == 400
    assert "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva." in html
    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None
    # What was typed is still in the boxes.
    assert format_estonian_date(before) in html


def test_a_refusal_writes_nothing_and_keeps_the_form_open(signed_in, normal_matter, engagement):
    response = _save(signed_in, normal_matter, engagement, title="   ")
    html = response.content.decode()

    assert response.status_code == 400
    assert 'name="occurred_on"' in html
    engagement.refresh_from_db()
    assert engagement.title == "Liikmete küsitlus"


def test_a_correction_changes_no_field_the_form_does_not_offer(
    signed_in, normal_matter, specialist
):
    """`response_count` is not on this form, so it is not this form's to clear."""
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Küsitlus",
        occurred_on=RECORDED,
        response_count=14,
        actor=specialist,
    )

    _save(signed_in, normal_matter, engagement, title="Küsitlus liikmetele")

    engagement.refresh_from_db()
    assert engagement.response_count == 14


# -- C. the chronology row ----------------------------------------------------


def test_an_undated_engagement_never_prints_its_created_at_as_a_fact(
    signed_in, normal_matter, specialist
):
    """The fallback places the row. It does not describe it (docs/adr/0078 §2)."""
    engagement = add_engagement(
        matter=normal_matter, kind=EngagementKind.SURVEY, title="Vana voor", actor=specialist
    )
    recorded_today = format_estonian_date(timezone.localdate())

    milestone = _milestone(normal_matter, specialist, engagement)
    assert milestone.display_date == ENGAGEMENT_DATE_UNKNOWN
    assert milestone.display_date != recorded_today

    # And the row is still there to be read and corrected, rather than dropped.
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    body = page.content.decode()
    chronology = body[body.index('id="ajalugu-loend"') :]
    assert "Kaasamine: Vana voor" in chronology
    assert ENGAGEMENT_DATE_UNKNOWN in chronology


def test_a_correction_adds_no_row_to_the_chronology(
    signed_in, normal_matter, engagement, specialist
):
    before, _ = matter_timeline(matter=normal_matter, user=specialist, limit=50)

    _save(signed_in, normal_matter, engagement, title="Liikmete ja töögrupi küsitlus")

    after, _ = matter_timeline(matter=normal_matter, user=specialist, limit=50)
    assert len(after) == len(before)


# -- D. authorization, visibility and crafted posts ---------------------------


def test_a_reader_sees_the_row_but_no_muuda(client, reader, normal_matter, engagement):
    client.force_login(reader)
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = page.content.decode()

    assert page.status_code == 200
    assert "Kaasamine: Liikmete küsitlus" in html
    assert _url(normal_matter, engagement) not in html


def test_somebody_without_business_write_is_refused_at_the_route(
    client, reader, normal_matter, engagement
):
    """404 from the decorator, on both verbs — the same answer the route gives
    for a Matter that does not exist, so a refusal describes no surface."""
    client.force_login(reader)

    assert client.get(_url(normal_matter, engagement)).status_code == 404
    assert client.post(_url(normal_matter, engagement), _fields(engagement)).status_code == 404
    engagement.refresh_from_db()
    assert engagement.title == "Liikmete küsitlus"


def test_any_business_writer_may_correct_a_colleagues_kaasamine(
    client, normal_matter, engagement, other_specialist
):
    """Not owner-only and not creator-only (docs/adr/0042)."""
    client.force_login(other_specialist)

    response = client.post(
        _url(normal_matter, engagement), _fields(engagement, title="Kolleeg parandas")
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.title == "Kolleeg parandas"


def test_a_restricted_child_is_read_through_its_own_scope(
    client, reader, normal_matter, specialist
):
    """AUTH-003. The child is read through its own `visible_to`, not off the Matter.

    A `Kaasamine` may carry a stricter visibility than the Matter it hangs off,
    so the route's lookup is the child's own scoped queryset — the expression
    asserted here — and never `matter.engagements`.

    The *route* cannot show the two answers apart, and that is a property of
    the role sets rather than of this view: `ROLES_WITH_RESTRICTED_ACCESS` and
    `ROLES_WITH_BUSINESS_WRITE` hold the same two roles, so anybody the
    decorator lets through can also read restricted content. What the route can
    be held to is that a restricted record and a record that does not exist
    give a reader the *same* answer, so a refusal describes no surface.
    """
    from app.core.enums import Visibility

    hidden = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Piiratud voor",
        occurred_on=RECORDED,
        actor=specialist,
    )
    hidden.visibility_override = Visibility.RESTRICTED
    hidden.save(update_fields=["visibility_override"])

    scoped = MatterEngagement.objects.visible_to(reader)
    assert not scoped.filter(pk=hidden.pk).exists()
    assert MatterEngagement.objects.visible_to(specialist).filter(pk=hidden.pk).exists()

    client.force_login(reader)
    missing = reverse(
        "matters:update_engagement",
        kwargs={"pk": normal_matter.pk, "engagement_id": _uuid.uuid4()},
    )

    assert client.get(_url(normal_matter, hidden)).status_code == 404
    assert client.get(missing).status_code == 404
    assert client.post(_url(normal_matter, hidden), _fields(hidden)).status_code == 404
    hidden.refresh_from_db()
    assert hidden.title == "Piiratud voor"


def test_a_restricted_matters_engagement_is_unreachable_from_outside_it(
    client, reader, restricted_matter, specialist
):
    hidden = add_engagement(
        matter=restricted_matter,
        kind=EngagementKind.SURVEY,
        title="Salajane voor",
        occurred_on=RECORDED,
        actor=specialist,
    )

    client.force_login(reader)

    assert client.get(_url(restricted_matter, hidden)).status_code == 404
    assert client.post(_url(restricted_matter, hidden), _fields(hidden)).status_code == 404
    hidden.refresh_from_db()
    assert hidden.title == "Salajane voor"


def test_an_engagement_on_another_matter_is_not_reachable_through_this_one(
    signed_in, normal_matter, specialist
):
    other = factories.MatterFactory(owner=specialist)
    elsewhere = add_engagement(
        matter=other, kind=EngagementKind.SURVEY, title="Teise teema voor", actor=specialist
    )

    crafted = reverse(
        "matters:update_engagement",
        kwargs={"pk": normal_matter.pk, "engagement_id": elsewhere.pk},
    )

    assert signed_in.get(crafted).status_code == 404
    assert signed_in.post(crafted, _fields(elsewhere)).status_code == 404
    elsewhere.refresh_from_db()
    assert elsewhere.title == "Teise teema voor"


def test_an_anonymous_request_is_sent_to_sign_in_rather_than_refused(
    client, normal_matter, engagement
):
    response = client.get(_url(normal_matter, engagement))

    assert response.status_code == 302
    # Sent to sign in, carrying where they were going — not refused, which
    # would tell somebody who is not signed in that the record exists.
    assert response.url.startswith("/konto/")
    assert _url(normal_matter, engagement) in response.url


# -- E. closed Matters --------------------------------------------------------


def test_a_closed_matter_refuses_the_correction_and_writes_nothing(
    signed_in, normal_matter, engagement, specialist
):
    """The rule this feature deliberately keeps (docs/adr/0075 §12)."""
    _close(normal_matter, specialist)

    response = _save(signed_in, normal_matter, engagement, title="Ei tohi")
    html = response.content.decode()

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.title == "Liikmete küsitlus"
    # The refusal is a sentence in the form that is still open, holding what
    # was typed — not a swap that looks like somebody else's successful save.
    assert 'name="occurred_on"' in html
    assert "Ei tohi" in html


def test_a_closed_matter_shows_no_muuda_control(signed_in, normal_matter, engagement, specialist):
    _close(normal_matter, specialist)

    html = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert "Kaasamine: Liikmete küsitlus" in html
    assert _url(normal_matter, engagement) not in html


def test_the_guard_is_the_row_lock_and_not_the_rendered_page(normal_matter, engagement, specialist):
    """A stale tab posts to a server with no memory of which page it came from,
    so the refusal has to live under the Matter's own lock."""
    from app.core.errors import DomainError

    _close(normal_matter, specialist)

    with pytest.raises(DomainError):
        correct_engagement(engagement=engagement, title="Otse teenuse kaudu", actor=specialist)

    engagement.refresh_from_db()
    assert engagement.title == "Liikmete küsitlus"


def test_reopening_is_the_way_out(signed_in, normal_matter, engagement, specialist):
    from app.matters.services import reopen_matter

    _close(normal_matter, specialist)
    reopen_matter(matter=normal_matter, actor=specialist)
    normal_matter.refresh_from_db()

    response = _save(
        signed_in, normal_matter, engagement, occurred_on=format_estonian_date(LONG_AGO)
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.occurred_on == LONG_AGO


# -- F. optimistic concurrency ------------------------------------------------


def test_a_stale_form_does_not_overwrite_the_newer_record(
    signed_in, normal_matter, engagement, specialist
):
    stale = engagement_revision_token(engagement)
    correct_engagement(engagement=engagement, title="Kolleegi parandus", actor=specialist)

    response = signed_in.post(
        _url(normal_matter, engagement),
        _fields(engagement, title="Minu parandus", revision=stale),
    )
    html = response.content.decode()

    assert response.status_code == 409
    engagement.refresh_from_db()
    assert engagement.title == "Kolleegi parandus"
    assert ENGAGEMENT_EDIT_CONFLICT in html
    # Both versions are on the screen and neither is chosen for them.
    assert "Minu parandus" in html
    assert "Kolleegi parandus" in html
    # The token is not advanced, so the next submit cannot silently win.
    assert _revision_in(html) == stale


def test_a_stale_form_carrying_the_same_values_is_still_a_conflict(
    normal_matter, engagement, specialist
):
    """Being overtaken is the fact, not disagreeing about the words."""
    stale = engagement_revision_token(engagement)
    correct_engagement(engagement=engagement, title="Uus pealkiri", actor=specialist)

    with pytest.raises(EngagementEditConflict):
        correct_engagement(
            engagement=engagement,
            title="Uus pealkiri",
            actor=specialist,
            expected_revision=stale,
        )


def test_a_post_carrying_no_token_at_all_is_refused(signed_in, normal_matter, engagement):
    response = signed_in.post(_url(normal_matter, engagement), _fields(engagement, revision=""))

    assert response.status_code == 409
    engagement.refresh_from_db()
    assert engagement.title == "Liikmete küsitlus"


def test_the_service_leaves_a_caller_with_no_opinion_alone(normal_matter, engagement, specialist):
    """`None` means «no opinion», and is for the importer, a data fix, a test."""
    correct_engagement(engagement=engagement, title="Ilma arvamuseta", actor=specialist)

    engagement.refresh_from_db()
    assert engagement.title == "Ilma arvamuseta"


def test_a_fresh_form_saves_after_reading_the_conflict(
    signed_in, normal_matter, engagement, specialist
):
    stale = engagement_revision_token(engagement)
    correct_engagement(engagement=engagement, title="Kolleegi parandus", actor=specialist)
    signed_in.post(_url(normal_matter, engagement), _fields(engagement, revision=stale))

    response = _save(signed_in, normal_matter, engagement, title="Lepitatud")

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.title == "Lepitatud"


# -- G. audit -----------------------------------------------------------------


def test_a_correction_files_one_engagement_changed_event_naming_the_dates(
    signed_in, normal_matter, engagement, specialist
):
    """The existing convention, unchanged: the fields that moved, and values
    only for the small ones (`update_engagement`)."""
    before = ChangeEvent.objects.filter(matter=normal_matter).count()

    _save(signed_in, normal_matter, engagement, occurred_on=format_estonian_date(LONG_AGO))

    events = ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENGAGEMENT_CHANGED
    )
    assert events.count() == 1
    assert ChangeEvent.objects.filter(matter=normal_matter).count() == before + 1
    payload = events.get().payload
    assert payload["fields"] == ["occurred_on"]
    assert payload["occurred_on_from"] == RECORDED.isoformat()
    assert payload["occurred_on_to"] == LONG_AGO.isoformat()


def test_a_correction_that_changes_nothing_writes_no_event(
    signed_in, normal_matter, engagement, specialist
):
    _save(signed_in, normal_matter, engagement)

    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENGAGEMENT_CHANGED
    ).exists()


def test_the_correction_writes_no_entry_and_no_second_engagement(
    signed_in, normal_matter, engagement
):
    _save(signed_in, normal_matter, engagement, title="Parandatud")

    assert MatterEngagement.objects.filter(matter=normal_matter).count() == 1
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENTRY_ADDED
    ).exists()


# -- H. what a correction must leave alone ------------------------------------
#
# Both dates are inert by decision, and `Tagasisidet ootame kuni` emphatically
# so: «no work item, no overdue badge, no count, no statistic, no filter, no
# register sort» (docs/adr/0078 §3). Correcting one is the loudest possible
# place for that to break, because a past deadline written onto an old
# consultation is exactly the shape an overdue queue would pick up.


def test_correcting_the_dates_writes_no_other_record(signed_in, normal_matter, specialist):
    """Before and after, over the records a deadline could have become.

    A deadline **in the past** on purpose. Since docs/adr/0085 §3 the reply-by
    date *is* read as work — one derived `WorkItem`, which is why the work
    surface is no longer part of the comparison below — but it still **writes**
    nothing: no `NextAction`, no `MatterImportantDate`, no
    `Matter.response_deadline`, and no closure. That is the half of
    docs/adr/0078 §3 this release keeps, and the half that would be expensive to
    lose quietly.
    """
    from app.intelligence.models import MatterImportantDate
    from app.workflow.models import NextAction

    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=RECORDED,
        actor=specialist,
    )
    before_deadline = normal_matter.response_deadline
    before_actions = list(
        NextAction.objects.filter(matter=normal_matter).values_list("pk", flat=True)
    )

    response = _save(
        signed_in,
        normal_matter,
        engagement,
        occurred_on=format_estonian_date(LONG_AGO),
        feedback_deadline=format_estonian_date(LONG_AGO + dt.timedelta(days=5)),
    )

    assert response.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_deadline == LONG_AGO + dt.timedelta(days=5)

    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline == before_deadline
    assert normal_matter.is_open is True
    assert normal_matter.closed_at is None
    assert (
        list(NextAction.objects.filter(matter=normal_matter).values_list("pk", flat=True))
        == before_actions
    )
    assert not MatterImportantDate.objects.filter(matter=normal_matter).exists()


def test_correcting_only_the_dates_leaves_the_search_projection_alone(
    normal_matter, engagement, specialist
):
    """The projection follows the record's *text*, and dates are not indexed.

    One row before and one row after, saying the same thing — so a date
    correction neither adds a search row nor rewrites one. (A *title*
    correction does update the row, which is the projection working; that is
    asserted in tests/test_search_freshness.py.)
    """
    from app.search.models import SearchDocument, SearchSourceKind

    def projected():
        return sorted(
            (row.title, row.body_text, row.alias_text)
            for row in SearchDocument.objects.filter(
                matter=normal_matter, source_kind=SearchSourceKind.ENGAGEMENT
            )
        )

    before = projected()
    assert len(before) == 1

    correct_engagement(
        engagement=engagement,
        occurred_on=LONG_AGO,
        feedback_deadline=LONG_AGO + dt.timedelta(days=3),
        actor=specialist,
    )

    assert projected() == before


def test_the_archive_projection_is_not_touched(normal_matter, engagement, specialist):
    """A correction changes no record mode and no closure, so nothing moves
    between the current register and the archive."""
    before = (normal_matter.record_mode, normal_matter.is_open, normal_matter.disposition)

    correct_engagement(engagement=engagement, occurred_on=LONG_AGO, actor=specialist)

    normal_matter.refresh_from_db()
    assert (normal_matter.record_mode, normal_matter.is_open, normal_matter.disposition) == before
