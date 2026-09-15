"""`Kaasamise kuupäev` at the precision it is actually known to.

docs/adr/0082 takes one date off docs/adr/0079 §11's exact-only list. A
consultation round is routinely remembered as «oktoobris» or «2019» — it is a
statement about something that happened out in the world, not a day this office
recorded or owes — and before this a person who knew only that had two answers
available, an invented day or an empty field, both of them worse than the one
they had.

What this module holds
----------------------
The four precisions through the two real write paths (`+ Kaasamine` and
`Muuda`), and for every one of them the three claims that have to hold
together: what was stored, what the chronology *says*, and what the edit form
reopens on. Storing ``2026-10-01`` + ``MONTH`` correctly and then printing
``1.10.2026`` is the same defect arriving one layer later, and the anchors here
are deliberately the awkward ones — a quarter and a year whose first day is not
a day anybody would have typed.

Beside the matrix, the rules that are easy to get right in the create path and
wrong everywhere else:

* an **unknown** date stays unknown, through every live write path including
  the superseded `sissekanne/` composer, and is never an approximate one;
* no surface renders an anchor as a day — not the chronology, not *Viimane
  tegevus*, not *Viimati muudetud*, not the correction form;
* `Tagasisidet ootame kuni` stays an exact day and stays inert (§2);
* the correction protections this PR already had — concurrency, validation,
  permissions, closed Matters, crafted POSTs — still hold with a period in the
  form.

Not held here: `tests/test_engagement_correction.py` owns the correction
contract itself, and `tests/test_engagement_dates.py` owns the *no stamped
today* regression that preceded this.
"""

from __future__ import annotations

import datetime as dt
import re

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

#: ``(precision, the fields that precision asks for, the stored anchor, how it
#: reads)``.
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
    (
        DatePrecision.EXACT,
        {"occurred_on": "15.10.2025"},
        dt.date(2025, 10, 15),
        "15.10.2025",
    ),
    (
        DatePrecision.MONTH,
        {"engagement_month": "10", "engagement_year": "2025"},
        dt.date(2025, 10, 1),
        "oktoober 2025",
    ),
    (
        DatePrecision.QUARTER,
        {"engagement_quarter": "4", "engagement_year": "2025"},
        dt.date(2025, 10, 1),
        "IV kvartal 2025",
    ),
    (
        DatePrecision.YEAR,
        {"engagement_year": "2019"},
        dt.date(2019, 1, 1),
        "2019",
    ),
]

PERIOD_IDS = [str(precision) for precision, _, _, _ in PERIODS]

#: The anchors that are *not* days anybody typed. Used where the point of the
#: test is that an anchor must not be printed, which an exact date cannot show.
APPROXIMATE = [row for row in PERIODS if row[0] != DatePrecision.EXACT]
APPROXIMATE_IDS = [str(precision) for precision, _, _, _ in APPROXIMATE]


def _add(client, matter, precision, answers, **extra):
    """One `+ Kaasamine` save, through the route a person actually uses."""
    payload = {
        "kind": EngagementKind.SURVEY,
        "audience": "liikmed",
        "engagement_precision": precision,
        **answers,
        **extra,
    }
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _edit(client, engagement, precision, answers, **extra):
    """One `Muuda` save on an existing row, through its own route."""
    payload = {
        "kind": engagement.kind,
        "title": engagement.title,
        "engagement_precision": precision,
        "revision": engagement.updated_at.isoformat(),
        **answers,
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
    """The one chronology row this engagement renders, as text."""
    body = _detail(client, matter)
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    return body[start : body.index("</div>", start)]


def _fact(matter: Matter, user):
    """*Viimane tegevus* for one Matter, read the way the register reads it."""
    annotated = annotate_last_activity(Matter.objects.filter(pk=matter.pk), user).first()
    return activity_of(annotated)


# ===========================================================================
# A — the control exists, and it is the shared one
# ===========================================================================


def test_the_panel_offers_the_same_four_precisions_as_every_other_period(signed_in, specialist):
    """§3. One composer, four chips, real radios — not a second widget.

    Asserted as the radio group rather than as the labels, because the labels
    are also on three other panels of the same page: what has to be true is
    that `+ Kaasamine` grew *this* control.
    """
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_detail(signed_in, matter))

    radios = re.findall(r'<input[^>]*type="radio"[^>]*name="engagement_precision"[^>]*>', panel)
    assert len(radios) == 4, f"{len(radios)} precision radios on + Kaasamine"
    assert all("precision__radio" in radio for radio in radios)
    chips = panel[panel.index("precision__chips") : panel.index("</fieldset>")]
    assert 'type="hidden"' not in chips, "the precision is unstateable without JavaScript"
    # The day box is still `occurred_on`, still inside the composer, and still
    # pre-filled with today: widening the question did not change the answer
    # people give nine times out of ten.
    assert 'name="occurred_on"' in panel
    assert f'value="{format_estonian_date(timezone.localdate())}"' in panel


