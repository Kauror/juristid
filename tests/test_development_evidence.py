"""Adding evidence to a `Menetluse areng` that is already on the chronology.

The product rule this module asserts:

    A `Menetluse areng` already filed may be given **more** evidence — by
    anybody who may author business content, on an open Matter only, from the
    chronology row it is on. The step itself is not touched, the evidence it
    already carries is not touched, and nothing is ever removed.

The gap this closes
-------------------
`app.documents.services` has been able to link a `Document` to a
`MatterProceduralDevelopment` since docs/adr/0091 §5: the canonical mapping names
the column, `capture_supporting_evidence` writes the three canonical rows in one
transaction, and `record_procedural_development_document` records which step the
bytes evidence. What there was no way to do was *reach* that path after the fact.
`+ Menetluse areng` captured the files that arrived **with** the step, and after
the panel closed there was no lawyer-facing route to the same pipeline — so a
revised draft that turned up a fortnight later had nowhere to go except the
Matter's general document list, where nothing says which step it is the evidence
for.

What this is not
----------------
**It is not QA-06 widened.** `Muuda` corrects what the row *says* — `Sündmus`,
the period, `Juristi märkus` — and deliberately carries no upload control, for
the reason `ExternalPositionEditForm` states: a correction form that also
captured bytes would make «what changed» unanswerable from one event, and a
correction that re-posted files could silently detach one. The two acts stay two
acts with two audit trails, and §D below is what holds them apart.

**It is not a replacement and not a removal.** A revised draft is new bytes on a
new document; no `DocumentVersion` is superseded and no `DocumentLink` is
deleted. There is no delete route, on an open Matter or a closed one (§N).

**And it carries no revision token**, deliberately. Optimistic concurrency exists
on the *correction* because two people editing one sentence is a lost update.
Two people attaching two different papers to one step is not: both links are
wanted and there is no earlier value for a later writer to overwrite. No evidence
capture in `app.matters.workspace` carries one, and inventing one here would
refuse the second lawyer's file to protect a sentence nobody touched (§M).
"""

from __future__ import annotations

import datetime as dt
import uuid as _uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.links import DocumentLink
from app.documents.models import Document, DocumentVersion
from app.documents.uploads import UploadRejected
from app.matters.models import MatterProceduralDevelopment
from app.matters.services import close_matter
from app.matters.timeline import matter_timeline
from app.matters.workspace import add_development_evidence, add_procedural_development
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db


# -- harness ----------------------------------------------------------------


def _days_ago(days: int) -> dt.date:
    """A day in the past, relative to the clock rather than written down.

    A chronology row only renders once its day has arrived, so a hard-coded date
    is a test that starts passing or failing depending on when it is run.
    """
    return timezone.localdate() - dt.timedelta(days=days)


HEADLINE = "Ministeerium saatis uue eelnõu versiooni"
NOTE = "Versioon ei arvesta meie varasemat ettepanekut."
FIRST_FILE = "esimene-eelnou.pdf"
LATER_FILE = "parandatud-eelnou.pdf"


def _pdf(name: str, body: bytes = b"%PDF-1.4 synthetic evidence") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def _url(matter, development) -> str:
    return reverse(
        "matters:add_development_evidence",
        kwargs={"pk": matter.pk, "development_id": development.pk},
    )


@pytest.fixture
def development(normal_matter, specialist):
    """One filed step, with one file already on it and a step it opened.

    Filed through `add_procedural_development` rather than built as rows: the
    point of almost every test below is that *this* operation's writes are left
    alone, and a record assembled by hand would have none of them to leave alone.
    """
    result = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title=HEADLINE,
        occurred_on=_days_ago(3),
        note=NOTE,
        next_text="Loen uue versiooni läbi",
        next_date=timezone.localdate() + dt.timedelta(days=4),
        uploads=[_pdf(FIRST_FILE)],
    )
    return result.record


def _links(development) -> list[DocumentLink]:
    return list(DocumentLink.objects.filter(procedural_development=development))


def _filenames(development) -> set[str]:
    return {
        version.original_filename
        for version in DocumentVersion.objects.filter(
            document__in=[link.document for link in _links(development)]
        )
    }


# ---------------------------------------------------------------------------
# §A. the act itself
# ---------------------------------------------------------------------------


def test_a_a_later_file_is_captured_against_the_development(development, specialist):
    """Requirement 1. A valid file arrives on a step already on the chronology."""
    result = add_development_evidence(
        development=development, author=specialist, uploads=[_pdf(LATER_FILE)]
    )

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.matter_id == development.matter_id
    assert DocumentLink.objects.filter(
        document=document, procedural_development=development
    ).exists()
    assert DocumentVersion.objects.filter(document=document, original_filename=LATER_FILE).exists()


