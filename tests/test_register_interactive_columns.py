"""Teemad's column headings filter and sort, and agree with everything else.

Five headings became controls. Hetkeseis, Vastutaja and Järgmiseks open a filter
menu; Kuupäev and Viimane tegevus sort. Nothing else about the register changed:
the state is still the address, the chips above the table are still what says
what is narrowing it, and Täpsem otsing is still the same filter seen from a
different control (docs/adr/0071).

What is worth asserting is therefore mostly *agreement*, and the tests below are
organised around the four places two answers could drift apart.

**A heading and the panel are one filter.** Every menu is built from the same
context list the panel's own select is, so what has to be proved is that the
populations really are the same object and that a filter set from either place
is read back by both.

**The Kuupäev column and the Kuupäev ordering are one rule.** The row shows the
open step's own date when the visible step has one and the Matter's
`Arvamuse tähtaeg` otherwise; the ORDER BY has to make exactly that choice, or
the page shows 12.09, 15.09, 13.09 and calls itself sorted. So the tests here
read **the dates the rows actually render** rather than re-deriving them.

**Viimane tegevus sorts the derived fact, never `updated_at`.** Most of this
register is imported and `updated_at` is the moment the 2026 cutover touched the
row, which is the whole reason the column exists (ADR 0026).

**A restricted child changes nothing.** Not the displayed date, not the row's
position, not the option list. A row this reader may not read is a row that does
not exist for this reader — in both directions, and in the ordering as much as
in the text.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import activity, register_dates, views
from app.matters.models import Matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TODAY = timezone.localdate()


# ---------------------------------------------------------------------------
# Reading the page the way a lawyer does
# ---------------------------------------------------------------------------


def titles_on(response) -> list[str]:
    """The rows, in the order the register put them in."""
    return [matter.title for matter in response.context["page"].object_list]


def body_of(response) -> str:
    assert response.status_code == 200
    return response.content.decode()


#: The register's table head, and nothing else on the page. Sliced rather than
#: searched for: «Vastutaja» and «Kuupäev» are ordinary Estonian and appear in
#: the filter panel, the saved-view chips and the Arvamused section below, so a
#: bare ``in body`` would prove nothing about the heading.
def thead_of(response) -> str:
    body = body_of(response)
    start = body.index('<table class="table table--register">')
    return body[start : body.index("</thead>", start)]


#: One register row's rendered Kuupäev cell. The dates are read back off the
#: page so that an ordering which agrees with a selector but not with the table
#: cannot pass (`e2e/test_register_columns.py` asserts the same thing in a
#: browser).
DATE_CELL = re.compile(
    r'<td class="table__date">.*?(?:<span class="dateline[^"]*">\s*([^<\s][^<]*?)\s*</span>|'
    r'<span class="muted">(—)</span>)',
    re.DOTALL,
)


def dates_rendered(response) -> list[str]:
    """The Kuupäev column, top to bottom, as the reader sees it."""
    body = body_of(response)
    table = body[body.index('<table class="table table--register">') :]
    return [found or dash for found, dash in DATE_CELL.findall(table)]


def last_activity_rendered(response) -> list[str]:
    """The Viimane tegevus column, top to bottom."""
    body = body_of(response)
    table = body[body.index('<table class="table table--register">') :]
    cells = re.findall(r'<td class="table__date table__lastactivity">(.*?)</td>', table, re.DOTALL)
    return [re.sub(r"<[^>]+>", "", cell).strip() or "—" for cell in cells]


def estonian(value) -> str:
    return f"{value.day}.{value.month}.{value.year}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def a_row(db, specialist):
    """One visible row, because an empty register renders no table at all.

    The head is inside the table, so a test about a heading needs something for
    the heading to sit above.
    """
    return factories.MatterFactory(owner=specialist, title="Näidisrida")


@pytest.fixture
def stages(db):
    """Two rows of the seeded vocabulary — used, never invented.

    `StageVocabulary` is reference data. `Matter.stage` points at these rows and
    the list a lawyer sees is the department's own, so a test inventing its own
    two stages would be asserting about a vocabulary the product does not have
    (app/workflow/migrations/0004_seed_stage_vocabulary.py).
    """
    from app.workflow.models import StageVocabulary

    return [
        StageVocabulary.objects.get(key="consultation"),
        StageVocabulary.objects.get(key="parliament"),
    ]


def matter(owner=None, **kwargs) -> Matter:
    return factories.MatterFactory(owner=owner, **kwargs)


def indexed(subject: Matter) -> Matter:
    """Project one Matter into the search index, the way a save would.

    `?q=` reads the search projection, so a test about text *and* a heading has
    to put the rows there — the same helper `tests/test_register_search.py` uses.
    """
    from app.search.indexing import indexable_matters, refresh_matters

    refresh_matters(indexable_matters().filter(pk=subject.pk))
    return subject


def open_action(matter_, *, text="Naidissamm", target_date=None, actor=None, **kwargs):
    from app.workflow.services import set_next_action

    return set_next_action(
        matter=matter_,
        text=text,
        target_date=target_date,
        actor=actor or matter_.owner,
        **kwargs,
    )


# ===========================================================================
# A. The headings exist, and only on Teemad
# ===========================================================================


def test_the_register_head_offers_all_five_controls(signed_in, stages, a_row):
    head = thead_of(signed_in.get(REGISTER))

    assert head.count("data-uxpopover") == 3, "three filter menus"
    assert "aria-sort=" in head
    for name in ("hetkeseis", "vastutaja", "tegevus"):
        assert f"filtreeri {name[:4]}" in head or name in head
    for label in ("Hetkeseis", "Vastutaja", "Järgmiseks", "Kuupäev", "Viimane tegevus"):
        assert label in head


def test_the_accessible_name_contains_the_visible_heading(signed_in, stages, a_row):
    """WCAG 2.5.3: a voice user asks for the word they can see."""
    head = thead_of(signed_in.get(REGISTER))

    assert 'Hetkeseis<span class="visually-hidden"> — filtreeri hetkeseisu järgi' in head
    assert 'Vastutaja<span class="visually-hidden"> — filtreeri vastutaja järgi' in head
    assert 'Järgmiseks<span class="visually-hidden"> — filtreeri järgmise tegevuse järgi' in head


def test_saabunud_keeps_its_static_headings(signed_in, stages):
    """The row partial is shared; the interaction is not (brief 23).

    A filtering control on Saabunud would write `?vastutaja=` into an address
    that page reads nothing out of — a control that looks like it works and
    changes nothing.
    """
    matter(owner=None)

    body = body_of(signed_in.get(reverse("matters:inbox")))

    assert '<th scope="col" class="table__stage">Hetkeseis</th>' in body
    assert "colhead" not in body
    assert "table__quietlink" not in body


def test_the_register_marks_itself_as_the_interactive_table(signed_in):
    assert signed_in.get(REGISTER).context["register_interactive"] is True


# ===========================================================================
# B. Hetkeseis — a filter, from the vocabulary Täpsem otsing offers
# ===========================================================================


def test_the_stage_menu_offers_the_active_vocabulary_and_nothing_else(signed_in, stages, a_row):
    """One vocabulary, read once. A heading with its own list would be a second
    one to keep in step."""
    from app.workflow.models import StageVocabulary

    retired = factories.StageFactory(key="pensionil", label_et="Pensionil", is_active=False)

    response = signed_in.get(REGISTER)
    head = thead_of(response)

    active = list(StageVocabulary.objects.filter(is_active=True).order_by("sort_order"))
    assert list(response.context["stages"]) == active
    assert set(stages) <= set(active)
    for stage in active:
        assert f"hetkeseis={stage.key}" in head
    assert retired.label_et not in head


def test_the_stage_heading_filters_the_same_population_as_the_panel(signed_in, stages):
    chosen, other = stages
    wanted = matter(stage=chosen, title="Kooskõlastusel olev teema")
    matter(stage=other, title="Menetluses olev teema")

    through_heading = signed_in.get(REGISTER, {"hetkeseis": chosen.key})

    assert titles_on(through_heading) == [wanted.title]
    # The panel writes the same parameter, which is the whole claim: there is
    # one filter, and two controls that set it.
    assert titles_on(signed_in.get(REGISTER, {"hetkeseis": chosen.key, "q": ""})) == [wanted.title]


def test_koik_removes_the_stage_filter_and_keeps_everything_else(signed_in, stages):
    chosen, _ = stages
    response = signed_in.get(REGISTER, {"hetkeseis": chosen.key, "olek": "koik", "leht": "2"})

    clear = response.context["column_filters"]["hetkeseis"]["clear_query"]

    assert "hetkeseis" not in clear
    assert "olek=koik" in clear
    # A different population starts at its first page.
    assert "leht" not in clear


def test_an_active_stage_filter_is_visible_on_the_heading(signed_in, stages):
    chosen, _ = stages
    matter(stage=chosen, title="Kooskõlastusel olev teema")

    plain = thead_of(signed_in.get(REGISTER))
    filtered = thead_of(signed_in.get(REGISTER, {"hetkeseis": chosen.key}))

    assert "is-filtered" not in plain
    assert "is-filtered" in filtered
    assert "filter on aktiivne" in filtered


def test_a_row_value_is_the_filter_that_selects_it(signed_in, stages):
    chosen, _ = stages
    matter(stage=chosen, title="Kooskõlastusel olev teema")

    body = body_of(signed_in.get(REGISTER, {"olek": "avatud", "toovoit": "puudub"}))
    link = re.search(r'<td class="table__stage">\s*<a[^>]*href="\?([^"]*)"', body)

    assert link, "the stage cell is not a link"
    query = link.group(1).replace("&amp;", "&")
    assert f"hetkeseis={chosen.key}" in query
    # Everything else the reader had chosen travels with it (brief 15).
    assert "olek=avatud" in query
    assert "toovoit=puudub" in query
    assert f'aria-label="Filtreeri hetkeseis: {chosen.label_et}"' in body


# ===========================================================================
# C. Vastutaja — the filter's own population, never the chooser's
# ===========================================================================


def test_the_owner_menu_is_the_panels_own_population(signed_in, specialist):
    """`owner_filter_choices`, read once for both controls (ADR 0036)."""
    departed = factories.UserFactory(display_name="Lahkunud Kolleeg", is_active=False)
    matter(owner=departed, title="Lahkunud kolleegi teema")

    response = signed_in.get(REGISTER)
    head = thead_of(response)

    offered = list(response.context["owners"])
    assert departed in offered, "a colleague who left still owns work somebody looks for"
    for person in offered:
        assert f"vastutaja={person.pk}" in head
    assert "vastutaja=puudub" in head


def test_the_owner_heading_filters_and_maaramata_works(signed_in, specialist, other_specialist):
    mine = matter(owner=specialist, title="Minu teema")
    theirs = matter(owner=other_specialist, title="Kolleegi teema")
    ownerless = matter(owner=None, title="Vastutajata teema")

    by_person = signed_in.get(REGISTER, {"vastutaja": str(other_specialist.pk)})
    missing = signed_in.get(REGISTER, {"vastutaja": "puudub"})

    assert titles_on(by_person) == [theirs.title]
    assert titles_on(missing) == [ownerless.title]
    assert mine.title not in titles_on(by_person)


def test_a_person_reachable_only_through_a_restricted_matter_is_not_named(client, stages):
    """An option is a name on a page. A name that appears only because of
    records this reader may not see tells them a colleague, a file and a working
    relationship exist (app/accounts/selectors.py)."""
    hidden_owner = factories.UserFactory(display_name="Varjatud Vastutaja", is_active=False)
    matter(owner=hidden_owner, visibility=Visibility.RESTRICTED, title="Piiratud teema")
    matter(title="Tavaline teema")  # so there is a table to read the head off
    stranger = factories.ReaderFactory()
    client.force_login(stranger)

    head = thead_of(client.get(REGISTER, {"olek": "koik"}))

    assert hidden_owner.display_name not in head
    assert str(hidden_owner.pk) not in head


def test_a_row_owner_is_the_filter_that_selects_them(signed_in, specialist):
    matter(owner=specialist, title="Minu teema")

    body = body_of(signed_in.get(REGISTER))

    assert f"vastutaja={specialist.pk}#tulemused" in body
    assert f'aria-label="Filtreeri vastutaja: {specialist.display_name}"' in body
    # Quiet, not blue. The cell keeps the class that clips a long name.
    assert 'class="table__quietlink table__clip"' in body


# ===========================================================================
# D. Järgmiseks — the register's own work states, not the sentence's letters
# ===========================================================================


def test_the_next_action_menu_offers_the_existing_tegevus_semantics(signed_in, a_row):
    from app.matters.views import NEXT_ACTION_LABELS

    response = signed_in.get(REGISTER)
    head = thead_of(response)

    assert list(response.context["next_action_options"]) == list(NEXT_ACTION_LABELS.items())
    for value, label in NEXT_ACTION_LABELS.items():
        assert f"tegevus={value}" in head
        assert label in head


def test_the_menu_states_match_the_registers_own_definitions(signed_in, specialist):
    """Puudub, Tähtaeg möödas and Ülevaatus käes, each exactly as `?tegevus=`
    already means them — no second definition, and no alphabetical text sort."""
    from app.matters.selectors import REVIEW_DUE

    nothing = matter(owner=specialist, title="Sammuta teema")
    late = matter(owner=specialist, title="Hilinenud teema")
    open_action(late, text="Ammu tehtud", target_date=TODAY - timedelta(days=3))
    review = matter(owner=specialist, title="Ülevaatuse teema")
    open_action(
        review,
        text="Vaatan üle",
        target_date=TODAY,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
    )

    assert titles_on(signed_in.get(REGISTER, {"tegevus": "puudub"})) == [nothing.title]
    assert titles_on(signed_in.get(REGISTER, {"tegevus": "hilinenud"})) == [late.title]
    assert titles_on(signed_in.get(REGISTER, {"tegevus": REVIEW_DUE})) == [review.title]


def test_a_jargmiseks_sentence_is_not_a_filter_link(signed_in, specialist):
    """Arbitrary wording belongs to the search box, not to a column heading."""
    subject = matter(owner=specialist, title="Sammuga teema")
    open_action(subject, text="Helistan ministeeriumi", target_date=TODAY)

    body = body_of(signed_in.get(REGISTER))

    assert "Helistan ministeeriumi" in body
    assert "tegevus=Helistan" not in body


def test_the_next_action_heading_carries_its_state(signed_in, a_row):
    filtered = thead_of(signed_in.get(REGISTER, {"tegevus": "puudub"}))

    assert "is-filtered" in filtered
    assert 'class="colhead__option is-chosen"' in filtered


# ===========================================================================
# E. Kuupäev — the displayed date is the sort key
# ===========================================================================


def test_the_row_shows_the_open_steps_own_date_when_it_has_one(signed_in, specialist):
    subject = matter(
        owner=specialist, title="Sammuga teema", response_deadline=TODAY + timedelta(days=90)
    )
    open_action(subject, target_date=TODAY + timedelta(days=2))

    shown = dates_rendered(signed_in.get(REGISTER))

    assert shown == [estonian(TODAY + timedelta(days=2))]


def test_the_row_falls_back_to_the_response_deadline(signed_in, specialist):
    """Including when an open step exists but carries no date at all — the case
    a `Case(When(has_action=True, ...))` ordering would get wrong."""
    dateless = matter(
        owner=specialist,
        title="Kuupäevata sammuga teema",
        response_deadline=TODAY + timedelta(days=5),
    )
    open_action(
        dateless,
        text="Ootan vastust",
        target_date=None,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
    )

    shown = dates_rendered(signed_in.get(REGISTER))

    assert shown == [estonian(TODAY + timedelta(days=5))]


def test_neither_known_renders_an_em_dash(signed_in, specialist):
    matter(owner=specialist, title="Kuupäevata teema")

    assert dates_rendered(signed_in.get(REGISTER)) == ["—"]


def test_the_annotation_agrees_with_the_row_on_every_shape(signed_in, specialist):
    """The two readings of one rule, held against each other.

    Four shapes distinguish them: a dated step, a step with no date, no step at
    all, and a Matter with neither. Anything that reads one column while the
    template reads another disagrees on at least one of these.
    """
    from app.matters.selectors import matter_list_queryset

    dated = matter(owner=specialist, title="A", response_deadline=TODAY + timedelta(days=90))
    open_action(dated, target_date=TODAY + timedelta(days=1))
    dateless_step = matter(owner=specialist, title="B", response_deadline=TODAY + timedelta(days=2))
    open_action(
        dateless_step,
        target_date=None,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
    )
    deadline_only = matter(owner=specialist, title="C", response_deadline=TODAY + timedelta(days=3))
    matter(owner=specialist, title="D")

    rows = register_dates.annotate_display_date(matter_list_queryset(specialist), specialist)
    for row in rows:
        read = register_dates.register_date(row)
        annotated = getattr(row, register_dates.DISPLAY_DATE)
        assert (read.value if read else None) == annotated, row.title

    assert deadline_only.response_deadline == TODAY + timedelta(days=3)


def test_kuupaev_ascending_is_exact_and_puts_the_undated_last(signed_in, specialist):
    late = matter(owner=specialist, title="Hiline", response_deadline=TODAY + timedelta(days=30))
    early = matter(owner=specialist, title="Varane")
    open_action(early, target_date=TODAY + timedelta(days=1))
    middle = matter(
        owner=specialist, title="Keskmine", response_deadline=TODAY + timedelta(days=10)
    )
    undated = matter(owner=specialist, title="Kuupäevata")

    response = signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC})

    assert titles_on(response) == [early.title, middle.title, late.title, undated.title]
    # And the dates on screen really are in that order.
    assert dates_rendered(response) == [
        estonian(TODAY + timedelta(days=1)),
        estonian(TODAY + timedelta(days=10)),
        estonian(TODAY + timedelta(days=30)),
        "—",
    ]
    assert undated.title == titles_on(response)[-1]


def test_kuupaev_descending_is_exact_and_still_puts_the_undated_last(signed_in, specialist):
    """PostgreSQL's own default is NULLS FIRST descending, which would have
    opened «hiliseim enne» on a page of em dashes."""
    late = matter(owner=specialist, title="Hiline", response_deadline=TODAY + timedelta(days=30))
    early = matter(owner=specialist, title="Varane")
    open_action(early, target_date=TODAY + timedelta(days=1))
    undated = matter(owner=specialist, title="Kuupäevata")

    response = signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_DESC})

    assert titles_on(response) == [late.title, early.title, undated.title]
    assert dates_rendered(response)[-1] == "—"


def test_a_restricted_step_moves_neither_the_date_nor_the_row(client, specialist):
    """Authorization happens before the sort key contributes.

    The step is three weeks earlier than anything else on the page. To the
    reader who may see it the row sorts first; to the reader who may not, the
    row shows — and sorts on — the Matter's own deadline, and its position says
    nothing about a step that reader cannot read.
    """
    hidden_day = TODAY + timedelta(days=1)
    subject = matter(
        owner=specialist,
        title="Piiratud sammuga teema",
        response_deadline=TODAY + timedelta(days=40),
    )
    action = open_action(subject, text="Varjatud samm", target_date=hidden_day)
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])
    other = matter(
        owner=specialist, title="Tavaline teema", response_deadline=TODAY + timedelta(days=5)
    )

    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    client.force_login(stranger)
    theirs = client.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC})

    assert titles_on(theirs) == [other.title, subject.title]
    assert dates_rendered(theirs) == [
        estonian(TODAY + timedelta(days=5)),
        estonian(TODAY + timedelta(days=40)),
    ]
    assert estonian(hidden_day) not in body_of(theirs)

    client.force_login(specialist)
    mine = client.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC})

    assert titles_on(mine) == [subject.title, other.title]


def test_an_approximate_step_sorts_on_its_day_and_prints_its_quarter(signed_in, specialist):
    """`value` and `display` are deliberately different.

    A step recorded as *III kvartal* sorts on the day it is anchored to and
    prints the quarter, because rendering it as an exact day would manufacture a
    certainty the source never had (master specification 3.5).
    """
    anchor = TODAY.replace(month=7, day=1) + timedelta(days=365)
    approximate = matter(owner=specialist, title="Ligikaudne teema")
    open_action(
        approximate,
        target_date=anchor,
        date_precision=DatePrecision.QUARTER,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
    )
    later = matter(
        owner=specialist, title="Hilisem teema", response_deadline=anchor + timedelta(days=1)
    )
    earlier = matter(
        owner=specialist, title="Varasem teema", response_deadline=anchor - timedelta(days=1)
    )

    response = signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC})

    assert titles_on(response) == [earlier.title, approximate.title, later.title]
    assert "dateline--approximate" in body_of(response)
    assert estonian(anchor) not in dates_rendered(response)[1]
    assert "kvartal" in dates_rendered(response)[1].lower()


# ===========================================================================
# F. Viimane tegevus — the derived fact, never `updated_at`
# ===========================================================================


def test_the_activity_annotation_agrees_with_the_rendered_column(signed_in, specialist):
    from app.matters.selectors import matter_list_queryset

    written = matter(owner=specialist, title="Sissekandega teema")
    factories.EntryFactory(
        matter=written,
        author=specialist,
        occurred_at=timezone.now() - timedelta(days=4),
    )
    arrived = matter(
        owner=specialist, title="Saabunud teema", received_date=TODAY - timedelta(days=9)
    )
    silent = matter(owner=specialist, title="Vaikne teema", origin="LEGACY_IMPORT")

    rows = activity.annotate_activity_date(matter_list_queryset(specialist))
    for row in rows:
        fact = activity.activity_of(row)
        assert (fact.occurred_on if fact else None) == getattr(row, activity.ACTIVITY_DATE), (
            row.title
        )

    assert {written.title, arrived.title, silent.title} <= set(titles_on(signed_in.get(REGISTER)))


def test_an_imported_row_does_not_borrow_the_cutover_timestamp(signed_in, specialist):
    """`updated_at` is today on every imported row; the column and the sort both
    refuse it (ADR 0026)."""
    from app.matters.selectors import matter_list_queryset

    imported = matter(owner=specialist, title="Imporditud teema", origin="LEGACY_IMPORT")
    imported.save()  # `updated_at` is now

    row = activity.annotate_activity_date(
        matter_list_queryset(specialist).filter(pk=imported.pk)
    ).get()

    assert getattr(row, activity.ACTIVITY_DATE) is None
    assert last_activity_rendered(signed_in.get(REGISTER)) == ["—"]


def test_viimane_tegevus_newest_first_is_exact(signed_in, specialist):
    old = matter(owner=specialist, title="Vana", received_date=TODAY - timedelta(days=30))
    recent = matter(owner=specialist, title="Hiljutine", received_date=TODAY - timedelta(days=2))
    silent = matter(owner=specialist, title="Vaikne", origin="LEGACY_IMPORT")

    response = signed_in.get(REGISTER, {"jarjestus": views.ACTIVITY_SORT_NEWEST})

    assert titles_on(response) == [recent.title, old.title, silent.title]
    assert last_activity_rendered(response) == [
        estonian(TODAY - timedelta(days=2)),
        estonian(TODAY - timedelta(days=30)),
        "—",
    ]


def test_viimane_tegevus_oldest_first_is_exact_and_keeps_the_unknown_last(signed_in, specialist):
    old = matter(owner=specialist, title="Vana", received_date=TODAY - timedelta(days=30))
    recent = matter(owner=specialist, title="Hiljutine", received_date=TODAY - timedelta(days=2))
    silent = matter(owner=specialist, title="Vaikne", origin="LEGACY_IMPORT")

    response = signed_in.get(REGISTER, {"jarjestus": views.ACTIVITY_SORT_OLDEST})

    assert titles_on(response) == [old.title, recent.title, silent.title]
    assert last_activity_rendered(response)[-1] == "—"


def test_the_latest_fact_wins_rather_than_the_most_canonical(signed_in, specialist):
    """The module's own rule, asserted through the ordering."""
    subject = matter(
        owner=specialist, title="Mitme faktiga teema", received_date=TODAY - timedelta(days=40)
    )
    factories.EntryFactory(
        matter=subject, author=specialist, occurred_at=timezone.now() - timedelta(days=1)
    )
    other = matter(
        owner=specialist, title="Ühe faktiga teema", received_date=TODAY - timedelta(days=3)
    )

    response = signed_in.get(REGISTER, {"jarjestus": views.ACTIVITY_SORT_NEWEST})

    assert titles_on(response) == [subject.title, other.title]


