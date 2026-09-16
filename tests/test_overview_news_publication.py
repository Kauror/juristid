"""`Ülevaade / uudis` — one publication activity, any public address (docs/adr/0085).

`tests/test_website_overviews.py` owns the lifecycle this record has had since
docs/adr/0081 and is deliberately left holding it. This file owns what ADR 0085
decided on top of it, and nothing else:

* **the name**, on every surface a lawyer reads — because a rename that reaches
  the launcher and not the chronology is two names for one activity on one page;
* **the widened address rule**: any syntactically valid public `http`/`https`
  page, with the safety half of ADR 0081 §3 kept intact — a parsed host, no
  userinfo, no non-web scheme, refused rather than truncated;
* **the date contract**, which docs/adr/0089 §8 has since replaced: ADR 0085 §3
  filled the box with today the moment somebody typed an address, and that
  default is now withdrawn on both halves — the panel renders an empty box, no
  page carries today for the browser to use, and an empty box means *unknown*
  rather than a refusal;
* **the boundaries that did not move**, asserted here rather than assumed,
  because a rename is exactly the change under which a rule quietly stops being
  enforced (ADR 0085 §4).

The publication date's own rules — an address without a date, a date cleared on
purpose, an existing date left alone — are `tests/test_overview_news_unknown_date.py`,
which owns docs/adr/0089 §8 the way this file owns ADR 0085.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.enums import WebsiteOverviewStatus
from app.matters.models import MatterWebsiteOverview
from app.matters.services import (
    WebsiteOverviewConflict,
    cancel_website_overview,
    close_matter,
    correct_website_overview_link,
    normalize_overview_news_url,
    plan_website_overview,
    publish_website_overview,
    website_overview_revision,
)
from app.matters.timeline import matter_timeline
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

PUBLISHED_ON = dt.date(2026, 3, 14)

#: Three addresses that are the whole point of ADR 0085 §2: the Chamber's own
#: page, a trade paper over `https`, and an older piece served over plain `http`.
KODA_URL = "https://koda.ee/uudised/pakendiseaduse-ulevaade"
NEWS_HTTPS_URL = "https://uudised.example/2026/03/kaubanduskoda-hoiatab"
NEWS_HTTP_URL = "http://vana.uudisteportaal.example/2019/artikkel"


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _add(client, matter, **fields):
    return client.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        fields,
        headers={"HX-Request": "true"},
    )


def _panel(client, matter) -> str:
    body = _detail(client, matter)
    panel = body[body.index('id="lisa-koduleht"') :]
    return panel[: panel.index("</form>")]


def _published(matter, actor, url: str = KODA_URL, on: dt.date = PUBLISHED_ON):
    return publish_website_overview(
        overview=plan_website_overview(matter=matter, actor=actor),
        url=url,
        published_on=on,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# §1 — the name, on every surface that renders it
# ---------------------------------------------------------------------------


def test_the_launcher_chip_names_the_neutral_activity(signed_in, normal_matter):
    """§1. One chip for both kinds of publication, and the old name is gone."""
    body = _detail(signed_in, normal_matter)

    assert "+ Ülevaade / uudis" in body
    assert "Kodulehe ülevaade" not in body


def test_the_panel_reads_in_the_neutral_wording_throughout(signed_in, normal_matter):
    """§3's table, asserted where a lawyer meets it.

    Every string here is one somebody reads before deciding what to type, and a
    panel that said `ülevaade` in the legend and `uudis` on the button would be
    two different promises about one form.
    """
    panel = _panel(signed_in, normal_matter)

    assert ">Lisa ülevaade / uudis<" in panel
    assert "ülevaade või uudis" in panel
    assert "Kui ülevaade või uudis on juba avaldatud" in panel
    assert "Avaldatud ülevaate või uudise link" in panel
    assert "Avaldamise kuupäev" in panel
    assert "koda.ee" not in panel


def test_the_panel_asks_for_no_kind(signed_in, normal_matter):
    """§1. The link is sufficient, so there is nothing here asking which it is.

    A `liik` would be a question at planning time whose answer exists at
    publication time: blank or guessed on most rows, consumed by nothing and
    therefore corrected by nobody.
    """
    panel = _panel(signed_in, normal_matter)

    assert 'type="radio"' not in panel
    assert "<select" not in panel
    assert "liik" not in panel.lower()
    # The two boxes, and the ones ADR 0081 §2 still refuses.
    assert 'name="url"' in panel
    assert 'name="published_on"' in panel
    assert "<textarea" not in panel
    assert 'type="file"' not in panel


def test_the_planned_strip_reads_in_the_neutral_wording(signed_in, normal_matter, specialist):
    """§3. The heading names the list; the sentence names one row's state."""
    plan_website_overview(matter=normal_matter, actor=specialist)

    body = _detail(signed_in, normal_matter)
    strip = body[body.index('id="kodulehe-ulevaated"') :]

    assert "Ülevaated / uudised" in strip
    assert "Ülevaade või uudis on plaanis, aga veel avaldamata." in strip
    assert "Lisa link ja avaldamiskuupäev" in strip
    assert "Kodulehe" not in strip


