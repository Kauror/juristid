"""Business capabilities: role defaults, individual overrides, and what they never touch.

docs/adr/0145 §3. Three properties, each its own section:

1. **With no overrides, nothing changed.** Every capability's default is the
   role rule it replaced, asserted against that rule for every role.
2. **An override reaches every surface the capability guards** — the GET that
   would render a control, the POST that acts, and the service beneath both.
3. **Administrative power is not business access.** An account administrator
   reads and writes exactly what their role reads and writes; RESTRICTED stays
   where ADR 0042 put it.

These run in the default mode (`none`) and under the shared gate, because the
business capabilities are mode-independent — only *exercising* an
administrative one needs personal sign-in.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.accounts import capabilities
from app.accounts.enums import Capability, UserRole
from app.core.authorization import (
    DEPARTMENT_VIEWER,
    ROLES_WITH_WORK_VICTORY_REVIEW,
    has_capability,
    is_department_head,
    may_assign_work,
    may_review_work_victory,
    may_view_department_management,
    may_write_business_content,
    scope_for_user,
)
from app.core.enums import Visibility
from app.intelligence.services import (
    REVIEW_NOT_PERMITTED,
    add_work_victory_candidate,
    confirm_work_victory,
)
from app.matters.models import Matter
from app.matters.services import ASSIGNMENT_NOT_PERMITTED, assign_matter
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

PAGE = "matters:department"
ROLES = [UserRole.SPECIALIST, UserRole.DEPARTMENT_HEAD, UserRole.READER, UserRole.ADMINISTRATOR]


def _account(role, **overrides):
    return factories.UserFactory(role=role, capability_overrides=overrides)


# -- 1. the defaults are the old rules --------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_the_defaults_are_exactly_the_rules_they_replaced(role):
    user = _account(role)

    assert may_view_department_management(user) == is_department_head(user)
    assert may_review_work_victory(user) == (role in {"DEPARTMENT_HEAD"})
    assert may_assign_work(user) == may_write_business_content(user)
    assert not has_capability(user, Capability.MANAGE_ACCOUNTS)
    assert not has_capability(user, Capability.DELEGATE_ADMINISTRATION)


def test_the_review_role_set_is_read_from_the_capability_defaults():
    assert ROLES_WITH_WORK_VICTORY_REVIEW == frozenset({"DEPARTMENT_HEAD"})
    assert (
        ROLES_WITH_WORK_VICTORY_REVIEW
        is capabilities.DEFAULT_HOLDERS[Capability.REVIEW_WORK_VICTORIES.value]
    )


def test_no_role_is_an_account_administrator_by_default():
    for capability in capabilities.ADMINISTRATIVE_CAPABILITIES:
        assert capabilities.DEFAULT_HOLDERS[capability] == frozenset()


@pytest.mark.parametrize(
    "who",
    ["anonymous", "department_viewer", "inactive_head", "none"],
)
def test_somebody_who_is_not_acting_holds_nothing(who):
    from django.contrib.auth.models import AnonymousUser

    subject = {
        "anonymous": AnonymousUser(),
        "department_viewer": DEPARTMENT_VIEWER,
        "inactive_head": factories.DepartmentHeadFactory(
            is_active=False,
            capability_overrides={"accounts.manage": "allow"},
        ),
        "none": None,
    }[who]
    for capability in capabilities.ALL_CAPABILITIES:
        assert not has_capability(subject, capability)


def test_unrecognised_overrides_are_ignored():
    user = _account(
        UserRole.SPECIALIST,
        **{"department.view_management": "yes please", "everything.admin": "allow"},
    )
    user.capability_overrides["work.assign"] = ["deny"]

    assert not has_capability(user, Capability.VIEW_DEPARTMENT_MANAGEMENT)
    assert has_capability(user, Capability.ASSIGN_WORK)
    assert not has_capability(user, "everything.admin")


def test_a_write_capability_cannot_make_a_reader_or_a_technician_an_author():
    for role in (UserRole.READER, UserRole.ADMINISTRATOR):
        user = _account(role, **{"work_victory.review": "allow", "work.assign": "allow"})
        assert not may_review_work_victory(user)
        assert not may_assign_work(user)


def test_the_management_view_may_be_lent_to_a_reader_but_not_a_technician():
    reader = _account(UserRole.READER, **{"department.view_management": "allow"})
    technician = _account(UserRole.ADMINISTRATOR, **{"department.view_management": "allow"})

    assert may_view_department_management(reader)
    assert not may_view_department_management(technician)


def test_delegation_without_management_is_nothing():
    user = _account(UserRole.SPECIALIST, **{"accounts.delegate": "allow"})
    assert not has_capability(user, Capability.DELEGATE_ADMINISTRATION)


# -- 2. an override reaches every surface ------------------------------------------------


def test_a_deputy_sees_the_management_sections(client):
    factories.MatterFactory()
    deputy = _account(UserRole.SPECIALIST, **{"department.view_management": "allow"})
    client.force_login(deputy)

    assert "uxstat" in client.get(reverse(PAGE)).content.decode()


def test_a_head_without_the_view_does_not(client):
    factories.MatterFactory()
    head = _account(UserRole.DEPARTMENT_HEAD, **{"department.view_management": "deny"})
    client.force_login(head)

    assert "uxstat" not in client.get(reverse(PAGE)).content.decode()


def test_a_colleagues_desk_follows_the_same_capability(client):
    colleague = factories.UserFactory()
    url = reverse("matters:person_work", kwargs={"pk": colleague.pk})

    client.force_login(_account(UserRole.SPECIALIST, **{"department.view_management": "allow"}))
    assert client.get(url).status_code == 200

    client.force_login(_account(UserRole.DEPARTMENT_HEAD, **{"department.view_management": "deny"}))
    assert client.get(url).status_code == 404

    client.force_login(_account(UserRole.SPECIALIST))
    assert client.get(url).status_code == 404


def test_a_lent_view_shows_a_reader_nothing_restricted(client):
    """The sections are built for the reader's own scope, so RESTRICTED stays out."""
    owner = factories.UserFactory()
    factories.MatterFactory(
        owner=owner, visibility=Visibility.RESTRICTED, title="Piiratud juhtimisvaates"
    )
    factories.MatterFactory(owner=owner, title="Tavaline juhtimisvaates")
    reader = _account(UserRole.READER, **{"department.view_management": "allow"})
    client.force_login(reader)

    body = client.get(reverse(PAGE)).content.decode()

    assert "uxstat" in body
    assert "Piiratud juhtimisvaates" not in body
    assert not scope_for_user(reader).sees_all_restricted


