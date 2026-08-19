"""Shared model bases.

``app.core`` owns no tables of its own; it owns the shapes and the one
authorization chokepoint every other module reuses.
"""

from __future__ import annotations

from typing import Any

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
