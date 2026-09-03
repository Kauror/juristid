"""`/osakond/` — one department page where there were two.

What this file is for
---------------------
`/ulevaade/` and `/osakonna-too/` answered the same question and printed several
of the same numbers twice. Merging them is only an improvement if the merged
page keeps every promise both halves made, so this suite asserts the contract
rather than the layout:

* **the route replaced both**, and every old bookmark still opens the page it
  described, query string and all;
* **read access is Ülevaade's**, and the two manager sections are Osakonna töö's
  — and for anybody else they are not calculated at all;
* **every figure opens exactly the population it counted**, by Matter id and not
  by resemblance;
* **rows and Matters stay different numbers**, because one file can be late and
  unowned at once;
* **the five deadline windows partition** the eligible population across awkward
  calendars — no date in two, none in none;
* **waiting is not lateness**, which the merge is exactly the kind of change
  that could quietly undo;
* **one business fact has one definition**: Aruandlus and the team table's Kokku
  row count the same opinions.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.services import (
    add_important_date,
    add_work_victory_candidate,
    confirm_work_victory,
)
from app.matters import department as dep
from app.matters import department_dashboard as dd
from app.matters import overview as ov
from app.matters import work_items as wi
from app.matters.models import Matter
from app.matters.register_filters import register_population
from app.matters.services import close_matter, create_matter
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories
from tests.gate import apply_shared_gate

pytestmark = pytest.mark.django_db

PAGE = "/osakond/"
OVERVIEW_LEGACY = "/ulevaade/"
WORK_LEGACY = "/osakonna-too/"
REGISTER = "/teemad/"


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def send_opinion(capture_evidence, specialist):
    """A SENT Submission with the immutable evidence the database insists on.

    `submissions_sent_requires_timestamp_and_evidence` is a check constraint,
    not a convention (ADR 0011): a fixture that set the status directly would be
    asserting against a row the product can never produce.
    """

    def send(matter, *, when, title="Naidisarvamus"):
        version = capture_evidence(matter, b"%PDF-1.4 synthetic", f"{title}.pdf", "application/pdf")
        return factories.SubmissionFactory(
            matter=matter,
            title=title,
            status=SubmissionStatus.SENT,
            sent_at=when,
            final_version=version,
        )

    return send


@pytest.fixture
def world(db, department_head, specialist, other_specialist, today, send_opinion):
    """One member of every population the page counts, plus near-misses.

    Deliberately more than the minimum: every figure needs something that is in
    it and something that only looks as if it should be, or a filter matching
    everything would pass this suite.
    """
    week_end = wi.end_of_iso_week(today)

    # Late in two different ways, on one Matter: two work rows, one file. This
    # is the pair that keeps row counts and Matter counts honest.
    doubly_late = create_matter(
        title="Kaks möödunud tähtaega", owner=specialist, reference_year=2026, actor=specialist
    )
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

    # A real deadline in this calendar week, and one after it.
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
    later = create_matter(
        title="Hilisem tähtaeg", owner=other_specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=later,
        text="Esita teine arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=week_end + timedelta(days=9),
        actor=specialist,
    )

    # Waiting, with a review date in the future. Not a deadline, not late.
    waiting = create_matter(
        title="Ootan ministeeriumi vastust", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=waiting,
        text="Ootan uut sõnastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=2),
        actor=specialist,
    )

    # No instruction at all, and no owner at all: the two undated states no
    # date-based population can produce.
    quiet = create_matter(
        title="Järgmise tegevuseta", owner=other_specialist, reference_year=2026, actor=specialist
    )
    ownerless = create_matter(
        title="Vastutajata teema",
        owner=None,
        reference_year=2026,
        actor=specialist,
        received_date=today - timedelta(days=1),
    )

    # Rows that must not leak into an open-work count.
    close_matter(
        matter=create_matter(title="Suletud teema", owner=specialist, actor=specialist),
        disposition=Disposition.COMPLETED,
        actor=specialist,
    )
    factories.ArchiveMatterFactory(title="Arhiivirida")

    # Two opinions sent this year on **one** file, which is what separates
    # counting opinions from counting the files that carry them.
    sent_at = timezone.now() - timedelta(days=2)
    for index in (1, 2):
        send_opinion(this_week, when=sent_at - timedelta(hours=index), title=f"Arvamus {index}")
    send_opinion(doubly_late, when=sent_at, title="Arvamus kolmandal teemal")

    return {
        "doubly_late": doubly_late,
        "this_week": this_week,
        "later": later,
        "waiting": waiting,
        "quiet": quiet,
        "ownerless": ownerless,
    }


def page_for(person, **kwargs) -> dep.Department:
    return dep.build_department(person, is_head=True, **kwargs)


# =========================================================================
# §34 — the route replaced both
# =========================================================================


def test_department_page_replaces_both(client, department_head, world):
    """One canonical address, and two permanent redirects onto it.

    Not a second view behind each old path: a 301 keeps every bookmark, every
    pasted link and every historical reference working, and there is one page to
    maintain rather than three.
    """
    client.force_login(department_head)

    assert client.get(PAGE).status_code == 200

    for legacy in (OVERVIEW_LEGACY, WORK_LEGACY):
        response = client.get(legacy)
        assert response.status_code == 301, legacy
        assert response.headers["Location"] == PAGE, legacy


@pytest.mark.parametrize(
    ("legacy", "query"),
    [
        (OVERVIEW_LEGACY, "vaade=valdkonniti"),
        (OVERVIEW_LEGACY, "vaade=osakond"),
        (OVERVIEW_LEGACY, "vaade=valdkonniti&jarjesta=nimi"),
        (WORK_LEGACY, "periood=30"),
        (WORK_LEGACY, "periood=30&liik=arvamused"),
        (WORK_LEGACY, "periood=vahemik&alates=01.01.2026&kuni=31.01.2026"),
    ],
)
def test_a_legacy_url_keeps_its_query_string(client, department_head, world, legacy, query):
    """A saved scope, period or custom range still describes the same view."""
    response = client.get(f"{legacy}?{query}")
    assert response.status_code == 301
    assert response.headers["Location"] == f"{PAGE}?{query}"


def test_a_legacy_url_redirects_once(client, department_head, world):
    """One hop, not a chain. `/osakonna-too/` does not go via `/ulevaade/`."""
    client.force_login(department_head)
    response = client.get(f"{WORK_LEGACY}?periood=30", follow=True)
    assert [status for _url, status in response.redirect_chain] == [301]
    assert response.request["PATH_INFO"] == PAGE


def test_the_legacy_scope_arrives_in_the_scope_it_named(client, department_head, world):
    """`?vaade=valdkonniti` is a scope of the new route, not a lost parameter."""
    client.force_login(department_head)
    response = client.get(f"{OVERVIEW_LEGACY}?vaade=valdkonniti", follow=True)
    assert response.context["scope"] == ov.SCOPE_AREAS


# =========================================================================
# §45 — navigation
# =========================================================================


def test_the_navigation_names_one_department_destination(client, department_head, world):
    """`Osakond`, once, for everybody.

    There were two items for one question: a universal `Ülevaade` on the bar and
    a head-only `Osakond` inside «Veel». A bar that offers the same reader both
    is a bar they have to choose between before they can read anything.
    """
    client.force_login(department_head)
    body = client.get(PAGE).content.decode()

    assert ">Osakond</a>" in body
    assert body.count(f'href="{PAGE}"') == 1
    assert ">Ülevaade</a>" not in body
    assert ">Osakonna töö</a>" not in body


def test_a_specialist_sees_the_same_single_destination(client, specialist, world):
    client.force_login(specialist)
    body = client.get(PAGE).content.decode()
    assert body.count(f'href="{PAGE}"') == 1
    assert ">Ülevaade</a>" not in body


# =========================================================================
# §35 — access
# =========================================================================


def test_the_head_sees_the_whole_page(client, department_head, world):
    client.force_login(department_head)
    body = client.get(PAGE).content.decode()

    assert "uxstat" in body
    for heading in ("Vajab sekkumist", "Eesolev", "Tehtud", "Valdkonnad", "Uued teemad"):
        assert heading in body, heading


def test_a_specialist_reads_the_page_without_the_manager_sections(client, specialist, world):
    """The page is shared; two of its sections are not."""
    client.force_login(specialist)
    response = client.get(PAGE)
    body = response.content.decode()

    assert response.status_code == 200
    assert "uxstat" not in body
    assert "Tehtud" not in body
    assert "Vajab sekkumist" in body
    assert "Eesolev" in body
    assert "Aruandlus" in body


def test_a_reader_keeps_the_read_access_they_had(client, world):
    client.force_login(factories.UserFactory(role=UserRole.READER))
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "uxstat" not in response.content.decode()


def test_an_administrator_does_not_inherit_the_manager_sections(client, world):
    """Technical administration is not business access."""
    client.force_login(factories.AdministratorFactory(is_superuser=True))
    assert "uxstat" not in client.get(PAGE).content.decode()


class TestSharedGate:
    """The no-persona reader, whose access this page inherited from Ülevaade."""

    PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105

    @pytest.fixture(autouse=True)
    def gate_mode(self, settings):
        # Through the shared helper, which states the throttle rather than
        # inheriting whatever `config/settings.py` happens to default to.
        return apply_shared_gate(settings, self.PASSWORD)

    @pytest.fixture
    def behind_the_gate(self, client):
        response = client.post(reverse("accounts:shared_gate"), {"password": self.PASSWORD})
        assert response.status_code == 302
        return client

    def test_the_department_page_opens_with_no_persona(self, behind_the_gate, world):
        response = behind_the_gate.get(PAGE)
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated
        assert "Vajab sekkumist" in response.content.decode()

    def test_it_carries_neither_manager_section(self, behind_the_gate, world):
        """Knowing a password says nothing about who is reading."""
        body = behind_the_gate.get(PAGE).content.decode()
        assert "uxstat" not in body
        assert "Tehtud" not in body

    def test_a_restricted_matter_is_invisible_to_it(self, behind_the_gate, specialist, world):
        create_matter(
            title="Piiratud teema jagatud ukse taga",
            owner=specialist,
            visibility=Visibility.RESTRICTED,
            reference_year=2026,
            actor=specialist,
        )
        assert "Piiratud teema jagatud ukse taga" not in behind_the_gate.get(PAGE).content.decode()

    def test_the_old_address_still_opens_it(self, behind_the_gate, world):
        response = behind_the_gate.get(OVERVIEW_LEGACY, follow=True)
        assert response.status_code == 200
        assert response.request["PATH_INFO"] == PAGE


# =========================================================================
# §41 — an unauthorized section is not calculated
# =========================================================================


def test_a_non_head_never_reaches_the_manager_builders(client, specialist, world, monkeypatch):
    """Not "computed and hidden" — not computed.

    Asserted by making the two builders explode. A template condition over data
    that was read anyway is a leak waiting for one careless `{% if %}` to be
    edited; a branch that never calls them cannot leak whatever the template
    later says.
    """

    def refuse(*args, **kwargs):  # pragma: no cover - the assertion is that it is not called
        raise AssertionError("a manager-only population was read for a non-head")

    monkeypatch.setattr(dep.dd, "team_rows", refuse)
    monkeypatch.setattr(dep.dd, "build_digest", refuse)

    client.force_login(specialist)
    assert client.get(PAGE).status_code == 200


def test_the_head_does_reach_them(client, department_head, world, monkeypatch):
    """The counterpart, so the test above cannot pass by never being reached."""
    called: list[str] = []
    original = dep.dd.team_rows
    monkeypatch.setattr(
        dep.dd, "team_rows", lambda *a, **k: (called.append("team"), original(*a, **k))[1]
    )
    client.force_login(department_head)
    client.get(PAGE)
    assert called == ["team"]


def test_the_pseudo_viewer_never_becomes_a_head(world):
    """Authority comes from the authenticated role, never from the viewer."""
    from app.core.authorization import DEPARTMENT_VIEWER, is_department_head

    assert not is_department_head(DEPARTMENT_VIEWER)
    built = dep.build_department(DEPARTMENT_VIEWER, is_head=False)
    assert built.team == []
    assert built.digest is None


# =========================================================================
# §36 — every Seis figure opens the population it counted
# =========================================================================


SEIS_POPULATIONS = {
    "overdue": lambda today: {"olek": "avatud", "liik": "FULL", "too": wi.WORK_OVERDUE},
    "week": lambda today: {
        "olek": "avatud",
        "liik": "FULL",
        "too": wi.WORK_DEADLINE_THIS_WEEK,
    },
    "unassigned": lambda today: {"olek": "avatud", "liik": "FULL", "vastutaja": "puudub"},
    "unreviewed": lambda today: dd._unreviewed_params(today),
    "no_action": lambda today: {"olek": "avatud", "liik": "FULL", "tegevus": "puudub"},
}


def test_the_strip_carries_the_six_approved_figures(department_head, world, today):
    built = page_for(department_head, today=today)
    assert [figure.key for figure in built.seis] == [
        "overdue",
        "week",
        "unassigned",
        "unreviewed",
        "no_action",
        "sent",
    ]
    assert [figure.caption for figure in built.seis] == [
        "üle tähtaja",
        "tähtaeg sel nädalal",
        "vastutajata",
        "uut läbi vaatamata",
        "järgmise tegevuseta",
        "arvamust välja · 7 p",
    ]


@pytest.mark.parametrize("key", sorted(SEIS_POPULATIONS))
def test_a_seis_figure_counts_its_canonical_population(department_head, world, today, key):
    built = page_for(department_head, today=today)
    figure = next(item for item in built.seis if item.key == key)
    canonical = register_population(department_head, SEIS_POPULATIONS[key](today), today=today)
    assert figure.value == canonical.count()


@pytest.mark.parametrize("key", sorted(SEIS_POPULATIONS))
def test_a_seis_figure_opens_the_matters_it_counted(client, department_head, world, today, key):
    """By id, not by total: two populations of the same size are not the same list."""
    client.force_login(department_head)
    built = page_for(department_head, today=today)
    figure = next(item for item in built.seis if item.key == key)

    response = client.get(figure.url)
    assert response.status_code == 200
    shown = {matter.pk for matter in response.context["page"].object_list}
    counted = set(
        register_population(department_head, SEIS_POPULATIONS[key](today), today=today).values_list(
            "pk", flat=True
        )
    )
    assert shown == counted
    assert figure.value == len(counted)


def test_the_sent_figure_states_a_number_it_cannot_open(department_head, world, today):
    """The one figure on the strip that carries no link, and why.

    It counts a seven-day window; the Arvamused workspace narrows by year and by
    month. The only destination available therefore holds more letters than the
    number beside it, so the figure states the number and offers nothing — an
    honest number beats a link to a different list, which is the treatment the
    team table's three historical columns already get (docs/adr/0049 §4, DS-24).
    """
    built = page_for(department_head, today=today)
    figure = next(item for item in built.seis if item.key == "sent")

    assert (
        figure.value
        == dd.sent_submissions(
            department_head, since=today - timedelta(days=dd.SENT_WINDOW_DAYS)
        ).count()
    )
    assert figure.url == ""
    assert [item.key for item in built.seis if not item.url] == ["sent"]


def test_every_other_figure_lands_on_the_register_rows(department_head, world, today):
    """A filtered register opens on its search box and its narrowing panel.

    Without the fragment the rows are below all of it, and a reader who clicked
    «12 üle tähtaja» has to scroll to find out whether twelve came back. It was
    Ülevaade's alone, so the two pages behaved differently from the same kind of
    number; on one strip they land the same way.
    """
    built = page_for(department_head, today=today)
    register = [item for item in built.seis if item.url.startswith(REGISTER)]

    assert len(register) == 5
    for figure in register:
        assert figure.url.endswith(dd.RESULTS_ANCHOR), figure.key


def test_the_strip_no_longer_carries_what_moved_to_the_rail(department_head, world, today):
    """«Uut sel nädalal» is a rail row now, and it is only in one place."""
    built = page_for(department_head, today=today)
    assert "uut sel nädalal" not in [figure.caption for figure in built.seis]
    assert "Uut sel nädalal" in [row.label for row in built.incoming]


# =========================================================================
# §18 — the Uued teemad rail
# =========================================================================


def test_the_incoming_rail_carries_exactly_the_three_approved_rows(department_head, world, today):
    built = page_for(department_head, today=today)
    assert [row.label for row in built.incoming] == [
        "Uut sel nädalal",
        "Uut läbi vaatamata",
        f"Muutusteta {wi.QUIET_DAYS} p",
    ]


def test_every_rail_row_opens_what_it_counted(client, department_head, world, today):
    client.force_login(department_head)
    built = page_for(department_head, today=today)

    for row in [*built.incoming, *built.areas]:
        response = client.get(row.url)
        assert response.status_code == 200, row.label
        assert response.context["total"] == row.count, row.label


# =========================================================================
# §37 — one business fact, one definition
# =========================================================================


def test_reporting_matches_team_total(department_head, world, today):
    """Aruandlus and the team table count the same opinions, from one window.

    The two were a Matter count and a Submission count respectively, so a file
    that produced two opinions in a year made them differ by one — which the
    fixture deliberately creates.
    """
    built = page_for(department_head, today=today)
    total = next(row for row in built.team if row.is_total)
    index = {key: position for position, (key, _l, _g, _s) in enumerate(dd.TEAM_COLUMNS)}
    reporting = {row.label: row.count for row in built.reporting}

    start, end = dd.reporting_year(today)
    canonical = dd.sent_submissions(department_head, since=start, until=end).count()

    assert canonical >= 3, "the fixture must carry more opinions than files"
    assert total.cells[index["sent_year"]].value == canonical
    assert reporting["Saadetud arvamusi"] == canonical


def test_the_year_columns_count_opinions_rather_than_files(department_head, world, today):
    """Two opinions on one Matter are two opinions."""
    built = page_for(department_head, today=today)
    total = next(row for row in built.team if row.is_total)
    index = {key: position for position, (key, _l, _g, _s) in enumerate(dd.TEAM_COLUMNS)}

    files_with_opinions = (
        Matter.objects.filter(
            submissions__status=SubmissionStatus.SENT, submissions__sent_at__year=today.year
        )
        .distinct()
        .count()
    )
    assert total.cells[index["sent_year"]].value > files_with_opinions


def test_the_reporting_rail_counts_confirmed_work_victories(
    department_head, specialist, world, today
):
    """The status this field actually stores, and the year the list filters on."""
    confirm_work_victory(
        record=add_work_victory_candidate(
            matter=world["this_week"],
            title="Üleminekuaeg pikendati",
            period_date=date(today.year, 3, 1),
            period_end=date(today.year, 3, 31),
            date_precision=DatePrecision.MONTH,
            actor=specialist,
        ),
        actor=department_head,
    )
    built = page_for(department_head, today=today)
    reporting = {row.label: row for row in built.reporting}

    assert reporting["Töövõite kinnitatud"].count == 1
    query = parse_qs(urlparse(reporting["Töövõite kinnitatud"].url).query)
    assert query["staatus"] == [WorkVictoryStatus.CONFIRMED]
    assert query["aasta"] == [str(today.year)]


def test_every_reporting_row_opens_a_list_of_its_own_size(client, department_head, world, today):
    client.force_login(department_head)
    built = page_for(department_head, today=today)

    for row in built.reporting:
        response = client.get(row.url)
        assert response.status_code == 200, row.label
        assert response.context["total"] == row.count, row.label


def test_the_team_total_and_the_strip_agree(department_head, world, today):
    built = page_for(department_head, today=today)
    total = next(row for row in built.team if row.is_total)
    unassigned = next(row for row in built.team if row.is_unassigned)
    figures = {figure.key: figure.value for figure in built.seis}
    index = {key: position for position, (key, _l, _g, _s) in enumerate(dd.TEAM_COLUMNS)}

    assert total.cells[index["overdue"]].value == figures["overdue"]
    assert total.cells[index["week"]].value == figures["week"]
    assert total.cells[index["no_action"]].value == figures["no_action"]
    assert unassigned.cells[index["open"]].value == figures["unassigned"]
    assert total.cells[index["open"]].value == built.open_matters


# =========================================================================
# §43 — rows and Matters are different numbers
# =========================================================================


def test_one_matter_with_two_attention_rows_counts_as_two_rows_and_one_teema(
    department_head, world, today
):
    """41 rida over «Ava kõik 33 teemat» is the whole distinction, in miniature."""
    built = page_for(department_head, today=today)
    matter = world["doubly_late"]

    rows = [row for row in built.interventions if row.matter.pk == matter.pk]
    assert len(rows) == 2
    assert built.intervention_total > built.intervention_matters


def test_the_intervention_link_opens_the_matters_it_named(client, department_head, world, today):
    client.force_login(department_head)
    built = page_for(department_head, today=today)
    response = client.get(built.intervention_url)
    assert response.context["total"] == built.intervention_matters


def test_a_deadline_group_counts_rows_and_links_matters(department_head, specialist, today):
    """Two genuine obligations on one file are two rows and one Matter to open."""
    matter = create_matter(
        title="Kaks kohustust ühel teemal",
        owner=specialist,
        reference_year=2026,
        actor=specialist,
    )
    set_next_action(
        matter=matter,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )
    add_important_date(
        matter=matter,
        title="Oluline tähtaeg täna",
        date_value=today,
        period_end=today,
        actor=specialist,
    )

    group = next(g for g in dd.upcoming_groups(specialist, today) if g.key == "tana")
    assert group.count == 2
    assert group.matter_count == 1


# =========================================================================
# Eesolev discloses its windows in place
#
# Each window used to head itself with «kõik N →», a link into the register. It
# is a native `<details>` now: the heading is the summary, the deadlines are the
# body, and «kõik N» opens them where the reader is standing.
#
# Two things follow, and both are asserted rather than assumed. The count is the
# *deadlines* it reveals and no longer the Matters the register would have
# listed, because a control that opens two rows and says «kõik 1» is describing
# a different population from the one it produces. And the second, nested
# «Näita veel N» disclosure the two sliced windows carried is gone: opening a
# window shows the whole window.
#
# What did not change: the section's own «Kõik tähtajad →» is still the
# register, and nothing about which dates are eligible, whose they are or what
# order they come in.
# =========================================================================


def eesolev(client, person) -> str:
    """The rendered *Eesolev* section, and nothing else on the page.

    Sliced rather than parsed, and the status is checked first: a redirect
    decodes to a body holding none of these strings, so an assertion about
    absence would pass on a page nobody could open.
    """
    client.force_login(person)
    response = client.get(PAGE)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'aria-label="Eesolev"' in body
    return body.split('aria-label="Eesolev"')[-1].split("</section>")[0]


def windows_of(panel: str) -> list[str]:
    """Each window's `<details>`, in the order the panel prints them."""
    return [chunk.split("</details>")[0] for chunk in panel.split('<details class="uxdl">')[1:]]


