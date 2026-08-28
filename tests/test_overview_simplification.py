"""`Minu tiim` is gone, and the three numbers that outlived it are honest.

The product decision is in docs/adr/0039: Ülevaade had two scopes answering the
same question over the same population, so the one without a population of its
own was retired. `Kogu osakond` remains.

Three of its counts moved into `Aruandlus` — `Sissekandeid sel nädalal`,
`Esitatud arvamusi <kuu>` and `Tähtaegu sel nädalal`. The risk in a move like
this is not that the rows fail to render. It is that a number quietly changes
meaning on the way: a population widened because the scope that narrowed it
disappeared, a restricted child suddenly inside a total, a week that runs from a
different Monday than it used to.

So the assertions here are about *what the numbers count*, not about markup:

* the retired view leaves nothing behind that a reader or a URL can reach;
* each of the three is computed from the database on the request, not seeded;
* each is bounded by the ISO week or the calendar month it names;
* and each refuses a child the reader may not read, while still counting the
  same child for somebody entitled to it (docs/adr/0038).
"""

from __future__ import annotations

import datetime
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import overview as ov
from app.matters.services import create_matter
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

OVERVIEW = "matters:overview"

#: The three that survived, in the order Aruandlus prints them.
RETAINED = ("Sissekandeid sel nädalal", "Esitatud arvamusi", "Tähtaegu sel nädalal")

#: Everything the retired scope put on the page. Named rather than described,
#: because "no team content" is only checkable against a list of what team
#: content was.
GONE = (
    "Minu tiim",
    "vaade=tiim",
    "Tiimi tegevus",
    "Tiimi tähtajad",
    "personblock",
    "teamrow",
    'id="inimesed"',
    # The footnote the view needed because it could not mean its own name.
    "Tiimi koosseisu ei ole süsteemis eraldi kirjas",
)


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def midweek():
    """A Wednesday, so *this week* has a Monday behind it and a Sunday ahead.

    Every window here is asserted against both ends. A date that happened to be
    a Monday would make the lower bound and `today` the same day, and half of
    what these tests check would hold by coincidence.
    """
    return datetime.date(2026, 8, 12)


def aruandlus(page: ov.Overview) -> dict[str, int]:
    return {row.label: row.count for row in page.reporting}


def row_starting(page: ov.Overview, prefix: str):
    return next(row for row in page.reporting if row.label.startswith(prefix))


def entry_on(matter, when, *, author, restricted: bool = False):
    """One `Sissekanne`, dated, optionally stricter than its Matter."""
    entry = factories.EntryFactory(
        matter=matter,
        author=author,
        occurred_at=datetime.datetime.combine(when, datetime.time(9, 0), tzinfo=datetime.UTC),
    )
    if restricted:
        entry.visibility_override = Visibility.RESTRICTED
        entry.save(update_fields=["visibility_override"])
    return entry


def sent_on(matter, when, *, title="Arvamus", restricted=False):
    """A canonical SENT `Submission`, with the final version its state requires.

    The evidence is restricted first when the opinion is. The database refuses a
    RESTRICTED submission whose final evidence is readable — the file *is* the
    opinion, so a restriction the document does not share is no restriction at
    all (app/submissions/migrations/0002_final_evidence_integrity.py).
    """
    from app.documents.services import add_evidence_version

    document = factories.DocumentFactory(matter=matter)
    version = add_evidence_version(
        document=document,
        content=f"%PDF-1.4\n{title}".encode(),
        original_filename="naidis.pdf",
        mime_type="application/pdf",
    )
    if restricted:
        document.visibility_override = Visibility.RESTRICTED
        document.save(update_fields=["visibility_override"])
    return factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=datetime.datetime.combine(when, datetime.time(9, 0), tzinfo=datetime.UTC),
        final_version=version,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


def a_matter(owner, title="Ühisveevärgi seaduse eelnõu"):
    return create_matter(title=title, owner=owner, reference_year=2026, actor=owner)


# ---------------------------------------------------------------------------
# A. Minu tiim is absent
# ---------------------------------------------------------------------------


