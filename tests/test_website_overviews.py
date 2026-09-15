"""`Kodulehe ülevaade` — a Matter owes koda.ee a summary, and later has one.

Three states, two columns, and one trust boundary. The rules this file is most
careful about are the ones a screenshot cannot show:

* a plan carries no address and no date, and publishing is what gives it both;
* the only address a published overview may hold is a `koda.ee` page over
  `https`, decided by a **parsed host** rather than by a substring — which is the
  difference between `https://koda.ee/x` and `https://koda.ee.example.com/x`;
* the publication date is the person's and is never stamped by the server;
* a closed Matter refuses every new record and still permits a correction to an
  address it already holds;
* closing a Matter cancels every plan it still owed, in the same transaction,
  each with its own audit event — and is never blocked by one;
* none of it is work: no `NextAction`, no work item, no deadline, no metric, no
  search row, no archive projection (docs/adr/0081).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import work_items
from app.matters.enums import WebsiteOverviewStatus
from app.matters.models import Entry, MatterWebsiteOverview
from app.matters.my_work import build_my_work
from app.matters.services import (
    WebsiteOverviewConflict,
    cancel_website_overview,
    close_matter,
    correct_website_overview_link,
    normalize_koda_website_url,
    plan_website_overview,
    publish_website_overview,
    website_overview_revision,
)
from app.matters.timeline import TIMELINE_EVENT_TYPES, matter_timeline
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

KODA_URL = "https://koda.ee/uudised/pakendiseaduse-ulevaade"
KODA_SUBDOMAIN_URL = "https://www.koda.ee/uudised/pakendiseaduse-ulevaade"
PUBLISHED_ON = dt.date(2026, 3, 14)


def _planned(matter, actor=None):
    return plan_website_overview(matter=matter, actor=actor or matter.owner)


def _published(matter, actor=None, url: str = KODA_URL, on: dt.date = PUBLISHED_ON):
    return publish_website_overview(
        overview=_planned(matter, actor),
        url=url,
        published_on=on,
        actor=actor or matter.owner,
    )


def _events(matter, event_type):
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type)


# ---------------------------------------------------------------------------
# The record, and the three states it can be in
# ---------------------------------------------------------------------------


def test_a_plan_is_recorded_with_no_address_and_no_date(normal_matter, specialist):
    """§1. The whole content of a planned row is *that the write-up is owed*.

    There is no page yet, so there is nothing to link to and nothing to date —
    and a record that invented either would be a record nobody could correct,
    because nobody would know it was wrong.
    """
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""
    assert overview.published_on is None
    assert overview.published_at is None
    assert overview.cancelled_at is None
    assert overview.created_by == specialist

    event = _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_PLANNED).get()
    assert event.actor == specialist
    assert event.object_id == overview.pk
    assert event.payload["status"] == WebsiteOverviewStatus.PLANNED


def test_planning_writes_no_entry(normal_matter, specialist):
    """One action must not become two records that can later disagree.

    The same rule `add_engagement` keeps: the structured fact is the record, and
    a narrative note saying the same thing is a second version of it.
    """
    plan_website_overview(matter=normal_matter, actor=specialist)

    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_publishing_keeps_the_date_the_person_supplied(normal_matter, specialist):
    """§2. An explicitly submitted date survives, whatever day it is today.

    This is the defect docs/adr/0078 §2 fixed for `Kaasamine`, written down
    before it can arrive here: an overview published in March and recorded in
    September is a March publication, and a server that stamped today would file
    a false fact with no box on the screen contradicting it.
    """
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    published = publish_website_overview(
        overview=overview, url=KODA_URL, published_on=PUBLISHED_ON, actor=specialist
    )

    assert published.status == WebsiteOverviewStatus.PUBLISHED
    assert published.url == KODA_URL
    assert published.published_on == PUBLISHED_ON
    assert published.published_on != timezone.localdate()
    # When it was *written down* is a different fact from when the page went up,
    # and both are kept.
    assert published.published_at is not None
    assert published.published_by == specialist

    event = _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED).get()
    assert event.payload["url"] == KODA_URL
    assert event.payload["published_on"] == PUBLISHED_ON.isoformat()


def test_a_subdomain_of_koda_ee_is_a_koda_page(normal_matter, specialist):
    """§3. `www.koda.ee` and every other subdomain are the Chamber's own site."""
    published = publish_website_overview(
        overview=_planned(normal_matter, specialist),
        url=KODA_SUBDOMAIN_URL,
        published_on=PUBLISHED_ON,
        actor=specialist,
    )

    assert published.url == KODA_SUBDOMAIN_URL


