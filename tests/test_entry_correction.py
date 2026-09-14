"""Correcting a Sissekanne that is already filed.

The product rule this module asserts:

    An entry that is already in the chronology may be corrected — its *text*,
    and nothing else — by anybody who may author business content, on an open
    Matter and on a closed one alike.

Closure means no **new** business work: no note, no next step, no consultation,
no upload, no opinion. It has never meant that a fact recorded wrongly in 2023
must stay wrong, and `tests/test_closed_matter_documents.py` deliberately left
that question open («whether a *historical* correction … should be possible on a
closed Matter … is a separate product question»). This is the answer: it is, and
the boundary beside it does not move — every test that proves the correction
works on a closed Matter also proves that adding something new to it still
refuses.

Four things follow from «correction, not new fact», and each has tests below.

**No second record.** No `Entry` is created, `occurred_at` is untouched, and the
line keeps its place in the chronology. An edit that appeared as «muudetud
today» would be a new historical event describing no new work.

**The previous wording survives, exactly.** `EntryRevision` is append-only in the
database, and the chain reads in order however many corrections there have been.

**A stale form cannot overwrite a fresh one.** The row lock protects the revision
chain; it does not tell a second editor their browser was holding an old copy.
The `revision` token does, and a save that carries a stale one writes nothing.

**A refusal leaves the page usable.** What the person typed stays in the box, the
stored entry is untouched, and no revision is written.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.entry_enums import EntryKind
from app.matters.models import Entry, EntryRevision
from app.matters.services import (
    ENTRY_EDIT_CONFLICT,
    EntryEditConflict,
    add_entry,
    close_matter,
    edit_entry,
    entry_revision_token,
)
from app.matters.timeline import matter_timeline
from app.search.services import result_count
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


# -- harness ----------------------------------------------------------------


def _url(matter, entry) -> str:
    return reverse("matters:edit_entry", kwargs={"pk": matter.pk, "entry_id": entry.pk})


def _open_form(client, matter, entry):
    """Click `Muuda`: the row's body region comes back as the edit form."""
    return client.get(_url(matter, entry))


def _revision_in(html: str) -> str:
    """The token the rendered form is holding.

    Read out of the markup rather than computed, because the whole point of the
    field is that the browser carries the server's value back unchanged — a test
    that recomputed it would prove the service works and say nothing about
    whether the form actually ships it.
    """
    marker = 'name="revision"'
    assert marker in html, "the edit form carries no revision token"
    field = html[html.index(marker) : html.index(marker) + 400]
    start = field.index('value="') + len('value="')
    return field[start : field.index('"', start)]


def _save(client, matter, subject, body: str):
    """Save a correction the way the page does: with the token the row is at.

    Re-read first, because several tests correct the same entry twice and the
    second form is filled from what the first one wrote. A test that hard-coded
    an old token would be exercising the conflict path while claiming to
    exercise the ordinary one.
    """
    subject.refresh_from_db()
    return client.post(
        _url(matter, subject),
        {"body": body, "revision": entry_revision_token(subject)},
    )


def _close(matter, actor):
    """Close it the way the other tab does — through the domain service."""
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=actor, reason="QA")
    matter.refresh_from_db()
    return matter


@pytest.fixture
def entry(normal_matter, specialist):
    return add_entry(
        matter=normal_matter,
        body="<p>Ministeerium lubas Alfaversiooni reedeks.</p>",
        author=specialist,
        kind=EntryKind.NOTE,
    )


# -- A. an open Matter ------------------------------------------------------


def test_the_chronology_offers_muuda_on_an_entry(signed_in, normal_matter, entry):
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = page.content.decode()

    assert page.status_code == 200
    assert f"sissekanne-{entry.pk}-sisu" in html
    assert _url(normal_matter, entry) in html
    assert ">Muuda<" in html


def test_the_form_opens_with_the_existing_body_in_it(signed_in, normal_matter, entry):
    response = _open_form(signed_in, normal_matter, entry)
    html = response.content.decode()

    assert response.status_code == 200
    assert "Ministeerium lubas Alfaversiooni reedeks." in html
    assert 'name="body"' in html
    assert _revision_in(html) == entry_revision_token(entry)
    # The ids are keyed on the entry, because a reader may open two rows at
    # once and two elements sharing an id make a label reach the wrong box.
    assert f"id_sissekanne_{entry.pk}_body" in html


