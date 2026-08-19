"""The rebuildable projection and its ranking.

Two things are being asserted throughout: that the index is genuinely derived —
delete it, rebuild it, get the same answers — and that the deterministic tiers
put exact answers ahead of fuzzy ones, so a lawyer typing a reference gets that
file rather than a relevance score's opinion about it.
"""

from __future__ import annotations

import pytest

from app.matters.services import create_matter
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import indexable_matters, rebuild_all, refresh_matter, refresh_matters
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import (
    MATCH_REFERENCE,
    MATCH_TAXONOMY,
    MATCH_TITLE,
    result_count,
    search_matters,
)
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def corpus(db, specialist):
    """A small Estonian legal/policy corpus with known expected answers."""
    ministry = Organisation.objects.create(
        name="Majandus- ja Kommunikatsiooniministeerium",
        organisation_type=OrganisationType.MINISTRY,
    )
    ministry.aliases.create(alias="MKM", alias_type="ABBREVIATION")

    environment = Organisation.objects.create(
        name="Kliimaministeerium", organisation_type=OrganisationType.MINISTRY
    )

    area = factories.PolicyAreaFactory(name_et="Maksundus")
    tag = factories.TagFactory(name_et="Käibemaks")
    tag.aliases.create(alias="KMS")

    matters = {
        "vat": create_matter(
            title="Käibemaksuseaduse muutmise seaduse eelnõu",
            owner=specialist,
            reference_year=2026,
            addressee_organisation=ministry,
        ),
        "cyber": create_matter(
            title="Küberturvalisuse seaduse ja teiste seaduste muutmise seadus",
            owner=specialist,
            reference_year=2026,
            addressee_organisation=ministry,
        ),
        "climate": create_matter(
            title="Kliimakindla majanduse seaduse väljatöötamiskavatsus",
            owner=specialist,
            reference_year=2026,
            addressee_organisation=environment,
        ),
        "waste": create_matter(
            title="Jäätmeseaduse muutmise seaduse eelnõu",
            owner=specialist,
            reference_year=2025,
        ),
    }
    matters["vat"].policy_areas.set([area])
    matters["vat"].tags.add(tag, through_defaults={})
    rebuild_all()
    return matters


def _references(results) -> list[str]:
    return [result.matter.display_reference for result in results]


# -- lifecycle -------------------------------------------------------------


def test_the_index_can_be_built_from_an_empty_table(corpus) -> None:
    SearchDocument.objects.all().delete()
    assert SearchDocument.objects.count() == 0

    result = rebuild_all()
    assert result.documents == len(corpus)
    assert result.index_version == INDEX_VERSION


def test_rebuilding_twice_produces_the_same_index(corpus) -> None:
    first = rebuild_all()
    second = rebuild_all()
    assert first.documents == second.documents
    assert SearchDocument.objects.count() == first.documents


def test_a_rebuild_clears_documents_left_behind_by_an_earlier_run(corpus) -> None:
    """The claim "safe to delete and rebuild" rests on this.

    A Matter cannot be deleted — its audit trail protects it — so the realistic
    stale state is a duplicate or orphaned document left by an interrupted run,
    which is what this builds.
    """
    from django.utils import timezone

    expected = SearchDocument.objects.count()
    SearchDocument.objects.create(
        matter=corpus["waste"],
        source_kind=SearchSourceKind.ENTRY,
        source_object_id=None,
        title="Aegunud dokument varasemast käivitusest",
        indexed_at=timezone.now(),
    )
    assert SearchDocument.objects.count() == expected + 1

    rebuild_all()
    assert SearchDocument.objects.count() == expected
    assert not SearchDocument.objects.filter(title__startswith="Aegunud").exists()


def test_refreshing_one_matter_is_idempotent(corpus) -> None:
    matter = corpus["vat"]
    refresh_matter(matter)
    refresh_matter(matter)
    assert (
        SearchDocument.objects.filter(matter=matter, source_kind=SearchSourceKind.MATTER).count()
        == 1
    )


def test_a_refreshed_matter_becomes_findable_under_its_new_title(corpus, specialist) -> None:
    matter = corpus["waste"]
    matter.title = "Pakendiseaduse muutmise seaduse eelnõu"
    matter.save(update_fields=["title", "updated_at"])
    refresh_matter(matter)

    assert _references(search_matters(query="Pakendiseaduse", user=specialist)) == [
        matter.display_reference
    ]