def test_the_scope_chooser_offers_two_scopes_and_neither_is_minu_tiim():
    assert [label for _, label in ov.SCOPES] == ["Kogu osakond", "Valdkonniti"]


def test_the_overview_still_renders_kogu_osakond(client, department_head):
    client.force_login(department_head)

    body = client.get(reverse(OVERVIEW)).content.decode()

    assert "Kogu osakond" in body
    assert "Vajab sekkumist" in body
    assert "Koormus" in body


@pytest.mark.parametrize("marker", GONE)
def test_no_trace_of_the_retired_view_reaches_the_page(client, department_head, specialist, marker):
    """Asserted on a populated page, so an empty one cannot pass it by default."""
    matter = a_matter(specialist)
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=1),
        actor=specialist,
    )
    client.force_login(department_head)

    body = client.get(reverse(OVERVIEW)).content.decode()

    assert body.count("loadrow__who") >= 1, "the Koormus rail should be populated"
    assert marker not in body


def test_the_retired_scope_is_not_reachable_by_asking_for_it(client, department_head):
    """Not merely untabbed. There is no second body behind the parameter."""
    client.force_login(department_head)

    body = client.get(reverse(OVERVIEW) + "?vaade=tiim").content.decode()

    assert "personblock" not in body
    assert "Tiimi tegevus" not in body


def test_the_department_page_carries_no_second_per_person_projection(department_head, today):
    """`PersonLoad` stopped carrying rows when the view that printed them went.

    A dataclass still assembling a week of work items per colleague would be the
    dead second implementation this change exists to remove — invisible on the
    page and paid for on every request.
    """
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)

    assert not hasattr(page, "people")
    assert not hasattr(page, "team_activity")
    assert not hasattr(page, "is_team")
    assert all(not hasattr(load, "items") for load in page.loads)


# ---------------------------------------------------------------------------
# B. The three metrics survived, in Aruandlus, still calculated
# ---------------------------------------------------------------------------


def test_aruandlus_holds_the_three_retained_rows_and_the_year_rows(department_head, today):
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    labels = [row.label for row in page.reporting]

    assert labels[0] == "Sissekandeid sel nädalal"
    assert labels[1].startswith("Esitatud arvamusi ")
    assert labels[2] == "Tähtaegu sel nädalal"
    # The block they moved into, not a block of their own beside it.
    assert f"Suletud teemasid {today.year}" in labels


def test_the_three_rows_are_rendered_inside_the_aruandlus_block(client, department_head):
    """Native rows of that block: the same `railrow` markup as the year rows.

    Sliced out of the rendered rail so a matching string anywhere else on the
    page — the Seis strip prints the same month wording — cannot pass this.
    """
    client.force_login(department_head)
    body = client.get(reverse(OVERVIEW)).content.decode()

    start = body.index('aria-label="Aruandlus"')
    block = body[start : body.index("</section>", start)]

    for label in RETAINED:
        assert label in block, label
    assert "railblock__label" in block
    assert block.count("railrow__key") == 6


def test_each_retained_row_counts_what_the_database_holds(department_head, specialist, midweek):
    """Values come from queries: seed one of each, and each row moves by one."""
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=midweek)
    before = aruandlus(page)
    month_label = row_starting(page, "Esitatud arvamusi ").label

    matter = a_matter(specialist)
    entry_on(matter, midweek, author=specialist)
    sent_on(matter, midweek)
    set_next_action(
        matter=matter,
        text="Esitan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=midweek + timedelta(days=1),
        actor=specialist,
    )

    after = aruandlus(ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=midweek))

    assert after["Sissekandeid sel nädalal"] == before["Sissekandeid sel nädalal"] + 1
    assert after[month_label] == before[month_label] + 1
    assert after["Tähtaegu sel nädalal"] == before["Tähtaegu sel nädalal"] + 1


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime.date(2026, 8, 12), "augustis"),
        (datetime.date(2026, 9, 12), "septembris"),
        (datetime.date(2026, 5, 12), "mais"),
        (datetime.date(2026, 3, 12), "märtsis"),
    ],
)
def test_the_month_wording_follows_the_date_and_stays_estonian(department_head, when, expected):
    """Derived from the day, spelled from the table.

    The inessive is not one suffix: *mais* drops nothing, *märtsis* adds two
    letters and *septembris* loses a vowel. A rule guessed from three examples
    produces *augusts*, which is the small wrongness that makes a page read as
    machine-written (app/matters/overview.py).
    """
    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=when)

    assert row_starting(page, "Esitatud arvamusi ").label == f"Esitatud arvamusi {expected}"


