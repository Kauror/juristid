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
    """A child record whose visibility is derived from its parent Matter.

    ``visibility_override`` is the only field a user sets, and it may only make
    the record *more* restrictive. ``effective_visibility`` is derived on save
    and is what the authorization boundary reads, so a child can never end up
    less restrictive than its parent (master specification 5.2).
    """

    visibility_override = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        blank=True,
        default="",
        verbose_name="nähtavuse kitsendus",
        help_text="Tühi tähendab, et nähtavus päritakse teemalt.",
    )
    effective_visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.NORMAL,
        editable=False,
        db_index=True,
        verbose_name="tegelik nähtavus",
    )

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(
                    visibility_override=Visibility.RESTRICTED,
                    effective_visibility=Visibility.NORMAL,
                ),
                name="%(app_label)s_%(class)s_effective_not_weaker_than_override",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.recompute_effective_visibility()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = sorted({*update_fields, "effective_visibility"})
        super().save(*args, **kwargs)

    def parent_visibility(self) -> str:
        raise NotImplementedError

    def recompute_effective_visibility(self) -> str:
        parent = self.parent_visibility()
        own = self.visibility_override or Visibility.NORMAL.value
        self.effective_visibility = most_restrictive(parent, own)
        return self.effective_visibility
