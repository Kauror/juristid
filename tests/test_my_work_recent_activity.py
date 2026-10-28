"""«Viimased tegevused» on Minu asjad (owner's UX package, 2026-10-28).

One list in the main column, right under Aktiivsed teemad, that replaced the
rail's «Viimati muudetud» and «Minu viimased sissekanded»
(app/matters/recent_activity.py). What these tests hold it to:

* the two populations — work on the subject's Matters by anybody, and the
  subject's own entries on other people's Matters — and nothing read twice;
* one act, one row, through the audit rows' shared `operation_id`; distinct acts
  stay distinct, and a step's supersession is not a second act;
* the actor is the record's own person, named only when it is not the desk's
  owner, and never invented for an imported or source fact;
* authorization: a Matter the reader may not open, and a record restricted
  below a Matter they can, produce nothing;
* newest first, the Teema first in the markup, «Näita veel» holding exactly the
  rest, and a read whose query count does not grow with the desk.
"""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.audit.operations import composer_operation
from app.core.enums import Visibility
from app.matters import recent_activity
from app.matters.enums import MatterOrigin
from app.matters.recent_activity import (
    ACTION_DONE_LABEL,
    ACTION_REPLACED_LABEL,
    ACTION_SET_LABEL,
    ENTRY_LABEL,
    build_recent_activity,
    when_label,
)
from app.matters.services import add_entry
from app.workflow.enums import DatePrecision
from app.workflow.services import complete_next_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

MY_WORK = reverse("matters:my_work")


def _matter(owner, **extra):
    extra.setdefault("received_date", None)
    return factories.MatterFactory(owner=owner, **extra)


def _rows(reader, subject):
    return build_recent_activity(reader, subject).rows


def _labels(rows):
    return [(row.matter.title, row.label) for row in rows]


# ---------------------------------------------------------------------------
# Who is in it
# ---------------------------------------------------------------------------


def test_a_colleagues_step_on_my_matter_is_listed_with_their_name(specialist, other_specialist):
    mine = _matter(specialist, title="Minu teema")
    set_next_action(matter=mine, text="Helista", actor=other_specialist)

    rows = _rows(specialist, specialist)

    assert _labels(rows) == [("Minu teema", ACTION_SET_LABEL)]
    assert rows[0].actor == other_specialist


def test_my_own_entry_on_a_colleagues_matter_is_listed_without_my_name(
    specialist, other_specialist
):
    theirs = _matter(other_specialist, title="Kolleegi teema")
    add_entry(matter=theirs, body="<p>Kirjutasin</p>", author=specialist)

    rows = _rows(specialist, specialist)

    assert _labels(rows) == [("Kolleegi teema", ENTRY_LABEL)]
    assert rows[0].actor is None


def test_a_colleagues_entry_on_their_own_matter_is_not_on_my_desk(specialist, other_specialist):
    theirs = _matter(other_specialist, title="Kolleegi teema")
    add_entry(matter=theirs, body="<p>Nende oma</p>", author=other_specialist)

    assert _rows(specialist, specialist) == []


def test_my_entry_on_my_own_matter_is_listed_once(specialist):
    mine = _matter(specialist, title="Minu teema")
    add_entry(matter=mine, body="<p>Üks kord</p>", author=specialist)

    assert _labels(_rows(specialist, specialist)) == [("Minu teema", ENTRY_LABEL)]


def test_several_distinct_activities_on_one_matter_are_several_rows(specialist, other_specialist):
    """Not the latest per Matter, which is what «Viimati muudetud» showed."""
    mine = _matter(specialist, title="Minu teema")
    factories.EntryFactory(
        matter=mine, author=specialist, occurred_at=timezone.now() - timedelta(hours=3)
    )
    set_next_action(matter=mine, text="Helista", actor=other_specialist)

    assert _labels(_rows(specialist, specialist)) == [
        ("Minu teema", ACTION_SET_LABEL),
        ("Minu teema", ENTRY_LABEL),
    ]


def test_the_list_is_newest_first_across_matters(specialist):
    now = timezone.now()
    for title, hours in (("Keskmine", 5), ("Uusim", 1), ("Vanim", 30)):
        factories.EntryFactory(
            matter=_matter(specialist, title=title),
            author=specialist,
            occurred_at=now - timedelta(hours=hours),
        )

    assert [row.matter.title for row in _rows(specialist, specialist)] == [
        "Uusim",
        "Keskmine",
        "Vanim",
    ]


