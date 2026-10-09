"""Minu asjad's two opinion figures: this week's, and every opinion in preparation.

«arvamust koostada sel nädalal» and «arvamust koostamisel kokku» close the Seis
strip, on one's own desk and on a colleague's (`app/matters/my_work.py`,
`OpinionWork`). Neither is a new definition, and the tests below hold them to
that:

* **in preparation** is ``dashboard.drafting_matters`` — Osakond's ARVAMUS
  KOOSTAMISEL population, the register's ``?arvamus=koostamisel`` — narrowed to
  the Matters the desk's owner *owns*;
* **due this week** is that population with a recorded ``Arvamuse tähtaeg``
  from Monday to Sunday of the current week, both inclusive, in Tallinn.

`user` decides what may be read and `subject` whose Matters are counted, so
every authorization case is asserted from the reader's side. Every figure is a
link, and every link is asserted to open *exactly* the Matters counted — as row
identities, through the register's own pipeline and through the view.

Most worlds here put an opinion in preparation the historical way, with a DRAFT
Submission: that reading is kept (docs/adr/0061), and it is the half of the
definition every branch agrees on. Since the 2026-09-27 amendment to ADR 0061
the interface records an opinion sent in one act and starts no DRAFT, so a
lawyer's opinion work is a «Koostan arvamuse» step against an owed deadline
(docs/adr/0149); the tests for that path — written natively, sent, requested
again — are at the end.

No fixture uses an imported or confidential row. Dates that only the figures
read are fixed and passed as ``today``; dates the clock-reading paths see — the
rendered page, a send — are relative to the real day.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import parse_qsl, urlparse

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.core.enums import Visibility
from app.matters import dashboard
from app.matters import work_items as wi
from app.matters.department_dashboard import TEAM_COLUMNS, team_rows
from app.matters.models import Matter
from app.matters.my_work import OpinionWork, build_my_work, opinion_work
from app.matters.register_filters import register_population
from app.matters.services import assign_matter, close_matter, create_matter
from app.submissions.services import create_submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import establish_opinion_preparation_action, set_next_action
from tests.follow_ups import send_koja_arvamus

pytestmark = pytest.mark.django_db

#: Friday 9 October 2026, and the calendar week it falls in.
FRIDAY = dt.date(2026, 10, 9)
MONDAY = dt.date(2026, 10, 5)
SUNDAY = dt.date(2026, 10, 11)

WEEK_KEY = "opinions_week"
TOTAL_KEY = "opinions_drafting"
WEEK_CAPTION = "arvamust koostada sel nädalal"
TOTAL_CAPTION = "arvamust koostamisel kokku"


# ---------------------------------------------------------------------------
# Building the worlds
# ---------------------------------------------------------------------------


def a_matter(owner: Any, *, deadline: dt.date | None = None, **kwargs: Any) -> Matter:
    """An open FULL Matter, made the way `Uus teema` makes one."""
    kwargs.setdefault("title", f"Arvamuse teema {deadline or 'tähtajata'}")
    return create_matter(owner=owner, actor=owner, response_deadline=deadline, **kwargs)


def drafted(owner: Any, *, deadline: dt.date | None = None, **kwargs: Any) -> Matter:
    """A Matter whose opinion is in preparation the historical way — a DRAFT."""
    matter = a_matter(owner, deadline=deadline, **kwargs)
    create_submission(matter=matter, title="Arvamuse mustand", actor=owner)
    return matter


def figures(user: Any, today: dt.date = FRIDAY, *, subject: Any = None) -> OpinionWork:
    return opinion_work(user, subject if subject is not None else user, today)


def pair(user: Any, today: dt.date = FRIDAY, *, subject: Any = None) -> tuple[int, int]:
    """(due this week, in preparation) — the two numbers the strip prints."""
    work = figures(user, today, subject=subject)
    return work.due_this_week, work.in_preparation


def params_of(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlparse(url).query))


def listed(user: Any, url: str) -> set[Any]:
    """Exactly the rows ``/teemad/?<url's query>`` pages through for this reader."""
    return set(register_population(user, params_of(url)).values_list("pk", flat=True))


