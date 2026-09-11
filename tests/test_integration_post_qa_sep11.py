"""Where the four QA-correctness branches meet, and could only meet here.

Each of #180, #181, #182 and #183 is green on its own branch, and each one's
tests assert the rule it changed. What no branch could assert is what happens
where two of them touch the same record from opposite directions — because on
the branch that wrote one half, the other half did not exist.

Four seams, and the question each one answers:

**Workspace evidence against the opinion register (#180 x #182).** #180 taught
six operations to capture files; #182 narrowed what `Registreeri saatmine` will
accept. A file attached to a `Töövõit` is now a `Document` on the Matter with a
stored binary, which is most of what an opinion candidate looks like. It must
not be offered as one, and the reason must be structural rather than a
coincidence of ordering.

**Closure against evidence capture (#180 internal, with a file).** The branch
proves a stale `LISA TEEMALE` save with a file leaves no partial evidence, and
proves a stale *completion* is refused - but never the two together, which is
the combination the reported incident actually had: a lawyer describing what
they did, attaching the file that proves it, into a tab that no longer matches
the world.

**A closed Matter against the opinion routes (#180 x #182).** Recorded rather
than changed: `register_sent_opinion` deliberately does not take the
closed-Matter guard, because filing a historical send onto finished work is
what the register import does. The test pins the behaviour so that a later
decision to guard it is a decision, not a discovery.

**Register state against the workspace (#181 x #180).** The chip row is built
from the request's own query string, and #180 added no register parameter - so
the fix must survive a live Matter carrying the new workspace's records.
"""

from __future__ import annotations

import datetime
import html
from urllib.parse import parse_qs

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.links import DocumentLink
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.intelligence.models import MatterWorkVictory
from app.matters import workspace
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Entry
from app.matters.services import close_matter
from app.submissions.enums import SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.opinions import unregistered_opinion_documents
from app.submissions.services import (
    create_submission,
    mark_submission_sent,
    register_sent_opinion,
    select_final_evidence,
)
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _post(client, route, matter, payload, files=None):
    data = dict(payload)
    if files:
        data["attachments"] = files
    return client.post(
        reverse(route, kwargs={"pk": matter.pk}),
        data,
        headers={"HX-Request": "true"},
    )


def _action(matter, actor, *, text: str = "Vaadata ministeeriumi vastus ule"):
    return set_next_action(
        matter=matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + datetime.timedelta(days=7),
        actor=actor,
    )


def _close_elsewhere(matter, actor):
    """Close the Matter the way the other tab does: through the domain service."""
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=actor, reason="QA")
    matter.refresh_from_db()
    return matter


def _opinion_file(matter, *, name: str, actor):
    """A file the application classifies as the Chamber's opinion."""
    document = create_document(
        matter=matter, title=name, role=DocumentRole.KODA_SUBMISSION_FINAL, created_by=actor
    )
    add_evidence_version(
        document=document,
        content=f"%PDF-1.4 {name}".encode(),
        original_filename=name,
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    document.refresh_from_db()
    return document


# ===========================================================================
# 1. Workspace evidence is not an opinion candidate  (#180 x #182)
# ===========================================================================


def test_a_file_attached_to_a_work_victory_is_never_a_send_candidate(
    signed_in, normal_matter, specialist
):
    """The seam neither branch could see.

    #180 made `+ Töövõit` capture files; #182 made `Registreeri saatmine` read
    one list to decide what it will accept. Both now write and read `Document`
    rows on the same Matter, and the only thing keeping them apart is that
    `capture_supporting_evidence` files workspace evidence as
    `DocumentRole.OTHER` while `opinion_documents` matches
    `KODA_SUBMISSION_FINAL` or a sent submission's own evidence.

    Asserted through the read model *and* the service, because the read model
    only decides what the page offers.
    """
    response = _post(
        signed_in,
        "matters:add_work_victory",
        normal_matter,
        {"victory_change": "Uleminekuaeg pikenes kuue kuuni"},
        files=[_pdf("toovoidu_tous.pdf")],
    )
    assert response.status_code == 200, response.status_code

    victory = MatterWorkVictory.objects.get(matter=normal_matter)
    link = DocumentLink.objects.get(work_victory=victory)
    document = link.document

    assert document.role == DocumentRole.OTHER
    assert document.current_version_id is not None, "the bytes really are stored"

    candidates = unregistered_opinion_documents(normal_matter, viewer=specialist)
    assert document.pk not in {candidate.pk for candidate in candidates}

    # And a crafted post naming it is refused, not merely un-offered. Measured
    # rather than assumed: `register_sent_opinion` itself does *not* look at
    # `Document.role` - it is the composition the archive importer also uses,
    # and refusing by role there would stop a legitimate historical filing. The
    # guarantee lives one level up, where `register_sent` resolves the posted
    # identifier inside the candidate set it built for this reader, so the
    # reachable surface refuses even though the service would not (AUTH-003
    # §21).
    response = signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": normal_matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Voltsitud registreering",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-sent_on": "1.09.2026",
            "saadetud-recipients": str(factories.OrganisationFactory().pk),
        },
        follow=True,
    )

    # Refused for naming a document that is not a candidate, and not merely for
    # some other field being wrong: `sent_on` and `recipients` are both filled
    # in, so `document` is the only thing left to refuse. (`sent_on`, not
    # `sent_at` - the model attribute and the form field are spelled
    # differently, and a payload carrying the wrong key would have been refused
    # for the missing date instead, which is passing for the wrong reason.)
    assert response.status_code == 200, response.status_code
    body = response.content.decode()
    assert "Saatmise registreerimine ebaõnnestus" in body
    assert "Saadetud fail" in body, "the refusal did not name the document field"
    assert Submission.objects.filter(matter=normal_matter).count() == 0


