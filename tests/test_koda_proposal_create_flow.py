"""A Chamber proposal or appeal, entered from `Uus teema` — the owner's decision of 2026-10-12.

docs/adr/0151, amendment of 2026-10-12. Two legitimate journeys, one canonical
architecture:

* **A — still work to be done.** `Õigusakt` «Koja ettepanek või pöördumine»,
  nothing ticked: a working Teema on the `Koja algatus` track, no `Saabus`, no
  `Arvamuse tähtaeg`, no send, no check; the files are ordinary documents. When
  the proposal really goes out, `+ Koja arvamus` records it as a
  `KODA_PROPOSAL` and schedules the thirty-day check from that send.
* **B/C — already sent.** The same instrument, the files that went, and
  «Märgi saadetud Koja arvamuseks»: the same save records the Teema *and* the
  sent proposal — one `Submission` for the whole package, through the same
  `register_sent_opinion_on_open_matter` the Dokumendid send uses, with the
  check scheduled off the recorded day and promoted to the current work.
* **D — refusals.** No file, another instrument, no addressee, a future day, a
  refused file, a closing `Hetkeseis`, a double submit: nothing half-made.
* **Reporting** reads the result from canonical data alone, exactly as it reads
  the same proposal recorded later.
* **An ordinary Teema is unchanged.**
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.matters import workspace
from app.matters.forms import (
    SENT_PROPOSAL_CANNOT_CLOSE,
    SENT_PROPOSAL_NEEDS_AN_ADDRESSEE,
    SENT_PROPOSAL_NEEDS_THE_INSTRUMENT,
)
from app.matters.models import Matter
from app.matters.workspace import SENT_PROPOSAL_NEEDS_A_FILE
from app.organisations.models import Organisation
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext, parse_period
from app.reporting.services import compute
from app.submissions.enums import (
    RecipientRole,
    SentAtPrecision,
    SubmissionKind,
    SubmissionStatus,
)
from app.submissions.models import Submission, SubmissionSentFile
from app.submissions.services import SENT_DATE_IN_THE_FUTURE
from app.taxonomy.models import LegalInstrumentType
from app.workflow.enums import ActionStatus, Track
from app.workflow.follow_ups import FOLLOW_UP_PROPOSAL_TEXT, sent_on_of
from app.workflow.models import MatterPlanStep, NextAction, OpinionFollowUp, StageVocabulary
from app.workflow.stage_flow import IN_FORCE
from tests import synthetic_corpus as corpus

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")
STAGE = reverse("matters:intake_stage")


def today() -> dt.date:
    return timezone.localdate()


def et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def instrument(key: str) -> list[str]:
    return [str(LegalInstrumentType.objects.get(key=key).pk)]


def pdf(name: str, text: str = "Koja pöördumine") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, corpus.text_pdf([text]), content_type="application/pdf")


def xlsx(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        corpus.annex_xlsx(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def form_token(client: Client) -> str:
    match = re.search(r'name="intake" value="([^"]+)"', client.get(CREATE).content.decode())
    assert match
    return match.group(1)


@pytest.fixture
def ministry(db) -> Organisation:
    return Organisation.objects.create(name="Näidisministeerium")


@pytest.fixture
def committee(db) -> Organisation:
    return Organisation.objects.create(name="Näidiskomisjon")


def sent_fields(title: str, ministry: Organisation, **extra: Any) -> dict[str, Any]:
    """A filled `Uus teema` with «Märgi saadetud Koja arvamuseks» ticked."""
    data: dict[str, Any] = {
        "title": title,
        "legal_instruments": instrument("koja-ettepanek"),
        "koda_sent": "on",
        "sent_on": et(today()),
        "sent_recipients": [str(ministry.pk)],
    }
    data.update(extra)
    return data


def submissions_of(matter: Matter) -> list[Submission]:
    return list(Submission.objects.filter(matter=matter))


def checks_of(matter: Matter) -> list[NextAction]:
    return list(NextAction.objects.filter(matter=matter, follow_up__isnull=False))


def metric(viewer: Any, key: str):
    context = ReportingContext(
        viewer=viewer, period=parse_period("koik", today()), today=today(), now=timezone.now()
    )
    return compute(key, context)


def segment(result: Any, label: str) -> int:
    return sum(item.value for item in result.segments if item.label == label)


# ---------------------------------------------------------------------------
# Scenario A — the board asks Koda to prepare an appeal later
# ---------------------------------------------------------------------------


def test_a_proposal_still_to_be_prepared_is_ordinary_work(signed_in, specialist, evidence_root):
    response = signed_in.post(
        CREATE,
        {
            "title": "Pöördumine: koostada kahe nädala jooksul",
            "legal_instruments": instrument("koja-ettepanek"),
            "received_date": et(today()),
            "response_deadline": et(today() + dt.timedelta(days=14)),
            "files": [pdf("taustamaterjal.pdf")],
        },
    )

    assert response.status_code == 302
    (matter,) = Matter.objects.filter(title="Pöördumine: koostada kahe nädala jooksul")
    assert matter.track == Track.KODA_INITIATIVE
    assert (matter.received_date, matter.response_deadline) == (None, None)
    assert matter.response_requested_at is None
    assert submissions_of(matter) == []
    assert not OpinionFollowUp.objects.filter(submission__matter=matter).exists()
    assert checks_of(matter) == []
    (document,) = Document.objects.filter(matter=matter)
    assert document.role != DocumentRole.KODA_SUBMISSION_FINAL
    # The ordinary working Teema: the standard plan is offered as before.
    assert MatterPlanStep.objects.filter(matter=matter).exists()


def test_sending_it_later_records_the_same_canonical_proposal(
    signed_in, specialist, ministry, evidence_root
):
    signed_in.post(
        CREATE,
        {"title": "Hiljem saadetav pöördumine", "legal_instruments": instrument("koja-ettepanek")},
    )
    matter = Matter.objects.get(title="Hiljem saadetav pöördumine")
    sent_on = today() - dt.timedelta(days=2)

    result = workspace.add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        recipients=[ministry],
        sent_on=sent_on,
        upload=pdf("Pöördumine.pdf"),
    )

    proposal = result.record
    assert proposal.kind == SubmissionKind.KODA_PROPOSAL
    assert proposal.status == SubmissionStatus.SENT
    assert proposal.final_version_id is not None
    assert proposal.sent_at_precision == SentAtPrecision.DATE
    assert sent_on_of(proposal.sent_at) == sent_on
    assert [row.organisation for row in proposal.recipient_rows.all()] == [ministry]
    follow_up = OpinionFollowUp.objects.get(submission=proposal)
    assert follow_up.first_due_on == sent_on + dt.timedelta(days=30)
    (check,) = checks_of(matter)
    assert check.text == FOLLOW_UP_PROPOSAL_TEXT
    assert (
        segment(metric(specialist, keys.SUBMISSIONS_BY_KIND), "Koja ettepanek või pöördumine") == 1
    )


# ---------------------------------------------------------------------------
# Scenario B — an already-sent appeal, filed directly
# ---------------------------------------------------------------------------


def test_an_already_sent_proposal_is_recorded_in_the_same_save(
    signed_in, specialist, ministry, evidence_root
):
    response = signed_in.post(
        CREATE, sent_fields("Saadetud pöördumine", ministry, files=[pdf("Pöördumine.pdf")])
    )

    assert response.status_code == 302
    (matter,) = Matter.objects.filter(title="Saadetud pöördumine")
    assert matter.track == Track.KODA_INITIATIVE
    assert (matter.received_date, matter.response_deadline) == (None, None)
    # One document, one version — the bytes are filed once.
    (document,) = Document.objects.filter(matter=matter)
    (version,) = DocumentVersion.objects.filter(document=document)
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    # One sent proposal, standing on exactly that version.
    (proposal,) = submissions_of(matter)
    assert proposal.kind == SubmissionKind.KODA_PROPOSAL
    assert proposal.status == SubmissionStatus.SENT
    assert proposal.final_version_id == version.pk
    assert proposal.sent_at_precision == SentAtPrecision.DATE
    assert sent_on_of(proposal.sent_at) == today()
    assert [
        row.organisation for row in proposal.recipient_rows.filter(role=RecipientRole.ADDRESSEE)
    ] == [ministry]
    assert not SubmissionSentFile.objects.filter(submission=proposal).exists()
    # The check, from the recorded day, and it is the current work.
    follow_up = OpinionFollowUp.objects.get(submission=proposal)
    assert follow_up.sent_on == today()
    assert follow_up.first_due_on == today() + dt.timedelta(days=30)
    (check,) = checks_of(matter)
    assert check.text == FOLLOW_UP_PROPOSAL_TEXT == "Kontrolli, mis on pöördumisest saanud"
    assert check.status == ActionStatus.OPEN
    assert check.target_date == today() + dt.timedelta(days=30)
    assert NextAction.objects.filter(matter=matter).count() == 1
    # No competing «Tutvu materjaliga»: a Teema filed already sent gets no plan.
    assert not MatterPlanStep.objects.filter(matter=matter).exists()


def test_the_send_is_one_act_with_its_files_in_the_history(
    signed_in, specialist, ministry, evidence_root
):
    signed_in.post(CREATE, sent_fields("Üks toiming", ministry, files=[pdf("Pöördumine.pdf")]))
    matter = Matter.objects.get(title="Üks toiming")

    events = ChangeEvent.objects.filter(matter=matter)
    sent = events.get(event_type=ChangeEventType.SUBMISSION_SENT)
    created = events.get(event_type=ChangeEventType.SUBMISSION_CREATED)
    document_created = events.get(event_type=ChangeEventType.DOCUMENT_CREATED)
    assert events.filter(event_type=ChangeEventType.MATTER_CREATED).count() == 1
    assert sent.operation_id is not None
    assert created.operation_id == sent.operation_id == document_created.operation_id


def test_the_teema_page_shows_the_proposal_the_check_and_the_document_once(
    signed_in, specialist, ministry, evidence_root
):
    signed_in.post(
        CREATE, sent_fields("Nähtav pöördumine", ministry, files=[pdf("Pöördumine.pdf")])
    )
    matter = Matter.objects.get(title="Nähtav pöördumine")

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = page.content.decode()
    assert FOLLOW_UP_PROPOSAL_TEXT in body
    assert "Järgmine samm on määramata" not in body
    assert "Tutvu materjaliga" not in body
    rail = page.context["opinion_rail"]
    assert len(rail) == 1
    documents = signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert documents.content.decode().count("Pöördumine.pdf") >= 1
    assert Document.objects.filter(matter=matter).count() == 1


def test_a_send_in_the_past_is_checked_thirty_days_after_that_day(
    signed_in, specialist, ministry, evidence_root
):
    sent_on = today() - dt.timedelta(days=40)
    signed_in.post(
        CREATE,
        sent_fields("Ammu saadetud", ministry, sent_on=et(sent_on), files=[pdf("Pöördumine.pdf")]),
    )

    (proposal,) = submissions_of(Matter.objects.get(title="Ammu saadetud"))
    assert sent_on_of(proposal.sent_at) == sent_on
    assert OpinionFollowUp.objects.get(submission=proposal).first_due_on == sent_on + dt.timedelta(
        days=30
    )


def test_a_typed_new_addressee_is_resolved_like_koja_arvamus(signed_in, specialist, evidence_root):
    signed_in.post(
        CREATE,
        {
            "title": "Uuele asutusele",
            "legal_instruments": instrument("koja-ettepanek"),
            "koda_sent": "on",
            "sent_on": et(today()),
            "sent_recipient_name": "Näidisamet",
            "files": [pdf("Pöördumine.pdf")],
        },
    )

    (proposal,) = submissions_of(Matter.objects.get(title="Uuele asutusele"))
    assert [row.organisation.name for row in proposal.recipient_rows.all()] == ["Näidisamet"]


def test_the_sender_is_not_the_addressee(signed_in, specialist, ministry, committee, evidence_root):
    """`Saatja` and the proposal's addressee are two facts; neither is read off the other."""
    signed_in.post(
        CREATE,
        sent_fields(
            "Kaks fakti",
            committee,
            source_organisations=[str(ministry.pk)],
            files=[pdf("Pöördumine.pdf")],
        ),
    )

    matter = Matter.objects.get(title="Kaks fakti")
    (proposal,) = submissions_of(matter)
    assert [o.name for o in matter.source_organisations.all()] == ["Näidisministeerium"]
    assert [row.organisation.name for row in proposal.recipient_rows.all()] == ["Näidiskomisjon"]


