"""The centralized authorization boundary.

These are the non-retrofittable rules: inherited visibility, a child that can
only be more restrictive, and technical administration that is not business
access (master specification 5.2, 16.2).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from app.accounts.services import grant_break_glass
from app.core.authorization import UNRESTRICTED_OVERRIDE_VALUES, scope_for_user
from app.core.enums import Visibility, most_restrictive
from app.core.errors import DomainError
from app.documents.models import Document
from app.documents.services import create_document
from app.matters.models import Matter
from app.matters.services import set_matter_visibility
from tests import factories

pytestmark = pytest.mark.django_db


# -- Matter visibility ------------------------------------------------------


def test_normal_matter_is_visible_to_any_active_user(normal_matter, other_specialist):
    assert normal_matter in Matter.objects.visible_to(other_specialist)


def test_restricted_matter_is_hidden_from_an_uninvolved_specialist(
    restricted_matter, other_specialist
):
    assert restricted_matter not in Matter.objects.visible_to(other_specialist)


def test_restricted_matter_is_visible_to_its_owner(restricted_matter, specialist):
    assert restricted_matter in Matter.objects.visible_to(specialist)


def test_restricted_matter_is_visible_to_a_collaborator(restricted_matter, other_specialist):
    restricted_matter.collaborators.add(other_specialist)
    assert restricted_matter in Matter.objects.visible_to(other_specialist)


def test_restricted_matter_is_visible_to_the_department_head(restricted_matter, department_head):
    assert restricted_matter in Matter.objects.visible_to(department_head)


def test_technical_administration_is_not_business_access(restricted_matter, administrator):
    """An administrator role alone never reads restricted content."""
    assert restricted_matter not in Matter.objects.visible_to(administrator)


def test_superuser_alone_is_not_business_access(restricted_matter, superuser):
    assert restricted_matter not in Matter.objects.visible_to(superuser)


def test_anonymous_and_inactive_users_see_nothing(normal_matter, specialist):
    assert Matter.objects.visible_to(AnonymousUser()).count() == 0
    assert Matter.objects.visible_to(None).count() == 0

    specialist.is_active = False
    specialist.save(update_fields=["is_active", "updated_at"])
    assert Matter.objects.visible_to(specialist).count() == 0


def test_break_glass_opens_restricted_content_only_while_it_is_valid(
    restricted_matter, other_specialist, department_head
):
    assert restricted_matter not in Matter.objects.visible_to(other_specialist)

    grant = grant_break_glass(
        user=other_specialist,
        granted_by=department_head,
        reason="Tugijuhtum",
        duration=timedelta(hours=1),
    )
    assert scope_for_user(other_specialist).break_glass_grant_id == grant.id
    assert restricted_matter in Matter.objects.visible_to(other_specialist)

    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.starts_at = timezone.now() - timedelta(hours=2)
    grant.save(update_fields=["expires_at", "starts_at", "updated_at"])
    assert restricted_matter not in Matter.objects.visible_to(other_specialist)


def test_visible_matters_are_not_duplicated_by_the_collaborator_join(
    restricted_matter, specialist, other_specialist
):
    restricted_matter.collaborators.add(specialist, other_specialist)
    assert Matter.objects.visible_to(specialist).count() == 1


# -- Child visibility -------------------------------------------------------


def test_child_inherits_matter_visibility(restricted_matter, specialist):
    document = create_document(matter=restricted_matter, title="Tõend", created_by=specialist)
    assert document.effective_visibility == Visibility.RESTRICTED


def test_child_may_be_more_restrictive_than_its_matter(normal_matter, specialist):
    document = create_document(
        matter=normal_matter,
        title="Tundlik tõend",
        created_by=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    assert document.effective_visibility == Visibility.RESTRICTED


def test_child_can_never_become_less_restrictive_than_its_matter(restricted_matter, specialist):
    document = create_document(
        matter=restricted_matter,
        title="Tõend",
        created_by=specialist,
        visibility_override=Visibility.NORMAL,
    )
    assert document.effective_visibility == Visibility.RESTRICTED

    document.visibility_override = Visibility.NORMAL
    document.save()
    document.refresh_from_db()
    assert document.effective_visibility == Visibility.RESTRICTED


def test_document_under_restricted_matter_is_hidden_from_uninvolved_users(
    restricted_matter, specialist, other_specialist
):
    create_document(matter=restricted_matter, title="Tõend", created_by=specialist)
    assert Document.objects.visible_to(other_specialist).count() == 0
    assert Document.objects.visible_to(specialist).count() == 1


def test_restricting_a_matter_propagates_to_its_children(
    normal_matter, specialist, other_specialist
):
    document = create_document(matter=normal_matter, title="Tõend", created_by=specialist)
    assert Document.objects.visible_to(other_specialist).count() == 1

    set_matter_visibility(matter=normal_matter, visibility=Visibility.RESTRICTED, actor=specialist)

    document.refresh_from_db()
    assert document.effective_visibility == Visibility.RESTRICTED
    assert Document.objects.visible_to(other_specialist).count() == 0


def test_relaxing_a_matter_leaves_individually_restricted_children_restricted(
    restricted_matter, specialist, other_specialist
):
    inherited = create_document(matter=restricted_matter, title="Tavaline", created_by=specialist)
    explicit = create_document(
        matter=restricted_matter,
        title="Eriti tundlik",
        created_by=specialist,
        visibility_override=Visibility.RESTRICTED,
    )

    set_matter_visibility(matter=restricted_matter, visibility=Visibility.NORMAL, actor=specialist)

    inherited.refresh_from_db()
    explicit.refresh_from_db()
    assert inherited.effective_visibility == Visibility.NORMAL
    assert explicit.effective_visibility == Visibility.RESTRICTED

    visible = set(Document.objects.visible_to(other_specialist))
    assert inherited in visible
    assert explicit not in visible


# -- no write path can produce a stale, leaking visibility -------------------


def test_a_bulk_update_on_the_matter_hides_its_children_immediately(
    normal_matter, specialist, other_specialist
):
    """The service is not the only safe way to restrict a Matter.

    Effective visibility is derived, so a write that bypasses
    set_matter_visibility entirely still hides the children at once.
    """
    create_document(matter=normal_matter, title="Tõend", created_by=specialist)
    assert Document.objects.visible_to(other_specialist).count() == 1

    Matter.objects.filter(pk=normal_matter.pk).update(visibility=Visibility.RESTRICTED)

    assert Document.objects.visible_to(other_specialist).count() == 0
    assert Document.objects.visible_to(specialist).count() == 1


def test_a_raw_save_on_the_matter_hides_its_children_immediately(
    normal_matter, specialist, other_specialist
):
    create_document(matter=normal_matter, title="Tõend", created_by=specialist)

    normal_matter.visibility = Visibility.RESTRICTED
    normal_matter.save(update_fields=["visibility", "updated_at"])

    assert Document.objects.visible_to(other_specialist).count() == 0


def test_a_bulk_update_on_the_child_cannot_relax_it(
    restricted_matter, specialist, other_specialist
):
    """A child override can only add restriction, never remove the parent's."""
    document = create_document(matter=restricted_matter, title="Tõend", created_by=specialist)

    Document.objects.filter(pk=document.pk).update(visibility_override=Visibility.NORMAL)

    assert Document.objects.visible_to(other_specialist).count() == 0
    document.refresh_from_db()
    assert document.effective_visibility == Visibility.RESTRICTED


