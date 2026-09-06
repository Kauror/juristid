"""A Matter's opinions are documents, and `Dokumendid` is where they live.

The contract docs/adr/0061 decided, asserted from the four directions it can go
wrong in:

**What counts as an opinion.** The union of the `KODA_SUBMISSION_FINAL` role and
the exact evidence of a SENT `Submission`, deduplicated by document. The role
alone is not the answer and never was — `select_final_evidence` deliberately
leaves a file's classification alone — and a version of this question answered
from the role denied a sent opinion in the Matter rail (UX-005). One
implementation, in `app/submissions/opinions.py`, read by the rail, the badge,
the filter and the per-row metadata; the tests below fire at all four so the one
answer cannot start being three.

**What the reader is told, and what they are not.** `Arvamus` where `★ Lõplik`
was, the sent date and the addressee under the filename, and none of the
evidence mechanics: no checksum, no version, no duplicate byte size, no archive
importer's match reasoning. Those rows all still exist — the last section asserts
that too, because removing prose from a screen and removing provenance from a
database are different acts and only one of them happened.

**Authorization.** A visible `Submission` is not authority to name its evidence.
A restricted final version produces no filename, no badge, no anchor and no
count anywhere on this page.

**The old address.** A redirect and not a 404, after the authorization check, so
a bookmark still works and a stranger still learns nothing.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.search.models import SearchSourceKind
from app.submissions.enums import SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.opinions import OPINION_ROLE_FILTER
from app.submissions.services import (
    create_submission,
    mark_submission_sent,
    select_final_evidence,
)
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _documents_url(matter, **params: str) -> str:
    url = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    if not params:
        return url
    return url + "?" + "&".join(f"{key}={value}" for key, value in params.items())


def _page(client, matter, **params: str) -> str:
    response = client.get(_documents_url(matter, **params))
    assert response.status_code == 200
    return response.content.decode()


def _file(matter, *, name: str, role: str = DocumentRole.KODA_SUBMISSION_FINAL, actor=None):
    """One document with one immutable version, through the services that own it."""
    document = create_document(matter=matter, title=name, role=role, created_by=actor)
    add_evidence_version(
        document=document,
        content=f"%PDF-1.4 {name}".encode(),
        original_filename=name,
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    document.refresh_from_db()
    return document


def _send(matter, document, *, actor, title="Koja arvamus", recipients=None, kind=None):
    """Bind a file already on the Matter to a SENT Submission, the long way.

    The three services, not `register_sent_opinion`, so a test of the union does
    not depend on the composition that also has to satisfy it.
    """
    submission = create_submission(
        matter=matter,
        title=title,
        kind=kind or SubmissionKind.FORMAL_OPINION,
        actor=actor,
        recipients=recipients or [],
    )
    select_final_evidence(submission=submission, version=document.current_version, actor=actor)
    return mark_submission_sent(submission=submission, actor=actor)


# ---------------------------------------------------------------------------
# 1. The label: `Arvamus`, never `Lõplik`
# ---------------------------------------------------------------------------


def test_a_role_classified_opinion_is_badged_arvamus(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    body = _page(signed_in, matter)

    assert "badge--opinion" in body
    assert "Arvamus" in body
    assert "Lõplik" not in body
    assert "★" not in body


def test_the_stored_role_is_not_rewritten_to_produce_the_label(signed_in, specialist):
    """Presentation, not storage. No migration, no backfill (docs/adr/0061 §26)."""
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    _page(signed_in, matter)

    document.refresh_from_db()
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    assert DocumentRole.KODA_SUBMISSION_FINAL.label == "Koja väljasaadetud arvamus"


def test_the_implementation_label_never_reaches_the_reader(signed_in, specialist):
    """`KODA_SUBMISSION_FINAL` may sit in the DOM as a form value; never as words."""
    matter = factories.MatterFactory(owner=specialist)
    _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    body = _page(signed_in, matter)

    assert ">KODA_SUBMISSION_FINAL<" not in body
    assert "Koja väljasaadetud arvamus" not in body


def test_the_upload_panel_offers_arvamus_and_still_posts_the_stored_role(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _page(signed_in, matter)

    assert '<option value="KODA_SUBMISSION_FINAL">Arvamus</option>' in " ".join(body.split())


# ---------------------------------------------------------------------------
# 2. The union: role, or the evidence of a send
# ---------------------------------------------------------------------------


def test_final_evidence_of_a_send_is_an_opinion_whatever_the_file_is_classified_as(
    signed_in, specialist
):
    """The half that did not work before (UX-005).

    `select_final_evidence` binds the evidence and leaves the classification
    alone — a letter that arrived from a ministry is still an incoming official
    document even after Koda relied on those bytes — so the role branch alone
    would call this file something other than the Chamber's opinion.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _file(
        matter,
        name="Saadetud_tekst.pdf",
        role=DocumentRole.INCOMING_AUTHORITY,
        actor=specialist,
    )
    _send(matter, document, actor=specialist)

    body = _page(signed_in, matter)

    assert "Saadetud_tekst.pdf" in body
    assert "badge--opinion" in body
    # And the classification really was left alone.
    document.refresh_from_db()
    assert document.role == DocumentRole.INCOMING_AUTHORITY


