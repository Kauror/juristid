"""The facts rail's structure, and the surface that replaced `Koja seisukoht`.

Two defects and one product decision, all reported from hands-on use of the
Teema page:

* `Teema andmed` was far taller than the facts in it. The cause was not spacing.
  Every editable fact was written as ``<p class="railcard__row">`` containing a
  ``<details>``, and the HTML parser closes an open ``p`` at a ``<details>``
  start tag wherever it appears — so one fact became a label-only paragraph, a
  sibling disclosure and an empty paragraph: three flex children and three gaps,
  with the value on a line below its own label. The geometry of that is a
  browser test (`e2e/test_teema_rail.py`); that it cannot come back is this one.

* `+ Lisa` did nothing a person could see. The disclosure opened correctly and
  the editor appeared at the document's top-left corner, one viewport height
  down — `.inlineedit__form` is absolutely positioned and the rail had no
  positioned ancestor for it. Also a browser test, for the same reason.

* There is no separate free-text `Koja seisukoht` in this product. What the
  Chamber produced on a Matter is the opinion it sent, and that is a file.

`Matter.position_summary` is deliberately still stored, still written by
`matters:update_position` and still indexed for search. Retiring a rail block is
not a reason to drop a column, and nothing here asserts that it is gone.

Since docs/adr/0060 the rail is **read-only**: filenames and nothing else. The
upload it used to carry is on Dokumendid, where the Matter's files are, and the
`Arvamused →` link went with the per-Matter page it pointed at.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.matters.services import set_position
from app.submissions.enums import SubmissionStatus
from tests import factories

pytestmark = pytest.mark.django_db


def _body(response) -> str:
    return response.content.decode()


def _detail(client, matter) -> str:
    return _body(client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})))


def _upload(name: str = "Koja_arvamus.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def _rail_block(body: str, block_id: str) -> str:
    """One rail card's markup, by id, up to the start of the next one."""
    start = body.index(f'id="{block_id}"')
    rest = body[start:]
    end = rest.find('class="railcard"', 1)
    return rest if end == -1 else rest[:end]


def _opinion_document(matter, *, name="Koja_arvamus.pdf", visibility=None):
    """One `Koja arvamus` on a Matter, through the services that own it."""
    from app.documents.services import add_evidence_version, create_document

    document = create_document(
        matter=matter,
        title=name,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=matter.owner,
    )
    if visibility is not None:
        document.visibility_override = visibility
        document.save(update_fields=["visibility_override"])
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4 test",
        original_filename=name,
        mime_type="application/pdf",
        uploaded_by=matter.owner,
    )
    return document


# ---------------------------------------------------------------------------
# The row is a <div>, because a <p> cannot hold the editor
# ---------------------------------------------------------------------------

#: Any `railcard__row` opened as a paragraph. The class is what the stylesheet
#: lays out as a two-cell fact; the tag is what decides whether the cells stay
#: inside it.
PARAGRAPH_ROW = re.compile(r"<p[^>]*class=\"[^\"]*railcard__row")


def test_no_facts_row_is_a_paragraph(signed_in, specialist):
    """The defect, in the one place it can be caught before a browser sees it.

    ``<p>`` is not a container the parser will keep open across a ``<details>``:
    the *tree builder* closes it, so nothing in the template, in Django, or in
    a response-body assertion looks wrong. Only the DOM does.

    A `<div>` has no such rule. Asserting the tag rather than the geometry means
    a future editable fact written as a paragraph fails here, in a test that
    needs neither a browser nor a screenshot.
    """
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert "railcard__row" in body, "the facts rail did not render at all"
    assert not PARAGRAPH_ROW.search(body), (
        "a railcard__row is a <p>: the parser will close it at the first "
        "<details> and split the fact into three boxes"
    )


