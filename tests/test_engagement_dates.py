"""`Kaasamise kuupäev`, and the reply-by date that is no longer on this panel.

Until 2026-09-14 the panel asked for neither. `add_engagement_compact` passed
`timezone.localdate()` for `occurred_on` on every save, so a consultation held
in March and written up in September was stored as a September consultation —
a false fact, produced by the server, with no box on the screen a person could
have corrected. The first half of this file is about that stamp being gone and
staying gone.

The second half is `feedback_deadline`, which records what was asked of the
people who were contacted — «ootan vastuseid kuni 22.09». **`+ Kaasamine` no
longer asks for it**: docs/adr/0091 §2 took the question off the capture panel
and made opening a wait one explicit act on the round's own chronology row,
`Ootan tagasisidet`. The column, its storage, its rendering and the correction
form's box are untouched, so the tests here follow the column rather than the
panel, and the act itself is held by `tests/test_engagement_feedback_wait.py`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.enums import EngagementKind
from app.matters.forms import EngagementForm
from app.matters.models import MatterEngagement
from app.matters.services import add_engagement, engagement_revision_token, update_engagement
from app.matters.timeline import matter_timeline
from tests import factories

pytestmark = pytest.mark.django_db


def _post(client, matter, **fields):
    """One `+ Kaasamine` save, through the route a person actually uses."""
    payload = {"kind": EngagementKind.SURVEY, "audience": "liikmed"}
    payload.update(fields)
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _panel(body: str) -> str:
    """The `+ Kaasamine` panel's own markup, from its id to the next panel's."""
    start = body.index('id="lisa-kaasamine"')
    # To the family after it. `marge-tahtaeg` used to be the next chip in the
    # document and is now a sub-choice *inside* `+ Märge`, which stands before
    # `+ Kaasamine` — so slicing to it reaches backwards and finds nothing
    # (docs/adr/0097 §8).
    return body[start : body.index('id="lisa-arvamus"', start)]


def _workspace(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


# ---------------------------------------------------------------------------
# A — the date is on the screen, and so is the default
# ---------------------------------------------------------------------------


def test_the_panel_asks_for_the_engagement_date_and_shows_todays_default(signed_in, specialist):
    """**§7.A.** The box exists, it is labelled `Kaasamise kuupäev`, and the
    default it applies is *visible* rather than applied on the server.

    That is the whole difference from what this replaces. Today was always the
    value being stored; what nobody could see was that it was being stored.
    """
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_workspace(signed_in, matter))

    assert "Kaasamise kuupäev" in panel
    assert 'name="occurred_on"' in panel
    assert f'value="{format_estonian_date(timezone.localdate())}"' in panel


def test_the_panel_does_not_ask_how_long_feedback_is_awaited(signed_in, specialist):
    """`Tagasisidet ootame kuni` is **not on this panel** — no box, no spans.

    **docs/adr/0091 §2 narrows docs/adr/0086 §2 past its default.** That record
    pre-filled the box with today + 7, on an argument that was right about its own
    subject: a week is what a round asks for when nobody says otherwise, and it is
    not a value anybody presses `Salvesta` past without reading.

    What it did not weigh is that pressing past *this* box is not like pressing
    past the one above it. An accepted `Kaasamise kuupäev` is a fact that is
    probably right; an accepted `Tagasisidet ootame kuni` is a managed activity
    with a work item, a responsible person, an overdue state and a second
    deliberate act to end it. Recording «19.09 — kaasati 234 tööstusettevõtet» is a
    completed act, and every one of them was acquiring a wait. The lawyers called
    that too complicated, and they were describing work the application had
    assigned them (lawyer feedback 11).

    Emptying the default was the first answer and it was not enough: an empty box
    is still a box that has to be read, understood and skipped on every round a
    lawyer files, and it was still this panel that opened a managed wait.

    **The wait itself is not withdrawn.** It is `Ootan tagasisidet` on the round's
    own chronology row now, with the three spans travelling with it, and every
    rule docs/adr/0086 §3 wrote still applies — which is what
    `tests/test_engagement_feedback_wait.py` holds.
    """
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_workspace(signed_in, matter))

    assert "Tagasisidet ootame kuni" not in panel
    assert 'name="feedback_deadline"' not in panel
    # The spans went with the question, so the panel holds no quick-date control
    # of its own either — a chip with nothing to write into would be a control
    # that silently does nothing.
    assert "data-quickdate" not in panel


def test_the_correction_form_keeps_the_reply_by_box(signed_in, specialist):
    """docs/adr/0091 §2. `Muuda` is where a stored deadline is read and repaired.

    What lost the field is the one surface whose job is capture. Taking it off
    the correction form as well would make every #227 deadline uneditable, which
    is the opposite of the historical-compatibility promise this round keeps.
    """
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + dt.timedelta(days=9)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=timezone.localdate(),
        feedback_deadline=deadline,
        actor=specialist,
    )

    body = signed_in.get(
        reverse(
            "matters:update_engagement", kwargs={"pk": matter.pk, "engagement_id": engagement.pk}
        )
    ).content.decode()

    assert "Tagasisidet ootame kuni" in body
    assert f'value="{format_estonian_date(deadline)}"' in body


# ---------------------------------------------------------------------------
# B, C, D — what is stored is what was typed
# ---------------------------------------------------------------------------


def test_a_historical_engagement_date_survives_the_save(signed_in, specialist):
    """**§7.B.** Thirty days ago, stored as thirty days ago.

    The regression this stands on: the view used to discard whatever the form
    cleaned and write `timezone.localdate()`.
    """
    matter = factories.MatterFactory(owner=specialist)
    when = timezone.localdate() - dt.timedelta(days=30)

    response = _post(signed_in, matter, occurred_on=format_estonian_date(when))

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on == when
    assert engagement.occurred_on != timezone.localdate()


def test_clearing_the_date_stores_no_date_at_all(signed_in, specialist):
    """**§7.C.** An emptied box is an answer, and the answer is «kuupäev teadmata».

    `MatterEngagement.occurred_on` has always been nullable and the chronology
    has always been able to say so. What was missing was any way for a person to
    *mean* it.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, occurred_on="")

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on is None, "the server put today back"