def opened(client: Any, url: str) -> set[Any]:
    """The same, through the register view itself, as a browser sends it."""
    response = client.get(url)
    assert response.status_code == 200, url
    page = response.context["page"]
    assert page.paginator.count == len(page.object_list), "the list spans pages"
    return {matter.pk for matter in page.object_list}


def drafting_cell(user: Any, person: Any):
    """One person's ARVAMUS KOOSTAMISEL cell on Osakond's team table."""
    index = next(i for i, (key, *_rest) in enumerate(TEAM_COLUMNS) if key == "drafting")
    row = next(row for row in team_rows(user) if row.key == str(person.pk))
    return row.cells[index]


def strip(work: Any) -> dict[str, Any]:
    return {figure.key: figure for figure in work.seis}


# ---------------------------------------------------------------------------
# The week: Monday to Sunday, both inclusive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deadline", "due"),
    [
        pytest.param(dt.date(2026, 10, 4), False, id="overdue-from-last-week"),
        pytest.param(MONDAY, True, id="due-on-monday"),
        pytest.param(dt.date(2026, 10, 8), True, id="passed-earlier-this-week"),
        pytest.param(FRIDAY, True, id="due-today"),
        pytest.param(SUNDAY, True, id="due-on-sunday"),
        pytest.param(dt.date(2026, 10, 12), False, id="due-next-monday"),
        pytest.param(dt.date(2026, 10, 15), False, id="due-next-week"),
    ],
)
def test_the_week_runs_from_monday_to_sunday(specialist, deadline, due) -> None:
    """One opinion in preparation, its deadline moved across the week's edges.

    Always in the total — the total has no horizon — and in this week's figure
    only between Monday and Sunday. A deadline that passed on Thursday is still
    this week's opinion on Friday; one from last week is not, however late.
    """
    drafted(specialist, deadline=deadline)

    assert pair(specialist) == (1 if due else 0, 1)


def test_an_opinion_with_no_deadline_is_in_preparation_and_never_due(specialist) -> None:
    drafted(specialist, deadline=None)

    assert pair(specialist) == (0, 1)


@pytest.mark.parametrize("offset", range(7), ids=["E", "T", "K", "N", "R", "L", "P"])
def test_every_day_of_the_week_reads_the_same_week(specialist, offset) -> None:
    """The figure is the calendar week's, not a rolling seven days from today."""
    monday = drafted(specialist, deadline=MONDAY, title="Esmaspäevane")
    sunday = drafted(specialist, deadline=SUNDAY, title="Pühapäevane")
    drafted(specialist, deadline=SUNDAY + dt.timedelta(days=1), title="Järgmise nädala")
    today = MONDAY + dt.timedelta(days=offset)

    work = figures(specialist, today)

    assert (work.week_start, work.week_end) == (MONDAY, SUNDAY)
    assert work.due_this_week == 2
    assert listed(specialist, work.due_url) == {monday.pk, sunday.pk}
    assert params_of(work.due_url)["tahtaeg_alates"] == "5.10.2026"
    assert params_of(work.due_url)["tahtaeg_kuni"] == "11.10.2026"


def test_the_week_crosses_a_month_boundary(specialist) -> None:
    """Wednesday 30 September 2026: the week is 28 September to 4 October."""
    inside = {
        drafted(specialist, deadline=dt.date(2026, 9, 28)).pk,
        drafted(specialist, deadline=dt.date(2026, 10, 1)).pk,
        drafted(specialist, deadline=dt.date(2026, 10, 4)).pk,
    }
    drafted(specialist, deadline=dt.date(2026, 9, 27))
    drafted(specialist, deadline=dt.date(2026, 10, 5))

    work = figures(specialist, dt.date(2026, 9, 30))

    assert (work.week_start, work.week_end) == (dt.date(2026, 9, 28), dt.date(2026, 10, 4))
    assert (work.due_this_week, work.in_preparation) == (3, 5)
    assert listed(specialist, work.due_url) == inside