def test_the_workspace_file_and_a_real_opinion_stay_apart_on_the_same_matter(
    signed_in, normal_matter, specialist
):
    """Both files exist on one Matter; exactly one of them is offered."""
    _post(
        signed_in,
        "matters:add_note",
        normal_matter,
        {"body": "<p>Ministeeriumi kiri saabus.</p>"},
        files=[_pdf("ministeeriumi_kiri.pdf")],
    )
    opinion = _opinion_file(normal_matter, name="Koja_arvamus.pdf", actor=specialist)

    candidates = unregistered_opinion_documents(normal_matter, viewer=specialist)

    assert [candidate.pk for candidate in candidates] == [opinion.pk]
    assert Document.objects.filter(matter=normal_matter).count() == 2


# ===========================================================================
# 2. A stale completion carrying a file  (#180, the combination it was missing)
# ===========================================================================


def test_a_stale_completion_with_a_file_leaves_no_entry_no_document_and_no_link(
    signed_in, normal_matter, specialist
):
    """The reported incident's exact shape.

    The branch proves a stale *add* with a file leaves no partial evidence, and
    proves a stale *completion* is refused - separately. This is both at once,
    which is what a lawyer finishing a task actually does: describe the result
    and attach the proof of it.

    All four state probes matter. A refusal that wrote the `Document` and not
    the `Entry` would leave a file on a closed Matter supporting nothing, and
    `check_evidence_integrity` would have no way to say what it was for.

    **Two mechanisms stand behind that outcome, and only one of them is being
    named here.** `close_matter` cancels the open step through
    `end_open_action_for_closure`, so the stale-action check would refuse this
    even with no closed-Matter guard at all - measured: with the `is_open` test
    removed from `lock_open_matter_for_business_write`, every state assertion
    below still holds. The state alone therefore cannot tell the two apart, so
    the service is asked directly for which refusal it gives. The guard runs
    first, and a closed Matter must say it is closed rather than tell somebody
    to refresh a page that will not help them.
    """
    action = _action(normal_matter, specialist)
    _close_elsewhere(normal_matter, specialist)

    response = _post(
        signed_in,
        "matters:complete_current_action",
        normal_matter,
        {"action_id": str(action.pk), "body": "<p>Saatsin arvamuse valja.</p>"},
        files=[_pdf("saadetud_arvamus.pdf")],
    )

    assert response.status_code == 400, response.status_code
    assert Entry.objects.filter(matter=normal_matter).count() == 0
    assert Document.objects.filter(matter=normal_matter).count() == 0
    assert DocumentLink.objects.count() == 0
    action.refresh_from_db()
    assert action.status != ActionStatus.COMPLETED

    with pytest.raises(DomainError) as refusal:
        workspace.complete_current_action(
            matter=normal_matter,
            author=specialist,
            action_id=action.pk,
            body="<p>Saatsin arvamuse valja.</p>",
            uploads=[_pdf("saadetud_arvamus.pdf")],
        )
    assert str(refusal.value) == CLOSED_MATTER_REFUSAL


def test_the_same_completion_succeeds_whole_while_the_matter_is_open(
    signed_in, normal_matter, specialist
):
    """The other half: the refusal above is closure, not the file.

    Without this the test above would keep passing if attachments stopped
    working altogether.
    """
    action = _action(normal_matter, specialist)

    response = _post(
        signed_in,
        "matters:complete_current_action",
        normal_matter,
        {"action_id": str(action.pk), "body": "<p>Saatsin arvamuse valja.</p>"},
        files=[_pdf("saadetud_arvamus.pdf")],
    )

    assert response.status_code == 200, response.status_code
    entry = Entry.objects.get(matter=normal_matter)
    link = DocumentLink.objects.get(entry=entry)
    assert link.document.matter_id == normal_matter.pk
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED


# ===========================================================================
# 3. The opinion routes against a closed Matter  (#180 x #182, recorded)
# ===========================================================================


