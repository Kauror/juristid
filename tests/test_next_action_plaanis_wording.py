"""A lawyer's own `Järgmiseks` date is a *plan*, not a *tähtaeg*.

What this file is for
---------------------
This product has three different dates and used to print one word over all
three. `Arvamuse tähtaeg` is what Koda owes an outside body; `Oluline tähtaeg`
is a watched milestone somebody outside would notice; and the date on an open
`Järgmiseks` step is when its author intends to do the thing, which they may
move again this afternoon without telling anybody. Calling the third a *tähtaeg*
made the first two indistinguishable from it (docs/adr/0054 §Amendment).

Two things this suite is careful about, because both are how the same fix goes
wrong:

* **Storage did not move.** `DateSemantics.DEADLINE` is still stored, is still
  the only semantics a `DO` may carry into overdue, and is still labelled
  *Tähtaeg* in Django's own vocabulary. Every assertion that a word changed is
  paired with one that reads the record back out of the database, so a later
  change that renamed the enum to make the display right would fail here.
* **A mixed population keeps the word.** *Üle tähtaja*, *Tähtaeg sel nädalal*
  and the rest genuinely hold `Oluline tähtaeg` and `Arvamuse tähtaeg` rows as
  well as self-set ones. Renaming those would have been a string search rather
  than a decision, so this file pins them as deliberately unchanged — and pins
  the mixing itself, because a label test over a population that turned out to
  hold only self-set plans would be holding a defect in place.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.services import add_important_date
from app.matters import overview as ov
from app.matters import selectors
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.register_dates import RESPONSE_DEADLINE_LABEL, register_date
from app.matters.services import create_matter
from app.matters.views import NEXT_ACTION_LABELS
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

REGISTER = "/teemad/"
DEPARTMENT = "/osakond/"

#: What a self-set date reads as, in the two casings the product uses: title
#: case in the register's Kuupäev cell, upper case on a work row.
PLAANIS = "Plaanis"
PLAANIS_CAPS = "PLAANIS"

#: The word that must no longer describe one, in both of those casings.
TAHTAEG = "Tähtaeg"
TAHTAEG_CAPS = "TÄHTAEG"

STEP_TEXT = "Vaata uus eelnõu üle"


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def planned(specialist, today):
    """A native `Järgmiseks`: exactly what the Teema composer creates.

    `DO` + `DEADLINE` + `EXACT`, which is the one combination that can go
    overdue and the one this wording decision is about.
    """
    matter = create_matter(title="Plaaniga teema", owner=specialist, reference_year=2026)
    return set_next_action(
        matter=matter,
        text=STEP_TEXT,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=6),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )


def body_of(response) -> str:
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def date_meanings(body: str) -> list[str]:
    """The register's Kuupäev cells, as the words they actually rendered.

    Scoped to the cell rather than searched for in the page. `"Plaanis" in body`
    proves nothing: the register also carries `?tahtaeg_alates=` and the
    *Tähtaeg sel kuul* saved view, both about `Arvamuse tähtaeg`, so a
    whole-page search for either word answers the wrong question.
    """
    return re.findall(r'<span class="datemeaning">([^<]*)</span>', body)


def work_meanings(body: str) -> list[str]:
    return re.findall(r'<span class="workrow2__meaning">([^<]*)</span>', body)


def with_prefetch(user, matter_pk) -> Matter:
    """One Matter carrying the prefetch `register_date` insists on."""
    return (
        Matter.objects.visible_to(user)
        .prefetch_related(selectors.open_action_prefetch(user))
        .get(pk=matter_pk)
    )


def item_for(user, action, today) -> wi.WorkItem:
    items = wi.work_items(user, today=today)
    return next(item for item in items if item.is_action and item.object_id == action.pk)


# ---------------------------------------------------------------------------
# A. Teemad — the Kuupäev cell
# ---------------------------------------------------------------------------


def test_the_register_date_cell_calls_a_self_set_date_plaanis(specialist, planned, today):
    shown = register_date(with_prefetch(specialist, planned.matter_id), today)

    assert shown is not None
    assert shown.meaning == PLAANIS
    assert shown.value == planned.target_date
    # Paired with the storage read on purpose: renaming the enum would satisfy
    # the assertion above and break this one.
    stored = NextAction.objects.get(pk=planned.pk)
    assert stored.date_semantics == DateSemantics.DEADLINE
    assert stored.kind == ActionKind.DO


def test_the_rendered_register_row_says_plaanis_and_not_tahtaeg(signed_in, planned):
    meanings = date_meanings(body_of(signed_in.get(REGISTER)))

    assert PLAANIS in meanings
    assert TAHTAEG not in meanings


# ---------------------------------------------------------------------------
# B. Minu asjad
# ---------------------------------------------------------------------------


def test_a_minu_asjad_work_row_reads_plaanis(specialist, planned, today):
    item = item_for(specialist, planned, today)

    assert item.meaning == PLAANIS_CAPS
    assert item.meaning_line == PLAANIS_CAPS
    assert NextAction.objects.get(pk=planned.pk).date_semantics == DateSemantics.DEADLINE


def test_the_rendered_minu_asjad_row_says_plaanis(signed_in, planned):
    meanings = work_meanings(body_of(signed_in.get(reverse("matters:my_work"))))

    assert PLAANIS_CAPS in meanings
    for meaning in meanings:
        assert not meaning.startswith(TAHTAEG_CAPS), meaning


def test_the_portfolio_line_names_the_plan_in_lower_case(signed_in, specialist, planned, today):
    """`portfolio_row.html` prints the same meaning through `|lower`."""
    page = build_my_work(specialist, today=today)
    row = next(row for row in page.portfolio.rows if row.matter.pk == planned.matter_id)

    assert row.action is not None
    assert row.action.meaning.lower() == "plaanis"
    assert f"{STEP_TEXT} · plaanis" in body_of(signed_in.get(reverse("matters:my_work")))


# ---------------------------------------------------------------------------
# C. Osakond — the same row, rendered for somebody else's work
# ---------------------------------------------------------------------------


def test_a_shared_work_row_does_not_show_tahtaeg_as_its_meaning(
    client, department_head, specialist, planned
):
    """A colleague's desk renders `work_item_row.html` for the same NextAction."""
    client.force_login(department_head)
    url = reverse("matters:person_work", kwargs={"pk": specialist.pk})
    meanings = work_meanings(body_of(client.get(url)))

    assert PLAANIS_CAPS in meanings
    for meaning in meanings:
        assert not meaning.startswith(TAHTAEG_CAPS), meaning