def bodies_of(panel: str) -> list[str]:
    """What each window discloses: its `<details>` with the `<summary>` cut off."""
    return [window.split("</summary>")[-1] for window in windows_of(panel)]


@pytest.fixture
def four_windows(specialist, today):
    """One populated window at each of three horizons, and a WAIT beside one.

    The three deadlines in *kaugemal* are what makes the ordering and the
    "everything is inside the disclosure" assertions worth making: the far
    window is one of the two that used to print five rows and hide the rest.
    """

    def deadline(title, *, when):
        matter = create_matter(title=title, owner=specialist, reference_year=2026, actor=specialist)
        set_next_action(
            matter=matter,
            text="Esita arvamus",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=when,
            actor=specialist,
        )
        return matter

    starts = {key: begins for key, _label, begins, _ends in dd.upcoming_windows(today)}
    made = {
        "tana": [deadline("Tänane tähtaeg", when=starts["tana"])],
        "nadal": [deadline("Nädala tähtaeg", when=starts["nadal"])],
        "kaugemal": [
            deadline(f"Kauge tähtaeg {index}", when=starts["kaugemal"] + timedelta(days=index))
            for index in range(3)
        ],
    }

    # Not a deadline, and it sits in the same window as one that is. A predicate
    # that widened by one kind would put it on this panel and nothing else here
    # would notice (master specification 18.8).
    watched = create_matter(
        title="Ootel — mitte tähtaeg", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=watched,
        text="Ootan vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=starts["nadal"],
        actor=specialist,
    )
    made["ootel"] = [watched]
    return made


