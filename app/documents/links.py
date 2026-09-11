"""Which exact business record one piece of evidence supports.

A ``Document`` has always belonged to a ``Matter``, and until now that was the
whole of what the system knew about it. That is enough while files arrive one
per save at the top of a single composer; it is not enough once a person can
attach two PDFs to a work victory, a scan to a commencement and a member's reply
to a consultation in the same afternoon. Six months later "which of these eleven
files is the evidence for *that* Töövõit" has to be answerable, and the only
answers a Matter-level document offers are its filename and its timestamp —
both of which are guesses (docs/adr/0075 §5).

So this is one additive link table: *this document supports that record*, stated
once, at the moment the record is written.

Why five nullable typed columns and not a generic target
--------------------------------------------------------
A ``GenericForeignKey`` would be one column pair and no referential integrity at
all: nothing stops a content-type/id pair naming a row that does not exist, a
row on another Matter, or a row in a table that has since been dropped, and
every read costs a query per kind. This codebase does not use one anywhere.

Five columns with an exactly-one ``CHECK`` is the shape
``related_materials.MatterBackgroundMaterial`` and
``related_materials.RelatedSuggestionDismissal`` already use for the same
problem, and it is the one with the stronger guarantees: every link is a real
foreign key, the database refuses a row that names two records or none, and one
``select_related`` reads every kind at once.

The cost is that a sixth kind of linkable record is a migration. That is the
correct cost — what evidence may be attached to is a product decision, not a
shape a caller invents at run time.

Why the Matter is not stored here
---------------------------------
The invariant is *document and record belong to the same Matter*, and the Matter
is already on both ends of the link. Copying it onto the link row would add a
third place for it to be recorded, and therefore a third thing that can
disagree, while still letting the database check nothing — a ``CHECK``
constraint sees one row and cannot follow a foreign key. So the rule is enforced
at the one service that creates links
(:func:`app.documents.services.link_document_to_record`), which refuses a link
whose two ends disagree, and ``manage.py check_evidence_integrity`` reports any
row that ever does — the same division of labour the rest of the evidence layer
already uses for what PostgreSQL is structurally unable to see.

The alternative that would put it in the database is a composite foreign key on
``(document_id, matter_id)``, which needs a redundant unique constraint on
``(id, matter)`` across six production tables and raw SQL the ORM cannot see.
Disproportionate to one invariant written in one place (docs/adr/0075 §6).

Visibility
----------
A link is not business content and carries no visibility of its own. It is
readable exactly when **both** of its ends are, which is what
:meth:`DocumentLinkQuerySet.visible_to` computes — the more restrictive of the
two, never the document's alone. A restricted document linked to a normal note
contributes no row, and a normal document linked to a restricted fact
contributes none either.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.core.models import BaseModel

#: The link's target columns, in the order a reader meets them on the Teema
#: page. Exactly one is set on every row. The CHECK constraint, the uniqueness
#: constraints and the visibility clause are all generated from this tuple, so
#: the four cannot drift apart.
TARGET_FIELDS: tuple[str, ...] = (
    "entry",
    "engagement",
    "important_date",
    "effective_date",
    "work_victory",
)


def _exactly_one_target() -> models.Q:
    """``Q`` matching a row that names exactly one of :data:`TARGET_FIELDS`."""
    clauses: list[models.Q] = []
    for chosen in TARGET_FIELDS:
        clause = models.Q(**{f"{chosen}__isnull": False})
        for other in TARGET_FIELDS:
            if other != chosen:
                clause &= models.Q(**{f"{other}__isnull": True})
        clauses.append(clause)
    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return combined


class DocumentLinkQuerySet(models.QuerySet):
    def visible_to(self, user: object | None) -> DocumentLinkQuerySet:
        """Links whose document **and** whose record this reader may read.

        Both ends, conjoined, because a link is a statement about two records
        and repeating either of them to somebody who may not see it is a
        disclosure. The document half is the clause ``Document.visible_to``
        applies; the record half is applied per target column and skipped where
        that column is NULL, since a LEFT JOIN to a row that does not exist
        yields NULL and would otherwise fail every comparison
        (AUTH-003, docs/adr/0038).
        """
        scope = scope_for_user(user)
        condition = child_visibility_q(
            scope,
            parent_prefix="document__matter__",
            override_field="document__visibility_override",
        )
        for field in TARGET_FIELDS:
            clause = child_visibility_q(
                scope,
                parent_prefix=f"{field}__matter__",
                override_field=f"{field}__visibility_override",
            )
            # **An empty `Q` is skipped rather than OR-ed in**, and this is not
            # a micro-optimisation. A reader who sees everything gets `Q()` from
            # `child_visibility_q`, and Django's `Q(x) | Q()` collapses to
            # `Q(x)` — so the conjunction would have demanded that *every*
            # target column be NULL, which is the one thing no link row ever is.
            # Measured: the whole table went invisible to a specialist.
            if not clause:
                continue
            condition &= models.Q(**{f"{field}__isnull": True}) | clause
        return apply_scope(self, condition)


class DocumentLink(BaseModel):
    """One document supports one business record, stated explicitly."""

    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="links",
        verbose_name="dokument",
    )

    # -- exactly one of the five below ---------------------------------------
    entry = models.ForeignKey(
        "matters.Entry",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_links",
        verbose_name="sissekanne",
    )
    engagement = models.ForeignKey(
        "matters.MatterEngagement",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_links",
        verbose_name="kaasamine",
    )
    important_date = models.ForeignKey(
        "intelligence.MatterImportantDate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_links",
        verbose_name="oluline tähtaeg",
    )
    effective_date = models.ForeignKey(
        "intelligence.MatterEffectiveDate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_links",
        verbose_name="jõustumine",
    )
    work_victory = models.ForeignKey(
        "intelligence.MatterWorkVictory",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="document_links",
        verbose_name="töövõit",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="sidus",
    )

    objects = DocumentLinkQuerySet.as_manager()

    class Meta:
        verbose_name = "dokumendi seos"
        verbose_name_plural = "dokumendi seosed"
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one_target(),
                name="documents_link_has_exactly_one_record",
            ),
            # One document supports one record once. A duplicate row would
            # render the same file twice under the same fact and make "how many
            # files support this" a question with two answers.
            *[
                models.UniqueConstraint(
                    fields=["document", field],
                    condition=models.Q(**{f"{field}__isnull": False}),
                    name=f"documents_one_link_per_{field}",
                )
                for field in TARGET_FIELDS
            ],
        ]

    def __str__(self) -> str:
        return f"{self.document_id} → {self.target_field or '—'}"

    @property
    def target_field(self) -> str:
        """Which of :data:`TARGET_FIELDS` this row names, or ``""``."""
        for field in TARGET_FIELDS:
            if getattr(self, f"{field}_id", None) is not None:
                return field
        return ""

    @property
    def record(self) -> Any:
        """The business record this link supports."""
        field = self.target_field
        return getattr(self, field) if field else None
