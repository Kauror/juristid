"""One rule for who currently does the department's work.

`app/accounts/selectors.py` answers two questions that were separate rules until
docs/adr/0036: *whose work is this application showing* (the persona list) and
*who may current business work be given to* (every owner and responsible
control). They are the same question about the same account, and this suite
asserts the single definition rather than either of its consumers — the persona
half has its own cases in `test_persona_candidates.py`, and the assignment half
has its own in `test_work_assignment_eligibility.py`.

Two failure modes these cases exist to prevent:

**A predicate that has drifted from its queryset.** A form narrows a list with
the queryset and a service refuses a value with the predicate. If the two ever
disagree the rule is enforced in one half of a flow and not the other, which is
the exact shape of the defect this whole branch is about. Parity is asserted
directly, over every case, rather than left to two docstrings promising the same
thing.

**A rule written as a name.** The department's people change. Every assertion
below is about a *role* on a fixture, never about a display name, so the suite
goes on being true the day somebody joins or leaves.
"""

from __future__ import annotations

import pytest

from app.accounts.enums import UserRole
from app.accounts.selectors import (
    DEPARTMENT_WORK_ROLES,
    assignable_business_users,
    assignable_including,
    department_workers,
    is_assignable_business_user,
    is_department_worker,
)
from tests import factories

pytestmark = pytest.mark.django_db


def _inactive(person):
    """Deactivate without going through `save`, which would touch nothing else."""
    type(person).objects.filter(pk=person.pk).update(is_active=False)
    person.refresh_from_db()
    return person


# =========================================================================
# A–H. who the rule includes, and who it does not
# =========================================================================


def test_an_active_specialist_is_a_department_worker():
    person = factories.UserFactory(role=UserRole.SPECIALIST, display_name="Spetsialist")

    assert person in department_workers()
    assert is_department_worker(person)


def test_an_active_department_head_is_a_department_worker():
    head = factories.DepartmentHeadFactory(display_name="Osakonnajuht")

    assert head in department_workers()
    assert is_department_worker(head)


def test_an_administrator_is_not_a_department_worker():
    """Administering the system is not doing the department's work.

    The account that runs the deployment exists for that, and a file assigned
    to it would be a claim that somebody is working on something nobody is.
    """
    admin = factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Administraator")

    assert admin not in department_workers()
    assert not is_department_worker(admin)


def test_a_reader_is_not_a_department_worker():
    reader = factories.ReaderFactory(display_name="Lugeja")

    assert reader not in department_workers()
    assert not is_department_worker(reader)


def test_an_inactive_specialist_is_not_a_department_worker():
    """A colleague who has left still owns the work they did.

    The historical record goes on naming them — it is true — and the rule stops
    offering them. That difference is the whole point of this branch: see
    `assignable_including` below, and the preservation cases in
    `test_work_assignment_eligibility.py`.
    """
    former = _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Endine"))

    assert former not in department_workers()
    assert not is_department_worker(former)


def test_a_technical_staff_account_is_not_a_department_worker_even_with_a_work_role():
    staff = factories.UserFactory(
        role=UserRole.SPECIALIST, is_staff=True, display_name="Tehniline konto"
    )

    assert staff not in department_workers()
    assert not is_department_worker(staff)


def test_a_superuser_is_not_a_department_worker_even_with_a_work_role():
    owner = factories.UserFactory(
        role=UserRole.DEPARTMENT_HEAD, is_superuser=True, display_name="Süsteemi omanik"
    )

    assert owner not in department_workers()
    assert not is_department_worker(owner)


def test_an_ordinary_technical_account_is_not_a_department_worker():
    """H. An account that exists so something can sign in, and for no other reason.

    Neither of the two work roles, active, and not otherwise remarkable — the
    shape a service or integration account takes when nobody has thought about
    what role to give it. `is_active=True` alone used to be enough to be handed
    a file; the role is what decides now.
    """
    technical = factories.UserFactory(role=UserRole.READER, display_name="Integratsioon")

    assert technical not in department_workers()
    assert not is_department_worker(technical)