def test_every_populated_window_is_one_details_shut_by_default(
    client, department_head, four_windows, today
):
    """A native disclosure, and none of them opens itself.

    The tag is matched exactly, so `<details class="uxdl" open>` fails here
    rather than shipping a panel that decides which window the reader came for.
    The `<div>` it replaces fails the same assertion, and so would a scripted
    imitation of one.
    """
    panel = eesolev(client, department_head)
    populated = [group for group in dd.upcoming_groups(department_head, today) if group.count]

    assert len(populated) == 3
    assert panel.count('<details class="uxdl">') == len(populated)
    assert panel.count('<summary class="uxdl__head">') == len(populated)
    assert '<div class="uxdl">' not in panel
    assert "<details" not in panel.replace('<details class="uxdl">', "")


def test_each_summary_names_its_window_and_counts_the_deadlines_it_opens(
    client, department_head, four_windows, today
):
    """Label, interval and «kõik N» — and N is rows, not files.

    The count is read off the group rather than written here, so a window whose
    population changes cannot make this pass while the page says something
    else.
    """
    panel = eesolev(client, department_head)
    populated = [group for group in dd.upcoming_groups(department_head, today) if group.count]
    summaries = [window.split("</summary>")[0] for window in windows_of(panel)]

    assert len(summaries) == len(populated)
    for group, summary in zip(populated, summaries, strict=True):
        assert group.label.upper() in summary, group.key
        assert group.range_label in summary, group.key
        assert f"kõik {group.count}" in summary, group.key
        # The chevron is a `::after` in the stylesheet. In the markup it would
        # be in the accessible name, read out as a word.
        assert "▾" not in summary and "▴" not in summary


