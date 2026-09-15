"""Two usability fixes: a reply-by date on the strip, and a fillable overview panel.

Both are docs/adr/0083, and both narrow a decision the Teema page already
carried rather than replacing it.

**`Tagasiside tähtaeg` on the process strip.** ADR 0078 §3 put the engagement's
reply-by date on its own chronology row, which is below the fold, so a lawyer
opening a file could not see that members owe answers by the 22nd without
scrolling. It now draws a strip column — as a *dated point the file is heading
for* (ADR 0074 §12.4), never as the engagement itself, which ADR 0074 §12.1
retired and this does not bring back.

The tests that matter most here are the non-effects. A date drawn on a strip is
a reading of a record, and the whole risk of drawing it is that somewhere
downstream it grows an obligation: a work item, a badge, a lateness reading, a
row in a deadline list. None of that may happen, and §B holds it.

**The `+ Kodulehe ülevaade` panel.** ADR 0081 §1 made it one button and no
fields. That is right for planning and silent about recording a page that is
already up, and what a reader met first was a fieldless form with a vague
`Salvesta` that reads as a broken text box. §C holds the three answers the form
now gives, and that the koda.ee boundary is unchanged.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import EngagementKind, WebsiteOverviewStatus
from app.matters.models import MatterWebsiteOverview
from app.matters.process_timeline import (
    DEADLINE_LABEL,
    FEEDBACK_DEADLINE_LABEL,
    process_steps,
)
from app.matters.services import add_engagement
from tests import factories

pytestmark = pytest.mark.django_db

KODA_URL = "https://koda.ee/uudised/pakendiseaduse-ulevaade"
PUBLISHED_ON = dt.date(2026, 3, 14)


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _strip(body: str) -> str:
    """The process strip's own markup, and nothing else on the page."""
    start = body.index('class="tl-strip"')
    return body[start : body.index("</div>", start)]


def _round(matter, *, deadline, title="liikmed", occurred=None):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=occurred or dt.date(2026, 2, 1),
        feedback_deadline=deadline,
        actor=matter.owner,
    )


def _labels(matter, user):
    return [step.label for step in process_steps(matter=matter, user=user)]


# ===========================================================================
# A — the column exists, says the right thing, and is told apart
# ===========================================================================


def test_a_reply_by_date_draws_its_own_strip_column(normal_matter, specialist):
    """ADR 0083 §1. The point of the whole fix: visible without scrolling."""
    _round(normal_matter, deadline=dt.date(2026, 3, 22))

    steps = process_steps(matter=normal_matter, user=specialist)
    feedback = [s for s in steps if s.label == FEEDBACK_DEADLINE_LABEL]

    assert len(feedback) == 1
    assert feedback[0].display == "22.3.2026"
    assert feedback[0].detail == "liikmed"


def test_a_round_with_no_reply_by_date_draws_nothing(normal_matter, specialist):
    """ADR 0074 §12.1 stands: the engagement is not a milestone.

    What draws a column is the dated point. A consultation without one is a
    chronology row and nothing else, however many of them a file has run.
    """
    _round(normal_matter, deadline=None)
    _round(normal_matter, deadline=None, title="töögrupp")

    assert FEEDBACK_DEADLINE_LABEL not in _labels(normal_matter, specialist)


def test_several_rounds_draw_several_columns_told_apart_by_who_was_asked(normal_matter, specialist):
    """Several per Matter is ordinary, and «Keda kaasati» is what separates them."""
    _round(normal_matter, deadline=dt.date(2026, 3, 22), title="liikmed")
    _round(normal_matter, deadline=dt.date(2026, 5, 4), title="kaubandusvaldkonna töögrupp")

    feedback = [
        s
        for s in process_steps(matter=normal_matter, user=specialist)
        if s.label == FEEDBACK_DEADLINE_LABEL
    ]

    assert [s.display for s in feedback] == ["22.3.2026", "4.5.2026"]
    assert [s.detail for s in feedback] == ["liikmed", "kaubandusvaldkonna töögrupp"]


