"""Apply a reviewed plan of document display titles through `Muuda nime`.

The OneNote import set `Document.title` to the attachment's filename, and many
of those names say nothing («Seletuskiri.pdf», «E-kiri (31).eml»). A reviewed
plan proposes better display titles. This module is the only way such a plan
reaches the database, and it adds nothing of its own to what a person does on a
Dokumendid row:

* **Same path.** Every rename is `rename_document`, with the same title rules,
  the same refusals (closed Matter, removed document, empty or over-long title)
  and the same audit event, `DOCUMENT_TITLE_CHANGED` with ``from`` and ``to`` —
  one event per rename. `DocumentVersion.original_filename`, the bytes, the
  checksum and the versions are not touched, because the service does not touch
  them.
* **Same actor rules.** The actor is named explicitly and must be an active user
  who may write business content and can see the document, as the view asks.
* **Only what the plan saw.** A row applies only when the document's title is
  still exactly the plan's ``current_title``. A title somebody has changed
  since is refused, never overwritten. A title already equal to the proposal is
  reported as done and writes nothing, so a second run is a no-op.
* **Only what was approved.** Only rows recommended ``APPROVE`` with
  ``suggestion_kind`` ``EXPLICIT_TITLE`` are eligible. Contextual descriptions
  are never applied by this module.
* **Dry run first.** `plan_sha256` identifies the exact file; applying requires
  the digest from a reviewed dry run. The file is read **once**: the digest is
  of exactly the bytes that are parsed and applied, so a file swapped between
  a digest and a second read cannot apply rows nobody reviewed.
* **Only printable titles.** A proposal holding a control character (NUL
  included), a bidirectional override or isolate, or another invisible format
  character is refused as that row's ``REFUSED_BAD_PROPOSAL``, naming the
  character: «Arvamus» + U+202E + «fdp.exe» reads on screen as «Arvamusexe.pdf».

Each row is its own transaction: one refusal does not undo the rows before it.
The comparison with ``current_title`` is made under the same Matter → Document
locks `rename_document` takes, so a concurrent edit cannot slip between the
check and the write.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from app.core.authorization import may_write_business_content
from app.core.errors import DomainError
from app.documents.models import Document
from app.documents.services import (
    DOCUMENT_ALREADY_REMOVED,
    DOCUMENT_TITLE_MAX,
    rename_document,
)
from app.matters.locks import CLOSED_MATTER_REFUSAL, lock_open_matter_for_business_write

#: The only recommendation this module applies.
APPROVE = "APPROVE"
#: The only suggestion kind this module applies.
EXPLICIT_TITLE = "EXPLICIT_TITLE"

#: Fields every plan row must carry.
REQUIRED_FIELDS = (
    "document_id",
    "current_title",
    "proposed_title",
    "recommendation",
    "suggestion_kind",
)

# Outcomes, one per row.
WOULD_RENAME = "WOULD_RENAME"
RENAMED = "RENAMED"
ALREADY_APPLIED = "ALREADY_APPLIED"
SKIPPED_NOT_APPROVED = "SKIPPED_NOT_APPROVED"
SKIPPED_NOT_EXPLICIT = "SKIPPED_NOT_EXPLICIT"
REFUSED_TITLE_CHANGED = "REFUSED_TITLE_CHANGED"
REFUSED_NOT_FOUND = "REFUSED_NOT_FOUND"
REFUSED_NOT_VISIBLE = "REFUSED_NOT_VISIBLE"
REFUSED_REMOVED = "REFUSED_REMOVED"
REFUSED_CLOSED_MATTER = "REFUSED_CLOSED_MATTER"
REFUSED_BAD_PROPOSAL = "REFUSED_BAD_PROPOSAL"
REFUSED_SERVICE = "REFUSED_SERVICE"

OUTCOMES = (
    WOULD_RENAME,
    RENAMED,
    ALREADY_APPLIED,
    SKIPPED_NOT_APPROVED,
    SKIPPED_NOT_EXPLICIT,
    REFUSED_TITLE_CHANGED,
    REFUSED_NOT_FOUND,
    REFUSED_NOT_VISIBLE,
    REFUSED_REMOVED,
    REFUSED_CLOSED_MATTER,
    REFUSED_BAD_PROPOSAL,
    REFUSED_SERVICE,
)

#: Refused when the plan file cannot be used at all.
PLAN_UNREADABLE = "Pealkirjade plaani ei saa lugeda"
#: Refused when a row lacks a required field.
PLAN_ROW_INCOMPLETE = "Pealkirjade plaani rida on puudulik"
#: Refused when a document appears twice in one plan.
PLAN_DUPLICATE_DOCUMENT = "Sama dokument on plaanis mitu korda"
#: Refused when the actor cannot be found or is not active.
ACTOR_UNKNOWN = "Tegijat ei leitud või ta pole aktiivne"
#: Refused when the actor may not write business content.
ACTOR_MAY_NOT_WRITE = "Tegija ei tohi sisu muuta"
#: Refused when the digest of the plan does not match the reviewed one.
PLAN_DIGEST_MISMATCH = "Plaani kontrollsumma ei vasta ülevaadatud plaanile"
#: Why a row is `REFUSED_BAD_PROPOSAL` (its `RowResult.detail`).
PROPOSAL_FORBIDDEN_CHARACTER = "Pakutud pealkirjas on keelatud märk"
PROPOSAL_EMPTY = "Pakutud pealkiri on tühi"
PROPOSAL_TOO_LONG = "Pakutud pealkiri on liiga pikk"
#: Raised inside a row's transaction when the title moved since the plan.
_TITLE_MOVED = "title changed since the plan"


class TitlePlanError(DomainError):
    """The plan or the actor cannot be used; nothing was written."""


@dataclass(frozen=True)
class PlanRow:
    line: int
    document_id: uuid.UUID
    current_title: str
    proposed_title: str
    recommendation: str
    suggestion_kind: str


@dataclass(frozen=True)
class RowResult:
    line: int
    document_id: uuid.UUID
    outcome: str
    detail: str = ""


@dataclass
class ApplyReport:
    plan_sha256: str
    applied: bool
    results: list[RowResult] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(result.outcome for result in self.results)
        return {name: tally.get(name, 0) for name in OUTCOMES}


def plan_sha256(path: Path) -> str:
    return hashlib.sha256(_read(path)).hexdigest()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise TitlePlanError(f"{PLAN_UNREADABLE}: {error}") from error


def load_plan(path: Path) -> list[PlanRow]:
    """Read a JSON Lines plan. Refuses the whole file on any malformed row."""
    return parse_plan(_read(path))


def parse_plan(data: bytes) -> list[PlanRow]:
    """The rows of a JSON Lines plan's bytes. Refuses the whole plan on any malformed row."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise TitlePlanError(f"{PLAN_UNREADABLE}: {error}") from error
    rows: list[PlanRow] = []
    seen: set[uuid.UUID] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TitlePlanError(f"{PLAN_UNREADABLE}: rida {number}") from error
        if not isinstance(data, dict) or any(
            not isinstance(data.get(name), str) for name in REQUIRED_FIELDS
        ):
            raise TitlePlanError(f"{PLAN_ROW_INCOMPLETE}: rida {number}")
        try:
            document_id = uuid.UUID(data["document_id"])
        except ValueError as error:
            raise TitlePlanError(f"{PLAN_ROW_INCOMPLETE}: rida {number}") from error
        if document_id in seen:
            raise TitlePlanError(f"{PLAN_DUPLICATE_DOCUMENT}: rida {number}")
        seen.add(document_id)
        rows.append(
            PlanRow(
                line=number,
                document_id=document_id,
                current_title=data["current_title"],
                proposed_title=data["proposed_title"],
                recommendation=data["recommendation"],
                suggestion_kind=data["suggestion_kind"],
            )
        )
    return rows