def test_no_path_from_the_panel_stamps_today_behind_the_persons_back(signed_in, specialist):
    """The same claim as above, made about the code rather than about one save.

    A cleared date and a backdated one are both proved; this is what catches a
    re-introduction that only fires when some *other* field is also present.
    """
    matter = factories.MatterFactory(owner=specialist)
    when = dt.date(2019, 3, 4)

    _post(
        signed_in,
        matter,
        occurred_on=format_estonian_date(when),
        response_count="12",
        smaily_url="https://sendsmaily.net/c/1",
    )

    assert MatterEngagement.objects.get().occurred_on == when


def test_a_refused_save_hands_the_typed_date_back_rather_than_todays(signed_in, specialist):
    """**§7.D.** The audience is missing, so the panel comes back — carrying the
    date that was typed into it, not the default it opened with."""
    matter = factories.MatterFactory(owner=specialist)
    when = timezone.localdate() - dt.timedelta(days=90)
    typed = format_estonian_date(when)

    response = _post(signed_in, matter, audience="", occurred_on=typed)
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    # Its own panel came back chosen, with the refusal inside it.
    radio = body[body.index('id="lisa-kaasamine-valik"') :]
    assert "checked" in radio[: radio.index(">")]
    assert f'value="{typed}"' in body
    assert f'value="{format_estonian_date(timezone.localdate())}"' not in _panel(body)


def test_a_refused_save_hands_both_cleared_boxes_back_empty(signed_in, specialist):
    """The half of the refusal contract a bound form is easiest to lose.

    Both dates open pre-filled, so a panel that came back **unbound** after a
    refusal would look like it had worked: the boxes would be holding today and
    today + 7 again, and the person who deliberately emptied them would press
    `Salvesta` a second time and file the two dates they had just removed.

    Django's own `is_bound` is what prevents it, and this is the assertion that
    says so — for the cleared case specifically, because the typed case above
    passes even on a form that re-applies its initial to an *absent* field
    (docs/adr/0086 §2).
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter, audience="", occurred_on="")
    panel = _panel(response.content.decode())

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    field = panel[panel.index('name="occurred_on"') :]
    field = field[: field.index(">")]
    assert 'value=""' in field or "value=" not in field, f"occurred_on came back filled: {field}"


def test_the_panel_stores_no_deadline_at_all(signed_in, specialist):
    """docs/adr/0091 §2. The panel does not ask, so it cannot answer.

    Asserted on the *column* rather than on the rendered box, because that is the
    claim that matters: an ordinary new round is a completed act on the file and
    not an open activity on somebody's desk. Nothing in the view or the service
    supplies a day the form did not send, and the form no longer has one to send.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter)

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get()
    assert engagement.feedback_deadline is None
    assert engagement.has_feedback_wait is False