def test_every_editable_fact_keeps_its_editor_inside_its_row(signed_in, specialist):
    """Label and value stay in one row, with the disclosure between them.

    The template is what the browser parses, so this reads the markup the way
    the tree builder would: from each row's opening tag to its close, the
    `railcard__key` and the `inlineedit` disclosure have to be in the same span
    of text. When the row was a paragraph they were siblings.
    """
    matter = factories.MatterFactory(owner=specialist)
    body = _detail(signed_in, matter)

    rows = re.findall(r"<div[^>]*class=\"railcard__row\"[^>]*>(.*?)</div>\s*(?=<)", body, re.S)
    editable = [row for row in rows if "inlineedit" in row]

    assert editable, "no editable fact rendered for a writer"
    for row in editable:
        assert "railcard__key" in row
        assert "railcard__editable" in row


# ---------------------------------------------------------------------------
# `Koja seisukoht` is retired
# ---------------------------------------------------------------------------


def test_the_rail_no_longer_carries_a_koja_seisukoht_block(signed_in, specialist):
    """Removed, not renamed, and not replaced by another text field."""
    matter = factories.MatterFactory(owner=specialist)
    set_position(
        matter=matter,
        position_summary="Koda ei toeta pakendiaktsiisi tõusu.",
        actor=specialist,
    )

    body = _detail(signed_in, matter)

    assert 'id="koja-seisukoht"' not in body
    assert "railposition" not in body
    assert "Seisukohta ei ole" not in body
    assert "Lisa seisukoht" not in body
    # And no editor took its place in the rail.
    assert "id_position_summary" not in body


def test_the_retired_block_is_gone_from_every_surface_the_rail_reaches(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_position(matter=matter, position_summary="Koda toetab.", actor=specialist)

    for name in ("matters:matter_detail", "matters:matter_position", "matters:matter_documents"):
        # Followed: `matter_position` is a compatibility redirect since the
        # per-Matter Arvamused page was retired (docs/adr/0060).
        body = _body(signed_in.get(reverse(name, kwargs={"pk": matter.pk}), follow=True))
        assert "railposition" not in body, name
        assert 'id="koja-seisukoht"' not in body, name


def test_the_position_column_is_untouched(signed_in, specialist):
    """The surface was retired; the data was not.

    `position_summary` is still stored, still writable through its own service
    and still fed to the search index. This PR retires a block in a 300px
    column, which is not a reason to drop a column somebody's history is in.
    """
    matter = factories.MatterFactory(owner=specialist)
    set_position(matter=matter, position_summary="Koda toetab.", actor=specialist)

    matter.refresh_from_db()
    assert matter.position_summary == "Koda toetab."


# ---------------------------------------------------------------------------
# `Koja arvamus` — the Chamber's opinion, as a file
# ---------------------------------------------------------------------------


def test_the_rail_offers_koja_arvamus_and_says_when_there_is_none(signed_in, specialist):
    """A quiet empty state, and nothing asking for work that has not come up."""
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert 'id="koja-arvamus"' in body
    assert "Koja arvamus" in body
    assert "Arvamust ei ole lisatud." in body
    assert "+ Lisa arvamus" not in body


def test_the_rail_is_read_only(signed_in, specialist):
    """Quick reference, not a workspace (docs/adr/0060 §6).

    Uploading an opinion is on Dokumendid, where the Matter's files and their
    upload panel already are; a form in 300px beside a file list that has one is
    the same control twice. And `Arvamused →` led to a page that showed this
    same file a third time — it is retired, and nothing replaces it here.
    """
    matter = factories.MatterFactory(owner=specialist)
    _opinion_document(matter)

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")

    assert "Koja_arvamus.pdf" in block
    assert "+ Lisa arvamus" not in block
    assert "Arvamused" not in block
    assert reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}) not in block


