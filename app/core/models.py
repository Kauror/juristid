"""Shared model bases.

``app.core`` owns no tables of its own; it owns the shapes and the one
authorization chokepoint every other module reuses.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from app.core.enums import Visibility, most_restrictive
from app.core.errors import ImmutableRecordError
from app.core.ids import uuid7


class BaseModel(models.Model):
    """Time-sortable UUID primary key plus creation/update stamps."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppendOnlyModel(models.Model):
    """A record that is written once and never changed or removed.

    The Python guards below are a courtesy; the guarantee is a database trigger
    installed by each append-only table's migration.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError(f"{type(self).__name__} rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ImmutableRecordError(f"{type(self).__name__} rows are append-only.")


class VisibilityInheritingModel(BaseModel):
    """A child record whose visibility follows its parent Matter.

    ``visibility_override`` is the only stored visibility field, and it can only
    make a record *more* restrictive. The effective visibility is **never
    stored**: it is computed from the parent and the override, at query time by
    ``app.core.authorization`` and in Python by the property below.

    That is deliberate. A denormalised column would have to be kept in step with
    every change to the parent Matter, and any write that bypassed the service
    that maintained it — a bulk ``update()``, a data migration, a shell session,
    a future importer — would leave a stale value that reads as *less*
    restrictive than the truth. Deriving it removes the failure mode instead of
    guarding against it (master specification 5.2, 16.2).
    """

    visibility_override = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        blank=True,
        default="",
        db_index=True,
        verbose_name="nähtavuse kitsendus",
        help_text="Tühi tähendab, et nähtavus päritakse teemalt.",
    )

    class Meta:
        abstract = True

    def parent_visibility(self) -> str:
        raise NotImplementedError

    @property
    def effective_visibility(self) -> str:
        """The visibility that actually applies to this record.

        Reads the parent Matter, so in list contexts prefer the queryset
        annotation, which computes the same value in SQL.
        """
        own = self.visibility_override or Visibility.NORMAL.value
        return most_restrictive(self.parent_visibility(), own)

    @property
    def is_restricted(self) -> bool:
        return self.effective_visibility == Visibility.RESTRICTED


class RemovableRecord(models.Model):
    """A user-created business record a lawyer may take off the active file.

    `Teema käik` is professional history, and a lawyer who files a `Märge` on
    the wrong Matter, records the same `Kaasamine` twice or mistypes an
    `Oluline tähtaeg` has put something on a colleague's file that never
    happened. Until this existed the only repair was `Muuda` — rewriting the
    mistake into something else — which leaves a record nobody made and a date
    nobody chose (OWNER-04, docs/adr/0102).

    **Removal is not deletion and not cancellation.** Three meanings are kept
    apart here, and confusing any two of them is how a file starts lying:

    * `FactStatus.CANCELLED` — «Tühistatud». The plan changed. The expectation
      was real, somebody called it off, and both halves are history: the row
      stays in the chronology saying so (Stage-2G brief 5, 33).
    * `SubmissionStatus.WITHDRAWN` — «Tagasi võetud». The letter went out and
      was taken back. The sending is still a fact about the world.
    * Removal, here — **this record should never have been on this file.**
      There is nothing to say in the chronology, because nothing happened.

    **What it does.** ``removed_at`` takes the row out of every business read
    through the model's own ``visible_to`` chokepoint, which is the same place
    visibility is enforced and therefore the same place a reader, a count, a
    search projection, the rail and an export all pass through. Nothing is
    filtered at call sites, because a filter at a call site is a filter some
    other call site forgets.

    **What it does not do.** No byte is destroyed. The `ChangeEvent` trail
    keeps what was recorded, who recorded it and who removed it; evidence stays
    in the immutable store under its retention rules; and the technical audit
    path reads through the plain manager, where a removed row is still there.
    The lawyer's meaning is «take this off the active file», never «erase that
    it ever existed» — and the second is not a thing this architecture offers
    anybody (AGENTS.md: audit append-only, evidence immutable).

    Restoration is deliberately **not** a user-facing action. A mistake removed
    in error is re-recorded, which is one capture away and truthful about who
    put it back; an undelete button implies a recycle bin that this product
    does not have and would have to authorize, scope and test as a second
    surface.
    """

    removed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="eemaldatud",
        help_text="Täidetud, kui kirje on aktiivsest teema käigust eemaldatud.",
    )
    #: Who took it off the file. ``SET_NULL`` because a departed colleague's
    #: account may go while the record stays, and the `ChangeEvent` carries the
    #: attribution that has to survive regardless.
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="eemaldaja",
    )

    class Meta:
        abstract = True

    @property
    def is_removed(self) -> bool:
        return self.removed_at is not None
