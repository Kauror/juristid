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

`Matter.position_summary` is deliberately still stored, still edited on the
Arvamused surface and still indexed for search. Retiring a rail block is not a
reason to drop a column, and nothing here asserts that it is gone.
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
        body = _body(signed_in.get(reverse(name, kwargs={"pk": matter.pk})))
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
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert 'id="koja-arvamus"' in body
    assert "Koja arvamus" in body
    assert "Arvamust ei ole lisatud." in body
    assert "+ Lisa arvamus" in body


def test_an_uploaded_opinion_appears_in_the_rail(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_document(matter)

    body = _detail(signed_in, matter)

    assert "Koja_arvamus.pdf" in body
    assert "Arvamust ei ole lisatud." not in body
    document.refresh_from_db()
    download = reverse("documents:download", kwargs={"pk": document.current_version.pk})
    assert download in body


def test_the_upload_posts_to_the_one_evidence_route(signed_in, specialist):
    """No second file-storage path. The rail uses the route that already exists,
    with the role fixed rather than offered."""
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}) in body
    assert 'name="role" value="KODA_SUBMISSION_FINAL"' in body


def test_uploading_from_the_rail_stores_one_document_and_returns_to_the_teema(
    signed_in, specialist
):
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
    assert "+ Lisa arvamus" not in body
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
    """One list, not two. `select_final_evidence` writes a
    KODA_SUBMISSION_FINAL document, so an opinion bound to a SENT Submission is
    already in the rail's population and is not stored a second time."""
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


def test_the_rail_keeps_the_way_to_the_formal_arvamused_surface(signed_in, specialist):
    """The retired block carried the only link to it from a Matter."""
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert reverse("matters:matter_position", kwargs={"pk": matter.pk}) in body


# ---------------------------------------------------------------------------
# The rest of the rail is untouched
# ---------------------------------------------------------------------------


def test_the_other_rail_blocks_are_still_there(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    body = _detail(signed_in, matter)

    assert "Sildid" in body
    assert "Märkmed" in body
    assert "Teema andmed" in body
    assert "Teemaviide" in body
