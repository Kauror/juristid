"""Loading Postipoiss letters, and pairing each with its Teema (docs/adr/0216).

Three operations and one rule.

**Load** stores a sealed package — letters, their files, and the Teema the
offline matching proposed — and decides nothing. A letter whose proposed Teema
this database does not have keeps the proposal as text and waits like any other.

**Apply** turns a pairing into what a Teema shows: the files linked as archive
letters of that Teema and, where the letter has its file and a date, a sent
`Koja arvamus` dated with the letter's own date. It goes through the opinion
archive's evidence path — the Matter lock, the existing version when the Teema
already holds these exact bytes, `create_document` + `add_evidence_version`
otherwise — so nothing here is a second way of filing evidence.

**Decide** is a lawyer accepting the proposal, choosing another Teema, or
leaving the letter as background. Every decision names its person.

**The seven-day rule** (owner, 2026-11-17): a Teema that already holds a sent
opinion within seven days of the letter is the same act. The letter is attached
to it rather than written as a second opinion, and Statistika counts it once.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from app.core.errors import DomainError
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
from app.legacy_import.opinion_enums import ArchiveLinkBasis
from app.legacy_import.postipoiss_models import (
    PairingBasis,
    PairingState,
    PairingTier,
    PostipoissLetter,
    PostipoissLetterFile,
    PostipoissPairing,
)

#: The same window Statistika uses to treat two records as one opinion.
SAME_ACT_DAYS = 7
#: Tiers the automatic phase may apply. Calibrated in docs/adr/0216 §3.
AUTOMATIC_TIERS = frozenset({PairingTier.A})
MANIFEST = "manifest.json"
SOURCE_PREFIX = "postipoiss"
_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".rtf": "application/rtf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------


@dataclass
class Package:
    root: Path
    sha256: str
    letters: list[dict[str, Any]]


def read_package(root: str | Path, *, expected_sha256: str = "") -> Package:
    """The manifest, its digest, and a refusal if it is not the reviewed one."""
    base = Path(root)
    path = base / MANIFEST
    if not path.is_file():
        raise DomainError(f"Paketis puudub {MANIFEST}.")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise DomainError(f"Pakett ei ole ülevaadatud pakett ({digest[:12]}…).")
    data = json.loads(raw.decode("utf-8"))
    letters = list(data.get("letters") or [])
    if not letters:
        raise DomainError("Paketis ei ole ühtegi kirja.")
    return Package(root=base, sha256=digest, letters=letters)


def _reference(text: str) -> tuple[int, int] | None:
    parts = (text or "").strip().split("_")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _matters_by_reference(references: Iterable[str]) -> dict[str, Any]:
    from app.matters.models import Matter

    wanted = {ref: pair for ref in references if (pair := _reference(ref))}
    if not wanted:
        return {}
    query = Q()
    for year, number in wanted.values():
        query |= Q(reference_year=year, reference_number=number)
    found = {
        (matter.reference_year, matter.reference_number): matter
        for matter in Matter.objects.filter(query)
    }
    return {ref: found[pair] for ref, pair in wanted.items() if pair in found}


@dataclass
class LoadReport:
    package_sha256: str = ""
    letters_created: int = 0
    letters_already: int = 0
    files_created: int = 0
    binaries_created: int = 0
    binaries_reused: int = 0
    proposals_resolved: int = 0
    proposals_unresolved: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        tiers = ", ".join(f"{k}={v}" for k, v in sorted(self.by_tier.items()))
        lines = [
            f"pakett {self.package_sha256}",
            f"kirjad: uued {self.letters_created}, juba olemas {self.letters_already}",
            f"failid: uued {self.files_created}; baidid uued {self.binaries_created}, "
            f"taaskasutatud {self.binaries_reused}",
            f"ettepanekud: teema leitud {self.proposals_resolved}, "
            f"teemat pole {self.proposals_unresolved}",
            f"kindlus: {tiers}",
        ]
        lines += [f"PROBLEEM: {problem}" for problem in self.problems]
        return "\n".join(lines)


def _date(value: Any) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load(package: Package, *, write: bool) -> LoadReport:
    """Store the package's letters, files and proposals. Idempotent; decides nothing.

    With ``write=False`` every check runs and nothing is stored, so the report a
    reviewer reads is the report the real load will print.
    """
    from app.documents.services import evidence_storage
    from app.legacy_import.opinion_materialize import storage_key_for

    report = LoadReport(package_sha256=package.sha256)
    existing = set(
        PostipoissLetter.objects.filter(
            kirja_id__in=[str(row.get("kirja_id")) for row in package.letters]
        ).values_list("kirja_id", flat=True)
    )
    matters = _matters_by_reference(
        str((row.get("suggestion") or {}).get("reference") or "") for row in package.letters
    )
    storage = evidence_storage()
    seen_ids: set[str] = set()
    for row in package.letters:
        kirja_id = str(row.get("kirja_id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not kirja_id or not title:
            report.problems.append("kiri ilma ID või pealkirjata")
            continue
        if kirja_id in seen_ids:
            report.problems.append(f"{kirja_id}: paketis kaks korda")
            continue
        seen_ids.add(kirja_id)
        suggestion = row.get("suggestion") or {}
        tier = str(suggestion.get("tier") or "F")
        if tier not in PairingTier.values:
            report.problems.append(f"{kirja_id}: tundmatu kindlus {tier!r}")
            continue
        report.by_tier[tier] = report.by_tier.get(tier, 0) + 1
        reference = str(suggestion.get("reference") or "")
        matter = matters.get(reference)
        if reference:
            if matter is None:
                report.proposals_unresolved += 1
            else:
                report.proposals_resolved += 1
        if kirja_id in existing:
            report.letters_already += 1
            continue
        files = []
        for spec in row.get("files") or []:
            sha = str(spec.get("sha256") or "").lower()
            source = package.root / "files" / sha
            if len(sha) != 64 or not source.is_file():
                report.problems.append(f"{kirja_id}: fail {sha[:12]}… puudub paketist")
                continue
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                report.problems.append(f"{kirja_id}: fail {sha[:12]}… ei vasta räsile")
                continue
            files.append((spec, sha, data))
        if not write:
            report.letters_created += 1
            report.files_created += len(files)
            continue
        with transaction.atomic():
            letter = PostipoissLetter.objects.create(
                kirja_id=kirja_id,
                year=int(row.get("year") or 0),
                register_row=row.get("register_row") or None,
                registered_on=_date(row.get("registered_on")),
                letter_date=_date(row.get("letter_date")),
                title=title[:500],
                recipient=str(row.get("recipient") or "")[:400],
                department=str(row.get("department") or "")[:200],
                file_source=str(row.get("file_source") or "")[:80],
                body=str(row.get("body") or ""),
                package_sha256=package.sha256,
            )
            for ordinal, (spec, sha, data) in enumerate(files):
                binary = OpinionArchiveBinary.objects.filter(sha256=sha).first()
                if binary is None:
                    key = storage_key_for(sha)
                    if not storage.exists(key):
                        # Bytes before the row, as the archive does it.
                        storage.save(key, ContentFile(data))
                    filename = str(spec.get("filename") or sha)[:255]
                    binary = OpinionArchiveBinary.objects.create(
                        sha256=sha,
                        size_bytes=len(data),
                        mime_type=_MIME_BY_SUFFIX.get(
                            Path(filename).suffix.lower(), "application/octet-stream"
                        ),
                        storage_key=key,
                        source_archive_sha256=package.sha256,
                        materialized_at=timezone.now(),
                    )
                    report.binaries_created += 1
                else:
                    report.binaries_reused += 1
                PostipoissLetterFile.objects.get_or_create(
                    letter=letter,
                    binary=binary,
                    defaults={
                        "filename": str(spec.get("filename") or sha)[:255],
                        "ordinal": ordinal,
                    },
                )
                report.files_created += 1
            PostipoissPairing.objects.create(
                letter=letter,
                tier=tier,
                signals=[str(s)[:80] for s in suggestion.get("signals") or []][:20],
                suggested_reference=reference[:20],
                suggested_matter=matter,
            )
        report.letters_created += 1
    return report


# ---------------------------------------------------------------------------
# Applying a pairing
# ---------------------------------------------------------------------------


@dataclass
class ApplyOutcome:
    submission_created: bool = False
    joined_existing: bool = False
    files_linked: int = 0
    note: str = ""


def _sent_on(letter: PostipoissLetter) -> datetime.date | None:
    return letter.letter_date or letter.registered_on


def _same_act(matter: Any, sent_on: datetime.date) -> Any:
    from app.submissions.models import Submission

    window = datetime.timedelta(days=SAME_ACT_DAYS)
    low = timezone.make_aware(datetime.datetime.combine(sent_on - window, datetime.time.min))
    high = timezone.make_aware(datetime.datetime.combine(sent_on + window, datetime.time.max))
    return (
        Submission.objects.filter(matter=matter, sent_at__range=(low, high))
        .historically_sent()
        .order_by("sent_at")
        .first()
    )


def _final_version(
    matter: Any, letter: PostipoissLetter, file: PostipoissLetterFile, actor: Any
) -> Any:
    from app.documents.enums import DocumentRole
    from app.documents.models import DocumentVersion
    from app.documents.services import add_evidence_version, create_document, evidence_storage

    binary = file.binary
    existing = (
        DocumentVersion.objects.filter(document__matter=matter, sha256=binary.sha256)
        .order_by("version_number")
        .first()
    )
    if existing is not None:
        return existing
    storage = evidence_storage()
    try:
        with storage.open(binary.storage_key, "rb") as handle:
            content = handle.read()
    except FileNotFoundError:
        return None
    document = create_document(
        matter=matter,
        title=letter.title[:400],
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
        provenance_note=f"Postipoiss · {letter.kirja_id} · {file.filename}",
    )
    return add_evidence_version(
        document=document,
        content=content,
        original_filename=file.filename,
        mime_type=binary.mime_type,
        uploaded_by=actor,
        expected_sha256=binary.sha256,
        acquired_at=timezone.now(),
        source_identifier=f"{SOURCE_PREFIX}:{letter.kirja_id}",
    )


def _attach_recipient(submission: Any, raw: str) -> None:
    from app.legacy_import.resolution import MappingTables, resolve_organisation
    from app.submissions.enums import RecipientRole
    from app.submissions.models import SubmissionRecipient

    raw = raw.strip()
    if not raw:
        return
    resolution = resolve_organisation(raw, MappingTables.empty())
    if resolution.value is None:
        return
    SubmissionRecipient.objects.get_or_create(
        submission=submission,
        organisation=resolution.value,
        defaults={"role": RecipientRole.ADDRESSEE, "note": raw[:200]},
    )


def _provenance(letter: PostipoissLetter, pairing: PostipoissPairing, basis: str) -> str:
    return (
        f"Postipoiss {letter.kirja_id}.\n"
        f"Teema alus: {PairingBasis(basis).label}; kindlus {pairing.tier}"
        f"{' (' + ', '.join(pairing.signals) + ')' if pairing.signals else ''}.\n"
        f"Kuupäev: {'kirja enda kuupäev' if letter.letter_date else 'registreerimise kuupäev'}.\n"
        f"Saaja Postipoisis: „{letter.recipient}“."
    )


def _apply(pairing: PostipoissPairing, matter: Any, *, actor: Any, basis: str) -> ApplyOutcome:
    from app.matters.enums import MatterDataClass
    from app.matters.locks import lock_matter_for_evidence_integrity
    from app.submissions.enums import SentAtPrecision, SubmissionKind, SubmissionStatus
    from app.submissions.models import Submission

    if pairing.state == PairingState.APPLIED:
        raise DomainError("See kiri on juba teemaga seotud.")
    matter = lock_matter_for_evidence_integrity(matter.pk)
    if matter.data_class == MatterDataClass.TEST:
        raise DomainError("Testandmete teemaga ajaloolist kirja ei seota.")
    letter = pairing.letter
    files = list(letter.files.select_related("binary").order_by("ordinal"))
    sent_on = _sent_on(letter)
    outcome = ApplyOutcome()
    submission = _same_act(matter, sent_on) if sent_on else None
    if submission is not None:
        outcome.joined_existing = True
    elif files and sent_on:
        version = _final_version(matter, letter, files[0], actor)
        if version is None:
            raise DomainError(f"{letter.kirja_id}: faili ei ole salvestuses.")
        submission = Submission.objects.create(
            matter=matter,
            kind=SubmissionKind.FORMAL_OPINION,
            title=letter.title[:400],
            status=SubmissionStatus.SENT,
            sent_at=timezone.make_aware(datetime.datetime.combine(sent_on, datetime.time(0, 0))),
            sent_at_precision=SentAtPrecision.DATE,
            final_version=version,
            created_by=actor,
            channel="Postipoiss",
            reference=letter.kirja_id[:200],
            notes=_provenance(letter, pairing, basis),
        )
        _attach_recipient(submission, letter.recipient)
        outcome.submission_created = True
    else:
        outcome.note = "kirjal pole faili ega kuupäeva; seotud ilma arvamuseta"
    now = timezone.now()
    for file in files:
        _, created = OpinionArchiveMatterLink.objects.get_or_create(
            binary=file.binary,
            matter=matter,
            defaults={
                "basis": ArchiveLinkBasis.APPLIED_SUBMISSION
                if outcome.submission_created
                else ArchiveLinkBasis.REVIEWED,
                "linked_by": actor,
                "linked_at": now,
                "note": f"Postipoiss {letter.kirja_id}",
            },
        )
        outcome.files_linked += int(created)
    pairing.state = PairingState.APPLIED
    pairing.basis = basis
    pairing.matter = matter
    pairing.submission = submission
    pairing.joined_existing = outcome.joined_existing
    pairing.decided_by = actor
    pairing.decided_at = now
    pairing.save(
        update_fields=[
            "state",
            "basis",
            "matter",
            "submission",
            "joined_existing",
            "decided_by",
            "decided_at",
            "updated_at",
        ]
    )
    return outcome


@dataclass
class AutoReport:
    applied: int = 0
    submissions_created: int = 0
    joined_existing: int = 0
    background: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [
            f"seotud automaatselt: {self.applied}",
            f"uusi väljasaadetud arvamusi: {self.submissions_created}",
            f"liidetud olemasoleva arvamusega (±{SAME_ACT_DAYS} päeva): {self.joined_existing}",
            f"teemata, jäetud taustaks: {self.background}",
            f"vahele jäetud: {len(self.skipped)}",
        ]
        return "\n".join(lines + [f"  {s}" for s in self.skipped[:50]])


def auto_apply(*, write: bool) -> AutoReport:
    """Apply every pending calibrated pairing. Without ``write`` it rolls back."""
    report = AutoReport()
    with transaction.atomic():
        sid = transaction.savepoint()
        rows = (
            PostipoissPairing.objects.filter(
                state=PairingState.PENDING,
                tier__in=AUTOMATIC_TIERS,
                suggested_matter__isnull=False,
            )
            .select_related("letter", "suggested_matter")
            .order_by("letter__year", "letter__register_row")
        )
        for pairing in rows:
            if not pairing.letter.files.exists():
                report.skipped.append(f"{pairing.letter.kirja_id}: faili pole")
                continue
            try:
                with transaction.atomic():
                    outcome = _apply(
                        pairing, pairing.suggested_matter, actor=None, basis=PairingBasis.AUTOMATIC
                    )
            except DomainError as refusal:
                report.skipped.append(f"{pairing.letter.kirja_id}: {refusal}")
                continue
            report.applied += 1
            report.submissions_created += int(outcome.submission_created)
            report.joined_existing += int(outcome.joined_existing)
        report.background = _background_unmatched()
        if write:
            transaction.savepoint_commit(sid)
        else:
            transaction.savepoint_rollback(sid)
    return report


#: Why a tier-F letter is background without a person (docs/adr/0216 §7).
UNMATCHED_NOTE = "Teemat ei leitud; taustaks omaniku otsusega 18.11.2026."


def _background_unmatched() -> int:
    """Tier F — no Teema found — is background from the start (owner,
    2026-11-18). Only a pairing nobody has decided: one a lawyer took back from
    background stays waiting, and a rerun does not overrule that person."""
    return PostipoissPairing.objects.filter(
        state=PairingState.PENDING, tier=PairingTier.F, decided_at__isnull=True
    ).update(
        state=PairingState.BACKGROUND,
        basis=PairingBasis.AUTOMATIC,
        decided_at=timezone.now(),
        note=UNMATCHED_NOTE,
        updated_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# A lawyer's decisions
# ---------------------------------------------------------------------------


def pairings_for(user: Any) -> QuerySet[PostipoissPairing]:
    """Every pairing; a Teema the reader may not see is not shown on the row.

    The letters are the department's own outgoing correspondence and are read
    like the opinion archive. What the reader may not see is the *Teema*: the
    page renders a suggested or chosen Teema only through `visible_matter`.
    """
    return PostipoissPairing.objects.select_related(
        "letter", "suggested_matter", "matter", "decided_by"
    ).prefetch_related("letter__files__binary")


def visible_matter(user: Any, matter: Any) -> Any:
    from app.matters.models import Matter

    if matter is None:
        return None
    return Matter.objects.visible_to(user).filter(pk=matter.pk).first()


def matter_by_reference(user: Any, reference: str) -> Any:
    from app.matters.enums import MatterDataClass
    from app.matters.models import Matter

    pair = _reference(reference)
    if pair is None:
        raise DomainError("Teema viide on kujul aasta_number, näiteks 2014_85.")
    matter = (
        Matter.objects.visible_to(user)
        .filter(reference_year=pair[0], reference_number=pair[1])
        .exclude(data_class=MatterDataClass.TEST)
        .first()
    )
    if matter is None:
        raise DomainError(f"Teemat {reference} ei leitud.")
    return matter


def _lock(pairing: PostipoissPairing) -> PostipoissPairing:
    return PostipoissPairing.objects.select_for_update().select_related("letter").get(pk=pairing.pk)


@transaction.atomic
def accept(pairing: PostipoissPairing, *, actor: Any) -> ApplyOutcome:
    locked = _lock(pairing)
    if locked.state != PairingState.PENDING:
        raise DomainError("Sellel kirjal on juba otsus.")
    matter = visible_matter(actor, locked.suggested_matter)
    if matter is None:
        raise DomainError("Pakutud teemat ei ole; vali teema viitega.")
    return _apply(locked, matter, actor=actor, basis=PairingBasis.ACCEPTED)


@transaction.atomic
def choose(pairing: PostipoissPairing, *, actor: Any, reference: str) -> ApplyOutcome:
    locked = _lock(pairing)
    if locked.state == PairingState.APPLIED:
        raise DomainError("See kiri on juba teemaga seotud.")
    matter = matter_by_reference(actor, reference)
    return _apply(locked, matter, actor=actor, basis=PairingBasis.CHOSEN)


@transaction.atomic
def leave_as_background(pairing: PostipoissPairing, *, actor: Any, note: str = "") -> None:
    locked = _lock(pairing)
    if locked.state != PairingState.PENDING:
        raise DomainError("Sellel kirjal on juba otsus.")
    locked.state = PairingState.BACKGROUND
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.note = note[:2000]
    locked.save(update_fields=["state", "decided_by", "decided_at", "note", "updated_at"])


@transaction.atomic
def reopen(pairing: PostipoissPairing, *, actor: Any) -> None:
    """Background back to waiting. An applied pairing is not reopened here:
    it wrote a sent opinion, and taking that back is the Teema page's own act."""
    locked = _lock(pairing)
    if locked.state != PairingState.BACKGROUND:
        raise DomainError("Tagasi saab võtta ainult taustaks jäetud kirja.")
    locked.state = PairingState.PENDING
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=["state", "decided_by", "decided_at", "updated_at"])