def test_a_matter_may_owe_and_hold_several_overviews(normal_matter, specialist):
    """A long proceeding is written up more than once, so nothing is unique on
    `matter` alone."""
    first = _published(normal_matter, specialist, url=KODA_URL)
    second = _published(normal_matter, specialist, url=f"{KODA_URL}-teine")
    third = plan_website_overview(matter=normal_matter, actor=specialist)

    assert MatterWebsiteOverview.objects.filter(matter=normal_matter).count() == 3
    assert {first.status, second.status} == {WebsiteOverviewStatus.PUBLISHED}
    assert third.status == WebsiteOverviewStatus.PLANNED


def test_a_plan_can_be_cancelled_and_the_row_stays(normal_matter, specialist):
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    cancelled = cancel_website_overview(overview=overview, actor=specialist)

    assert cancelled.status == WebsiteOverviewStatus.CANCELLED
    assert cancelled.cancelled_at is not None
    assert MatterWebsiteOverview.objects.filter(pk=overview.pk).exists()
    assert (
        _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_CANCELLED).get().payload["reason"]
        == "manual"
    )


def test_a_published_overview_cannot_be_cancelled(normal_matter, specialist):
    """§1. The page is on koda.ee. A record claiming it was never published
    would be the file disagreeing with the website."""
    overview = _published(normal_matter, specialist)

    with pytest.raises(DomainError):
        cancel_website_overview(overview=overview, actor=specialist)

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert not _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_CANCELLED).exists()


def test_a_cancelled_overview_is_terminal(normal_matter, specialist):
    overview = cancel_website_overview(
        overview=_planned(normal_matter, specialist), actor=specialist
    )

    with pytest.raises(DomainError):
        publish_website_overview(
            overview=overview, url=KODA_URL, published_on=PUBLISHED_ON, actor=specialist
        )
    with pytest.raises(DomainError):
        cancel_website_overview(overview=overview, actor=specialist)

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.CANCELLED
    assert overview.url == ""


def test_publishing_an_already_published_overview_is_refused(normal_matter, specialist):
    """Publishing is a transition and corrections have their own operation, so
    the audit trail can say which of the two happened."""
    overview = _published(normal_matter, specialist)

    with pytest.raises(DomainError):
        publish_website_overview(
            overview=overview,
            url=f"{KODA_URL}-uus",
            published_on=PUBLISHED_ON,
            actor=specialist,
        )

    overview.refresh_from_db()
    assert overview.url == KODA_URL


# ---------------------------------------------------------------------------
# §3 — the address, and everything that only looks like one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://koda.ee/uudised/x",
        "https://koda.ee.example.com/uudised/x",
        "https://notkoda.ee/uudised/x",
        "https://example.com/koda.ee/uudised",
        "https://koda.ee@example.com/uudised",
        "https://example.com/?u=https://koda.ee/x",
        "ftp://koda.ee/uudised/x",
        "javascript:alert('koda.ee')",
        "koda.ee/uudised/x",
        "https:///uudised/x",
        "see ei ole aadress",
    ],
)
def test_an_address_that_is_not_a_koda_page_is_refused(url):
    """§3. Parsed host, never a substring.

    Every entry here contains the string `koda.ee` or claims to be a URL, and not
    one of them is a page on the Chamber's website. `koda.ee.example.com` is
    somebody else's domain; `https://koda.ee@example.com/` puts it in the
    *userinfo*, which is what a browser ignores and a substring check believes.
    """
    with pytest.raises(DomainError):
        normalize_koda_website_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://koda.ee",
        "https://koda.ee/",
        "https://koda.ee/uudised/x",
        "https://www.koda.ee/uudised/x",
        "https://arvamused.koda.ee/2026/x?q=1#top",
        "  https://koda.ee/uudised/x  ",
    ],
)
def test_a_koda_page_over_https_is_accepted(url):
    assert normalize_koda_website_url(url) == url.strip()


