"""A synthetic current-portfolio world, rich enough to prove Stage 2F.

Every name, title and institution here is invented. No Koda source row, matter
title, colleague name or unresolved owner cell may appear in a fixture
(master specification 5.3, 23.5).

What this builds is the shape the real register has and none of its content: a
current year imported conservatively as ARCHIVE, with owner cells written the
way the register writes them — a first name, sometimes two names in one cell,
sometimes blank — plus the awkward rows that decide whether an operation is
safe. A closed row. A bare reference. A natively created Matter wearing the
same number. A restricted file. A decade-old row owned by somebody who has
left.

The raw source rows are built **through the era contract** rather than at fixed
column letters, so a fixture cannot quietly disagree with the parser about
which column is ``VASTUTAJA``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.core.enums import Visibility
from app.legacy_import.contracts import contract_for_year
from app.legacy_import.enums import ProposedRecordMode, RowOutcome
from app.legacy_import.models import (
    ImportBatch,
    ImportRowLedger,
    MatchMethod,
    MatterSourceReference,
)
from app.legacy_import.parser import SOURCE_SYSTEM
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import create_imported_matter, create_matter
from app.organisations.models import Organisation, OrganisationType
from app.workflow.models import StageVocabulary

CURRENT_YEAR = 2026
ARCHIVE_YEAR = 2014

#: The reviewed closure label. The one workbook value that means Koda stopped
#: rather than saying where the external process stands.
CLOSURE_LABEL = "rohkem pole tegevusi plaanis"
STAGE_LABEL = "kooskõlastusringil"

MINISTRY_NAME = "Näidisministeerium"


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@dataclass
class People:
    sandra: User
    martin: User
    anu: User
    anneli: User
    head: User
    former: User

    @property
    def specialists(self) -> list[User]:
        return [self.sandra, self.martin, self.anu, self.anneli]


def _person(
    *, upn: str, display_name: str, role: str = UserRole.SPECIALIST, is_active: bool = True
) -> User:
    return User.objects.create(
        upn=upn,
        display_name=display_name,
        role=role,
        is_active=is_active,
        is_synthetic=True,
    )


def build_people() -> People:
    """Four specialists, a department head, and one colleague who has left.

    ``Anu Näidis`` and ``Anneli Näidis`` exist so that a source cell reading
    ``Anu`` is *not* ambiguous — one exact full name is ``Anu Näidis`` and no
    other display name is ``Anu`` — while a cell reading ``Näidis`` is
    unresolvable and a cell reading ``Anu, Martin`` names two people. The
    departed ``Kadri Endine`` is what makes the inactive-user rules testable:
    her name must still resolve in history and must never be offered as
    somebody to hand new work to.
    """
    return People(
        sandra=_person(upn="sandra@example.invalid", display_name="Sandra Näidis"),
        martin=_person(upn="martin@example.invalid", display_name="Martin Näidis"),
        anu=_person(upn="anu@example.invalid", display_name="Anu Näidis"),
        anneli=_person(upn="anneli@example.invalid", display_name="Anneli Näidis"),
        head=_person(
            upn="juht@example.invalid",
            display_name="Tiina Juhataja",
            role=UserRole.DEPARTMENT_HEAD,
        ),
        former=_person(
            upn="kadri@example.invalid",
            display_name="Kadri Endine",
            is_active=False,
        ),
    )


# ---------------------------------------------------------------------------
# Source rows, built through the contract
# ---------------------------------------------------------------------------


def raw_row(year: int, **values: str) -> dict[str, str]:
    """One ``source_row_raw`` mapping, keyed by column letter.

    Canonical field names in, column letters out, resolved through the reviewed
    era contract. A fixture that wrote ``{"H": "Sandra"}`` would agree with the
    2026 sheet by coincidence and disagree with 2014 silently.
    """
    contract = contract_for_year(year)
    if contract is None:
        raise AssertionError(f"No era contract for {year}; the fixture cannot build a row.")
    row: dict[str, str] = {}
    for canonical_field, value in values.items():
        column = contract.column_for(canonical_field)
        if column is None:
            raise AssertionError(f"The {year} contract has no {canonical_field!r} column.")
        row[column.letter] = value
    return row


def snapshot_for(name: str) -> str:
    """A stable synthetic snapshot digest, distinct per fixture workbook."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


@dataclass
class Register:
    """Everything one synthetic import run produced."""

    batch: ImportBatch
    snapshot: str
    people: People
    ministry: Organisation
    stage: StageVocabulary
    matters: dict[str, Matter] = field(default_factory=dict)
    _row: int = 10

    def next_row(self) -> int:
        self._row += 1
        return self._row


