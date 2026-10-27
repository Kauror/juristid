"""The Haldus shell's section registry: which parts of Haldus this reader may open.

Each section says who may see it with a predicate over the request, and the
sub-navigation, the landing page and the top-bar link are all built from the
same predicates — so the shell can never offer a section it would refuse.
Showing is not authorizing: every route behind a section asks its own question
again and refuses a crafted URL on its own (docs/adr/0186).

Two kinds of standing are read here, and kept apart:

* **the persona** (`request.user`) — what a department head's work-management
  surfaces have always followed, under every mode;
* **the verified administrator** (`admin_session.verified_administrator`) —
  account administration and system status, never the persona.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse

from app.accounts import admin_session
from app.core.authorization import may_view_department_management


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    url_name: str
    visible: Callable[[HttpRequest], bool]
    #: One line on the landing page: what the section is for.
    description: str = ""

    def url(self) -> str:
        return reverse(self.url_name)


def _route_exists(url_name: str) -> bool:
    try:
        reverse(url_name)
    except NoReverseMatch:
        return False
    return True


def _persona(request: HttpRequest) -> object | None:
    return getattr(request, "user", None)


def may_open_accounts(request: HttpRequest) -> bool:
    return admin_session.may_administer_accounts(request)


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


def may_open_department(request: HttpRequest) -> bool:
    """Osakonna töökorraldus: whoever holds the department management view."""
    return may_view_department_management(_persona(request))


def may_open_system(request: HttpRequest) -> bool:
    """Süsteemi seis: a verified technical administrator, and nobody else."""
    return admin_session.is_technical_administrator(request)


def _any_other(request: HttpRequest) -> bool:
    return any(section.visible(request) for section in SECTIONS if section.key != "ulevaade")


SECTIONS: tuple[Section, ...] = (
    Section(
        "ulevaade",
        "Ülevaade",
        "haldus:index",
        _any_other,
        "Mida sa siin teha saad ja kas haldus on tagatud.",
    ),
    Section(
        "kasutajad",
        "Kasutajad ja õigused",
        "account_admin:list",
        may_open_accounts,
        "Kontod, rollid, õigused, kutsed, väljalülitamine ja teise teguri lähtestamine.",
    ),
    Section(
        "klassifikaatorid",
        "Klassifikaatorid",
        "reference_data:index",
        may_open_reference_data,
        "Organisatsioonid, sildid ja muud võrdlusandmed.",
    ),
    Section(
        "osakond",
        "Osakonna töökorraldus",
        "haldus:osakond",
        may_open_department,
        "Osakonna juhtimisvaade, kolleegide töölauad ja ülevaatust ootavad töövõidud.",
    ),
    Section(
        "susteem",
        "Süsteemi seis",
        "haldus:susteem",
        may_open_system,
        "Versioon, otsinguindeksi värskus ning failitöötluse vead kokkuvõttes.",
    ),
    Section(
        "lovable",
        "Lovable sünkroonimine",
        "haldus:lovable",
        may_open_system,
        "Öise Lovable'i sünkroonimise seis, viimased tulemused ja ülevaatust ootavad muudatused.",
    ),
)

_CACHE = "_juristid_haldus_sections"


def visible_sections(request: HttpRequest) -> list[Section]:
    """The sections this reader may open, in registry order. Cached per request."""
    cached = getattr(request, _CACHE, None)
    if cached is not None:
        return list(cached)
    sections = [section for section in SECTIONS if section.visible(request)]
    setattr(request, _CACHE, tuple(sections))
    return sections


def section(key: str) -> Section:
    return next(item for item in SECTIONS if item.key == key)
