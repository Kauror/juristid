"""The owner's user-side correction batch of 29 September 2026 (docs/adr/0120).

One module per batch rather than seven, because the seven fixes share the
fixtures that make them true and each section says which finding it holds:

1. the «Koja arvamus on saadetud» sentence is gone from `PRAEGUNE TEGEVUS`;
2. an upcoming `Oluline tähtaeg` is the next step where no `Järgmiseks` is set;
3. `+ Kaasamine` asks `Tagasisidet ootame kuni`, optional (UQ-10);
4. `Minu asjad` bands a period by when it is due, prints it in words, and each
   figure counts its own section (UQ-09);
5. a document can be re-versioned, reclassified and removed (UQ-12);
6. `Võta tagasi` is on the opinion's own row, behind a confirmation (UQ-11);
7. `Kustuta` is offered only where the deletion plan has no blocker (UQ-13);

and, across them, the rule that a date is valid input whether it is past, today
or future — what it *means* is decided on reading.
"""

from __future__ import annotations

import datetime
import re
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.links import DocumentLink
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    DOCUMENT_UNDER_LEGAL_HOLD,
    KEPT_OPINION_EVIDENCE,
    OPINION_UPLOAD_REFUSAL,
    SENT_OPINION_EVIDENCE,
    WITHDRAWN_OPINION_EVIDENCE,
    add_version_on_open_matter,
    change_document_role,
    remove_document,
    set_legal_hold,
)
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterImportantDate
from app.intelligence.services import (
    add_important_date,
    cancel_important_date,
    update_important_date,
)
from app.matters import work_items as wi
from app.matters.deletion import delete_matter, plan_matter_deletion
from app.matters.forms import CompactEngagementForm, EngagementForm
from app.matters.models import Matter, MatterEngagement
from app.matters.my_work import build_my_work
from app.matters.next_step import upcoming_milestone, without_next_step
from app.matters.removal import remove_matter_record
from app.matters.selectors import MISSING, filter_by_next_action
from app.matters.services import compose_update, edit_entry
from app.search.models import SearchDocument, SearchSourceKind
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    REMOVED_DOCUMENT_IS_NOT_EVIDENCE,
    register_sent_opinion,
    withdraw_submission,
)
from app.workflow.dates import period_bounds
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.services import complete_next_action, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: A Tuesday. Its ISO week ends on Sunday 4 October, and 1 October — the anchor
#: of «oktoober 2026» — is the Thursday of that week: the exact case UQ-09
#: reported.
TODAY = date(2026, 9, 29)


def _milestone(matter, actor, title, day, precision=DatePrecision.EXACT):
    start, end = period_bounds(day, precision)
    return add_important_date(
        matter=matter,
        title=title,
        date_value=start,
        period_end=end,
        date_precision=precision,
        actor=actor,
    )


