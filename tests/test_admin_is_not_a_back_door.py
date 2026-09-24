"""Django admin reads what the product lets its user read, and writes no business record.

The Stage-0 admin registered `Matter`, `Document`, tag assignments, break-glass
grants and people with Django's default add and change forms. Once the product's
services took over every business write, those forms were a second way in that
skipped `visible_to`, the Matter lock, the domain audit and every invariant the
services keep (ENG-008). A superuser could read a RESTRICTED Matter the product
answered 404 for, declassify it, mint a ten-year break-glass grant for a READER
and make them a department head — with nothing in `ChangeEvent` or
`SecurityAuditEvent`.

What is pinned here:

* every business record is read-only in the admin, whoever the superuser is;
* what the admin lists is what the product would show that person — a
  restricted Matter, its documents, their filenames and its audit rows appear
  only through a real right to read them (a break-glass grant, for instance);
* people: only the display name and the active flag change here; a grant is
  never minted here;
* the reference data the admin is the supported editor for still edits
  (docs/adr/0041 §6), and tombstones are still listed (docs/adr/0096 §4.2).
"""

from __future__ import annotations

import datetime as dt
from html.parser import HTMLParser

import pytest
from django.utils import timezone

from app.accounts.enums import AuthMode, UserRole
from app.accounts.models import BreakGlassGrant, User
from app.accounts.services import grant_break_glass
from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.core.enums import Visibility
from app.documents.models import Document
from app.matters.models import Matter
from app.organisations.models import Organisation
from app.taxonomy.models import Tag
from tests import factories

pytestmark = pytest.mark.django_db

HIDDEN_TITLE = "Konfidentsiaalne liikmete tagasiside"
HIDDEN_FILE = "salajane-liikmete-kiri.pdf"