def test_staged_files_are_the_sent_package_too(signed_in, specialist, ministry, evidence_root):
    """With scripting on, the files are staged before the save; they become the
    proposal's documents exactly once."""
    token = form_token(signed_in)
    staged = signed_in.post(STAGE, {"intake": token, "files": [pdf("Pöördumine.pdf")]})
    assert staged.status_code == 200

    response = signed_in.post(CREATE, sent_fields("Ette laaditud", ministry, intake=token))

    assert response.status_code == 302
    matter = Matter.objects.get(title="Ette laaditud")
    (document,) = Document.objects.filter(matter=matter)
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    (proposal,) = submissions_of(matter)
    assert proposal.final_version_id == document.current_version_id
    assert len(checks_of(matter)) == 1


# ---------------------------------------------------------------------------
# Scenario C — a package of several files
# ---------------------------------------------------------------------------


def test_several_files_are_one_sent_package(signed_in, specialist, ministry, evidence_root):
    signed_in.post(
        CREATE,
        sent_fields(
            "Pöördumine lisadega",
            ministry,
            files=[pdf("Pöördumine.pdf"), xlsx("Lisa.xlsx"), pdf("Lisa.pdf", "Lisa tekst")],
        ),
    )

    matter = Matter.objects.get(title="Pöördumine lisadega")
    documents = list(Document.objects.filter(matter=matter))
    assert len(documents) == 3
    assert DocumentVersion.objects.filter(document__matter=matter).count() == 3
    assert {document.role for document in documents} == {DocumentRole.KODA_SUBMISSION_FINAL}
    (proposal,) = submissions_of(matter)
    letter = proposal.final_version
    assert letter.original_filename == "Pöördumine.pdf"
    further = list(
        SubmissionSentFile.objects.filter(submission=proposal)
        .order_by("position")
        .values_list("version__original_filename", flat=True)
    )
    assert further == ["Lisa.xlsx", "Lisa.pdf"]
    assert OpinionFollowUp.objects.filter(submission__matter=matter).count() == 1
    assert len(checks_of(matter)) == 1


