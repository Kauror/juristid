"""The Teemad register picks its page first and annotates only that page (JPERF-007).

The 2026-10-17 audit measured the register's row query at 10,000 open Matters:
PostgreSQL evaluated the per-row correlated subqueries for every visible Matter
under the top-12 sort, before the limit, and the page took 241 ms for twelve rows.
`_RegisterPaginator` now draws the page as primary keys and reads the annotated
rows for those keys alone.

Two things are held here.

* **Nothing a reader sees changes.** Every page of the register, for readers who
  see different things (a specialist, a READER who may not see restricted work, a
  department head), under every sort, status, data-class, scope and organisation
  filter, renders byte for byte what slicing the annotated query rendered — the
  old paginator is the oracle, run on the same world in the same test. (Since
  docs/adr/0181 a READER sees restricted work too; the readers still differ by
  scope — ``?ulatus=minu`` — and an administrator without a grant sees nothing.)
* **The cost stays bounded.** The row query names the page's ids, and the whole
  page stays within the audit's budget of 30 queries however many Matters the
  register holds.

The «Tehtud» path (JPERF-001) is here too, because it is the other half of the
same pull request: a `Lisa tagasiside` opened on a named round reuses the page's
one institution reading, so it costs at most one query more than the plain Teema
page and draws the same form.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.core.paginator import Paginator
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import forms as matter_forms
from app.matters import views as matter_views
from app.matters.enums import EngagementKind, MatterDataClass
from app.matters.services import add_engagement, add_entry
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

#: The audit's recommended budget for the register (PERFORMANCE-AUDIT.md §10).
REGISTER_QUERY_BUDGET = 30

#: How the row query names the page it reads: its primary keys, as literals.
PAGE_IDS = '"matters_matter"."id" IN (\''


def _world(specialist, other_specialist, organisation):
    """Forty Matters that differ in every way a register row or its order can.

    Restricted and TEST Matters, closed ones, two years, Matters with and without
    a deadline, a restricted entry on a normal Matter (so the *Viimane tegevus* a
    READER sees differs from the specialist's), sent opinions, open steps with and
    without a date, a collaborator (``?ulatus=minu`` joins a many-to-many) and an
    organisation (``?asutus=`` does too).
    """
    today = timezone.localdate()
    second = factories.OrganisationFactory(name="Õiguskantsleri Näidisbüroo")
    matters = []
    for index in range(40):
        owner = (specialist, other_specialist, None)[index % 3]
        closed = index % 9 == 4
        matter = factories.MatterFactory(
            owner=owner,
            title=("Ärimaksu", "Õigusabi", "Zooloogia", "keskkonnatasu", "Andmekaitse")[index % 5]
            + f" eelnõu {index:02d}",
            reference_year=today.year - (1 if index % 4 == 0 else 0),
            visibility=Visibility.RESTRICTED if index % 7 == 0 else Visibility.NORMAL,
            data_class=MatterDataClass.TEST if index % 5 == 1 else MatterDataClass.REAL,
            is_open=not closed,
            disposition=Disposition.COMPLETED if closed else "",
            closed_at=timezone.now() - datetime.timedelta(days=index) if closed else None,
            response_deadline=(
                None if index % 3 == 0 else today + datetime.timedelta(days=(index * 7) % 50 - 20)
            ),
            source_organisations=[organisation if index % 2 else second],
        )
        if index % 6 == 2:
            matter.collaborators.add(specialist)
        if index % 4 == 1:
            add_entry(
                matter=matter,
                body=f"<p>Kirje {index}</p>",
                author=specialist,
                occurred_at=timezone.now() - datetime.timedelta(days=index),
            )
        if index % 8 == 3:
            add_entry(
                matter=matter,
                body=f"<p>Piiratud kirje {index}</p>",
                author=specialist,
                occurred_at=timezone.now() - datetime.timedelta(hours=index),
                visibility_override=Visibility.RESTRICTED,
            )
        if index % 10 == 7:
            factories.send_opinion_through_services(matter, specialist)
        if index % 3 == 1:
            factories.NextActionFactory(
                matter=matter,
                target_date=(today + datetime.timedelta(days=index - 15)) if index % 2 else None,
            )
        matters.append(matter)
    return matters


#: Every narrowing and ordering the register's row query can take.
CASES = [
    {},
    {"jarjestus": "title"},
    {"jarjestus": "updated"},
    {"jarjestus": "deadline"},
    {"jarjestus": "kuupaev_asc"},
    {"jarjestus": "kuupaev_desc"},
    {"jarjestus": "viimane_uusim"},
    {"jarjestus": "viimane_vanim"},
    {"olek": "koik"},
    {"olek": "koik", "jarjestus": "kuupaev_desc"},
    {"andmed": "test"},
    {"andmed": "paris", "olek": "koik"},
    {"ulatus": "minu"},
    {"ulatus": "minu", "jarjestus": "viimane_uusim"},
    {"asutus": "__organisation__"},
    {"kaupa": "30", "olek": "koik"},
]

_TOKENS = [
    (
        re.compile(r'name="csrfmiddlewaretoken" value="[^"]+"'),
        'name="csrfmiddlewaretoken" value=""',
    ),
    (re.compile(r'name="salvestus" value="[^"]+"'), 'name="salvestus" value=""'),
]


def _normalised(response) -> str:
    assert response.status_code == 200
    text = response.content.decode()
    for pattern, replacement in _TOKENS:
        text = pattern.sub(replacement, text)
    return text


def _register(client, params, page):
    return client.get(
        reverse("matters:matter_list"), {**params, "leht": page}, headers={"HX-Request": "true"}
    )


@pytest.mark.parametrize("who", ["specialist", "reader", "department_head"])
def test_every_register_page_is_what_slicing_the_annotated_rows_rendered(
    client, specialist, other_specialist, reader, department_head, organisation, monkeypatch, who
):
    """Old paginator against new, on the same world, for three readers and every case.

    The old one is Django's own `Paginator.page`: it sliced the annotated query,
    so the database evaluated every row's subqueries before the limit. Whatever it
    rendered — rows, order, cells, the count, the page links, what a restricted
    record may show — is the oracle the new one is held to, byte for byte.
    """
    _world(specialist, other_specialist, organisation)
    client.force_login(
        {"specialist": specialist, "reader": reader, "department_head": department_head}[who]
    )
    compared = 0
    for case in CASES:
        params = {
            k: (str(organisation.pk) if v == "__organisation__" else v) for k, v in case.items()
        }
        for page in (1, 2, 4):
            new = _normalised(_register(client, params, page))
            with monkeypatch.context() as old_paging:
                old_paging.setattr(matter_views._RegisterPaginator, "page", Paginator.page)
                old = _normalised(_register(client, params, page))
            assert new == old, (who, params, page)
            compared += 1
    assert compared == len(CASES) * 3


@pytest.mark.parametrize(
    "case", [{}, {"jarjestus": "viimane_uusim", "olek": "koik"}, {"andmed": "test"}]
)
def test_the_whole_register_page_is_unchanged_too(
    client, specialist, other_specialist, reader, organisation, monkeypatch, case
):
    """The full page, not only the results region: filters, chips, the Arvamused section."""
    _world(specialist, other_specialist, organisation)
    for user in (specialist, reader):
        client.force_login(user)
        new = _normalised(client.get(reverse("matters:matter_list"), case))
        with monkeypatch.context() as old_paging:
            old_paging.setattr(matter_views._RegisterPaginator, "page", Paginator.page)
            old = _normalised(client.get(reverse("matters:matter_list"), case))
        assert new == old, (user, case)


def test_the_arvamused_figure_is_still_every_sent_opinion_the_reader_may_see(
    client, specialist, other_specialist, reader, administrator, organisation
):
    """The Saadetud figure reuses the list's own count; it still answers the old question.

    The old figure was its own `COUNT` of `visible_to(reader)` at SENT. A restricted
    Matter carries one of the sent opinions; since docs/adr/0181 the READER counts
    it as the specialist does, the administrator without a grant counts nothing,
    and each must still equal that old count.
    """
    _world(specialist, other_specialist, organisation)
    figures = {}
    for user in (specialist, reader, administrator):
        client.force_login(user)
        response = client.get(reverse("matters:matter_list"))
        expected = Submission.objects.visible_to(user).filter(status=SubmissionStatus.SENT).count()
        assert response.context["opinion_sent_count"] == expected
        figures[user.pk] = expected
    assert figures[specialist.pk] == figures[reader.pk] > figures[administrator.pk] == 0


def test_the_rows_differ_between_readers_so_the_comparison_can_fail(
    client, specialist, other_specialist, reader, administrator, organisation
):
    """The world is not one every reader sees alike, or the test above proves little.

    Since docs/adr/0181 the READER sees the restricted Teema as the specialist
    does, so the readers differ by scope (``?ulatus=minu``) instead, and an
    administrator without a grant sees none of it.
    """
    matters = _world(specialist, other_specialist, organisation)
    restricted = next(m for m in matters if m.visibility == Visibility.RESTRICTED and m.is_open)

    def seen(user, params):
        client.force_login(user)
        return _normalised(_register(client, params, 1))

    assert restricted.title in seen(specialist, {"kaupa": "50"})
    assert restricted.title in seen(reader, {"kaupa": "50"})
    assert restricted.title not in seen(administrator, {"kaupa": "50"})
    mine = {"ulatus": "minu", "kaupa": "50"}
    assert seen(specialist, mine).count("eelnõu ") > seen(reader, mine).count("eelnõu ")


def test_the_row_query_reads_only_the_page_it_shows(
    signed_in, specialist, other_specialist, organisation
):
    """The annotated row query is restricted to the page's ids, never the population.

    The `activity_*` annotations are what `matter_list_queryset` adds for the
    *Viimane tegevus* cell; whichever query carries them is the one that
    evaluates the per-row subqueries, so it must name the twelve rows it draws.
    """
    _world(specialist, other_specialist, organisation)
    with CaptureQueriesContext(connection) as captured:
        response = signed_in.get(reverse("matters:matter_list"))
    assert response.status_code == 200

    annotated = [
        q["sql"] for q in captured.captured_queries if 'AS "activity_entry_at"' in q["sql"]
    ]
    assert annotated, "the register drew no annotated row query"
    for sql in annotated:
        # The page's own ids, as literals — not an `IN (SELECT …)` a filter wrote.
        assert PAGE_IDS in sql, sql[-400:]


@pytest.mark.parametrize("population", [0, 30])
def test_the_register_stays_within_its_query_budget(
    signed_in, specialist, other_specialist, organisation, population
):
    """≤ 30 queries for the whole Teemad page, flat in the number of Matters.

    On the varied world above (restricted and TEST Matters, sent opinions,
    restricted entries), plus another ``population`` ordinary ones.
    """
    _world(specialist, other_specialist, organisation)
    for index in range(population):
        matter = factories.MatterFactory(
            owner=specialist if index % 2 else other_specialist,
            visibility=Visibility.RESTRICTED if index % 7 == 0 else Visibility.NORMAL,
            source_organisations=[organisation],
        )
        factories.NextActionFactory(matter=matter, target_date=timezone.localdate())
        add_entry(matter=matter, body="<p>Kirje</p>", author=specialist)

    with CaptureQueriesContext(connection) as captured:
        response = signed_in.get(reverse("matters:matter_list"))

    assert response.status_code == 200
    assert len(captured.captured_queries) <= REGISTER_QUERY_BUDGET


# ---------------------------------------------------------------------------
# «Tehtud» on a wait (JPERF-001)
# ---------------------------------------------------------------------------


def _teema(client, matter, params=None):
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}), params or {})


def _feedback_form(text: str) -> str:
    start = text.index('id="kaasamine-tagasiside"')
    return text[start : text.index("</form>", start)]


def _waiting_round(matter, actor):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=actor,
    )


def test_tehtud_costs_at_most_one_query_more_than_the_plain_teema_page(
    signed_in, specialist, organisation
):
    """The page's institution reading is reused, so only the finished-rounds read is added."""
    for _ in range(4):
        factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    round_ = _waiting_round(matter, specialist)
    _teema(signed_in, matter)  # warm per-process caches the way a second view would

    with CaptureQueriesContext(connection) as plain:
        assert _teema(signed_in, matter).status_code == 200
    with CaptureQueriesContext(connection) as tehtud:
        response = _teema(signed_in, matter, {"ava": "tagasiside", "kaasamine": str(round_.pk)})

    assert response.status_code == 200
    assert re.search(
        rf'<option value="{round_.pk}"[^>]*selected', _feedback_form(response.content.decode())
    )
    assert len(tehtud.captured_queries) <= len(plain.captured_queries) + 1


def test_tehtud_draws_the_same_form_as_reading_the_catalogue_again(
    signed_in, specialist, organisation, monkeypatch
):
    """Old against new: the form opened on the round is the same form, chip for chip.

    The old path built `Lisa tagasiside` again with no `choices`, so the form
    read the catalogue, the ranking and the spellings for itself. Taking the
    hand-off away reproduces it exactly; the rendered page must not differ.
    """
    for name in ("Rahandusministeerium", "Õiguskantsleri Näidisbüroo", "Ärilaenude Liit"):
        factories.MatterFactory(
            owner=specialist, source_organisations=[factories.OrganisationFactory(name=name)]
        )
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    round_ = _waiting_round(matter, specialist)
    params = {"ava": "tagasiside", "kaasamine": str(round_.pk)}

    with CaptureQueriesContext(connection) as new_queries:
        new = _normalised(_teema(signed_in, matter, params))

    attach = matter_forms.attach_organisation_picker

    def without_hand_off(form, **kwargs):
        attach(form, **kwargs)
        del form.organisation_choices

    monkeypatch.setattr(matter_forms, "attach_organisation_picker", without_hand_off)
    with CaptureQueriesContext(connection) as old_queries:
        old = _normalised(_teema(signed_in, matter, params))

    assert new == old
    assert len(new_queries.captured_queries) < len(old_queries.captured_queries)