def resolve_actor(identifier: str) -> Any:
    """The named, active user who may write business content — or a refusal."""
    users = get_user_model()
    name = (identifier or "").strip()
    actor = users._default_manager.filter(**{users.USERNAME_FIELD: name}).first() if name else None
    if actor is None or not actor.is_active:
        raise TitlePlanError(ACTOR_UNKNOWN)
    if not may_write_business_content(actor):
        raise TitlePlanError(ACTOR_MAY_NOT_WRITE)
    return actor


def _clean(title: str) -> str:
    # The service's own whitespace rule, so «already applied» means what it will store.
    return " ".join(title.split())


#: Whitespace the service collapses to one space (`_clean`): allowed in a proposal.
_COLLAPSED_CONTROLS = frozenset("\t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85")
#: Unicode categories a display title never holds: controls (NUL among them),
#: format characters (the bidirectional overrides and isolates, zero-width
#: marks), unpaired surrogates and private-use characters.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})


def forbidden_character(title: str) -> str:
    """The first character a display title may not hold, as «U+202E», or ""."""
    for character in title:
        if character in _COLLAPSED_CONTROLS:
            continue
        if unicodedata.category(character) in _FORBIDDEN_CATEGORIES:
            return f"U+{ord(character):04X}"
    return ""


def _eligibility(row: PlanRow) -> tuple[str, str]:
    """The outcome that stops the row before the database, and why; ``("", "")`` if none."""
    if row.recommendation != APPROVE:
        return SKIPPED_NOT_APPROVED, ""
    if row.suggestion_kind != EXPLICIT_TITLE:
        return SKIPPED_NOT_EXPLICIT, ""
    forbidden = forbidden_character(row.proposed_title)
    if forbidden:
        return REFUSED_BAD_PROPOSAL, f"{PROPOSAL_FORBIDDEN_CHARACTER} {forbidden}"
    cleaned = _clean(row.proposed_title)
    if not cleaned:
        return REFUSED_BAD_PROPOSAL, PROPOSAL_EMPTY
    if len(cleaned) > DOCUMENT_TITLE_MAX:
        return REFUSED_BAD_PROPOSAL, PROPOSAL_TOO_LONG
    return "", ""