def test_the_chronology_names_the_activity_and_labels_the_link(
    signed_in, normal_matter, specialist
):
    """§2. `Ava ülevaade või uudis` promises no particular site, because the row
    no longer guarantees one — and the address is still never the row's text."""
    _published(normal_matter, specialist, url=NEWS_HTTPS_URL)

    body = _detail(signed_in, normal_matter)
    row = body[body.index('id="ajajoon"') :]

    assert "Ülevaade / uudis" in row
    assert "Ava ülevaade või uudis" in row
    assert "Ava kodulehel" not in row
    assert f'href="{NEWS_HTTPS_URL}"' in row
    assert f">{NEWS_HTTPS_URL}<" not in row
    assert 'target="_blank"' in row
    assert 'rel="noopener noreferrer"' in row
    assert "avaneb uues aknas" in row


def test_the_refusals_name_the_record_they_belong_to(normal_matter, specialist):
    """§5. A Teema page renders three kinds of address.

    «Link peab sisaldama veebiaadressi.» would be the only refusal on it that
    did not say which link it meant, leaving the reader to find out by
    elimination.
    """
    for bad in ("ei ole aadress", "ftp://example.com/x", "https://kasutaja:pw@example.com/x"):
        with pytest.raises(DomainError) as refusal:
            normalize_overview_news_url(bad)
        assert "levaate või uudise link" in str(refusal.value), bad


def test_the_audit_labels_read_in_the_neutral_wording():
    """§5. The stored values keep their spelling; the labels are what a human sees."""
    assert ChangeEventType.WEBSITE_OVERVIEW_PLANNED.label == "Ülevaade / uudis plaanis"
    assert ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED.label == "Ülevaade / uudis avaldatud"
    assert ChangeEventType.WEBSITE_OVERVIEW_CANCELLED.label == "Ülevaade / uudis tühistatud"
    assert (
        ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED.label
        == "Ülevaate või uudise linki või kuupäeva parandatud"
    )
    # The values are written into every row this record has ever produced, and
    # renaming one would be a data migration for a word nobody reads.
    assert ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED.value == "WEBSITE_OVERVIEW_PUBLISHED"


# ---------------------------------------------------------------------------
# §2 — any public web address, and everything that still is not one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        KODA_URL,
        "https://www.koda.ee/uudised/x",
        NEWS_HTTPS_URL,
        NEWS_HTTP_URL,
        "https://kaubanduskoda.example/en/news/2026?ref=rss#top",
        "http://localhost:8000/uudised/x",
        "https://192.0.2.10/uudised/x",
    ],
)
def test_any_public_web_address_is_accepted(url):
    """§2. No host allow-list, and no attempt to judge a host's worth either.

    `localhost` and a literal address are on the list deliberately: this
    application never contacts what is stored, so «is that host reachable from
    here» is not a question it is entitled to answer, and a rule that guessed
    would refuse an intranet page the department genuinely published on.
    """
    assert normalize_overview_news_url(url) == url


