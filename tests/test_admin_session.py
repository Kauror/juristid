"""The verified administrator session under the shared gate (docs/adr/0186).

The step-up through the real views, and every way it must end or refuse: the
same sentence for every wrong answer, the throttle, enrolment for an account
without an authenticator, idle and absolute expiry, the security epoch (a power
or credential change), a switched-off account, the gate closing, «Lõpeta halduri
seanss» — and what it must never do: change the persona, open business content,
or survive into a session that is not the one it was proved in.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import admin_session, administration, capabilities, mfa
from app.accounts.admin_session import VerifiedActor
from app.accounts.enums import Capability, UserRole
from app.accounts.models import TotpDevice, User
from app.audit.enums import AuthAssurance, AuthMethod, SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.admin_session import (
    apply_admin_gate,
    choose,
    full_admin,
    pass_gate,
    step_up,
    technical_admin,
    verified,
)
from tests.local_auth import OTHER_PASSWORD, PASSWORD, code, person
from tests.refusals import refused

pytestmark = pytest.mark.django_db

INDEX = "haldus:index"
LIST = "account_admin:list"


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _events(kind, **filters):
    return SecurityAuditEvent.objects.filter(event_type=kind, **filters)


def _age(client, **changes):
    """Move the administrator session's own stamps into the past."""
    session = client.session
    state = dict(session[admin_session.SESSION_STATE])
    for key, delta in changes.items():
        state[key] = (timezone.now() - delta).isoformat()
    session[admin_session.SESSION_STATE] = state
    session.save()


# -- the step-up itself ----------------------------------------------------------------


def test_password_and_code_open_the_administrator_session_without_a_persona(client):
    admin, secret = technical_admin()
    verified(client, admin, secret)

    page = client.get(reverse(LIST))

    assert page.status_code == 200
    # Not a sign-in to the application: nobody is `request.user`.
    assert not page.wsgi_request.user.is_authenticated
    started = _events(SecurityEventType.ADMINISTRATOR_SESSION_STARTED, actor=admin).get()
    assert started.auth_method == AuthMethod.ADMINISTRATOR_SESSION
    assert started.auth_assurance == AuthAssurance.INDIVIDUAL_MFA
    assert started.authenticated_principal_id == admin.pk
    assert started.acting_persona_id is None
    assert started.detail["path"] == "administrator_session"


def test_the_step_up_is_behind_the_gate(client):
    response = client.get(reverse("haldus:sign_in"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:shared_gate"))


@pytest.mark.parametrize("mode", ["none", "cloudflare_access"])
def test_the_step_up_does_not_exist_outside_the_shared_gate(client, settings, mode):
    settings.AUTH_MODE = mode
    settings.CF_ACCESS_TEAM_DOMAIN = "koda.cloudflareaccess.com"
    settings.CF_ACCESS_AUDIENCE = "aud"
    client.force_login(factories.UserFactory())
    for name in ("haldus:sign_in", "haldus:sign_in_code", "haldus:enrol", "haldus:reauthenticate"):
        assert client.get(reverse(name)).status_code in (403, 404), name


def test_every_wrong_answer_is_the_same_sentence(client):
    admin, _ = full_admin()
    specialist, _ = person()  # a correct password, but no administrative standing
    pass_gate(client)

    wrong = client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": "vale" * 5})
    unknown = client.post(
        reverse("haldus:sign_in"), {"email": "keegi@koda.ee", "password": PASSWORD}
    )
    not_admin = client.post(
        reverse("haldus:sign_in"), {"email": specialist.upn, "password": PASSWORD}
    )

    for response in (wrong, unknown, not_admin):
        assert response.status_code == 400
        assert admin_session.SIGN_IN_REFUSED in response.content.decode()
    reasons = set(
        _events(
            SecurityEventType.AUTHENTICATION_FAILED, detail__path="administrator_session"
        ).values_list("detail__reason", flat=True)
    )
    assert reasons == {"bad_password", "unknown_account", "not_an_administrator"}
    assert admin_session.SESSION_STATE not in client.session


def test_a_correct_password_alone_opens_nothing(client):
    admin, _ = full_admin()
    pass_gate(client)

    response = client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})

    assert response["Location"] == reverse("haldus:sign_in_code")
    assert client.get(reverse(LIST)).status_code == 404
    assert client.get(reverse(INDEX)).status_code == 404