def test_the_count_on_the_summary_is_deadlines_and_not_matters(
    client, department_head, specialist, today
):
    """Two obligations on one file open two rows, so the control says «kõik 2».

    This is the count semantics the disclosure changed. «kõik N →» used to open
    the register, which lists files, so it counted files; «kõik N» opens the
    rows underneath it, so it counts rows. The fixture is the one case where the
    two numbers differ, and the old number is asserted absent as well — a panel
    that printed both would pass a test that only looked for the new one.
    """
    matter = create_matter(
        title="Kaks tähtaega ühel teemal", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=matter,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )
    add_important_date(
        matter=matter,
        title="Oluline tähtaeg täna",
        date_value=today,
        period_end=today,
        actor=specialist,
    )

    group = next(g for g in dd.upcoming_groups(department_head, today) if g.key == "tana")
    assert (group.count, group.matter_count) == (2, 1)

    panel = eesolev(client, department_head)
    assert "kõik 2" in panel
    assert "kõik 1" not in panel


def test_opening_a_window_exposes_every_row_it_counted_in_its_own_order(
    client, department_head, four_windows, today
):
    """«kõik 3» opens three rows, in the order the read model put them.

    Every row is inside its own window's `<details>`, so this fails if a row
    leaks into the panel around the disclosures as well as if one is missing.
    The order is compared against `group.items` rather than against a list
    written here: sorting is not this round's to change, and an assertion that
    restated it would go green on a branch that broke it.
    """
    panel = eesolev(client, department_head)
    populated = [group for group in dd.upcoming_groups(department_head, today) if group.count]
    bodies = bodies_of(panel)

    assert len(bodies) == len(populated)
    for group, body in zip(populated, bodies, strict=True):
        titles = [item.matter.title for item in group.items]
        assert len(titles) == group.count
        positions = [body.find(title) for title in titles]
        assert all(position >= 0 for position in positions), f"{group.key}: {titles}"
        assert positions == sorted(positions), f"{group.key} reordered its rows"