@pytest.mark.parametrize(
    ("url", "because"),
    [
        ("", "empty is a question about the record's state, not the address"),
        ("javascript:alert(1)", "script delivery dressed as an address"),
        ("data:text/html,<script>alert(1)</script>", "the same, base64 or not"),
        ("file:///c:/uudised/x", "not somewhere a reader's browser can follow"),
        ("mailto:info@koda.ee", "not a page"),
        ("ftp://example.com/x", "not a web scheme"),
        ("koda.ee/uudised/x", "no scheme at all"),
        ("https:///uudised/x", "a scheme and no host"),
        ("https://user:pw@/uudised/x", "an authority made of nothing but credentials"),
        ("https://koda.ee@example.com/x", "the name is in the userinfo a browser ignores"),
        ("http://kasutaja:parool@example.com/x", "a password on the file"),
        ("see ei ole aadress", "not a URL"),
    ],
)
def test_what_is_still_refused(url, because):
    """§2. The safety half of ADR 0081 §3, which the widening deliberately kept.

    Empty is the one entry that is not a refusal: it is returned as an empty
    string, because whether an address is *required* is a question about the
    state the record is in and is answered by `publish_website_overview`.
    """
    if url == "":
        assert normalize_overview_news_url(url) == ""
        return
    with pytest.raises(DomainError):
        normalize_overview_news_url(url)


def test_an_over_long_address_is_refused_rather_than_truncated():
    """Finding F-1, unchanged: a link cut off is a link that no longer resolves."""
    with pytest.raises(DomainError) as refusal:
        normalize_overview_news_url("https://uudised.example/" + "a" * 1000)
    assert "liiga pikk" in str(refusal.value)


def test_the_widening_took_nothing_away_from_the_engagement_or_position_rules():
    """§2. The safety half is *shared*, and the userinfo rule is this one's alone.

    `Kaasamine` and `Väline seisukoht` record addresses out of a historical
    register that this department did not choose and cannot re-issue, so a
    credential-bearing one there is a fact to preserve rather than a refusal to
    make. Sharing the implementation must not have quietly changed either.
    """
    from app.matters.services import normalize_engagement_url, normalize_external_position_url

    credentialed = "https://kasutaja:parool@kampaania.example/x"
    assert normalize_engagement_url(credentialed) == credentialed
    assert normalize_external_position_url(credentialed) == credentialed
    with pytest.raises(DomainError):
        normalize_overview_news_url(credentialed)


@pytest.mark.parametrize("url", [NEWS_HTTPS_URL, NEWS_HTTP_URL])
def test_publishing_to_an_arbitrary_host_succeeds_through_the_panel(signed_in, normal_matter, url):
    """§2, end to end: the panel's one-act published path, on somebody else's site."""
    response = _add(signed_in, normal_matter, url=url, published_on="14.03.2026")

    assert response.status_code == 200
    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    assert overview.url == url
    assert overview.published_on == PUBLISHED_ON
    # The row genuinely passed through both states, so the history says so.
    kinds = set(
        ChangeEvent.objects.filter(matter=normal_matter).values_list("event_type", flat=True)
    )
    assert ChangeEventType.WEBSITE_OVERVIEW_PLANNED in kinds
    assert ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED in kinds


def test_a_published_row_can_be_corrected_onto_a_different_host(normal_matter, specialist):
    """§2. A correction is bound by the same rule, which is now the same rule."""
    overview = _published(normal_matter, specialist, url=KODA_URL)

    correct_website_overview_link(
        overview=overview,
        url=NEWS_HTTP_URL,
        published_on=PUBLISHED_ON,
        actor=specialist,
        expected_revision=website_overview_revision(overview),
    )

    overview.refresh_from_db()
    assert overview.url == NEWS_HTTP_URL
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    event = ChangeEvent.objects.get(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    )
    assert event.payload["url_from"] == KODA_URL
    assert event.payload["url_to"] == NEWS_HTTP_URL


def test_several_publications_on_one_matter_on_several_hosts(normal_matter, specialist):
    """A long proceeding is written up more than once, and not always in one place."""
    for url in (KODA_URL, NEWS_HTTPS_URL, NEWS_HTTP_URL):
        _published(normal_matter, specialist, url=url)
    plan_website_overview(matter=normal_matter, actor=specialist)

    rows = MatterWebsiteOverview.objects.filter(matter=normal_matter)

    assert rows.filter(status=WebsiteOverviewStatus.PUBLISHED).count() == 3
    assert rows.filter(status=WebsiteOverviewStatus.PLANNED).count() == 1
    assert {row.url for row in rows.published()} == {KODA_URL, NEWS_HTTPS_URL, NEWS_HTTP_URL}

    items, _ = matter_timeline(matter=normal_matter, user=specialist)
    milestones = [item for item in items if item.website_overview is not None]
    assert len(milestones) == 3
    assert {item.milestone.what for item in milestones} == {"Ülevaade / uudis"}