def test_a_wrong_code_is_refused_and_counted(client):
    admin, _ = full_admin()
    pass_gate(client)
    client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})

    response = client.post(reverse("haldus:sign_in_code"), {"code": "000000"})

    assert response.status_code == 400
    assert admin_session.SESSION_STATE not in client.session
    assert _events(
        SecurityEventType.AUTHENTICATION_FAILED, detail__reason="bad_code", subject_id=admin.pk
    ).exists()


def test_password_guesses_lock_out_like_the_personal_sign_in(client):
    admin, _ = full_admin()
    pass_gate(client)
    statuses = [
        client.post(
            reverse("haldus:sign_in"), {"email": admin.upn, "password": f"vale-{n}" * 3}
        ).status_code
        for n in range(12)
    ]
    assert 429 in statuses
    # Locked: even the right password is not checked now.
    response = client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})
    assert response.status_code == 429


def test_a_pending_password_ends_if_the_credentials_move(client):
    admin, secret = full_admin()
    pass_gate(client)
    client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})
    User.objects.filter(pk=admin.pk).update(security_epoch=admin.security_epoch + 1)

    response = client.post(reverse("haldus:sign_in_code"), {"code": code(secret, admin)})

    assert response["Location"] == reverse("haldus:sign_in")
    assert admin_session.SESSION_STATE not in client.session


def test_a_pending_password_expires(client):
    admin, secret = full_admin()
    pass_gate(client)
    client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})
    session = client.session
    pending = dict(session[admin_session.SESSION_PENDING])
    pending["at"] = (
        timezone.now() - timedelta(seconds=admin_session.PENDING_SECONDS + 5)
    ).isoformat()
    session[admin_session.SESSION_PENDING] = pending
    session.save()

    response = client.post(reverse("haldus:sign_in_code"), {"code": code(secret, admin)})

    assert response["Location"] == reverse("haldus:sign_in")


# -- enrolment for an administrator without an authenticator ------------------------------


def test_an_administrator_without_an_authenticator_must_enrol_first(client):
    admin, _ = person(manage=True, delegate=True)
    pass_gate(client)

    response = client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})
    assert response["Location"] == reverse("haldus:enrol")
    page = client.get(reverse("haldus:enrol"))
    assert page.status_code == 200
    assert client.get(reverse(LIST)).status_code == 404  # nothing open yet

    sealed = client.session[admin_session.SESSION_SEALED_ENROLMENT]
    enrolment = mfa.resume_enrolment(admin, sealed)
    import pyotp

    confirmed = client.post(reverse("haldus:enrol"), {"code": pyotp.TOTP(enrolment.secret).now()})

    assert confirmed.status_code == 200
    assert "Taastekoodid" in confirmed.content.decode()
    assert TotpDevice.objects.filter(user=admin, confirmed_at__isnull=False).exists()
    assert client.get(reverse(LIST)).status_code == 200
    assert _events(SecurityEventType.MFA_ENROLLED, subject_id=admin.pk).exists()
    assert sealed not in str(dict(client.session))  # the sealed secret left the session


def test_an_account_with_an_authenticator_is_never_offered_enrolment(client):
    admin, _ = full_admin()
    pass_gate(client)
    client.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})

    assert client.get(reverse("haldus:enrol"))["Location"] == reverse("haldus:sign_in")


def test_enrolment_needs_the_password_first(client):
    pass_gate(client)
    assert client.get(reverse("haldus:enrol"))["Location"] == reverse("haldus:sign_in")


# -- how it ends -------------------------------------------------------------------------


