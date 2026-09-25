"""The Estonian lawyer-query corpus: what a search should find, and why (ENG-031).

Specification §14.5 and ADR 0006 ask for a maintained corpus of real lawyer
queries as the regression suite for ranking and indexing. These are synthetic —
no Koda text — but shaped like the queries the audit reproduced: a base form
typed against an inflected one, a word typed without õ/ä/ö/ü/š/ž, a statute
name inside a compound, the start of a long word. Each case names the row it
must reach and the reason it should (or, for the residuals, why it cannot).

**The residuals are part of the contract.** PostgreSQL's Estonian stemmer does
not undo stem-alternating inflection — `tähtaeg`/`tähtaja`,
`riigihange`/`riigihanke`, `keskkond`/`keskkonna` — and nothing in this round
pretends otherwise: no synonym table, no stemmer written in Python. Those
cases are listed as *not found*, with the alternation named, so a future
change that does solve one of them turns a line of this file green on purpose
rather than by accident.

The targets are one row of each kind the lawyers write: a Teema, a Märge, a
Kaasamine, a Väline seisukoht, a sent-opinion draft, a document and a legacy
entry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.utils import timezone

from app.accounts.models import User
from app.documents.models import Document, DocumentVersion
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.organisations.models import Organisation
from app.search.indexing import rebuild_all
from app.search.models import SearchSourceKind
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission


@dataclass(frozen=True)
class Case:
    query: str
    target: str
    found: bool
    why: str


TEXT = {
    "matter_statute": "Tarbijakaitseseaduse muutmise seaduse eelnõu",
    "matter_procurement": "Riigihangete seaduse täiendamine",
    "matter_farmers": "Põllumajandustootjate Liidu pöördumine",
    "development_title": "Ministeerium saatis Tarbijakaitseseaduse eelnõu uue versiooni",
    "development_note": (
        "Arvamuse esitamise tähtaeg on ületatud; ootame ministeeriumi seisukohta."
    ),
    "second_development_note": (
        "Tähtaja ületanud taotlused lükati tagasi. Töölepingu seaduse muudatus "
        "puudutab tööandjate kohustusi."
    ),
    "engagement_title": "Liikmete küsitlus energiajulgeoleku teemal",
    "engagement_note": "Küsitlus puudutas ettevõtete halduskoormust ja keskkonnatasusid.",
    "position_organisation": "Põllumajandustootjate Liit",
    "position_summary": ("Liit toetab üleminekuaja pikendamist ja maksuerisust väiketootjatele."),
    "submission_title": "Koja arvamus käibemaksuseaduse muudatuste kohta",
    "submission_summary": (
        "Koda ei toeta aktsiisitõusu; ettepanek on kaaluda riigihanke korra lihtsustamist."
    ),
    "document_title": "Seletuskiri määruse eelnõu juurde",
    "document_filename": "seletuskiri_maaruse_eelnou.docx",
    "entry_body": (
        "Kohtumisel arutati keskkonnatasude seaduse rakendamist ja Majandus- ja "
        "Kommunikatsiooniministeeriumi seisukohta. Kohal olid Šokolaaditootjate "
        "Liidu ja žürii esindajad."
    ),
}

CASES: tuple[Case, ...] = (
    # -- a base form typed against an inflected one --------------------------
    Case(
        "Tarbijakaitseseadus", "development", True, "nominative; the Märge title has the genitive"
    ),
    Case(
        "Tarbijakaitseseadus",
        "matter_statute",
        True,
        "nominative; the Teema title has the genitive",
    ),
    Case("seadus", "second_development", True, "nominative; the note says «seaduse»"),
    Case("arvamus", "development", True, "nominative; the note says «Arvamuse»"),
    Case("seisukoht", "development", True, "nominative; the note has the partitive «seisukohta»"),
    Case("seisukoht", "entry", True, "nominative; the entry has «seisukohta»"),
    Case(
        "energiajulgeolek", "engagement", True, "nominative; the Kaasamine title has the genitive"
    ),
    Case("halduskoormus", "engagement", True, "nominative; the note has the partitive"),
    Case(
        "ettevõte", "engagement", True, "nominative; the note has the plural genitive «ettevõtete»"
    ),
    Case("maksuerisus", "position", True, "nominative; the summary has the partitive"),
    Case("käibemaksuseadus", "submission", True, "nominative; the opinion title has the genitive"),
    Case("Riigihangete seadus", "matter_procurement", True, "statute name, base form"),
    Case("kohustus", "second_development", True, "nominative; the note has «kohustusi»"),
    Case("tööandja", "second_development", True, "the stemmer maps «tööandjate» to it"),
    Case("pikendamine", "position", True, "nominative; the summary has «pikendamist»"),
    Case("lihtsustamine", "submission", True, "nominative; the summary has «lihtsustamist»"),
    Case("väiketootja", "position", True, "base form of «väiketootjatele»"),
    # -- compounds, and the start of a long word --------------------------------
    Case("Tarbijakaitse", "development", True, "first half of the compound statute name"),
    Case("aktsiis", "submission", True, "first half of «aktsiisitõusu»"),
    Case("keskkonnatasu", "engagement", True, "base form of «keskkonnatasusid»"),
    Case("keskkonnatasu", "entry", True, "base form of «keskkonnatasude»"),
    Case("riigihank", "submission", True, "the stem a lawyer types for «riigihanke»"),
    Case(
        "Kommunikatsiooniministeerium", "entry", True, "second half of a hyphenated ministry name"
    ),
    Case("Majandus- ja Kommunikatsiooniministeerium", "entry", True, "the ministry's full name"),
    # -- typed without diacritics ------------------------------------------------
    Case("Pollumajandustootjate Liit", "position", True, "õ typed as o (the audit's case)"),
    Case("tahtaja uletanud", "second_development", True, "ä and ü typed as a and u"),
    Case("Tahtaeg on uletatud", "development", True, "ä and ü typed as a and u"),
    Case("maaruse eelnou", "document", True, "ä and õ typed as a and o"),
    Case("kaibemaksuseaduse", "submission", True, "ä typed as a"),
    Case("liikmete kusitlus", "engagement", True, "ü typed as u"),
    Case("tooandjate", "second_development", True, "ö typed as o"),
    Case("Liidu poordumine", "matter_farmers", True, "ö typed as o in a Teema title"),
    Case("eelnou", "development", True, "õ typed as o"),
    Case("sokolaaditootjate", "entry", True, "š typed as s"),
    Case("zurii", "entry", True, "ž and ü typed as z and u"),
    # -- exact forms: must stay found -----------------------------------------------
    Case("tähtaja", "second_development", True, "the exact form"),
    Case("tähtaeg", "development", True, "the exact form"),
    Case("küsitlus", "engagement", True, "the exact form"),
    Case("Põllumajandustootjate Liit", "position", True, "the organisation's own name"),
    Case("üleminekuaja", "position", True, "the exact form in the summary"),
    Case("ministeeriumi seisukohta", "development", True, "exact forms, two words"),
    Case("Seletuskiri", "document", True, "the document's name"),
    Case("maaruse", "document", True, "part of the file name"),
    Case("ettepanek", "submission", True, "the exact form"),
    Case("seaduse eelnõu uue versiooni", "development", True, "exact forms, a phrase"),
    # -- the residuals: stem alternation this round does not solve ------------------
    Case("tähtaeg", "second_development", False, "stem alternation aeg → aja («Tähtaja»)"),
    Case("üleminekuaeg", "position", False, "stem alternation aeg → aja («üleminekuaja»)"),
    Case("riigihange", "submission", False, "stem alternation ge → ke («riigihanke»)"),
    Case("keskkond", "entry", False, "stem alternation nd → nn inside «keskkonnatasude»"),
    Case("ettepaneku", "submission", False, "a longer inflected query than the text's form"),
    # -- nothing ---------------------------------------------------------------------
    Case("xylofonimängija", "nothing", False, "no such word anywhere"),
)


@dataclass
class RecallWorld:
    user: User
    targets: dict[str, tuple[str, object]]


def build() -> RecallWorld:
    """One row of each kind, with the text above, and a full rebuild."""
    email = f"recall.{uuid.uuid4().hex[:8]}@example.invalid"
    user = User.objects.create(
        email=email, upn=email, display_name="Mari Maasikas", role="SPECIALIST"
    )
    now = timezone.now()
    farmers = Organisation.objects.create(
        name=TEXT["position_organisation"], normalized_name="pollumajandustootjate liit"
    )

    def matter(title: str, number: int) -> Matter:
        return Matter.objects.create(
            title=title, owner=user, reference_year=2026, reference_number=880000 + number
        )

    statute = matter(TEXT["matter_statute"], 1)
    procurement = matter(TEXT["matter_procurement"], 2)
    farmer_matter = matter(TEXT["matter_farmers"], 3)
    carrier = matter("Kandev teema", 4)

    development = MatterProceduralDevelopment.objects.create(
        matter=carrier,
        title=TEXT["development_title"],
        note=TEXT["development_note"],
        created_by=user,
    )
    second_development = MatterProceduralDevelopment.objects.create(
        matter=carrier, title="Teade", note=TEXT["second_development_note"], created_by=user
    )
    engagement = MatterEngagement.objects.create(
        matter=carrier,
        kind=EngagementKind.values[0],
        title=TEXT["engagement_title"],
        note=TEXT["engagement_note"],
        created_by=user,
    )
    position = MatterExternalPosition.objects.create(
        matter=carrier,
        organisation=farmers,
        provenance="DISCOVERED",
        summary=TEXT["position_summary"],
        created_by=user,
    )
    submission = Submission.objects.create(
        matter=carrier,
        title=TEXT["submission_title"],
        summary=TEXT["submission_summary"],
        status=SubmissionStatus.DRAFT,
    )
    document = Document.objects.create(matter=carrier, title=TEXT["document_title"])
    version = DocumentVersion.objects.create(
        document=document,
        version_number=1,
        storage_key=f"recall/{document.pk}",
        original_filename=TEXT["document_filename"],
        mime_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        acquired_at=now,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])
    entry = Entry.objects.create(
        matter=carrier,
        author=user,
        kind=EntryKind.values[0],
        occurred_at=now,
        body=f"<p>{TEXT['entry_body']}</p>",
    )
    rebuild_all()
    return RecallWorld(
        user=user,
        targets={
            "matter_statute": (SearchSourceKind.MATTER, statute.pk),
            "matter_procurement": (SearchSourceKind.MATTER, procurement.pk),
            "matter_farmers": (SearchSourceKind.MATTER, farmer_matter.pk),
            "development": (SearchSourceKind.PROCEDURAL_DEVELOPMENT, development.pk),
            "second_development": (SearchSourceKind.PROCEDURAL_DEVELOPMENT, second_development.pk),
            "engagement": (SearchSourceKind.ENGAGEMENT, engagement.pk),
            "position": (SearchSourceKind.EXTERNAL_POSITION, position.pk),
            "submission": (SearchSourceKind.SUBMISSION, submission.pk),
            "document": (SearchSourceKind.DOCUMENT, document.pk),
            "entry": (SearchSourceKind.ENTRY, entry.pk),
            "nothing": ("", None),
        },
    )


def outcomes(world: RecallWorld, search_documents) -> list[tuple[Case, bool]]:
    """Whether each case's target is among the results, for the given search."""
    results = []
    for case in CASES:
        kind, pk = world.targets[case.target]
        rows = {
            (row_kind, row_pk)
            for row_kind, row_pk in search_documents(query=case.query, user=world.user).values_list(
                "source_kind", "source_object_id"
            )
        }
        hit = (kind, pk) in rows if pk is not None else bool(rows)
        results.append((case, hit))
    return results