class _FormFields(HTMLParser):
    """Every submittable value on an admin form, the way a browser would send it."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, list[str]] = {}
        self._select: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        name = a.get("name")
        if tag == "input" and name and a.get("type") not in {"submit", "button", "file"}:
            if a.get("type") in {"checkbox", "radio"} and "checked" not in a:
                return
            default = "on" if a.get("type") in {"checkbox", "radio"} else ""
            self.data.setdefault(name, []).append(a.get("value", default))
        elif tag == "select" and name:
            self._select = name
            self.data.setdefault(name, [])
        elif tag == "option" and self._select and "selected" in a:
            self.data[self._select].append(a.get("value", ""))
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = [""]

    def handle_data(self, data: str) -> None:
        if self._textarea:
            self.data[self._textarea][0] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None


def _form(client, url: str) -> dict[str, list[str]]:
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    parser = _FormFields()
    parser.feed(response.content.decode())
    return parser.data


@pytest.fixture
def admin_client(client, superuser):
    client.force_login(superuser)
    return client


@pytest.fixture
def secret(specialist):
    return factories.MatterFactory(
        owner=specialist, visibility=Visibility.RESTRICTED, title=HIDDEN_TITLE
    )


@pytest.fixture
def ordinary(specialist):
    return factories.MatterFactory(owner=specialist, title="Tavaline avatud teema")


# -- no business write ---------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("visibility", Visibility.NORMAL),  # declassify
        ("is_open", ""),  # close, outside close_matter
        ("reference_number", "999"),  # human reference allocation
        ("title", "Ümber nimetatud admini kaudu"),
    ],
)
def test_a_matter_cannot_be_changed_through_the_admin(admin_client, ordinary, field, value):
    before = Matter.all_objects.values().get(pk=ordinary.pk)

    response = admin_client.post(
        f"/admin/matters/matter/{ordinary.pk}/change/", {field: value, "title": "x"}
    )

    assert response.status_code == 403
    assert Matter.all_objects.values().get(pk=ordinary.pk) == before


def test_a_restricted_matter_cannot_be_declassified_through_the_admin(admin_client, secret):
    admin_client.post(
        f"/admin/matters/matter/{secret.pk}/change/", {"visibility": Visibility.NORMAL}
    )

    secret.refresh_from_db()
    assert secret.visibility == Visibility.RESTRICTED
    assert (
        not ChangeEvent.objects.filter(matter=secret).exclude(event_type="MATTER_CREATED").exists()
    )


def test_no_matter_is_created_through_the_admin(admin_client):
    assert admin_client.get("/admin/matters/matter/add/").status_code == 403
    before = Matter.all_objects.count()
    admin_client.post("/admin/matters/matter/add/", {"title": "Admini teema"})
    assert Matter.all_objects.count() == before


def test_a_document_cannot_be_moved_to_another_matter(admin_client, ordinary, specialist):
    document = factories.DocumentFactory(matter=ordinary)
    other = factories.MatterFactory(owner=specialist)

    response = admin_client.post(
        f"/admin/documents/document/{document.pk}/change/", {"matter": str(other.pk)}
    )

    assert response.status_code == 403
    document.refresh_from_db()
    assert document.matter_id == ordinary.pk
    assert admin_client.get("/admin/documents/document/add/").status_code == 403


def test_a_tag_assignment_is_not_written_through_the_admin(admin_client):
    assert admin_client.get("/admin/matters/tagassignment/add/").status_code == 403


def test_a_tombstone_is_listed_and_cannot_be_edited(admin_client, specialist):
    from app.matters.deletion import delete_matter

    matter = factories.MatterFactory(owner=specialist, title="Kustutatud teema")
    delete_matter(matter=matter, actor=specialist)

    listing = admin_client.get("/admin/matters/matter/").content.decode()
    assert "Kustutatud teema" in listing
    assert admin_client.get(f"/admin/matters/matter/{matter.pk}/change/").status_code == 200

    response = admin_client.post(
        f"/admin/matters/matter/{matter.pk}/change/", {"title": "Ülestõusnud", "deleted_at": ""}
    )

    assert response.status_code == 403
    tombstone = Matter.all_objects.get(pk=matter.pk)
    assert tombstone.deleted_at is not None
    assert tombstone.title == "Kustutatud teema"


def test_a_role_or_privilege_cannot_be_raised_through_the_admin(admin_client, reader):
    url = f"/admin/accounts/user/{reader.pk}/change/"
    data = _form(admin_client, url)
    assert "role" not in data and "is_superuser" not in data and "is_staff" not in data
    assert "upn" not in data

    admin_client.post(
        url,
        {
            **data,
            "display_name": "Lugeja Uus Nimi",
            "role": UserRole.DEPARTMENT_HEAD,
            "is_superuser": "on",
            "is_staff": "on",
            "upn": "keegi.teine@example.invalid",
        },
    )

    reader.refresh_from_db()
    assert reader.display_name == "Lugeja Uus Nimi"  # the supported rename still works
    assert reader.role == UserRole.READER
    assert not reader.is_superuser and not reader.is_staff
    assert reader.upn != "keegi.teine@example.invalid"


def test_no_person_is_created_through_the_admin(admin_client):
    assert admin_client.get("/admin/accounts/user/add/").status_code == 403


def test_no_break_glass_grant_is_minted_through_the_admin(admin_client, superuser, reader):
    now = timezone.now()
    assert admin_client.get("/admin/accounts/breakglassgrant/add/").status_code == 403

    admin_client.post(
        "/admin/accounts/breakglassgrant/add/",
        {
            "user": str(reader.pk),
            "granted_by": str(superuser.pk),
            "reason": "kümme aastat",
            "starts_at_0": now.strftime("%Y-%m-%d"),
            "starts_at_1": "00:00:00",
            "expires_at_0": (now + dt.timedelta(days=3650)).strftime("%Y-%m-%d"),
            "expires_at_1": "00:00:00",
        },
    )

    assert not BreakGlassGrant.objects.exists()


# -- no restricted read --------------------------------------------------------


@pytest.fixture
def secret_world(secret, capture_evidence, specialist):
    """A restricted Matter with a file and an audit row, and a readable one beside it."""
    capture_evidence(secret, b"%PDF-1.4 salajane", HIDDEN_FILE, "application/pdf")
    ChangeEvent.objects.create(
        matter=secret,
        event_type="MATTER_UPDATED",
        summary=f"Muudeti: {HIDDEN_TITLE}",
        actor=specialist,
    )
    return secret


ADMIN_PAGES = (
    "/admin/matters/matter/",
    "/admin/documents/document/",
    "/admin/documents/documentversion/",
    "/admin/audit/changeevent/",
    "/admin/matters/tagassignment/",
)


def test_the_admin_lists_nothing_restricted(admin_client, secret_world, ordinary):
    for page in ADMIN_PAGES:
        body = admin_client.get(page).content.decode()
        assert HIDDEN_TITLE not in body, page
        assert HIDDEN_FILE not in body, page
    assert "Tavaline avatud teema" in admin_client.get(ADMIN_PAGES[0]).content.decode()


def test_the_admin_opens_no_restricted_record(admin_client, secret_world):
    document = Document.objects.get(matter=secret_world)
    event = ChangeEvent.objects.get(matter=secret_world, event_type="MATTER_UPDATED")

    for url in (
        f"/admin/matters/matter/{secret_world.pk}/change/",
        f"/admin/documents/document/{document.pk}/change/",
        f"/admin/documents/documentversion/{document.current_version_id}/change/",
        f"/admin/audit/changeevent/{event.pk}/change/",
    ):
        response = admin_client.get(url, follow=True)
        body = response.content.decode()
        assert HIDDEN_TITLE not in body, url
        assert HIDDEN_FILE not in body, url


def test_a_break_glass_grant_is_what_opens_it(admin_client, superuser, department_head, secret):
    """The admin follows the product's rule rather than ignoring it either way."""
    grant_break_glass(
        user=superuser,
        granted_by=department_head,
        reason="tõrke uurimine",
        duration=dt.timedelta(hours=2),
    )

    body = admin_client.get("/admin/matters/matter/").content.decode()

    assert HIDDEN_TITLE in body


