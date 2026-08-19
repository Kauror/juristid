from __future__ import annotations

from django.db import models


class DocumentRole(models.TextChoices):
    """What the document is, not where it is stored."""

    INCOMING_AUTHORITY = "INCOMING_AUTHORITY", "Saabunud ametlik dokument"
    ORIGINAL_EMAIL = "ORIGINAL_EMAIL", "Algne e-kiri"
    MEMBER_FEEDBACK = "MEMBER_FEEDBACK", "Liikme tagasiside"
    EXTERNAL_POSITION = "EXTERNAL_POSITION", "Välise osapoole seisukoht"
    KODA_SUBMISSION_FINAL = "KODA_SUBMISSION_FINAL", "Koja väljasaadetud arvamus"
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
    PENDING = "PENDING", "Ootel"
    DONE = "DONE", "Tehtud"
    FAILED = "FAILED", "Ebaõnnestus"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Ei kohaldu"
