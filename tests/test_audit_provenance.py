"""Every new audit row says how its actor arrived (docs/adr/0180, csf_2a1004b90bbd9b1e5be3ba77).

Under the shared gate the ``actor`` on a row is a persona somebody picked from a
list behind one department password. These tests hold the provenance that now
travels with every new row — method, assurance, the authenticated principal (or
none), the acting persona and one correlation id per request — and that the
history pages render a shared-gate actor as *chosen*, never as personally
verified. The gate, the persona choice and the roles themselves are unchanged
and are not tested for change here (finding #1 is an accepted pilot risk).
"""

from __future__ import annotations

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory
from django.urls import reverse

from app.accounts import shared_gate
from app.accounts.enums import UserRole
from app.audit import provenance
from app.audit.enums import AuthAssurance, AuthMethod, ChangeEventType, SecurityEventType
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.audit.operations import composer_operation
from app.audit.services import record_change_event, record_security_event
from app.matters.department_changes import ChangeRow
from tests import factories
from tests.follow_ups import PDF
from tests.gate import PASSWORD, apply_shared_gate
from tests.local_auth import apply_local_password, person, sign_in

pytestmark = pytest.mark.django_db

QUALIFIER = "valitud ühisparooliga"


def _gate_client() -> Client:
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    return client


def _act_as(client: Client, user) -> None:
    response = client.post(reverse("accounts:act_as"), {"user_id": str(user.pk)})
    assert response.status_code == 302


def _meeting(client: Client, matter, summary: str = "Kohtusime ministeeriumiga."):
    return client.post(
        reverse("matters:add_meeting", kwargs={"pk": matter.pk}),
        {
            "occurred_on": "1.10.2026",
            "summary": summary,
            "attachments": SimpleUploadedFile("protokoll.pdf", PDF, content_type="application/pdf"),
        },
        headers={"HX-Request": "true"},
    )


def _gate_request(user=None):
    """A request behind the gate, acting as ``user`` — the state the views see."""
    request = RequestFactory().post("/")
    SessionMiddleware(lambda r: None).process_request(request)
    shared_gate.open_gate(request)
    from django.contrib.auth.models import AnonymousUser

    request.user = user if user is not None else AnonymousUser()
    return request


# -- the shared gate, end to end --------------------------------------------------------


def test_a_shared_gate_write_records_method_persona_and_one_correlation(settings):
    apply_shared_gate(settings)
    marko = factories.DepartmentHeadFactory(display_name="Marko Näidisjuht")
    matter = factories.MatterFactory(owner=marko)
    client = _gate_client()
    _act_as(client, marko)

    response = _meeting(client, matter)

    assert response.status_code == 200, response.content[:400]
    rows = list(ChangeEvent.objects.filter(matter=matter, actor=marko))
    assert len(rows) >= 2, [row.event_type for row in rows]
    assert {row.auth_method for row in rows} == {AuthMethod.SHARED_GATE}
    assert {row.auth_assurance for row in rows} == {AuthAssurance.SHARED_SECRET}
    assert {row.acting_persona_id for row in rows} == {marko.pk}
    # Choosing a persona proves nobody: no row names an authenticated principal.
    assert {row.authenticated_principal_id for row in rows} == {None}
    correlations = {row.correlation_id for row in rows}
    assert len(correlations) == 1 and None not in correlations

    chosen = SecurityAuditEvent.objects.get(event_type=SecurityEventType.PERSONA_SELECTED)
    assert chosen.auth_method == AuthMethod.SHARED_GATE
    assert chosen.acting_persona_id == marko.pk
    assert chosen.authenticated_principal_id is None
    assert chosen.correlation_id is not None
    # A different request, a different correlation.
    assert chosen.correlation_id not in correlations


def test_the_gate_itself_records_what_it_proves(settings):
    apply_shared_gate(settings)
    Client().post(reverse("accounts:shared_gate"), {"password": "vale parool"})
    _gate_client()

    failed = SecurityAuditEvent.objects.get(event_type=SecurityEventType.AUTHENTICATION_FAILED)
    passed = SecurityAuditEvent.objects.get(event_type=SecurityEventType.SHARED_GATE_PASSED)

    assert (failed.auth_method, failed.auth_assurance) == (
        AuthMethod.SHARED_GATE,
        AuthAssurance.NONE,
    )
    assert (passed.auth_method, passed.auth_assurance) == (
        AuthMethod.SHARED_GATE,
        AuthAssurance.SHARED_SECRET,
    )
    assert passed.acting_persona_id is None
    assert passed.authenticated_principal_id is None
    assert failed.correlation_id and passed.correlation_id
    assert failed.correlation_id != passed.correlation_id


