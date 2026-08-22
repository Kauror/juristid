"""What the final approved register snapshot says about each Matter, today.

``CurrentRegisterState`` is **derived data**, in exactly the sense
``SearchDocument`` is. It can be deleted in full and rebuilt from the immutable
``MatterSourceReference`` rows it reads, nothing in the domain writes business
state through it, and no canonical decision depends on it. What it holds is one
interpretation of one snapshot, cached so that a question the whole application
asks — *is this Matter current work, and is its opinion still being drafted* —
is one indexed predicate rather than a JSON read through a per-era contract,
repeated per row, in Python.

Why it exists at all
--------------------
The facts are already stored. ``MatterSourceReference.source_row_raw`` keeps
every cell verbatim and ``app.legacy_import.source_cells`` reads them back
through the era contract that describes the sheet. That is the right home for
provenance and the wrong shape for a dashboard: the column letter differs
between years, so "``VÄLJA`` is blank" is not a filter any queryset can express,
and "the latest snapshot's row" is a per-Matter subquery before the question
even starts.

Three things follow from being derived, and they are the whole discipline here:

**It never decides anything canonical.** Retiring a Matter, reactivating one and
refreshing its fields are all performed against ``Matter`` by services in
``app.matters.services``. This table records what the source said; it is not
consulted to find out what the application then did.

**It stores no visibility.** Every read goes through the Matter, exactly as
``SearchDocument`` does, for the reason ADR 0005 records.

**It is keyed to a snapshot.** ``source_snapshot_sha256`` says which workbook
produced the interpretation, so a state row can never be silently older than the
evidence it claims to summarise, and rebuilding from a newer snapshot replaces
the interpretation without touching a single source reference.
"""

from __future__ import annotations

from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.core.models import BaseModel


class RegisterCurrency(models.TextChoices):
    """What the approved snapshot says about a Matter's current standing."""

    CURRENT = "CURRENT", "Jooksev töö"
    #: A terminal ``HETKESEIS``. Not a closure: no disposition and no date are
    #: claimed, here or on the Matter this describes.
    RETIRED = "RETIRED", "Registris lõppenud"
    #: ``JÄRGMISEKS`` names exactly one other Matter the work moved to.
    SUPERSEDED = "SUPERSEDED", "Jätkub teise teema all"
    #: Continuation wording that does not say, or does not say uniquely, where
    #: the work went. A person decides; nothing is guessed.
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Vajab ülevaatust"


class CurrentRegisterStateQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> CurrentRegisterStateQuerySet:
        """Scoped through the Matter, like every other read in the system."""
        return apply_scope(self, matter_visibility_q(scope_for_user(user), prefix="matter__"))

    def current(self) -> CurrentRegisterStateQuerySet:
        return self.filter(currency=RegisterCurrency.CURRENT)

    def drafting(self) -> CurrentRegisterStateQuerySet:
        """Current work whose opinion has not been recorded as sent.

        The ``Arvamusi koostamisel`` population. Both halves are required: a
        Matter with no send date whose proceeding has ended is not a drafting
        task, and a current Matter that already sent its opinion is not one
        either (``app.legacy_import.register_semantics``).
        """
        return self.current().filter(opinion_sent_date__isnull=True)


