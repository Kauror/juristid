"""An approximate `Kaasamise kuupäev` keeps meaning what it meant.

docs/adr/0082 took one date off docs/adr/0079 §11's exact-only list and gave
`+ Kaasamine` the four-way `Täpsus` control. docs/adr/0086 §1 takes the control
back off both `Kaasamine` surfaces — it was two decisions deep on a panel whose
overwhelming case is «this happened today» — and **pays for that by preserving
everything already recorded through it.**

So this module changed sides. It used to prove that a period could be *entered*;
it now proves that one already stored is not lost, not misread and not quietly
rewritten by a simplified form that cannot express it. The column, the stored
anchors, `format_at_precision` and every surface that renders an approximate
engagement are untouched.

What this module holds
----------------------
* the two write surfaces offer **no** precision control and store exact days;
* every stored period still renders as the period — the chronology, the
  timeline read model, *Viimane tegevus*, *Viimati muudetud*, the register row —
  and **no surface prints its anchor as a day**;
* the correction form opens such a record with the day box *empty*, names the
  stored period beside it, and treats an empty box on save as «leave it alone».
  That rule is the whole of docs/adr/0086 §1's preservation, and the regression
  it guards is a lawyer fixing a typo in the audience and silently deleting
  «oktoober 2025»;
* `Kustuta salvestatud kuupäev` is the one deliberate way to remove such a
  date, and it is rendered only for the records that need it;
* an **unknown** date stays unknown, through every live write path including
  the superseded `sissekanne/` composer, and is never an approximate one;
* the correction protections — concurrency, validation, permissions, closed
  Matters, crafted POSTs — still hold with a period on the record.

Not held here: `tests/test_engagement_correction.py` owns the correction
contract itself, `tests/test_engagement_dates.py` owns the date defaults and the
*no stamped today* regression, and `tests/test_engagement_feedback_wait.py` owns
the waiting workflow.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.activity import activity_of, annotate_last_activity
from app.matters.enums import EngagementKind
from app.matters.models import Matter, MatterEngagement
from app.matters.my_work import recent_changes
from app.matters.services import (
    DomainError,
    add_engagement,
    close_matter,
    correct_engagement,
)
from app.matters.timeline import ENGAGEMENT_DATE_UNKNOWN, matter_timeline
from app.workflow.enums import DatePrecision, Disposition
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

#: ``(precision, the stored anchor, how it reads)``.
#:
#: The anchors are chosen so a surface that fell back to rendering one prints
#: something visibly wrong rather than something plausible: nobody recording a
#: consultation «oktoobris 2025» would have typed 1 October.
#:
#: **Every period is in the past**, and that is load-bearing rather than
#: incidental. The chronology projects facts that have *happened* and drops a
#: milestone dated after today, so a future engagement can be stored correctly
#: and read back from nowhere — a test written around one asserts on an empty
#: page and passes for the wrong reason.
PERIODS = [
    (DatePrecision.EXACT, dt.date(2025, 10, 15), "15.10.2025"),
    (DatePrecision.MONTH, dt.date(2025, 10, 1), "oktoober 2025"),
    (DatePrecision.QUARTER, dt.date(2025, 10, 1), "IV kvartal 2025"),
    (DatePrecision.YEAR, dt.date(2019, 1, 1), "2019"),
]

PERIOD_IDS = [str(precision) for precision, _, _ in PERIODS]

#: The anchors that are *not* days anybody typed. Used where the point of the
#: test is that an anchor must not be printed, which an exact date cannot show.
APPROXIMATE = [row for row in PERIODS if row[0] != DatePrecision.EXACT]
APPROXIMATE_IDS = [str(precision) for precision, _, _ in APPROXIMATE]


def _stored(matter, precision, anchor, **extra):
    """One engagement already dated to a period, written by the service.

    Through `add_engagement` rather than through a form, and that is the point
    of this whole module now: the forms cannot state a period any more, and
    these are the rows that were written while they could. The importer and the
    register enrichment write the same shapes, so this is not a fixture-only
    state (docs/adr/0086 §1).
    """
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=anchor,
        occurred_on_precision=precision,
        **extra,
    )


def _add(client, matter, **extra):
    """One `+ Kaasamine` save, through the route a person actually uses."""
    payload = {"audience": "liikmed", **extra}
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _edit(client, engagement, **extra):
    """One `Muuda` save on an existing row, through its own route.

    `occurred_on` is deliberately **not** defaulted here. Leaving it out is what
    a person does when they open the form on an approximate record and change
    something else, and that is the case docs/adr/0086 §1's preservation rule is
    about.
    """
    payload = {
        "title": engagement.title,
        "revision": engagement.updated_at.isoformat(),
        **extra,
    }
    return client.post(_edit_url(engagement), payload, headers={"HX-Request": "true"})


def _edit_url(engagement) -> str:
    return reverse(
        "matters:update_engagement",
        kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _panel(body: str) -> str:
    """The `+ Kaasamine` panel's markup, from its id to the end of its form."""
    section = body[body.index('id="lisa-kaasamine"') :]
    return section[: section.index("</form>")]


