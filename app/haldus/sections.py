"""The Haldus shell's registry: seven groups of items, and which ones this reader may open.

Two levels since UX-002 (docs/adr/0221): a **group** (Ülevaade, Isiklikud seaded,
Töökorraldus, Andmed, Integratsioonid, Süsteem ja õigused, Rakendus) holds
**items**, and each item is one destination with one predicate over the request.
A group is shown only when at least one of its items is. The sidebar, the
overview's attention list, the search destinations and the account menu's
Haldus entry are all built from the same predicates — so the shell can never
offer a page it would refuse. Showing is not authorizing: every route behind an
item asks its own question again and refuses a crafted URL on its own
(docs/adr/0186).

Each item also names every URL name it is the **sidebar home** of (`routes`):
the page itself, its sub-pages and its POST endpoints. The current item is
found from the resolved URL name through that list, so a page cannot be the
home of two items, and `tests/test_haldus_route_inventory.py` proves every
Haldus URL name has exactly one.

Two kinds of standing are read here, and kept apart:

* **the persona** (`request.user`) — what a department head's work-management
  surfaces and the business pages have always followed, under every mode;
* **the verified administrator** (`admin_session.verified_administrator`) —
  account administration, the integrations and system status, never the persona.

Each predicate answers exactly what its route answers for a GET (owner brief
UX-002: the menu never offers a route that refuses, or hides one that opens).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse

from app.accounts import admin_session
from app.core.authorization import (
    is_department_head,
    may_read_business_content,
    may_review_historical_matches,
    may_view_department_management,
)


@dataclass(frozen=True)
class Item:
    """One destination in the sidebar."""

    key: str
    label: str
    url_name: str
    visible: Callable[[HttpRequest], bool]
    #: One line on the overview and in search: what the page is for.
    description: str = ""
    #: Every other URL name this item is the sidebar home of (sub-pages, POSTs).
    also: tuple[str, ...] = ()

    @property
    def routes(self) -> tuple[str, ...]:
        return (self.url_name, *self.also)

    def url(self) -> str:
        return reverse(self.url_name)


#: The flat registry's old name; an item is what a section was.
Section = Item


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    items: tuple[Item, ...] = field(default=())


@dataclass(frozen=True)
class VisibleGroup:
    """A group as one reader sees it: only its visible items."""

    key: str
    label: str
    items: tuple[Item, ...]


def _route_exists(url_name: str) -> bool:
    try:
        reverse(url_name)
    except NoReverseMatch:
        return False
    return True


def _persona(request: HttpRequest) -> object | None:
    return getattr(request, "user", None)


def _signed_in(request: HttpRequest) -> bool:
    """A persona chosen (shared gate) or an account signed in: what `login_required` asks."""
    return bool(getattr(_persona(request), "is_authenticated", False))


def everybody(request: HttpRequest) -> bool:
    """Pages behind the shared gate alone, with no business content (docs/adr/0034)."""
    return True


# -- Isiklikud seaded ------------------------------------------------------------------


def may_open_settings(request: HttpRequest) -> bool:
    """Kuvamine: the signed-in persona's own display choices. Nobody else's."""
    return _signed_in(request)


def may_open_notification_settings(request: HttpRequest) -> bool:
    """Teavitused: offered to exactly the readers the bell is drawn for (UI-03).

    The one item narrower than its route: `notifications:settings` is
    `login_required` and reads only one's own choices, but a technical
    administrator without a business role has no bell and nothing to set, so
    the sidebar does not offer it (`haldus.notification_settings_link`, the
    same rule). Narrower never offers a route that refuses; the inventory test
    names this exception (docs/adr/0221).
    """
    return may_read_business_content(_persona(request))


# -- Töökorraldus ----------------------------------------------------------------------


def may_open_department(request: HttpRequest) -> bool:
    """Osakonna töökorraldus: whoever holds the department management view."""
    return may_view_department_management(_persona(request))


def may_open_accounts(request: HttpRequest) -> bool:
    return admin_session.may_administer_accounts(request)


# -- Andmed ----------------------------------------------------------------------------


def may_open_reference_data(request: HttpRequest) -> bool:
    """Klassifikaatorid, where the reference-data workstream's pages exist.

    Hidden while the route is not installed. The capability is
    `reference.manage` (`app.reference_data.access`); the persona's holding is
    read as the reference-data pages read it, and a verified administrator's
    holding too — the integration adds the same fallback to those pages'
    actor (docs/adr/0186, 0187).
    """
    if not _route_exists("reference_data:index"):
        return False
    try:
        from app.reference_data.access import (  # type: ignore[import-not-found,unused-ignore]
            may_manage_reference_data,
        )
    except ImportError:
        return False
    if may_manage_reference_data(_persona(request)):
        return True
    principal = admin_session.verified_administrator(request)
    return principal is not None and bool(may_manage_reference_data(principal))


def may_open_letters(request: HttpRequest) -> bool:
    """Ajaloolised kirjad: whoever reads business content — the lawyers (docs/adr/0216).

    Deciding is a business write and its route asks that of its own; reading is
    the department's own outgoing correspondence, read like the opinion archive.
    """
    return may_read_business_content(_persona(request))


def may_open_opinion_archive(request: HttpRequest) -> bool:
    """Arvamuste arhiiv: `require_archive_reader` — a business reader (docs/adr/0028)."""
    from app.legacy_import.opinion_access import may_read_archive

    return _signed_in(request) and may_read_archive(_persona(request))


def may_open_opinion_queue(request: HttpRequest) -> bool:
    """Arvamuste ülevaatus: `require_opinion_queue_operator` — the administrator persona
    who also reads business content (docs/adr/0028, 0181)."""
    from app.legacy_import.opinion_access import may_use_opinion_queue

    persona = _persona(request)
    return (
        _signed_in(request)
        and may_use_opinion_queue(persona)
        and may_read_business_content(persona)
    )


def may_open_history_review(request: HttpRequest) -> bool:
    """Ajaloo ülevaatus: the historical reconciliation queue's own two questions."""
    persona = _persona(request)
    return (
        _signed_in(request)
        and may_review_historical_matches(persona)
        and may_read_business_content(persona)
    )


