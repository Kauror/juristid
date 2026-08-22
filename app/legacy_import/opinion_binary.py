"""The archived letters themselves, as durable evidence independent of a Matter.

Stage 2H catalogued the archive and filed the third of it whose evidence cleared
the threshold. The other two thirds stayed a catalogue: rows describing a PDF
that still lived only inside `Opinions.zip`. An operator could read that a file
existed and could not open it, and nothing outside the zip could search it.

The obstacle was never storage. It was that a `Document` belongs to a `Matter`
and an unresolved archive file has no Matter — and inventing one, an "archive
Matter" to hold the unfiled, would put a row into the register that no lawyer
ever opened and would quietly become the answer to questions nobody meant to ask.
`Document.matter` stays required. The archive gets its own evidence holder
instead, and the truth "we hold these bytes and do not yet know whose they are"
is represented directly (docs/adr/0023).

Three models, and the reason each is separate:

``OpinionArchiveBinary`` is **one exact byte sequence**, stored once. It is
canonical evidence and lives in the evidence storage class beside
`DocumentVersion`'s bytes, because it is the same kind of thing: an immutable
original whose loss cannot be repaired by recomputing it.

``OpinionArchiveText`` is what a parser read out of those bytes. Derived,
rebuildable, stamped with the parser that produced it, and never editable —
a correction is a re-extraction, and the PDF is what it always was.

``OpinionArchiveMatterLink`` says this archived letter is *related to* that
Matter. That is a weaker and more common claim than "Koda sent this on this
date", which remains a `Submission`. Four files in the measured corpus concern
several Matters genuinely; this is where that lives, instead of four duplicate
Submissions asserting four dispatches that never happened.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel
from app.legacy_import.opinion_archive import (
    OpinionArchiveItem,
    OpinionMatchCandidate,
)
from app.legacy_import.opinion_enums import (
    ArchiveLinkBasis,
    ArchiveTextState,
)


class OpinionArchiveBinary(BaseModel):
    """One exact byte sequence from the archive, held as canonical evidence.

    Identity is the SHA-256. Two archive occurrences with the same bytes share
    one of these, which is the whole point of separating them: the occurrence
    records *where a copy was found* and the binary records *what we hold*.
    Collapsing them would erase the first; storing the bytes twice would make
    the store lie about how much distinct evidence exists.
    """

    sha256 = models.CharField(max_length=64, unique=True, verbose_name="SHA-256")
    size_bytes = models.BigIntegerField(verbose_name="suurus baitides")
    mime_type = models.CharField(max_length=200, verbose_name="MIME tüüp")

    #: Where the bytes are, in the evidence storage class. Never derived from
    #: the archive's own path: an attacker-controlled or merely eccentric path
    #: inside a zip is not a filesystem layout, and the archive contains names
    #: that were already mojibake before they were zipped.
    storage_key = models.CharField(max_length=500, unique=True, verbose_name="hoidla võti")

    #: The archive snapshot these bytes were taken out of. Provenance for the
    #: bytes themselves, distinct from any one occurrence's path.
    source_archive_sha256 = models.CharField(max_length=64, verbose_name="arhiivi SHA-256")
    materialized_at = models.DateTimeField(verbose_name="jäädvustatud")

    class Meta:
        verbose_name = "arvamuste arhiivi bait"
        verbose_name_plural = "arvamuste arhiivi baidid"
        ordering = ["-materialized_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="opinion_binary_sha256_is_lowercase_hex",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0),
                name="opinion_binary_size_is_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sha256[:12]}… ({self.size_bytes} B)"


class OpinionArchiveText(BaseModel):
    """A parser's reading of one archive binary. Derived, never evidence.

    Kept apart from the binary rather than as columns on it, so that rebuilding
    the text is an operation on this row and can never touch the bytes. A failed
    re-extraction leaves the evidence exactly where it was.

    The state is explicit and includes the honest outcomes. A scanned letter with
    no text layer is not a failure, and a file the safety policy declines to
    parse is neither a failure nor an absence of text — both are answers, and a
    reader who cannot tell them apart will draw the wrong conclusion about
    coverage.
    """

    binary = models.OneToOneField(
        OpinionArchiveBinary,
        on_delete=models.CASCADE,
        related_name="text",
        verbose_name="bait",
    )
    state = models.CharField(
        max_length=32,
        choices=ArchiveTextState.choices,
        default=ArchiveTextState.PENDING,
        db_index=True,
        verbose_name="olek",
    )
    #: The whole extracted body, normalised whitespace, no markup. Searched
    #: through the archive projection rather than queried directly.
    body = models.TextField(blank=True, verbose_name="tekst")
    page_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="lehti")
    #: Only where the parser could establish it. A page number this code guessed
    #: would be worse than no page number, because it would be quoted.
    characters = models.PositiveIntegerField(default=0, verbose_name="märke")

    parser = models.CharField(max_length=100, blank=True, verbose_name="parser")
    parser_version = models.CharField(max_length=50, blank=True, verbose_name="parseri versioon")
    extracted_at = models.DateTimeField(null=True, blank=True, verbose_name="eraldatud")
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "arvamuse arhiivi tekst"
        verbose_name_plural = "arvamuste arhiivi tekstid"
        ordering = ["-extracted_at"]

    def __str__(self) -> str:
        return f"{self.state} {self.binary_id}"

    @property
    def has_body(self) -> bool:
        return self.state == ArchiveTextState.DONE and bool(self.body)


class OpinionArchiveMatterLink(BaseModel):
    """This archived letter is related to this Matter.

    Deliberately weaker than a `Submission`, and deliberately many-to-many.

    A reviewer pressing *Seo teemaga* is asserting that this file belongs to
    this Matter. They are not asserting that Koda sent it on a date — that is a
    separate decision with a higher bar, and `review_approves_submission` on the
    candidate is what carries it. Keeping the two apart is what lets an operator
    record the thing they actually know.

    It is also how a genuinely multi-Matter letter is represented. The corpus
    has four exact binaries that concern more than one Matter; each gets several
    links and, at most, one canonical Submission on whichever Matter the
    evidence makes primary. Duplicating the PDF into several Matters would have
    invented dispatches that never happened.
    """

    binary = models.ForeignKey(
        OpinionArchiveBinary,
        on_delete=models.CASCADE,
        related_name="matter_links",
        verbose_name="bait",
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="opinion_archive_links",
        verbose_name="teema",
    )
    #: The occurrence the reviewer was looking at. Provenance for the decision,
    #: not part of the link's identity: the same bytes found at a second path do
    #: not make a second relationship to the same Matter.
    item = models.ForeignKey(
        OpinionArchiveItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matter_links",
        verbose_name="arhiivikirje",
    )
    candidate = models.ForeignKey(
        OpinionMatchCandidate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matter_links",
        verbose_name="kandidaat",
    )

    basis = models.CharField(
        max_length=32,
        choices=ArchiveLinkBasis.choices,
        verbose_name="alus",
    )
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="opinion_archive_links",
        verbose_name="siduja",
    )
    linked_at = models.DateTimeField(verbose_name="seotud")
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "arvamuse arhiivi teemaseos"
        verbose_name_plural = "arvamuste arhiivi teemaseosed"
        ordering = ["-linked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["binary", "matter"],
                name="opinion_one_archive_link_per_binary_matter",
            ),
        ]
        indexes = [
            models.Index(fields=["matter"], name="opinion_link_matter"),
        ]

    def __str__(self) -> str:
        return f"{self.binary_id} → {self.matter_id}"
