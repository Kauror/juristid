"""The Teema's «Registritest» tab and the in-place tab strip (owner, 2026-11-21).

The register watch's per-Teema list moved from «Uued dokumendid registrites»
filtered to one Teema into a third tab on the Teema page. It is always there,
counts what waits, and says plainly when the Teema is not updated
automatically. The three tabs swap the area under them, so the header and the
rail stay put.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from app.register_watch import views as register_views
from app.register_watch.models import (
    FindingStatus,
    Relevance,
    WatchFinding,
    WatchSource,
    WatchTarget,
)
from tests import factories

pytestmark = pytest.mark.django_db


def _tab(matter):
    return reverse("matters:matter_registers", kwargs={"pk": matter.pk})


def _finding(matter, **extra):
    target, _ = WatchTarget.objects.get_or_create(
        matter=matter,
        source=WatchSource.HTM_REGISTER,
        identifier="973 SE",
        defaults={"label": "973 SE", "origin": "TITLE"},
    )
    return WatchFinding.objects.create(
        target=target,
        matter=matter,
        external_id=extra.pop("external_id", "doc:t1"),
        title=extra.pop("title", "Muudatusettepanekud (973 SE) eelnõule"),
        occurred_on=dt.date(2026, 11, 6),
        url="https://dok.hm.ee/et/document.html?id=t1",
        detail="8-1/26/5965",
        **extra,
    )


def test_the_tab_lists_what_waits_and_its_forms_come_back_to_it(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Kutseseaduse eelnõu (973 SE)")
    finding = _finding(matter)
    client.force_login(specialist)

    body = client.get(_tab(matter)).content.decode()

    assert "Muudatusettepanekud (973 SE) eelnõule" in body
    assert register_views.WATCHED_LINE in body
    assert 'name="tagasi" value="teema"' in body
    # The header and the rail are on this tab too, so swapping keeps them.
    assert 'id="teema-tabsisu"' in body and 'id="teema-andmed"' in body
    assert re.search(r'aria-current="page"\s+href="' + re.escape(_tab(matter)) + '"', body)

    response = client.post(
        reverse("register_watch:decide", kwargs={"pk": finding.pk}),
        {"otsus": "jata", "tagasi": "teema"},
    )

    assert response.status_code == 302 and response["Location"] == _tab(matter)
    finding.refresh_from_db()
    assert finding.status == FindingStatus.DISMISSED


def test_a_phrase_saved_on_the_tab_comes_back_to_it(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    client.force_login(specialist)

    response = client.post(
        reverse("register_watch:phrase", kwargs={"pk": matter.pk}),
        {"allikas": WatchSource.KLIM_REGISTER, "sonad": "Jäätmeseadus", "tagasi": "teema"},
    )

    assert response.status_code == 302 and response["Location"] == _tab(matter)


def test_a_teema_whose_ministry_register_cannot_be_read_says_so(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Relvaseaduse muutmise eelnõu")
    matter.source_organisations.add(factories.OrganisationFactory(name="Siseministeerium"))
    client.force_login(specialist)

    body = client.get(_tab(matter)).content.decode()

    assert "Seda teemat ei uuendata automaatselt: Siseministeerium" in body
    assert "Kontrolli registrit käsitsi." in body
    assert "regtab__state--unread" in body


def test_a_teema_with_nothing_to_search_by_says_how_to_give_it_words(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Eelnõu")
    client.force_login(specialist)

    body = client.get(_tab(matter)).content.decode()

    assert register_views.UNKNOWN_LINE in body
    assert "Otsingusõnad" in body


def test_a_teema_in_its_ministrys_register_is_watched_before_the_first_run(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Jäätmeseaduse muutmise eelnõu")
    matter.source_organisations.add(factories.OrganisationFactory(name="Kliimaministeerium"))

    assert register_views.watch_state(matter, [])["state"] == "watched"


def test_a_closed_teema_is_not_watched(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Kutseseaduse eelnõu (973 SE)")
    matter.is_open = False

    assert register_views.watch_state(matter, []) == {
        "state": "closed",
        "line": register_views.CLOSED_LINE,
    }


def test_the_badge_counts_only_what_waits_and_is_related(client, specialist):
    matter = factories.MatterFactory(owner=specialist, title="Kutseseaduse eelnõu (973 SE)")
    _finding(matter)
    _finding(matter, external_id="doc:t2", relevance=Relevance.UNRELATED)
    client.force_login(specialist)

    for url in (
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        reverse("matters:matter_documents", kwargs={"pk": matter.pk}),
        _tab(matter),
    ):
        body = client.get(url).content.decode()
        assert 'class="tabs__badge" title="1 uus dokument ootab">1<' in body, url


def test_every_tab_swaps_only_the_area_under_the_strip(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    for url in (
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        reverse("matters:matter_documents", kwargs={"pk": matter.pk}),
        _tab(matter),
    ):
        body = client.get(url).content.decode()
        assert body.count('hx-target="#teema-tabsisu"') >= 3, url
        assert body.count('id="teema-tabsisu"') == 1, url
        assert body.count('id="teema-vaade-wrap"') == 1, url
        assert 'hx-push-url="true"' in body


def test_a_rail_save_on_the_registers_tab_reloads_it(rf, specialist):
    from app.matters.views import _render_overview

    matter = factories.MatterFactory(owner=specialist)
    request = rf.post("/", HTTP_HX_CURRENT_URL=f"http://testserver{_tab(matter)}")
    request.user = specialist

    response = _render_overview(request, matter)

    assert response["HX-Refresh"] == "true"


def test_a_header_edit_sent_from_registritest_answers_with_the_header_alone(signed_in, specialist):
    from app.matters.services import create_matter, matter_field_revision
    from app.workflow.models import StageVocabulary

    matter = create_matter(title="Päis registritest", actor=specialist, owner=specialist)
    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {
            "stage": str(StageVocabulary.objects.get(key="consultation").pk),
            "revision": matter_field_revision(matter, "stage"),
        },
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=f"http://testserver{_tab(matter)}",
    )

    assert response.status_code == 200
    assert "HX-Retarget" not in response
    body = response.content.decode()
    tabs = body.split('id="teema-vaated"', 1)[1].split("</nav>", 1)[0]
    assert tabs.count('aria-current="page"') == 1
    assert re.search(r'aria-current="page"\s+href="' + re.escape(_tab(matter)) + '"', tabs)
