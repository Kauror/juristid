"""`Dokumendid` prints an overview's address, not a label (docs/adr/0106 §7).

docs/adr/0105 §3 made the `Ülevaade / uudis` chronology row show its address
instead of `Ava ülevaade või uudis`, and deliberately left this surface alone as
out of that round's scope. The result was two presentations of one link on one
file — a lawyer opening `Teema käik` read `koda.ee/uudised/…` and the same link
under `Dokumendid` read a sentence about what a link is for.

`tests/test_website_overviews.py` owns what `link_display` does to an address;
this owns that this template calls it, and that the stored `url` is still the
whole `href`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from app.matters.services import plan_website_overview, publish_website_overview
from app.submissions.models import SubmissionWebsiteOverviewLink

pytestmark = pytest.mark.django_db

KODA_URL = "https://koda.ee/uudised/pakendiseaduse-ulevaade"
KODA_SHOWN = "koda.ee/uudised/pakendiseaduse-ulevaade"


def _documents(client, matter) -> str:
    response = client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def opinion_with_overview(normal_matter, specialist, organisation, evidence_root):
    """One sent `Koja arvamus`, linked to one published write-up.

    Built through the ordinary services rather than with `objects.create`, so the
    row the page reads is the row the product makes.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.workspace import add_matter_koda_opinion

    result = add_matter_koda_opinion(
        matter=normal_matter,
        author=specialist,
        upload=SimpleUploadedFile(
            "arvamus.pdf", b"%PDF-1.4 synthetic evidence", content_type="application/pdf"
        ),
        recipients=[organisation],
        sent_on=dt.date(2026, 3, 1),
        title="Koja arvamus",
    )
    overview = publish_website_overview(
        overview=plan_website_overview(matter=normal_matter, actor=specialist),
        url=KODA_URL,
        published_on=dt.date(2026, 3, 14),
        actor=specialist,
    )
    SubmissionWebsiteOverviewLink.objects.create(
        submission=result.record, website_overview=overview, linked_by=specialist
    )
    return result.record, overview


def test_the_address_is_the_link_text(signed_in, normal_matter, opinion_with_overview):
    """31, 32, 33. The address reads, the label is gone, the `href` is whole."""
    body = _documents(signed_in, normal_matter)

    assert "Ava ülevaade või uudis" not in body
    assert KODA_SHOWN in body
    assert f'href="{KODA_URL}"' in body


def test_the_link_keeps_its_new_tab_treatment(signed_in, normal_matter, opinion_with_overview):
    """Unchanged by the wording: the safety half of the link is not a style."""
    body = _documents(signed_in, normal_matter)
    block = body[body.index("Seotud ülevaated / uudised") :]

    assert 'target="_blank"' in block
    assert 'rel="noopener noreferrer"' in block
    assert "avaneb uues aknas" in block


def test_the_state_and_the_day_still_read_beside_it(
    signed_in, normal_matter, opinion_with_overview
):
    """What this round did *not* change on this row.

    Unlike the chronology, `Dokumendid` names the state: this is a list of
    write-ups attached to one letter, where `Plaanis` and `Avaldatud` are the
    distinction a reader is scanning for. Only the link's own wording moved.
    """
    body = _documents(signed_in, normal_matter)
    block = body[body.index("Seotud ülevaated / uudised") :]

    assert "Avaldatud" in block
    assert "14.3.2026" in block


def test_one_formatter_decides_how_an_address_prints(normal_matter):
    """No second URL formatter was written for this surface.

    Asserted against the property rather than the page: what matters is that
    both templates read the same one, and a second implementation would pass a
    rendering test today and drift the first time either changed. The rules are
    `MatterWebsiteOverview.link_display`'s own and are not restated here beyond
    the two this round's wording depends on — the scheme goes, and a historical
    address carrying userinfo never prints it (red-team F-2).
    """
    from app.matters.models import MatterWebsiteOverview

    plain = MatterWebsiteOverview(matter=normal_matter, url="https://uudised.example/lugu/?ref=1")
    # The scheme goes; the path's own slash before a query stays, because there
    # it is part of the address rather than punctuation at the end of one.
    assert plain.link_display == "uudised.example/lugu/?ref=1"
    trailing = MatterWebsiteOverview(matter=normal_matter, url="https://uudised.example/lugu/")
    assert trailing.link_display == "uudised.example/lugu"

    # Unsaved on purpose: `normalize_website_overview_url` has refused
    # credentials since docs/adr/0081 §3, so no row can be written carrying one.
    # The property is what protects the rows that predate that rule.
    historical = MatterWebsiteOverview(matter=normal_matter, url="https://user:pw@koda.ee/lugu")
    assert historical.link_display == "koda.ee/lugu"
    assert "pw" not in historical.link_display