def _victory(matter, author):
    return add_work_victory_candidate(matter=matter, title="Kandidaat", actor=author)


def test_a_deputy_may_confirm_a_work_victory(client):
    author = factories.UserFactory()
    matter = factories.MatterFactory(owner=author)
    record = _victory(matter, author)
    deputy = _account(UserRole.SPECIALIST, **{"work_victory.review": "allow"})
    client.force_login(deputy)

    response = client.post(
        reverse(
            "intelligence:confirm_work_victory", kwargs={"matter_id": matter.pk, "pk": record.pk}
        )
    )

    assert response.status_code == 302
    record.refresh_from_db()
    assert record.status == "CONFIRMED"


def test_a_head_without_review_is_refused_at_the_route_and_the_service(client):
    author = factories.UserFactory()
    matter = factories.MatterFactory(owner=author)
    record = _victory(matter, author)
    head = _account(UserRole.DEPARTMENT_HEAD, **{"work_victory.review": "deny"})
    client.force_login(head)
    url = reverse(
        "intelligence:confirm_work_victory", kwargs={"matter_id": matter.pk, "pk": record.pk}
    )

    assert client.get(url).status_code == 403
    assert client.post(url).status_code == 403
    with refused(REVIEW_NOT_PERMITTED):
        confirm_work_victory(record=record, actor=head)
    record.refresh_from_db()
    assert record.status != "CONFIRMED"


def test_a_specialist_is_refused_by_the_service_too():
    author = factories.UserFactory()
    record = _victory(factories.MatterFactory(owner=author), author)

    with refused(REVIEW_NOT_PERMITTED):
        confirm_work_victory(record=record, actor=author)


# -- assignment -------------------------------------------------------------------------


@pytest.fixture
def no_assign():
    return _account(UserRole.SPECIALIST, **{"work.assign": "deny"})


def test_without_work_assign_the_service_refuses_a_reassignment(no_assign):
    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=no_assign)

    with refused(ASSIGNMENT_NOT_PERMITTED):
        assign_matter(matter=matter, owner=other, actor=no_assign)
    matter.refresh_from_db()
    assert matter.owner == no_assign


def test_an_operations_assignment_is_not_a_persons(no_assign):
    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=None)

    assign_matter(matter=matter, owner=other, actor=no_assign, provenance={"method": "test"})

    matter.refresh_from_db()
    assert matter.owner == other


def test_without_work_assign_the_register_row_route_refuses(client, no_assign):
    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=None)
    client.force_login(no_assign)

    client.post(reverse("matters:assign_owner", kwargs={"pk": matter.pk}), {"owner": other.pk})

    matter.refresh_from_db()
    assert matter.owner is None