def test_the_rail_states_no_evidence_mechanics(signed_in, specialist):
    """No version, no size, no checksum and no badge (docs/adr/0060 §6, §28).

    Every one of those is a fact about the evidence rather than about the
    opinion, they are all stated where evidence is stated, and none of them
    helps somebody who came here to open the letter.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_document(matter)
    document.refresh_from_db()
    version = document.current_version

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")

    assert version.sha256[:16] not in block
    assert "Lõplik" not in block
    assert "Tõend" not in block
    # The old rendering was `v1 · 13 bytes` under a `★` badge. Asserted with the
    # separator so the guard cannot be satisfied by an unrelated `v1` elsewhere.
    assert f"v{version.version_number} ·" not in block
    assert "badge--evidence" not in block
    assert "★" not in block


def test_several_opinions_all_appear_in_the_rail(signed_in, specialist):
    """A Matter may hold an initial opinion, a supplement and a joint letter.

    There is no `Matter.final_opinion` and there is not going to be one: a
    single-valued shortcut could only ever name one of the three
    (docs/adr/0060 §10).
    """
    matter = factories.MatterFactory(owner=specialist)
    for name in ("Esimene.pdf", "Taiendav.pdf", "Uhispoordumine.pdf"):
        _opinion_document(matter, name=name)

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")

    for name in ("Esimene.pdf", "Taiendav.pdf", "Uhispoordumine.pdf"):
        assert block.count(name) == 1, name
    assert "Arvamust ei ole lisatud." not in block


def test_an_uploaded_opinion_appears_in_the_rail(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_document(matter)

    body = _detail(signed_in, matter)

    assert "Koja_arvamus.pdf" in body
    assert "Arvamust ei ole lisatud." not in body
    document.refresh_from_db()
    download = reverse("documents:download", kwargs={"pk": document.current_version.pk})
    assert download in body


def test_uploading_an_opinion_stores_one_document_and_asserts_no_send(signed_in, specialist):
    """The `tagasi=teema` path still works; nothing in the rail posts to it now.

    Kept because the route's closed return vocabulary is what stops it becoming
    an open redirect, and because the invariant under it is the one this whole
    change rests on: a file uploaded as `Arvamus` records that Koda holds it and
    never that Koda sent it (docs/adr/0060 §18).
    """
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _upload(), "role": DocumentRole.KODA_SUBMISSION_FINAL, "tagasi": "teema"},
    )

    assert response.status_code == 302
    assert response.url == reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    documents = Document.objects.filter(matter=matter, role=DocumentRole.KODA_SUBMISSION_FINAL)
    assert documents.count() == 1
    document = documents.get()
    assert document.current_version is not None
    assert document.current_version.original_filename == "Koja_arvamus.pdf"
    # A file on the record is not a claim that anything was formally sent.
    assert not matter.submissions.exists()


def test_an_upload_without_the_marker_still_lands_on_dokumendid(signed_in, specialist):
    """The Dokumendid panel's own behaviour is unchanged."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _upload(), "role": DocumentRole.INCOMING_AUTHORITY},
    )

    assert response.status_code == 302
    assert response.url == reverse("matters:matter_documents", kwargs={"pk": matter.pk})


def test_the_return_target_is_a_word_and_never_a_url(signed_in, specialist):
    """`tagasi` is a closed vocabulary, so it cannot become an open redirect."""
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {
            "upload": _upload(),
            "role": DocumentRole.KODA_SUBMISSION_FINAL,
            "tagasi": "https://example.invalid/",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("matters:matter_documents", kwargs={"pk": matter.pk})


def test_a_reader_sees_the_opinion_but_is_offered_no_upload(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _opinion_document(matter)
    client.force_login(reader)

    body = _detail(client, matter)

    assert "Koja arvamus" in body
    assert "Koja_arvamus.pdf" in body
    assert reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}) not in body


def test_a_reader_may_not_upload_one_by_posting(client, reader, specialist):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = client.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _upload(), "role": DocumentRole.KODA_SUBMISSION_FINAL, "tagasi": "teema"},
    )

    assert response.status_code == 404
    assert not Document.objects.filter(matter=matter).exists()


def test_an_opinion_restricted_below_its_matter_is_not_named_in_the_rail(
    client, reader, specialist
):
    """AUTH-003 §21: a filename is a disclosure whether or not the bytes are.

    The retired block enforced this by intersecting a Submission's visibility
    with its evidence's. The list is `Document`s now, so `visible_to` is the
    whole rule — but the rule itself has not moved.
    """
    matter = factories.MatterFactory(owner=specialist)
    _opinion_document(matter, name="Salajane_arvamus.pdf", visibility=Visibility.RESTRICTED)
    client.force_login(reader)

    body = _detail(client, matter)

    assert "Salajane_arvamus.pdf" not in body
    assert "Arvamust ei ole lisatud." in body