# ---------------------------------------------------------------------------
# One act, one row
# ---------------------------------------------------------------------------


def test_one_composer_save_is_one_row(specialist):
    """An entry and the step it set, in one operation: one row naming both."""
    mine = _matter(specialist, title="Minu teema")
    with composer_operation():
        add_entry(matter=mine, body="<p>Tehtud</p>", author=specialist)
        set_next_action(matter=mine, text="Järgmine", actor=specialist)

    rows = _rows(specialist, specialist)

    assert len(rows) == 1
    assert rows[0].label == "Sissekanne lisatud, järgmine tegevus määratud"


def test_the_same_acts_in_two_operations_are_two_rows(specialist):
    mine = _matter(specialist, title="Minu teema")
    with composer_operation():
        add_entry(matter=mine, body="<p>Üks</p>", author=specialist)
    with composer_operation():
        set_next_action(matter=mine, text="Teine", actor=specialist)

    assert len(_rows(specialist, specialist)) == 2


def test_a_replaced_step_is_one_change_not_an_end_and_a_start(specialist, other_specialist):
    mine = _matter(specialist, title="Minu teema")
    set_next_action(matter=mine, text="Esimene", actor=specialist)
    set_next_action(matter=mine, text="Teine", actor=other_specialist)

    rows = _rows(specialist, specialist)

    assert [row.label for row in rows] == [ACTION_REPLACED_LABEL, ACTION_SET_LABEL]
    assert rows[0].actor == other_specialist


def test_a_completed_step_is_listed_as_done(specialist, other_specialist):
    mine = _matter(specialist, title="Minu teema")
    action = set_next_action(matter=mine, text="Helista", actor=specialist)
    complete_next_action(action=action, actor=other_specialist)

    rows = _rows(specialist, specialist)

    assert rows[0].label == ACTION_DONE_LABEL
    assert rows[0].actor == other_specialist


# ---------------------------------------------------------------------------
# Nobody is invented
# ---------------------------------------------------------------------------


def test_a_step_an_importer_set_is_not_an_activity(specialist):
    mine = _matter(specialist)
    factories.NextActionFactory(matter=mine, created_by=None)

    assert _rows(specialist, specialist) == []


def test_an_arrival_date_is_listed_with_no_person(specialist):
    _matter(
        specialist,
        title="Saabunud",
        origin=MatterOrigin.LEGACY_IMPORT,
        received_date=timezone.localdate() - timedelta(days=2),
    )

    rows = _rows(specialist, specialist)

    assert _labels(rows) == [("Saabunud", "Saabus")]
    assert rows[0].actor is None


def test_a_matter_with_nothing_recorded_has_no_row_whatever_its_updated_at(specialist):
    """`Matter.updated_at` is a statement about the row, never about a person."""
    _matter(specialist, origin=MatterOrigin.NATIVE)

    assert _rows(specialist, specialist) == []


def test_an_imported_closure_names_nobody(specialist):
    _matter(
        specialist,
        title="Suletud",
        is_open=False,
        disposition="COMPLETED",
        closed_at=timezone.now() - timedelta(days=1),
        closed_by=None,
    )

    rows = _rows(specialist, specialist)

    assert _labels(rows) == [("Suletud", "Teema suleti")]
    assert rows[0].actor is None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.fixture
