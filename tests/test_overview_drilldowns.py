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
* *Saadetud arvamusi 2026* and *Suletud teemasid 2026* carried the year in the
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

Since ADR 0049 the department scope of `build_overview` is a read model rather
than a rendered page: `/osakond/` composes `intervention_rows`, `area_rows` and
the rest of it alongside `department_dashboard`'s own populations. The walk is
kept over both scopes, because those selectors are still what the page renders
with — and the figures the *merged* page prints are walked the same way in
`tests/test_department_page.py`, against the object that page actually builds.

**Counting is half of it.** A destination holding the right *number* of the
wrong Matters keeps every assertion above green, and that is not a hypothetical
shape: the two figures that came apart historically — *N üle tähtaja* and
*N valdkonda vastutajata* — were both wrong by identity long before anybody
counted them, and one of them was briefly wrong by identity alone. So the walk
also carries, for every register drill-down, the set of Matter ids the
*canonical* population holds, and asserts the destination lists exactly those.

The expected set is never a filter rewritten here. Each claim names the read
model that produced its figure — `Populations.open_matters`, `Populations.quiet`,
`wi.work_population_ids` — and the set is that population narrowed the way the
count was narrowed. The three-way assertion is what keeps it honest: the page's
number, the size of the canonical set, and the rows the destination lists must
all agree, so a test-side derivation that drifted from the production count
fails here rather than quietly redefining the contract it is checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import pytest
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import overview as ov
from app.matters import work_items as wi
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
    """One number on the page, the destination printed beside it, and who is in it.

    ``expected`` is the set of Matter ids the *canonical* population holds — the
    read model the figure was counted from, narrowed the way the count was
    narrowed, never a filter restated here. It is what turns "the destination
    holds N rows" into "the destination holds exactly these rows".
    """

    where: str
    count: int
    url: str
    expected: frozenset[int]


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
    watched, quiet_area, unwatched, decoy = list(
        PolicyArea.objects.filter(is_active=True).order_by("sort_order")[:4]
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

    # A deadline in this calendar week, and one past the Sunday it ends on. The
    # second lands in *Ülejäänud kuu* or, in the week that runs past the month
    # end, in *Kaugemal* — which window is not the point, and the assertion
    # below finds it rather than naming it (`dd.upcoming_windows`).
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

    # A ministry two of these arrived from, so the Asutused rail renders a row
    # and the walk actually asks about it. Without one, that whole branch of
    # the page is walked over an empty list and asserts nothing.
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    doubly_late.source_organisations.add(ministry)
    ripe.source_organisations.add(ministry)

    # The shape that makes the identity assertion do work. One area carrying two
    # late Matters and two uninstructed ones: *üle tähtaja* and *järgmise
    # tegevuseta* print the same number, and the sets behind them share nothing.
    # Swap the two destinations and every count on the page still adds up.
    decoy_overdue = []
    decoy_quiet = []
    for index in range(2):
        late = create_matter(
            title=f"Peibutis hilinenud {index}",
            owner=specialist,
            reference_year=2026,
            actor=specialist,
        )
        late.policy_areas.add(decoy)
        set_next_action(
            matter=late,
            text="Peibutise hilinenud samm",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today - timedelta(days=5 + index),
            actor=specialist,
        )
        decoy_overdue.append(late)

        idle = create_matter(
            title=f"Peibutis tegevuseta {index}",
            owner=specialist,
            reference_year=2026,
            actor=specialist,
        )
        idle.policy_areas.add(decoy)
        decoy_quiet.append(idle)

    return {
        "doubly_late": doubly_late,
        "delegated": delegated,
        "ripe": ripe,
        "quiet": quiet,
        "ownerless": ownerless,
        "watched": watched,
        "ministry": ministry,
        "decoy": decoy,
        "decoy_overdue": decoy_overdue,
        "decoy_quiet": decoy_quiet,
    }


# ---------------------------------------------------------------------------
# Walking the page
# ---------------------------------------------------------------------------


def _ids(queryset) -> frozenset[int]:
    return frozenset(queryset.values_list("pk", flat=True))


def _organisation_members(open_matters) -> dict[str, frozenset[int]]:
    """Which Matters each canonical organisation's figure was counted from.

    Grouped in Python off the same ``source_organisations`` relation
    ``organisation_ranking`` counts on, because that is what makes this the
    figure's own population rather than a second query that resembles it.

    Keyed by the name, which is what the row prints. Two canonical
    organisations sharing a name would merge here — and would then fail the
    size assertion below rather than pass quietly, which is the right way round
    for a case the import is supposed to make impossible.
    """
    members: dict[str, set[int]] = {}
    for name, pk in open_matters.filter(source_organisations__isnull=False).values_list(
        "source_organisations__name", "pk"
    ):
        members.setdefault(name, set()).add(pk)
    return {name: frozenset(pks) for name, pks in members.items()}


def claims_for(page: ov.Overview, pop: ov.Populations, items: list[wi.WorkItem]) -> list[Claim]:
    """Every ``(number, destination, population)`` triple one scope renders.

    Read off the page object rather than out of the HTML, because a template
    renders ``{{ row.count }}`` beside ``{{ row.url }}`` and those are the two
    values that have to agree. Parsing the HTML would test the parser.

    The third value is taken from the read models the page itself counted
    through — ``Populations.open_matters``, ``Populations.quiet`` and the shared
    work model's own ``work_population_ids`` — narrowed by the same area or
    organisation the figure was narrowed by. Nothing here restates a register
    filter or invents a second idea of *late*: the one definition of overdue is
    still :mod:`app.matters.work_items`, asked for its ids instead of its rows.
    """
    open_matters = pop.open_matters
    # The identical expression `area_rows` narrows the overdue column with,
    # through the selector that names it, so the set and the count cannot come
    # from two different readings of the same word.
    overdue_ids = wi.work_population_ids(pop.user, wi.WORK_OVERDUE, today=page.today, items=items)

    claims: list[Claim] = []

    for row in page.areas:
        in_area = open_matters.filter(policy_areas__key=row.key)
        claims.append(Claim(f"valdkond:{row.key}:avatud", row.open_count, row.url, _ids(in_area)))
        claims.append(
            Claim(
                f"valdkond:{row.key}:hilinenud",
                row.overdue,
                row.overdue_url,
                _ids(in_area.filter(pk__in=overdue_ids)),
            )
        )
        claims.append(
            Claim(
                f"valdkond:{row.key}:tegevuseta",
                row.no_action,
                row.no_action_url,
                _ids(pop.quiet.filter(policy_areas__key=row.key)),
            )
        )
        if row.is_unowned:
            # `is_unowned` means *nobody at all* owns work here, so the area's
            # unassigned list is the area's open list. That equality is the
            # figure's whole claim, and it is asserted rather than assumed:
            # an area where one file among ten is unowned is not unowned.
            claims.append(
                Claim(
                    f"valdkond:{row.key}:vastutajata",
                    row.open_count,
                    row.unassigned_url,
                    _ids(in_area),
                )
            )

    by_organisation = _organisation_members(open_matters)
    for row in page.organisations:
        if row.url:
            claims.append(
                Claim(
                    f"asutused:{row.label}",
                    row.count,
                    row.url,
                    by_organisation.get(row.label, frozenset()),
                )
            )

    return claims


def walk(user, today, *, scope: str = ov.SCOPE_AREAS, **kwargs) -> tuple[ov.Overview, list[Claim]]:
    """Build one scope and collect every claim on it, from one set of populations.

    `build_overview` resolves the reader's populations and reads the work model
    itself; both are handed in here so the claims are derived from the *same*
    objects the page was built from rather than from a second, similar read.
    """
    pop = ov.Populations.for_user(user)
    items = wi.work_items(user, today=today)
    page = ov.build_overview(user, scope=scope, today=today, items=items, **kwargs)
    return page, claims_for(page, pop, items)


def list_claims(claims: list[Claim]) -> list[Claim]:
    """The claims whose destination is a list surface with a total of its own."""
    return [claim for claim in claims if urlparse(claim.url).path.startswith((REGISTER, OPINIONS))]


def register_claims(claims: list[Claim]) -> list[Claim]:
    return [claim for claim in claims if urlparse(claim.url).path.startswith(REGISTER)]


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


def _at_page(url: str, number: int) -> str:
    """The same destination, one page further in. Fragment and filters intact."""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "leht"
    ]
    query.append(("leht", str(number)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def destination_ids(client, url: str) -> frozenset[int]:
    """Every Matter the destination lists, read through the real view.

    All of its pages, not the first one. The register paginates at twelve, so a
    population that outgrows a page would otherwise start "agreeing" with any
    expectation whose first twelve rows happened to match — which is the same
    class of false pass this file exists to close.
    """
    response = client.get(url)
    assert response.status_code == 200, url
    found: set[int] = set()
    for number in range(1, response.context["paginator"].num_pages + 1):
        page = client.get(_at_page(url, number)).context["page"]
        found.update(matter.pk for matter in page.object_list)
    return frozenset(found)


#: One scope now. The department scope of `build_overview` is gone with the
#: page it fed, and the merged page's own figures are walked the same way in
#: `tests/test_department_page.py`, against the object that page builds.
ALL_SCOPES = [ov.SCOPE_AREAS]


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
    _, claims = walk(department_head, today, scope=scope)
    claims = list_claims(claims)

    assert claims, f"{scope} rendered no list destinations at all"
    for claim in claims:
        assert shown_total(client, claim.url) == claim.count, (
            f"{scope}/{claim.where}: the page says {claim.count}, {claim.url} does not"
        )


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_every_drilldown_opens_exactly_those_matters(client, department_head, world, today, scope):
    """The other half of the promise: not *how many*, but *which*.

    A list holding the right number of the wrong Matters satisfies every count
    above and is exactly as wrong as a list holding too few. It is also the
    likelier defect, because the two ways a figure and its link come apart —
    one is narrowed by a column the other does not know about, or the two
    narrow on conditions that merely correlate — both preserve the count far
    more often than they preserve the membership.

    Three values, not two. The page's number, the size of the canonical
    population, and the ids the destination lists must all agree: the middle one
    is what stops this test from quietly grading its own homework, because a
    derivation here that drifted from the way the figure is actually counted
    fails on the first assertion instead of redefining the contract.
    """
    client.force_login(department_head)
    _, claims = walk(department_head, today, scope=scope)
    claims = register_claims(claims)

    assert claims, f"{scope} rendered no register destinations at all"
    for claim in claims:
        assert len(claim.expected) == claim.count, (
            f"{scope}/{claim.where}: the page counted {claim.count} but the population "
            f"it was counted from holds {len(claim.expected)} — the expectation below "
            "is derived from a different question than the figure asks"
        )
        assert destination_ids(client, claim.url) == claim.expected, (
            f"{scope}/{claim.where}: {claim.url} lists the wrong Matters. The count "
            "may well be right; the rows are not the ones the figure counted."
        )


def test_the_identity_check_is_what_catches_a_swapped_destination(
    client, department_head, world, today
):
    """Proof that the assertion above is not decorative.

    The Peibutis area carries two late Matters and two uninstructed ones, so its
    *üle tähtaja* and *järgmise tegevuseta* figures print the same number over
    populations that share nothing. Point either figure at the other's list and
    the page still adds up perfectly — every total matches, every filter chip
    renders, every link lands on the results.

    So this asserts the interchange is real (same count) and that identity is
    the only thing that distinguishes them (disjoint sets). No production code
    is patched to stage the failure: the fixture makes the two destinations
    genuinely substitutable, and only `expected` can tell them apart.
    """
    client.force_login(department_head)
    _, claims = walk(department_head, today)
    key = world["decoy"].key
    by_where = {claim.where: claim for claim in claims}
    overdue = by_where[f"valdkond:{key}:hilinenud"]
    idle = by_where[f"valdkond:{key}:tegevuseta"]

    assert overdue.count == idle.count > 0, (
        "the decoy area no longer prints two equal figures, so this test proves nothing"
    )
    assert not (overdue.expected & idle.expected), "the two decoy populations overlap"

    # Interchangeable by every assertion in this file except one...
    assert shown_total(client, idle.url) == overdue.count
    assert shown_total(client, overdue.url) == idle.count

    # ...and told apart only here.
    assert destination_ids(client, idle.url) != overdue.expected
    assert destination_ids(client, overdue.url) != idle.expected


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_every_drilldown_lands_on_the_results(department_head, world, today, scope):
    """A register link that lands above the rows makes the reader hunt for them."""
    _, claims = walk(department_head, today, scope=scope)

    for claim in register_claims(claims):
        assert claim.url.endswith(ov.RESULTS_ANCHOR), f"{scope}/{claim.where}"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_every_drilldown_carries_a_filter(department_head, world, today, scope):
    """None of them may open the bare register, whatever the number happens to be."""
    _, claims = walk(department_head, today, scope=scope)

    for claim in register_claims(claims):
        assert urlparse(claim.url).query, f"{scope}/{claim.where} opens an unfiltered register"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_the_filter_is_visible_on_arrival(client, department_head, world, today, scope):
    """Arriving from a number, the reader can see *why* this set is on screen.

    A filtered list with no visible chip is indistinguishable from a broken
    register, and the reader has no way back to the whole list.
    """
    client.force_login(department_head)
    _, claims = walk(department_head, today, scope=scope)

    for claim in register_claims(claims):
        response = client.get(claim.url)
        assert response.context["has_any_filter"] is True, f"{scope}/{claim.where}"
        assert response.context["active_filters"], f"{scope}/{claim.where}"


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_a_restricted_matter_is_absent_from_both_halves(client, reader, world, scope):
    """Hiding a row while leaving it in the total tells the reader it exists.

    Asserted across every drill-down at once, because the property belongs to
    the page rather than to any one figure: both halves come from
    ``visible_to``, so neither may leak.
    """
    client.force_login(reader)
    _, claims = walk(reader, timezone.localdate(), scope=scope)

    for claim in register_claims(claims):
        assert "Piiratud teema" not in rows_at(client, claim.url), f"{scope}/{claim.where}"


# ---------------------------------------------------------------------------
# The specific breakages, named
# ---------------------------------------------------------------------------


def test_the_area_footer_opens_every_area_including_the_empty_ones(department_head, world, today):
    """A number of areas opens a list of areas, and all of them are in it."""
    folded = ov.build_overview(department_head, scope=ov.SCOPE_AREAS, today=today)
    expanded = ov.build_overview(
        department_head, scope=ov.SCOPE_AREAS, today=today, show_empty_areas=True
    )

    assert folded.empty_areas > 0
    assert len(expanded.areas) == folded.area_total
