"""«Minu vaated» — a person's own named Teemad views (UX-007, docs/adr/0222).

docs/adr/0043 §2 kept every view in the address and stored nothing. That still
holds for what a view *is*: a register address. What this module adds is a
person keeping up to :data:`MAX_VIEWS` of those addresses under names of their
own, beside the four predefined chips (`register_filters.saved_views`), which
are unchanged.

Five rules, each answering one of the questions ADR 0043 asked:

* **Ownership.** Every read and write is keyed on the signed-in person
  (`request.user`: the selected persona under the shared gate). A view id that
  is not the sender's own is «not found», never «forbidden».
* **Sharing.** A view is never shared; its *address* is, exactly as before —
  «Kopeeri» still hands over the link, and the colleague who opens it sees it
  through their own visibility.
* **Naming.** 1–60 characters after trimming, no control characters, unique per
  person regardless of case.
* **Staleness.** Only parameters the register reads are stored, canonically
  (sorted, de-duplicated, blanks and `leht` dropped). On every read the stored
  query is filtered through the same list again, so a parameter a later release
  retires is dropped when the view is opened rather than breaking it; the chip
  says so in its tooltip.
* **What is never stored.** A result set, a count, a Matter id list or anything
  authorization decided. Opening a view is the ordinary register request for
  whoever opens it, and the chip's count comes from the register's own pipeline
  (`register_selection`) for that reader, today.

**One filtering engine.** Counting goes through `register_selection.selection_from`
→ `searched` → `filtered` (`apply_register_filters`) — the path the register's
own table and its Excel export read — so a chip reading 12 opens twelve rows.
Bounded: one aggregate statement for all of a person's views, plus the search's
own two reads (word normalisation, search index) per view that carries free
text and at most one shared read of the work model for every `?too=` view
(tests/test_saved_register_views.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.db.models.functions import Lower
from django.http import QueryDict
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.models import Matter, SavedRegisterView
from app.matters.register_filters import _current_view_params, as_text

#: The most views one person keeps (owner decision BLOCK-1, 2026-11-24).
MAX_VIEWS = 20

#: The longest name, in characters, after trimming.
MAX_NAME_LENGTH = 60

#: The longest canonical query kept. A register address is a few hundred
#: characters; anything longer was not written by this site's own controls.
MAX_QUERY_LENGTH = 2000

#: Chips drawn in the strip before the rest wait behind «Minu vaated ▾».
VISIBLE_CHIPS = 6

#: Of those, the ones a narrow screen (≤ 720 px, static/css/ux.css) still draws.
#: Three: at 375 px the four predefined chips already take two lines, and three
#: own chips, «Minu vaated» and «Salvesta» keep the strip to about five lines
#: above the list's first row; the rest are one tap away in «Minu vaated».
NARROW_CHIPS = 3

#: The database's own guard on one name per person (app/matters/models.py).
NAME_CONSTRAINT = "matters_savedview_user_name"

#: Parameters that are never part of a view: the page a reader happened to be on.
_NEVER_STORED = frozenset({"leht"})


def known_params() -> frozenset[str]:
    """Every parameter the register reads: filters, status, sort, search, columns, page size.

    Read from the register's own lists rather than restated, so a filter added to
    `FILTER_LABELS` is savable the day it exists and one removed from it is
    dropped from every stored view the next time that view is read.
    """
    from app.matters.register_columns import COLUMNS_PARAM
    from app.matters.views import PAGE_SIZE_PARAM, REGISTER_PARAMS

    return frozenset((*REGISTER_PARAMS, COLUMNS_PARAM, PAGE_SIZE_PARAM)) - _NEVER_STORED


def canonical_pairs(params: Any) -> tuple[list[tuple[str, str]], list[str]]:
    """The register parameters of ``params`` in canonical order, and the names dropped.

    Sorted by name, each name's values sorted and de-duplicated, blanks left out
    (`register_filters._comparable`'s reading of "the same view"). `olek` is the
    one parameter whose blank is a choice — «every state» — so it is kept.
    """
    text = as_text(params)
    known = known_params()
    pairs: list[tuple[str, str]] = []
    dropped: list[str] = []
    for name in sorted(text):
        values = sorted({value.strip() for value in text.getlist(name)})
        if name not in known:
            if name not in _NEVER_STORED and any(values):
                dropped.append(name)
            continue
        kept = [value for value in values if value] or ([""] if name == "olek" else [])
        pairs.extend((name, value) for value in kept)
    return pairs, dropped


def canonical_query(params: Any) -> str:
    """``params`` as the query string a view stores and its chip links to."""
    pairs, _ = canonical_pairs(params)
    return urlencode(pairs)


@dataclass(frozen=True)
class PersonalView:
    """One of a person's views as the chip strip draws it."""

    pk: Any
    name: str
    query: str
    count: int
    active: bool
    #: Parameter names the stored view carried that the register no longer reads.
    dropped: tuple[str, ...] = ()


def _clean_name(raw: Any) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise DomainError("Anna vaatele nimi.")
    if len(name) > MAX_NAME_LENGTH:
        raise DomainError(f"Vaate nimi võib olla kuni {MAX_NAME_LENGTH} märki.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
        raise DomainError("Vaate nimes ei saa olla juhtmärke.")
    return name


def _name_taken(user: Any, name: str, *, exclude: Any = None) -> bool:
    taken = SavedRegisterView.objects.filter(user=user).annotate(lowered=Lower("name"))
    if exclude is not None:
        taken = taken.exclude(pk=exclude)
    return taken.filter(lowered=name.lower()).exists()


def _name_taken_refusal(name: str) -> DomainError:
    return DomainError(f"Vaade nimega «{name}» on sul juba olemas.")


def _lock_person(user: Any) -> None:
    """Serialise one person's view writes on their own user row.

    Two tabs saving, or renaming to the same name, at once queue here, so the
    second one's count and name check read what the first one committed
    (READ COMMITTED: each statement after the lock sees the new rows).
    """
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()


def _write_name(user: Any, name: str, write: Any) -> SavedRegisterView:
    """Run ``write`` in a savepoint; a name the database refuses is a refusal in words.

    The person lock makes this unreachable through this module; the savepoint
    is for any writer that skips it, so a clash is never an unhandled
    `IntegrityError` and never spoils the caller's transaction.
    """
    try:
        with transaction.atomic():
            return write()
    except IntegrityError as error:
        cause = getattr(error, "__cause__", None)
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        if constraint != NAME_CONSTRAINT:
            raise
        raise _name_taken_refusal(name) from error


def save_view(*, user: Any, name: Any, params: Any) -> SavedRegisterView:
    """Keep the register address ``params`` under ``name`` for ``user``.

    Refused, with the reason in words: an empty, too long or duplicate name, a
    person already at :data:`MAX_VIEWS`, or an address with nothing the register
    reads (`?leht=3` alone is not a view). The person's own row is locked for the
    count and the name check, so two tabs saving at once cannot pass the cap or
    take one name together (tests/test_saved_register_views_concurrency.py).
    """
    clean = _clean_name(name)
    query = canonical_query(params)
    if not query:
        raise DomainError("See on Teemade vaikevaade. Vali enne salvestamist mõni filter.")
    if len(query) > MAX_QUERY_LENGTH:
        raise DomainError("Selle vaate aadress on salvestamiseks liiga pikk.")
    with transaction.atomic():
        _lock_person(user)
        mine = SavedRegisterView.objects.filter(user=user)
        if mine.count() >= MAX_VIEWS:
            raise DomainError(
                f"Sul on juba {MAX_VIEWS} vaadet. Eemalda mõni, enne kui uue salvestad."
            )
        if _name_taken(user, clean):
            raise _name_taken_refusal(clean)
        last = mine.aggregate(last=Max("position"))["last"]
        return _write_name(
            user,
            clean,
            lambda: SavedRegisterView.objects.create(
                user=user,
                name=clean,
                query=query,
                position=0 if last is None else last + 1,
            ),
        )


def own_view(user: Any, pk: Any) -> SavedRegisterView | None:
    """The view ``pk`` if it is ``user``'s own, else ``None`` — the caller answers 404."""
    return SavedRegisterView.objects.filter(user=user, pk=pk).first()


def rename_view(*, view: SavedRegisterView, name: Any) -> SavedRegisterView:
    """Give one of the person's own views a new name. Its filters are untouched.

    Under the same person lock as `save_view`: two renames to one name at once
    end with one renamed view and one refusal in words, never a database error.
    """
    clean = _clean_name(name)
    with transaction.atomic():
        _lock_person(view.user)
        if not SavedRegisterView.objects.filter(pk=view.pk).exists():
            raise DomainError("Seda vaadet enam ei ole.")
        if clean != view.name and _name_taken(view.user, clean, exclude=view.pk):
            raise _name_taken_refusal(clean)
        view.name = clean

        def write() -> SavedRegisterView:
            view.save(update_fields=["name", "updated_at"])
            return view

        return _write_name(view.user, clean, write)


def remove_view(*, view: SavedRegisterView) -> None:
    """Forget one of the person's own views. Nothing else changes."""
    view.delete()


def stored_query(view: SavedRegisterView) -> tuple[str, tuple[str, ...]]:
    """The view's query as the register reads it today, and what no longer applies."""
    pairs, dropped = canonical_pairs(QueryDict(view.query))
    return urlencode(pairs), tuple(dropped)


def personal_views(user: Any, params: Any, *, today: date | None = None) -> list[PersonalView]:
    """The person's views, counted for them now, with the one they are on marked.

    Counted through the register's own selection pipeline in **one aggregate
    statement** — `COUNT(*) FILTER (WHERE id IN (<view i>))` per view over the
    reader's visible Matters — rather than a query per chip. The work model a
    `?too=` view narrows by is read once and shared, the way Ülevaade shares it.
    """
    from app.matters import register_selection
    from app.matters import work_items as wi

    views = list(SavedRegisterView.objects.filter(user=user))
    if not views:
        return []
    today = today or timezone.localdate()
    current = _current_view_params(params)
    population = Matter.objects.visible_to(user)
    readable = [(view, *stored_query(view)) for view in views]
    shared_items = (
        wi.work_items(user, today=today)
        if any("too" in QueryDict(query) for _, query, _ in readable)
        else None
    )
    counts: dict[str, Count] = {}
    for index, (_view, query, _dropped) in enumerate(readable):
        selection = register_selection.selection_from(user, QueryDict(query))
        narrowed, _echo = selection.filtered(
            selection.searched(population), today=today, shared_items=shared_items
        )
        counts[f"view_{index}"] = Count("pk", filter=Q(pk__in=narrowed.values("pk")))
    totals = population.aggregate(**counts)
    return [
        PersonalView(
            pk=view.pk,
            name=view.name,
            query=query,
            count=int(totals[f"view_{index}"] or 0),
            active=current == _current_view_params(QueryDict(query)),
            dropped=dropped,
        )
        for index, (view, query, dropped) in enumerate(readable)
    ]
