"""A synthetic world with the shapes the final register cutover has to survive.

Every title, name and institution is invented. No Koda row, matter title,
colleague name or source sentence appears here (master specification 5.3, 23.5).

What it reproduces is the *structure* of the approved snapshot and none of its
content: two maintained sheets rather than one, terminal and non-terminal
statuses side by side, a `VÄLJA` that is populated on live work and blank on
work still being drafted, continuation notes in all four of their forms, and the
five ways a person can already have decided something the register now disagrees
with.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from app.legacy_import.models import ImportBatch, MatterSourceReference
from app.legacy_import.parser import SOURCE_SYSTEM
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import create_imported_matter, create_matter
from app.workflow.enums import Disposition
from tests.synthetic_portfolio import (
    People,
    Register,
    add_source_reference,
    build_people,
    build_register,
    snapshot_for,
)

CURRENT_SHEET = 2026
CARRY_SHEET = 2025

#: The years the synthetic snapshot is approved for — the two its rows live on.
#: A reviewed snapshot carries a scope as well as a digest, so a test that
#: approves one has to approve both halves or it is not modelling the real
#: thing (app/legacy_import/final_cutover.py).
REVIEWED_YEARS = frozenset({CARRY_SHEET, CURRENT_SHEET})


def approve_snapshot(
    monkeypatch: Any,
    *,
    sha256: str,
    years: frozenset[int] = REVIEWED_YEARS,
) -> None:
    """Approve a snapshot the way a reviewed code change approves a real one.

    One helper rather than three copies of the same monkeypatch, so that the
    day the policy grows a third field the tests learn about it in one place.
    """
    from app.legacy_import.final_cutover import ReviewedSnapshot

    monkeypatch.setattr(
        "app.legacy_import.final_cutover.REVIEWED_SNAPSHOTS",
        (ReviewedSnapshot(sha256=sha256, label="sünteetiline", current_years=years),),
    )


#: The two labels that end current work, and three that do not. Spelled here as
#: the register spells them, because the vocabulary is the thing under test.
TERMINAL_IN_FORCE = "jõustunud"
TERMINAL_NO_PLANS = "rohkem pole tegevusi plaanis"
LIVE_CONSULTATION = "kooskõlastusringil"
LIVE_PARLIAMENT = "Riigikogus"
#: A real status, not a gap. It stays current, and a test says so.
LIVE_OTHER = "muu"

#: The approved snapshot for this world, and an earlier one that must survive it.
FINAL_SNAPSHOT = snapshot_for("final-cutover-21-08")
EARLIER_SNAPSHOT = snapshot_for("earlier-snapshot")

# -- the cast, by the shape each one tests ---------------------------------

CURRENT_DRAFTING = "Sünteetiline pakendieelnõu, arvamus koostamisel"
CURRENT_SENT = "Sünteetiline energiaeelnõu, arvamus saadetud"
CURRENT_OTHER_STATUS = "Sünteetiline määrus staatusega muu"
CARRY_OVER_LIVE = "Sünteetiline eelmise aasta elav menetlus"
CARRY_OVER_REOPENABLE = "Sünteetiline ülemineku käigus arhiveeritud"
RETIRING_IN_FORCE = "Sünteetiline jõustunud akt"
RETIRING_NO_PLANS = "Sünteetiline lõpetatud töö"
SUPERSEDED_ROW = "Sünteetiline jätkub mujal"
AMBIGUOUS_ROW = "Sünteetiline mitmeti mõistetav jätkumine"
BARE_REFERENCE_ROW = "Sünteetiline pelga viitega rida"
PADDING_ROW = "Sünteetiline eelnummerdatud tühi rida"
REAL_CLOSURE = "Sünteetiline tegelikult suletud teema"
NATIVE_ROW = "Sünteetiline kohapeal loodud teema"
CONFLICT_ENTRY = "Sünteetiline hilisema sissekandega teema"
CONFLICT_ACTION = "Sünteetiline avatud tegevusega teema"
CONFLICT_SUBMISSION = "Sünteetiline kohapealse arvamusega teema"

#: The register writes a first name. `Ireen` deliberately matches no account in
#: this world, which is the shape the real snapshot has for two current rows.
OWNER_KNOWN = "Sandra"
OWNER_UNKNOWN = "Ireen"


class World:
    """The built world, addressable by title."""

    def __init__(self, people: People, register: Register) -> None:
        self.people = people
        self.register = register
        self.matters: dict[str, Matter] = {}

    def __getitem__(self, title: str) -> Matter:
        return self.matters[title]

    def refresh(self, title: str) -> Matter:
        return Matter.objects.get(pk=self.matters[title].pk)


def _imported(
    world: World,
    title: str,
    *,
    year: int,
    number: int,
    record_mode: str = RecordMode.ARCHIVE,
    is_open: bool = True,
    origin: str = MatterOrigin.LEGACY_IMPORT,
) -> Matter:
    matter = create_imported_matter(
        title=title,
        reference_year=year,
        reference_number=number,
        record_mode=record_mode,
    )
    changed: list[str] = []
    if origin != matter.origin:
        matter.origin = origin
        changed.append("origin")
    if is_open != matter.is_open:
        matter.is_open = is_open
        changed.append("is_open")
    if changed:
        matter.save(update_fields=[*changed, "updated_at"])
    world.matters[title] = matter
    return matter


def _row(
    world: World,
    title: str,
    *,
    year: int,
    status: str,
    owner: str = OWNER_KNOWN,
    next_action: str = "",
    opinion_sent: str = "",
    snapshot: str | None = None,
    title_cell: str | None = None,
) -> MatterSourceReference:
    return add_source_reference(
        world.register,
        world.matters[title],
        year=year,
        owner_cell=owner,
        status_cell=status,
        next_action_cell=next_action,
        opinion_sent_cell=opinion_sent,
        title_cell=title if title_cell is None else title_cell,
        snapshot=snapshot or FINAL_SNAPSHOT,
    )


def build_world() -> World:
    """One synthetic snapshot carrying every shape the operation must handle."""
    people = build_people()
    register = build_register(people)
    register.snapshot = FINAL_SNAPSHOT
    world = World(people, register)

    # -- already current, and staying so ------------------------------------
    _imported(world, CURRENT_DRAFTING, year=2026, number=1, record_mode=RecordMode.FULL)
    _row(world, CURRENT_DRAFTING, year=CURRENT_SHEET, status=LIVE_CONSULTATION)

    _imported(world, CURRENT_SENT, year=2026, number=2, record_mode=RecordMode.FULL)
    _row(
        world,
        CURRENT_SENT,
        year=CURRENT_SHEET,
        status=LIVE_PARLIAMENT,
        # Sent, and still running. The row the "VÄLJA closes a Matter" reading
        # gets wrong.
        opinion_sent="14.05.2026",
    )

    # Live work whose responsible person has no account here. Two current rows
    # in the approved snapshot have exactly this shape, and the owner must stay
    # unresolved rather than being guessed at or dropped.
    _imported(world, CURRENT_OTHER_STATUS, year=2026, number=3, record_mode=RecordMode.FULL)
    _row(
        world,
        CURRENT_OTHER_STATUS,
        year=CURRENT_SHEET,
        status=LIVE_OTHER,
        owner=OWNER_UNKNOWN,
    )

    # -- 2025 carry-over: live work the year-only rule had archived ----------
    _imported(world, CARRY_OVER_LIVE, year=2025, number=4)
    _row(world, CARRY_OVER_LIVE, year=CARRY_SHEET, status=LIVE_CONSULTATION)

    # Retired by the Stage-2I default: ARCHIVE, closed, and inventing nothing.
    _imported(world, CARRY_OVER_REOPENABLE, year=2025, number=5, is_open=False)
    _row(world, CARRY_OVER_REOPENABLE, year=CARRY_SHEET, status=LIVE_PARLIAMENT)

    # -- leaving the current set --------------------------------------------
    _imported(world, RETIRING_IN_FORCE, year=2026, number=6, record_mode=RecordMode.FULL)
    _row(world, RETIRING_IN_FORCE, year=CURRENT_SHEET, status=TERMINAL_IN_FORCE)

    _imported(world, RETIRING_NO_PLANS, year=2026, number=7, record_mode=RecordMode.FULL)
    _row(world, RETIRING_NO_PLANS, year=CURRENT_SHEET, status=TERMINAL_NO_PLANS)

    # -- continuation, in its four forms ------------------------------------
    _imported(world, SUPERSEDED_ROW, year=2025, number=8, record_mode=RecordMode.FULL)
    _row(
        world,
        SUPERSEDED_ROW,
        year=CARRY_SHEET,
        status=LIVE_CONSULTATION,
        next_action="Jätkub teema 2026_1 all.",
    )

    _imported(world, AMBIGUOUS_ROW, year=2025, number=9, record_mode=RecordMode.FULL)
    _row(
        world,
        AMBIGUOUS_ROW,
        year=CARRY_SHEET,
        status=LIVE_CONSULTATION,
        next_action="Jätkub kas teema 2026_1 või 2026_2 all.",
    )

    # A reference with no continuation wording: an ordinary cross-reference.
    _imported(world, BARE_REFERENCE_ROW, year=2025, number=11, record_mode=RecordMode.FULL)
    _row(
        world,
        BARE_REFERENCE_ROW,
        year=CARRY_SHEET,
        status=LIVE_CONSULTATION,
        next_action="Seotud teemaga 2026_2, ootan tagasisidet.",
    )

    # -- a pre-numbered blank -----------------------------------------------
    _imported(world, PADDING_ROW, year=2026, number=300)
    _row(world, PADDING_ROW, year=CURRENT_SHEET, status="", title_cell="")

    # -- decisions a person already made ------------------------------------
    closed = _imported(world, REAL_CLOSURE, year=2025, number=12)
    closed.is_open = False
    closed.disposition = Disposition.MONITORING_STOPPED
    closed.closed_at = timezone.now()
    closed.save(update_fields=["is_open", "disposition", "closed_at", "updated_at"])
    _row(world, REAL_CLOSURE, year=CARRY_SHEET, status=LIVE_CONSULTATION)

    native = create_matter(title=NATIVE_ROW, owner=people.sandra)
    world.matters[NATIVE_ROW] = native
    _row(world, NATIVE_ROW, year=CURRENT_SHEET, status=TERMINAL_IN_FORCE)

    for title, number in (
        (CONFLICT_ENTRY, 13),
        (CONFLICT_ACTION, 14),
        (CONFLICT_SUBMISSION, 15),
    ):
        _imported(world, title, year=2026, number=number, record_mode=RecordMode.FULL)
        _row(world, title, year=CURRENT_SHEET, status=TERMINAL_NO_PLANS)

    # -- an unresolvable owner on live work ---------------------------------
    _row(
        world,
        CURRENT_DRAFTING,
        year=CURRENT_SHEET,
        status=LIVE_CONSULTATION,
        owner=OWNER_UNKNOWN,
        snapshot=EARLIER_SNAPSHOT,
    )

    return world


# ---------------------------------------------------------------------------
# The sixteen-sheet shape, which is what production actually holds
# ---------------------------------------------------------------------------

OUT_2018_BLANK = "Sünteetiline 2018 staatuseta rida"
OUT_2023_LIVE = "Sünteetiline 2023 lõpetamata staatusega rida"
OUT_2024_LIVE = "Sünteetiline 2024 lõpetamata staatusega rida"
OUT_2019_STILL_OPEN = "Sünteetiline 2019 ekslikult jooksev rida"
OUT_2014_WITH_ENTRY = "Sünteetiline 2014 hilisema sissekandega rida"
OUT_2016_REAL_CLOSURE = "Sünteetiline 2016 tegelikult suletud rida"
IN_2025_LIVE = "Sünteetiline 2025 jooksev rida"
IN_2026_LIVE = "Sünteetiline 2026 jooksev rida"


def build_historical_world() -> World:
    """A snapshot spanning years the register stopped maintaining.

    The production defect in one fixture. `add_source_reference` writes
    ``legacy_status`` only where the year's era contract has the column, so the
    2018, 2019, 2014 and 2016 rows below genuinely carry no status — exactly
    what the real workbook's older sheets look like, rather than a blank string
    standing in for one. That is the whole point: the row cannot answer "did
    this work end", and the operation must not read the silence as "no".
    """
    people = build_people()
    register = build_register(people)
    register.snapshot = FINAL_SNAPSHOT
    world = World(people, register)

    # Outside the reviewed scope, and already historical. The regression: every
    # one of these was proposed for ACTIVATE in production.
    _imported(world, OUT_2018_BLANK, year=2018, number=1, is_open=False)
    _row(world, OUT_2018_BLANK, year=2018, status="")

    # Outside the scope even though the column exists and says "not finished".
    # This is what makes the rule *scope* rather than "years lacking HETKESEIS".
    _imported(world, OUT_2023_LIVE, year=2023, number=2, is_open=False)
    _row(world, OUT_2023_LIVE, year=2023, status=LIVE_CONSULTATION)

    _imported(world, OUT_2024_LIVE, year=2024, number=3, is_open=False)
    _row(world, OUT_2024_LIVE, year=2024, status=LIVE_PARLIAMENT)

    # Out of scope and somehow still current: the register retires it.
    _imported(world, OUT_2019_STILL_OPEN, year=2019, number=4, record_mode=RecordMode.FULL)
    _row(world, OUT_2019_STILL_OPEN, year=2019, status="")

    # Out of scope, still current, and somebody has worked on it since.
    _imported(world, OUT_2014_WITH_ENTRY, year=2014, number=5, record_mode=RecordMode.FULL)
    _row(world, OUT_2014_WITH_ENTRY, year=2014, status="")

    # Out of scope with a real recorded closure, which nothing may overwrite.
    closed = _imported(world, OUT_2016_REAL_CLOSURE, year=2016, number=6, is_open=False)
    closed.disposition = Disposition.MONITORING_STOPPED
    closed.closed_at = timezone.now()
    closed.save(update_fields=["disposition", "closed_at", "updated_at"])
    _row(world, OUT_2016_REAL_CLOSURE, year=2016, status="")

    # Inside the scope, so the ordinary rules decide.
    _imported(world, IN_2025_LIVE, year=2025, number=7, is_open=False)
    _row(world, IN_2025_LIVE, year=CARRY_SHEET, status=LIVE_CONSULTATION)

    _imported(world, IN_2026_LIVE, year=2026, number=8, record_mode=RecordMode.FULL)
    _row(world, IN_2026_LIVE, year=CURRENT_SHEET, status=LIVE_CONSULTATION)

    return world


def earlier_snapshot_reference(world: World, title: str, **kwargs: Any) -> MatterSourceReference:
    """One observation from a workbook that came before the approved one."""
    return _row(world, title, snapshot=EARLIER_SNAPSHOT, **kwargs)


def other_batch(name: str) -> ImportBatch:
    return ImportBatch.objects.create(
        source_system=SOURCE_SYSTEM,
        source_file_name=f"{name}.xlsx",
        source_snapshot_sha256=snapshot_for(name),
        importer_version="cutover-test",
        contract_version="test",
        started_at=timezone.now(),
    )