def test_the_same_address_is_still_filed_at_most_once_per_matter(normal_matter, specialist):
    """§4. The one uniqueness there is, unchanged by the widening."""
    from django.db import IntegrityError, transaction

    _published(normal_matter, specialist, url=NEWS_HTTPS_URL)
    second = plan_website_overview(matter=normal_matter, actor=specialist)

    with pytest.raises(IntegrityError), transaction.atomic():
        publish_website_overview(
            overview=second, url=NEWS_HTTPS_URL, published_on=PUBLISHED_ON, actor=specialist
        )


# ---------------------------------------------------------------------------
# §3 — the date default, on the half of it the server owns
# ---------------------------------------------------------------------------


def test_the_date_box_is_empty_and_no_default_is_offered_anywhere(signed_in, normal_matter):
    """§3, as docs/adr/0089 §8 replaces it: there is no default left to offer.

    ADR 0085 §3 wrote today into `data-publication-default` and let an island in
    `ux.js` put it in the box the moment somebody typed an address. Lawyer
    testing measured what that produced — a plausible date, already there,
    accepted without being read — so both halves are withdrawn: the attribute,
    and the `initial` on the publish form's own box.

    All three assertions are the same claim from three sides: the box is empty,
    nothing tells the browser what today is, and nothing pairs the two boxes.
    """
    panel = _panel(signed_in, normal_matter)
    box = panel[panel.index('name="published_on"') :]
    box = box[: box.index(">")]

    assert 'value=""' in box or "value=" not in box, box
    assert "data-publication-default" not in panel
    assert "data-publication-trigger" not in panel


def test_no_page_and_no_script_carries_the_publication_default(
    signed_in, normal_matter, specialist
):
    """§3. The island is gone from `ux.js`, not merely unused by this panel.

    A Matter carrying a planned row renders the strip's publish form as well, so
    this is the page with the most date boxes on it — and none of them is wired
    to anything (docs/adr/0089 §8).
    """
    plan_website_overview(matter=normal_matter, actor=specialist)

    body = _detail(signed_in, normal_matter)
    script = (Path(settings.BASE_DIR) / "static" / "js" / "ux.js").read_text(encoding="utf-8")

    assert "data-publication-default" not in body
    assert "data-publication-trigger" not in body
    assert "bindPublicationDate(" not in script


def test_nothing_anywhere_supplies_a_publication_date(signed_in, normal_matter, specialist):
    """§3. Not a default and not a fallback — which is the whole of ADR 0078 §2.

    The strongest form of the claim, because it is the one the lawyer feedback
    actually rests on: an address and no date, through the service *and* through
    the browser's own route, stores `NULL` on both paths. If anything reached
    for the clock, one of these two rows would be dated today.
    """
    through_service = plan_website_overview(matter=normal_matter, actor=specialist)
    publish_website_overview(
        overview=through_service, url=NEWS_HTTPS_URL, published_on=None, actor=specialist
    )

    response = _add(signed_in, normal_matter, url=NEWS_HTTP_URL, published_on="")

    assert response.status_code == 200
    through_service.refresh_from_db()
    assert through_service.status == WebsiteOverviewStatus.PUBLISHED
    assert through_service.published_on is None

    through_browser = MatterWebsiteOverview.objects.get(matter=normal_matter, url=NEWS_HTTP_URL)
    assert through_browser.status == WebsiteOverviewStatus.PUBLISHED
    assert through_browser.published_on is None
    assert timezone.localdate() not in {
        through_service.published_on,
        through_browser.published_on,
    }


def test_an_untouched_form_still_records_a_plan(signed_in, normal_matter):
    """§3. The empty submit, still reachable and still a complete answer."""
    response = _add(signed_in, normal_matter)

    assert response.status_code == 200
    overview = MatterWebsiteOverview.objects.get(matter=normal_matter)
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert overview.url == ""
    assert overview.published_on is None


