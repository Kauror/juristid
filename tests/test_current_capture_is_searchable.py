"""What the product captures today is what search can find today.

Search was answering against a capture model the product had left behind. The
composer simplification made `+ Märge` write a `MatterProceduralDevelopment`;
the indexer had been built around `Entry`, which the ordinary UI no longer
creates. Nothing bridged the two, so:

* every note a lawyer wrote after that change was absent from the corpus, while
  the seeded legacy `Entry` rows went on matching — so searching for your own
  words returned silence, which reads as «not recorded» rather than as «not
  indexed»;
* `MatterExternalPosition` had never been indexed at all, so a recorded opinion
  could not be found by its text, and an organisation known to a Matter *only*
  through its feedback could not be found at all — the MATTER row carries
  senders, and a company whose opinion somebody wrote down is not one.

(QA-003, QA-020.)

The freshness half matters as much as the projection half: `refresh_engagement`
exists because AUTH-003 created a source kind that only a full rebuild ever
wrote, and «recorded but not searchable until an operator runs a command» is
the same defect with a longer fuse. So these assert on writes, not on rebuilds.

What stays out of the corpus is asserted here too, because widening a
projection is exactly when a confidentiality decision gets lost by accident.
"""

from __future__ import annotations

import pytest

from app.matters.models import MatterExternalPosition
from app.matters.workspace import add_procedural_development
from app.search.models import SearchDocument, SearchSourceKind
from app.search.services import search_documents
from app.workflow.enums import DatePrecision

pytestmark = pytest.mark.django_db

NOTE_TITLE = "Ministeerium saatis eelnõu teise lugemise versiooni"
NOTE_BODY = "Komisjon arutas üleminekuaja pikendamist."


def _note(matter, author, *, title=NOTE_TITLE, note=NOTE_BODY):
    return add_procedural_development(
        matter=matter,
        author=author,
        title=title,
        occurred_on=None,
        occurred_on_precision=DatePrecision.EXACT.value,
        note=f"<p>{note}</p>",
    )


def _titles(user, query: str) -> list[str]:
    """The Matters a query reaches, through the corpus the page reads.

    `search_documents` rather than `search_matters`: the latter is the
    picker-shaped call that filters to MATTER rows, so it could not see a child
    row by construction and would prove nothing about whether one exists.
    """
    return [document.matter.title for document in search_documents(query=query, user=user)]


# ---------------------------------------------------------------------------
# `Märge`
# ---------------------------------------------------------------------------


def test_a_note_is_projected_when_it_is_written(normal_matter, specialist):
    _note(normal_matter, specialist)

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT)
    assert row.matter_id == normal_matter.pk
    assert row.title == NOTE_TITLE
    # The note is rich text and the index stores its words, not its markup.
    assert "<p>" not in row.body_text
    assert "üleminekuaja" in row.body_text


def test_a_note_is_findable_by_its_own_words(normal_matter, specialist):
    """The reported symptom, at the surface that produced it."""
    _note(normal_matter, specialist)

    assert normal_matter.title in _titles(specialist, "teise lugemise versiooni")


def test_correcting_a_note_moves_what_search_finds(normal_matter, specialist):
    """A correction that search does not follow leaves the old words findable."""
    from app.matters.services import correct_procedural_development

    development = _note(normal_matter, specialist).record
    correct_procedural_development(
        development=development,
        title="Ministeerium võttis eelnõu tagasi",
        occurred_on=development.occurred_on,
        occurred_on_precision=development.occurred_on_precision,
        note=development.note,
        process_phase=development.process_phase,
        actor=specialist,
    )

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT)
    assert row.title == "Ministeerium võttis eelnõu tagasi"


def test_removing_a_note_removes_its_projection(normal_matter, specialist):
    """The cascade is what is load-bearing, so the cascade is what is asserted."""
    development = _note(normal_matter, specialist).record
    development.delete()

    assert not SearchDocument.objects.filter(
        source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT
    ).exists()


def test_a_note_on_a_restricted_matter_is_not_findable_by_a_reader(
    restricted_matter, specialist, reader
):
    _note(restricted_matter, specialist)

    assert _titles(reader, "teise lugemise versiooni") == []


# ---------------------------------------------------------------------------
# `Arvamus / tagasiside`
# ---------------------------------------------------------------------------


def _position(matter, specialist, organisation=None, *, summary="Toetab pikemat üleminekuaega."):
    """A recorded position, named the way the record insists one is named.

    `matters_external_position_author_or_label` is the rule docs/adr/0091 §3.3
    put in the database: a position names an organisation, and received
    feedback may name a `source_label` instead. These tests are about the
    projection, not about that rule, so they satisfy it the ordinary way.
    """
    from app.matters.enums import ExternalPositionProvenance

    return MatterExternalPosition.objects.create(
        matter=matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED,
        source_label="" if organisation else "Liikmete küsitlus",
        summary=summary,
        created_by=specialist,
    )


def test_a_recorded_opinion_is_findable_by_its_text(normal_matter, specialist):
    _position(normal_matter, specialist)

    assert normal_matter.title in _titles(specialist, "pikemat üleminekuaega")


def test_an_organisation_known_only_through_feedback_finds_its_matter(
    normal_matter, specialist, organisation
):
    """QA-020, and the reason it is the same change as QA-003.

    The Matter row carries the Matter's senders. A body that appears on this
    file only because somebody wrote down what it thinks is not a sender, so
    before the opinion had a row of its own there was nowhere for its name to
    live.
    """
    _position(normal_matter, specialist, organisation)

    assert normal_matter.title in _titles(specialist, organisation.name)


def test_the_lawyers_private_note_stays_out_of_the_corpus(normal_matter, specialist):
    """§9 of the lawyer-workflow package, kept while the projection widened.

    `Juristi märkus` is this office's own assessment of a third party.
    Disclosing it is a decision somebody makes, not a convenience search
    performs — and widening a projection is exactly when such a decision gets
    lost by accident.
    """
    position = _position(normal_matter, specialist)
    position.lawyer_note = "Nende põhjendus ei arvesta liikmete kulumõjuga."
    position.save()

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.EXTERNAL_POSITION)
    # The body is the summary since ENG-083 moved it there to be quoted, and
    # only the summary: the note is not in any column of the row.
    assert row.body_text == position.summary
    assert all(
        "kulumõjuga" not in getattr(row, column)
        for column in ("title", "identifiers", "alias_text", "people_text", "body_text")
    )
    assert _titles(specialist, "kulumõjuga") == []


def test_an_opinion_on_a_restricted_matter_is_not_findable_by_a_reader(
    restricted_matter, specialist, reader
):
    _position(restricted_matter, specialist)

    assert _titles(reader, "pikemat üleminekuaega") == []


def test_a_restricted_opinion_disappears_without_a_reindex(normal_matter, specialist, reader):
    """Authorization is evaluated against the record's *current* override.

    This is the whole reason the opinion gets a row of its own rather than
    being folded into the Matter's, and it is the rule AUTH-003 established for
    `Kaasamine`: restricting a child hides it from search on the next query,
    with nothing reindexed.
    """
    from app.core.enums import Visibility

    position = _position(normal_matter, specialist)
    # A reader may read this open Matter, so the child is the only thing the
    # override can be about. Since docs/adr/0042 both lawyer roles read the
    # whole department, which is why the unauthorized party here is a reader
    # rather than a colleague.
    assert normal_matter.title in _titles(reader, "pikemat üleminekuaega")

    MatterExternalPosition.objects.filter(pk=position.pk).update(
        visibility_override=Visibility.RESTRICTED
    )

    assert _titles(reader, "pikemat üleminekuaega") == []
