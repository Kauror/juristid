"""QA authz (2026-10-14): existence oracles over the surfaces added since the last audits.

`tests/test_read_side_existence_oracles.py` already proves, for READER and
ADMINISTRATOR, that a hidden RESTRICTED Matter or a NORMAL Matter carrying
RESTRICTED children of every kind changes nothing those two receive on the
register, the Matter page, search and Statistika. Three things it does not do,
and this file adds:

1. **The newer surfaces** (ADR 0150-0161): the Jõustumised list and calendar,
   `Kõik muudatused`, the management metric lists (`/statistika/loend/`) and
   their CSV, the submission list's month windows, and every link the
   Statistika and register pages themselves draw (harvested, not guessed).
2. **The shared-gate reader with no persona** (`DepartmentViewer`). It reaches
   every `@gate_required` page — all of Statistika, Arvamused, Osakond and the
   document routes — and the existing oracle never runs as it.
3. **A READER who participates in one RESTRICTED Matter** (the
   "restricted-record permission" persona): participation in one file must not
   widen what they learn about another.

Same harness, same two worlds, same comparison: everything captured before the
hidden record is added must be byte-identical after it (CSRF normalised).
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from app.accounts.enums import UserRole
from app.core.enums import Visibility
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate
from tests.test_read_side_existence_oracles import (  # noqa: F401 - fixture re-export
    HIDDEN_ACTION,
    HIDDEN_PHRASE,
    HIDDEN_TITLE,
    HIDDEN_WORLDS,
    SHARED_WORD,
    _capture,
    _differences,
    add_restricted_matter,
    surfaces,
    world,
)

pytestmark = pytest.mark.django_db


def add_restricted_related_link(world, capture_evidence, extract):  # noqa: F811
    """A RESTRICTED Matter linked (Seotud teemad, ADR 0150) to a visible one,
    through the canonical service, so both files get their history rows."""
    from app.related_materials.services import link_related_matters

    hidden = add_restricted_matter(world, capture_evidence, extract)
    link_related_matters(matter=world["first"], other=hidden["matter"], actor=world["owner"])
    return hidden


def add_restricted_initiative(world, capture_evidence, extract):  # noqa: F811
    """A RESTRICTED Koja ettepanek (ADR 0151): track «Koja algatus», no Saabus,
    with a sent opinion — the population the initiative statistics read."""
    from app.matters.models import Matter
    from app.workflow.enums import Track

    hidden = add_restricted_matter(world, capture_evidence, extract)
    Matter.objects.filter(pk=hidden["matter"].pk).update(
        track=Track.KODA_INITIATIVE, received_date=None
    )
    return hidden


#: The two hidden worlds of the existing oracle, plus two built on the newer
#: features. Kept local so the original file stays untouched.
ALL_HIDDEN = {
    **HIDDEN_WORLDS,
    "restricted-related-link": add_restricted_related_link,
    "restricted-initiative": add_restricted_initiative,
}

#: Link prefixes worth following from the Statistika and register pages. The
#: rest of the application is already in `surfaces`.
HARVEST_PREFIXES = ("/statistika/", "/teemad/?", "/arvamused/", "/osakond/")
HARVEST_LIMIT = 160
HREF = re.compile(r'href="([^"#]+)')


def newer_surfaces(world) -> list[str]:  # noqa: F811
    today = world["today"]
    register = reverse("matters:matter_list")
    month = today.strftime("%Y-%m")
    next_month = (today.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
    first = world["first"]
    host = world["host"]
    urls = [
        # Jõustumised (ADR 0158): the list and the calendar over the register.
        register + "?joustumine=on&vaade=joustumised",
        register + "?joustumine=on&vaade=joustumised&olek=koik",
        register + "?joustumine=on&vaade=joustumised&joustumised_koik=jargmised",
        register + "?joustumine=on&vaade=joustumised&joustumiste_jarjestus=teema",
        register + f"?joustumine=on&vaade=kalender&kuu={month}",
        register + f"?joustumine=on&vaade=kalender&kuu={next_month}",
        register + "?joustumine=puudub",
        register
        + f"?joustub_alates={today.isoformat()}"
        + f"&joustub_kuni={(today + timedelta(days=40)).isoformat()}&vaade=joustumised",
        register + "?andmed=paris",
        register + "?andmed=paris&olek=koik",
        # Kõik muudatused (ADR 0092) on both visible files.
        reverse("matters:matter_changes", kwargs={"pk": first.pk}),
        reverse("matters:matter_changes", kwargs={"pk": host.pk}),
        # Osakond, Muudatused (ADR 0159) — 404 for everybody here, must stay so.
        reverse("matters:department_changes"),
        # Statistika windows and lists (ADR 0160).
        reverse("reporting:submissions") + f"?aasta={today.year}",
        reverse("reporting:submissions") + f"?aasta={today.year}&kuu={today.month}",
        reverse("reporting:overview") + f"?periood=aasta&aasta={today.year}",
        reverse("reporting:activity") + f"?aasta={today.year}",
        reverse("reporting:matters") + f"?aasta={today.year}",
        reverse("reporting:overview") + "?andmed=paris",
    ]
    return urls


def harvest(client: Client, seeds: list[str]) -> list[str]:
    found: list[str] = []
    seen = set(seeds)
    for url in seeds:
        response = client.get(url)
        if response.status_code != 200 or getattr(response, "streaming", False):
            continue
        for href in HREF.findall(response.content.decode()):
            href = href.replace("&amp;", "&")
            if urlsplit(href).netloc:
                continue
            if not href.startswith(HARVEST_PREFIXES) or href in seen:
                continue
            seen.add(href)
            found.append(href)
            if len(found) >= HARVEST_LIMIT:
                return found
    return found


def _signed_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def collaborating_reader(world):  # noqa: F811
    """A READER who is a collaborator on one RESTRICTED Matter of their own."""
    reader = factories.UserFactory(role=UserRole.READER, display_name="Lugeja Kaastöötaja")
    own = factories.MatterFactory(
        title="Lugeja enda piiratud teema",
        owner=world["owner"],
        visibility=Visibility.RESTRICTED,
        reference_year=2099,
        reference_number=777,
        received_date=world["today"],
        response_deadline=world["today"],
    )
    own.collaborators.set([reader])
    return reader, own


def _all_urls(world, client_for_harvest: Client) -> list[str]:  # noqa: F811
    base = surfaces(world) + newer_surfaces(world)
    seeds = [
        reverse("reporting:overview"),
        reverse("reporting:matters"),
        reverse("reporting:activity"),
        reverse("reporting:submissions"),
        reverse("reporting:quality"),
        reverse("matters:matter_list") + "?joustumine=on&vaade=joustumised",
        reverse("matters:department"),
    ]
    harvested = harvest(client_for_harvest, seeds)
    # Non-vacuity: the crawl must actually reach the ADR 0160 metric lists.
    assert any("naitaja=" in url for url in harvested), harvested[:20]
    assert len(harvested) >= 20, harvested
    print(f"harvested {len(harvested)} links")
    return base + harvested


def _assert_nothing_moved(before, after, persona: str) -> None:
    for url, (_status, _location, body) in after.items():
        for token in (HIDDEN_TITLE, HIDDEN_PHRASE, HIDDEN_ACTION):
            if token in url:
                continue
            assert token not in body, f"{token} reached {persona} on {url}"
    assert _differences(before, after) == ""


@pytest.mark.parametrize("hidden", list(ALL_HIDDEN))
def test_reader_newer_surfaces_do_not_move(world, capture_evidence, extract, hidden):  # noqa: F811
    client = _signed_in(world["reader"])
    urls = _all_urls(world, _signed_in(world["owner"]))

    before = _capture(client, urls)
    ALL_HIDDEN[hidden](world, capture_evidence, extract)
    after = _capture(client, urls)

    _assert_nothing_moved(before, after, "reader")


@pytest.mark.parametrize("hidden", list(ALL_HIDDEN))
def test_collaborating_reader_learns_nothing_about_another_file(
    world,  # noqa: F811
    collaborating_reader,
    capture_evidence,
    extract,
    hidden,
):
    reader, own = collaborating_reader
    client = _signed_in(reader)
    urls = _all_urls(world, _signed_in(world["owner"])) + [  # noqa: RUF005
        reverse("matters:matter_detail", kwargs={"pk": own.pk}),
    ]

    before = _capture(client, urls)
    # The allowed half: their own restricted file opens.
    assert before[reverse("matters:matter_detail", kwargs={"pk": own.pk})][0] == 200
    ALL_HIDDEN[hidden](world, capture_evidence, extract)
    after = _capture(client, urls)

    _assert_nothing_moved(before, after, "collaborating reader")


@pytest.fixture
def department_viewer(settings, world):  # noqa: F811
    """A client past the shared gate that has chosen no persona."""
    apply_shared_gate(settings)
    client = Client()
    response = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    assert response.status_code == 302
    return client


@pytest.mark.parametrize("hidden", list(ALL_HIDDEN))
def test_department_viewer_gate_surfaces_do_not_move(
    world,  # noqa: F811
    department_viewer,
    capture_evidence,
    extract,
    hidden,
):
    # Harvest as the department head behind the same gate is impossible without
    # a second session; harvest from the department viewer itself (it reaches
    # every gate page), which is what an outsider with the password would do.
    urls = _all_urls(world, department_viewer)

    before = _capture(department_viewer, urls)
    reachable = [u for u, (status, _l, _b) in before.items() if status == 200]
    assert any(u.startswith("/statistika/") for u in reachable), (
        "the gate reader reached no Statistika page"
    )
    ALL_HIDDEN[hidden](world, capture_evidence, extract)
    after = _capture(department_viewer, urls)

    _assert_nothing_moved(before, after, "department viewer")


def test_department_viewer_sees_normal_work_on_the_gate_pages(world, department_viewer):  # noqa: F811
    """The allowed half: the gate pages are useful, not empty."""
    body = department_viewer.get(reverse("submissions:sent")).content.decode()
    assert "Avalik arvamus" in body
    download = department_viewer.get(
        reverse("documents:download", kwargs={"pk": world["document"].versions.first().pk})
    )
    assert download.status_code == 200


def test_the_lawyers_see_the_restricted_related_link(world, capture_evidence, extract):  # noqa: F811
    """Allowed half of the related-link world: the file's lawyers see the link."""
    hidden = add_restricted_related_link(world, capture_evidence, extract)
    body = (
        _signed_in(world["owner"])
        .get(
            reverse("related_materials:section", kwargs={"pk": world["first"].pk}),
            HTTP_HX_REQUEST="true",
        )
        .content.decode()
    )
    assert hidden["matter"].title[:20] in body