def test_the_reply_by_date_is_not_given_a_precision_control(signed_in, specialist):
    """§2. `Tagasisidet ootame kuni` stays on docs/adr/0079 §11's list.

    It is a day somebody named to other people — «vastake 22. septembriks» — so
    there is nothing for a period to mean. One precision group on this panel,
    and it belongs to the engagement date.
    """
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_detail(signed_in, matter))

    assert 'name="feedback_deadline"' in panel
    assert panel.count("precision__chips") == 1
    assert 'name="feedback_deadline_precision"' not in panel
    assert re.search(r'name="feedback_deadline"[^>]*type="date"', panel) is None


def test_no_half_year_chip_is_offered_on_the_engagement_date(signed_in, specialist):
    """§3 and docs/adr/0079 §7. Four chips, and `Poolaasta` is not one of them."""
    matter = factories.MatterFactory(owner=specialist)

    panel = _panel(_detail(signed_in, matter))

    radios = re.findall(r'name="engagement_precision"[^>]*value="([A-Z_]+)"', panel)
    assert radios == ["EXACT", "MONTH", "QUARTER", "YEAR"]


# ===========================================================================
# B — the matrix: create, render, reopen
# ===========================================================================


@pytest.mark.parametrize(("precision", "answers", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_the_panel_stores_the_period_it_was_given(
    signed_in, specialist, precision, answers, anchor, reads
):
    """§1, §4. The anchor and the precision together, normalised the one way.

    `bounds_for` is the shared normaliser, so a quarter stated here is the same
    stored value as a quarter stated on `+ Oluline tähtaeg`. A second
    normalisation would put one period in two places in a sort.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, precision, answers)
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on == anchor
    assert engagement.occurred_on_precision == precision
    assert engagement.display_date == reads


@pytest.mark.parametrize(("precision", "answers", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_the_chronology_prints_the_period_and_never_its_anchor(
    signed_in, specialist, precision, answers, anchor, reads
):
    """§3. The row a reader actually sees, on the page they actually open."""
    matter = factories.MatterFactory(owner=specialist)
    _add(signed_in, matter, precision, answers)
    engagement = MatterEngagement.objects.get()

    row = _chronology_line(signed_in, matter, engagement)

    assert reads in row
    if precision != DatePrecision.EXACT:
        assert format_estonian_date(anchor) not in row, "the anchor reached the screen as a day"
    assert ENGAGEMENT_DATE_UNKNOWN not in row


@pytest.mark.parametrize(("precision", "answers", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_the_timeline_read_model_says_the_same_thing_as_the_page(
    signed_in, specialist, precision, answers, anchor, reads
):
    """The projection behind the row, so a template change cannot hide a
    regression in the thing the template reads."""
    matter = factories.MatterFactory(owner=specialist)
    _add(signed_in, matter, precision, answers)

    rows, _more = matter_timeline(matter=matter, user=specialist)
    milestone = next(row.milestone for row in rows if row.is_engagement)

    assert milestone.display_date == reads


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "reads"), APPROXIMATE, ids=APPROXIMATE_IDS
)
def test_the_edit_form_reopens_on_the_period_with_the_day_box_empty(
    signed_in, specialist, precision, answers, anchor, reads
):
    """§3, and docs/adr/0079 §2 stated where it is easiest to break.

    An editor that opened `01.10.2026` in the date box for a record meaning
    *oktoober 2026* would invite the person to save the invented day back — the
    create-path defect arriving through the edit path.
    """
    matter = factories.MatterFactory(owner=specialist)
    _add(signed_in, matter, precision, answers)
    engagement = MatterEngagement.objects.get()

    form = signed_in.get(_edit_url(engagement)).content.decode()

    chosen = re.search(rf'<input[^>]*value="{precision}"[^>]*>', form)
    assert chosen is not None and "checked" in chosen.group(0), form[:2000]
    day_box = form[form.index('name="occurred_on"') :]
    day_box = day_box[: day_box.index(">")]
    assert 'value=""' in day_box or "value=" not in day_box, (
        f"the anchor is sitting in the day box: {day_box}"
    )
    assert format_estonian_date(anchor) not in form


@pytest.mark.parametrize(("precision", "answers", "anchor", "reads"), PERIODS, ids=PERIOD_IDS)
def test_a_period_round_trips_through_the_correction_form(
    signed_in, specialist, precision, answers, anchor, reads
):
    """Open it, save it back unchanged, and nothing moves.

    The round trip is the test that catches a composer whose initial values and
    whose POST names disagree: such a form renders correctly, reads correctly,
    and quietly rewrites the record the first time somebody presses `Salvesta`.
    """
    matter = factories.MatterFactory(owner=specialist)
    _add(signed_in, matter, precision, answers)
    engagement = MatterEngagement.objects.get()

    response = _edit(signed_in, engagement, precision, answers)
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on == anchor
    assert engagement.occurred_on_precision == precision
    assert engagement.display_date == reads


def test_an_exact_engagement_can_be_corrected_to_the_month_it_really_was(signed_in, specialist):
    """The correction this ADR exists for: today's stamp told the truth.

    A row the old panel filed as «today» is exactly the row somebody now wants
    to say «see oli oktoobris» about.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="liikmed", occurred_on=dt.date(2026, 9, 14)
    )

    response = _edit(
        signed_in,
        engagement,
        DatePrecision.MONTH,
        {"engagement_month": "10", "engagement_year": "2026"},
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2026, 10, 1)
    assert engagement.occurred_on_precision == DatePrecision.MONTH
    assert engagement.display_date == "oktoober 2026"


def test_a_month_can_be_corrected_to_the_exact_day_somebody_found(signed_in, specialist):
    """And back again, because a period is a statement and not a lock."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )

    response = _edit(signed_in, engagement, DatePrecision.EXACT, {"occurred_on": "17.10.2026"})
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2026, 10, 17)
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.display_date == "17.10.2026"


# ===========================================================================
# C — an unknown date is not an approximate one
# ===========================================================================


def test_an_empty_date_stays_unknown_and_takes_no_precision(signed_in, specialist):
    """§5. Absence has no precision, and «kuupäev teadmata» is still an answer.

    The regression guarded here is the tempting one: a widened date control
    that answers «I do not know» with `YEAR` and this year.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, DatePrecision.EXACT, {"occurred_on": ""})
    assert response.status_code == 200

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on is None
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.display_date == ""
    assert engagement.has_approximate_date is False