@pytest.mark.parametrize("today", [dt.date(2026, 12, 31), dt.date(2027, 1, 1)])
def test_the_week_crosses_a_year_boundary(specialist, today) -> None:
    """ISO week 53 of 2026 runs Monday 28 December to Sunday 3 January 2027."""
    inside = {
        drafted(specialist, deadline=dt.date(2026, 12, 28)).pk,
        drafted(specialist, deadline=dt.date(2027, 1, 3)).pk,
    }
    drafted(specialist, deadline=dt.date(2026, 12, 27))
    drafted(specialist, deadline=dt.date(2027, 1, 4))

    work = figures(specialist, today)

    assert (work.week_start, work.week_end) == (dt.date(2026, 12, 28), dt.date(2027, 1, 3))
    assert work.due_this_week == 2
    assert listed(specialist, work.due_url) == inside
    assert params_of(work.due_url)["tahtaeg_alates"] == "28.12.2026"
    assert params_of(work.due_url)["tahtaeg_kuni"] == "3.1.2027"


@pytest.mark.parametrize(
    ("instant", "local_monday"),
    [
        # Sunday 22:30 in UTC is already Monday 01:30 in Tallinn (EEST, UTC+3).
        pytest.param(
            dt.datetime(2026, 10, 11, 22, 30, tzinfo=dt.UTC),
            dt.date(2026, 10, 12),
            id="utc-sunday-is-tallinn-monday",
        ),
        # And after the clocks go back (EET, UTC+2): Sunday 22:30 UTC is
        # Monday 00:30 in Tallinn.
        pytest.param(
            dt.datetime(2026, 11, 1, 22, 30, tzinfo=dt.UTC),
            dt.date(2026, 11, 2),
            id="after-the-clocks-go-back",
        ),
    ],
)
def test_the_week_is_the_tallinn_calendar_week(specialist, monkeypatch, instant, local_monday):
    """The page asks for no date, so the week is whatever day it is in Tallinn."""
    sunday = local_monday - dt.timedelta(days=1)
    this_week = drafted(specialist, deadline=local_monday, title="Selle nädala")
    drafted(specialist, deadline=sunday, title="Eelmise nädala")
    monkeypatch.setattr(timezone, "now", lambda: instant)

    work = build_my_work(specialist)

    assert work.today == local_monday
    assert (work.opinions.week_start, work.opinions.due_this_week) == (local_monday, 1)
    assert listed(specialist, work.opinions.due_url) == {this_week.pk}


# ---------------------------------------------------------------------------
# What is counted, and what is not
# ---------------------------------------------------------------------------


def test_several_drafts_on_one_matter_are_one_opinion(specialist) -> None:
    matter = drafted(specialist, deadline=FRIDAY)
    create_submission(matter=matter, title="Teine mustand", actor=specialist)
    create_submission(matter=matter, title="Kolmas mustand", actor=specialist)

    work = figures(specialist)

    assert (work.due_this_week, work.in_preparation) == (1, 1)
    assert (
        listed(specialist, work.due_url) == listed(specialist, work.preparation_url) == {matter.pk}
    )


def test_a_matter_whose_only_opinion_went_out_is_not_in_preparation(
    specialist, organisation
) -> None:
    """`+ Koja arvamus` records a send in one act, and answers the deadline."""
    matter = a_matter(specialist, deadline=timezone.localdate())
    send_koja_arvamus(matter, specialist, [organisation], timezone.localdate(), answers_deadline="")

    assert pair(specialist, timezone.localdate()) == (0, 0)


def test_a_closed_matter_holding_a_draft_is_not_current_work(specialist) -> None:
    matter = drafted(specialist, deadline=FRIDAY)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    assert pair(specialist) == (0, 0)


def test_the_figures_follow_the_matter_to_its_new_owner(
    specialist, other_specialist, department_head
) -> None:
    matter = drafted(specialist, deadline=FRIDAY)
    assert pair(specialist) == (1, 1)

    assign_matter(matter=matter, owner=other_specialist, actor=department_head)

    assert pair(specialist) == (0, 0)
    assert pair(other_specialist) == (1, 1)
    assert pair(department_head, subject=other_specialist) == (1, 1)
    assert drafting_cell(department_head, other_specialist).value == 1


