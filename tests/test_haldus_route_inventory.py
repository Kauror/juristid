"""Every Haldus page has exactly one sidebar home, and the sidebar answers as the routes do.

UX-002 (docs/adr/0221) replaced the flat strip with seven groups. Two promises
hold it together, and both are checked here against the running URL
configuration rather than against a list copied into the test:

1. **Exactly one home.** Every URL name under `/haldus/`, and every page outside
   it that the sidebar now gathers (notification settings, the reporting
   administration pages, Uuendused, Kiirklahvid), is named by the `routes` of
   exactly one registry item. A new Haldus route with no home fails here, and
   so does one claimed twice.
2. **The menu equals the guard.** For every persona and administrative role —
   lawyer, department head, reference manager, the administrator persona,
   a technical administrator with and without a verified administrator
   session, a shared-gate session with no persona, and a signed-in reader —
   each item is shown exactly when its own route answers a GET with 200. The
   menu can never offer a page that refuses, nor hide one that opens.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from app.accounts.enums import Capability, UserRole
from app.haldus import sections
from tests import factories
from tests.admin_session import (
    apply_admin_gate,
    choose,
    full_admin,
    pass_gate,
    technical_admin,
    verified,
)

pytestmark = pytest.mark.django_db

#: Pages outside `/haldus/` that the sidebar gathers (preparation report §3 B).
OUTSIDE = (
    "notifications:settings",
    "notifications:reset_settings",
    "reporting:quality",
    "reporting:historical",
    "reporting:definitions",
    "core:release_notes",
    "core:release_notes_lovable",
    "core:release_notes_lovable_export",
    "core:keyboard_shortcuts",
)


def _walk(
    patterns: list[URLPattern | URLResolver], namespace: str = "", prefix: str = ""
) -> Iterator[tuple[str, str]]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            inner = namespace
            if entry.namespace:
                inner = f"{namespace}:{entry.namespace}" if namespace else entry.namespace
            yield from _walk(entry.url_patterns, inner, prefix + str(entry.pattern))
        elif entry.name:
            name = f"{namespace}:{entry.name}" if namespace else entry.name
            yield name, prefix + str(entry.pattern)


def _haldus_url_names() -> set[str]:
    return {
        name for name, route in _walk(get_resolver().url_patterns) if route.startswith("haldus/")
    }


def test_the_groups_are_the_contracted_seven_in_order():
    assert [(group.key, group.label) for group in sections.GROUPS] == [
        ("ulevaade", "Ülevaade"),
        ("seaded", "Isiklikud seaded"),
        ("tookorraldus", "Töökorraldus"),
        ("andmed", "Andmed"),
        ("integratsioonid", "Integratsioonid"),
        ("susteem", "Süsteem ja õigused"),
        ("rakendus", "Rakendus"),
    ]
    assert [item.key for item in sections.group_of("lovable").items] == [
        "lovable",
        "onenote",
        "registrid",
    ]


def test_there_are_haldus_routes_to_check():
    """Guards the guard: a walk that stopped finding routes would pass in silence."""
    names = _haldus_url_names()
    assert len(names) >= 60
    assert {"haldus:index", "account_admin:list", "legacy_import:opinion_queue"} <= names


def test_every_haldus_page_has_exactly_one_sidebar_home():
    claimed = Counter(route for item in sections.SECTIONS for route in item.routes)
    twice = sorted(name for name, count in claimed.items() if count > 1)
    assert not twice, f"claimed by two items: {twice}"

    wanted = _haldus_url_names() | set(OUTSIDE)
    homeless = sorted(wanted - set(claimed))
    assert not homeless, f"no sidebar home: {homeless}"


def test_every_claimed_route_exists():
    """A stale name in `routes` is a home for nothing."""
    every = {name for name, _route in _walk(get_resolver().url_patterns)}
    missing = sorted(set(sections.HOMES) - every)
    assert not missing, missing


def test_no_existing_address_moved():
    """UX-002 adds two destinations and moves none (owner brief: bookmarks keep working)."""
    for name, path in (
        ("haldus:index", "/haldus/"),
        ("haldus:data_sync", "/haldus/andmete-sunkroonimine/"),
        ("haldus:onenote_holds", "/haldus/andmete-sunkroonimine/onenote/ootel/"),
        ("haldus:register_watch", "/haldus/registrid/"),
        ("haldus:lovable", "/haldus/lovable/"),
        ("account_admin:list", "/haldus/kasutajad/"),
        ("account_admin:without_owner", "/haldus/kasutajad/ilma-vastutajata/"),
        ("reference_data:index", "/haldus/klassifikaatorid/"),
        ("legacy_import:opinion_queue", "/haldus/arvamuste-ulevaatus/"),
        ("legacy_import:review_queue", "/haldus/ajaloo-ulevaatus/"),
        ("legacy_import:opinion_archive_browse", "/haldus/arvamuste-arhiiv/"),
        ("core:development_status", "/haldus/arendus/"),
        ("notifications:settings", "/teavitused/seaded/"),
        # The two new destinations.
        ("haldus:settings", "/haldus/seaded/"),
        ("haldus:onenote", "/haldus/andmete-sunkroonimine/onenote/"),
    ):
        assert reverse(name) == path, name


def test_the_current_item_follows_the_url_name():
    assert sections.home_of("haldus:data_sync_differences").key == "lovable"
    assert sections.home_of("haldus:onenote_holds").key == "onenote"
    assert sections.home_of("haldus:register_watch_run").key == "registrid"
    assert sections.home_of("account_admin:without_owner").key == "vastutajata"
    assert sections.home_of("reference_data:tag_merge").key == "klassifikaatorid"
    assert sections.home_of("matters:my_work") is None


# -- the menu equals the guard, persona by persona ---------------------------------


@pytest.fixture
def gate(settings):
    return apply_admin_gate(settings)


def _lawyer(client: Client) -> None:
    pass_gate(client)
    choose(client, factories.UserFactory())


def _head(client: Client) -> None:
    pass_gate(client)
    choose(client, factories.DepartmentHeadFactory())


def _reference_manager(client: Client) -> None:
    pass_gate(client)
    manage = Capability.MANAGE_REFERENCE_DATA.value
    choose(client, factories.UserFactory(capability_overrides={manage: "allow"}))


def _head_holding_administration(client: Client) -> None:
    pass_gate(client)
    choose(client, full_admin(role=UserRole.DEPARTMENT_HEAD)[0])


def _verified_technical(client: Client) -> None:
    admin, secret = technical_admin()
    full_admin()
    verified(client, admin, secret)


def _verified_technical_with_lawyer(client: Client) -> None:
    admin, secret = technical_admin()
    verified(client, admin, secret)
    choose(client, factories.UserFactory())


def _verified_account_administrator(client: Client) -> None:
    """A full administrator who is not a technical one: accounts, no integrations."""
    admin, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    verified(client, admin, secret)


def _gate_only(client: Client) -> None:
    pass_gate(client)


PERSONAS: dict[str, Callable[[Client], None]] = {
    "lawyer": _lawyer,
    "department-head": _head,
    "reference-manager": _reference_manager,
    "head-holding-administration": _head_holding_administration,
    "verified-technical-administrator": _verified_technical,
    "verified-technical-administrator-and-lawyer": _verified_technical_with_lawyer,
    "verified-account-administrator": _verified_account_administrator,
    "shared-gate-only": _gate_only,
}


def _opens(response) -> bool:
    return response.status_code == 200


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_each_item_is_shown_exactly_when_its_route_opens(client, gate, persona):
    PERSONAS[persona](client)
    mismatches = []
    for item in sections.SECTIONS:
        response = client.get(item.url())
        shown = item.visible(response.wsgi_request)
        if shown != _opens(response):
            mismatches.append((item.key, shown, response.status_code))
    assert not mismatches, mismatches


#: Accounts that are no persona behind the gate (the persona list leaves out the
#: ADMINISTRATOR and the READER roles), signed in under a mode without one.
SIGNED_IN = {
    "reader": factories.ReaderFactory,
    "administrator-persona": factories.AdministratorFactory,
}


@pytest.mark.parametrize("account", sorted(SIGNED_IN))
def test_a_signed_in_account_sees_what_opens(client, settings, account):
    """The same rule for a reader and the administrator persona (Testadministraator)."""
    settings.AUTH_MODE = "none"
    client.force_login(SIGNED_IN[account]())
    mismatches = []
    for item in sections.SECTIONS:
        response = client.get(item.url())
        if item.visible(response.wsgi_request) != _opens(response):
            mismatches.append((item.key, response.status_code))
    assert not mismatches, mismatches
    nav = client.get(reverse("haldus:index")).content.decode()
    assert '<nav class="haldusnav"' in nav


@pytest.mark.parametrize("persona", sorted(set(PERSONAS) - {"shared-gate-only"}))
def test_the_sidebar_offers_exactly_the_visible_items(client, gate, persona):
    PERSONAS[persona](client)
    page = client.get(reverse("haldus:index"))
    assert page.status_code == 200
    body = page.content.decode()
    nav = body[
        body.index('<nav class="haldusnav"') : body.index(
            "</nav>", body.index('<nav class="haldusnav"')
        )
    ]
    offered = [item for item in sections.SECTIONS if f'href="{item.url()}"' in nav]
    assert offered == sections.visible_sections(page.wsgi_request)
    # A group heading only where one of its items is shown.
    for group in sections.GROUPS:
        any_shown = any(item in offered for item in group.items)
        heading = f'id="haldusnav-{group.key}"'
        if group.key == "ulevaade":
            continue
        assert (heading in nav) == any_shown, (group.key, persona)


def test_a_shared_gate_session_with_no_persona_gets_no_shell(client, gate):
    pass_gate(client)
    assert client.get(reverse("haldus:index")).status_code == 404
    assert client.get(reverse("core:release_notes")).status_code == 200
    body = client.get(reverse("core:release_notes")).content.decode()
    assert 'class="haldusnav"' not in body