def test_a_restricted_entry_moves_neither_the_column_nor_the_row(client, specialist):
    """A date that moved would announce that the entry exists."""
    subject = matter(
        owner=specialist,
        title="Piiratud sissekandega teema",
        received_date=TODAY - timedelta(days=40),
    )
    factories.EntryFactory(
        matter=subject,
        author=specialist,
        occurred_at=timezone.now(),
        visibility_override=Visibility.RESTRICTED,
    )
    other = matter(
        owner=specialist, title="Tavaline teema", received_date=TODAY - timedelta(days=3)
    )

    stranger = factories.ReaderFactory()
    client.force_login(stranger)
    theirs = client.get(REGISTER, {"jarjestus": views.ACTIVITY_SORT_NEWEST})

    assert titles_on(theirs) == [other.title, subject.title]
    assert last_activity_rendered(theirs) == [
        estonian(TODAY - timedelta(days=3)),
        estonian(TODAY - timedelta(days=40)),
    ]

    client.force_login(specialist)
    assert (
        titles_on(client.get(REGISTER, {"jarjestus": views.ACTIVITY_SORT_NEWEST}))[0]
        == subject.title
    )


# ===========================================================================
# G. The sort contract
# ===========================================================================


def test_the_default_ordering_is_unchanged_by_the_clickable_headings(signed_in, specialist):
    """No product decision changed the order Teemad opens in (brief 19)."""
    for number, title in ((11, "Kolmas"), (12, "Teine"), (13, "Esimene")):
        factories.MatterFactory(
            owner=specialist, title=title, reference_year=2097, reference_number=number
        )

    default = titles_on(signed_in.get(REGISTER))

    assert default == titles_on(signed_in.get(REGISTER, {"jarjestus": "reference"}))
    assert default[:3] == ["Esimene", "Teine", "Kolmas"]