def test_a_child_created_without_the_service_still_inherits(
    restricted_matter, specialist, other_specialist
):
    document = Document.objects.create(
        matter=restricted_matter, title="Otse loodud", created_by=specialist
    )
    assert document.effective_visibility == Visibility.RESTRICTED
    assert Document.objects.visible_to(other_specialist).count() == 0


def test_nothing_stores_a_child_effective_visibility_column():
    """A stored copy is what could go stale, so there must not be one."""
    stored = {field.name for field in Document._meta.fields}
    assert "effective_visibility" not in stored
    assert "visibility_override" in stored


def test_the_sql_annotation_agrees_with_the_python_property(
    restricted_matter, normal_matter, specialist
):
    inherited = create_document(matter=restricted_matter, title="A", created_by=specialist)
    overridden = create_document(
        matter=normal_matter,
        title="B",
        created_by=specialist,
        visibility_override=Visibility.RESTRICTED,
    )
    plain = create_document(matter=normal_matter, title="C", created_by=specialist)

    annotated = {
        row.id: row.derived_visibility for row in Document.objects.with_effective_visibility()
    }
    for document in (inherited, overridden, plain):
        assert annotated[document.id] == document.effective_visibility

    assert annotated[inherited.id] == Visibility.RESTRICTED
    assert annotated[overridden.id] == Visibility.RESTRICTED
    assert annotated[plain.id] == Visibility.NORMAL


