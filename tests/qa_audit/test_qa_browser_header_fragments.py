"""Independent QA 2026-10-14 — browser area: fragments the Teema page swaps in after a save.

Found in a real browser against main 81a7d6e4 (worklog
C:/CC/_worklogs/independent-qa-2026-10-14/agents/browser/, scripts wf_tabs.py and
wf_teema.py) and pinned here at template level, because the defect is in what
the server sends back for the swap, not in the browser.

`xfail(strict=True)` marks a reproduced defect; the test flips when it is fixed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.models import Matter
from app.matters.services import create_matter, matter_field_revision
from app.workflow.models import StageVocabulary

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def tabs_fragment(body: str) -> str:
    """The `Teema vaated` nav as the response draws it."""
    assert 'id="teema-vaated"' in body, "the response carries no tab bar at all"
    return body.split('id="teema-vaated"', 1)[1].split("</nav>", 1)[0]


def detail_tabs(client, matter: Matter) -> str:
    return tabs_fragment(
        client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    )


def test_the_full_page_marks_the_teema_tab_current(signed_in, specialist):
    """Control: the page itself states which tab is open."""
    matter = create_matter(title="Vahekaardi kontroll", actor=specialist, owner=specialist)

    tabs = detail_tabs(signed_in, matter)

    assert 'aria-current="page"' in tabs
    assert "is-active" in tabs


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-BROWSER-01: an inline header edit (Hetkeseis menu, ADR 0156) re-renders "
        "#teema-pais from _header_context, which has no `tab`, so the swapped-in tab bar "
        "loses aria-current=page and the active underline on both Teema and Dokumendid"
    ),
)
def test_a_header_stage_change_keeps_the_open_tab_marked(signed_in, specialist):
    matter = create_matter(title="Hetkeseis päisest", actor=specialist, owner=specialist)

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {
            "stage": str(stage("consultation").pk),
            "revision": matter_field_revision(matter, "stage"),
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.stage.key == "consultation"
    assert 'aria-current="page"' in tabs_fragment(response.content.decode())


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-BROWSER-01: a column save that re-sends the header out of band "
        "(_render_overview(header_out_of_band=True), e.g. + Lisa → Arvamuse tähtaeg) sets "
        "`tab` only on the non-header branch, so the OOB tab bar loses aria-current=page"
    ),
)
def test_a_column_save_with_the_header_out_of_band_keeps_the_open_tab_marked(signed_in, specialist):
    matter = create_matter(title="Arvamuse tähtaeg uuesti", actor=specialist, owner=specialist)
    due = timezone.localdate() + timedelta(days=10)

    response = signed_in.post(
        reverse("matters:add_response_deadline", kwargs={"pk": matter.pk}),
        {"response_deadline_date": due.strftime("%d.%m.%Y")},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="teema-pais"' in body and 'hx-swap-oob="true"' in body
    assert 'aria-current="page"' in tabs_fragment(body)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-BROWSER-02: the header Hetkeseis menu swaps only #teema-pais, so `Tegevused` "
        "(grouped by Hetkeseis period) keeps showing the previous period as current until "
        "the page is reloaded"
    ),
)
def test_a_header_stage_change_redraws_the_period_grouped_chronology(signed_in, specialist):
    matter = create_matter(
        title="Perioodid päisest", actor=specialist, owner=specialist, stage=stage("idea")
    )

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {"stage": str(stage("government").pk), "revision": matter_field_revision(matter, "stage")},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    # A reload draws the new period as the current one; the swap must not leave
    # the old grouping on screen.
    reloaded = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "(praegune hetkeseis)" in reloaded
    assert "(praegune hetkeseis)" in response.content.decode()