def test_a_sent_submissions_final_evidence_is_the_same_row(signed_in, specialist, organisation):
    """One list, not two, when the document carries the role already.

    This is the easy half and it always worked: `_opinion_document` creates the
    file as a `KODA_SUBMISSION_FINAL`, so it is in the rail through the role
    branch and binding it to a Submission does not store it twice. The half that
    did *not* work — the same flow on a document that was never classified as an
    opinion — is the group of tests below (UX-005)."""
    from app.documents.services import evidence_storage  # noqa: F401  (settings guard)
    from app.submissions.services import (
        create_submission,
        mark_submission_sent,
        select_final_evidence,
    )

    matter = factories.MatterFactory(owner=specialist)
    submission = create_submission(
        matter=matter, title=matter.title, actor=specialist, recipients=[organisation]
    )
    document = _opinion_document(matter, name="Saadetud_arvamus.pdf")
    select_final_evidence(submission=submission, version=document.current_version, actor=specialist)
    mark_submission_sent(submission=submission, actor=specialist)

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.SENT

    # Counted inside the block, not across the page: the send is also an event
    # on the chronology, which is a different fact about the same file.
    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")
    assert block.count("Saadetud_arvamus.pdf") == 1


def test_no_matter_surface_links_to_the_retired_arvamused_page(signed_in, specialist):
    """It is a compatibility redirect, not a destination (docs/adr/0060 §19).

    Nothing in the product may send a reader through a hop they do not need. The
    route stays so old bookmarks still work; the navigation to it is gone.
    """
    matter = factories.MatterFactory(owner=specialist)
    retired = reverse("matters:matter_position", kwargs={"pk": matter.pk})

    for name in ("matters:matter_detail", "matters:matter_documents"):
        body = _body(signed_in.get(reverse(name, kwargs={"pk": matter.pk})))
        assert f'href="{retired}"' not in body, name


# ---------------------------------------------------------------------------
# The rest of the rail is untouched
# ---------------------------------------------------------------------------


def test_the_other_rail_blocks_are_still_there(signed_in, specialist):
    """What this round retired is `Koja seisukoht`, and nothing else here.

    `Sildid` is no longer among them. It was retired separately, by the
    simplified next-action round, as a UI retirement with `Tag` and
    `TagAssignment` untouched — so the assertion that used to sit here is now
    the opposite one, and it lives with the rest of that decision in
    `tests/test_simplified_next_action.py` (ADR 0052 §14).
    """
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert "Märkmed" in body
    assert "Teema andmed" in body
    assert "Teemaviide" in body
    assert "Sildid" not in body


# ---------------------------------------------------------------------------
# The rail may not deny an opinion the Matter has sent (UX-005)
# ---------------------------------------------------------------------------
#
# `Koja arvamus` used to list `DocumentRole.KODA_SUBMISSION_FINAL` alone, on the
# documented grounds that every path writes it. Two do not: `select_final_evidence`
# writes `final_version` and nothing else, and `attach_final_evidence` handed an
# existing document binds the evidence without touching the classification. So a
# Matter rendered `Koja arvamused 1 · Saadetud` in its main column and
# `Arvamust ei ole lisatud` in this rail, in one viewport, and offered to add a
# second copy of a letter it had already sent.
#
# The fix is in the read model: the rail asks which visible documents *represent*
# a Chamber opinion, which is the role **or** the evidence of a SENT Submission.
# `Document.role` still classifies a file and is never rewritten to compensate.


def _incoming_document(matter, *, name="Ministeeriumi_kiri.pdf"):
    """A file that arrived, classified as what it is.

    The realistic shape of the defect: a lawyer picks the document already on the
    Matter as the evidence for the opinion going out. Nothing about that act
    makes the arriving letter stop being an arriving letter.
    """
    from app.documents.services import add_evidence_version, create_document

    document = create_document(
        matter=matter,
        title=name,
        role=DocumentRole.INCOMING_AUTHORITY,
        created_by=matter.owner,
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4 test",
        original_filename=name,
        mime_type="application/pdf",
        uploaded_by=matter.owner,
    )
    return document


