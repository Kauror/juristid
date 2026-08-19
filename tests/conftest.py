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


@pytest.fixture
def organisation(db):
    return factories.OrganisationFactory()


@pytest.fixture
def stage(db):
    return factories.StageFactory()


@pytest.fixture
def signed_in(client, specialist):
    """A client signed in as an ordinary specialist."""
    client.force_login(specialist)
    return client


@pytest.fixture
def pdf_bytes():
    """The smallest thing that passes the upload signature check."""
    return b"%PDF-1.4 synthetic evidence"