def test_a_several_files_arrive_in_one_act(development, specialist):
    """The picker takes `multiple`, and one save is one operation.

    The two documents share an `operation_id`, because one person pressed one
    button once — which is the only thing that lets a reader tie them together
    later without either row holding a copy of the other.
    """
    result = add_development_evidence(
        development=development,
        author=specialist,
        uploads=[_pdf("lisa-a.pdf"), _pdf("lisa-b.pdf")],
    )

    assert len(result.documents) == 2
    events = ChangeEvent.objects.filter(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED,
        object_id=development.pk,
    )
    assert events.count() == 3  # the original file, and these two
    assert len({event.operation_id for event in events.order_by("-created_at")[:2]}) == 1


def test_b_the_evidence_that_was_already_there_remains(development, specialist):
    """Requirement 2. Additive means additive: the first file is still linked."""
    before = {link.document_id for link in _links(development)}
    assert before  # the fixture put one there

    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    after = {link.document_id for link in _links(development)}
    assert before < after
    assert _filenames(development) == {FIRST_FILE, LATER_FILE}


def test_c_the_new_file_renders_on_this_development_and_no_other(
    normal_matter, development, specialist
):
    """Requirement 3. The chronology puts the file under the step it evidences.

    A second development is filed beside the first precisely so that «it renders
    somewhere on the page» cannot pass for «it renders on the right row».
    """
    other = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Eelnõu läks valitsusse",
        occurred_on=_days_ago(2),
    ).record

    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    page, _ = matter_timeline(matter=normal_matter, user=specialist)
    ours = next(item for item in page if item.record == development)
    theirs = next(item for item in page if item.record == other)

    labels = {file.label for file in ours.files}
    assert labels == {FIRST_FILE, LATER_FILE}
    assert not theirs.files
    # And it is not also a chronology line of its own: a file is not an event.
    assert sum(1 for item in page if item.record == development) == 1


# ---------------------------------------------------------------------------
# §D. what the act does not touch
# ---------------------------------------------------------------------------


def test_d_the_sentence_the_date_and_the_note_are_unchanged(development, specialist):
    """Requirement 4. This is not a correction, and does not act like one."""
    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    development.refresh_from_db()
    assert development.title == HEADLINE
    assert development.occurred_on == _days_ago(3)
    assert development.note == NOTE
    # And no correction event was written, because nothing was corrected.
    assert not ChangeEvent.objects.filter(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_CORRECTED,
        object_id=development.pk,
    ).exists()


def test_e_the_stage_is_unchanged(normal_matter, specialist, stage):
    """Requirement 5. `Hetkeseis` belongs to the file, not to this row's files."""
    filed = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title=HEADLINE,
        occurred_on=_days_ago(3),
        stage=stage,
    ).record
    normal_matter.refresh_from_db()
    before = normal_matter.stage_id

    add_development_evidence(development=filed, author=specialist, uploads=[_pdf(LATER_FILE)])

    normal_matter.refresh_from_db()
    assert normal_matter.stage_id == before
    assert (
        not ChangeEvent.objects.filter(
            event_type=ChangeEventType.MATTER_STAGE_CHANGED, matter=normal_matter
        )
        .exclude(
            # the one the original save wrote
            operation_id__in=ChangeEvent.objects.filter(
                event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED, matter=normal_matter
            ).values_list("operation_id", flat=True)
        )
        .exists()
    )


def test_f_the_open_next_action_is_unchanged(normal_matter, development, specialist):
    """Requirement 6. Attaching a paper assigns nobody any work."""
    open_before = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)

    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    open_after = NextAction.objects.get(matter=normal_matter, status=ActionStatus.OPEN)
    assert open_after.pk == open_before.pk
    assert open_after.text == open_before.text
    assert open_after.target_date == open_before.target_date
    # Not superseded and not duplicated: still exactly one step on this file.
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_d_the_existing_evidence_is_not_versioned_over(development, specialist):
    """A revised draft is new bytes on a new document, never a new version here.

    The immutability rule stated as the thing that would break it: if this act
    ever superseded the file already on the step, the first document would grow a
    second version. It does not.
    """
    first = _links(development)[0].document

    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    assert DocumentVersion.objects.filter(document=first).count() == 1
    assert DocumentVersion.objects.get(document=first).original_filename == FIRST_FILE


# ---------------------------------------------------------------------------
# §G. a refusal leaves nothing behind
# ---------------------------------------------------------------------------