def test_registering_a_historical_send_still_works_on_a_closed_matter(normal_matter, specialist):
    """Pinned, not decided.

    `register_sent_opinion` takes `lock_matter_for_evidence_integrity` and not
    `lock_open_matter_for_business_write`, so a closed Matter accepts the
    registration of a send that already happened. That is deliberate and has
    precedent - `add_engagement` is unguarded at the leaf for the same reason,
    because the register import files finished work onto Matters that are
    closed - but the reachable *surface* is `Dokumendid`, whose `can_write` asks
    only about the reader's role.

    Whether a person should be able to do this from the page is a product
    question. This test states today's answer so that changing it is a decision
    somebody made rather than a behaviour that drifted.
    """
    document = _opinion_file(normal_matter, name="Koja_arvamus.pdf", actor=specialist)
    _close_elsewhere(normal_matter, specialist)
    recipient = factories.OrganisationFactory()

    submission = register_sent_opinion(
        document=document,
        version=document.current_version,
        title="Koja arvamus",
        actor=specialist,
        recipients=[recipient],
        sent_at=timezone.now() - datetime.timedelta(days=30),
        sent_at_precision=SentAtPrecision.DATE,
    )

    assert submission.status == SubmissionStatus.SENT
    assert submission.sent_at_precision == SentAtPrecision.DATE
    assert submission.sent_at.date() != timezone.localdate()


def test_a_drafts_final_evidence_is_still_refused_on_a_closed_matter(normal_matter, specialist):
    """#182's narrowing is about the record, not about the Matter's state."""
    document = _opinion_file(normal_matter, name="Koostatav_arvamus.pdf", actor=specialist)
    draft = create_submission(
        matter=normal_matter,
        title="Koostatav arvamus",
        kind=SubmissionKind.FORMAL_OPINION,
        actor=specialist,
    )
    select_final_evidence(submission=draft, version=document.current_version, actor=specialist)
    _close_elsewhere(normal_matter, specialist)

    with pytest.raises(DomainError):
        register_sent_opinion(
            document=document,
            version=document.current_version,
            title="Sama fail teist korda",
            actor=specialist,
            recipients=[factories.OrganisationFactory()],
            sent_at=timezone.now() - datetime.timedelta(days=1),
            sent_at_precision=SentAtPrecision.DATE,
        )

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert Submission.objects.filter(matter=normal_matter).count() == 1


def test_marking_a_draft_sent_still_means_now_beside_all_of_this(normal_matter, specialist):
    """The distinction #182 exists to keep: two acts, two sources of the date."""
    document = _opinion_file(normal_matter, name="Koostatav_arvamus.pdf", actor=specialist)
    draft = create_submission(
        matter=normal_matter,
        title="Koostatav arvamus",
        kind=SubmissionKind.FORMAL_OPINION,
        actor=specialist,
    )
    select_final_evidence(submission=draft, version=document.current_version, actor=specialist)

    sent = mark_submission_sent(submission=draft, actor=specialist)

    assert sent.status == SubmissionStatus.SENT
    assert sent.sent_at.date() == timezone.localdate()
    assert sent.sent_at_precision == SentAtPrecision.TIMESTAMP


# ===========================================================================
# 4. The register chip beside the new workspace  (#181 x #180)
# ===========================================================================


def test_removing_the_search_chip_keeps_every_other_dimension(signed_in, specialist):
    """#181's rule, on a register whose rows carry the new workspace's records.

    Asserted through the rendered `href` and not only the context value, the
    way #181's own test does, because a template that went back to
    `cleared_query` would leave a context assertion green - that is the shape
    the bug had. What is added here is the company it now keeps: a Matter with
    an open step written through `set_next_action`, listed beside the sort and
    three filters, so the fix is exercised against the state #180 produces
    rather than an empty register.
    """
    matter = factories.MatterFactory(owner=specialist, title="Eelnou uleminekuajast")
    set_next_action(
        matter=matter,
        text="Vaadata ule",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + datetime.timedelta(days=3),
        actor=specialist,
    )

    response = signed_in.get(
        reverse("matters:matter_list"),
        {
            "q": "eelnou",
            "olek": "koik",
            "vastutaja": str(specialist.pk),
            "jarjestus": "kuupaev_asc",
            "kaupa": "30",
        },
    )
    remaining = parse_qs(response.context["cleared_search_query"])

    assert "q" not in remaining
    assert remaining["olek"] == ["koik"]
    assert remaining["vastutaja"] == [str(specialist.pk)]
    assert remaining["jarjestus"] == ["kuupaev_asc"]
    assert remaining["kaupa"] == ["30"]

    body = response.content.decode()
    chip = body.split('<span class="filterchip__label">Otsing:</span>')[0]
    href = chip.rsplit('<a class="filterchip" href="?', 1)[1].split('"', 1)[0]
    assert parse_qs(html.unescape(href)) == remaining