def test_an_old_bookmark_still_means_what_it_meant(signed_in, specialist):
    """`?jarjestus=deadline` orders on the Matter's own `Arvamuse tähtaeg`, and
    still does. It is not quietly re-pointed at the Kuupäev column, which shows
    the open step's date whenever the visible step has one (brief 18)."""
    with_step = matter(
        owner=specialist, title="Sammuga", response_deadline=TODAY + timedelta(days=30)
    )
    open_action(with_step, target_date=TODAY + timedelta(days=1))
    plain = matter(owner=specialist, title="Sammuta", response_deadline=TODAY + timedelta(days=10))

    by_deadline = titles_on(signed_in.get(REGISTER, {"jarjestus": "deadline"}))
    by_column = titles_on(signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC}))

    assert by_deadline == [plain.title, with_step.title]
    assert by_column == [with_step.title, plain.title]
    assert views.SORT_FIELDS["deadline"] == ("response_deadline",)


def test_an_unreadable_ordering_falls_back_to_the_default(signed_in, specialist):
    matter(owner=specialist, title="Teema")

    assert titles_on(signed_in.get(REGISTER, {"jarjestus": "; DROP"})) == titles_on(
        signed_in.get(REGISTER)
    )


def test_the_panel_offers_every_ordering_a_heading_can_set(signed_in):
    """Otherwise submitting Täpsem otsing would post the select's first option
    and silently undo an ordering chosen from a column (brief 16, 20)."""
    body = body_of(signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_DESC}))
    panel = body.split('<select class="field__input" name="jarjestus">', 1)[1].split(
        "</select>", 1
    )[0]

    for value in views.SORT_LABELS:
        assert f'value="{value}"' in panel
    selected = [line for line in panel.splitlines() if "selected" in line]
    assert len(selected) == 1
    assert f'value="{views.DATE_SORT_DESC}"' in selected[0]


