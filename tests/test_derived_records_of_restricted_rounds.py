"""A record derived from a restricted `Kaasamine` round is never less restricted (ADR 0162).

QA-AUTHZ-01 (2026-10-14): a reply (`Meile saadetud tagasiside`) recorded against
a RESTRICTED round was stored NORMAL, and so were its files: a READER or an
administrator read it on the Teema page, Teema käik, Dokumendid and `/otsing/`,
and the shared-gate visitor downloaded its file.

Two halves are proved here, for every persona the product has:

* **new rows** — the reply and its file are created with the round's
  restriction (`record_external_position`, then 0137's capture);
* **legacy rows** — a reply and file written *before* that, still NORMAL in
  their own columns, are hidden at read time through the round. They are built
  by the real service and then reset to the old shape with `.update()`, which
  is exactly what the old code left behind (search rows included).

The allowed half — the round's lawyers, the head, the Matter's participants and
a break-glass grant — still see everything.

Since docs/adr/0181 the restriction is a label, not a boundary between
authorized users: a READER reads the reply and its file like the owner, and the
perimeter is an administrator without a grant or a persona-less gate visitor,
who read nothing of the Matter at all. The stored shape (0162) is unchanged.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts.models import BreakGlassGrant
from app.core.authorization import Scope
from app.core.derived_visibility import external_position_visibility_q
from app.core.enums import Visibility
from app.documents.links import DocumentLink
from app.documents.models import Document, DocumentVersion
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.models import MatterEngagement, MatterExternalPosition, MatterWebsiteOverview
from app.matters.services import correct_external_position, external_position_revision
from app.matters.workspace import _overview_for_engagement_link, add_matter_external_position
from app.search.services import result_count
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
REPLY = "VASTUS-RINGILE-7781"
FILE = "vastus-ringile-7781.pdf"
ROUND = "PIIRATUD-RING-7781"
OVERVIEW_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/piiratud-7781"


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def people(db):
    owner = factories.UserFactory(display_name="Omanik Ott")
    head = factories.DepartmentHeadFactory()
    administrator = factories.AdministratorFactory()
    return {
        "owner": owner,
        "specialist": factories.UserFactory(display_name="Teine Jurist"),
        "head": head,
        "reader": factories.ReaderFactory(),
        "administrator": administrator,
        "collaborating_reader": factories.ReaderFactory(display_name="Kaasatud Lugeja"),
    }


def _round(matter, owner, *, restricted: bool = True) -> MatterEngagement:
    return MatterEngagement.objects.create(
        matter=matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title=ROUND,
        created_by=owner,
        lifecycle_tracked=True,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


def _reply(matter, owner, round_) -> MatterExternalPosition:
    return add_matter_external_position(
        matter=matter,
        author=owner,
        organisation=None,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        source_label="Liikmete vastused",
        summary=REPLY,
        stated_on=timezone.localdate(),
        engagement=round_,
        uploads=[SimpleUploadedFile(FILE, b"%PDF-1.4 vastus ringile", content_type=PDF)],
    ).record


def _make_legacy(reply: MatterExternalPosition) -> None:
    """Reset a reply and its files to the NORMAL shape the old code wrote."""
    MatterExternalPosition.objects.filter(pk=reply.pk).update(visibility_override="")
    Document.objects.filter(links__external_position=reply).update(visibility_override="")


@pytest.fixture(params=["new", "legacy"])
def world(request, people):
    matter = factories.MatterFactory(
        title="Avalik teema piiratud ringiga",
        owner=people["owner"],
        visibility=Visibility.NORMAL,
        received_date=timezone.localdate(),
    )
    matter.collaborators.set([people["collaborating_reader"]])
    round_ = _round(matter, people["owner"])
    reply = _reply(matter, people["owner"], round_)
    if request.param == "legacy":
        _make_legacy(reply)
    reply.refresh_from_db()
    version = DocumentVersion.objects.get(document__links__external_position=reply)
    return {
        **people,
        "shape": request.param,
        "matter": matter,
        "round": round_,
        "reply": reply,
        "document": version.document,
        "version": version,
    }


def _pages(matter) -> dict[str, str]:
    return {
        "detail": reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
        "timeline": reverse("matters:timeline_page", kwargs={"pk": matter.pk}),
        "documents": reverse("matters:matter_documents", kwargs={"pk": matter.pk}),
        "changes": reverse("matters:matter_changes", kwargs={"pk": matter.pk}),
    }


def _file_routes(world) -> dict[str, str]:
    return {
        "download": reverse("documents:download", kwargs={"pk": world["version"].pk}),
        "open": reverse("documents:open", kwargs={"pk": world["version"].pk}),
        "detail": reverse("documents:document_detail", kwargs={"pk": world["document"].pk}),
    }


def _gate_client(settings) -> Client:
    apply_shared_gate(settings)
    gate = Client()
    assert gate.post(reverse("accounts:shared_gate"), {"password": PASSWORD}).status_code == 302
    return gate


# ---------------------------------------------------------------------------
# Creation: the stored shape
# ---------------------------------------------------------------------------


def test_every_new_reply_and_its_file_carry_the_rounds_restriction(people):
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)
    round_ = _round(matter, people["owner"])
    # Every answer to the round, not only the first (ADR 0162 §1; D8 later replies).
    replies = [_reply(matter, people["owner"], round_) for _ in range(3)]
    for reply in replies:
        assert reply.visibility_override == Visibility.RESTRICTED
        documents = Document.objects.filter(links__external_position=reply)
        assert documents.exists()
        assert set(documents.values_list("visibility_override", flat=True)) == {"RESTRICTED"}
    # docs/adr/0181: the label is kept, and every authorized user reads the replies.
    for persona in ("owner", "reader"):
        visible = MatterExternalPosition.objects.visible_to(people[persona])
        assert visible.filter(engagement=round_).count() == 3, persona
    assert (
        not MatterExternalPosition.objects.visible_to(people["administrator"])
        .filter(engagement=round_)
        .exists()
    )


def test_a_reply_to_an_unrestricted_round_is_unrestricted_and_readable(people):
    """Control: nothing changes for the ordinary round."""
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)
    reply = _reply(matter, people["owner"], _round(matter, people["owner"], restricted=False))
    assert reply.visibility_override == ""
    assert reply.effective_visibility == Visibility.NORMAL
    reader = people["reader"]
    assert MatterExternalPosition.objects.visible_to(reader).filter(pk=reply.pk).exists()
    document = Document.objects.get(links__external_position=reply)
    assert Document.objects.visible_to(reader).filter(pk=document.pk).exists()
    body = _client(reader).get(_pages(matter)["detail"]).content.decode()
    assert REPLY in body
    assert result_count(query=REPLY, user=reader) >= 1


def test_a_reply_re_pointed_at_a_restricted_round_takes_its_restriction(people):
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)
    open_round = _round(matter, people["owner"], restricted=False)
    reply = _reply(matter, people["owner"], open_round)
    assert reply.visibility_override == ""
    secret = _round(matter, people["owner"])
    corrected = correct_external_position(
        position=reply,
        organisation=None,
        url="",
        stated_on=reply.stated_on,
        stated_on_precision=reply.stated_on_precision,
        summary=reply.summary,
        engagement=secret,
        provenance=reply.provenance,
        source_label=reply.source_label,
        actor=people["owner"],
        expected_revision=external_position_revision(reply),
    )
    corrected.refresh_from_db()
    assert corrected.visibility_override == Visibility.RESTRICTED
    # The file captured while the round was open is read like the round: by every
    # authorized user (docs/adr/0181), by no outsider.
    document = Document.objects.get(links__external_position=reply)
    assert Document.objects.visible_to(people["reader"]).filter(pk=document.pk).exists()
    assert Document.objects.visible_to(people["owner"]).filter(pk=document.pk).exists()
    assert not Document.objects.visible_to(people["administrator"]).filter(pk=document.pk).exists()


# ---------------------------------------------------------------------------
# Read time: every persona, both shapes
# ---------------------------------------------------------------------------


def test_an_administrator_without_a_grant_reaches_neither_the_matter_nor_the_reply(world):
    """The perimeter (docs/adr/0181): an outsider reads nothing — not even the NORMAL Matter."""
    user = world["administrator"]
    reply, document = world["reply"], world["document"]
    assert reply.effective_visibility == Visibility.RESTRICTED
    assert not MatterExternalPosition.objects.visible_to(user).filter(pk=reply.pk).exists()
    assert not Document.objects.visible_to(user).filter(pk=document.pk).exists()
    assert not DocumentLink.objects.visible_to(user).filter(document=document).exists()
    client = _client(user)
    for name, url in _pages(world["matter"]).items():
        response = client.get(url)
        assert response.status_code == 404, name
        body = response.content.decode()
        assert REPLY not in body, f"{name} ({world['shape']})"
        assert FILE not in body, f"{name} ({world['shape']})"
        assert ROUND not in body, f"{name} ({world['shape']})"
    for term in (REPLY, "vastus-ringile-7781"):
        assert result_count(query=term, user=user) == 0, term
    for name, url in _file_routes(world).items():
        assert client.get(url).status_code == 404, name


def test_the_gate_visitor_with_no_persona_is_sent_to_choose_one(world, settings):
    """It used to get a 404; since docs/adr/0181 every file route asks for a persona first."""
    gate = _gate_client(settings)
    for name, url in _file_routes(world).items():
        response = gate.get(url)
        assert response.status_code == 302, f"{name} ({world['shape']})"
        assert response["Location"].startswith(reverse("accounts:choose_persona")), name
        assert b"vastus ringile" not in response.content, name


@pytest.mark.parametrize(
    "persona", ["owner", "specialist", "head", "collaborating_reader", "reader"]
)
def test_every_authorized_reader_sees_the_reply_and_opens_its_file(world, persona):
    """Since docs/adr/0181 the READER who does not collaborate is one of them."""
    user = world[persona]
    assert MatterExternalPosition.objects.visible_to(user).filter(pk=world["reply"].pk).exists()
    assert Document.objects.visible_to(user).filter(pk=world["document"].pk).exists()
    client = _client(user)
    detail = client.get(_pages(world["matter"])["detail"]).content.decode()
    assert REPLY in detail
    documents = client.get(_pages(world["matter"])["documents"]).content.decode()
    assert FILE in documents
    assert client.get(_file_routes(world)["download"]).status_code == 200
    assert result_count(query=REPLY, user=user) >= 1


def test_break_glass_opens_the_reply_and_its_expiry_closes_it(world):
    admin = world["administrator"]
    client = _client(admin)
    download = _file_routes(world)["download"]
    assert client.get(download).status_code == 404
    grant = BreakGlassGrant.objects.create(
        user=admin,
        granted_by=world["head"],
        reason="Kontroll",
        starts_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert client.get(download).status_code == 200
    assert MatterExternalPosition.objects.visible_to(admin).filter(pk=world["reply"].pk).exists()
    BreakGlassGrant.objects.filter(pk=grant.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert client.get(download).status_code == 404


def test_counts_move_alike_for_every_reader_and_never_for_an_outsider(people):
    """A count that moves is a disclosure (docs/adr/0038), for either shape.

    Since docs/adr/0181 the READER's counts move exactly as the owner's do; the
    outsider whose counts must not move is an administrator without a grant.
    """
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)

    def counts(user):
        return (
            Document.objects.visible_to(user).filter(matter=matter).count(),
            MatterExternalPosition.objects.visible_to(user).filter(matter=matter).count(),
            DocumentLink.objects.visible_to(user).filter(document__matter=matter).count(),
            result_count(query="vastus", user=user),
        )

    outsider_before = counts(people["administrator"])
    new = _reply(matter, people["owner"], _round(matter, people["owner"]))
    legacy = _reply(matter, people["owner"], _round(matter, people["owner"]))
    _make_legacy(legacy)
    assert new.pk != legacy.pk
    assert counts(people["administrator"]) == outsider_before == (0, 0, 0, 0)
    assert counts(people["reader"]) == counts(people["owner"])
    assert counts(people["reader"])[1] == 2


def test_relaxing_the_round_shows_a_legacy_reply_again_but_never_a_copied_one(people):
    """Live half follows the round; the copied half fails closed (0137 §2, 0162 §3).

    Since docs/adr/0181 no request reaches a narrowed scope, so the builder is
    exercised through a hand-built one (a non-participant who does not read
    RESTRICTED by role); every real reader sees both rows.
    """
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)
    round_ = _round(matter, people["owner"])
    copied = _reply(matter, people["owner"], round_)
    legacy = _reply(matter, people["owner"], round_)
    _make_legacy(legacy)
    MatterEngagement.objects.filter(pk=round_.pk).update(visibility_override="")
    narrowed = Scope(
        user=people["reader"],
        is_authenticated=True,
        sees_restricted_by_role=False,
        break_glass_grant_id=None,
    )
    visible = MatterExternalPosition.objects.filter(external_position_visibility_q(narrowed))
    assert visible.filter(pk=legacy.pk).exists()
    assert not visible.filter(pk=copied.pk).exists()
    as_reader = MatterExternalPosition.objects.visible_to(people["reader"])
    assert as_reader.filter(pk__in=[legacy.pk, copied.pk]).count() == 2


# ---------------------------------------------------------------------------
# The round's Ülevaate link (QA-AUTHZ-P1)
# ---------------------------------------------------------------------------


def test_a_restricted_rounds_overview_is_restricted_and_leaves_the_plan_alone(people):
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)
    from app.matters.services import plan_website_overview

    plan = plan_website_overview(matter=matter, actor=people["owner"], kind="")
    round_ = _round(matter, people["owner"])
    overview = _overview_for_engagement_link(
        matter=matter, url=OVERVIEW_URL, actor=people["owner"], engagement=round_
    )
    assert overview.pk != plan.pk
    assert overview.visibility_override == Visibility.RESTRICTED
    plan.refresh_from_db()
    assert plan.status == "PLANNED"
    # docs/adr/0181: the READER reads both, as the owner does; an outsider neither.
    for persona in ("reader", "owner"):
        readable = MatterWebsiteOverview.objects.visible_to(people[persona])
        assert readable.filter(pk__in=[overview.pk, plan.pk]).count() == 2, persona
    outsider = MatterWebsiteOverview.objects.visible_to(people["administrator"])
    assert not outsider.filter(pk__in=[overview.pk, plan.pk]).exists()


def test_an_unrestricted_rounds_overview_still_publishes_the_plan(people):
    """Control: the ordinary round consumes the Matter's plan, as ADR 0142 §C says."""
    matter = factories.MatterFactory(owner=people["owner"], visibility=Visibility.NORMAL)
    from app.matters.services import plan_website_overview

    plan = plan_website_overview(matter=matter, actor=people["owner"], kind="")
    round_ = _round(matter, people["owner"], restricted=False)
    overview = _overview_for_engagement_link(
        matter=matter, url=OVERVIEW_URL, actor=people["owner"], engagement=round_
    )
    assert overview.pk == plan.pk
    assert overview.visibility_override == ""
