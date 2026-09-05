"""The rebuildable projection and its ranking.

Two things are being asserted throughout: that the index is genuinely derived —
delete it, rebuild it, get the same answers — and that the deterministic tiers
put exact answers ahead of fuzzy ones, so a lawyer typing a reference gets that
file rather than a relevance score's opinion about it.
"""

from __future__ import annotations

import pytest

from app.matters.models import Matter
from app.matters.services import create_matter
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import indexable_matters, rebuild_all, refresh_matter, refresh_matters
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.search.services import (
    MATCH_FUZZY,
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
    """2,500 matters must not mean 2,500 round trips per related table.

    Was 30, measured at 13 over the corpus plus twenty synthetic Matters. 17
    leaves room for one more prefetched relation and stays an order of magnitude
    below the shape it exists to catch: the failure is a query per Matter, and
    there are more than twenty of them here.
    """
    for index in range(20):
        create_matter(title=f"Sünteetiline teema {index}", owner=specialist, reference_year=2026)
    with django_assert_max_num_queries(17):
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


def test_entry_text_is_indexed_as_its_own_row(db, specialist) -> None:
    """The gap ADR 0013 stated, closed in Stage 2B — and closed *as its own row*.

    Stage 2A asserted the opposite of this, correctly for that stage. What
    matters now is not merely that the text is findable but that it is findable
    as an entry: folding it into the Matter's row would make every hit read "the
    matter matched" and lose which entry it was (docs/adr/0014).
    """
    matter = create_matter(title="Sünteetiline teema", owner=specialist, reference_year=2026)
    factories.EntryFactory(matter=matter, body="<p>Konfidentsiaalne sissekande tekst</p>")
    rebuild_all()

    assert SearchDocument.objects.filter(source_kind=SearchSourceKind.ENTRY).exists()
    # The Matter row itself still carries no entry text.
    matter_row = SearchDocument.objects.get(matter=matter, source_kind=SearchSourceKind.MATTER)
    assert "sissekande" not in matter_row.body_text
    # And a Matter-only search does not answer with the entry.
    assert search_matters(query="Konfidentsiaalne sissekande", user=specialist) == []


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


# -- a full rebuild is all or nothing --------------------------------------
#
# Being derived data makes the index cheap to recreate. It does not make a
# half-built one safe to serve: a partial index answers confidently and silently
# with a fraction of the corpus, and an empty results page looks the same
# whether the matter does not exist or the rebuild died before reaching it.


def test_an_interrupted_rebuild_leaves_the_previous_index_complete(
    corpus, specialist, monkeypatch
) -> None:
    from app.search import indexing

    original_title = corpus["waste"].title
    complete = SearchDocument.objects.count()
    assert search_matters(query="Jäätmeseaduse", user=specialist)

    # Change canonical data so a rebuild would genuinely produce different
    # output. `update()` bypasses the post_save signal, so the index stays as it
    # was and the difference is only visible once a rebuild runs.
    Matter.objects.filter(pk=corpus["waste"].pk).update(
        title="Pakendiseaduse muutmise seaduse eelnõu"
    )

    # Fail partway: with one matter per batch, the second batch raises after the
    # first has already been written.
    calls = {"count": 0}
    recompute = indexing._recompute_vectors

    def failing(documents):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated interruption partway through the rebuild")
        return recompute(documents)

    monkeypatch.setattr(indexing, "_recompute_vectors", failing)
    with pytest.raises(RuntimeError):
        rebuild_all(batch_size=1)
    monkeypatch.undo()

    # The previous index is intact: same size, and still answering.
    assert SearchDocument.objects.count() == complete
    assert search_matters(query="Jäätmeseaduse", user=specialist)[0].matter == corpus["waste"]
    assert SearchDocument.objects.get(matter=corpus["waste"]).title == original_title
    # And the half-written new state is nowhere: the interrupted run got as far
    # as one batch, and that batch is gone with the rest.
    assert search_matters(query="Pakendiseaduse", user=specialist) == []


def test_a_rebuild_after_an_interrupted_one_produces_the_new_complete_index(
    corpus, specialist, monkeypatch
) -> None:
    from app.search import indexing

    complete = SearchDocument.objects.count()
    Matter.objects.filter(pk=corpus["waste"].pk).update(
        title="Pakendiseaduse muutmise seaduse eelnõu"
    )

    calls = {"count": 0}
    recompute = indexing._recompute_vectors

    def failing(documents):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated interruption partway through the rebuild")
        return recompute(documents)

    monkeypatch.setattr(indexing, "_recompute_vectors", failing)
    with pytest.raises(RuntimeError):
        rebuild_all(batch_size=1)
    monkeypatch.undo()

    result = rebuild_all()
    assert result.documents == complete
    assert search_matters(query="Pakendiseaduse", user=specialist)[0].matter == corpus["waste"]
    assert search_matters(query="Jäätmeseaduse", user=specialist) == []


def test_an_interrupted_rebuild_leaves_no_orphans_behind(corpus, monkeypatch) -> None:
    """Whatever the interrupted run wrote is rolled back with everything else."""
    from app.search import indexing

    before = set(SearchDocument.objects.values_list("pk", flat=True))

    calls = {"count": 0}
    recompute = indexing._recompute_vectors

    def failing(documents):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated interruption partway through the rebuild")
        return recompute(documents)

    monkeypatch.setattr(indexing, "_recompute_vectors", failing)
    with pytest.raises(RuntimeError):
        rebuild_all(batch_size=1)
    monkeypatch.undo()

    assert set(SearchDocument.objects.values_list("pk", flat=True)) == before


# -- typo tolerance on titles of realistic length ---------------------------
#
# The deployment found this. `similarity()` divides shared trigrams by the
# trigrams of the *whole* string, so it decays as titles get longer — and real
# Estonian legal titles are long. The earlier test passed only because its
# synthetic title was short enough to hide the effect.


LONG_TITLE = (
    "Pakendiseaduse, jäätmeseaduse ja tootjavastutusorganisatsiooni seaduse "
    "muutmise seaduse eelnõu väljatöötamiskavatsus ning sellega seonduvalt "
    "teiste seaduste muutmine"
)


@pytest.fixture
def long_titled(db, specialist):
    matter = create_matter(title=LONG_TITLE, owner=specialist, reference_year=2026)
    create_matter(
        title="Käibemaksuseaduse muutmise seaduse eelnõu", owner=specialist, reference_year=2026
    )
    rebuild_all()
    return matter


def test_a_one_letter_typo_is_caught_inside_a_realistically_long_title(
    long_titled, specialist
) -> None:
    """`pakendiseeaduse` against a 170-character title.

    Whole-string similarity scores this 0.259 — under the old 0.3 threshold —
    while word similarity scores it 0.824, because it compares the query against
    the best-matching run of words rather than against everything.
    """
    found = [
        result.matter.pk for result in search_matters(query="pakendiseeaduse", user=specialist)
    ]
    assert long_titled.pk in found


def test_another_typo_in_the_middle_of_the_same_title(long_titled, specialist) -> None:
    found = [result.matter.pk for result in search_matters(query="jäätmeseeaduse", user=specialist)]
    assert long_titled.pk in found


def test_typo_tolerance_does_not_become_a_source_of_noise(long_titled, specialist) -> None:
    """The fuzzy tier is a last resort, not a wildcard.

    A word that shares only a stem with the corpus must not drag every matter
    back: that turns the bottom tier into random results and teaches people to
    distrust the whole ranking.
    """
    results = search_matters(query="kalapüügikvoot", user=specialist)
    assert results == []


def test_the_fuzzy_tier_still_ranks_last(long_titled, specialist) -> None:
    """An exact title match must never lose to a typo match."""
    results = search_matters(query="Käibemaksuseaduse muutmise seaduse eelnõu", user=specialist)
    assert results[0].matter.title == "Käibemaksuseaduse muutmise seaduse eelnõu"
    assert results[0].match_kind != MATCH_FUZZY


def test_an_exact_reference_is_still_exact_or_nothing(long_titled, specialist) -> None:
    """The fuzzy change must not loosen reference lookup."""
    assert search_matters(query="2026_99999", user=specialist) == []
