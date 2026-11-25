"""Where `/` opens for one person: their chosen landing page, safely (docs/adr/0221).

A choice from a fixed allowlist of existing destinations — never a stored URL —
so it cannot be an open redirect. It is checked against what the person may
open twice: when it is saved (`save`) and at every redirect (`landing_url`),
because a permission can change after the choice. Anything not allowed, not
known or not chosen falls back to Minu asjad, the default since Stage 2D
(`core.views.home`). An explicit `next` always wins over it: the callers ask
for it only when there is none.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.urls import reverse

from app.core.errors import DomainError

DEFAULT = "minu-asjad"


def _signed_in(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False))


def _monitooring(user: Any) -> bool:
    from app.register_watch import candidate_notices

    return _signed_in(user) and bool(candidate_notices.may_see(user))


@dataclass(frozen=True)
class LandingPage:
    key: str
    label: str
    url_name: str
    #: The route's own question, asked of the person (`login_required`, and
    #: Monitooring's audience on top).
    allowed: Callable[[Any], bool] = _signed_in

    def url(self) -> str:
        return reverse(self.url_name)


#: The allowlist, in the order the choice offers it.
LANDING_PAGES: tuple[LandingPage, ...] = (
    LandingPage(DEFAULT, "Minu asjad", "matters:my_work"),
    LandingPage("teemad", "Teemad", "matters:matter_list"),
    LandingPage("osakond", "Osakond", "matters:department"),
    LandingPage("statistika", "Statistika", "reporting:dashboard"),
    LandingPage("monitooring", "Monitooring", "register_watch:candidates", _monitooring),
    LandingPage("teavitused", "Teavitused", "notifications:list"),
)

_BY_KEY = {page.key: page for page in LANDING_PAGES}


class LandingRefused(DomainError):
    """A landing page that is not on the list, or not this person's to open."""


def choices(user: Any) -> list[LandingPage]:
    """The pages this person may choose now."""
    return [page for page in LANDING_PAGES if page.allowed(user)]


def chosen(user: Any) -> str:
    """The stored key, or the default; never checked here — `landing_page` checks."""
    if not _signed_in(user):
        return DEFAULT
    from app.accounts.models import PersonalPreference

    stored = (
        PersonalPreference.objects.filter(user=user).values_list("landing_page", flat=True).first()
    )
    return stored or DEFAULT


def landing_page(user: Any) -> LandingPage:
    """The page `/` opens for this person now: their choice if still allowed, else Minu asjad."""
    page = _BY_KEY.get(chosen(user))
    if page is None or not page.allowed(user):
        return _BY_KEY[DEFAULT]
    return page


def landing_url(user: Any) -> str:
    return landing_page(user).url()


def save(user: Any, key: str) -> LandingPage:
    """Store ``key`` as this person's landing page, after checking it is theirs to open."""
    page = _BY_KEY.get(str(key))
    if page is None:
        raise LandingRefused("Sellist avalehte ei saa valida.")
    if not page.allowed(user):
        raise LandingRefused("Seda lehte ei saa sulle avalehena valida.")
    from app.accounts.models import PersonalPreference

    PersonalPreference.objects.update_or_create(
        user=user, defaults={"landing_page": "" if page.key == DEFAULT else page.key}
    )
    return page