def test_an_empty_address_is_not_a_refusal_on_its_own():
    """Whether emptiness is allowed is a question about the *state* a record is
    in, and it is answered by the operation that requires an address."""
    assert normalize_koda_website_url("") == ""
    assert normalize_koda_website_url(None) == ""


def test_an_over_long_address_is_refused_rather_than_truncated():
    """A link cut off at a thousand characters is a link that no longer
    resolves, and a stored pointer that is quietly wrong is worse than a refusal
    (red-team finding F-1)."""
    with pytest.raises(DomainError):
        normalize_koda_website_url("https://koda.ee/" + "a" * 1000)


def test_publishing_without_an_address_or_a_date_is_refused(normal_matter, specialist):
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    with pytest.raises(DomainError):
        publish_website_overview(
            overview=overview, url="", published_on=PUBLISHED_ON, actor=specialist
        )
    with pytest.raises(DomainError):
        publish_website_overview(
            overview=overview, url=KODA_URL, published_on=None, actor=specialist
        )

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""
    assert overview.published_on is None


# ---------------------------------------------------------------------------
# §6 — what the database refuses, whatever wrote it
# ---------------------------------------------------------------------------


def test_a_published_row_cannot_exist_without_an_address(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterWebsiteOverview.objects.create(
            matter=normal_matter,
            status=WebsiteOverviewStatus.PUBLISHED,
            published_on=PUBLISHED_ON,
            published_at=timezone.now(),
        )


def test_a_published_row_cannot_exist_without_a_date(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterWebsiteOverview.objects.create(
            matter=normal_matter,
            status=WebsiteOverviewStatus.PUBLISHED,
            url=KODA_URL,
            published_at=timezone.now(),
        )


def test_an_unpublished_row_cannot_carry_an_address_or_a_date(normal_matter):
    """A link on a record that says nothing was published is a link a reader
    would follow."""
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterWebsiteOverview.objects.create(
            matter=normal_matter, status=WebsiteOverviewStatus.PLANNED, url=KODA_URL
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterWebsiteOverview.objects.create(
            matter=normal_matter,
            status=WebsiteOverviewStatus.CANCELLED,
            cancelled_at=timezone.now(),
            published_on=PUBLISHED_ON,
        )


def test_one_published_address_is_filed_at_most_once_per_matter(normal_matter, specialist):
    _published(normal_matter, specialist, url=KODA_URL)
    second = plan_website_overview(matter=normal_matter, actor=specialist)

    with pytest.raises(IntegrityError), transaction.atomic():
        publish_website_overview(
            overview=second, url=KODA_URL, published_on=PUBLISHED_ON, actor=specialist
        )


def test_the_same_address_on_two_matters_is_two_legitimate_records(specialist):
    first = factories.MatterFactory(owner=specialist)
    second = factories.MatterFactory(owner=specialist)

    _published(first, specialist, url=KODA_URL)
    _published(second, specialist, url=KODA_URL)

    assert MatterWebsiteOverview.objects.filter(url=KODA_URL).count() == 2


# ---------------------------------------------------------------------------
# §5, §6 — corrections, concurrency and the closed Matter
# ---------------------------------------------------------------------------


def test_a_published_address_can_be_corrected_and_the_correction_is_audited(
    normal_matter, specialist
):
    overview = _published(normal_matter, specialist)
    published_at = overview.published_at
    corrected_on = dt.date(2026, 3, 16)

    correct_website_overview_link(
        overview=overview,
        url=f"{KODA_URL}-parandatud",
        published_on=corrected_on,
        actor=specialist,
    )

    overview.refresh_from_db()
    assert overview.url == f"{KODA_URL}-parandatud"
    assert overview.published_on == corrected_on
    # A typo found in March does not change who wrote the publication down.
    assert overview.published_at == published_at
    assert overview.published_by == specialist

    event = _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED).get()
    assert sorted(event.payload["fields"]) == ["published_on", "url"]
    assert event.payload["url_from"] == KODA_URL
    assert event.payload["url_to"] == f"{KODA_URL}-parandatud"
    assert event.payload["published_on_from"] == PUBLISHED_ON.isoformat()
    assert event.payload["published_on_to"] == corrected_on.isoformat()


def test_a_correction_that_changes_nothing_writes_nothing(normal_matter, specialist):
    overview = _published(normal_matter, specialist)

    correct_website_overview_link(
        overview=overview, url=KODA_URL, published_on=PUBLISHED_ON, actor=specialist
    )

    assert not _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED).exists()


def test_a_correction_is_refused_on_a_record_that_was_never_published(normal_matter, specialist):
    """This is what stops the correction route being a way to publish a plan on
    a closed Matter: the transition that *creates* a publication is the guarded
    one, and this one can only move an address that already exists."""
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    with pytest.raises(DomainError):
        correct_website_overview_link(
            overview=overview, url=KODA_URL, published_on=PUBLISHED_ON, actor=specialist
        )

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""


def test_a_stale_revision_is_refused_and_writes_nothing(normal_matter, specialist):
    overview = _published(normal_matter, specialist)
    stale = website_overview_revision(overview)

    correct_website_overview_link(
        overview=overview,
        url=f"{KODA_URL}-esimene",
        published_on=PUBLISHED_ON,
        actor=specialist,
    )
    overview.refresh_from_db()

    with pytest.raises(WebsiteOverviewConflict):
        correct_website_overview_link(
            overview=overview,
            url=f"{KODA_URL}-teine",
            published_on=PUBLISHED_ON,
            actor=specialist,
            expected_revision=stale,
        )

    overview.refresh_from_db()
    assert overview.url == f"{KODA_URL}-esimene"
    assert _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED).count() == 1


