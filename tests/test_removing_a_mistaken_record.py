"""A row filed by mistake comes off the file, and the trail of it does not.

`Teema käik` is professional history, and until OWNER-04 the only repair for a
`Märge` filed on the wrong Teema was `Muuda` — rewriting the mistake into
something else, which leaves a sentence nobody wrote, dated a day nobody chose,
attributed to whoever happened to be fixing it.

docs/adr/0102 states the division these hold:

* removal is neither `Tühistatud` nor `Tagasi võetud`, and none of the three
  may quietly become another;
* the row leaves every business read through the one chokepoint that already
  enforces authorization — the chronology, the rail, search, the counts;
* nothing is destroyed: the `ChangeEvent` trail keeps what was recorded and who
  removed it, and the evidence stays in its store;
* a closed Matter refuses, a reader never reaches the endpoint, a crafted
  identifier is a 404, a stale second tab is a 409 that writes nothing, and a
  double submit is not an error.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.intelligence.enums import FactStatus
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
from app.matters.models import (
    Entry,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.matters.removal import (
    RECORD_NOT_ON_MATTER,
    RecordRemovalConflict,
    record_revision,
    removable_kinds,
    remove_matter_record,
)
from app.matters.workspace import add_procedural_development
from app.search.models import SearchDocument, SearchSourceKind
from app.search.services import search_documents
from app.workflow.enums import DatePrecision

pytestmark = pytest.mark.django_db

NOTE_TITLE = "Ministeerium saatis eelnõu uue versiooni"


def _note(matter, author, *, title: str = NOTE_TITLE):
    return add_procedural_development(
        matter=matter,
        author=author,
        title=title,
        occurred_on=datetime.date(2026, 9, 1),
        occurred_on_precision=DatePrecision.EXACT.value,
        note="<p>Komisjon arutas üleminekuaega.</p>",
    ).record


def _remove(matter, kind, record, actor, **kwargs):
    return remove_matter_record(
        matter_id=matter.pk,
        kind_key=kind,
        record_id=record.pk,
        actor=actor,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The record leaves the file and nothing is destroyed
# ---------------------------------------------------------------------------


def test_a_removed_note_is_gone_from_every_business_read(normal_matter, specialist):
    development = _note(normal_matter, specialist)
    _remove(normal_matter, "marge", development, specialist)

    assert (
        not MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


def test_the_row_itself_is_still_there(normal_matter, specialist):
    """A column, not a `DELETE`. Nothing this product does erases a record."""
    development = _note(normal_matter, specialist)
    _remove(normal_matter, "marge", development, specialist)

    stored = MatterProceduralDevelopment.objects.get(pk=development.pk)
    assert stored.removed_at is not None
    assert stored.removed_by_id == specialist.pk
    assert stored.title == NOTE_TITLE


def test_the_audit_trail_names_the_record_and_the_person(normal_matter, specialist):
    development = _note(normal_matter, specialist)
    _remove(normal_matter, "marge", development, specialist)

    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_REMOVED, matter=normal_matter
    )
    assert event.actor_id == specialist.pk
    assert event.summary == NOTE_TITLE
    # And the record of it arriving is untouched beside it.
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED, matter=normal_matter
    ).exists()


def test_each_family_keeps_its_own_audit_event(normal_matter, specialist):
    """QA reads `Kõik muudatused` as sentences; a shared event would not be one."""
    engagement = MatterEngagement.objects.create(
        matter=normal_matter, title="Liikmete küsitlus", created_by=specialist
    )
    _remove(normal_matter, "kaasamine", engagement, specialist)

    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENGAGEMENT_REMOVED, matter=normal_matter
    ).exists()


# ---------------------------------------------------------------------------
# Removal is not cancellation and not withdrawal
# ---------------------------------------------------------------------------


def test_a_cancelled_deadline_still_reads_on_the_file(normal_matter, specialist):
    """The act docs/adr/0102 refuses to fold into removal.

    A plan somebody called off is history: the expectation was real, calling it
    off is itself a fact, and the chronology goes on saying `Tühistatud`.
    """
    deadline = MatterImportantDate.objects.create(
        matter=normal_matter,
        title="Komisjoni istung",
        date_value=datetime.date(2026, 11, 5),
        period_end=datetime.date(2026, 11, 5),
        date_precision=DatePrecision.EXACT.value,
        status=FactStatus.CANCELLED,
        created_by=specialist,
    )

    assert MatterImportantDate.objects.visible_to(specialist).filter(pk=deadline.pk).exists()


def test_a_removed_deadline_does_not(normal_matter, specialist):
    deadline = MatterImportantDate.objects.create(
        matter=normal_matter,
        title="Komisjoni istung",
        date_value=datetime.date(2026, 11, 5),
        period_end=datetime.date(2026, 11, 5),
        date_precision=DatePrecision.EXACT.value,
        created_by=specialist,
    )
    _remove(normal_matter, "tahtaeg", deadline, specialist)

    assert not MatterImportantDate.objects.visible_to(specialist).filter(pk=deadline.pk).exists()
    # And the status column is untouched: the two concepts do not share one.
    assert MatterImportantDate.objects.get(pk=deadline.pk).status == FactStatus.ACTIVE


def test_a_removed_commencement_leaves_the_rail(normal_matter, specialist):
    """The rail reads the same scoped selector the chronology does."""
    from app.intelligence.selectors import matter_intelligence

    commencement = MatterEffectiveDate.objects.create(
        matter=normal_matter,
        description="põhiosa",
        date_value=datetime.date(2027, 1, 1),
        period_end=datetime.date(2027, 1, 1),
        date_precision=DatePrecision.EXACT.value,
        created_by=specialist,
    )
    _remove(normal_matter, "joustumine", commencement, specialist)

    facts = matter_intelligence(normal_matter, specialist)
    assert [record.pk for record in facts.effective_dates] == []


# ---------------------------------------------------------------------------
# Search follows on the write, with nothing rebuilt
# ---------------------------------------------------------------------------


def _titles(user, query: str) -> list[str]:
    return [document.matter.title for document in search_documents(query=query, user=user)]


def test_a_removed_note_leaves_the_corpus_immediately(normal_matter, specialist):
    """«Findable until an operator runs a command» is the same defect, delayed."""
    development = _note(normal_matter, specialist)
    assert normal_matter.title in _titles(specialist, "uue versiooni")

    _remove(normal_matter, "marge", development, specialist)

    assert _titles(specialist, "uue versiooni") == []
    assert not SearchDocument.objects.filter(
        source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT, source_object_id=development.pk
    ).exists()


def test_a_rebuild_does_not_put_it_back(normal_matter, specialist):
    """The per-write path and the rebuild path have to agree, or one undoes the other."""
    from app.search.child_indexing import indexable_developments, refresh_developments

    development = _note(normal_matter, specialist)
    _remove(normal_matter, "marge", development, specialist)

    refresh_developments(indexable_developments())

    assert not SearchDocument.objects.filter(
        source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT, source_object_id=development.pk
    ).exists()


def test_the_integrity_check_does_not_report_a_shortfall(normal_matter, specialist):
    """A check that cries wolf on every corrected Matter is a check nobody reads."""
    from app.search.management.commands.check_search_integrity import _expected_populations

    development = _note(normal_matter, specialist)
    _remove(normal_matter, "marge", development, specialist)

    expected = {kind: count for _label, kind, count in _expected_populations()}
    assert expected[SearchSourceKind.PROCEDURAL_DEVELOPMENT.value] == 0


# ---------------------------------------------------------------------------
# The files a removed record carried
# ---------------------------------------------------------------------------


def test_a_link_to_a_removed_record_stops_claiming_to_belong_to_it(
    normal_matter, specialist, tmp_path
):
    """Active reference and retained evidence are two different things.

    The bytes stay — a paper that arrived on a Matter is evidence of the Matter
    — but a file row still reading «kuulub: …» under a record nobody can see is
    the page pointing at something that is not there.
    """
    from app.documents.links import DocumentLink
    from tests import factories

    development = _note(normal_matter, specialist)
    document = factories.DocumentFactory(matter=normal_matter)
    link = DocumentLink.objects.create(document=document, procedural_development=development)

    assert DocumentLink.objects.visible_to(specialist).filter(pk=link.pk).exists()

    _remove(normal_matter, "marge", development, specialist)

    assert not DocumentLink.objects.visible_to(specialist).filter(pk=link.pk).exists()
    # The row and the document are both still there for the technical path.
    assert DocumentLink.objects.filter(pk=link.pk).exists()


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_closed_matter_refuses(normal_matter, specialist):
    """Removing a row is editing the file's account of what happened.

    That is what closure stops, and the rule is enforced under the Matter's own
    row lock rather than by whether the page drew a chip — a browser holding a
    page from before the closure still has every button on it
    (docs/adr/0076 §2).
    """
    from app.core.errors import DomainError
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    development = _note(normal_matter, specialist)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    with pytest.raises(DomainError):
        _remove(normal_matter, "marge", development, specialist)

    assert (
        MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


def test_a_record_on_another_matter_is_refused(normal_matter, specialist):
    """The identifier a caller passes may not widen what it reaches."""
    from app.core.errors import DomainError
    from tests import factories

    elsewhere = factories.MatterFactory(owner=specialist)
    development = _note(elsewhere, specialist)

    with pytest.raises(DomainError) as refusal:
        _remove(normal_matter, "marge", development, specialist)

    assert str(refusal.value) == RECORD_NOT_ON_MATTER


def test_a_stale_second_tab_writes_nothing(normal_matter, specialist):
    """QA-002's rule, applied to the act that cannot be undone by correcting."""
    from app.matters.services import correct_procedural_development

    development = _note(normal_matter, specialist)
    stale = record_revision(development)

    correct_procedural_development(
        development=development,
        title="Ministeerium võttis eelnõu tagasi",
        occurred_on=development.occurred_on,
        occurred_on_precision=development.occurred_on_precision,
        note=development.note,
        process_phase=development.process_phase,
        actor=specialist,
    )

    with pytest.raises(RecordRemovalConflict):
        _remove(normal_matter, "marge", development, specialist, expected_revision=stale)

    assert (
        MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


def test_a_double_submit_is_not_an_error(normal_matter, specialist):
    """The second call finds exactly the state the person asked for."""
    development = _note(normal_matter, specialist)
    _remove(normal_matter, "marge", development, specialist)

    again = _remove(normal_matter, "marge", development, specialist)

    assert again.removed_at is not None
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_REMOVED, matter=normal_matter
        ).count()
        == 1
    )


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


