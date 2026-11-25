"""Package A1 (2026-11-25): one-line «Viimased tegevused» and a folded «Varasemad».

UX-003 — Minu asjad's recent activities:

* the date and the act follow the Teema's title in the same link, on one line;
* twenty rows are on screen before «Näita veel», the cap stays fifty;
* the whole title stays reachable (``title`` and the link's text) when the line cuts it;
* the page's reads do not grow with the number of rows.

UX-004 — `/teavitused/` «Varasemad»:

* a remembered block (`notifications.history`), closed for somebody who has not chosen;
* «Varasemad (N)» counts every history item on all pages, exactly;
* turning a page shows it open for that view without changing what is stored;
* drawing the page, open or closed, marks nothing read;
* the count's cost does not grow with the history.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.template import Context, Template
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.accounts import disclosures
from app.accounts.models import DisclosurePreference
from app.matters import recent_activity
from app.matters.models import MatterAssignmentNotice
from app.matters.services import assign_matter
from app.notifications import acknowledgement, feed, inbox
from tests import factories

pytestmark = pytest.mark.django_db

MY_WORK = reverse("matters:my_work")
NOTIFICATIONS = reverse("notifications:list")
HISTORY_KEY = "notifications.history"


# ---------------------------------------------------------------------------
# UX-003
# ---------------------------------------------------------------------------


def _feed(body: str) -> str:
    feed_html = body[body.index('aria-label="Viimased tegevused"') :]
    return feed_html[: feed_html.index("</section>")]


def _entries(owner, count: int, *, title: str = "Teema") -> None:
    now = timezone.now()
    matter = factories.MatterFactory(owner=owner, received_date=None, title=title)
    for hours in range(count):
        factories.EntryFactory(
            matter=matter, author=owner, occurred_at=now - timedelta(hours=hours + 1)
        )


def test_twenty_rows_are_on_screen_before_naita_veel():
    assert recent_activity.FEED_VISIBLE == 20
    assert recent_activity.FEED_LIMIT == 50


def test_the_meta_follows_the_title_inside_one_link(client, specialist):
    _entries(specialist, 1, title="Raamatupidamise seaduse muutmise seadus (991 SE)")
    client.force_login(specialist)

    row = _feed(client.get(MY_WORK).content.decode())
    row = row[row.index('class="actline"') :]
    row = row[: row.index("</a>")]

    matter_at = row.index('class="actline__matter"')
    meta_at = row.index('class="actline__meta"')
    assert matter_at < meta_at
    # The meta is a sibling right after the title, not a second line below it.
    between = row[row.index("</span>", matter_at) : meta_at]
    assert re.fullmatch(r"</span>\s*<span ", between)
    assert row.index("—") < row.index("actline__when")


def test_a_long_title_stays_whole_in_the_link(client, specialist):
    long_title = "Väga pika pealkirjaga teema " * 8
    _entries(specialist, 1, title=long_title.strip())
    client.force_login(specialist)

    feed_html = _feed(client.get(MY_WORK).content.decode())

    assert f'title="{long_title.strip()}"' in feed_html
    assert f'<span class="actline__matter">{long_title.strip()}</span>' in feed_html


def test_twenty_rows_then_the_rest_behind_naita_veel(client, specialist):
    _entries(specialist, 30)
    client.force_login(specialist)

    feed_html = _feed(client.get(MY_WORK).content.decode())
    shown = feed_html[: feed_html.index('class="pw-more"')]

    assert shown.count('class="actline"') == 20
    assert "Näita veel 10" in feed_html
    assert feed_html.count('class="actline"') == 30


def _my_work_queries(client) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(MY_WORK)
    assert response.status_code == 200
    return len(queries.captured_queries)


def test_more_rows_do_not_cost_more_reads(client, specialist):
    client.force_login(specialist)
    _entries(specialist, 3, title="Esimene")
    few = _my_work_queries(client)

    _entries(specialist, 40, title="Teine")
    many = _my_work_queries(client)

    assert many == few


# ---------------------------------------------------------------------------
# UX-004
# ---------------------------------------------------------------------------


def _read_history(owner, colleague, count: int) -> None:
    """``count`` «Uus asi» notices for ``owner``, every one of them read."""
    start = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    for number in range(count):
        notice_matter = factories.MatterFactory(owner=None, title=f"Varasem teema {number:02}")
        assign_matter(matter=notice_matter, owner=owner, actor=colleague)
        MatterAssignmentNotice.objects.filter(matter=notice_matter).update(
            created_at=start + timedelta(minutes=number)
        )
    for notice in MatterAssignmentNotice.objects.filter(recipient=owner):
        acknowledgement.mark_read(owner, inbox.TYPE_ASSIGNMENT, notice)


def _history_block(body: str) -> str:
    start = body.index('id="varasemad"')
    return body[start : body.index("</section>", start)]


def _details_tag(block: str) -> str:
    start = block.index("<details")
    return block[start : block.index(">", start) + 1]


def test_the_key_is_registered_and_closed_by_default():
    assert disclosures.DISCLOSURES[HISTORY_KEY].default_open is False


def test_varasemad_is_closed_until_the_reader_opens_it(client, specialist, other_specialist):
    _read_history(specialist, other_specialist, 3)
    client.force_login(specialist)

    tag = _details_tag(_history_block(client.get(NOTIFICATIONS).content.decode()))

    assert f'data-disclosure="{HISTORY_KEY}"' in tag
    assert 'data-disclosure-saved="0"' in tag
    assert "data-disclosure-keep" in tag
    assert not re.search(r"\sopen(\s|>)", tag)
    assert "data-disclosure-shown" not in tag


def test_a_reader_who_opened_it_finds_it_open(client, specialist, other_specialist):
    _read_history(specialist, other_specialist, 3)
    disclosures.remember(specialist, HISTORY_KEY, opened=True)
    client.force_login(specialist)

    tag = _details_tag(_history_block(client.get(NOTIFICATIONS).content.decode()))

    assert 'data-disclosure-saved="1"' in tag
    assert re.search(r"\sopen(\s|>)", tag)
    assert "data-disclosure-shown" not in tag


def test_the_count_is_every_history_item_on_all_pages(client, specialist, other_specialist):
    _read_history(specialist, other_specialist, feed.PAGE_SIZE + 4)
    client.force_login(specialist)

    page = feed.history_page(specialist, with_total=True)
    walked = 0
    cursor = None
    while True:
        step = feed.history_page(specialist, cursor=cursor)
        walked += len(step.items)
        cursor = step.cursor
        if cursor is None:
            break
    block = _history_block(client.get(NOTIFICATIONS).content.decode())

    assert page.total == walked == feed.PAGE_SIZE + 4
    assert len(page.items) == feed.PAGE_SIZE
    assert f'<span class="notifhistory__count">({feed.PAGE_SIZE + 4})</span>' in block


def test_the_count_is_asked_for_only_by_the_page(specialist, other_specialist):
    _read_history(specialist, other_specialist, 2)

    assert feed.history_page(specialist).total is None
    assert feed.history_page(specialist, with_total=True).total == 2


def test_a_reader_with_no_business_role_counts_nothing(administrator):
    assert feed.history_page(administrator, with_total=True).total == 0


def test_the_next_page_shows_it_open_without_storing_that(client, specialist, other_specialist):
    _read_history(specialist, other_specialist, feed.PAGE_SIZE + 2)
    client.force_login(specialist)
    first = feed.history_page(specialist)

    body = client.get(f"{NOTIFICATIONS}?varasemad={first.next}").content.decode()
    tag = _details_tag(_history_block(body))

    assert re.search(r"\sopen(\s|>)", tag)
    assert 'data-disclosure-shown="1"' in tag
    assert 'data-disclosure-saved="0"' in tag
    assert "Tagasi uuemate juurde" in _history_block(body)
    assert not DisclosurePreference.objects.filter(key=HISTORY_KEY).exists()


def test_the_next_page_of_an_open_block_needs_no_mark(client, specialist, other_specialist):
    _read_history(specialist, other_specialist, feed.PAGE_SIZE + 2)
    disclosures.remember(specialist, HISTORY_KEY, opened=True)
    client.force_login(specialist)
    first = feed.history_page(specialist)

    tag = _details_tag(
        _history_block(client.get(f"{NOTIFICATIONS}?varasemad={first.next}").content.decode())
    )

    assert re.search(r"\sopen(\s|>)", tag)
    assert "data-disclosure-shown" not in tag


def test_drawing_the_page_marks_nothing_read(client, specialist, other_specialist):
    _read_history(specialist, other_specialist, 3)
    unread_matter = factories.MatterFactory(owner=None, title="Lugemata teema")
    assign_matter(matter=unread_matter, owner=specialist, actor=other_specialist)
    disclosures.remember(specialist, HISTORY_KEY, opened=True)
    client.force_login(specialist)
    before = list(
        MatterAssignmentNotice.objects.order_by("pk").values_list(
            "pk", "viewed_at", "superseded_at"
        )
    )
    unread_before = len(inbox.inbox_for(specialist))

    client.get(NOTIFICATIONS)
    disclosures.remember(specialist, HISTORY_KEY, opened=False)
    client.get(NOTIFICATIONS)

    after = list(
        MatterAssignmentNotice.objects.order_by("pk").values_list(
            "pk", "viewed_at", "superseded_at"
        )
    )
    assert after == before
    assert len(inbox.inbox_for(specialist)) == unread_before == 1


def _notifications_queries(client) -> int:
    with CaptureQueriesContext(connection) as queries:
        response = client.get(NOTIFICATIONS)
    assert response.status_code == 200
    return len(queries.captured_queries)


def test_the_count_does_not_grow_with_the_history(client, specialist, other_specialist):
    client.force_login(specialist)
    _read_history(specialist, other_specialist, 2)
    small = _notifications_queries(client)

    MatterAssignmentNotice.objects.all().delete()
    _read_history(specialist, other_specialist, feed.PAGE_SIZE * 2)
    large = _notifications_queries(client)

    assert large == small


def test_the_tag_draws_force_open_only_for_a_closed_block(specialist):
    request = RequestFactory().get("/")
    request.user = specialist
    template = Template(
        '{% load disclosures %}<details {% disclosure "notifications.history" force_open=f %}>'
    )

    closed = template.render(Context({"request": request, "f": False}))
    forced = template.render(Context({"request": request, "f": True}))
    disclosures.remember(specialist, HISTORY_KEY, opened=True)
    request = RequestFactory().get("/")
    request.user = specialist
    stored_open = template.render(Context({"request": request, "f": True}))

    assert " open" not in closed and "data-disclosure-shown" not in closed
    assert ' data-disclosure-shown="1" open' in forced and 'data-disclosure-saved="0"' in forced
    assert " open" in stored_open and "data-disclosure-shown" not in stored_open
