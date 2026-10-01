"""A record carries its own words: `Pealkiri` on `Ülevaade / uudis`, and
`Veebileht` and `Märkus` on `Kaasamine` (docs/adr/0127).

JUR-CASE-07: two write-ups of one file read as two identical `Ülevaade / uudis`
lines in a closed `Teema käik` — a closed row shows only its headline and day.
JUR-CASE-08/09: a round's public koda.ee page had to be pasted into
`Keda kaasati`, and a caveat about an attached list had nowhere to go.

Asserted here:

* **the overview title** — optional, stored as typed on create, `Avalda` and
  `Muuda`; the headline reads `Ülevaade / uudis: <pealkiri>` and an untitled row
  reads exactly as before; the link still says `Ülevaade / uudis` and still
  follows the stored address; a correction that does not carry the box leaves
  the name alone; a restricted write-up's name reaches nobody who may not see it;
* **the round's page and note** — the existing `url` and `note` columns, asked on
  `+ Kaasamine` and `Muuda` as `Veebileht` and `Märkus`, normalised by the same
  rule as the provider links, printed in the open row only; the work lists go on
  reading the audience; search reads what it always read.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters import work_items
from app.matters.enums import EngagementKind
from app.matters.forms import CompactWebsiteOverviewForm, WebsiteOverviewLinkForm
from app.matters.models import (
    WEBSITE_OVERVIEW_TITLE_MAX_LENGTH,
    MatterEngagement,
    MatterWebsiteOverview,
)
from app.matters.services import (
    WEBSITE_OVERVIEW_TITLE_TOO_LONG,
    add_engagement,
    correct_website_overview_link,
    engagement_revision_token,
    plan_website_overview,
    publish_website_overview,
)
from app.matters.timeline import engagement_milestone, matter_timeline
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
VTK_PAGE = "https://www.koda.ee/uudised/juristieksam-vtk"
BILL_PAGE = "https://www.koda.ee/uudised/juristieksam-eelnou"
KODA_PAGE = "https://www.koda.ee/hetkel-kasil/juristieksam"
CAVEAT = (
    "Fail sisaldab kaasamise sihtrühma tööloendit; see ei tõenda, et kõigile loendis "
    "olevatele ettevõtetele kiri saadeti."
)


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _published(matter, *, url: str, title: str = "", on: dt.date | None = None):
    overview = plan_website_overview(matter=matter, actor=matter.owner)
    return publish_website_overview(
        overview=overview,
        url=url,
        published_on=on or dt.date(2026, 9, 9),
        actor=matter.owner,
        title=title,
    )


def _overview_rows(matter, user):
    items, _more = matter_timeline(matter=matter, user=user)
    return [item for item in items if item.website_overview is not None]


# ---------------------------------------------------------------------------
# JUR-CASE-07 — `Pealkiri` on `Ülevaade / uudis`
# ---------------------------------------------------------------------------


def test_an_untitled_overview_reads_exactly_as_before(normal_matter, specialist):
    overview = _published(normal_matter, url=VTK_PAGE)

    (row,) = _overview_rows(normal_matter, specialist)
    assert overview.title == ""
    assert row.milestone.what == "Ülevaade / uudis"
    assert overview.headline == "Ülevaade / uudis"
    assert overview.link_label == "Ülevaade / uudis"


def test_create_with_a_title_stores_it_and_heads_the_row(signed_in, normal_matter, specialist):
    response = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": normal_matter.pk}),
        {
            "published_on": "09.09.2026",
            "overview_title": "  Koja seisukoht juristieksami VTK kohta  ",
            "url": "www.koda.ee/uudised/juristieksam-vtk",
        },
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.title == "Koja seisukoht juristieksami VTK kohta"
    # The address rule is the one it always was: a bare host gains its scheme.
    assert overview.url == VTK_PAGE
    assert overview.published_on == dt.date(2026, 9, 9)
    (row,) = _overview_rows(normal_matter, specialist)
    assert row.milestone.what == "Ülevaade / uudis: Koja seisukoht juristieksami VTK kohta"
    published = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED
    )
    assert published.payload["title"] == "Koja seisukoht juristieksami VTK kohta"
    assert published.summary == "Koja seisukoht juristieksami VTK kohta"


def test_two_overviews_are_told_apart_without_opening_them(signed_in, normal_matter, specialist):
    _published(normal_matter, url=VTK_PAGE, title="Koja seisukoht VTK kohta")
    _published(
        normal_matter, url=BILL_PAGE, title="Koja seisukoht eelnõu kohta", on=dt.date(2026, 9, 27)
    )

    body = signed_in.get(_teema(normal_matter)).content.decode()

    # Each headline is on the row's one line — the closed accordion's text and
    # the name its toggle reads (`aria-labelledby kaik-<pk>-rida`).
    for overview in MatterWebsiteOverview.objects.filter(matter=normal_matter):
        assert (
            f'id="kodulehe-ulevaade-{overview.pk}-pealkiri" class="uxtl__mswhat">'
            f"{overview.headline}<"
        ) in body
    assert "Ülevaade / uudis: Koja seisukoht VTK kohta" in body
    assert "Ülevaade / uudis: Koja seisukoht eelnõu kohta" in body
    # The link is still the link, to the stored address.
    assert f'href="{VTK_PAGE}"' in body and f'href="{BILL_PAGE}"' in body


def test_muuda_renames_the_page_and_swaps_the_headline(signed_in, normal_matter, specialist):
    overview = _published(normal_matter, url=VTK_PAGE, title="Vana pealkiri")
    url = reverse(
        "matters:correct_website_overview",
        kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
    )

    opened = signed_in.get(url, **HX).content.decode()
    assert 'value="Vana pealkiri"' in opened

    response = signed_in.post(
        url,
        {
            "title": "Koja seisukoht juristieksami VTK kohta",
            "url": VTK_PAGE,
            "published_on": "09.09.2026",
            "revision": overview.revision_token,
        },
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    overview.refresh_from_db()
    assert overview.title == "Koja seisukoht juristieksami VTK kohta"
    body = response.content.decode()
    assert 'hx-swap-oob="true">Ülevaade / uudis: Koja seisukoht juristieksami VTK kohta<' in body
    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    )
    assert event.payload["fields"] == ["title"]
    assert (event.payload["title_from"], event.payload["title_to"]) == (
        "Vana pealkiri",
        "Koja seisukoht juristieksami VTK kohta",
    )


def test_an_emptied_box_clears_the_title(signed_in, normal_matter):
    overview = _published(normal_matter, url=VTK_PAGE, title="Pealkiri")
    signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {
            "title": "",
            "url": VTK_PAGE,
            "published_on": "09.09.2026",
            "revision": overview.revision_token,
        },
        **HX,
    )

    overview.refresh_from_db()
    assert overview.title == ""
    assert overview.headline == "Ülevaade / uudis"


def test_a_correction_that_does_not_carry_the_box_keeps_the_title(signed_in, normal_matter):
    """A row opened before the box existed asked nothing about the name."""
    overview = _published(normal_matter, url=VTK_PAGE, title="Pealkiri")
    signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {"url": BILL_PAGE, "published_on": "09.09.2026", "revision": overview.revision_token},
        **HX,
    )

    overview.refresh_from_db()
    assert (overview.title, overview.url) == ("Pealkiri", BILL_PAGE)


def test_a_service_caller_that_names_no_title_leaves_it_alone(normal_matter, specialist):
    overview = _published(normal_matter, url=VTK_PAGE, title="Pealkiri")

    correct_website_overview_link(
        overview=overview, url=BILL_PAGE, published_on=None, actor=specialist
    )

    overview.refresh_from_db()
    assert overview.title == "Pealkiri"


def test_publishing_a_plan_can_name_the_page(signed_in, normal_matter):
    overview = plan_website_overview(matter=normal_matter, actor=normal_matter.owner)

    response = signed_in.post(
        reverse(
            "matters:publish_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {
            "title": "Uudis",
            "url": VTK_PAGE,
            "published_on": "",
            "revision": overview.revision_token,
        },
        **HX,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    overview.refresh_from_db()
    assert (overview.title, overview.url, overview.published_on) == ("Uudis", VTK_PAGE, None)


def test_an_overlong_title_is_refused_beside_the_box_and_nothing_is_written(
    signed_in, normal_matter
):
    title = "x" * (WEBSITE_OVERVIEW_TITLE_MAX_LENGTH + 1)
    form = CompactWebsiteOverviewForm(
        {"published_on": "09.09.2026", "overview_title": title, "url": VTK_PAGE}
    )
    assert not form.is_valid()
    assert "overview_title" in form.errors
    edit = WebsiteOverviewLinkForm({"title": title, "url": VTK_PAGE, "published_on": ""})
    assert not edit.is_valid() and "title" in edit.errors

    with pytest.raises(DomainError, match=re.escape(WEBSITE_OVERVIEW_TITLE_TOO_LONG)):
        _published(normal_matter, url=VTK_PAGE, title=title)
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter, status="PUBLISHED")


def test_a_restricted_overviews_title_reaches_no_reader(client, specialist, reader):
    matter = factories.MatterFactory(owner=specialist)
    overview = _published(matter, url=VTK_PAGE, title="Salajane pealkiri")
    MatterWebsiteOverview.objects.filter(pk=overview.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    client.force_login(reader)
    response = client.get(_teema(matter))
    body = response.content.decode()

    # The reader is on the page — otherwise the absences below prove nothing.
    assert response.status_code == 200 and matter.title in body
    assert "Salajane pealkiri" not in body
    assert VTK_PAGE not in body


def test_a_reader_cannot_rename_an_overview(client, normal_matter, reader):
    overview = _published(normal_matter, url=VTK_PAGE, title="Pealkiri")
    client.force_login(reader)

    response = client.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
        ),
        {"title": "Võõras", "url": VTK_PAGE, "published_on": "", "revision": ""},
        **HX,
    )

    assert response.status_code in (403, 404)
    overview.refresh_from_db()
    assert overview.title == "Pealkiri"


def test_the_opinions_overview_chip_names_the_page(normal_matter):
    from app.submissions.forms import WebsiteOverviewChoiceField

    overview = _published(normal_matter, url=VTK_PAGE, title="Koja seisukoht VTK kohta")
    field = WebsiteOverviewChoiceField(queryset=MatterWebsiteOverview.objects.all())

    assert field.label_from_instance(overview).startswith(
        "Avaldatud · Koja seisukoht VTK kohta · 9.9.2026 · "
    )


# ---------------------------------------------------------------------------
# JUR-CASE-08 / 09 — `Veebileht` and `Märkus` on `Kaasamine`
# ---------------------------------------------------------------------------


def _compact(matter) -> str:
    return reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk})


def _edit(matter, engagement) -> str:
    return reverse(
        "matters:update_engagement", kwargs={"pk": matter.pk, "engagement_id": engagement.pk}
    )


def _create(client, matter, **extra):
    return client.post(
        _compact(matter),
        {
            "audience": "Õigusteenuse valdkonna Koja liikmed",
            "occurred_on": timezone.localdate().strftime("%d.%m.%Y"),
            **extra,
        },
        **HX,
    )


def test_create_stores_veebileht_and_markus_on_the_existing_columns(signed_in, normal_matter):
    response = _create(
        signed_in,
        normal_matter,
        website_url="www.koda.ee/hetkel-kasil/juristieksam",
        engagement_note=CAVEAT,
    )

    assert response.status_code == 200, response.content.decode()[:1500]
    engagement = MatterEngagement.objects.get(matter=normal_matter)
    assert engagement.url == KODA_PAGE
    assert engagement.note == CAVEAT
    assert engagement.title == "Õigusteenuse valdkonna Koja liikmed"
    assert engagement.smaily_url == "" and engagement.alchemer_url == ""
    added = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.ENGAGEMENT_ADDED
    )
    assert added.payload["has_url"] is True and added.payload["has_note"] is True
    # The note's words are on the record, not copied into the audit row.
    assert CAVEAT not in str(added.payload)


def test_a_refused_veebileht_is_named_beside_its_box(signed_in, normal_matter):
    response = _create(signed_in, normal_matter, website_url="javascript:alert(1)")

    assert response.status_code == 400
    assert 'id="id_website_url_error"' in response.content.decode()
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()


def test_muuda_edits_and_clears_veebileht_and_markus(signed_in, normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        url=KODA_PAGE,
        note="Vana märkus.",
        actor=specialist,
    )
    payload = {
        "title": "liikmed",
        "occurred_on": "",
        "feedback_deadline": "",
        "feedback_received": "",
        "smaily_url": "",
        "alchemer_url": "",
        "revision": engagement_revision_token(engagement),
    }

    response = signed_in.post(
        _edit(normal_matter, engagement),
        {**payload, "url": "koda.ee/hetkel-kasil/uus", "note": CAVEAT},
        **HX,
    )
    assert response.status_code == 200, response.content.decode()[:1500]
    engagement.refresh_from_db()
    assert (engagement.url, engagement.note) == ("https://koda.ee/hetkel-kasil/uus", CAVEAT)

    payload["revision"] = engagement_revision_token(engagement)
    signed_in.post(_edit(normal_matter, engagement), {**payload, "url": "", "note": ""}, **HX)
    engagement.refresh_from_db()
    assert (engagement.url, engagement.note) == ("", "")


def test_the_row_prints_veebileht_as_a_link_and_markus_under_its_label(
    signed_in, normal_matter, specialist
):
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        url=KODA_PAGE,
        smaily_url="https://sendsmaily.net/c/1",
        alchemer_url="https://survey.alchemer.eu/s3/1",
        note=CAVEAT,
        actor=specialist,
    )

    body = signed_in.get(_teema(normal_matter)).content.decode()

    assert f'href="{KODA_PAGE}"' in body
    assert ">Veebileht<span" in body
    # Three links, three names, never one address standing in for another.
    assert ">Smaily<span" in body and ">Alchemer<span" in body
    assert '<span class="uxtl__msnotelabel">Märkus</span>' in body
    assert CAVEAT in body


def test_the_three_links_keep_their_order_and_names(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        url=KODA_PAGE,
        smaily_url="https://sendsmaily.net/c/1",
        alchemer_url="https://survey.alchemer.eu/s3/1",
        actor=specialist,
    )

    labels = [link.label for link in engagement_milestone(engagement).links]
    assert labels == ["Veebileht", "Smaily", "Alchemer"]


def test_an_imported_mailings_address_is_not_called_a_web_page(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="Kirjade voor",
        url="https://sendsmaily.net/preview/123",
        actor=specialist,
    )

    assert [link.label for link in engagement_milestone(engagement).links] == ["Link"]


def test_a_round_without_page_or_note_reads_exactly_as_before(normal_matter, specialist):
    engagement = add_engagement(
        matter=normal_matter, kind=EngagementKind.OTHER, title="liikmed", actor=specialist
    )

    milestone = engagement_milestone(engagement)
    assert milestone.links == ()
    assert (milestone.own_note, milestone.own_note_label) == ("", "")
    assert milestone.what == "Kaasamine: liikmed"


def test_a_restricted_rounds_page_and_note_reach_no_reader(client, specialist, reader):
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="Salajane kaasamine",
        url=KODA_PAGE,
        note="SALAJANE MÄRKUS",
        actor=specialist,
    )
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    client.force_login(reader)
    response = client.get(_teema(matter))
    body = response.content.decode()

    assert response.status_code == 200 and matter.title in body
    assert "SALAJANE MÄRKUS" not in body
    assert KODA_PAGE not in body


def test_search_reads_the_note_and_the_pages_host_as_it_always_did(normal_matter, specialist):
    from app.search.indexing import rebuild_all
    from app.search.models import SearchDocument

    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="liikmed",
        url=KODA_PAGE + "?utm_source=SECRET",
        note=CAVEAT,
        actor=specialist,
    )
    rebuild_all()

    row = SearchDocument.objects.get(engagement=engagement)
    assert "tööloendit" in row.body_text
    assert "koda.ee" in row.alias_text
    assert "SECRET" not in row.alias_text


def test_work_lists_read_the_audience_never_the_page_or_the_note(normal_matter, specialist):
    long_page = "https://www.koda.ee/hetkel-kasil/" + "pikk-aadress-" * 60
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.OTHER,
        title="Õigusteenuse valdkonna Koja liikmed",
        url=long_page[:1000],
        note=CAVEAT * 5,
        feedback_deadline=timezone.localdate() + dt.timedelta(days=5),
        actor=specialist,
    )

    (item,) = [
        item
        for item in work_items.work_items(specialist)
        if item.source_type == work_items.SOURCE_FEEDBACK_WAIT
    ]
    assert item.text == engagement.title
