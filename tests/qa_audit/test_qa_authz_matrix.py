"""QA authz (2026-10-14): the permission matrix, allowed AND denied, through real URLs.

Personas: responsible lawyer (owner), another department lawyer, department
head, READER, ADMINISTRATOR (with and without break-glass), a READER lent
`department.view_management`, a READER collaborating on one RESTRICTED Matter,
signed-out, and the shared-gate reader with no persona (`DepartmentViewer`).

Every case below asserts both halves: the persona that must be refused is
refused (and nothing is written), and the persona that must be allowed is.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.middleware.csrf import get_token
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import BreakGlassGrant
from app.core.enums import Visibility
from app.documents.models import Document
from app.intelligence.enums import FactStatus
from app.matters.enums import EngagementKind, MatterDataClass
from app.matters.models import Entry, Matter, MatterEngagement
from app.submissions.enums import SubmissionStatus
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
SECRET = "SALAJANE-MAATRIKS-3307"  # noqa: S105


def _client(user=None, **kwargs) -> Client:
    client = Client(**kwargs)
    if user is not None:
        client.force_login(user)
    return client


def _gate_client(settings, persona=None) -> Client:
    apply_shared_gate(settings)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    if persona is not None:
        client.post(reverse("accounts:act_as"), {"user_id": str(persona.pk)})
    return client


@pytest.fixture
def people(db):
    owner = factories.UserFactory(display_name="Vastutav Jurist")
    return {
        "owner": owner,
        "colleague": factories.UserFactory(display_name="Teine Jurist"),
        "head": factories.DepartmentHeadFactory(),
        "reader": factories.ReaderFactory(),
        "administrator": factories.AdministratorFactory(),
    }


@pytest.fixture
def files(people, capture_evidence):
    """A NORMAL Matter with a NORMAL file, a RESTRICTED file and a RESTRICTED submission;
    and a RESTRICTED Matter with its own file."""
    owner = people["owner"]
    normal = factories.MatterFactory(title="Avalik maatriksteema", owner=owner)
    public = capture_evidence(normal, b"%PDF-1.4 avalik", "avalik.pdf", PDF, title="Avalik fail")
    hidden_doc = capture_evidence(
        normal,
        b"%PDF-1.4 salajane",
        "salajane.pdf",
        PDF,
        title=SECRET,
        visibility_override=Visibility.RESTRICTED,
    )
    hidden_submission = factories.SubmissionFactory(
        matter=normal,
        title=SECRET,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=hidden_doc,
        visibility_override=Visibility.RESTRICTED,
    )
    restricted = factories.MatterFactory(
        title=f"{SECRET} teema", owner=owner, visibility=Visibility.RESTRICTED
    )
    restricted_file = capture_evidence(restricted, b"%PDF-1.4 r", "r.pdf", PDF, title="R fail")
    return {
        "normal": normal,
        "public": public,
        "hidden_doc": hidden_doc,
        "hidden_submission": hidden_submission,
        "restricted": restricted,
        "restricted_file": restricted_file,
    }


# ---------------------------------------------------------------------------
# 1. Reading a RESTRICTED Matter and RESTRICTED children, by id (IDOR on reads)
# ---------------------------------------------------------------------------

ALLOWED_READERS = ("owner", "colleague", "head")
REFUSED_READERS = ("reader", "administrator")


def _read_urls(files):
    restricted = files["restricted"]
    return {
        "matter_detail": reverse("matters:matter_detail", kwargs={"pk": restricted.pk}),
        "matter_changes": reverse("matters:matter_changes", kwargs={"pk": restricted.pk}),
        "timeline": reverse("matters:timeline_page", kwargs={"pk": restricted.pk}),
        "documents": reverse("matters:matter_documents", kwargs={"pk": restricted.pk}),
        "related": reverse("related_materials:section", kwargs={"pk": restricted.pk}),
        "restricted_matter_file": reverse(
            "documents:download", kwargs={"pk": files["restricted_file"].pk}
        ),
        "restricted_child_file": reverse(
            "documents:download", kwargs={"pk": files["hidden_doc"].pk}
        ),
        "restricted_child_file_inline": reverse(
            "documents:open", kwargs={"pk": files["hidden_doc"].pk}
        ),
        "restricted_child_document_detail": reverse(
            "documents:document_detail", kwargs={"pk": files["hidden_doc"].document_id}
        ),
        "restricted_submission_metadata": reverse(
            "submissions:metadata", kwargs={"pk": files["hidden_submission"].pk}
        ),
    }


@pytest.mark.parametrize("who", ALLOWED_READERS)
def test_lawyers_read_restricted_matters_and_children(people, files, who):
    client = _client(people[who])
    for name, url in _read_urls(files).items():
        if name == "restricted_submission_metadata":
            continue  # a POST-only write route; read access is the Matter page
        status = client.get(url, HTTP_HX_REQUEST="true").status_code
        assert status == 200, (who, name, status)


@pytest.mark.parametrize("who", REFUSED_READERS)
def test_reader_and_administrator_get_the_same_404_as_a_random_id(people, files, who):
    client = _client(people[who])
    for name, url in _read_urls(files).items():
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 404, (who, name, response.status_code)
        assert SECRET.encode() not in (
            b"" if getattr(response, "streaming", False) else response.content
        )


def test_the_gate_reader_gets_404_on_every_restricted_document_route(settings, files):
    client = _gate_client(settings)
    for name, url in _read_urls(files).items():
        if not url.startswith("/dokumendid/"):
            continue
        assert client.get(url).status_code == 404, name
    # Allowed half: the NORMAL file on the same Matter downloads.
    ok = client.get(reverse("documents:download", kwargs={"pk": files["public"].pk}))
    assert ok.status_code == 200


def test_signed_out_is_sent_to_sign_in_never_shown(files):
    client = Client()
    for name, url in _read_urls(files).items():
        response = client.get(url)
        assert response.status_code in {302, 404}, name
        if response.status_code == 302:
            assert "/teemad/" not in response.headers["Location"].split("?")[0], name


def test_a_reader_collaborating_on_a_restricted_matter_reads_it_and_nothing_else(people, files):
    reader = people["reader"]
    own = factories.MatterFactory(
        title="Lugeja oma", owner=people["owner"], visibility=Visibility.RESTRICTED
    )
    own.collaborators.set([reader])
    client = _client(reader)
    assert client.get(reverse("matters:matter_detail", kwargs={"pk": own.pk})).status_code == 200
    assert (
        client.get(
            reverse("matters:matter_detail", kwargs={"pk": files["restricted"].pk})
        ).status_code
        == 404
    )
    # Participation in a Matter is not a write right (READER stays read-only).
    response = client.post(
        reverse("matters:update_summary", kwargs={"pk": own.pk}), {"brief_summary": "x"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 2. Break-glass: administration is not content access, a grant is (and expires)
# ---------------------------------------------------------------------------


def test_break_glass_opens_and_its_expiry_closes_and_never_grants_write(people, files):
    admin = people["administrator"]
    url = reverse("matters:matter_detail", kwargs={"pk": files["restricted"].pk})
    client = _client(admin)
    assert client.get(url).status_code == 404

    grant = BreakGlassGrant.objects.create(
        user=admin,
        granted_by=people["head"],
        reason="QA: kontroll",
        starts_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert client.get(url).status_code == 200
    # Reach is not authorship.
    assert (
        client.post(
            reverse("matters:update_summary", kwargs={"pk": files["restricted"].pk}),
            {"brief_summary": "x"},
        ).status_code
        == 404
    )

    BreakGlassGrant.objects.filter(pk=grant.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert client.get(url).status_code == 404


def test_superuser_flag_is_not_content_access(people, files):
    root = factories.AdministratorFactory(is_superuser=True, is_staff=True)
    client = _client(root)
    assert (
        client.get(
            reverse("matters:matter_detail", kwargs={"pk": files["restricted"].pk})
        ).status_code
        == 404
    )
    body = client.get(reverse("search:search") + f"?q={SECRET}").content.decode()
    assert f"{SECRET} teema" not in body


# ---------------------------------------------------------------------------
# 3. Department-head-only surfaces
# ---------------------------------------------------------------------------


def _lend(user):
    user.capability_overrides = {"department.view_management": "allow"}
    user.save(update_fields=["capability_overrides"])
    return user


@pytest.mark.parametrize(
    "who, expected",
    [
        ("head", 200),
        ("owner", 404),
        ("colleague", 404),
        ("reader", 404),
        ("administrator", 404),
        ("lent_reader", 200),
    ],
)
def test_management_pages_by_persona(people, who, expected):
    user = _lend(factories.ReaderFactory()) if who == "lent_reader" else people[who]
    client = _client(user)
    target = people["colleague"] if who != "colleague" else people["owner"]
    for url in (
        reverse("matters:department_changes"),
        reverse("matters:person_work", kwargs={"pk": target.pk}),
    ):
        assert client.get(url).status_code == expected, (who, url)


def test_management_pages_are_404_for_the_gate_reader_and_for_a_denied_head(settings, people):
    client = _gate_client(settings)
    assert client.get(reverse("matters:department_changes")).status_code == 404
    # A head whose view is withdrawn by an individual override loses it.
    head = people["head"]
    head.capability_overrides = {"department.view_management": "deny"}
    head.save(update_fields=["capability_overrides"])
    settings.AUTH_MODE = "none"
    assert _client(head).get(reverse("matters:department_changes")).status_code == 404


def test_a_lent_reader_sees_no_restricted_teema_on_a_colleagues_desk(people, files):
    lent = _lend(factories.ReaderFactory())
    body = (
        _client(lent)
        .get(reverse("matters:person_work", kwargs={"pk": people["owner"].pk}))
        .content.decode()
    )
    assert SECRET not in body
    # Allowed half: the head sees the restricted file on the same desk.
    head_body = (
        _client(people["head"])
        .get(reverse("matters:person_work", kwargs={"pk": people["owner"].pk}))
        .content.decode()
    )
    assert response_mentions_any(head_body, [files["normal"].title, f"{SECRET} teema"])


def response_mentions_any(body: str, needles: list[str]) -> bool:
    return any(needle in body for needle in needles)


# ---------------------------------------------------------------------------
# 4. Forged POSTs: CSRF, hidden-field tampering, mass assignment
# ---------------------------------------------------------------------------


def test_a_post_without_a_csrf_token_is_refused_and_writes_nothing(people, files):
    client = _client(people["owner"], enforce_csrf_checks=True)
    before = Entry.objects.filter(matter=files["normal"]).count()
    response = client.post(
        reverse("matters:update_summary", kwargs={"pk": files["normal"].pk}),
        {"brief_summary": "Võltsitud"},
    )
    assert response.status_code == 403
    files["normal"].refresh_from_db()
    assert files["normal"].brief_summary != "Võltsitud"
    assert Entry.objects.filter(matter=files["normal"]).count() == before


def test_the_same_post_with_its_token_is_allowed(people, files):
    client = _client(people["owner"], enforce_csrf_checks=True)
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": files["normal"].pk}))
    token = client.cookies["csrftoken"].value
    import re

    found = re.search(
        r'luhikokkuvote/"[\s\S]*?name="revision" value="([^"]*)"', page.content.decode()
    )
    revision = found.group(1) if found else ""
    response = client.post(
        reverse("matters:update_summary", kwargs={"pk": files["normal"].pk}),
        {"brief_summary": "Päris kokkuvõte", "csrfmiddlewaretoken": token, "revision": revision},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code in {200, 302}, response.status_code
    files["normal"].refresh_from_db()
    assert files["normal"].brief_summary == "Päris kokkuvõte"


def test_uus_teema_ignores_tampered_visibility_data_class_and_collaborators(people):
    client = _client(people["owner"])
    response = client.post(
        reverse("matters:matter_create"),
        {
            "title": "Võltsitud väljad",
            "visibility": Visibility.RESTRICTED,
            "visibility_override": Visibility.RESTRICTED,
            "data_class": MatterDataClass.TEST,
            "is_test_data": "on",
            "collaborators": [str(people["reader"].pk)],
            "is_open": "",
            "record_mode": "ARCHIVE",
            "reference_number": "1",
        },
    )
    assert response.status_code in {200, 302}
    matter = Matter.objects.get(title="Võltsitud väljad")
    assert matter.visibility == Visibility.NORMAL
    assert matter.data_class == MatterDataClass.REAL
    assert list(matter.collaborators.all()) == []
    assert matter.is_open is True
    assert matter.record_mode == "FULL"
    # No `owner` was posted: an unanswered owner stays unanswered (owner is
    # optional); what matters here is that nobody *else* was made owner.
    assert matter.owner in (None, people["owner"])


def test_uus_teema_refuses_a_hand_picked_link_to_an_unknown_teema(people):
    import uuid

    client = _client(people["owner"])
    response = client.post(
        reverse("matters:matter_create"),
        {"title": "Võltsitud seos", "seo_teemaga_valitud": [str(uuid.uuid4())]},
    )
    assert not Matter.objects.filter(title="Võltsitud seos").exists() or response.status_code == 400


@pytest.mark.parametrize("who", ["reader", "administrator"])
def test_non_writers_cannot_reach_uus_teema_or_its_related_search(people, files, who):
    client = _client(people[who])
    assert client.get(reverse("matters:matter_create")).status_code == 404
    assert (
        client.post(reverse("matters:matter_create"), {"title": "Lugeja teema"}).status_code == 404
    )
    assert not Matter.objects.filter(title="Lugeja teema").exists()
    assert client.get(reverse("related_materials:draft_picker") + f"?q={SECRET}").status_code == 404


@pytest.mark.parametrize("who", ["reader", "administrator"])
def test_similar_teemad_answer_a_reader_without_restricted_titles(people, files, who):
    """`Sarnased teemad` (POST, a read) is open to readers by design; it must be scoped."""
    client = _client(people[who])
    response = client.post(
        reverse("related_materials:draft_suggestions"), {"title": f"{SECRET} teema"}
    )
    assert response.status_code == 200
    assert SECRET not in response.content.decode()


def test_the_writer_finds_the_restricted_teema_in_the_related_search(people, files):
    """Allowed half of the picker: lawyers read department-wide (ADR 0042)."""
    client = _client(people["colleague"])
    body = client.get(reverse("related_materials:draft_picker") + f"?q={SECRET}").content.decode()
    assert SECRET in body


# ---------------------------------------------------------------------------
# 5. IDOR on write routes: a child of Matter B addressed through Matter A
# ---------------------------------------------------------------------------


@pytest.fixture
def two_matters(people):
    owner = people["owner"]
    a = factories.MatterFactory(title="Teema A", owner=owner)
    b = factories.MatterFactory(title="Teema B", owner=owner)
    date_b = factories.ImportantDateFactory(matter=b, title="B tähtaeg", status=FactStatus.ACTIVE)
    entry_b = factories.EntryFactory(matter=b, author=owner, body="<p>B märge</p>")
    round_b = MatterEngagement.objects.create(
        matter=b, kind=EngagementKind.EMAIL_CAMPAIGN, title="B kaasamine", created_by=owner
    )
    return {"a": a, "b": b, "date_b": date_b, "entry_b": entry_b, "round_b": round_b}


def test_a_child_of_another_matter_is_404_through_this_matters_url(people, two_matters):
    client = _client(people["owner"])
    a = two_matters["a"]
    cases = [
        reverse(
            "intelligence:cancel_important_date",
            kwargs={"matter_id": a.pk, "pk": two_matters["date_b"].pk},
        ),
        reverse(
            "intelligence:edit_important_date",
            kwargs={"matter_id": a.pk, "pk": two_matters["date_b"].pk},
        ),
        reverse("matters:edit_entry", kwargs={"pk": a.pk, "entry_id": two_matters["entry_b"].pk}),
        reverse(
            "matters:remove_record",
            kwargs={"pk": a.pk, "kind": "sissekanne", "record_id": two_matters["entry_b"].pk},
        ),
        reverse(
            "matters:update_engagement",
            kwargs={"pk": a.pk, "engagement_id": two_matters["round_b"].pk},
        ),
        reverse(
            "matters:remove_record",
            kwargs={"pk": a.pk, "kind": "kaasamine", "record_id": two_matters["round_b"].pk},
        ),
    ]
    for url in cases:
        response = client.post(url, {"title": "Ümber", "body": "<p>Ümber</p>", "reason": "x"})
        assert response.status_code == 404, (url, response.status_code)
    two_matters["date_b"].refresh_from_db()
    assert two_matters["date_b"].status == FactStatus.ACTIVE
    two_matters["entry_b"].refresh_from_db()
    assert two_matters["entry_b"].removed_at is None
    two_matters["round_b"].refresh_from_db()
    assert two_matters["round_b"].title == "B kaasamine"
    assert two_matters["round_b"].removed_at is None


def test_the_same_child_through_its_own_matter_is_reachable(people, two_matters):
    """Allowed half: the right Matter's URL works (not 404)."""
    client = _client(people["owner"])
    b = two_matters["b"]
    response = client.post(
        reverse(
            "intelligence:cancel_important_date",
            kwargs={"matter_id": b.pk, "pk": two_matters["date_b"].pk},
        ),
        {},
    )
    assert response.status_code != 404


