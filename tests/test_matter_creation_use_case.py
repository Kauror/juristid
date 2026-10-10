"""One creation sequence behind both doors into a Matter (AUD-010).

`Uus teema` and `Saabunud` used to orchestrate the same creation twice, and
the copies had drifted: Saabunud wrote today into a received date the person
had cleared, `Uus teema` stored it as unknown. Both now call
`app.matters.intake.file_new_matter`, and these tests pin what that sequence
owns — and the one product decision the consolidation carries out (D-3): a
cleared received date is unknown, on either door, and no server invents today.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.documents.models import Document
from app.matters.forms import IncomingIntakeForm
from app.matters.intake import file_incoming, file_new_matter
from app.matters.models import Matter
from app.workflow.enums import Disposition
from app.workflow.models import MatterPlanStep, StageVocabulary
from tests.refusals import refused

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 synthetic incoming draft"
INTAKE = reverse("matters:intake")
CREATE = reverse("matters:matter_create")


def _estonian(day: date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _saabunud(client: Any, **fields: Any) -> Any:
    payload = {
        "visibility": Visibility.NORMAL,
        "uploads": [SimpleUploadedFile("eelnou.pdf", PDF)],
        **fields,
    }
    return client.post(INTAKE, payload)


def _attach_one_pdf(actor: Any, *names: str) -> Any:
    def attach(matter: Matter) -> None:
        for name in names or ("eelnou.pdf",):
            file_incoming(
                matter=matter,
                filename=name,
                content=PDF,
                mime_type="application/pdf",
                actor=actor,
            )

    return attach


# ---------------------------------------------------------------------------
# D-3: a cleared received date is unknown, on both doors
# ---------------------------------------------------------------------------


def test_saabunud_with_the_received_date_cleared_stores_no_date(signed_in) -> None:
    """The box arrives holding today; a person who empties it has said the date
    is not known, and the Teema is filed without one (D-3). Saabunud used to
    write `timezone.localdate()` in its place on the server."""
    response = _saabunud(signed_in, title="Kuupäevata saadetis", received_date="")

    assert response.status_code == 302
    assert Matter.objects.get(title="Kuupäevata saadetis").received_date is None


def test_saabunud_stores_the_received_date_it_was_given(signed_in) -> None:
    arrived = timezone.localdate() - timedelta(days=3)
    _saabunud(signed_in, title="Eile-üleeile saabunud", received_date=_estonian(arrived))

    assert Matter.objects.get(title="Eile-üleeile saabunud").received_date == arrived


def test_saabunud_still_prefills_today_as_a_visible_initial_value() -> None:
    """What stays: the unbound form shows today, which the person can clear."""
    assert IncomingIntakeForm.base_fields["received_date"].initial is timezone.localdate


def test_uus_teema_with_the_received_date_cleared_stores_no_date(signed_in) -> None:
    signed_in.post(CREATE, {"title": "Uus kuupäevata teema", "received_date": ""})

    assert Matter.objects.get(title="Uus kuupäevata teema").received_date is None


# ---------------------------------------------------------------------------
# A deadline is a request recorded now — and only a deadline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", ["saabunud", "uus_teema"])
def test_response_requested_at_is_written_only_with_a_deadline(signed_in, door) -> None:
    due = _estonian(timezone.localdate() + timedelta(days=14))
    for title, deadline in (("Tähtajaga", due), ("Tähtajata", "")):
        fields = {"title": f"{title} {door}", "response_deadline": deadline}
        if door == "saabunud":
            response = _saabunud(signed_in, **fields)
        else:
            response = signed_in.post(CREATE, fields)
        assert response.status_code == 302

    with_deadline = Matter.objects.get(title=f"Tähtajaga {door}")
    without = Matter.objects.get(title=f"Tähtajata {door}")
    assert with_deadline.response_deadline is not None
    assert with_deadline.response_requested_at is not None
    assert without.response_deadline is None
    assert without.response_requested_at is None


# ---------------------------------------------------------------------------
# The use case itself
# ---------------------------------------------------------------------------


def test_a_non_terminal_creation_seeds_the_standard_plan(specialist) -> None:
    matter = file_new_matter(
        title="Tavaline töö",
        actor=specialist,
        received_date=None,
        attach_files=_attach_one_pdf(specialist),
        seed_plan=True,
    )

    assert matter.is_open
    assert matter.received_date is None
    assert MatterPlanStep.objects.filter(matter=matter).exists()
    assert Document.objects.filter(matter=matter).count() == 1


def test_no_plan_is_seeded_unless_asked(specialist) -> None:
    """A caller recording history leaves `seed_plan` off (docs/adr/0133 §5)."""
    matter = file_new_matter(title="Ajalooline", actor=specialist)

    assert not MatterPlanStep.objects.filter(matter=matter).exists()


def test_a_terminal_stage_closes_the_new_matter_and_seeds_nothing(specialist) -> None:
    """«Jõustunud» files the Teema closed, last, after its files (docs/adr/0131 §10)."""
    matter = file_new_matter(
        title="Juba jõustunud",
        actor=specialist,
        stage=StageVocabulary.objects.get(key="in_force"),
        attach_files=_attach_one_pdf(specialist),
        seed_plan=True,
    )

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == Disposition.COMPLETED
    assert not MatterPlanStep.objects.filter(matter=matter).exists()
    # The file was written while the Matter was still open.
    assert Document.objects.filter(matter=matter).count() == 1
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_CLOSED
    ).exists()


def test_the_files_are_one_operation_and_the_handover_note_is_not(specialist) -> None:
    """docs/adr/0121 §6: the files share one `operation_id`; the note stands apart."""
    matter = file_new_matter(
        title="Kolm faili",
        actor=specialist,
        handover_note="Vaata lisa 2.",
        attach_files=_attach_one_pdf(specialist, "a.pdf", "b.pdf", "c.pdf"),
    )

    created = ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.DOCUMENT_CREATED)
    operations = set(created.values_list("operation_id", flat=True))
    assert created.count() == 3
    assert len(operations) == 1 and None not in operations
    others = ChangeEvent.objects.filter(matter=matter).exclude(
        event_type__in=[ChangeEventType.DOCUMENT_CREATED, ChangeEventType.EVIDENCE_VERSION_ADDED]
    )
    assert not others.filter(operation_id__in=operations).exists()


def test_after_files_runs_in_the_same_transaction(specialist) -> None:
    """A refusal in the caller's extras takes the whole Teema with it."""
    from app.core.errors import DomainError

    refusal = "Seost ei saa luua."

    def refuse(matter: Matter) -> None:
        raise DomainError(refusal)

    with refused(refusal):
        file_new_matter(
            title="Poolik",
            actor=specialist,
            attach_files=_attach_one_pdf(specialist),
            after_files=refuse,
            seed_plan=True,
        )

    assert not Matter.objects.filter(title="Poolik").exists()
    assert not Document.objects.exists()