def test_a_current_revision_is_accepted(normal_matter, specialist):
    overview = _published(normal_matter, specialist)

    correct_website_overview_link(
        overview=overview,
        url=f"{KODA_URL}-parandatud",
        published_on=PUBLISHED_ON,
        actor=specialist,
        expected_revision=website_overview_revision(overview),
    )

    overview.refresh_from_db()
    assert overview.url == f"{KODA_URL}-parandatud"


def test_closing_a_matter_cancels_every_plan_it_still_owed(normal_matter, specialist):
    """§5. A closed file that still said «a kodulehe ülevaade is owed» would be
    an instruction nobody can act on — every route that could publish or cancel
    one refuses a closed Matter."""
    first = plan_website_overview(matter=normal_matter, actor=specialist)
    second = plan_website_overview(matter=normal_matter, actor=specialist)
    published = _published(normal_matter, specialist)

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    first.refresh_from_db()
    second.refresh_from_db()
    published.refresh_from_db()
    assert first.status == WebsiteOverviewStatus.CANCELLED
    assert second.status == WebsiteOverviewStatus.CANCELLED
    assert first.cancelled_at is not None
    # A published overview is a record of a page that exists, and closure does
    # not touch it.
    assert published.status == WebsiteOverviewStatus.PUBLISHED

    reasons = [
        event.payload["reason"]
        for event in _events(normal_matter, ChangeEventType.WEBSITE_OVERVIEW_CANCELLED)
    ]
    assert reasons == ["matter_closed", "matter_closed"]


def test_a_planned_overview_never_blocks_a_closure(normal_matter, specialist):
    plan_website_overview(matter=normal_matter, actor=specialist)

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    normal_matter.refresh_from_db()
    assert normal_matter.is_open is False


def test_the_closure_and_its_cancellations_are_one_transaction(normal_matter, specialist):
    """All of it or none of it. A closure that shut the file and left the plans
    standing would leave exactly the state this cancellation exists to prevent.
    """
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    with pytest.raises(DomainError), transaction.atomic():
        close_matter(matter=normal_matter, disposition="EI_OLE_OLEMAS", actor=specialist)

    normal_matter.refresh_from_db()
    overview.refresh_from_db()
    assert normal_matter.is_open is True
    assert overview.status == WebsiteOverviewStatus.PLANNED


