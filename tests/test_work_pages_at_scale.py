"""Osakond, Minu asjad and Statistika at scale, without changing what they say (JPERF-005).

The 2026-10-17 audit measured the three pages growing linearly with open Matters:
2.0–3.3 s for one user and 1.7–2.2 MB of HTML at 10,000 open Matters. The changes
this file holds:

* **«Muutusteta 30 p»** leaves out, by an index probe, the files that plainly
  moved since the cutoff before annotating the rest with the per-Matter
  correlated maxima (`activity.known_active_since`);
* the **capped lists** that annotate (Osakond's quiet interventions, Minu asjad's
  quiet rail) draw their slice on the primary key first (`activity.annotated_slice`);
* **Minu asjad** stopped annotating every open file on the desk twice (the
  rail's *Viimati muudetud*, since replaced by *Viimased tegevused*, which
  annotates nothing — app/matters/recent_activity.py), and its month figures
  compare timestamps with a plain range instead of a `AT TIME ZONE` cast
  (`start_of_local_day`), as do Osakond's *Tehtud* and `sent_submissions` (`local_day_range`);
* **Statistika** reads the work model only up to the end of the one window its
  two work figures can reach;
* **render caps**: Minu asjad's portfolio and each Osakond *Eesolev* window
  draw at most a fixed number of rows, keep counting all of them, and open the
  rest in the register.

Each is held against the code it replaced, in the same test, on one world with
restricted and TEST Matters, a restricted entry that makes a file quiet for one
reader and not for another, and readers who see different things: the old
behaviour is patched back in and the page must render byte for byte the same.
The render caps are the one deliberate difference, and they are tested on their
own: below the cap nothing changes, above it the rows are the head of the same
order and every count is still the whole population.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

import pytest
from django.db import connection
from django.db.models import Q
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.dates import start_of_local_day
from app.core.enums import Visibility
from app.matters import activity, my_work
from app.matters import department_dashboard as dd
from app.matters import overview as ov
from app.matters import work_items as wi
from app.matters.enums import MatterDataClass
from app.matters.models import Entry
from app.matters.services import add_entry
from app.reporting import overview_strip
from app.workflow.enums import DatePrecision, Disposition
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

#: PERFORMANCE-AUDIT.md §10, query counts (deterministic, so CI holds them).
#: Osakond +1 since docs/adr/0188: the rail asks once for commencement checks on
#: Teemad with no Vastutaja, for the holder of the management view.
OSAKOND_BUDGET = 46
#: Minu asjad +1 since the rail's Kalender (docs/adr/0158, amendment of
#: 2026-10-26): its commencements, read once; its work is the page's own list.
#: +1 since docs/adr/0120's amendment of 2026-10-27: planned and estimated
#: commencements left the work model and the calendar reads them once itself.
#: +10 since «Viimased tegevused» (owner's UX package, 2026-10-28): one capped
#: read per activity source (entries, sends, steps, Kaasamine, Märge, Väline
#: seisukoht, Ülevaade, the Matter's own closure/arrival, OneNote pages) and
#: one for the audit rows that group them — fixed, whatever the desk holds
#: (`tests/test_my_work_recent_activity.py`). «Minu viimased sissekanded»'s
#: one read went with it.
MINU_ASJAD_BUDGET = 42
#: Statistika +1 since the UX package of 2026-10-28: «Mida need arvud
#: tähendavad» is a remembered block, and the reader's saved block states are
#: read once per request (app/accounts/disclosures.py).
STATISTIKA_BUDGET = 34


def _days_ago(days: int) -> datetime.datetime:
    return timezone.now() - datetime.timedelta(days=days)


def _world(specialist, other_specialist, organisation, *, count: int = 36) -> list[Any]:
    """Open work that differs in every way the three pages read.

    Restricted and TEST Matters; three owners (one of them nobody); files whose
    last activity is old (an entry, or only the received date), recent (an
    entry, a step a person set, a sent opinion, a recent arrival) or old *except*
    for a restricted entry yesterday — quiet for a READER and not for a
    specialist. Steps due in the past, this week, next month and far ahead, one
    recorded to a month; response deadlines on half of them.
    """
    today = timezone.localdate()
    matters = []
    for index in range(count):
        matter = factories.MatterFactory(
            owner=(specialist, other_specialist, None)[index % 3],
            title=("Ärimaksu", "Õigusabi", "Zooloogia", "keskkonnatasu")[index % 4]
            + f" eelnõu {index:02d}",
            visibility=Visibility.RESTRICTED if index % 7 == 0 else Visibility.NORMAL,
            data_class=MatterDataClass.TEST if index % 5 == 1 else MatterDataClass.REAL,
            received_date=today - datetime.timedelta(days=3 if index % 11 == 0 else 90),
            response_deadline=(
                today + datetime.timedelta(days=(index * 3) % 50 - 12) if index % 2 == 0 else None
            ),
            source_organisations=[organisation],
        )
        shape = index % 4
        if shape == 0:
            add_entry(
                matter=matter, body="<p>Vana</p>", author=specialist, occurred_at=_days_ago(60)
            )
        elif shape == 1:
            add_entry(matter=matter, body="<p>Uus</p>", author=specialist, occurred_at=_days_ago(2))
        elif shape == 2:
            add_entry(
                matter=matter, body="<p>Vana</p>", author=specialist, occurred_at=_days_ago(45)
            )
            add_entry(
                matter=matter,
                body="<p>Piiratud</p>",
                author=specialist,
                occurred_at=_days_ago(1),
                visibility_override=Visibility.RESTRICTED,
            )
        if index % 3 == 1:
            offset = (-20, -3, 2, 25, 140)[index % 5]
            action = factories.NextActionFactory(
                matter=matter,
                target_date=today + datetime.timedelta(days=offset),
                date_precision=DatePrecision.MONTH if index % 9 == 7 else DatePrecision.EXACT,
                responsible=matter.owner,
                created_by=specialist,
            )
            if index % 2:
                # A step a person set long ago does not make the file recent.
                NextAction.objects.filter(pk=action.pk).update(created_at=_days_ago(40))
        if index % 10 == 7:
            factories.send_opinion_through_services(matter, specialist)
        matters.append(matter)
    closed = factories.MatterFactory(
        owner=specialist,
        title="Suletud eelnõu",
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=_days_ago(1),
    )
    matters.append(closed)
    return matters


_TOKENS = [
    (
        re.compile(r'name="csrfmiddlewaretoken" value="[^"]+"'),
        'name="csrfmiddlewaretoken" value=""',
    ),
    (re.compile(r'name="salvestus" value="[^"]+"'), 'name="salvestus" value=""'),
]


def _html(response) -> str:
    assert response.status_code == 200
    text = response.content.decode()
    for pattern, replacement in _TOKENS:
        text = pattern.sub(replacement, text)
    return text


def _old_behaviour(monkeypatch) -> None:
    """Put back, for the length of a `monkeypatch.context()`, what each change replaced."""

    def old_slice(queryset, user, *order, limit):
        return list(activity.annotate_last_activity(queryset, user).order_by(*order)[:limit])

    def old_day_range(field, first, last=None):
        condition = Q(**{f"{field}__date__gte": first})
        if last is not None:
            condition &= Q(**{f"{field}__date__lte": last})
        return condition

    reading = wi.work_items

    monkeypatch.setattr(activity, "known_active_since", lambda user, since: Q(pk__in=[]))
    monkeypatch.setattr(ov, "annotated_slice", old_slice)
    monkeypatch.setattr(my_work, "annotated_slice", old_slice)
    monkeypatch.setattr(dd, "local_day_range", old_day_range)
    monkeypatch.setattr(
        overview_strip.work_items,
        "work_items",
        lambda user, *, today=None, responsible=None, latest=None: reading(
            user, today=today, responsible=responsible
        ),
    )
    monkeypatch.setattr(my_work, "PORTFOLIO_LIMIT", 10**9)
    monkeypatch.setattr(dd, "UPCOMING_LIMIT", 10**9)


PAGES = [
    ("department_head", "matters:department", None),
    ("department_head", "matters:department", {"vaade": "valdkonniti"}),
    ("specialist", "matters:department", None),
    ("reader", "matters:department", None),
    ("specialist", "matters:my_work", None),
    ("other_specialist", "matters:my_work", {"vaade": "jargmiseta"}),
    ("department_head", "matters:person_work", "specialist"),
    ("specialist", "reporting:overview", None),
    ("department_head", "reporting:overview", {"periood": "koik"}),
]


@pytest.mark.parametrize(("who", "route", "extra"), PAGES)
def test_each_page_renders_what_the_code_it_replaced_rendered(
    client,
    specialist,
    other_specialist,
    department_head,
    reader,
    organisation,
    monkeypatch,
    who,
    route,
    extra,
):
    """Old against new, byte for byte, on the same world in the same test."""
    people = {
        "specialist": specialist,
        "other_specialist": other_specialist,
        "department_head": department_head,
        "reader": reader,
    }
    _world(specialist, other_specialist, organisation)
    client.force_login(people[who])
    if route == "matters:person_work":
        url, params = reverse(route, kwargs={"pk": people[extra].pk}), {}
    else:
        url, params = reverse(route), extra or {}

    new = _html(client.get(url, params))
    with monkeypatch.context() as old:
        _old_behaviour(old)
        before = _html(client.get(url, params))

    assert new == before


def test_a_restricted_entry_keeps_a_file_active_for_every_reader_alike(
    specialist, other_specialist, reader, administrator, organisation
):
    """A restricted entry yesterday makes a file active for every authorized reader.

    It used to make the file active for the specialist and quiet for a READER,
    which is what let the comparison below fail. Since docs/adr/0181 a READER
    sees yesterday's entry too, so the two populations are one; an
    administrator without a business role has no quiet files at all.
    """
    matters = _world(specialist, other_specialist, organisation)
    # Old work and a restricted entry yesterday (shape 2), on a NORMAL Matter.
    restricted_activity = [
        m
        for index, m in enumerate(matters[:-1])
        if index % 4 == 2 and m.visibility == Visibility.NORMAL
    ]

    for_specialist = set(wi.quiet_matters(specialist))
    for_reader = set(wi.quiet_matters(reader))

    assert for_reader == for_specialist
    # The restricted entry is what keeps these active, for both alike.
    kept_active = [m for m in restricted_activity if m.pk not in for_specialist]
    assert kept_active
    assert set(wi.quiet_matters(administrator)) == set()


def _old_quiet(user, today):
    """`work_items.quiet_matters` as it stood: every open file annotated, as Matters."""
    cutoff = today - datetime.timedelta(days=wi.QUIET_DAYS)
    quiet = []
    for matter in activity.annotate_last_activity(wi.open_matters(user), user):
        fact = activity.activity_of(matter)
        if fact is not None and fact.occurred_on < cutoff:
            quiet.append(matter.pk)
    return quiet


@pytest.mark.parametrize("who", ["specialist", "reader", "department_head"])
def test_the_quiet_population_is_the_one_every_file_annotated_would_give(
    specialist, other_specialist, reader, department_head, organisation, who
):
    """The probe only skips work, and values read as values: the same ids as before."""
    _world(specialist, other_specialist, organisation)
    user = {"specialist": specialist, "reader": reader, "department_head": department_head}[who]
    today = timezone.localdate()

    new = wi.quiet_matters(user, today)
    before = _old_quiet(user, today)

    assert sorted(map(str, new)) == sorted(map(str, before))
    assert new, "the world must hold quiet files, or the comparison proves nothing"


def _old_work_items(user, today, responsible=None, latest=None):
    """`work_items.work_items` as it stood: every row joining its people and stage."""
    actions = wi.dated_actions(user, responsible=responsible)
    deadlines = wi.important_deadlines(user, owner=responsible)
    responses = wi.outstanding_response_deadlines(user, owner=responsible)
    waits = wi.dated_feedback_waits(user, owner=responsible)
    if latest is not None:
        actions = actions.filter(target_date__lte=latest)
        deadlines = deadlines.filter(date_value__lte=latest)
        responses = responses.filter(response_deadline__lte=latest)
        waits = waits.filter(feedback_deadline__lte=latest)
    action_rows = list(actions)
    subjects = wi.follow_up_subjects(action_rows, user)
    items = [
        wi.action_item(action, today, subject=subjects.get(action.follow_up_id))
        for action in action_rows
    ]
    items += [wi._deadline_item(record, today) for record in deadlines]
    items += [wi._response_deadline_item(matter, today) for matter in responses]
    items += [wi.feedback_wait_item(engagement, today) for engagement in waits]
    return wi.sort_items(items)


def _described(items):
    """Every field of every item, and what its Matter, owner, stage and person say."""
    return [
        (
            item,
            item.matter.pk,
            item.matter.title,
            getattr(item.matter.owner, "pk", None),
            getattr(item.matter.owner, "display_name", None),
            getattr(item.matter.stage, "pk", None),
            getattr(item.matter.stage, "label_et", None),
            getattr(item.responsible, "pk", None),
            getattr(item.responsible, "display_name", None),
        )
        for item in items
    ]


@pytest.mark.parametrize("who", ["specialist", "reader", "department_head"])
def test_the_work_model_is_the_one_the_joins_built(
    specialist, other_specialist, reader, department_head, organisation, stage, who
):
    """People and stages read once give every row what its own join gave it."""
    matters = _world(specialist, other_specialist, organisation)
    for matter in matters[:12]:
        matter.stage = stage
        matter.save(update_fields=["stage"])
    user = {"specialist": specialist, "reader": reader, "department_head": department_head}[who]
    today = timezone.localdate()

    assert _described(wi.work_items(user, today=today)) == _described(_old_work_items(user, today))
    mine = wi.work_items(user, today=today, responsible=specialist)
    assert _described(mine) == _described(_old_work_items(user, today, responsible=specialist))
    bounded = wi.work_items(user, today=today, latest=today + datetime.timedelta(days=30))
    assert _described(bounded) == _described(
        _old_work_items(user, today, latest=today + datetime.timedelta(days=30))
    )
    assert any(item.matter.stage is not None for item in mine + bounded)


def test_the_quiet_probe_spares_the_annotation_for_files_that_moved(
    specialist, other_specialist, organisation
):
    """The annotated read covers only the files the probe could not settle."""
    matters = _world(specialist, other_specialist, organisation)
    with CaptureQueriesContext(connection) as captured:
        wi.quiet_matters(specialist)

    (annotated,) = [
        q["sql"] for q in captured.captured_queries if 'AS "activity_entry_at"' in q["sql"]
    ]
    assert "NOT (" in annotated and "EXISTS" in annotated
    assert len(matters) > 0


# ---------------------------------------------------------------------------
# Local-day ranges
# ---------------------------------------------------------------------------


def test_a_local_day_range_is_the_same_rows_as_the_date_cast(specialist, normal_matter):
    """Around Tallinn midnight and across the October DST change, both spellings agree."""
    tallinn_midnights = [
        start_of_local_day(datetime.date(2026, 10, 1)),
        start_of_local_day(datetime.date(2026, 10, 25)),  # DST ends that night
        start_of_local_day(datetime.date(2026, 10, 26)),
        start_of_local_day(datetime.date(2026, 3, 29)),  # DST starts that night
    ]
    for midnight in tallinn_midnights:
        for delta in (-1, 0, 1):
            add_entry(
                matter=normal_matter,
                body="<p>Piiril</p>",
                author=specialist,
                occurred_at=midnight + datetime.timedelta(seconds=delta),
            )
    entries = Entry.objects.filter(matter=normal_matter)
    days = [datetime.date(2026, 3, 28), datetime.date(2026, 3, 29), datetime.date(2026, 10, 1)]
    days += [datetime.date(2026, 10, 25), datetime.date(2026, 10, 26), datetime.date(2026, 9, 30)]
    for first in days:
        assert set(entries.filter(occurred_at__gte=start_of_local_day(first))) == set(
            entries.filter(occurred_at__date__gte=first)
        ), first
        for last in days:
            ranged = set(entries.filter(dd.local_day_range("occurred_at", first, last)))
            cast = set(entries.filter(occurred_at__date__gte=first, occurred_at__date__lte=last))
            assert ranged == cast, (first, last)


def test_the_month_figures_no_longer_cast_every_entry_to_a_local_date(specialist, organisation):
    """The person's entries this month are counted by a plain range on `occurred_at`."""
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    add_entry(matter=matter, body="<p>Kirje</p>", author=specialist)
    with CaptureQueriesContext(connection) as captured:
        my_work.person_stats(specialist, specialist, timezone.localdate())

    counted = [q["sql"] for q in captured.captured_queries if 'FROM "matters_entry"' in q["sql"]]
    assert counted
    assert not any("AT TIME ZONE" in sql for sql in counted), counted