@pytest.mark.parametrize(
    "fields",
    [
        {"url": "", "published_on": "14.03.2026"},
        {"url": "https://koda.ee@example.com/x", "published_on": "14.03.2026"},
    ],
)
def test_a_refusal_comes_back_holding_exactly_what_was_submitted(signed_in, normal_matter, fields):
    """§3. A swap preserves what was typed **and** what was deliberately cleared.

    The cleared date is the one worth a test: a refusal that helpfully put today
    back would overwrite a decision the person had just made, and would do it at
    the moment they were least likely to look.
    """
    response = _add(signed_in, normal_matter, **fields)
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=normal_matter).exists()
    assert 'id="lisa-koduleht"' in body

    panel = body[body.index('id="lisa-koduleht"') :]
    panel = panel[: panel.index("</form>")]
    for name, value in fields.items():
        box = panel[panel.index(f'name="{name}"') :]
        box = box[: box.index(">")]
        if value:
            assert f'value="{value}"' in box, box
        else:
            assert 'value=""' in box or "value=" not in box, box


# ---------------------------------------------------------------------------
# §4 — the boundaries a rename must not have moved
# ---------------------------------------------------------------------------


def test_the_lifecycle_transitions_are_what_they_were(normal_matter, specialist):
    """§4. Published is terminal for cancellation; cancelled is terminal outright."""
    published = _published(normal_matter, specialist)
    with pytest.raises(DomainError):
        cancel_website_overview(overview=published, actor=specialist)

    cancelled = plan_website_overview(matter=normal_matter, actor=specialist)
    cancel_website_overview(overview=cancelled, actor=specialist)
    with pytest.raises(DomainError):
        publish_website_overview(
            overview=cancelled, url=NEWS_HTTPS_URL, published_on=PUBLISHED_ON, actor=specialist
        )
    with pytest.raises(DomainError):
        cancel_website_overview(overview=cancelled, actor=specialist)

    published.refresh_from_db()
    cancelled.refresh_from_db()
    assert published.status == WebsiteOverviewStatus.PUBLISHED
    assert cancelled.status == WebsiteOverviewStatus.CANCELLED


def test_a_planned_row_reaches_no_work_surface_search_or_archive(normal_matter, specialist):
    """§4. ADR 0081 §4's absences, re-proven under the new name.

    A widened address rule is exactly the kind of change that invites «and while
    we are here, index it»; this is the assertion that says nobody did.
    """
    from app.matters import work_items
    from app.matters.my_work import build_my_work

    before = rebuild_all().documents
    plan_website_overview(matter=normal_matter, actor=specialist)
    published = _published(normal_matter, specialist, url=NEWS_HTTPS_URL)
    after = rebuild_all()

    assert work_items.work_items(specialist) == []
    assert not normal_matter.next_actions.exists()
    my_work = build_my_work(specialist)
    assert my_work.has_work is False
    assert my_work.bands == []

    assert after.documents == before
    assert not SearchDocument.objects.filter(source_object_id=published.pk).exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM search_searchdocument WHERE body_text ILIKE %s",
            ["%uudised.example%"],
        )
        assert cursor.fetchone()[0] == 0

    assert not normal_matter.documents.exists()
    assert not normal_matter.submissions.exists()
    assert not normal_matter.work_victories.exists()
    assert not normal_matter.important_dates.exists()


def test_a_closed_matter_refuses_every_crafted_write_and_still_allows_a_correction(
    signed_in, specialist
):
    """§4. Both halves of ADR 0081 §5, which the rename did not touch.

    The refusals are the service's, under the row lock, because a POST may
    arrive from a tab that was open before somebody else shut the file — the
    controls not being rendered decides nothing.
    """
    matter = factories.MatterFactory(owner=specialist)
    published = _published(matter, specialist, url=NEWS_HTTPS_URL)
    planned = plan_website_overview(matter=matter, actor=specialist)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="tehtud"
    )

    # The closure cancelled the plan it still owed, and did not touch the page
    # that exists.
    planned.refresh_from_db()
    published.refresh_from_db()
    assert planned.status == WebsiteOverviewStatus.CANCELLED
    assert published.status == WebsiteOverviewStatus.PUBLISHED

    assert _add(signed_in, matter, url=NEWS_HTTP_URL, published_on="14.03.2026").status_code == 400
    assert not MatterWebsiteOverview.objects.filter(matter=matter, url=NEWS_HTTP_URL).exists()

    # And the correction, which closure has never meant to forbid.
    response = signed_in.post(
        reverse(
            "matters:correct_website_overview",
            kwargs={"pk": matter.pk, "overview_id": published.pk},
        ),
        {
            "url": NEWS_HTTP_URL,
            "published_on": "14.03.2026",
            "revision": website_overview_revision(published),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    published.refresh_from_db()
    assert published.url == NEWS_HTTP_URL
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    ).exists()


def test_a_stale_correction_on_a_closed_matter_writes_nothing(normal_matter, specialist):
    """§4. The expected-revision protection, on the one route closure allows.

    The token is compared after the row lock and before anything is decided, so
    a refusal leaves the record exactly as the other writer left it.
    """
    published = _published(normal_matter, specialist, url=KODA_URL)
    stale = website_overview_revision(published)
    correct_website_overview_link(
        overview=published,
        url=NEWS_HTTPS_URL,
        published_on=PUBLISHED_ON,
        actor=specialist,
        expected_revision=stale,
    )
    close_matter(
        matter=normal_matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        reason="tehtud",
    )
    corrections_before = ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
    ).count()

    with pytest.raises(WebsiteOverviewConflict):
        correct_website_overview_link(
            overview=published,
            url=NEWS_HTTP_URL,
            published_on=dt.date(2026, 4, 1),
            actor=specialist,
            expected_revision=stale,
        )

    published.refresh_from_db()
    assert published.url == NEWS_HTTPS_URL
    assert published.published_on == PUBLISHED_ON
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED
        ).count()
        == corrections_before
    )