def build_register(people: People) -> Register:
    ministry, _ = Organisation.objects.get_or_create(
        name=MINISTRY_NAME, defaults={"organisation_type": OrganisationType.MINISTRY}
    )
    return Register(
        batch=ImportBatch.objects.create(
            source_system=SOURCE_SYSTEM,
            source_file_name="sunteetiline-register.xlsx",
            source_snapshot_sha256=snapshot_for("stage-2f"),
            importer_version="2F-test",
            contract_version="test",
            started_at=timezone.now(),
        ),
        snapshot=snapshot_for("stage-2f"),
        people=people,
        ministry=ministry,
        stage=StageVocabulary.objects.get(key="consultation"),
    )


def add_source_reference(
    register: Register,
    matter: Matter,
    *,
    year: int = CURRENT_YEAR,
    owner_cell: str = "",
    status_cell: str = "",
    next_action_cell: str = "",
    title_cell: str = "",
    opinion_sent_cell: str = "",
    received_cell: str = "",
    deadline_cell: str = "",
    addressee_cell: str = "",
    snapshot: str | None = None,
    conflict_state: str = "NONE",
) -> MatterSourceReference:
    """Attach one verbatim register row to a Matter.

    The four optional cells below arrived with the final cutover (ADR 0021),
    which reads columns Stage 2F had no use for. Written through the same
    contract lookup as the rest, so a fixture still cannot disagree with the
    parser about which letter holds what.
    """
    values = {"owner_name": owner_cell}
    for canonical, cell in (
        ("opinion_sent_date", opinion_sent_cell),
        ("received_date", received_cell),
        ("response_deadline", deadline_cell),
        ("addressee_organisation", addressee_cell),
    ):
        if cell:
            values[canonical] = cell
    contract = contract_for_year(year)
    if contract is not None and contract.column_for("legacy_status") is not None:
        values["legacy_status"] = status_cell
    if contract is not None and contract.column_for("next_action_text") is not None:
        values["next_action_text"] = next_action_cell
    values["title"] = title_cell or matter.title
    values["matter_reference"] = matter.display_reference

    return MatterSourceReference.objects.create(
        matter=matter,
        import_batch=register.batch,
        source_system=SOURCE_SYSTEM,
        source_file_name=register.batch.source_file_name,
        source_snapshot_sha256=snapshot or register.snapshot,
        source_sheet=str(year),
        source_row_number=register.next_row(),
        source_row_raw=raw_row(year, **values),
        source_title=values["title"],
        source_era=str(year) if year >= 2025 else "2011-2017",
        source_contract_version="1.0",
        source_parser_version="test",
        match_method=MatchMethod.REFERENCE_TOKEN,
        conflict_state=conflict_state,
    )


def add_ledger_entry(
    register: Register,
    matter: Matter,
    *,
    year: int = CURRENT_YEAR,
    proposed: str = ProposedRecordMode.ARCHIVE.value,
    outcome: str = RowOutcome.WOULD_CREATE.value,
    anomalies: list[str] | None = None,
) -> ImportRowLedger:
    return ImportRowLedger.objects.create(
        import_batch=register.batch,
        matter=matter,
        source_sheet=str(year),
        source_row_number=register.next_row(),
        source_reference=matter.display_reference,
        outcome=outcome,
        anomalies=anomalies or [],
        proposed_record_mode=proposed,
        proposed_record_mode_reason="sünteetiline",
    )


def imported_matter(
    register: Register,
    *,
    number: int,
    title: str,
    year: int = CURRENT_YEAR,
    **extra: Any,
) -> Matter:
    """An archive Matter as the conservative import would have left it."""
    extra.setdefault("data_quality_tier", DataQualityTier.TIER_3_REGISTER_ARCHIVE)
    extra.setdefault("source_era", str(year))
    # Popped rather than defaulted in the signature: `create_imported_matter`
    # takes `record_mode` by name, so leaving it in `extra` would pass the same
    # keyword twice.
    record_mode = extra.pop("record_mode", RecordMode.ARCHIVE)
    matter = create_imported_matter(
        title=title,
        reference_year=year,
        reference_number=number,
        record_mode=record_mode,
        **extra,
    )
    register.matters[title] = matter
    return matter


# ---------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------

#: Titles, so a test can name a row without repeating a string literal.
OWNED_CANDIDATE = "Pakendiseaduse muutmine sünteetiline"
UNASSIGNED = "Ehitusseadustiku eelnõu vastutajata"
CLOSED = "Lõpetatud sünteetiline registririda"
BARE = "Tühja sisuga registririda"
NATIVE = "Süsteemis loodud sünteetiline teema"
ALREADY_FULL = "Juba aktiivne sünteetiline teema"
RESTRICTED = "Piiratud sünteetiline teema"
SHARED_OWNER = "Kahe juristi ühine teema"
UNKNOWN_OWNER = "Tundmatu vastutajaga teema"
CONFLICTED_OWNER = "Vastuoluliste allikatega teema"
HISTORICAL = "Vana registririda endise kolleegiga"


@dataclass
class Portfolio:
    people: People
    register: Register

    def matter(self, title: str) -> Matter:
        return Matter.objects.get(title=title)