def test_a_window_holds_the_whole_of_itself_rather_than_a_preview(
    client, department_head, specialist, today
):
    """The rows the read model still calls `rest` are in the same disclosure.

    *Kaugemal* is one of the two windows that used to print five rows and put
    the remainder behind a second «Näita veel N» control inside the group. Six
    rows, one disclosure, and the nested control is gone from the panel
    entirely — `deadline_more.html` was deleted with it, so this guards against
    it coming back rather than against a live branch.
    """
    far_starts = dd.upcoming_windows(today)[-1][2]
    for index in range(6):
        matter = create_matter(
            title=f"Kauge tähtaeg {index}", owner=specialist, reference_year=2026, actor=specialist
        )
        set_next_action(
            matter=matter,
            text="Esita arvamus",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=far_starts + timedelta(days=index),
            actor=specialist,
        )

    group = next(g for g in dd.upcoming_groups(department_head, today) if g.key == "kaugemal")
    assert group.count == 6
    assert group.preview and group.rest, "the fixture no longer exercises the old split"

    panel = eesolev(client, department_head)
    assert "Näita veel" not in panel
    assert "Näita kõiki" not in panel

    (body,) = bodies_of(panel)
    for item in [*group.preview, *group.rest]:
        assert item.matter.title in body, item.matter.title


def test_the_window_no_longer_links_to_the_register_and_the_section_still_does(
    client, department_head, four_windows, today
):
    """One link left the panel and one stayed, and they are not the same link.

    «Kõik tähtajad →» opens the register on every open deadline and is
    navigation. A window's own control is not: it opens rows on this page. The
    register URL each window used to carry is asserted absent by its own value,
    and `too_alates` — the parameter that made a link a *window's* link, which
    the section's link does not carry — is asserted absent too, so this cannot
    pass by the whole panel having lost its links.
    """
    panel = eesolev(client, department_head)

    for group in dd.upcoming_groups(department_head, today):
        if group.count:
            assert escape(group.url) not in panel, group.key
    assert "too_alates" not in panel

    assert "Kõik tähtajad →" in panel
    assert "?olek=avatud&amp;liik=FULL&amp;too=tahtaeg-vahemik" in panel

    # And nothing interactive inside a disclosure trigger: a link there would be
    # two controls in one place, whatever it pointed at.
    for window in windows_of(panel):
        head = window.split("</summary>")[0]
        assert "<a " not in head
        assert "<button" not in head


def test_a_review_date_still_never_reaches_a_window(client, department_head, four_windows, today):
    """WAIT and MONITOR are «look at this again», and the disclosure did not widen that.

    The fixture puts a WAIT in the same window as a real deadline, so a panel
    that had started accepting review dates would print it beside one that
    belongs — and the count on that summary would grow with it.
    """
    panel = eesolev(client, department_head)
    assert "Ootel — mitte tähtaeg" not in panel

    week = next(g for g in dd.upcoming_groups(department_head, today) if g.key == "nadal")
    assert week.count == 1
    assert "kõik 1" in panel


def test_a_restricted_deadline_is_disclosed_to_the_team_and_not_to_a_reader(
    client, department_head, specialist, reader, today
):
    """Authorization is upstream of the disclosure, and stayed there.

    Serving the rows with the page and hiding them in CSS would be a leak no
    assertion about the *visible* panel could see, so this reads the markup: the
    title is in the department head's HTML and not in the reader's, and each of
    them is offered a window counting only what they may see.

    A `READER` rather than a second specialist, because since docs/adr/0042 a
    second specialist is not an outsider — asserting absence against one would
    be asserting nothing (`tests/conftest.py`).
    """
    restricted = create_matter(
        title="Piiratud tähtajaga teema",
        owner=specialist,
        reference_year=2026,
        actor=specialist,
        visibility=Visibility.RESTRICTED,
    )
    set_next_action(
        matter=restricted,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today,
        actor=specialist,
    )

    for person, may_see in ((department_head, True), (reader, False)):
        panel = eesolev(client, person)
        assert ("Piiratud tähtajaga teema" in panel) is may_see, person
        group = next(g for g in dd.upcoming_groups(person, today) if g.key == "tana")
        assert group.count == (1 if may_see else 0)
        assert ("kõik 1" in panel) is may_see, person


# =========================================================================
# §39 — the five windows partition the future
# =========================================================================


CALENDARS = [
    pytest.param(date(2026, 8, 30), id="today-sunday"),
    pytest.param(date(2026, 8, 31), id="today-monday-month-end"),
    pytest.param(date(2026, 9, 28), id="next-week-crosses-the-month"),
    pytest.param(date(2026, 12, 28), id="year-boundary"),
    pytest.param(date(2028, 2, 27), id="leap-february"),
    pytest.param(date(2026, 2, 25), id="short-february"),
]


