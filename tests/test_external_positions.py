"""`Väline seisukoht` — what another organisation said, and where to read it.

One record, one organisation, one source minimum. The rules this file is most
careful about are the ones a screenshot cannot show:

* a position needs an author and **one of three** sources — the written
  `Seisukoht`, a link, or an attached document — and a save carrying none of
  them is refused with everything typed intact (docs/adr/0084 §3, amended
  2026-09-16);
* the organisation comes from the one shared catalogue `Saatja` and `Adressaat`
  already answer, validated against the whole of it and created only through
  `resolve_organisation_name`;
* the link is a public `http(s)` address checked by a **parsed host**, rendered
  by its hostname or as `Ava seisukoht`, and never printed as chronology text;
* the document goes through the existing upload/`Document`/`DocumentVersion`/
  `DocumentLink` pipeline under the existing `EXTERNAL_POSITION` role — no
  second file store, no copied bytes, no separately stored text;
* `Seisukoha kuupäev` is recorded at the precision it is known to, including
  not at all;
* the relation to a `Kaasamine` is optional, and must stay so: most positions
  are unsolicited;
* a closed Matter refuses a new position, a correction, and every crafted POST
  that tries either;
* corrections are optimistically concurrent, and a stale one changes neither
  the metadata nor the source;
* none of it is work: no `NextAction`, no deadline, no work item, no metric, no
  search row, no archive projection (docs/adr/0084).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.links import TARGET_FIELDS, DocumentLink
from app.documents.models import Document
from app.matters import work_items
from app.matters.enums import EngagementKind
from app.matters.models import Entry, MatterExternalPosition
from app.matters.my_work import build_my_work
from app.matters.services import (
    EXTERNAL_POSITION_EDIT_CONFLICT,
    EXTERNAL_POSITION_NEEDS_ORGANISATION,
    EXTERNAL_POSITION_NEEDS_SOURCE,
    ExternalPositionConflict,
    add_engagement,
    close_matter,
    correct_external_position,
    external_position_revision,
    normalize_external_position_url,
)
from app.matters.timeline import (
    EXTERNAL_POSITION_DATE_UNKNOWN,
    TIMELINE_EVENT_TYPES,
    matter_timeline,
)
from app.matters.workspace import add_matter_external_position
from app.organisations.models import Organisation
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument
from app.workflow.enums import DatePrecision, Disposition
from tests import factories

pytestmark = pytest.mark.django_db

POSITION_URL = "https://rahandusministeerium.ee/uudised/pakendiseadus"
STATED_ON = dt.date(2026, 3, 14)


def _pdf(name: str = "seisukoht.pdf", body: bytes = b"%PDF-1.4 sisu") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _events(matter, event_type):
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type)


@pytest.fixture
def ministry(db):
    return factories.OrganisationFactory(name="Rahandusministeerium")


def _recorded(matter, organisation, actor, **kwargs):
    """A position through the workspace door, which is the one a person uses.

    **Provenance is left to the default**, which is `DISCOVERED` — so everything
    this file records is a `Teiste arvamus`, and that is why the headlines below
    read under that name rather than under `Väline seisukoht`. docs/adr/0091 §3
    split the chip in two and named the record by how it reached the file; nothing
    else in this file's subject moved, and the two rules that genuinely differ —
    the optional author for aggregate received feedback, and the lawyer's own note
    — are `tests/test_lawyer_workflow_package.py`, beside the rest of that round.
    """
    return add_matter_external_position(
        matter=matter,
        author=actor,
        organisation=organisation,
        **kwargs,
    ).record


# ---------------------------------------------------------------------------
# §1–§3 — the record, and the source minimum
# ---------------------------------------------------------------------------


def test_a_url_only_position_is_recorded(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    assert position.organisation_id == ministry.pk
    assert position.url == POSITION_URL
    assert position.document_links.count() == 0
    assert position.stated_on is None
    assert position.created_by_id == specialist.pk


def test_a_document_only_position_is_recorded(normal_matter, specialist, ministry, evidence_root):
    position = _recorded(normal_matter, ministry, specialist, uploads=[_pdf()])

    assert position.url == ""
    link = DocumentLink.objects.get(external_position=position)
    assert link.document.matter_id == normal_matter.pk
    assert link.document.current_version is not None


def test_a_position_may_carry_both_sources(normal_matter, specialist, ministry, evidence_root):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL, uploads=[_pdf()])

    assert position.url == POSITION_URL
    assert position.document_links.count() == 1


def test_a_position_with_none_of_the_three_sources_is_refused(normal_matter, specialist, ministry):
    with pytest.raises(DomainError) as refusal:
        _recorded(normal_matter, ministry, specialist)

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_SOURCE
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_a_text_only_position_is_recorded(normal_matter, specialist, ministry):
    """The case the source rule was widened for: feedback with nowhere else to live.

    A member association answers a consultation in two sentences by e-mail. No
    file worth keeping, no page anybody published — and before this the record
    was refused, which is what made people paste a ministry's front page into
    the link box to get past it (docs/adr/0084 §3, amended 2026-09-16).
    """
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        summary="Toetab eelnõu, kuid soovib pikemat üleminekuaega.",
    )

    assert position.summary == "Toetab eelnõu, kuid soovib pikemat üleminekuaega."
    assert position.url == ""
    assert position.document_links.count() == 0
    assert position.organisation_id == ministry.pk


def test_whitespace_is_not_a_written_position(normal_matter, specialist, ministry):
    """A `Seisukoht` of three spaces records nothing, and the service says so.

    The text is trimmed *before* the source rule reads it, so the one thing that
    cannot happen is a record that passed the rule and then stored an empty
    column (`_external_position_source`).
    """
    with pytest.raises(DomainError) as refusal:
        _recorded(normal_matter, ministry, specialist, summary="   \n  ")

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_SOURCE
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


@pytest.mark.parametrize(
    "sources",
    [
        pytest.param({"summary": "Toetab."}, id="text"),
        pytest.param({"url": POSITION_URL}, id="link"),
        pytest.param({"summary": "Toetab.", "url": POSITION_URL}, id="text+link"),
        pytest.param({"summary": "Toetab.", "uploads": [_pdf()]}, id="text+file"),
        pytest.param({"url": POSITION_URL, "uploads": [_pdf()]}, id="link+file"),
        pytest.param(
            {"summary": "Toetab.", "url": POSITION_URL, "uploads": [_pdf()]},
            id="text+link+file",
        ),
    ],
)
def test_every_combination_of_the_three_sources_saves(
    normal_matter, specialist, ministry, evidence_root, sources
):
    """Any one alone is enough and any combination is ordinary.

    A ministry that publishes a page, sends the paper *and* summarises it in a
    covering mail has stated one position with three sources, not three
    positions (docs/adr/0084 §3, amended 2026-09-16).
    """
    position = _recorded(normal_matter, ministry, specialist, **sources)

    assert MatterExternalPosition.objects.filter(matter=normal_matter).count() == 1
    assert bool(position.summary) == ("summary" in sources)
    assert bool(position.url) == ("url" in sources)
    assert position.document_links.count() == len(sources.get("uploads", ()))


def test_a_position_with_no_organisation_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError) as refusal:
        _recorded(normal_matter, None, specialist, url=POSITION_URL)

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_ORGANISATION
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_a_refused_upload_takes_the_position_with_it(
    normal_matter, specialist, ministry, evidence_root
):
    """All or none. A position claiming a file it does not have is worse than a
    refusal, so the capture and the record land together (docs/adr/0075 §8)."""
    from app.documents.uploads import UploadRejected

    bad = SimpleUploadedFile("seisukoht.exe", b"MZ ei ole pdf", content_type="application/pdf")

    with pytest.raises((UploadRejected, DomainError)):
        _recorded(normal_matter, ministry, specialist, uploads=[bad])

    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()
    assert not Document.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §4 — the organisation, and several positions from one of them
# ---------------------------------------------------------------------------


def test_one_organisation_may_state_several_positions(normal_matter, specialist, ministry):
    first = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    second = _recorded(
        normal_matter, ministry, specialist, url="https://rahandusministeerium.ee/uudised/teine"
    )

    assert first.pk != second.pk
    assert MatterExternalPosition.objects.filter(organisation=ministry).count() == 2


def test_two_positions_may_share_one_address(normal_matter, specialist, ministry):
    """A page carrying two bodies' positions is one address filed twice, and it
    is a legitimate pair of records rather than a duplicate."""
    other = factories.OrganisationFactory(name="Kliimaministeerium")

    _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    _recorded(normal_matter, other, specialist, url=POSITION_URL)

    assert MatterExternalPosition.objects.filter(url=POSITION_URL).count() == 2


def test_the_catalogue_row_is_protected_from_deletion(normal_matter, specialist, ministry):
    from django.db.models import ProtectedError

    _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    with pytest.raises(ProtectedError):
        ministry.delete()


# ---------------------------------------------------------------------------
# §5 — the date, at the precision it is known to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("precision", "anchor", "reads"),
    [
        (DatePrecision.EXACT.value, dt.date(2026, 3, 14), "14.3.2026"),
        (DatePrecision.MONTH.value, dt.date(2026, 3, 1), "märts 2026"),
        (DatePrecision.QUARTER.value, dt.date(2026, 1, 1), "I kvartal 2026"),
        (DatePrecision.YEAR.value, dt.date(2026, 1, 1), "2026"),
    ],
)
def test_a_date_reads_at_the_precision_it_was_recorded_to(
    normal_matter, specialist, ministry, precision, anchor, reads
):
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        url=POSITION_URL,
        stated_on=anchor,
        stated_on_precision=precision,
    )

    assert position.stated_on == anchor
    assert position.stated_on_precision == precision
    assert position.display_date == reads


def test_an_unknown_date_is_stored_as_nothing_and_never_as_today(
    normal_matter, specialist, ministry
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    assert position.stated_on is None
    assert position.display_date == ""
    # Absence has no precision, and the database says so as well.
    assert position.stated_on_precision == DatePrecision.EXACT.value


def test_an_undated_row_cannot_carry_a_precision(normal_matter, ministry):
    with pytest.raises(IntegrityError), transaction.atomic():
        MatterExternalPosition.objects.create(
            matter=normal_matter,
            organisation=ministry,
            url=POSITION_URL,
            stated_on=None,
            stated_on_precision=DatePrecision.MONTH.value,
        )


def test_an_unknown_precision_is_refused_by_the_service(normal_matter, specialist, ministry):
    with pytest.raises(DomainError):
        _recorded(
            normal_matter,
            ministry,
            specialist,
            url=POSITION_URL,
            stated_on=STATED_ON,
            stated_on_precision="MIDAGI_MUUD",
        )


# ---------------------------------------------------------------------------
# §6 — the address: what is accepted, and how it is rendered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "ftp://example.org/fail.pdf",
        "vbscript:msgbox(1)",
        "koda.ee/ilma-skeemita",
        "https://",
        "https://user:pw@/uudised",
    ],
)
def test_a_hostile_or_malformed_address_is_refused(url):
    with pytest.raises(DomainError):
        normalize_external_position_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://rahandusministeerium.ee/uudised/x",
        "http://vana.example.org/seisukoht",
        "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:1",
    ],
)
def test_a_public_http_address_is_accepted(url):
    assert normalize_external_position_url(url) == url


def test_an_over_long_address_is_refused_rather_than_truncated():
    with pytest.raises(DomainError):
        normalize_external_position_url("https://example.org/" + "a" * 1200)


def test_an_empty_address_is_not_a_refusal_on_its_own():
    assert normalize_external_position_url("") == ""
    assert normalize_external_position_url(None) == ""


def test_the_link_is_labelled_by_its_host(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    assert position.link_label == "rahandusministeerium.ee"


def test_a_link_carrying_credentials_never_shows_them(normal_matter, ministry):
    """The label is the parsed host, so a password in the authority cannot reach
    a rendered page (red-team finding F-2)."""
    position = MatterExternalPosition.objects.create(
        matter=normal_matter,
        organisation=ministry,
        url="https://kasutaja:parool@example.org/seisukoht",
    )

    assert position.link_label == "example.org"


# ---------------------------------------------------------------------------
# §7 — the document, its role, and the one evidence pipeline
# ---------------------------------------------------------------------------


def test_the_attached_document_carries_the_external_position_role(
    normal_matter, specialist, ministry, evidence_root
):
    position = _recorded(normal_matter, ministry, specialist, uploads=[_pdf()])

    document = DocumentLink.objects.get(external_position=position).document
    assert document.role == DocumentRole.EXTERNAL_POSITION


def test_the_document_uses_the_existing_version_pipeline(
    normal_matter, specialist, ministry, evidence_root
):
    position = _recorded(normal_matter, ministry, specialist, uploads=[_pdf()])

    version = DocumentLink.objects.get(external_position=position).document.current_version
    assert version is not None
    assert version.sha256
    assert version.size_bytes > 0
    assert version.original_filename == "seisukoht.pdf"


def test_the_link_table_gained_one_target_and_keeps_its_exactly_one_rule(
    normal_matter, specialist, ministry, evidence_root
):
    assert "external_position" in TARGET_FIELDS
    position = _recorded(normal_matter, ministry, specialist, uploads=[_pdf()])
    link = DocumentLink.objects.get(external_position=position)

    with pytest.raises(IntegrityError), transaction.atomic():
        # Naming two records at once is what the CHECK refuses, and the new
        # column is inside it.
        DocumentLink.objects.create(
            document=link.document,
            external_position=position,
            entry=factories.EntryFactory(matter=normal_matter, author=specialist),
        )


def test_a_document_cannot_be_linked_across_matters(specialist, ministry, evidence_root):
    from app.documents.services import link_document_to_record

    here = factories.MatterFactory(owner=specialist)
    elsewhere = factories.MatterFactory(owner=specialist)
    position = _recorded(here, ministry, specialist, url=POSITION_URL)
    stray = factories.DocumentFactory(matter=elsewhere)

    with pytest.raises(DomainError):
        link_document_to_record(document=stray, record=position, actor=specialist)


def test_a_restricted_position_hides_its_document_link(specialist, reader, ministry, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    position = _recorded(matter, ministry, specialist, uploads=[_pdf()])
    MatterExternalPosition.objects.filter(pk=position.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert DocumentLink.objects.visible_to(reader).filter(external_position=position).count() == 0
    mine = DocumentLink.objects.visible_to(specialist).filter(external_position=position)
    assert mine.count() == 1


# ---------------------------------------------------------------------------
# §8 — the optional `Kaasamine` relation
# ---------------------------------------------------------------------------


def test_a_position_may_answer_an_engagement(normal_matter, specialist, ministry):
    engagement = add_engagement(
        matter=normal_matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="liikmed", actor=specialist
    )

    position = _recorded(
        normal_matter, ministry, specialist, url=POSITION_URL, engagement=engagement
    )

    assert position.engagement_id == engagement.pk


def test_an_unsolicited_position_needs_no_engagement(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    assert position.engagement is None


def test_an_engagement_on_another_matter_is_refused(specialist, ministry):
    here = factories.MatterFactory(owner=specialist)
    elsewhere = factories.MatterFactory(owner=specialist)
    stray = add_engagement(
        matter=elsewhere, kind=EngagementKind.EMAIL_CAMPAIGN, title="liikmed", actor=specialist
    )

    with pytest.raises(DomainError):
        _recorded(here, ministry, specialist, url=POSITION_URL, engagement=stray)

    assert not MatterExternalPosition.objects.filter(matter=here).exists()


# ---------------------------------------------------------------------------
# §9 — the chronology
# ---------------------------------------------------------------------------


def _timeline(matter, user):
    items, _ = matter_timeline(matter=matter, user=user)
    return items


def test_the_chronology_names_the_organisation_and_never_the_address(
    normal_matter, specialist, ministry
):
    _recorded(
        normal_matter,
        ministry,
        specialist,
        url=POSITION_URL,
        stated_on=STATED_ON,
        summary="Toetab eelnõu.",
    )

    row = next(item for item in _timeline(normal_matter, specialist) if item.external_position)

    assert row.milestone.what == "Teiste arvamus: Rahandusministeerium"
    assert row.milestone.display_date == "14.3.2026"
    assert row.milestone.sub == "Toetab eelnõu."
    assert [link.label for link in row.milestone.links] == ["rahandusministeerium.ee"]
    assert POSITION_URL not in row.milestone.what
    assert POSITION_URL not in row.milestone.sub


def test_an_undated_position_says_so_rather_than_printing_the_day_it_was_typed(
    normal_matter, specialist, ministry
):
    _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    row = next(item for item in _timeline(normal_matter, specialist) if item.external_position)

    assert row.milestone.display_date == "Kuupäev teadmata"


def test_the_linked_engagement_reads_on_the_row(normal_matter, specialist, ministry):
    engagement = add_engagement(
        matter=normal_matter, kind=EngagementKind.EMAIL_CAMPAIGN, title="liikmed", actor=specialist
    )
    _recorded(normal_matter, ministry, specialist, url=POSITION_URL, engagement=engagement)

    row = next(item for item in _timeline(normal_matter, specialist) if item.external_position)

    assert "Vastus kaasamisele: liikmed" in row.milestone.sub


def test_the_attached_file_reads_under_the_row_and_adds_no_line(
    normal_matter, specialist, ministry, evidence_root
):
    _recorded(normal_matter, ministry, specialist, uploads=[_pdf()])

    items = _timeline(normal_matter, specialist)
    rows = [item for item in items if item.external_position]

    assert len(rows) == 1
    assert [file.label for file in rows[0].files] == ["seisukoht.pdf"]


def test_a_future_position_is_not_yet_history(normal_matter, specialist, ministry):
    from django.utils import timezone

    _recorded(
        normal_matter,
        ministry,
        specialist,
        url=POSITION_URL,
        stated_on=timezone.localdate() + dt.timedelta(days=30),
    )

    assert not [item for item in _timeline(normal_matter, specialist) if item.external_position]


def test_a_reader_who_may_not_see_the_position_gets_no_row(specialist, reader, ministry):
    matter = factories.MatterFactory(owner=specialist)
    position = _recorded(matter, ministry, specialist, url=POSITION_URL)
    MatterExternalPosition.objects.filter(pk=position.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert not [item for item in _timeline(matter, reader) if item.external_position]
    assert [item for item in _timeline(matter, specialist) if item.external_position]


def test_recording_a_position_writes_no_entry(normal_matter, specialist, ministry):
    _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    assert not Entry.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §10 — the audit trail
# ---------------------------------------------------------------------------


def test_recording_writes_a_recorded_event_and_a_source_event(normal_matter, specialist, ministry):
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        url=POSITION_URL,
        stated_on=dt.date(2026, 3, 1),
        stated_on_precision=DatePrecision.MONTH.value,
    )

    recorded = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_RECORDED).get()
    assert recorded.actor_id == specialist.pk
    assert recorded.object_id == position.pk
    assert recorded.summary == "Rahandusministeerium"
    assert recorded.payload["organisation"] == str(ministry.pk)
    assert recorded.payload["stated_on"] == "2026-03-01"
    assert recorded.payload["stated_on_precision"] == DatePrecision.MONTH.value
    assert recorded.payload["has_url"] is True

    source = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED).get()
    assert source.payload == {"url_from": None, "url_to": POSITION_URL}


def test_a_document_only_position_records_the_link(
    normal_matter, specialist, ministry, evidence_root
):
    _recorded(normal_matter, ministry, specialist, uploads=[_pdf()])

    linked = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_DOCUMENT_LINKED).get()
    assert linked.summary == "seisukoht.pdf"
    assert linked.actor_id == specialist.pk
    # And no source event, because there is no address to have moved.
    assert not _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED).exists()


def test_a_correction_records_what_moved_with_both_values(normal_matter, specialist, ministry):
    other = factories.OrganisationFactory(name="Kliimaministeerium")
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL, stated_on=STATED_ON)

    corrected = correct_external_position(
        position=position,
        organisation=other,
        url="https://kliimaministeerium.ee/uudised/x",
        stated_on=dt.date(2026, 4, 2),
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Vastu.",
        engagement=None,
        actor=specialist,
    )

    assert corrected.organisation_id == other.pk
    event = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_CORRECTED).get()
    assert set(event.payload["fields"]) == {"organisation_id", "url", "stated_on", "summary"}
    assert event.payload["organisation_from"] == str(ministry.pk)
    assert event.payload["organisation_to"] == str(other.pk)
    assert event.payload["stated_on_from"] == "2026-03-14"
    assert event.payload["stated_on_to"] == "2026-04-02"

    source = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED).latest(
        "created_at"
    )
    assert source.payload["url_from"] == POSITION_URL
    assert source.payload["url_to"] == "https://kliimaministeerium.ee/uudised/x"


def test_a_correction_that_changes_nothing_writes_nothing(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    before = ChangeEvent.objects.filter(matter=normal_matter).count()

    correct_external_position(
        position=position,
        organisation=ministry,
        url=POSITION_URL,
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="",
        engagement=None,
        actor=specialist,
    )

    assert ChangeEvent.objects.filter(matter=normal_matter).count() == before


def test_a_precision_correction_is_audited_even_when_the_anchor_does_not_move(
    normal_matter, specialist, ministry
):
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        url=POSITION_URL,
        stated_on=dt.date(2026, 10, 1),
        stated_on_precision=DatePrecision.MONTH.value,
    )

    correct_external_position(
        position=position,
        organisation=ministry,
        url=POSITION_URL,
        stated_on=dt.date(2026, 10, 1),
        stated_on_precision=DatePrecision.QUARTER.value,
        summary="",
        engagement=None,
        actor=specialist,
    )

    event = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_CORRECTED).get()
    assert event.payload["stated_on_precision_from"] == DatePrecision.MONTH.value
    assert event.payload["stated_on_precision_to"] == DatePrecision.QUARTER.value


def test_none_of_the_four_events_is_a_chronology_event_type():
    """The chronology renders the position from the canonical record, so reading
    the events as well would state one act twice (docs/adr/0074 §14)."""
    for event_type in (
        ChangeEventType.EXTERNAL_POSITION_RECORDED,
        ChangeEventType.EXTERNAL_POSITION_CORRECTED,
        ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED,
        ChangeEventType.EXTERNAL_POSITION_DOCUMENT_LINKED,
    ):
        assert event_type not in TIMELINE_EVENT_TYPES


# ---------------------------------------------------------------------------
# §11 — optimistic concurrency
# ---------------------------------------------------------------------------


def test_a_stale_correction_is_refused_and_writes_nothing(
    normal_matter, specialist, other_specialist, ministry
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    stale = external_position_revision(position)

    correct_external_position(
        position=position,
        organisation=ministry,
        url="https://rahandusministeerium.ee/uudised/parandatud",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Esimene parandus.",
        engagement=None,
        actor=other_specialist,
    )
    position.refresh_from_db()

    with pytest.raises(ExternalPositionConflict) as conflict:
        correct_external_position(
            position=position,
            organisation=ministry,
            url="https://rahandusministeerium.ee/uudised/teine",
            stated_on=dt.date(2026, 5, 5),
            stated_on_precision=DatePrecision.EXACT.value,
            summary="Teine parandus.",
            engagement=None,
            actor=specialist,
            expected_revision=stale,
        )

    assert str(conflict.value) == EXTERNAL_POSITION_EDIT_CONFLICT
    position.refresh_from_db()
    # Neither the metadata nor the source moved.
    assert position.url == "https://rahandusministeerium.ee/uudised/parandatud"
    assert position.summary == "Esimene parandus."
    assert position.stated_on is None


def test_a_current_revision_is_accepted(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    corrected = correct_external_position(
        position=position,
        organisation=ministry,
        url=POSITION_URL,
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Täpsustus.",
        engagement=None,
        actor=specialist,
        expected_revision=external_position_revision(position),
    )

    assert corrected.summary == "Täpsustus."


def test_a_correction_may_not_empty_the_last_of_the_three(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    with pytest.raises(DomainError) as refusal:
        correct_external_position(
            position=position,
            organisation=ministry,
            url="",
            stated_on=None,
            stated_on_precision=DatePrecision.EXACT.value,
            summary="",
            engagement=None,
            actor=specialist,
        )

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_SOURCE
    position.refresh_from_db()
    assert position.url == POSITION_URL


def test_a_text_backed_position_may_lose_its_address(normal_matter, specialist, ministry):
    """The correction reads what the save would *result* in, not what is stored.

    Emptying the link of a position whose `Seisukoht` holds what the ministry
    wrote leaves a fully sourced record, so it is an ordinary correction — the
    same shape as the document-backed case below it, through the third source
    rather than the second (docs/adr/0084 §3, amended 2026-09-16).
    """
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    corrected = correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab eelnõu.",
        engagement=None,
        actor=specialist,
    )

    assert corrected.url == ""
    assert corrected.summary == "Toetab eelnõu."


def test_a_text_only_position_may_not_have_its_text_emptied(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")

    with pytest.raises(DomainError) as refusal:
        correct_external_position(
            position=position,
            organisation=ministry,
            url="",
            stated_on=None,
            stated_on_precision=DatePrecision.EXACT.value,
            summary="",
            engagement=None,
            actor=specialist,
        )

    assert str(refusal.value) == EXTERNAL_POSITION_NEEDS_SOURCE
    position.refresh_from_db()
    assert position.summary == "Toetab eelnõu."


def test_a_text_only_position_may_have_its_text_replaced(normal_matter, specialist, ministry):
    """Correcting the wording is not unsourcing the record."""
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")

    corrected = correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab eelnõu, kuid soovib pikemat üleminekuaega.",
        engagement=None,
        actor=specialist,
    )

    assert corrected.summary == "Toetab eelnõu, kuid soovib pikemat üleminekuaega."
    assert corrected.url == ""


def test_a_document_backed_position_may_lose_its_address(
    normal_matter, specialist, ministry, evidence_root
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL, uploads=[_pdf()])

    corrected = correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="",
        engagement=None,
        actor=specialist,
    )

    assert corrected.url == ""
    assert corrected.document_links.count() == 1


# ---------------------------------------------------------------------------
# §12 — a closed Matter
# ---------------------------------------------------------------------------


@pytest.fixture
def closed_matter(normal_matter, specialist):
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    normal_matter.refresh_from_db()
    return normal_matter


def test_a_closed_matter_refuses_a_new_position(closed_matter, specialist, ministry):
    with pytest.raises(DomainError):
        _recorded(closed_matter, ministry, specialist, url=POSITION_URL)

    assert not MatterExternalPosition.objects.filter(matter=closed_matter).exists()


def test_a_closed_matter_refuses_a_correction(normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    with pytest.raises(DomainError):
        correct_external_position(
            position=position,
            organisation=ministry,
            url="https://rahandusministeerium.ee/uudised/muu",
            stated_on=None,
            stated_on_precision=DatePrecision.EXACT.value,
            summary="",
            engagement=None,
            actor=specialist,
        )

    position.refresh_from_db()
    assert position.url == POSITION_URL


def test_closing_a_matter_leaves_recorded_positions_alone(normal_matter, specialist, ministry):
    """Unlike a planned `Ülevaade / uudis`, a position is not an outstanding
    obligation — it is a fact that already happened, so closure does nothing to
    it at all."""
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    position.refresh_from_db()
    assert position.url == POSITION_URL
    assert MatterExternalPosition.objects.filter(matter=normal_matter).count() == 1


def test_there_is_no_route_that_deletes_a_position():
    from django.urls import get_resolver

    names = {
        pattern.name
        for pattern in get_resolver().url_patterns
        if hasattr(pattern, "url_patterns")
        for pattern in pattern.url_patterns
        if getattr(pattern, "name", None)
    }
    assert not any(name and "valine_seisukoht" in name and "kustuta" in name for name in names)


# ---------------------------------------------------------------------------
# §13 — the non-effects
# ---------------------------------------------------------------------------


def test_a_position_is_not_work(normal_matter, specialist, ministry):
    from app.workflow.models import NextAction

    before_deadline = normal_matter.response_deadline
    _recorded(normal_matter, ministry, specialist, url=POSITION_URL, stated_on=STATED_ON)
    normal_matter.refresh_from_db()

    assert normal_matter.response_deadline == before_deadline
    assert not NextAction.objects.filter(matter=normal_matter).exists()
    assert work_items.work_items(specialist) == []
    my_work = build_my_work(specialist)
    assert my_work.has_work is False
    assert my_work.bands == []
    assert my_work.undated == []
    assert my_work.overdue == 0


def test_a_position_reaches_no_search_or_archive_projection(normal_matter, specialist, ministry):
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        url=POSITION_URL,
        summary="Toetab eelnõu, kuid soovib pikemat üleminekuaega.",
    )

    before = rebuild_all().documents
    after = rebuild_all()

    assert after.documents == before
    assert not SearchDocument.objects.filter(source_object_id=position.pk).exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM search_searchdocument WHERE body_text ILIKE %s",
            ["%üleminekuaega%"],
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM search_searchdocument WHERE alias_text ILIKE %s",
            ["%rahandusministeerium.ee%"],
        )
        assert cursor.fetchone()[0] == 0


def test_a_position_is_no_submission_and_no_work_victory(normal_matter, specialist, ministry):
    from app.intelligence.models import MatterWorkVictory
    from app.submissions.models import Submission

    _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §14 — the page
# ---------------------------------------------------------------------------


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _post(client, name, matter, data=None, **kwargs):
    return client.post(
        reverse("matters:" + name, kwargs={"pk": matter.pk, **kwargs}),
        data or {},
        headers={"HX-Request": "true"},
    )


def _panel_markup(body: str) -> str:
    """The `+ Teiste arvamus` panel alone, sliced out of the page.

    **The page now renders this panel twice.** docs/adr/0091 §3 split the chip in
    two over one record and one partial, so `+ Meile saadetud tagasiside` comes
    first and carries the same field names — and `_tag_with` finds the *first*
    match. Every assertion in this file is about the second one, which is the panel
    these routes post to, so the slice has to happen before the search.

    Sliced by the two panel ids rather than by a form boundary: the ids are the
    launcher's own contract (`WORKSPACE_PANELS`), and a test that guessed where one
    `<form>` ends would break on markup that is not this file's subject.
    """
    start = body.index('id="lisa-valine-seisukoht"')
    return body[start:]


def _tag_with(body: str, needle: str) -> str:
    """The one element whose markup carries ``needle``, for attribute assertions.

    Attribute order in a rendered control is Django's widget template's, not
    ours, so asserting `value="x" checked` as a substring tests the template
    rather than the answer being held — and `class="chip__input"` sits between
    those two on a chip. This returns the tag so an assertion can ask whether it
    is checked without also asserting where the class went.

    It also scopes the question to one control. A Teema page renders several
    date boxes and `+ Kaasamine`'s carries today as well, so «today is not on
    this page» is never the claim; «today is not in *this* box» is.
    """
    index = body.index(needle)
    return body[body.rindex("<", 0, index) : body.index(">", index) + 1]


def _stated_on_box(body: str) -> str:
    """The `Seisukoha kuupäev` box this response is about.

    Two elements can carry that name, in two different kinds of response, and the
    helper answers for both:

    * a **whole Teema page** — a fresh render or a refused save — renders the
      shared panel twice since docs/adr/0091 §3, so the page is sliced to
      `+ Teiste arvamus` first and the box found inside it. Searching the page
      would find `+ Meile saadetud tagasiside`'s box, which no assertion here is
      about;
    * a **chronology-row fragment** — what `Muuda` and a correction swap in —
      holds one box, on `ExternalPositionEditForm`, which keeps Django's default
      `id_stated_on` because there is one of it per row and the row is the swap
      target.

    Deciding from the response rather than taking a parameter, because every caller
    already knows which it has and the distinction is not what any of them is
    testing.
    """
    scoped = _panel_markup(body) if 'id="lisa-valine-seisukoht"' in body else body
    return _tag_with(scoped, 'name="stated_on"')


def _as_typed(day: dt.date) -> str:
    """One day, the way `EstonianDateInput` writes it into an unbound box.

    `j.n.Y` — no leading zeros — which is not the `dd.mm.yyyy` a person types
    and a bound form hands straight back. Both are accepted on the way in, and
    a test that assumed one shape held for both would pass on the bound path
    and fail on the initial one (`app.core.widgets`).
    """
    return f"{day.day}.{day.month}.{day.year}"


def test_the_launcher_offers_the_panel_on_an_open_matter(signed_in, normal_matter, ministry):
    body = _detail(signed_in, normal_matter)

    assert 'id="lisa-valine-seisukoht"' in body
    # The chip is named by how the record reached the file. It was
    # `+ Väline seisukoht`; docs/adr/0091 §3 split it in two, and this panel is
    # the half that records what Koda found somewhere.
    assert "+ Teiste arvamus" in body
    assert "Rahandusministeerium" in body


def test_a_closed_matter_offers_no_panel(signed_in, closed_matter):
    assert 'id="lisa-valine-seisukoht"' not in _detail(signed_in, closed_matter)


def test_the_panel_records_a_url_only_position(signed_in, normal_matter, ministry):
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {"organisation": str(ministry.pk), "url": POSITION_URL, "position_precision": "EXACT"},
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.url == POSITION_URL
    assert position.organisation_id == ministry.pk


def test_the_panel_records_a_file_only_position(signed_in, normal_matter, ministry, evidence_root):
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "attachments": _pdf(),
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.url == ""
    assert position.document_links.count() == 1


def test_the_panel_records_a_text_only_position(signed_in, normal_matter, ministry):
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "summary": "Ministeerium toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.summary == "Ministeerium toetab eelnõu."
    assert position.url == ""
    assert position.document_links.count() == 0


def test_a_save_recording_nothing_is_refused_and_keeps_what_was_typed(
    signed_in, normal_matter, ministry
):
    """All three boxes empty is the only refusal left, and it names all three.

    The organisation, the date and the `Seotud kaasamine` the person had already
    answered come back in their controls: losing them would cost somebody the
    record they opened the panel to make (docs/adr/0084 §3, amended
    2026-09-16).
    """
    engagement = add_engagement(
        matter=normal_matter,
        kind=EngagementKind.EMAIL_CAMPAIGN,
        title="liikmed",
        occurred_on=dt.date(2026, 2, 1),
        actor=None,
    )

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "stated_on": "14.03.2026",
            "engagement": str(engagement.pk),
            "summary": "",
            "url": "",
        },
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()
    assert EXTERNAL_POSITION_NEEDS_SOURCE in body
    # Everything the person had already answered is still in its control —
    # in **this** panel's controls. `+ Meile saadetud tagasiside` renders the same
    # field names above it and is unbound, so a search over the whole page would
    # find its empty twin and pass or fail for the wrong reason
    # (docs/adr/0091 §3.5, `_panel_markup`).
    panel = _panel_markup(body)
    assert 'value="14.03.2026"' in _stated_on_box(body)
    assert "selected" in _tag_with(panel, f'value="{engagement.pk}"')
    assert "checked" in _tag_with(panel, f'value="{ministry.pk}"')


def test_a_hostile_address_is_refused_on_the_page_with_the_value_returned(
    signed_in, normal_matter, ministry
):
    typed = "javascript:alert(1)"

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {"organisation": str(ministry.pk), "url": typed, "position_precision": "EXACT"},
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()
    assert "http:// või https://" in body
    assert typed in body


def test_a_position_with_no_organisation_is_refused_on_the_page(signed_in, normal_matter):
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {"url": POSITION_URL, "position_precision": "EXACT"},
    )

    assert response.status_code == 400
    assert EXTERNAL_POSITION_NEEDS_ORGANISATION in response.content.decode()


def test_a_crafted_post_naming_another_matters_engagement_is_refused(
    signed_in, normal_matter, specialist, ministry
):
    elsewhere = factories.MatterFactory(owner=specialist)
    stray = add_engagement(
        matter=elsewhere, kind=EngagementKind.EMAIL_CAMPAIGN, title="mujal", actor=specialist
    )

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "url": POSITION_URL,
            "engagement": str(stray.pk),
            "position_precision": "EXACT",
        },
    )

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_a_closed_matter_refuses_a_crafted_post(signed_in, closed_matter, ministry):
    response = _post(
        signed_in,
        "add_external_position",
        closed_matter,
        {"organisation": str(ministry.pk), "url": POSITION_URL, "position_precision": "EXACT"},
    )

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=closed_matter).exists()


def test_the_chronology_renders_a_safe_link_and_no_raw_address(
    signed_in, normal_matter, specialist, ministry
):
    _recorded(normal_matter, ministry, specialist, url=POSITION_URL, stated_on=STATED_ON)

    body = _detail(signed_in, normal_matter)

    assert "Teiste arvamus: Rahandusministeerium" in body
    assert 'rel="noopener noreferrer"' in body
    assert 'target="_blank"' in body
    assert ">rahandusministeerium.ee<" in body
    assert "avaneb uues aknas" in body


def test_the_row_offers_a_correction_and_saves_it(signed_in, normal_matter, specialist, ministry):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)

    opened = signed_in.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        )
    )
    assert opened.status_code == 200
    assert POSITION_URL in opened.content.decode()

    saved = _post(
        signed_in,
        "update_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "url": POSITION_URL,
            "summary": "Toetab.",
            "position_precision": "EXACT",
            "revision": external_position_revision(position),
        },
        position_id=position.pk,
    )

    assert saved.status_code == 200
    position.refresh_from_db()
    assert position.summary == "Toetab."


def test_a_stale_correction_from_the_page_answers_409_and_writes_nothing(
    signed_in, normal_matter, specialist, other_specialist, ministry
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    stale = external_position_revision(position)
    correct_external_position(
        position=position,
        organisation=ministry,
        url=POSITION_URL,
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Kolleegi parandus.",
        engagement=None,
        actor=other_specialist,
    )

    response = _post(
        signed_in,
        "update_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "url": POSITION_URL,
            "summary": "Minu parandus.",
            "position_precision": "EXACT",
            "revision": stale,
        },
        position_id=position.pk,
    )
    body = response.content.decode()

    assert response.status_code == 409
    assert EXTERNAL_POSITION_EDIT_CONFLICT in body
    assert "Minu parandus." in body
    assert "Kolleegi parandus." in body
    # The stale token is not advanced: the next submit may not overwrite what
    # the other writer saved (QA-09).
    assert stale in body
    position.refresh_from_db()
    assert position.summary == "Kolleegi parandus."


def test_a_closed_matter_refuses_a_crafted_correction(
    signed_in, normal_matter, specialist, ministry
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    response = _post(
        signed_in,
        "update_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "url": "https://rahandusministeerium.ee/uudised/muu",
            "position_precision": "EXACT",
            "revision": external_position_revision(position),
        },
        position_id=position.pk,
    )

    assert response.status_code == 400
    position.refresh_from_db()
    assert position.url == POSITION_URL


def test_a_reader_may_not_write_a_position(client, reader, normal_matter, ministry):
    client.force_login(reader)

    response = client.post(
        reverse("matters:add_external_position", kwargs={"pk": normal_matter.pk}),
        {"organisation": str(ministry.pk), "url": POSITION_URL},
    )

    assert response.status_code == 404
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_a_restricted_position_is_invisible_to_a_reader_on_the_page(
    client, reader, specialist, normal_matter, ministry
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    MatterExternalPosition.objects.filter(pk=position.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    client.force_login(reader)

    body = _detail(client, normal_matter)

    assert "Teiste arvamus: Rahandusministeerium" not in body


def test_a_reader_may_not_open_the_correction_form(
    client, reader, normal_matter, specialist, ministry
):
    position = _recorded(normal_matter, ministry, specialist, url=POSITION_URL)
    client.force_login(reader)

    response = client.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        )
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# §16 — the organisation catalogue is the shared one
#
# `Organisatsioon` is answered by the control docs/adr/0073 built for `Saatja`
# and `Adressaat`, over the one `organisations.Organisation` catalogue and
# through `resolve_organisation_name`. These assert that it is *the same pool*
# rather than a ministry shortlist that happens to look like one — the failure
# mode is a picker that renders eight chips and silently validates against
# eight rows, which reads correctly right up to the day somebody needs the
# ninth body.
# ---------------------------------------------------------------------------


@pytest.fixture
def catalogue(db, ministry):
    """A catalogue bigger than any shortlist, so «the tail» is a real state."""
    bodies = [ministry]
    bodies.extend(
        factories.OrganisationFactory(name=name)
        for name in (
            "Eesti Kaubandus-Tööstuskoda",
            "Eesti Tööandjate Keskliit",
            "Justiitsministeerium",
            "Kliimaministeerium",
            "Majandus- ja Kommunikatsiooniministeerium",
            "Rahvusraamatukogu",
            "Riigikantselei",
            "Sotsiaalministeerium",
            "Tartu Ülikool",
            "Ühistranspordikeskus",
        )
    )
    return bodies


def test_the_panel_validates_against_the_whole_shared_catalogue(catalogue, specialist):
    """The same pool `Adressaat` validates against, not the chips on screen.

    Both forms point their field at `Organisation.objects.order_by("name")`;
    narrowing either to what is rendered would refuse a correct answer given
    through the search, which is the defect docs/adr/0073 names by hand.
    """
    from app.matters.forms import CompactExternalPositionForm, MatterCreateForm

    panel = CompactExternalPositionForm(viewer=specialist)
    uus_teema = MatterCreateForm(viewer=specialist)

    pool = {organisation.pk for organisation in Organisation.objects.all()}
    assert {row.pk for row in panel.fields["organisation"].queryset} == pool
    assert {row.pk for row in uus_teema.fields["addressee_organisation"].queryset} == pool
    assert len(pool) == len(catalogue)


def test_every_institution_is_offered_even_when_it_is_not_a_chip(catalogue, specialist):
    """The shortlist is presentation; the tail is rendered too, and `hidden`.

    `organisation_split` is where one stops and the other starts, and the union
    of the two is the whole catalogue — which is what lets the search reveal a
    body rather than describe one.
    """
    from app.matters.forms import CompactExternalPositionForm

    panel = CompactExternalPositionForm(viewer=specialist)

    offered = {organisation.pk for organisation in panel.organisation_offered}
    assert offered == {organisation.pk for organisation in Organisation.objects.all()}
    assert panel.organisation_split is not None
    assert len(panel.organisation_chip_choices) == panel.organisation_split
    assert panel.organisation_tail_choices, "a catalogue this size must have a searchable tail"


def test_the_recorded_spellings_reach_the_control(catalogue, specialist):
    """«MKM» finds the ministry it names, through a recorded alias.

    The same `organisation_alias_terms()` the Uus teema picker is given. A
    search that fell back to substring matching would find nothing for an
    abbreviation nobody typed into the name.
    """
    from app.matters.forms import CompactExternalPositionForm
    from app.organisations.models import OrganisationAlias

    mkm = Organisation.objects.get(name="Majandus- ja Kommunikatsiooniministeerium")
    OrganisationAlias.objects.create(organisation=mkm, alias="MKM")

    panel = CompactExternalPositionForm(viewer=specialist)

    assert (
        "mkm" in str(panel.fields["organisation"].widget.alias_terms.get(str(mkm.pk), "")).lower()
    )


def test_a_body_outside_the_shortlist_saves_from_the_panel(signed_in, normal_matter, catalogue):
    """The answer the search exists for, posted the way the browser posts it."""
    from app.matters.forms import CompactExternalPositionForm

    panel = CompactExternalPositionForm(viewer=None)
    tail_body = Organisation.objects.order_by("name").last()

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(tail_body.pk),
            "position_precision": "EXACT",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    assert panel.fields["organisation"].queryset.filter(pk=tail_body.pk).exists()
    assert MatterExternalPosition.objects.get(matter=normal_matter).organisation_id == tail_body.pk


def test_a_new_organisation_is_created_through_the_picker_and_then_selected(
    signed_in, normal_matter
):
    """`+ Lisa uus organisatsioon` — the shared add-new path, not a second one.

    The typed name posts as `organisation_name`, `resolve_addressee` hands it to
    `resolve_organisation_name` inside the save's own transaction, and the
    position points at the row that came back (docs/adr/0073, docs/adr/0084 §2).
    """
    before = Organisation.objects.count()

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation_name": "Eesti Uus Selts MTÜ",
            "position_precision": "EXACT",
            "summary": "Ei toeta eelnõu praegusel kujul.",
        },
    )

    assert response.status_code == 200
    assert Organisation.objects.count() == before + 1
    created = Organisation.objects.get(name="Eesti Uus Selts MTÜ")
    assert MatterExternalPosition.objects.get(matter=normal_matter).organisation_id == created.pk


def test_a_typed_name_that_already_names_a_body_reuses_it(signed_in, normal_matter, ministry):
    """Typing is not creating. The normalisation is the shared one, so «  Rahandus­
    ministeerium » with stray whitespace is the institution it already names."""
    before = Organisation.objects.count()

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation_name": "   Rahandusministeerium  ",
            "position_precision": "EXACT",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    assert Organisation.objects.count() == before
    assert MatterExternalPosition.objects.get(matter=normal_matter).organisation_id == ministry.pk


def test_a_typed_alias_reuses_the_body_it_names(signed_in, normal_matter, ministry):
    from app.organisations.models import OrganisationAlias

    OrganisationAlias.objects.create(organisation=ministry, alias="RaM")
    before = Organisation.objects.count()

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation_name": "RaM",
            "position_precision": "EXACT",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    assert Organisation.objects.count() == before
    assert MatterExternalPosition.objects.get(matter=normal_matter).organisation_id == ministry.pk


def test_a_typed_name_wins_over_a_chip_that_was_merely_left_selected(
    signed_in, normal_matter, ministry
):
    """`resolve_addressee`'s precedence, shared rather than restated here."""
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "organisation_name": "Eesti Uus Selts MTÜ",
            "position_precision": "EXACT",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.organisation.name == "Eesti Uus Selts MTÜ"