def test_saving_changes_the_body_and_creates_no_second_entry(signed_in, normal_matter, entry):
    before = Entry.objects.filter(matter=normal_matter).count()

    response = _save(
        signed_in, normal_matter, entry, "<p>Ministeerium lubas Beetaversiooni esmaspäevaks.</p>"
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert "Beetaversiooni" in entry.body
    assert Entry.objects.filter(matter=normal_matter).count() == before
    # And the row comes back in its read state, not as a form still open.
    assert 'name="body"' not in response.content.decode()


def test_the_corrected_row_carries_the_muudetud_marker_out_of_band(signed_in, normal_matter, entry):
    response = signed_in.post(
        _url(normal_matter, entry),
        {"body": "<p>Parandatud sõnastus.</p>", "revision": entry_revision_token(entry)},
    )
    html = response.content.decode()

    assert response.status_code == 200
    # The marker lives in the meta line, which is not part of this response —
    # so it has to arrive addressed to its own id.
    assert f'id="sissekanne-{entry.pk}-muudetud"' in html
    assert 'hx-swap-oob="outerHTML"' in html
    assert "muudetud" in html


def test_tuhista_gives_back_the_stored_text_and_writes_nothing(signed_in, normal_matter, entry):
    response = signed_in.get(_url(normal_matter, entry), {"vaade": "lugemine"})
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="body"' not in html
    assert "Ministeerium lubas Alfaversiooni reedeks." in html
    entry.refresh_from_db()
    assert entry.edit_count == 0
    assert EntryRevision.objects.filter(entry=entry).count() == 0


def test_any_business_writer_may_correct_a_colleagues_entry(
    client, normal_matter, entry, other_specialist
):
    """Not owner-only, not author-only.

    A typo in a colleague's entry is the department's problem, and narrowing the
    correction to whoever typed it would make the product slower than the
    OneNote page it replaces (docs/adr/0042, `may_write_business_content`).
    """
    client.force_login(other_specialist)

    response = client.post(
        _url(normal_matter, entry),
        {"body": "<p>Kolleeg parandas kuupäeva.</p>", "revision": entry_revision_token(entry)},
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert "Kolleeg parandas kuupäeva." in entry.body


# -- B. a closed Matter -----------------------------------------------------


def test_a_closed_matters_entry_is_still_correctable(signed_in, normal_matter, entry, specialist):
    _close(normal_matter, specialist)

    opened = _open_form(signed_in, normal_matter, entry)
    assert opened.status_code == 200

    response = _save(
        signed_in, normal_matter, entry, "<p>Lõpetatud teema parandatud sissekanne.</p>"
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert "Lõpetatud teema parandatud sissekanne." in entry.body
    assert EntryRevision.objects.get(entry=entry).body.startswith(
        "<p>Ministeerium lubas Alfaversiooni"
    )


def test_correcting_a_closed_matter_does_not_reopen_it(signed_in, normal_matter, entry, specialist):
    _close(normal_matter, specialist)
    closed_at = normal_matter.closed_at
    disposition = normal_matter.disposition

    _save(signed_in, normal_matter, entry, "<p>Parandus.</p>")

    normal_matter.refresh_from_db()
    assert normal_matter.is_open is False
    assert normal_matter.closed_at == closed_at
    assert normal_matter.disposition == disposition
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.MATTER_REOPENED
    ).exists()


def test_new_business_work_on_a_closed_matter_still_refuses(
    signed_in, normal_matter, entry, specialist
):
    """The correction does not widen the boundary it sits beside.

    Fired at the same Matter, in the same test, right after a correction has
    succeeded on it — so a change that let `Muuda` through by relaxing the
    closed-Matter gate could not pass this.
    """
    _close(normal_matter, specialist)
    _save(signed_in, normal_matter, entry, "<p>Parandus.</p>")
    entries_after_correction = Entry.objects.filter(matter=normal_matter).count()

    refused = signed_in.post(
        reverse("matters:add_note", kwargs={"pk": normal_matter.pk}),
        {"body": "<p>Uus märge lõpetatud teemale.</p>"},
    )

    assert refused.status_code == 400
    assert Entry.objects.filter(matter=normal_matter).count() == entries_after_correction

    action = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        {"text": "Uus samm lõpetatud teemal", "target_date": "20.10.2026"},
        headers={"HX-Request": "true"},
    )
    assert action.status_code == 400


# -- C and D. who may not ---------------------------------------------------


def test_a_reader_sees_the_entry_but_no_muuda(client, reader, normal_matter, entry):
    client.force_login(reader)

    page = client.get(reverse("matters:matter_detail", kwargs={"pk": normal_matter.pk}))
    html = page.content.decode()

    assert page.status_code == 200
    assert "Ministeerium lubas Alfaversiooni reedeks." in html
    assert _url(normal_matter, entry) not in html
    assert ">Muuda<" not in html


@pytest.mark.parametrize("persona", ["reader", "administrator"])
def test_somebody_without_business_write_is_refused_at_the_route(
    client, request, persona, normal_matter, entry
):
    """404 and not 403, and the entry is untouched.

    A 403 answers «you could do this with another role», which is a description
    of the application handed to exactly the person who should not have it
    (`app.core.decorators.business_write_required`).
    """
    client.force_login(request.getfixturevalue(persona))

    assert client.get(_url(normal_matter, entry)).status_code == 404
    assert (
        client.post(
            _url(normal_matter, entry),
            {"body": "<p>Loata parandus.</p>", "revision": ""},
        ).status_code
        == 404
    )

    entry.refresh_from_db()
    assert "Alfaversiooni" in entry.body
    assert entry.edit_count == 0
    assert not EntryRevision.objects.filter(entry=entry).exists()


def test_an_anonymous_request_is_sent_to_sign_in_rather_than_refused(client, normal_matter, entry):
    response = client.get(_url(normal_matter, entry))
    assert response.status_code == 302
    assert "/konto/" in response["Location"]


# -- E. the hidden-object oracle --------------------------------------------


def test_an_entry_on_another_matter_is_not_reachable_through_this_one(
    signed_in, normal_matter, entry, specialist
):
    """The Matter in the path and the entry in it have to belong together."""
    other = factories.MatterFactory(owner=specialist)
    stranger = add_entry(matter=other, body="<p>Teise teema sissekanne.</p>", author=specialist)

    response = signed_in.get(
        reverse("matters:edit_entry", kwargs={"pk": normal_matter.pk, "entry_id": stranger.pk})
    )

    assert response.status_code == 404


def test_a_missing_entry_and_a_refused_one_are_indistinguishable(
    client, reader, normal_matter, entry
):
    """Nothing in the refusal says whether the entry exists.

    The same status and the same body for a real entry a reader may not correct
    and for an id that was never issued — which is the contract the rest of the
    product keeps for a hidden object (AUTH-003).
    """
    client.force_login(reader)
    missing = "00000000-0000-7000-8000-000000000000"

    real = client.get(_url(normal_matter, entry))
    absent = client.get(
        reverse("matters:edit_entry", kwargs={"pk": normal_matter.pk, "entry_id": missing})
    )

    assert real.status_code == absent.status_code == 404
    assert real.content == absent.content


def test_a_restricted_matter_is_not_reachable_by_somebody_outside_it(
    client, restricted_matter, specialist, administrator
):
    hidden = add_entry(matter=restricted_matter, body="<p>Tundlik.</p>", author=specialist)
    client.force_login(administrator)

    assert client.get(_url(restricted_matter, hidden)).status_code == 404


# -- F. the revision chain --------------------------------------------------


def test_two_corrections_preserve_both_earlier_wordings_in_order(
    signed_in, normal_matter, specialist
):
    subject = add_entry(matter=normal_matter, body="<p>Alpha</p>", author=specialist)

    _save(signed_in, normal_matter, subject, "<p>Beta</p>")
    subject.refresh_from_db()
    assert "Beta" in subject.body

    _save(signed_in, normal_matter, subject, "<p>Gamma</p>")
    subject.refresh_from_db()

    assert "Gamma" in subject.body
    assert subject.edit_count == 2
    revisions = list(EntryRevision.objects.filter(entry=subject).order_by("revision_number"))
    assert [revision.revision_number for revision in revisions] == [1, 2]
    assert "Alpha" in revisions[0].body
    assert "Beta" in revisions[1].body


def test_a_correction_records_who_made_it(signed_in, normal_matter, entry, specialist):
    _save(signed_in, normal_matter, entry, "<p>Parandus.</p>")
    assert EntryRevision.objects.get(entry=entry).edited_by == specialist


# -- G. audit ---------------------------------------------------------------


def test_a_correction_files_one_entry_edited_event_and_no_entry_added(
    signed_in, normal_matter, entry, specialist
):
    before = ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENTRY_ADDED
    ).count()

    _save(signed_in, normal_matter, entry, "<p>Parandus.</p>")

    edited = ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENTRY_EDITED
    )
    assert edited.count() == 1
    assert edited.get().actor == specialist
    assert edited.get().payload["revision"] == 1
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.ENTRY_ADDED
        ).count()
        == before
    )