def test_g_an_invalid_file_writes_nothing(development, specialist):
    """Requirements 7 and 8. All or none, and «none» means no orphan either."""
    documents_before = Document.objects.count()
    versions_before = DocumentVersion.objects.count()
    links_before = DocumentLink.objects.count()

    with pytest.raises((UploadRejected, DomainError)):
        add_development_evidence(
            development=development,
            author=specialist,
            uploads=[SimpleUploadedFile("pahalane.exe", b"MZ\x00\x00", content_type="text/plain")],
        )

    assert Document.objects.count() == documents_before
    assert DocumentVersion.objects.count() == versions_before
    assert DocumentLink.objects.count() == links_before
    assert _filenames(development) == {FIRST_FILE}


def test_g_the_second_file_being_refused_unwinds_the_first(development, specialist):
    """The ordering that makes «all or none» true rather than probable.

    A valid file and then a refused one in the same save: the valid one must not
    survive, because a step claiming evidence and holding half of it is the state
    the single transaction exists to make unreachable.
    """
    documents_before = Document.objects.count()

    with pytest.raises((UploadRejected, DomainError)):
        add_development_evidence(
            development=development,
            author=specialist,
            uploads=[
                _pdf("korras.pdf"),
                SimpleUploadedFile("pahalane.exe", b"MZ\x00\x00", content_type="text/plain"),
            ],
        )

    assert Document.objects.count() == documents_before
    assert _filenames(development) == {FIRST_FILE}


def test_g_an_empty_picker_is_refused_by_the_form(signed_in, normal_matter, development):
    """A save that chose nothing is a person who meant to choose something.

    Refused with the field's own sentence rather than written as a no-op that
    answered 200 — which would be the page telling them it worked.
    """
    response = signed_in.post(_url(normal_matter, development), {})

    assert response.status_code == 400
    assert "Vali vähemalt üks fail." in response.content.decode()
    assert _filenames(development) == {FIRST_FILE}


# ---------------------------------------------------------------------------
# §H. who may do it, and what a stranger is told
# ---------------------------------------------------------------------------


def test_h_a_restricted_development_cannot_be_reached_by_guessing_its_url(
    client, normal_matter, specialist, reader
):
    """Requirement 9. The same answer to the GET and to the POST, and no leak.

    A `Menetluse areng` may carry a stricter visibility override than its Matter,
    and this route resolves it through the child's **own** `visible_to` scope: a
    restricted row inside a Teema somebody may open is indistinguishable here from
    a row that does not exist, to both verbs.

    `reader` is the persona this repository uses for «genuinely not authorized to
    this», because since docs/adr/0042 a second lawyer is not an outsider — every
    role in `ROLES_WITH_RESTRICTED_ACCESS` sees restricted children by design. The
    same persona and the same acceptance the QA-06 module uses for the correction
    route next door, so the two surfaces cannot drift apart on what a stranger is
    told.
    """
    hidden = add_procedural_development(
        matter=normal_matter, author=specialist, title="Salajane samm", occurred_on=_days_ago(2)
    ).record
    hidden.visibility_override = Visibility.RESTRICTED
    hidden.save(update_fields=["visibility_override"])

    client.force_login(reader)
    url = _url(normal_matter, hidden)

    # Not on the page at all, so there is nothing to press.
    detail = client.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert "Salajane samm" not in detail
    assert f"menetluse-areng-{hidden.pk}-toend" not in detail

    assert client.get(url).status_code in (403, 404)
    assert client.post(url, {"attachments": _pdf(LATER_FILE)}).status_code in (403, 404)

    invented = reverse(
        "matters:add_development_evidence",
        kwargs={"pk": normal_matter.pk, "development_id": _uuid.uuid4()},
    )
    # The same answer for a row that is there and hidden as for one that is not.
    assert client.get(invented).status_code in (403, 404)
    assert not _links(hidden)


def test_h_a_reader_may_not_add_evidence(client, reader, normal_matter, development):
    """`business_write_required`, and nothing narrower: a reader gets its 404."""
    client.force_login(reader)

    assert (
        client.post(_url(normal_matter, development), {"attachments": _pdf(LATER_FILE)}).status_code
        == 404
    )
    assert _filenames(development) == {FIRST_FILE}


def test_h_a_development_on_another_teema_is_not_reachable_through_this_one(
    signed_in, normal_matter, specialist
):
    """The Matter in the path is a check, not decoration."""
    elsewhere = factories.MatterFactory(owner=specialist)
    theirs = add_procedural_development(
        matter=elsewhere, author=specialist, title="Teise teema samm", occurred_on=_days_ago(2)
    ).record

    response = signed_in.post(
        reverse(
            "matters:add_development_evidence",
            kwargs={"pk": normal_matter.pk, "development_id": theirs.pk},
        ),
        {"attachments": _pdf(LATER_FILE)},
    )

    assert response.status_code == 404
    assert not _links(theirs)