def test_a_reader_may_not_write_any_of_it(client, reader, normal_matter, specialist):
    """§4. The permission boundary, which a rename has no business relaxing."""
    overview = plan_website_overview(matter=normal_matter, actor=specialist)
    client.force_login(reader)

    routes = [
        (reverse("matters:add_website_overview", kwargs={"pk": normal_matter.pk}), {}),
        (
            reverse(
                "matters:publish_website_overview",
                kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
            ),
            {"url": NEWS_HTTPS_URL, "published_on": "14.03.2026"},
        ),
        (
            reverse(
                "matters:cancel_website_overview",
                kwargs={"pk": normal_matter.pk, "overview_id": overview.pk},
            ),
            {},
        ),
    ]
    for url, data in routes:
        assert client.post(url, data, headers={"HX-Request": "true"}).status_code in (403, 404)

    overview.refresh_from_db()
    assert overview.status == WebsiteOverviewStatus.PLANNED
    assert MatterWebsiteOverview.objects.filter(matter=normal_matter).count() == 1


# ---------------------------------------------------------------------------
# §6 — the migrations, and the proof they carry no data
# ---------------------------------------------------------------------------


def test_the_rename_migrations_are_state_only():
    """§6. One `AlterModelOptions` and one `AlterField` over `choices`.

    Neither is a database object: `verbose_name` is Python metadata and
    `choices` is validation and display, so a rename that reached the schema
    would be a rename that had done something it was not asked to.
    """
    from django.db.migrations.executor import MigrationExecutor

    loader = MigrationExecutor(connection).loader
    expected = {
        ("matters", "0026_overview_news_verbose_name"): ["AlterModelOptions"],
        ("audit", "0021_overview_news_event_labels"): ["AlterField"],
    }
    for (app_label, name), operations in expected.items():
        migration = loader.get_migration(app_label, name)
        assert [type(op).__name__ for op in migration.operations] == operations
        assert not any(
            type(op).__name__ in {"RunPython", "RunSQL"} for op in migration.operations
        ), f"{app_label}/{name} carries a data migration"


def test_widening_the_address_rule_needed_no_constraint_change():
    """§6. The boundary never lived in the database, so nothing there moved.

    The constraints that do exist are lifecycle integrity — both-or-neither, the
    timestamps, the vocabulary, the per-Matter uniqueness of a published
    address — and every one of them is still on the table.
    """
    names = {constraint.name for constraint in MatterWebsiteOverview._meta.constraints}

    assert names == {
        "matters_website_overview_status_vocabulary",
        # `…_has_link`, not `…_has_link_and_date`: docs/adr/0089 §8 replaced the
        # first implication with a weaker one, in
        # `matters/0028_overview_news_optional_publication_date`. Widening the
        # *address* rule still needed no constraint change — that is what this
        # test is about — and this name is the one thing on the list that moved
        # for a different decision.
        "matters_website_overview_published_has_link",
        "matters_website_overview_unpublished_has_neither",
        "matters_website_overview_published_has_timestamp",
        "matters_website_overview_cancelled_has_timestamp",
        "matters_website_overview_one_row_per_published_link",
        "matters_website_overview_visibility_vocabulary",
    }