def _url(matter, kind: str, record) -> str:
    return reverse(
        "matters:remove_record",
        kwargs={"pk": matter.pk, "kind": kind, "record_id": record.pk},
    )


def test_a_reader_cannot_reach_the_endpoint(client, normal_matter, specialist, reader):
    development = _note(normal_matter, specialist)
    client.force_login(reader)

    response = client.post(_url(normal_matter, "marge", development))

    assert response.status_code == 404
    assert (
        MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


def test_a_get_removes_nothing(signed_in, normal_matter, specialist):
    development = _note(normal_matter, specialist)

    response = signed_in.get(_url(normal_matter, "marge", development))

    assert response.status_code == 405
    assert (
        MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


def test_an_unknown_kind_is_a_404(signed_in, normal_matter, specialist):
    development = _note(normal_matter, specialist)
    url = reverse(
        "matters:remove_record",
        kwargs={"pk": normal_matter.pk, "kind": "toimik", "record_id": development.pk},
    )

    assert signed_in.post(url).status_code == 404


def test_a_record_on_another_matter_is_a_404_through_the_endpoint(
    signed_in, normal_matter, specialist
):
    """The identifier in the path may not widen what the request reaches.

    The service refuses it too, under the lock. Both are asserted because they
    fail differently: this is how a guessed UUID is answered, and the service's
    is what a caller reaching it another way gets.
    """
    from tests import factories

    elsewhere = factories.MatterFactory(owner=specialist)
    development = _note(elsewhere, specialist)

    response = signed_in.post(_url(normal_matter, "marge", development))

    assert response.status_code == 404
    assert (
        MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


def test_the_lookup_goes_through_the_childs_own_chokepoint(signed_in, normal_matter, specialist):
    """A restricted child is read through `visible_to`, never by id off the Matter.

    Since docs/adr/0042 both lawyer roles read the whole department, so a
    colleague is *not* the unauthorized party for a child override, and a
    reader never reaches this endpoint at all — which leaves the chokepoint
    itself as the thing to assert. It is the same one
    `_development_for_correction` uses, and reading a child any other way would
    bypass an override the record carries (AUTH-003, docs/adr/0038).
    """
    development = _note(normal_matter, specialist)
    MatterProceduralDevelopment.objects.filter(pk=development.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert not (
        MatterProceduralDevelopment.objects.visible_to(None).filter(pk=development.pk).exists()
    )
    # And the endpoint reads through exactly that queryset.
    response = signed_in.post(
        _url(normal_matter, "marge", development),
        {"revision": record_revision(development)},
    )
    assert response.status_code == 200


def test_the_post_removes_the_record_and_answers_the_page(signed_in, normal_matter, specialist):
    development = _note(normal_matter, specialist)

    response = signed_in.post(
        _url(normal_matter, "marge", development),
        {"revision": record_revision(development)},
    )

    assert response.status_code == 200
    assert NOTE_TITLE not in response.content.decode()
    assert (
        not MatterProceduralDevelopment.objects.visible_to(specialist)
        .filter(pk=development.pk)
        .exists()
    )


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def test_every_removable_family_is_named_once_and_resolves(normal_matter, specialist):
    """Guards the guard: a key that stopped resolving fails in silence."""
    kinds = removable_kinds()

    assert set(kinds) == {
        "sissekanne",
        "marge",
        "kaasamine",
        "seisukoht",
        "ulevaade",
        "tahtaeg",
        "joustumine",
        "toovoit",
    }
    for key, kind in kinds.items():
        assert kind.key == key
        assert kind.label and kind.genitive
        assert hasattr(kind.model, "removed_at")


def test_a_sent_koja_arvamus_is_not_removable():
    """The exclusion docs/adr/0102 argues rather than omits.

    A sent opinion is a letter that left this office. Taking it off the file
    would be the record claiming it never went; the domain's own answer to «we
    took it back» is `SubmissionStatus.WITHDRAWN`, which says something
    different and true.
    """
    from app.submissions.models import Submission

    assert Submission not in {kind.model for kind in removable_kinds().values()}


def test_an_entry_is_removable_and_its_revisions_are_untouched(normal_matter, specialist):
    """The one removable record whose correction history is append-only."""
    from app.matters.models import EntryRevision
    from app.matters.services import add_entry, edit_entry

    entry = add_entry(matter=normal_matter, body="<p>Esimene sõnastus.</p>", author=specialist)
    edit_entry(entry=entry, body="<p>Teine sõnastus.</p>", actor=specialist)
    revisions = EntryRevision.objects.filter(entry=entry).count()
    assert revisions

    _remove(normal_matter, "sissekanne", entry, specialist)

    assert not Entry.objects.visible_to(specialist).filter(pk=entry.pk).exists()
    assert EntryRevision.objects.filter(entry=entry).count() == revisions


def test_a_removed_opinion_leaves_the_corpus(normal_matter, specialist, organisation):
    from app.matters.enums import ExternalPositionProvenance

    position = MatterExternalPosition.objects.create(
        matter=normal_matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED,
        summary="Toetab pikemat üleminekuaega.",
        created_by=specialist,
    )
    assert normal_matter.title in _titles(specialist, "pikemat üleminekuaega")

    _remove(normal_matter, "seisukoht", position, specialist)

    assert _titles(specialist, "pikemat üleminekuaega") == []