def test_a_published_address_stays_correctable_on_a_closed_matter(normal_matter, specialist):
    """§5. Closure means no new business content. It has never meant that a fact
    recorded wrongly must stay wrong (docs/adr/0075 §12)."""
    overview = _published(normal_matter, specialist)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    correct_website_overview_link(
        overview=overview,
        url=f"{KODA_URL}-parandatud",
        published_on=dt.date(2026, 3, 16),
        actor=specialist,
        expected_revision=website_overview_revision(overview),
    )

    overview.refresh_from_db()
    normal_matter.refresh_from_db()
    assert overview.url == f"{KODA_URL}-parandatud"
    # And nothing reopened the file to allow it.
    assert normal_matter.is_open is False
    assert _events(normal_matter, ChangeEventType.MATTER_REOPENED).count() == 0


# ---------------------------------------------------------------------------
# The Teema page: the launcher, the strip and the chronology
# ---------------------------------------------------------------------------


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _post(client, name, matter, data=None, **kwargs):
    return client.post(
        reverse("matters:" + name, kwargs={"pk": matter.pk, **kwargs}),
        data or {},
        headers={"HX-Request": "true"},
    )


def test_the_panel_records_a_plan_with_one_button(signed_in, normal_matter):
    response = _post(signed_in, "add_website_overview", normal_matter)

    assert response.status_code == 200
    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""
    assert overview.published_on is None


def test_a_planned_overview_reads_in_its_own_strip(signed_in, normal_matter, specialist):
    plan_website_overview(matter=normal_matter, actor=specialist)

    body = _detail(signed_in, normal_matter)

    assert 'id="kodulehe-ulevaated"' in body
    assert "Ülevaade on plaanis, aga veel avaldamata." in body


def test_a_matter_with_nothing_planned_renders_no_strip(signed_in, normal_matter):
    assert 'id="kodulehe-ulevaated"' not in _detail(signed_in, normal_matter)


def test_publishing_from_the_strip_stores_the_supplied_date(signed_in, normal_matter, specialist):
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    response = _post(
        signed_in,
        "publish_website_overview",
        normal_matter,
        {"url": KODA_URL, "published_on": "14.03.2026"},
        overview_id=overview.pk,
    )

    assert response.status_code == 200
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == KODA_URL
    assert overview.published_on == PUBLISHED_ON


