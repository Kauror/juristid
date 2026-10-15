"""QA authz (2026-10-14): records *derived* from a restricted record.

ADR 0137-0139 decided three times that a record created *because of* a
restricted record is never less restricted than it: a file captured for a
record (0137), the completion entry of a step (0138), the replacement of a step
(0139). Follow-up checks copy the opinion's restriction (0146) and a
`Kaasamine`'s own files and its closing feedback inherit the round's.

This file asks the same question of the derived records those ADRs did not
name, through the real HTTP routes a lawyer uses:

* `+ Kaasamine · Lisa tagasiside` (`matters:add_engagement_reply`, ADR 0142):
  a `Meile saadetud tagasiside` tied to a RESTRICTED round, and its file.
* `+ Koja arvamus` that *was the step* (ADR 0126 §2) where the step is
  RESTRICTED: the step folded under the visible «Arvamus välja» row.

The adversary is a READER (and the shared-gate department viewer for the
document routes, which are `@gate_required`). The allowed half: the lawyers
still see everything.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.models import Document, DocumentVersion
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.search.services import result_count
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
SECRET_REPLY = "SALAJANE-VASTUS-5512"  # noqa: S105
SECRET_FILE = "salajane-vastus-5512.pdf"  # noqa: S105
SECRET_ROUND = "SALAJANE-RING-5512"  # noqa: S105


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def restricted_round(db):
    owner = factories.UserFactory(display_name="Omanik Olev")
    matter = factories.MatterFactory(
        title="Avalik teema kaasamisega",
        owner=owner,
        visibility=Visibility.NORMAL,
        received_date=timezone.localdate(),
    )
    round_ = MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title=SECRET_ROUND,
        created_by=owner,
        lifecycle_tracked=True,
        visibility_override=Visibility.RESTRICTED,
    )
    return {"owner": owner, "matter": matter, "round": round_}


def _record_reply(world) -> MatterExternalPosition:
    """The lawyer records what came back from the round, with its file, over HTTP."""
    lawyer = _client(world["owner"])
    response = lawyer.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": world["matter"].pk}),
        {
            "engagement": str(world["round"].pk),
            "summary": SECRET_REPLY,
            "stated_on": timezone.localdate().strftime("%d.%m.%Y"),
            "attachments": SimpleUploadedFile(SECRET_FILE, b"%PDF-1.4 vastus", content_type=PDF),
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200, response.content[:2000]
    reply = MatterExternalPosition.objects.get(matter=world["matter"], engagement=world["round"])
    return reply


def test_the_lawyer_can_record_a_reply_to_a_restricted_round_and_sees_it(restricted_round):
    """Allowed half: the writer records the reply and reads it back."""
    reply = _record_reply(restricted_round)
    body = (
        _client(restricted_round["owner"])
        .get(reverse("matters:matter_detail", kwargs={"pk": restricted_round["matter"].pk}))
        .content.decode()
    )
    assert SECRET_REPLY in body
    assert reply.summary == SECRET_REPLY


def test_a_reply_to_a_restricted_round_is_not_shown_to_a_reader(restricted_round):
    reader = factories.ReaderFactory()
    _record_reply(restricted_round)
    client = _client(reader)
    matter = restricted_round["matter"]
    pages = {
        "detail": reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        "timeline": reverse("matters:timeline_page", kwargs={"pk": matter.pk}),
        "documents": reverse("matters:matter_documents", kwargs={"pk": matter.pk}),
        "changes": reverse("matters:matter_changes", kwargs={"pk": matter.pk}),
    }
    leaked = {
        name: url
        for name, url in pages.items()
        if SECRET_REPLY in client.get(url).content.decode()
        or SECRET_FILE in client.get(url).content.decode()
    }
    assert leaked == {}
    # `/otsing/` echoes the query in its search box and heading, so the page
    # body always holds the term asked for; what must not appear is a *hit*.
    # (Remediation A: the QA version compared the whole body, which no fix can
    # satisfy.) The file name is never echoed, so it is checked in the body.
    search = reverse("search:search")
    for term in (SECRET_REPLY, "salajane-vastus-5512"):
        assert result_count(query=term, user=reader) == 0, term
        body = client.get(search, {"q": term}).content.decode()
        assert "0 vastet" in body, term
        assert SECRET_FILE not in body, term
    # Allowed half: the same searches do find it for the lawyer.
    assert result_count(query=SECRET_REPLY, user=restricted_round["owner"]) >= 1


def test_a_reply_and_its_file_are_never_less_restricted_than_the_round(restricted_round):
    reply = _record_reply(restricted_round)
    documents = Document.objects.filter(links__external_position=reply)
    assert documents.exists()
    assert reply.effective_visibility == Visibility.RESTRICTED
    assert {d.effective_visibility for d in documents} == {Visibility.RESTRICTED}


def test_the_gate_reader_cannot_download_a_reply_file_of_a_restricted_round(
    restricted_round, settings
):
    reply = _record_reply(restricted_round)
    version = DocumentVersion.objects.get(document__links__external_position=reply)
    apply_shared_gate(settings)
    gate = Client()
    assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    assert gate.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404


def test_the_restricted_round_itself_stays_hidden_from_the_reader(restricted_round):
    """Control: the round (not the reply) is correctly hidden today."""
    reader = factories.ReaderFactory()
    body = (
        _client(reader)
        .get(reverse("matters:matter_detail", kwargs={"pk": restricted_round["matter"].pk}))
        .content.decode()
    )
    assert SECRET_ROUND not in body


# ---------------------------------------------------------------------------
# A Koja arvamus that completes a RESTRICTED current step
# ---------------------------------------------------------------------------

SECRET_STEP = "SALAJANE-SAMM-6620"  # noqa: S105


def test_a_restricted_step_completed_by_an_opinion_is_not_folded_under_it_for_a_reader(
    capture_evidence,
):
    owner = factories.UserFactory()
    organisation = factories.OrganisationFactory()
    matter = factories.MatterFactory(owner=owner, visibility=Visibility.NORMAL)
    matter.source_organisations.set([organisation])
    step = factories.NextActionFactory(
        matter=matter,
        text=SECRET_STEP,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate(),
        date_precision=DatePrecision.EXACT,
        status=ActionStatus.OPEN,
        responsible=owner,
        visibility_override=Visibility.RESTRICTED,
    )
    from app.matters import workspace

    workspace.add_matter_koda_opinion(
        matter=matter,
        author=owner,
        uploads=[SimpleUploadedFile("arvamus.pdf", b"%PDF-1.4 arvamus", content_type=PDF)],
        sent_on=timezone.localdate(),
        recipients=[organisation],
        complete_action_id=step.pk,
    )
    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED

    reader = factories.ReaderFactory()
    client = _client(reader)
    for url in (
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        reverse("matters:timeline_page", kwargs={"pk": matter.pk}),
        reverse("matters:matter_changes", kwargs={"pk": matter.pk}),
    ):
        assert SECRET_STEP not in client.get(url).content.decode(), url
    # Allowed half.
    owner_body = (
        _client(owner)
        .get(reverse("matters:timeline_page", kwargs={"pk": matter.pk}))
        .content.decode()
    )
    assert SECRET_STEP in owner_body