def _chronology_line(client, matter, engagement) -> str:
    """The one chronology row this engagement renders, as text.

    Cut at the row's own `</article>` rather than at the first `</div>`. The
    element this starts at holds nested `<div>`s — since docs/adr/0105 §2 the
    very first thing inside it is `.uxtl__head` — so «up to the next `</div>`»
    stopped one line in, and the assertions about what the row *says* were
    reading a slice that no longer contained the sub-line, the wait or the
    files. The article is the row, which is what this helper claims to return.
    """
    body = _detail(client, matter)
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    return body[start : body.index("</article>", start)]


def _fact(matter: Matter, user):
    """*Viimane tegevus* for one Matter, read the way the register reads it."""
    annotated = annotate_last_activity(Matter.objects.filter(pk=matter.pk), user).first()
    return activity_of(annotated)


# ===========================================================================
# A — the control is gone from both surfaces
# ===========================================================================


def test_the_panel_offers_no_precision_control(signed_in, specialist):
    """docs/adr/0086 §1. `+ Kaasamine` asks for a day, and asks once.

    Asserted as the radio group rather than as the labels, because the labels
    are also on three other panels of the same page: what has to be true is that
    `+ Kaasamine` no longer carries *this* control.
    """
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_detail(signed_in, matter))

    assert 'name="engagement_precision"' not in panel
    assert "precision__chips" not in panel
    # The day box is still `occurred_on`, still inside the panel, and still
    # pre-filled with today: what went is the question about how exactly the
    # answer is known, not the answer.
    assert 'name="occurred_on"' in panel
    assert f'value="{format_estonian_date(timezone.localdate())}"' in panel


def test_the_correction_form_offers_no_precision_control(signed_in, specialist):
    """And the editor with it, including on a record that carries a period.

    A form that could write a precision the creating panel cannot would be a
    record correctable into a shape it could never have been created in — the
    rule `EngagementForm` already keeps, read the other way round.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2025, 10, 1))

    form = signed_in.get(_edit_url(engagement)).content.decode()

    assert 'name="engagement_precision"' not in form
    assert "precision__chips" not in form


def test_neither_surface_offers_a_kind_control(signed_in, specialist):
    """docs/adr/0086 §1's other subtraction, checked on both write surfaces.

    `Liik` was a classification nothing read back. The column keeps every value
    it holds; what went is the question.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.EXACT, dt.date(2025, 10, 15))

    panel = _panel(_detail(signed_in, matter))
    form = signed_in.get(_edit_url(engagement)).content.decode()
    row = form[form.index(f'id="kaasamine-{engagement.pk}-sisu"') :]

    assert 'name="kind"' not in panel
    assert 'name="kind"' not in row


# ===========================================================================
# B — a stored period still reads as itself, everywhere
# ===========================================================================