# -- H. search freshness ----------------------------------------------------


def test_the_search_projection_follows_the_correction(signed_in, normal_matter, specialist):
    subject = add_entry(
        matter=normal_matter, body="<p>Pakendimaterjalikvoot</p>", author=specialist
    )
    assert result_count(query="Pakendimaterjalikvoot", user=specialist) == 1

    _save(signed_in, normal_matter, subject, "<p>Pakendiringlusmäär</p>")

    assert result_count(query="Pakendiringlusmäär", user=specialist) == 1
    # The superseded wording is no longer current entry content. It survives in
    # `EntryRevision`, which is edit history and deliberately not a search
    # surface: finding a Matter by words nobody can see on it any more would be
    # the index answering for a version of the record that no longer exists.
    assert result_count(query="Pakendimaterjalikvoot", user=specialist) == 0


# -- I. the chronology does not move ----------------------------------------


def test_the_corrected_entry_keeps_its_date_and_its_place(signed_in, normal_matter, specialist):
    older = add_entry(matter=normal_matter, body="<p>Vanem</p>", author=specialist)
    factories.EntryFactory(matter=normal_matter, author=specialist, body="<p>Uuem</p>")
    occurred_at = older.occurred_at

    rows, _ = matter_timeline(matter=normal_matter, user=specialist)
    before = [item.sort_key for item in rows]

    _save(signed_in, normal_matter, older, "<p>Vanem, parandatud</p>")

    older.refresh_from_db()
    after, _ = matter_timeline(matter=normal_matter, user=specialist)

    assert older.occurred_at == occurred_at
    assert [item.sort_key for item in after] == before