def test_the_included_roles_are_exactly_the_two_that_do_department_work():
    assert DEPARTMENT_WORK_ROLES == frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value}
    )


# =========================================================================
# I. order
# =========================================================================


def test_department_workers_are_ordered_by_display_name():
    """The order the product has always used for a list of colleagues.

    Asserted as a *rule* and not as three names: the fixtures are created out
    of order and the assertion is that the result is sorted, so nothing here
    encodes who the department currently is.
    """
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Sirje")
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Anu")
    factories.DepartmentHeadFactory(display_name="Mihkel")

    names = list(department_workers().values_list("display_name", flat=True))
    assert names == sorted(names)
    assert names == ["Anu", "Mihkel", "Sirje"]


# =========================================================================
# J. the predicate and the queryset are one rule
# =========================================================================


def test_the_predicate_agrees_with_the_queryset_for_every_case():
    """Parity over the whole matrix, in one place.

    Every account shape the rule has an opinion about is created at once and
    both halves are asked about all of them. A future edit that narrows only
    the queryset — or only the predicate — fails here rather than in whichever
    half of some flow happens to be exercised.
    """
    population = [
        factories.UserFactory(role=UserRole.SPECIALIST, display_name="Aktiivne spetsialist"),
        factories.DepartmentHeadFactory(display_name="Aktiivne juht"),
        factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Administraator"),
        factories.ReaderFactory(display_name="Lugeja"),
        factories.UserFactory(
            role=UserRole.SPECIALIST, is_staff=True, display_name="Personalikonto"
        ),
        factories.UserFactory(
            role=UserRole.DEPARTMENT_HEAD, is_superuser=True, display_name="Juurkasutaja"
        ),
        _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Lahkunud")),
        _inactive(factories.DepartmentHeadFactory(display_name="Lahkunud juht")),
    ]

    offered = set(department_workers())
    for person in population:
        assert is_department_worker(person) is (person in offered), (
            f"{person.display_name}: the predicate and the queryset disagree"
        )


def test_the_predicate_refuses_things_that_are_not_a_user():
    """Nothing without a primary key is somebody work can be given to.

    `DEPARTMENT_VIEWER` is the sentinel the shared gate uses for *no persona
    selected*. It reads the register and is not a person, so it cannot become
    one and cannot be handed a file. `AnonymousUser` is the same answer for the
    same reason.

    Note what is deliberately *not* asserted: an unsaved `User` carries a
    primary key already, because the model generates its UUID in Python rather
    than in the database. The predicate is asked about objects that came out of
    a queryset or a form field, and pretending it screens for unsaved instances
    would be asserting something the model does not do.
    """
    from django.contrib.auth.models import AnonymousUser

    from app.core.authorization import DEPARTMENT_VIEWER

    assert not is_department_worker(None)
    assert not is_department_worker(DEPARTMENT_VIEWER)
    assert not is_department_worker(AnonymousUser())


# =========================================================================
# The two consumers are the one rule
# =========================================================================


def test_assignment_and_the_base_rule_are_the_same_population():
    """Not "agree today" — the same rows, asserted against a mixed population.

    `assignable_business_users` is a name the assignment surfaces read, so that
    what they mean is legible where they call it. It must not become a second
    definition.
    """
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Spetsialist")
    factories.DepartmentHeadFactory(display_name="Juht")
    factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Administraator")
    _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Lahkunud"))

    assert list(assignable_business_users()) == list(department_workers())


def test_the_assignment_predicate_is_the_base_predicate():
    admin = factories.UserFactory(role=UserRole.ADMINISTRATOR)
    person = factories.UserFactory(role=UserRole.SPECIALIST)

    assert is_assignable_business_user(person) is is_department_worker(person) is True
    assert is_assignable_business_user(admin) is is_department_worker(admin) is False