# ---------------------------------------------------------------------------
# C. Date semantics: each row is bounded by the period it names
# ---------------------------------------------------------------------------


def test_the_entry_week_runs_monday_to_sunday_and_excludes_both_neighbours(
    department_head, specialist, midweek
):
    """Work already written up, so the window is the whole ISO week.

    The upper bound is the half this move added: it was open-ended, so an entry
    somebody dated into next month counted towards *this week* (docs/adr/0039).
    """
    matter = a_matter(specialist)
    monday = midweek - timedelta(days=midweek.weekday())
    sunday = monday + timedelta(days=6)

    entry_on(matter, monday, author=specialist)
    entry_on(matter, sunday, author=specialist)
    entry_on(matter, monday - timedelta(days=1), author=specialist)
    entry_on(matter, sunday + timedelta(days=1), author=specialist)
    entry_on(matter, midweek + timedelta(days=40), author=specialist)

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=midweek)

    assert aruandlus(page)["Sissekandeid sel nädalal"] == 2


def test_the_deadline_week_runs_today_to_sunday(department_head, specialist, midweek):
    """Work still ahead, so the window starts now — the same one Minu töö uses.

    Deliberately not the entry window. A deadline that passed on Monday is
    overdue, and the Seis strip and the intervention list are where the
    department is told about it; counting it again under *sel nädalal* would say
    there is still time.
    """
    matter = a_matter(specialist)
    monday = midweek - timedelta(days=midweek.weekday())
    sunday = monday + timedelta(days=6)

    for label, when in (
        ("passed", monday),
        ("today", midweek),
        ("sunday", sunday),
        ("next-week", sunday + timedelta(days=1)),
    ):
        add_important_date(
            matter=matter,
            title=f"Tähtaeg {label}",
            date_value=when,
            period_end=when,
            actor=specialist,
        )

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=midweek)

    assert aruandlus(page)["Tähtaegu sel nädalal"] == 2


def test_the_month_row_holds_this_month_across_the_transition(department_head, specialist, midweek):
    """The last day of the month is in it; the first of the next is not."""
    matter = a_matter(specialist)
    first = midweek.replace(day=1)
    last = datetime.date(2026, 8, 31)

    sent_on(matter, first, title="Kuu algus")
    sent_on(matter, last, title="Kuu lõpp")
    sent_on(matter, last + timedelta(days=1), title="Järgmine kuu")
    sent_on(matter, first - timedelta(days=1), title="Eelmine kuu")

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=midweek)

    assert row_starting(page, "Esitatud arvamusi ").count == 2


def test_the_month_row_opens_exactly_the_month_it_names(client, department_head, specialist):
    """The one of the three with a list behind it, so the link carries the month.

    A label that says *augustis* over a link that opens the whole year is the
    defect ADR 0033 was written for, and it is the reason this row keeps its
    `?aasta=&kuu=` rather than borrowing the year rows' link.
    """
    today = timezone.localdate()
    matter = a_matter(specialist)
    sent_on(matter, today, title="Selle kuu arvamus")

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    row = row_starting(page, "Esitatud arvamusi ")

    assert f"aasta={today.year}" in row.url and f"kuu={today.month}" in row.url

    client.force_login(department_head)
    listed = client.get(row.url).context["page"].paginator.count
    assert listed == row.count


# ---------------------------------------------------------------------------
# D. The old URL
# ---------------------------------------------------------------------------


