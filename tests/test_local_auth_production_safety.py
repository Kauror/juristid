"""The release boundary: personal sign-in ships dormant (docs/adr/0145 §13).

The most important property of this feature is what it does **not** do to the
deployment that runs today. Production is `AUTH_MODE=shared_gate`; nothing in
this change may alter that mode's behaviour, open a new door beside it, send a
message, or turn an existing account into something it was not. Each test here
names one of those promises and checks it on the code and settings that ship.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.checks import run_checks
from django.urls import reverse

from app.accounts import local_auth, shared_gate
from app.accounts.enums import AuthMode, Capability, ProvisioningState, UserRole
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.gate import PASSWORD as GATE_PASSWORD
from tests.gate import apply_shared_gate
from tests.local_auth import apply_local_password, person

ROOT = Path(django_settings.BASE_DIR)

#: Every route the local mode adds, with its kwargs. The private ones too: a
#: dormant mode has no pages at all, not merely no public ones.
LOCAL_ROUTES = [
    ("accounts:sign_in", {}),
    ("accounts:sign_in_second_factor", {}),
    ("accounts:activate", {}),
    ("accounts:forgot_password", {}),
    ("accounts:reset_password", {}),
    ("accounts:change_password", {}),
    ("accounts:profile", {}),
    ("accounts:security", {}),
    ("accounts:security_enrol", {}),
    ("accounts:security_recovery_codes", {}),
    ("accounts:security_remove", {}),
    ("accounts:reauthenticate", {}),
]

ADMIN_ROUTES = [
    ("account_admin:list", False),
    ("account_admin:create", False),
    ("account_admin:detail", True),
    ("account_admin:send_link", True),
    ("account_admin:cancel_invitation", True),
    ("account_admin:deactivate", True),
    ("account_admin:reactivate", True),
    ("account_admin:reset_second_factor", True),
    ("account_admin:change_email", True),
]


def _ids(**overrides):
    return {message.id for message in run_checks(include_deployment_checks=False)}


# -- the settings that ship ------------------------------------------------------------------


def _settings_source() -> str:
    return (ROOT / "config" / "settings.py").read_text(encoding="utf-8")


def test_the_default_mode_is_unchanged():
    assert 'AUTH_MODE = env("AUTH_MODE", "none")' in _settings_source()


def test_the_production_templates_still_name_the_shared_gate():
    for template in (
        ROOT / "deploy" / "unraid-main" / ".env.example",
        ROOT / "deploy" / "recovery-rehearsal" / "real-data.env.example",
    ):
        text = template.read_text(encoding="utf-8")
        assert re.search(r"^AUTH_MODE=shared_gate$", text, re.M), template
        assert not re.search(r"^AUTH_MODE=local_password", text, re.M), template
        assert not re.search(r"^ACCOUNT_EMAIL_DELIVERY_ENABLED=1", text, re.M), template


def test_no_tracked_deployment_file_switches_the_mode_on():
    """Explained in comments, never set: a commented line switches nothing on."""
    setting = re.compile(
        r"^\s*(AUTH_MODE\s*[=:]\s*['\"]?local_password|ACCOUNT_EMAIL_DELIVERY_ENABLED\s*[=:]\s*['\"]?(1|true|yes|on))",
        re.I | re.M,
    )
    for path in (ROOT / "deploy").rglob("*"):
        if path.suffix in {".yml", ".yaml", ".env", ".example", ".sh"} and path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not setting.search(text), path


def test_account_mail_is_off_and_delivers_nothing_by_default():
    source = _settings_source()
    assert 'env_bool("ACCOUNT_EMAIL_DELIVERY_ENABLED", default=False)' in source
    assert 'env("DJANGO_EMAIL_BACKEND", "app.accounts.mail.DeliveryDisabledBackend")' in source


def test_the_shipped_hash_cost_is_above_owasps_floor():
    source = _settings_source()
    memory = int(re.search(r'"LOCAL_AUTH_ARGON2_MEMORY_KIB", (\d+)\)', source).group(1))
    time_cost = int(re.search(r'"LOCAL_AUTH_ARGON2_TIME_COST", (\d+)\)', source).group(1))
    assert memory >= 19456
    assert time_cost >= 2
    assert (
        django_settings.PASSWORD_HASHERS[0] == "app.accounts.passwords.JuristidArgon2PasswordHasher"
    )


# -- the shared gate, exactly as before ----------------------------------------------------------


@pytest.mark.django_db
class TestUnderTheSharedGate:
    @pytest.fixture(autouse=True)
    def gate(self, settings):
        return apply_shared_gate(settings)

    @pytest.fixture
    def behind_the_gate(self, client):
        assert (
            client.post(reverse("accounts:shared_gate"), {"password": GATE_PASSWORD}).status_code
            == 302
        )
        return client

    def test_the_gate_and_the_persona_still_work(self, behind_the_gate):
        persona = factories.UserFactory(role=UserRole.SPECIALIST)

        chosen = behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})

        assert chosen.status_code == 302
        assert behind_the_gate.get(reverse("matters:my_work")).status_code == 200
        assert SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.PERSONA_SELECTED, actor=persona
        ).exists()

    def test_choosing_a_persona_is_not_a_personal_sign_in(self, behind_the_gate):
        persona = factories.UserFactory(role=UserRole.SPECIALIST)
        behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})

        persona.refresh_from_db()
        assert persona.last_authenticated_at is None
        assert local_auth.SESSION_USER not in behind_the_gate.session

    @pytest.mark.parametrize(("route", "kwargs"), LOCAL_ROUTES)
    def test_every_personal_sign_in_page_is_absent(self, behind_the_gate, route, kwargs):
        persona = factories.DepartmentHeadFactory()
        behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})

        assert behind_the_gate.get(reverse(route, kwargs=kwargs)).status_code == 404
        assert behind_the_gate.post(reverse(route, kwargs=kwargs), {}).status_code == 404

    @pytest.mark.parametrize(("route", "takes_pk"), ADMIN_ROUTES)
    def test_an_administrator_persona_administers_nothing(self, behind_the_gate, route, takes_pk):
        """A persona that *holds* `accounts.manage` still gets a 404 — a choice is not a proof."""
        persona = factories.DepartmentHeadFactory(
            capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"}
        )
        target = factories.UserFactory()
        behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})
        url = reverse(route, kwargs={"pk": target.pk} if takes_pk else {})

        assert behind_the_gate.get(url).status_code in (404, 405)
        assert behind_the_gate.post(url, {"display_name": "X", "role": "READER"}).status_code == 404
        target.refresh_from_db()
        assert target.role == UserRole.SPECIALIST and target.is_active

    def test_the_bar_offers_no_administration(self, behind_the_gate):
        persona = factories.DepartmentHeadFactory(
            capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"}
        )
        behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})

        page = behind_the_gate.get(reverse("matters:my_work")).content.decode()

        assert reverse("account_admin:list") not in page
        assert reverse("accounts:profile") not in page

    def test_the_department_head_persona_keeps_the_management_view(self, behind_the_gate):
        factories.MatterFactory()
        head = factories.DepartmentHeadFactory()
        behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(head.pk)})

        assert "uxstat" in behind_the_gate.get(reverse("matters:department")).content.decode()

    def test_signing_out_still_returns_to_the_start(self, behind_the_gate):
        response = behind_the_gate.post(reverse("accounts:sign_out"))
        assert response["Location"] == reverse("core:home")

    def test_the_gate_still_hashes_with_pbkdf2(self):
        shared_gate._hash_for.cache_clear()
        assert shared_gate._hash_for(GATE_PASSWORD).startswith("pbkdf2_sha256$")

    def test_turning_account_mail_on_beside_the_gate_refuses_to_start(self, settings):
        settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = True
        assert "juristid.E036" in _ids()

    def test_the_gate_configuration_raises_nothing_new(self):
        assert not {message for message in _ids() if message.startswith("juristid.E03")}


@pytest.mark.django_db
@pytest.mark.parametrize(("route", "kwargs"), LOCAL_ROUTES)
def test_without_any_authenticator_the_pages_do_not_exist(client, settings, route, kwargs):
    settings.AUTH_MODE = AuthMode.NONE
    client.force_login(factories.UserFactory())

    assert client.get(reverse(route, kwargs=kwargs)).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(("route", "takes_pk"), ADMIN_ROUTES)
def test_without_personal_sign_in_administration_does_not_exist(client, settings, route, takes_pk):
    settings.AUTH_MODE = AuthMode.NONE
    admin = factories.UserFactory(
        capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"},
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(admin)
    url = reverse(route, kwargs={"pk": admin.pk} if takes_pk else {})

    assert client.post(url, {}).status_code == 404


@pytest.mark.django_db
def test_cloudflare_access_refuses_before_any_personal_sign_in_page(client, settings):
    settings.AUTH_MODE = AuthMode.CLOUDFLARE_ACCESS
    settings.CF_ACCESS_TEAM_DOMAIN = "koda.cloudflareaccess.com"
    settings.CF_ACCESS_AUDIENCE = "aud"

    assert client.get(reverse("accounts:sign_in")).status_code == 403


# -- existing accounts and their sessions -------------------------------------------------------


@pytest.mark.django_db
def test_an_existing_account_reads_as_it_always_did():
    """The new columns default to what an account already is."""
    user = factories.UserFactory()

    assert user.provisioning_state == ProvisioningState.ACTIVATED
    assert user.capability_overrides == {}
    assert user.security_epoch == 0
    assert user.local_password_set_at is None
    assert user.last_authenticated_at is None
    assert user.account_status == "ACTIVE"


@pytest.mark.django_db
def test_an_existing_session_survives_the_deployment():
    """At epoch 0 the session hash is byte-for-byte Django's, so nobody is signed out."""
    from django.contrib.auth.base_user import AbstractBaseUser

    user = factories.UserFactory()
    user.set_unusable_password()

    assert user.get_session_auth_hash() == AbstractBaseUser._get_session_auth_hash(user)
    user.security_epoch = 1
    assert user.get_session_auth_hash() != AbstractBaseUser._get_session_auth_hash(user)


