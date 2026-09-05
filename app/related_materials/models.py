"""What a person decided about related material. Nothing here is inferred.

Three tables, three human statements, and the recommendation engine writes to
none of them. A suggestion is computed when the section is opened and forgotten
when the page is closed; only a click turns it into one of these rows
(docs/adr/0061).

**`MatterRelation`** — *these two Matters are related.* One symmetric statement
with no type. The master specification describes a typed `MatterRelationship`
with `SUCCESSOR_OF`, `RELATED_TO`, `IMPLEMENTS_OR_TRANSPOSES` and
`DUPLICATE_OF`; this table is the `RELATED_TO` slice of it and deliberately
nothing else — an approved scope reduction recorded in ADR 0061, not an
oversight. `SUCCESSOR_OF` already exists as `Matter.superseded_by` and stays
there. The other two are *directional*, and a directional relation cannot live
in a table whose whole design is that A↔B is one row, so a future typed model is
a different table rather than a column added here.

**`MatterBackgroundMaterial`** — *this existing material is useful background
for this Matter.* A canonical `Submission` on some other Matter, or a held
`OpinionArchiveBinary`. Deliberately weaker than both things it resembles: it
does not move the Submission, does not claim the letter was sent for this
Matter, and is not an `OpinionArchiveMatterLink`, which asserts that a letter
*concerns* a Matter. Two explicit nullable keys and an exactly-one constraint,
because a generic foreign key would let a POST name a row of any table.

**`RelatedSuggestionDismissal`** — *do not suggest this here again.* Durable and
Matter-level: if one lawyer working a file says a candidate is unrelated, the
team is not shown it on every page load. Weak preference state, so it goes
when its candidate goes, and adding the candidate for real clears it.

Deletion behaviours, chosen rather than defaulted. A relation or a background
row goes with its Matter (`CASCADE`): a deleted Matter has no relations to keep,
and the TEST-data purge inventory sees both keys, so a relation between a TEST
and a REAL Matter is reported as a straddling blocker rather than removed
(`app/matters/purge.py`, tests/test_related_materials.py). A background row
goes with its Submission for the same reason and holds its archive binary under
`PROTECT`, because archive evidence is never deleted and the schema should say
so. Dismissals cascade with whatever they name.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from app.core.models import BaseModel


class MatterRelation(BaseModel):
    """Matter A is related to Matter B. One row, both directions."""

    #: The pair is stored in one canonical order — the smaller primary key in
    #: ``matter_a`` — and the database refuses any other. That is what makes
    #: A/B and B/A the same row rather than two, without trusting the caller
    #: to have sorted them (`services.link_related_matters`).
    matter_a = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="relations_as_first",
        verbose_name="teema A",
    )
    matter_b = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="relations_as_second",
        verbose_name="teema B",
    )
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="matter_relations_made",
        verbose_name="sidus",
    )
    linked_at = models.DateTimeField(verbose_name="seotud")
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "seotud teema"
        verbose_name_plural = "seotud teemad"
        ordering = ["-linked_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(matter_a__lt=F("matter_b")),
                name="related_relation_pair_is_canonical",
            ),
            models.UniqueConstraint(
                fields=["matter_a", "matter_b"],
                name="related_one_relation_per_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["matter_b"], name="related_relation_by_second"),
        ]

    def __str__(self) -> str:
        return f"{self.matter_a_id} ↔ {self.matter_b_id}"

    def other_than(self, matter_id: object) -> object:
        """The primary key on the far side of this relation from ``matter_id``."""
        return self.matter_b_id if self.matter_a_id == matter_id else self.matter_a_id


class MatterBackgroundMaterial(BaseModel):
    """Somebody chose this existing material as useful background for a Matter."""

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="background_materials",
        verbose_name="teema",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="background_uses",
        verbose_name="arvamus",
    )
    archive_binary = models.ForeignKey(
        "legacy_import.OpinionArchiveBinary",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="background_uses",
        verbose_name="arhiivikiri",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="background_materials_added",
        verbose_name="lisas",
    )
    added_at = models.DateTimeField(verbose_name="lisatud")
    note = models.TextField(blank=True, verbose_name="märkus")

    class Meta:
        verbose_name = "taustmaterjal"
        verbose_name_plural = "taustmaterjalid"
        ordering = ["-added_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(submission__isnull=False, archive_binary__isnull=True)
                    | Q(submission__isnull=True, archive_binary__isnull=False)
                ),
                name="related_background_has_exactly_one_source",
            ),
            models.UniqueConstraint(
                fields=["matter", "submission"],
                condition=Q(submission__isnull=False),
                name="related_one_background_per_submission",
            ),
            models.UniqueConstraint(
                fields=["matter", "archive_binary"],
                condition=Q(archive_binary__isnull=False),
                name="related_one_background_per_archive_binary",
            ),
        ]

    def __str__(self) -> str:
        source = self.submission_id or self.archive_binary_id
        return f"{self.matter_id} ← {source}"

    @property
    def is_submission(self) -> bool:
        return self.submission_id is not None


class RelatedSuggestionDismissal(BaseModel):
    """«Ei ole seotud»: this candidate is not to be suggested for this Matter."""

    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="related_suggestion_dismissals",
        verbose_name="teema",
    )
    candidate_matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dismissed_as_related_candidate",
        verbose_name="kandidaatteema",
    )
    candidate_submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dismissed_as_background_candidate",
        verbose_name="kandidaatarvamus",
    )
    candidate_archive_binary = models.ForeignKey(
        "legacy_import.OpinionArchiveBinary",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="dismissed_as_background_candidate",
        verbose_name="kandidaatarhiivikiri",
    )
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="related_suggestions_dismissed",
        verbose_name="peitis",
    )
    dismissed_at = models.DateTimeField(verbose_name="peidetud")

    class Meta:
        verbose_name = "peidetud soovitus"
        verbose_name_plural = "peidetud soovitused"
        ordering = ["-dismissed_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        candidate_matter__isnull=False,
                        candidate_submission__isnull=True,
                        candidate_archive_binary__isnull=True,
                    )
                    | Q(
                        candidate_matter__isnull=True,
                        candidate_submission__isnull=False,
                        candidate_archive_binary__isnull=True,
                    )
                    | Q(
                        candidate_matter__isnull=True,
                        candidate_submission__isnull=True,
                        candidate_archive_binary__isnull=False,
                    )
                ),
                name="related_dismissal_has_exactly_one_candidate",
            ),
            models.CheckConstraint(
                condition=~Q(candidate_matter=F("matter")),
                name="related_dismissal_is_not_of_itself",
            ),
            models.UniqueConstraint(
                fields=["matter", "candidate_matter"],
                condition=Q(candidate_matter__isnull=False),
                name="related_one_dismissal_per_candidate_matter",
            ),
            models.UniqueConstraint(
                fields=["matter", "candidate_submission"],
                condition=Q(candidate_submission__isnull=False),
                name="related_one_dismissal_per_candidate_submission",
            ),
            models.UniqueConstraint(
                fields=["matter", "candidate_archive_binary"],
                condition=Q(candidate_archive_binary__isnull=False),
                name="related_one_dismissal_per_candidate_binary",
            ),
        ]

    def __str__(self) -> str:
        candidate = (
            self.candidate_matter_id
            or self.candidate_submission_id
            or self.candidate_archive_binary_id
        )
        return f"{self.matter_id} ✕ {candidate}"
