"""`/admin/login/` checks no password where the application has a sign-in of its own.

Django's admin mounts its own `LoginView`: an address and a password, handed to
`ModelBackend`, no throttle, no second factor. Past the shared gate that was a
way to sign in as a staff account that is never a persona; under personal
sign-in it was a password oracle for every `is_staff` account and a way round
the second factor ADR 0145 §8 makes mandatory for them (AUD-051 AU-2).

What is pinned here:

* under the shared gate and under `local_password`, a credential POST to the
  page reaches no authentication backend and changes no session;
* signed out, the page sends the visitor to the mode's own sign-in;
* a staff account that signed in properly — `/konto/sisene/`, password and
  code — still reaches the admin;
* under `AUTH_MODE=none` (a laptop, CI) Django's page is unchanged.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth.backends import ModelBackend
from django.urls import reverse

from app.accounts.enums import UserRole
from app.accounts.models import User
from tests import factories
from tests.gate import PASSWORD as GATE_PASSWORD
from tests.gate import apply_shared_gate
from tests.local_auth import PASSWORD, apply_local_password, person, sign_in

pytestmark = pytest.mark.django_db

ADMIN_LOGIN = "/admin/login/"
STAFF_PASSWORD = "tehniline haldur teab seda parooli"  # noqa: S105 - test database only


def _staff() -> User:
    user = factories.UserFactory(
        upn="tehniline.haldur@koda.ee",
        role=UserRole.ADMINISTRATOR,
        is_staff=True,
        is_superuser=True,
        is_synthetic=False,
    )
    user.set_password(STAFF_PASSWORD)
    user.save()
    return user


def _credentials(user: User) -> dict[str, str]:
    return {"username": user.upn, "password": STAFF_PASSWORD, "next": "/admin/"}


@pytest.fixture
def no_backend():
    """Any authentication attempt at all, counted."""
    with mock.patch.object(
        ModelBackend, "authenticate", autospec=True, return_value=None
    ) as authenticate:
        yield authenticate


# -- the shared gate ------------------------------------------------------------------


class TestPastTheSharedGate:
    @pytest.fixture(autouse=True)
    def gate(self, settings):
        return apply_shared_gate(settings)

    @pytest.fixture
    def behind_the_gate(self, client):
        response = client.post(reverse("accounts:shared_gate"), {"password": GATE_PASSWORD})
        assert response.status_code == 302
        return client

    def test_a_staff_password_posted_to_the_admin_is_never_checked(
        self, behind_the_gate, no_backend
    ):
        staff = _staff()
        before = dict(behind_the_gate.session)

        response = behind_the_gate.post(ADMIN_LOGIN, _credentials(staff))

        assert no_backend.call_count == 0
        assert "_auth_user_id" not in behind_the_gate.session
        assert dict(behind_the_gate.session) == before
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:choose_persona"))

    def test_the_admin_has_no_browser_entrance_behind_the_gate(self, behind_the_gate):
        """The persona list offers no staff account, so nothing past it opens the admin."""
        _staff()

        response = behind_the_gate.get("/admin/", follow=True)

        assert response.redirect_chain[0][0].startswith(ADMIN_LOGIN)
        assert response.request["PATH_INFO"] == reverse("accounts:choose_persona")
        assert "_auth_user_id" not in behind_the_gate.session


# -- personal sign-in -----------------------------------------------------------------


class TestUnderPersonalSignIn:
    @pytest.fixture(autouse=True)
    def local(self, settings):
        return apply_local_password(settings)

    def test_a_signed_in_colleague_cannot_try_a_staff_password(self, client, no_backend):
        """The oracle the finding names: signed in as oneself, guessing somebody else's."""
        colleague, _ = person()
        assert sign_in(client, colleague).status_code == 302
        staff = _staff()
        no_backend.reset_mock()

        response = client.post(ADMIN_LOGIN, _credentials(staff))

        assert no_backend.call_count == 0
        assert client.session["_auth_user_id"] == str(colleague.pk)
        assert response.status_code == 302
        assert response["Location"] == reverse("core:home")

    def test_signed_out_the_admin_sends_to_personal_sign_in(self, client, no_backend):
        _staff()

        response = client.post(ADMIN_LOGIN, {"username": "x", "password": "y"})

        assert no_backend.call_count == 0
        assert "_auth_user_id" not in client.session
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:sign_in"))

    def test_a_staff_account_reaches_the_admin_through_password_and_code(self, client):
        staff, secret = person(
            role=UserRole.ADMINISTRATOR, is_staff=True, is_superuser=True, totp=True
        )
        response = sign_in(client, staff, password=PASSWORD, secret=secret)
        assert response.status_code == 302

        assert client.get("/admin/").status_code == 200
        login_page = client.get(ADMIN_LOGIN + "?next=/admin/accounts/user/")
        assert login_page.status_code == 302
        assert login_page["Location"] == "/admin/accounts/user/"

    def test_an_external_next_is_not_followed(self, client):
        staff, secret = person(
            role=UserRole.ADMINISTRATOR, is_staff=True, is_superuser=True, totp=True
        )
        sign_in(client, staff, password=PASSWORD, secret=secret)

        response = client.get(ADMIN_LOGIN + "?next=https://elsewhere.test/")

        assert response["Location"] == reverse("admin:index")


# -- development ----------------------------------------------------------------------


def test_without_an_authentication_mode_django_s_own_page_still_signs_in(client, settings):
    """`AUTH_MODE=none` has no other sign-in for a technical account, and no real data."""
    settings.AUTH_MODE = "none"
    staff = _staff()

    assert client.get(ADMIN_LOGIN).status_code == 200
    response = client.post(ADMIN_LOGIN, _credentials(staff))

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(staff.pk)
