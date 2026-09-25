"""A small synthetic search corpus with every source kind, for Round-6 tests.

Deterministic, synthetic and Estonian-shaped: statute names, ministries with
their abbreviations, inflected legal nouns, a restricted Matter, restricted
children on an ordinary one, a READER who collaborates on one restricted file.
No real Koda data.

Built with ``bulk_create`` and one ``rebuild_all``, which is how the projection
is filled in production after an import, so the rows these tests read are
exactly the rows the indexer writes.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.utils import timezone

from app.accounts.models import User
from app.core.text import normalize_for_matching
from app.documents.enums import DerivativeStatus
from app.documents.models import (
    Document,
    DocumentDerivative,
    DocumentTextFragment,
    DocumentVersion,
)
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind, RecordMode
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterSourceOrganisation,
    TagAssignment,
)
from app.organisations.models import Organisation, OrganisationAlias
from app.search.indexing import rebuild_all
from app.submissions.enums import RecipientRole, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from app.taxonomy.models import PolicyArea, Tag, TagAlias

NOUNS = (
    ("seadus", "seaduse", "seadust"),
    ("arvamus", "arvamuse", "arvamust"),
    ("tähtaeg", "tähtaja", "tähtaega"),
    ("seisukoht", "seisukoha", "seisukohta"),
    ("määrus", "määruse", "määrust"),
    ("ettepanek", "ettepaneku", "ettepanekut"),
    ("riigihange", "riigihanke", "riigihanget"),
    ("ettevõte", "ettevõtte", "ettevõtet"),
    ("maks", "maksu", "maksu"),
    ("keskkond", "keskkonna", "keskkonda"),
    ("eelnõu", "eelnõu", "eelnõu"),
    ("kooskõlastus", "kooskõlastuse", "kooskõlastust"),
    ("põllumajandus", "põllumajanduse", "põllumajandust"),
    ("üleminekuaeg", "üleminekuaja", "üleminekuaega"),
    ("halduskoormus", "halduskoormuse", "halduskoormust"),
    ("pakend", "pakendi", "pakendit"),
)
STATUTES = (
    "Tarbijakaitseseadus",
    "Pakendiseadus",
    "Kliimaseadus",
    "Riigihangete seadus",
    "Maksukorralduse seadus",
    "Töölepingu seadus",
    "Jäätmeseadus",
    "Käibemaksuseadus",
)
ORGANISATIONS = (
    ("Majandus- ja Kommunikatsiooniministeerium", ("MKM",)),
    ("Kliimaministeerium", ()),
    ("Rahandusministeerium", ("RaM",)),
    ("Põllumajandustootjate Liit", ("PTL",)),
    ("Eesti Tööandjate Keskliit", ("ETKL",)),
    ("Maksu- ja Tolliamet", ("MTA", "EMTA")),
)
VERBS = ("esitas", "kooskõlastas", "arutas", "muutis", "täpsustas", "saatis", "toetas")
ADJECTIVES = ("uue", "kehtiva", "täiendava", "riikliku", "ajutise", "olulise")
PEOPLE = ("Mari Maasikas", "Jüri Juurikas", "Kärt Kask", "Tõnu Tamm")

#: Words each placed in exactly one row of one kind, so a test can ask for a
#: precise source and see nothing else.
SENTINELS = {
    "matter_title": "Zqxmatterpealkiri",
    "development_note": "zqxmärkmesisu",
    "engagement_title": "Zqxkaasamine",
    "position_summary": "zqxseisukohatekst",
    "submission_summary": "zqxarvamusekokkuvõte",
    "entry_body": "zqxsissekandesisu",
    "fragment_body": "zqxlehekülgsisu",
    "document_title": "Zqxdokumendinimi",
    "restricted_matter_title": "Zqxpiiratudteema",
    "restricted_development_note": "zqxpiiratudmärge",
    "restricted_document_title": "Zqxpiiratuddokument",
    "collaborator_matter_title": "Zqxkaasatudteema",
}


@dataclass
class Corpus:
    specialist: User
    other_specialist: User
    department_head: User
    reader: User
    administrator: User
    matters: list[Matter] = field(default_factory=list)
    restricted_matter: Matter | None = None
    collaborator_matter: Matter | None = None
    exact_title: str = ""
    submission_reference: str = ""
    document_filename: str = ""


def _user(role: str, name: str) -> User:
    email = f"{role.lower()}.{uuid.uuid4().hex[:8]}@example.invalid"
    return User.objects.create(email=email, upn=email, display_name=name, role=role)


def build(*, matters: int = 40, seed: str = "juristid-search-corpus") -> Corpus:
    rng = random.Random(seed)  # noqa: S311 - synthetic test text, not a secret
    now = timezone.now()

    corpus = Corpus(
        specialist=_user("SPECIALIST", PEOPLE[0]),
        other_specialist=_user("SPECIALIST", PEOPLE[1]),
        department_head=_user("DEPARTMENT_HEAD", PEOPLE[2]),
        reader=_user("READER", PEOPLE[3]),
        administrator=_user("ADMINISTRATOR", "Süsteemi Haldur"),
    )
    specialists = [corpus.specialist, corpus.other_specialist]

    organisations = []
    for name, aliases in ORGANISATIONS:
        organisation = Organisation.objects.create(
            name=name, normalized_name=normalize_for_matching(name)
        )
        organisations.append(organisation)
        for alias in aliases:
            OrganisationAlias.objects.create(
                organisation=organisation,
                alias=alias,
                normalized_alias=normalize_for_matching(alias),
            )
    areas = [
        PolicyArea.objects.create(key=f"r6-{i}", name_et=name)
        for i, name in enumerate(("Maksundus", "Keskkond", "Tööõigus", "Põllumajandus"))
    ]
    tag = Tag.objects.create(key="r6-pakendid", name_et="pakendid")
    TagAlias.objects.create(tag=tag, alias="pakendiaruandlus", normalized_alias="pakendiaruandlus")

    def phrase() -> str:
        return f"{rng.choice(ADJECTIVES)} {rng.choice(rng.choice(NOUNS))}"

    def sentence() -> str:
        return f"{rng.choice(ORGANISATIONS)[0]} {rng.choice(VERBS)} {phrase()} ja {phrase()}."

    def paragraph(sentences: int) -> str:
        return " ".join(sentence() for _ in range(sentences))

    rows: list[Matter] = []
    for n in range(matters):
        statute = STATUTES[n % len(STATUTES)]
        noun = NOUNS[n % len(NOUNS)]
        title = f"{statute} {('muutmise', 'täiendamise')[n % 2]} seaduse eelnõu ({noun[1]} kord)"
        archive = n % 5 == 4
        rows.append(
            Matter(
                title=title,
                alternate_titles=[f"{statute.split()[0]} ja {noun[0]}"] if n % 7 == 0 else [],
                reference_year=None if archive else 2020 + n % 6,
                reference_number=None if archive else 9000 + n,
                record_mode=RecordMode.ARCHIVE if archive else RecordMode.FULL,
                is_open=not archive,
                owner=specialists[n % 2],
                visibility="NORMAL",
                addressee_organisation=organisations[n % len(organisations)],
                brief_summary=paragraph(2),
            )
        )
    rows.append(
        Matter(
            title=f"{SENTINELS['matter_title']} teema",
            owner=corpus.specialist,
            visibility="NORMAL",
            reference_year=2026,
            reference_number=9901,
        )
    )
    rows.append(
        Matter(
            title=f"{SENTINELS['restricted_matter_title']} teema",
            owner=corpus.other_specialist,
            visibility="RESTRICTED",
            reference_year=2026,
            reference_number=9902,
        )
    )
    rows.append(
        Matter(
            title=f"{SENTINELS['collaborator_matter_title']} teema",
            owner=corpus.other_specialist,
            visibility="RESTRICTED",
            reference_year=2026,
            reference_number=9903,
        )
    )
    Matter.objects.bulk_create(rows)
    corpus.matters = list(
        Matter.objects.filter(reference_number__gte=9000).order_by("reference_number")
    )
    corpus.matters += list(
        Matter.objects.filter(reference_number__isnull=True, owner__in=specialists).order_by(
            "title"
        )
    )
    by_reference = {matter.reference_number: matter for matter in corpus.matters}
    corpus.restricted_matter = by_reference[9902]
    corpus.collaborator_matter = by_reference[9903]
    corpus.exact_title = by_reference[9003].title

    Matter.collaborators.through.objects.bulk_create(
        [
            Matter.collaborators.through(
                matter_id=corpus.collaborator_matter.pk, user_id=corpus.reader.pk
            ),
            Matter.collaborators.through(
                matter_id=corpus.collaborator_matter.pk, user_id=corpus.specialist.pk
            ),
        ]
    )
    MatterSourceOrganisation.objects.bulk_create(
        [
            MatterSourceOrganisation(matter=matter, organisation=organisations[(i + 2) % 6])
            for i, matter in enumerate(corpus.matters)
            if i % 3 == 0
        ]
    )
    Matter.policy_areas.through.objects.bulk_create(
        [
            Matter.policy_areas.through(matter_id=matter.pk, policyarea_id=areas[i % 4].pk)
            for i, matter in enumerate(corpus.matters)
            if i % 2 == 0
        ]
    )
    TagAssignment.objects.bulk_create(
        [TagAssignment(matter=matter, tag=tag) for matter in corpus.matters[:5]]
    )

    ordinary = [m for m in corpus.matters if m.visibility == "NORMAL"]
    documents, versions, developments, engagements, positions = [], [], [], [], []
    submissions, entries = [], []

    def day() -> date:
        return date(2021, 1, 1) + timedelta(days=rng.randint(0, 1500))

    for i, matter in enumerate(corpus.matters):
        restricted_child = i % 9 == 1
        document = Document(
            id=uuid.uuid4(),
            matter=matter,
            title=f"Seletuskiri {NOUNS[i % len(NOUNS)][1]} {i}",
            visibility_override="RESTRICTED" if restricted_child else "",
        )
        documents.append(document)
        versions.append(
            DocumentVersion(
                id=uuid.uuid4(),
                document=document,
                version_number=1,
                storage_key=f"r6/{document.pk}",
                original_filename=f"seletuskiri_{NOUNS[i % len(NOUNS)][0]}_{i}.docx",
                mime_type="application/pdf",
                size_bytes=100,
                sha256="0" * 64,
                acquired_at=now,
            )
        )
        developments.append(
            MatterProceduralDevelopment(
                matter=matter,
                title=f"{ORGANISATIONS[i % 6][0]} {VERBS[i % len(VERBS)]} {phrase()}",
                note=paragraph(3),
                occurred_on=day(),
                visibility_override="RESTRICTED" if restricted_child else "",
                created_by=matter.owner,
            )
        )
        if i % 2 == 0:
            engagements.append(
                MatterEngagement(
                    matter=matter,
                    kind=EngagementKind.values[i % len(EngagementKind.values)],
                    title=f"Liikmete küsitlus: {phrase()}",
                    note=paragraph(1),
                    occurred_on=day(),
                    created_by=matter.owner,
                )
            )
            positions.append(
                MatterExternalPosition(
                    matter=matter,
                    organisation=organisations[i % 6],
                    provenance="DISCOVERED",
                    summary=paragraph(2),
                    stated_on=day(),
                    created_by=matter.owner,
                )
            )
            submissions.append(
                Submission(
                    matter=matter,
                    title=f"Koja arvamus: {phrase()}",
                    summary=paragraph(2),
                    reference=f"7-1/{2020 + i % 6}/{i}",
                    status=SubmissionStatus.DRAFT,
                )
            )
        if i % 3 == 0:
            entries.append(
                Entry(
                    matter=matter,
                    author=matter.owner,
                    kind=EntryKind.values[i % len(EntryKind.values)],
                    occurred_at=now,
                    body=f"<p>{paragraph(2)}</p>",
                )
            )

    # One of each sentinel, where the tests expect to find it.
    first = ordinary[0]
    documents.append(
        Document(id=uuid.uuid4(), matter=first, title=f"{SENTINELS['document_title']} lisa")
    )
    versions.append(
        DocumentVersion(
            id=uuid.uuid4(),
            document=documents[-1],
            version_number=1,
            storage_key="r6/sentinel",
            original_filename="lisa_zqx.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            sha256="0" * 64,
            acquired_at=now,
        )
    )
    documents.append(
        Document(
            id=uuid.uuid4(),
            matter=first,
            title=f"{SENTINELS['restricted_document_title']} lisa",
            visibility_override="RESTRICTED",
        )
    )
    versions.append(
        DocumentVersion(
            id=uuid.uuid4(),
            document=documents[-1],
            version_number=1,
            storage_key="r6/restricted-sentinel",
            original_filename="piiratud_zqx.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            sha256="0" * 64,
            acquired_at=now,
        )
    )
    developments.append(
        MatterProceduralDevelopment(
            matter=first,
            title="Ministeerium saatis uue versiooni",
            note=f"Märkus: {SENTINELS['development_note']} ja {phrase()}.",
            created_by=first.owner,
        )
    )
    developments.append(
        MatterProceduralDevelopment(
            matter=first,
            title="Piiratud märge",
            note=f"Märkus: {SENTINELS['restricted_development_note']}.",
            visibility_override="RESTRICTED",
            created_by=first.owner,
        )
    )
    engagements.append(
        MatterEngagement(
            matter=first,
            kind=EngagementKind.values[0],
            title=f"{SENTINELS['engagement_title']} küsitlus",
            created_by=first.owner,
        )
    )
    positions.append(
        MatterExternalPosition(
            matter=first,
            organisation=organisations[3],
            provenance="DISCOVERED",
            summary=f"Seisukoht: {SENTINELS['position_summary']}.",
            created_by=first.owner,
        )
    )
    submissions.append(
        Submission(
            matter=first,
            title="Koja arvamus pakendiseaduse kohta",
            summary=f"Kokkuvõte: {SENTINELS['submission_summary']}.",
            reference="7-1/2024/15",
            status=SubmissionStatus.DRAFT,
        )
    )
    entries.append(
        Entry(
            matter=first,
            author=corpus.specialist,
            kind=EntryKind.values[0],
            occurred_at=now,
            body=f"<p>Sisu {SENTINELS['entry_body']}.</p>",
        )
    )
    corpus.submission_reference = "7-1/2024/15"
    corpus.document_filename = "lisa_zqx.pdf"

    Document.objects.bulk_create(documents)
    DocumentVersion.objects.bulk_create(versions)
    for document, version in zip(documents, versions, strict=True):
        document.current_version_id = version.pk
    Document.objects.bulk_update(documents, ["current_version"])

    derivatives, fragments = [], []
    for i, version in enumerate(versions[:-2]):
        if i % 4 != 0:
            continue
        derivative = DocumentDerivative(
            id=uuid.uuid4(),
            version=version,
            kind="EXTRACTED_TEXT",
            generator="r6-test",
            generator_version="1",
            status=DerivativeStatus.ACTIVE,
        )
        derivatives.append(derivative)
        for page in range(3):
            text = paragraph(6)
            if i == 0 and page == 1:
                text += f" {SENTINELS['fragment_body']}."
            fragments.append(
                DocumentTextFragment(
                    derivative=derivative, ordinal=page, text=text, locator_label=f"lk {page + 1}"
                )
            )
    DocumentDerivative.objects.bulk_create(derivatives)
    DocumentTextFragment.objects.bulk_create(fragments)
    MatterProceduralDevelopment.objects.bulk_create(developments)
    MatterEngagement.objects.bulk_create(engagements)
    MatterExternalPosition.objects.bulk_create(positions)
    Submission.objects.bulk_create(submissions)
    SubmissionRecipient.objects.bulk_create(
        [
            SubmissionRecipient(
                submission=submission,
                organisation=organisations[i % 6],
                role=RecipientRole.values[0],
            )
            for i, submission in enumerate(submissions)
        ]
    )
    Entry.objects.bulk_create(entries)

    rebuild_all()
    return corpus


def personas(corpus: Corpus) -> dict[str, Any]:
    """Every kind of reader the authorization rules distinguish."""
    from app.core.authorization import DEPARTMENT_VIEWER

    return {
        "specialist": corpus.specialist,
        "other_specialist": corpus.other_specialist,
        "department_head": corpus.department_head,
        "reader": corpus.reader,
        "administrator": corpus.administrator,
        "department_viewer": DEPARTMENT_VIEWER,
    }