def test_the_idle_limit_ends_it(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    _age(client, last_seen=timedelta(seconds=901))

    assert client.get(reverse(LIST)).status_code == 404
    assert _events(
        SecurityEventType.ADMINISTRATOR_SESSION_ENDED, detail__reason="idle_timeout"
    ).exists()
    assert admin_session.SESSION_STATE not in client.session


def test_the_absolute_limit_ends_it_however_busy(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    _age(client, verified_at=timedelta(seconds=7201))

    assert client.get(reverse(LIST)).status_code == 404
    assert _events(
        SecurityEventType.ADMINISTRATOR_SESSION_ENDED, detail__reason="absolute_timeout"
    ).exists()


def test_activity_keeps_it_inside_the_absolute_limit(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    _age(client, last_seen=timedelta(seconds=600))

    assert client.get(reverse(LIST)).status_code == 200


def test_unusable_limits_fall_back_to_safe_defaults(settings):
    settings.ADMIN_SESSION_IDLE_SECONDS = "nonsense"
    settings.ADMIN_SESSION_ABSOLUTE_SECONDS = 10**9
    settings.ADMIN_SESSION_REAUTH_SECONDS = 0
    assert admin_session.idle_seconds() == admin_session.DEFAULT_IDLE_SECONDS
    assert admin_session.absolute_seconds() == admin_session.DEFAULT_ABSOLUTE_SECONDS
    assert admin_session.reauth_seconds() == admin_session.DEFAULT_REAUTH_SECONDS
    settings.ADMIN_SESSION_IDLE_SECONDS = 40000
    settings.ADMIN_SESSION_ABSOLUTE_SECONDS = 3600
    assert admin_session.idle_seconds() == 3600  # never longer than the absolute limit


def test_a_capability_withdrawn_mid_session_ends_it(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    other, _ = full_admin()
    third, _ = full_admin()  # continuity: three usable, so the change is allowed
    administration.update_account(
        actor=admin_session._mint(other, via=admin_session.PATH),
        user=admin,
        display_name=admin.display_name,
        role=admin.role,
        wanted={Capability.MANAGE_ACCOUNTS.value: False},
    )

    assert client.get(reverse(LIST)).status_code == 404
    assert _events(
        SecurityEventType.ADMINISTRATOR_SESSION_ENDED, detail__reason="credentials_changed"
    ).exists()
    del third


def _local_actor(user):
    """A VerifiedActor minted the way a local_password request would mint one."""
    return admin_session._mint(user, via="local_password")


def test_a_password_change_ends_it(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    admin.set_password(OTHER_PASSWORD)
    admin.save(update_fields=["password"])

    assert client.get(reverse(LIST)).status_code == 404


def test_a_switched_off_administrator_loses_it(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    User.objects.filter(pk=admin.pk).update(is_active=False)

    assert client.get(reverse(LIST)).status_code == 404
    # And cannot verify again.
    other = Client()
    pass_gate(other)
    response = other.post(reverse("haldus:sign_in"), {"email": admin.upn, "password": PASSWORD})
    assert response.status_code == 400


def test_signing_out_ends_it_with_everything_else(client):
    admin, secret = full_admin()
    verified(client, admin, secret)

    client.post(reverse("accounts:sign_out"))

    assert _events(
        SecurityEventType.ADMINISTRATOR_SESSION_ENDED, detail__reason="signed_out"
    ).exists()
    pass_gate(client)
    assert client.get(reverse(LIST)).status_code == 404


def test_the_gate_expiring_ends_it(client, settings):
    admin, secret = full_admin()
    verified(client, admin, secret)
    session = client.session
    session["shared_gate:passed_at"] = (
        timezone.now() - timedelta(seconds=settings.SHARED_GATE_SESSION_SECONDS + 1)
    ).isoformat()
    session.save()

    assert client.get(reverse(LIST))["Location"].startswith(reverse("accounts:shared_gate"))
    assert _events(
        SecurityEventType.ADMINISTRATOR_SESSION_ENDED, detail__reason="gate_closed"
    ).exists()
    pass_gate(client)
    assert client.get(reverse(LIST)).status_code == 404


def test_rotating_the_shared_password_ends_it(client, settings):
    admin, secret = full_admin()
    verified(client, admin, secret)
    settings.SHARED_GATE_PASSWORD = "uus-jagatud-parool-mida-keegi-ei-tea"  # noqa: S105

    client.get(reverse(LIST))

    assert _events(
        SecurityEventType.ADMINISTRATOR_SESSION_ENDED, detail__reason="shared_password_changed"
    ).exists()


def test_ending_it_keeps_the_gate_and_the_persona(client):
    admin, secret = full_admin()
    persona = factories.UserFactory(role=UserRole.SPECIALIST)
    verified(client, admin, secret)
    choose(client, persona)

    response = client.post(reverse("haldus:sign_out"))

    assert response.status_code == 302
    assert client.get(reverse(LIST)).status_code == 404
    page = client.get(reverse("matters:my_work"))
    assert page.status_code == 200 and page.wsgi_request.user == persona


def test_a_copied_state_from_another_gate_pass_is_worthless(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    state = client.session[admin_session.SESSION_STATE]

    thief = Client()
    pass_gate(thief)
    session = thief.session
    session[admin_session.SESSION_STATE] = state
    session.save()

    assert thief.get(reverse(LIST)).status_code == 404


# -- the persona is a lens, the administrator a principal --------------------------------


def test_switching_persona_keeps_the_administrator_and_the_actor(client):
    """Defined: a persona change neither ends nor changes the verified administrator."""
    head, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    other_persona = factories.UserFactory(role=UserRole.SPECIALIST)
    target = factories.UserFactory(role=UserRole.SPECIALIST)
    pass_gate(client)
    choose(client, head)
    step_up(client, head, secret)
    choose(client, other_persona)  # somebody else's work on screen now

    assert client.get(reverse(LIST)).status_code == 200
    from app.accounts.forms import field_name

    response = client.post(
        reverse("account_admin:detail", kwargs={"pk": target.pk}),
        {"display_name": "Uus Nimi", "role": target.role, field_name("work.assign"): "on"},
    )

    assert response.status_code == 302
    row = _events(SecurityEventType.ACCOUNT_UPDATED, subject_id=target.pk).get()
    assert row.actor_id == head.pk  # the verified principal, never the persona
    assert row.authenticated_principal_id == head.pk
    assert row.acting_persona_id == other_persona.pk
    assert row.auth_method == AuthMethod.ADMINISTRATOR_SESSION

    # «Ilma kasutajata» too: the persona goes, the administrator stays.
    client.post(reverse("accounts:act_as"), {"user_id": ""})
    assert client.get(reverse(LIST)).status_code == 200


def test_choosing_the_head_persona_is_not_verification(client):
    head = factories.DepartmentHeadFactory(
        capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"}
    )
    target = factories.UserFactory()
    pass_gate(client)
    choose(client, head)

    assert client.get(reverse(LIST)).status_code == 404
    assert (
        client.post(reverse("account_admin:deactivate", kwargs={"pk": target.pk})).status_code
        == 404
    )
    target.refresh_from_db()
    assert target.is_active


def test_a_technical_administrator_reads_no_business_content(client):
    admin, secret = technical_admin()
    matter = factories.MatterFactory(title="ARIASI-SALAJANE-4417")
    verified(client, admin, secret)

    assert client.get(reverse(INDEX)).status_code == 200
    assert client.get(reverse("haldus:susteem")).status_code == 200
    for name, kwargs in (
        ("matters:matter_list", {}),
        ("matters:matter_detail", {"pk": matter.pk}),
        ("matters:department", {}),
        ("search:search", {}),
    ):
        response = client.get(reverse(name, kwargs=kwargs))
        assert response.status_code == 302, name
        assert response["Location"].startswith(reverse("accounts:choose_persona")), name
        assert "ARIASI-SALAJANE-4417" not in response.content.decode()


# -- the second lock: what the services accept --------------------------------------------


def test_a_verified_actor_cannot_be_made_by_hand():
    admin, _ = full_admin()
    with pytest.raises(TypeError):
        VerifiedActor(user=admin, via="administrator_session", epoch=0)
    with pytest.raises(TypeError):
        VerifiedActor(user=admin, via="administrator_session", epoch=0, seal=object())


def test_under_the_gate_a_bare_account_administers_nothing():
    admin, _ = full_admin()
    with refused(administration.NOT_VERIFIED):
        administration.create_account(
            actor=admin, email="x@koda.ee", display_name="X", role="SPECIALIST"
        )
    assert not User.objects.filter(upn="x@koda.ee").exists()


def test_a_local_actor_is_refused_under_the_gate():
    admin, _ = full_admin()
    with refused(administration.NOT_VERIFIED):
        administration.create_account(
            actor=_local_actor(admin), email="x@koda.ee", display_name="X", role="SPECIALIST"
        )


def test_an_actor_whose_epoch_moved_is_refused_under_the_lock():
    admin, _ = full_admin()
    actor = admin_session._mint(admin, via=admin_session.PATH)
    User.objects.filter(pk=admin.pk).update(security_epoch=admin.security_epoch + 1)

    with refused(administration.STALE_PROOF):
        administration.create_account(
            actor=actor, email="x@koda.ee", display_name="X", role="SPECIALIST"
        )


def test_the_view_hands_the_services_the_verified_principal(client):
    admin, secret = full_admin()
    persona = factories.UserFactory(role=UserRole.SPECIALIST)
    pass_gate(client)
    choose(client, persona)
    step_up(client, admin, secret)

    response = client.post(
        reverse("account_admin:create"),
        {"display_name": "Uus Kolleeg", "email": "uus.kolleeg@koda.ee", "role": "SPECIALIST"},
    )

    assert response.status_code == 302
    created = User.objects.get(upn="uus.kolleeg@koda.ee")
    assert created.created_by_id == admin.pk
    row = _events(SecurityEventType.ACCOUNT_CREATED, subject_id=created.pk).get()
    assert row.actor_id == admin.pk and row.acting_persona_id == persona.pk


# -- fresh proof before a change -----------------------------------------------------------


def test_a_change_after_the_window_asks_for_password_and_code(client):
    admin, secret = full_admin()
    target = factories.UserFactory()
    verified(client, admin, secret)
    _age(client, strong_at=timedelta(seconds=601))

    response = client.post(reverse("account_admin:deactivate", kwargs={"pk": target.pk}))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("haldus:reauthenticate"))
    target.refresh_from_db()
    assert target.is_active

    refused_proof = client.post(
        reverse("haldus:reauthenticate"), {"password": PASSWORD, "code": ""}
    )
    assert refused_proof.status_code == 400  # the code is not optional here
    accepted = client.post(
        reverse("haldus:reauthenticate"),
        {"password": PASSWORD, "code": code(secret, admin), "next": reverse(LIST)},
    )
    assert accepted["Location"] == reverse(LIST)
    assert _events(
        SecurityEventType.REAUTHENTICATED, actor=admin, detail__path="administrator_session"
    ).exists()


def test_an_unauthorized_direct_post_changes_nothing(client):
    """A crafted POST from a gate session without the step-up: 404, nothing written."""
    target = factories.UserFactory()
    pass_gate(client)
    choose(client, factories.DepartmentHeadFactory())
    before = SecurityAuditEvent.objects.filter(subject_id=target.pk).count()

    for route in (
        "account_admin:detail",
        "account_admin:deactivate",
        "account_admin:reset_second_factor",
        "account_admin:change_email",
    ):
        response = client.post(
            reverse(route, kwargs={"pk": target.pk}),
            {"display_name": "X", "role": "READER", "email": "x@koda.ee"},
        )
        assert response.status_code == 404, route
    target.refresh_from_db()
    assert target.is_active and target.role == UserRole.SPECIALIST
    assert SecurityAuditEvent.objects.filter(subject_id=target.pk).count() == before


def test_nobody_grants_themselves_anything_through_the_session(client):
    manager, secret = person(manage=True, totp=True)  # manages, may not delegate
    verified(client, manager, secret)
    from app.accounts.forms import field_name

    response = client.post(
        reverse("account_admin:detail", kwargs={"pk": manager.pk}),
        {
            "display_name": manager.display_name,
            "role": manager.role,
            field_name(Capability.MANAGE_ACCOUNTS.value): "on",
            field_name(Capability.DELEGATE_ADMINISTRATION.value): "on",
        },
    )

    assert response.status_code in (302, 400)
    manager.refresh_from_db()
    assert capabilities.clean_overrides(manager.capability_overrides) == {
        Capability.MANAGE_ACCOUNTS.value: capabilities.ALLOW
    }


# -- security review round (P2, P3a, P3c, P3d) --------------------------------------------


def test_business_pages_do_not_keep_the_administrator_session_alive(client):
    admin, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    pass_gate(client)
    choose(client, admin)
    step_up(client, admin, secret)
    _age(client, last_seen=timedelta(seconds=600))
    before = client.session[admin_session.SESSION_STATE]["last_seen"]

    for name in ("matters:matter_list", "matters:my_work", "matters:department"):
        assert client.get(reverse(name)).status_code == 200
    assert client.session[admin_session.SESSION_STATE]["last_seen"] == before

    _age(client, last_seen=timedelta(seconds=901))
    client.get(reverse("matters:matter_list"))
    assert client.get(reverse(LIST)).status_code == 404  # idled out despite the browsing


def test_a_haldus_view_keeps_it_alive(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    _age(client, last_seen=timedelta(seconds=600))
    before = client.session[admin_session.SESSION_STATE]["last_seen"]

    assert client.get(reverse(LIST)).status_code == 200

    assert client.session[admin_session.SESSION_STATE]["last_seen"] != before
    _age(client, last_seen=timedelta(seconds=400))
    assert client.get(reverse(INDEX)).status_code == 200


def test_a_verified_actor_cannot_be_transplanted():
    import copy
    import dataclasses

    admin, _ = full_admin()
    victim, _ = full_admin()
    actor = admin_session._mint(admin, via=admin_session.PATH)
    assert admin_session.is_verified_actor(actor)

    with pytest.raises(TypeError):
        dataclasses.replace(actor, user=victim)
    clone = copy.copy(actor)
    assert not admin_session.is_verified_actor(clone)
    with refused(administration.NOT_VERIFIED):
        administration.create_account(
            actor=clone, email="x@koda.ee", display_name="X", role="SPECIALIST"
        )


def test_reauthentication_survives_a_password_hash_upgrade(client):
    from django.contrib.auth.hashers import make_password

    admin, secret = full_admin()
    verified(client, admin, secret)
    # A hash under an older algorithm, which `check_password` upgrades and
    # saves — moving the credential stamp the session is bound to. The session
    # is first re-bound to that hash, as if it had been opened under it.
    User.objects.filter(pk=admin.pk).update(
        password=make_password(PASSWORD, hasher="pbkdf2_sha256")
    )
    session = client.session
    state = dict(session[admin_session.SESSION_STATE])
    state["binding"] = admin_session.binding(User.objects.get(pk=admin.pk))
    session[admin_session.SESSION_STATE] = state
    session.save()
    _age(client, strong_at=timedelta(seconds=601))

    accepted = client.post(
        reverse("haldus:reauthenticate"),
        {"password": PASSWORD, "code": code(secret, admin), "next": reverse(LIST)},
    )

    assert accepted["Location"] == reverse(LIST)
    assert not User.objects.get(pk=admin.pk).password.startswith("pbkdf2_sha256$")
    assert client.get(reverse(LIST)).status_code == 200


def test_the_link_holder_is_asked_again_inside_the_transaction():
    from app.accounts import credentials, tokens
    from app.accounts.enums import CredentialTokenPurpose

    holder, _ = person(manage=True, delegate=True, password=None)
    issued = tokens.issue(user=holder, purpose=CredentialTokenPurpose.ACTIVATION)
    proof = tokens.proof_of(issued.token)

    result = credentials.set_password_with_link(
        proof=proof,
        purpose=CredentialTokenPurpose.ACTIVATION,
        password=PASSWORD,
        holder_may=lambda user: False,
    )

    assert result is None
    holder.refresh_from_db()
    assert not holder.has_local_password
    assert tokens.is_live(issued.record)  # the link was not spent