def test_an_opinion_belongs_to_the_owner_not_to_whoever_carries_a_step(
    specialist, other_specialist
) -> None:
    """Ownership, as Osakond's column counts it — never the step's responsible.

    The step a colleague carries puts that colleague's *timeline* to work this
    week, and moves neither opinion figure: the opinion is the file's, and the
    file is the owner's.
    """
    mine = drafted(specialist, deadline=FRIDAY, title="Minu arvamus")
    set_next_action(
        matter=mine,
        text="Kooskõlasta arvamus ministeeriumiga",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=FRIDAY,
        actor=specialist,
        responsible=other_specialist,
    )
    theirs = drafted(other_specialist, deadline=FRIDAY, title="Kolleegi arvamus")
    set_next_action(
        matter=theirs,
        text="Vaata kolleegi arvamus üle",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=FRIDAY,
        actor=other_specialist,
        responsible=specialist,
    )

    assert pair(specialist) == (1, 1)
    assert listed(specialist, figures(specialist).due_url) == {mine.pk}
    assert pair(other_specialist) == (1, 1)
    assert listed(other_specialist, figures(other_specialist).due_url) == {theirs.pk}


def test_dated_work_that_is_not_an_opinion_moves_only_the_week_figure(specialist) -> None:
    """«sel nädalal» counts every kind of dated work; the opinion figure does not.

    An ordinary step due this week, on a Matter whose `Arvamuse tähtaeg` is
    this week as well, is week work and is not an opinion being written.
    """
    today = timezone.localdate()
    matter = a_matter(specialist, deadline=today, title="Tavaline töö")
    set_next_action(
        matter=matter,
        text="Helista ministeeriumi",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )

    work = build_my_work(specialist, today=today)
    shown = strip(work)

    assert shown["week"].value == 1
    assert (shown[WEEK_KEY].value, shown[TOTAL_KEY].value) == (0, 0)


# ---------------------------------------------------------------------------
# Who may read what
# ---------------------------------------------------------------------------


def test_a_restricted_matter_counts_only_for_a_reader_who_may_open_it(
    specialist, department_head, reader
) -> None:
    hidden = drafted(specialist, deadline=FRIDAY, visibility=Visibility.RESTRICTED)

    assert pair(specialist) == (1, 1)
    assert pair(department_head, subject=specialist) == (1, 1)
    assert pair(reader, subject=specialist) == (0, 0)
    reading = figures(reader, subject=specialist)
    for url in (reading.due_url, reading.preparation_url):
        assert hidden.pk not in listed(reader, url)


def test_a_draft_restricted_below_its_matter_moves_no_figure_for_a_reader(
    specialist, department_head, reader
) -> None:
    """The Matter is readable to both; the opinion being written is not.

    Counting it for a reader who may not open it would disclose that a draft
    exists on a named file (AUTH-003).
    """
    matter = a_matter(specialist, deadline=FRIDAY)
    create_submission(
        matter=matter,
        title="Konfidentsiaalne mustand",
        actor=specialist,
        visibility_override=Visibility.RESTRICTED,
    )

    assert matter.pk in set(Matter.objects.visible_to(reader).values_list("pk", flat=True))
    assert pair(department_head, subject=specialist) == (1, 1)
    assert pair(reader, subject=specialist) == (0, 0)


def test_the_head_reads_a_colleagues_desk_through_their_own_entitlement(
    client, specialist, department_head
) -> None:
    drafted(specialist, deadline=timezone.localdate(), visibility=Visibility.RESTRICTED)
    drafted(specialist, deadline=None)
    today = timezone.localdate()

    client.force_login(department_head)
    response = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk}))

    assert response.status_code == 200
    work = response.context["work"]
    assert work.subject == specialist and not work.is_self
    expected = figures(department_head, today, subject=specialist)
    assert (work.opinions.due_this_week, work.opinions.in_preparation) == (1, 2)
    assert work.opinions == expected


@pytest.mark.parametrize("who", ["other_specialist", "reader"])
def test_nobody_else_may_open_a_colleagues_desk(client, request, specialist, who) -> None:
    drafted(specialist, deadline=timezone.localdate())
    client.force_login(request.getfixturevalue(who))

    response = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk}))

    assert response.status_code == 404
    assert TOTAL_CAPTION not in response.content.decode()


# ---------------------------------------------------------------------------
# The count is the list
# ---------------------------------------------------------------------------