def test_an_ambiguous_typed_name_is_refused_rather_than_guessed(signed_in, normal_matter):
    """Two bodies answering one spelling is a question for a person.

    The shared refusal, reached through this panel: nothing is created, nothing
    is picked, and no position is written (`resolve_organisation_name` §7D).
    """
    from app.organisations.models import OrganisationAlias

    first = factories.OrganisationFactory(name="Eesti Kaubandus-Tööstuskoda")
    second = factories.OrganisationFactory(name="Eesti Kaubanduskoda")
    for organisation in (first, second):
        OrganisationAlias.objects.create(organisation=organisation, alias="Koda")
    before = Organisation.objects.count()

    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation_name": "Koda",
            "position_precision": "EXACT",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 400
    assert "vali nimekirjast" in response.content.decode()
    assert Organisation.objects.count() == before
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_no_position_exists_without_an_organisation_even_by_crafted_post(signed_in, normal_matter):
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {"position_precision": "EXACT", "summary": "Toetab eelnõu."},
    )

    assert response.status_code == 400
    assert EXTERNAL_POSITION_NEEDS_ORGANISATION in response.content.decode()
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


# ---------------------------------------------------------------------------
# §17 — the date box, its default, and clearing it
#
# docs/adr/0084 §2 as amended 2026-09-16: `+ Väline seisukoht` opens on today at
# `Täpne päev`, visibly; `Muuda` opens on what the record holds and never on
# today; and an emptied box is a real answer that survives a refused save.
# ---------------------------------------------------------------------------


