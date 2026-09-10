"""Teemad as the one discovery surface, and the retirement of «Tähtajad».

Three generated department-wide reading pages went away — Olulised tähtajad,
Jõustuvad aktid and Töövõidud — and what they showed moved to where it is
actually used: `Töövõit` and `Jõustumine` are structured filters on the
register, an `Oluline tähtaeg` is its owner's own upcoming work
(docs/adr/0071).

Nothing about the facts changed, which is the half most worth asserting. The
models, the rows and the write surfaces on the Matter page are untouched, and
every retired address still resolves.

Two rules run through nearly every test below.

**A Matter appears once, whatever it carries.** Both new filters consult a child
table, and a file that commences in three stages or won twice is still one row
in a register that pages Matters.

**Authorization happens before the existence test contributes anything.** A
restricted child may not make its Matter appear — nor, under `puudub`, make it
disappear.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.intelligence.enums import EffectiveDateKind, FactStatus, WorkVictoryStatus
from app.matters import register_filters
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")


#: Every register response is read through the page's own object list, so a
#: filter that "works" only in a selector cannot pass these.
def titles_on(response) -> list[str]:
    return [matter.title for matter in response.context["page"].object_list]


def rows_of(response) -> int:
    return len(response.context["page"].object_list)


# ---------------------------------------------------------------------------
# A. the navigation
# ---------------------------------------------------------------------------

LINK = re.compile(r"<a\b[^>]*>([^<]+)</a>")


def navigation_of(response) -> str:
    """The main navigation's markup and nothing else on the page.

    Sliced rather than searched for. «Tähtajad» is ordinary Estonian and appears
    in headings, filter legends and tables all over this application, so a bare
    ``in body`` would fail on any of them and prove nothing about the bar.
    """
    assert response.status_code == 200
    body = response.content.decode()
    start = body.index('<nav class="topnav"')
    return body[start : body.index("</nav>", start)]


def labels_of(navigation: str) -> list[str]:
    seen: list[str] = []
    for label in LINK.findall(navigation):
        text = label.strip()
        if text and text not in seen:
            seen.append(text)
    return seen


@pytest.mark.parametrize(
    "route",
    ["matters:department", "matters:my_work", "matters:matter_list", "reporting:overview"],
)
def test_tahtajad_is_not_on_the_bar(signed_in, route):
    """Asserted on four surfaces, because the bar is one template on all of them.

    A single-page check would still pass if some page shadowed the include with
    a copy of its own. The *order* of what remains is
    `tests/test_topnav_order_and_labels.py`'s contract, not this file's; what
    this asserts is the product decision — the destination is gone.
    """
    assert "Tähtajad" not in navigation_of(signed_in.get(reverse(route)))


def test_tahtajad_is_not_in_the_veel_disclosure_either(signed_in):
    """Both branches of the include are read.

    Below 1560px the secondary destinations sit inside «Veel» and above it they
    sit inline; only one is displayed, but both are in the markup. An item
    merely pushed into the disclosure would still be a destination.
    """
    navigation = navigation_of(signed_in.get(reverse("matters:department")))

    wide = navigation.index('<span class="topnav__wide">')
    menu = navigation.index('<div class="topnav__menu">')
    for branch in (navigation[wide:menu], navigation[menu:]):
        assert labels_of(branch) == ["Statistika"]


@pytest.mark.parametrize("retired", ["Tähtajad", "Jälgimine", "Jõustuvad aktid", "Töövõidud"])
def test_no_retired_reading_destination_returns_under_another_name(signed_in, retired):
    """The point is that the *workspace* is gone, not that one word is.

    Putting «Töövõidud» back as its own item would rebuild the parallel model
    this change removed, so the whole family is asserted rather than the one
    label that happened to be on the bar.
    """
    assert retired not in navigation_of(signed_in.get(reverse("matters:department")))


def test_teemad_and_statistika_are_still_there(signed_in):
    """The half that must *not* have happened."""
    navigation = navigation_of(signed_in.get(reverse("matters:matter_list")))

    assert ">Teemad</a>" in navigation
    assert ">Statistika</a>" in navigation


def test_the_bar_reads_the_same_way_for_the_head(client, department_head):
    """Not conditional on a role. It never was."""
    client.force_login(department_head)

    assert "Tähtajad" not in navigation_of(client.get(reverse("matters:department")))


# ---------------------------------------------------------------------------
# B. Töövõit as a register filter
# ---------------------------------------------------------------------------


def _victory(matter, *, status=WorkVictoryStatus.CONFIRMED, year: int | None = None, **extra):
    """One work victory, dated by calendar year unless told otherwise.

    A confirmed row carries ``confirmed_at`` by database constraint — the
    product's own rule that a decision records when it was taken — so the
    factory is given one whenever the status is CONFIRMED.
    """
    from django.utils import timezone

    period = datetime.date(year, 1, 1) if year else None
    end = datetime.date(year, 12, 31) if year else None
    return factories.WorkVictoryFactory(
        matter=matter,
        status=status,
        period_date=period,
        period_end=end,
        date_precision=DatePrecision.YEAR,
        confirmed_at=timezone.now() if status == WorkVictoryStatus.CONFIRMED else None,
        **extra,
    )


def test_a_confirmed_victory_makes_its_matter_findable(signed_in):
    won = factories.MatterFactory(title="Võidetud teema")
    factories.MatterFactory(title="Tavaline teema")
    _victory(won)

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == ["Võidetud teema"]


def test_a_matter_without_a_victory_is_excluded(signed_in):
    factories.MatterFactory(title="Tavaline teema")

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == []


def test_several_victories_return_the_matter_once(signed_in):
    """``Exists`` is a correlated subquery, not a join.

    The failure this rules out is the one the sender filters above pay
    ``.distinct()`` to avoid: a file that won three times appearing three times.
    """
    won = factories.MatterFactory(title="Kolm korda võidetud")
    for _ in range(3):
        _victory(won)

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == ["Kolm korda võidetud"]
    assert response.context["total"] == 1


def test_an_internal_candidate_is_not_a_reader_visible_victory(signed_in):
    """The register uses ``VISIBLE_VICTORY_STATUS`` and does not decide for itself.

    A machine's or an import's proposal that nobody has decided on is still a
    proposal. It reached nobody's Töövõidud page and it reaches nobody's
    register filter either — one definition, imported
    (app/intelligence/selectors.py).
    """
    proposed = factories.MatterFactory(title="Kandidaadiga teema")
    _victory(proposed, status=WorkVictoryStatus.CANDIDATE)

    found = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(found) == []

    absent = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "puudub", "olek": "koik"})
    assert titles_on(absent) == ["Kandidaadiga teema"]


def test_the_filter_reads_the_same_definition_the_page_used(signed_in):
    """Stated as an identity rather than as a behaviour, so a future edit to
    either side has to notice the other."""
    from app.intelligence.selectors import VISIBLE_VICTORY_STATUS

    assert VISIBLE_VICTORY_STATUS == WorkVictoryStatus.CONFIRMED


def test_puudub_finds_the_files_with_no_visible_victory(signed_in):
    won = factories.MatterFactory(title="Võidetud")
    plain = factories.MatterFactory(title="Võitmata")
    _victory(won)

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "puudub", "olek": "koik"})
    assert titles_on(response) == [plain.title]


def test_a_year_selects_the_business_period_and_never_infers_one(signed_in):
    """A victory with no period stays out of every year (Stage-2G brief 27)."""
    old = factories.MatterFactory(title="Kahekümne neljas")
    new = factories.MatterFactory(title="Kahekümne kuues")
    undated = factories.MatterFactory(title="Perioodita")
    _victory(old, year=2024)
    _victory(new, year=2026)
    _victory(undated)

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "2024", "olek": "koik"})
    assert titles_on(response) == ["Kahekümne neljas"]


def test_an_unsupported_year_empties_the_list_rather_than_raising(signed_in):
    """`?toovoit=99999` reached `period_date__year` once and raised (CORR-02)."""
    factories.MatterFactory(title="Ükskõik")

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "99999", "olek": "koik"})
    assert response.status_code == 200
    assert titles_on(response) == []


def test_a_nonsense_value_empties_the_list(signed_in):
    factories.MatterFactory(title="Ükskõik")

    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "jah", "olek": "koik"})
    assert titles_on(response) == []


# ---------------------------------------------------------------------------
# C. Jõustumine as a register filter
# ---------------------------------------------------------------------------


def _commencement(matter, *, kind=EffectiveDateKind.KNOWN_DATE, start=None, end=None, **extra):
    return factories.EffectiveDateFactory(
        matter=matter,
        kind=kind,
        date_value=start,
        period_end=end if start is not None else None,
        **extra,
    )


def test_an_active_commencement_makes_its_matter_findable(signed_in):
    commencing = factories.MatterFactory(title="Jõustuv teema")
    factories.MatterFactory(title="Tavaline teema")
    _commencement(commencing, start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 1))

    response = signed_in.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == ["Jõustuv teema"]


def test_a_matter_with_no_commencement_is_excluded(signed_in):
    factories.MatterFactory(title="Tavaline teema")

    response = signed_in.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == []


def test_a_cancelled_commencement_is_not_an_active_one(signed_in):
    """Cancelled records are kept and marked on the Matter, never hidden — and
    they are not what «millel on jõustumine» means (Stage-2G brief 8, 33)."""
    matter = factories.MatterFactory(title="Tühistatud jõustumisega")
    _commencement(
        matter,
        start=datetime.date(2026, 4, 1),
        end=datetime.date(2026, 4, 1),
        status=FactStatus.CANCELLED,
    )

    response = signed_in.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == []


def test_several_commencement_rows_return_the_matter_once(signed_in):
    """One law routinely commences in stages (Stage-2G brief 11)."""
    staged = factories.MatterFactory(title="Kolmes etapis")
    for month in (1, 6, 12):
        _commencement(
            staged,
            start=datetime.date(2026, month, 1),
            end=datetime.date(2026, month, 1),
        )

    response = signed_in.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == ["Kolmes etapis"]
    assert response.context["total"] == 1


def test_the_filter_reads_the_structured_relation_and_not_the_title(signed_in):
    """A Matter whose *title* says jõustumine carries no commencement."""
    factories.MatterFactory(title="Seaduse jõustumine ja rakendamine")

    response = signed_in.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == []


def test_puudub_finds_the_files_with_no_commencement(signed_in):
    commencing = factories.MatterFactory(title="Jõustuv")
    plain = factories.MatterFactory(title="Jõustumiseta")
    _commencement(commencing, start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 1))

    response = signed_in.get(
        REGISTER, {register_filters.COMMENCEMENT_PARAM: "puudub", "olek": "koik"}
    )
    assert titles_on(response) == [plain.title]


# --- the window -----------------------------------------------------------


def test_the_commencement_window_includes_both_ends(signed_in):
    """01.04–30.04 means April, including the 30th."""
    first = factories.MatterFactory(title="Esimene")
    last = factories.MatterFactory(title="Viimane")
    before = factories.MatterFactory(title="Enne")
    after = factories.MatterFactory(title="Pärast")
    for matter, day in (
        (first, datetime.date(2026, 4, 1)),
        (last, datetime.date(2026, 4, 30)),
        (before, datetime.date(2026, 3, 31)),
        (after, datetime.date(2026, 5, 1)),
    ):
        _commencement(matter, start=day, end=day)

    response = signed_in.get(
        REGISTER,
        {
            register_filters.COMMENCEMENT_START_PARAM: "1.4.2026",
            register_filters.COMMENCEMENT_END_PARAM: "30.4.2026",
            "olek": "koik",
        },
    )
    assert set(titles_on(response)) == {"Esimene", "Viimane"}


def test_an_open_ended_commencement_window_is_allowed(signed_in):
    soon = factories.MatterFactory(title="Tulemas")
    past = factories.MatterFactory(title="Ammu")
    _commencement(soon, start=datetime.date(2026, 6, 1), end=datetime.date(2026, 6, 1))
    _commencement(past, start=datetime.date(2019, 6, 1), end=datetime.date(2019, 6, 1))

    response = signed_in.get(
        REGISTER,
        {register_filters.COMMENCEMENT_START_PARAM: "1.1.2026", "olek": "koik"},
    )
    assert titles_on(response) == ["Tulemas"]


def test_a_quarter_is_inside_a_window_only_when_the_whole_quarter_is(signed_in):
    """The period rule, stated as the case that makes it matter.

    *III kvartal 2026* is a claim about a quarter. Asked for 01.07–31.08 the
    honest answer is no — it may well commence in September — and asked for the
    whole quarter it is yes. Reducing it to 01.07 and matching the first window
    would state a precision nobody recorded (master specification 3.5).
    """
    matter = factories.MatterFactory(title="Kolmas kvartal")
    _commencement(
        matter,
        start=datetime.date(2026, 7, 1),
        end=datetime.date(2026, 9, 30),
        date_precision=DatePrecision.QUARTER,
    )

    partial = signed_in.get(
        REGISTER,
        {
            register_filters.COMMENCEMENT_START_PARAM: "1.7.2026",
            register_filters.COMMENCEMENT_END_PARAM: "31.8.2026",
            "olek": "koik",
        },
    )
    assert titles_on(partial) == []

    whole = signed_in.get(
        REGISTER,
        {
            register_filters.COMMENCEMENT_START_PARAM: "1.7.2026",
            register_filters.COMMENCEMENT_END_PARAM: "30.9.2026",
            "olek": "koik",
        },
    )
    assert titles_on(whole) == ["Kolmas kvartal"]


@pytest.mark.parametrize("kind", [EffectiveDateKind.GENERAL_ORDER, EffectiveDateKind.UNKNOWN])
def test_an_undated_commencement_is_never_fabricated_into_a_window(signed_in, kind):
    """«Jõustub üldises korras» and «kuupäev täpsustamisel» carry no date at all,
    by database constraint. Neither bound can match one (Stage-2G brief 12, 14)."""
    matter = factories.MatterFactory(title="Kuupäevata jõustumine")
    _commencement(matter, kind=kind)

    windowed = signed_in.get(
        REGISTER,
        {
            register_filters.COMMENCEMENT_START_PARAM: "1.1.2000",
            register_filters.COMMENCEMENT_END_PARAM: "31.12.2099",
            "olek": "koik",
        },
    )
    assert titles_on(windowed) == []


@pytest.mark.parametrize("kind", [EffectiveDateKind.GENERAL_ORDER, EffectiveDateKind.UNKNOWN])
def test_an_undated_commencement_is_still_found_by_the_existence_filter(signed_in, kind):
    """Which is the honest place for a commencement nobody has dated."""
    matter = factories.MatterFactory(title="Kuupäevata jõustumine")
    _commencement(matter, kind=kind)

    response = signed_in.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == ["Kuupäevata jõustumine"]


def test_an_unreadable_commencement_date_empties_the_list_and_echoes_itself(signed_in):
    """A chip reading "31.02.2024" above the whole register is a lie the reader
    has no way to catch — the rule every other date filter here keeps."""
    factories.MatterFactory(title="Ükskõik")

    response = signed_in.get(
        REGISTER,
        {register_filters.COMMENCEMENT_START_PARAM: "31.02.2024", "olek": "koik"},
    )
    assert titles_on(response) == []
    assert response.context["filters"]["joustub_alates"] == "31.02.2024"


def test_the_window_needs_no_joustumine_parameter_beside_it(signed_in):
    """A window is itself an existence claim, so it stands alone."""
    matter = factories.MatterFactory(title="Aprillis")
    _commencement(matter, start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 1))

    response = signed_in.get(
        REGISTER,
        {
            register_filters.COMMENCEMENT_START_PARAM: "1.4.2026",
            register_filters.COMMENCEMENT_END_PARAM: "30.4.2026",
            "olek": "koik",
        },
    )
    assert titles_on(response) == ["Aprillis"]


# ---------------------------------------------------------------------------
# D. authorization — a restricted child may not be inferred
# ---------------------------------------------------------------------------


def test_a_restricted_matters_victory_does_not_reach_a_non_participant(client, reader):
    """The Matter is invisible, so nothing about it is answerable — including
    "does it carry a work victory".

    A ``READER`` rather than a second specialist, and deliberately: since
    docs/adr/0042 a specialist reads RESTRICTED work *by role*, so pointing this
    at one would assert nothing about the filter. `READER` is a real,
    authenticated colleague who genuinely may not (tests/conftest.py).
    """
    hidden = factories.MatterFactory(title="Salajane võit", visibility=Visibility.RESTRICTED)
    _victory(hidden)
    client.force_login(reader)

    found = client.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(found) == []

    absent = client.get(REGISTER, {register_filters.VICTORY_PARAM: "puudub", "olek": "koik"})
    assert titles_on(absent) == []


def test_a_child_restricted_below_a_readable_matter_leaks_neither_way(client, reader):
    """The case the #160/#164 hardening is about, asked of the new filters.

    The Matter is readable and the victory on it is not. It must not appear
    under `?toovoit=on` — that would disclose that a restricted record exists —
    and it must appear under `?toovoit=puudub`, because for this reader there is
    no visible victory on it and any other answer would be the same disclosure
    read backwards.
    """
    matter = factories.MatterFactory(title="Avalik teema")
    _victory(matter, visibility_override=Visibility.RESTRICTED)
    client.force_login(reader)

    found = client.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(found) == []

    absent = client.get(REGISTER, {register_filters.VICTORY_PARAM: "puudub", "olek": "koik"})
    assert titles_on(absent) == ["Avalik teema"]


def test_a_restricted_commencement_leaks_through_neither_filter_nor_window(client, reader):
    matter = factories.MatterFactory(title="Avalik teema")
    _commencement(
        matter,
        start=datetime.date(2026, 4, 1),
        end=datetime.date(2026, 4, 1),
        visibility_override=Visibility.RESTRICTED,
    )
    client.force_login(reader)

    found = client.get(REGISTER, {register_filters.COMMENCEMENT_PARAM: "on", "olek": "koik"})
    assert titles_on(found) == []

    windowed = client.get(
        REGISTER,
        {
            register_filters.COMMENCEMENT_START_PARAM: "1.4.2026",
            register_filters.COMMENCEMENT_END_PARAM: "30.4.2026",
            "olek": "koik",
        },
    )
    assert titles_on(windowed) == []


def test_a_participant_does_see_their_own_restricted_matters_facts(client, specialist):
    """The other half. Authorization narrows; it does not blanket-hide."""
    mine = factories.MatterFactory(
        title="Minu salajane", visibility=Visibility.RESTRICTED, owner=specialist
    )
    _victory(mine)
    client.force_login(specialist)

    response = client.get(REGISTER, {register_filters.VICTORY_PARAM: "on", "olek": "koik"})
    assert titles_on(response) == ["Minu salajane"]


def test_the_victory_year_options_never_name_a_restricted_only_year(client, reader):
    """A year offered in the control is a year the reader can actually open.

    Built from the visible population, so a year only a restricted Matter uses
    is not a filter option somebody can notice the absence of
    (Stage-2G brief 31).
    """
    hidden = factories.MatterFactory(title="Salajane", visibility=Visibility.RESTRICTED)
    _victory(hidden, year=2019)
    client.force_login(reader)

    response = client.get(REGISTER)
    assert 2019 not in response.context["victory_years"]


# ---------------------------------------------------------------------------
# E. the URL contract
# ---------------------------------------------------------------------------


def test_the_new_filters_compose_with_q_and_with_each_other(signed_in, specialist):
    """An intersection, like every other pair of register dimensions."""
    from app.search.indexing import indexable_matters, refresh_matters

    both = factories.MatterFactory(title="Pakendiseaduse eelnõu", owner=specialist)
    only_victory = factories.MatterFactory(title="Pakendiseaduse teine", owner=specialist)
    _victory(both)
    _victory(only_victory)
    _commencement(both, start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 1))
    refresh_matters(indexable_matters())

    response = signed_in.get(
        REGISTER,
        {
            "q": "pakendiseaduse",
            register_filters.VICTORY_PARAM: "on",
            register_filters.COMMENCEMENT_PARAM: "on",
            "ulatus": "minu",
            "olek": "koik",
        },
    )
    assert titles_on(response) == ["Pakendiseaduse eelnõu"]


def test_each_new_filter_renders_a_removable_chip(signed_in):
    response = signed_in.get(
        REGISTER,
        {
            register_filters.VICTORY_PARAM: "on",
            register_filters.COMMENCEMENT_PARAM: "on",
            register_filters.COMMENCEMENT_START_PARAM: "1.4.2026",
            register_filters.COMMENCEMENT_END_PARAM: "30.4.2026",
        },
    )
    chips = {chip["name"]: chip for chip in response.context["active_filters"]}

    assert chips["toovoit"]["value"] == "Töövõiduga"
    assert chips["joustumine"]["value"] == "Jõustumisega"
    assert chips["joustub_alates"]["value"] == "1.4.2026"
    assert chips["joustub_kuni"]["value"] == "30.4.2026"


def test_a_year_chip_reads_as_the_year(signed_in):
    response = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "2024"})
    chips = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}

    assert chips["toovoit"] == "2024"


def test_a_chip_removes_only_itself(signed_in):
    response = signed_in.get(
        REGISTER,
        {register_filters.VICTORY_PARAM: "on", register_filters.COMMENCEMENT_PARAM: "on"},
    )
    chips = {chip["name"]: chip["remove_query"] for chip in response.context["active_filters"]}

    assert "toovoit" not in chips["toovoit"]
    assert "joustumine" in chips["toovoit"]


def test_tuhjenda_koik_clears_them(signed_in):
    response = signed_in.get(
        REGISTER,
        {
            register_filters.VICTORY_PARAM: "on",
            register_filters.COMMENCEMENT_PARAM: "on",
            register_filters.COMMENCEMENT_START_PARAM: "1.4.2026",
            register_filters.COMMENCEMENT_END_PARAM: "30.4.2026",
        },
    )
    assert response.context["cleared_query"] == ""


def test_live_search_carries_them_forward(signed_in):
    """Typing into the register's box narrows the chosen filters rather than
    silently widening the population."""
    response = signed_in.get(
        REGISTER,
        {register_filters.VICTORY_PARAM: "on", register_filters.COMMENCEMENT_PARAM: "puudub"},
    )
    carried = dict(response.context["carried_params"])

    assert carried["toovoit"] == "on"
    assert carried["joustumine"] == "puudub"


def test_pagination_preserves_them(signed_in):
    for index in range(3):
        matter = factories.MatterFactory(title=f"Võidetud {index}")
        _victory(matter)

    response = signed_in.get(
        REGISTER,
        {register_filters.VICTORY_PARAM: "on", "olek": "koik", "suurus": "1"},
    )
    assert "toovoit=on" in response.context["query_string"]
    assert "leht" not in response.context["query_string"]


def test_the_panel_offers_both_dimensions(signed_in):
    """A dimension a link can set and the panel cannot is one somebody can
    arrive at and never reproduce (Stage-2E brief 38)."""
    body = signed_in.get(REGISTER).content.decode()

    assert 'name="toovoit"' in body
    assert 'name="joustumine"' in body
    assert 'name="joustub_alates"' in body
    assert 'name="joustub_kuni"' in body


def test_a_chosen_value_comes_back_selected_in_the_panel(signed_in):
    """Otherwise the next submit silently drops a filter the chip still claims."""
    body = signed_in.get(REGISTER, {register_filters.VICTORY_PARAM: "puudub"}).content.decode()
    start = body.index('<select class="field__input" name="toovoit">')
    block = body[start : body.index("</select>", start)]

    assert '<option value="puudub" selected>' in block.replace(" >", ">")


# ---------------------------------------------------------------------------
# F. the retired addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route,destination",
    [
        ("intelligence:work_victories", f"{REGISTER}?toovoit=on"),
        ("intelligence:effective_dates", f"{REGISTER}?joustumine=on"),
        ("intelligence:work_victories_legacy", f"{REGISTER}?toovoit=on"),
        ("intelligence:effective_dates_legacy", f"{REGISTER}?joustumine=on"),
    ],
)
def test_a_retired_fact_page_lands_on_the_register_filter_that_replaced_it(
    client, specialist, route, destination
):
    """One hop, and it resolves. The legacy addresses point straight at the new
    destination rather than at the `/jalgimine/` route, so no bookmark pays for
    two redirects and there is no chain to reason about."""
    client.force_login(specialist)
    response = client.get(reverse(route))

    assert response.status_code == 302
    assert response["Location"] == destination
    assert client.get(response["Location"]).status_code == 200


@pytest.mark.parametrize(
    "route", ["intelligence:important_dates", "intelligence:important_dates_legacy"]
)
def test_olulised_tahtajad_lands_on_the_readers_own_work(client, specialist, route):
    """A deadline belongs to whoever owns the file, so the destination is the
    reader's own queue rather than a list of everybody's."""
    client.force_login(specialist)
    response = client.get(reverse(route))

    assert response.status_code == 302
    assert response["Location"] == reverse("matters:my_work")
    assert client.get(response["Location"]).status_code == 200


def test_a_reader_with_no_persona_is_not_sent_somewhere_that_bounces(client, settings):
    """Behind the shared gate with nobody selected there is no «minu».

    ``matters:my_work`` is ``login_required`` and would bounce that reader to
    the persona page with no explanation of why the link they followed did not
    open, so they get the register — a real destination, holding the same
    Matters.
    """
    from app.accounts.enums import AuthMode

    password = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = password
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    assert client.post(reverse("accounts:shared_gate"), {"password": password}).status_code == 302

    response = client.get(reverse("intelligence:important_dates"))
    assert response["Location"] == REGISTER


def test_no_retired_address_redirects_to_another_redirect(client, specialist):
    """The one thing a family of redirects must not do."""
    client.force_login(specialist)
    for route in (
        "intelligence:important_dates",
        "intelligence:effective_dates",
        "intelligence:work_victories",
        "intelligence:important_dates_legacy",
        "intelligence:effective_dates_legacy",
        "intelligence:work_victories_legacy",
    ):
        first = client.get(reverse(route))
        assert first.status_code == 302, route
        assert client.get(first["Location"]).status_code == 200, route


def test_the_write_surfaces_on_the_matter_page_are_untouched(client, specialist):
    """Retiring a reading page is not deleting a business fact.

    Every route that records, corrects or cancels one of these facts still
    resolves — and still under the Matter it belongs to.
    """
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    for name in ("add_important_date", "add_effective_date", "add_work_victory"):
        url = reverse(f"intelligence:{name}", kwargs={"matter_id": matter.pk})
        assert client.get(url).status_code == 200, name


# ---------------------------------------------------------------------------
# G. cost
# ---------------------------------------------------------------------------


def test_the_new_filters_do_not_cost_a_query_per_row(signed_in, django_assert_max_num_queries):
    """Bounded database work, not a Python loop over the register.

    Both filters are one correlated ``EXISTS`` for the whole page, so the number
    of queries does not move with the number of Matters that match.
    """
    for index in range(20):
        matter = factories.MatterFactory(title=f"Võidetud {index}")
        _victory(matter)
        _commencement(matter, start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 1))

    with django_assert_max_num_queries(40):
        response = signed_in.get(
            REGISTER,
            {
                register_filters.VICTORY_PARAM: "on",
                register_filters.COMMENCEMENT_PARAM: "on",
                register_filters.COMMENCEMENT_START_PARAM: "1.4.2026",
                register_filters.COMMENCEMENT_END_PARAM: "30.4.2026",
                "olek": "koik",
            },
        )
    assert response.context["total"] == 20
