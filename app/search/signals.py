"""Keeping the projection in step with the records it projects.

Stage 1's search read the Matter table directly, so a new Matter was findable
the instant it was saved. A projection is not, and CI proved the point: with
indexing left to an operator, a freshly seeded Matter searched for by title
returned nothing at all — quietly, with a plausible empty-results page. A search
that silently misses records is worse than no search, because people stop
checking.

So the ordinary path maintains itself. These handlers cover the writes the
application actually performs: saving a Matter, and changing its policy areas or
tags. They are deliberately narrow, and two things are **not** covered:

* bulk writes that bypass signals — ``QuerySet.update()``, ``bulk_create()``,
  data migrations. The importer is the one bulk writer in the system and it
  refreshes explicitly, under :func:`app.search.indexing.suspend_indexing`.
* renaming an Organisation or editing its aliases, which changes the indexed
  text of every Matter pointing at it. That is a taxonomy-administration event,
  it is rare, and fanning out from it would mean reindexing thousands of rows
  inside somebody's form submission. ``rebuild_search_index`` is the answer and
  is documented as such.

Both gaps are recoverable by a rebuild, which is the property the projection was
designed around.
"""

from __future__ import annotations

from typing import Any

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from app.matters.models import Matter, TagAssignment
from app.search.indexing import indexable_matters, indexing_is_suspended, refresh_matters


def _refresh(matter_id: Any) -> None:
    if indexing_is_suspended() or matter_id is None:
        return
    refresh_matters(indexable_matters().filter(pk=matter_id))


@receiver(post_save, sender=Matter, dispatch_uid="search_refresh_matter")
def refresh_on_matter_save(sender: type[Matter], instance: Matter, **kwargs: Any) -> None:
    _refresh(instance.pk)


@receiver(m2m_changed, sender=Matter.policy_areas.through, dispatch_uid="search_refresh_areas")
def refresh_on_policy_areas_changed(
    sender: type, instance: Any, action: str, **kwargs: Any
) -> None:
    # post_* only: the pre_* actions fire before the rows exist, so indexing
    # then would store the state the change is about to replace.
    if action in {"post_add", "post_remove", "post_clear"}:
        _refresh(getattr(instance, "pk", None))


@receiver(m2m_changed, sender=Matter.tags.through, dispatch_uid="search_refresh_tags")
def refresh_on_tags_changed(sender: type, instance: Any, action: str, **kwargs: Any) -> None:
    if action in {"post_add", "post_remove", "post_clear"}:
        _refresh(getattr(instance, "pk", None))


@receiver(post_save, sender=TagAssignment, dispatch_uid="search_refresh_tag_added")
@receiver(post_delete, sender=TagAssignment, dispatch_uid="search_refresh_tag_removed")
def refresh_on_tag_assignment(
    sender: type[TagAssignment], instance: TagAssignment, **kwargs: Any
) -> None:
    """Tags need both handlers, and CI proved it.

    ``Matter.tags`` has an explicit through model, because an assignment records
    who confirmed it and when. That means two different write paths reach it and
    each misses the other's signal: ``matter.tags.add(...)`` goes through
    ``bulk_create`` and never fires ``post_save``, while a ``TagAssignment``
    created directly by a service never fires ``m2m_changed``.

    Refreshing twice when both happen is cheap. Missing one makes a tagged
    matter unfindable by its tag, silently.
    """
    _refresh(instance.matter_id)