def test_indexing_does_not_explode_into_a_query_per_matter(
    corpus, specialist, django_assert_max_num_queries
) -> None:
    """2,500 matters must not mean 2,500 round trips per related table."""
    for index in range(20):
        create_matter(title=f"Sünteetiline teema {index}", owner=specialist, reference_year=2026)
    with django_assert_max_num_queries(30):
        refresh_matters(indexable_matters())


# -- ranking ---------------------------------------------------------------


def test_an_exact_reference_outranks_everything(corpus, specialist) -> None:
    reference = corpus["cyber"].display_reference
    results = search_matters(query=reference, user=specialist)
    assert results[0].matter == corpus["cyber"]
    assert results[0].match_kind == MATCH_REFERENCE


@pytest.mark.parametrize("separator", ["_", "-", " "])
def test_a_reference_is_found_however_it_is_typed(corpus, specialist, separator: str) -> None:
    matter = corpus["cyber"]
    typed = f"{matter.reference_year}{separator}{matter.reference_number}"
    results = search_matters(query=typed, user=specialist)
    assert results[0].matter == matter


def test_an_exact_title_ranks_above_a_merely_related_one(corpus, specialist) -> None:
    results = search_matters(query="Jäätmeseaduse muutmise seaduse eelnõu", user=specialist)
    assert results[0].matter == corpus["waste"]
    assert results[0].match_kind == MATCH_TITLE


def test_estonian_stemming_finds_an_inflected_form(corpus, specialist) -> None:
    """`käibemaks` must find `Käibemaksuseaduse`-titled work."""
    found = _references(search_matters(query="käibemaksuseadus", user=specialist))
    assert corpus["vat"].display_reference in found


def test_a_title_typo_is_caught_by_trigram_fallback(corpus, specialist) -> None:
    results = search_matters(query="Jäätmeseeaduse", user=specialist)
    assert corpus["waste"].display_reference in _references(results)


def test_an_organisation_alias_finds_its_matters(corpus, specialist) -> None:
    found = _references(search_matters(query="MKM", user=specialist))
    assert corpus["vat"].display_reference in found
    assert corpus["cyber"].display_reference in found
    assert corpus["climate"].display_reference not in found


def test_a_tag_alias_finds_its_matter(corpus, specialist) -> None:
    results = search_matters(query="KMS", user=specialist)
    assert corpus["vat"].display_reference in _references(results)
    assert results[0].match_kind == MATCH_TAXONOMY


def test_a_policy_area_finds_its_matter(corpus, specialist) -> None:
    assert corpus["vat"].display_reference in _references(
        search_matters(query="Maksundus", user=specialist)
    )


def test_diacritic_free_typing_still_finds_the_organisation(corpus, specialist) -> None:
    """Estonian users type both `õigusloome` and `oigusloome`."""
    found = _references(search_matters(query="Kliimaministeerium", user=specialist))
    assert corpus["climate"].display_reference in found


def test_an_empty_query_returns_nothing_rather_than_everything(corpus, specialist) -> None:
    assert search_matters(query="", user=specialist) == []
    assert result_count(query="   ", user=specialist) == 0


def test_ranking_ignores_who_owns_the_matter(corpus, specialist, other_specialist) -> None:
    """A search that favours one lawyer's files hides another's."""
    corpus["waste"].owner = other_specialist
    corpus["waste"].save(update_fields=["owner", "updated_at"])
    rebuild_all()

    first = _references(search_matters(query="seaduse eelnõu", user=specialist))
    corpus["waste"].owner = specialist
    corpus["waste"].save(update_fields=["owner", "updated_at"])
    rebuild_all()
    second = _references(search_matters(query="seaduse eelnõu", user=specialist))
    assert first == second


# -- what is deliberately not indexed --------------------------------------


def test_raw_workbook_rows_never_reach_the_index(db, specialist) -> None:
    """A source row can contain anything. It is provenance, not search content."""
    matter = create_matter(title="Sünteetiline teema", owner=specialist, reference_year=2026)
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_row_raw={"B": "Salajane lähterea sisu mida ei indekseerita"},
        source_title="Salajane lähterea sisu mida ei indekseerita",
    )
    rebuild_all()

    assert search_matters(query="Salajane lähterea sisu", user=specialist) == []
    document = SearchDocument.objects.get(matter=matter)
    assert "Salajane" not in document.title
    assert "Salajane" not in document.alias_text
    assert document.body_text == ""