def test_the_heading_cycles_through_three_states(signed_in, stages):
    """Unsorted, one way, the other, then back to the register's own order — so
    the heading that turned the ordering on also turns it off (brief 9)."""
    plain = signed_in.get(REGISTER).context["column_sorts"]["kuupaev"]
    ascending = signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC}).context["column_sorts"]
    descending = signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_DESC}).context[
        "column_sorts"
    ]

    assert plain["direction"] == "none"
    assert f"jarjestus={views.DATE_SORT_ASC}" in plain["query"]
    assert ascending["kuupaev"]["direction"] == "ascending"
    assert f"jarjestus={views.DATE_SORT_DESC}" in ascending["kuupaev"]["query"]
    assert descending["kuupaev"]["direction"] == "descending"
    assert "jarjestus" not in descending["kuupaev"]["query"]
    # And the other column is untouched by either.
    assert descending["viimane"]["direction"] == "none"


def test_aria_sort_states_the_direction_on_the_cell(signed_in, a_row):
    ascending = thead_of(signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC}))
    newest = thead_of(signed_in.get(REGISTER, {"jarjestus": views.ACTIVITY_SORT_NEWEST}))

    assert 'class="table__date" aria-sort="ascending"' in ascending
    assert 'table__lastactivity" aria-sort="none"' in ascending
    assert 'table__lastactivity" aria-sort="descending"' in newest