def test_a_correction_adds_no_row_to_the_chronology(signed_in, normal_matter, entry, specialist):
    before, _ = matter_timeline(matter=normal_matter, user=specialist)

    _save(signed_in, normal_matter, entry, "<p>Parandus.</p>")

    after, _ = matter_timeline(matter=normal_matter, user=specialist)
    assert len(after) == len(before)


# -- J. the stale form ------------------------------------------------------


def test_a_stale_form_does_not_overwrite_the_newer_text(
    client, normal_matter, entry, specialist, other_specialist
):
    """A opens the form, B saves, A submits. B wins and A is told so."""
    client.force_login(specialist)
    stale = _revision_in(_open_form(client, normal_matter, entry).content.decode())

    edit_entry(entry=entry, body="<p>B kirjutas uue sõnastuse.</p>", actor=other_specialist)

    response = client.post(
        _url(normal_matter, entry),
        {"body": "<p>A kirjutas vana koopia peale.</p>", "revision": stale},
    )
    html = response.content.decode()

    assert response.status_code == 409
    entry.refresh_from_db()
    assert "B kirjutas uue sõnastuse." in entry.body

    # A's words are still in the box, B's are beside it to read, and the token
    # was not advanced — so submitting again without reconciling conflicts again.
    assert "A kirjutas vana koopia peale." in html
    assert "B kirjutas uue sõnastuse." in html
    assert ENTRY_EDIT_CONFLICT in html
    assert _revision_in(html) == stale

    # One correction happened, not two, and the chain says whose.
    assert entry.edit_count == 1
    revisions = list(EntryRevision.objects.filter(entry=entry))
    assert len(revisions) == 1
    assert "Alfaversiooni" in revisions[0].body
    assert revisions[0].edited_by == other_specialist