def test_the_intervention_row_for_a_late_plan_reads_plaanis(specialist, today):
    """Osakond's «Vajab sekkumist» carries the meaning and the day it was."""
    matter = create_matter(title="Hilinenud plaan", owner=specialist, reference_year=2026)
    set_next_action(
        matter=matter,
        text="Saada arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=4),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )

    items = wi.work_items(specialist, today=today)
    rows = ov.intervention_rows(specialist, today, items)
    row = next(row for row in rows if row.matter.pk == matter.pk)

    assert row.meaning.startswith(PLAANIS_CAPS)
    assert TAHTAEG_CAPS not in row.meaning


def test_the_department_page_never_calls_a_self_set_date_a_tahtaeg(
    client, department_head, specialist, today
):
    """Every intervention meaning on `/osakond/`, read as rendered.

    The two remaining `TÄHTAEG` spellings a row may legitimately carry are
    `OLULINE TÄHTAEG` and `ARVAMUSE TÄHTAEG`, so the assertion is about the
    bare word standing on its own.
    """
    matter = create_matter(title="Osakonna hilinenud plaan", owner=specialist, reference_year=2026)
    set_next_action(
        matter=matter,
        text="Koosta vastus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=2),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )
    client.force_login(department_head)
    body = body_of(client.get(DEPARTMENT))
    meanings = re.findall(r'<span class="interrow__meaning">([^<]*)</span>', body)

    assert any(meaning.startswith(PLAANIS_CAPS) for meaning in meanings), meanings
    for meaning in meanings:
        assert not meaning.startswith(TAHTAEG_CAPS), meaning


# ---------------------------------------------------------------------------
# D. The next-action-specific overdue filter
# ---------------------------------------------------------------------------


def test_the_next_action_overdue_filter_is_called_ule_aja():
    """`?tegevus=hilinenud` selects one thing, and must not name it a tähtaeg."""
    assert NEXT_ACTION_LABELS["hilinenud"] == "Üle aja"
    assert "Tähtaeg möödas" not in NEXT_ACTION_LABELS.values()


def test_that_filter_still_selects_exactly_a_late_self_set_plan(specialist, today):
    """The wording changed; the condition did not (`_open_action_condition`)."""
    late = create_matter(title="Üle aja teema", owner=specialist, reference_year=2026)
    set_next_action(
        matter=late,
        text="Ammu tehtud",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=3),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )
    review = create_matter(title="Ülevaatuse teema", owner=specialist, reference_year=2026)
    set_next_action(
        matter=review,
        text="vaata üle",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - timedelta(days=3),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )

    selected = selectors.filter_by_next_action(
        Matter.objects.visible_to(specialist), specialist, "hilinenud", today
    )

    assert set(selected.values_list("pk", flat=True)) == {late.pk}


def test_the_rendered_filter_control_offers_ule_aja(signed_in, planned):
    body = body_of(signed_in.get(REGISTER))

    assert "Üle aja" in body
    assert "Tähtaeg möödas" not in body


# ---------------------------------------------------------------------------
# E-F. The two dates that genuinely are deadlines
# ---------------------------------------------------------------------------


def test_arvamuse_tahtaeg_keeps_its_name_on_the_register_row(specialist, today):
    """A Matter with no dated step falls through to its own response deadline."""
    matter = create_matter(
        title="Tähtajaga teema",
        owner=specialist,
        reference_year=2026,
        response_deadline=today + timedelta(days=10),
    )

    shown = register_date(with_prefetch(specialist, matter.pk), today)

    assert shown is not None
    assert shown.meaning == RESPONSE_DEADLINE_LABEL == "Arvamuse tähtaeg"


def test_arvamuse_tahtaeg_keeps_its_name_as_work(specialist, today):
    matter = create_matter(
        title="Tööna loetav tähtaeg",
        owner=specialist,
        reference_year=2026,
        response_deadline=today + timedelta(days=3),
    )
    items = wi.work_items(specialist, today=today)
    item = next(item for item in items if item.matter_id == matter.pk)

    assert item.meaning == wi.MEANING_RESPONSE == "ARVAMUSE TÄHTAEG"


def test_oluline_tahtaeg_keeps_its_name(specialist, today):
    matter = create_matter(title="Olulise tähtajaga teema", owner=specialist, reference_year=2026)
    when = today + timedelta(days=5)
    add_important_date(
        matter=matter,
        title="Riigikogu teine lugemine",
        date_value=when,
        period_end=when,
        actor=specialist,
    )
    items = wi.work_items(specialist, today=today)
    item = next(item for item in items if item.matter_id == matter.pk)

    assert item.meaning == wi.MEANING_IMPORTANT == "OLULINE TÄHTAEG"


def test_the_teema_header_deadline_is_still_arvamuse_tahtaeg(signed_in, specialist, today):
    """The metaline reads `Tähtaeg`, and it means Koda's own opinion deadline.

    ADR 0050 and ADR 0059 both rest on that reading, so this is the one place
    the word must survive a change whose whole subject is the word.
    """
    matter = create_matter(
        title="Päisega teema",
        owner=specialist,
        reference_year=2026,
        response_deadline=today + timedelta(days=8),
    )
    body = body_of(signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))

    assert '<span class="metaline__label">Tähtaeg</span>' in body


