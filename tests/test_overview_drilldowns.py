"""Every clickable number on Ülevaade opens exactly the rows it claims.

What this file is for
---------------------
Ülevaade is a page of counts, and each one is a promise that a list exists
behind it holding precisely that many things. The promise was kept in some
places and quietly broken in others, always the same way: the number came from
one query and the link from a second, similar one.

The failures were not subtle once they were looked for.

* *N üle tähtaja* counted late **work items** and opened a list of **Matters**,
  so two missed deadlines on one file promised two rows and produced one.
* *N valdkonda vastutajata* counted **policy areas** and opened every ownerless
  **Matter** — three areas, eleven files.
* *N inimest* on the since-retired Minu tiim opened the whole register.
* The team strip summed per-person counts, which silently drops every unowned
  file, and linked to a register list that includes them.
* *Esitatud arvamusi 2026* and *Suletud teemasid 2026* carried the year in the
  label and not in the link.
* *Näita kõiki 41* under Vajab sekkumist carried ``sekkumine=hilinenud``.
* *Näita ülejäänud 3* under Tähtajad opened the whole register sorted by date.

So this suite does not test one figure. It walks the page's own object model,
collects every ``(count, destination)`` pair the templates render, and asks the
destination — through the real view, so what is asserted is the number the
reader actually sees — whether it holds that many rows. A drill-down added
later without a matching population fails here rather than being discovered by
somebody who trusted it.

Two destinations are legitimately not the register: ``/arvamused/`` lists
canonical Submissions, and two figures count things the register does not hold
at all (people, policy areas) and therefore open the list of those on this page.
Both are asserted for what they are rather than exempted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import overview as ov
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.services import close_matter, create_matter
from app.submissions.enums import SubmissionStatus
from app.taxonomy.models import PolicyArea
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = "/teemad/"
OPINIONS = "/arvamused/"


@dataclass(frozen=True)
class Claim:
    """One number on the page and the destination printed beside it."""

    where: str
    count: int
    url: str


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def world(db, department_head, specialist, other_specialist, today):
    """One Matter of every shape the page counts, plus rows that must not count.

    Deliberately more than the minimum. Every population needs at least one
    member and at least one near-miss, or a filter that matched everything would
    pass this suite.
    """
    watched, quiet_area, unwatched = list(
        PolicyArea.objects.filter(is_active=True).order_by("sort_order")[:3]
    )

    # Late in two different ways, on one Matter: two work items, one row in the
    # register. This is the pair that made the old figure and its list disagree.
    doubly_late = create_matter(
        title="Kaks möödunud tähtaega", owner=specialist, reference_year=2026, actor=specialist
    )
    doubly_late.policy_areas.add(watched)
    set_next_action(
        matter=doubly_late,
        text="Hilinenud tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=6),
        actor=specialist,
    )
    add_important_date(
        matter=doubly_late,
        title="Möödunud oluline tähtaeg",
        date_value=today - timedelta(days=3),
        period_end=today - timedelta(days=3),
        actor=specialist,
    )

    # Late work whose responsible is not the owner: the two questions the rail
    # prints side by side rather than summing.
    delegated = create_matter(
        title="Hilinenud volitatud tegevus",
        owner=specialist,
        reference_year=2026,
        actor=specialist,
    )
    delegated.policy_areas.add(watched)
    set_next_action(
        matter=delegated,
        text="Teise inimese hilinenud samm",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=2),
        responsible=other_specialist,
        actor=specialist,
    )

    # Ripe for a look, never late.
    ripe = create_matter(
        title="Ülevaatamiseks küps", owner=specialist, reference_year=2026, actor=specialist
    )
    ripe.policy_areas.add(quiet_area)
    set_next_action(
        matter=ripe,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - timedelta(days=4),
        actor=specialist,
    )

    # A deadline inside each of the two windows the Tähtajad table shows.
    week_end = wi.end_of_iso_week(today)
    this_week = create_matter(
        title="Selle nädala tähtaeg", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=this_week,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )
    next_week = create_matter(
        title="Järgmise nädala tähtaeg", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=next_week,
        text="Esita teine arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=week_end + timedelta(days=3),
        actor=specialist,
    )

    # No instruction at all, and no owner at all: the two undated attention
    # states, which no date-based population can produce.
    quiet = create_matter(
        title="Järgmise tegevuseta", owner=other_specialist, reference_year=2026, actor=specialist
    )
    quiet.policy_areas.add(quiet_area)
    ownerless = create_matter(
        title="Vastutajata teema",
        owner=None,
        reference_year=2026,
        actor=specialist,
        received_date=today - timedelta(days=1),
    )
    ownerless.policy_areas.add(unwatched)

    # A Matter closed this year, an archive row and a restricted one: three
    # kinds of row that must not leak into an open-work count.
    close_matter(
        matter=create_matter(title="Suletud teema", owner=specialist, actor=specialist),
        disposition=Disposition.COMPLETED,
        actor=specialist,
    )
    factories.ArchiveMatterFactory(title="Arhiivirida")
    create_matter(
        title="Piiratud teema",
        owner=specialist,
        visibility=Visibility.RESTRICTED,
        reference_year=2026,
        actor=specialist,
        response_deadline=today + timedelta(days=2),
    )

    # One opinion being written, so the new figure has a member.
    factories.SubmissionFactory(matter=ripe, status=SubmissionStatus.DRAFT)
    return {
        "doubly_late": doubly_late,
        "delegated": delegated,
        "ripe": ripe,
        "quiet": quiet,
        "ownerless": ownerless,
        "watched": watched,
    }


# ---------------------------------------------------------------------------
# Walking the page
# ---------------------------------------------------------------------------


def claims_for(page: ov.Overview) -> list[Claim]:
    """Every ``(number, destination)`` pair the templates render for one scope.

    Read off the page object rather than out of the HTML, because a template
    renders ``{{ row.count }}`` beside ``{{ row.url }}`` and those are the two
    values that have to agree. Parsing the HTML would test the parser.
    """
    claims: list[Claim] = []

    for figure in page.figures:
        claims.append(Claim(f"seis:{figure.key}", figure.count, figure.url))

    if page.intervention_url:
        claims.append(Claim("vajab-sekkumist", page.intervention_matters, page.intervention_url))

    for group in page.deadlines:
        claims.append(Claim(f"tahtajad:{group.key}", group.matter_count, group.url))

    for person in page.loads:
        claims.append(Claim(f"koormus:{person.name}:avatud", person.open_count, person.url))
        claims.append(Claim(f"koormus:{person.name}:hilinenud", person.overdue, person.overdue_url))
        claims.append(
            Claim(f"koormus:{person.name}:tegevuseta", person.no_action, person.no_action_url)
        )

    for row in page.areas:
        claims.append(Claim(f"valdkond:{row.key}:avatud", row.open_count, row.url))
        claims.append(Claim(f"valdkond:{row.key}:hilinenud", row.overdue, row.overdue_url))
        claims.append(Claim(f"valdkond:{row.key}:tegevuseta", row.no_action, row.no_action_url))
        if row.is_unowned:
            claims.append(
                Claim(f"valdkond:{row.key}:vastutajata", row.open_count, row.unassigned_url)
            )

    for name, rows in (
        ("valdkonnad-rail", page.area_rail),
        ("uued-teemad", page.incoming),
        ("asutused", page.organisations),
        ("aruandlus", page.reporting),
    ):
        for row in rows:
            if row.url:
                claims.append(Claim(f"{name}:{row.label}", row.count, row.url))

    return claims


def list_claims(page: ov.Overview) -> list[Claim]:
    """The claims whose destination is a list surface with a total of its own."""
    return [
        claim
        for claim in claims_for(page)
        if urlparse(claim.url).path.startswith((REGISTER, OPINIONS))
    ]


def register_claims(page: ov.Overview) -> list[Claim]:
    return [claim for claim in claims_for(page) if urlparse(claim.url).path.startswith(REGISTER)]


def shown_total(client, url: str) -> int:
    """What the destination itself says it is showing.

    The view's own context, not a queryset rebuilt here. A test that recomputed
    the population would prove two similar conditions agree with each other and
    say nothing about the page.
    """
    response = client.get(url)
    assert response.status_code == 200, url
    return response.context["total"]


def rows_at(client, url: str) -> set[str]:
    return {matter.title for matter in client.get(url).context["page"].object_list}


ALL_SCOPES = [ov.SCOPE_DEPARTMENT, ov.SCOPE_AREAS]


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_every_drilldown_opens_exactly_what_it_counted(
    client, department_head, world, today, scope
):
    """The whole complaint, asserted once per destination on the page.

    Both list destinations are covered: Teemad and the Arvamused workspace. The
    one figure that opens a list *on this page* — unowned areas — is asserted
    separately, because there is no register total to compare against.
    """
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=scope, today=today)
    claims = list_claims(page)

    assert claims, f"{scope} rendered no list destinations at all"
    for claim in claims:
        assert shown_total(client, claim.url) == claim.count, (
            f"{scope}/{claim.where}: the page says {claim.count}, {claim.url} does not"
        )


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_every_drilldown_lands_on_the_results(department_head, world, today, scope):
    """A register link that lands above the rows makes the reader hunt for them."""
    page = ov.build_overview(department_head, scope=scope, today=today)

    for claim in register_claims(page):
        assert claim.url.endswith(ov.RESULTS_ANCHOR), f"{scope}/{claim.where}"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_every_drilldown_carries_a_filter(department_head, world, today, scope):
    """None of them may open the bare register, whatever the number happens to be."""
    page = ov.build_overview(department_head, scope=scope, today=today)

    for claim in register_claims(page):
        assert urlparse(claim.url).query, f"{scope}/{claim.where} opens an unfiltered register"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_the_filter_is_visible_on_arrival(client, department_head, world, today, scope):
    """Arriving from a number, the reader can see *why* this set is on screen.

    A filtered list with no visible chip is indistinguishable from a broken
    register, and the reader has no way back to the whole list.
    """
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=scope, today=today)

    for claim in register_claims(page):
        response = client.get(claim.url)
        assert response.context["has_any_filter"] is True, f"{scope}/{claim.where}"
        assert response.context["active_filters"], f"{scope}/{claim.where}"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_a_restricted_matter_is_absent_from_both_halves(client, other_specialist, world, scope):
    """Hiding a row while leaving it in the total tells the reader it exists.

    Asserted across every drill-down at once, because the property belongs to
    the page rather than to any one figure: both halves come from
    ``visible_to``, so neither may leak.
    """
    client.force_login(other_specialist)
    page = ov.build_overview(other_specialist, scope=scope)

    for claim in register_claims(page):
        assert "Piiratud teema" not in rows_at(client, claim.url), f"{scope}/{claim.where}"


# ---------------------------------------------------------------------------
# The specific breakages, named
# ---------------------------------------------------------------------------


def test_the_overdue_figure_counts_matters_and_not_late_rows(department_head, world, today):
    """One Matter with two missed deadlines is one row in the list it opens."""
    items = wi.work_items(department_head, today=today)
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    figure = next(f for f in page.figures if f.key == "overdue")

    assert len(wi.overdue_items(items)) == 3, (
        "the fixture no longer holds two late rows on one file"
    )
    assert figure.count == 2


def test_the_unowned_areas_figure_opens_the_list_of_areas(client, department_head, world, today):
    """It counts policy areas, so it may not open a list of Matters."""
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=ov.SCOPE_AREAS, today=today)
    figure = next(f for f in page.figures if f.key == "unowned")

    assert not urlparse(figure.url).path.startswith(REGISTER)
    assert figure.url.endswith(ov.UNOWNED_ANCHOR)
    assert figure.count == len(page.unowned_areas)

    body = client.get(reverse("matters:overview") + figure.url).content.decode()
    assert 'id="vastutajata-valdkonnad"' in body


def test_the_strip_counts_the_department_and_not_the_sum_of_the_rail_rows(
    client, department_head, world, today
):
    """Summing per-person counts drops every unowned file.

    The rail lists those separately as *Vastutajata*, so the strip was short by
    exactly that many while the register link beside it was not. Asserted on
    Kogu osakond's Koormus rail, which is where the per-person rows live now
    that Minu tiim is retired (docs/adr/0039).
    """
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    figure = next(f for f in page.figures if f.key == "open")

    assert page.unassigned >= 1
    assert figure.count == sum(person.open_count for person in page.loads) + page.unassigned
    assert shown_total(client, figure.url) == figure.count


def test_a_persons_overdue_link_asks_about_responsibility_not_ownership(
    client, department_head, world, other_specialist, today
):
    """A late instruction belongs to whoever must do it, not to the file's owner.

    The old link said ``?vastutaja=``, which asks who owns the Matter — so a
    colleague carrying one delegated late step and owning a different file
    opened the wrong list under a number that read 1.
    """
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    person = next(load for load in page.loads if load.user.pk == other_specialist.pk)

    assert person.overdue == 1
    # The file they own carries no late work at all, which is the whole point.
    assert person.open_count == 1
    assert rows_at(client, person.overdue_url) == {"Hilinenud volitatud tegevus"}
    assert rows_at(client, person.url) == {"Järgmise tegevuseta"}


def test_the_deadline_group_link_holds_that_window_and_not_the_register(
    client, department_head, world, today
):
    """ "Näita ülejäänud 3" opened the whole register sorted by date."""
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    groups = {group.key: group for group in page.deadlines}

    assert rows_at(client, groups["jargmisel"].url) == {"Järgmise nädala tähtaeg"}
    assert "Selle nädala tähtaeg" in rows_at(client, groups["sel_nadalal"].url)


def test_the_intervention_link_holds_every_reason_at_once(client, department_head, world, today):
    """Vajab sekkumist mixes four kinds of trouble; its link must hold all four."""
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    titles = rows_at(client, page.intervention_url)

    assert {
        "Kaks möödunud tähtaega",
        "Hilinenud volitatud tegevus",
        "Ülevaatamiseks küps",
        "Järgmise tegevuseta",
        "Vastutajata teema",
    } <= titles
    assert "Selle nädala tähtaeg" not in titles


def test_show_all_under_the_intervention_list_shows_all_of_it(department_head, world, today):
    """The footer promised the whole list and opened the late rows only."""
    whole = ov.build_overview(
        department_head,
        scope=ov.SCOPE_DEPARTMENT,
        today=today,
        intervention_filter=ov.INTERVENTION_ALL,
    )
    unfiltered = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)

    assert whole.intervention_total == unfiltered.intervention_total
    assert len(whole.intervention_preview) == whole.intervention_total


def test_the_area_footer_opens_every_area_including_the_empty_ones(department_head, world, today):
    """A number of areas opens a list of areas, and all of them are in it."""
    folded = ov.build_overview(department_head, scope=ov.SCOPE_AREAS, today=today)
    expanded = ov.build_overview(
        department_head, scope=ov.SCOPE_AREAS, today=today, show_empty_areas=True
    )

    assert folded.empty_areas > 0
    assert len(expanded.areas) == folded.area_total


def test_the_reporting_rail_carries_its_year_into_the_link(client, department_head, world, today):
    """*Suletud teemasid 2026* opened every closed Matter there has ever been."""
    client.force_login(department_head)
    close_matter(
        matter=create_matter(title="Ammu suletud", owner=None, actor=department_head),
        disposition=Disposition.COMPLETED,
        actor=department_head,
    )
    Matter.objects.filter(title="Ammu suletud").update(
        closed_at=timezone.now() - timedelta(days=800)
    )

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    row = next(row for row in page.reporting if row.label.startswith("Suletud"))

    assert rows_at(client, row.url) == {"Suletud teema"}
    assert shown_total(client, row.url) == row.count


def test_the_drafting_figure_counts_canonical_opinions_being_written(
    client, department_head, world, today
):
    """Canonical Submissions in DRAFT, and nothing that merely looks like one.

    Not the register's VÄLJA column, which is what the Excel era knew about a
    sent date, and not the historical archive — 767 letters that were sent
    rather than written, and that are not Submission rows at all.
    """
    client.force_login(department_head)
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    figure = next(f for f in page.figures if f.key == "drafting")

    assert figure.count == 1
    assert urlparse(figure.url).path == OPINIONS
    assert shown_total(client, figure.url) == 1

    response = client.get(figure.url)
    listed = {submission.status for submission in response.context["page"].object_list}
    assert listed == {SubmissionStatus.DRAFT}