def test_the_persona_list_is_the_same_population_as_the_assignment_list():
    """The defect this branch exists to close, asserted as one line.

    Before docs/adr/0036 the persona list was narrowed and the ownership
    controls were not, so an account that had just been refused as a persona
    was still one dropdown away from owning a file.
    """
    from app.accounts.selectors import persona_candidates

    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Spetsialist")
    factories.UserFactory(role=UserRole.ADMINISTRATOR, is_staff=True, display_name="Administraator")
    factories.ReaderFactory(display_name="Lugeja")

    assert list(persona_candidates()) == list(assignable_business_users())


# =========================================================================
# assignable_including — the current workers, plus what a record already names
# =========================================================================


def test_including_nobody_is_the_plain_assignable_list():
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Spetsialist")
    factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Administraator")

    assert list(assignable_including(None)) == list(assignable_business_users())
    assert list(assignable_including()) == list(assignable_business_users())


def test_a_bound_value_that_is_no_longer_assignable_is_kept():
    """The record's own owner survives, so an unrelated edit can be saved."""
    current = factories.UserFactory(role=UserRole.SPECIALIST, display_name="Praegune")
    former = _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Endine"))

    offered = list(assignable_including(former))
    assert former in offered
    assert current in offered


def test_a_bound_value_does_not_admit_anybody_else():
    """Only this record's person, never "anybody inactive".

    The sharp case: keeping the bound value must not widen the population to
    every account that shares its reason for exclusion. A second departed
    colleague is still refused, which is what stops the union from becoming a
    way to assign new work to somebody who has left.
    """
    named = _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Nimetatud"))
    other_former = _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Teine"))
    admin = factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Administraator")

    offered = list(assignable_including(named))
    assert named in offered
    assert other_former not in offered
    assert admin not in offered


def test_a_bound_value_that_is_still_assignable_appears_exactly_once():
    """The union is a set, not a concatenation.

    A duplicated row would render the same colleague twice in the select and
    would make `count()` wrong for anything reading the population.
    """
    person = factories.UserFactory(role=UserRole.SPECIALIST, display_name="Praegune")
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Kolleeg")

    offered = list(assignable_including(person))
    assert offered.count(person) == 1
    assert len(offered) == len(set(offered))


def test_several_bound_values_are_all_kept():
    first = _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Esimene"))
    second = factories.UserFactory(role=UserRole.ADMINISTRATOR, display_name="Teine")

    offered = list(assignable_including(first, second))
    assert first in offered
    assert second in offered


def test_the_union_is_ordered_by_display_name_like_every_other_person_list():
    """A preserved historical owner sorts among the colleagues, not beside them.

    The list is read by somebody looking for a name; a value appended at the
    end because of how it got there would be the control explaining its own
    implementation.
    """
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Sirje")
    factories.UserFactory(role=UserRole.SPECIALIST, display_name="Anu")
    former = _inactive(factories.UserFactory(role=UserRole.SPECIALIST, display_name="Mihkel"))

    names = list(assignable_including(former).values_list("display_name", flat=True))
    assert names == ["Anu", "Mihkel", "Sirje"]


def test_the_rule_reads_a_role_and_never_a_person():
    """A grep-shaped assertion over the module that decides eligibility.

    The thing being prevented is a *future* edit: somebody adding
    `exclude(upn="…")` to keep one account out, which works that afternoon and
    is wrong the following month. `test_persona_candidates.py` asserts the same
    property; it is repeated here because this suite is the one somebody
    changing the assignment rule opens.
    """
    import ast
    import inspect

    from app.accounts import selectors

    tree = ast.parse(inspect.getsource(selectors))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        body = [
            statement
            for statement in node.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        ]
        code = ast.unparse(ast.Module(body=body, type_ignores=[]))
        # `display_name` is allowed inside `order_by`, which is presentation.
        # It is forbidden as a *filter*, which would be a rule about a person.
        for forbidden in ("upn", "email", "entra_object_id", "display_name=", "display_name__"):
            assert forbidden not in code, (
                f"{node.name}() reads {forbidden}, which identifies a person rather than a role"
            )