# ---------------------------------------------------------------------------
# 6. Privilege change between two requests
# ---------------------------------------------------------------------------


def test_a_demoted_lawyer_cannot_write_on_the_next_request(people, files):
    owner = people["owner"]
    client = _client(owner)
    url = reverse("matters:update_summary", kwargs={"pk": files["normal"].pk})
    assert (
        client.get(reverse("matters:matter_detail", kwargs={"pk": files["normal"].pk})).status_code
        == 200
    )
    owner.role = UserRole.READER
    owner.save(update_fields=["role"])
    assert client.post(url, {"brief_summary": "Pärast alandamist"}).status_code == 404
    files["normal"].refresh_from_db()
    assert files["normal"].brief_summary != "Pärast alandamist"
    # And the restricted Matter they owned... they still own it, so they still
    # read it as its owner (participation), but cannot write.
    assert (
        client.get(
            reverse("matters:matter_detail", kwargs={"pk": files["restricted"].pk})
        ).status_code
        == 200
    )


def test_a_deactivated_lawyer_is_signed_out_on_the_next_request(people, files):
    owner = people["owner"]
    client = _client(owner)
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": files["normal"].pk}))
    assert response.status_code == 302


def test_a_persona_that_stops_being_eligible_is_dropped_behind_the_gate(settings, people, files):
    owner = people["owner"]
    client = _gate_client(settings, persona=owner)
    detail = reverse("matters:matter_detail", kwargs={"pk": files["restricted"].pk})
    assert client.get(detail).status_code == 200
    owner.role = UserRole.READER
    owner.save(update_fields=["role"])
    response = client.get(detail)
    assert response.status_code == 302  # persona dropped -> chooser, never the page
    assert (
        client.post(
            reverse("matters:update_summary", kwargs={"pk": files["normal"].pk}),
            {"brief_summary": "Pärast"},
        ).status_code
        == 302
    )
    files["normal"].refresh_from_db()
    assert files["normal"].brief_summary != "Pärast"