def test_two_rounds_due_on_one_day_are_ordered_deterministically(normal_matter, specialist):
    """A same-day collision must not be placed by whatever order the database returned."""
    same = dt.date(2026, 3, 22)
    _round(normal_matter, deadline=same, title="liikmed")
    _round(normal_matter, deadline=same, title="töögrupp")

    first = [
        s.detail
        for s in process_steps(matter=normal_matter, user=specialist)
        if s.label == FEEDBACK_DEADLINE_LABEL
    ]
    second = [
        s.detail
        for s in process_steps(matter=normal_matter, user=specialist)
        if s.label == FEEDBACK_DEADLINE_LABEL
    ]

    assert first == second == ["liikmed", "töögrupp"]


def test_the_two_deadlines_are_separate_columns_with_separate_words(specialist):
    """ADR 0083 §1. What this office owes, and what it asked of other people.

    They can fall on one day and are still two facts; a strip that merged them
    would turn an internal collection date into an official obligation.
    """
    same = dt.date(2026, 3, 22)
    matter = factories.MatterFactory(owner=specialist, response_deadline=same)
    _round(matter, deadline=same)

    labels = _labels(matter, specialist)

    assert FEEDBACK_DEADLINE_LABEL in labels
    assert DEADLINE_LABEL in labels
    assert FEEDBACK_DEADLINE_LABEL != DEADLINE_LABEL
    # Members answer Koda before Koda answers the ministry, so a same-day tie
    # puts the collection date first.
    assert labels.index(FEEDBACK_DEADLINE_LABEL) < labels.index(DEADLINE_LABEL)


def test_the_column_reaches_the_rendered_page(signed_in, normal_matter):
    """The strip a lawyer actually sees, not only the read model behind it."""
    _round(normal_matter, deadline=dt.date(2026, 3, 22))

    strip = _strip(_detail(signed_in, normal_matter))

    assert FEEDBACK_DEADLINE_LABEL in strip
    assert "22.3.2026" in strip
    assert 'title="liikmed"' in strip


def test_a_matter_with_no_rounds_draws_the_strip_it_always_did(signed_in, specialist):
    """The empty case: nothing added, nothing removed."""
    matter = factories.MatterFactory(owner=specialist, response_deadline=dt.date(2026, 3, 22))

    labels = _labels(matter, specialist)

    assert FEEDBACK_DEADLINE_LABEL not in labels
    assert DEADLINE_LABEL in labels


def test_a_restricted_round_cannot_change_the_strip_for_a_reader_who_may_not_see_it(
    normal_matter, specialist, reader
):
    """AUTH-003. Scoped before the strip is derived, never filtered afterwards.

    A column count, a connector count or a spacing that moved would announce the
    restricted round's existence to somebody who cannot open it.

    `reader` and not a second specialist: since docs/adr/0042 a colleague in the
    department is not an outsider and sees restricted work by role, so a test
    written against `other_specialist` would pass for the wrong reason — it
    would be asserting that somebody who *may* see the round does see it.
    """
    from app.core.enums import Visibility

    engagement = _round(normal_matter, deadline=dt.date(2026, 3, 22))
    engagement.visibility_override = Visibility.RESTRICTED
    engagement.save(update_fields=["visibility_override", "updated_at"])

    assert FEEDBACK_DEADLINE_LABEL in _labels(normal_matter, specialist)
    assert FEEDBACK_DEADLINE_LABEL not in _labels(normal_matter, reader)


# ===========================================================================
# B — the non-effects, which are the whole risk of drawing a deadline
# ===========================================================================


def test_a_reply_by_date_is_not_the_official_deadline(normal_matter, specialist):
    """ADR 0078 §3. Drawing it on a strip does not promote it."""
    _round(normal_matter, deadline=dt.date(2026, 3, 22))

    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline is None
    assert DEADLINE_LABEL not in _labels(normal_matter, specialist)