# ---------------------------------------------------------------------------
# Scenario D — refusals, and nothing half-made
# ---------------------------------------------------------------------------


def nothing_was_written(title: str) -> None:
    assert not Matter.objects.filter(title=title).exists()
    assert not Submission.objects.filter(title__icontains="Pöördumine").exists()
    assert not OpinionFollowUp.objects.exists()


def test_the_fast_path_without_a_file_is_refused(signed_in, specialist, ministry):
    response = signed_in.post(CREATE, sent_fields("Failita", ministry))

    assert response.status_code == 400
    assert SENT_PROPOSAL_NEEDS_A_FILE in response.content.decode()
    nothing_was_written("Failita")


def test_the_fast_path_on_another_instrument_is_refused(
    signed_in, specialist, ministry, evidence_root
):
    response = signed_in.post(
        CREATE,
        sent_fields(
            "Seadus kiirteel",
            ministry,
            legal_instruments=instrument("seadus"),
            files=[pdf("Pöördumine.pdf")],
        ),
    )

    assert response.status_code == 400
    assert response.context["form"].errors["koda_sent"] == [SENT_PROPOSAL_NEEDS_THE_INSTRUMENT]
    nothing_was_written("Seadus kiirteel")


def test_the_fast_path_without_an_addressee_is_refused(signed_in, specialist, evidence_root):
    response = signed_in.post(
        CREATE,
        {
            "title": "Adressaadita",
            "legal_instruments": instrument("koja-ettepanek"),
            "koda_sent": "on",
            "sent_on": et(today()),
            "files": [pdf("Pöördumine.pdf")],
        },
    )

    assert response.status_code == 400
    assert response.context["form"].errors["sent_recipients"] == [SENT_PROPOSAL_NEEDS_AN_ADDRESSEE]
    nothing_was_written("Adressaadita")