@pytest.fixture
def mixed_desk(specialist, other_specialist):
    """Every shape at once, on one person's desk, around the real today."""
    today = timezone.localdate()
    monday, sunday = wi.start_of_iso_week(today), wi.end_of_iso_week(today)
    due = {
        drafted(specialist, deadline=monday, title="Esmaspäeval").pk,
        drafted(specialist, deadline=sunday, title="Pühapäeval").pk,
    }
    twice = drafted(specialist, deadline=today, title="Kaks mustandit")
    create_submission(matter=twice, title="Teine mustand", actor=specialist)
    due.add(twice.pk)
    later = {
        drafted(specialist, deadline=monday - dt.timedelta(days=1), title="Eelmisel nädalal").pk,
        drafted(specialist, deadline=sunday + dt.timedelta(days=1), title="Järgmisel nädalal").pk,
        drafted(specialist, deadline=None, title="Tähtajata").pk,
    }
    closed = drafted(specialist, deadline=today, title="Suletud")
    close_matter(matter=closed, disposition=Disposition.COMPLETED, actor=specialist)
    drafted(other_specialist, deadline=today, title="Kolleegi oma")
    a_matter(specialist, deadline=today, title="Mustandita")
    return today, due, due | later


@pytest.mark.parametrize("viewer", ["specialist", "department_head"])
def test_each_figure_opens_exactly_the_matters_it_counts(
    client, request, specialist, mixed_desk, viewer
) -> None:
    """Compared as row identities, through the register pipeline and the view."""
    today, due, preparing = mixed_desk
    user = request.getfixturevalue(viewer)
    client.force_login(user)
    url = (
        reverse("matters:my_work")
        if viewer == "specialist"
        else reverse("matters:person_work", kwargs={"pk": specialist.pk})
    )

    work = client.get(url).context["work"]
    shown = strip(work)

    assert work.today == today
    assert shown[WEEK_KEY].value == len(due) == 3
    assert shown[TOTAL_KEY].value == len(preparing) == 6
    for key, expected in ((WEEK_KEY, due), (TOTAL_KEY, preparing)):
        assert listed(user, shown[key].url) == expected, key
        assert opened(client, shown[key].url) == expected, key


def test_the_links_say_the_population_in_the_registers_own_words(specialist) -> None:
    """Open FULL, this owner, an opinion in preparation — and the week's two days."""
    work = figures(specialist)
    owner = str(specialist.pk)

    assert urlparse(work.preparation_url).path == reverse("matters:matter_list")
    assert params_of(work.preparation_url) == {
        "olek": "avatud",
        "liik": "FULL",
        "vastutaja": owner,
        "arvamus": "koostamisel",
    }
    assert params_of(work.due_url) == {
        **params_of(work.preparation_url),
        "tahtaeg_alates": format_estonian_date(MONDAY),
        "tahtaeg_kuni": format_estonian_date(SUNDAY),
    }


def test_the_total_is_the_owners_cell_on_osakond(
    department_head, specialist, other_specialist, mixed_desk
) -> None:
    """One definition, one number: the desk and the team table agree, per person."""
    for person in (specialist, other_specialist):
        work = figures(department_head, subject=person)
        cell = drafting_cell(department_head, person)

        assert work.in_preparation == cell.value, person
        assert listed(department_head, work.preparation_url) == listed(department_head, cell.url), (
            person
        )
        assert work.in_preparation == (
            dashboard.drafting_matters(department_head).filter(owner=person).count()
        )


# ---------------------------------------------------------------------------
# The page around them
# ---------------------------------------------------------------------------


def test_the_strip_keeps_its_four_figures_and_adds_the_two_after_them(specialist) -> None:
    work = build_my_work(specialist, today=FRIDAY)

    assert [figure.key for figure in work.seis] == [
        "open",
        "overdue",
        "week",
        "no_action",
        WEEK_KEY,
        TOTAL_KEY,
    ]
    assert [figure.caption for figure in work.seis][-2:] == [WEEK_CAPTION, TOTAL_CAPTION]
    # Counts of work state, not warnings: neither figure carries a tone.
    assert [figure.tone for figure in work.seis][-2:] == ["", ""]


