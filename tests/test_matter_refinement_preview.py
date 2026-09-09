"""The Teema design-refinement preview surface. PREVIEW ONLY.

This branch is a visual study waiting for the product owner's eye, not a
release. What is worth testing about it is therefore not what the page looks
like — that is what the screenshots are for — but the three promises the
preview makes about the rest of the application:

1. **It cannot be reached in production.** Two gates, and both are checked here:
   the route is absent from the URLconf outside `DEBUG`, and the view refuses on
   its own even if something routes to it anyway.
2. **It changes nothing.** `/teemad/<pk>/` renders the production template and
   references no preview asset, and a `GET` of the preview writes no record.
3. **It invents no user-facing text.** `07-copy-inventory.md` carries a
   negative specification — a list of strings the design says are *not* on the
   page — and this asserts the list.

The first assertion is free rather than clever: `config/test_settings.py` sets
`DEBUG = False`, so the whole suite runs against a URLconf that does not contain
the preview route. Reaching the view at all takes a direct call.
"""

from __future__ import annotations

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import NoReverseMatch, path, reverse

from app.core import design_preview
from app.matters.models import Entry
from app.workflow.models import NextAction
from config.urls import urlpatterns as production_urlpatterns
from tests import factories

pytestmark = pytest.mark.django_db

#: The production routing table plus the one address `DEBUG` would have added.
#:
#: The suite runs with `DEBUG = False`, which is what makes
#: `test_the_preview_route_is_absent_from_a_production_urlconf` free — and also
#: what leaves no address for a test client to fetch. Rather than reloading
#: `app.core.urls` mid-run and leaking a mutated URLconf into every test after
#: it, the route is mounted here, on this module, for the tests that ask for it.
#: Every other namespace is present, because the preview includes production
#: partials that reverse half a dozen of them.
urlpatterns = [
    *production_urlpatterns,
    path(
        "eelvaate-test/<uuid:pk>/",
        design_preview.matter_refinement,
        name="matter_refinement_preview_test",
    ),
]

preview_urls = override_settings(ROOT_URLCONF=__name__, DEBUG=True)


# Every string the design frame carries that this preview is responsible for
# rendering — the section labels, the two moved cards, the add controls.
DESIGN_STRINGS = (
    "Järgmiseks",
    "Olulised tähtajad",
    "Kaasamine",
    "Ajajoon",
    "+ Lisa tähtaeg",
    "+ Lisa kaasamine",
    "Teema andmed",
    "Koja arvamus",
    "Seotud materjalid",
    "Märkmed",
)

# `07-copy-inventory.md`, "TEXT NOT PRESENT — do not add". Each of these is a
# real production string that the design frame does not carry, and none of them
# is authority to delete the capability behind it — see the reconciliation
# report. What this asserts is only that the preview draws the frame.
TEXT_NOT_PRESENT = (
    "Ava ajajoon",
    "Muu valdkond",
    "Andmeklass",
    "Märgi pärisandmeteks",
    "Märgi testandmeteks",
    "ainult sulle nähtav mustand",
    "Salvestub automaatselt · ei lähe ajajoonele",
    "+ Lisa oluline tähtaeg",
    "Lisa seotud teema",
    # The timeline filter, checked by its markup rather than by its three
    # labels. «Sissekanded» also occurs inside the restricted-visibility help
    # sentence in the ⋯ menu — which is production copy for a workflow the
    # design does not touch, and exactly the thing the copy contract says to
    # preserve.
    'class="timelinefilter"',
    "Ajajoone filter",
    # `Võimalikud seosed` survives as the *group key* inside the rail's `Lisa`
    # disclosure; what the design drops is the separate control that opened it.
    "data-related-suggest",
)