def test_a_future_send_date_is_refused(signed_in, specialist, ministry, evidence_root):
    response = signed_in.post(
        CREATE,
        sent_fields(
            "Tulevikus",
            ministry,
            sent_on=et(today() + dt.timedelta(days=1)),
            files=[pdf("Pöördumine.pdf")],
        ),
    )

    assert response.status_code == 400
    assert response.context["form"].errors["sent_on"] == [SENT_DATE_IN_THE_FUTURE]
    nothing_was_written("Tulevikus")


def test_a_closing_stage_is_refused_with_the_fast_path(
    signed_in, specialist, ministry, evidence_root
):
    closing = StageVocabulary.objects.get(key=IN_FORCE)
    response = signed_in.post(
        CREATE,
        sent_fields("Kohe suletud", ministry, stage=str(closing.pk), files=[pdf("Pöördumine.pdf")]),
    )

    assert response.status_code == 400
    assert response.context["form"].errors["stage"] == [SENT_PROPOSAL_CANNOT_CLOSE]
    nothing_was_written("Kohe suletud")


def test_a_refused_file_refuses_the_whole_creation(signed_in, specialist, evidence_root):
    """A file the reader refuses takes the Teema, the send and a typed new
    institution with it."""
    response = signed_in.post(
        CREATE,
        {
            "title": "Vigane fail",
            "legal_instruments": instrument("koja-ettepanek"),
            "koda_sent": "on",
            "sent_on": et(today()),
            "sent_recipient_name": "Ajutine amet",
            "files": [SimpleUploadedFile("Pöördumine.pdf", b"not a pdf", "application/pdf")],
        },
    )

    assert response.status_code == 400
    nothing_was_written("Vigane fail")
    assert not Organisation.objects.filter(name="Ajutine amet").exists()
    assert not Document.objects.exists()