def may_open_reporting_admin(request: HttpRequest) -> bool:
    """Andmekvaliteet, Ajaloolised materjalid, Statistika definitsioonid: `login_required`."""
    return _signed_in(request)


# -- Integratsioonid -------------------------------------------------------------------


def may_open_system(request: HttpRequest) -> bool:
    """A verified technical administrator, and nobody else: Lovable, OneNote, Süsteemi seis."""
    return admin_session.is_technical_administrator(request)


def may_open_register_watch(request: HttpRequest) -> bool:
    """Registrite jälgimine: whoever reads business content, like the review page (ADR 0217 §8)."""
    return may_read_business_content(_persona(request))


# -- Süsteem ja õigused ----------------------------------------------------------------


def may_open_development_status(request: HttpRequest) -> bool:
    """Arendusseis: the department head or the opinion queue's administrator persona."""
    from app.legacy_import.opinion_access import may_use_opinion_queue

    persona = _persona(request)
    return _signed_in(request) and (is_department_head(persona) or may_use_opinion_queue(persona))


def may_sign_in_as_administrator(request: HttpRequest) -> bool:
    """Halduri sisselogimine: under the shared gate, while no verified session is open.

    The same for every reader (security review P3b): a persona's administrative
    standing changes nothing visible. Under personal sign-in the account itself
    is the administrator and the route sends everybody back to Ülevaade.
    """
    from app.accounts import local_auth

    if local_auth.is_local_password() or not admin_session.is_available():
        return False
    return admin_session.verified_administrator(request) is None


# -- Ülevaade --------------------------------------------------------------------------


def _any_administrative(request: HttpRequest) -> bool:
    return any(
        item.visible(request)
        for group in GROUPS
        for item in group.items
        if item.visible is not everybody
        and item.key != "ulevaade"
        and item.visible is not may_sign_in_as_administrator
    )


def may_open_overview(request: HttpRequest) -> bool:
    """Ülevaade: anybody signed in as somebody, and whoever another item opens for.

    Since 2026-10-28 every signed-in persona or account reaches it (the owner's
    brief, Haldus as a gear icon). It shows nothing privileged: each item keeps
    its own predicate here and its own refusal in its view. A shared-gate
    session with no persona reaches it only through a verified administrator
    session, as before (docs/adr/0186) — the pages open to everybody behind the
    gate (Uuendused, Kiirklahvid) and the administrator sign-in do not count.
    """
    if _signed_in(request):
        return True
    return _any_administrative(request)


