"""An `Ülevaade / uudis` may be published with no known date (docs/adr/0089 §8).

docs/adr/0081 §2 required a publication date on every published row, and
docs/adr/0085 §3 then filled the box with today the moment somebody typed an
address. Lawyer testing in September 2026 measured what the pair produced: a
plausible date, already in the box, accepted without being read — on the one
column that is the person's own statement.

So the rule changed. This file owns the change, and the properties it holds are
the ones that erode quietly:

* **an address alone is a publication**, through the service, through the
  panel and through the planned row's own publish form;
* **`NULL` means unknown**, and no layer substitutes anything for it — not the
  clock, not `created_at`, not `published_at`, not a sentinel date;
* **an existing date survives everything**, including an unrelated correction;
* **a date can be cleared on purpose and stays cleared**;
* **the lifecycle is untouched**: an undated publication is `Avaldatud`, not
  planned, not cancelled, and its address is as valid as any other's;
* **every read path tolerates `NULL`** — the chronology, the page, search and
  the archive — and none of them prints an internal timestamp in its place.

The record's other rules stay where they were: the lifecycle in
`tests/test_website_overviews.py`, the name and the address rule in
`tests/test_overview_news_publication.py`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import WebsiteOverviewStatus
from app.matters.models import MatterWebsiteOverview
from app.matters.services import (
    correct_website_overview_link,
    plan_website_overview,
    publish_website_overview,
)
from app.matters.timeline import WEBSITE_OVERVIEW_DATE_UNKNOWN, matter_timeline
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument

pytestmark = pytest.mark.django_db

KODA_URL = "https://koda.ee/uudised/pakendiseaduse-ulevaade"
NEWS_URL = "https://uudised.example/2026/03/kaubanduskoda-hoiatab"
PUBLISHED_ON = dt.date(2026, 3, 14)


def _undated(matter, actor, url: str = KODA_URL):
    return publish_website_overview(
        overview=plan_website_overview(matter=matter, actor=actor),
        url=url,
        published_on=None,
        actor=actor,
    )


def _dated(matter, actor, url: str = NEWS_URL, on: dt.date = PUBLISHED_ON):
    return publish_website_overview(
        overview=plan_website_overview(matter=matter, actor=actor),
        url=url,
        published_on=on,
        actor=actor,
    )


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _add(client, matter, **fields):
    return client.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        fields,
        headers={"HX-Request": "true"},
    )


# ===========================================================================
# A — an address is enough
# ===========================================================================


def test_a_link_only_publication_is_accepted_and_stays_unknown(normal_matter, specialist):
    """Scenario E. The whole change, in its smallest form."""
    overview = _undated(normal_matter, specialist)

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == KODA_URL
    assert overview.published_on is None


def test_the_panel_records_a_link_only_publication(signed_in, normal_matter):
    """Scenario E, through the control a lawyer actually uses."""
    response = _add(signed_in, normal_matter, url=KODA_URL, published_on="")

    assert response.status_code == 200
    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.published_on is None


def test_the_planned_rows_publish_form_records_one_too(signed_in, normal_matter, specialist):
    """The other route into `Avaldatud`, and it agrees with the first.

    `Lisa link ja avaldamiskuupäev` used to carry `initial=timezone.localdate`,
    so somebody publishing an old plan got today in the box before they had
    thought about it. The box is empty now and an empty submit publishes.
    """
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    response = signed_in.post(
        reverse(
            "matters:publish_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {"url": KODA_URL, "published_on": "", "revision": overview.revision_token},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.published_on is None


def test_a_date_with_no_address_is_still_refused(signed_in, normal_matter):
    """The half that did **not** change, asserted so the change stays narrow.

    A publication date is a fact about a page, so one filed with nothing to open
    is a claim about nothing.
    """
    response = _add(signed_in, normal_matter, url="", published_on="14.03.2026")

    assert response.status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()


# ===========================================================================
# B — nothing invents a date
# ===========================================================================


def test_no_layer_substitutes_a_date_for_an_unknown_one(normal_matter, specialist):
    """Scenario E. The specific values that would be wrong, named individually.

    Today, the Matter's creation day, the row's own `created_at`, the moment the
    publication was written down, and the three sentinel dates a schema reaches
    for when somebody wants a non-null column.
    """
    overview = _undated(normal_matter, specialist)

    overview.refresh_from_db()
    forbidden = {
        timezone.localdate(),
        timezone.localdate(normal_matter.created_at),
        timezone.localdate(overview.created_at),
        timezone.localdate(overview.published_at),
        dt.date(1900, 1, 1),
        dt.date(1970, 1, 1),
        dt.date(9999, 12, 31),
    }
    assert overview.published_on is None
    assert overview.published_on not in forbidden


def test_the_audit_payload_records_the_unknown_date_as_null(normal_matter, specialist):
    """An audit payload is the last place a guessed business date should appear.

    It is what a later reader reconstructs the record from, so a timestamp
    written in here would become the file's own statement about somebody else's
    website.
    """
    _undated(normal_matter, specialist)

    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED
    )
    assert event.payload["url"] == KODA_URL
    assert event.payload["published_on"] is None


def test_published_at_and_published_on_stay_two_different_facts(normal_matter, specialist):
    """One exists and the other does not, which is the point of keeping them apart.

    `published_at` records when somebody wrote the publication down. That is
    known, it is stored, and it is not the day the page went up.
    """
    overview = _undated(normal_matter, specialist)

    overview.refresh_from_db()
    assert overview.published_at is not None
    assert overview.published_by == specialist
    assert overview.published_on is None


# ===========================================================================
# C — existing dates are never touched
# ===========================================================================


def test_a_known_date_is_stored_exactly_as_given(normal_matter, specialist):
    """Scenario F. The change takes nothing away from the dated case."""
    overview = _dated(normal_matter, specialist)

    overview.refresh_from_db()
    assert overview.published_on == PUBLISHED_ON

    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED
    )
    assert event.payload["published_on"] == PUBLISHED_ON.isoformat()


def test_correcting_only_the_address_leaves_an_existing_date_alone(normal_matter, specialist):
    """Scenario H. An unrelated correction is not an opportunity to re-guess.

    The date is passed back unchanged and the event says only the address moved.
    """
    overview = _dated(normal_matter, specialist)

    correct_website_overview_link(
        overview=overview,
        url=KODA_URL,
        published_on=PUBLISHED_ON,
        actor=specialist,
    )

    overview.refresh_from_db()
    assert overview.url == KODA_URL
    assert overview.published_on == PUBLISHED_ON
    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    )
    assert event.payload["fields"] == ["url"]


def test_a_date_can_be_added_to_an_undated_publication_later(normal_matter, specialist):
    """Unknown is not permanent: somebody who finds the day records it."""
    overview = _undated(normal_matter, specialist)

    correct_website_overview_link(
        overview=overview, url=KODA_URL, published_on=PUBLISHED_ON, actor=specialist
    )

    overview.refresh_from_db()
    assert overview.published_on == PUBLISHED_ON
    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    )
    assert event.payload["published_on_from"] is None
    assert event.payload["published_on_to"] == PUBLISHED_ON.isoformat()


def test_a_date_can_be_cleared_and_does_not_spring_back(normal_matter, specialist):
    """Scenario G. The gesture the lawyer feedback asked for by name.

    Somebody realises the date on the file was a guess. They empty the box, the
    row stays a publication, and the next save does not put today back — because
    nothing anywhere supplies one.
    """
    overview = _dated(normal_matter, specialist, url=KODA_URL)

    correct_website_overview_link(
        overview=overview, url=KODA_URL, published_on=None, actor=specialist
    )

    overview.refresh_from_db()
    assert overview.published_on is None
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == KODA_URL

    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    )
    assert event.payload["fields"] == ["published_on"]
    assert event.payload["published_on_from"] == PUBLISHED_ON.isoformat()
    assert event.payload["published_on_to"] is None

    # And a second correction that changes nothing writes nothing — which is the
    # half that proves today did not quietly return.
    correct_website_overview_link(
        overview=overview, url=KODA_URL, published_on=None, actor=specialist
    )
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
        ).count()
        == 1
    )


def test_clearing_a_date_through_the_browser_clears_it(signed_in, normal_matter, specialist):
    """Scenario G, on the route somebody actually takes."""
    overview = _dated(normal_matter, specialist, url=KODA_URL)

    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {"url": KODA_URL, "published_on": "", "revision": overview.revision_token},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    overview.refresh_from_db()
    assert overview.published_on is None
    assert overview.status == WebsiteOverviewStatus.PUBLISHED


# ===========================================================================
# D — the lifecycle is not weakened
# ===========================================================================


def test_an_undated_publication_is_published_and_not_planned_or_cancelled(
    normal_matter, specialist
):
    """A blank date is not a downgrade, a cancellation or an invalid address.

    docs/adr/0089 §8 changed one implication and nothing else: `Avaldatud` still
    means a page exists at an address a reader can open.
    """
    overview = _undated(normal_matter, specialist)

    overview.refresh_from_db()
    assert overview.is_published
    assert not overview.is_planned
    assert not overview.is_cancelled
    assert overview.cancelled_at is None


def test_an_undated_publication_still_refuses_a_second_publication(normal_matter, specialist):
    """`PUBLISHED → PUBLISHED` is a correction, not a republication."""
    from app.core.errors import DomainError

    overview = _undated(normal_matter, specialist)

    with pytest.raises(DomainError):
        publish_website_overview(
            overview=overview, url=NEWS_URL, published_on=PUBLISHED_ON, actor=specialist
        )


def test_an_undated_publication_still_refuses_cancellation(normal_matter, specialist):
    """The page is up, and a record denying it would be the file disagreeing
    with the world — a rule a missing date does not touch."""
    from app.core.errors import DomainError
    from app.matters.services import cancel_website_overview

    overview = _undated(normal_matter, specialist)

    with pytest.raises(DomainError):
        cancel_website_overview(overview=overview, actor=specialist)


def test_a_plan_still_carries_neither_an_address_nor_a_date(normal_matter, specialist):
    """The other implication is untouched, and the database still says so."""
    overview = plan_website_overview(matter=normal_matter, actor=specialist)

    overview.refresh_from_db()
    assert overview.url == ""
    assert overview.published_on is None


# ===========================================================================
# E — every read path tolerates an unknown date
# ===========================================================================


def test_the_chronology_shows_the_row_and_says_the_date_is_unknown(
    signed_in, normal_matter, specialist
):
    """Scenario E's display half, and the §12 rule behind it.

    The row **appears** — dropping it would hide a page that exists — and it
    prints «Kuupäev teadmata» rather than the day somebody typed the address in.
    The three words are the ones a `Väline seisukoht` with no date already
    prints, because it is the same fact about the file.
    """
    _undated(normal_matter, specialist)

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    milestones = [item.milestone for item in items if item.is_milestone]
    overview_rows = [m for m in milestones if m.what == "Ülevaade / uudis"]

    assert len(overview_rows) == 1
    assert overview_rows[0].display_date == WEBSITE_OVERVIEW_DATE_UNKNOWN
    assert overview_rows[0].links[0].url == KODA_URL


def test_the_page_never_prints_an_internal_timestamp_as_the_publication_date(
    signed_in, normal_matter, specialist
):
    """§12. `created_at` places the row; it must never describe it.

    The row was created today, so today's date appearing anywhere near this
    milestone would be exactly the invented fact the rule forbids.
    """
    overview = _undated(normal_matter, specialist)

    body = _detail(signed_in, normal_matter)
    # This one milestone's own headline and date cell, not the whole chronology:
    # `Alustatud` legitimately carries today, and the launcher chip above it
    # carries the same four words.
    chronology = body[body.index('id="ajajoon"') :]
    headline = chronology.index('uxtl__mswhat">Ülevaade / uudis<')
    cell = chronology[
        headline : chronology.index("</span>", chronology.index("uxtl__msdate", headline))
    ]

    assert WEBSITE_OVERVIEW_DATE_UNKNOWN in cell
    stamped = timezone.localdate(overview.created_at)
    assert f"{stamped.day}.{stamped.month}.{stamped.year}" not in cell


def test_a_dated_and_an_undated_publication_read_side_by_side(signed_in, normal_matter, specialist):
    """A file may hold both, and each says what it knows."""
    _dated(normal_matter, specialist, url=NEWS_URL)
    _undated(normal_matter, specialist, url=KODA_URL)

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    dates = [
        item.milestone.display_date
        for item in items
        if item.is_milestone and item.milestone.what == "Ülevaade / uudis"
    ]

    assert WEBSITE_OVERVIEW_DATE_UNKNOWN in dates
    assert "14.3.2026" in dates


def test_an_undated_publication_sorts_behind_the_dated_ones_deterministically(
    normal_matter, specialist
):
    """§12. A technical ordering, and it states nothing about when the page went up.

    `NULLS LAST` on `published_on`, then `created_at`, then `id`. The list must
    not reshuffle between two reads, and it must not imply a chronology it does
    not have.
    """
    _dated(normal_matter, specialist, url=NEWS_URL)
    first_unknown = _undated(normal_matter, specialist, url=KODA_URL)
    second_unknown = _undated(normal_matter, specialist, url="https://muu.example/x")

    ordered = list(MatterWebsiteOverview.objects.filter(matter=normal_matter))
    again = list(MatterWebsiteOverview.objects.filter(matter=normal_matter))

    assert [row.pk for row in ordered] == [row.pk for row in again]
    assert ordered[0].published_on == PUBLISHED_ON
    assert {row.pk for row in ordered[1:]} == {first_unknown.pk, second_unknown.pk}


def test_the_page_prints_the_publication_date_exactly_once(signed_in, normal_matter, specialist):
    """The out-of-band cell belongs to the correction's answer, not to the page.

    `website_overview_link.html` is included by the chronology on every ordinary
    render *and* returned on its own by `Paranda link`. The date cell it carries
    is for the second case only — emitted on the first it would print the day
    twice inside one row, which is the page saying one fact two ways and is
    exactly the shape a swap-target refactor produces by accident.
    """
    _dated(normal_matter, specialist, url=KODA_URL)

    body = _detail(signed_in, normal_matter)

    assert body.count("14.3.2026") == 1
    assert "hx-swap-oob" not in body


def test_a_conflict_panel_names_an_unknown_date_rather_than_omitting_it(
    signed_in, normal_matter, specialist
):
    """The one place where omitting the date would be ambiguous rather than quiet.

    This panel exists to say what the *other* version holds on the two columns
    being corrected. A line printing an address and nothing else leaves a reader
    unable to tell «their version has no date» from «this panel does not show
    dates» — and this is the panel somebody consults precisely because the two
    versions disagree (docs/adr/0089 §10).
    """
    overview = _dated(normal_matter, specialist, url=KODA_URL)
    stale = overview.revision_token
    # Somebody else clears the date while this person's form sits open.
    correct_website_overview_link(
        overview=overview, url=KODA_URL, published_on=None, actor=specialist
    )

    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {"url": KODA_URL, "published_on": "01.04.2026", "revision": stale},
        headers={"HX-Request": "true"},
    )
    body = response.content.decode()

    assert response.status_code == 409
    assert "Praegu on kirjas:" in body
    assert WEBSITE_OVERVIEW_DATE_UNKNOWN in body
    # And this person's own typed value is still in the box, unchanged.
    assert 'value="1.4.2026"' in body or 'value="01.04.2026"' in body


def test_search_and_the_archive_survive_an_undated_publication(normal_matter, specialist):
    """§8's default: this record is in neither projection, and a rebuild proves it.

    The assertion worth having is that `rebuild_all()` **runs** over a Matter
    carrying an undated publication: a projection that had started reading
    `published_on` without a null guard would raise here rather than in
    production.
    """
    _undated(normal_matter, specialist)

    rebuild_all()

    assert not SearchDocument.objects.filter(
        body_text__icontains="pakendiseaduse-ulevaade"
    ).exists()
    assert SearchDocument.objects.filter(matter=normal_matter).exists()