# ---------------------------------------------------------------------------
# §J. a closed Matter
# ---------------------------------------------------------------------------


def test_j_a_closed_matter_refuses_the_addition(normal_matter, development, specialist):
    """Requirement 10. The established business-write rule, under the row lock.

    Refused by the service and not by whether the page drew a button: the browser
    that posts may be holding a page from before somebody else closed the file.
    Reopening is the way out, and it leaves somebody's name on both decisions.
    """
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    with pytest.raises(DomainError):
        add_development_evidence(
            development=development, author=specialist, uploads=[_pdf(LATER_FILE)]
        )

    assert _filenames(development) == {FIRST_FILE}


def test_j_a_closed_matter_answers_the_post_without_writing(
    signed_in, normal_matter, development, specialist
):
    """And through the route, where a stale tab actually arrives."""
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    response = signed_in.post(_url(normal_matter, development), {"attachments": _pdf(LATER_FILE)})

    assert response.status_code == 400
    assert _filenames(development) == {FIRST_FILE}
    assert Document.objects.filter(matter=normal_matter).count() == 1


# ---------------------------------------------------------------------------
# §K. visibility
# ---------------------------------------------------------------------------


def test_k_evidence_added_later_is_no_more_visible_than_the_step_it_supports(
    normal_matter, specialist, reader
):
    """Requirement 11, stated as the comparison that makes it meaningful.

    A restricted development's later file must be exactly as reachable as the
    file that arrived with it — no more. Both are asserted on one record so that
    a change to the evidence pipeline cannot quietly make the second kind looser
    than the first.
    """
    hidden = add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Salajane samm",
        occurred_on=_days_ago(2),
        uploads=[_pdf(FIRST_FILE)],
    ).record
    hidden.visibility_override = Visibility.RESTRICTED
    hidden.save(update_fields=["visibility_override"])

    add_development_evidence(development=hidden, author=specialist, uploads=[_pdf(LATER_FILE)])

    # The links are invisible to a reader who may not see the record they name —
    # both of them, and for the same reason.
    visible = DocumentLink.objects.visible_to(reader).filter(procedural_development=hidden)
    assert not visible.exists()
    assert (
        DocumentLink.objects.visible_to(specialist).filter(procedural_development=hidden).count()
        == 2
    )

    # And the row carrying them is not on that reader's chronology at all, so
    # neither file draws a line of its own there.
    page, _ = matter_timeline(matter=normal_matter, user=reader)
    assert all(item.record != hidden for item in page)


def test_k_the_link_is_visible_to_somebody_who_may_see_the_step(
    normal_matter, development, specialist, other_specialist
):
    """The other half, so the test above cannot pass by hiding everything."""
    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    assert (
        DocumentLink.objects.visible_to(other_specialist)
        .filter(procedural_development=development)
        .count()
        == 2
    )


# ---------------------------------------------------------------------------
# §L. the audit trail
# ---------------------------------------------------------------------------


def test_l_the_link_is_recorded_as_its_own_event(development, specialist):
    """`DOCUMENT_CREATED` says bytes arrived; this says which step they evidence."""
    add_development_evidence(development=development, author=specialist, uploads=[_pdf(LATER_FILE)])

    event = (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED,
            object_id=development.pk,
        )
        .order_by("-created_at")
        .first()
    )
    assert event is not None
    assert event.actor_id == specialist.pk
    assert event.summary == LATER_FILE


# ---------------------------------------------------------------------------
# §M. repetition, and the token this act does not carry
# ---------------------------------------------------------------------------


def test_m_evidence_can_be_added_again_and_again(development, specialist):
    """Requirement 13. A proceeding produces paper for months."""
    for name in ("teine.pdf", "kolmas.pdf", "neljas.pdf"):
        add_development_evidence(development=development, author=specialist, uploads=[_pdf(name)])

    assert _filenames(development) == {FIRST_FILE, "teine.pdf", "kolmas.pdf", "neljas.pdf"}
    assert len(_links(development)) == 4


def test_m_two_people_adding_two_papers_do_not_refuse_each_other(
    development, specialist, other_specialist
):
    """Why there is no revision token, stated as the behaviour that would break.

    Both files are wanted. A concurrency scheme borrowed from the correction
    would refuse the second lawyer to protect a sentence neither of them touched.
    """
    add_development_evidence(development=development, author=specialist, uploads=[_pdf("a.pdf")])
    add_development_evidence(
        development=development, author=other_specialist, uploads=[_pdf("b.pdf")]
    )

    assert _filenames(development) == {FIRST_FILE, "a.pdf", "b.pdf"}