# ---------------------------------------------------------------------------
# D2 — the *other* write path, which kept the stamp after the panel lost it
# ---------------------------------------------------------------------------
#
# `+ Kaasamine` was fixed on 2026-09-14. `ComposerForm` was not, and it is a
# live route: `teemad/<pk>/sissekanne/` is the superseded composer, kept on
# purpose for the browsers still holding a page that posts to it
# (docs/adr/0075 §11). Its template is included by nothing, so the only callers
# left are a stale tab and a crafted POST — and both were still having today
# written onto their consultation by the server.
#
# It has no date box and cannot grow one, because no surface renders it. So it
# records that the date is not known, which is the only truthful answer a
# surface that cannot ask is entitled to give.


def _compose(client, matter, **fields):
    """One save through the superseded composer, with engagement data on it."""
    payload = {
        "body": "Küsisime liikmetelt arvamust.",
        "engagement_kind": EngagementKind.SURVEY,
        "engagement_audience": "liikmed",
    }
    payload.update(fields)
    return client.post(reverse("matters:compose", kwargs={"pk": matter.pk}), payload)


def test_the_superseded_composer_route_stores_no_date_rather_than_today(signed_in, specialist):
    """**The route**, not the form helper — this is what a stale tab reaches."""
    matter = factories.MatterFactory(owner=specialist)

    response = _compose(signed_in, matter)

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on is None
    assert engagement.occurred_on != timezone.localdate()
    assert engagement.feedback_deadline is None