# ---------------------------------------------------------------------------
# Render caps (the deliberate, visible change)
# ---------------------------------------------------------------------------


def test_the_portfolio_renders_the_head_of_its_order_and_counts_the_whole_desk(
    client, specialist, other_specialist, organisation, monkeypatch
):
    _world(specialist, other_specialist, organisation)
    whole = my_work.build_my_work(specialist).portfolio
    monkeypatch.setattr(my_work, "PORTFOLIO_LIMIT", 4)
    capped = my_work.build_my_work(specialist).portfolio

    assert whole.beyond_cap == 0
    assert [row.matter.pk for row in capped.rows] == [row.matter.pk for row in whole.rows[:4]]
    assert capped.count == whole.count == len(whole.rows) > 4
    assert capped.total == whole.total
    assert capped.beyond_cap == whole.count - 4
    assert [chip.count for chip in capped.chips] == [chip.count for chip in whole.chips]

    client.force_login(specialist)
    page = _html(client.get(reverse("matters:my_work")))
    assert f"Siin on 4 esimest {whole.count}-st." in page
    assert f'href="{whole.register_url.replace("&", "&amp;")}"' in page


def test_an_eesolev_window_renders_its_head_and_still_counts_every_deadline(
    client, specialist, other_specialist, department_head, organisation, monkeypatch
):
    _world(specialist, other_specialist, organisation)
    groups = {group.key: group for group in dd.upcoming_groups(department_head)}
    window = max(groups.values(), key=lambda group: group.count)
    assert window.count > 2
    assert window.rendered == window.items and window.beyond_cap == 0

    monkeypatch.setattr(dd, "UPCOMING_LIMIT", 2)
    capped = {group.key: group for group in dd.upcoming_groups(department_head)}[window.key]

    assert capped.rendered == window.items[:2]
    assert capped.count == window.count
    assert capped.beyond_cap == window.count - 2

    client.force_login(department_head)
    page = _html(client.get(reverse("matters:department")))
    assert f"Siin on 2 esimest {window.count}-st." in page
    assert capped.url.replace("&", "&amp;") in page


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "route", "budget"),
    [
        ("department_head", "matters:department", OSAKOND_BUDGET),
        ("specialist", "matters:my_work", MINU_ASJAD_BUDGET),
        ("specialist", "reporting:overview", STATISTIKA_BUDGET),
    ],
)
@pytest.mark.parametrize("population", [36, 72])
def test_each_page_stays_within_its_query_budget(
    client,
    specialist,
    other_specialist,
    department_head,
    organisation,
    who,
    route,
    budget,
    population,
):
    """PERFORMANCE-AUDIT.md §10, flat in the number of open Matters."""
    _world(specialist, other_specialist, organisation, count=population)
    client.force_login({"specialist": specialist, "department_head": department_head}[who])
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse(route))
    assert response.status_code == 200
    assert len(captured.captured_queries) <= budget
