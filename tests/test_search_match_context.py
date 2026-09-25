"""A result says what matched, and a merged tag is found through its successor.

Two findings about what a search result can tell a lawyer.

* **ENG-083.** Several hits on one Teema — a Märge, a Kaasamine, a received
  opinion — rendered identically: a kind badge and the Teema's title. A
  received opinion matched by a phrase in its summary showed no excerpt at all,
  because the summary was its *title*. And a search for a colleague ranked their
  entries in the taxonomy tier, labelled «Asutus, valdkond või silt».
* **ENG-081.** `Tag.merged_into` promises that a retired tag stays findable
  through the one that replaced it. The indexer never followed the merge, and a
  merge owed no refresh of any kind.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.utils import timezone

from app.matters.entry_enums import EntryKind
from app.matters.models import (
    Entry,
    Matter,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    TagAssignment,
)
from app.organisations.models import Organisation
from app.search.models import SearchRebuildDebt
from app.search.services import MATCH_PERSON, MATCH_TAXONOMY, search, search_documents
from app.taxonomy.models import Tag, TagAlias

pytestmark = pytest.mark.django_db


@pytest.fixture
def matter(specialist):
    return Matter.objects.create(
        title="Pakendiseaduse muutmise eelnõu",
        owner=specialist,
        reference_year=2026,
        reference_number=770001,
    )


def _titles(query, user):
    return [row.matter.title for row in search_documents(query=query, user=user)]


# -- ENG-083: each result names the record that matched ---------------------


def test_a_note_hit_names_the_note_under_the_teema(matter, specialist):
    MatterProceduralDevelopment.objects.create(
        matter=matter,
        title="Ministeerium saatis kooskõlastusringile",
        note="Tagasiside tähtaeg on kolmapäev.",
        created_by=specialist,
    )
    (result,) = list(search(query="kooskõlastusringile", user=specialist))
    assert result.matter == matter
    assert result.source_title == "Ministeerium saatis kooskõlastusringile"

    client = Client()
    client.force_login(specialist)
    html = client.get("/otsing/", {"q": "kooskõlastusringile"}).content.decode()
    assert "Ministeerium saatis kooskõlastusringile" in html


def test_a_received_opinion_is_quoted_and_named_by_who_gave_it(matter, specialist):
    ministry = Organisation.objects.create(
        name="Kliimaministeerium", normalized_name="kliimaministeerium"
    )
    MatterExternalPosition.objects.create(
        matter=matter,
        organisation=ministry,
        provenance="DISCOVERED",
        summary="Ministeerium toetab üleminekuaja pikendamist väiketootjatele.",
        created_by=specialist,
    )
    (result,) = search(query="väiketootjatele", user=specialist)
    assert result.source_title == "Kliimaministeerium"
    quoted = "".join(run.text for run in result.snippet)
    assert "väiketootjatele" in quoted
    assert any(run.highlight for run in result.snippet)


def test_an_authors_name_is_labelled_as_the_author(matter, specialist):
    specialist.display_name = "Maarika Kuusemets"
    specialist.save(update_fields=["display_name"])
    Entry.objects.create(
        matter=matter,
        author=specialist,
        kind=EntryKind.values[0],
        occurred_at=timezone.now(),
        body="<p>Kohtumise kokkuvõte.</p>",
    )
    results = search(query="Kuusemets", user=specialist)
    assert [result.match_kind for result in results] == [MATCH_PERSON]
    assert results[0].match_label == "Autor"
    assert results[0].match_kind != MATCH_TAXONOMY


def test_an_organisation_on_an_entry_is_still_a_taxonomy_hit(matter, specialist):
    ministry = Organisation.objects.create(
        name="Rahandusministeerium", normalized_name="rahandusministeerium"
    )
    Entry.objects.create(
        matter=matter,
        author=specialist,
        organisation=ministry,
        kind=EntryKind.values[0],
        occurred_at=timezone.now(),
        body="<p>Kohtumine.</p>",
    )
    kinds = {
        r.source_kind: r.match_kind for r in search(query="Rahandusministeerium", user=specialist)
    }
    assert kinds["ENTRY"] == MATCH_TAXONOMY


# -- ENG-081: a merged tag is found through its successor --------------------


@pytest.fixture
def tags():
    old = Tag.objects.create(key="r6-vanasilt", name_et="Vanasilt")
    older = Tag.objects.create(key="r6-vanimsilt", name_et="Vanimsilt")
    new = Tag.objects.create(key="r6-uussilt", name_et="Uussilt")
    TagAlias.objects.create(tag=new, alias="Uusnimekuju", normalized_alias="uusnimekuju")
    return old, older, new


def test_a_matter_tagged_with_a_merged_tag_is_found_through_the_successor(matter, specialist, tags):
    old, older, new = tags
    TagAssignment.objects.create(matter=matter, tag=old)
    other = Matter.objects.create(
        title="Teine teema", owner=specialist, reference_year=2026, reference_number=770002
    )
    TagAssignment.objects.create(matter=other, tag=older)
    older.merged_into = old
    older.is_active = False
    older.save()
    debt_before = SearchRebuildDebt.objects.count()

    old.merged_into = new
    old.is_active = False
    old.save()

    # Found by the governed name, by its alias, and still by the retired one.
    for query in ("Uussilt", "Uusnimekuju", "Vanasilt"):
        assert matter.title in _titles(query, specialist), query
    # A tag merged into the merged tag resolves through it as well.
    assert other.title in _titles("Uussilt", specialist)
    # Refreshed where it happened, not owed as a rebuild of the corpus.
    assert SearchRebuildDebt.objects.count() == debt_before


def test_a_save_that_does_not_move_the_merge_refreshes_nothing(matter, specialist, tags):
    old, _, new = tags
    TagAssignment.objects.create(matter=matter, tag=old)
    old.merged_into = new
    old.is_active = False
    old.save()

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    old.definition = "Selgitus"
    with CaptureQueriesContext(connection) as captured:
        old.save(update_fields=["definition"])
    assert not [q for q in captured.captured_queries if "search_searchdocument" in q["sql"]]
