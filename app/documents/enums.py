from __future__ import annotations

from django.db import models


class DocumentRole(models.TextChoices):
    """What the document is, not where it is stored."""

    INCOMING_AUTHORITY = "INCOMING_AUTHORITY", "Saabunud ametlik dokument"
    ORIGINAL_EMAIL = "ORIGINAL_EMAIL", "Algne e-kiri"
    MEMBER_FEEDBACK = "MEMBER_FEEDBACK", "Liikme tagasiside"
    EXTERNAL_POSITION = "EXTERNAL_POSITION", "Välise osapoole seisukoht"
    KODA_SUBMISSION_FINAL = "KODA_SUBMISSION_FINAL", "Koja väljasaadetud arvamus"
    # An attachment's business meaning is not knowable from the fact that it
    # arrived attached to something. Mail comes from ministries, members,
    # associations and colleagues alike, so calling every attachment an
    # incoming official document would file half of them wrongly. This role
    # says exactly what is known — it came out of a message — and a lawyer
    # reclassifies it when they know more (Stage-2B brief 26).
    EMAIL_ATTACHMENT = "EMAIL_ATTACHMENT", "E-kirja manus"
    OUTCOME_EVIDENCE = "OUTCOME_EVIDENCE", "Tulemuse tõend"
    WORKING_DOCUMENT = "WORKING_DOCUMENT", "Töödokument"
    OTHER = "OTHER", "Muu"


class RetentionClass(models.TextChoices):
    """Placeholder classes pending the privacy/legal retention decision.

    The values exist from the first migration because retention and legal hold
    cannot be retrofitted onto evidence that has already been captured
    (master specification 16.2). The policy behind them is an open decision.
    """

    UNCLASSIFIED = "UNCLASSIFIED", "Määramata"
    POLICY_RECORD = "POLICY_RECORD", "Poliitikakirje"
    RAW_EMAIL = "RAW_EMAIL", "Algne e-kiri"
    MEMBER_CONFIDENTIAL = "MEMBER_CONFIDENTIAL", "Liikme konfidentsiaalne materjal"


class MalwareScanState(models.TextChoices):
    PENDING = "PENDING", "Ootel"
    CLEAN = "CLEAN", "Puhas"
    INFECTED = "INFECTED", "Nakatunud"
    QUARANTINED = "QUARANTINED", "Karantiinis"
    SKIPPED = "SKIPPED", "Vahele jäetud"
    ERROR = "ERROR", "Viga"


class ExtractionState(models.TextChoices):
    """What is known about extracting text from one exact binary.

    ``DONE`` means every derivative the format requires was written and
    committed. It must never mean "a worker had a go": a version marked done
    after a partial parse is invisible to every retry path there is, and the
    missing half of a 200-page draft is exactly the half nobody notices is
    missing (Stage-2B brief 10, 102).
    """

    PENDING = "PENDING", "Ootel"
    # Claimed by a worker. A worker that dies here leaves the row in this state,
    # which is why claiming stamps a time and stale claims are reclaimed rather
    # than waited on.
    PROCESSING = "PROCESSING", "Töötlemisel"
    DONE = "DONE", "Tehtud"
    FAILED = "FAILED", "Ebaõnnestus"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Ei kohaldu"


class DerivativeKind(models.TextChoices):
    """What was derived from one exact binary.

    Every value here is rebuildable from the evidence it points at. None of it
    is evidence: the original bytes are what Koda received or sent, and a
    parser's opinion about them is a convenience that may be deleted and
    regenerated at any time (docs/adr/0003, docs/adr/0014).
    """

    EXTRACTED_TEXT = "EXTRACTED_TEXT", "Eraldatud tekst"
    OCR_TEXT = "OCR_TEXT", "OCR tekst"
    SAFE_PREVIEW = "SAFE_PREVIEW", "Turvaline eelvaade"
    THUMBNAIL = "THUMBNAIL", "Pisipilt"
    EMAIL_METADATA = "EMAIL_METADATA", "E-kirja metaandmed"


class DerivativeStatus(models.TextChoices):
    """The lifecycle that lets a parser upgrade fail without losing search.

    A new parser version builds alongside the old one and only becomes ACTIVE
    when it has finished. Until then the previous representation keeps serving,
    which is the difference between an upgrade that degrades and one that
    silently empties a lawyer's search results (Stage-2B brief 8).
    """

    BUILDING = "BUILDING", "Koostamisel"
    ACTIVE = "ACTIVE", "Kehtiv"
    SUPERSEDED = "SUPERSEDED", "Asendatud"
    FAILED = "FAILED", "Ebaõnnestus"


class TextSource(models.TextChoices):
    """Where a fragment's characters actually came from.

    Presenting OCR output as if it were the document's own text is a provenance
    defect: OCR is a guess with a known error rate, and a lawyer quoting it into
    an opinion is entitled to know that.
    """

    NATIVE = "NATIVE", "Dokumendi oma tekst"
    OCR = "OCR", "OCR"


class LocatorKind(models.TextChoices):
    """How to say where inside a file a fragment sits.

    A format that does not expose rendered pagination does not get a page
    number. "Lõik 12" is honest and useful; "lk 4" invented from a paragraph
    index sends somebody to the wrong place in a 90-page draft and costs more
    trust than it saves (Stage-2B brief 7, 16).
    """

    PAGE = "PAGE", "Lehekülg"
    SLIDE = "SLIDE", "Slaid"
    SHEET = "SHEET", "Tööleht"
    SECTION = "SECTION", "Osa"
    LINE_RANGE = "LINE_RANGE", "Read"
    BODY = "BODY", "Sisu"
    NONE = "NONE", "Määramata"