def test_the_simplified_panel_stores_an_exact_day(signed_in, specialist):
    """What the one remaining write shape actually writes.

    `EXACT` and the day in the box. The column and its vocabulary are untouched
    — this is the only value the forms can now produce, not the only value the
    model can hold (docs/adr/0086 §1).
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, occurred_on="15.10.2025")
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on == dt.date(2025, 10, 15)
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.display_date == "15.10.2025"


@pytest.mark.parametrize(("precision", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_the_service_still_stores_every_period(specialist, precision, anchor, reads):
    """§1, §4 of docs/adr/0082, unchanged below the form.

    `bounds_for` is still the shared normaliser and the importer still writes
    through it, so a quarter on a consultation is still the same stored value as
    a quarter on an `Oluline tähtaeg`. Removing a control did not narrow a
    column.
    """
    matter = factories.MatterFactory(owner=specialist)

    engagement = _stored(matter, precision, anchor)

    assert engagement.occurred_on == anchor
    assert engagement.occurred_on_precision == precision
    assert engagement.display_date == reads


@pytest.mark.parametrize(("precision", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_the_chronology_prints_the_period_and_never_its_anchor(
    signed_in, specialist, precision, anchor, reads
):
    """The row a reader actually sees, on the page they actually open."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, precision, anchor)

    row = _chronology_line(signed_in, matter, engagement)

    assert reads in row
    if precision != DatePrecision.EXACT:
        assert format_estonian_date(anchor) not in row, "the anchor reached the screen as a day"
    assert ENGAGEMENT_DATE_UNKNOWN not in row


@pytest.mark.parametrize(("precision", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_the_timeline_read_model_says_the_same_thing_as_the_page(
    specialist, precision, anchor, reads
):
    """The projection behind the row, so a template change cannot hide a
    regression in the thing the template reads."""
    matter = factories.MatterFactory(owner=specialist)
    _stored(matter, precision, anchor)

    rows, _more = matter_timeline(matter=matter, user=specialist)
    milestone = next(row.milestone for row in rows if row.is_engagement)

    assert milestone.display_date == reads


@pytest.mark.parametrize(("precision", "anchor", "reads"), APPROXIMATE, ids=APPROXIMATE_IDS)
def test_the_edit_form_opens_a_period_with_an_empty_box_and_says_so(
    signed_in, specialist, precision, anchor, reads
):
    """docs/adr/0079 §2 and docs/adr/0086 §1, stated where it is easiest to break.

    An editor that opened `01.10.2025` in the date box for a record meaning
    *oktoober 2025* would invite the person to save the invented day back. The
    box is therefore empty — and because an empty box on every other row means
    «clear the date», this row has to *say* what its empty box means, and offer
    the deliberate way to clear it.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, precision, anchor)

    form = signed_in.get(_edit_url(engagement)).content.decode()

    day_box = form[form.index('name="occurred_on"') :]
    day_box = day_box[: day_box.index(">")]
    assert 'value=""' in day_box or "value=" not in day_box, (
        f"the anchor is sitting in the day box: {day_box}"
    )
    assert format_estonian_date(anchor) not in form
    assert reads in form, "the stored period is not named anywhere on the form"
    assert 'name="clear_occurred_on"' in form


def test_an_exact_record_opens_with_its_day_and_no_clear_control(signed_in, specialist):
    """The other side of the rule, and why the checkbox is conditional.

    On a row the day box can show, the box already clears the column — a second
    control saying the same thing would be a second way to mean one act.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.EXACT, dt.date(2025, 10, 15))

    form = signed_in.get(_edit_url(engagement)).content.decode()

    day_box = form[form.index('name="occurred_on"') :]
    day_box = day_box[: day_box.index(">")]
    assert 'value="15.10.2025"' in day_box, day_box
    assert 'name="clear_occurred_on"' not in form


@pytest.mark.parametrize(("precision", "anchor", "reads"), APPROXIMATE, ids=APPROXIMATE_IDS)
def test_saving_a_period_back_with_an_empty_day_box_keeps_it(
    signed_in, specialist, precision, anchor, reads
):
    """**The regression this whole round has to not cause.**

    A lawyer opens `Muuda` on «kaasamine oktoobris 2025» to fix a typo in the
    audience and presses `Salvesta`. The day box was empty when the form opened,
    because an anchor is not a day — so a form that read «empty» as «clear the
    date» would delete what somebody recorded, silently, on a save about
    something else entirely (docs/adr/0086 §1).
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, precision, anchor)

    response = _edit(signed_in, engagement, title="kaubandusvaldkonna töögrupp")
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.title == "kaubandusvaldkonna töögrupp"
    assert engagement.occurred_on == anchor
    assert engagement.occurred_on_precision == precision
    assert engagement.display_date == reads


def test_a_period_can_be_corrected_to_the_exact_day_somebody_found(signed_in, specialist):
    """A period is a statement, not a lock: typing a day replaces it.

    This is the one direction the simplified form can move an approximate date
    in, and it is the useful one — somebody finds the mailing and now knows the
    day.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2026, 10, 1))

    response = _edit(signed_in, engagement, occurred_on="17.10.2026")
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2026, 10, 17)
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.display_date == "17.10.2026"


