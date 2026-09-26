"""A new interactive send names who it went to (ENG-041).

A draft may be started with nobody addressed — who a letter goes to is still
being worked out while it is written — and it may be given its file. What it may
not do is become a SENT record naming nobody. `Registreeri saatmine` and
`+ Koja arvamus` refused that since R2-01; the draft row's `Märgi saadetuks`
asked only for a channel, and its service asked nothing, so the canonical record
could say Koda's opinion went to no one — and the correction form then refused
every save of that record until somebody invented an addressee.

Now the draft row asks `Adressaadid`, opening on whoever the draft names, and
`mark_submission_sent_on_open_matter` refuses a send with none — for the form
and for a post that never saw it. A refusal leaves the draft a draft: no send
event, no recipient change, no timestamp.

**Not retroactive.** An archive-applied SENT opinion whose recipient could not be
resolved is legitimate history and still reads everywhere it did.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.submissions.enums import RecipientRole, SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent_on_open_matter,
)
from tests import factories

pytestmark = pytest.mark.django_db

#: The refusal, as the person reads it. A literal rather than the service's
#: constant, so the wording itself is what is held.
SEND_NEEDS_ADDRESSEE = "Saadetuks märkimiseks on vaja vähemalt üht adressaati."


def _documents(client, matter):
    response = client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response


def _draft(matter, *, actor, addressees=(), copied=(), with_file=True):
    submission = create_submission(
        matter=matter,
        title="Koja arvamus eelnõule",
        actor=actor,
        recipients=list(addressees),
        for_information=list(copied),
    )
    if with_file:
        attach_final_evidence(
            submission=submission,
            content=b"%PDF-1.4 final",
            original_filename="koja-arvamus.pdf",
            mime_type="application/pdf",
            actor=actor,
        )
    submission.refresh_from_db()
    return submission


def _send(client, draft, **data):
    return client.post(reverse("submissions:mark_sent", kwargs={"pk": draft.pk}), data)


def _roles(submission) -> set[tuple[str, str]]:
    return {
        (row.organisation.name, row.role)
        for row in SubmissionRecipient.objects.filter(submission=submission).select_related(
            "organisation"
        )
    }


def _events(matter, event_type) -> int:
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type).count()


def _assert_still_a_draft(draft, *, roles) -> None:
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert draft.sent_at is None
    assert draft.sent_by_id is None
    assert _roles(draft) == roles
    assert _events(draft.matter, ChangeEventType.SUBMISSION_SENT) == 0
    assert _events(draft.matter, ChangeEventType.SUBMISSION_RECIPIENTS_CHANGED) == 0


# ---------------------------------------------------------------------------
# A draft may be addressed to nobody, and may be given its file
# ---------------------------------------------------------------------------


def test_a_draft_may_be_created_with_no_recipient(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("submissions:create", kwargs={"matter_id": matter.pk}),
        {"arvamus-title": "Koja arvamus eelnõule", "arvamus-kind": "FORMAL_OPINION"},
    )

    assert response.status_code == 302
    draft = Submission.objects.get(matter=matter)
    assert draft.status == SubmissionStatus.DRAFT
    assert _roles(draft) == set()


def test_a_draft_with_no_addressee_may_be_given_its_file(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist, with_file=False)

    signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}),
        {"upload": SimpleUploadedFile("lopp.pdf", b"%PDF-1.4 x", content_type="application/pdf")},
    )

    draft.refresh_from_db()
    assert draft.final_version_id is not None
    assert draft.status == SubmissionStatus.DRAFT


# ---------------------------------------------------------------------------
# The send refuses no addressee, through the page and past it
# ---------------------------------------------------------------------------


def test_sending_with_no_addressee_is_refused_and_writes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist)

    response = _send(signed_in, draft, channel="EIS")

    # Back on the draft's own row, with the refusal said in words.
    assert response.status_code == 302
    assert response.url.endswith(f"#arvamus-{draft.pk}")
    page = signed_in.get(response.url).content.decode()
    assert SEND_NEEDS_ADDRESSEE in page
    assert "message--error" in page
    _assert_still_a_draft(draft, roles=set())
    draft.refresh_from_db()
    assert draft.channel == ""


def test_a_post_that_empties_the_addressees_is_refused_and_keeps_them(
    signed_in, specialist, organisation
):
    """No partial write: the draft's own addressee is still there afterwards."""
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist, addressees=[organisation])

    _send(signed_in, draft)

    _assert_still_a_draft(draft, roles={(organisation.name, RecipientRole.ADDRESSEE)})