def test_a_stale_form_carrying_the_same_words_is_still_a_conflict(
    normal_matter, entry, specialist, other_specialist
):
    """Being overtaken is the fact, not disagreeing about the text.

    Answering this with a silent success would teach the person that their copy
    was current when it was two writes behind.
    """
    stale = entry_revision_token(entry)
    edit_entry(entry=entry, body="<p>Sama uus sõnastus.</p>", actor=other_specialist)

    with pytest.raises(EntryEditConflict):
        edit_entry(
            entry=entry,
            body="<p>Sama uus sõnastus.</p>",
            actor=specialist,
            expected_revision=stale,
        )


def test_a_post_carrying_no_token_at_all_is_refused(signed_in, normal_matter, entry):
    """An absent token is a stale form, not permission to skip the check.

    Every rendered form ships one, so a POST without it is hand-made — and the
    one thing it must not be able to do is overwrite a version it has never
    read. The empty string is compared like any other value and loses.
    """
    response = signed_in.post(_url(normal_matter, entry), {"body": "<p>Ilma märgita.</p>"})

    assert response.status_code == 409
    entry.refresh_from_db()
    assert "Alfaversiooni" in entry.body
    assert entry.edit_count == 0


def test_a_fresh_form_saves_after_reading_the_conflict(
    signed_in, normal_matter, entry, other_specialist
):
    """The way out of a conflict is to re-read and save again."""
    edit_entry(entry=entry, body="<p>B kirjutas.</p>", actor=other_specialist)
    entry.refresh_from_db()

    response = signed_in.post(
        _url(normal_matter, entry),
        {"body": "<p>Kokku lepitud sõnastus.</p>", "revision": entry_revision_token(entry)},
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert "Kokku lepitud sõnastus." in entry.body


def test_the_service_leaves_a_caller_with_no_opinion_alone(normal_matter, entry, specialist):
    """`expected_revision=None` is «no opinion», not an unchecked door.

    It is for the caller with no earlier version to be holding — a data fix, a
    migration, a test. Every browser path supplies a token.
    """
    edit_entry(entry=entry, body="<p>Skripti parandus.</p>", actor=specialist)
    entry.refresh_from_db()
    assert "Skripti parandus." in entry.body


# -- K. a refused save ------------------------------------------------------


def test_an_empty_body_keeps_the_stored_text_and_the_form_open(signed_in, normal_matter, entry):
    response = signed_in.post(
        _url(normal_matter, entry),
        {"body": "<p><br></p>", "revision": entry_revision_token(entry)},
    )
    html = response.content.decode()

    assert response.status_code == 400
    assert 'name="body"' in html
    assert "Sissekanne vajab sisu." in html

    entry.refresh_from_db()
    assert "Alfaversiooni" in entry.body
    assert entry.edit_count == 0
    assert not EntryRevision.objects.filter(entry=entry).exists()
    assert not ChangeEvent.objects.filter(
        matter=normal_matter, event_type=ChangeEventType.ENTRY_EDITED
    ).exists()


def test_saving_the_same_text_writes_no_revision(signed_in, normal_matter, entry):
    response = signed_in.post(
        _url(normal_matter, entry),
        {
            "body": entry.body,
            "revision": entry_revision_token(entry),
        },
    )

    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.edit_count == 0
    assert not EntryRevision.objects.filter(entry=entry).exists()


def test_a_correction_cannot_smuggle_markup_past_the_sanitiser(signed_in, normal_matter, entry):
    _save(signed_in, normal_matter, entry, '<p>Ohutu</p><script>alert("x")</script>')

    entry.refresh_from_db()
    assert "script" not in entry.body.lower()
    assert "Ohutu" in entry.body


def test_a_correction_changes_only_the_body(signed_in, normal_matter, entry):
    """Author, kind, `Toimus` and visibility are not fields on this form."""
    before = (entry.author_id, entry.kind, entry.occurred_at, entry.visibility_override)

    signed_in.post(
        _url(normal_matter, entry),
        {
            "body": "<p>Parandus.</p>",
            "revision": entry_revision_token(entry),
            # Everything a determined caller might try to post alongside it.
            "author": "",
            "kind": EntryKind.MEETING,
            "occurred_at": "1.1.2000",
            "visibility_override": "RESTRICTED",
        },
    )

    entry.refresh_from_db()
    assert (entry.author_id, entry.kind, entry.occurred_at, entry.visibility_override) == before
