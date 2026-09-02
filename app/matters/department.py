"""Osakond — one page for «kus osakond seisab», composed from what already answers it.

The department had two operational pages. *Ülevaade* answered "where is the
department losing time" for every reader; *Osakonna töö* answered "what is the
team doing, what is ahead, what is done" for the head. They asked one question
between them and printed the same number in two places: two Seis strips sharing
three figures, two deadline panels over one ``real_deadlines`` population, an
intervention list in one page's main column and again in the other's rail, and
an Aruandlus block on both. This module is what replaced them (docs/adr/0049).

**It is a composition layer and nothing else.** Every population here is read by
the function that already owned it — :mod:`app.matters.department_dashboard` for
the strip, the team table, *Eesolev* and *Tehtud*, :mod:`app.matters.overview`
for the intervention list and the area rail. Nothing is recomputed, no state is
cached, no model was added and there is no second work-item system. A definition
written a second time here is how two screens start disagreeing about the same
Matter, which is the failure the merge exists to end rather than to relocate.

Three rules run through it, all of them inherited rather than invented.

**Authorization before arithmetic.** Every population begins at
``visible_to(viewer)``. In shared-gate mode the viewer is the department
sentinel :func:`app.core.decorators.viewer_for` resolves, exactly as Ülevaade
resolved it — NORMAL visibility and no participation, so nothing RESTRICTED
appears because a shared password was typed.

**Manager sections are decided by role, and then not calculated.** *Meeskond*
and *Tehtud* were Osakonna töö's, and they stay the head's. For anybody else
they are not hidden in the template — they are never built, so a specialist's
request does not run the nine grouped team queries or read the digest at all.
The authority for that comes from the real authenticated role
(``is_department_head``), never from the pseudo-viewer the shared gate hands the
rest of the page.

**Every number opens the list it counted.** Rows are work items and
drill-through populations are Matters, and the two are printed as the different
numbers they are: *Vajab sekkumist* can hold forty-one rows across thirty-three
files, and a heading that said "41" over a list of thirty-three would be the
failure this discipline exists to prevent (master specification 18.9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.utils import timezone

from app.matters import department_dashboard as dd
from app.matters import overview as ov
from app.matters import work_items as wi

#: How many *Vajab sekkumist* rows stand above the disclosure. Five, which is
#: what frame C draws; the rest open in place on the same page, so a scan never
#: costs a page load (design handoff C §3.3).
INTERVENTION_PREVIEW = 5

#: How many area rows the *Valdkonnad* rail lists before «Kõik valdkonnad →».
AREA_RAIL_ROWS = 5


@dataclass
class Department:
    """One rendered department page, in one scope, for one authorized reader."""

    scope: str
    today: date
    #: Whether the manager-only sections were built. False means they were never
    #: computed, not that they were computed and withheld.
    is_head: bool = False
    open_matters: int = 0
    seis: list[dd.SeisFigure] = field(default_factory=list)

    # Kogu osakond
    team: list[dd.TeamRow] = field(default_factory=list)
    previous_week_label: str = ""
    interventions: list[ov.InterventionRow] = field(default_factory=list)
    #: Work rows, uncapped. Deliberately a different number from
    #: :attr:`intervention_matters`: one Matter that is both late and unowned is
    #: two rows and one file to open.
    intervention_total: int = 0
    intervention_matters: int = 0
    intervention_url: str = ""
    upcoming: list[dd.UpcomingGroup] = field(default_factory=list)
    digest: dd.Digest | None = None
    periods: list[dd.PeriodOption] = field(default_factory=list)

    # The rail
    areas: list[ov.CountRow] = field(default_factory=list)
    incoming: list[dd.RailRow] = field(default_factory=list)
    reporting: list[dd.RailRow] = field(default_factory=list)

    #: Valdkonniti renders the current area view unchanged, from the object that
    #: already builds it. There is no second area implementation here.
    area_page: ov.Overview | None = None

    @property
    def is_department(self) -> bool:
        return self.scope == ov.SCOPE_DEPARTMENT

    @property
    def is_areas(self) -> bool:
        return self.scope == ov.SCOPE_AREAS

    @property
    def intervention_preview(self) -> list[ov.InterventionRow]:
        return self.interventions[:INTERVENTION_PREVIEW]

    @property
    def intervention_rest(self) -> list[ov.InterventionRow]:
        return self.interventions[INTERVENTION_PREVIEW:]

    @property
    def intervention_remaining(self) -> int:
        return max(0, self.intervention_total - INTERVENTION_PREVIEW)

    @property
    def has_team(self) -> bool:
        return bool(self.team)

    @property
    def has_former_members(self) -> bool:
        return any(row.is_former for row in self.team)

    @property
    def has_upcoming(self) -> bool:
        return any(group.count for group in self.upcoming)


def build_department(
    viewer: Any,
    *,
    is_head: bool = False,
    scope: str = ov.SCOPE_DEPARTMENT,
    today: date | None = None,
    params: Any = None,
    sort: str = ov.SORT_OPEN,
    show_empty_areas: bool = False,
) -> Department:
    """Assemble one scope of ``/osakond/`` for one reader.

    ``viewer`` is who the page is *authorized* as — a persona, or the shared
    gate's department sentinel. ``is_head`` is who the request is *authenticated*
    as, and it is a separate argument on purpose: the two are different
    questions, and a pseudo-viewer must never be able to become a department
    head by being passed to the wrong function (brief §22).

    The area scope delegates whole to :func:`app.matters.overview.build_overview`.
    Its table, its sort control and its two rail blocks are the current approved
    implementation and are reused rather than rebuilt — this page changed where
    the scope lives, not what it says.
    """
    today = today or timezone.localdate()
    params = params if params is not None else {}
    scope = ov.scope_from(scope)
    page = Department(scope=scope, today=today, is_head=is_head)

    # One read of the shared work model for the whole page, both scopes, read
    # before the first consumer rather than by each of them. Every surface below
    # wants the same list — this reader, this day, unnarrowed — and the ones
    # that used to read it themselves went to the database five times for one
    # answer while both modules' docstrings claimed they went once.
    items = wi.work_items(viewer, today=today)

    page.seis = dd.seis_figures(viewer, today, items=items)

    if scope == ov.SCOPE_AREAS:
        page.area_page = ov.build_overview(
            viewer,
            scope=ov.SCOPE_AREAS,
            today=today,
            sort=sort,
            show_empty_areas=show_empty_areas,
            items=items,
        )
        page.open_matters = page.area_page.open_matters
        # The same Aruandlus block the department scope prints, from the same
        # selectors. Switching scope must not change a number nobody asked to
        # change (docs/adr/0049 §8).
        page.reporting = dd.reporting_rail(viewer, today)
        return page

    people = ov.Populations.for_user(viewer)

    # The population `Populations` already resolved, not a fifth
    # `visible_to`: `wi.open_matters` and `dashboard.active_matters` are the
    # same open-FULL population, and resolving a scope costs a break-glass
    # lookup every time it is asked for (`ov.Populations`).
    page.open_matters = people.open_matters.count()

    page.interventions = ov.intervention_rows(viewer, today, items, pop=people)
    page.intervention_total = len(page.interventions)
    # Matters, because the link opens a register list and the register lists
    # files. Read through the shared population rather than by de-duplicating
    # the rows above: the row list is capped and this number is not.
    page.intervention_matters = len(
        wi.work_population_ids(
            viewer,
            wi.WORK_NEEDS_ATTENTION,
            today=today,
            items=items,
            quiet=people.quiet,
            ownerless=people.ownerless,
        )
    )
    page.intervention_url = ov.intervention_url()

    page.upcoming = dd.upcoming_groups(viewer, today, items=items)

    page.areas = [
        ov.CountRow(label=row.name, count=row.open_count, url=row.url)
        for row in ov.area_rows(viewer, today, items, pop=people)[0][:AREA_RAIL_ROWS]
    ]
    page.incoming = dd.incoming_rail(viewer, today)
    page.reporting = dd.reporting_rail(viewer, today)

    if is_head:
        # Built only here. A specialist's request never reaches these two, so
        # the manager-only populations are not read at all rather than read and
        # withheld by a template condition (brief §4, §41).
        page.team = dd.team_rows(viewer, today, items=items)
        page.previous_week_label = dd.short_range(*dd.previous_week(today))
        period = dd.period_from(params, today)
        kind = dd.kind_from(params)
        page.digest = dd.build_digest(viewer, period, kind)
        page.periods = dd.period_options(period, kind)

    return page