def test_a_failing_send_leaves_no_teema_no_document_and_no_institution(
    signed_in, specialist, evidence_root, monkeypatch
):
    from app.core.errors import DomainError
    from app.submissions import services as submission_services

    def refuse(**_: Any) -> None:
        raise DomainError("Saatmine ebaõnnestus.")

    monkeypatch.setattr(submission_services, "register_sent_opinion_on_open_matter", refuse)

    response = signed_in.post(
        CREATE,
        {
            "title": "Katkenud saatmine",
            "legal_instruments": instrument("koja-ettepanek"),
            "koda_sent": "on",
            "sent_on": et(today()),
            "sent_recipient_name": "Teine ajutine amet",
            "files": [pdf("Pöördumine.pdf")],
        },
    )

    assert response.status_code == 400
    assert "Saatmine ebaõnnestus." in response.content.decode()
    nothing_was_written("Katkenud saatmine")
    assert not Document.objects.exists()
    assert not Organisation.objects.filter(name="Teine ajutine amet").exists()


def test_a_refused_fast_path_keeps_its_files_and_its_answers(signed_in, specialist, evidence_root):
    response = signed_in.post(
        CREATE,
        {
            "title": "",
            "legal_instruments": instrument("koja-ettepanek"),
            "koda_sent": "on",
            "sent_on": et(today()),
            "files": [pdf("Pöördumine.pdf")],
        },
    )

    assert response.status_code == 400
    assert response.context["sent_proposal_offered"] is True
    assert [item.filename for item in response.context["held_files"]] == ["Pöördumine.pdf"]
    body = response.content.decode()
    section = body.split('id="saadetud-koja-arvamus"', 1)[1].split(">", 1)[0]
    assert "hidden" not in section


def test_the_same_form_twice_is_one_teema_one_send_one_check(specialist, ministry, evidence_root):
    client = Client()
    client.force_login(specialist)
    token = form_token(client)
    client.post(STAGE, {"intake": token, "files": [pdf("Pöördumine.pdf")]})
    data = sent_fields("Kaks vajutust", ministry, intake=token)

    first = client.post(CREATE, data)
    second = client.post(CREATE, data)

    assert first.status_code == 302
    assert second.status_code == 302
    (matter,) = Matter.objects.filter(title="Kaks vajutust")
    assert len(submissions_of(matter)) == 1
    assert OpinionFollowUp.objects.filter(submission__matter=matter).count() == 1
    assert len(checks_of(matter)) == 1