class CurrentRegisterState(BaseModel):
    """One Matter's standing in the final approved register snapshot."""

    matter = models.OneToOneField(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="current_register_state",
        verbose_name="teema",
    )
    #: The immutable observation this interpretation was read from. PROTECT
    #: rather than CASCADE: source evidence is not deleted, and if something
    #: ever tried, losing the evidence silently while keeping the summary of it
    #: is the wrong half to keep.
    source_reference = models.ForeignKey(
        "legacy_import.MatterSourceReference",
        on_delete=models.PROTECT,
        related_name="current_states",
        verbose_name="allikaviide",
    )
    source_snapshot_sha256 = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="hetktõmmise SHA-256",
        help_text="Milline töövihik selle tõlgenduse andis.",
    )
    source_sheet = models.CharField(max_length=200, blank=True, verbose_name="leht")
    source_row_number = models.PositiveIntegerField(null=True, blank=True)

    currency = models.CharField(
        max_length=32,
        choices=RegisterCurrency.choices,
        db_index=True,
        verbose_name="registri seis",
    )
    status_label = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="HETKESEIS",
        help_text="Allika sõnastuses, tõlgendamata.",
    )
    #: ``VÄLJA``. Preserved as the date it is, and read only for its presence:
    #: this is not ``Submission.sent_at`` and must never be rendered as one. A
    #: sent opinion's canonical record is a Submission with immutable final
    #: evidence, and a date is not evidence (ADR 0011).
    opinion_sent_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="VÄLJA",
        help_text="Registri märge arvamuse väljasaatmise kohta. Ei ole Submission.",
    )
    #: ``JÄRGMISEKS``, verbatim. Displayed as a source instruction and never
    #: converted into a ``NextAction``: the same sentence carries a deadline, a
    #: reminder and a guess about somebody else's timetable, and the structured
    #: model refuses to average them (ADR 0011, and the contract's own note on
    #: column L).
    next_action_text = models.TextField(blank=True, verbose_name="JÄRGMISEKS")
    owner_raw = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="VASTUTAJA allikas",
        help_text="Eesnimi allika sõnastuses; kanooniline vastutaja on Matter.owner.",
    )
    owner_resolved = models.BooleanField(
        default=False,
        verbose_name="vastutaja tuvastatud",
        help_text="Kas allika nimi vastab üheselt kasutajale.",
    )
    continues_under_reference = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="jätkub teema all",
        help_text="AAAA_N, kui JÄRGMISEKS ütleb üheselt, kuhu töö liikus.",
    )
    review_reason = models.TextField(blank=True, verbose_name="ülevaatuse põhjus")
    observed_at = models.DateTimeField(verbose_name="tuletatud")

    objects = CurrentRegisterStateQuerySet.as_manager()

    class Meta:
        verbose_name = "registri hetkeseis"
        verbose_name_plural = "registri hetkeseisud"
        ordering = ["-source_sheet", "matter"]
        indexes = [
            models.Index(
                fields=["currency", "opinion_sent_date"],
                name="legacy_register_drafting",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(currency__in=RegisterCurrency.values),
                name="legacy_register_currency_vocabulary",
            ),
            # A continuation reference is meaningful only for the verdict that
            # produces one. Without this a later edit could leave a SUPERSEDED
            # row pointing nowhere, or a CURRENT row claiming it moved.
            models.CheckConstraint(
                condition=(
                    models.Q(currency=RegisterCurrency.SUPERSEDED)
                    & ~models.Q(continues_under_reference="")
                )
                | (
                    ~models.Q(currency=RegisterCurrency.SUPERSEDED)
                    & models.Q(continues_under_reference="")
                ),
                name="legacy_register_continuation_only_when_superseded",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.matter_id} · {self.currency}"

    @property
    def is_current(self) -> bool:
        return self.currency == RegisterCurrency.CURRENT

    @property
    def is_drafting(self) -> bool:
        """Current, and no send date recorded. The ``Arvamusi koostamisel`` test."""
        return self.is_current and self.opinion_sent_date is None

    @property
    def has_source_instruction(self) -> bool:
        return bool(self.next_action_text.strip())

    @property
    def source_responsibility(self) -> str:
        """The name the register gives, for a source-responsibility breakdown.

        Deliberately the raw cell rather than the resolved account. Two current
        Matters name somebody with no account, and reporting them as
        *Määramata* would lose the one fact the register is certain about —
        that a named person is responsible — while inventing an account for
        them would be worse (Stage-2F owner resolver).
        """
        return self.owner_raw.strip()