def test_every_row_the_services_write_inside_a_shared_gate_request_is_stamped(settings):
    """The constructors stamp; no caller passes anything (`app.audit.services`)."""
    apply_shared_gate(settings)
    ireen = factories.UserFactory(role=UserRole.SPECIALIST)
    matter = factories.MatterFactory(owner=ireen)
    request = _gate_request(ireen)

    with provenance.request_context(request) as correlation:
        change = record_change_event(
            event_type=ChangeEventType.MATTER_TITLE_CHANGED, matter=matter, actor=ireen
        )
        unattributed = record_change_event(
            event_type=ChangeEventType.MATTER_TITLE_CHANGED, matter=matter
        )
        security = record_security_event(
            event_type=SecurityEventType.DOCUMENT_DOWNLOADED, actor=ireen
        )

    for row in (change, unattributed, security):
        row.refresh_from_db()
        assert row.auth_method == AuthMethod.SHARED_GATE
        assert row.auth_assurance == AuthAssurance.SHARED_SECRET
        # The persona the session acts as, whatever `actor` the caller passed.
        assert row.acting_persona_id == ireen.pk
        assert row.authenticated_principal_id is None
        assert row.correlation_id == correlation
    assert unattributed.actor_id is None
    assert change.actor_id == ireen.pk


def test_a_request_context_ends_with_its_request():
    request = _gate_request()
    with provenance.request_context(request):
        pass
    row = record_security_event(event_type=SecurityEventType.EXPORT_GENERATED)

    assert row.auth_method == AuthMethod.SYSTEM
    assert row.correlation_id is None


# -- history pages -------------------------------------------------------------


def test_history_says_a_shared_gate_name_was_chosen_and_old_rows_read_as_before(settings):
    apply_shared_gate(settings)
    marko = factories.DepartmentHeadFactory(display_name="Marko Näidisjuht")
    matter = factories.MatterFactory(owner=marko)
    # A row from before provenance was recorded: the empty method every
    # historical row has, written straight through the ORM as the old
    # constructors wrote it.
    ChangeEvent.objects.create(
        event_type=ChangeEventType.MATTER_TITLE_CHANGED,
        matter=matter,
        actor=marko,
        summary="Vana rida",
    )
    client = _gate_client()
    _act_as(client, marko)
    _meeting(client, matter)

    log = client.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk})).content.decode()
    chronology = client.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert f'<span class="changelog__who">Marko Näidisjuht ({QUALIFIER})</span>' in log
    # The old row still names its actor exactly as it always did.
    assert '<span class="changelog__who">Marko Näidisjuht</span>' in log
    # The chronology line keeps its compact name and says it in the tooltip.
    assert f'title="Marko Näidisjuht ({QUALIFIER})"' in chronology


def test_the_department_change_list_labels_shared_gate_rows():
    shared = ChangeRow(
        at=None,
        event_type="",
        badge="",
        verb="",
        detail="",
        actor_name="Marko Näidisjuht",
        initials="MN",
        matter_title="",
        url="",
        auth_method=AuthMethod.SHARED_GATE,
    )
    personal = ChangeRow(
        **{**shared.__dict__, "auth_method": AuthMethod.LOCAL_PASSWORD},
    )
    historical = ChangeRow(**{**shared.__dict__, "auth_method": ""})

    assert shared.actor_label == f"Marko Näidisjuht ({QUALIFIER})"
    assert personal.actor_label == "Marko Näidisjuht"
    assert historical.actor_label == "Marko Näidisjuht"


# -- personal sign-in, development sign-in, system ----------------------------------------


def test_a_personal_sign_in_attributes_the_authenticated_account(settings):
    apply_local_password(settings)
    user, secret = person(totp=True)
    client = Client()

    assert sign_in(client, user, secret=secret).status_code == 302

    event = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED, actor=user
    )
    assert event.auth_method == AuthMethod.LOCAL_PASSWORD
    assert event.auth_assurance == AuthAssurance.INDIVIDUAL_MFA
    assert event.authenticated_principal_id == user.pk
    assert event.acting_persona_id == user.pk
    assert event.correlation_id is not None
    assert event.actor_qualifier == ""


