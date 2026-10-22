"""One internal read policy (docs/adr/0181): inside reads everything, outside reads nothing.

The owner's rule, tested as a matrix of *who* against *what*:

* **Inside** — an active account acting as SPECIALIST (the owner, and a
  colleague who is neither owner nor collaborator), DEPARTMENT_HEAD or READER.
  They must get **identical** read results, RESTRICTED material included.
* **Outside** — anonymous, an inactive account, ADMINISTRATOR and a superuser
  without a business role, the shared-gate session with no persona, and a gate
  session that has aged out. They must get **none** of it: no row, no count, no
  search fragment, no file, no thumbnail, no historical page or its XML, no CSV.

The world is the one the round-2 scan named (csf_0e7751e7…, csf_a37ab56b…): a
NORMAL Matter carrying a previously RESTRICTED document and entry, a RESTRICTED
email with an attachment, a RESTRICTED Matter, and a historical OneNote page
whose files include a restricted one. Under the policy those are not internal
breaches; these tests prove the two halves that are left.

Read is not write: the last block proves no write, review or administrative
capability moved.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import shared_gate
from app.accounts.enums import UserRole
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.core.authorization import (
    DEPARTMENT_VIEWER,
    NOTHING,
    ROLES_WITH_BUSINESS_READ,
    ROLES_WITH_BUSINESS_WRITE,
    child_visibility_q,
    matter_visibility_q,
    may_read_business_content,
    may_review_work_victory,
    may_write_business_content,
    scope_for_user,
)
from app.core.enums import Visibility
from app.documents.derivatives import (
    DerivativeKind,
    DerivativeStatus,
    DocumentDerivative,
    EmailAttachmentLink,
)
from app.documents.models import Document, DocumentVersion
from app.legacy_import.opinion_access import may_read_archive
from app.matters.models import Entry, Matter
from app.reporting.context import ReportingContext, parse_period
from app.reporting.selectors import historical
from app.search.services import search_documents
from app.workflow.models import NextAction
from tests import factories
from tests import synthetic_corpus as corpus
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
EML = "message/rfc822"

NORMAL_TITLE = "AVALIK-TEEMA-4417"
RESTRICTED_TITLE = "PIIRATUD-TEEMA-4418"
RESTRICTED_CHILD_FILE = "piiratud-lisa-4419.pdf"
RESTRICTED_ENTRY = "PIIRATUD-SISSEKANNE-4420"
HISTORICAL_TITLE = "AJALOOLINE-LEHT-4421"
HISTORICAL_RESTRICTED_FILE = "ajalooline-piiratud-4422.pdf"
HISTORICAL_XML = b"<one:Page>AJALOOLINE-XML-4423</one:Page>"

#: Words that exist only on business records. Nothing outside may receive one.
TOKENS = (
    NORMAL_TITLE,
    RESTRICTED_TITLE,
    RESTRICTED_CHILD_FILE,
    RESTRICTED_ENTRY,
    HISTORICAL_TITLE,
    HISTORICAL_RESTRICTED_FILE,
    "AJALOOLINE-XML-4423",
    corpus.ONLY_IN_ATTACHMENT,
    corpus.ONLY_IN_EMAIL_BODY,
)

INSIDE = ("owner", "colleague", "department_head", "reader")
OUTSIDE = ("anonymous", "inactive", "administrator", "superuser", "department_viewer")


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


def _thumbnail(version: DocumentVersion) -> DocumentDerivative:
    from app.documents.extraction.orchestrator import derivative_storage

    existing = (
        DocumentDerivative.objects.filter(
            version=version, kind=DerivativeKind.THUMBNAIL, status=DerivativeStatus.ACTIVE
        )
        .exclude(storage_key="")
        .first()
    )
    if existing is not None:
        return existing
    key = derivative_storage().save(
        f"policy/{uuid.uuid4().hex}.png", ContentFile(b"\x89PNG\r\n\x1a\npolicy")
    )
    return DocumentDerivative.objects.create(
        version=version,
        kind=DerivativeKind.THUMBNAIL,
        generator="policy",
        generator_version="1",
        status=DerivativeStatus.ACTIVE,
        storage_key=key,
    )


def _historical_page(matter: Matter, capture_evidence: Any) -> Any:
    """A OneNote page on ``matter`` with a NORMAL and a RESTRICTED file, and its XML."""
    from app.legacy_import.historical_apply import legacy_source_storage
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        LegacySourceResource,
        LegacySourceResourceImport,
        MatterSourcePage,
    )

    now = timezone.now()
    key = legacy_source_storage().save(
        f"policy/{uuid.uuid4().hex}.xml", ContentFile(HISTORICAL_XML)
    )
    page = LegacySourcePage.objects.create(
        source_page_id="{policy-page}",
        page_key="policy-page",
        source_notebook="Õigusloome",
        source_section="2019",
        capture_id="policy",
        title=HISTORICAL_TITLE,
        blocks=[
            {"kind": "FILE_ATTACHMENT", "resource_key": "avalik", "ordinal": 1},
            {"kind": "FILE_ATTACHMENT", "resource_key": "piiratud", "ordinal": 2},
        ],
        source_xml_storage_key=key,
        first_imported_at=now,
        latest_imported_at=now,
    )
    link = MatterSourcePage.objects.create(matter=matter, source_page=page, match_method="EXACT")
    for resource_key, filename, override in (
        ("avalik", "ajalooline-avalik.pdf", ""),
        ("piiratud", HISTORICAL_RESTRICTED_FILE, Visibility.RESTRICTED),
    ):
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key=resource_key,
            original_filename=filename,
            size_bytes=4321,
            archive_relative_path=f"files/{filename}",
        )
        version = capture_evidence(
            matter,
            b"%PDF-1.4 " + resource_key.encode(),
            filename,
            PDF,
            visibility_override=override,
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=version.document,
            document_version=version,
        )
    return link


@pytest.fixture
def world(capture_evidence, extract) -> dict[str, Any]:
    owner = factories.UserFactory(display_name="Omanik Olev")
    people = {
        "owner": owner,
        "colleague": factories.UserFactory(display_name="Kolleeg Kati"),
        "department_head": factories.DepartmentHeadFactory(display_name="Juhataja Jaan"),
        "reader": factories.ReaderFactory(display_name="Lugeja Liis"),
        "inactive": factories.UserFactory(display_name="Lahkunud Lauri", is_active=False),
        "administrator": factories.AdministratorFactory(display_name="Haldur Hugo"),
        "superuser": factories.AdministratorFactory(
            display_name="Superkasutaja Silver", is_superuser=True
        ),
    }

    normal = factories.MatterFactory(owner=owner, title=NORMAL_TITLE)
    restricted = factories.MatterFactory(
        owner=owner, title=RESTRICTED_TITLE, visibility=Visibility.RESTRICTED
    )

    # A NORMAL parent with a previously RESTRICTED child: a document and an entry.
    restricted_child = capture_evidence(
        normal,
        b"%PDF-1.4 piiratud lisa",
        RESTRICTED_CHILD_FILE,
        PDF,
        visibility_override=Visibility.RESTRICTED,
    )
    factories.EntryFactory(
        matter=normal,
        author=owner,
        body=f"<p>{RESTRICTED_ENTRY}</p>",
        visibility_override=Visibility.RESTRICTED,
    )
    factories.EntryFactory(matter=restricted, author=owner, body="<p>Piiratud teema märge.</p>")
    factories.NextActionFactory(matter=restricted, responsible=owner)

    # A RESTRICTED email on the NORMAL Matter, extracted, with its attachment
    # extracted too — the attachment row of csf_0e7751e7….
    email = capture_evidence(
        normal,
        corpus.consultation_eml(),
        "konsultatsioon.eml",
        EML,
        visibility_override=Visibility.RESTRICTED,
    )
    extract(email)
    links = EmailAttachmentLink.objects.filter(parent_version=email).select_related(
        "attachment_version"
    )
    for row in links:
        extract(row.attachment_version)
    attachment = links.get(
        attachment_version__original_filename__endswith=".pdf"
    ).attachment_version
    # Each attachment was created carrying its email's restriction (0137); the
    # scan's case is one that did not, so this one is made to have lost it.
    assert attachment.document.visibility_override == Visibility.RESTRICTED
    Document.objects.filter(pk=attachment.document_id).update(visibility_override="")
    attachment.refresh_from_db()

    link = _historical_page(restricted, capture_evidence)

    return {
        **people,
        "normal": normal,
        "restricted": restricted,
        "restricted_child": restricted_child,
        "email": email,
        "attachment": attachment,
        "thumbnail": _thumbnail(attachment),
        "source_link": link,
    }


def _viewer(world: dict[str, Any], name: str) -> Any:
    if name == "anonymous":
        return AnonymousUser()
    if name == "department_viewer":
        return DEPARTMENT_VIEWER
    return world[name]


def _reads(viewer: Any) -> dict[str, set[Any]]:
    """Everything ``viewer`` may read, family by family, through the real managers."""
    context = ReportingContext(
        viewer=viewer,
        period=parse_period("koik", timezone.localdate()),
        today=timezone.localdate(),
        now=timezone.now(),
    )
    return {
        "matters": set(Matter.objects.visible_to(viewer).values_list("pk", flat=True)),
        "documents": set(Document.objects.visible_to(viewer).values_list("pk", flat=True)),
        "entries": set(Entry.objects.visible_to(viewer).values_list("pk", flat=True)),
        "actions": set(NextAction.objects.visible_to(viewer).values_list("pk", flat=True)),
        "events": set(
            scope_change_events(ChangeEvent.objects.all(), viewer).values_list("pk", flat=True)
        ),
        "attachment_search": set(
            search_documents(query=corpus.ONLY_IN_ATTACHMENT, user=viewer).values_list(
                "pk", flat=True
            )
        ),
        "email_search": set(
            search_documents(query=corpus.ONLY_IN_EMAIL_BODY, user=viewer).values_list(
                "pk", flat=True
            )
        ),
        "historical_pages": set(historical.visible_pages(context).values_list("pk", flat=True)),
        "historical_resources": set(
            historical.visible_resources(context).values_list("pk", flat=True)
        ),
        "historical_counts": {
            ("pages", historical.legacy_source_pages(context).value),
            ("occurrences", historical.historical_resource_occurrences(context).value),
            ("bytes", historical.historical_resource_bytes(context).value),
        },
    }


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def test_the_business_read_roles_are_the_three_business_roles():
    assert ROLES_WITH_BUSINESS_READ == frozenset(
        {UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value, UserRole.READER.value}
    )
    # Read is not write: READER reads and still writes nothing.
    assert UserRole.READER.value not in ROLES_WITH_BUSINESS_WRITE
    assert UserRole.ADMINISTRATOR.value not in ROLES_WITH_BUSINESS_READ


@pytest.mark.parametrize("name", INSIDE)
def test_every_authorized_internal_user_may_read(world, name):
    viewer = world[name]
    assert may_read_business_content(viewer)
    assert may_read_archive(viewer)
    scope = scope_for_user(viewer)
    assert scope.is_authenticated and scope.sees_all_restricted


@pytest.mark.parametrize("name", OUTSIDE)
def test_nobody_else_may_read(world, name):
    viewer = _viewer(world, name)
    assert not may_read_business_content(viewer)
    assert not may_read_archive(viewer)
    scope = scope_for_user(viewer)
    assert not scope.is_authenticated
    assert matter_visibility_q(scope) == NOTHING
    assert child_visibility_q(scope) == NOTHING


def test_a_superuser_with_a_business_role_reads_by_the_role_not_the_flag(world):
    lawyer_superuser = factories.UserFactory(is_superuser=True, is_staff=True)
    assert may_read_business_content(lawyer_superuser)
    unknown_role = factories.UserFactory(role="KONSULTANT")
    assert not may_read_business_content(unknown_role)


# ---------------------------------------------------------------------------
# Identical reads inside, nothing outside — through every read family
# ---------------------------------------------------------------------------


def test_every_authorized_internal_user_reads_the_same_everything(world):
    reads = {name: _reads(world[name]) for name in INSIDE}

    owner = reads["owner"]
    for name in INSIDE:
        assert reads[name] == owner, name

    # …and "everything" is everything: both Matters, the restricted child, the
    # restricted email and its attachment, the restricted entry, the
    # historical page with both of its files.
    assert owner["matters"] == {world["normal"].pk, world["restricted"].pk}
    assert set(Document.objects.values_list("pk", flat=True)) == owner["documents"]
    assert {
        world["restricted_child"].document_id,
        world["email"].document_id,
        world["attachment"].document_id,
    } <= owner["documents"]
    assert set(Entry.objects.values_list("pk", flat=True)) == owner["entries"]
    assert owner["attachment_search"] and owner["email_search"]
    assert owner["historical_pages"] == {world["source_link"].source_page_id}
    assert len(owner["historical_resources"]) == 2


@pytest.mark.parametrize("name", OUTSIDE)
def test_nobody_else_reads_anything_at_all(world, name):
    reads = _reads(_viewer(world, name))

    counts = reads.pop("historical_counts")
    assert all(value == 0 for _, value in counts), counts
    assert reads == {family: set() for family in reads}, name


def test_a_break_glass_grant_still_opens_everything_and_only_while_it_lasts(world):
    from app.accounts.services import grant_break_glass

    administrator = world["administrator"]
    assert _reads(administrator)["matters"] == set()

    grant = grant_break_glass(
        user=administrator,
        granted_by=world["department_head"],
        reason="Andmebaasi parandus",
        duration=timedelta(hours=1),
    )
    assert may_read_business_content(administrator)
    assert may_read_archive(administrator)
    assert _reads(administrator) == _reads(world["owner"])
    # A grant is sight, never authorship.
    assert not may_write_business_content(administrator)

    grant.revoked_at = timezone.now()
    grant.save(update_fields=["revoked_at"])
    assert not may_read_business_content(administrator)
    assert _reads(administrator)["matters"] == set()


# ---------------------------------------------------------------------------
# The same, over HTTP: pages, files, thumbnails, the historical page and XML,
# the Statistika historical tab and the materials CSV, search
# ---------------------------------------------------------------------------


def _urls(world: dict[str, Any]) -> dict[str, str]:
    return {
        "normal": reverse("matters:matter_detail", kwargs={"pk": world["normal"].pk}),
        "restricted": reverse("matters:matter_detail", kwargs={"pk": world["restricted"].pk}),
        "documents": reverse("matters:matter_documents", kwargs={"pk": world["normal"].pk}),
        "child_download": reverse(
            "documents:download", kwargs={"pk": world["restricted_child"].pk}
        ),
        "attachment_download": reverse("documents:download", kwargs={"pk": world["attachment"].pk}),
        "attachment_open": reverse("documents:open", kwargs={"pk": world["attachment"].pk}),
        "attachment_detail": reverse(
            "documents:document_detail", kwargs={"pk": world["attachment"].document_id}
        ),
        "thumbnail": reverse("documents:thumbnail", kwargs={"pk": world["thumbnail"].pk}),
        "source_page": reverse("legacy_import:source_page", kwargs={"pk": world["source_link"].pk}),
        "source_xml": reverse("legacy_import:source_xml", kwargs={"pk": world["source_link"].pk}),
        "historical": reverse("reporting:historical") + "?periood=koik",
        "materials_csv": reverse("reporting:export", kwargs={"slug": "materjalid"}),
        "matters_csv": reverse("reporting:export", kwargs={"slug": "teemad"}) + "?periood=koik",
        "search": reverse("search:search") + f"?q={corpus.ONLY_IN_ATTACHMENT}",
    }


#: The routes that hand over bytes; for everybody outside they answer no 200.
FILE_URLS = ("child_download", "attachment_download", "attachment_open", "thumbnail", "source_xml")


def _body(response: Any) -> str:
    if getattr(response, "streaming", False):
        raw = b"".join(response.streaming_content)
    else:
        raw = response.content
    return raw.decode("utf-8", errors="replace")


def _signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.parametrize("name", INSIDE)
def test_every_authorized_internal_user_opens_every_surface(world, name):
    client = _signed_in(world[name])
    urls = _urls(world)
    for key, url in urls.items():
        response = client.get(url)
        assert response.status_code == 200, (name, key, response.status_code)
    assert RESTRICTED_TITLE in _body(client.get(urls["restricted"]))
    assert RESTRICTED_CHILD_FILE in _body(client.get(urls["documents"]))
    assert HISTORICAL_RESTRICTED_FILE in _body(client.get(urls["source_page"]))
    assert "AJALOOLINE-XML-4423" in _body(client.get(urls["source_xml"]))
    assert RESTRICTED_TITLE in _body(client.get(urls["matters_csv"]))
    assert world["attachment"].document.title in _body(client.get(urls["search"]))


def _assert_learns_nothing(client: Client, world: dict[str, Any], *, refusal: Any) -> None:
    for key, url in _urls(world).items():
        response = client.get(url)
        body = _body(response)
        # The search page repeats the words it was asked for; what it must not
        # do is answer them.
        tokens = [token for token in TOKENS if not (key == "search" and token in url)]
        assert not [token for token in tokens if token in body], (key, response.status_code)
        if key in FILE_URLS:
            assert response.status_code != 200, key
        refusal(key, response)


def test_anonymous_learns_nothing(world):
    login = reverse("accounts:dev_login")

    def refusal(key: str, response: Any) -> None:
        assert response.status_code == 302 and response["Location"].startswith(login), key

    _assert_learns_nothing(Client(), world, refusal=refusal)


def test_an_inactive_account_learns_nothing(world):
    login = reverse("accounts:dev_login")

    def refusal(key: str, response: Any) -> None:
        assert response.status_code == 302 and response["Location"].startswith(login), key

    _assert_learns_nothing(_signed_in(world["inactive"]), world, refusal=refusal)


@pytest.mark.parametrize("name", ["administrator", "superuser"])
def test_technical_administration_without_a_business_role_learns_nothing(world, name):
    def refusal(key: str, response: Any) -> None:
        # Signed in, so not redirected: an empty page, an empty CSV or a 404 —
        # never a record, never bytes.
        assert response.status_code in (200, 404), (key, response.status_code)

    _assert_learns_nothing(_signed_in(world[name]), world, refusal=refusal)


def test_the_shared_gate_before_a_persona_learns_nothing(world, settings):
    apply_shared_gate(settings)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    chooser = reverse("accounts:choose_persona")

    def refusal(key: str, response: Any) -> None:
        assert response.status_code == 302 and response["Location"].startswith(chooser), key

    _assert_learns_nothing(client, world, refusal=refusal)


def test_an_aged_out_gate_session_learns_nothing_even_with_a_persona(world, settings):
    apply_shared_gate(settings)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    client.post(reverse("accounts:act_as"), {"user_id": str(world["owner"].pk)})
    assert client.get(_urls(world)["restricted"]).status_code == 200

    session = client.session
    session[shared_gate.GATE_PASSED_AT] = (timezone.now() - timedelta(hours=13)).isoformat()
    session.save()
    gate = reverse("accounts:shared_gate")

    def refusal(key: str, response: Any) -> None:
        assert response.status_code == 302 and response["Location"].startswith(gate), key

    _assert_learns_nothing(client, world, refusal=refusal)


def test_an_invalid_gate_stamp_learns_nothing(world, settings):
    apply_shared_gate(settings)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    client.post(reverse("accounts:act_as"), {"user_id": str(world["owner"].pk)})
    session = client.session
    session[shared_gate.GATE_PASSED_AT] = "eile"
    session.save()
    gate = reverse("accounts:shared_gate")

    def refusal(key: str, response: Any) -> None:
        assert response.status_code == 302 and response["Location"].startswith(gate), key

    _assert_learns_nothing(client, world, refusal=refusal)


def test_every_persona_behind_the_gate_reads_the_same(world, settings):
    """The persona chooses whose desk is shown, never what may be read (csf_5e02eab0…)."""
    apply_shared_gate(settings)
    client = Client()
    assert client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    url = _urls(world)["restricted"]
    for name in ("owner", "colleague", "department_head"):
        client.post(reverse("accounts:act_as"), {"user_id": str(world[name].pk)})
        assert client.get(url).status_code == 200, name
        assert client.get(_urls(world)["attachment_download"]).status_code == 200, name


# ---------------------------------------------------------------------------
# Read is not write
# ---------------------------------------------------------------------------


def test_reading_everything_is_not_writing_anything(world, pdf_bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    reader = world["reader"]
    assert not may_write_business_content(reader)
    assert not may_review_work_victory(reader)
    assert may_write_business_content(world["colleague"])
    # A specialist who reads every restricted file still cannot review a Töövõit.
    assert not may_review_work_victory(world["colleague"])
    assert may_review_work_victory(world["department_head"])

    documents_before = Document.objects.count()
    response = _signed_in(reader).post(
        reverse("documents:upload_evidence", kwargs={"matter_id": world["restricted"].pk}),
        {
            "role": "INCOMING_AUTHORITY",
            "upload": SimpleUploadedFile("lugeja.pdf", pdf_bytes, content_type=PDF),
        },
    )
    assert response.status_code == 404
    assert Document.objects.count() == documents_before


# ---------------------------------------------------------------------------
# The administrator's reconciliation queues show business content
# ---------------------------------------------------------------------------

QUEUE_PAGE_TITLE = "AJALOOLINE-ULEVAATUS-4431"
QUEUE_LETTER_TITLE = "ARHIIVIKIRI-ULEVAATUS-4432"


@pytest.fixture
def queues(world) -> dict[str, Any]:
    """One pending candidate in each queue, each naming the RESTRICTED Matter."""
    import datetime as dt

    from app.legacy_import.opinion_archive import (
        OpinionArchiveBatch,
        OpinionArchiveItem,
        OpinionMatchCandidate,
    )
    from app.legacy_import.opinion_binary import OpinionArchiveBinary
    from app.legacy_import.opinion_enums import OpinionCandidateState, OpinionMatchClass
    from app.legacy_import.source_pages import HistoricalMatchCandidate, LegacySourcePage

    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_page_id="{policy-review}",
        page_key="policy-review",
        source_notebook="Õigusloome",
        source_section="2019",
        capture_id="policy",
        title=QUEUE_PAGE_TITLE,
        first_imported_at=now,
        latest_imported_at=now,
    )
    historical = HistoricalMatchCandidate.objects.create(
        source_page=page, matter=world["restricted"], excel_title=RESTRICTED_TITLE
    )
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="c" * 64,
        importer_version="test/0",
        started_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256="d" * 64,
        size_bytes=512,
        mime_type="application/pdf",
        storage_key="opinion-archive/dd/dd/" + "d" * 64,
        source_archive_sha256="c" * 64,
        materialized_at=now,
    )
    item = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="c" * 64,
        archive_relative_path="Opinions/ulevaatus.pdf",
        original_filename="ulevaatus.pdf",
        sha256=binary.sha256,
        size_bytes=512,
        detected_type="application/pdf",
        filename_date=dt.date(2024, 4, 10),
        filename_recipient="Naidisministeerium",
        filename_title=QUEUE_LETTER_TITLE,
        binary=binary,
    )
    opinion = OpinionMatchCandidate.objects.create(
        item=item,
        matter=world["restricted"],
        batch=batch,
        match_class=OpinionMatchClass.CONTENT_MULTI_SIGNAL,
        state=OpinionCandidateState.PENDING,
    )
    return {"historical": historical, "opinion": opinion}


QUEUE_URLS = {
    "historical": "legacy_import:review_queue",
    "opinion": "legacy_import:opinion_queue",
}
QUEUE_TOKENS = (QUEUE_PAGE_TITLE, QUEUE_LETTER_TITLE, RESTRICTED_TITLE)


@pytest.mark.parametrize("queue", sorted(QUEUE_URLS))
def test_an_administrator_without_a_grant_sees_no_queue_row(world, queues, queue):
    """The queue stays theirs to work; its rows are business content (docs/adr/0181)."""

    response = _signed_in(world["administrator"]).get(reverse(QUEUE_URLS[queue]) + "?olek=PENDING")

    assert response.status_code == 403
    body = _body(response)
    assert not [token for token in QUEUE_TOKENS if token in body], body[:400]


@pytest.mark.parametrize("queue", sorted(QUEUE_URLS))
def test_an_administrator_with_a_grant_works_the_queue_with_its_rows(world, queues, queue):
    from app.accounts.services import grant_break_glass

    grant_break_glass(
        user=world["administrator"],
        granted_by=world["department_head"],
        reason="Ajaloo sidumine",
        duration=timedelta(hours=1),
    )
    response = _signed_in(world["administrator"]).get(reverse(QUEUE_URLS[queue]) + "?olek=PENDING")

    assert response.status_code == 200
    body = _body(response)
    assert (QUEUE_PAGE_TITLE if queue == "historical" else QUEUE_LETTER_TITLE) in body


@pytest.mark.parametrize("queue", sorted(QUEUE_URLS))
def test_nobody_else_reaches_a_queue_at_all(world, queues, queue):
    """Working the queue is still the administrator's alone: a reader is refused."""
    response = _signed_in(world["reader"]).get(reverse(QUEUE_URLS[queue]))

    assert response.status_code in (403, 404)
    body = _body(response)
    assert not [token for token in QUEUE_TOKENS if token in body]


def test_an_administrator_without_a_grant_cannot_decide_a_candidate(world, queues):
    """The decision routes ask the same two questions before anything is read."""
    from app.legacy_import.source_pages import CandidateState

    client = _signed_in(world["administrator"])
    historical = queues["historical"]
    response = client.post(
        reverse("legacy_import:review_decide", kwargs={"pk": historical.pk}),
        {"action": "background"},
    )
    assert response.status_code == 403
    historical.refresh_from_db()
    assert historical.state == CandidateState.PENDING

    opinion = queues["opinion"]
    response = client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": opinion.pk}), {"action": "x"}
    )
    assert response.status_code == 403