@pytest.mark.parametrize("day", CALENDARS)
def test_upcoming_windows_partition(day):
    """Five consecutive intervals, and nothing between or across them.

    Checked day by day over a year rather than on sampled dates: a boundary that
    is wrong by one day is exactly the bug that survives a test which only looks
    at the middle of each window.
    """
    windows = dd.upcoming_windows(day)
    assert [key for key, _l, _s, _e in windows] == list(dd.UPCOMING_WINDOWS)

    for cursor in (day + timedelta(days=offset) for offset in range(366)):
        holding = [
            key
            for key, _label, starts, ends in windows
            if starts <= cursor and (ends is None or cursor <= ends)
        ]
        assert holding, f"{cursor} is in no window (today={day})"
        assert len(holding) == 1, f"{cursor} is in {holding} (today={day})"


@pytest.mark.parametrize("day", CALENDARS)
def test_the_windows_start_where_the_labels_say(day):
    windows = {key: (label, starts, ends) for key, label, starts, ends in dd.upcoming_windows(day)}

    assert windows["tana"][1] == windows["tana"][2] == day
    assert windows["homme"][1] == day + timedelta(days=1)
    assert windows["homme"][0] == dd.weekday_name(day + timedelta(days=1))
    assert windows["nadal"][1] == day + timedelta(days=2)
    assert windows["nadal"][2] == wi.end_of_iso_week(day) + timedelta(days=7)
    assert windows["kuu"][1] == windows["nadal"][2] + timedelta(days=1)
    assert windows["kaugemal"][1] == windows["kuu"][2] + timedelta(days=1)
    assert windows["kaugemal"][2] is None


def test_no_future_deadline_falls_out_of_the_panel(department_head, specialist, today):
    """Every real deadline ahead is in exactly one rendered group."""
    for offset in (0, 1, 2, 5, 9, 20, 45, 120, 400):
        matter = create_matter(
            title=f"Tähtaeg {offset} päeva pärast",
            owner=specialist,
            reference_year=2026,
            actor=specialist,
        )
        set_next_action(
            matter=matter,
            text="Esita arvamus",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today + timedelta(days=offset),
            actor=specialist,
        )

    groups = dd.upcoming_groups(department_head, today)
    placed = [item.matter_id for group in groups for item in group.items]
    eligible = [
        item.matter_id
        for item in wi.real_deadlines(wi.work_items(department_head, today=today))
        if item.when is not None and item.when >= today
    ]

    assert sorted(placed) == sorted(eligible)
    assert len(placed) == len(set(placed)), "a deadline landed in two windows"


def test_a_new_deadline_source_reaches_eesolev_without_osakond_code(
    department_head, specialist, today
):
    """The property this page was built to have, asserted on the source that proved it.

    `Arvamuse tähtaeg` became a first-class work source on a parallel branch
    (ADR 0031 §5). Nothing in `app/matters/department.py` or
    `department_dashboard.py` mentions `response_deadline`, and it appears in
    *Eesolev* all the same — because the panel consumes `real_deadlines()`
    generically rather than naming the sources it will accept. A future source
    added to the shared read model arrives here the same way (brief §26).
    """
    import app.matters.department
    import app.matters.department_dashboard

    for module in (app.matters.department, app.matters.department_dashboard):
        assert "response_deadline" not in inspect.getsource(module), module.__name__

    matter = create_matter(
        title="Arvamuse tähtajaga teema",
        owner=specialist,
        reference_year=2026,
        actor=specialist,
        response_deadline=today + timedelta(days=3),
    )

    groups = {group.key: group for group in dd.upcoming_groups(department_head, today)}
    assert matter.pk in {item.matter_id for item in groups["nadal"].items}


def test_an_overdue_deadline_is_not_in_eesolev(department_head, world, today):
    """Eesolev is what is ahead. What is late is in Vajab sekkumist."""
    groups = dd.upcoming_groups(department_head, today)
    placed = {item.matter_id for group in groups for item in group.items}
    assert world["doubly_late"].pk not in placed


def test_kaugemal_is_a_list_rather_than_a_summary(department_head, specialist, today):
    far = dd.upcoming_windows(today)[-1][2]
    for offset in range(6):
        matter = create_matter(
            title=f"Kauge tähtaeg {offset}",
            owner=specialist,
            reference_year=2026,
            actor=specialist,
        )
        set_next_action(
            matter=matter,
            text="Esita arvamus",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=far + timedelta(days=offset),
            actor=specialist,
        )

    group = next(g for g in dd.upcoming_groups(department_head, today) if g.key == "kaugemal")
    assert group.count == 6
    assert len(group.preview) == dd.UPCOMING_PREVIEW
    assert len(group.rest) == 1
    assert group.range_label.startswith("alates ")


def test_every_upcoming_group_opens_the_matters_it_counted(
    client, department_head, specialist, today
):
    client.force_login(department_head)
    for offset in (0, 1, 3, 12, 200):
        matter = create_matter(
            title=f"Tähtaeg +{offset}", owner=specialist, reference_year=2026, actor=specialist
        )
        set_next_action(
            matter=matter,
            text="Esita arvamus",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today + timedelta(days=offset),
            actor=specialist,
        )

    for group in dd.upcoming_groups(department_head, today):
        if not group.count:
            continue
        response = client.get(group.url)
        assert response.status_code == 200, group.key
        shown = {matter.pk for matter in response.context["page"].object_list}
        assert shown == {item.matter_id for item in group.items}, group.key
        assert response.context["total"] == group.matter_count, group.key


# =========================================================================
# §38 — waiting is not lateness
# =========================================================================


def test_wait_not_in_upcoming(department_head, specialist, today):
    """A review date is «look at this again», never a missed obligation.

    WAIT and MONITOR both, and in each of the future windows, because the merge
    is exactly the kind of change that could quietly widen one predicate.
    """
    offsets = {"täna": 0, "homme": 1, "nädal": 3, "kuu": 12, "kaugemal": 200}
    watched = []
    for kind in (ActionKind.WAIT, ActionKind.MONITOR):
        for label, offset in offsets.items():
            matter = create_matter(
                title=f"{kind.label} {label}",
                owner=specialist,
                reference_year=2026,
                actor=specialist,
            )
            set_next_action(
                matter=matter,
                text=f"{kind.label} {label}",
                kind=kind,
                date_semantics=DateSemantics.REVIEW_ON,
                target_date=today + timedelta(days=offset),
                actor=specialist,
            )
            watched.append(matter.pk)

    # And one genuine DO deadline, so the assertion cannot pass on an empty panel.
    real = create_matter(
        title="Päris tähtaeg", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=real,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=3),
        actor=specialist,
    )

    placed = {
        item.matter_id
        for group in dd.upcoming_groups(department_head, today)
        for item in group.items
    }
    assert real.pk in placed
    assert not placed & set(watched)

    figures = {figure.key: figure.value for figure in dd.seis_figures(department_head, today)}
    assert figures["overdue"] == 0
    assert figures["week"] == len(
        [
            item
            for item in wi.real_deadlines(wi.work_items(department_head, today=today))
            if item.when is not None and today <= item.when <= wi.end_of_iso_week(today)
        ]
    )