def test_the_schema_change_is_additive():
    """No data rewritten, no row created, nothing dropped (docs/adr/0145 §14)."""
    from django.db import migrations
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    allowed = (
        migrations.CreateModel,
        migrations.AddField,
        migrations.AddConstraint,
        migrations.AddIndex,
        migrations.AlterField,
    )
    for key in (
        ("accounts", "0004_local_authentication"),
        ("audit", "0035_local_authentication_events"),
    ):
        for operation in loader.disk_migrations[key].operations:
            assert isinstance(operation, allowed), (key, operation)


def test_the_release_still_serving_survives_the_migration():
    """What `migration_plan` will say at deploy time, decided now (ENG-014).

    Every new account column is nullable or has a *database* default, so the
    release still serving can insert an account between the migration and the
    swap. The one operation the gate flags is the reviewed constraint on the
    existing account table, which every existing row (ACTIVATED by default) and
    every row the old release can write (it cannot name the column) satisfies.
    """
    from django.db.migrations.loader import MigrationLoader

    from app.core.deployment import consequential_operations

    loader = MigrationLoader(None, ignore_no_migrations=True)
    before = loader.project_state(("accounts", "0003_sharedgatethrottle"))
    accounts = consequential_operations(
        loader.disk_migrations[("accounts", "0004_local_authentication")], state=before
    )
    audit_before = loader.project_state(("audit", "0034_document_title_changed_event"))
    audit = consequential_operations(
        loader.disk_migrations[("audit", "0035_local_authentication_events")], state=audit_before
    )

    assert list(accounts) == ["AddConstraint"]
    constraints = [
        operation.constraint.name
        for operation in loader.disk_migrations[
            ("accounts", "0004_local_authentication")
        ].operations
        if type(operation).__name__ == "AddConstraint"
    ]
    assert constraints == ["accounts_user_unactivated_is_inactive"]
    assert audit == {}