# ---------------------------------------------------------------------------
# G. Historical semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "semantics", "label", "meaning"),
    (
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON, "Vaatan üle", "VAATAN ÜLE"),
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND, "Oodatav", "OODATAV AEG"),
    ),
)
def test_a_historical_semantics_keeps_its_words(specialist, today, kind, semantics, label, meaning):
    """Imported `REVIEW_ON` and `EXPECTED_AROUND` rows are read back unchanged."""
    matter = create_matter(title=f"Ajalooline {semantics}", owner=specialist, reference_year=2026)
    action = set_next_action(
        matter=matter,
        text="vaata rakendusaktid üle",
        kind=kind,
        date_semantics=semantics,
        target_date=today + timedelta(days=4),
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )

    assert action.date_label == label
    assert item_for(specialist, action, today).meaning == meaning


def test_the_stored_vocabulary_is_untouched():
    """`Plaanis` is presentation. `DEADLINE` is storage, and still says Tähtaeg.

    Renaming the enum would have been a migration for a word no product screen
    renders, which is exactly what this change refused to do.
    """
    assert DateSemantics.DEADLINE.value == "DEADLINE"
    assert DateSemantics.DEADLINE.label == "Tähtaeg"
    assert DateSemantics.REVIEW_ON.label == "Vaatan üle"
    assert ActionKind.DO.value == "DO"


# ---------------------------------------------------------------------------
# H. Mixed populations keep the word, on purpose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    (
        wi.WORK_OVERDUE,
        wi.WORK_DEADLINE_THIS_WEEK,
        wi.WORK_DEADLINE_NEXT_WEEK,
        wi.WORK_DEADLINE_30_DAYS,
        wi.WORK_DEADLINE_BEYOND,
        wi.WORK_DEADLINE_WINDOW,
    ),
)
def test_a_mixed_deadline_population_keeps_the_word_tahtaeg(key):
    """These headings sit over lists holding all three kinds of date."""
    label = wi.WORK_POPULATION_LABELS[key]

    assert "ähtaeg" in label or "ähtaja" in label, label


def test_the_band_over_a_mixed_list_keeps_its_word():
    assert wi.BAND_LABELS[wi.BAND_OVERDUE] == "Üle tähtaja"


def test_those_populations_really_are_mixed(specialist, today):
    """Which is the only thing that justifies the two tests above.

    Proven rather than asserted about: a population whose rows all came from a
    `NextAction` would have no claim to the word, and pinning its label would
    then be pinning a defect in place.
    """
    plan = create_matter(title="Plaan sel nädalal", owner=specialist, reference_year=2026)
    set_next_action(
        matter=plan,
        text="Koosta arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )
    create_matter(
        title="Arvamus sel nädalal",
        owner=specialist,
        reference_year=2026,
        response_deadline=today,
    )
    milestone = create_matter(title="Verstapost sel nädalal", owner=specialist, reference_year=2026)
    add_important_date(
        matter=milestone,
        title="Teine lugemine",
        date_value=today,
        period_end=today,
        actor=specialist,
    )

    items = wi.work_items(specialist, today=today)
    meanings = {item.meaning for item in wi.real_deadlines(items)}

    assert meanings == {wi.MEANING_DEADLINE, wi.MEANING_RESPONSE, wi.MEANING_IMPORTANT}
    assert wi.MEANING_DEADLINE == PLAANIS_CAPS