def test_a_personal_password_without_a_second_factor_is_recorded_as_such(settings):
    apply_local_password(settings)
    user, _ = person()

    assert sign_in(Client(), user).status_code == 302

    event = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.AUTHENTICATION_SUCCEEDED, actor=user
    )
    assert (event.auth_method, event.auth_assurance, event.authenticated_principal_id) == (
        AuthMethod.LOCAL_PASSWORD,
        AuthAssurance.INDIVIDUAL_PASSWORD,
        user.pk,
    )


def test_the_development_sign_in_names_a_persona_and_proves_nobody(client, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(specialist)

    assert _meeting(client, matter).status_code == 200

    rows = list(ChangeEvent.objects.filter(matter=matter, actor=specialist))
    assert rows
    assert {(row.auth_method, row.auth_assurance) for row in rows} == {
        (AuthMethod.DEV_LOGIN, AuthAssurance.NONE)
    }
    assert {row.acting_persona_id for row in rows} == {specialist.pk}
    assert {row.authenticated_principal_id for row in rows} == {None}
    assert all(row.actor_qualifier == "" for row in rows)


def test_rows_written_outside_a_request_are_the_systems(specialist):
    matter = factories.MatterFactory(owner=specialist)

    lone = record_change_event(event_type=ChangeEventType.MATTER_TITLE_CHANGED, matter=matter)
    with composer_operation() as operation:
        grouped = record_change_event(
            event_type=ChangeEventType.MATTER_TITLE_CHANGED, matter=matter
        )
    security = record_security_event(event_type=SecurityEventType.IMPORT_RUN)

    for row in (lone, grouped, security):
        assert row.auth_method == AuthMethod.SYSTEM
        assert row.auth_assurance == AuthAssurance.SYSTEM
        assert row.acting_persona_id is None
        assert row.authenticated_principal_id is None
    assert lone.correlation_id is None
    assert security.correlation_id is None
    # A composer save with no request is still one operation, and correlates as one.
    assert grouped.correlation_id == operation


def test_a_job_queued_by_a_request_is_system_work_under_its_correlation(specialist):
    marker = factories.MatterFactory(owner=specialist)
    request = _gate_request(specialist)
    with provenance.request_context(request) as correlation:
        pass

    with provenance.correlated(correlation):
        row = record_change_event(event_type=ChangeEventType.MATTER_TITLE_CHANGED, matter=marker)

    assert row.auth_method == AuthMethod.SYSTEM
    assert row.acting_persona_id is None
    assert row.correlation_id == correlation


# -- history is not rewritten ----------------------------------------------------------------


@pytest.fixture
def audit_back_at_the_end():
    yield
    _migrate_audit_to(("audit", "0038_auth_provenance"))


def _migrate_audit_to(target: tuple[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


def test_the_migration_leaves_existing_rows_not_recorded(specialist, audit_back_at_the_end):
    """No row is rewritten and no authentication is inferred (docs/adr/0180 §3)."""
    matter = factories.MatterFactory(owner=specialist)
    _migrate_audit_to(("audit", "0037_matter_permanent_deletion_events"))
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO audit_changeevent (id, created_at, matter_id, actor_id, event_type,"
            " occurred_at, object_type, summary, payload) VALUES"
            " (gen_random_uuid(), now(), %s, %s, 'MATTER_TITLE_CHANGED', now(), '', 'enne', '{}')",
            [matter.pk, specialist.pk],
        )
        cursor.execute("SELECT id, actor_id, summary, occurred_at FROM audit_changeevent")
        before = sorted(cursor.fetchall())

    _migrate_audit_to(("audit", "0038_auth_provenance"))

    with connection.cursor() as cursor:
        cursor.execute("SELECT id, actor_id, summary, occurred_at FROM audit_changeevent")
        after = sorted(cursor.fetchall())
        cursor.execute(
            "SELECT auth_method, auth_assurance, authenticated_principal_id, acting_persona_id,"
            " correlation_id FROM audit_changeevent WHERE summary = 'enne'"
        )
        provenance_columns = cursor.fetchone()
    assert after == before
    assert provenance_columns == ("", "", None, None, None)