def test_a_document_qualifying_both_ways_is_one_row(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist)

    body = _page(signed_in, matter)

    assert body.count(f'id="dokument-{document.pk}"') == 1
    assert body.count("badge--opinion") == 1


def test_a_draft_submissions_evidence_is_not_yet_an_opinion(signed_in, specialist):
    """UX-005 in the other direction: a badge must not assert a send.

    A draft's `final_version` is a text somebody is preparing. Calling it
    `Arvamus` beside the sent ones would claim an act that has not happened.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _file(
        matter, name="Mustand.pdf", role=DocumentRole.WORKING_DOCUMENT, actor=specialist
    )
    submission = create_submission(matter=matter, title="Koostamisel", actor=specialist)
    select_final_evidence(submission=submission, version=document.current_version, actor=specialist)

    body = _page(signed_in, matter)

    assert "Mustand.pdf" in body
    assert "badge--opinion" not in body


def test_every_opinion_on_a_matter_appears(signed_in, specialist):
    """An initial opinion, a supplement and a joint letter are three rows.

    There is no `Matter.final_opinion` and there is not going to be one: a
    single-valued shortcut could only ever name one of them (docs/adr/0061 §10).
    """
    matter = factories.MatterFactory(owner=specialist)
    names = ("Esimene.pdf", "Taiendav.pdf", "Uhispoordumine.pdf")
    for name in names:
        _file(matter, name=name, actor=specialist)

    body = _page(signed_in, matter, roll=OPINION_ROLE_FILTER)

    for name in names:
        assert name in body, name
    assert body.count("badge--opinion") == 3


# ---------------------------------------------------------------------------
# 3. The Roll filter
# ---------------------------------------------------------------------------


def test_the_arvamus_filter_returns_the_whole_union(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    by_role = _file(matter, name="Rollijargi.pdf", actor=specialist)
    by_send = _file(
        matter, name="Saatmisejargi.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=specialist
    )
    _send(matter, by_send, actor=specialist)
    unrelated = _file(
        matter, name="Ministeeriumist.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=specialist
    )

    body = _page(signed_in, matter, roll=OPINION_ROLE_FILTER)

    assert by_role.current_version.original_filename in body
    assert by_send.current_version.original_filename in body
    assert unrelated.current_version.original_filename not in body


def test_the_filter_menu_offers_arvamus_and_not_the_stored_role(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _page(signed_in, matter)
    compact = " ".join(body.split())

    assert f'<option value="{OPINION_ROLE_FILTER}"' in compact
    # The stored role is still a real filter value and an old URL still works;
    # it is simply not on the menu, because it cannot express the union.
    assert '<option value="KODA_SUBMISSION_FINAL" ' not in compact


def test_an_old_role_filter_link_still_finds_the_opinions(signed_in, specialist):
    """A saved `?roll=KODA_SUBMISSION_FINAL` returns a strict superset.

    Everything the role matched, plus the sent opinions whose file was never
    reclassified — which is what somebody who saved that link was looking for
    (docs/adr/0061 §9).
    """
    matter = factories.MatterFactory(owner=specialist)
    by_role = _file(matter, name="Rollijargi.pdf", actor=specialist)
    by_send = _file(
        matter, name="Saatmisejargi.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=specialist
    )
    _send(matter, by_send, actor=specialist)

    body = _page(signed_in, matter, roll="KODA_SUBMISSION_FINAL")

    assert by_role.current_version.original_filename in body
    assert by_send.current_version.original_filename in body


def test_an_unrelated_role_filter_is_unaffected(signed_in, specialist):
    """Asked of the file table, which is the thing the filter filters.

    The `Arvamused` block under it is not filtered and must not be: it lists
    what somebody owes work on and what may be registered as sent, and hiding
    those because the table above is showing incoming mail would make the block
    disagree with itself.
    """
    matter = factories.MatterFactory(owner=specialist)
    _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    incoming = _file(
        matter, name="Ministeeriumist.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=specialist
    )

    body = _page(signed_in, matter, roll=DocumentRole.INCOMING_AUTHORITY)
    table = body.split('id="failid-heading"', 1)[1].split('id="arvamuste-haldus"', 1)[0]

    assert incoming.current_version.original_filename in table
    assert "Koja_arvamus.pdf" not in table
    assert "badge--opinion" not in table


# ---------------------------------------------------------------------------
# 4. What the row says, and what it does not
# ---------------------------------------------------------------------------


def test_a_sent_opinion_row_carries_the_date_and_the_addressee(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist, recipients=[organisation])

    body = _page(signed_in, matter)

    assert "doctable__sent" in body
    assert "Saadetud" in body
    assert organisation.name in body


def test_an_uploaded_opinion_claims_no_send(signed_in, specialist):
    """A file on the record is not a claim that anything went out (§18)."""
    matter = factories.MatterFactory(owner=specialist)
    _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    body = _page(signed_in, matter)

    assert "badge--opinion" in body
    assert "doctable__sent" not in body
    assert "Saatmist ei ole registreeritud." in body


def test_a_date_only_send_never_renders_an_invented_time(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    submission = create_submission(matter=matter, title="Ajalooline", actor=specialist)
    select_final_evidence(submission=submission, version=document.current_version, actor=specialist)
    mark_submission_sent(
        submission=submission,
        actor=specialist,
        sent_at=timezone.make_aware(datetime.datetime(2024, 4, 10, 0, 0)),
        sent_at_precision=SentAtPrecision.DATE,
    )

    body = _page(signed_in, matter)

    assert "10.4.2024" in body
    assert "10.4.2024 00:00" not in body


def test_the_row_states_no_evidence_mechanics(signed_in, specialist):
    """No checksum, no `Tõend` badge, no duplicated size (§11, §12, §28).

    The version and the size have columns of their own in this table. Printing
    them again under the filename was the retired card's habit, not a fact
    anybody was missing.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist)
    version = document.current_version

    body = _page(signed_in, matter)

    assert version.sha256 not in body
    assert version.sha256[:16] not in body
    assert "SHA-256" not in body
    assert "Täpne saadetud fail" not in body
    assert "evidenceblock" not in body
    assert "badge--evidence" not in body