def test_the_panel_opens_on_today_at_exact_precision(signed_in, normal_matter, ministry):
    from app.matters.forms import CompactExternalPositionForm

    panel = CompactExternalPositionForm(matter=normal_matter, viewer=None)
    today = timezone.localdate()

    assert panel["stated_on"].value() == today
    assert panel["position_precision"].value() == DatePrecision.EXACT.value
    body = _detail(signed_in, normal_matter)
    assert f'value="{_as_typed(today)}"' in _stated_on_box(body)


def test_today_is_a_visible_suggestion_and_the_posted_value_is_what_is_stored(
    signed_in, normal_matter, ministry
):
    """A default in a box a person reads is not a stamp behind their back.

    What reaches the database is whatever the browser posted — the suggestion
    if they accepted it, their own day if they typed one (docs/adr/0078 §2).
    """
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "stated_on": "14.03.2026",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.stated_on == dt.date(2026, 3, 14)
    assert position.stated_on_precision == DatePrecision.EXACT.value


def test_a_cleared_box_stores_nothing_and_stays_cleared(signed_in, normal_matter, ministry):
    """«Kuupäev teadmata» is a real answer, and the default does not override it."""
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "stated_on": "",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 200
    position = MatterExternalPosition.objects.get(matter=normal_matter)
    assert position.stated_on is None
    assert position.stated_on_precision == DatePrecision.EXACT.value
    assert EXTERNAL_POSITION_DATE_UNKNOWN in response.content.decode()