# ---------------------------------------------------------------------------
# Reporting — canonical data, nothing else
# ---------------------------------------------------------------------------


def test_reporting_reads_the_fast_path_like_a_later_send(
    signed_in, specialist, ministry, evidence_root
):
    sent_on = today() - dt.timedelta(days=3)
    signed_in.post(
        CREATE,
        sent_fields("Aruandlus", ministry, sent_on=et(sent_on), files=[pdf("Pöördumine.pdf")]),
    )

    assert segment(metric(specialist, keys.MATTERS_BY_TRACK), "Koja algatus") == 1
    assert metric(specialist, keys.SUBMISSIONS_SENT).value == 1
    assert (
        segment(metric(specialist, keys.SUBMISSIONS_BY_KIND), "Koja ettepanek või pöördumine") == 1
    )
    assert segment(metric(specialist, keys.SUBMISSIONS_BY_RECIPIENT), "Näidisministeerium") == 1
    (proposal,) = Submission.objects.all()
    assert sent_on_of(proposal.sent_at) == sent_on


def test_both_paths_converge_on_the_same_canonical_send(
    signed_in, specialist, ministry, evidence_root
):
    sent_on = today() - dt.timedelta(days=1)
    signed_in.post(
        CREATE, sent_fields("Kiirtee", ministry, sent_on=et(sent_on), files=[pdf("Pöördumine.pdf")])
    )
    signed_in.post(CREATE, {"title": "Tavatee", "legal_instruments": instrument("koja-ettepanek")})
    later = Matter.objects.get(title="Tavatee")
    workspace.add_matter_koda_opinion(
        matter=later,
        author=specialist,
        recipients=[ministry],
        sent_on=sent_on,
        upload=pdf("Pöördumine.pdf"),
    )

    def shape(matter: Matter) -> tuple:
        (proposal,) = submissions_of(matter)
        follow_up = OpinionFollowUp.objects.get(submission=proposal)
        (check,) = checks_of(matter)
        return (
            matter.track,
            proposal.kind,
            proposal.status,
            proposal.sent_at,
            proposal.sent_at_precision,
            proposal.final_version.document.role,
            [row.organisation_id for row in proposal.recipient_rows.all()],
            follow_up.sent_on,
            follow_up.first_due_on,
            check.text,
            check.target_date,
        )

    assert shape(Matter.objects.get(title="Kiirtee")) == shape(later)


# ---------------------------------------------------------------------------
# An ordinary Teema is exactly as it was
# ---------------------------------------------------------------------------


def test_an_ordinary_teema_with_files_is_unchanged(signed_in, specialist, evidence_root):
    due = today() + dt.timedelta(days=10)
    response = signed_in.post(
        CREATE,
        {
            "title": "Tavaline eelnõu",
            "legal_instruments": instrument("seadus"),
            "received_date": et(today()),
            "response_deadline": et(due),
            "files": [pdf("eelnou.pdf")],
        },
    )

    assert response.status_code == 302
    matter = Matter.objects.get(title="Tavaline eelnõu")
    assert matter.track == ""
    assert matter.received_date == today()
    assert matter.response_deadline == due
    assert matter.response_requested_at is not None
    assert submissions_of(matter) == []
    assert checks_of(matter) == []
    (document,) = Document.objects.filter(matter=matter)
    assert document.role == DocumentRole.INCOMING_AUTHORITY
    assert MatterPlanStep.objects.filter(matter=matter).exists()


def test_the_sent_answers_are_ignored_when_the_box_is_not_ticked(
    signed_in, specialist, ministry, evidence_root
):
    """A hidden section posts nothing that matters: unticked, the day and the
    addressee are set aside and no send is recorded."""
    signed_in.post(
        CREATE,
        {
            "title": "Märkimata",
            "legal_instruments": instrument("koja-ettepanek"),
            "sent_on": et(today()),
            "sent_recipients": [str(ministry.pk)],
            "files": [pdf("Pöördumine.pdf")],
        },
    )

    matter = Matter.objects.get(title="Märkimata")
    assert submissions_of(matter) == []
    assert checks_of(matter) == []