# ===========================================================================
# H. Composition — nothing a heading does resets anything else
# ===========================================================================


def test_a_heading_filter_keeps_every_other_parameter(signed_in, stages, specialist):
    """Every option in every menu, not only the one the test happened to pick."""
    chosen, _ = stages
    matter(owner=specialist, stage=chosen, title="Kooskõlastusel olev teema")
    carried = {
        "olek": "koik",
        "toovoit": "puudub",
        "jarjestus": views.DATE_SORT_ASC,
        "leht": "3",
    }

    response = signed_in.get(REGISTER, carried)
    head = thead_of(response).replace("&amp;", "&")
    menus, sorts = head.split("colhead colhead--sort", 1)

    for query in re.findall(r'href="\?([^"]*)#tulemused"', menus):
        for survivor in ("olek=koik", "toovoit=puudub", f"jarjestus={views.DATE_SORT_ASC}"):
            assert survivor in query, f"{survivor} was dropped by {query}"
        assert "leht" not in query, query

    # A sort changes the ordering and nothing else, including the filters.
    for query in re.findall(r'href="\?([^"]*)#tulemused"', sorts):
        assert "olek=koik" in query and "toovoit=puudub" in query, query
        assert "leht" not in query, query
    assert "leht" not in response.context["column_filters"]["hetkeseis"]["clear_query"]