def test_entry_text_is_not_indexed_in_this_stage(db, specialist) -> None:
    """Deferred to Stage 2B on purpose (docs/adr/0013)."""
    matter = create_matter(title="Sünteetiline teema", owner=specialist, reference_year=2026)
    factories.EntryFactory(matter=matter, body="<p>Konfidentsiaalne sissekande tekst</p>")
    rebuild_all()

    assert search_matters(query="Konfidentsiaalne sissekande", user=specialist) == []
    assert not SearchDocument.objects.filter(source_kind=SearchSourceKind.ENTRY).exists()


def test_the_projection_stores_no_visibility_of_its_own(corpus) -> None:
    """The field the specification sketches and Stage 0 proved must not exist.

    A stored authorization value goes stale the instant a Matter is restricted,
    and a projection is refreshed after the fact by definition.
    """
    columns = {field.name for field in SearchDocument._meta.get_fields()}
    assert "visibility" not in columns
    assert "effective_visibility" not in columns
    assert "visibility_override" not in columns


# -- the projection maintains itself ---------------------------------------
#
# CI caught this the hard way: with indexing left to an operator, a seeded
# Matter searched for by title returned nothing at all, quietly, behind a
# plausible empty-results page. A search that silently misses records is worse
# than no search, because people stop checking it.


def test_a_newly_created_matter_is_findable_without_any_reindex(db, specialist) -> None:
    matter = create_matter(
        title="Sünteetiline pakendiseaduse muutmise eelnõu",
        owner=specialist,
        reference_year=2026,
    )
    assert search_matters(query="pakendiseaduse", user=specialist)[0].matter == matter


def test_renaming_a_matter_updates_what_finds_it(db, specialist) -> None:
    matter = create_matter(title="Sünteetiline esialgne pealkiri", owner=specialist)
    matter.title = "Sünteetiline muudetud pealkiri"
    matter.save(update_fields=["title", "updated_at"])

    assert search_matters(query="muudetud", user=specialist)[0].matter == matter
    assert search_matters(query="esialgne", user=specialist) == []


def test_adding_a_tag_makes_the_matter_findable_by_it(db, specialist) -> None:
    matter = create_matter(title="Sünteetiline sildistatav teema", owner=specialist)
    tag = factories.TagFactory(name_et="Riigihanked")
    matter.tags.add(tag, through_defaults={})

    assert search_matters(query="Riigihanked", user=specialist)[0].matter == matter


def test_adding_a_policy_area_makes_the_matter_findable_by_it(db, specialist) -> None:
    matter = create_matter(title="Sünteetiline valdkondlik teema", owner=specialist)
    matter.policy_areas.add(factories.PolicyAreaFactory(name_et="Keskkonnaõigus"))

    assert search_matters(query="Keskkonnaõigus", user=specialist)[0].matter == matter


def test_exactly_one_document_survives_repeated_saves(db, specialist) -> None:
    matter = create_matter(title="Sünteetiline korduvalt salvestatud teema", owner=specialist)
    for _ in range(3):
        matter.save()
    assert SearchDocument.objects.filter(matter=matter).count() == 1


def test_a_bulk_writer_can_suspend_indexing_and_refresh_once(db, specialist) -> None:
    """The escape hatch the importer uses, so 2,455 rows are not 2,455 refreshes."""
    from app.search.indexing import suspend_indexing

    with suspend_indexing():
        matter = create_matter(title="Sünteetiline hulgi loodud teema", owner=specialist)
        assert not SearchDocument.objects.filter(matter=matter).exists()

    refresh_matter(matter)
    assert search_matters(query="hulgi", user=specialist)[0].matter == matter


# -- reference queries are exact ------------------------------------------


def test_a_reference_query_never_falls_back_to_a_similar_reference(corpus, specialist) -> None:
    """`2026_1` and `2026_2` are different files.

    Trigram similarity rates them as nearly identical, so a fuzzy fallback here
    would hand a lawyer the wrong matter for the most precise query they can
    type.
    """
    target = corpus["vat"]
    results = search_matters(query=target.display_reference, user=specialist)
    assert [result.matter for result in results] == [target]


def test_an_unknown_reference_returns_nothing_rather_than_a_near_miss(corpus, specialist) -> None:
    assert search_matters(query="2026_99999", user=specialist) == []
