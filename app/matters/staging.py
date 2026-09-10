"""Files chosen on `Uus teema`, before there is a Teema to attach them to.

The register's real starting point is a file. A ministry sends a covering
letter, a draft and two annexes, and every fact the form then asks for — who
sent it, by when Koda must answer, what kind of proceeding it is — is on the
first page of the letter. Reading it is what `app.matters.intake_suggestions`
does, and until now it could only do so *after* the Matter existed, because
the only thing the extraction system knows how to read is a
``DocumentVersion``, and a ``DocumentVersion`` belongs to a ``Document``,
which belongs to a ``Matter`` (docs/adr/0060).

So the reading was in the wrong place. The moment the answers are worth
having is while the form is still open. These two tables are what makes that
possible without inventing a Matter to hang the file on (docs/adr/0064).

**This is not business data, and every line of it is written to keep that
true.** A staged file is in no register, no search index, no statistic, no
timeline and no Dokumendid list. It has no visibility of its own because it
belongs to nobody but the person who picked it; it has no audit trail because
nothing has happened yet that a colleague could need to know about. What it
has instead is an owner, an expiry and a consumed stamp — the three facts a
temporary thing needs so that it can be found, refused to a stranger, and
eventually swept.

**It is not the evidence store either.** The bytes live in the same storage
class as a refused form's held uploads (`app.documents.pending`): not backed
up, no volume of its own, disposable by construction. Losing all of it costs
somebody one re-pick. What makes that safe is that nothing here is evidence of
anything *yet* — evidence is what `Loo teema` creates, through
``add_evidence_version``, from these exact bytes and nobody else's.

Three things this deliberately does **not** do, each of which was considered:

* **It does not make ``Document.matter`` nullable.** A Document with no Matter
  would be a canonical row in a state the whole authorization model assumes is
  impossible — ``visible_to`` reads a Matter's visibility through it — and
  every list, count and search projection would need a clause about it.
* **It does not write ``DocumentDerivative`` rows.** Those hang off a
  ``DocumentVersion``, they are what search reads, and they carry a rebuild
  contract. A staged file's extracted text is a few paragraphs of JSON on its
  own row, read by exactly one caller and thrown away with it.
* **It does not hold a provisional Matter.** There is no such thing in this
  product. Before `Loo teema` there is no Teema, and a hidden one that half the
  application filtered out would be the same defect written in SQL.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from app.core.models import BaseModel
from app.documents.enums import DocumentRole, ExtractionState, MalwareScanState


class MatterIntakeSessionQuerySet(models.QuerySet):
    def owned_by(self, user: object | None) -> MatterIntakeSessionQuerySet:
        """This person's staging, and nobody else's.

        The only supported way to reach a session from a request. Ownership is
        the whole authorization story here — a staged file has no visibility
        rules of its own because it is not a record about anything, it is one
        person's unfinished form — so a route that forgot this clause would be
        handing a stranger the text of somebody's incoming correspondence.

        An anonymous caller matches nothing rather than everything; the routes
        are behind ``@login_required`` as well, and this is the second lock.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def usable(self) -> MatterIntakeSessionQuerySet:
        """Sessions a form may still resume: not consumed, not expired."""
        return self.filter(consumed_at__isnull=True, expires_at__gt=timezone.now())

    def stale(self) -> MatterIntakeSessionQuerySet:
        """Sessions nobody is coming back for, whatever became of them.

        A session somebody abandoned an hour past its expiry and one that was
        consumed by a Matter that has already been created are the same thing
        to a sweeper: material no form will ever ask for again. They are kept
        apart in the *model* because the difference explains what happened;
        they are swept together because the answer is the same.
        """
        return self.filter(
            models.Q(expires_at__lte=timezone.now()) | models.Q(consumed_at__isnull=False)
        )