def test_a_refused_address_comes_back_with_what_was_typed(signed_in, normal_matter, specialist):
    """§2. What somebody entered survives the refusal, and the sentence says
    which condition was violated rather than «viga»."""
    overview = plan_website_overview(matter=normal_matter, actor=specialist)
    typed = "http://koda.ee.example.com/uudised/x"

    response = _post(
        signed_in,
        "publish_website_overview",
        normal_matter,
        {"url": typed, "published_on": "14.03.2026"},
        overview_id=overview.pk,
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert typed in body
    assert "14.03.2026" in body
    assert "https://" in body
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED


def test_a_missing_date_is_refused_with_its_own_sentence(signed_in, normal_matter, specialist):
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    response = _post(
        signed_in,
        "publish_website_overview",
        normal_matter,
        {"url": KODA_URL, "published_on": ""},
        overview_id=overview.pk,
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert "avaldamise kuupäeva" in body
    assert KODA_URL in body
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED


def test_the_chronology_shows_a_labelled_link_and_never_the_address(
    signed_in, normal_matter, specialist
):
    """§4. `Ava kodulehel`, in a new tab, said out loud for a screen reader —
    and the URL itself is in the `href` and nowhere a reader has to parse it."""
    _published(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)
    row = body[body.index('id="ajajoon"') :]

    assert "Ava kodulehel" in row
    assert 'target="_blank"' in row
    assert 'rel="noopener noreferrer"' in row
    assert "avaneb uues aknas" in row
    assert f'href="{KODA_URL}"' in row
    # The address is the link's destination, never its text.
    assert f">{KODA_URL}<" not in row


def test_the_chronology_records_published_and_cancelled_and_not_planned(normal_matter, specialist):
    """§4. The permanent chronology holds completed milestones. A plan is work
    the file still owes, and it reads in the strip where it can be acted on."""
    plan_website_overview(matter=normal_matter, actor=specialist)
    cancel_website_overview(overview=_planned(normal_matter, specialist), actor=specialist)
    _published(normal_matter, specialist)

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    overviews = [item for item in items if item.website_overview is not None]

    assert {item.website_overview.status for item in overviews} == {
        WebsiteOverviewStatus.PUBLISHED,
        WebsiteOverviewStatus.CANCELLED,
    }
    assert [item.milestone.what for item in overviews] == [
        "Kodulehe ülevaade",
        "Kodulehe ülevaade",
    ]


def test_none_of_the_four_events_is_a_chronology_event_type():
    """The chronology renders these two milestones from the canonical record,
    so reading the event as well would state one act twice (docs/adr/0074 §14).
    """
    for event_type in (
        ChangeEventType.WEBSITE_OVERVIEW_PLANNED,
        ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED,
        ChangeEventType.WEBSITE_OVERVIEW_CANCELLED,
        ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED,
    ):
        assert event_type not in TIMELINE_EVENT_TYPES


def test_a_correction_is_offered_and_saved_from_the_chronology_row(
    signed_in, normal_matter, specialist
):
    overview = _published(normal_matter, specialist)
    url = reverse(
        "matters:correct_website_overview",
        kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
    )

    opened = signed_in.get(url, headers={"HX-Request": "true"})
    assert opened.status_code == 200
    assert KODA_URL in opened.content.decode()

    saved = signed_in.post(
        url,
        {
            "url": f"{KODA_URL}-parandatud",
            "published_on": "16.03.2026",
            "revision": website_overview_revision(overview),
        },
        headers={"HX-Request": "true"},
    )

    assert saved.status_code == 200
    overview.refresh_from_db()
    assert overview.url == f"{KODA_URL}-parandatud"
    assert overview.published_on == dt.date(2026, 3, 16)


def test_a_stale_correction_from_the_page_answers_409_and_writes_nothing(
    signed_in, normal_matter, specialist
):
    overview = _published(normal_matter, specialist)
    stale = website_overview_revision(overview)
    correct_website_overview_link(
        overview=overview,
        url=f"{KODA_URL}-esimene",
        published_on=PUBLISHED_ON,
        actor=specialist,
    )

    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {"url": f"{KODA_URL}-teine", "published_on": "14.03.2026", "revision": stale},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 409
    overview.refresh_from_db()
    assert overview.url == f"{KODA_URL}-esimene"


# ---------------------------------------------------------------------------
# §5 — a closed Matter, including the POST that arrives without a page
# ---------------------------------------------------------------------------


@pytest.fixture
def closed_matter(normal_matter, specialist):
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    normal_matter.refresh_from_db()
    return normal_matter


def test_a_closed_matter_offers_no_launcher_and_no_planned_controls(signed_in, closed_matter):
    body = _detail(signed_in, closed_matter)

    assert 'id="lisa-koduleht"' not in body
    assert 'id="kodulehe-ulevaated"' not in body


def test_a_closed_matter_refuses_a_crafted_plan(signed_in, closed_matter):
    """The page is not the boundary. A browser holding the Teema from before the
    closure still has every button, and its POST reaches a server with no memory
    of which page it came from (R2-02)."""
    response = _post(signed_in, "add_website_overview", closed_matter)

    assert response.status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=closed_matter).exists()


def test_a_closed_matter_refuses_a_crafted_publication(signed_in, normal_matter, specialist):
    overview = plan_website_overview(matter=normal_matter, actor=specialist)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    overview.refresh_from_db()
    # Closure cancelled it, so re-plan the harder case: a row the closure never
    # saw, written straight to the table, is still refused by the route.
    overview = MatterWebsiteOverview.objects.create(
        matter=normal_matter, status=WebsiteOverviewStatus.PLANNED
    )

    response = _post(
        signed_in,
        "publish_website_overview",
        normal_matter,
        {"url": KODA_URL, "published_on": "14.03.2026"},
        overview_id=overview.pk,
    )

    assert response.status_code == 400
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""


def test_a_closed_matter_refuses_a_crafted_cancellation(signed_in, normal_matter, specialist):
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    overview = MatterWebsiteOverview.objects.create(
        matter=normal_matter, status=WebsiteOverviewStatus.PLANNED
    )

    response = _post(
        signed_in,
        "cancel_website_overview",
        normal_matter,
        {},
        overview_id=overview.pk,
    )

    assert response.status_code == 400
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED


def test_a_reader_may_not_write_any_of_it(client, reader, normal_matter, specialist):
    """`business_write_required` answers 404, so a refusal describes no surface
    (docs/adr/0037)."""
    overview = plan_website_overview(matter=normal_matter, actor=specialist)
    client.force_login(reader)

    assert _post(client, "add_website_overview", normal_matter).status_code == 404
    assert (
        _post(
            client,
            "publish_website_overview",
            normal_matter,
            {"url": KODA_URL, "published_on": "14.03.2026"},
            overview_id=overview.pk,
        ).status_code
        == 404
    )
    assert (
        _post(
            client, "cancel_website_overview", normal_matter, {}, overview_id=overview.pk
        ).status_code
        == 404
    )
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED


# ---------------------------------------------------------------------------
# §4 — what it is deliberately not
# ---------------------------------------------------------------------------


def test_a_planned_overview_is_not_work(normal_matter, specialist):
    """No `NextAction`, no work item, no deadline, no `Minu asjad` row. A column
    that generated a task would make every Matter with a plan on it read as
    late."""
    plan_website_overview(matter=normal_matter, actor=specialist)

    assert work_items.work_items(specialist) == []
    assert not normal_matter.next_actions.exists()
    my_work = build_my_work(specialist)
    assert my_work.has_work is False
    assert my_work.bands == []
    assert my_work.undated == []
    assert my_work.overdue == 0


def test_a_published_overview_reaches_no_search_or_archive_projection(normal_matter, specialist):
    """§4. Not a search result, not a projection row, not a document. The
    address is a pointer on one Matter's file and nothing indexes it."""
    before = rebuild_all().documents

    overview = _published(normal_matter, specialist)
    after = rebuild_all()

    assert after.documents == before
    assert not SearchDocument.objects.filter(source_object_id=overview.pk).exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM search_searchdocument WHERE body_text ILIKE %s",
            ["%pakendiseaduse-ulevaade%"],
        )
        assert cursor.fetchone()[0] == 0