def _predict(row: PlanRow, actor: Any) -> RowResult:
    """What applying this row would do, read without locks or writes."""
    skipped, detail = _eligibility(row)
    if skipped:
        return RowResult(row.line, row.document_id, skipped, detail)
    document = Document._base_manager.select_related("matter").filter(pk=row.document_id).first()
    if document is None:
        return RowResult(row.line, row.document_id, REFUSED_NOT_FOUND)
    if document.is_removed:
        return RowResult(row.line, row.document_id, REFUSED_REMOVED)
    if not Document.objects.visible_to(actor).filter(pk=document.pk).exists():
        return RowResult(row.line, row.document_id, REFUSED_NOT_VISIBLE)
    if document.title == _clean(row.proposed_title):
        return RowResult(row.line, row.document_id, ALREADY_APPLIED)
    if document.title != row.current_title:
        return RowResult(row.line, row.document_id, REFUSED_TITLE_CHANGED)
    if not document.matter.is_open:
        return RowResult(row.line, row.document_id, REFUSED_CLOSED_MATTER)
    return RowResult(row.line, row.document_id, WOULD_RENAME)


def _apply_row(row: PlanRow, actor: Any) -> RowResult:
    predicted = _predict(row, actor)
    if predicted.outcome != WOULD_RENAME:
        return predicted
    try:
        with transaction.atomic():
            document = Document._base_manager.get(pk=row.document_id)
            # Matter, then Document — the one lock order — and only then the comparison.
            lock_open_matter_for_business_write(document.matter_id)
            locked = Document._base_manager.select_for_update(no_key=True).get(pk=document.pk)
            if locked.title == _clean(row.proposed_title):
                return RowResult(row.line, row.document_id, ALREADY_APPLIED)
            if locked.title != row.current_title:
                raise DomainError(_TITLE_MOVED)
            rename_document(document=locked, title=row.proposed_title, actor=actor)
    except DomainError as error:
        message = str(error)
        if message == _TITLE_MOVED:
            return RowResult(row.line, row.document_id, REFUSED_TITLE_CHANGED)
        if message == CLOSED_MATTER_REFUSAL:
            return RowResult(row.line, row.document_id, REFUSED_CLOSED_MATTER)
        if message == DOCUMENT_ALREADY_REMOVED:
            return RowResult(row.line, row.document_id, REFUSED_REMOVED)
        return RowResult(row.line, row.document_id, REFUSED_SERVICE, message)
    return RowResult(row.line, row.document_id, RENAMED)


def run_plan(
    path: Path,
    *,
    actor_identifier: str,
    apply: bool = False,
    expect_plan_sha256: str = "",
) -> ApplyReport:
    """Dry-run (default) or apply a plan; returns one result per row.

    Applying requires ``expect_plan_sha256`` equal to the file's digest, so what
    is written is exactly what a person reviewed in a dry run. The file is read
    once, and the digest is of the very bytes the rows are parsed from.
    """
    data = _read(path)
    digest = hashlib.sha256(data).hexdigest()
    rows = parse_plan(data)
    actor = resolve_actor(actor_identifier)
    if apply and expect_plan_sha256.strip().lower() != digest:
        raise TitlePlanError(PLAN_DIGEST_MISMATCH)
    report = ApplyReport(plan_sha256=digest, applied=apply)
    for row in rows:
        report.results.append(_apply_row(row, actor) if apply else _predict(row, actor))
    return report
