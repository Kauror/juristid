"""What changed in Juristid, written for the people who use it.

One entry per calendar day, in one file in version control:
``docs/release-notes/uuendused.toml``. Not a database table, not a
project-management feature, and not the Git log — the surface that renders it
(``/uuendused/``) answers one question, *"what is different for me when I use
Juristid"*, and a reader who wanted the commit history would not be on it.

**A day, not a deployment.** Several production releases on one calendar day are
one entry. The day a change reaches production is the day it became available to
somebody; which of that day's three images carried it is an operational fact
with no reader (``docs/release-notes/README.md``).

**Plain text, never markup.** A bullet is a sentence somebody typed into a TOML
file. It is rendered through Django's ordinary autoescaping, so a typo cannot
become an XSS surface and an ampersand cannot become an entity.

TOML for the same two reasons the era contracts are TOML
(``app/legacy_import/contracts.py``): it is read by people at least as often as
by code, and ``tomllib`` is in the standard library, so nobody has to install
anything to check what the page will say.

The file is parsed once per process and held. It describes the image the process
was started from — it cannot change while the process lives — and the page it
feeds therefore costs no database query and no network call at all.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

from app.workflow.enums import ESTONIAN_MONTHS

#: How the day an entry names was established, most reliable first. Read by a
#: maintainer, never rendered: a lawyer has no use for the distinction, and
#: printing it would put the deployment machinery back on a page that exists to
#: keep it off.
#:
#: ``deployment``    — a release report, a production snapshot or a recorded
#:                     smoke test names that revision live on that day;
#: ``release-image`` — the production image for that revision was built for
#:                     deployment that day
#:                     (``.github/workflows/release-image.yml``), which is the
#:                     release channel and is used for nothing else;
#: ``merge``         — a backfill fallback only. The day the change reached
#:                     ``main``, used where no deployment day could be
#:                     established at all. New entries never use it.
BASES: frozenset[str] = frozenset({"deployment", "release-image", "merge"})


class ReleaseNotesError(Exception):
    """The release-note source is missing, malformed or contradicts itself.

    Raised rather than swallowed. A page that quietly renders the half of the
    history it could parse is worse than one that does not render at all: the
    reader cannot tell a quiet fortnight from a broken file, and neither can the
    person who broke it.
    """


@dataclass(frozen=True)
class ReleaseDay:
    """One calendar day's worth of changes, as a reader sees them."""

    date: date
    changes: tuple[str, ...]

    @property
    def heading(self) -> str:
        """``9. september 2026``.

        Spelled out rather than ``9.9.2026``. This is a group heading over a
        list — the same shape as the month headings the deadline surfaces
        already write — and not a date on a Matter, which is what
        ``app/core/dates.py``'s one-format rule is about. The month names come
        from the application's own vocabulary rather than from Django's locale
        data, so the page reads the same wherever the process runs.
        """
        return f"{self.date.day}. {ESTONIAN_MONTHS[self.date.month - 1]} {self.date.year}"

    @property
    def count(self) -> int:
        return len(self.changes)

    @property
    def count_label(self) -> str:
        """``1 uuendus``, ``7 uuendust``.

        Estonian takes the partitive singular after every number but one, so
        this is two forms rather than a plural rule.
        """
        return f"{self.count} uuendus" if self.count == 1 else f"{self.count} uuendust"


def release_notes_path() -> Path:
    return Path(settings.BASE_DIR) / "docs" / "release-notes" / "uuendused.toml"


def _require(raw: dict[str, Any], key: str, where: str) -> Any:
    if key not in raw:
        raise ReleaseNotesError(f"{where}: required key {key!r} is missing.")
    return raw[key]


def parse_release_notes(text: str, *, source: str = "uuendused.toml") -> tuple[ReleaseDay, ...]:
    """Every day the source describes, newest first.

    Ordered here rather than in the file. Hand-maintained order is a rule
    somebody has to remember at the exact moment they are thinking about
    wording, and getting it wrong would drop a new entry silently into the
    middle of the page.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ReleaseNotesError(f"{source}: {error}") from error

    days: list[ReleaseDay] = []
    seen: dict[date, int] = {}
    for index, entry in enumerate(_require(raw, "day", source)):
        where = f"{source}: day #{index + 1}"
        if not isinstance(entry, dict):
            raise ReleaseNotesError(f"{where} is not a table.")

        day = _parse_date(_require(entry, "date", where), where)
        if day in seen:
            # The whole point of the format. Two tables for one day are two
            # accordions for one day, which is the arrangement this page exists
            # in order not to have.
            raise ReleaseNotesError(
                f"{where}: {day.isoformat()} is already described by day "
                f"#{seen[day] + 1}. One entry per day — put the changes together."
            )
        seen[day] = index

        basis = str(entry.get("basis", "deployment"))
        if basis not in BASES:
            raise ReleaseNotesError(f"{where}: unknown basis {basis!r}.")

        days.append(
            ReleaseDay(date=day, changes=_parse_changes(_require(entry, "changes", where), where))
        )

    if not days:
        raise ReleaseNotesError(f"{source}: no days are described.")
    return tuple(sorted(days, key=lambda entry: entry.date, reverse=True))


def _parse_date(value: Any, where: str) -> date:
    """The day, from either a TOML date or an ISO string.

    TOML has a real date type and the file uses it, so ``date = 2026-09-09``
    arrives already parsed. A quoted ``"2026-09-09"`` is accepted as well: the
    difference is one pair of quotation marks, and refusing a file that says
    exactly what it means would be a rule with no reason behind it.
    """
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ReleaseNotesError(
            f"{where}: {value!r} is not a date — write it as 2026-09-09."
        ) from error


def _parse_changes(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ReleaseNotesError(f"{where}: at least one change is required.")
    changes: list[str] = []
    for position, change in enumerate(value, start=1):
        if not isinstance(change, str) or not change.strip():
            # An empty bullet renders as an empty list item: a marker with
            # nothing beside it, which reads as a change nobody bothered to
            # describe rather than as a mistake in a file.
            raise ReleaseNotesError(f"{where}: change #{position} is empty.")
        changes.append(change.strip())
    return tuple(changes)


@lru_cache(maxsize=1)
def load_release_notes() -> tuple[ReleaseDay, ...]:
    """The committed release notes, parsed once per process.

    Keyed on nothing, because there is nothing to key on: the file is baked into
    the image beside the code it describes and cannot change under a running
    process. Editing it against a development server needs a restart, which
    ``runserver`` performs by itself.
    """
    path = release_notes_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseNotesError(f"{path} could not be read: {error}") from error
    return parse_release_notes(text, source=path.name)