GROUPS: tuple[Group, ...] = (
    Group(
        "ulevaade",
        "Ülevaade",
        (
            Item(
                "ulevaade",
                "Ülevaade",
                "haldus:index",
                may_open_overview,
                "Mis vajab tähelepanu: hoiatused, liidestuste seis ja otseteed.",
            ),
        ),
    ),
    Group(
        "seaded",
        "Isiklikud seaded",
        (
            Item(
                "kuvamine",
                "Kuvamine",
                "haldus:settings",
                may_open_settings,
                "Hele või tume kujundus, teksti suurus, avaleht ja plokkide vaikeseaded.",
            ),
            Item(
                "teavitused",
                "Teavitused",
                "notifications:settings",
                may_open_notification_settings,
                "Millest ja millal sulle teada antakse.",
                also=("notifications:reset_settings",),
            ),
        ),
    ),
    Group(
        "tookorraldus",
        "Töökorraldus",
        (
            Item(
                "osakond",
                "Osakonna töökorraldus",
                "haldus:osakond",
                may_open_department,
                "Osakonna juhtimisvaade, kolleegide töölauad ja ülevaatust ootavad töövõidud.",
            ),
            Item(
                "vastutajata",
                "Töö ilma vastutajata",
                "account_admin:without_owner",
                may_open_accounts,
                "Väljalülitatud inimeste avatud teemad ja sammud ning vastutajata teemad.",
            ),
        ),
    ),
    Group(
        "andmed",
        "Andmed",
        (
            Item(
                "klassifikaatorid",
                "Klassifikaatorid",
                "reference_data:index",
                may_open_reference_data,
                "Organisatsioonid, sildid ja muud võrdlusandmed.",
                also=(
                    "reference_data:organisations",
                    "reference_data:organisation_create",
                    "reference_data:organisation_duplicates",
                    "reference_data:organisation",
                    "reference_data:organisation_rename",
                    "reference_data:organisation_alias_add",
                    "reference_data:organisation_alias_remove",
                    "reference_data:tags",
                    "reference_data:tag_create",
                    "reference_data:tag",
                    "reference_data:tag_edit",
                    "reference_data:tag_alias_add",
                    "reference_data:tag_alias_remove",
                    "reference_data:tag_merge",
                    "reference_data:tag_status",
                    "reference_data:vocabulary",
                ),
            ),
            Item(
                "kirjad",
                "Ajaloolised kirjad",
                "haldus:letters",
                may_open_letters,
                "Postipoisi väljaminevad kirjad 2008–2026: teemaga sidumine, ettepanekud ja taust.",
                also=("haldus:letters_decide",),
            ),
            Item(
                "arhiiv",
                "Arvamuste arhiiv",
                "legacy_import:opinion_archive_browse",
                may_open_opinion_archive,
                "Varasemad arvamused failidena: otsing, seosed teemadega.",
                also=(
                    "legacy_import:opinion_archive_detail",
                    "legacy_import:opinion_archive_link",
                    "legacy_import:opinion_archive_file",
                ),
            ),
            Item(
                "arvamuste_ulevaatus",
                "Arvamuste ülevaatus",
                "legacy_import:opinion_queue",
                may_open_opinion_queue,
                "Arvamuste sidumine teemadega, mida automaatika ei otsustanud.",
                also=("legacy_import:opinion_decide",),
            ),
            Item(
                "ajaloo_ulevaatus",
                "Ajaloo ülevaatus",
                "legacy_import:review_queue",
                may_open_history_review,
                "OneNote'i ajaloo sidumine teemadega, mida automaatika ei otsustanud.",
                also=("legacy_import:review_decide",),
            ),
            Item(
                "andmekvaliteet",
                "Andmekvaliteet",
                "reporting:quality",
                may_open_reporting_admin,
                "Andmekvaliteedi järjekorrad: puudused ja vastuolud, mida parandada.",
            ),
            Item(
                "ajaloolised_materjalid",
                "Ajaloolised materjalid",
                "reporting:historical",
                may_open_reporting_admin,
                "OneNote'i ajaloolise materjali maht ja ülesehitus.",
            ),
        ),
    ),
    Group(
        "integratsioonid",
        "Integratsioonid",
        (
            Item(
                "lovable",
                "Lovable",
                "haldus:data_sync",
                may_open_system,
                "Lovable'i sünkroonimise seis, kontroll ja käivitus, erinevused, nimed ja ajalugu.",
                also=(
                    "haldus:data_sync_check",
                    "haldus:data_sync_now",
                    "haldus:data_sync_differences",
                    "haldus:data_sync_resolve",
                    "haldus:data_sync_release_hold",
                    "haldus:data_sync_history",
                    "haldus:data_sync_names",
                    "haldus:lovable",
                    "haldus:lovable_check",
                ),
            ),
            Item(
                "onenote",
                "OneNote",
                "haldus:onenote",
                may_open_system,
                "OneNote'i kogumite kontroll ja import ning ootel kirjed.",
                also=(
                    "haldus:data_sync_onenote_verify",
                    "haldus:data_sync_onenote_import",
                    "haldus:onenote_holds",
                ),
            ),
            Item(
                "ai_kasutus",
                "AI kasutus",
                "haldus:ai_usage",
                may_open_system,
                "Mudelipäringud, tokenid ja hinnanguline maksumus; kuu hoiatuspiirid.",
                also=("haldus:ai_usage_csv",),
            ),
            Item(
                "registrid",
                "Registrite jälgimine",
                "haldus:register_watch",
                may_open_register_watch,
                "Öised registrikontrollid: loetud allikad, leitud dokumendid, vead ja AI kasutus.",
                also=("haldus:register_watch_sources", "haldus:register_watch_run"),
            ),
        ),
    ),
    Group(
        "susteem",
        "Süsteem ja õigused",
        (
            Item(
                "kasutajad",
                "Kasutajad ja õigused",
                "account_admin:list",
                may_open_accounts,
                "Kontod, rollid, õigused, kutsed, väljalülitamine ja teise teguri lähtestamine.",
                also=(
                    "account_admin:create",
                    "account_admin:detail",
                    "account_admin:send_link",
                    "account_admin:cancel_invitation",
                    "account_admin:deactivate",
                    "account_admin:reactivate",
                    "account_admin:reset_second_factor",
                    "account_admin:change_email",
                ),
            ),
            Item(
                "susteem",
                "Süsteemi seis",
                "haldus:susteem",
                may_open_system,
                "Versioon, otsinguindeksi värskus ning failitöötluse vead kokkuvõttes.",
            ),
            Item(
                "arendus",
                "Arendusseis",
                "core:development_status",
                may_open_development_status,
                "Uue versiooni ehitamisel lahtiseks jäänud küsimused.",
            ),
            Item(
                "halduri_seanss",
                "Halduri sisselogimine",
                "haldus:sign_in",
                may_sign_in_as_administrator,
                "Halduri seanss oma parooli ja autentimisrakenduse koodiga.",
                also=(
                    "haldus:sign_in_code",
                    "haldus:enrol",
                    "haldus:reauthenticate",
                    "haldus:sign_out",
                ),
            ),
        ),
    ),
    Group(
        "rakendus",
        "Rakendus",
        (
            Item(
                "uuendused",
                "Uuendused",
                "core:release_notes",
                everybody,
                "Juristide rakenduse uuendused ja Lovable'i öiste muudatuste osa.",
                also=("core:release_notes_lovable", "core:release_notes_lovable_export"),
            ),
            Item(
                "kiirklahvid",
                "Kiirklahvid",
                "core:keyboard_shortcuts",
                everybody,
                "Saadaolevad kiirklahvid ja nende kasutamine.",
            ),
            Item(
                "definitsioonid",
                "Statistika definitsioonid",
                "reporting:definitions",
                may_open_reporting_admin,
                "Kuidas iga statistika näitaja arvutatakse.",
            ),
        ),
    ),
)

