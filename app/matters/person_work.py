"""The person workspace, and the one private thing on it.

`/inimesed/<id>/asjad/` is the same page as Minu asjad, read about somebody
else. It exists because clicking a colleague on the department page used to open the
register filtered by owner, and a register row answers "what is this Matter",
not "what is on this person's desk" (design handoff, Minu asjad §A).

Two rules this module exists to hold, and neither is a matter of taste.

**The gate is the one that already exists.** Self, or the department head, and
otherwise **404** rather than 403 — the convention `department_views` and
`get_visible_matter` already follow, because a 403 confirms the page is there
and that somebody else may read it. No new entitlement is created: a department
head can already see this work on Osakond and in the register.

**The scratchpad is not part of the workspace at all.** It is fetched by the
*self* view only, written only through an endpoint that reads `request.user`,
and there is no parameter anywhere in this module that can name whose notes to
load. The manager's response does not hide the block — it does not contain it
(01-EHITUSJUHIS §3.5, §8; 03-BACKEND §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet

from app.accounts.models import User
from app.accounts.selectors import department_workers
from app.core.authorization import is_department_head
from app.matters.models import PersonalScratchpad

#: How much a person may keep on the desk pad. Generous, and a bound: an
#: unbounded text field behind an autosave is a way to fill a table by holding
#: a key down.
SCRATCHPAD_MAX_LENGTH = 20_000


def may_open_person_work(user: Any, subject: Any) -> bool:
    """Self, or the department head. Nothing else, and nothing new.

    `is_staff` is deliberately absent: technical administration is not business
    access, and this page is a colleague's whole queue (AGENTS.md).
    """
    if user is None or getattr(user, "pk", None) is None:
        return False
    if getattr(subject, "pk", None) == user.pk:
        return True
    return is_department_head(user)


def resolve_subject(raw_id: Any) -> User | None:
    """Whose desk this is, by id and never by name.

    Former colleagues resolve. A person who has left but still owns open work
    is exactly who a department head needs to look at, and taking their page
    away would take that work off every screen (design handoff, Minu asjad §I).
    """
    return User.objects.filter(pk=raw_id).first()


def switcher_people() -> QuerySet[User]:
    """Who the ‹ ▾ › control walks through.

    `department_workers()` — active caseworkers, ordered by display name, which
    is the order the Osakond team table is in. Walking the two lists must
    feel like walking one list, so they are one query rather than two orderings
    that agree today (docs/adr/0036; `01-EHITUSJUHIS` §9.1 leaves the choice
    open and this is the prototype's answer).

    A former colleague is deliberately not in it: their page still opens by URL,
    but a switcher that offers people who have left is a switcher that grows
    forever.
    """
    return department_workers()


@dataclass(frozen=True)
class Switcher:
    """The person control: who is shown, who is either side, and who is who."""

    current: User
    people: list[User]
    previous: User | None
    following: User | None


def build_switcher(subject: User) -> Switcher:
    people = list(switcher_people())
    ids = [person.pk for person in people]
    if subject.pk in ids:
        index = ids.index(subject.pk)
        previous = people[index - 1] if index > 0 else people[-1] if len(people) > 1 else None
        following = people[(index + 1) % len(people)] if len(people) > 1 else None
    else:
        # A former colleague, reached by URL. The list still walks the active
        # people; there is simply no position to step from.
        previous = following = None
    return Switcher(current=subject, people=people, previous=previous, following=following)


# ---------------------------------------------------------------------------
# Märkmed
# ---------------------------------------------------------------------------


def scratchpad_for(user: Any) -> PersonalScratchpad | None:
    """This person's own notepad, or nothing if they have never written one.

    Takes a user, not an id, and is called with `request.user` in exactly one
    place. There is no lookup here that a URL could reach.
    """
    if user is None or getattr(user, "pk", None) is None:
        return None
    return PersonalScratchpad.objects.filter(user=user).first()


def save_scratchpad(user: Any, body: str) -> PersonalScratchpad:
    """Write the signed-in person's own notepad. There is no other signature.

    Deliberately not `save_scratchpad(subject, body)`. A subject parameter is
    all it would take to turn an autosave endpoint into a way to write into
    somebody else's private notes, so the parameter does not exist — the
    refusal is structural rather than a check that could be edited out
    (03-BACKEND §2).
    """
    text = (body or "")[:SCRATCHPAD_MAX_LENGTH]
    row, _ = PersonalScratchpad.objects.update_or_create(user=user, defaults={"body": text})
    return row