def test_a_cleared_box_survives_a_refused_save_still_cleared(signed_in, normal_matter, ministry):
    """The bound form is re-rendered, so today does not creep back in.

    An `initial` that reasserted itself on a refusal would hand the person a
    date they had deliberately removed, one `Salvesta` from storing it.
    """
    response = _post(
        signed_in,
        "add_external_position",
        normal_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "stated_on": "",
            "summary": "",
            "url": "",
        },
    )
    body = response.content.decode()

    assert response.status_code == 400
    assert 'value=""' in _stated_on_box(body) or "value=" not in _stated_on_box(body)
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_an_undated_record_reopens_undated_and_not_on_today(
    signed_in, normal_matter, specialist, ministry
):
    """`Muuda` shows what the record holds. Here that is nothing."""
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")

    response = signed_in.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        )
    )
    body = response.content.decode()

    assert response.status_code == 200
    box = _stated_on_box(body)
    assert 'value=""' in box or "value=" not in box
    assert _as_typed(timezone.localdate()) not in box


def test_a_dated_record_reopens_on_its_own_day(signed_in, normal_matter, specialist, ministry):
    position = _recorded(
        normal_matter, ministry, specialist, summary="Toetab.", stated_on=STATED_ON
    )

    response = signed_in.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        )
    )

    assert 'value="14.3.2026"' in _stated_on_box(response.content.decode())