def test_a_draft_copied_to_somebody_but_addressed_to_nobody_is_refused(
    signed_in, specialist, organisation
):
    """A «teadmiseks» row is not an addressee (the ENG-061 distinction)."""
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist, copied=[organisation])

    _send(signed_in, draft)

    _assert_still_a_draft(draft, roles={(organisation.name, RecipientRole.FOR_INFORMATION)})


def test_an_organisation_the_catalogue_does_not_hold_is_refused(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist)

    response = _send(signed_in, draft, recipients=[str(uuid.uuid4())])

    page = signed_in.get(response.url).content.decode()
    assert "Valitud adressaati ei leitud" in page
    _assert_still_a_draft(draft, roles=set())


@pytest.mark.parametrize("addressees", [None, []])
def test_the_service_itself_refuses_a_send_with_no_addressee(specialist, addressees):
    """The rule is the service's, so a caller that never rendered the form meets it."""
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist)

    with pytest.raises(DomainError) as refusal:
        mark_submission_sent_on_open_matter(
            submission=draft, actor=specialist, addressees=addressees
        )

    assert str(refusal.value) == SEND_NEEDS_ADDRESSEE
    _assert_still_a_draft(draft, roles=set())


def test_an_addressee_already_copied_in_is_refused_without_a_partial_write(
    signed_in, specialist, organisation
):
    """`set_recipients`' overlap refusal rolls the whole send back."""
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist, copied=[organisation])

    _send(signed_in, draft, recipients=[str(organisation.pk)])

    _assert_still_a_draft(draft, roles={(organisation.name, RecipientRole.FOR_INFORMATION)})


# ---------------------------------------------------------------------------
# With an addressee it goes out, and says to whom
# ---------------------------------------------------------------------------


def test_sending_with_one_addressee_succeeds_and_the_event_names_it(
    signed_in, specialist, organisation
):
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist)

    response = _send(signed_in, draft, recipients=[str(organisation.pk)], channel="EIS")

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT
    assert draft.sent_by == specialist
    assert draft.channel == "EIS"
    # The real-time act: now, to the second (docs/adr/0061's 2026-09-11 table).
    assert draft.sent_at_precision == SentAtPrecision.TIMESTAMP
    assert _roles(draft) == {(organisation.name, RecipientRole.ADDRESSEE)}
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.SUBMISSION_SENT)
    assert event.payload["addressees"] == [organisation.name]
    # One act, one event: choosing the addressee is part of sending.
    assert _events(matter, ChangeEventType.SUBMISSION_RECIPIENTS_CHANGED) == 0
    # And it lands on the file row it changed, naming the addressee there.
    assert f"#dokument-{draft.final_version.document_id}" in response.url
    assert organisation.name in _documents(signed_in, matter).content.decode()


def test_the_send_replaces_the_drafts_addressees_and_keeps_its_copies(
    signed_in, specialist, organisation
):
    matter = factories.MatterFactory(owner=specialist)
    first = factories.OrganisationFactory(name="Esialgne adressaat")
    copied = factories.OrganisationFactory(name="Teadmiseks liit")
    draft = _draft(matter, actor=specialist, addressees=[first], copied=[copied])

    _send(signed_in, draft, recipients=[str(organisation.pk)])

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT
    assert _roles(draft) == {
        (organisation.name, RecipientRole.ADDRESSEE),
        (copied.name, RecipientRole.FOR_INFORMATION),
    }
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.SUBMISSION_SENT)
    assert event.payload["addressees"] == [organisation.name]


def test_the_service_sends_a_draft_that_already_names_its_addressee(specialist, organisation):
    """``addressees=None`` keeps what the draft names — and that is enough."""
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist, addressees=[organisation])

    mark_submission_sent_on_open_matter(submission=draft, actor=specialist)

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT


def test_a_sent_opinion_is_not_re_addressed_by_a_second_press(signed_in, specialist, organisation):
    """Already sent is refused with its own sentence, and its addressees stand."""
    matter = factories.MatterFactory(owner=specialist)
    other = factories.OrganisationFactory(name="Teine asutus")
    draft = _draft(matter, actor=specialist)
    _send(signed_in, draft, recipients=[str(organisation.pk)])

    _send(signed_in, draft, recipients=[str(other.pk)])

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT
    assert _roles(draft) == {(organisation.name, RecipientRole.ADDRESSEE)}
    assert _events(matter, ChangeEventType.SUBMISSION_SENT) == 1


# ---------------------------------------------------------------------------
# The control on the draft row
# ---------------------------------------------------------------------------


def test_the_send_form_asks_for_addressees_and_opens_on_the_drafts_own(
    signed_in, specialist, organisation
):
    matter = factories.MatterFactory(owner=specialist)
    other = factories.OrganisationFactory(name="Valimata asutus")
    draft = _draft(matter, actor=specialist, addressees=[organisation])

    body = _documents(signed_in, matter).content.decode()
    box = f"id_saatmine_{draft.pk}_recipients"
    start = body.index(f'id="{box}"')
    select = body[body.rindex("<select", 0, start) : body.index("</select>", start)]

    # Labelled, required by the browser as well as by the service, described.
    assert f'<label class="field draftrow__addressees" for="{box}">' in body
    assert "Adressaadid" in body
    assert " required" in select
    assert " multiple" in select
    assert f'aria-describedby="{box}_helptext"' in select
    assert f'id="{box}_helptext"' in body
    # Opening on the draft's addressee, and on nobody else.
    assert f'<option value="{organisation.pk}" selected>' in select
    assert f'<option value="{other.pk}">' in select


def test_two_drafts_have_two_sets_of_ids(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    first = _draft(matter, actor=specialist, addressees=[organisation])
    second = _draft(matter, actor=specialist)

    body = _documents(signed_in, matter).content.decode()

    for draft in (first, second):
        assert body.count(f'id="id_saatmine_{draft.pk}_recipients"') == 1


def test_a_draft_without_its_file_is_not_offered_the_send(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    draft = _draft(matter, actor=specialist, with_file=False)

    body = _documents(signed_in, matter).content.decode()

    assert f"id_saatmine_{draft.pk}_recipients" not in body
    assert reverse("submissions:mark_sent", kwargs={"pk": draft.pk}) not in body


# ---------------------------------------------------------------------------
# Not retroactive: an archive send with no resolved addressee still reads
# ---------------------------------------------------------------------------


def test_a_historical_send_with_no_addressee_still_reads_everywhere(signed_in, specialist):
    """The shape `opinion_apply` writes when a recipient could not be resolved."""
    matter = factories.MatterFactory(owner=specialist, title="Arhiiviteema")
    document = create_document(
        matter=matter,
        title="vana-arvamus.pdf",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=specialist,
    )
    version = add_evidence_version(
        document=document,
        content=b"%PDF-1.4 vana",
        original_filename="vana-arvamus.pdf",
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    historical = factories.SubmissionFactory(
        matter=matter,
        title="Vana arvamus",
        status=SubmissionStatus.SENT,
        sent_at=timezone.make_aware(datetime.datetime(2019, 3, 12)),
        sent_at_precision=SentAtPrecision.DATE,
        final_version=version,
    )
    assert _roles(historical) == set()

    # Teema käik, the opinion's file row and its panel, the register, Statistika.
    teema = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert teema.status_code == 200
    assert "Arvamus välja" in teema.content.decode()
    assert "vana-arvamus.pdf" in _documents(signed_in, matter).content.decode()
    register = signed_in.get(reverse("submissions:sent"))
    assert register.status_code == 200
    assert "Vana arvamus" in register.content.decode()
    statistika = signed_in.get(reverse("reporting:submissions"), {"periood": "koik"})
    assert statistika.status_code == 200
    assert statistika.context["total"] == 1

    historical.refresh_from_db()
    assert historical.status == SubmissionStatus.SENT