def test_an_old_minu_tiim_link_opens_the_surviving_overview(client, department_head):
    """A bookmark from before the retirement must not 404 and must not resurrect.

    Normalization rather than a redirect, which is what `scope_from` already did
    for every unrecognised value — `tiim` is simply one of those now.
    """
    client.force_login(department_head)

    response = client.get(reverse(OVERVIEW) + "?vaade=tiim")
    body = response.content.decode()

    assert response.status_code == 200
    assert 'aria-current="page"' in body
    assert body.index('aria-current="page"') < body.index("Valdkonniti")
    assert "Vajab sekkumist" in body


def test_the_old_link_renders_the_same_page_as_the_department_scope(client, department_head):
    """Byte-for-byte, once the per-request CSRF token is taken out.

    A weaker assertion — "both contain Vajab sekkumist" — would still pass if
    the retired parameter reached a body of its own that happened to share a
    heading. Nothing is normalized here except the one value that is a fresh
    random string on every response.
    """
    import re

    def without_csrf(body: str) -> str:
        return re.sub(r'value="[A-Za-z0-9]{64}"', 'value="CSRF"', body)

    client.force_login(department_head)

    retired = client.get(reverse(OVERVIEW) + "?vaade=tiim").content.decode()
    surviving = client.get(reverse(OVERVIEW) + "?vaade=osakond").content.decode()

    assert without_csrf(retired) == without_csrf(surviving)
    assert without_csrf(retired) != retired, "the CSRF token should have been normalized"


# ---------------------------------------------------------------------------
# E. Authorization — a restricted child stays out of every one of the three
# ---------------------------------------------------------------------------
#
# The invariant AUTH-003 established (docs/adr/0038): a Matter being visible is
# never authority to project a child of it. Each case seeds a NORMAL Matter
# everybody can read and hangs a RESTRICTED child off it, then asserts the count
# from two sides — absent for the stranger, present for the participant. Only
# asserting the stranger would pass if the row were never counted for anybody.


def restricted_pair(specialist, when):
    """A NORMAL Matter both readers see, carrying children only one may."""
    matter = a_matter(specialist, title="Avalik teema piiratud lastega")
    entry_on(matter, when, author=specialist, restricted=True)
    sent_on(matter, when, title="Piiratud arvamus", restricted=True)
    action = set_next_action(
        matter=matter,
        text="Piiratud tähtaeg",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=when + timedelta(days=1),
        actor=specialist,
    )
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])
    return matter


def test_a_restricted_child_is_absent_from_all_three_counts_for_a_stranger(specialist, midweek):
    stranger = factories.ReaderFactory()
    restricted_pair(specialist, midweek)

    counts = aruandlus(ov.build_overview(stranger, scope=ov.SCOPE_DEPARTMENT, today=midweek))
    month = next(label for label in counts if label.startswith("Esitatud arvamusi "))

    assert counts["Sissekandeid sel nädalal"] == 0
    assert counts[month] == 0
    assert counts["Tähtaegu sel nädalal"] == 0


def test_the_same_children_are_counted_for_the_colleague_entitled_to_them(specialist, midweek):
    """The other half. Without it the assertions above hold for an empty page."""
    restricted_pair(specialist, midweek)

    counts = aruandlus(ov.build_overview(specialist, scope=ov.SCOPE_DEPARTMENT, today=midweek))
    month = next(label for label in counts if label.startswith("Esitatud arvamusi "))

    assert counts["Sissekandeid sel nädalal"] == 1
    assert counts[month] == 1
    assert counts["Tähtaegu sel nädalal"] == 1


def test_the_stranger_still_sees_the_matter_itself(client, specialist, midweek):
    """The restriction is on the children, so hiding the Matter would prove nothing."""
    from app.matters.models import Matter

    stranger = factories.ReaderFactory()  # not a lawyer: docs/adr/0042
    matter = restricted_pair(specialist, midweek)
    client.force_login(stranger)

    body = client.get(reverse(OVERVIEW)).content.decode()

    assert Matter.objects.visible_to(stranger).filter(pk=matter.pk).exists()
    assert "Piiratud tähtaeg" not in body
    assert "Piiratud arvamus" not in body