def test_an_approximate_record_reopens_on_its_period_and_never_on_its_anchor(
    signed_in, normal_matter, specialist, ministry
):
    """The anchor is a place in a sort, not a day to hand back to somebody."""
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        summary="Toetab.",
        stated_on=dt.date(2026, 10, 1),
        stated_on_precision=DatePrecision.MONTH.value,
    )

    response = signed_in.get(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": normal_matter.pk, "position_id": position.pk},
        )
    )
    body = response.content.decode()

    assert "01.10.2026" not in body
    assert _as_typed(timezone.localdate()) not in _stated_on_box(body)
    assert 'value="2026"' in body


def test_a_correction_that_names_no_date_leaves_the_record_undated(
    normal_matter, specialist, ministry
):
    """The correction form carries no `initial`, so an undated row stays undated."""
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab.")

    corrected = correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab endiselt.",
        engagement=None,
        actor=specialist,
    )

    assert corrected.stated_on is None
    assert corrected.stated_on_precision == DatePrecision.EXACT.value


# ---------------------------------------------------------------------------
# §18 — what a text-only position does to the rest of the product
#
# Nothing. The same absences §13 and §15 hold for a linked one, asserted again
# through the source that has no link and no file — because «it reaches no
# metric» is a claim about the record, not about its URL column.
# ---------------------------------------------------------------------------