class MatterIntakeSession(BaseModel):
    """One person's unfinished `Uus teema` form, and the files on it."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="matter_intake_sessions",
        verbose_name="omanik",
    )
    #: When this stops being resumable. Read rather than computed, so that a
    #: change to the grace period never retroactively expires — or revives —
    #: staging that is already open on somebody's screen.
    expires_at = models.DateTimeField(db_index=True, verbose_name="aegub")
    #: When `Loo teema` succeeded and these files became evidence. A consumed
    #: session is finished: it can no longer be added to, read or promoted, and
    #: it is kept only until the sweeper comes past, so that a double submit
    #: finds a closed door rather than an empty one.
    consumed_at = models.DateTimeField(null=True, blank=True, verbose_name="kasutatud")

    objects = MatterIntakeSessionQuerySet.as_manager()

    class Meta:
        verbose_name = "teema ettevalmistus"
        verbose_name_plural = "teemade ettevalmistused"
        ordering = ["-created_at"]
        indexes = [
            # The one read a request makes: this person's open staging, newest
            # first. Everything else about this table is the sweeper's.
            models.Index(fields=["owner", "-created_at"], name="matters_intake_owner"),
        ]

    def __str__(self) -> str:
        return f"intake session {self.pk}"

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_usable(self) -> bool:
        return not self.is_consumed and not self.is_expired


class MatterIntakeFileQuerySet(models.QuerySet):
    def live(self) -> MatterIntakeFileQuerySet:
        """The files still on the form: everything the person has not taken off.

        Removal is a stamp rather than a delete, so that a browser and the
        server cannot end up disagreeing about a file that is half gone. What
        `Loo teema` promotes, what the analyser reads and what the page lists
        are all this set, which is the only way those three can agree.
        """
        return self.filter(removed_at__isnull=True)

    def in_order(self) -> MatterIntakeFileQuerySet:
        return self.order_by("ordinal", "created_at")


class MatterIntakeFile(BaseModel):
    """One validated file waiting for the `Loo teema` that will make it evidence.

    Everything on it is either a fact about the bytes — which must survive
    unchanged into the ``DocumentVersion`` — or a fact about reading them,
    which is disposable. The two are not mixed: ``sha256`` is checked again at
    promotion, and ``text`` is thrown away.
    """

    session = models.ForeignKey(
        MatterIntakeSession,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name="ettevalmistus",
    )
    #: The order the person offered the files in, which is the order they
    #: become Documents. Never renumbered: taking the first file off must not
    #: change what the second one is.
    ordinal = models.PositiveIntegerField(verbose_name="järjekord")

    # -- the bytes, and what must reach the evidence store unchanged --------
    storage_key = models.CharField(max_length=500, verbose_name="hoidla võti")
    original_filename = models.CharField(max_length=400, verbose_name="algne failinimi")
    mime_type = models.CharField(max_length=200, verbose_name="MIME tüüp")
    size_bytes = models.BigIntegerField(verbose_name="suurus baitides")
    #: Recorded at upload and verified again at promotion. The point of this
    #: column is the assertion it makes possible: what became evidence is
    #: byte-for-byte what the browser sent, not a reconstruction of it.
    sha256 = models.CharField(max_length=64, verbose_name="SHA-256")
    #: What intake would call this file, decided from its name at upload by
    #: ``app.matters.intake.role_for`` and stored so the analyser can rank a
    #: message ahead of the letter it carried without re-deriving it.
    role = models.CharField(
        max_length=32,
        choices=DocumentRole.choices,
        default=DocumentRole.INCOMING_AUTHORITY,
        verbose_name="roll",
    )

    # -- reading them, which is disposable ---------------------------------
    # Dead since docs/adr/0072. Nothing writes it, nothing reads it, and no
    # gate stands on it; it is kept because dropping a column from a
    # 19 000-row production table is a destructive migration performed for
    # neatness. See `MalwareScanState` for the full note.
    malware_scan_state = models.CharField(
        max_length=32,
        choices=MalwareScanState.choices,
        default=MalwareScanState.PENDING,
        verbose_name="pahavarakontroll",
    )
    extraction_state = models.CharField(
        max_length=32,
        choices=ExtractionState.choices,
        default=ExtractionState.PENDING,
        db_index=True,
        verbose_name="teksti eraldamine",
    )
    extraction_claimed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="töötlemine algas"
    )
    extraction_note = models.CharField(max_length=300, blank=True, verbose_name="eraldamise märkus")
    #: The parser's fragments, as JSON rather than as ``DocumentTextFragment``
    #: rows. Those belong to a ``DocumentVersion``, are what search reads and
    #: carry a rebuild contract; this is a handful of paragraphs read once, by
    #: one caller, and deleted with the row.
    text = models.JSONField(default=list, blank=True, verbose_name="eraldatud tekst")
    #: What the budget planner reads, so a file whose text the analysis will not
    #: reach is never loaded (`intake_suggestions/input.py`).
    text_character_count = models.PositiveIntegerField(default=0, verbose_name="tähemärke")
    #: A message's own headers, in the shape the mail parser publishes them.
    email_metadata = models.JSONField(null=True, blank=True, verbose_name="kirja metaandmed")

    #: Taken back off the form. A stamp rather than a delete: the row records
    #: that a file was offered and withdrawn, the bytes go when the session is
    #: swept, and nothing anywhere has to reason about a half-deleted file.
    removed_at = models.DateTimeField(null=True, blank=True, verbose_name="eemaldatud")

    objects = MatterIntakeFileQuerySet.as_manager()

    class Meta:
        verbose_name = "ettevalmistatud fail"
        verbose_name_plural = "ettevalmistatud failid"
        ordering = ["session", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "ordinal"],
                name="matters_intake_file_unique_ordinal",
            ),
            # Two staged files may never address the same stored object, for
            # the same reason two DocumentVersions may not: deleting one would
            # take the other's bytes with it.
            models.UniqueConstraint(
                fields=["storage_key"],
                name="matters_intake_file_unique_storage_key",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="matters_intake_file_sha256_is_lowercase_hex",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0),
                name="matters_intake_file_size_not_negative",
            ),
        ]
        indexes = [
            # The worker's queue read: what is waiting to be parsed, oldest
            # first. Partial, because every row it will ever ask about is
            # unfinished and the table's whole history is not.
            models.Index(
                fields=["created_at"],
                condition=models.Q(
                    extraction_state__in=[ExtractionState.PENDING, ExtractionState.PROCESSING],
                    removed_at__isnull=True,
                ),
                name="matters_intake_queue",
            ),
        ]

    def __str__(self) -> str:
        return self.original_filename

    @property
    def is_reading(self) -> bool:
        """Still on its way to an answer, one way or the other."""
        return self.extraction_state in (ExtractionState.PENDING, ExtractionState.PROCESSING)