def test_without_work_assign_the_header_control_refuses_and_is_not_offered(client, no_assign):
    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=no_assign)
    client.force_login(no_assign)

    response = client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "owner"}),
        {"owner": other.pk},
    )

    assert response.status_code == 400
    matter.refresh_from_db()
    assert matter.owner == no_assign
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    assert "field='owner'" not in page
    assert reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "owner"}) not in page


def test_without_work_assign_new_work_can_only_name_oneself(no_assign):
    from app.matters.forms import IncomingIntakeForm, MatterCreateForm

    other = factories.UserFactory()
    for form_class in (MatterCreateForm, IncomingIntakeForm):
        form = form_class(viewer=no_assign)
        offered = list(form.fields["owner"].queryset)
        assert offered == [no_assign], form_class.__name__
        bound = form_class({"title": "Uus", "owner": str(other.pk)}, viewer=no_assign)
        bound.is_valid()
        assert "owner" in bound.errors, form_class.__name__


def test_without_work_assign_the_edit_form_keeps_the_owner(no_assign):
    from app.matters.forms import MatterEditForm

    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=no_assign)
    form = MatterEditForm({"owner": str(other.pk)}, matter=matter, viewer=no_assign)

    assert form.fields["owner"].disabled
    form.is_valid()
    assert form.cleaned_data.get("owner") in (no_assign, no_assign.pk)


def test_a_specialist_still_assigns_by_default(client):
    """Collaborative work is not narrowed: ADR 0037 and 0042 stand."""
    specialist = factories.UserFactory()
    other = factories.UserFactory()
    matter = factories.MatterFactory(owner=None)
    client.force_login(specialist)

    client.post(reverse("matters:assign_owner", kwargs={"pk": matter.pk}), {"owner": other.pk})

    matter.refresh_from_db()
    assert matter.owner == other


# -- 3. administration is not business access --------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_account_administration_adds_nothing_to_business_access(role):
    plain = _account(role)
    admin = _account(role, **{"accounts.manage": "allow", "accounts.delegate": "allow"})

    assert has_capability(admin, Capability.DELEGATE_ADMINISTRATION)
    for capability in capabilities.BUSINESS_CAPABILITIES:
        assert has_capability(admin, capability) == has_capability(plain, capability)
    assert may_write_business_content(admin) == may_write_business_content(plain)
    assert scope_for_user(admin).sees_all_restricted == scope_for_user(plain).sees_all_restricted
    assert not admin.is_staff and not admin.is_superuser


def test_a_reader_administrator_still_cannot_read_restricted_work():
    owner = factories.UserFactory()
    secret = factories.MatterFactory(owner=owner, visibility=Visibility.RESTRICTED)
    reader_admin = _account(UserRole.READER, **{"accounts.manage": "allow"})

    assert not Matter.objects.visible_to(reader_admin).filter(pk=secret.pk).exists()


def test_both_lawyer_roles_keep_department_wide_access():
    owner = factories.UserFactory()
    secret = factories.MatterFactory(owner=owner, visibility=Visibility.RESTRICTED)
    for role in (UserRole.SPECIALIST, UserRole.DEPARTMENT_HEAD):
        colleague = _account(role, **{"department.view_management": "deny", "work.assign": "deny"})
        assert Matter.objects.visible_to(colleague).filter(pk=secret.pk).exists()
        assert may_write_business_content(colleague)


def test_only_the_central_service_reads_the_overrides():
    """The predicates read the central resolution; nothing compares overrides itself."""
    from pathlib import Path

    from django.conf import settings

    root = Path(settings.BASE_DIR) / "app"
    readers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "migrations" not in path.parts
        and "capability_overrides" in path.read_text(encoding="utf-8")
    )
    assert readers == [
        "accounts/admin_views.py",
        "accounts/administration.py",
        "accounts/models.py",
        "core/authorization.py",
    ]


def test_denied_and_granted_are_mode_independent_but_administration_is_not(settings, rf):
    """Under the shared gate a persona holding `accounts.manage` administers nothing."""
    from app.accounts import local_auth
    from tests.gate import apply_shared_gate

    apply_shared_gate(settings)
    persona = _account(
        UserRole.DEPARTMENT_HEAD, **{"accounts.manage": "allow", "accounts.delegate": "allow"}
    )
    request = rf.get("/")
    request.user = persona
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    request.session[local_auth.SESSION_USER] = str(persona.pk)
    request.session[local_auth.SESSION_SECOND_FACTOR_AT] = "2026-10-08T10:00:00+00:00"

    assert has_capability(persona, Capability.MANAGE_ACCOUNTS)
    assert not local_auth.may_administer_accounts(request)