def test_a_text_only_position_reads_on_the_chronology_with_no_link(
    normal_matter, specialist, ministry
):
    position = _recorded(
        normal_matter,
        ministry,
        specialist,
        summary="Toetab eelnõu, kuid soovib pikemat üleminekuaega.",
        stated_on=STATED_ON,
    )

    rows = [item for item in _timeline(normal_matter, specialist) if item.external_position]

    assert len(rows) == 1
    milestone = rows[0].milestone
    assert milestone.what == "Teiste arvamus: Rahandusministeerium"
    assert milestone.sub == "Toetab eelnõu, kuid soovib pikemat üleminekuaega."
    assert milestone.links == ()
    assert milestone.display_date == "14.3.2026"
    assert rows[0].external_position.pk == position.pk


def test_a_text_only_position_creates_no_work_and_reaches_no_projection(
    normal_matter, specialist, ministry
):
    rebuild_all()
    before = SearchDocument.objects.count()

    _recorded(
        normal_matter,
        ministry,
        specialist,
        summary="Toetab eelnõu.",
        stated_on=STATED_ON,
    )
    normal_matter.refresh_from_db()

    assert SearchDocument.objects.count() == before
    assert normal_matter.response_deadline is None
    assert work_items.work_items(specialist) == []
    assert build_my_work(specialist).has_work is False
    assert not Entry.objects.filter(matter=normal_matter).exists()