def test_a_passed_review_date_is_attention_rather_than_overdue(
    client, department_head, specialist, today
):
    """It is on the page, in the list that means «look at this again»."""
    matter = create_matter(
        title="Ootan vastust", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=matter,
        text="Ootan uut sõnastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - timedelta(days=4),
        actor=specialist,
    )

    built = page_for(department_head, today=today)
    assert matter.pk in {row.matter.pk for row in built.interventions}

    figures = {figure.key: figure.value for figure in built.seis}
    assert figures["overdue"] == 0

    client.force_login(department_head)
    body = client.get(PAGE).content.decode()
    assert "interrow--danger" not in body


# =========================================================================
# §40 — the Tehtud kind filter
# =========================================================================


@pytest.fixture
def finished(db, department_head, specialist, today, send_opinion):
    """One of each kind of finished work, inside the default seven-day window."""
    matter = create_matter(
        title="Lõpetatud töö", owner=specialist, reference_year=2026, actor=specialist
    )
    send_opinion(matter, when=timezone.now() - timedelta(days=1), title="Lõpetatud arvamus")
    confirm_work_victory(
        record=add_work_victory_candidate(
            matter=matter,
            title="Töövõit",
            period_date=today,
            period_end=today,
            date_precision=DatePrecision.EXACT,
            actor=specialist,
        ),
        actor=department_head,
    )
    factories.EntryFactory(matter=matter, occurred_at=timezone.now() - timedelta(days=1))
    closed = create_matter(title="Suletud töö", owner=specialist, actor=specialist)
    close_matter(matter=closed, disposition=Disposition.COMPLETED, actor=specialist)
    return matter


@pytest.mark.parametrize(
    ("value", "kinds"),
    [
        ("arvamused", {"sent"}),
        ("toovoidud", {"win"}),
        ("suletud", {"closed"}),
        ("sissekanded", {"entry"}),
    ],
)
def test_digest_kind_filter(client, department_head, finished, today, value, kinds):
    """The filter narrows the rows and leaves the summary alone.

    Both halves matter. A summary that moved with the filter would leave the
    page unable to say what the period produced; rows that ignored it would make
    the control a decoration.
    """
    client.force_login(department_head)
    response = client.get(f"{PAGE}?liik={value}")
    digest = response.context["page"].digest
    whole = dd.build_digest(department_head, digest.period, dd.KIND_ALL)

    assert digest.rows, value
    assert {row.kind for row in digest.rows} == kinds
    assert (digest.sent, digest.closed, digest.victories, digest.entries) == (
        whole.sent,
        whole.closed,
        whole.victories,
        whole.entries,
    )
    assert digest.total < whole.total


def test_an_unknown_kind_shows_everything(client, department_head, finished):
    """A hand-edited URL must not render a convincing empty page."""
    client.force_login(department_head)
    digest = client.get(f"{PAGE}?liik=ei-ole-olemas").context["page"].digest
    assert digest.kind == dd.KIND_ALL
    assert {row.kind for row in digest.rows} == {"sent", "win", "closed", "entry"}


def test_the_two_controls_preserve_each_other(client, department_head, finished):
    """Changing the period keeps the kind, and changing the kind keeps the period."""
    client.force_login(department_head)
    built = client.get(f"{PAGE}?periood=30&liik=arvamused").context["page"]

    for option in built.periods:
        query = parse_qs(option.query)
        assert query["liik"] == ["arvamused"], option.key
        assert query["vaade"] == [ov.SCOPE_DEPARTMENT]

    for option in built.digest.kinds:
        assert parse_qs(option.query)["periood"] == ["30"], option.key


def test_a_custom_range_survives_a_change_of_kind(client, department_head, finished):
    client.force_login(department_head)
    built = client.get(f"{PAGE}?periood=vahemik&alates=01.01.2026&kuni=31.12.2026").context["page"]

    assert built.digest.period.is_custom
    for option in built.digest.kinds:
        query = parse_qs(option.query)
        assert query["periood"] == ["vahemik"]
        # Re-rendered in the product's own date format rather than echoed back,
        # and read again by `parse_flexible_date`, which accepts both.
        assert query["alates"] == ["1.1.2026"]
        assert query["kuni"] == ["31.12.2026"]


def test_the_summary_counts_the_whole_period_not_the_visible_rows(
    client, department_head, finished
):
    client.force_login(department_head)
    body = client.get(f"{PAGE}?liik=arvamused").content.decode()
    assert "sissekannet" in body


# =========================================================================
# §42 — restricted content
# =========================================================================


def test_restricted_not_leaked(client, department_head, specialist, other_specialist, today):
    """A file one reader may see contributes nothing at all to another's page.

    Not "the title is absent" — the counts are smaller too. Authorization before
    arithmetic means an unauthorized reader's numbers were never inflated by a
    row they may not open.
    """
    hidden = create_matter(
        title="Piiratud eelnõu, mida ei tohi näha",
        owner=specialist,
        visibility=Visibility.RESTRICTED,
        reference_year=2026,
        actor=specialist,
    )
    set_next_action(
        matter=hidden,
        text="Hilinenud piiratud tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=5),
        actor=specialist,
    )
    visible = create_matter(
        title="Tavaline eelnõu", owner=specialist, reference_year=2026, actor=specialist
    )
    set_next_action(
        matter=visible,
        text="Hilinenud tavaline tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=5),
        actor=specialist,
    )

    outsider = factories.UserFactory(role=UserRole.READER)
    head_page = page_for(department_head, today=today)
    outsider_page = dep.build_department(outsider, is_head=False, today=today)

    def overdue(built):
        return next(figure.value for figure in built.seis if figure.key == "overdue")

    assert overdue(head_page) == overdue(outsider_page) + 1
    assert hidden.pk in {row.matter.pk for row in head_page.interventions}
    assert hidden.pk not in {row.matter.pk for row in outsider_page.interventions}
    assert outsider_page.open_matters == head_page.open_matters - 1

    client.force_login(outsider)
    body = client.get(PAGE).content.decode()
    assert "Piiratud eelnõu, mida ei tohi näha" not in body
    assert "Tavaline eelnõu" in body