# ---------------------------------------------------------------------------
# 5. Management is secondary
# ---------------------------------------------------------------------------


def test_withdrawal_is_behind_the_rows_management_disclosure(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    submission = _send(matter, document, actor=specialist, recipients=[organisation])

    body = _page(signed_in, matter)
    menu = body.split('id="dokument-', 1)[1]

    assert "opinionmenu" in menu
    assert "Saatmise andmed" in menu
    assert "Võta tagasi" in menu
    # A POST with its own form, never a link: a withdrawal a prefetcher could
    # perform is not a withdrawal anybody decided on.
    action = reverse("submissions:withdraw", kwargs={"pk": submission.pk})
    assert f'action="{action}"' in menu
    assert f'href="{action}"' not in menu


def test_the_send_details_keep_every_fact_the_retired_card_carried(
    signed_in, specialist, organisation
):
    """Kind, channel, reference, teadmiseks and co-signatories (§14, §35)."""
    other = factories.OrganisationFactory(name="Teine ministeerium")
    partner = factories.OrganisationFactory(name="Kaasesitaja liit")
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    submission = create_submission(
        matter=matter,
        title="Ühispöördumine",
        kind=SubmissionKind.JOINT_LETTER,
        actor=specialist,
        recipients=[organisation],
        for_information=[other],
        joint_submitters=[partner],
        channel="EIS",
        reference="1-2/26-345",
    )
    select_final_evidence(submission=submission, version=document.current_version, actor=specialist)
    mark_submission_sent(submission=submission, actor=specialist)

    body = _page(signed_in, matter)

    assert "Ühispöördumine" in body or SubmissionKind.JOINT_LETTER.label in body
    assert organisation.name in body
    assert other.name in body
    assert partner.name in body
    assert "EIS" in body
    assert "1-2/26-345" in body


def test_a_reader_is_offered_no_write_control(client, reader, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    submission = _send(matter, document, actor=specialist, recipients=[organisation])
    client.force_login(reader)

    body = _page(client, matter)

    assert "Koja_arvamus.pdf" in body
    assert "Võta tagasi" not in body
    assert "+ Uus arvamus" not in body
    assert "+ Registreeri saatmine" not in body
    assert reverse("submissions:withdraw", kwargs={"pk": submission.pk}) not in body


def test_a_draft_is_shown_compactly_and_only_while_it_exists(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    create_submission(matter=matter, title="Koostamisel arvamus", actor=specialist)

    body = _page(signed_in, matter)

    assert "Koostamisel arvamus" in body
    assert "draftrow" in body
    # An action somebody owes, so the block is open rather than folded away.
    assert 'id="arvamuste-haldus"' in body


# ---------------------------------------------------------------------------
# 6. Registering a send from a file already on the record
# ---------------------------------------------------------------------------


def test_registering_a_send_creates_one_canonical_submission(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    response = signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus pakendiseadusele",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-recipients": [str(organisation.pk)],
            "saadetud-channel": "EIS",
            "saadetud-reference": "1-2/26-9",
            "saadetud-sent_on": "2026-06-01",
        },
    )

    assert response.status_code == 302
    assert reverse("matters:matter_documents", kwargs={"pk": matter.pk}) in response.url
    assert f"#dokument-{document.pk}" in response.url

    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version_id == document.current_version_id
    assert submission.sent_at_precision == SentAtPrecision.DATE
    # `localdate`, because the stored moment is aware midnight in the
    # department's timezone and reading `.date()` off the UTC value it comes
    # back as would report the day before (app/matters/forms.py `_as_datetime`).
    assert timezone.localdate(submission.sent_at) == datetime.date(2026, 6, 1)
    assert submission.channel == "EIS"
    assert submission.reference == "1-2/26-9"
    assert [row.organisation for row in submission.recipient_rows.all()] == [organisation]


def test_registering_a_send_writes_the_audit_events_the_services_own(
    signed_in, specialist, organisation
):
    """Composition, not a fourth implementation (§17, §42)."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-recipients": [str(organisation.pk)],
        },
    )

    kinds = set(ChangeEvent.objects.filter(matter=matter).values_list("event_type", flat=True))
    assert ChangeEventType.SUBMISSION_CREATED in kinds
    assert ChangeEventType.SUBMISSION_SENT in kinds


def test_registering_a_send_refuses_a_document_from_another_matter(signed_in, specialist):
    """The form's own choices are a usability gate, never the authorization one."""
    matter = factories.MatterFactory(owner=specialist)
    other = factories.MatterFactory(owner=specialist)
    elsewhere = _file(other, name="Vale_teema.pdf", actor=specialist)

    response = signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(elsewhere.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
        },
    )

    assert response.status_code == 302
    assert not Submission.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=other).exists()