def _sent_on(matter, document, actor, organisation, *, send=True):
    from app.submissions.services import (
        create_submission,
        mark_submission_sent,
        select_final_evidence,
    )

    submission = create_submission(
        matter=matter, title=matter.title, actor=actor, recipients=[organisation]
    )
    select_final_evidence(submission=submission, version=document.current_version, actor=actor)
    if send:
        mark_submission_sent(submission=submission, actor=actor)
    submission.refresh_from_db()
    return submission


def test_a_sent_opinion_is_listed_even_though_its_document_kept_its_own_role(
    signed_in, specialist, organisation
):
    matter = factories.MatterFactory(owner=specialist)
    document = _incoming_document(matter)
    submission = _sent_on(matter, document, specialist, organisation)

    assert submission.status == SubmissionStatus.SENT
    # The point of the test: the role was not rewritten to make this pass.
    document.refresh_from_db()
    assert document.role == DocumentRole.INCOMING_AUTHORITY

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")
    assert block.count("Ministeeriumi_kiri.pdf") == 1


def test_the_rail_does_not_say_there_is_no_opinion_beside_one_it_sent(
    signed_in, specialist, organisation
):
    """The contradiction itself: both statements were on screen at once."""
    matter = factories.MatterFactory(owner=specialist)
    _sent_on(matter, _incoming_document(matter), specialist, organisation)

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")
    assert "Arvamust ei ole lisatud." not in block


def test_a_draft_submissions_evidence_is_not_promoted_to_an_opinion(
    signed_in, specialist, organisation
):
    """A file somebody is preparing is not a letter the Chamber has sent.

    The union is `SENT` and deliberately not "has a final_version": widening it
    to drafts would be this defect in the other direction.
    """
    matter = factories.MatterFactory(owner=specialist)
    submission = _sent_on(matter, _incoming_document(matter), specialist, organisation, send=False)

    assert submission.status == SubmissionStatus.DRAFT
    assert submission.final_version_id is not None

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")
    assert "Ministeeriumi_kiri.pdf" not in block
    assert "Arvamust ei ole lisatud." in block


def test_a_document_in_both_branches_is_listed_once(signed_in, specialist, organisation):
    """The ordinary closing flow satisfies the role *and* the Submission."""
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_document(matter, name="Koja_arvamus.pdf")
    _sent_on(matter, document, specialist, organisation)

    document.refresh_from_db()
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL

    block = _rail_block(_detail(signed_in, matter), "koja-arvamus")
    assert block.count("Koja_arvamus.pdf") == 1


def test_visibility_still_gates_the_filename_of_a_sent_opinion(
    client, reader, specialist, organisation
):
    """AUTH-003 §21 applies to the whole union, not just the role branch.

    Restricted after the send, so the domain check that guards *binding* is not
    what is being tested here — the question is only whether the widened read
    model still narrows by `visible_to`.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _incoming_document(matter, name="Salajane_saadetud.pdf")
    _sent_on(matter, document, specialist, organisation)

    document.visibility_override = Visibility.RESTRICTED
    document.save(update_fields=["visibility_override"])

    client.force_login(reader)
    block = _rail_block(_detail(client, matter), "koja-arvamus")

    assert "Salajane_saadetud.pdf" not in block
    assert "Arvamust ei ole lisatud." in block


def test_the_union_does_not_reach_across_matters(signed_in, specialist, organisation):
    """A second Matter's opinion is not this Matter's, whatever is bound where."""
    mine = factories.MatterFactory(owner=specialist)
    theirs = factories.MatterFactory(owner=specialist, title="Teine teema")
    _sent_on(
        theirs, _incoming_document(theirs, name="Teise_teema_kiri.pdf"), specialist, organisation
    )

    block = _rail_block(_detail(signed_in, mine), "koja-arvamus")

    assert "Teise_teema_kiri.pdf" not in block
    assert "Arvamust ei ole lisatud." in block