def test_an_opinion_in_preparation_leaves_the_timeline_as_it_was(specialist) -> None:
    """A draft is not a dated work item; the bands and the first four figures hold."""
    today = timezone.localdate()
    matter = a_matter(specialist, deadline=today, title="Tähtajaga teema")

    def timeline() -> tuple[Any, ...]:
        work = build_my_work(specialist, today=today)
        bands = [
            (band.key, band.total, [(item.source_type, item.object_id) for item in band.items])
            for band in work.bands
        ]
        return bands, [(figure.key, figure.value, figure.url) for figure in work.seis[:4]]

    before = timeline()
    create_submission(matter=matter, title="Arvamuse mustand", actor=specialist)
    after = timeline()

    assert after == before
    assert pair(specialist, today) == (1, 1)


def test_the_page_draws_a_count_as_a_link_and_drops_a_zero(client, specialist) -> None:
    """The strip's own convention: every figure that survives is its list."""
    drafted(specialist, deadline=None)
    client.force_login(specialist)

    response = client.get(reverse("matters:my_work"))
    body = response.content.decode()
    total = strip(response.context["work"])[TOTAL_KEY]

    assert total.value == 1
    href = total.url.replace("&", "&amp;")
    assert f'<a class="seis__figure" href="{href}">' in body
    figure = body[body.index(f'href="{href}"') :]
    figure = figure[: figure.index("</a>")]
    assert '<span class="seis__number">1</span>' in figure
    assert f'<span class="seis__caption">{TOTAL_CAPTION}</span>' in figure
    assert WEEK_CAPTION not in body


# ---------------------------------------------------------------------------
# The workflow as it is used: «Koostan arvamuse», no DRAFT, a send in one act
# ---------------------------------------------------------------------------

#: These read the native half of «koostamisel» — an open «Koostan arvamuse»
#: step against an `Arvamuse tähtaeg` still owed — in
#: `register_filters.opinion_state_q` (docs/adr/0149). The desk follows it with
#: no rule of its own: a send that answers the deadline ends the work on the
#: next read, and a new request starts it again.


def prepared(owner: Any, deadline: dt.date, **kwargs: Any) -> Matter:
    """`Uus teema` with an `Arvamuse tähtaeg`, then «Koostan arvamuse» — no DRAFT."""
    matter = a_matter(owner, deadline=deadline, **kwargs)
    establish_opinion_preparation_action(
        matter=matter, prepare_by=deadline, actor=owner, responsible=owner
    )
    return matter


def test_an_opinion_being_written_needs_no_draft(specialist, department_head) -> None:
    today = timezone.localdate()
    matter = prepared(specialist, today)

    assert pair(specialist, today) == (1, 1)
    assert listed(specialist, figures(specialist, today).due_url) == {matter.pk}
    assert drafting_cell(department_head, specialist).value == 1


def test_sending_the_opinion_takes_it_off_both_figures(specialist, organisation) -> None:
    """Automatically: the send answers the deadline, and nobody closes the step."""
    today = timezone.localdate()
    matter = prepared(specialist, today)
    assert pair(specialist, today) == (1, 1)

    send_koja_arvamus(matter, specialist, [organisation], today, answers_deadline="")

    assert pair(specialist, today) == (0, 0)
    # The «Koostan arvamuse» step was not completed by hand, and did not need to be.
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()


def test_a_new_request_after_a_send_is_in_preparation_again(specialist, organisation) -> None:
    from app.matters.response_deadlines import request_response_deadline

    today = timezone.localdate()
    matter = prepared(specialist, today)
    send_koja_arvamus(matter, specialist, [organisation], today, answers_deadline="")
    assert pair(specialist, today) == (0, 0)

    request_response_deadline(matter=matter, deadline=today, actor=specialist)

    assert pair(specialist, today) == (1, 1)


def test_the_native_world_holds_no_draft_and_no_register_row(specialist) -> None:
    """What the three tests above stand on: `prepared` makes a «Koostan
    arvamuse» step against an owed deadline and nothing else, so whatever counts
    that Matter is the native half — never a DRAFT or a register row beside it.
    """
    from app.legacy_import.current_state import CurrentRegisterState
    from app.submissions.models import Submission

    matter = prepared(specialist, FRIDAY)

    assert not Submission.objects.filter(matter=matter).exists()
    assert not CurrentRegisterState.objects.filter(matter=matter).exists()