def test_no_write_surface_can_create_a_new_period(signed_in, specialist):
    """The accepted cost of docs/adr/0086 §1, asserted rather than assumed.

    A crafted POST carrying the retired control's field names writes an exact
    day — the fields do not exist, Django drops them, and the form resolves the
    day box. Nothing half-reads them into a period.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(
        signed_in,
        matter,
        occurred_on="15.10.2025",
        engagement_precision=DatePrecision.MONTH,
        engagement_month="10",
        engagement_year="2025",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on == dt.date(2025, 10, 15)
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.has_approximate_date is False


# ===========================================================================
# C — an unknown date is not an approximate one
# ===========================================================================


def test_an_empty_date_stays_unknown_and_takes_no_precision(signed_in, specialist):
    """§5. Absence has no precision, and «kuupäev teadmata» is still an answer.

    The regression guarded here is the tempting one: a widened date control
    that answers «I do not know» with `YEAR` and this year.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, occurred_on="")
    assert response.status_code == 200

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on is None
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.display_date == ""
    assert engagement.has_approximate_date is False


def test_the_clear_checkbox_removes_a_period_and_its_precision(signed_in, specialist):
    """docs/adr/0082 §5 and docs/adr/0086 §1. Deliberate, and only deliberate.

    `NULL` + `MONTH` is a period with nothing to qualify — a record in that
    state renders as neither a date nor «kuupäev teadmata» but as whichever the
    reading surface guessed — so removing the date normalises the precision back
    to `EXACT` with it. The removal itself takes a checkbox, because on this one
    kind of record an empty day box means «leave it alone».
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2026, 10, 1))

    response = _edit(signed_in, engagement, clear_occurred_on="on")
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on is None
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert ENGAGEMENT_DATE_UNKNOWN in _chronology_line(signed_in, engagement.matter, engagement)


def test_the_editor_opens_an_undated_record_with_an_empty_box_and_not_today(signed_in, specialist):
    """§5. The day box declares `initial=timezone.localdate` for the *add* route.

    A correction form that inherited it would open a record with no date at all
    showing today, one `Salvesta` away from stamping the file with a day nobody
    chose — the defect docs/adr/0078 §2 removed, coming back through the edit
    path. `_engagement_edit_form` fills this box from the record, and an undated
    record fills it with nothing.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="Vana voor")

    form = signed_in.get(_edit_url(engagement)).content.decode()

    day_box = form[form.index('name="occurred_on"') :]
    day_box = day_box[: day_box.index(">")]
    assert format_estonian_date(timezone.localdate()) not in day_box, day_box
    assert 'value=""' in day_box or "value=" not in day_box, day_box
    # No clear control either: there is nothing stored for it to remove, and the
    # box in front of them already says so.
    assert 'name="clear_occurred_on"' not in form