def test_a_sort_keeps_every_filter(signed_in, stages, specialist):
    chosen, other = stages
    wanted = matter(
        owner=specialist, stage=chosen, title="A", response_deadline=TODAY + timedelta(days=9)
    )
    second = matter(
        owner=specialist, stage=chosen, title="B", response_deadline=TODAY + timedelta(days=1)
    )
    matter(owner=specialist, stage=other, title="C")

    response = signed_in.get(REGISTER, {"hetkeseis": chosen.key, "jarjestus": views.DATE_SORT_ASC})

    assert titles_on(response) == [second.title, wanted.title]


def test_search_text_filter_and_sort_compose(signed_in, specialist, stages):
    """`q` + a heading filter + a heading sort, all three at once."""
    chosen, other = stages
    early = indexed(
        matter(
            owner=specialist,
            stage=chosen,
            title="Pakendiseaduse muutmine",
            response_deadline=TODAY + timedelta(days=1),
        )
    )
    late = indexed(
        matter(
            owner=specialist,
            stage=chosen,
            title="Pakendiaktsiisi muutmine",
            response_deadline=TODAY + timedelta(days=20),
        )
    )
    # Matches the text but not the stage.
    indexed(matter(owner=specialist, stage=other, title="Pakendijäätmete aruandlus"))
    # Matches the stage but not the text.
    indexed(matter(owner=specialist, stage=chosen, title="Hoopis muu teema"))

    response = signed_in.get(
        REGISTER,
        {"q": "Pakendi", "hetkeseis": chosen.key, "jarjestus": views.DATE_SORT_DESC},
    )

    assert titles_on(response) == [late.title, early.title]