def test_a_reader_may_not_register_a_send_by_posting(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    client.force_login(reader)

    response = client.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
        },
    )

    assert response.status_code == 404
    assert not Submission.objects.filter(matter=matter).exists()


def test_a_registered_opinion_leaves_the_unregistered_list(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist)

    response = signed_in.get(_documents_url(matter))

    assert list(response.context["unregistered_opinions"]) == []
    assert "+ Registreeri saatmine" not in response.content.decode()


# ---------------------------------------------------------------------------
# 7. The whole lifecycle, from its new home
# ---------------------------------------------------------------------------


def test_the_two_opinion_forms_do_not_share_element_ids(signed_in, specialist):
    """Three forms on this page carry a `title`; one id each.

    Duplicate ids make every `<label for>` ambiguous for a screen reader, and
    they made `#id_title` a strict-mode violation for the browser suite — which
    is how this was found (app/submissions/forms.py).
    """
    matter = factories.MatterFactory(owner=specialist)
    _file(matter, name="Koja_arvamus.pdf", actor=specialist)

    body = _page(signed_in, matter)

    assert body.count('id="id_title"') == 1
    assert 'id="id_arvamus-title"' in body
    assert 'id="id_saadetud-title"' in body


def test_the_full_opinion_lifecycle_runs_from_documents(signed_in, specialist, organisation):
    """Draft, evidence, recipients, send, read back, withdraw — all on one page."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent

    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(
        reverse("submissions:create", kwargs={"matter_id": matter.pk}),
        {
            "arvamus-title": "Koja arvamus eelnõule",
            "arvamus-kind": SubmissionKind.FORMAL_OPINION,
            "arvamus-recipients": [str(organisation.pk)],
            "arvamus-channel": "EIS",
        },
    )
    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.DRAFT
    assert "Koja arvamus eelnõule" in _page(signed_in, matter)

    signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": submission.pk}),
        {
            "upload": SimpleUploadedFile(
                "koja-arvamus.pdf", b"%PDF-1.4 final", content_type="application/pdf"
            )
        },
    )
    submission.refresh_from_db()
    assert submission.final_version_id is not None

    sent = signed_in.post(
        reverse("submissions:mark_sent", kwargs={"pk": submission.pk}),
        {"channel": "EIS", "reference": "1-2/26-77"},
    )
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.SENT
    # Back on the row that changed, not at the top of the file list.
    assert f"#dokument-{submission.final_version.document_id}" in sent.url

    body = _page(signed_in, matter)
    assert "koja-arvamus.pdf" in body
    assert "badge--opinion" in body
    assert organisation.name in body
    assert "1-2/26-77" in body
    # The draft block no longer mentions it: one opinion, one representation.
    assert "draftrow" not in body

    signed_in.post(reverse("submissions:withdraw", kwargs={"pk": submission.pk}))
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.WITHDRAWN
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.SUBMISSION_WITHDRAWN
    ).exists()
    # The exact evidence of what was sent survives the withdrawal.
    assert submission.final_version_id is not None


def test_reading_documents_writes_no_change_event(signed_in, specialist):
    """Opening a page is not a business action (§42)."""
    from app.audit.models import ChangeEvent

    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist)
    before = ChangeEvent.objects.filter(matter=matter).count()

    _page(signed_in, matter)
    _page(signed_in, matter, roll=OPINION_ROLE_FILTER)

    assert ChangeEvent.objects.filter(matter=matter).count() == before


# ---------------------------------------------------------------------------
# 8. Authorization
# ---------------------------------------------------------------------------


def test_a_restricted_opinion_is_not_named_badged_anchored_or_counted(client, reader, specialist):
    """AUTH-003 §21: a filename is a disclosure whether or not the bytes are."""
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Salajane_arvamus.pdf", actor=specialist)
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    _send(matter, document, actor=specialist)
    client.force_login(reader)

    response = client.get(_documents_url(matter))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Salajane_arvamus.pdf" not in body
    assert "badge--opinion" not in body
    assert f"dokument-{document.pk}" not in body
    assert document.current_version.sha256[:16] not in body
    assert list(response.context["unregistered_opinions"]) == []


def test_a_restricted_opinion_is_not_named_in_the_rail(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Salajane_arvamus.pdf", actor=specialist)
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    client.force_login(reader)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()

    assert "Salajane_arvamus.pdf" not in body
    assert "Arvamust ei ole lisatud." in body


def test_the_arvamus_filter_does_not_widen_visibility(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Salajane_arvamus.pdf", actor=specialist)
    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])
    client.force_login(reader)

    body = _page(client, matter, roll=OPINION_ROLE_FILTER)

    assert "Salajane_arvamus.pdf" not in body


# ---------------------------------------------------------------------------
# 9. The retired address
# ---------------------------------------------------------------------------


def test_the_old_address_redirects_to_the_matters_opinions(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.get(reverse("matters:matter_position", kwargs={"pk": matter.pk}))

    assert response.status_code == 302
    assert response.url == _documents_url(matter, roll=OPINION_ROLE_FILTER)


def test_the_old_address_does_not_loop(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.get(
        reverse("matters:matter_position", kwargs={"pk": matter.pk}), follow=True
    )

    assert response.status_code == 200
    assert len(response.redirect_chain) == 1


def test_the_old_address_still_checks_who_is_asking(client, reader, specialist):
    """404 and never a redirect: a redirect would confirm the Matter exists.

    A READER, because both lawyer roles read the department including its
    RESTRICTED files since docs/adr/0042 — a "stranger specialist" is not a
    stranger to this Matter and would prove nothing.
    """
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    client.force_login(reader)

    response = client.get(reverse("matters:matter_position", kwargs={"pk": matter.pk}))

    assert response.status_code == 404
    assert reverse("matters:matter_documents", kwargs={"pk": matter.pk}) not in str(
        response.content
    )


def test_nothing_in_the_product_links_to_the_retired_address(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist)
    retired = reverse("matters:matter_position", kwargs={"pk": matter.pk})

    for name in ("matters:matter_detail", "matters:matter_documents"):
        body = signed_in.get(reverse(name, kwargs={"pk": matter.pk})).content.decode()
        assert f'href="{retired}"' not in body, name


# ---------------------------------------------------------------------------
# 10. Search routing
# ---------------------------------------------------------------------------


def test_a_submission_result_targets_the_matters_opinions_and_not_the_retired_page(
    specialist,
):
    """The routing rule on its own, without depending on how a query ranks."""
    from app.search.services import SearchResult
    from app.search.views import _target_url

    matter = factories.MatterFactory(owner=specialist)
    result = SearchResult(
        matter=matter,
        match_kind="body",
        rank=1.0,
        source_kind=SearchSourceKind.SUBMISSION,
        submission_id="0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f",
    )

    target = _target_url(result)

    assert target == _documents_url(matter, roll=OPINION_ROLE_FILTER)
    assert reverse("matters:matter_position", kwargs={"pk": matter.pk}) not in target
    # No anchor built from the submission: a Submission a reader may find can
    # point at a Document restricted below it, and an anchor made from the
    # submission alone would name a file the page then refuses to render
    # (docs/adr/0061, AUTH-003 §21).
    assert "#" not in target


def test_the_other_search_targets_are_unchanged(specialist):
    """A rule that moved one case must not have moved the other four."""
    from app.search.services import SearchResult
    from app.search.views import _target_url

    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    matter_url = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    plain = SearchResult(matter=matter, match_kind="body", rank=1.0)
    assert _target_url(plain) == matter_url

    fragment = SearchResult(
        matter=matter,
        match_kind="body",
        rank=1.0,
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT,
        document_id=document.pk,
    )
    assert _target_url(fragment) == reverse("documents:document_detail", kwargs={"pk": document.pk})


def test_a_submission_search_result_reaches_the_opinions_end_to_end(
    signed_in, specialist, organisation
):
    """The same rule through the real page, found the way a lawyer finds it.

    A reference rather than a title word: `Submission.reference` is indexed and
    exact, so this asserts the routing rather than the tokeniser
    (tests/test_content_search.py).
    """
    matter = factories.MatterFactory(owner=specialist, title="Pakendiseaduse eelnõu")
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    submission = create_submission(
        matter=matter,
        title="Koja arvamus",
        actor=specialist,
        recipients=[organisation],
        reference="AINULAADNEVIIDE-42",
    )
    select_final_evidence(submission=submission, version=document.current_version, actor=specialist)
    mark_submission_sent(submission=submission, actor=specialist)

    body = signed_in.get(reverse("search:search"), {"q": "AINULAADNEVIIDE-42"}).content.decode()

    assert _documents_url(matter, roll=OPINION_ROLE_FILTER) in body
    assert reverse("matters:matter_position", kwargs={"pk": matter.pk}) not in body


# ---------------------------------------------------------------------------
# 11. The provenance left the screen and stayed in the database
# ---------------------------------------------------------------------------


def _archive_letter(*, sha: str = "b" * 64, title: str = "Varasem kiri"):
    """One held archive letter, its batch, its binary and its occurrence.

    Every string invented; mirrors `tests/test_opinions_workspace.py`, which is
    the suite that owns the corpus.
    """
    from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
    from app.legacy_import.opinion_binary import OpinionArchiveBinary

    batch, _ = OpinionArchiveBatch.objects.get_or_create(
        archive_sha256="a" * 64,
        defaults={"importer_version": "test/0", "started_at": timezone.now()},
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key=f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}",
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    item = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path=f"Opinions/2024/{sha[:6]}.pdf",
        original_filename="naidis.pdf",
        sha256=sha,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient="Näidisministeerium",
        filename_title=title,
        binary=binary,
    )
    return batch, binary, item


def test_archive_reconstruction_diagnostics_are_not_user_facing(signed_in, specialist):
    """The four sentences that used to sit under every restored opinion.

    They belong to reconciliation and to the operator investigating a match, not
    to a lawyer reading a Matter — and none of the rows behind them is touched
    (docs/adr/0061 §29).
    """
    from app.legacy_import.opinion_archive import OpinionSubmissionImport
    from app.legacy_import.opinion_enums import (
        OpinionMatchClass,
        RecipientBasis,
        SentDateBasis,
    )

    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Taastatud_arvamus.pdf", actor=specialist)
    submission = _send(matter, document, actor=specialist)
    batch, _binary, item = _archive_letter()
    record = OpinionSubmissionImport.objects.create(
        batch=batch,
        item=item,
        submission=submission,
        match_class=OpinionMatchClass.CONTENT_MULTI_SIGNAL,
        sent_date_basis=SentDateBasis.EXCEL_OUT_DATE,
        recipient_basis=RecipientBasis.EXCEL_ADDRESSEE,
    )

    body = _page(signed_in, matter)

    for phrase in (
        "Taastatud arvamuste arhiivist",
        "teema tuvastus",
        "kuupäeva alus",
        "saaja alus",
        "SHA-256",
    ):
        assert phrase not in body, phrase

    # Every row is still there, and still says what it said.
    record.refresh_from_db()
    assert record.match_class == OpinionMatchClass.CONTENT_MULTI_SIGNAL
    assert record.sent_date_basis == SentDateBasis.EXCEL_OUT_DATE
    assert record.recipient_basis == RecipientBasis.EXCEL_ADDRESSEE
    assert OpinionSubmissionImport.objects.filter(submission=submission).exists()


def test_the_exact_evidence_and_its_checksum_are_untouched(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    submission = _send(matter, document, actor=specialist)
    version = document.current_version

    _page(signed_in, matter)

    submission.refresh_from_db()
    assert submission.final_version_id == version.pk
    version.refresh_from_db()
    assert len(version.sha256) == 64
    # And the checksum is still stated where evidence is stated.
    detail = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": document.pk})
    ).content.decode()
    assert version.sha256 in detail


def test_no_document_role_was_rewritten_anywhere_on_the_page(signed_in, specialist):
    """No data migration, and no quiet one either (docs/adr/0061 §40)."""
    matter = factories.MatterFactory(owner=specialist)
    incoming = _file(
        matter, name="Ministeeriumist.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=specialist
    )
    _send(matter, incoming, actor=specialist)

    before = dict(Document.objects.filter(matter=matter).values_list("pk", "role"))
    _page(signed_in, matter)
    _page(signed_in, matter, roll=OPINION_ROLE_FILTER)
    after = dict(Document.objects.filter(matter=matter).values_list("pk", "role"))

    assert before == after
    assert after[incoming.pk] == DocumentRole.INCOMING_AUTHORITY


# ---------------------------------------------------------------------------
# 12. Linked archive letters keep a home
# ---------------------------------------------------------------------------


def _link_a_letter(matter, *, sha: str = "b" * 64, title: str = "Varasem kiri"):
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter
    from app.legacy_import.opinion_search import rebuild_archive_index

    _batch, binary, _item = _archive_letter(sha=sha, title=title)
    link_matter(
        binary=binary,
        matter=matter,
        basis=ArchiveLinkBasis.REVIEWED,
        actor=factories.DepartmentHeadFactory(),
    )
    # The row renders its title from the archive's own projection, so the
    # projection has to exist.
    rebuild_archive_index()
    return binary


def test_an_archive_reader_still_reaches_the_letters_from_the_matter(client, specialist):
    """Retiring a surface could not be allowed to take access with it (§22).

    Signed in as an ADMINISTRATOR, because `may_read_archive` is a question
    about the corpus and this fixture set gives that role the corpus
    (docs/adr/0028, docs/adr/0056).
    """
    matter = factories.MatterFactory(owner=specialist)
    binary = _link_a_letter(matter)
    client.force_login(factories.AdministratorFactory())

    body = _page(client, matter)

    assert "Seotud arhiivikirjad" in body
    assert "Varasem kiri" in body
    assert reverse("legacy_import:opinion_archive_detail", kwargs={"pk": binary.pk}) in body
    # Evidence of correspondence, never a dispatch record: no match provenance
    # rides along with it.
    assert "teema tuvastus" not in body
    assert "match_class" not in body


def test_a_reader_without_the_archive_gets_no_row_no_count_and_no_hint(client, specialist):
    from app.accounts.enums import UserRole

    matter = factories.MatterFactory(owner=specialist)
    _link_a_letter(matter, sha="c" * 64, title="Salajane kiri")
    client.force_login(factories.UserFactory(role=UserRole.READER))

    body = _page(client, matter)

    assert "Seotud arhiivikirjad" not in body
    assert "seotud-arhiivikirjad" not in body
    assert "Salajane kiri" not in body


def test_archive_letters_are_kept_apart_from_the_file_table(client, specialist):
    """The weaker claim must not look like the stronger one.

    A canonical Submission says Koda sent an opinion. An archive letter says we
    hold a file somebody judged to concern this teema, with no date or recipient
    promoted to a canonical fact.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _send(matter, document, actor=specialist)
    _link_a_letter(matter)
    client.force_login(factories.AdministratorFactory())

    body = _page(client, matter)
    letters = body.split('id="seotud-arhiivikirjad"', 1)[1]

    assert "Varasem kiri" in letters
    assert "Koja_arvamus.pdf" not in letters
    assert "badge--opinion" not in letters


# ---------------------------------------------------------------------------
# 13. No projection change
# ---------------------------------------------------------------------------


def test_neither_search_index_version_moves():
    """A URL computed at render time is not a projection change (§41).

    Asserted against the two constants rather than described in a commit
    message: the whole cost of getting this wrong is a rebuild nobody
    authorised, and a rebuild is decided by these two strings.
    """
    from app.legacy_import.opinion_search_models import ARCHIVE_INDEX_VERSION
    from app.search.models import INDEX_VERSION

    assert INDEX_VERSION == "AUTH003.1"
    assert ARCHIVE_INDEX_VERSION == "1"