def test_saving_an_undated_record_back_unchanged_stamps_nothing(signed_in, specialist):
    """And the round trip, which is what a person actually does to it.

    Opening `Muuda` to fix a typo in the audience must not date the
    consultation as a side effect.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(matter=matter, kind=EngagementKind.SURVEY, title="Vana voor")

    response = _edit(signed_in, engagement, title="Vana voor 2019")
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.title == "Vana voor 2019"
    assert engagement.occurred_on is None
    assert engagement.occurred_on_precision == DatePrecision.EXACT


def test_the_service_refuses_to_store_a_precision_on_a_date_that_does_not_exist():
    """The same rule below the form, because the form is not the only writer."""
    matter = factories.MatterFactory()

    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=None,
        occurred_on_precision=DatePrecision.QUARTER,
    )

    assert engagement.occurred_on_precision == DatePrecision.EXACT


def test_an_unknown_date_stays_unknown_through_the_superseded_composer(signed_in, specialist):
    """`teemad/<pk>/sissekanne/` — a live route with no date box at all.

    It cannot ask, so it must not answer: not today, and not a year either. The
    route rather than the form helper, because a stale tab reaches the route.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:compose", kwargs={"pk": matter.pk}),
        {
            "body": "Küsisime liikmetelt arvamust.",
            "engagement_kind": EngagementKind.SURVEY,
            "engagement_audience": "liikmed",
        },
    )
    assert response.status_code == 200

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on is None
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.occurred_on != timezone.localdate()


def test_no_live_write_path_invents_a_period_for_an_unknown_date(signed_in, specialist):
    """All three doors at once, so none can regress while another is watched."""
    panel_matter = factories.MatterFactory(owner=specialist)
    composer_matter = factories.MatterFactory(owner=specialist)
    edit_matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(edit_matter, DatePrecision.MONTH, dt.date(2026, 10, 1))

    _add(signed_in, panel_matter, occurred_on="")
    signed_in.post(
        reverse("matters:compose", kwargs={"pk": composer_matter.pk}),
        {
            "body": "Küsisime liikmetelt arvamust.",
            "engagement_kind": EngagementKind.SURVEY,
            "engagement_audience": "liikmed",
        },
    )
    _edit(signed_in, engagement, clear_occurred_on="on")

    for created in MatterEngagement.objects.all():
        assert created.occurred_on is None, created.matter_id
        assert created.occurred_on_precision == DatePrecision.EXACT, created.matter_id


# ===========================================================================
# D — no anchor is printed as a day, anywhere
# ===========================================================================


@pytest.mark.parametrize(("precision", "anchor", "reads"), APPROXIMATE, ids=APPROXIMATE_IDS)
def test_viimane_tegevus_reads_the_period_rather_than_the_anchor(
    specialist, precision, anchor, reads
):
    """§3. `MatterActivityFact` carries the precision of the row it read.

    The register column renders this through `display_date`; without the
    precision travelling with the date it would print `1.10.2026` for a
    consultation nobody dated to 1 October.
    """
    matter = factories.MatterFactory(owner=specialist)
    _stored(matter, precision, anchor)

    fact = _fact(matter, specialist)

    assert fact is not None
    assert fact.occurred_on == anchor, "the anchor is still what the column sorts on"
    assert fact.date_precision == precision
    assert fact.display_date == reads
    assert fact.display_date != format_estonian_date(anchor)


def test_viimane_tegevus_is_unchanged_for_every_exact_fact(specialist):
    """The other half, and the one that decides whether a baseline moves.

    `display_date` for an exact date is `format_estonian_date`, which is byte
    for byte the `j.n.Y` the register cell rendered before this round.
    """
    matter = factories.MatterFactory(owner=specialist, received_date=dt.date(2026, 9, 7))
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 9, 12),
    )

    fact = _fact(matter, specialist)

    assert fact is not None
    assert fact.date_precision == DatePrecision.EXACT
    assert fact.display_date == "12.9.2026"
    assert fact.compact_display == "12.9"


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (DatePrecision.MONTH, "10.26"),
        (DatePrecision.QUARTER, "IV kvartal 2026"),
        (DatePrecision.YEAR, "2026"),
    ],
)
def test_viimati_muudetud_has_a_compact_reading_that_is_not_a_day(specialist, precision, expected):
    """*Viimati muudetud* on Minu töö prints `j.n` and has no room for a year.

    A month therefore becomes `10.26` — two numbers, the shape the dense work
    surfaces already use (`WorkItem.compact_month`) — and a quarter or a year is
    spelled out, because neither has a two-number form a reader would arrive at
    unaided. What must not appear is `1.10`.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=precision,
    )

    rows = recent_changes(specialist, specialist)

    assert [row.compact_display for row in rows] == [expected]
    assert rows[0].occurred_on == dt.date(2026, 10, 1)


def test_an_exact_engagement_wins_a_tie_against_an_approximate_one(specialist):
    """Two rows on one anchor: the day is the more informative of the two.

    Deterministic on purpose. A column that flickered between `1.10.2026` and
    *oktoober 2026* across identical requests would be a column nobody trusts.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="kuu",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="päev",
        occurred_on=dt.date(2026, 10, 1),
    )

    fact = _fact(matter, specialist)

    assert fact is not None
    assert fact.date_precision == DatePrecision.EXACT
    assert fact.display_date == "1.10.2026"