def test_a_signed_in_superuser_under_cloudflare_access_reads_nothing_restricted(
    client, settings, monkeypatch, secret_world
):
    """The mode the finding is about: Access signs any active real account in."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from django.core.cache import cache

    from app.accounts import cloudflare_access
    from tests.test_cloudflare_access import AUDIENCE, TEAM_DOMAIN, assertion

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.AUTH_MODE = AuthMode.CLOUDFLARE_ACCESS
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM_DOMAIN
    settings.CF_ACCESS_AUDIENCE = AUDIENCE
    settings.DEV_LOGIN_ENABLED = False
    cache.clear()
    published = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    published.update({"kid": "current", "alg": "RS256", "use": "sig"})
    monkeypatch.setattr(cloudflare_access, "_fetch_jwks", lambda: {"keys": [published]})

    User.objects.create_user(
        upn="tehniline@naidiskoda.invalid",
        display_name="Tehniline haldur",
        role=UserRole.ADMINISTRATOR,
        is_staff=True,
        is_superuser=True,
        is_synthetic=False,
    )
    token = assertion(key, email="tehniline@naidiskoda.invalid")

    for page in ADMIN_PAGES:
        response = client.get(page, HTTP_CF_ACCESS_JWT_ASSERTION=token)
        assert response.status_code == 200, page
        assert HIDDEN_TITLE not in response.content.decode(), page
        assert HIDDEN_FILE not in response.content.decode(), page
    cache.clear()


def test_security_audit_rows_are_still_listed(admin_client, superuser):
    SecurityAuditEvent.objects.create(event_type="PERSONA_SELECTED", actor=superuser)
    assert admin_client.get("/admin/audit/securityauditevent/").status_code == 200


# -- reference data stays maintainable -----------------------------------------


def test_an_organisation_is_still_renamed_through_the_admin(admin_client):
    organisation = factories.OrganisationFactory(name="Vana nimi")
    url = f"/admin/organisations/organisation/{organisation.pk}/change/"
    data = _form(admin_client, url)

    response = admin_client.post(url, {**data, "name": "Uus nimi"})

    assert response.status_code == 302, response.content.decode()[:2000]
    assert Organisation.objects.get(pk=organisation.pk).name == "Uus nimi"


def test_a_tag_is_still_renamed_through_the_admin(admin_client):
    tag = factories.TagFactory(name_et="Vana silt")
    url = f"/admin/taxonomy/tag/{tag.pk}/change/"
    data = _form(admin_client, url)

    response = admin_client.post(url, {**data, "name_et": "Uus silt"})

    assert response.status_code == 302, response.content.decode()[:2000]
    assert Tag.objects.get(pk=tag.pk).name_et == "Uus silt"
