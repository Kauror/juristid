"""A readable record never names a related record its reader may not read.

Two findings with one rule — authorization before presentation, including when
the object is reached through a foreign key or an administrative queue:

* ENG-047: a `Väline seisukoht` printed the title of the `Kaasamine` it answered
  through the foreign key, so a round restricted below the Matter, or taken off
  the file, was still named; the sent-opinions list linked final evidence
  restricted below its Submission; a historical source page named attachments
  whose documents the reader may not read; and a closure could adopt a removed
  commencement row as the file's current fact.
* ENG-067: the administrator's reconciliation queues printed RESTRICTED Matter
  titles and ids, and a crafted decision linked archive material to a Matter the
  administrator may not read, answering with its reference.

Plus the side effect ENG-047 found: correcting an unrelated field of a position
cleared a relation to a round the corrector could not see.
"""

from __future__ import annotations

import datetime as dt
import uuid
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.legacy_import.opinion_archive import OpinionMatchCandidate
from app.legacy_import.opinion_enums import OpinionCandidateState, OpinionMatchClass
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.removal import remove_matter_record
from app.matters.services import add_engagement, record_external_position
from app.submissions.enums import SubmissionStatus
from tests import factories

pytestmark = pytest.mark.django_db

ROUND = "R3-KAASAMINE-ALFA"
HIDDEN_ROUND = "R3-KAASAMINE-SALAJANE"
HIDDEN_MATTER = "R3 konfidentsiaalne liikmete tagasiside"


