"""`Töö olek` — the word beside a Teema's title: is the Chamber working on this?

Three questions are kept apart on the file, and this module answers only the
second (docs/adr/0148 §5):

1. **`Hetkeseis`** — where the external legislative process stands. Its own
   control, its own vocabulary, unchanged here.
2. **Work status** — whether the Chamber is working on the file now, is no
   longer working on it, finished it on purpose, or carries the work on under
   another Matter.
3. **The explicit completion** — the Chamber's decision to conclude, recorded
   as a closure `Disposition`.

**Derived, never stored.** Every answer below is read off state the file
already holds — `is_open`, `disposition` and `superseded_by` — so there is no
second, independently editable status that could disagree with the closure it
describes. Reopening a file, closing it through a terminal `Hetkeseis` or naming
a successor changes the badge because it changes those columns, and nothing
else does.

The four answers, and what decides each:

* **Aktiivne** — the file is open. An opinion having been sent does not change
  this: monitoring after a send is ordinary work (docs/adr/0146).
* **Jätkub mujal** — closed because the work continues under another Matter:
  `SUPERSEDED`, which `close_matter` only accepts with a successor.
* **Mitteaktiivne** — closed without the Chamber deciding to conclude: the act
  came into force (`COMPLETED`, which only «Jõustunud» records now), the
  initiator withdrew (`INITIATIVE_WITHDRAWN`), or an archive record closed with
  no reason at all. Nothing here asserts a Chamber closure that did not happen.
* **Lõpetatud** — every other closure: the Chamber stopped monitoring, formed
  no position, finished its response, or closed it for a stated other reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.workflow.enums import Disposition


@dataclass(frozen=True)
class WorkStatus:
    """One answer: a stable key, the word a reader sees, and its style modifier."""

    key: str
    label: str
    modifier: str


ACTIVE = WorkStatus("ACTIVE", "Aktiivne", "active")
INACTIVE = WorkStatus("INACTIVE", "Mitteaktiivne", "inactive")
CONCLUDED = WorkStatus("CONCLUDED", "Lõpetatud", "concluded")
CONTINUES = WorkStatus("CONTINUES", "Jätkub mujal", "continues")

WORK_STATUSES: tuple[WorkStatus, ...] = (ACTIVE, INACTIVE, CONCLUDED, CONTINUES)

#: Closures that end the Chamber's active work without being its decision to
#: conclude. The empty string is an archive record closed with no reason, which
#: the closure constraint permits and which says nothing about a decision.
NOT_A_CHAMBER_DECISION: frozenset[str] = frozenset(
    {"", Disposition.COMPLETED.value, Disposition.INITIATIVE_WITHDRAWN.value}
)


def work_status_of(matter: Any) -> WorkStatus:
    """The badge for this Matter, from the columns it already holds."""
    if matter.is_open:
        return ACTIVE
    disposition = matter.disposition or ""
    if getattr(matter, "superseded_by_id", None) or disposition == Disposition.SUPERSEDED:
        return CONTINUES
    if disposition in NOT_A_CHAMBER_DECISION:
        return INACTIVE
    return CONCLUDED