def build_portfolio() -> Portfolio:
    """The whole synthetic world, imported and awaiting Stage 2F.

    Every Matter here is ARCHIVE unless the scenario says otherwise, because
    that is what the conservative import actually produces — which is the
    problem Stage 2F exists to address, and a fixture that started from the
    fixed state would prove nothing.
    """
    people = build_people()
    register = build_register(people)
    today = timezone.localdate()

    # A real current row: named owner, mapped stage, an instruction in the
    # source, and a deadline still ahead.
    owned = imported_matter(
        register,
        number=1,
        title=OWNED_CANDIDATE,
        stage=register.stage,
        received_date=today - dt.timedelta(days=10),
        response_deadline=today + dt.timedelta(days=3),
        addressee_organisation=register.ministry,
    )
    add_source_reference(
        register,
        owned,
        owner_cell="Sandra",
        status_cell=STAGE_LABEL,
        next_action_cell="Ootame ministeeriumi vastust",
    )
    add_ledger_entry(register, owned, proposed=ProposedRecordMode.FULL_CANDIDATE.value)

    # A current row nobody is named on. The most useful state a department head
    # can be shown, and it must survive promotion without acquiring an owner.
    unassigned = imported_matter(
        register,
        number=2,
        title=UNASSIGNED,
        stage=register.stage,
        received_date=today - dt.timedelta(days=2),
        response_deadline=today + dt.timedelta(days=5),
    )
    add_source_reference(register, unassigned, owner_cell="", status_cell=STAGE_LABEL)

    # Explicit closure. The import recorded that Koda stopped and never
    # recorded when, so this is an ARCHIVE record with a disposition and no
    # closure timestamp — and it must stay one.
    closed = imported_matter(
        register,
        number=3,
        title=CLOSED,
        is_open=False,
        disposition="MONITORING_STOPPED",
        received_date=today - dt.timedelta(days=200),
    )
    add_source_reference(register, closed, owner_cell="Martin", status_cell=CLOSURE_LABEL)

    # A reference and a title and nothing else.
    bare = imported_matter(register, number=4, title=BARE)
    add_source_reference(register, bare, owner_cell="")

    # Somebody has been working in Juristid under a 2026 number. The import
    # must not overwrite it and the promotion must not either.
    native = create_matter(
        title=NATIVE,
        assign_reference=False,
        reference_year=CURRENT_YEAR,
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        owner=people.martin,
        reporting_year=CURRENT_YEAR,
    )
    # Both halves together: the CHECK constraint refuses a number without a
    # year, and `assign_reference=False` left both null.
    native.reference_year = CURRENT_YEAR
    native.reference_number = 5
    native.save(update_fields=["reference_year", "reference_number", "updated_at"])
    add_source_reference(register, native, owner_cell="Martin")

    # Already activated by an earlier reviewed decision.
    already = imported_matter(
        register,
        number=6,
        title=ALREADY_FULL,
        record_mode=RecordMode.FULL,
        owner=people.anu,
        stage=register.stage,
        received_date=today - dt.timedelta(days=20),
    )
    add_source_reference(register, already, owner_cell="Anu", status_cell=STAGE_LABEL)

    # A restricted current file. A specialist who is not on it must never see
    # it; the department head must, because the role entitles that.
    restricted = imported_matter(
        register,
        number=7,
        title=RESTRICTED,
        visibility=Visibility.RESTRICTED,
        stage=register.stage,
        received_date=today - dt.timedelta(days=5),
        response_deadline=today + dt.timedelta(days=2),
    )
    add_source_reference(
        register, restricted, owner_cell="Sandra", next_action_cell="Konfidentsiaalne samm"
    )

    # Two names in one cell. A shared file, never one person's.
    shared = imported_matter(register, number=8, title=SHARED_OWNER, stage=register.stage)
    add_source_reference(register, shared, owner_cell="Sandra, Martin", status_cell=STAGE_LABEL)

    # A name no account carries.
    unknown = imported_matter(register, number=9, title=UNKNOWN_OWNER, stage=register.stage)
    add_source_reference(register, unknown, owner_cell="Näidis", status_cell=STAGE_LABEL)

    # Two source rows naming two different people. Not a tie to break.
    conflicted = imported_matter(register, number=10, title=CONFLICTED_OWNER, stage=register.stage)
    add_source_reference(register, conflicted, owner_cell="Sandra", status_cell=STAGE_LABEL)
    add_source_reference(
        register,
        conflicted,
        owner_cell="Martin",
        status_cell=STAGE_LABEL,
        snapshot=snapshot_for("stage-2f-second"),
    )

    # A decade-old row whose owner has since left.
    historical = imported_matter(
        register,
        number=11,
        title=HISTORICAL,
        year=ARCHIVE_YEAR,
        source_era="2011-2017",
    )
    add_source_reference(register, historical, year=ARCHIVE_YEAR, owner_cell="Kadri")

    return Portfolio(people=people, register=register)