def test_a_restricted_matter_stays_out_of_the_rails(department_head, specialist, today):
    outsider = factories.UserFactory(role=UserRole.READER)
    hidden = create_matter(
        title="Piiratud, vastutajata",
        owner=None,
        visibility=Visibility.RESTRICTED,
        reference_year=2026,
        actor=specialist,
        received_date=today,
    )
    assert hidden.pk

    head_rail = {row.label: row.count for row in page_for(department_head, today=today).incoming}
    outsider_rail = {
        row.label: row.count
        for row in dep.build_department(outsider, is_head=False, today=today).incoming
    }
    assert head_rail["Uut sel nädalal"] == outsider_rail["Uut sel nädalal"] + 1


# =========================================================================
# §46 — Valdkonniti is a scope of the same route
# =========================================================================


def test_valdkonniti_uses_the_approved_area_view(client, department_head, world):
    client.force_login(department_head)
    response = client.get(f"{PAGE}?vaade=valdkonniti")
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["page"].is_areas
    assert "uxstat" not in body, "there is no team grid in the area scope"
    assert "Vastutajata valdkonnad" in body
    assert "Kõige aktiivsemad asutused" in body
    assert "Aruandlus" in body


def test_both_scopes_print_one_aruandlus_and_the_same_numbers(
    client, department_head, world, today
):
    """Switching scope must not change a number nobody asked to change.

    There were two blocks reading two nearly-identical definitions of «Suletud
    teemasid», and the difference only showed to somebody who happened to switch
    (docs/adr/0049 §8).
    """
    client.force_login(department_head)
    canonical = [
        (row.label, row.count, row.url) for row in dd.reporting_rail(department_head, today)
    ]

    for query in ("", "?vaade=valdkonniti"):
        page = client.get(f"{PAGE}{query}").context["page"]
        assert [(row.label, row.count, row.url) for row in page.reporting] == canonical, query
        body = client.get(f"{PAGE}{query}").content.decode()
        assert body.count('aria-label="Aruandlus"') == 1, query


def test_the_scope_control_returns_to_the_same_route(client, department_head, world):
    client.force_login(department_head)
    body = client.get(f"{PAGE}?vaade=valdkonniti").content.decode()

    assert 'href="?vaade=osakond"' in body
    assert 'href="?vaade=valdkonniti"' in body


def test_an_unknown_scope_falls_back_to_the_department(client, department_head, world):
    client.force_login(department_head)
    response = client.get(f"{PAGE}?vaade=tiim")
    assert response.status_code == 200
    assert response.context["page"].is_department


# =========================================================================
# §32/§33 — the wording the design settled on
# =========================================================================


def test_the_page_uses_the_approved_vocabulary(client, department_head, world):
    client.force_login(department_head)
    body = client.get(PAGE).content.decode()

    for word in ("Osakond", "Vajab sekkumist", "Eesolev", "Tehtud", "uut läbi vaatamata"):
        assert word in body, word

    lowered = body.casefold()
    for forbidden in ("vajab tähelepanu", "triaaž", "seisma jäänud", "tähtajad registris"):
        assert forbidden not in lowered, forbidden


def test_the_page_never_ranks_or_scores_anybody(client, department_head, world):
    client.force_login(department_head)
    body = client.get(PAGE).content.decode().casefold()
    for forbidden in ("töökoormus", "tulemuslikkus", "produktiivsus", "edetabel", "punktisumma"):
        assert forbidden not in body


def test_the_rail_carries_three_blocks_and_no_repeat(client, department_head, world):
    """Koormus is gone and Vajab sekkumist is not in the rail as well."""
    client.force_login(department_head)
    body = client.get(PAGE).content.decode()

    assert 'aria-label="Faktid"' in body
    assert 'aria-label="Koormus"' not in body
    # Once in the main column, and nowhere in the rail. The section carries the
    # phrase twice — as its accessible name and as its heading — so what is
    # counted is the section, not the words.
    assert body.count('aria-label="Vajab sekkumist"') == 1
    assert body.count('aria-label="Aruandlus"') == 1
    assert body.count('class="railblock"') == 3


def test_meeskond_has_no_visible_heading(client, department_head, world):
    """It is the first content block, and the grid captions itself."""
    client.force_login(department_head)
    body = client.get(PAGE).content.decode()

    assert "uxstat" in body
    assert ">Meeskond<" not in body
    assert body.index("uxstat") < body.index("Vajab sekkumist")


# =========================================================================
# §47 — the merged page did not become two pages' worth of queries
# =========================================================================


def test_the_page_does_not_query_once_per_lawyer(client, department_head, world):
    """The query count is a property of the page, not of the department's size."""
    for index in range(6):
        colleague = factories.UserFactory(display_name=f"Kolleeg {index}")
        create_matter(
            title=f"Kolleegi teema {index}",
            owner=colleague,
            reference_year=2026,
            actor=colleague,
        )

    client.force_login(department_head)
    with CaptureQueriesContext(connection) as first:
        client.get(PAGE)
    baseline = len(first)

    for index in range(6, 12):
        colleague = factories.UserFactory(display_name=f"Kolleeg {index}")
        create_matter(
            title=f"Kolleegi teema {index}",
            owner=colleague,
            reference_year=2026,
            actor=colleague,
        )

    with CaptureQueriesContext(connection) as second:
        client.get(PAGE)

    assert len(second) == baseline, (
        f"twice the department cost {len(second)} queries instead of {baseline}"
    )


def test_a_specialist_pays_for_less_than_the_head(client, department_head, specialist, world):
    """Not calculating the manager sections is visible in the query count."""
    client.force_login(department_head)
    with CaptureQueriesContext(connection) as head_queries:
        client.get(PAGE)

    client.force_login(specialist)
    with CaptureQueriesContext(connection) as specialist_queries:
        client.get(PAGE)

    assert len(specialist_queries) < len(head_queries)


def test_naita_veel_holds_the_remainder_of_the_same_list(department_head, world, today):
    """The rows behind «Näita veel N ▾» are the rest of the list above them.

    Moved here from `tests/test_overview_drilldowns.py` when the department
    scope of `build_overview` was retired. The invariant did not move with the
    page — `Department` carries these three properties now — and it is the one
    thing in that file with no equivalent already asserted here, so it travelled
    rather than being deleted with the read model it used to be read from.

    The number on the control, the rows on screen and the rows behind it are
    three readings of one answer, not a second query that can disagree with the
    first.
    """
    page = dep.build_department(department_head, is_head=True, today=today)

    assert page.intervention_preview + page.intervention_rest == page.interventions
    assert page.intervention_remaining == len(page.intervention_rest)
    assert page.intervention_total == len(page.interventions)

    # And no row is on screen *and* behind the disclosure. Two copies of a row
    # is the defect a slice can produce without changing any count.
    shown = [id(row) for row in page.intervention_preview]
    hidden = [id(row) for row in page.intervention_rest]
    assert not set(shown) & set(hidden)