def test_the_audit_change_only_widens_the_vocabulary():
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    operation = loader.disk_migrations[("audit", "0035_local_authentication_events")].operations[0]
    assert operation.name == "event_type"
    values = {value for value, _ in operation.field.choices}
    assert {member.value for member in SecurityEventType} == values


@pytest.mark.django_db
def test_nothing_in_the_release_creates_a_user():
    """Migrations ran on this database; it holds no account until a test makes one."""
    assert User.objects.count() == 0


def test_no_existing_password_is_touched_by_the_code_that_ships():
    """Only the person's own link, their own change form and the test helpers set one."""
    callers = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "app").rglob("*.py")
        if "migrations" not in path.parts and ".set_password(" in path.read_text(encoding="utf-8")
    )
    assert callers == ["app/accounts/credentials.py", "app/accounts/models.py"]


# -- unsafe configuration refuses to start ---------------------------------------------------------


@pytest.mark.django_db
class TestLocalPasswordConfiguration:
    @pytest.fixture(autouse=True)
    def local(self, settings):
        apply_local_password(settings)
        settings.DEBUG = False
        settings.SESSION_COOKIE_SECURE = True
        settings.CSRF_COOKIE_SECURE = True
        settings.SECURE_SSL_REDIRECT = True
        return settings

    def test_a_sound_configuration_raises_nothing(self):
        assert not {message for message in _ids() if message.startswith("juristid.E03")}

    def test_a_short_password_floor_is_refused(self, settings):
        settings.LOCAL_AUTH_PASSWORD_MIN_LENGTH = 12
        assert "juristid.E030" in _ids()

    def test_a_hasher_other_than_argon2_is_refused(self, settings):
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
        assert "juristid.E031" in _ids()

    def test_a_test_grade_hash_cost_is_refused_with_real_data(self, settings):
        settings.REAL_DATA_ALLOWED = True
        settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "x" * 40
        settings.LOCAL_AUTH_ARGON2_MEMORY_KIB = 1024
        assert "juristid.E031" in _ids()

    def test_an_endless_session_is_refused(self, settings):
        settings.LOCAL_AUTH_SESSION_IDLE_SECONDS = 0
        assert "juristid.E032" in _ids()

    def test_real_data_needs_a_dedicated_mfa_key(self, settings):
        settings.REAL_DATA_ALLOWED = True
        settings.LOCAL_AUTH_ARGON2_MEMORY_KIB = 65536
        settings.LOCAL_AUTH_ARGON2_TIME_COST = 2
        settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = ""
        assert "juristid.E033" in _ids()

    def test_an_insecure_cookie_is_refused(self, settings):
        settings.SESSION_COOKIE_SECURE = False
        assert "juristid.E034" in _ids()

    def test_real_data_without_https_is_refused(self, settings):
        settings.REAL_DATA_ALLOWED = True
        settings.SECURE_SSL_REDIRECT = False
        settings.SECURE_PROXY_SSL_HEADER = None
        assert "juristid.E035" in _ids()

    def test_mail_without_an_https_link_base_is_refused(self, settings):
        settings.REAL_DATA_ALLOWED = True
        settings.ACCOUNT_LINK_BASE_URL = "http://juristid.koda.ee"
        assert "juristid.E037" in _ids()

    def test_mail_into_a_console_on_real_data_is_refused(self, settings):
        settings.REAL_DATA_ALLOWED = True
        settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
        assert "juristid.E038" in _ids()

    def test_the_synthetic_sign_in_beside_it_is_refused(self, settings):
        settings.DEV_LOGIN_ENABLED = True
        assert "juristid.E008" in _ids()

    def test_the_mode_is_recognised(self):
        assert "juristid.E009" not in _ids()


@pytest.mark.django_db
def test_an_unknown_mode_still_refuses_to_start(settings):
    settings.AUTH_MODE = "local_passwords"
    assert "juristid.E009" in _ids()


@pytest.mark.django_db
def test_real_data_with_local_password_satisfies_the_authenticator_rule(settings):
    apply_local_password(settings)
    settings.REAL_DATA_ALLOWED = True
    assert "juristid.E006" not in _ids()


def test_the_login_url_follows_the_mode():
    source = _settings_source()
    assert '"shared_gate": "accounts:choose_persona"' in source
    assert '"local_password": "accounts:sign_in"' in source


@pytest.mark.django_db
def test_an_administrator_capability_alone_never_opens_the_django_admin(client, settings):
    """Account administration is not `is_staff`: the technical admin stays separate."""
    apply_local_password(settings)
    admin, secret = person(manage=True, delegate=True, totp=True)
    from tests.local_auth import sign_in

    sign_in(client, admin, secret=secret)

    assert not admin.is_staff
    assert client.get("/admin/").status_code == 302
    assert Capability.MANAGE_ACCOUNTS.value in admin.capability_overrides