def test_a_text_only_position_is_refused_on_a_closed_matter(closed_matter, signed_in, ministry):
    """The panel is not rendered, and that decides nothing: the POST still lands."""
    response = _post(
        signed_in,
        "add_external_position",
        closed_matter,
        {
            "organisation": str(ministry.pk),
            "position_precision": "EXACT",
            "summary": "Toetab eelnõu.",
        },
    )

    assert response.status_code == 400
    assert not MatterExternalPosition.objects.filter(matter=closed_matter).exists()


def test_a_reader_may_not_record_a_text_only_position(client, reader, normal_matter, ministry):
    client.force_login(reader)

    response = client.post(
        reverse("matters:add_external_position", kwargs={"pk": normal_matter.pk}),
        {"organisation": str(ministry.pk), "summary": "Toetab eelnõu."},
    )

    assert response.status_code == 404
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


def test_a_stale_correction_of_a_text_only_position_writes_nothing(
    normal_matter, specialist, ministry
):
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")
    stale = external_position_revision(position)
    correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab eelnõu osaliselt.",
        engagement=None,
        actor=specialist,
        expected_revision=stale,
    )

    with pytest.raises(ExternalPositionConflict):
        correct_external_position(
            position=position,
            organisation=ministry,
            url="",
            stated_on=None,
            stated_on_precision=DatePrecision.EXACT.value,
            summary="Ei toeta eelnõu.",
            engagement=None,
            actor=specialist,
            expected_revision=stale,
        )

    position.refresh_from_db()
    assert position.summary == "Toetab eelnõu osaliselt."