def test_a_passed_reply_by_date_is_never_late_and_makes_no_work(normal_matter, specialist):
    """The decision ADR 0083 says is most worth stating.

    A consultation recorded months after the fact has a reply-by date months in
    the past. It must not make the file overdue on the day it is typed in.
    """
    from app.matters import work_items
    from app.workflow.models import NextAction

    _round(normal_matter, deadline=dt.date(2020, 1, 1), occurred=dt.date(2019, 12, 1))

    assert not NextAction.objects.filter(matter=normal_matter).exists()
    assert not normal_matter.important_dates.exists()
    assert all(item.matter.pk != normal_matter.pk for item in work_items.work_items(specialist))

    # And the strip itself asserts no urgency: no countdown, no «üle», no
    # «praegu» — the words are the label and the date.
    step = next(
        s
        for s in process_steps(matter=normal_matter, user=specialist)
        if s.label == FEEDBACK_DEADLINE_LABEL
    )
    assert step.date_line == "1.1.2020"
    assert "p üle" not in step.date_line
    assert "praegu" not in step.date_line


def test_a_reply_by_date_reaches_no_work_or_archive_surface(signed_in, specialist):
    """The deadline is isolated from the engagement, which *is* indexed.

    A `Kaasamine` has been a search source since docs/adr/0027 — that is how a
    colleague finds the round by the host its mailing ran on — so counting
    documents before and after adding one would be measuring the engagement, not
    the deadline. The control is a second Matter carrying an identical round
    with **no** reply-by date: whatever the deadline costs must be the
    difference between the two, and it is nothing.
    """
    from app.matters import work_items
    from app.search.indexing import rebuild_all
    from app.search.models import SearchDocument

    with_deadline = factories.MatterFactory(owner=specialist, title="Teema tähtajaga")
    without = factories.MatterFactory(owner=specialist, title="Teema tähtajata")
    _round(with_deadline, deadline=dt.date(2026, 3, 22))
    _round(without, deadline=None)
    rebuild_all()

    assert SearchDocument.objects.filter(matter=with_deadline).count() == (
        SearchDocument.objects.filter(matter=without).count()
    )
    # And the day itself is nowhere in the projection, in either spelling.
    for spelling in ("22.03.2026", "22.3.2026", "2026-03-22"):
        assert not SearchDocument.objects.filter(body_text__icontains=spelling).exists(), spelling
        assert not SearchDocument.objects.filter(title__icontains=spelling).exists(), spelling

    # No work item on either, and the watched-deadline surface is about dates
    # somebody else announced rather than what this office asked for.
    assert work_items.work_items(specialist) == []
    tahtajad = signed_in.get(reverse("intelligence:important_dates"), follow=True)
    assert FEEDBACK_DEADLINE_LABEL not in tahtajad.content.decode()


def test_nothing_completes_or_cancels_because_the_day_passed(normal_matter, specialist):
    """No automatic transition hangs off the date."""
    engagement = _round(normal_matter, deadline=dt.date(2020, 1, 1))

    process_steps(matter=normal_matter, user=specialist)

    engagement.refresh_from_db()
    assert engagement.feedback_deadline == dt.date(2020, 1, 1)
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENGAGEMENT_CHANGED
    ).exists()


# ===========================================================================
# C — the overview panel: three answers, one boundary
# ===========================================================================


def _add(client, matter, **fields):
    return client.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        fields,
        headers={"HX-Request": "true"},
    )


def test_an_empty_form_still_records_a_plan(signed_in, normal_matter):
    """ADR 0083 §2. Both boxes left empty is still a complete answer."""
    response = _add(signed_in, normal_matter)
    assert response.status_code == 200

    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""
    assert overview.published_on is None


def test_the_button_says_what_it_does(signed_in, normal_matter):
    """`Salvesta` on a fieldless panel read as a broken text box."""
    panel = _detail(signed_in, normal_matter)
    panel = panel[panel.index('id="lisa-koduleht"') :]
    panel = panel[: panel.index("</form>")]

    assert ">Lisa ülevaade<" in panel
    assert "planeeritud" not in panel
    assert ">Salvesta<" not in panel
    # And the two optional controls are there, labelled, under their own legend.
    assert 'name="url"' in panel
    assert 'name="published_on"' in panel
    assert "Kodulehe link" in panel
    assert "Avaldamise kuupäev" in panel
    assert "Kui ülevaade on juba avaldatud" in panel