class _FormFields(HTMLParser):
    """The values a browser would submit from a rendered form."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}
        self._select: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        name = a.get("name")
        if tag == "input" and name and a.get("type") not in {"submit", "button", "file"}:
            if a.get("type") in {"checkbox", "radio"} and "checked" not in a:
                return
            default = "on" if a.get("type") in {"checkbox", "radio"} else ""
            self.data[name] = a.get("value", default)
        elif tag == "select" and name:
            self._select = name
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in a:
            self.data[self._select] = a.get("value", "")
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = ""

    def handle_data(self, data: str) -> None:
        if self._textarea:
            self.data[self._textarea] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None


def _form(html: str) -> dict[str, str]:
    parser = _FormFields()
    parser.feed(html)
    return {key: value.strip("\n") for key, value in parser.data.items()}


# -- ENG-047: the round a position answered -----------------------------------------


@pytest.fixture
def organisation(db):
    return factories.OrganisationFactory()


def _round(matter, specialist, title=ROUND):
    return add_engagement(matter=matter, kind=EngagementKind.SURVEY, title=title, actor=specialist)


def _position(matter, specialist, organisation, engagement, summary="Toetab muudatust"):
    return record_external_position(
        matter=matter,
        organisation=organisation,
        summary=summary,
        engagement=engagement,
        stated_on=timezone.localdate(),
        actor=specialist,
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _row_url(matter, position) -> str:
    return reverse(
        "matters:update_external_position",
        kwargs={"pk": matter.pk, "position_id": position.pk},
    )


def test_a_visible_round_is_named(signed_in, normal_matter, specialist, organisation):
    _position(normal_matter, specialist, organisation, _round(normal_matter, specialist))

    assert f"Vastus kaasamisele: {ROUND}" in _detail(signed_in, normal_matter)


def test_a_removed_round_is_not_named(signed_in, normal_matter, specialist, organisation):
    engagement = _round(normal_matter, specialist)
    position = _position(normal_matter, specialist, organisation, engagement)
    remove_matter_record(
        matter_id=normal_matter.pk, kind_key="kaasamine", record_id=engagement.pk, actor=specialist
    )

    assert ROUND not in _detail(signed_in, normal_matter)
    fragment = signed_in.get(_row_url(normal_matter, position), {"vaade": "lugemine"})
    assert ROUND not in fragment.content.decode()
    # Nothing was cleared to get there: the relation is stored as it was.
    assert MatterExternalPosition.objects.get(pk=position.pk).engagement_id == engagement.pk


def test_a_restricted_round_is_not_named_to_somebody_who_may_not_read_it(
    client, normal_matter, specialist, organisation, reader, administrator
):
    engagement = _round(normal_matter, specialist, title=HIDDEN_ROUND)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    _position(normal_matter, specialist, organisation, engagement)

    for person in (reader, administrator):
        client.force_login(person)
        assert HIDDEN_ROUND not in _detail(client, normal_matter)
        assert str(engagement.pk) not in _detail(client, normal_matter)

    client.force_login(specialist)  # the owner may read it
    assert f"Vastus kaasamisele: {HIDDEN_ROUND}" in _detail(client, normal_matter)


def test_the_department_head_still_reads_a_restricted_round(
    client, normal_matter, specialist, organisation, department_head
):
    engagement = _round(normal_matter, specialist, title=HIDDEN_ROUND)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    _position(normal_matter, specialist, organisation, engagement)
    client.force_login(department_head)

    assert HIDDEN_ROUND in _detail(client, normal_matter)


def _hidden_link(matter, specialist, organisation):
    """A position answering a round its corrector can no longer see (removed)."""
    engagement = _round(matter, specialist)
    position = _position(matter, specialist, organisation, engagement)
    remove_matter_record(
        matter_id=matter.pk, kind_key="kaasamine", record_id=engagement.pk, actor=specialist
    )
    return MatterExternalPosition.objects.get(pk=position.pk), engagement


def test_an_unrelated_correction_keeps_a_relation_the_corrector_cannot_see(
    signed_in, normal_matter, specialist, organisation
):
    position, engagement = _hidden_link(normal_matter, specialist, organisation)
    opened = signed_in.get(_row_url(normal_matter, position)).content.decode()
    assert ROUND not in opened and str(engagement.pk) not in opened
    data = _form(opened)
    assert data["engagement"] == "jaab-samaks"

    response = signed_in.post(
        _row_url(normal_matter, position), {**data, "summary": "Toetab, parandatud"}
    )

    assert response.status_code == 200
    position.refresh_from_db()
    assert position.summary == "Toetab, parandatud"
    assert position.engagement_id == engagement.pk


def test_clearing_the_relation_on_purpose_still_clears_it(
    signed_in, normal_matter, specialist, organisation
):
    position, _engagement = _hidden_link(normal_matter, specialist, organisation)
    data = _form(signed_in.get(_row_url(normal_matter, position)).content.decode())

    signed_in.post(_row_url(normal_matter, position), {**data, "engagement": ""})

    position.refresh_from_db()
    assert position.engagement_id is None


def test_choosing_a_visible_round_still_works(signed_in, normal_matter, specialist, organisation):
    position, _engagement = _hidden_link(normal_matter, specialist, organisation)
    other = _round(normal_matter, specialist, title="R3-TEINE-KAASAMINE")
    data = _form(signed_in.get(_row_url(normal_matter, position)).content.decode())

    signed_in.post(_row_url(normal_matter, position), {**data, "engagement": str(other.pk)})

    position.refresh_from_db()
    assert position.engagement_id == other.pk


def test_keep_is_not_a_value_a_form_without_a_hidden_round_accepts(
    signed_in, normal_matter, specialist, organisation
):
    position = _position(normal_matter, specialist, organisation, None)
    data = _form(signed_in.get(_row_url(normal_matter, position)).content.decode())
    assert "jaab-samaks" not in signed_in.get(_row_url(normal_matter, position)).content.decode()

    response = signed_in.post(
        _row_url(normal_matter, position), {**data, "engagement": "jaab-samaks"}
    )

    assert response.status_code == 400


# -- ENG-047: final evidence on the sent list ----------------------------------------


def test_restricted_final_evidence_is_neither_linked_nor_named(
    client, specialist, reader, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(
        matter,
        b"%PDF-1.4 salajane",
        "salajane-lopparvamus.pdf",
        "application/pdf",
        visibility_override=Visibility.RESTRICTED,
    )
    factories.SubmissionFactory(
        matter=matter,
        title="R3 saadetud arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )

    client.force_login(reader)
    body = client.get(reverse("submissions:sent")).content.decode()
    assert "R3 saadetud arvamus" in body  # the Submission itself is readable
    assert str(version.pk) not in body
    assert "salajane-lopparvamus" not in body

    client.force_login(specialist)
    owner_view = client.get(reverse("submissions:sent")).content.decode()
    assert reverse("documents:open", kwargs={"pk": version.pk}) in owner_view


# -- ENG-047: historical source page ---------------------------------------------


@pytest.fixture
def source_page_with_files(specialist, capture_evidence):
    """A readable historical page carrying one readable and one restricted file."""
    from app.legacy_import.source_pages import (
        LegacySourcePage,
        LegacySourceResource,
        LegacySourceResourceImport,
        MatterSourcePage,
    )

    matter = factories.MatterFactory(owner=specialist)
    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_page_id="{r3-page}",
        page_key="r3-page",
        source_notebook="Õigusloome",
        source_section="2019",
        capture_id="r3",
        title="R3 ajalooline leht",
        blocks=[
            {"kind": "FILE_ATTACHMENT", "resource_key": "avalik", "ordinal": 1},
            {"kind": "FILE_ATTACHMENT", "resource_key": "salajane", "ordinal": 2},
        ],
        first_imported_at=now,
        latest_imported_at=now,
    )
    link = MatterSourcePage.objects.create(matter=matter, source_page=page, match_method="EXACT")
    for key, filename, override in (
        ("avalik", "avalik-kiri.pdf", ""),
        ("salajane", "salajane-liikme-kiri.pdf", Visibility.RESTRICTED),
    ):
        resource = LegacySourceResource.objects.create(
            source_page=page,
            resource_key=key,
            original_filename=filename,
            size_bytes=4321,
            archive_relative_path=f"files/{filename}",
        )
        version = capture_evidence(
            matter,
            b"%PDF-1.4 " + key.encode(),
            filename,
            "application/pdf",
            visibility_override=override,
        )
        LegacySourceResourceImport.objects.create(
            matter_source_page=link,
            resource=resource,
            document=version.document,
            document_version=version,
        )
    return link


def test_a_historical_page_names_no_attachment_its_reader_may_not_read(
    client, reader, specialist, source_page_with_files
):
    from app.documents.models import DocumentVersion

    url = reverse("legacy_import:source_page", kwargs={"pk": source_page_with_files.pk})
    hidden = DocumentVersion.objects.get(original_filename="salajane-liikme-kiri.pdf")

    client.force_login(reader)
    body = client.get(url).content.decode()
    assert "avalik-kiri.pdf" in body
    assert "salajane-liikme-kiri" not in body
    assert str(hidden.pk) not in body and str(hidden.document_id) not in body
    assert "Fail, mida selles kasutajavaates ei kuvata" in body

    client.force_login(specialist)
    assert "salajane-liikme-kiri.pdf" in client.get(url).content.decode()


# -- ENG-047: a removed row is not the file's current fact ------------------------


def test_a_removed_commencement_is_not_reused_by_a_closure(normal_matter, specialist):
    from app.intelligence.enums import EffectiveDateKind
    from app.intelligence.models import MatterEffectiveDate
    from app.intelligence.services import add_effective_date
    from app.matters.services import _closure_commencement

    day = dt.date(2026, 1, 1)
    removed = add_effective_date(
        matter=normal_matter,
        actor=specialist,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=day,
        period_end=day,
    )
    MatterEffectiveDate.objects.filter(pk=removed.pk).update(
        removed_at=timezone.now(), removed_by=specialist
    )

    kept = _closure_commencement(
        matter=normal_matter,
        author=specialist,
        effective={"date_value": day, "period_end": day},
    )

    assert kept.pk != removed.pk
    assert kept.removed_at is None


# -- ENG-067: the reconciliation queues -------------------------------------------


@pytest.fixture
def archive_item(db):
    """One archived letter for the queue to propose Matters for."""
    from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
    from app.legacy_import.opinion_binary import OpinionArchiveBinary

    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256="b" * 64,
        size_bytes=512,
        mime_type="application/pdf",
        storage_key="opinion-archive/bb/bb/" + "b" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    return OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/naidis.pdf",
        original_filename="naidis.pdf",
        sha256=binary.sha256,
        size_bytes=512,
        detected_type="application/pdf",
        filename_date=dt.date(2024, 4, 10),
        filename_recipient="Naidisministeerium",
        filename_title="Naidisarvamus",
        binary=binary,
    )


@pytest.fixture
def hidden_matter(specialist):
    return factories.MatterFactory(
        owner=specialist, visibility=Visibility.RESTRICTED, title=HIDDEN_MATTER
    )


def _candidate(archive_item, matter=None, klass=OpinionMatchClass.CONTENT_MULTI_SIGNAL):
    return OpinionMatchCandidate.objects.create(
        item=archive_item,
        matter=matter,
        batch=archive_item.batch,
        match_class=klass,
        state=OpinionCandidateState.PENDING,
    )


def test_the_opinion_queue_names_no_restricted_matter(
    client,
    administrator,
    archive_item,
    hidden_matter,
    normal_matter,
):
    _candidate(archive_item, hidden_matter)
    _candidate(archive_item, normal_matter)
    client.force_login(administrator)

    body = client.get(reverse("legacy_import:opinion_queue")).content.decode()

    assert HIDDEN_MATTER not in body
    assert str(hidden_matter.pk) not in body
    assert normal_matter.title[:40] in body
    assert "Ettepanek osutab teemale, mida selles kasutajavaates ei kuvata." in body


def _decide(client, candidate, matter_id):
    return client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "link", "matter": matter_id},
        follow=True,
    )


def _messages(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def test_a_crafted_link_to_a_hidden_matter_is_refused_like_a_missing_one(
    client,
    administrator,
    archive_item,
    hidden_matter,
    specialist,
):
    from app.matters.deletion import delete_matter

    tombstone = factories.MatterFactory(owner=specialist)
    delete_matter(matter=tombstone, actor=specialist)
    client.force_login(administrator)

    answers = {}
    for (label, target), klass in zip(
        (
            ("hidden", str(hidden_matter.pk)),
            ("random", str(uuid.uuid4())),
            ("tombstone", str(tombstone.pk)),
            ("malformed", "not-a-uuid"),
        ),
        # One matterless proposal per archive_item and class is a database rule.
        OpinionMatchClass.values,
        strict=False,
    ):
        candidate = _candidate(archive_item, klass=klass)
        response = _decide(client, candidate, target)
        assert response.status_code == 200, label
        candidate.refresh_from_db()
        assert candidate.state == OpinionCandidateState.PENDING, label
        assert candidate.matter_id is None, label
        answers[label] = _messages(response)
        body = response.content.decode()
        assert HIDDEN_MATTER not in body and hidden_matter.display_reference not in body

    assert len({tuple(answer) for answer in answers.values()}) == 1, answers
    assert answers["hidden"] == ["Valitud teemat ei leitud."]


def test_a_proposal_pointing_at_a_hidden_matter_cannot_be_confirmed(
    client,
    administrator,
    archive_item,
    hidden_matter,
):
    candidate = _candidate(archive_item, hidden_matter)
    client.force_login(administrator)

    response = client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "link"},
        follow=True,
    )

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.PENDING
    assert _messages(response) == ["Valitud teemat ei leitud."]


def test_a_link_to_a_visible_matter_still_works(
    client,
    administrator,
    archive_item,
    normal_matter,
):
    candidate = _candidate(archive_item)
    client.force_login(administrator)

    _decide(client, candidate, str(normal_matter.pk))

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.LINKED
    assert candidate.matter_id == normal_matter.pk


@pytest.fixture
def historical_candidate(hidden_matter):
    from app.legacy_import.source_pages import HistoricalMatchCandidate, LegacySourcePage

    now = timezone.now()
    page = LegacySourcePage.objects.create(
        source_page_id="{r3-review}",
        page_key="r3-review",
        source_notebook="Õigusloome",
        source_section="2019",
        capture_id="r3",
        title="R3 ülevaatuse leht",
        first_imported_at=now,
        latest_imported_at=now,
    )
    return HistoricalMatchCandidate.objects.create(
        source_page=page, matter=hidden_matter, excel_title=HIDDEN_MATTER
    )


def test_the_history_queue_names_no_restricted_matter_and_will_not_link_to_it(
    client, administrator, historical_candidate, hidden_matter
):
    from app.legacy_import.source_pages import MatterSourcePage

    client.force_login(administrator)

    body = client.get(reverse("legacy_import:review_queue")).content.decode()
    assert HIDDEN_MATTER not in body
    assert str(hidden_matter.pk) not in body

    response = client.post(
        reverse("legacy_import:review_decide", kwargs={"pk": historical_candidate.pk}),
        {"decision": "link"},
        follow=True,
    )
    assert _messages(response) == ["Valitud teemat ei leitud."]
    assert not MatterSourcePage.objects.filter(matter=hidden_matter).exists()


# -- the two-world matrix ---------------------------------------------------------
#
# One restricted world and one normal world, read by the four roles, through every
# surface this round changed. The principle, and the only thing asserted: a route
# reveals nothing more because it reached the object through a foreign key or an
# administrative queue (ENG-047, ENG-067; ENG-008 is the admin half, in
# tests/test_admin_is_not_a_back_door.py).

ROLES = ("owner", "department_head", "reader", "administrator")


@pytest.fixture
def two_worlds(specialist, organisation, capture_evidence, archive_item):
    hidden = factories.MatterFactory(
        owner=specialist, visibility=Visibility.RESTRICTED, title=HIDDEN_MATTER
    )
    normal = factories.MatterFactory(owner=specialist, title="R3 tavaline teema")
    restricted_round = _round(normal, specialist, title=HIDDEN_ROUND)
    MatterEngagement.objects.filter(pk=restricted_round.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    _position(normal, specialist, organisation, restricted_round)
    version = capture_evidence(
        normal,
        b"%PDF-1.4 maatriks",
        "maatriksi-salajane.pdf",
        "application/pdf",
        visibility_override=Visibility.RESTRICTED,
    )
    factories.SubmissionFactory(
        matter=normal,
        title="R3 maatriksi arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    _candidate(archive_item, hidden)
    return hidden, normal


@pytest.mark.parametrize("role", ROLES)
def test_no_surface_reveals_more_than_the_matter_itself(
    client, role, specialist, department_head, reader, administrator, two_worlds
):
    hidden, normal = two_worlds
    person = {
        "owner": specialist,
        "department_head": department_head,
        "reader": reader,
        "administrator": administrator,
    }[role]
    may_read_restricted = role in {"owner", "department_head"}
    client.force_login(person)

    detail = client.get(reverse("matters:matter_detail", kwargs={"pk": hidden.pk}))
    assert (detail.status_code == 200) is may_read_restricted

    normal_page = _detail(client, normal)
    assert (HIDDEN_ROUND in normal_page) is may_read_restricted

    sent = client.get(reverse("submissions:sent")).content.decode()
    assert "maatriksi-salajane" not in sent  # restricted below its Submission: never named

    if role == "administrator":
        queue = client.get(reverse("legacy_import:opinion_queue")).content.decode()
        assert HIDDEN_MATTER not in queue
        assert str(hidden.pk) not in queue