def test_pagination_carries_the_heading_state(signed_in, specialist, stages):
    chosen, _ = stages
    for index in range(30):
        matter(
            owner=specialist,
            stage=chosen,
            title=f"Teema {index:02d}",
            response_deadline=TODAY + timedelta(days=index + 1),
        )

    response = signed_in.get(REGISTER, {"hetkeseis": chosen.key, "jarjestus": views.DATE_SORT_DESC})
    body = body_of(response)

    assert response.context["paginator"].num_pages > 1
    assert f"hetkeseis={chosen.key}" in response.context["query_string"]
    assert f"jarjestus={views.DATE_SORT_DESC}" in response.context["query_string"]
    assert "leht=2" in body


def test_changing_only_the_page_size_keeps_the_filter_and_the_sort(signed_in, specialist, stages):
    """«näita korraga» changes how much of the same list is on screen (brief 22)."""
    chosen, _ = stages
    matter(owner=specialist, stage=chosen, title="Teema")

    response = signed_in.get(
        REGISTER, {"hetkeseis": chosen.key, "jarjestus": views.ACTIVITY_SORT_OLDEST, "leht": "2"}
    )

    for option in response.context["page_size_options"]:
        assert f"hetkeseis={chosen.key}" in option["query"], option
        assert f"jarjestus={views.ACTIVITY_SORT_OLDEST}" in option["query"], option
        assert "leht" not in option["query"], option


def test_the_second_page_is_still_in_the_chosen_order(signed_in, specialist):
    for index in range(30):
        matter(
            owner=specialist,
            title=f"Teema {index:02d}",
            response_deadline=TODAY + timedelta(days=index + 1),
        )

    first = dates_rendered(signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC}))
    second = dates_rendered(
        signed_in.get(REGISTER, {"jarjestus": views.DATE_SORT_ASC, "leht": "2"})
    )

    # The database ordered before the page boundary was drawn (brief 26).
    assert first == sorted(
        first, key=lambda text: [int(part) for part in reversed(text.split("."))]
    )
    assert second[0] not in first


# ===========================================================================
# I. One filter state, two controls
# ===========================================================================