def outsider(db, monkeypatch):
    """A reader who sees NORMAL records and only the RESTRICTED ones they take part in.

    No request builds that scope since docs/adr/0181 — every business reader
    reads everything, everybody else nothing — but the builders still implement
    the narrowing, and it is the only reader for whom a restricted record and
    a visible one differ, so it is hand-built here the way
    `tests/test_child_projection_visibility.py` does.
    """
    from app.core import authorization
    from app.core.authorization import Scope

    real = authorization.scope_for_user
    user = factories.UserFactory()

    def scope(person):
        if getattr(person, "pk", None) == user.pk:
            return Scope(
                user=person,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(person)

    monkeypatch.setattr("app.matters.models.scope_for_user", scope)
    monkeypatch.setattr("app.matters.activity.scope_for_user", scope)
    monkeypatch.setattr("app.workflow.models.scope_for_user", scope, raising=False)
    return user


def test_nothing_at_all_for_a_reader_with_no_business_scope(specialist, administrator):
    mine = _matter(specialist)
    add_entry(matter=mine, body="<p>x</p>", author=specialist)

    assert _rows(administrator, specialist) == []


def test_a_restricted_matter_is_absent_for_a_reader_outside_it(specialist, outsider):
    visible = _matter(specialist, title="Tavaline")
    add_entry(matter=visible, body="<p>Näha</p>", author=specialist)
    secret = _matter(specialist, title="Salajane", visibility=Visibility.RESTRICTED)
    add_entry(matter=secret, body="<p>Peidus</p>", author=specialist)

    assert {row.matter.title for row in _rows(specialist, specialist)} == {"Tavaline", "Salajane"}
    assert [row.matter.title for row in _rows(outsider, specialist)] == ["Tavaline"]


def test_my_entry_on_a_restricted_matter_of_somebody_else_is_absent_for_an_outsider(
    specialist, other_specialist, outsider
):
    """Authorship is not authorization."""
    secret = _matter(other_specialist, title="Salajane", visibility=Visibility.RESTRICTED)
    secret.collaborators.add(specialist)
    add_entry(matter=secret, body="<p>Minu oma</p>", author=specialist)

    assert [row.matter.title for row in _rows(specialist, specialist)] == ["Salajane"]
    assert _rows(outsider, specialist) == []


def test_a_restricted_entry_on_a_visible_matter_is_absent(specialist, outsider):
    mine = _matter(specialist, title="Tavaline")
    add_entry(
        matter=mine,
        body="<p>Piiratud</p>",
        author=specialist,
        visibility_override=Visibility.RESTRICTED,
    )

    assert _rows(specialist, specialist) != []
    assert _rows(outsider, specialist) == []


def test_a_restricted_step_on_a_visible_matter_names_nobody_to_an_outsider(
    specialist, other_specialist, outsider
):
    mine = _matter(specialist, title="Tavaline")
    action = set_next_action(matter=mine, text="Salajane samm", actor=other_specialist)
    action.visibility_override = Visibility.RESTRICTED
    action.save(update_fields=["visibility_override"])

    assert [row.actor for row in _rows(specialist, specialist)] == [other_specialist]
    assert _rows(outsider, specialist) == []


# ---------------------------------------------------------------------------
# How it reads
# ---------------------------------------------------------------------------


def test_the_date_reads_as_a_day_today_as_today_and_a_period_as_a_period():
    today = dt.date(2026, 10, 28)
    moment = timezone.make_aware(dt.datetime(2026, 10, 28, 14, 5))

    assert when_label(today, today=today, moment=moment, precision="EXACT") == "täna 14:05"
    assert when_label(today, today=today, moment=None, precision="EXACT") == "täna"
    assert when_label(dt.date(2026, 10, 19), today=today, moment=None, precision="EXACT") == "19.10"
    assert when_label(dt.date(2025, 3, 4), today=today, moment=None, precision="EXACT") == (
        "4.3.2025"
    )
    assert (
        when_label(dt.date(2026, 10, 1), today=today, moment=None, precision=DatePrecision.MONTH)
        == "oktoober 2026"
    )


def test_the_row_puts_the_teema_first_and_names_the_colleague(client, specialist, other_specialist):
    mine = _matter(specialist, title="Teema pealkiri")
    set_next_action(matter=mine, text="Helista", actor=other_specialist)
    client.force_login(specialist)

    body = client.get(MY_WORK).content.decode()
    row = body[body.index('class="actline"') :]
    row = row[: row.index("</a>")]

    assert row.index("Teema pealkiri") < row.index("actline__when") < row.index("actline__actor")
    assert ACTION_SET_LABEL in row
    assert reverse("matters:matter_detail", kwargs={"pk": mine.pk}) in body


def test_my_own_row_does_not_repeat_my_name(client, specialist):
    mine = _matter(specialist, title="Teema pealkiri")
    add_entry(matter=mine, body="<p>Minu</p>", author=specialist)
    client.force_login(specialist)

    body = client.get(MY_WORK).content.decode()

    assert 'class="actline"' in body
    assert "actline__actor" not in body


def test_the_feed_sits_right_after_aktiivsed_teemad_in_the_main_column(client, specialist):
    client.force_login(specialist)
    body = client.get(MY_WORK).content.decode()
    main = body[body.index('class="worklayout2__main"') : body.index('class="worklayout2__rail"')]

    assert main.index('aria-labelledby="aktiivsed-teemad"') < main.index(
        'aria-label="Viimased tegevused"'
    )
    assert "Viimati muudetud" not in body
    assert "Minu viimased sissekanded" not in body
    assert "Sinu teemadel ei ole veel tegevusi." in body


def test_naita_veel_holds_exactly_the_rest(client, specialist):
    now = timezone.now()
    mine = _matter(specialist, title="Teema")
    for hours in range(recent_activity.FEED_VISIBLE + 3):
        factories.EntryFactory(
            matter=mine, author=specialist, occurred_at=now - timedelta(hours=hours)
        )

    activity = build_recent_activity(specialist, specialist)
    client.force_login(specialist)
    body = client.get(MY_WORK).content.decode()
    feed = body[body.index('aria-label="Viimased tegevused"') :]
    feed = feed[: feed.index("</section>")]
    more = feed[feed.index('class="pw-more"') :]

    assert len(activity.preview) == recent_activity.FEED_VISIBLE
    assert activity.remaining == 3
    assert "Näita veel 3" in feed
    assert feed.count('class="actline"') == recent_activity.FEED_VISIBLE + 3
    assert more.count('class="actline"') == 3
    assert "viimast tegevust" not in feed


def test_past_the_cap_the_page_says_it_shows_the_latest(client, specialist, monkeypatch):
    monkeypatch.setattr(recent_activity, "FEED_LIMIT", 4)
    monkeypatch.setattr(recent_activity, "FEED_VISIBLE", 2)
    now = timezone.now()
    mine = _matter(specialist)
    for hours in range(6):
        factories.EntryFactory(
            matter=mine, author=specialist, occurred_at=now - timedelta(hours=hours)
        )
    client.force_login(specialist)

    body = client.get(MY_WORK).content.decode()

    assert body.count('class="actline"') == 4
    assert "Siin on 4 viimast tegevust." in body


def test_the_read_is_bounded_whatever_the_size_of_the_desk(specialist, other_specialist):
    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            build_recent_activity(specialist, specialist)
        return len(captured)

    def grow(count: int) -> None:
        for _ in range(count):
            mine = _matter(specialist)
            add_entry(matter=mine, body="<p>x</p>", author=other_specialist)
            set_next_action(matter=mine, text="Samm", actor=other_specialist)
            theirs = _matter(other_specialist)
            add_entry(matter=theirs, body="<p>y</p>", author=specialist)

    grow(2)
    small = queries()
    grow(8)

    assert queries() == small
    assert small <= 16


# ---------------------------------------------------------------------------
# The rail around it (requirement 3)
# ---------------------------------------------------------------------------


def test_minu_statistika_is_a_remembered_block(client, specialist):
    client.force_login(specialist)
    body = client.get(MY_WORK).content.decode()
    at = body.index('class="foldout__label">Minu statistika<')
    tag = body[body.rindex("<details", 0, at) : body.index(">", body.rindex("<details", 0, at))]

    assert 'data-disclosure="my_work.statistics"' in tag
    assert " open" in tag


def test_a_closed_statistics_block_stays_closed(client, specialist):
    from app.accounts.disclosures import remember

    remember(specialist, "my_work.statistics", opened=False)
    client.force_login(specialist)
    body = client.get(MY_WORK).content.decode()
    at = body.index('class="foldout__label">Minu statistika<')
    tag = body[body.rindex("<details", 0, at) : body.index(">", body.rindex("<details", 0, at))]

    assert " open" not in tag


def test_a_colleagues_desk_carries_no_scratchpad_anywhere(client, specialist, department_head):
    from app.matters.person_work import save_scratchpad

    save_scratchpad(specialist, "Isiklik meelespea 42")
    client.force_login(department_head)

    body = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk})).content.decode()

    assert "Isiklik meelespea 42" not in body
    assert reverse("matters:save_scratchpad") not in body
    assert 'aria-label="Märkmed"' not in body
    assert 'aria-label="Kiirvaade"' in body