def test_an_overview_is_no_document_no_submission_and_no_work_victory(normal_matter, specialist):
    _published(normal_matter, specialist)

    assert not normal_matter.documents.exists()
    assert not normal_matter.submissions.exists()
    assert not normal_matter.work_victories.exists()
    assert not normal_matter.important_dates.exists()
    assert not normal_matter.effective_dates.exists()
    assert not normal_matter.engagements.exists()


# ---------------------------------------------------------------------------
# §6 — the migration, and the proof that it carries no data
# ---------------------------------------------------------------------------


def test_the_table_arrives_with_no_data_migration():
    """One `CreateModel`, and nothing that reads or writes an existing row.

    This record did not exist before, so every Matter in the register has zero
    of them — which is what an empty table means, with no `RunPython`, no
    `RunSQL` and no backfill. Deriving a planned overview from a note, a tag, an
    engagement of kind `Kaasamiskutse veebis` or a koda.ee address somebody once
    pasted somewhere would put an intention on the file that nobody stated.
    """
    from django.db.migrations.executor import MigrationExecutor

    migration = MigrationExecutor(connection).loader.get_migration(
        "matters", "0022_matter_website_overview"
    )

    assert [type(operation).__name__ for operation in migration.operations] == ["CreateModel"]
    assert migration.operations[0].name == "MatterWebsiteOverview"


def test_the_audit_vocabulary_migration_moves_no_row():
    """`choices` is validation and display in Django, never a database object,
    so adding four of them alters the column's Python metadata and touches no
    audit row."""
    from django.db.migrations.executor import MigrationExecutor

    migration = MigrationExecutor(connection).loader.get_migration(
        "audit", "0018_website_overview_events"
    )

    assert [type(operation).__name__ for operation in migration.operations] == ["AlterField"]
    assert migration.operations[0].name == "event_type"


def test_neither_migration_in_this_release_runs_python_or_sql():
    """The generic form of the two assertions above, so a later edit to either
    file cannot quietly add a data step under a name that still reads as
    additive."""
    from django.db.migrations.executor import MigrationExecutor

    loader = MigrationExecutor(connection).loader
    for app_label, name in (
        ("matters", "0022_matter_website_overview"),
        ("audit", "0018_website_overview_events"),
    ):
        operations = loader.get_migration(app_label, name).operations
        assert not any(
            type(operation).__name__ in {"RunPython", "RunSQL"} for operation in operations
        ), f"{app_label}/{name} carries a data migration"
