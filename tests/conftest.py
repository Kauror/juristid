from __future__ import annotations

import pytest

from tests import factories


@pytest.fixture
def specialist(db):
    return factories.UserFactory()


@pytest.fixture
def other_specialist(db):
    return factories.UserFactory()


@pytest.fixture
def department_head(db):
    return factories.DepartmentHeadFactory()


@pytest.fixture
def administrator(db):
    return factories.AdministratorFactory()


@pytest.fixture
def superuser(db):
    return factories.AdministratorFactory(is_superuser=True)


@pytest.fixture
def normal_matter(db, specialist):
    return factories.MatterFactory(owner=specialist)


@pytest.fixture
def restricted_matter(db, specialist):
    from app.core.enums import Visibility

    return factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
