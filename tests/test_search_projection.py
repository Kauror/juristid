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


def test_a_rebuild_removes_documents_for_matters_that_no_longer_exist(corpus) -> None:
    """The claim "safe to delete and rebuild" rests on this."""
    orphan = SearchDocument.objects.first()
    assert orphan is not None
    stale_count = SearchDocument.objects.count()

    corpus["waste"].delete()
    rebuild_all()
    assert SearchDocument.objects.count() == stale_count - 1


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
