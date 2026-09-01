"""What to call a colleague in a list, when a first name is not enough.

The department addresses each other by first name, and the compact UI is built
on that: a row of chips reading *Ireen · Martin · Sandra* is read at a glance
where full names are read one at a time. That stays.

What the pilot found is what happens when two people share one. Two `Sandra`
rows in a Vastutaja picker are two identical-looking answers to a question with
one right answer, and the wrong one assigns a file to the wrong lawyer and sends
the notice to the wrong desk (pilot QA F-03).

**Disambiguate within the list, not globally.** Turning every `Sandra` into
`Sandra Tamm <sandra.tamm@…>` everywhere would pay the cost of a collision that
usually does not exist. The question is only ever "can the reader tell these
particular rows apart", so the answer is computed over the population actually
being offered: with one Sandra in it, she is `Sandra`.

**Three steps, each taken only when the one before it is not enough.**

1. the short name — what the person is called;
2. the full display name, for everybody whose short name is shared;
3. the account's own name — the local part of the UPN, and the whole UPN if
   even that repeats — for anybody two of whom are called exactly the same
   thing. A readable account identity rather than a UUID: `Sandra Tamm
   (sandra.tamm)` tells a colleague which mailbox it is, and a row of hex tells
   them nothing.

Only a person whose *own* name is ambiguous is lengthened. One Sandra Tamm and
one Sandra Kask beside Martin Saar render as `Sandra Tamm`, `Sandra Kask` and
`Martin` — Martin's name was never in doubt and does not grow because somebody
else's was.

**Never an identifier.** This decides what is *shown*. What is submitted stays
the user's immutable id, everywhere, so nothing in the application ever selects
a person by the name on a chip.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any


def _short(user: Any) -> str:
    return (user.get_short_name() or "").strip() or (user.upn or "").strip()


def _full(user: Any) -> str:
    return (getattr(user, "display_name", "") or "").strip() or _short(user)


def _account(user: Any) -> str:
    """The readable half of the account name — `sandra.tamm` of `sandra.tamm@…`."""
    upn = (getattr(user, "upn", "") or "").strip()
    return upn.split("@", 1)[0] if upn else ""


def disambiguated_names(people: Iterable[Any], *, start_full: bool = False) -> dict[Any, str]:
    """The shortest label that tells each of ``people`` apart from the rest.

    Keyed by primary key, so a caller renders `labels[person.pk]` and never has
    to hold the objects in the same order twice. A person who appears twice in
    the input counts once: the same account offered under two headings is not a
    collision.

    ``start_full`` begins the ladder at the display name instead of the short
    one, for the surfaces that already show full names — `Vali kasutaja` is a
    page for choosing *who you are*, and shortening it to first names to save
    space would be a worse answer to the same question. Those surfaces still get
    the last rung when two people are called exactly the same thing.
    """
    unique: dict[Any, Any] = {}
    for person in people:
        if person is not None and person.pk not in unique:
            unique[person.pk] = person

    labels: dict[Any, str] = {}
    ambiguous: list[Any] = list(unique.values())
    if not start_full:
        shorts = Counter(_short(person) for person in unique.values())
        ambiguous = []
        for pk, person in unique.items():
            short = _short(person)
            if shorts[short] > 1:
                ambiguous.append(person)
            else:
                labels[pk] = short

    if not ambiguous:
        return labels

    fulls = Counter(_full(person) for person in ambiguous)
    still: list[Any] = []
    for person in ambiguous:
        full = _full(person)
        if fulls[full] > 1:
            still.append(person)
        else:
            labels[person.pk] = full

    if not still:
        return labels

    # Two accounts whose display names are identical. The account name is the
    # smallest stable thing left that a person can read; the full UPN is what
    # remains when even that repeats across domains, and it is unique by
    # construction.
    accounts = Counter(_account(person) for person in still)
    for person in still:
        account = _account(person)
        tail = account if account and accounts[account] == 1 else (person.upn or str(person.pk))
        labels[person.pk] = f"{_full(person)} ({tail})"

    return labels


def name_among(person: Any, people: Iterable[Any], *, start_full: bool = False) -> str:
    """One person's label within ``people``. The template's way in."""
    if person is None:
        return ""
    labels = disambiguated_names([*people, person], start_full=start_full)
    return labels.get(person.pk, _full(person) if start_full else _short(person))
