"""Who may be offered as a persona, and who may not.

The list behind the shared door is not "every account that can sign in". It
answers a business question — *whose work is this application showing?* — and
only the department's policy and legal people are an answer to it.

Two failure modes these cases exist to prevent, both of which have a name in the
repository already:

**A technical account becoming somebody.** The administrator account exists to
run the system, not to do the department's work, and an audit row reading
"selected: Testadministraator" would be a claim about work nobody did. This is
the same refusal `app/core/authorization.py` makes about RESTRICTED content, one
step earlier in the flow.

**A rule written as a name.** The department's people change. Every assertion
below is about a *role* on a fixture, never about a display name, so the suite
goes on being true the day somebody joins or leaves (docs/adr/0034).
"""

from __future__ import annotations

import pytest

from app.accounts.enums import UserRole
from app.accounts.selectors import (
    PERSONA_ROLES,
    is_persona_candidate,
    persona_candidates,
    persona_from_id,
)
from tests import factories

pytestmark = pytest.mark.django_db


# -- A, B. the department's own work roles ---------------------------------


def test_an_active_specialist_is_a_candidate():
    person = factories.UserFactory(role=UserRole.SPECIALIST, display_name="Aktiivne spetsialist")

    assert person in persona_candidates()
    assert is_persona_candidate(person)


def test_an_active_department_head_is_a_candidate():
    head = factories.DepartmentHeadFactory(display_name="Aktiivne osakonnajuht")

    assert head in persona_candidates()
    assert is_persona_candidate(head)


# -- C, D, E, F, G. everybody else -----------------------------------------


def test_an_administrator_is_not_a_candidate():
    """Technical administration is not business identity.

    The role that runs the system is deliberately absent from
    `ROLES_WITH_RESTRICTED_ACCESS` and `ROLES_WITH_BUSINESS_WRITE` for exactly
    this reason; being selectable as a persona would reintroduce through the
    front door what those two sets close.
    """
    admin = factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Administraator")

    assert admin not in persona_candidates()
    assert not is_persona_candidate(admin)


def test_a_reader_is_not_a_candidate():
    reader = factories.ReaderFactory(display_name="Lugeja")

    assert reader not in persona_candidates()
    assert not is_persona_candidate(reader)


def test_a_technical_staff_account_is_not_a_candidate_even_with_a_work_role():
    """Stricter than "the role is enough", and deliberately so.

    A technical account is precisely what a crafted POST reaches for, and this
    deployment keeps technical administration on separate accounts from
    business work. The cost of the strictness is visible and fixable — granting
    a lawyer Django-admin access takes them off the list, and somebody notices
    the same day. The opposite failure is a privileged account quietly becoming
    selectable, which nobody notices at all.
    """
    staff = factories.UserFactory(
        role=UserRole.SPECIALIST, is_staff=True, display_name="Tehniline konto"
    )

    assert staff not in persona_candidates()
    assert not is_persona_candidate(staff)


def test_a_superuser_is_not_a_candidate():
    owner = factories.UserFactory(
        role=UserRole.DEPARTMENT_HEAD,
        is_superuser=True,
        is_staff=True,
        display_name="Süsteemi omanik",
    )

    assert owner not in persona_candidates()
    assert not is_persona_candidate(owner)


def test_an_inactive_specialist_is_not_a_candidate():
    """A colleague who has left still owns the work they did.

    The historical record goes on naming them — it is true — and the selector
    stops offering them, which is the whole difference between a register and a
    staff list (Vali kasutaja brief 4).
    """
    former = factories.UserFactory(role=UserRole.SPECIALIST, display_name="Endine kolleeg")
    type(former).objects.filter(pk=former.pk).update(is_active=False)
    former.refresh_from_db()

    assert former not in persona_candidates()
    assert not is_persona_candidate(former)


# -- H. the rule is the role, not the person -------------------------------