def test_the_register_row_renders_the_activity_through_display_date(signed_in, specialist):
    """The cell itself, so a template that went back to the raw date fails."""
    matter = factories.MatterFactory(owner=specialist, title="Pakendiseaduse muudatused")
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )

    body = signed_in.get(reverse("matters:matter_list")).content.decode()

    # `rindex`: the column heading carries the same class, and a slice that
    # started there would run through the whole row and pass on the title.
    cell = body[body.rindex("table__lastactivity") :]
    cell = cell[: cell.index("</td>")]
    assert "oktoober 2026" in cell
    assert "1.10.2026" not in cell


# ===========================================================================
# E — the reply-by date stays exact, and stays inert
# ===========================================================================


def test_the_reply_by_date_round_trips_beside_a_period_and_stays_a_day(signed_in, specialist):
    """§2. The two dates are different kinds of fact and are stored as such."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(
        matter,
        DatePrecision.MONTH,
        dt.date(2025, 10, 1),
        feedback_deadline=dt.date(2025, 10, 22),
    )

    response = _edit(signed_in, engagement, feedback_deadline="22.10.2025")
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on_precision == DatePrecision.MONTH
    assert engagement.feedback_deadline == dt.date(2025, 10, 22)
    # Printed as the exact day it is, on the row it belongs to.
    row = _chronology_line(signed_in, matter, engagement)
    assert "22.10.2025" in row


def test_a_reply_by_date_inside_the_engagement_period_is_accepted(signed_in, specialist):
    """§2. The rule compares against the period's *first* day, deliberately.

    «Kaasamine oktoobris, vastuseid ootan 15. oktoobriks» is the commonest thing
    a round run over a month says. Comparing against the period's end would
    refuse it — and the anchor is still what the rule reads, even though no form
    can state one any more.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2025, 10, 1))

    response = _edit(signed_in, engagement, feedback_deadline="15.10.2025")

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    assert engagement.feedback_deadline == dt.date(2025, 10, 15)


def test_a_reply_by_date_before_the_whole_period_is_still_refused(signed_in, specialist):
    """The rule keeps working when the anchor is not in the day box.

    A rule that read the *box* rather than the resolved date would simply stop
    firing for a record dated to a period — which is a validation that silently
    switches off on exactly the rows the form cannot show.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2025, 10, 1))

    response = _edit(signed_in, engagement, feedback_deadline="20.9.2025")

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.feedback_deadline is None
    assert "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva." in response.content.decode()


def test_an_approximate_engagement_creates_no_deadline_record_of_its_own(specialist):
    """§2, §5. A period never grows an end, and never becomes a stored deadline.

    An open feedback wait is read as work since docs/adr/0086 §3, and that is a
    *reading* — no `NextAction`, no `MatterImportantDate` and no
    `Matter.response_deadline` is written, which is what this has always
    guarded.
    """
    matter = factories.MatterFactory(owner=specialist)

    engagement = _stored(
        matter,
        DatePrecision.YEAR,
        dt.date(2019, 1, 1),
        feedback_deadline=dt.date(2019, 10, 22),
    )

    matter.refresh_from_db()
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not matter.important_dates.exists()
    assert matter.response_deadline is None
    assert not hasattr(engagement, "period_end")


# ===========================================================================
# F — the correction protections still hold with a period in the form
# ===========================================================================


def test_a_stale_correction_on_a_period_writes_nothing(signed_in, specialist):
    """Optimistic concurrency is compared before anything is decided."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2025, 10, 1))
    stale = engagement.updated_at.isoformat()
    correct_engagement(engagement=engagement, title="teine nimi")

    response = signed_in.post(
        _edit_url(engagement),
        {"title": "liikmed", "occurred_on": "17.10.2025", "revision": stale},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 409
    engagement.refresh_from_db()
    assert engagement.title == "teine nimi"
    assert engagement.occurred_on == dt.date(2025, 10, 1)
    assert engagement.occurred_on_precision == DatePrecision.MONTH


def test_an_unreadable_day_is_refused_and_nothing_is_written(signed_in, specialist):
    """A date box is a text box, and what arrives in it is not always a date."""
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, occurred_on="32.13.2025")

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()


