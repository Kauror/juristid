"""`Submission` — one outbound written advocacy action.

This is the canonical record of what Koda actually sent, and the entity every
future opinion count is derived from. There is deliberately no
`Matter.opinion_sent_date`: a Matter can produce several submissions, and a
single column on the Matter could only ever record one of them
(master specification 11.2, 18.2).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.core.models import BaseModel, VisibilityInheritingModel
from app.submissions.enums import (
    RecipientRole,
    SentAtPrecision,
    SubmissionKind,
    SubmissionStatus,
)

#: What a `Submission` ↔ `Ülevaade / uudis` link says when the two belong to
#: different files.
#:
#: Defined here rather than beside the other refusals in `services`, because
#: `SubmissionWebsiteOverviewLink.clean` is the second guard on the same rule and
#: `services` already imports this module — one sentence, two enforcement points,
#: no import cycle (docs/adr/0093 §2).
CROSS_MATTER_OVERVIEW_LINK = (
    "Ülevaate või uudise saab siduda ainult sama teema arvamusega. Vali ülevaade selle teema alt."
)


class SubmissionQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> SubmissionQuerySet:
        return apply_scope(self, child_visibility_q(scope_for_user(user)))

    def sent(self) -> SubmissionQuerySet:
        return self.filter(status=SubmissionStatus.SENT)

    def historically_sent(self) -> SubmissionQuerySet:
        """Every submission that **was actually sent**, whatever it is now.

        `sent()` above asks a question about the present — «is this the opinion
        that currently stands» — and it is the right question for a portfolio, a
        count of live advocacy, or the card on the Matter page.

        It is the wrong question for a *history*. Sending was a business act
        performed on a day, by a person, to named recipients, and withdrawing
        the opinion afterwards does not mean the letter never went out; it means
        one more thing happened. A chronology populated by `status=SENT` makes a
        sent opinion vanish from the file's own history the moment it is
        withdrawn or superseded, which is the defect this method exists to close
        (docs/adr/0092 §3).

        **`sent_at` is the canonical fact and the audit event is not.**
        `SUBMISSION_SENT.occurred_at` is when somebody pressed the button, which
        for an opinion reconstructed from the historical register is a fact about
        the import; `sent_at` is when the letter went. That distinction is the
        whole of docs/adr/0092 §3 and is not reopened here.

        **A `DRAFT` is excluded even when it carries a `sent_at`.** The CHECK
        constraint `submissions_sent_requires_timestamp_and_evidence` binds the
        timestamp to the SENT status and says nothing about a draft, so a row
        carrying a stray timestamp is malformed data rather than a send — and a
        history that materialised a draft out of one would be inventing an act.
        The three terminal statuses are named positively rather than DRAFT being
        excluded, so a status added later is absent until somebody decides.
        """
        return self.filter(
            status__in=(
                SubmissionStatus.SENT,
                SubmissionStatus.WITHDRAWN,
                SubmissionStatus.SUPERSEDED,
            ),
            sent_at__isnull=False,
        )


class Submission(VisibilityInheritingModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="teema",
    )
    kind = models.CharField(
        max_length=40,
        choices=SubmissionKind.choices,
        default=SubmissionKind.FORMAL_OPINION,
        db_index=True,
        verbose_name="liik",
    )
    title = models.CharField(max_length=400, verbose_name="pealkiri")
    status = models.CharField(
        max_length=16,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.DRAFT,
        db_index=True,
        verbose_name="olek",
    )

    recipients = models.ManyToManyField(
        "organisations.Organisation",
        through="submissions.SubmissionRecipient",
        blank=True,
        related_name="received_submissions",
        verbose_name="saajad",
    )
    joint_submitters = models.ManyToManyField(
        "organisations.Organisation",
        through="submissions.SubmissionJointSubmitter",
        blank=True,
        related_name="joint_submissions",
        verbose_name="kaasesitajad",
        help_text="Teised organisatsioonid, kelle nimel pöördumine ühiselt esitati.",
    )

    #: `Märksõnad` — what **this letter** argued about, as the department's own
    #: governed vocabulary. Separate from `Matter.tags`, which says what the file
    #: is about: nothing here is inherited from the Matter, and nothing on the
    #: Matter is changed by an edit here (docs/adr/0093 §1).
    tags = models.ManyToManyField(
        "taxonomy.Tag",
        through="submissions.SubmissionTagAssignment",
        blank=True,
        related_name="submissions",
        verbose_name="märksõnad",
    )
    #: `Seotud ülevaated / uudised` — where this letter was written up for the
    #: membership. Optional in both directions, explicit in both directions, and
    #: never inferred from a URL, a title, a date or chronological proximity
    #: (docs/adr/0093 §2).
    website_overviews = models.ManyToManyField(
        "matters.MatterWebsiteOverview",
        through="submissions.SubmissionWebsiteOverviewLink",
        blank=True,
        related_name="submissions",
        verbose_name="seotud ülevaated / uudised",
    )

    sent_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="saadetud")
    # How much of `sent_at` the source actually said. A submission captured in
    # this system carries a real timestamp; one reconstructed from the historical
    # register carries a date, and the time in the column is an anchor nobody
    # supplied. Rendering that anchor as "00:00" would tell a lawyer the letter
    # went out at midnight, so the UI asks this field before it picks a format
    # and the anchor never reaches a screen (Stage-2H brief 20).
    sent_at_precision = models.CharField(
        max_length=16,
        choices=SentAtPrecision.choices,
        default=SentAtPrecision.TIMESTAMP,
        verbose_name="kuupäeva täpsus",
    )
    channel = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="kanal",
        help_text="Näiteks EIS, e-post või dokumendiregistri viide.",
    )
    reference = models.CharField(max_length=200, blank=True, verbose_name="viide")

    # The exact binary that was sent. Immutable once captured; a correction is a
    # new version, and a new decision is a new Submission.
    final_version = models.ForeignKey(
        "documents.DocumentVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="finalised_submissions",
        verbose_name="lõplik tõend",
    )
    working_document = models.ForeignKey(
        "documents.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="draft_submissions",
        verbose_name="töödokument",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_submissions",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sent_submissions",
    )
    notes = models.TextField(blank=True, verbose_name="märkused")
    #: `Kokkuvõte` — what Koda actually said in this opinion, in the lawyer's own
    #: words.
    #:
    #: **A column of its own rather than a longer `title`.** `title` is this
    #: record's *identity*: it is what `Submission` has never been allowed to
    #: leave empty, what the outbound register prints in a cell, what the document
    #: the bytes live under is called, and what a colleague scans a list of sends
    #: by. A paragraph summarising an opinion is none of those things, and putting
    #: one there would make every list of opinions a wall of prose and every
    #: identifying cell a truncation.
    #:
    #: **And a column of its own rather than `notes`.** `notes` is «märkused» —
    #: bookkeeping beside the record, which is why it is what the body of the
    #: search projection already reads. This is the substance of the letter, it is
    #: what `+ Koja arvamus` asks for in place of a headline, and a reader looking
    #: for what the Chamber argued must not have to guess which of two boxes the
    #: last person used.
    #:
    #: Blank is ordinary and stays ordinary: every Submission recorded before this
    #: column existed has one, no value is derived for them, and nothing reads a
    #: headline out of the first sentence of it (docs/adr/0095 §2).
    summary = models.TextField(blank=True, verbose_name="kokkuvõte")

    objects = SubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = "väljasaadetud arvamus"
        verbose_name_plural = "väljasaadetud arvamused"
        ordering = ["-sent_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="submissions_title_required",
            ),
            # A sent submission without its exact final text is an unverifiable
            # claim about what Koda argued. The database refuses to hold one.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=SubmissionStatus.SENT)
                    | models.Q(sent_at__isnull=False, final_version__isnull=False)
                ),
                name="submissions_sent_requires_timestamp_and_evidence",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    visibility_override__in=["", Visibility.NORMAL, Visibility.RESTRICTED]
                ),
                name="submissions_visibility_vocabulary",
            ),
        ]
        indexes = [
            models.Index(fields=["matter", "-sent_at"], name="submissions_matter_sent"),
            models.Index(fields=["status", "-sent_at"], name="submissions_status_sent"),
        ]

    def __str__(self) -> str:
        return self.title

    def parent_visibility(self) -> str:
        return self.matter.visibility

    @property
    def is_sent(self) -> bool:
        return self.status == SubmissionStatus.SENT

    @property
    def has_final_evidence(self) -> bool:
        return self.final_version_id is not None


class SubmissionRecipient(BaseModel):
    """One organisation on a submission, and why it is there.

    Addressee and "teadmiseks" are different facts. Only the addressees answer
    the question a reporting count asks — who Koda formally wrote to.
    """

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="recipient_rows",
        verbose_name="arvamus",
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="submission_recipient_rows",
        verbose_name="organisatsioon",
    )
    role = models.CharField(
        max_length=32,
        choices=RecipientRole.choices,
        default=RecipientRole.ADDRESSEE,
        db_index=True,
        verbose_name="roll",
    )
    note = models.CharField(max_length=200, blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "arvamuse saaja"
        verbose_name_plural = "arvamuse saajad"
        ordering = ["role", "organisation__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "organisation"],
                name="submissions_unique_recipient_per_submission",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organisation_id} ({self.role})"


class SubmissionJointSubmitter(BaseModel):
    """A co-signatory, and whether they have actually confirmed.

    A joint letter is only joint once the other association agrees. Recording an
    intended co-signatory as a confirmed one would overstate who stood behind
    the text.
    """

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="joint_submitter_rows",
        verbose_name="arvamus",
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="joint_submission_rows",
        verbose_name="organisatsioon",
    )
    confirmed = models.BooleanField(default=False, verbose_name="kinnitatud")
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="kinnitatud")
    note = models.CharField(max_length=200, blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "kaasesitaja"
        verbose_name_plural = "kaasesitajad"
        ordering = ["organisation__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "organisation"],
                name="submissions_unique_joint_submitter",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(confirmed=False, confirmed_at__isnull=True)
                    | models.Q(confirmed=True, confirmed_at__isnull=False)
                ),
                name="submissions_joint_confirmation_consistent",
            ),
        ]

    def __str__(self) -> str:
        state = "kinnitatud" if self.confirmed else "ootel"
        return f"{self.organisation_id} ({state})"


class SubmissionTagAssignment(BaseModel):
    """One `Märksõna` on one `Koja arvamus`, chosen by a person for that letter.

    **The vocabulary is `taxonomy.Tag` and there is no second one.** ADR 0091 §6.5
    declined per-opinion keywords on the ground that a second, narrower vocabulary
    would create two answers to «what is this about». The half of that argument
    which was right is kept here in full: this table holds no names, no free text
    and no lifecycle of its own — only a pointer into the governed vocabulary the
    department already curates, with its aliases, its merges and its `is_active`
    (docs/adr/0093 §1).

    **It is not `matters.TagAssignment` and does not extend it.** That record
    points at a `Matter` and means «this file is about»; this one points at a
    `Submission` and means «this letter argued about». A file on the packaging act
    is one thing; the opinion on the first draft, the supplementary letter six
    months later and the joint submission at second reading are three letters that
    argued three things, and the department wants to find the letter.

    **Nothing arrives here by inheritance or inference.** No row is written because
    the Matter carries the tag, because the title contains a word, because the file
    that went out mentions one, because of who it went to, or because of the
    `Liik`. A person selects a tag or the Submission has none — and no historical
    Submission was given one when this table was created.

    **No `source` column**, unlike `matters.TagAssignment`. That model distinguishes
    a manual assignment from an imported or rule-derived one because both of those
    exist for a Matter. Here exactly one thing writes rows — a person choosing on
    the opinion's own metadata surface — so a column recording which of one source
    it was would be a column that can only ever say `MANUAL`.
    """

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="tag_assignments",
        verbose_name="arvamus",
    )
    #: `PROTECT`, exactly as `matters.TagAssignment.tag` is: a governed vocabulary
    #: row that something is classified by must not vanish out from under it. The
    #: department retires a tag by deactivating or merging it, which leaves every
    #: assignment standing and searchable through the canonical tag.
    tag = models.ForeignKey(
        "taxonomy.Tag",
        on_delete=models.PROTECT,
        related_name="submission_assignments",
        verbose_name="märksõna",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_submission_tags",
        verbose_name="lisas",
    )
    assigned_at = models.DateTimeField(null=True, blank=True, verbose_name="lisatud")

    class Meta:
        verbose_name = "arvamuse märksõna"
        verbose_name_plural = "arvamuse märksõnad"
        ordering = ["tag__name_et"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "tag"],
                name="submissions_unique_tag_per_submission",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.submission_id} · {self.tag_id}"


class SubmissionWebsiteOverviewLink(BaseModel):
    """This opinion was written up in that `Ülevaade / uudis` — a person said so.

    ADR 0091 §8 deferred this relation because it did not exist and because
    creating a publication record there would have collided with the package that
    owns publication. `MatterWebsiteOverview` has since shipped with its three
    states, its constraints, its services and its audit vocabulary, so what is
    added here is **only the relation** (docs/adr/0093 §2).

    **Many-to-many, and both directions are ordinary.** One write-up frequently
    covers the initial opinion and the supplementary letter together; a long
    proceeding is written up more than once. Neither side is unique and neither
    elects a primary.

    **It copies nothing.** No URL, no title, no status and no date is denormalised
    onto this row: the address and the day the page went up are the overview's own
    columns, corrected on the overview's own record, and a copy here would be a
    second place for them to disagree.

    **Same Matter, or no row.** The invariant is enforced where the write happens —
    `app.submissions.services.set_submission_website_overviews`, under the
    transaction — and restated in :meth:`clean` for the admin and for anything
    constructing a row directly. Deliberately not a trigger and deliberately not a
    denormalised `matter_id`: this repository does not emulate cross-table CHECK
    constraints that way, and a copied key is one more column that can go stale.

    **Both foreign keys are `CASCADE`**, unlike the `PROTECT` on a tag. Both
    endpoints already cascade from their Matter, so a `PROTECT` on either would make
    deleting a Matter fail on a metadata row — and unlinking is a correction of
    metadata that deletes neither endpoint, which is a different act from this
    cascade and is what the service performs.
    """

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="website_overview_links",
        verbose_name="arvamus",
    )
    website_overview = models.ForeignKey(
        "matters.MatterWebsiteOverview",
        on_delete=models.CASCADE,
        related_name="submission_links",
        verbose_name="ülevaade / uudis",
    )
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="linked_submission_overviews",
        verbose_name="sidus",
    )
    linked_at = models.DateTimeField(null=True, blank=True, verbose_name="seotud")

    class Meta:
        verbose_name = "arvamuse seos ülevaatega"
        verbose_name_plural = "arvamuse seosed ülevaadetega"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "website_overview"],
                name="submissions_unique_overview_link",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.submission_id} · {self.website_overview_id}"

    def clean(self) -> None:
        """The same-Matter invariant, restated where a form or the admin can see it.

        The service is the canonical writer and refuses this under the transaction;
        this is the second guard `MatterWebsiteOverview`'s own forms already keep
        the shape of, so a row built outside the service — in the admin, in a
        shell, in a future importer — fails with a sentence rather than with a
        relation nobody can explain.
        """
        from django.core.exceptions import ValidationError

        super().clean()
        if self.submission_id is None or self.website_overview_id is None:
            return
        if self.submission.matter_id != self.website_overview.matter_id:
            raise ValidationError(CROSS_MATTER_OVERVIEW_LINK)