# ---------------------------------------------------------------------------
# §19 — the audit trail of a written position
# ---------------------------------------------------------------------------


def test_a_text_only_creation_records_it_and_writes_no_source_event(
    normal_matter, specialist, ministry
):
    """`EXTERNAL_POSITION_SOURCE_CHANGED` stays the address's own event.

    It exists because an address that quietly became a different page is the one
    way this record can lie (docs/adr/0084 §7). A position with no address has
    no such history to write, and `has_summary` on the recorded event is what
    says a written position is there.
    """
    _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")

    recorded = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_RECORDED)
    assert recorded.count() == 1
    payload = recorded.get().payload
    assert payload["has_summary"] is True
    assert payload["has_url"] is False
    assert not _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED).exists()


def test_a_corrected_position_names_the_field_and_never_quotes_it(
    normal_matter, specialist, ministry
):
    """What moved, not what it now says. The `update_engagement` rule.

    A payload carrying another organisation's words would put the same text in
    two places with two lifetimes, and the audit copy is the one nobody can
    correct.
    """
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")

    correct_external_position(
        position=position,
        organisation=ministry,
        url="",
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab eelnõu, kuid soovib pikemat üleminekuaega.",
        engagement=None,
        actor=specialist,
        expected_revision=external_position_revision(position),
    )

    corrected = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_CORRECTED)
    assert corrected.count() == 1
    payload = corrected.get().payload
    assert payload["fields"] == ["summary"]
    assert "Toetab eelnõu" not in str(payload)
    assert "üleminekuaega" not in str(payload)


def test_gaining_an_address_is_still_a_source_event(normal_matter, specialist, ministry):
    """A text-only position that later gets a link records where it now points."""
    position = _recorded(normal_matter, ministry, specialist, summary="Toetab eelnõu.")

    correct_external_position(
        position=position,
        organisation=ministry,
        url=POSITION_URL,
        stated_on=None,
        stated_on_precision=DatePrecision.EXACT.value,
        summary="Toetab eelnõu.",
        engagement=None,
        actor=specialist,
        expected_revision=external_position_revision(position),
    )

    source = _events(normal_matter, ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED)
    assert source.count() == 1
    assert source.get().payload == {"url_from": None, "url_to": POSITION_URL}