# ---------------------------------------------------------------------------
# §N. what does not exist
# ---------------------------------------------------------------------------


def test_n_there_is_no_route_that_removes_evidence(normal_matter, development):
    """Requirement 14. Evidence is additive and immutable, and stays that way."""
    for name in (
        "remove_development_evidence",
        "delete_development_evidence",
        "development_evidence_delete",
        "detach_development_evidence",
    ):
        with pytest.raises(NoReverseMatch):
            reverse(
                f"matters:{name}",
                kwargs={"pk": normal_matter.pk, "development_id": development.pk},
            )


def test_n_the_workspace_exposes_no_remover(development):
    """And not behind the route either: the module has no such callable."""
    from app.matters import workspace

    assert not [
        name
        for name in dir(workspace)
        if ("evidence" in name or "document" in name)
        and any(word in name for word in ("remove", "delete", "detach", "unlink"))
    ]


# ---------------------------------------------------------------------------
# §P. the route, end to end
# ---------------------------------------------------------------------------


def test_p_the_row_offers_the_action_and_the_get_opens_the_picker(
    signed_in, normal_matter, development
):
    """The surface: `+ Lisa tõend` beside `Muuda`, opening in the same region."""
    detail = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()
    assert f'id="menetluse-areng-{development.pk}-toend"' in detail
    assert _url(normal_matter, development) in detail

    opened = signed_in.get(_url(normal_matter, development))
    assert opened.status_code == 200
    body = opened.content.decode()
    assert f'id="id_menetluse_areng_{development.pk}_toend_failid"' in body
    # The picker and nothing about the record: no title box, no date, no note.
    assert 'name="title"' not in body
    assert 'name="occurred_on"' not in body
    assert 'name="note"' not in body


def test_p_a_posted_file_comes_back_as_the_whole_column_with_the_file_on_it(
    signed_in, normal_matter, development
):
    """The answer is the column, because the file is rendered around the row.

    Coming back with the row alone would answer a successful upload with a row
    that looks exactly as it did before the file arrived.
    """
    response = signed_in.post(_url(normal_matter, development), {"attachments": _pdf(LATER_FILE)})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="teema-vaade"' in body
    assert LATER_FILE in body
    assert FIRST_FILE in body
    assert _filenames(development) == {FIRST_FILE, LATER_FILE}


def test_p_cancel_re_reads_the_row(signed_in, normal_matter, development):
    """`Tühista` is a re-read of what the record says, not a client-side hide."""
    response = signed_in.get(_url(normal_matter, development) + "?vaade=lugemine")

    assert response.status_code == 200
    body = response.content.decode()
    assert f'id="menetluse-areng-{development.pk}-sisu"' in body
    assert "id_menetluse_areng_" not in body
    assert HEADLINE in body


def test_p_a_closed_matter_offers_no_action(signed_in, normal_matter, development, specialist):
    """No button on a finished file — and the service refuses anyway (§J)."""
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    detail = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk})
    ).content.decode()

    assert f'id="menetluse-areng-{development.pk}-toend"' not in detail


def test_p_the_correction_form_still_has_no_upload_control(signed_in, normal_matter, development):
    """The two acts stay apart, asserted from the surface a lawyer actually sees.

    This is the claim the whole module rests on: adding this route must not have
    turned `Muuda` into a place where bytes can arrive.
    """
    body = signed_in.get(
        reverse(
            "matters:update_development",
            kwargs={"pk": normal_matter.pk, "development_id": development.pk},
        )
    ).content.decode()

    assert 'type="file"' not in body
    assert "attachments" not in body


def test_p_only_one_of_the_two_forms_is_ever_open(signed_in, normal_matter, development):
    """They replace the same region, so a row cannot correct and capture at once."""
    picker = signed_in.get(_url(normal_matter, development)).content.decode()
    assert 'name="title"' not in picker

    editor = signed_in.get(
        reverse(
            "matters:update_development",
            kwargs={"pk": normal_matter.pk, "development_id": development.pk},
        )
    ).content.decode()
    assert 'name="title"' in editor
    assert "toend_failid" not in editor


def test_p_a_get_may_not_be_used_to_write(signed_in, normal_matter, development):
    """The opener opens. Nothing is captured until something is posted."""
    signed_in.get(_url(normal_matter, development))

    assert _filenames(development) == {FIRST_FILE}
    assert MatterProceduralDevelopment.objects.get(pk=development.pk).title == HEADLINE
