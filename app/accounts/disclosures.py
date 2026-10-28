"""Remembered blocks — which informational accordions a person keeps open.

One mechanism for the whole application (docs/adr/0194). A stable informational
block — *Minu statistika*, a window of Osakond's *Eesolev*, the definitions under
a Statistika chart — opens the way its reader last left it, on any device. A
temporary disclosure — `Lisa tegevus`, `Muuda`, a menu, a confirmation, an
error that has to open by itself — is not registered here and keeps behaving as
it always has: its state is the operation's, not a preference.

**The registry is the boundary.** Every remembered block has a semantic key
written below, never a position (`accordion-3`) and never a value that grows
with the data (a Matter's id, a day). The endpoint refuses any other key, so a
person can never hold more rows than this tuple has entries, and a crafted
request cannot fill the table.

**Whose preference.** The person the request is signed in as: the persona the
shared gate selected, or the account under personal sign-in. Never an id from
the request — there is no parameter that names somebody else — and never read
by authorization. Choosing Marko's desk at the shared gate and folding a block
changes how Marko's desk folds: that is the approved shared-workstation
behaviour, and it is presentation state only.

**Only an explicit choice is stored.** A block nobody touched has no row and
renders its registered default, so a default changed in a later release still
reaches everybody who never decided otherwise. «Taasta plokkide vaikeseaded»
deletes the person's rows and nothing else.

**One read per page.** The first block a template renders reads the person's
rows once (a handful at most) and keeps them on the request; every later block
on the page, and every one in an HTMX fragment of it, uses that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest

#: The attribute the request carries its person's stored choices on.
_CACHE_ATTRIBUTE = "_disclosure_preferences"


@dataclass(frozen=True)
class Disclosure:
    key: str
    #: Whether the block is open for somebody who has not chosen.
    default_open: bool
    #: Where it is, in words — for the inventory and for a reviewer.
    where: str


#: Every remembered block. Adding one is a line here and an attribute on its
#: `<details>` (`{% disclosure "key" %}`); nothing else.
DISCLOSURES: dict[str, Disclosure] = {
    item.key: item
    for item in (
        # Osakond → Eesolev: the six date windows (app/matters/department_dashboard.py).
        Disclosure("department.upcoming.today", True, "Osakond · Eesolev · Täna"),
        Disclosure("department.upcoming.tomorrow", True, "Osakond · Eesolev · Homme"),
        Disclosure(
            "department.upcoming.remaining_week",
            True,
            "Osakond · Eesolev · Käesoleva nädala ülejäänud päevad",
        ),
        Disclosure("department.upcoming.next_week", True, "Osakond · Eesolev · Järgmine nädal"),
        Disclosure("department.upcoming.rest_month", False, "Osakond · Eesolev · Ülejäänud kuu"),
        Disclosure("department.upcoming.far", False, "Osakond · Eesolev · Kaugemal"),
        # Minu asjad.
        Disclosure("my_work.statistics", True, "Minu asjad · Minu statistika"),
        Disclosure("my_work.recent_activity", True, "Minu asjad · Viimased tegevused"),
        # Teema → Dokumendid.
        Disclosure("matter_documents.working", False, "Teema · Dokumendid · Töödokumendid"),
        Disclosure("matter_documents.archive", False, "Teema · Dokumendid · Seotud arhiivikirjad"),
        # Dokument.
        Disclosure("document.data", False, "Dokument · Dokumendi andmed"),
        # Statistika. One key per *kind* of explanation rather than per chart:
        # «show me the definitions» is the preference a reader has, and a key
        # per metric would grow with the catalogue.
        Disclosure(
            "reporting.overview.definitions", False, "Statistika · Mida need arvud tähendavad"
        ),
        Disclosure("reporting.definition", False, "Statistika · Definitsioon (iga arvu all)"),
        Disclosure("reporting.chart_table", False, "Statistika · Joonise andmed tabelina"),
    )
}


def is_registered(key: str) -> bool:
    return key in DISCLOSURES


def _person(request: HttpRequest) -> Any | None:
    """The person whose preferences this request reads and writes, or ``None``.

    `request.user` behind `login_required` is the selected persona under the
    shared gate and the account under personal sign-in — the same principal
    every other view authorizes. Anybody else (no persona yet, signed out) has
    no preferences: the defaults, and nothing to write.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def stored_choices(request: HttpRequest) -> dict[str, bool]:
    """This request's person's explicit choices, read once per request."""
    cached = getattr(request, _CACHE_ATTRIBUTE, None)
    if cached is not None:
        return cached
    from app.accounts.models import DisclosurePreference

    person = _person(request)
    choices: dict[str, bool] = {}
    if person is not None:
        choices = dict(
            DisclosurePreference.objects.filter(user=person).values_list("key", "is_open")
        )
    setattr(request, _CACHE_ATTRIBUTE, choices)
    return choices


def is_open(request: HttpRequest | None, key: str) -> bool:
    """Whether ``key`` renders open: the stored choice, else the default.

    An unregistered key is a programming error a test catches; at runtime it
    renders closed rather than failing the page.
    """
    disclosure = DISCLOSURES.get(key)
    if disclosure is None:
        return False
    if request is None:
        return disclosure.default_open
    return stored_choices(request).get(key, disclosure.default_open)


def remember(person: Any, key: str, *, opened: bool) -> None:
    """Store one explicit choice; the last write for a key wins.

    One statement, ``INSERT … ON CONFLICT DO UPDATE``, so two quick toggles
    arriving together cannot fail on the unique constraint or leave a second
    row.
    """
    from app.accounts.models import DisclosurePreference

    if key not in DISCLOSURES:
        raise ValueError(f"Unregistered disclosure key: {key!r}")
    DisclosurePreference.objects.bulk_create(
        [DisclosurePreference(user=person, key=key, is_open=opened)],
        update_conflicts=True,
        unique_fields=["user", "key"],
        update_fields=["is_open", "updated_at"],
    )


def reset(person: Any) -> int:
    """Forget every choice this person made; every block returns to its default."""
    from app.accounts.models import DisclosurePreference

    deleted, _ = DisclosurePreference.objects.filter(user=person).delete()
    return deleted