def test_clearing_a_period_clears_the_precision_with_it(signed_in, specialist):
    """§5. `NULL` + `MONTH` is a period with nothing to qualify.

    A record left in that state renders as neither a date nor «kuupäev
    teadmata» but as whichever of the two the reading surface guessed.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )

    response = _edit(signed_in, engagement, DatePrecision.EXACT, {"occurred_on": ""})
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement.refresh_from_db()
    assert engagement.occurred_on is None
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert ENGAGEMENT_DATE_UNKNOWN in _chronology_line(signed_in, engagement.matter, engagement)


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
    engagement = add_engagement(
        matter=edit_matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )

    _add(signed_in, panel_matter, DatePrecision.EXACT, {"occurred_on": ""})
    signed_in.post(
        reverse("matters:compose", kwargs={"pk": composer_matter.pk}),
        {
            "body": "Küsisime liikmetelt arvamust.",
            "engagement_kind": EngagementKind.SURVEY,
            "engagement_audience": "liikmed",
        },
    )
    _edit(signed_in, engagement, DatePrecision.EXACT, {"occurred_on": ""})

    for created in MatterEngagement.objects.all():
        assert created.occurred_on is None, created.matter_id
        assert created.occurred_on_precision == DatePrecision.EXACT, created.matter_id


# ===========================================================================
# D — no anchor is printed as a day, anywhere
# ===========================================================================


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "reads"), APPROXIMATE, ids=APPROXIMATE_IDS
)
def test_viimane_tegevus_reads_the_period_rather_than_the_anchor(
    specialist, precision, answers, anchor, reads
):
    """§3. `MatterActivityFact` carries the precision of the row it read.

    The register column renders this through `display_date`; without the
    precision travelling with the date it would print `1.10.2026` for a
    consultation nobody dated to 1 October.
    """
    matter = factories.MatterFactory(owner=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=anchor,
        occurred_on_precision=precision,
    )

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

    response = _add(
        signed_in,
        matter,
        DatePrecision.MONTH,
        {"engagement_month": "10", "engagement_year": "2025"},
        feedback_deadline="22.10.2025",
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    engagement = MatterEngagement.objects.get()
    assert engagement.occurred_on_precision == DatePrecision.MONTH
    assert engagement.feedback_deadline == dt.date(2025, 10, 22)
    # Printed as the exact day it is, on the row it belongs to.
    row = _chronology_line(signed_in, matter, engagement)
    assert "Tagasisidet ootame kuni 22.10.2025" in row


def test_a_reply_by_date_inside_the_engagement_period_is_accepted(signed_in, specialist):
    """§2. The rule compares against the period's *first* day, deliberately.

    «Kaasamine oktoobris, vastuseid ootan 15. oktoobriks» is the commonest
    thing a round run over a month says. Comparing against the period's end
    would refuse it.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(
        signed_in,
        matter,
        DatePrecision.MONTH,
        {"engagement_month": "10", "engagement_year": "2025"},
        feedback_deadline="15.10.2025",
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    assert MatterEngagement.objects.get().feedback_deadline == dt.date(2025, 10, 15)


def test_a_reply_by_date_before_the_whole_period_is_still_refused(signed_in, specialist):
    """The rule keeps working when the anchor is not in the day box.

    A rule that read `occurred_on` directly would simply stop firing for three
    of the four precisions, which is a validation that silently switches off.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(
        signed_in,
        matter,
        DatePrecision.MONTH,
        {"engagement_month": "10", "engagement_year": "2025"},
        feedback_deadline="20.9.2025",
    )

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    assert "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva." in response.content.decode()


def test_an_approximate_engagement_creates_no_work_and_no_deadline(signed_in, specialist):
    """§2, §5. An approximate date is not a new deadline or overdue source.

    The whole risk of widening a date is that somewhere downstream a period
    grows an end, and an end grows a badge. Nothing here does.
    """
    matter = factories.MatterFactory(owner=specialist)

    _add(
        signed_in,
        matter,
        DatePrecision.YEAR,
        {"engagement_year": "2019"},
        feedback_deadline="22.10.2019",
    )

    matter.refresh_from_db()
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not matter.important_dates.exists()
    assert matter.response_deadline is None
    engagement = MatterEngagement.objects.get()
    assert not hasattr(engagement, "period_end")


# ===========================================================================
# F — the correction protections still hold with a period in the form
# ===========================================================================


def test_a_stale_correction_carrying_a_period_writes_nothing(signed_in, specialist):
    """Optimistic concurrency is compared before anything is decided."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="liikmed", occurred_on=dt.date(2026, 9, 14)
    )
    stale = engagement.updated_at.isoformat()
    correct_engagement(engagement=engagement, title="teine nimi")

    response = signed_in.post(
        _edit_url(engagement),
        {
            "kind": engagement.kind,
            "title": "liikmed",
            "engagement_precision": DatePrecision.QUARTER,
            "engagement_quarter": "4",
            "engagement_year": "2026",
            "revision": stale,
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 409
    engagement.refresh_from_db()
    assert engagement.title == "teine nimi"
    assert engagement.occurred_on == dt.date(2026, 9, 14)
    assert engagement.occurred_on_precision == DatePrecision.EXACT


def test_an_impossible_period_is_refused_and_nothing_is_written(signed_in, specialist):
    """Quarter V does not exist, and a partial period is not a date."""
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, DatePrecision.QUARTER, {"engagement_quarter": "5"})

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()


def test_a_period_named_without_its_year_is_refused(signed_in, specialist):
    """«Kuu» with nothing in either select is a question half-answered.

    Deliberately *not* read as «kuupäev teadmata»: somebody who chose a chip
    meant to say something, and answering them with a silent NULL is the
    product deciding what they meant.
    """
    matter = factories.MatterFactory(owner=specialist)

    response = _add(signed_in, matter, DatePrecision.MONTH, {"engagement_month": "10"})

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()


def test_a_crafted_half_year_is_refused_on_a_record_that_never_had_one(signed_in, specialist):
    """docs/adr/0079 §7, §9. The chips are built per record, and so is the
    validation: a precision this control does not offer cannot arrive through
    a hand-written POST either."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="liikmed", occurred_on=dt.date(2026, 9, 14)
    )

    response = _edit(
        signed_in,
        engagement,
        DatePrecision.HALF_YEAR,
        {"engagement_half": "2", "engagement_year": "2026"},
    )

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert engagement.occurred_on == dt.date(2026, 9, 14)


def test_a_closed_matter_refuses_a_period_correction_too(signed_in, specialist):
    """The PR's closed-Matter rule, unchanged by the widened date.

    Enforced under the Matter's row lock rather than by whether a button was
    rendered, so a crafted POST meets the same refusal.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter, kind=EngagementKind.SURVEY, title="liikmed", occurred_on=dt.date(2026, 9, 14)
    )
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="valmis"
    )

    response = _edit(
        signed_in,
        engagement,
        DatePrecision.MONTH,
        {"engagement_month": "10", "engagement_year": "2026"},
    )

    assert response.status_code == 400
    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2026, 9, 14)
    assert engagement.occurred_on_precision == DatePrecision.EXACT


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
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
        actor=specialist,
    )

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
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=dt.date(2026, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )

    correct_engagement(engagement=engagement, title="kaubandusvaldkonna töögrupp")

    engagement.refresh_from_db()
    assert engagement.occurred_on == dt.date(2026, 10, 1)
    assert engagement.occurred_on_precision == DatePrecision.MONTH
