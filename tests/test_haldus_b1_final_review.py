"""Haldus B1 final review: nothing lost, nothing revealed, everything reachable (docs/adr/0221).

The coordinator's final review of the seven-group sidebar asked for three
proofs, each against a frozen copy of r92 (`e344a474`), the last release before
UX-002, rather than against the registry under test:

1. **No address lost.** Every URL name under `/haldus/` on r92, and each page
   outside it the sidebar gathers, still exists with the same route pattern —
   bookmarks keep working.
2. **The same refusal as before.** For the two readers who administer nothing —
   the reader (Testlugeja) and an ordinary lawyer — every one of those routes
   answers a GET with exactly the status it answered on r92. And every sidebar
   item they are now offered was already open to them on r92 (or is their own
   new Kuvamine page), so no new navigation item reveals anything.
3. **Every administration function reachable.** Each item is shown in the
   sidebar and opens for at least one authorized persona, and its GET sub-pages
   are linked from its own page.

The frozen tables were read from origin/main's running URL configuration with
the same personas and placeholder arguments as below; they are data, not a
second registry, and a later release that adds a Haldus route changes nothing
here (`tests/test_haldus_route_inventory.py` gives the new one its home).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from app.haldus import sections
from tests import factories
from tests.admin_session import apply_admin_gate, choose, pass_gate
from tests.test_haldus_route_inventory import PERSONAS, SIGNED_IN

pytestmark = pytest.mark.django_db

#: Every URL name under `/haldus/` and each gathered outside page on r92 (e344a474), with its
#: route pattern — read from origin/main's URL configuration on 25.11.2026, not typed.
R92_ROUTES: dict[str, str] = {
    "account_admin:cancel_invitation": "haldus/kasutajad/<uuid:pk>/tuhista-kutse/",
    "account_admin:change_email": "haldus/kasutajad/<uuid:pk>/aadress/",
    "account_admin:create": "haldus/kasutajad/uus/",
    "account_admin:deactivate": "haldus/kasutajad/<uuid:pk>/lulita-valja/",
    "account_admin:detail": "haldus/kasutajad/<uuid:pk>/",
    "account_admin:list": "haldus/kasutajad/",
    "account_admin:reactivate": "haldus/kasutajad/<uuid:pk>/lulita-sisse/",
    "account_admin:reset_second_factor": "haldus/kasutajad/<uuid:pk>/teine-tegur/",
    "account_admin:send_link": "haldus/kasutajad/<uuid:pk>/link/",
    "account_admin:without_owner": "haldus/kasutajad/ilma-vastutajata/",
    "core:development_status": "haldus/arendus/",
    "core:keyboard_shortcuts": "kiirklahvid/",
    "core:release_notes": "uuendused/",
    "core:release_notes_lovable": "uuendused/lovable/",
    "core:release_notes_lovable_export": "uuendused/lovable/excel/",
    "haldus:data_sync": "haldus/andmete-sunkroonimine/",
    "haldus:data_sync_check": "haldus/andmete-sunkroonimine/kontrolli/",
    "haldus:data_sync_differences": "haldus/andmete-sunkroonimine/erinevused/",
    "haldus:data_sync_history": "haldus/andmete-sunkroonimine/ajalugu/",
    "haldus:data_sync_names": "haldus/andmete-sunkroonimine/nimed/",
    "haldus:data_sync_now": "haldus/andmete-sunkroonimine/sunkrooni/",
    "haldus:data_sync_onenote_import": "haldus/andmete-sunkroonimine/onenote/impordi/",
    "haldus:data_sync_onenote_verify": "haldus/andmete-sunkroonimine/onenote/kontrolli/",
    "haldus:data_sync_release_hold": "haldus/andmete-sunkroonimine/ootel-sulgemine/",
    "haldus:data_sync_resolve": "haldus/andmete-sunkroonimine/erinevused/otsusta/",
    "haldus:enrol": "haldus/sisene/seadista/",
    "haldus:index": "haldus/",
    "haldus:letters": "haldus/ajaloolised-kirjad/",
    "haldus:letters_decide": "haldus/ajaloolised-kirjad/<uuid:pk>/",
    "haldus:lovable": "haldus/lovable/",
    "haldus:lovable_check": "haldus/lovable/kontrolli/",
    "haldus:onenote_holds": "haldus/andmete-sunkroonimine/onenote/ootel/",
    "haldus:osakond": "haldus/osakond/",
    "haldus:reauthenticate": "haldus/kinnita/",
    "haldus:register_watch": "haldus/registrid/",
    "haldus:register_watch_run": "haldus/registrid/<uuid:pk>/",
    "haldus:register_watch_sources": "haldus/registrid/allikad/",
    "haldus:sign_in": "haldus/sisene/",
    "haldus:sign_in_code": "haldus/sisene/kood/",
    "haldus:sign_out": "haldus/lopeta/",
    "haldus:susteem": "haldus/susteem/",
    "legacy_import:opinion_archive_browse": "haldus/arvamuste-arhiiv/",
    "legacy_import:opinion_archive_detail": "haldus/arvamuste-arhiiv/<uuid:pk>/",
    "legacy_import:opinion_archive_file": "haldus/arvamuste-arhiiv/<uuid:pk>/fail/",
    "legacy_import:opinion_archive_link": "haldus/arvamuste-arhiiv/<uuid:pk>/seos/",
    "legacy_import:opinion_decide": "haldus/arvamuste-ulevaatus/<uuid:pk>/",
    "legacy_import:opinion_queue": "haldus/arvamuste-ulevaatus/",
    "legacy_import:review_decide": "haldus/ajaloo-ulevaatus/<uuid:pk>/",
    "legacy_import:review_queue": "haldus/ajaloo-ulevaatus/",
    "notifications:reset_settings": "teavitused/seaded/taasta/",
    "notifications:settings": "teavitused/seaded/",
    "reference_data:index": "haldus/klassifikaatorid/",
    "reference_data:organisation": "haldus/klassifikaatorid/organisatsioonid/<uuid:pk>/",
    "reference_data:organisation_alias_add": "haldus/klassifikaatorid/organisatsioonid/<uuid:pk>/nimekujud/",  # noqa: E501
    "reference_data:organisation_alias_remove": "haldus/klassifikaatorid/organisatsioonid/<uuid:pk>/nimekujud/<uuid:alias_pk>/eemalda/",  # noqa: E501
    "reference_data:organisation_create": "haldus/klassifikaatorid/organisatsioonid/uus/",
    "reference_data:organisation_duplicates": "haldus/klassifikaatorid/organisatsioonid/kattuvused/",  # noqa: E501
    "reference_data:organisation_rename": "haldus/klassifikaatorid/organisatsioonid/<uuid:pk>/nimi/",  # noqa: E501
    "reference_data:organisations": "haldus/klassifikaatorid/organisatsioonid/",
    "reference_data:tag": "haldus/klassifikaatorid/sildid/<uuid:pk>/",
    "reference_data:tag_alias_add": "haldus/klassifikaatorid/sildid/<uuid:pk>/nimekujud/",
    "reference_data:tag_alias_remove": "haldus/klassifikaatorid/sildid/<uuid:pk>/nimekujud/<uuid:alias_pk>/eemalda/",  # noqa: E501
    "reference_data:tag_create": "haldus/klassifikaatorid/sildid/uus/",
    "reference_data:tag_edit": "haldus/klassifikaatorid/sildid/<uuid:pk>/muuda/",
    "reference_data:tag_merge": "haldus/klassifikaatorid/sildid/<uuid:pk>/liida/",
    "reference_data:tag_status": "haldus/klassifikaatorid/sildid/<uuid:pk>/seis/",
    "reference_data:tags": "haldus/klassifikaatorid/sildid/",
    "reference_data:vocabulary": "haldus/klassifikaatorid/<slug:key>/",
    "reporting:definitions": "statistika/definitsioonid/",
    "reporting:historical": "statistika/ajalooline/",
    "reporting:quality": "statistika/andmekvaliteet/",
}

#: What a GET answered on r92 for the two readers who administer nothing, per URL name
#: (placeholder arguments for routes that take one; see `_url`).
R92_STATUS: dict[str, dict[str, int]] = {
    "lawyer": {
        "account_admin:cancel_invitation": 404,
        "account_admin:change_email": 404,
        "account_admin:create": 404,
        "account_admin:deactivate": 404,
        "account_admin:detail": 404,
        "account_admin:list": 404,
        "account_admin:reactivate": 404,
        "account_admin:reset_second_factor": 404,
        "account_admin:send_link": 404,
        "account_admin:without_owner": 404,
        "core:development_status": 404,
        "core:keyboard_shortcuts": 200,
        "core:release_notes": 200,
        "core:release_notes_lovable": 200,
        "core:release_notes_lovable_export": 404,
        "haldus:data_sync": 404,
        "haldus:data_sync_check": 405,
        "haldus:data_sync_differences": 404,
        "haldus:data_sync_history": 404,
        "haldus:data_sync_names": 404,
        "haldus:data_sync_now": 404,
        "haldus:data_sync_onenote_import": 404,
        "haldus:data_sync_onenote_verify": 405,
        "haldus:data_sync_release_hold": 404,
        "haldus:data_sync_resolve": 404,
        "haldus:enrol": 302,
        "haldus:index": 200,
        "haldus:letters": 200,
        "haldus:letters_decide": 405,
        "haldus:lovable": 404,
        "haldus:lovable_check": 405,
        "haldus:onenote_holds": 404,
        "haldus:osakond": 404,
        "haldus:reauthenticate": 302,
        "haldus:register_watch": 200,
        "haldus:register_watch_run": 404,
        "haldus:register_watch_sources": 200,
        "haldus:sign_in": 200,
        "haldus:sign_in_code": 302,
        "haldus:sign_out": 405,
        "haldus:susteem": 404,
        "legacy_import:opinion_archive_browse": 200,
        "legacy_import:opinion_archive_detail": 404,
        "legacy_import:opinion_archive_file": 404,
        "legacy_import:opinion_archive_link": 405,
        "legacy_import:opinion_decide": 405,
        "legacy_import:opinion_queue": 403,
        "legacy_import:review_decide": 405,
        "legacy_import:review_queue": 404,
        "notifications:reset_settings": 405,
        "notifications:settings": 200,
        "reference_data:index": 404,
        "reference_data:organisation": 404,
        "reference_data:organisation_alias_add": 404,
        "reference_data:organisation_alias_remove": 404,
        "reference_data:organisation_create": 404,
        "reference_data:organisation_duplicates": 404,
        "reference_data:organisation_rename": 404,
        "reference_data:organisations": 404,
        "reference_data:tag": 404,
        "reference_data:tag_alias_add": 404,
        "reference_data:tag_alias_remove": 404,
        "reference_data:tag_create": 404,
        "reference_data:tag_edit": 404,
        "reference_data:tag_merge": 404,
        "reference_data:tag_status": 404,
        "reference_data:tags": 404,
        "reference_data:vocabulary": 404,
        "reporting:definitions": 200,
        "reporting:historical": 200,
        "reporting:quality": 200,
    },
    "reader": {
        "account_admin:cancel_invitation": 404,
        "account_admin:change_email": 404,
        "account_admin:create": 404,
        "account_admin:deactivate": 404,
        "account_admin:detail": 404,
        "account_admin:list": 404,
        "account_admin:reactivate": 404,
        "account_admin:reset_second_factor": 404,
        "account_admin:send_link": 404,
        "account_admin:without_owner": 404,
        "core:development_status": 404,
        "core:keyboard_shortcuts": 200,
        "core:release_notes": 200,
        "core:release_notes_lovable": 200,
        "core:release_notes_lovable_export": 404,
        "haldus:data_sync": 404,
        "haldus:data_sync_check": 405,
        "haldus:data_sync_differences": 404,
        "haldus:data_sync_history": 404,
        "haldus:data_sync_names": 404,
        "haldus:data_sync_now": 404,
        "haldus:data_sync_onenote_import": 404,
        "haldus:data_sync_onenote_verify": 405,
        "haldus:data_sync_release_hold": 404,
        "haldus:data_sync_resolve": 404,
        "haldus:enrol": 404,
        "haldus:index": 200,
        "haldus:letters": 200,
        "haldus:letters_decide": 404,
        "haldus:lovable": 404,
        "haldus:lovable_check": 405,
        "haldus:onenote_holds": 404,
        "haldus:osakond": 404,
        "haldus:reauthenticate": 404,
        "haldus:register_watch": 200,
        "haldus:register_watch_run": 404,
        "haldus:register_watch_sources": 200,
        "haldus:sign_in": 404,
        "haldus:sign_in_code": 404,
        "haldus:sign_out": 404,
        "haldus:susteem": 404,
        "legacy_import:opinion_archive_browse": 200,
        "legacy_import:opinion_archive_detail": 404,
        "legacy_import:opinion_archive_file": 404,
        "legacy_import:opinion_archive_link": 405,
        "legacy_import:opinion_decide": 405,
        "legacy_import:opinion_queue": 403,
        "legacy_import:review_decide": 405,
        "legacy_import:review_queue": 404,
        "notifications:reset_settings": 405,
        "notifications:settings": 200,
        "reference_data:index": 404,
        "reference_data:organisation": 404,
        "reference_data:organisation_alias_add": 404,
        "reference_data:organisation_alias_remove": 404,
        "reference_data:organisation_create": 404,
        "reference_data:organisation_duplicates": 404,
        "reference_data:organisation_rename": 404,
        "reference_data:organisations": 404,
        "reference_data:tag": 404,
        "reference_data:tag_alias_add": 404,
        "reference_data:tag_alias_remove": 404,
        "reference_data:tag_create": 404,
        "reference_data:tag_edit": 404,
        "reference_data:tag_merge": 404,
        "reference_data:tag_status": 404,
        "reference_data:tags": 404,
        "reference_data:vocabulary": 404,
        "reporting:definitions": 200,
        "reporting:historical": 200,
        "reporting:quality": 200,
    },
}

#: Placeholder arguments: nothing in the test database has these keys, so a
#: route that takes one answers its guard's refusal or its own «not found».
_PLACEHOLDER = {"int": "999999", "uuid": str(uuid.UUID(int=0))}
_ARGUMENT = re.compile(r"<(?:(\w+):)?\w+>")


def _url(pattern: str) -> str:
    return "/" + _ARGUMENT.sub(lambda m: _PLACEHOLDER.get(m.group(1) or "str", "naidis"), pattern)


def _walk(
    patterns: list[URLPattern | URLResolver], namespace: str = "", prefix: str = ""
) -> dict[str, str]:
    found: dict[str, str] = {}
    for entry in patterns:
        if isinstance(entry, URLResolver):
            inner = namespace
            if entry.namespace:
                inner = f"{namespace}:{entry.namespace}" if namespace else entry.namespace
            found.update(_walk(entry.url_patterns, inner, prefix + str(entry.pattern)))
        elif entry.name:
            name = f"{namespace}:{entry.name}" if namespace else entry.name
            found[name] = prefix + str(entry.pattern)
    return found


def _sidebar(body: str) -> str:
    start = body.index('<nav class="haldusnav"')
    return body[start : body.index("</nav>", start)]


def test_the_frozen_tables_are_not_empty():
    """Guards the guard: an empty table would prove nothing in silence."""
    assert len(R92_ROUTES) >= 70
    assert set(R92_STATUS["lawyer"]) == set(R92_STATUS["reader"]) == set(R92_ROUTES)


def test_no_r92_address_is_lost_or_moved():
    now = _walk(get_resolver().url_patterns)
    lost = sorted(name for name in R92_ROUTES if name not in now)
    moved = sorted(
        (name, R92_ROUTES[name], now[name])
        for name in R92_ROUTES
        if name in now and now[name] != R92_ROUTES[name]
    )
    assert not lost, f"removed since r92: {lost}"
    assert not moved, f"moved since r92: {moved}"


# -- the readers who administer nothing ----------------------------------------------


def _as_lawyer(settings) -> Client:
    apply_admin_gate(settings)
    client = Client()
    pass_gate(client)
    choose(client, factories.UserFactory())
    return client


def _as_reader(settings) -> Client:
    settings.AUTH_MODE = "none"
    client = Client()
    client.force_login(factories.ReaderFactory())
    return client


READERS: dict[str, Callable[..., Client]] = {"lawyer": _as_lawyer, "reader": _as_reader}

#: New since r92, and theirs: the persona's own display choices (docs/adr/0221 §2).
OWN_NEW_PAGES = {"haldus:settings"}


@pytest.mark.parametrize("who", sorted(READERS))
def test_every_r92_route_answers_these_readers_as_it_did(settings, who):
    client = READERS[who](settings)
    changed = []
    for name, pattern in sorted(R92_ROUTES.items()):
        status = client.get(_url(pattern)).status_code
        if status != R92_STATUS[who][name]:
            changed.append((name, R92_STATUS[who][name], status))
    assert not changed, f"(name, r92, now): {changed}"


@pytest.mark.parametrize("who", sorted(READERS))
def test_no_new_item_reveals_anything_to_these_readers(settings, who):
    client = READERS[who](settings)
    page = client.get("/haldus/")
    assert page.status_code == 200
    nav = _sidebar(page.content.decode())
    offered = {item.url_name for item in sections.SECTIONS if f'href="{item.url()}"' in nav}
    assert offered, "the sidebar offers these readers their own pages at least"
    not_open_before = sorted(
        name for name in offered - OWN_NEW_PAGES if R92_STATUS[who].get(name) != 200
    )
    assert not not_open_before, not_open_before
    # The two new addresses: their own Kuvamine opens; OneNote refuses, as Lovable does.
    assert client.get("/haldus/seaded/").status_code == 200
    assert client.get("/haldus/andmete-sunkroonimine/onenote/").status_code == 404
    assert "haldus:onenote" not in offered
    assert "haldus:data_sync" not in offered


# -- every administration function reachable -----------------------------------------


def _administrator_with_break_glass(client: Client) -> None:
    """The administrator persona during an audited break-glass grant: the reader the two
    reconciliation queues open for (the administrator role and a business read)."""
    from app.accounts.models import BreakGlassGrant

    administrator = factories.AdministratorFactory()
    BreakGlassGrant.objects.create(
        user=administrator,
        granted_by=factories.DepartmentHeadFactory(),
        reason="Näidisintsident",
        starts_at=timezone.now() - timedelta(minutes=5),
        expires_at=timezone.now() + timedelta(hours=2),
    )
    client.force_login(administrator)


def _signed_in(factory: Callable[[], object]) -> Callable[[Client], None]:
    def sign_in(client: Client) -> None:
        client.force_login(factory())

    return sign_in


#: Sub-pages reached by a step of a flow rather than by a link on the item's page.
NOT_LINKED = {
    "haldus:sign_in_code": "the administrator sign-in's step after the password",
    "haldus:enrol": "first sign-in only, reached from the code step",
    "haldus:reauthenticate": "reached when a fresh proof is asked for",
    # «Sünkrooni nüüd» is a link only while a mode is set; under OFF (the test
    # database) it is drawn disabled on purpose (templates/haldus/data_sync.html).
    "haldus:data_sync_now": "linked from Lovable's overview while synchronisation is on",
}


def _persona_runs() -> list[tuple[str, Callable[[Client], None], bool]]:
    runs = [(name, setup, True) for name, setup in sorted(PERSONAS.items())]
    runs += [(name, _signed_in(factory), False) for name, factory in sorted(SIGNED_IN.items())]
    runs.append(("administrator-with-break-glass", _administrator_with_break_glass, False))
    return runs


def test_every_item_is_reachable_for_an_authorized_persona(settings):
    patterns = _walk(get_resolver().url_patterns)
    reached: dict[str, str] = {}
    unlinked: list[tuple[str, str, str]] = []
    for who, setup, under_gate in _persona_runs():
        if under_gate:
            apply_admin_gate(settings)
        else:
            settings.AUTH_MODE = "none"
        client = Client()
        setup(client)
        overview = client.get("/haldus/")
        body = overview.content.decode()
        if overview.status_code != 200 or '<nav class="haldusnav"' not in body:
            continue
        nav = _sidebar(body)
        for item in sections.SECTIONS:
            if item.key in reached or f'href="{item.url()}"' not in nav:
                continue
            page = client.get(item.url())
            if page.status_code != 200:
                continue
            reached[item.key] = who
            # The item's own page and its sub-pages: a sub-page may be one level
            # down (Klassifikaatorid → Organisatsioonid → Võimalikud topeltkirjed).
            pages = [page.content.decode()]
            wanted = []
            for route in item.also:
                pattern = patterns[route]
                if "<" in pattern or route in NOT_LINKED:
                    continue
                sub = client.get(_url(pattern))
                if sub.status_code != 200:
                    continue  # a POST endpoint, or a confirmation a form leads to
                pages.append(sub.content.decode())
                wanted.append((route, pattern))
            for route, pattern in wanted:
                if not any(f'href="/{pattern}' in html for html in pages):
                    unlinked.append((item.key, route, who))
    unreached = sorted(item.key for item in sections.SECTIONS if item.key not in reached)
    assert not unreached, f"no authorized persona reaches: {unreached}"
    assert not unlinked, f"(item, sub-page, persona) not linked from the item's page: {unlinked}"