def test_the_department_head_reads_the_restricted_children_by_role(
    department_head, specialist, midweek
):
    """Entitlement here is the central rule, not a special case on this page."""
    restricted_pair(specialist, midweek)

    counts = aruandlus(ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=midweek))

    assert counts["Sissekandeid sel nädalal"] == 1
    assert counts["Tähtaegu sel nädalal"] == 1


def test_a_restricted_matters_children_are_hidden_from_the_counts_too(specialist, midweek):
    """The parent path, since the child rule derives from it every time."""
    stranger = factories.ReaderFactory()
    matter = a_matter(specialist, title="Piiratud teema")
    matter.visibility = Visibility.RESTRICTED
    matter.save(update_fields=["visibility"])
    entry_on(matter, midweek, author=specialist)

    stranger_counts = aruandlus(
        ov.build_overview(stranger, scope=ov.SCOPE_DEPARTMENT, today=midweek)
    )
    owner_counts = aruandlus(
        ov.build_overview(specialist, scope=ov.SCOPE_DEPARTMENT, today=midweek)
    )

    assert stranger_counts["Sissekandeid sel nädalal"] == 0
    assert owner_counts["Sissekandeid sel nädalal"] == 1


# ---------------------------------------------------------------------------
# F. The page did not get more expensive
# ---------------------------------------------------------------------------


def seed_rows(specialist, count: int) -> None:
    """`count` Matters, each carrying an entry and a deadline this week."""
    today = timezone.localdate()
    for index in range(count):
        matter = a_matter(specialist, title=f"Teema {index}")
        entry_on(matter, today, author=specialist)
        set_next_action(
            matter=matter,
            text=f"Tegevus {index}",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today + timedelta(days=1),
            actor=specialist,
        )


def test_the_overview_costs_the_same_whatever_is_on_it(client, department_head, specialist):
    """The property, rather than a ceiling: cost is flat in rows.

    A threshold passes for the wrong reason as soon as somebody raises it. This
    compares the page against itself at two sizes, so the one thing that must
    never happen — a per-row authorization or count query behind the moved
    metrics — fails here whatever the absolute number happens to be.

    `tests/test_multiple_senders.py` keeps the absolute ceiling; this keeps the
    shape.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client.force_login(department_head)

    def cost() -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(reverse(OVERVIEW)).status_code == 200
        return len(captured)

    seed_rows(specialist, 3)
    small = cost()
    seed_rows(specialist, 15)
    large = cost()

    assert small == large, f"{small} queries for 3 Matters, {large} for 18"


def test_the_week_of_entries_is_one_aggregate_in_the_departments_timezone(
    client, department_head, specialist
):
    """The one query the three rows actually added, and the timezone it runs in.

    Of the three, the month's opinions narrow a population the page already
    resolved and are handed the Seis strip's own number rather than counting it
    again; the week's deadlines are counted in Python off the work items the
    page already read. Only the entries bring an aggregate of their own.

    `occurred_at` is a moment and the row counts *days*, so the boundary depends
    entirely on which timezone the cast uses. Asserted against the SQL, because
    a server drifting to UTC would move the Monday by three hours and nothing on
    the page would look wrong.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    seed_rows(specialist, 3)
    client.force_login(department_head)

    with CaptureQueriesContext(connection) as captured:
        assert client.get(reverse(OVERVIEW)).status_code == 200

    # The week's aggregate specifically — other statements join `matters_entry`
    # for the feed and for "järgmise tegevuseta", and matching those would make
    # this assert something it does not mean.
    week_counts = [
        query["sql"]
        for query in captured.captured_queries
        if "COUNT(" in query["sql"]
        and "matters_entry" in query["sql"]
        and "occurred_at" in query["sql"]
    ]

    assert len(week_counts) == 1, week_counts
    assert "AT TIME ZONE 'Europe/Tallinn'" in week_counts[0]