def test_a_crafted_precision_cannot_reach_the_column_through_a_form(signed_in, specialist):
    """Neither form names `occurred_on_precision`, so neither view passes one.

    A POST carrying the retired control's field names, or the column's own,
    leaves the record exactly as the day box resolves it — an exact day. The
    vocabulary check in the service is the backstop below that
    (`test_the_service_refuses_a_precision_outside_the_vocabulary`).
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2025, 10, 1))

    response = _edit(
        signed_in,
        engagement,
        occurred_on_precision=DatePrecision.HALF_YEAR,
        engagement_precision=DatePrecision.HALF_YEAR,
        engagement_half="2",
        engagement_year="2025",
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement.refresh_from_db()
    # Untouched: the day box was empty and the record carries a period, so the
    # save left it alone (docs/adr/0086 §1).
    assert engagement.occurred_on_precision == DatePrecision.MONTH
    assert engagement.occurred_on == dt.date(2025, 10, 1)


def test_a_closed_matter_refuses_a_correction_on_a_period_too(signed_in, specialist):
    """The closed-Matter rule, unchanged by the narrowed date control.

    Enforced under the Matter's row lock rather than by whether a button was
    rendered, so a crafted POST meets the same refusal.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2025, 10, 1))
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    response = _edit(signed_in, engagement, occurred_on="17.10.2025")

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2025, 10, 1)
    assert engagement.occurred_on_precision == DatePrecision.MONTH


def test_the_service_refuses_a_precision_outside_the_vocabulary():
    """Below the form, and before the database. A `DomainError` names what was
    wrong; an `IntegrityError` out of a composer transaction names a
    constraint."""
    matter = factories.MatterFactory()

    with pytest.raises(DomainError):
        add_engagement(
            matter=matter,
            kind=EngagementKind.SURVEY,
            title="liikmed",
            occurred_on=dt.date(2026, 10, 1),
            occurred_on_precision="SOMETIME",
        )


# ===========================================================================
# G — the audit row is self-describing
# ===========================================================================


def test_the_audit_row_records_the_precision_beside_the_anchor(specialist):
    """An audit row carrying `2026-10-01` alone says «1 October» to whoever
    reads it back — the invention docs/adr/0079 §2 exists to refuse."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2026, 10, 1), actor=specialist)

    from app.audit.models import ChangeEvent

    added = ChangeEvent.objects.filter(object_id=engagement.pk).latest("created_at")
    assert added.payload["occurred_on"] == "2026-10-01"
    assert added.payload["occurred_on_precision"] == DatePrecision.MONTH

    correct_engagement(
        engagement=engagement,
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.QUARTER,
        actor=specialist,
    )

    changed = ChangeEvent.objects.filter(object_id=engagement.pk).latest("created_at")
    assert changed.payload["occurred_on_precision_from"] == DatePrecision.MONTH
    assert changed.payload["occurred_on_precision_to"] == DatePrecision.QUARTER
    # The date itself did not move, so it is not claimed to have.
    assert "occurred_on_from" not in changed.payload


def test_an_unrelated_correction_leaves_the_precision_alone(specialist):
    """`_UNSET` protects a field a caller does not name — including this one.

    The importer and the register refresh rely on that, and a title fix must
    not quietly make *oktoober 2026* mean 1 October.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = _stored(matter, DatePrecision.MONTH, dt.date(2026, 10, 1))

    correct_engagement(engagement=engagement, title="kaubandusvaldkonna töögrupp")

    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2026, 10, 1)
    assert engagement.occurred_on_precision == DatePrecision.MONTH