def test_the_superseded_composer_form_proposes_no_date_either(specialist):
    """The same claim about the form, so a re-introduction fails at both layers."""
    from app.matters.forms import ComposerForm

    matter = factories.MatterFactory(owner=specialist)
    form = ComposerForm(
        data={
            "body": "Küsisime liikmetelt arvamust.",
            "engagement_kind": EngagementKind.SURVEY,
            "engagement_audience": "liikmed",
        },
        matter=matter,
        viewer=specialist,
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["engagement_kwargs"]["occurred_on"] is None


def test_no_live_write_path_puts_today_on_an_engagement_behind_the_persons_back(
    signed_in, specialist
):
    """Both doors at once, so neither can regress while the other is watched.

    The panel is asked for a date and stores exactly what it was given; the
    composer is not asked and stores nothing. Neither invents today.
    """
    panel_matter = factories.MatterFactory(owner=specialist)
    composer_matter = factories.MatterFactory(owner=specialist)
    when = timezone.localdate() - dt.timedelta(days=120)

    _post(signed_in, panel_matter, occurred_on=format_estonian_date(when))
    _compose(signed_in, composer_matter)

    assert MatterEngagement.objects.get(matter=panel_matter).occurred_on == when
    assert MatterEngagement.objects.get(matter=composer_matter).occurred_on is None


def test_the_composer_still_writes_everything_else_it_always_did(signed_in, specialist):
    """The date is the only thing this changes. The rest of the save is untouched."""
    from app.matters.models import Entry

    matter = factories.MatterFactory(owner=specialist)

    _compose(signed_in, matter, engagement_responses="14")

    engagement = MatterEngagement.objects.get()
    assert engagement.title == "liikmed"
    assert engagement.kind == EngagementKind.SURVEY
    assert engagement.response_count == 14
    assert Entry.objects.filter(matter=matter).count() == 1


# ---------------------------------------------------------------------------
# E, F, G — the feedback deadline
# ---------------------------------------------------------------------------


def test_the_feedback_deadline_is_optional_and_blank_stores_null(signed_in, specialist):
    """**§7.E.** Most consultations never named one."""
    matter = factories.MatterFactory(owner=specialist)

    response = _post(signed_in, matter)

    assert response.status_code == 200
    assert MatterEngagement.objects.get().feedback_deadline is None


def test_the_feedback_deadline_is_stored_and_read_back_exactly(signed_in, specialist):
    """**§7.F.** Stored, re-read, and shown where the engagement is read.

    Written through `Ootan tagasisidet` since docs/adr/0091 §2, which is the one
    surface that opens a wait. The column, its storage and its rendering are
    untouched — only the door changed.
    """
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + dt.timedelta(days=8)

    assert _post(signed_in, matter).status_code == 200
    engagement = MatterEngagement.objects.get()
    response = signed_in.post(
        reverse(
            "matters:open_engagement_wait",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "revision": engagement_revision_token(engagement),
            "feedback_deadline": format_estonian_date(deadline),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_deadline == deadline
    assert MatterEngagement.objects.get(pk=engagement.pk).feedback_deadline == deadline

    # Shown on the round's own chronology row, as the state it is: its own line
    # with three wordings and a colour, rather than a third fragment of the
    # metadata sentence (docs/adr/0086 §3, §4).
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    row = body[start : body.index("</article>", start)]
    assert f"Ootame tagasisidet kuni {format_estonian_date(deadline)}" in row


def test_the_chronology_says_nothing_about_a_deadline_that_was_never_set(specialist):
    """**§5.** No «Määramata», no «—», no «Tähtaeg puudub».

    An absence the reader has to decode is worse than silence, and every row
    written before today has this absence.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmed",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    items, _ = matter_timeline(matter=matter, user=specialist)
    subs = " ".join(item.milestone.sub or "" for item in items if item.is_milestone)

    assert "Tagasisidet" not in subs
    assert "Määramata" not in subs
    assert "Tähtaeg puudub" not in subs


def _round(client, matter, **fields):
    """One round through the panel, returned as the record it wrote."""
    assert _post(client, matter, **fields).status_code == 200
    return MatterEngagement.objects.get()


def _wait(client, engagement, deadline):
    """`Ootan tagasisidet` — the one surface that opens a wait on a new round."""
    return client.post(
        reverse(
            "matters:open_engagement_wait",
            kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
        ),
        {
            "revision": engagement_revision_token(engagement),
            "feedback_deadline": format_estonian_date(deadline),
        },
        headers={"HX-Request": "true"},
    )


@pytest.mark.parametrize("offset", [0, 1, 400])
def test_a_deadline_on_or_after_the_engagement_date_is_accepted(signed_in, specialist, offset):
    """**§7.G.** Same day is «vastake tänaseks», later is the normal case.

    Asked through `Ootan tagasisidet` since docs/adr/0091 §2 — the rule is
    unchanged, and so is the surface it now has to be true of.
    """
    matter = factories.MatterFactory(owner=specialist)
    when = timezone.localdate() - dt.timedelta(days=10)
    engagement = _round(signed_in, matter, occurred_on=format_estonian_date(when))

    response = _wait(signed_in, engagement, when + dt.timedelta(days=offset))

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_deadline == when + dt.timedelta(days=offset)


def test_a_deadline_before_the_engagement_is_refused(signed_in, specialist):
    """**§7.G.** Nothing was ever asked to be answered before it was asked.

    The service keeps the rule now as well as the forms, because the act has no
    form to report on — one string, so the two cannot drift
    (`DEADLINE_BEFORE_ENGAGEMENT`, docs/adr/0091 §2).
    """
    matter = factories.MatterFactory(owner=specialist)
    when = timezone.localdate()
    engagement = _round(signed_in, matter, occurred_on=format_estonian_date(when))

    response = _wait(signed_in, engagement, when - dt.timedelta(days=1))

    assert response.status_code == 400
    assert "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva." in response.content.decode()
    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None


def test_the_correction_form_names_the_refusal_on_the_deadline():
    """The same rule read off the form that still asks both dates, so the field
    it lands on is pinned: the engagement date is the anchor and the deadline is
    what is being placed against it."""
    form = EngagementForm(
        {
            "kind": EngagementKind.SURVEY,
            "title": "liikmed",
            "occurred_on": "10.09.2026",
            "feedback_deadline": "01.09.2026",
        }
    )

    assert not form.is_valid()
    assert "feedback_deadline" in form.errors
    assert "occurred_on" not in form.errors


def test_a_deadline_with_no_engagement_date_is_accepted(signed_in, specialist):
    """**§7.G.** Somebody may remember what they asked for and not when."""
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + dt.timedelta(days=6)
    engagement = _round(signed_in, matter, occurred_on="")

    response = _wait(signed_in, engagement, deadline)

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.occurred_on is None
    assert engagement.feedback_deadline == deadline


def test_a_deadline_already_in_the_past_is_not_refused(specialist):
    """A consultation recorded months late had its deadline months ago.

    Refusing it would make the historical record unwritable to protect a rule
    nothing enforces — and this column enforces nothing by design. Asserted
    through `add_engagement`, which is what the importer and the shell reach and
    is the path a backdated round actually arrives on.
    """
    matter = factories.MatterFactory(owner=specialist)

    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2021, 1, 4),
        feedback_deadline=dt.date(2021, 1, 18),
        actor=specialist,
    )

    assert engagement.feedback_deadline == dt.date(2021, 1, 18)


# ---------------------------------------------------------------------------
# What it deliberately is not
# ---------------------------------------------------------------------------


def test_a_feedback_deadline_creates_no_work_and_moves_no_chronology_date(signed_in, specialist):
    """**§5.** Not a `NextAction`, not `Matter.response_deadline`, not a
    `MatterImportantDate`, and not the engagement's activity date.

    A past deadline is used on purpose: if any of these were wired up, an
    overdue item would be the loudest possible symptom.
    """
    from app.intelligence.models import MatterImportantDate
    from app.workflow.models import NextAction

    matter = factories.MatterFactory(owner=specialist)
    when = timezone.localdate() - dt.timedelta(days=60)

    _post(
        signed_in,
        matter,
        occurred_on=format_estonian_date(when),
        feedback_deadline=format_estonian_date(when + dt.timedelta(days=5)),
    )

    matter.refresh_from_db()
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not MatterImportantDate.objects.filter(matter=matter).exists()
    assert matter.response_deadline is None

    items, _ = matter_timeline(matter=matter, user=specialist)
    rows = [
        item for item in items if item.is_milestone and "Kaasamine" in (item.milestone.what or "")
    ]
    assert len(rows) == 1
    assert rows[0].milestone.display_date == format_estonian_date(when)


# ---------------------------------------------------------------------------
# H — rows written before the column existed
# ---------------------------------------------------------------------------


def test_an_engagement_with_no_feedback_deadline_reads_exactly_as_it_did(signed_in, specialist):
    """**§7.H.** Every row in production is this row."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Liikmed",
        occurred_on=dt.date(2020, 2, 3),
        response_count=7,
        actor=specialist,
    )

    assert engagement.feedback_deadline is None

    body = _workspace(signed_in, matter)
    assert "Kaasamine: Liikmed" in body
    assert "Vastuseid 7" in body
    # The row says nothing about a deadline it never had — no «Määramata», no
    # «—», no «Tähtaeg puudub». And the panel does not ask either, since
    # docs/adr/0091 §2; what the row *does* offer is the act that would open one.
    assert "Tagasisidet ootame kuni" not in _panel(body)
    assert "Ootame tagasisidet kuni" not in body
    assert "Ootan tagasisidet" in body, "the round offers no way to start waiting"


# ---------------------------------------------------------------------------
# §6 — the correction path must not clear what it never mentions
# ---------------------------------------------------------------------------


def test_an_unrelated_correction_leaves_the_feedback_deadline_alone(specialist):
    """`_UNSET`, proved rather than trusted.

    Every caller that existed before this column names the fields it is
    correcting and no others. A default of `None` instead of the sentinel would
    make a title fix silently erase a reply-by date.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmed",
        occurred_on=dt.date(2026, 9, 1),
        feedback_deadline=dt.date(2026, 9, 22),
        actor=specialist,
    )

    update_engagement(engagement=engagement, title="Liikmed ja töögrupp", actor=specialist)

    engagement.refresh_from_db()
    assert engagement.title == "Liikmed ja töögrupp"
    assert engagement.feedback_deadline == dt.date(2026, 9, 22)


def test_a_correction_that_names_the_deadline_can_clear_it(specialist):
    """An explicit `None` still removes a wrong one — the sentinel protects
    silence, not the value."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmed",
        feedback_deadline=dt.date(2026, 9, 22),
        actor=specialist,
    )

    update_engagement(engagement=engagement, feedback_deadline=None, actor=specialist)

    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None


def test_the_service_invents_no_deadline_for_a_caller_that_names_none(specialist):
    """The importer, the register enrichment, the opinion mapping — none of them
    knows about this column, and none of them may acquire a value for it."""
    matter = factories.MatterFactory(owner=specialist)

    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.WEB_CALL,
        title="Avalik kaasamiskutse",
        occurred_on=dt.date(2018, 6, 1),
        actor=specialist,
    )

    assert engagement.feedback_deadline is None