def test_a_non_department_account_is_excluded_by_its_role_and_not_by_its_name():
    """The rule that keeps somebody off the list must survive being renamed.

    A name check would be a rule that has to be found and edited every time the
    department changes, and would silently stop working the day an account is
    renamed. This asserts the mechanism rather than the outcome: the same
    person, renamed, is still excluded; and given a department role, is
    included — because the role is the only thing the selector reads.
    """
    outsider = factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Kaur")

    assert outsider not in persona_candidates()

    outsider.display_name = "Keegi hoopis teine"
    outsider.save(update_fields=["display_name", "updated_at"])
    assert outsider not in persona_candidates()

    outsider.role = UserRole.SPECIALIST
    outsider.save(update_fields=["role", "updated_at"])
    assert outsider in persona_candidates()


def test_no_name_or_address_is_written_into_the_rule():
    """The selector reads roles and activity, and nothing about a person.

    A grep-shaped assertion, because the thing being prevented is a *future*
    edit: somebody adding `exclude(upn="...")` to fix one account, which works
    that afternoon and is wrong the following month.
    """
    import ast
    import inspect

    from app.accounts import selectors

    tree = ast.parse(inspect.getsource(selectors))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    # Every function in the module, not the three the persona flow calls. The
    # rule now lives one level down in `department_workers` and the persona
    # entry points delegate to it, so a tuple naming only the delegates would
    # have stopped reading the code that decides anything (docs/adr/0036).
    for name in functions:
        node = functions[name]
        # Prose is allowed to name a colleague; a filter is not. The docstring
        # is dropped before the code is read, so the rule is about what runs.
        code = ast.unparse(
            ast.Module(
                body=[stmt for stmt in node.body if not _is_docstring(stmt)], type_ignores=[]
            )
        )
        # `display_name` is allowed inside `order_by`, which is presentation:
        # the list is read by somebody looking for a colleague. It is forbidden
        # as a *filter*, which would be a rule about a person.
        for forbidden in ("upn", "email", "entra_object_id", "display_name=", "display_name__"):
            assert forbidden not in code, (
                f"{name}() reads {forbidden}, which identifies a person rather than a role"
            )


def _is_docstring(statement: object) -> bool:
    import ast

    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def test_the_included_roles_are_the_two_that_do_department_work():
    assert PERSONA_ROLES == frozenset({UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value})


def test_the_persona_roles_are_the_department_work_roles_themselves():
    """One object, not two frozensets that happen to be equal today.

    The persona list and the assignment controls read the same rule, and this
    is the assertion that they cannot be given different role sets by an edit
    that only looks like a rename (docs/adr/0036).
    """
    from app.accounts.selectors import DEPARTMENT_WORK_ROLES

    assert PERSONA_ROLES is DEPARTMENT_WORK_ROLES


# -- ordering and lookup ---------------------------------------------------


def test_candidates_are_ordered_by_name():
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Sandra")
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Ann")
    factories.DepartmentHeadFactory(display_name="Marko")

    assert list(persona_candidates().values_list("display_name", flat=True)) == [
        "Ann",
        "Marko",
        "Sandra",
    ]


@pytest.mark.parametrize("raw", ["", "not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_a_lookup_that_names_nobody_returns_nothing(raw):
    assert persona_from_id(raw) is None


def test_a_lookup_for_an_excluded_account_returns_nothing():
    """The same answer as "no such user", on purpose.

    A refusal that distinguished "that account exists but may not be a persona"
    from "no such account" would tell somebody probing which guess was closer.
    """
    admin = factories.UserFactory(role=UserRole.ADMINISTRATOR)

    assert persona_from_id(str(admin.pk)) is None


def test_a_lookup_for_a_candidate_returns_them():
    person = factories.UserFactory(role=UserRole.SPECIALIST)

    assert persona_from_id(str(person.pk)) == person


def test_the_sentinel_department_viewer_is_not_a_candidate():
    """The no-persona reader is not a person and cannot become one."""
    from app.core.authorization import DEPARTMENT_VIEWER

    assert not is_persona_candidate(DEPARTMENT_VIEWER)
    assert not is_persona_candidate(None)