def test_the_date_box_is_not_pre_filled(signed_in, normal_matter):
    """Load-bearing: a pre-filled date would make «neither filled» unreachable.

    Every plan would then arrive carrying a publication date nobody typed.
    """
    panel = _detail(signed_in, normal_matter)
    panel = panel[panel.index('id="lisa-koduleht"') :]
    box = panel[panel.index('name="published_on"') :]
    box = box[: box.index(">")]

    assert 'value=""' in box or "value=" not in box, box


def test_a_link_and_a_date_record_a_published_overview_in_one_act(signed_in, normal_matter):
    """ADR 0083 §2. The case the old panel could not express."""
    response = _add(signed_in, normal_matter, url=KODA_URL, published_on="14.03.2026")
    assert response.status_code == 200

    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == KODA_URL
    assert overview.published_on == PUBLISHED_ON
    assert overview.published_at is not None


def test_creating_it_published_records_both_lifecycle_events(signed_in, normal_matter):
    """The row genuinely passed through both states, so the history says so."""
    _add(signed_in, normal_matter, url=KODA_URL, published_on="14.03.2026")

    kinds = set(
        ChangeEvent.objects.filter(matter=normal_matter).values_list("event_type", flat=True)
    )
    assert ChangeEventType.WEBSITE_OVERVIEW_PLANNED in kinds
    assert ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED in kinds


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"url": KODA_URL}, "published_on"),
        ({"published_on": "14.03.2026"}, "url"),
    ],
)
def test_half_a_publication_is_refused_and_keeps_what_was_typed(
    signed_in, normal_matter, fields, missing
):
    """Dropping a pasted address would lose the fact they opened the panel for."""
    response = _add(signed_in, normal_matter, **fields)
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()
    # The panel is reopened with the refusal on the missing box.
    assert 'id="lisa-koduleht"' in body
    for value in fields.values():
        assert value in body


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://koda.ee/uudised/x",
        "https://koda.ee.example.com/uudised/x",
        "https://notkoda.ee/uudised/x",
        "https://koda.ee@evil.example/x",
        "https://example.com/uudised/x",
        "ftp://koda.ee/x",
    ],
)
def test_the_koda_boundary_is_unchanged_on_the_new_path(signed_in, normal_matter, bad_url):
    """ADR 0081 §3's trust boundary, reached through the panel rather than the strip."""
    response = _add(signed_in, normal_matter, url=bad_url, published_on="14.03.2026")

    assert response.status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()


def test_a_subdomain_of_koda_ee_is_still_accepted(signed_in, normal_matter):
    response = _add(
        signed_in, normal_matter, url="https://www.koda.ee/uudised/x", published_on="14.03.2026"
    )

    assert response.status_code == 200
    assert MatterWebsiteOverview.objects.get().status == WebsiteOverviewStatus.PUBLISHED


def test_a_closed_matter_refuses_the_published_path_too(signed_in, specialist):
    """The closed-Matter guard is the service's and applies to both shapes."""
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = factories.MatterFactory(owner=specialist)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="tehtud"
    )

    response = _add(signed_in, matter, url=KODA_URL, published_on="14.03.2026")

    assert response.status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=matter).exists()


def test_a_planned_row_says_what_to_do_next(signed_in, normal_matter):
    """ADR 0083 §2. `Avalda` named the transition; this names the action."""
    _add(signed_in, normal_matter)

    body = _detail(signed_in, normal_matter)
    strip = body[body.index('id="kodulehe-ulevaated"') :]

    assert "Lisa link ja avaldamiskuupäev" in strip
    assert re.search(r"<summary[^>]*>Avalda</summary>", strip) is None
