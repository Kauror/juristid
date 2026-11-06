"""B-01: every bulk export and every break-glass read leaves one security row.

The trail promised them (master specification «SecurityAuditEvent» and §16.6,
docs/adr/0181, docs/incident-response.md §1) and nothing wrote them: eight
exports served to a READER and a break-glass read added 0 rows (2026-11 full
audit, agent B T1 and agent H).

The owner's safeguard is tested as hard as the row itself: the row says that an
export happened and how big it was, never what it said. Every export here is
built over a world whose Teema titles, opinion title and search text are unique
tokens, the test proves a token **is** in the file, and then that it is in no
column of the audit row.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta
from typing import Any

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.urls import reverse

from app.accounts.services import grant_break_glass
from app.audit.access import filter_summary
from app.audit.enums import AuthMethod, SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core import authorization
from app.core import views as core_views
from app.core.enums import Visibility
from app.legacy_import.lovable_sync_models import SyncMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as metric_keys
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate
from tests.lovable_observability_support import Pilot, control, night_at
from tests.report_builder_support import as_post, form_fields, sent

pytestmark = pytest.mark.django_db

NORMAL_TITLE = "AUDW2-AVALIK-7101"
RESTRICTED_TITLE = "AUDW2-PIIRATUD-7102"
SEARCH_TEXT = "AUDW2-OTSING-7103"
LOVABLE_TITLE = "AUDW2-LOVABLE-7104"
LOVABLE_VALUE = "AUDW2-LOVABLE-SEIS-7106"
TOKENS = (NORMAL_TITLE, RESTRICTED_TITLE, SEARCH_TEXT, LOVABLE_TITLE, LOVABLE_VALUE)

EXPORTED = SecurityEventType.EXPORT_GENERATED
USED = SecurityEventType.BREAK_GLASS_USED

#: Every GET export route: (route, kwargs, query, the export's name in the row,
#: format, the token that must be in the file, or "" where the file carries no
#: title). The query carries a search token where the route takes one.
GET_EXPORTS = [
    (
        "matters:export_table",
        {},
        {"q": "AUDW2"},
        "matters.register_table",
        "xlsx",
        RESTRICTED_TITLE,
    ),
    ("matters:export_full", {}, {}, "matters.register_full", "xlsx", RESTRICTED_TITLE),
    (
        "reporting:export",
        {"slug": "teemad"},
        {"periood": "koik"},
        "reporting.teemad",
        "csv",
        RESTRICTED_TITLE,
    ),
    (
        "reporting:export",
        {"slug": "arvamused"},
        {"periood": "koik"},
        "reporting.arvamused",
        "csv",
        RESTRICTED_TITLE,
    ),
    ("reporting:export", {"slug": "materjalid"}, {}, "reporting.materjalid", "csv", ""),
    ("reporting:export", {"slug": "andmekvaliteet"}, {}, "reporting.andmekvaliteet", "csv", ""),
    (
        "reporting:export",
        {"slug": "loend"},
        {"naitaja": metric_keys.OPINIONS_ON_TIME, "ruhm": "ajal", "periood": "koik"},
        "reporting.loend",
        "csv",
        "",
    ),
    (
        "reporting:export_legacy_xlsx",
        {},
        {},
        "reporting.legacy_register",
        "xlsx",
        RESTRICTED_TITLE,
    ),
    (
        "core:release_notes_lovable_export",
        {},
        {"vaade": "oised", "otsi": "AUDW2-LOVABLE"},
        "release_notes.lovable.oised",
        "xlsx",
        LOVABLE_VALUE,
    ),
    (
        "core:release_notes_lovable_export",
        {},
        {"vaade": "vaadeldud"},
        "release_notes.lovable.vaadeldud",
        "xlsx",
        "",
    ),
    (
        "core:release_notes_lovable_export",
        {},
        {"vaade": "ootel"},
        "release_notes.lovable.ootel",
        "xlsx",
        "",
    ),
]

IDS = [row[3] for row in GET_EXPORTS]


@pytest.fixture
def world(capture_evidence, settings, monkeypatch):
    settings.REAL_DATA_ALLOWED = False
    # The Lovable page reads «now» from one place; pinned after the two nights,
    # as `tests/test_lovable_pages.py` pins it.
    monkeypatch.setattr(core_views, "_lovable_now", lambda: night_at(3) + timedelta(hours=2))
    owner = factories.UserFactory()
    normal = factories.MatterFactory(owner=owner, title=NORMAL_TITLE)
    restricted = factories.MatterFactory(
        owner=owner, title=RESTRICTED_TITLE, visibility=Visibility.RESTRICTED
    )
    sent(restricted, capture_evidence, day=date(2026, 10, 2), summary="Toetame.")
    control(SyncMode.COMPARE)
    pilot = Pilot()
    pilot.add(501, teema=LOVABLE_TITLE)
    pilot.night(night_at(0))
    pilot.edit("2026_501", hetkeseis=LOVABLE_VALUE)
    pilot.night(night_at(1))
    return {"normal": normal, "restricted": restricted, "owner": owner}


def _url(name: str, kwargs: dict[str, str], query: dict[str, str]) -> str:
    from urllib.parse import urlencode

    url = reverse(name, kwargs=kwargs)
    return f"{url}?{urlencode(query)}" if query else url


def _payload(response: Any) -> bytes:
    raw = b"".join(response.streaming_content) if response.streaming else response.content
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return b"".join(archive.read(member) for member in archive.namelist())
    return raw


def _row_text(event: SecurityAuditEvent) -> str:
    """Every column of the row a reader of the trail could see, as one string."""
    return json.dumps(
        {
            "detail": event.detail,
            "subject_type": event.subject_type,
            "subject_id": str(event.subject_id),
            "user_agent": event.user_agent,
            "ip": event.ip_address,
        },
        ensure_ascii=False,
    )


def _events(event_type: str) -> list[SecurityAuditEvent]:
    return list(SecurityAuditEvent.objects.filter(event_type=event_type).order_by("created_at"))


def _signed_in(user: Any) -> Client:
    client = Client(HTTP_USER_AGENT="audw2-test")
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# Every export: one row, the right one, and nothing the file said
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["reader", "head"])
@pytest.mark.parametrize(
    ("name", "kwargs", "query", "export", "fmt", "token"), GET_EXPORTS, ids=IDS
)
def test_every_export_writes_one_row_without_its_contents(
    world, role, name, kwargs, query, export, fmt, token
):
    user = factories.ReaderFactory() if role == "reader" else factories.DepartmentHeadFactory()
    client = _signed_in(user)
    before = SecurityAuditEvent.objects.count()

    response = client.get(_url(name, kwargs, query))

    assert response.status_code == 200, response.status_code
    body = _payload(response)
    if token:
        assert token.encode() in body, "the file must carry the token the row must not"
    assert SecurityAuditEvent.objects.count() == before + 1
    (event,) = _events(EXPORTED)
    assert event.actor_id == user.pk
    assert event.succeeded
    assert event.detail["export"] == export
    assert event.detail["format"] == fmt
    assert event.detail["method"] == "GET"
    assert event.detail["acting_as_user"] == str(user.pk)
    assert "authenticated_via" in event.detail
    assert isinstance(event.detail["rows"], int)
    assert event.detail["filter_keys"] == sorted(query)
    assert bool(event.detail["filters_hmac"]) == bool(query)
    assert event.user_agent == "audw2-test"
    text = _row_text(event)
    assert not [t for t in TOKENS if t in text], text
    assert "AUDW2" not in text


def test_the_row_counts_are_the_records_the_file_was_built_from(world):
    client = _signed_in(factories.ReaderFactory())
    factories.MatterFactory(title="Muu teema, otsingule ei vasta")

    counts = {}
    for name, kwargs, query, export, _fmt, _token in GET_EXPORTS[:4] + GET_EXPORTS[7:9]:
        client.get(_url(name, kwargs, query))
        counts[export] = _events(EXPORTED)[-1].detail["rows"]

    # Four Teemad: the two titled «AUDW2-…», the Lovable pilot's (also
    # «AUDW2-…») and this one, which the searched table leaves out.
    assert Matter.objects.count() == 4
    assert counts == {
        "matters.register_table": 3,
        "matters.register_full": 4,
        "reporting.teemad": 4,
        "reporting.arvamused": 1,
        "reporting.legacy_register": 4,
        "release_notes.lovable.oised": 1,
    }


def test_an_administrator_without_a_grant_gets_an_empty_file_on_the_trail(world):
    """Not a refusal: the register exports answer an empty file, and it is logged."""
    admin = factories.AdministratorFactory()
    client = _signed_in(admin)

    response = client.get(reverse("matters:export_full"))

    assert response.status_code == 200
    assert RESTRICTED_TITLE.encode() not in _payload(response)
    (event,) = _events(EXPORTED)
    assert event.detail["rows"] == 0
    assert event.actor_id == admin.pk
    assert not _events(USED)


def test_the_word_overview_writes_one_row_with_its_period_and_no_title(world):
    reader = factories.ReaderFactory()
    client = _signed_in(reader)
    page = client.get(
        reverse("reporting:report_builder"), {"algus": "1.10.2026", "lopp": "31.10.2026"}
    )
    fields = form_fields(page)
    assert not _events(EXPORTED)

    response = client.post(reverse("reporting:report_export"), as_post(fields, toiming="eelvaade"))

    assert response.status_code == 200
    assert RESTRICTED_TITLE.encode() in _payload(response)
    (event,) = _events(EXPORTED)
    assert event.detail["export"] == "reporting.report_builder"
    assert event.detail["format"] == "docx"
    assert event.detail["method"] == "POST"
    assert event.detail["rows"] == 1
    assert event.detail["period_start"] == "2026-10-01"
    assert event.detail["period_end"] == "2026-10-31"
    assert event.detail["filter_keys"] == []
    assert not [t for t in TOKENS if t in _row_text(event)]


def test_the_row_records_the_shared_gate_persona_and_how_little_it_proves(world, settings):
    apply_shared_gate(settings)
    persona = factories.UserFactory()
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    client.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})

    response = client.get(_url("reporting:export", {"slug": "teemad"}, {"periood": "koik"}))

    assert response.status_code == 200
    (event,) = _events(EXPORTED)
    assert event.actor_id == persona.pk
    assert event.auth_method == AuthMethod.SHARED_GATE
    assert event.acting_persona_id == persona.pk
    assert event.authenticated_principal_id is None
    assert event.detail["acting_as_user"] == str(persona.pk)


def test_filter_values_are_digested_with_a_key_and_never_stored():
    from django.http import QueryDict

    one = filter_summary(QueryDict(f"q={SEARCH_TEXT}&periood=koik&tühi="))
    two = filter_summary(QueryDict(f"periood=koik&q={SEARCH_TEXT}"))
    other = filter_summary(QueryDict("periood=koik&q=muu"))
    made_up = filter_summary(QueryDict(f"{SEARCH_TEXT}=1&q=x"))

    assert one["filter_keys"] == ["periood", "q"]
    assert one["filters_hmac"] == two["filters_hmac"], "the same slice, the same digest"
    assert one["filters_hmac"] != other["filters_hmac"]
    assert SEARCH_TEXT not in json.dumps([one, two, made_up])
    # A parameter name nobody's page writes is counted, not recorded.
    assert made_up["filter_keys"] == ["q"]
    assert made_up["other_filter_keys"] == 1
    assert filter_summary(None) == {"filter_keys": [], "filters_hmac": ""}


# ---------------------------------------------------------------------------
# What writes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kwargs", "query", "export", "fmt", "token"), GET_EXPORTS, ids=IDS
)
def test_head_is_not_an_export(world, name, kwargs, query, export, fmt, token):
    client = _signed_in(factories.ReaderFactory())

    response = client.head(_url(name, kwargs, query))

    # 200 where the route answers HEAD, 405 where it is GET-only; never a row.
    assert response.status_code in (200, 405)
    assert not _events(EXPORTED)


def test_refused_and_unknown_exports_write_nothing(world):
    reader = _signed_in(factories.ReaderFactory())
    admin = _signed_in(factories.AdministratorFactory())
    anonymous = Client()
    lovable = reverse("core:release_notes_lovable_export")

    assert reader.get(reverse("reporting:export", kwargs={"slug": "tundmatu"})).status_code == 404
    assert reader.get(f"{lovable}?vaade=valmisolek").status_code == 404
    assert reader.post(f"{lovable}?vaade=oised").status_code == 405
    assert reader.post(reverse("matters:export_full")).status_code == 405
    assert admin.get(f"{lovable}?vaade=oised").status_code == 403
    assert anonymous.get(reverse("matters:export_full")).status_code == 302
    assert anonymous.get(reverse("reporting:export", kwargs={"slug": "teemad"})).status_code == 302

    assert not _events(EXPORTED)


def test_a_refused_word_export_writes_nothing(world):
    client = _signed_in(factories.ReaderFactory())
    page = client.get(
        reverse("reporting:report_builder"), {"algus": "1.10.2026", "lopp": "31.10.2026"}
    )
    fields = [(k, v) for k, v in form_fields(page) if k != "eelvaade_tunnus"]
    stale = as_post(fields, toiming="eelvaade")
    stale["eelvaade_tunnus"] = ["võltsitud"]

    assert client.post(reverse("reporting:report_export"), stale).status_code == 409
    assert client.post(reverse("reporting:report_export"), {}).status_code == 409
    admin = _signed_in(factories.AdministratorFactory())
    assert admin.post(reverse("reporting:report_export"), as_post(fields)).status_code == 404

    assert not _events(EXPORTED)


def test_the_append_only_trigger_accepts_the_rows_and_keeps_them(world):
    client = _signed_in(factories.ReaderFactory())
    client.get(reverse("matters:export_full"))
    (event,) = _events(EXPORTED)

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE audit_securityauditevent SET detail = '{}'::jsonb WHERE id = %s", [event.pk]
        )
    event.refresh_from_db()
    assert event.detail["export"] == "matters.register_full"


# ---------------------------------------------------------------------------
# Break-glass
# ---------------------------------------------------------------------------


def _grant(user: Any) -> Any:
    return grant_break_glass(
        user=user,
        granted_by=factories.DepartmentHeadFactory(),
        reason="AUDW2 hädaligipääsu põhjus",
        duration=timedelta(hours=1),
    )


def test_a_read_through_a_grant_writes_one_row_per_request(world):
    admin = factories.AdministratorFactory()
    grant = _grant(admin)
    client = _signed_in(admin)

    page = client.get(reverse("matters:matter_list"))

    assert page.status_code == 200
    assert RESTRICTED_TITLE in page.content.decode()
    (event,) = _events(USED)
    assert event.actor_id == admin.pk
    assert event.subject_type == "accounts.BreakGlassGrant"
    assert event.subject_id == grant.pk
    assert event.detail["grant"] == str(grant.pk)
    assert event.detail["path"] == reverse("matters:matter_list")
    assert event.detail["route"] == "matters:matter_list"
    assert event.detail["method"] == "GET"
    # No content, and not even the grant's reason (BREAK_GLASS_GRANTED has it).
    text = _row_text(event)
    assert not [t for t in TOKENS if t in text]
    assert "AUDW2" not in text

    client.get(reverse("matters:matter_list"))
    assert len(_events(USED)) == 2, "one row per request, not one per session"


def test_an_export_through_a_grant_is_logged_as_both(world):
    admin = factories.AdministratorFactory()
    grant = _grant(admin)
    client = _signed_in(admin)

    response = client.get(reverse("matters:export_full"))

    assert RESTRICTED_TITLE.encode() in _payload(response)
    (used,) = _events(USED)
    (exported,) = _events(EXPORTED)
    assert used.detail["grant"] == str(grant.pk)
    assert used.detail["route"] == "matters:export_full"
    assert exported.detail["rows"] == Matter.objects.count() == 3
    assert used.correlation_id == exported.correlation_id


def test_no_grant_no_read_and_no_row(world):
    client = _signed_in(factories.AdministratorFactory())

    assert (
        client.get(f"{reverse('core:release_notes_lovable_export')}?vaade=oised").status_code == 403
    )
    page = client.get(reverse("matters:matter_list"))
    assert RESTRICTED_TITLE not in page.content.decode()

    assert not _events(USED)


def test_an_expired_or_revoked_grant_writes_nothing(world):
    from app.accounts.services import revoke_break_glass

    admin = factories.AdministratorFactory()
    grant = _grant(admin)
    revoke_break_glass(grant=grant, revoked_by=factories.DepartmentHeadFactory())
    client = _signed_in(admin)

    assert RESTRICTED_TITLE not in client.get(reverse("matters:matter_list")).content.decode()
    assert not _events(USED)


@pytest.mark.parametrize("role", ["reader", "head", "specialist"])
def test_a_business_reader_relies_on_no_grant_and_writes_none(world, role):
    factory = {
        "reader": factories.ReaderFactory,
        "head": factories.DepartmentHeadFactory,
        "specialist": factories.UserFactory,
    }[role]
    user = factory()
    _grant(user)  # held, and not the reason they read (docs/adr/0181)
    client = _signed_in(user)

    assert RESTRICTED_TITLE in client.get(reverse("matters:matter_list")).content.decode()
    assert not _events(USED)


def test_outside_a_request_a_grant_writes_nothing(world):
    admin = factories.AdministratorFactory()
    _grant(admin)

    assert authorization.scope_for_user(admin).sees_all_restricted
    assert authorization.may_read_business_content(admin)
    assert not _events(USED)