def _matter_page(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _current_action(body: str) -> str:
    """The `PRAEGUNE TEGEVUS` section alone, so no other panel can answer for it."""
    match = re.search(
        r'<section class="curact[^"]*"[^>]*id="praegune-tegevus".*?</section>', body, re.S
    )
    assert match, "no PRAEGUNE TEGEVUS section on the page"
    return match.group(0)


def _sent_opinion(matter, specialist, organisation, capture_evidence, *, title="Koja arvamus"):
    version = capture_evidence(
        matter,
        b"%PDF-1.4 synthetic evidence",
        "koja-arvamus.pdf",
        "application/pdf",
        title=title,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
    )
    submission = register_sent_opinion(
        document=version.document,
        version=version,
        title=title,
        recipients=[organisation],
        sent_at=datetime.datetime(2026, 9, 1, 0, 0, tzinfo=datetime.UTC),
        sent_at_precision=SentAtPrecision.DATE,
        summary="Toetame eelnõu.",
        actor=specialist,
    )
    submission.refresh_from_db()
    return submission


# ---------------------------------------------------------------------------
# 1 — the continuation sentence is gone
# ---------------------------------------------------------------------------


def test_a_sent_opinion_puts_no_continuation_sentence_on_the_empty_panel(
    signed_in, specialist, organisation, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    _sent_opinion(matter, specialist, organisation, capture_evidence)

    panel = _current_action(_matter_page(signed_in, matter))

    assert "Järgmine samm on määramata" in panel
    assert "Koja arvamus on saadetud" not in panel
    assert "Menetlus võib jätkuda" not in panel
    assert "curact__continue" not in panel


# ---------------------------------------------------------------------------
# 2 — an upcoming Oluline tähtaeg is the next step when none is set
# ---------------------------------------------------------------------------


def test_an_upcoming_milestone_is_shown_as_the_next_step(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    day = timezone.localdate() + timedelta(days=2)
    _milestone(matter, specialist, "Ootan ministeeriumi tagasisidet", day)

    panel = _current_action(_matter_page(signed_in, matter))

    assert "Ootan ministeeriumi tagasisidet" in panel
    assert "Oluline tähtaeg" in panel
    assert f"{day.day}.{day.month}.{day.year}" in panel
    assert "Järgmine samm on määramata" not in panel


def test_an_explicit_next_step_outranks_any_milestone(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, specialist, "Ootan ministeeriumi tagasisidet", timezone.localdate())
    set_next_action(
        matter=matter,
        text="Koostan arvamuse",
        target_date=timezone.localdate() + timedelta(days=20),
        actor=specialist,
    )

    panel = _current_action(_matter_page(signed_in, matter))

    assert "Koostan arvamuse" in panel
    assert "Ootan ministeeriumi tagasisidet" not in panel


def test_a_past_or_cancelled_or_removed_milestone_is_never_the_next_step(specialist):
    matter = factories.MatterFactory(owner=specialist)
    past = _milestone(matter, specialist, "Möödunud", TODAY - timedelta(days=1))
    cancelled = _milestone(matter, specialist, "Tühistatud", TODAY + timedelta(days=3))
    cancel_important_date(record=cancelled, actor=specialist)
    removed = _milestone(matter, specialist, "Eemaldatud", TODAY + timedelta(days=4))
    remove_matter_record(
        matter_id=matter.pk, kind_key="tahtaeg", record_id=removed.pk, actor=specialist
    )

    records = MatterImportantDate.objects.filter(matter=matter).visible_to(specialist)

    assert past.has_passed(TODAY)
    assert upcoming_milestone(records, TODAY) is None
    assert without_next_step(Matter.objects.filter(pk=matter.pk), specialist, TODAY).exists()


def test_the_earliest_upcoming_milestone_wins_deterministically(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, specialist, "Kuu", date(2026, 10, 1), DatePrecision.MONTH)
    exact = _milestone(matter, specialist, "Täpne", date(2026, 10, 1))
    _milestone(matter, specialist, "Hilisem", date(2026, 10, 15))

    records = MatterImportantDate.objects.filter(matter=matter).visible_to(specialist)

    # Same anchor: the one whose period ends first is first.
    assert upcoming_milestone(records, TODAY) == exact
    assert upcoming_milestone(list(reversed(list(records))), TODAY) == exact


def test_a_milestone_dated_today_is_still_the_next_step(specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = _milestone(matter, specialist, "Täna", TODAY)
    records = MatterImportantDate.objects.filter(matter=matter).visible_to(specialist)

    assert upcoming_milestone(records, TODAY) == today
    assert upcoming_milestone(records, TODAY + timedelta(days=1)) is None


def test_editing_cancelling_or_removing_the_milestone_recalculates(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    record = _milestone(
        matter,
        specialist,
        "Ootan ministeeriumi tagasisidet",
        timezone.localdate() + timedelta(days=5),
    )
    later = timezone.localdate() + timedelta(days=40)
    start, end = period_bounds(later, DatePrecision.EXACT)
    update_important_date(
        record=record,
        title="Ootan ministeeriumi tagasisidet",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.EXACT,
        actor=specialist,
    )
    assert f"{later.day}.{later.month}.{later.year}" in _current_action(
        _matter_page(signed_in, matter)
    )

    remove_matter_record(
        matter_id=matter.pk, kind_key="tahtaeg", record_id=record.pk, actor=specialist
    )
    assert "Järgmine samm on määramata" in _current_action(_matter_page(signed_in, matter))


def test_the_register_and_minu_asjad_agree_with_the_matter_page(signed_in, specialist):
    """One rule: the Matter is not «järgmise tegevuseta» anywhere."""
    with_milestone = factories.MatterFactory(owner=specialist, title="Tähtajaga")
    _milestone(
        with_milestone, specialist, "Ootan vastust", timezone.localdate() + timedelta(days=3)
    )
    without = factories.MatterFactory(owner=specialist, title="Tühi")

    missing = filter_by_next_action(Matter.objects.all(), specialist, MISSING)
    assert set(missing.values_list("pk", flat=True)) == {without.pk}

    work = build_my_work(specialist, today=timezone.localdate())
    assert work.quiet_total == 1
    rows = {row.matter.pk: row for row in work.portfolio.all_rows}
    assert rows[with_milestone.pk].has_action
    assert rows[with_milestone.pk].next_action.text == "Ootan vastust"
    assert not rows[without.pk].has_action

    body = signed_in.get(reverse("matters:my_work")).content.decode()
    assert "oluline tähtaeg" in body
    assert body.count("järgmine tegevus puudub") == 1


def test_a_past_date_saves_and_does_not_become_the_next_step(specialist):
    """A retrospective record is valid input and is history, not a plan."""
    matter = factories.MatterFactory(owner=specialist)
    for precision, day in (
        (DatePrecision.EXACT, TODAY - timedelta(days=1)),
        (DatePrecision.MONTH, date(2026, 8, 1)),
        (DatePrecision.QUARTER, date(2026, 4, 1)),
        (DatePrecision.YEAR, date(2025, 1, 1)),
    ):
        _milestone(matter, specialist, f"Möödunud {precision}", day, precision)

    records = MatterImportantDate.objects.filter(matter=matter).visible_to(specialist)
    assert records.count() == 4
    assert upcoming_milestone(records, TODAY) is None


@pytest.mark.parametrize(
    "precision, day",
    [
        (DatePrecision.EXACT, TODAY - timedelta(days=30)),
        (DatePrecision.EXACT, TODAY),
        (DatePrecision.EXACT, TODAY + timedelta(days=30)),
        (DatePrecision.MONTH, date(2026, 3, 1)),
        (DatePrecision.MONTH, date(2026, 12, 1)),
        (DatePrecision.QUARTER, date(2026, 1, 1)),
        (DatePrecision.QUARTER, date(2027, 4, 1)),
        (DatePrecision.YEAR, date(2024, 1, 1)),
        (DatePrecision.YEAR, date(2028, 1, 1)),
    ],
)
def test_every_precision_accepts_past_today_and_future(specialist, precision, day):
    """Neither the next step nor a milestone refuses a date for being past or future."""
    milestone_matter = factories.MatterFactory(owner=specialist)
    record = _milestone(milestone_matter, specialist, "Tähtaeg", day, precision)
    assert record.date_value == period_bounds(day, precision)[0]

    step_matter = factories.MatterFactory(owner=specialist)
    action = set_next_action(
        matter=step_matter,
        text="Samm",
        target_date=period_bounds(day, precision)[0],
        date_precision=precision,
        actor=specialist,
    )
    assert action.status == ActionStatus.OPEN


# ---------------------------------------------------------------------------
# 3 — Kaasamine asks its feedback deadline on create, optionally
# ---------------------------------------------------------------------------


def _estonian(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def test_the_create_panel_asks_the_same_optional_deadline_as_the_correction():
    create = CompactEngagementForm().fields["feedback_deadline"]
    correct = EngagementForm().fields["feedback_deadline"]

    assert create.label == correct.label == "Tagasisidet ootame kuni"
    assert create.required is False
    assert create.initial is None


def test_the_panel_renders_the_deadline_box(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = _matter_page(signed_in, matter)
    panel = body[body.index('id="lisa-kaasamine"') :]
    panel = panel[: panel.index("</form>")]

    assert "Tagasisidet ootame kuni" in panel
    assert 'name="feedback_deadline"' in panel
    assert re.search(r'name="feedback_deadline"[^>]*value="[^"]+"', panel) is None


def test_a_round_created_with_a_deadline_is_waiting_at_once(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    deadline = timezone.localdate() + timedelta(days=3)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "liikmed", "feedback_deadline": _estonian(deadline)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:2000]
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.feedback_deadline == deadline
    assert engagement.has_open_feedback_wait
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.ENGAGEMENT_ADDED)
    assert event.payload["feedback_deadline"] == deadline.isoformat()

    waits = [
        item
        for item in wi.work_items(specialist, today=timezone.localdate())
        if item.source_type == wi.SOURCE_FEEDBACK_WAIT
    ]
    assert [item.meaning for item in waits] == [wi.MEANING_FEEDBACK_WAIT]

    page = _matter_page(signed_in, matter)
    # The reply-by day reads on the round's row; the rail column is retired
    # (docs/adr/0131 §13).
    assert "Ootame tagasisidet kuni" in page


def test_a_round_created_without_a_deadline_invents_none(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "liikmed", "feedback_deadline": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.feedback_deadline is None
    assert not engagement.has_open_feedback_wait
    assert not [
        item
        for item in wi.work_items(specialist, today=timezone.localdate())
        if item.source_type == wi.SOURCE_FEEDBACK_WAIT
    ]


def test_a_past_or_today_deadline_is_accepted_on_create(signed_in, specialist):
    """Recording a round after the fact: the reply-by day may already have gone."""
    for offset in (-10, 0):
        matter = factories.MatterFactory(owner=specialist)
        deadline = timezone.localdate() + timedelta(days=offset)
        response = signed_in.post(
            reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
            {
                "audience": "liikmed",
                "occurred_on": _estonian(timezone.localdate() - timedelta(days=20)),
                "feedback_deadline": _estonian(deadline),
            },
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert MatterEngagement.objects.get(matter=matter).feedback_deadline == deadline


def test_the_create_panel_keeps_the_date_order_rule(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    today = timezone.localdate()

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {
            "audience": "liikmed",
            "occurred_on": _estonian(today),
            "feedback_deadline": _estonian(today - timedelta(days=5)),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva." in response.content.decode()
    assert not MatterEngagement.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# 4 — Minu asjad: bands, labels and counts
# ---------------------------------------------------------------------------


def _step(owner, title, day, precision=DatePrecision.EXACT, **kwargs):
    matter = factories.MatterFactory(owner=owner, title=title)
    set_next_action(
        matter=matter,
        text=title,
        target_date=period_bounds(day, precision)[0],
        date_precision=precision,
        actor=owner,
        **kwargs,
    )
    return matter


def _band_texts(work, key):
    return [item.text for band in work.bands if band.key == key for item in band.items]


def _item(work, text):
    return next(item for band in work.bands for item in band.items if item.text == text)


@pytest.fixture
def mixed_desk(specialist):
    _step(specialist, "Täpne sel nädalal", date(2026, 10, 2))
    _step(specialist, "Täpne järgmisel nädalal", date(2026, 10, 7))
    _step(specialist, "Oktoober", date(2026, 10, 1), DatePrecision.MONTH)
    _step(specialist, "Neljas kvartal", date(2026, 10, 1), DatePrecision.QUARTER)
    _step(specialist, "Aasta", date(2026, 1, 1), DatePrecision.YEAR)
    _step(specialist, "September lõpeb sel nädalal", date(2026, 9, 1), DatePrecision.MONTH)
    waiting = factories.MatterFactory(owner=specialist, title="Kaasamine")
    MatterEngagement.objects.create(
        matter=waiting,
        title="liikmed",
        occurred_on=date(2026, 9, 20),
        feedback_deadline=date(2026, 10, 2),
    )
    return specialist


def test_exact_dates_band_as_before(mixed_desk):
    work = build_my_work(mixed_desk, today=TODAY)

    assert "Täpne sel nädalal" in _band_texts(work, wi.BAND_WEEK)
    assert "Täpne järgmisel nädalal" in _band_texts(work, wi.BAND_NEXT_30)


@pytest.mark.parametrize("title", ["Oktoober", "Neljas kvartal", "Aasta"])
def test_a_broad_period_is_not_banded_this_week_by_its_anchor(mixed_desk, title):
    work = build_my_work(mixed_desk, today=TODAY)

    assert title not in _band_texts(work, wi.BAND_WEEK)
    assert title not in _band_texts(work, wi.BAND_NEXT_30)
    assert title in _band_texts(work, wi.BAND_LATER)


def test_a_period_ending_this_week_is_still_a_period(mixed_desk):
    """docs/adr/0121 §7 narrows docs/adr/0120 §4: no day of a period — not its
    last one either — makes it this week's. It is `Hiljem` until it has ended."""
    work = build_my_work(mixed_desk, today=TODAY)

    assert "September lõpeb sel nädalal" not in _band_texts(work, wi.BAND_WEEK)
    assert "September lõpeb sel nädalal" in _band_texts(work, wi.BAND_LATER)


def test_a_wait_is_in_the_week_it_asked_for(mixed_desk):
    work = build_my_work(mixed_desk, today=TODAY)

    assert "liikmed" in _band_texts(work, wi.BAND_WEEK)


@pytest.mark.parametrize(
    "title, label",
    [("Oktoober", "oktoober 2026"), ("Neljas kvartal", "IV kvartal 2026"), ("Aasta", "2026")],
)
def test_a_broad_period_prints_in_words(mixed_desk, title, label):
    item = _item(build_my_work(mixed_desk, today=TODAY), title)

    assert item.short_date == label
    assert not re.fullmatch(r"\d{2}\.\d{2}", item.short_date)


def test_a_late_month_names_the_month_in_words(specialist):
    _step(specialist, "Juuli", date(2026, 7, 1), DatePrecision.MONTH)
    item = _item(build_my_work(specialist, today=TODAY), "Juuli")

    assert item.meaning_line == "PLAANIS juuli 2026"
    assert "07.26" not in item.meaning_line


def test_the_figures_are_the_totals_of_the_bands_they_open(mixed_desk):
    work = build_my_work(mixed_desk, today=TODAY)
    bands = {band.key: band for band in work.bands}
    figures = {figure.key: figure for figure in work.seis}

    assert figures["week"].value == bands[wi.BAND_WEEK].total == len(bands[wi.BAND_WEEK].items)
    assert figures["week"].url == f"#{wi.BAND_WEEK}"
    assert figures["week"].caption == "sel nädalal"
    # Two rows: the exact day and the wait. The month that ends on Wednesday is
    # a period, not a weekly deadline (docs/adr/0121 §7).
    assert figures["week"].value == 2


def test_the_overdue_figure_counts_the_overdue_band(specialist):
    _step(specialist, "Hilinenud", TODAY - timedelta(days=4))
    matter = _step(specialist, "Teine hilinenud", TODAY - timedelta(days=2))
    MatterEngagement.objects.create(
        matter=matter,
        title="liikmed",
        occurred_on=date(2026, 9, 1),
        feedback_deadline=TODAY - timedelta(days=1),
    )

    work = build_my_work(specialist, today=TODAY)
    bands = {band.key: band for band in work.bands}
    figures = {figure.key: figure for figure in work.seis}

    # Two rows share one Matter: the figure counts rows, as the band does.
    assert figures["overdue"].value == bands[wi.BAND_OVERDUE].total == 3
    assert figures["overdue"].url == f"#{wi.BAND_OVERDUE}"


def test_the_page_prints_the_band_anchor_and_no_two_number_month(signed_in, specialist):
    today = timezone.localdate()
    month_start = (today.replace(day=1) + timedelta(days=62)).replace(day=1)
    _step(specialist, "Kuu täpsusega samm", month_start, DatePrecision.MONTH)

    body = signed_in.get(reverse("matters:my_work")).content.decode()

    assert 'id="hiljem"' in body
    compact = f"{month_start.month:02d}.{month_start.year % 100:02d}"
    assert f">{compact}<" not in body


# ---------------------------------------------------------------------------
# 5 — correcting a document
# ---------------------------------------------------------------------------


def _upload(name="parandus.pdf", body=b"%PDF-1.4 corrected evidence"):
    return SimpleUploadedFile(name, body, content_type="application/pdf")


@pytest.fixture
def plain_document(specialist, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    version = capture_evidence(
        matter, b"%PDF-1.4 first", "vale.pdf", "application/pdf", role=DocumentRole.MEMBER_FEEDBACK
    )
    return version.document


def test_a_new_version_is_a_new_immutable_version_and_becomes_current(signed_in, plain_document):
    first = plain_document.current_version

    response = signed_in.post(
        reverse("documents:add_version", kwargs={"pk": plain_document.pk}),
        {"upload": _upload()},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse(
        "documents:document_detail", kwargs={"pk": plain_document.pk}
    )
    plain_document.refresh_from_db()
    assert Document.objects.filter(matter=plain_document.matter).count() == 1
    assert plain_document.current_version.version_number == 2
    assert plain_document.current_version.original_filename == "parandus.pdf"
    first.refresh_from_db()
    assert first.sha256 != plain_document.current_version.sha256
    assert list(
        plain_document.versions.order_by("version_number").values_list("pk", flat=True)
    ) == [
        first.pk,
        plain_document.current_version.pk,
    ]
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.EVIDENCE_VERSION_ADDED,
        object_id=plain_document.current_version.pk,
    ).exists()

    page = signed_in.get(reverse("documents:document_detail", kwargs={"pk": plain_document.pk}))
    body = page.content.decode()
    assert "Versioonid" in body and "vale.pdf" in body and "parandus.pdf" in body


def test_the_role_can_be_changed_and_is_audited(signed_in, plain_document):
    bytes_before = list(plain_document.versions.values_list("sha256", flat=True))

    response = signed_in.post(
        reverse("documents:change_role", kwargs={"pk": plain_document.pk}),
        {"role": DocumentRole.OTHER},
    )

    assert response.status_code == 302
    plain_document.refresh_from_db()
    assert plain_document.role == DocumentRole.OTHER
    assert list(plain_document.versions.values_list("sha256", flat=True)) == bytes_before
    event = ChangeEvent.objects.get(event_type=ChangeEventType.DOCUMENT_ROLE_CHANGED)
    assert event.payload == {"from": DocumentRole.MEMBER_FEEDBACK, "to": DocumentRole.OTHER}


def test_the_role_cannot_be_changed_to_an_opinion(plain_document, specialist):
    with pytest.raises(DomainError, match="Koja arvamus lisatakse teema lehel"):
        change_document_role(
            document=plain_document, role=DocumentRole.KODA_SUBMISSION_FINAL, actor=specialist
        )
    assert OPINION_UPLOAD_REFUSAL.startswith("Koja arvamus lisatakse")


def test_an_ordinary_upload_can_be_removed_and_leaves_no_trace_on_the_matter(
    signed_in, specialist, plain_document
):
    version = plain_document.current_version
    matter = plain_document.matter
    assert SearchDocument.objects.filter(
        source_kind=SearchSourceKind.DOCUMENT, source_object_id=plain_document.pk
    ).exists()

    response = signed_in.post(reverse("documents:remove", kwargs={"pk": plain_document.pk}))

    assert response.status_code == 302
    assert response["Location"] == reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    assert not Document.objects.visible_to(specialist).filter(pk=plain_document.pk).exists()
    # Kept for the audit and the evidence store.
    assert Document._base_manager.get(pk=plain_document.pk).removed_by == specialist
    assert DocumentVersion.objects.filter(pk=version.pk).exists()
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.DOCUMENT_REMOVED, object_id=plain_document.pk
    ).exists()
    assert not SearchDocument.objects.filter(
        source_kind=SearchSourceKind.DOCUMENT, source_object_id=plain_document.pk
    ).exists()
    listing = signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert "vale.pdf" not in listing.content.decode()
    assert (
        signed_in.get(reverse("documents:download", kwargs={"pk": version.pk})).status_code == 404
    )
    # A second press from a stale tab changes nothing.
    assert (
        signed_in.post(reverse("documents:remove", kwargs={"pk": plain_document.pk})).status_code
        == 404
    )
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.DOCUMENT_REMOVED).count() == 1


def test_a_removed_file_leaves_the_record_it_was_attached_to(specialist, capture_evidence):
    matter = factories.MatterFactory(owner=specialist)
    record = MatterEngagement.objects.create(matter=matter, title="liikmed")
    version = capture_evidence(matter, b"%PDF-1.4 x", "manus.pdf", "application/pdf")
    DocumentLink.objects.create(document=version.document, engagement=record)
    assert DocumentLink.objects.visible_to(specialist).count() == 1

    remove_document(document=version.document, actor=specialist)

    assert DocumentLink.objects.visible_to(specialist).count() == 0
    assert DocumentLink.objects.count() == 1


def test_a_sent_opinions_letter_cannot_be_removed_until_it_is_withdrawn(
    specialist, organisation, capture_evidence
):
    submission = _sent_opinion(
        factories.MatterFactory(owner=specialist), specialist, organisation, capture_evidence
    )
    document = submission.final_version.document

    with pytest.raises(DomainError, match=r"võta arvamus\s+kõigepealt tagasi"):
        remove_document(document=document, actor=specialist)
    with pytest.raises(DomainError, match="saadetud Koja arvamuse tõend"):
        add_version_on_open_matter(
            document=document,
            content=b"%PDF-1.4 other",
            original_filename="x.pdf",
            mime_type="application/pdf",
        )
    with pytest.raises(DomainError, match="saadetud Koja arvamuse tõend"):
        change_document_role(document=document, role=DocumentRole.OTHER, actor=specialist)
    document.refresh_from_db()
    assert document.removed_at is None
    assert document.versions.count() == 1
    assert SENT_OPINION_EVIDENCE.startswith("See fail on saadetud")

    withdraw_submission(submission=submission, actor=specialist)
    remove_document(document=document, actor=specialist)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.WITHDRAWN
    assert submission.final_version.document_id == document.pk
    assert DocumentVersion.objects.filter(pk=submission.final_version_id).exists()


def test_a_withdrawn_letter_is_not_revised_or_reclassified(
    specialist, organisation, capture_evidence
):
    submission = _sent_opinion(
        factories.MatterFactory(owner=specialist), specialist, organisation, capture_evidence
    )
    withdraw_submission(submission=submission, actor=specialist)
    document = submission.final_version.document

    with pytest.raises(DomainError, match="tagasi võetud Koja arvamuse tõend"):
        add_version_on_open_matter(
            document=document,
            content=b"%PDF-1.4 other",
            original_filename="x.pdf",
            mime_type="application/pdf",
        )
    assert WITHDRAWN_OPINION_EVIDENCE.startswith("See fail on tagasi võetud")


def test_a_superseded_letter_stays(specialist, organisation, capture_evidence):
    from app.submissions.services import supersede_submission

    submission = _sent_opinion(
        factories.MatterFactory(owner=specialist), specialist, organisation, capture_evidence
    )
    supersede_submission(submission=submission, actor=specialist)

    with pytest.raises(DomainError, match="asendatud või koostamisel"):
        remove_document(document=submission.final_version.document, actor=specialist)
    assert KEPT_OPINION_EVIDENCE.startswith("See fail on Koja arvamuse tõend")


def test_a_document_under_legal_hold_cannot_be_removed(specialist, plain_document):
    set_legal_hold(document=plain_document, on=True, reason="Kohtuasi", actor=specialist)

    with pytest.raises(DomainError, match="säilitamiskohustus"):
        remove_document(document=plain_document, actor=specialist)
    assert DOCUMENT_UNDER_LEGAL_HOLD.startswith("Dokumendile on seatud")


def test_a_removed_document_is_not_usable_as_evidence(specialist, organisation, plain_document):
    remove_document(document=plain_document, actor=specialist)

    with pytest.raises(DomainError, match="Teemalt eemaldatud dokumenti"):
        register_sent_opinion(
            document=plain_document,
            version=plain_document.current_version,
            title="Arvamus",
            recipients=[organisation],
            sent_at=datetime.datetime(2026, 9, 1, 0, 0, tzinfo=datetime.UTC),
            sent_at_precision=SentAtPrecision.DATE,
            actor=specialist,
        )
    assert REMOVED_DOCUMENT_IS_NOT_EVIDENCE.startswith("Teemalt eemaldatud")
    assert not Submission.objects.exists()


def test_the_page_offers_the_three_corrections_and_explains_a_refusal(
    signed_in, specialist, organisation, capture_evidence, plain_document
):
    body = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": plain_document.pk})
    ).content.decode()
    assert "Paranda dokumenti" in body
    assert reverse("documents:add_version", kwargs={"pk": plain_document.pk}) in body
    assert reverse("documents:change_role", kwargs={"pk": plain_document.pk}) in body
    assert reverse("documents:remove", kwargs={"pk": plain_document.pk}) in body

    submission = _sent_opinion(
        factories.MatterFactory(owner=specialist), specialist, organisation, capture_evidence
    )
    letter = submission.final_version.document
    body = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": letter.pk})
    ).content.decode()
    assert "võta arvamus" in body and "Võta tagasi" in body
    assert reverse("documents:remove", kwargs={"pk": letter.pk}) not in body
    assert reverse("documents:add_version", kwargs={"pk": letter.pk}) not in body


def test_a_reader_is_offered_no_correction(client, reader, plain_document):
    client.force_login(reader)
    response = client.get(reverse("documents:document_detail", kwargs={"pk": plain_document.pk}))

    assert "Paranda dokumenti" not in response.content.decode()
    assert client.post(
        reverse("documents:remove", kwargs={"pk": plain_document.pk})
    ).status_code in (403, 404)
    plain_document.refresh_from_db()
    assert plain_document.removed_at is None


# ---------------------------------------------------------------------------
# 6 — Võta tagasi on the opinion
# ---------------------------------------------------------------------------


def test_the_opinion_row_offers_withdrawal_behind_a_confirmation(
    signed_in, specialist, organisation, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    submission = _sent_opinion(matter, specialist, organisation, capture_evidence)

    body = _matter_page(signed_in, matter)
    row = body[body.index(f'id="koja-arvamus-{submission.pk}-sisu"') :]
    row = row[: row.index("</article>")] if "</article>" in row else row

    assert f'id="arvamus-{submission.pk}-tagasi"' in row
    assert "Võta tagasi" in row
    assert "Kinnita tagasivõtmine" in row
    assert "midagi ei kustutata" in row
    assert f'action="{reverse("submissions:withdraw", kwargs={"pk": submission.pk})}"' in row
    assert 'name="tagasi" value="teema"' in row


def test_both_ways_in_post_to_the_one_withdrawal(
    signed_in, specialist, organisation, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    submission = _sent_opinion(matter, specialist, organisation, capture_evidence)
    action = f'action="{reverse("submissions:withdraw", kwargs={"pk": submission.pk})}"'

    documents = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert action in documents
    assert "Kinnita tagasivõtmine" in documents
    assert action in _matter_page(signed_in, matter)


def test_withdrawing_from_the_teema_page_uses_the_withdrawal_service(
    signed_in, specialist, organisation, capture_evidence, monkeypatch
):
    import app.submissions.views as submission_views

    matter = factories.MatterFactory(owner=specialist)
    submission = _sent_opinion(matter, specialist, organisation, capture_evidence)
    calls = []
    real = submission_views.withdraw_submission

    def spy(**kwargs):
        calls.append(kwargs["submission"].pk)
        return real(**kwargs)

    monkeypatch.setattr(submission_views, "withdraw_submission", spy)

    response = signed_in.post(
        reverse("submissions:withdraw", kwargs={"pk": submission.pk}), {"tagasi": "teema"}
    )

    assert response.status_code == 302
    assert response["Location"] == (
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
        + f"#koja-arvamus-{submission.pk}-sisu"
    )
    assert calls == [submission.pk]
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.WITHDRAWN
    assert not Submission.objects.sent().filter(pk=submission.pk).exists()
    assert Submission.objects.historically_sent().filter(pk=submission.pk).exists()
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.SUBMISSION_WITHDRAWN, object_id=submission.pk
        ).count()
        == 1
    )
    # The evidence is where it was.
    assert submission.final_version.document.removed_at is None
    body = _matter_page(signed_in, matter)
    assert "Arvamus tagasi võetud" in body
    assert f'id="arvamus-{submission.pk}-tagasi"' not in body


# ---------------------------------------------------------------------------
# 7 — Kustuta only where deletion can succeed
# ---------------------------------------------------------------------------


def _header_offers_delete(client, matter) -> bool:
    return "matterhead__delete" in _matter_page(client, matter)


def test_a_deletable_matter_offers_kustuta(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    assert not plan_matter_deletion(matter).is_blocked
    assert _header_offers_delete(signed_in, matter)

    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
    assert "Kustuta teema" in edit


def test_a_matter_whose_history_is_kept_offers_no_kustuta(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    compose_update(matter=matter, author=specialist, body="<p>Märge</p>")
    entry = matter.entries.first()
    edit_entry(entry=entry, body="Parandatud märge", actor=specialist)
    assert plan_matter_deletion(matter).is_blocked

    assert not _header_offers_delete(signed_in, matter)
    edit = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()
    assert "mille muudatuslugu on jäädavalt salvestatud" in edit
    assert reverse("matters:matter_delete", kwargs={"pk": matter.pk}) not in edit


def test_the_route_still_refuses_a_blocked_deletion(signed_in, specialist, plain_document):
    matter = plain_document.matter
    set_legal_hold(document=plain_document, on=True, reason="Kohtuasi", actor=specialist)

    response = signed_in.post(reverse("matters:matter_delete", kwargs={"pk": matter.pk}))

    assert response.status_code == 400
    assert Matter.objects.filter(pk=matter.pk).exists()
    with pytest.raises(DomainError, match="Teemat ei saa kustutada"):
        delete_matter(matter=matter, actor=specialist)


def test_a_reader_is_never_offered_kustuta(client, reader):
    matter = factories.MatterFactory()
    client.force_login(reader)
    assert "matterhead__delete" not in _matter_page(client, matter)


# ---------------------------------------------------------------------------
# The ordinary next-step lifecycle still completes
# ---------------------------------------------------------------------------


def test_completing_the_explicit_step_hands_back_to_the_milestone(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _milestone(matter, specialist, "Riigikogu lugemine", timezone.localdate() + timedelta(days=30))
    action = set_next_action(
        matter=matter,
        text="Saadan arvamuse",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=3),
        actor=specialist,
    )
    assert "Saadan arvamuse" in _current_action(_matter_page(signed_in, matter))

    complete_next_action(action=action, actor=specialist)

    panel = _current_action(_matter_page(signed_in, matter))
    assert "Riigikogu lugemine" in panel
    assert "Järgmine samm on määramata" not in panel
    assert MatterImportantDate.objects.get(matter=matter).status == FactStatus.ACTIVE