def test_a_heading_filter_is_already_chosen_in_tapsem_otsing(signed_in, stages, specialist):
    chosen, _ = stages

    body = body_of(
        signed_in.get(REGISTER, {"hetkeseis": chosen.key, "vastutaja": str(specialist.pk)})
    )
    panel = body.split('<select class="field__input" name="hetkeseis">', 1)[1].split(
        "</select>", 1
    )[0]
    owner_panel = body.split('<select class="field__input" name="vastutaja">', 1)[1].split(
        "</select>", 1
    )[0]

    assert re.search(rf'value="{chosen.key}"\s+selected', panel)
    assert re.search(rf'value="{specialist.pk}"\s+selected', owner_panel)


def test_a_heading_filter_produces_the_ordinary_chip(signed_in, stages):
    """The same chip the equivalent Täpsem otsing control would produce, and
    removing it resets the heading too (brief 17)."""
    chosen, _ = stages
    matter(stage=chosen, title="Kooskõlastusel olev teema")

    response = signed_in.get(REGISTER, {"hetkeseis": chosen.key})
    chips = response.context["active_filters"]

    assert [chip["label"] for chip in chips] == ["Hetkeseis"]
    assert chips[0]["value"] == chosen.label_et
    assert "hetkeseis" not in chips[0]["remove_query"]

    after = signed_in.get(REGISTER + "?" + chips[0]["remove_query"])
    assert "is-filtered" not in thead_of(after)


def test_the_live_search_fragment_still_carries_the_headings(signed_in, stages, specialist):
    """The head is inside the region a keystroke replaces (brief 21)."""
    chosen, _ = stages
    indexed(matter(owner=specialist, stage=chosen, title="Pakendiseaduse muutmine"))

    fragment = signed_in.get(
        REGISTER,
        {"q": "Pakend", "hetkeseis": chosen.key, "jarjestus": views.DATE_SORT_ASC},
        headers={"HX-Request": "true"},
    )
    body = body_of(fragment)

    assert "colhead" in body
    assert "is-filtered" in body
    assert 'aria-sort="ascending"' in body
    assert f"hetkeseis={chosen.key}" in body


# ===========================================================================
# J. Cost and shape
# ===========================================================================


def test_the_interactive_register_needs_no_schema() -> None:
    """Query and presentation only (brief 25).

    Both new facts are interpretations of columns that already exist — the open
    step's date, the Matter's deadline, the nine activity facts — so the models
    are untouched and `makemigrations` has nothing to write. There is no stored
    sort preference and no filter table: what the reader is looking at lives in
    the address, as it always has.
    """
    from io import StringIO

    from django.core.management import call_command

    call_command("makemigrations", "--check", "--dry-run", stdout=StringIO())


def _queries_for(client, params, rows):
    """The queries the register costs for a page of ``rows``."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        response = client.get(REGISTER, params)
        assert len(titles_on(response)) == rows
    return list(captured.captured_queries)


def test_neither_sort_adds_a_query_per_row(signed_in, specialist):
    """The ordering is the database's work (brief 26).

    Asserted as *the cost does not grow with the number of rows* rather than as
    a fixed number: a shared register page is a moving target, and what a row
    may never cost is a query of its own.
    """
    sorts = ("reference", views.DATE_SORT_ASC, views.ACTIVITY_SORT_NEWEST)
    for index in range(3):
        subject = matter(owner=specialist, title=f"Teema {index:02d}")
        open_action(subject, target_date=TODAY + timedelta(days=index))

    small = {sort: len(_queries_for(signed_in, {"jarjestus": sort}, 3)) for sort in sorts}

    # Still one page: what is being measured is the cost of rendering rows, not
    # the cost of a second page (`PAGE_SIZE_CHOICES` opens at 12).
    for index in range(3, 12):
        subject = matter(owner=specialist, title=f"Teema {index:02d}")
        open_action(subject, target_date=TODAY + timedelta(days=index))

    for sort in sorts:
        after = len(_queries_for(signed_in, {"jarjestus": sort}, 12))
        assert after == small[sort], f"{sort}: {small[sort]} queries for 3 rows, {after} for 12"


def test_a_sorted_register_orders_in_sql(signed_in, specialist):
    """Not in Python, and not after the page boundary has been drawn."""
    for index in range(3):
        matter(
            owner=specialist,
            title=f"Teema {index}",
            response_deadline=TODAY + timedelta(days=index),
        )

    queries = _queries_for(signed_in, {"jarjestus": views.DATE_SORT_ASC}, 3)

    assert any("ORDER BY" in query["sql"] and "NULLS LAST" in query["sql"] for query in queries), (
        "no query orders the register with explicit null handling"
    )


def test_the_ordinary_page_pays_for_no_sort_key(signed_in, specialist):
    """The derived keys are annotated only when they are ordered on."""
    matter(owner=specialist, title="Teema")

    plain = " ".join(query["sql"] for query in _queries_for(signed_in, {}, 1))

    assert register_dates.DISPLAY_DATE not in plain
    assert activity.ACTIVITY_DATE not in plain
