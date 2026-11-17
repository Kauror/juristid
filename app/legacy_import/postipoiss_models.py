"""Koda's outgoing letters from Postipoiss, and which Teema each belongs to (docs/adr/0216).

Postipoiss is the SharePoint register in which the Chamber numbered every outgoing
letter from 2009 (Kirja ID «V2014-0072»). Its letters were exported once, their
signed containers opened, and every letter was matched offline against the
department's register — the rows Juristid's Teemad came from. The result arrives
as a sealed package (`postipoiss_import`), and three rows record it here:

``PostipoissLetter`` is **one registered letter**, with or without the file.
About a fifth of the register never had its file stored (Postipoiss kept only the
row until about 2015), and a registered letter without bytes is still a fact
about what Koda sent — so the letter is its own row rather than an archive
occurrence, which by definition is a file.

``PostipoissLetterFile`` is one file of that letter, its bytes held once in the
archive's evidence store as an `OpinionArchiveBinary` — the same holder, key
scheme and download route as every other archived letter (docs/adr/0023).

``PostipoissPairing`` is the **decision** about the letter: the Teema the offline
matching proposed and how confident it was, and then what happened — applied by
the calibrated automatic step, decided by a lawyer, or left as background. A
pairing is the only place a letter meets a Teema; nothing here writes to a Teema
until a pairing is applied, and applying goes through the same evidence and
Submission writers the opinion archive uses.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel


class PairingTier(models.TextChoices):
    """How the offline matching placed the letter (calibration: docs/adr/0216 §3).

    Only ``A`` may be applied without a person, and only by an explicit phase.
    """

    A = "A", "Kindel (kuupäev, saaja ja teema ühtivad)"
    B = "B", "Kuupäev ja saaja, teema ei kattu"
    C = "C", "Mitu kandidaati, teema eristab"
    D = "D", "Mitu kandidaati, ei eristu"
    E = "E", "Teema ja kuupäev, saaja erineb"
    F = "F", "Teemat ei leitud"


class PairingState(models.TextChoices):
    PENDING = "PENDING", "Ootab otsust"
    APPLIED = "APPLIED", "Seotud teemaga"
    BACKGROUND = "BACKGROUND", "Taustaks"


class PairingBasis(models.TextChoices):
    """Who decided the Teema: the calibrated rule, or a named person."""

    AUTOMATIC = "AUTOMATIC", "Automaatne (kalibreeritud)"
    ACCEPTED = "ACCEPTED", "Inimene kinnitas ettepaneku"
    CHOSEN = "CHOSEN", "Inimene valis teema"


class PostipoissLetter(BaseModel):
    kirja_id = models.CharField(max_length=80, unique=True, verbose_name="kirja ID")
    year = models.PositiveSmallIntegerField(db_index=True, verbose_name="aasta")
    register_row = models.PositiveIntegerField(null=True, blank=True, verbose_name="registri rida")
    registered_on = models.DateField(null=True, blank=True, verbose_name="registreeritud")
    #: The date printed in the letter itself («Meie 15.10.2010 nr 4/272»), where
    #: the file was read and carried its own number — the date a timeline shows.
    letter_date = models.DateField(null=True, blank=True, verbose_name="kirja kuupäev")
    title = models.CharField(max_length=500, verbose_name="pealkiri")
    recipient = models.CharField(max_length=400, blank=True, verbose_name="saaja")
    department = models.CharField(max_length=200, blank=True, verbose_name="osakond")
    #: Where the file came from: Postipoiss itself, or a recovery (SharePoint,
    #: koda.ee, a ministry register) proven by the letter's own number and date.
    file_source = models.CharField(max_length=80, blank=True, verbose_name="faili allikas")
    #: The letter's text as read when the package was built; what the page searches.
    body = models.TextField(blank=True, verbose_name="tekst")
    package_sha256 = models.CharField(max_length=64, verbose_name="paketi SHA-256")

    class Meta:
        verbose_name = "Postipoisi kiri"
        verbose_name_plural = "Postipoisi kirjad"
        ordering = ["-year", "-register_row", "kirja_id"]

    def __str__(self) -> str:
        return f"{self.kirja_id} {self.title[:60]}"

    @property
    def shown_date(self) -> object:
        return self.letter_date or self.registered_on


class PostipoissLetterFile(BaseModel):
    letter = models.ForeignKey(
        PostipoissLetter, on_delete=models.CASCADE, related_name="files", verbose_name="kiri"
    )
    binary = models.ForeignKey(
        "legacy_import.OpinionArchiveBinary",
        on_delete=models.PROTECT,
        related_name="postipoiss_files",
        verbose_name="bait",
    )
    filename = models.CharField(max_length=255, verbose_name="failinimi")
    ordinal = models.PositiveSmallIntegerField(default=0, verbose_name="järjekord")

    class Meta:
        verbose_name = "Postipoisi kirja fail"
        verbose_name_plural = "Postipoisi kirjade failid"
        ordering = ["letter", "ordinal"]
        constraints = [
            models.UniqueConstraint(fields=["letter", "binary"], name="postipoiss_file_once"),
        ]


class PostipoissPairing(BaseModel):
    letter = models.OneToOneField(
        PostipoissLetter, on_delete=models.CASCADE, related_name="pairing", verbose_name="kiri"
    )
    tier = models.CharField(
        max_length=1, choices=PairingTier.choices, db_index=True, verbose_name="kindlus"
    )
    signals = models.JSONField(default=list, blank=True, verbose_name="tunnused")
    #: The register reference the package proposed («2014_85»), kept verbatim
    #: even when this database has no such Teema.
    suggested_reference = models.CharField(max_length=20, blank=True, verbose_name="pakutud viide")
    suggested_matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postipoiss_suggestions",
        verbose_name="pakutud teema",
    )
    state = models.CharField(
        max_length=16,
        choices=PairingState.choices,
        default=PairingState.PENDING,
        db_index=True,
        verbose_name="olek",
    )
    basis = models.CharField(max_length=16, choices=PairingBasis.choices, blank=True)
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postipoiss_letters",
        verbose_name="teema",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postipoiss_pairings",
        verbose_name="väljasaadetud arvamus",
    )
    #: True when the Teema already held a sent opinion within seven days and this
    #: letter was attached to it instead of becoming a second one (ADR 0216 §5).
    joined_existing = models.BooleanField(default=False)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="otsustaja",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "Postipoisi kirja sidumine"
        verbose_name_plural = "Postipoisi kirjade sidumised"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(state=PairingState.APPLIED) | models.Q(matter__isnull=False),
                name="postipoiss_applied_has_matter",
            ),
        ]
