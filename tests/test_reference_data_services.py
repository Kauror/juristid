"""Haldus → Klassifikaatorid: the catalogue and vocabulary services (docs/adr/0187).

Each rule the services promise, against the database: duplicate prevention,
historical names, the reviewed baseline, stale pages, merges that move no
assignment, the audit row, and the search-freshness obligation every name
change owes.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.organisations import administration as catalogue
from app.organisations.models import AliasType, Organisation, OrganisationAlias
from app.search.indexing import rebuild_all
from app.search.models import SearchRebuildDebt, SearchRebuildReason
from app.search.services import search
from app.submissions.models import SubmissionTagAssignment
from app.taxonomy import administration as vocabulary
from app.taxonomy.models import TagAlias
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _owed(reason: str) -> bool:
    return SearchRebuildDebt.objects.filter(reason=reason).exists()


def _events(event_type: str) -> list[ChangeEvent]:
    return list(ChangeEvent.objects.filter(event_type=event_type))


# -- organisations: create -----------------------------------------------------------------


def test_creating_an_organisation_records_who_and_what(department_head):
    created = catalogue.create_organisation(
        actor=department_head, name="  Tarbijakaitse   Amet ", organisation_type="AUTHORITY"
    )

    assert created.name == "Tarbijakaitse Amet"
    (event,) = _events(ChangeEventType.ORGANISATION_CREATED)
    assert event.actor == department_head
    assert event.matter is None
    assert event.object_id == created.pk
    assert event.payload["name"] == "Tarbijakaitse Amet"


def test_a_name_the_catalogue_already_has_is_refused_and_named(department_head):
    existing = factories.OrganisationFactory(name="Konkurentsiamet")

    with refused(catalogue.duplicate_refusal(existing)):
        catalogue.create_organisation(actor=department_head, name="KONKURENTSIAMET")

    assert Organisation.objects.filter(normalized_name="konkurentsiamet").count() == 1
    assert _events(ChangeEventType.ORGANISATION_CREATED) == []


def test_a_name_another_body_answers_to_as_a_spelling_is_refused(department_head):
    existing = factories.OrganisationFactory(name="Andmekaitse Inspektsioon")
    OrganisationAlias.objects.create(organisation=existing, alias="AKI")

    with refused(catalogue.duplicate_refusal(existing)):
        catalogue.create_organisation(actor=department_head, name="aki")


def test_a_registry_code_already_held_is_refused(department_head):
    existing = factories.OrganisationFactory(name="Näidis OÜ", registry_code="10000001")

    with refused(catalogue.registry_code_refusal(existing)):
        catalogue.create_organisation(
            actor=department_head, name="Teine Näidis OÜ", registry_code="10000001"
        )


def test_a_blank_name_is_refused(department_head):
    with refused(catalogue.NAME_REQUIRED):
        catalogue.create_organisation(actor=department_head, name="­  ")


# -- organisations: rename -----------------------------------------------------------------


def test_a_rename_keeps_the_old_name_as_a_historical_spelling(department_head):
    body = factories.OrganisationFactory(name="Tehnilise Järelevalve Amet")

    result = catalogue.rename_organisation(
        actor=department_head,
        organisation=body,
        name="Tarbijakaitse ja Tehnilise Järelevalve Amet",
        expected_revision=catalogue.organisation_revision(body),
    )

    body.refresh_from_db()
    assert body.name == "Tarbijakaitse ja Tehnilise Järelevalve Amet"
    assert result.historical_alias is not None
    assert result.historical_alias.alias == "Tehnilise Järelevalve Amet"
    assert result.historical_alias.alias_type == AliasType.HISTORICAL_NAME
    (event,) = _events(ChangeEventType.ORGANISATION_RENAMED)
    assert event.payload["from"] == "Tehnilise Järelevalve Amet"
    assert event.payload["to"] == body.name
    assert event.actor == department_head


def test_a_typo_correction_may_drop_the_old_spelling(department_head):
    body = factories.OrganisationFactory(name="Konkurentsiammet")

    result = catalogue.rename_organisation(
        actor=department_head,
        organisation=body,
        name="Konkurentsiamet",
        expected_revision=catalogue.organisation_revision(body),
        keep_old_name=False,
    )

    assert result.historical_alias is None
    assert not body.aliases.exists()


def test_a_rename_owes_a_search_rebuild_and_the_old_name_still_finds_the_matter(
    department_head, specialist
):
    body = factories.OrganisationFactory(name="Vanaamet")
    matter = factories.MatterFactory(owner=specialist, addressee_organisation=body)
    rebuild_all()
    SearchRebuildDebt.objects.all().delete()

    catalogue.rename_organisation(
        actor=department_head,
        organisation=body,
        name="Uusametkond",
        expected_revision=catalogue.organisation_revision(body),
    )

    assert _owed(SearchRebuildReason.ORGANISATION_RENAMED)
    assert _owed(SearchRebuildReason.ORGANISATION_ALIAS_CHANGED)
    rebuild_all()
    for term in ("Uusametkond", "Vanaamet"):
        assert {result.matter.pk for result in search(query=term, user=specialist)} == {
            matter.pk
        }, term


def test_a_rename_into_another_bodys_name_is_refused(department_head):
    taken = factories.OrganisationFactory(name="Maksu- ja Tolliamet")
    body = factories.OrganisationFactory(name="Maksuamet")

    with refused(catalogue.duplicate_refusal(taken)):
        catalogue.rename_organisation(
            actor=department_head,
            organisation=body,
            name="maksu- ja tolliamet",
            expected_revision=catalogue.organisation_revision(body),
        )

    body.refresh_from_db()
    assert body.name == "Maksuamet"


def test_a_rename_from_a_stale_page_is_refused_and_writes_nothing(department_head):
    body = factories.OrganisationFactory(name="Ametkond A")
    stale = catalogue.organisation_revision(body)
    catalogue.add_organisation_alias(actor=department_head, organisation=body, alias="AA")

    with refused(catalogue.STALE):
        catalogue.rename_organisation(
            actor=department_head, organisation=body, name="Ametkond B", expected_revision=stale
        )

    body.refresh_from_db()
    assert body.name == "Ametkond A"
    assert _events(ChangeEventType.ORGANISATION_RENAMED) == []


def test_a_page_without_a_revision_is_treated_as_stale(department_head):
    body = factories.OrganisationFactory(name="Ametkond C")

    with refused(catalogue.STALE):
        catalogue.rename_organisation(
            actor=department_head, organisation=body, name="Ametkond D", expected_revision=""
        )


def test_a_reviewed_baseline_institution_is_not_renamed_here(department_head):
    ministry = factories.OrganisationFactory(name="Rahandusministeerium")

    with refused(catalogue.REVIEWED_RENAME):
        catalogue.rename_organisation(
            actor=department_head,
            organisation=ministry,
            name="Riigirahanduse Ministeerium",
            expected_revision=catalogue.organisation_revision(ministry),
        )


def test_a_baseline_institution_found_through_its_spelling_is_protected_too(department_head):
    body = factories.OrganisationFactory(name="Rahandusmin.")
    OrganisationAlias.objects.create(organisation=body, alias="Rahandusministeerium")

    with refused(catalogue.REVIEWED_RENAME):
        catalogue.rename_organisation(
            actor=department_head,
            organisation=body,
            name="Midagi muud",
            expected_revision=catalogue.organisation_revision(body),
        )


# -- organisations: aliases ----------------------------------------------------------------


def test_adding_a_spelling_audits_it_and_owes_a_rebuild(department_head):
    body = factories.OrganisationFactory(name="Riigi Infosüsteemi Amet")
    SearchRebuildDebt.objects.all().delete()

    alias = catalogue.add_organisation_alias(
        actor=department_head,
        organisation=body,
        alias="RIA",
        alias_type=AliasType.ABBREVIATION,
        expected_revision=catalogue.organisation_revision(body),
    )

    assert alias.alias_type == AliasType.ABBREVIATION
    assert _owed(SearchRebuildReason.ORGANISATION_ALIAS_CHANGED)
    (event,) = _events(ChangeEventType.ORGANISATION_ALIAS_ADDED)
    assert event.payload["alias"] == "RIA"


def test_a_spelling_another_body_already_has_is_refused(department_head):
    other = factories.OrganisationFactory(name="Riigikantselei")
    OrganisationAlias.objects.create(organisation=other, alias="RK")
    body = factories.OrganisationFactory(name="Riigikogu Kantselei")

    with refused(catalogue.duplicate_refusal(other)):
        catalogue.add_organisation_alias(actor=department_head, organisation=body, alias="rk")


def test_a_spelling_that_is_another_bodys_name_is_refused(department_head):
    other = factories.OrganisationFactory(name="Keskkonnaamet")
    body = factories.OrganisationFactory(name="Keskkonnainspektsioon")

    with refused(catalogue.duplicate_refusal(other)):
        catalogue.add_organisation_alias(
            actor=department_head, organisation=body, alias="Keskkonnaamet"
        )


@pytest.mark.parametrize(
    ("spelling", "message"),
    [("Päästeamet", catalogue.ALIAS_IS_THE_NAME), ("PA", catalogue.ALIAS_ALREADY_HERE)],
)
def test_a_spelling_the_body_already_has_is_refused(department_head, spelling, message):
    body = factories.OrganisationFactory(name="Päästeamet")
    OrganisationAlias.objects.create(organisation=body, alias="PA")

    with refused(message):
        catalogue.add_organisation_alias(actor=department_head, organisation=body, alias=spelling)


def test_removing_a_mistaken_spelling_audits_it(department_head):
    body = factories.OrganisationFactory(name="Politsei- ja Piirivalveamet")
    alias = OrganisationAlias.objects.create(organisation=body, alias="PPA vale")

    catalogue.remove_organisation_alias(
        actor=department_head,
        alias=alias,
        expected_revision=catalogue.organisation_revision(body),
    )

    assert not OrganisationAlias.objects.filter(pk=alias.pk).exists()
    (event,) = _events(ChangeEventType.ORGANISATION_ALIAS_REMOVED)
    assert event.payload["alias"] == "PPA vale"


def test_a_historical_name_is_never_removed(department_head):
    body = factories.OrganisationFactory(name="Uus nimi")
    alias = OrganisationAlias.objects.create(
        organisation=body, alias="Vana nimi", alias_type=AliasType.HISTORICAL_NAME
    )

    with refused(catalogue.HISTORICAL_NAME_KEPT):
        catalogue.remove_organisation_alias(
            actor=department_head,
            alias=alias,
            expected_revision=catalogue.organisation_revision(body),
        )

    assert OrganisationAlias.objects.filter(pk=alias.pk).exists()


def test_a_reviewed_spelling_of_a_baseline_institution_is_not_removed_here(department_head):
    ministry = factories.OrganisationFactory(name="Siseministeerium")
    alias = OrganisationAlias.objects.create(
        organisation=ministry, alias="SiM", alias_type=AliasType.ABBREVIATION
    )

    with refused(catalogue.REVIEWED_ALIAS):
        catalogue.remove_organisation_alias(
            actor=department_head,
            alias=alias,
            expected_revision=catalogue.organisation_revision(ministry),
        )


def test_references_are_counted_before_anybody_decides_anything(specialist):
    body = factories.OrganisationFactory(name="Viidatud amet")
    factories.MatterFactory(owner=specialist, addressee_organisation=body)
    factories.MatterFactory(owner=specialist, addressee_organisation=body)

    references = catalogue.organisation_references(body)

    assert references.by_relation["Matter.addressee_organisation"] == 2
    assert references.total == 2


def test_search_finds_a_body_by_a_folded_spelling():
    body = factories.OrganisationFactory(name="Põllumajandus- ja Toiduamet")
    OrganisationAlias.objects.create(organisation=body, alias="PTA")

    assert list(catalogue.search_organisations("pollumajandus")) == [body]
    assert list(catalogue.search_organisations("pta")) == [body]


# -- tags ------------------------------------------------------------------------------


def test_creating_a_tag_records_it(department_head):
    tag = vocabulary.create_tag(
        actor=department_head, key="halduskoormus", name="Halduskoormus", definition="Bürokraatia"
    )

    assert tag.owned_by == department_head
    (event,) = _events(ChangeEventType.TAG_CREATED)
    assert event.payload["key"] == "halduskoormus"


@pytest.mark.parametrize("key", ["Suur", "kaks sõna", "-algus", ""])
def test_an_invalid_key_is_refused(department_head, key):
    with refused(vocabulary.KEY_INVALID):
        vocabulary.create_tag(actor=department_head, key=key, name="Silt")


def test_a_tag_name_that_folds_onto_another_tag_is_refused(department_head):
    existing = factories.TagFactory(key="kaibemaks", name_et="Käibemaks")

    with refused(vocabulary.duplicate_refusal(existing)):
        vocabulary.create_tag(actor=department_head, key="km", name="kaibemaks")


def test_a_tag_name_that_is_another_tags_spelling_is_refused(department_head):
    existing = factories.TagFactory(key="vke", name_et="Väike- ja keskmise suurusega ettevõtted")
    TagAlias.objects.create(tag=existing, alias="VKE")

    with refused(vocabulary.duplicate_refusal(existing)):
        vocabulary.create_tag(actor=department_head, key="vke-2", name="vke")


def test_a_taken_key_is_refused(department_head):
    factories.TagFactory(key="ai", name_et="Tehisintellekt")

    with refused(vocabulary.KEY_TAKEN):
        vocabulary.create_tag(actor=department_head, key="ai", name="AI")


def test_renaming_a_tag_keeps_the_old_word_and_owes_a_rebuild(department_head):
    tag = factories.TagFactory(name_et="Kestlikkusaruanne")
    SearchRebuildDebt.objects.all().delete()

    vocabulary.update_tag(
        actor=department_head,
        tag=tag,
        name="Kestlikkusaruandlus",
        definition="CSRD",
        expected_revision=vocabulary.tag_revision(tag),
    )

    tag.refresh_from_db()
    assert tag.name_et == "Kestlikkusaruandlus"
    assert list(tag.aliases.values_list("alias", flat=True)) == ["Kestlikkusaruanne"]
    assert _owed(SearchRebuildReason.TAG_RENAMED)
    (event,) = _events(ChangeEventType.TAG_CHANGED)
    assert event.payload["changed"]["name"] == {
        "from": "Kestlikkusaruanne",
        "to": "Kestlikkusaruandlus",
    }
    assert "definition" in event.payload["changed"]


def test_saving_a_tag_unchanged_writes_nothing(department_head):
    tag = factories.TagFactory(name_et="Muutumatu", definition="Sama")

    vocabulary.update_tag(
        actor=department_head,
        tag=tag,
        name="Muutumatu",
        definition="Sama",
        expected_revision=vocabulary.tag_revision(tag),
    )

    assert _events(ChangeEventType.TAG_CHANGED) == []


def test_a_stale_tag_edit_is_refused(department_head):
    tag = factories.TagFactory(name_et="Algne")
    stale = vocabulary.tag_revision(tag)
    vocabulary.add_tag_alias(actor=department_head, tag=tag, alias="Teine sõna")

    with refused(vocabulary.STALE):
        vocabulary.update_tag(
            actor=department_head, tag=tag, name="Muudetud", definition="", expected_revision=stale
        )

    tag.refresh_from_db()
    assert tag.name_et == "Algne"


def test_a_tag_spelling_that_belongs_to_another_tag_is_refused(department_head):
    other = factories.TagFactory(name_et="Energeetika")
    tag = factories.TagFactory(name_et="Energiamajandus")

    with refused(vocabulary.duplicate_refusal(other)):
        vocabulary.add_tag_alias(actor=department_head, tag=tag, alias="energeetika")


def test_removing_a_tag_spelling_audits_it_and_owes_a_rebuild(department_head):
    tag = factories.TagFactory(name_et="Maksud")
    alias = TagAlias.objects.create(tag=tag, alias="Maksundus")
    SearchRebuildDebt.objects.all().delete()

    vocabulary.remove_tag_alias(
        actor=department_head, alias=alias, expected_revision=vocabulary.tag_revision(tag)
    )

    assert _owed(SearchRebuildReason.TAG_ALIAS_CHANGED)
    assert _events(ChangeEventType.TAG_ALIAS_REMOVED)


def test_a_merge_retires_the_tag_and_moves_no_assignment(department_head, specialist):
    old = factories.TagFactory(name_et="Bürokraatia")
    new = factories.TagFactory(name_et="Halduskoormus")
    matter = factories.MatterFactory(owner=specialist, title="Aruandluskohustuste teema")
    matter.tags.add(old)
    rebuild_all()

    vocabulary.merge_tag(
        actor=department_head, tag=old, into=new, expected_revision=vocabulary.tag_revision(old)
    )

    old.refresh_from_db()
    assert old.merged_into == new
    assert not old.is_active
    assert old.deprecated_at is not None
    assert list(matter.tags.all()) == [old]
    # Reprojected in the merge's own transaction: findable through the
    # tag that replaced it, with no rebuild in between.
    assert {r.matter.pk for r in search(query="Halduskoormus", user=specialist)} == {matter.pk}
    (event,) = _events(ChangeEventType.TAG_MERGED)
    assert event.payload["into"] == str(new.pk)
    assert event.payload["matters"] == 1


def test_a_merged_tag_is_frozen(department_head):
    old = factories.TagFactory(name_et="Vana silt")
    new = factories.TagFactory(name_et="Uus silt")
    vocabulary.merge_tag(
        actor=department_head, tag=old, into=new, expected_revision=vocabulary.tag_revision(old)
    )
    old.refresh_from_db()

    with refused(vocabulary.MERGED_IS_FROZEN):
        vocabulary.update_tag(
            actor=department_head,
            tag=old,
            name="Ümber",
            definition="",
            expected_revision=vocabulary.tag_revision(old),
        )
    with refused(vocabulary.MERGED_IS_FROZEN):
        vocabulary.reactivate_tag(
            actor=department_head, tag=old, expected_revision=vocabulary.tag_revision(old)
        )


def test_merging_into_a_retired_or_merged_tag_is_refused(department_head):
    retired = factories.TagFactory(name_et="Aegunud", is_active=False)
    tag = factories.TagFactory(name_et="Liidetav")

    with refused(vocabulary.MERGE_TARGET_RETIRED):
        vocabulary.merge_tag(
            actor=department_head,
            tag=tag,
            into=retired,
            expected_revision=vocabulary.tag_revision(tag),
        )


def test_two_opposite_merges_cannot_both_happen(department_head):
    first = factories.TagFactory(name_et="Esimene")
    second = factories.TagFactory(name_et="Teine")
    vocabulary.merge_tag(
        actor=department_head,
        tag=first,
        into=second,
        expected_revision=vocabulary.tag_revision(first),
    )
    second.refresh_from_db()

    with refused(vocabulary.MERGE_TARGET_RETIRED):
        vocabulary.merge_tag(
            actor=department_head,
            tag=second,
            into=first,
            expected_revision=vocabulary.tag_revision(second),
        )

    second.refresh_from_db()
    assert second.merged_into is None and second.is_active


def test_a_tag_cannot_be_merged_into_itself(department_head):
    tag = factories.TagFactory()

    with refused(vocabulary.MERGE_INTO_ITSELF):
        vocabulary.merge_tag(
            actor=department_head, tag=tag, into=tag, expected_revision=vocabulary.tag_revision(tag)
        )


def test_deactivating_keeps_every_classification_and_can_be_undone(department_head, specialist):
    tag = factories.TagFactory(name_et="Ajutine")
    matter = factories.MatterFactory(owner=specialist)
    matter.tags.add(tag)
    submission = factories.SubmissionFactory(matter=matter)
    SubmissionTagAssignment.objects.create(submission=submission, tag=tag)

    vocabulary.deactivate_tag(
        actor=department_head, tag=tag, expected_revision=vocabulary.tag_revision(tag)
    )
    tag.refresh_from_db()

    assert not tag.is_active
    assert list(matter.tags.all()) == [tag]
    assert SubmissionTagAssignment.objects.filter(tag=tag).count() == 1
    (event,) = _events(ChangeEventType.TAG_DEACTIVATED)
    assert event.payload["matters"] == 1 and event.payload["submissions"] == 1

    with refused(vocabulary.ALREADY_INACTIVE):
        vocabulary.deactivate_tag(
            actor=department_head, tag=tag, expected_revision=vocabulary.tag_revision(tag)
        )
    vocabulary.reactivate_tag(
        actor=department_head, tag=tag, expected_revision=vocabulary.tag_revision(tag)
    )
    tag.refresh_from_db()
    assert tag.is_active and tag.deprecated_at is None
    assert _events(ChangeEventType.TAG_REACTIVATED)


def test_no_service_deletes_a_tag(department_head):
    """The vocabulary's promise, stated as the absence of a verb."""
    assert not [name for name in dir(vocabulary) if "delete" in name]
    assert not [name for name in dir(catalogue) if "delete" in name or "merge" in name]