def test_unknown_visibility_is_rejected(normal_matter):
    with pytest.raises(DomainError):
        set_matter_visibility(matter=normal_matter, visibility="SECRET")


def test_archive_matters_are_scoped_the_same_way_as_full_matters(other_specialist):
    archive = factories.ArchiveMatterFactory(visibility=Visibility.RESTRICTED, owner=None)
    assert archive not in Matter.objects.visible_to(other_specialist)


# -- visibility values fail closed ------------------------------------------


def test_the_database_refuses_an_unknown_matter_visibility(normal_matter):
    with pytest.raises(IntegrityError), transaction.atomic():
        Matter.objects.filter(pk=normal_matter.pk).update(visibility="PUBLIC")


def test_the_database_refuses_an_unknown_child_override(normal_matter, specialist):
    document = create_document(matter=normal_matter, title="Tõend", created_by=specialist)
    with pytest.raises(IntegrityError), transaction.atomic():
        Document.objects.filter(pk=document.pk).update(visibility_override="PUBLIC")


def test_an_empty_override_remains_valid(normal_matter, specialist):
    document = create_document(matter=normal_matter, title="Tõend", created_by=specialist)
    Document.objects.filter(pk=document.pk).update(visibility_override="")
    Document.objects.filter(pk=document.pk).update(visibility_override=Visibility.NORMAL)
    Document.objects.filter(pk=document.pk).update(visibility_override=Visibility.RESTRICTED)


def _drop_constraint(table: str, name: str) -> None:
    """Remove a CHECK constraint for the duration of the test transaction.

    PostgreSQL DDL is transactional, so the test rollback restores it. This lets
    us prove the authorization layer fails closed on a value the constraint
    would normally have prevented — belt and braces, tested independently.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")


def test_an_unknown_matter_visibility_is_never_read_as_normal(
    normal_matter, specialist, other_specialist
):
    create_document(matter=normal_matter, title="Tõend", created_by=specialist)
    assert Matter.objects.visible_to(other_specialist).count() == 1

    _drop_constraint("matters_matter", "matters_visibility_vocabulary")
    Matter.objects.filter(pk=normal_matter.pk).update(visibility="PUBLIC")

    # Neither the Matter nor its children may appear for an uninvolved user.
    assert Matter.objects.visible_to(other_specialist).count() == 0
    assert Document.objects.visible_to(other_specialist).count() == 0
    # The owner still reaches it through participation, so nothing is orphaned.
    assert Matter.objects.visible_to(specialist).count() == 1


def test_an_unknown_child_override_is_never_read_as_normal(
    normal_matter, specialist, other_specialist
):
    document = create_document(matter=normal_matter, title="Tõend", created_by=specialist)
    assert Document.objects.visible_to(other_specialist).count() == 1

    _drop_constraint("documents_document", "documents_visibility_override_vocabulary")
    Document.objects.filter(pk=document.pk).update(visibility_override="PUBLIC")

    assert Document.objects.visible_to(other_specialist).count() == 0
    document.refresh_from_db()
    assert document.effective_visibility == Visibility.RESTRICTED


def test_an_unknown_value_resolves_to_restricted_rather_than_being_echoed():
    assert most_restrictive("PUBLIC", Visibility.NORMAL) == Visibility.RESTRICTED
    assert most_restrictive(Visibility.NORMAL, Visibility.NORMAL) == Visibility.NORMAL
    assert most_restrictive(Visibility.NORMAL, Visibility.RESTRICTED) == Visibility.RESTRICTED


def test_the_normal_child_condition_whitelists_rather_than_blacklists():
    """A blacklist would let any future unrecognised value through."""
    assert UNRESTRICTED_OVERRIDE_VALUES == ("", Visibility.NORMAL.value)