#: Every item, in sidebar order — the flat registry the older callers read.
SECTIONS: tuple[Item, ...] = tuple(item for group in GROUPS for item in group.items)

_CACHE = "_juristid_haldus_sections"


def visible_sections(request: HttpRequest) -> list[Item]:
    """The items this reader may open, in sidebar order. Cached per request.

    Nothing is visible to a reader who cannot open Ülevaade: the shell is not
    drawn for them, and the pages open to everybody behind the gate do not make
    a persona-less session an administrator.
    """
    cached = getattr(request, _CACHE, None)
    if cached is not None:
        return list(cached)
    items: list[Item] = []
    if may_open_overview(request):
        items = [item for item in SECTIONS if item.visible(request)]
    setattr(request, _CACHE, tuple(items))
    return items


def visible_groups(request: HttpRequest) -> list[VisibleGroup]:
    """The groups with at least one visible item, each with only those items."""
    shown = {item.key for item in visible_sections(request)}
    groups = []
    for group in GROUPS:
        items = tuple(item for item in group.items if item.key in shown)
        if items:
            groups.append(VisibleGroup(group.key, group.label, items))
    return groups


def section(key: str) -> Item:
    return next(item for item in SECTIONS if item.key == key)


def group_of(key: str) -> Group:
    return next(group for group in GROUPS if any(item.key == key for item in group.items))


#: URL name → the item that is its sidebar home.
HOMES: dict[str, Item] = {route: item for item in SECTIONS for route in item.routes}


def home_of(url_name: str) -> Item | None:
    """The item a URL name belongs to, or None for a page outside Haldus."""
    return HOMES.get(url_name)


def active_key(request: HttpRequest, fallback: str = "") -> str:
    """The current item's key, from the resolved URL name; ``fallback`` when it has none."""
    match = getattr(request, "resolver_match", None)
    view_name = getattr(match, "view_name", "") if match is not None else ""
    item = home_of(view_name) if view_name else None
    return item.key if item is not None else fallback