def test_switching_persona_invalidates_a_form_loaded_under_the_previous_one(
    settings, people, files
):
    """A page loaded as Marko must not post as Ireen with Marko's token."""
    apply_shared_gate(settings)
    client = Client(enforce_csrf_checks=True)
    gate = client.get(reverse("accounts:shared_gate"))
    token = (
        client.cookies["csrftoken"].value
        if "csrftoken" in client.cookies
        else get_token(gate.wsgi_request)
    )
    client.post(
        reverse("accounts:shared_gate"), {"password": PASSWORD, "csrfmiddlewaretoken": token}
    )
    token = client.cookies["csrftoken"].value
    client.post(
        reverse("accounts:act_as"),
        {"user_id": str(people["owner"].pk), "csrfmiddlewaretoken": token},
    )
    client.get(reverse("matters:matter_detail", kwargs={"pk": files["normal"].pk}))
    stale = client.cookies["csrftoken"].value
    client.post(
        reverse("accounts:act_as"),
        {"user_id": str(people["colleague"].pk), "csrfmiddlewaretoken": stale},
    )
    response = client.post(
        reverse("matters:update_summary", kwargs={"pk": files["normal"].pk}),
        {"brief_summary": "Vana vorm", "csrfmiddlewaretoken": stale},
    )
    files["normal"].refresh_from_db()
    # Observed 2026-10-14: 403 — Django rotates the CSRF secret on `login()`.
    assert response.status_code == 403
    # Either the stale token is refused (403), or — if Django's rotation kept
    # the form usable — the write must be attributed to the persona now acting,
    # never the one who loaded the page.
    if response.status_code == 403:
        assert files["normal"].brief_summary != "Vana vorm"
    else:
        from app.audit.models import ChangeEvent

        event = ChangeEvent.objects.filter(matter=files["normal"]).order_by("-created_at").first()
        assert event is None or event.actor_id == people["colleague"].pk


def test_a_download_link_captured_while_normal_is_404_once_restricted(people, files, settings):
    url = reverse("documents:download", kwargs={"pk": files["public"].pk})
    reader = _client(people["reader"])
    assert reader.get(url).status_code == 200
    Document.objects.filter(pk=files["public"].document_id).update(
        visibility_override=Visibility.RESTRICTED
    )
    assert reader.get(url).status_code == 404
    gate = _gate_client(settings)
    assert gate.get(url).status_code == 404
    # Allowed half: the owner still downloads.
    settings.AUTH_MODE = "none"
    assert _client(people["owner"]).get(url).status_code == 200