@pytest.fixture
def preview_matter(db, specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.ImportantDateFactory(matter=matter)
    factories.EntryFactory(matter=matter, author=specialist)
    return matter


def _request(user, matter):
    request = RequestFactory().get(f"/disainisusteem/teema-refinement/{matter.pk}/")
    request.user = user
    return request


def _fetch(client, user, matter):
    client.force_login(user)
    return client.get(f"/eelvaate-test/{matter.pk}/")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_the_preview_route_is_absent_from_a_production_urlconf() -> None:
    """The address does not exist when `DEBUG` is off.

    `app/core/urls.py` appends it conditionally, so a production routing table
    has nothing to resolve — which is a stronger guarantee than a view that
    refuses, because it holds however the request arrives. The suite runs with
    `DEBUG = False`, so this is simply what `reverse` does here.
    """
    with pytest.raises(NoReverseMatch):
        reverse("core:matter_refinement_preview", kwargs={"pk": "0" * 32})


def test_the_view_refuses_outside_development(specialist, preview_matter, settings) -> None:
    settings.DEBUG = False
    with pytest.raises(Http404):
        design_preview.matter_refinement(_request(specialist, preview_matter), pk=preview_matter.pk)


def test_the_view_refuses_where_real_data_is_allowed(specialist, preview_matter, settings) -> None:
    """The condition the whole safety argument rests on, stated on its own.

    A system check already refuses to boot with `DEBUG` and `REAL_DATA_ALLOWED`
    together, so this combination should be unreachable — which is exactly why
    the guard says it as well: the preview must not depend on a check somewhere
    else staying the way it is.
    """
    settings.DEBUG = True
    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(Http404):
        design_preview.matter_refinement(_request(specialist, preview_matter), pk=preview_matter.pk)


# ---------------------------------------------------------------------------
# What it renders
# ---------------------------------------------------------------------------


@preview_urls
def test_the_preview_renders_the_refinement(client, specialist, preview_matter) -> None:
    response = _fetch(client, specialist, preview_matter)
    body = response.content.decode()

    assert response.status_code == 200
    assert design_preview.TEMPLATE in [template.name for template in response.templates]
    # The four main-column blocks, in the design's order, and the rail.
    assert body.index("uxnext") < body.index("teema-faktid") < body.index("ajajoon")
    assert body.index("teema-faktid") < body.index("seotud-materjalid")
    assert 'id="olulised-tahtajad"' in body
    assert 'id="kaasamine"' in body
    assert "matter_refinement_preview.css" in body


@preview_urls
def test_the_preview_carries_the_design_strings(client, specialist, preview_matter) -> None:
    body = _fetch(client, specialist, preview_matter).content.decode()
    missing = [text for text in DESIGN_STRINGS if text not in body]
    assert not missing, f"design strings absent from the preview: {missing}"


@preview_urls
def test_the_preview_carries_no_text_the_design_removed(client, specialist, preview_matter) -> None:
    body = _fetch(client, specialist, preview_matter).content.decode()
    present = [text for text in TEXT_NOT_PRESENT if text in body]
    assert not present, f"strings the design says are not on the page: {present}"


@preview_urls
def test_reading_the_preview_writes_nothing(client, specialist, preview_matter) -> None:
    """A design study must not be able to change the record it is drawing."""
    before = (Entry.objects.count(), NextAction.objects.count())
    _fetch(client, specialist, preview_matter)
    assert (Entry.objects.count(), NextAction.objects.count()) == before


# ---------------------------------------------------------------------------
# And the page it is a study of
# ---------------------------------------------------------------------------


def test_the_matter_page_is_untouched(signed_in, specialist) -> None:
    """`/teemad/<pk>/` still renders the current design, from its own template.

    The preview is additive by construction — it adds files and one conditional
    route and edits no production template, stylesheet or script — and this is
    the assertion that says so from the outside.
    """
    matter = factories.MatterFactory(owner=specialist)
    response = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    names = [template.name for template in response.templates]

    assert response.status_code == 200
    assert "matters/matter_detail.html" in names
    assert "matters/partials/overview.html" in names
    assert design_preview.TEMPLATE not in names

    body = response.content.decode()
    assert "matter_refinement_preview.css" not in body
    assert "matter_refinement_preview.js" not in body
