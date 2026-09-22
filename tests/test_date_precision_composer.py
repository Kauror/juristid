"""Four Teema facts, four precisions, and nothing invented in between.

What this module holds
----------------------
The 4 × 4 matrix of docs/adr/0079 §1: `Järgmine tegevus`, `Oluline tähtaeg`,
`Jõustumine` and `Töövõit`, each recorded as an exact day, a month, a quarter
and a year, through the real form and the real endpoint. For every cell it
asserts three things that have to hold together — what was stored, what the
period runs to, and what the page then *says* — because storing
``2026-10-01`` + ``QUARTER`` correctly and then printing ``01.10.2026`` is the
same defect arriving one layer later.

Beside the matrix, the four rules that are easy to get right in the create path
and wrong everywhere else:

* an edit keeps the precision it is shown, and a stored `HALF_YEAR` survives
  somebody fixing a typo in the sentence beside it (§9);
* a commencement recorded to a quarter stores a quarter's worth of days, not a
  period that ends on its own first day (§13);
* a new `Töövõit` is asked for its period and nothing is defaulted, while an
  existing undated row keeps none (§10);
* an approximate anchor that collides with an `Arvamuse tähtaeg` does not
  silence the official obligation (§12).

Not held here: whether an approximate step is *late*, which is
``tests/test_approximate_lateness.py``'s subject and shipped ahead of this.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _post(client, route, matter, payload):
    return client.post(
        reverse(route, kwargs={"pk": matter.pk}),
        dict(payload),
        headers={"HX-Request": "true"},
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _panel(body: str, panel_id: str) -> str:
    """One launcher panel's markup, from its own id to the end of its form.

    Sliced rather than parsed, and bounded on both sides: an assertion made
    against the whole page passes because *some other panel* satisfies it, which
    is exactly the mistake a four-panel feature invites.
    """
    section = body[body.index(f'id="{panel_id}"') :]
    return section[: section.index("</form>")]


#: ``(precision, the fields that precision asks for, anchor, period end, how it
#: reads)`` — the four answers a person may give, once, for every surface.
#:
#: The anchors are deliberately the awkward ones: a quarter and a year whose
#: first day is *not* the day anybody would have typed, so a surface that fell
#: back to rendering the anchor prints something visibly wrong rather than
#: something plausible.
PERIODS = [
    (
        DatePrecision.EXACT,
        {"date": "15.10.2026"},
        datetime.date(2026, 10, 15),
        datetime.date(2026, 10, 15),
        "15.10.2026",
    ),
    (
        DatePrecision.MONTH,
        {"month": "10", "year": "2026"},
        datetime.date(2026, 10, 1),
        datetime.date(2026, 10, 31),
        "oktoober 2026",
    ),
    (
        DatePrecision.QUARTER,
        {"quarter": "4", "year": "2026"},
        datetime.date(2026, 10, 1),
        datetime.date(2026, 12, 31),
        "IV kvartal 2026",
    ),
    (
        DatePrecision.YEAR,
        {"year": "2027"},
        datetime.date(2027, 1, 1),
        datetime.date(2027, 12, 31),
        "2027",
    ),
]

PERIOD_IDS = [str(precision) for precision, _, _, _, _ in PERIODS]


def _fields(prefix: str, answers: dict[str, str], *, date_field: str | None = None) -> dict:
    out = {}
    for key, value in answers.items():
        out[date_field if key == "date" and date_field else f"{prefix}_{key}"] = value
    return out


# ===========================================================================
# A — Järgmine tegevus
# ===========================================================================


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "end", "reads"), PERIODS, ids=PERIOD_IDS
)
def test_a_next_action_is_recorded_at_the_precision_it_was_stated(
    signed_in, normal_matter, precision, answers, anchor, end, reads
):
    """§16, §17. What is stored, and what the Teema page then says about it.

    ``end`` is not stored on a `NextAction` and is not asserted here: the anchor
    and the precision determine it, which is why docs/adr/0079 §13 refused to
    add a column for it.
    """
    response = _post(
        signed_in,
        "matters:set_action",
        normal_matter,
        {
            "text": "Koosta arvamus",
            "next_precision": precision,
            **_fields("next", answers, date_field="target_date"),
        },
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.target_date == anchor
    assert action.date_precision == precision
    assert action.kind == ActionKind.DO
    assert action.date_semantics == DateSemantics.DEADLINE
    assert action.display_date == reads

    body = _detail(signed_in, normal_matter)
    # PRAEGUNE TEGEVUS prints the date and not the word: the step's own heading
    # already says what the date is for, and `date_label` — *Plaanis* — belongs
    # to the surfaces that show a step out of context, the register row and the
    # work lists (ADR 0054 §Amendment).
    assert reads in body
    if precision != DatePrecision.EXACT:
        # The anchor, spelled as a day, on the surface that shows the step.
        # This is the assertion the whole feature exists for: `01.10.2026`
        # must not appear anywhere for a record that says *IV kvartal 2026*.
        assert anchor.strftime("%d.%m.%Y") not in body


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "end", "reads"), PERIODS, ids=PERIOD_IDS
)
def test_editing_an_open_step_can_state_any_of_the_four(
    signed_in, normal_matter, specialist, precision, answers, anchor, end, reads
):
    """§16. Precision on create and lost on edit would be worse than neither.

    `Muuda` posts to the same endpoint through the same form, so this is the
    same save — which is exactly why it is asserted separately: the *service*
    supersedes rather than updates, and a precision dropped on the way into the
    replacement would look like an edit that silently changed the date.
    """
    set_next_action(
        matter=normal_matter,
        text="Vana tekst",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=datetime.date(2026, 3, 3),
        actor=specialist,
    )

    response = _post(
        signed_in,
        "matters:set_action",
        normal_matter,
        {
            "text": "Uus tekst",
            "next_precision": precision,
            **_fields("next", answers, date_field="target_date"),
        },
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.text == "Uus tekst"
    assert action.target_date == anchor
    assert action.date_precision == precision
    assert action.display_date == reads


def test_a_next_action_still_refuses_to_be_dateless(signed_in, normal_matter):
    """The invariant ADR 0052 §5 set, through the new control.

    A `DO` with a `DEADLINE` and no date cannot be met, missed or planned
    against. Adding three more ways to state a date did not add a way to state
    none.
    """
    response = _post(
        signed_in,
        "matters:set_action",
        normal_matter,
        {"text": "Koosta arvamus", "next_precision": DatePrecision.MONTH, "next_month": "10"},
    )

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_nothing_is_filed_for_today_when_the_period_is_missing(signed_in, normal_matter):
    """§27. An unknown date is not the current date, on any of the four chips."""
    for precision in (DatePrecision.MONTH, DatePrecision.QUARTER, DatePrecision.YEAR):
        response = _post(
            signed_in,
            "matters:set_action",
            normal_matter,
            {"text": "Koosta arvamus", "next_precision": precision},
        )
        assert response.status_code == 400, precision
    assert not NextAction.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# B — historical precisions survive an edit that was not about them
# ===========================================================================


def _historical(matter, actor, precision, anchor):
    return set_next_action(
        matter=matter,
        text="Vana samm",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=anchor,
        date_precision=precision,
        actor=actor,
    )


@pytest.mark.parametrize(
    ("precision", "anchor", "reads"),
    [
        (DatePrecision.HALF_YEAR, datetime.date(2027, 7, 1), "II poolaasta 2027"),
        (DatePrecision.INFERRED, datetime.date(2026, 11, 20), "20.11.2026"),
    ],
)
def test_an_unoffered_precision_is_offered_back_as_itself(
    signed_in, normal_matter, specialist, precision, anchor, reads
):
    """§18, §9. The editor shows what it will keep, rather than hiding it.

    A record the control cannot otherwise express earns a fifth chip naming the
    period it holds, selected. The alternative — a hidden field carrying the old
    value — keeps the data and tells the reader nothing, and a reader who cannot
    see what a control will do cannot be said to have chosen it.
    """
    _historical(normal_matter, specialist, precision, anchor)

    body = _detail(signed_in, normal_matter)

    assert f"Muutmata: {reads}" in body
    # And never as a new-entry option: the chip exists because this record has
    # it, not because the vocabulary does.
    assert "Poolaasta täpsusega" not in body


@pytest.mark.parametrize(
    ("precision", "anchor"),
    [
        (DatePrecision.HALF_YEAR, datetime.date(2027, 7, 1)),
        (DatePrecision.INFERRED, datetime.date(2026, 11, 20)),
    ],
)
def test_editing_only_the_text_keeps_a_precision_the_control_cannot_offer(
    signed_in, normal_matter, specialist, precision, anchor
):
    """§18. Fixing a typo is not a decision about the date.

    Turning *II poolaasta 2027* into `01.07.2027` because another field was
    edited is the product rewriting a record nobody asked it to — and it would
    do it silently, on a surface that shows no date control for the value it
    just replaced.
    """
    _historical(normal_matter, specialist, precision, anchor)

    response = _post(
        signed_in,
        "matters:set_action",
        normal_matter,
        {"text": "Parandatud tekst", "next_precision": precision},
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.text == "Parandatud tekst"
    assert action.date_precision == precision
    assert action.target_date == anchor


def test_choosing_an_offered_precision_replaces_a_historical_one(
    signed_in, normal_matter, specialist
):
    """The other half of §18: kept is the default, not a trap.

    A person who actually means «now it is October» must be able to say so, and
    the chip they choose wins.
    """
    _historical(normal_matter, specialist, DatePrecision.HALF_YEAR, datetime.date(2027, 7, 1))

    response = _post(
        signed_in,
        "matters:set_action",
        normal_matter,
        {
            "text": "Vana samm",
            "next_precision": DatePrecision.MONTH,
            "next_month": "10",
            "next_year": "2026",
        },
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.date_precision == DatePrecision.MONTH
    assert action.target_date == datetime.date(2026, 10, 1)


def test_a_crafted_post_cannot_name_a_precision_the_record_does_not_have(
    signed_in, normal_matter, specialist
):
    """The chip is built from the record, so the field refuses what it lacks.

    Without this the preservation mechanism would be a door: any POST naming
    `HALF_YEAR` would store one, on a surface that offers no way to choose it
    and therefore no way to notice it (ADR 0052 §4's rule, kept).
    """
    _historical(normal_matter, specialist, DatePrecision.MONTH, datetime.date(2026, 10, 1))

    response = _post(
        signed_in,
        "matters:set_action",
        normal_matter,
        {"text": "Uus tekst", "next_precision": DatePrecision.HALF_YEAR},
    )

    assert response.status_code == 400
    action = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert action.date_precision == DatePrecision.MONTH


# ===========================================================================
# C — Oluline tähtaeg
# ===========================================================================


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "end", "reads"), PERIODS, ids=PERIOD_IDS
)
def test_an_important_deadline_stores_the_whole_period(
    signed_in, normal_matter, precision, answers, anchor, end, reads
):
    """§19. Anchor, end and precision, and no `NextAction` anywhere near it."""
    response = _post(
        signed_in,
        "matters:add_important_date",
        normal_matter,
        {
            "deadline_title": "Kooskõlastusringi lõpp",
            "deadline_precision": precision,
            **_fields("deadline", answers),
        },
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterImportantDate.objects.get(matter=normal_matter)
    assert (record.date_value, record.period_end) == (anchor, end)
    assert record.date_precision == precision
    assert record.display_date == reads
    assert not NextAction.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# D — Jõustumine
# ===========================================================================


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "end", "reads"), PERIODS, ids=PERIOD_IDS
)
def test_a_commencement_stores_a_period_that_is_not_its_own_first_day(
    signed_in, normal_matter, precision, answers, anchor, end, reads
):
    """§20, and the defect it names.

    `add_matter_effective_date` passed ``period_end=date_value``. That is right
    for a day and wrong for everything else: a commencement recorded as *IV
    kvartal 2026* would have been stored as a period ending on 1 October, so
    `has_passed` — and the *Jõustuvad aktid* rail that reads it — would have
    called a three-month commencement over on its first day.
    """
    response = _post(
        signed_in,
        "matters:add_effective_date",
        normal_matter,
        {
            "effective_title": "Pakendiseaduse muudatused",
            "effective_precision": precision,
            **_fields("effective", answers, date_field="effective_on"),
        },
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterEffectiveDate.objects.get(matter=normal_matter)
    assert (record.date_value, record.period_end) == (anchor, end)
    assert record.date_precision == precision
    assert record.display_date == reads
    assert record.has_passed(anchor) is False
    # The end is the last day it covers, and the day after it is past.
    assert record.has_passed(end + datetime.timedelta(days=1)) is True


def test_an_approximate_commencement_is_never_stored_as_a_single_day(signed_in, normal_matter):
    """The constraint behind §20, stated as a rule rather than a case.

    A `QUARTER` or `YEAR` row whose `period_end` equals its anchor claims, to
    everything that reads this table, that a three-month or twelve-month
    commencement was over on day one.
    """
    for precision, answers, _, _, _ in PERIODS:
        MatterEffectiveDate.objects.filter(matter=normal_matter).delete()
        _post(
            signed_in,
            "matters:add_effective_date",
            normal_matter,
            {
                "effective_title": "Osad sätted",
                "effective_precision": precision,
                **_fields("effective", answers, date_field="effective_on"),
            },
        )
        record = MatterEffectiveDate.objects.get(matter=normal_matter)
        if precision != DatePrecision.EXACT:
            assert record.period_end > record.date_value, precision


# ===========================================================================
# E — Töövõit
# ===========================================================================


def test_a_work_victory_records_the_day_it_was_won_on(signed_in, normal_matter):
    """§21, narrowed by the owner. The period is one day, stated by the person.

    This was parametrised over all four precisions, and the panel offered all
    four. A töövõit is something Koda *achieved* and the organisation should be
    able to say when it happened, so the group went and `Millal` is one
    clearable box holding today (docs/adr/0097 §7).

    The *record* is unchanged: a day is stored as the period it is —
    `period_date` and `period_end` the same date under `EXACT` — and the
    reporting still reads periods. What narrowed is what this panel can
    produce, and the four-precision contract lives on where it is still
    offered, which is `Muuda` on a stored row.
    """
    response = _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati", "victory_date": "19.09.2026"},
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    record = MatterWorkVictory.objects.get(matter=normal_matter)
    won = datetime.date(2026, 9, 19)
    assert (record.period_date, record.period_end) == (won, won)
    assert record.date_precision == DatePrecision.EXACT
    assert record.status == WorkVictoryStatus.CONFIRMED


def test_a_new_work_victory_without_a_period_is_refused(signed_in, normal_matter):
    """§21. Not defaulted to today, not defaulted to this year, not stored blank.

    The panel used to send no period at all, so every win recorded from it
    reached the database with `period_date` NULL — invisible to
    `?toovoit=<aasta>` and to the reporting rail, and indistinguishable from an
    imported row whose period genuinely is unknown.
    """
    response = _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Üleminekuaeg pikendati", "victory_date": ""},
    )

    assert response.status_code == 400
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()


def test_the_work_victory_date_box_is_prefilled_with_today(signed_in, normal_matter):
    """This reverses §21, §27, and the owner gave the reason.

    The box stayed blank because `Täpne päev` was merely the first of four
    chips, and a pre-filled today underneath a precision nobody had chosen
    would have been a claim nobody made. There is no precision to choose now:
    the panel asks for the day a win was achieved, which is nearly always the
    day it is written up, and the default is visible in the box where it can be
    read, changed and emptied — the one shape docs/adr/0078 §2 allows a date
    default to take (docs/adr/0097 §7).
    """
    body = _detail(signed_in, normal_matter)
    today = datetime.date.today()

    panel = _panel(body, "marge-toovoit")

    assert f'value="{today.day}.{today.month}.{today.year}"' in panel
    assert 'name="victory_precision"' not in panel


def test_an_existing_undated_work_victory_is_left_exactly_as_it_is(
    signed_in, normal_matter, specialist
):
    """§21, §25. No backfill, no guessed year, and it still reads.

    A win whose period was never recorded does not acquire one because the form
    learned to ask. *Teadmata periood* is a true statement about a historical
    row and a false one about a row somebody filed this morning; this round
    fixes the second without rewriting the first.
    """
    record = factories.WorkVictoryFactory(
        matter=normal_matter,
        title="Ajalooline võit",
        period_date=None,
        period_end=None,
        status=WorkVictoryStatus.CONFIRMED,
        # A confirmed row records when it was confirmed — the database refuses
        # one that does not (`intelligence_work_victory_confirmed_has_timestamp`).
        # That is a different fact from the business period, which is exactly
        # the distinction this test is about.
        confirmed_at=timezone.now(),
    )

    record.refresh_from_db()
    assert record.period_date is None
    assert record.period_end is None
    assert record.has_period is False
    assert record.display_period == ""


# ===========================================================================
# F — the response obligation an approximate anchor must not silence
# ===========================================================================


@pytest.mark.parametrize(
    ("precision", "answers", "anchor", "reads"),
    [
        (
            DatePrecision.MONTH,
            {"month": "10", "year": "2026"},
            datetime.date(2026, 10, 1),
            "oktoober 2026",
        ),
        (
            DatePrecision.QUARTER,
            {"quarter": "4", "year": "2026"},
            datetime.date(2026, 10, 1),
            "IV kvartal 2026",
        ),
        (DatePrecision.YEAR, {"year": "2027"}, datetime.date(2027, 1, 1), "2027"),
    ],
)
def test_an_approximate_anchor_does_not_hide_the_official_deadline(
    signed_in, specialist, precision, answers, anchor, reads
):
    """§23, docs/adr/0079 §12. Two facts that share one internal number.

    PR #208 suppresses the secondary `Arvamuse tähtaeg` line when the surface's
    primary date already *is* that deadline — correctly, because printing the
    same day twice is the first fact stuttering rather than a second one.

    An approximate step breaks the assumption underneath it. *IV kvartal 2026*
    anchors on 1 October, so a Matter whose response deadline is 1 October makes
    the two values equal while the two facts stay a quarter apart. The anchor is
    not something the reader can see and not something anybody chose; erasing an
    official obligation on the strength of it would be this product hiding what
    Koda owes because of an implementation detail.
    """
    matter = factories.MatterFactory(
        owner=specialist,
        title="Kollisiooniga teema",
        reference_year=2026,
        reference_number=811,
        response_deadline=anchor,
    )

    response = _post(
        signed_in,
        "matters:set_action",
        matter,
        {
            "text": "Koosta arvamus",
            "next_precision": precision,
            **_fields("next", answers, date_field="target_date"),
        },
    )
    assert response.status_code == 200, response.content.decode()[:2000]

    body = _detail(signed_in, matter)
    assert reads in body
    assert "Arvamuse tähtaeg" in body


def test_an_exact_duplicate_is_still_suppressed(signed_in, specialist):
    """The behaviour #208 shipped, unchanged.

    Widening the rule for approximate dates must not widen it for exact ones:
    *Plaanis 01.10.2026* over *Arvamuse tähtaeg 01.10* is still one fact printed
    twice.
    """
    deadline = datetime.date(2026, 10, 1)
    matter = factories.MatterFactory(
        owner=specialist,
        title="Täpse kollisiooniga teema",
        reference_year=2026,
        reference_number=812,
        response_deadline=deadline,
    )

    from app.matters import work_items

    exact = work_items.secondary_response_obligation(
        matter, specialist, primary_date=deadline, primary_is_approximate=False
    )
    approximate = work_items.secondary_response_obligation(
        matter, specialist, primary_date=deadline, primary_is_approximate=True
    )

    assert exact is None
    assert approximate is not None


# ===========================================================================
# G — the control itself
# ===========================================================================


def test_the_precision_control_is_a_real_radio_group_on_every_panel(signed_in, normal_matter):
    """§6, §30. The precision can be stated with scripting off.

    The control this replaces was `<button type=button>` writing into a hidden
    input: with JavaScript off the buttons did nothing and the hidden field kept
    whatever it already held, so a reader without scripting could record only an
    exact day. A radio group arrives in the POST on its own.

    Asserted as a count per panel rather than by rendering one: the point is
    that all four panels got the same control, which is what a shared partial
    is for.
    """
    body = _detail(signed_in, normal_matter)

    # `marge-toovoit` is not among them since docs/adr/0097 §7: that panel asks
    # for a day. The shared partial's contract is what this asserts, and it is
    # asserted on the panels that draw it.
    for panel, field in (
        ("marge-tahtaeg", "deadline_precision"),
        ("marge-joustumine", "effective_precision"),
    ):
        section = _panel(body, panel)
        pattern = rf'<input[^>]*type="radio"[^>]*name="{field}"[^>]*>'
        radios = re.findall(pattern, section, re.S)
        assert len(radios) == 4, f"{panel}: {len(radios)} precision radios"
        assert all("precision__radio" in radio for radio in radios), panel
        chips = section[section.index("precision__chips") : section.index("</fieldset>")]
        assert 'type="hidden"' not in chips, f"{panel} still carries a hidden precision"


def test_the_four_labels_are_the_four_things_somebody_knows(signed_in, normal_matter):
    """§3, §30. And `Poolaasta` is not among them."""
    body = _detail(signed_in, normal_matter)

    for label in ("Täpne päev", "Kuu", "Kvartal", "Aasta"):
        assert label in body
    assert "Poolaasta" not in body
    assert "Tuletatud tekstist" not in body


def test_a_refused_period_comes_back_carrying_what_was_chosen(signed_in, normal_matter):
    """§26. The refusal keeps the precision and the values, and resets nothing.

    A control that answered a refusal by dropping back to `Täpne päev` would
    make the person state their answer twice — and the second time, under a
    chip they did not pick.
    """
    response = _post(
        signed_in,
        "matters:add_important_date",
        normal_matter,
        {
            "deadline_title": "",
            "deadline_precision": DatePrecision.QUARTER,
            "deadline_quarter": "4",
            "deadline_year": "2026",
        },
    )

    assert response.status_code == 400
    panel = _panel(response.content.decode(), "marge-tahtaeg")
    chosen = re.search(r'<input[^>]*value="QUARTER"[^>]*>', panel, re.S)
    assert chosen is not None, "the chosen precision is gone from the refusal"
    assert "checked" in chosen.group(0), "the refusal reset the chips to Täpne päev"
    assert 'value="2026"' in panel
    assert re.search(r'<option[^>]*value="4"[^>]*selected', panel) is not None


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"deadline_precision": "MONTH", "deadline_month": "13", "deadline_year": "2026"}, "kuu"),
        (
            {"deadline_precision": "QUARTER", "deadline_quarter": "5", "deadline_year": "2026"},
            "kvartal",
        ),
        ({"deadline_precision": "YEAR", "deadline_year": "20226"}, "aasta"),
        ({"deadline_precision": "YEAR", "deadline_year": "1200"}, "aasta"),
        ({"deadline_precision": "MONTH", "deadline_month": "10"}, "aasta"),
    ],
)
def test_an_impossible_period_is_refused_rather_than_normalised(
    signed_in, normal_matter, payload, field
):
    """§26. Quarter V does not exist, and the year 20226 is a typed 2026.

    The ranges are `app.workflow.dates`'s and are not restated in the form — a
    second copy of «1990 to 2100» is a second answer to what a year may be.
    """
    response = _post(
        signed_in,
        "matters:add_important_date",
        normal_matter,
        {"deadline_title": "Kooskõlastusringi lõpp", **payload},
    )

    assert response.status_code == 400, field
    assert not MatterImportantDate.objects.filter(matter=normal_matter).exists()


def test_one_partial_serves_every_panel(signed_in, normal_matter):
    """§5. One period composer, not four that drift.

    A structural assertion rather than a visual one: every panel that asks for
    a period rendering the same `data-precision` fieldset is what makes «a
    quarter is a quarter» true by construction instead of by several people
    remembering.

    Two panels draw it now — `Oluline tähtaeg` and `Jõustumine`. `Töövõit`
    stopped asking for a period and asks for a day (docs/adr/0097 §7), and the
    external-position panels stopped asking before that (docs/adr/0095). The
    claim is unchanged; the number is what the product has.
    """
    body = _detail(signed_in, normal_matter)

    assert body.count("data-precision") >= 2
    assert body.count('data-precision-for="quarter"') >= 2


def test_uus_teema_keeps_the_narrower_contract_it_had(signed_in):
    """The control exists where it is rendered, and nowhere else.

    ADR 0052 §4 deleted the next step's precision group *rather than hiding it*,
    because a control the page does not have must not be reachable through a
    crafted POST either. That rule outlives the decision it was written for:
    Uus teema asks for a title, an owner and a first step on one screen and does
    not offer four precision chips inside that, so the fields are absent there
    and a POST naming one changes nothing.
    """
    from app.matters.forms import NextActionForm

    assert set(NextActionForm(prefix="next").fields) == {"text", "target_date", "responsible"}
    assert "next_precision" in NextActionForm(periods=True).fields

    crafted = NextActionForm(
        {
            "next-text": "Koosta arvamus",
            "next-next_precision": DatePrecision.MONTH,
            "next-next_month": "10",
            "next-next_year": "2026",
        },
        prefix="next",
    )

    # **The crafted keys reach nothing, which is still the whole claim.** What
    # the assertion can no longer be is «the form refuses»: since docs/adr/0106 a
    # step with no day is an ordinary save, so this POST is valid *and* stores
    # nothing the crafted fields named. That is the stronger reading of ADR 0052
    # §4's rule — a control the page does not have is not merely refused, it is
    # absent — and it is what a `hidden` field would have failed.
    assert crafted.is_valid() is True, crafted.errors
    assert crafted.as_service_kwargs()["target_date"] is None
    assert crafted.as_service_kwargs()["date_precision"] == DatePrecision.EXACT


def test_the_quick_spans_belong_to_the_next_step_and_to_nothing_else(signed_in, normal_matter):
    """`Täna` / `Homme` / `+1 nädal` are a *next step*'s shortcuts.

    A Django `{% include %}` adds to the parent context rather than replacing
    it, so `quick_dates` — put there for `Järgmine tegevus` — reached the other
    three panels through the shared partial and wired four day-shortcut buttons
    to write into each of *their* date boxes. A milestone somebody else
    announced is not «homme», and a commencement certainly is not.

    The fix is `only` on every include; this is the assertion that keeps it,
    because the defect is invisible in the markup that causes it.
    """
    body = _detail(signed_in, normal_matter)

    for panel in ("marge-tahtaeg", "marge-joustumine", "marge-toovoit"):
        section = _panel(body, panel)
        assert "data-quickdate" not in section, f"{panel} offers day shortcuts"
        for label in ("Homme", "+1 nädal", "+2 nädalat"):
            assert label not in section, f"{panel} offers «{label}»"
